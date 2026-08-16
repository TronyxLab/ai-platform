# GREP_SUMMARY: test-healthcheck-lib healthcheck_poll D5 criterion running healthy none empty unhealthy timeout docker-compose static-contract
# STRUCTURE: ┌DI docker-объект (docker_ps/inspect_state_health) + sleep_fn┐ → ◇ D5-criterion (running AND healthy|""|none)
#            → ◇ unhealthy/exited → ждём → timeout → ⎋ "unhealthy" → ◇ shell-фасад static-contract (check_tcp R5) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for the единого docker-критерия «здоров» (D5, DevPlan 116 B5 T3):
##           core/internal/shared/docker_compose.py::healthcheck_poll — контейнер running AND
##           Health.Status ∈ {healthy, "", "none"} = здоров; "unhealthy"/exited → ждать (стартовые
##           гонки); timeout → "unhealthy". Bash-фасад core/lib/healthcheck.sh покрыт static-контрактом
##           (примитивы check_http/exec_check/check_docker_health присутствуют, check_tcp удалён — R5).
## @scope    DevPlan 139 W2 (миграция bash-тестов → Python-канон): exec_check/check_http bash-тесты
##           УДАЛЕНЫ (требовали Docker + исполняли shell-функции) — заменены прямыми тестами критерия
##           через DI-объект docker (никакого docker daemon). Docker-требований нет.
## @invariants
##   - Критерий тестируется нативно: docker_ps / inspect_state_health через DI-объект (docker=) +
##     sleep_fn (DevPlan 167 D2 CommandRunner-seam — 0 патчей)
##   - docker_compose.time.sleep — заменяется sleep_fn (таймаут-сценарии детерминированы)
##   - Bash исполняется ТОЛЬКО для static-контракта реального фасада healthcheck.sh (R5 check_tcp)
##   - Каждый тест — IMP:9-траектория (_assert_imp9)
## @rationale D5 (AGENTS.md root, TRAP[DECISION] 2026-08-01): единый канон healthcheck-критерия живёт
##            в docker_compose.healthcheck_poll; healthcheck.sh — стабильная shell-библиотека
##            (keep-исключение). Bash-функциональные тесты (exec_check/check_http) — класс P0
##            синтетики (исполнение shell в тестах); критерий тестируется в Python-каноне.
## @changes 2026-08-05 | DevPlan 139 W2 — миграция на Python-канон D5 (bash-тесты → критерий + static-контракт)
##           2026-08-14 | DevPlan 167 D2 — monkeypatch → DI-объект docker + sleep_fn (3 → 0 setattr)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.shared.docker_compose import healthcheck_poll

logger = logging.getLogger(__name__)

# Пути shell-фасада (static-контракт, НЕ функциональное исполнение бизнес-логики)
_HEALTHCHECK_LIB: Path = Path(__file__).resolve().parent.parent.parent / "core" / "lib" / "healthcheck.sh"
_LOGGING_LIB: Path = Path(__file__).resolve().parent.parent.parent / "core" / "lib" / "logging.sh"


def _assert_imp9(caplog: pytest.LogCaptureFixture) -> None:
    """LDD telemetry: печать IMP:7-10 траектории + assert найден IMP:9-лог (Anti-Illusion).

    ## @purpose — Тест не молчит: печатает реальную траекторию (IMP:7-10) до ассертов,
    ##            требует как минимум один IMP:9-лог в успешном сценарии.
    ## @io — ⇥ caplog → ⎋ None (assert found)
    ## @complexity — O(R) — R = записи caplog
    """
    found = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found"


class _FakeProc:
    """Fake subprocess.CompletedProcess (DI-контракт docker_ops-примитивов)."""

    def __init__(self, rc: int = 0, stdout: str = "") -> None:
        self.returncode = rc
        self.stdout = stdout
        self.stderr = ""


# ═══════════════════════════════════════════════════════════════════
# HELPERS: DI-фейки docker-объекта (D5-критерий, без docker daemon, 167 D2)
# ═══════════════════════════════════════════════════════════════════


# region FUNC__make_docker_fakes
def _make_docker_fakes(
    states: list[tuple[str, str]],
    *,
    ps_stdout: str = "cid1\n",
) -> tuple[object, object]:
    """Собрать DI-фейки для healthcheck_poll: docker-объект (docker_ps/inspect_state_health) + sleep_fn.

    ▶ ┌states[(state, health)] + ps_stdout┐ → ○ docker_ps → cids → ○ inspect_state_health per cid → ⎋ (docker, sleep_fn)

    ## @purpose — DI-шов для healthcheck_poll: docker-объект с примитивами docker_ps/inspect_state_health
    ##            передаётся параметром (docker=) — без реального docker daemon и без monkeypatch.
    ## @io — ⇥ states: list[(state, health)], ps_stdout: str → ⎋ (docker-объект, sleep_fn)
    ## @complexity — O(C) — C = контейнеры (по одному инспекту на cid)
    """
    from types import SimpleNamespace

    cids = [ln.strip() for ln in ps_stdout.splitlines() if ln.strip()]

    def fake_ps(filters=None, format=None, timeout=None) -> _FakeProc:  # ruff: ignore[A002] — keyword-контракт docker_ops.format
        return _FakeProc(rc=0, stdout=ps_stdout)

    def fake_inspect(cid: str, timeout: int | None = None) -> tuple[str, str]:
        # Состояние по порядку cid (1:1 — каждый инспект возвращает следующий state)
        idx = cids.index(cid) if cid in cids else 0
        return states[idx % len(states)]

    docker = SimpleNamespace(docker_ps=fake_ps, inspect_state_health=fake_inspect)
    return docker, lambda _s: None


# endregion FUNC__make_docker_fakes


# ═══════════════════════════════════════════════════════════════════
# TESTS: D5-критерий «здоров» (docker_compose.healthcheck_poll)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_d5_criterion_state_health
@pytest.mark.parametrize(
    ("state", "health", "expected", "imp9_note"),
    [
        pytest.param("running", "healthy", "healthy", "running+healthy → healthy", id="running_healthy"),
        pytest.param("running", "", "healthy", "running+'' → healthy", id="running_no_healthcheck_empty"),
        pytest.param("running", "none", "healthy", "running+none → healthy", id="running_health_none"),
        pytest.param("exited", "healthy", "unhealthy", "exited → unhealthy", id="exited_waits_timeout"),
    ],
)
# 🧪 TRAP[TEST] · REGRESSION · D5 — критерий «здоров»: (state, Health.Status) → healthcheck_poll
# · Scenario: running+healthy → "healthy"; running+"" (без healthcheck) → "healthy";
# ·   running+"none" → "healthy"; exited+healthy (не running) → "unhealthy" после timeout
# · Last fail: 5 расходящихся реализаций критерия (ps-filter/wrapper/inspect/lib/poller) до D5;
# ·   критерии, требующие строго "healthy", фейлили контейнеры без healthcheck;
# ·   критерии, проверявшие только Health.Status, считали exited+healthy здоровым
# · Remove if: критерий «здоров» изменён / exited-контейнер начнёт считаться здоровым
def test_d5_criterion_state_health(
    state: str,
    health: str,
    expected: str,
    imp9_note: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D5: (state, Health.Status) → результат healthcheck_poll (консолидация 4 кейсов, IMP:9)."""
    caplog.set_level(logging.DEBUG)
    docker, sleep_fn = _make_docker_fakes([(state, health)])

    result = healthcheck_poll("test-app", timeout=1, interval=1, docker=docker, sleep_fn=sleep_fn)

    assert result == expected, f"Expected {expected!r} for ({state}, {health!r}), got {result!r}"
    logger.critical("[IMP:9][test_healthcheck_lib][d5] %s", imp9_note)
    _assert_imp9(caplog)


# endregion FUNC_test_d5_criterion_state_health


# region FUNC_test_d5_unhealthy_waits_timeout
# 🧪 TRAP[TEST] · REGRESSION · D5 — "unhealthy" НЕ фейлит сразу (стартовые гонки)
# · Scenario: Health.Status="unhealthy" → поллинг до timeout → "unhealthy" (не fail-fast)
# · Last fail: критерии с fail-fast на "unhealthy" ломали деплой при стартовых гонках
# · Remove if: "unhealthy" начнёт фейлить немедленно
def test_d5_criterion_unhealthy_waits_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """D5: unhealthy → ждём (не fail сразу) → timeout → "unhealthy"."""
    caplog.set_level(logging.DEBUG)
    docker, sleep_fn = _make_docker_fakes([("running", "unhealthy")])

    result = healthcheck_poll("test-app", timeout=1, interval=1, docker=docker, sleep_fn=sleep_fn)

    assert result == "unhealthy", f"Expected 'unhealthy' after timeout, got {result!r}"
    # Факт события: поллинг наблюдал не-здоровое состояние (стартовые гонки обрабатываются)
    assert any("not healthy yet" in r.message for r in caplog.records), caplog.text
    logger.critical("[IMP:9][test_healthcheck_lib][d5] unhealthy → ждём → timeout unhealthy")
    _assert_imp9(caplog)


# endregion FUNC_test_d5_unhealthy_waits_timeout


# region FUNC_test_d5_all_containers_must_be_healthy
# 🧪 TRAP[TEST] · REGRESSION · D5 — ВСЕ контейнеры должны быть здоровы
# · Scenario: 2 контейнера — один unhealthy → "unhealthy" (любой не-здоров → ждать)
# · Last fail: критерии, проверявшие только первый контейнер, пропускали больные
# · Remove if: семантика «все здоровы» изменена
def test_d5_criterion_all_containers_must_be_healthy(caplog: pytest.LogCaptureFixture) -> None:
    """D5: все контейнеры running+healthy обязательны — один не-здоров → ждём → timeout."""
    caplog.set_level(logging.DEBUG)
    docker, sleep_fn = _make_docker_fakes([("running", "healthy"), ("running", "unhealthy")], ps_stdout="cid1\ncid2\n")

    result = healthcheck_poll("test-app", timeout=1, interval=1, docker=docker, sleep_fn=sleep_fn)

    assert result == "unhealthy", f"Expected 'unhealthy' (второй контейнер не-здоров), got {result!r}"
    assert any("not healthy yet" in r.message for r in caplog.records), caplog.text
    logger.critical("[IMP:9][test_healthcheck_lib][d5] все-контейнеры-обязаны-быть-здоровы → unhealthy")
    _assert_imp9(caplog)


# endregion FUNC_test_d5_all_containers_must_be_healthy


# region FUNC_test_d5_no_containers_waits_timeout
# 🧪 TRAP[TEST] · REGRESSION · D5 — контейнеров нет → ждём → timeout
# · Scenario: docker ps пуст → поллинг (не fail) → "unhealthy"
# · Last fail: критерии с fail-fast при пустом ps падали на стартовых гонках
# · Remove if: пустой ps начнёт фейлить немедленно
def test_d5_criterion_no_containers_waits_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """D5: нет контейнеров (docker ps пуст) → ждём → timeout "unhealthy"."""
    caplog.set_level(logging.DEBUG)
    docker, sleep_fn = _make_docker_fakes([("running", "healthy")], ps_stdout="")

    result = healthcheck_poll("test-app", timeout=1, interval=1, docker=docker, sleep_fn=sleep_fn)

    assert result == "unhealthy", f"Expected 'unhealthy' (нет контейнеров), got {result!r}"
    assert any("No containers" in r.message for r in caplog.records), caplog.text
    logger.critical("[IMP:9][test_healthcheck_lib][d5] пустой ps → unhealthy")
    _assert_imp9(caplog)


# endregion FUNC_test_d5_no_containers_waits_timeout


# region FUNC_test_d5_timeout_logs_warning
# 🧪 TRAP[TEST] · REGRESSION · D5 — timeout логируется warning'ом "unhealthy"
# · Scenario: поллинг не достиг здоровья → IMP:7 warning + возврат "unhealthy"
# · Last fail: N/A (D5-канон: non-fatal timeout — не raise)
# · Remove if: timeout-семантика изменена (raise вместо возврата)
def test_d5_criterion_timeout_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """D5: timeout → IMP:7 warning + "unhealthy" (non-fatal, не raise)."""
    caplog.set_level(logging.DEBUG)
    docker, sleep_fn = _make_docker_fakes([("running", "starting")])

    result = healthcheck_poll("test-app", timeout=1, interval=1, docker=docker, sleep_fn=sleep_fn)

    assert result == "unhealthy"
    assert any(r.levelno == logging.WARNING and "unhealthy" in r.message for r in caplog.records), caplog.text
    logger.critical("[IMP:9][test_healthcheck_lib][d5] timeout → warning unhealthy (non-fatal)")
    _assert_imp9(caplog)


# endregion FUNC_test_d5_timeout_logs_warning


# ═══════════════════════════════════════════════════════════════════
# TESTS: shell-фасад static-контракт (healthcheck.sh — keep-исключение)
# ═══════════════════════════════════════════════════════════════════


# region FUNC__run_bash_static
def _run_bash_static(tmp_path: Path, code: str) -> subprocess.CompletedProcess:
    """Static-контракт фасада: source реального healthcheck.sh + проверка type -t.

    ## @purpose — Единственное легитимное bash-исполнение: верификация реального shell-фасада
    ##            (примитивы присутствуют, check_tcp удалён — R5). Бизнес-критерий — в Python.
    ## @io — ⇥ tmp_path, code → ⎋ CompletedProcess
    ## @complexity O(1)
    """
    script = tmp_path / "test_facade.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'LOGGING_LIB="{_LOGGING_LIB}"\n'
        f'HEALTHCHECK_LIB="{_HEALTHCHECK_LIB}"\n'
        'source "$LOGGING_LIB"\n'
        'source "$HEALTHCHECK_LIB"\n'
        f"{code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=10, check=False)


# endregion FUNC__run_bash_static


# region FUNC_test_check_tcp_removed
# 🧪 TRAP[TEST] · NEGATIVE (R5) · B6 — check_tcp удалён + примитивы фасада присутствуют
# · Scenario: source healthcheck.sh → check_http/exec_check/check_docker_health = функции,
# ·   check_tcp = НЕ функция (удалён в волне 118 B6, 0 callers)
# · Last fail: check_tcp существовал до 118 B6 (healthcheck.sh L327-348)
# · Remove if: check_tcp будет восстановлен / примитив фасада удалён
def test_check_tcp_removed(tmp_path: Path) -> None:
    """Static-контракт фасада (расширен в W2): примитивы присутствуют, check_tcp удалён (R5, B6)."""
    result = _run_bash_static(
        tmp_path,
        """
        for fn in check_http exec_check check_docker_health; do
            if [[ "$(type -t "$fn")" != "function" ]]; then
                echo "[IMP:10][test] FAIL: $fn не определён в healthcheck.sh" >&2
                exit 1
            fi
        done
        if [[ "$(type -t check_tcp)" == "function" ]]; then
            echo "[IMP:10][test] FAIL: check_tcp всё ещё определён (B6 R5)" >&2
            exit 1
        fi
        echo "[IMP:9][test] healthcheck.sh фасад: примитивы OK, check_tcp REMOVED" >&2
        exit 0
        """,
    )

    assert result.returncode == 0, f"Фасад static-контракт нарушен: {result.stderr}"
    logger.critical("[IMP:9][test_healthcheck_lib][facade] static-контракт фасада OK (B6 R5)")


# endregion FUNC_test_check_tcp_removed
