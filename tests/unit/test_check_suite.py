"""
# GREP_SUMMARY: test-check-suite manifest validation diagnostic-list gate-modes fingerprint replay cache check-cache diff-scope preflight-facade
# STRUCTURE: ▶ tmp git-repo fixtures → ◇ validate_manifest (schema v1 negative) → ◇ diagnostic-list (дыры входят, lint/smoke нет) → ◇ fingerprint (стабильность/инвалидация/excludes) → ◇ replay-кэш (green replay / failed NOT / CHECK_CACHE=0) → ◇ gate-режимы (fail-fast/accumulate/non_blocking/allow_no_tests/junit-merge/PROJECT) → ◇ diff-скоуп → ◇ preflight-фасад (старые флаги) → ⎋ LDD IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/check_suite.py — SoT-манифест executor (DevPlan 120):
##           валидация schema v1, диагностический набор, fingerprint-кэш (replay), gate-режимы
##           (fail-fast/accumulate/non_blocking/allow_no_tests/junit-merge/PROJECT), diff-скоуп,
##           deprecated-фасад preflight.py.
## @scope    Wave 1-4 тесты DevPlan 120 §4. Native imports (core.internal.check_suite);
##           tmp_path-repo фикстуры (Zero Hardcode Rule); subprocess git — только фикстуры.
## @invariants
##   - tmp_path only — никаких hardcoded путей
##   - Каждый тест — фальсифицируемый assert (Test Honesty R1/R2)
##   - LDD: caplog IMP:9 trajectory (Anti-Illusion)
##   - Тесты run_* используют no_fix=True (fix-фаза гоняла бы make fix-gate — вне unit-скоупа)
## @rationale DevPlan 120 §4: валидация манифеста (невалидный tier/timeout/cmds → ошибка),
##            диагностический список (smoke/component/lint НЕ входят; check-manifests/ruff/
##            gates-docker входят), fingerprint стабилен на неизменённом дереве, replay только
##            зелёного прогона, CHECK_CACHE=0 без чтения/записи, excluded-пути не влияют,
##            diff-скоуп по изменённым файлам, фасад preflight (старые флаги работают).
## @changes 2026-08-02 | Created (DevPlan 120 Wave 1-4)
# endregion MODULE_CONTRACT
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from core.internal import check_suite
from core.internal.check_suite import (
    CheckSpec,
    _apply_project_filter,
    _apply_xdist,
    _build_diff_steps,
    _diff_files,
    compute_fingerprint,
    list_checks,
    run_diagnostic,
    run_diff,
    run_gate,
    validate_manifest,
)

logger = __import__("logging").getLogger(__name__)

# ── Фикстуры ────────────────────────────────────────────────────────────────────


def _write_manifest(root: Path, checks: list[dict]) -> None:
    """Write a tmp check-suite manifest under root/core/."""
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "core" / "check-suite.yaml").write_text(yaml.safe_dump({"version": 1, "checks": checks}), encoding="utf-8")


def _git_init(root: Path) -> None:
    """Initialize a git repo in tmp_path (fixture-level environment setup)."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """tmp_path as a git repo (fingerprint/cache/diff fixtures)."""
    _git_init(tmp_path)
    return tmp_path


_PASS_CMD = "python3 -c 'import sys; sys.exit(0)'"
_FAIL_CMD = "python3 -c 'import sys; sys.exit(1)'"
_NO_TESTS_CMD = "python3 -c 'import sys; sys.exit(5)'"


# ═══════════════════════════════════════════════════════════════
# Wave 1: валидация манифеста (schema v1)
# ═══════════════════════════════════════════════════════════════


# region Tests: validate_manifest


# 🧪 TRAP[TEST] · Wave 1 · schema v1: невалидный tier/timeout/cmds-покрытие → ошибка
# · Scenario: битый tier, timeout=0, id не kebab-case, cmds без покрытия gate-режима, дубль junit
# · Last fail: N/A (новый тест)
# · Remove if: schema v1 заменена (bump version)
def test_validate_manifest_rejects_invalid(tmp_path) -> None:
    """Невалидные записи манифеста дают ошибки валидации (tier/timeout/cmds/junit)."""
    bad = {
        "version": 1,
        "checks": [
            {"id": "Bad_ID", "tier": "unit", "timeout": 0, "cmd": "true"},
            {"id": "no-cmd", "tier": "static", "timeout": 10},
            {"id": "cmds-gap", "tier": "pytest", "timeout": 10, "gate_modes": ["full"], "cmds": {"fast": "pytest x"}},
            {
                "id": "junit-a",
                "tier": "pytest",
                "timeout": 10,
                "cmd": "pytest a",
                "gate_modes": ["full"],
                "junit": "tests/r.xml",
            },
            {
                "id": "junit-b",
                "tier": "pytest",
                "timeout": 10,
                "cmd": "pytest b",
                "gate_modes": ["full"],
                "junit": "tests/r.xml",
            },
        ],
    }
    errors = validate_manifest(bad)
    joined = "\n".join(errors)
    assert any("tier" in e for e in errors), f"tier-ошибка не детектирована: {joined}"
    assert any("timeout" in e for e in errors), f"timeout-ошибка не детектирована: {joined}"
    assert any("kebab" in e for e in errors), f"id-ошибка не детектирована: {joined}"
    assert any("cmd" in e and "cmds" in e for e in errors), f"cmds-покрытие не детектировано: {joined}"
    assert any("junit" in e for e in errors), f"junit-дубль не детектирован: {joined}"
    logger.critical("[IMP:9][test] validate_manifest: %d ошибок на невалидном манифесте", len(errors))


# 🧪 TRAP[TEST] · Wave 1 · schema v1: валидный манифест → 0 ошибок
# · Scenario: минимальный валидный набор (cmd + cmds-покрытие, kebab-ids, junit уникален)
# · Last fail: N/A
# · Remove if: validate_manifest удалена
def test_validate_manifest_accepts_valid() -> None:
    """Валидный манифест не даёт ошибок."""
    good = {
        "version": 1,
        "checks": [
            {"id": "pre-commit", "tier": "fix", "timeout": 120, "cmd": "make pre-commit-run", "gate_modes": ["fast"]},
            {
                "id": "gates",
                "tier": "pytest",
                "timeout": 180,
                "gate_modes": ["fast", "full"],
                "cmds": {"fast": "pytest a", "full": "pytest b"},
            },
        ],
    }
    assert validate_manifest(good) == []
    logger.critical("[IMP:9][test] validate_manifest: валидный манифест принят (0 ошибок)")


# endregion Tests: validate_manifest


# ═══════════════════════════════════════════════════════════════
# Wave 1: диагностический набор (list_checks)
# ═══════════════════════════════════════════════════════════════

# region Tests: list_checks


# 🧪 TRAP[TEST] · Wave 1 · REGRESSION (TRAP[BUG] 2026-08-02) · diagnostic резолвит cmds["fast"]
# · Scenario: чек с ТОЛЬКО cmds (gates/static_audit/predeploy) в диагностике → resolve_command(None)
# ·   == cmds["fast"] (иначе чек молча пропускался — ложный зелёный `make check`)
# · Last fail: 2026-08-02 — `make check` GREEN за 21s при пропущенных static_audit (3106 тестов)
# · Remove if: диагностика перестанет использовать fast-варианты
def test_resolve_command_diagnostic_falls_back_to_fast() -> None:
    """Диагностический контекст: cmds-only чек резолвит fast-вариант (регресс TRAP[BUG])."""
    spec = CheckSpec(
        id="static_audit",
        tier="pytest",
        timeout=300,
        cmds={
            "fast": "python3 -m core.internal.test_runner --marker static_audit",
            "full": "make test MARKER=static_audit",
        },
    )
    assert spec.resolve_command("full") == "make test MARKER=static_audit"
    assert spec.resolve_command("fast") == "python3 -m core.internal.test_runner --marker static_audit"
    # диагностика (None) — fast-вариант, НЕ None
    assert spec.resolve_command(None) == "python3 -m core.internal.test_runner --marker static_audit"
    logger.critical("[IMP:9][test] resolve_command(None) → cmds[fast] (регресс-гард молчаливого пропуска)")


# 🧪 TRAP[TEST] · Wave 1 · diagnostic-set: дыры входят, lint/smoke/component НЕ входят
# · Scenario: diagnostic список == манифестные diagnostic:true (check-manifests/ruff/gates-docker входят;
# ·   lint/check-file-lines/smoke/component/predeploy-docker исключены)
# · Last fail: 2026-08-02 — дыры AC-2 (check-manifests/ruff вне preflight)
# · Remove if: состав диагностического набора осознанно изменён
def test_diagnostic_list_includes_holes_excludes_heavy(tmp_path) -> None:
    """Diagnostic-набор: check-manifests/ruff/gates-docker входят; lint/smoke/component — нет."""
    _write_manifest(
        tmp_path,
        [
            {"id": "check-manifests", "tier": "static", "timeout": 60, "cmd": "make check-manifests"},
            {"id": "ruff-check", "tier": "static", "timeout": 60, "cmd": ".venv/bin/ruff check ."},
            {
                "id": "gates-docker",
                "tier": "pytest",
                "timeout": 180,
                "allow_no_tests": True,
                "cmd": "pytest tests/gates/ -m 'gate and requires_docker'",
            },
            {"id": "lint", "tier": "static", "timeout": 120, "cmd": "make lint", "diagnostic": False},
            {"id": "smoke", "tier": "pytest", "timeout": 600, "cmd": "make test MARKER=smoke", "diagnostic": False},
            {
                "id": "component",
                "tier": "pytest",
                "timeout": 600,
                "cmd": "make test MARKER=component",
                "diagnostic": False,
            },
        ],
    )
    manifest = check_suite.load_manifest(tmp_path)
    ids = [s.id for s in list_checks(manifest)]
    for hid in ("check-manifests", "ruff-check", "gates-docker"):
        assert hid in ids, f"дыра {hid} не в диагностическом наборе (AC-2)"
    for heavy in ("lint", "smoke", "component"):
        assert heavy not in ids, f"{heavy} не должен входить в диагностический набор"
    logger.critical("[IMP:9][test] diagnostic-set: %d чеков, дыры входят, heavy исключены", len(ids))


# 🧪 TRAP[TEST] · Wave 1 · diagnostic: порядок = канонический порядок манифеста
# · Scenario: list_checks(None) сохраняет порядок манифеста
# · Last fail: N/A
# · Remove if: порядок перестанет быть значимым (golden-паритет)
def test_list_checks_preserves_manifest_order() -> None:
    """Порядок diagnostic-набора = порядок манифеста."""
    manifest = {
        "version": 1,
        "checks": [
            {"id": "zeta", "tier": "static", "timeout": 10, "cmd": "true"},
            {"id": "alpha", "tier": "static", "timeout": 10, "cmd": "true"},
        ],
    }
    assert [s.id for s in list_checks(manifest)] == ["zeta", "alpha"]
    logger.critical("[IMP:9][test] list_checks: порядок манифеста сохранён")


# endregion Tests: list_checks


# ═══════════════════════════════════════════════════════════════
# Wave 2: fingerprint
# ═══════════════════════════════════════════════════════════════

# region Tests: fingerprint


# 🧪 TRAP[TEST] · Wave 3 · fingerprint: стабилен на неизменённом дереве
# · Scenario: git-repo с закоммиченным файлом → два вычисления ==
# · Last fail: N/A
# · Remove if: fingerprint-контракт изменён
def test_fingerprint_stable_on_unchanged_tree(git_repo: Path) -> None:
    """Fingerprint детерминирован на неизменённом дереве."""
    (git_repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=git_repo, check=True, capture_output=True)

    fp1 = compute_fingerprint(git_repo)
    fp2 = compute_fingerprint(git_repo)
    assert fp1 is not None and fp1 == fp2
    logger.critical("[IMP:9][test] fingerprint стабилен: %s", fp1[:16])


# 🧪 TRAP[TEST] · Wave 3 · fingerprint: меняется при правке файла
# · Scenario: правка tracked-файла → другой fingerprint
# · Last fail: N/A
# · Remove if: fingerprint-контракт изменён
def test_fingerprint_changes_on_file_edit(git_repo: Path) -> None:
    """Правка любого файла дерева → miss (инвалидация кэша)."""
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=git_repo, check=True, capture_output=True)
    fp1 = compute_fingerprint(git_repo)

    (git_repo / "a.txt").write_text("v2\n")
    fp2 = compute_fingerprint(git_repo)
    assert fp1 != fp2
    logger.critical("[IMP:9][test] fingerprint инвалидируется правкой файла")


# 🧪 TRAP[TEST] · Wave 3 · fingerprint: excluded-пути (report*.xml, .test_counter.json, .venv) не влияют
# · Scenario: untracked tests/report-x.xml + .test_counter.json + .venv/x → fingerprint неизменен
# · Last fail: N/A
# · Remove if: политика исключений fingerprint изменена
def test_fingerprint_ignores_excluded_paths(git_repo: Path) -> None:
    """Исключённые пути (report*.xml, .test_counter.json, .venv) не меняют fingerprint."""
    (git_repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=git_repo, check=True, capture_output=True)
    fp_before = compute_fingerprint(git_repo)

    (git_repo / "tests").mkdir(exist_ok=True)
    (git_repo / "tests" / "report-static.xml").write_text("<testsuites/>")
    (git_repo / ".test_counter.json").write_text('{"attempts": 3}')
    (git_repo / ".venv").mkdir(exist_ok=True)
    (git_repo / ".venv" / "x.txt").write_text("venv")

    fp_after = compute_fingerprint(git_repo)
    assert fp_before == fp_after
    logger.critical("[IMP:9][test] fingerprint игнорирует report*/test_counter/.venv (кэш не инвалидируется прогонами)")


# 🧪 TRAP[TEST] · Wave 3 · fingerprint: правка манифеста меняет fingerprint
# · Scenario: core/check-suite.yaml правка → другой fingerprint (инвалидация при bump/правке)
# · Last fail: N/A
# · Remove if: манифест перестанет входить в fingerprint
def test_fingerprint_changes_on_manifest_edit(git_repo: Path) -> None:
    """Правка манифеста (или bump version) инвалидирует fingerprint."""
    _write_manifest(git_repo, [{"id": "a", "tier": "static", "timeout": 10, "cmd": "true"}])
    fp1 = compute_fingerprint(git_repo)
    _write_manifest(git_repo, [{"id": "a", "tier": "static", "timeout": 20, "cmd": "true"}])
    fp2 = compute_fingerprint(git_repo)
    assert fp1 != fp2
    logger.critical("[IMP:9][test] fingerprint инвалидируется правкой манифеста")


# endregion Tests: fingerprint


# ═══════════════════════════════════════════════════════════════
# Wave 3: replay-кэш (run_diagnostic)
# ═══════════════════════════════════════════════════════════════

# region Tests: diagnostic cache replay


# 🧪 TRAP[TEST] · Wave 3 · replay: зелёный прогон реплеится при том же fingerprint
# · Scenario: 2× run_diagnostic(no_fix=True) на неизменённом дереве → 2-й содержит «replay», exit 0
# · Last fail: N/A
# · Remove if: кэш-механизм изменён
def test_diagnostic_replays_green_run(git_repo: Path, capsys) -> None:
    """Повторный прогон на неизменённом дереве реплеит зелёный отчёт (AC-3)."""
    _write_manifest(git_repo, [{"id": "ok", "tier": "static", "timeout": 30, "cmd": _PASS_CMD}])

    assert run_diagnostic(git_repo, no_fix=True, no_cache=False) == 0
    cache = git_repo / ".git" / "check-cache.json"
    assert cache.is_file(), "кэш должен быть записан после зелёного прогона"

    capsys.readouterr()  # очистить stdout 1-го прогона
    assert run_diagnostic(git_repo, no_fix=True, no_cache=False) == 0
    captured = capsys.readouterr()
    assert "replay" in captured.err.lower(), f"ожидался replay, got: {captured.err[-500:]}"
    logger.critical("[IMP:9][test] replay зелёного прогона сработал (AC-3)")


# 🧪 TRAP[TEST] · Wave 3 · replay: упавший прогон НЕ реплеится
# · Scenario: провал → кэш status=failed → повторный прогон ИСПОЛНЯЕТ чеки заново (без «replay»)
# · Last fail: N/A
# · Remove if: правило «failed never replayed» изменено
def test_diagnostic_never_replays_failed_run(git_repo: Path, capsys) -> None:
    """Упавший прогон никогда не реплеится как зелёный (AC-3)."""
    _write_manifest(git_repo, [{"id": "bad", "tier": "static", "timeout": 30, "cmd": _FAIL_CMD}])

    assert run_diagnostic(git_repo, no_fix=True, no_cache=False) == 1
    capsys.readouterr()
    assert run_diagnostic(git_repo, no_fix=True, no_cache=False) == 1
    captured = capsys.readouterr()
    assert "replay" not in captured.err.lower(), "упавший прогон не должен реплеиться"
    assert "FAILED CHECKS" in captured.out or "failed" in captured.out.lower()
    logger.critical("[IMP:9][test] упавший прогон исполняется заново (replay запрещён)")


# 🧪 TRAP[TEST] · Wave 3 · CHECK_CACHE=0: без чтения и записи кэша
# · Scenario: env CHECK_CACHE=0 → кэш-файл не создаётся; прогон честный
# · Last fail: N/A
# · Remove if: CHECK_CACHE удалён
def test_check_cache_zero_disables_cache(git_repo: Path, monkeypatch) -> None:
    """CHECK_CACHE=0 → полный прогон без чтения/записи кэша."""
    _write_manifest(git_repo, [{"id": "ok", "tier": "static", "timeout": 30, "cmd": _PASS_CMD}])
    monkeypatch.setenv("CHECK_CACHE", "0")

    assert run_diagnostic(git_repo, no_fix=True, no_cache=False) == 0
    assert not (git_repo / ".git" / "check-cache.json").exists(), "CHECK_CACHE=0 не должен писать кэш"
    logger.critical("[IMP:9][test] CHECK_CACHE=0: кэш не читается и не пишется")


# 🧪 TRAP[TEST] · Wave 3 · --no-cache: то же, что CHECK_CACHE=0 (без чтения)
# · Scenario: no_cache=True → кэш не создаётся даже после прогона
# · Last fail: N/A
# · Remove if: --no-cache удалён
def test_no_cache_flag_disables_write(git_repo: Path) -> None:
    """--no-cache: полный прогон, кэш не пишется."""
    _write_manifest(git_repo, [{"id": "ok", "tier": "static", "timeout": 30, "cmd": _PASS_CMD}])
    assert run_diagnostic(git_repo, no_fix=True, no_cache=True) == 0
    assert not (git_repo / ".git" / "check-cache.json").exists()
    logger.critical("[IMP:9][test] --no-cache: кэш не записан")


# endregion Tests: diagnostic cache replay


# ═══════════════════════════════════════════════════════════════
# Wave 2: gate-режимы
# ═══════════════════════════════════════════════════════════════

# region Tests: gate modes


# 🧪 TRAP[TEST] · Wave 2 · gate fast: fail-fast останавливает конвейер на первом блокирующем провале
# · Scenario: A(ok) → B(fail) → C(пишет файл) — C НЕ исполняется; exit 1
# · Last fail: N/A
# · Remove if: fail-fast семантика fast-режима изменена
def test_gate_fast_fail_fast_stops_pipeline(git_repo: Path) -> None:
    """fast: первый блокирующий провал стопит конвейер (C не исполняется)."""
    _write_manifest(
        git_repo,
        [
            {"id": "a", "tier": "pytest", "timeout": 30, "cmd": _PASS_CMD, "gate_modes": ["fast"]},
            {"id": "b", "tier": "pytest", "timeout": 30, "cmd": _FAIL_CMD, "gate_modes": ["fast"]},
            {
                "id": "c",
                "tier": "pytest",
                "timeout": 30,
                "cmd": 'python3 -c \'open("ran_c.txt", "w").write("x")\'',
                "gate_modes": ["fast"],
            },
        ],
    )
    assert run_gate(git_repo, "fast") == 1
    assert not (git_repo / "ran_c.txt").exists(), "fail-fast: шаг C не должен исполняться"
    logger.critical("[IMP:9][test] gate fast: fail-fast остановил конвейер после B")


# 🧪 TRAP[TEST] · Wave 2 · non_blocking: провал не роняет gate и не стопит fast
# · Scenario: A(non_blocking fail) → B(ok) → exit 0
# · Last fail: N/A
# · Remove if: non_blocking семантика изменена
def test_gate_non_blocking_does_not_fail(git_repo: Path) -> None:
    """non_blocking: провал чека не роняет gate (check-file-lines прецедент)."""
    _write_manifest(
        git_repo,
        [
            {
                "id": "a",
                "tier": "static",
                "timeout": 30,
                "cmd": _FAIL_CMD,
                "gate_modes": ["fast"],
                "non_blocking": True,
            },
            {"id": "b", "tier": "static", "timeout": 30, "cmd": _PASS_CMD, "gate_modes": ["fast"]},
        ],
    )
    assert run_gate(git_repo, "fast") == 0
    logger.critical("[IMP:9][test] gate: non_blocking провал не роняет gate (exit 0)")


# 🧪 TRAP[TEST] · Wave 2 · allow_no_tests: pytest rc=5 → PASS
# · Scenario: exit 5 с allow_no_tests → gate зелёный; без allow_no_tests → провал
# · Last fail: 2026-08-02 — gates-docker пуст (rc=5) ронял бы gate
# · Remove if: allow_no_tests семантика изменена
def test_gate_allow_no_tests_rc5_passes(git_repo: Path) -> None:
    """allow_no_tests: pytest exit 5 (0 тестов) → PASS, а не провал."""
    _write_manifest(
        git_repo,
        [
            {
                "id": "docker-gates",
                "tier": "pytest",
                "timeout": 30,
                "cmd": _NO_TESTS_CMD,
                "gate_modes": ["fast"],
                "allow_no_tests": True,
            },
        ],
    )
    assert run_gate(git_repo, "fast") == 0

    _write_manifest(
        git_repo,
        [
            {"id": "strict", "tier": "pytest", "timeout": 30, "cmd": _NO_TESTS_CMD, "gate_modes": ["fast"]},
        ],
    )
    assert run_gate(git_repo, "fast") == 1
    logger.critical("[IMP:9][test] gate: allow_no_tests rc=5 → PASS; без флага → FAIL")


# 🧪 TRAP[TEST] · Wave 2 · junit-merge: full-режим мержит отчёты через merge_junit
# · Scenario: чек создаёт tests/report-a.xml → после full-прогона tests/report.xml существует с tests=1
# · Last fail: N/A
# · Remove if: junit-merge перенесён в другой механизм
def test_gate_full_merges_junit_reports(git_repo: Path) -> None:
    """full: junit-отчёты чеков мержатся в tests/report.xml (DevPlan §3.6)."""
    junit_writer = (
        'python3 -c \'import pathlib; p=pathlib.Path("tests/report-a.xml"); p.parent.mkdir(parents=True, exist_ok=True); '
        'p.write_text("<testsuites><testsuite tests=\\"1\\" failures=\\"0\\" errors=\\"0\\" skipped=\\"0\\" time=\\"0\\">'
        '<testcase name=\\"t1\\"/></testsuite></testsuites>")\''
    )
    _write_manifest(
        git_repo,
        [
            {
                "id": "contract",
                "tier": "pytest",
                "timeout": 60,
                "cmd": junit_writer,
                "gate_modes": ["full"],
                "junit": "tests/report-a.xml",
            },
        ],
    )
    assert run_gate(git_repo, "full") == 0
    merged = git_repo / "tests" / "report.xml"
    assert merged.is_file(), "full-режим должен создать tests/report.xml"
    assert 'tests="1"' in merged.read_text(), "merged отчёт должен содержать 1 тест"
    logger.critical("[IMP:9][test] gate full: junit-merge создал tests/report.xml (tests=1)")


# 🧪 TRAP[TEST] · Wave 2 · PROJECT → -k для project_filter (прямые pytest-команды)
# · Scenario: _apply_project_filter("pytest tests/ -m x", "myproj") → "-k 'myproj'"; make-команды не трогаются
# · Last fail: N/A
# · Remove if: project_filter семантика изменена
def test_project_filter_appends_k() -> None:
    """PROJECT → -k только для прямых pytest-команд (паритет ci.mk)."""
    cmd = "pytest tests/ -m 'predeploy and not requires_docker' --tb=short"
    filtered = _apply_project_filter(cmd, "my-project")
    assert "-k my-project" in filtered or "-k 'my-project'" in filtered

    make_cmd = "make test MARKER=predeploy"
    assert _apply_project_filter(make_cmd, "my-project") == make_cmd
    assert _apply_project_filter(cmd, None) == cmd
    logger.critical("[IMP:9][test] PROJECT: -k добавлен только к прямой pytest-команде")


# 🧪 TRAP[TEST] · Wave 2 · gate mode composition: ci-docker = только predeploy-docker/smoke/component
# · Scenario: чек с gate_modes [ci-docker] → в ci-docker-наборе; без режима → нет
# · Last fail: N/A
# · Remove if: состав ci-docker изменён
def test_gate_mode_composition() -> None:
    """Состав шагов по gate_modes: ci-docker не включает fast-чеки."""
    manifest = {
        "version": 1,
        "checks": [
            {"id": "only-docker", "tier": "pytest", "timeout": 10, "cmd": "true", "gate_modes": ["ci-docker"]},
            {"id": "only-fast", "tier": "pytest", "timeout": 10, "cmd": "true", "gate_modes": ["fast"]},
            {"id": "diag-only", "tier": "static", "timeout": 10, "cmd": "true"},
        ],
    }
    docker_ids = [s.id for s in list_checks(manifest, gate_mode="ci-docker")]
    assert docker_ids == ["only-docker"]
    assert "diag-only" not in docker_ids
    logger.critical("[IMP:9][test] gate composition: ci-docker == [only-docker]")


# endregion Tests: gate modes


# ═══════════════════════════════════════════════════════════════
# Wave 4: diff-скоуп
# ═══════════════════════════════════════════════════════════════

# region Tests: diff scope


# 🧪 TRAP[TEST] · Wave 4 · diff scope: изменённый .py → ruff + pre-commit
# · Scenario: _build_diff_steps([a.py]) → 2 шага (pre-commit, ruff); test-файл → ruff+pytest;
# ·   README → только pre-commit
# · Last fail: N/A
# · Remove if: diff-скоуп состав изменён
def test_diff_steps_scope() -> None:
    """Diff-скоуп: .py → ruff+pre-commit; test-файл (это .py) → ruff+pytest; README → pre-commit."""
    root = Path("/tmp/placeholder")  # пути не читаются — только имена шагов
    steps_py = _build_diff_steps(root, ["src/foo.py"])
    assert [s[0] for s in steps_py] == ["pre-commit (diff)", "ruff check (diff)"]

    steps_test = _build_diff_steps(root, ["tests/unit/test_foo.py"])
    names = [s[0] for s in steps_test]
    # test-файл — тоже .py → ruff применяется (DevPlan §3.5 п.2: «изменённые .py» без исключений)
    assert names == ["pre-commit (diff)", "ruff check (diff)", "pytest (diff)"], names

    steps_readme = _build_diff_steps(root, ["README.md"])
    assert [s[0] for s in steps_readme] == ["pre-commit (diff)"]

    assert _build_diff_steps(root, []) == []
    logger.critical("[IMP:9][test] diff-скоуп: .py→ruff+pre-commit, test→ruff+pytest, README→pre-commit, пусто→[]")


# 🧪 TRAP[TEST] · Wave 4 · diff files: tracked-правка + untracked включаются
# · Scenario: git_repo: правка tracked + новый untracked файл → оба в diff-наборе
# · Last fail: N/A
# · Remove if: diff-детекция изменена
def test_diff_files_detects_tracked_and_untracked(git_repo: Path) -> None:
    """_diff_files: tracked-правка (vs HEAD) + untracked не-ignored файлы."""
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=git_repo, check=True, capture_output=True)

    (git_repo / "a.txt").write_text("v2\n")
    (git_repo / "new.txt").write_text("new\n")

    changed = _diff_files(git_repo)
    assert changed is not None
    assert "a.txt" in changed and "new.txt" in changed
    logger.critical("[IMP:9][test] diff-files: tracked-правка + untracked детектированы (%d)", len(changed))


# 🧪 TRAP[TEST] · Wave 4 · run_diff: пустой diff → exit 0 («nothing to diff»)
# · Scenario: чистый git_repo → run_diff == 0
# · Last fail: N/A
# · Remove if: run_diff удалён
def test_run_diff_empty_exit_zero(git_repo: Path, capsys) -> None:
    """Пустой diff → exit 0 без исполнения команд."""
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=git_repo, check=True, capture_output=True)

    assert run_diff(git_repo) == 0
    captured = capsys.readouterr()
    assert "Nothing to diff" in captured.err
    logger.critical("[IMP:9][test] run_diff: пустой diff → exit 0")


# endregion Tests: diff scope


# ═══════════════════════════════════════════════════════════════
# Wave 1: deprecated-фасад preflight.py (старые флаги)
# ═══════════════════════════════════════════════════════════════

# region Tests: preflight facade


# 🧪 TRAP[TEST] · Wave 1 · фасад: старые флаги маппятся на check_suite.run_diagnostic
# · Scenario: --skip-fix → no_fix=True; --json → json_output=True; --workers 2 → workers=2
# · Last fail: N/A
# · Remove if: preflight.py удалён (после полной миграции на check)
def test_preflight_facade_maps_legacy_flags(monkeypatch, capsys) -> None:
    """preflight.py — тонкий фасад: старые флаги работают (compose-safe-up прецедент)."""
    import core.internal.preflight as facade

    captured: dict = {}

    def fake_run_diagnostic(root, no_fix=False, json_output=False, workers=6, no_cache=False, verbose=False):
        captured.update(
            root=root, no_fix=no_fix, json_output=json_output, workers=workers, no_cache=no_cache, verbose=verbose
        )
        return 0

    monkeypatch.setattr("core.internal.preflight.run_diagnostic", fake_run_diagnostic)
    monkeypatch.setattr(sys, "argv", ["preflight", "--skip-fix", "--json", "--workers", "2"])

    assert facade.main() == 0
    assert captured["no_fix"] is True
    assert captured["json_output"] is True
    assert captured["workers"] == 2
    assert captured["no_cache"] is False
    assert "DEPRECATED" in capsys.readouterr().err
    logger.critical("[IMP:9][test] preflight-фасад: --skip-fix/--json/--workers → run_diagnostic")


# endregion Tests: preflight facade


# ═══════════════════════════════════════════════════════════════
# Wave 2: xdist-применение executor'ом (прямые pytest-команды)
# ═══════════════════════════════════════════════════════════════

# region Tests: xdist application


# 🧪 TRAP[TEST] · Wave 2 · xdist: -n auto вставляется в прямые pytest-команды при spec.xdist
# · Scenario: _apply_xdist("pytest tests/gates/ -m x", xdist=True) → "-n auto" после pytest;
# ·   make-команды и xdist=False не трогаются; TEST_NO_XDIST=1 отключает
# · Last fail: N/A
# · Remove if: xdist-политика executor'а изменена
def test_apply_xdist_inserts_n_auto(tmp_path, monkeypatch) -> None:
    """Executor добавляет -n auto только прямым pytest-командам (make/test_runner — сами)."""
    monkeypatch.setattr(check_suite, "_has_xdist", lambda python_path: True)
    spec = CheckSpec(id="gates", tier="pytest", timeout=10, xdist=True)

    out = _apply_xdist("pytest tests/gates/ -m 'gate and not requires_docker'", spec, tmp_path)
    assert "pytest -n auto tests/gates/" in out, f"-n auto не вставлен: {out}"

    make_out = _apply_xdist("make test MARKER=contract", spec, tmp_path)
    assert make_out == "make test MARKER=contract", "make-команды не должны получать -n"

    no_xdist_spec = CheckSpec(id="pd", tier="pytest", timeout=10, xdist=False)
    assert _apply_xdist("pytest tests/ -m x", no_xdist_spec, tmp_path) == "pytest tests/ -m x"

    monkeypatch.setenv("TEST_NO_XDIST", "1")
    assert _apply_xdist("pytest tests/ -m x", spec, tmp_path) == "pytest tests/ -m x"
    logger.critical("[IMP:9][test] _apply_xdist: -n auto только прямые pytest-команды; TEST_NO_XDIST отключает")


# endregion Tests: xdist application
