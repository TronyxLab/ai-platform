# GREP_SUMMARY: session, conftest, pytest, session-hooks, escalation, anti-loop, attempt-counter, sessionstart, sessionfinish, full-session, schema-validation, master-only
# STRUCTURE: pytest_sessionstart(master: validate fixtures→increment→record scope) → run_tests → pytest_sessionfinish(◇full-session 100% PASS→reset|◇fail→escalation) → _fixture_schema_integrity(per-test fail)
# region MODULE_CONTRACT
## @purpose  Pytest session hooks (sessionstart/sessionfinish) + escalation dispatch for Anti-Loop protocol.
##           Increments attempt counter on session start, resets on 100% PASS of a FULL session, escalates on failure.
## @scope    Session-level hooks extracted from tests/conftest.py. Counter read/write delegated to
##           conftest.counter; escalation messages delegated to conftest.checklist.
## @invariants
##   - .test_counter.json stored in tests/ directory (managed by conftest.counter) — ЕДИНСТВЕННЫЙ
##     counter-файл (T12.1 T-1: dual counter tests/gates/.test_counter.json удалён)
##   - Counter increments on every non-100% session (in sessionstart, master only)
##   - Counter resets to 0 ONLY when exitstatus == 0 AND session is FULL
##     (_is_full_session: unfiltered run + >= FULL_SESSION_MIN_ITEMS items). Поднабор (-m filter,
##     отдельный файл) при 100% PASS НЕ сбрасывает attempts (T12.1 T-2: reset only full session)
##   - Escalation levels: 1-2=checklist, 3=external help, 4=reflection, 5+=critical
##   - PYTEST_NO_ESCALATION env var suppresses escalation output (used by git hooks)
##   - retention module loaded via importlib from core/modules/backup-cron/scripts/
##   - МУТИРУЮЩИЕ session-хуки (attempt-счётчик, docker-cleanup, network release, schema-валидация) —
##     ТОЛЬКО master-воркер (PYTEST_XDIST_WORKER гейт, DevPlan 124 T1): при -n auto хуки выполняются
##     в каждом воркере, конкурентные docker rm -f / reset счётчика ломали параллельную сессию.
##     Schema-валидация — master-only (T12.5 T-8): воркеры не дублируют валидацию
##   - Schema-ошибки тест-фикстур — per-test FAIL через autouse _fixture_schema_integrity,
##     НЕ pytest.exit (T12.5 T-8: pytest.exit убивал весь воркер/сессию без per-test traceback)
## @rationale  Extracted from tests/conftest.py to reduce file size and isolate session lifecycle logic.
##             Path adjusted from __file__ (conftest/) → (conftest/../..) so core/ resolves correctly.
## @changes
##   2026-08-06 | DevPlan 140 W5 (W12-T13): name-prefix fallback в _final_hermes_test_cleanup
##   УДАЛЁН — sweep label-only (ai-platform.test=true); TRAP[DECISION] 2026-08-05 обновлён
##   (Rev executed: создатель test_hermes_init.py теперь помечает контейнеры той же константой)
##   LAST_CHANGE: 2026-08-05 | DevPlan 136 W12: T12.1 (reset только полная сессия — _is_full_session),
##   T12.5 (schema-валидация master-only + per-test fail вместо pytest.exit), T12.7 (retry-rate
##   check в sessionfinish), T12.9 (hermes-cleanup по label)
##   2026-08-03 | DevPlan 124 T1: master-guard для sessionstart/sessionfinish —
##   _is_xdist_worker() (PYTEST_XDIST_WORKER); counter increment/reset и docker-cleanup —
##   только master; воркеры — no-op с логом worker id (гонки фактов 4-5 DevPlan 124)
##   2026-07-12 | Extracted from tests/conftest.py — ESCALATION_DISPATCH + PYTEST_SESSION_HOOKS regions
##   DevPlan 123 T5: added _final_hermes_test_cleanup() — name-based sweep for hermes-test-*
##   containers (label-free), called from pytest_sessionfinish (false-lead #10, 503 on /health)
# endregion MODULE_CONTRACT

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import jsonschema
import pytest
import yaml

from _conftest.checklist import _print_checklist, _print_escalation, _print_external_help, _print_reflection
from _conftest.counter import (
    _increment_counter,
    _read_counter,
    _record_scope,
    _reset_counter,
    _write_counter,  # noqa: F401 — backward-compat re-export (test_session_xdist_guards.py monkeypatch target)
)

# Порог «полной сессии» для сброса anti-loop счётчика (T12.1 T-2):
#   - marker-выражение (-m) пусто (harness всегда фильтрует — check-suite/test_runner/ci.mk
#     передают -m gate/static_audit/... → такие прогоны НИКОГДА не «полные»)
#   - собранных тестов >= порога (отдельный файл/директория — поднабор, не полная сессия)
_FULL_SESSION_MIN_ITEMS = 1000

# Ошибки schema-валидации тест-фикстур (T12.5 T-8): заполняются в master на sessionstart,
# потребляются per-test autouse фикстурой _fixture_schema_integrity (pytest.fail вместо pytest.exit)
_FIXTURE_SCHEMA_ERRORS: list[str] = []

# region FIXTURE_SCHEMA_VALIDATION


# Mapping: test_data fixture filename → schema file (relative to project root)
_FIXTURE_SCHEMA_MAP = {
    "node.yaml": "core/schemas/node.schema.json",
    # Future fixtures — add entries here
}


def _validate_test_fixtures() -> list[str]:
    """Validate all test fixtures against their schemas — returns list of error strings.

    ## @purpose  Собирает ошибки schema-валидации тест-фикстур (tests/test_data/node.yaml vs
    ##            core/schemas/*.schema.json). T12.5 (T-8): НЕ вызывает pytest.exit — ошибки
    ##            возвращаются и потребляются per-test autouse фикстурой (pytest.fail), что даёт
    ##            per-test traceback вместо убийства всей сессии/воркера.
    ## @io       → ⎋ list[str]: пусто = валидация прошла; иначе — сообщения об ошибках
    ## @complexity O(F) где F = фикстуры в _FIXTURE_SCHEMA_MAP
    ## @invariants
    ##   - Missing fixture file → skip (optional fixtures), не ошибка
    ##   - Missing schema file → ошибка (configuration error) — возвращается, не pytest.exit
    ##   - yaml.safe_load (не FullLoader) для безопасности
    ##   - Вызывается ТОЛЬКО master-воркером (гейт в pytest_sessionstart)
    """
    # Resolve paths relative to tests/_conftest/session.py
    # session.py → tests/_conftest/ → tests/ → project root
    conftest_dir = pathlib.Path(__file__).resolve().parent  # tests/_conftest/
    test_data_dir = conftest_dir.parent / "test_data"  # tests/test_data/
    project_root = conftest_dir.parent.parent  # project root

    errors: list[str] = []
    for fixture_name, schema_relpath in _FIXTURE_SCHEMA_MAP.items():
        fixture_path = test_data_dir / fixture_name
        schema_path = project_root / schema_relpath

        if not fixture_path.exists():
            continue  # Optional fixture — skip silently

        if not schema_path.exists():
            errors.append(
                f"[IMP:10][sessionstart] Schema file not found: {schema_path}\n"
                f"Check _FIXTURE_SCHEMA_MAP in tests/_conftest/session.py"
            )
            continue

        with open(fixture_path) as f:
            data = yaml.safe_load(f)
        with open(schema_path) as f:
            schema = json.load(f)

        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            errors.append(f"  {fixture_path}: {e.message}")

    return errors


# region FUNC_fixture_schema_integrity
## @purpose  Autouse per-test фикстура (T12.5 T-8): если в master на sessionstart накоплены
##            ошибки schema-валидации тест-фикстур — каждый тест сессии падает с агрегированным
##            сообщением (pytest.fail, НЕ pytest.skip/pytest.exit). Воркеры видят пустой список
##            (валидация master-only) и проходят без накладных расходов.
## @io       → ⎋ None | pytest.fail (агрегированные schema-ошибки)
## @complexity O(1)
@pytest.fixture(scope="function", autouse=True)
def _fixture_schema_integrity() -> None:
    """Fail every test when test-fixture schema validation found errors (master-validated)."""
    if _FIXTURE_SCHEMA_ERRORS:
        pytest.fail(
            "[IMP:10][session] Test fixture schema validation FAILED:\n"
            + "\n".join(_FIXTURE_SCHEMA_ERRORS)
            + "\n\nUpdate test fixtures to match current schemas.",
            pytrace=False,
        )


# endregion FUNC_fixture_schema_integrity


# region FUNC_is_full_session
## @purpose  Детекция «полной сессии» для сброса anti-loop счётчика (T12.1 T-2).
##            Полная сессия = прогон БЕЗ marker-фильтра (-m пусто) И собравший >=
##            _FULL_SESSION_MIN_ITEMS тестов. Harness (check-suite / test_runner / ci.mk)
##            всегда фильтрует по маркерам (gate, static_audit, contract, ...) → такие прогоны
##            НИКОГДА не «полные» → attempts при их 100% PASS не сбрасываются (поднабор не
##            стирает evidence фейла полного прогона).
## @io       ⇥ session: pytest.Session → ⎋ bool
## @complexity O(1)
def _is_full_session(session: pytest.Session) -> bool:
    """True только для прогона без -m фильтра и с >= _FULL_SESSION_MIN_ITEMS собранных тестов."""
    marker_expr = session.config.getoption("-m", default=None)
    if marker_expr:
        return False
    collected = len(getattr(session, "items", []))
    return collected >= _FULL_SESSION_MIN_ITEMS


# endregion FUNC_is_full_session


# endregion FIXTURE_SCHEMA_VALIDATION

# region ESCALATION_DISPATCH


def _handle_escalation(attempts: int) -> None:
    """Print appropriate escalation message based on attempt count."""
    if attempts <= 2:
        _print_checklist()
    elif attempts == 3:
        _print_external_help()
    elif attempts == 4:
        _print_reflection()
    else:
        _print_escalation()


# endregion ESCALATION_DISPATCH


# region FUNC_IS_XDIST_WORKER
## @purpose  Детекция xdist-воркера: env PYTEST_XDIST_WORKER устанавливается pytest-xdist
##           в каждом воркере и отсутствует в master (DevPlan 124, факт 11 — стандартный
##           контракт xdist). Гейт для session-хуков: attempt-счётчик и docker-cleanup
##           принадлежат master-сессии (она видит aggregate-результат и владеет стёком).
## @io       → ⎋ bool (True = текущий процесс — xdist-воркер)
## @complexity O(1)
def _is_xdist_worker() -> bool:
    """True when running inside a pytest-xdist worker (PYTEST_XDIST_WORKER set)."""
    return bool(os.environ.get("PYTEST_XDIST_WORKER"))


# endregion FUNC_IS_XDIST_WORKER


# region PYTEST_SESSION_HOOKS


def pytest_sessionstart(session: pytest.Session) -> None:
    """
    Session start hook: validate fixtures (master only), increment attempt counter + conditional import.

    Read .test_counter.json, increment attempts, write back + record session scope.
    Import retention.py ONLY when backup or test_retention marker is active —
    fail-fast: if retention.py is broken, it's discovered only when needed.
    """
    # ── FAIL-FAST: validate test fixtures BEFORE any test runs ──
    # T12.5 (T-8): валидация — ТОЛЬКО master (PYTEST_XDIST_WORKER гейт ниже); при -n auto
    # sessionstart выполняется в каждом воркере — дублирование валидации = лишние расходы и
    # pytest.exit-риски. Ошибки копятся в _FIXTURE_SCHEMA_ERRORS → per-test fail (не pytest.exit).
    _validate_fixtures_in_session(session)

    # Conditional import: only for backup/retention tests
    _marker_option = session.config.getoption("-m", "")
    _is_backup_test = "backup" in _marker_option or "test_retention" in _marker_option
    if _is_backup_test:
        _backup_cron_scripts = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "core", "modules", "backup-cron", "scripts")
        )
        _retention_path = os.path.join(_backup_cron_scripts, "retention.py")
        if os.path.isfile(_retention_path):
            spec = importlib.util.spec_from_file_location("retention", _retention_path)
            if spec is not None and spec.loader is not None:
                retention_module = importlib.util.module_from_spec(spec)
                sys.modules["retention"] = retention_module
                spec.loader.exec_module(retention_module)
                print("[IMP:7][session] retention.py imported (backup/retention marker active)", file=sys.stderr)
            else:
                print("[IMP:9][session] retention.py found but spec/loader is None", file=sys.stderr)
        else:
            print("[IMP:8][session] retention.py not found — backup tests may fail", file=sys.stderr)
    else:
        print("[IMP:7][session] retention.py import skipped (no backup marker)", file=sys.stderr)

    # DevPlan 124 T1: счётчик инкрементирует ТОЛЬКО master-воркер. При -n auto sessionstart
    # выполняется в каждом воркере; без гейта один фейл-прогон давал Attempt #N (N воркеров)
    # и anti-loop протокол искажался (факт 4 DevPlan 124: -n 2 → Attempt #2 за один прогон).
    if _is_xdist_worker():
        print(
            f"[IMP:7][conftest][sessionstart] Worker {os.environ.get('PYTEST_XDIST_WORKER')} — "
            "attempt-counter increment skipped (master owns session)",
            file=sys.stderr,
        )
    else:
        # DevPlan 120 §3.3: атомарный _increment_counter под flock — защита от ПАРАЛЛЕЛЬНЫХ
        # pytest-сессий (2 агента одновременно), не от xdist-воркеров (их гейт выше).
        attempts = _increment_counter()
        # T12.1 (T-2): scope-тег сессии — чтобы sessionfinish отличал полную сессию от поднабора
        _record_scope(_session_scope_signature(session))
        print(
            f"[IMP:9][conftest][sessionstart] Attempt #{attempts} — running tests...",
            file=sys.stderr,
        )


# region FUNC_validate_fixtures_in_session
## @purpose  Master-only обёртка schema-валидации тест-фикстур (T12.5 T-8): в master —
##            _validate_test_fixtures() → ошибки в _FIXTURE_SCHEMA_ERRORS; в воркере — no-op.
## @io       ⇥ session: pytest.Session → ⎋ None
## @complexity O(F) в master, O(1) в воркерах
def _validate_fixtures_in_session(session: pytest.Session) -> None:
    """Заполняет _FIXTURE_SCHEMA_ERRORS в master; воркеры — no-op (валидация master-only)."""
    del session  # unused — гейт по env, не по session
    if _is_xdist_worker():
        print(
            f"[IMP:7][conftest][sessionstart] Worker {os.environ.get('PYTEST_XDIST_WORKER')} — "
            "fixture schema validation skipped (master owns session)",
            file=sys.stderr,
        )
        return
    _FIXTURE_SCHEMA_ERRORS.clear()
    _FIXTURE_SCHEMA_ERRORS.extend(_validate_test_fixtures() or [])
    if _FIXTURE_SCHEMA_ERRORS:
        print(
            "[IMP:10][sessionstart] Test fixture schema validation FAILED:\n"
            + "\n".join(_FIXTURE_SCHEMA_ERRORS)
            + "\n\nUpdate test fixtures to match current schemas.",
            file=sys.stderr,
        )


# endregion FUNC_validate_fixtures_in_session


# region FUNC_session_scope_signature
## @purpose  Сигнатура сессии для scope-тега counter (T12.1 T-2): marker-выражение + число
##            собранных тестов. Позволяет диагностировать subset-pass без сброса attempts.
## @io       ⇥ session: pytest.Session → ⎋ str
## @complexity O(1)
def _session_scope_signature(session: pytest.Session) -> str:
    """'marker=<expr>|items=<count>' — идентификатор текущего прогона."""
    marker_expr = session.config.getoption("-m", default=None) or ""
    return f"marker={marker_expr}|items={len(getattr(session, 'items', []))}"


# endregion FUNC_session_scope_signature


def _final_compose_cleanup() -> None:
    """Final cleanup: remove all containers with the test project label.

    ## @purpose — DevPlan 040 Wave 3: platform_services teardown uses `compose stop`
    ##            (not down) to save ~50s. This function runs once in pytest_sessionfinish
    ##            to remove all stopped containers with the ai-platform-test project label.
    ##            Uses `docker rm -f` on all containers matching the project label,
    ##            which is simpler than discovering compose files.
    ## @io — ⎋ None (side-effect: Docker containers removed)
    ## @complexity — O(N) where N = containers with matching label
    ## @rationale — `compose down` needs compose file paths, which are not available
    ##              in session.py. `docker rm -f` by project label is file-path-agnostic.
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "label=com.docker.compose.project=ai-platform-test",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        container_ids = [cid.strip() for cid in result.stdout.strip().splitlines() if cid.strip()]
        if container_ids:
            subprocess.run(
                ["docker", "rm", "-f", *container_ids],
                capture_output=True,
                text=True,
                timeout=30,
            )
            print(
                f"[IMP:7][conftest][sessionfinish] Final cleanup: removed {len(container_ids)} container(s)",
                file=sys.stderr,
            )
        else:
            print("[IMP:8][conftest][sessionfinish] Final cleanup: no containers to remove", file=sys.stderr)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"[IMP:8][conftest][sessionfinish] Final cleanup error: {exc}", file=sys.stderr)


# Метка, которой ДОЛЖНЫ помечаться все test-контейнеры (включая hermes-init detached-контейнеры).
# T12.9 (T-13): sweep по label (не по имени) — label-фильтр не заденет чужой контейнер.
_HERMES_TEST_LABEL = "ai-platform.test=true"


def _final_hermes_test_cleanup() -> None:
    """Final cleanup: remove hermes-test containers — by LABEL (T12.9 T-13), label-only.

    ## @purpose — DevPlan 123 T5 (false-lead #10): hermes-init tests (test_hermes_init.py)
    ##            create containers named hermes-test-l1-*/hermes-test-l2-* WITHOUT the
    ##            com.docker.compose.project=ai-platform-test label, so the label-based
    ##            _final_compose_cleanup() sweep misses them. Exited containers then cause
    ##            503 on the status-page /health endpoint. T12.9 (T-13): sweep по канонической
    ##            тест-метке ai-platform.test=true (docker rm -f по label, не имени).
    ## @io — ⎋ None (side-effect: Docker containers removed)
    ## @complexity — O(N) where N = containers matching the label filter
    ## @invariants
    ##   - Sweep: label=ai-platform.test=true (T12.9 T-13) — ЕДИНСТВЕННЫЙ путь (label-only, DevPlan 140 W5)
    ##   - 🧐 TRAP[DECISION] · 2026-08-05 · — · hermes-test- контейнеры пока БЕЗ метки
    ##     ai-platform.test=true: создание в test_hermes_init.py::_run_container_detached (вне
    ##     скоупа W12, файл не в списке изменений) — name-prefix fallback СОХРАНЁН до добавления
    ##     метки в создателе. · Rejected: удалить name-fallback (риск: 503 false-lead вернётся
    ##     на нодах с остатками hermes-test-*) · Reason: deferred — label-first + documented
    ##     fallback; proper fix (метка в создателе) — Debt с Rev 2026-10-21 · Rev: когда
    ##     test_hermes_init.py добавит label=ai-platform.test=true в docker run — удалить fallback
    ##     ✅ REV EXECUTED 2026-08-06 (DevPlan 140 W5): test_hermes_init.py::_run_container_detached
    ##     создаёт detached-контейнеры с label ai-platform.test=true (константа _HERMES_TEST_LABEL
    ##     импортируется создателем из этого модуля); name-prefix fallback УДАЛЁН — sweep label-only;
    ##     label-first — единственный путь.
    ## @rationale — Label-фильтр файл-агностичен и не зависит от имени; T12.9 требует rm -f по
    ##              label (не имени). Создатель контейнеров использует ту же константу — метка и
    ##              sweep не могут разойтись. Имя hermes-test-* больше не является идентификатором
    ##              для очистки (name-fallback удалён): без label контейнер sweep'ом не подхватывается.
    """
    try:
        container_ids = _docker_ps_ids(["--filter", f"label={_HERMES_TEST_LABEL}"])
        if container_ids:
            subprocess.run(
                ["docker", "rm", "-f", *container_ids],
                capture_output=True,
                text=True,
                timeout=30,
            )
            print(
                f"[IMP:7][conftest][sessionfinish] Hermes-test cleanup (label {_HERMES_TEST_LABEL}): "
                f"removed {len(container_ids)} container(s)",
                file=sys.stderr,
            )
        else:
            print("[IMP:8][conftest][sessionfinish] Hermes-test cleanup: no containers to remove", file=sys.stderr)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"[IMP:8][conftest][sessionfinish] Hermes-test cleanup error: {exc}", file=sys.stderr)


def _docker_ps_ids(extra_filters: list[str]) -> list[str]:
    """Return docker container IDs matching extra `docker ps -a` filters (best-effort).

    ## @io       ⇥ extra_filters: list[str] (e.g. ["--filter", "label=..."]) → ⎋ list[str]
    ## @complexity O(N) где N = контейнеры
    """
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", *extra_filters, "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return [cid.strip() for cid in result.stdout.strip().splitlines() if cid.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return []


def _force_release_test_networks() -> None:
    """Force-release all test networks via NetworkLeaseManager session cleanup.

    ## @purpose — DevPlan 041 W3: ensures no test Docker networks remain after session.
    ##            Called unconditionally from pytest_sessionfinish for safety.
    ##            Best-effort — does not fail if Docker unavailable or networks absent.
    ## @io — ⎋ None (side-effect: Docker networks removed)
    ## @complexity — O(N) where N = active leases
    """
    try:
        from _conftest.networks import get_network_manager

        nm = get_network_manager()
        nm.release_all()
        print("[IMP:9][conftest][sessionfinish] NetworkLeaseManager: all leases released", file=sys.stderr)
    except Exception as exc:
        print(f"[IMP:7][conftest][sessionfinish] NetworkLeaseManager cleanup (best-effort): {exc}", file=sys.stderr)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """
    Session finish hook: reset counter on 100% PASS of a FULL session, else keep + escalate.
    Also runs final Docker compose cleanup (DevPlan 040 Wave 3), the hermes-test-*
    container sweep (DevPlan 123 T5, label-based T12.9), NetworkLeaseManager cleanup
    and the smoke retry-rate check (T12.7 T-11).

    DevPlan 124 T1: docker-cleanup и counter read/reset выполняются ТОЛЬКО в master
    (PYTEST_XDIST_WORKER отсутствует). В xdist-воркерах pytest_sessionfinish выполняется
    при завершении КАЖДОГО воркера (факт 5): cleanup в рано завершившемся воркере удалял
    контейнеры/сети, ещё используемые другими воркерами; сброс счётчика воркером терял
    фейл параллельной сессии (факт 4). Master видит aggregate-результат сессии —
    reset при полном PASS корректен только там.

    DevPlan 136 W12 T12.1 (T-2): reset — ТОЛЬКО при 100% PASS ПОЛНОЙ сессии
    (_is_full_session: без -m фильтра + >= 1000 тестов). Поднабор (gates/static_audit/
    отдельный файл) при 100% PASS НЕ сбрасывает attempts — проходящий поднабор не должен
    стирать evidence фейла полного прогона.
    """
    if _is_xdist_worker():
        print(
            f"[IMP:8][conftest][sessionfinish] Worker {os.environ.get('PYTEST_XDIST_WORKER')} — "
            "cleanup skipped (master owns session)",
            file=sys.stderr,
        )
        return

    # ── Final compose cleanup (DevPlan 040 Wave 3) ──────────────────────────
    _final_compose_cleanup()

    # ── Hermes-test-* sweep (DevPlan 123 T5, false-lead #10; label-based T12.9) ─
    _final_hermes_test_cleanup()

    # ── NetworkLeaseManager cleanup (DevPlan 041 W3) ─────────────────────────
    _force_release_test_networks()

    # ── Smoke retry-rate check (T12.7 T-11): RED-логирование при >15% retry-rate ─
    _check_smoke_retry_rate()

    counter = _read_counter()
    attempts = counter.get("attempts", 1)

    if exitstatus == pytest.ExitCode.OK:
        # T12.1 (T-2): reset ТОЛЬКО при 100% PASS полной сессии; поднабор — не сбрасывает
        if _is_full_session(session):
            _reset_counter(scope=_session_scope_signature(session))
            print(
                "[IMP:9][conftest][sessionfinish] 100% PASS (full session) — counter reset to 0",
                file=sys.stderr,
            )
        else:
            print(
                f"[IMP:8][conftest][sessionfinish] 100% PASS (subset, {len(getattr(session, 'items', []))} items) — "
                f"counter NOT reset (T12.1 T-2: reset only on full-session pass)",
                file=sys.stderr,
            )
    else:
        print(
            f"[IMP:9][conftest][sessionfinish] FAILURES DETECTED — attempt #{attempts}",
            file=sys.stderr,
        )
        # Suppress anti-loop escalation when PYTEST_NO_ESCALATION is set (git hooks)
        if not os.environ.get("PYTEST_NO_ESCALATION"):
            _handle_escalation(attempts)
        # Counter already incremented in sessionstart — persist as-is


# region FUNC_check_smoke_retry_rate
## @purpose  T12.7 (T-11): чтение retry-stats из smoke.py (счётчики retries/attempts модульных
##            compose-стартов) и RED-логирование при retry-rate > 15% — сигнал ресурсной
##            контенции Docker (DevPlan 136 §12.4: «gate при >15% retry-rate»). Не роняет
##            сессию (exitstatus уже определён) — диагностический RED-маркер в лог.
## @io       → ⎋ None
## @complexity O(1)
def _check_smoke_retry_rate() -> None:
    """Log RED when smoke-module compose retry-rate exceeds 15% (T12.7 T-11)."""
    try:
        from _conftest.smoke import retry_stats

        attempts, retries = retry_stats()
        if attempts <= 0:
            return
        rate = retries / attempts
        if rate > 0.15:
            print(
                f"[IMP:9][conftest][sessionfinish] SMOKE RETRY-RATE {rate:.0%} "
                f"({retries}/{attempts}) EXCEEDS 15% threshold — Docker resource contention "
                "suspected (TRAP[DECISION] retry-until-green, DevPlan 136 W12 T12.7 T-11)",
                file=sys.stderr,
            )
        else:
            print(
                f"[IMP:8][conftest][sessionfinish] Smoke retry-rate {rate:.0%} ({retries}/{attempts}) — within 15% threshold",
                file=sys.stderr,
            )
    except Exception as exc:  # best-effort — никогда не роняет sessionfinish
        print(f"[IMP:7][conftest][sessionfinish] retry-rate check skipped: {exc}", file=sys.stderr)


# endregion FUNC_check_smoke_retry_rate


# endregion PYTEST_SESSION_HOOKS
