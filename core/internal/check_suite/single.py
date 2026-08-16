"""
# GREP_SUMMARY: check-suite, single, run-single, test-file, run-test-file, only-id, test_runner
# STRUCTURE: ▶ run_single ┌id→manifest→validate→parse→resolve(None)┐ → run_pytest_check ┌xdist+docker-lock+allow_no_tests┐ → format_report → ⎋ 0|1|2 ▶ run_test_file ┌test_runner --test-file┐ → cs.run_cmd → ⎋ 0|1
# region MODULE_CONTRACT
## @purpose  Точечные режимы пакета check_suite (DevPlan 170 W3 — извлечено из монолита
##           core/internal/check_suite.py): `--only <id>` (run_single) и
##           `--test-file <path>` (run_test_file, через test_runner compact-вывод).
## @scope    core/internal/check_suite/single.py — stdlib-only. Потребитель: __init__.py (CLI run).
## @invariants
##   - run_single: команда резолвится как в диагностике (cmds[fast] фолбэк); xdist применяется;
##     allow_no_tests (exit 5) → PASS; docker-чеки под процессным локом; PYTEST_NO_ESCALATION=1
##   - run_single: неизвестный id → exit 2 (ошибка использования)
##   - run_test_file: PYTEST_NO_ESCALATION=1; timeout = внутренний test_runner + запас executor'а;
##     вывод test_runner пробрасывается полностью; exit 0/1
## @rationale Выделение single/test-file режимов из монолита (research-A §1); pytest-цикл
##            дедуплицирован в runner.run_pytest_check; monkeypatch-контракт (run_cmd,
##            load_manifest) через пакетную атрибуцию — DI-HYG тестов.
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package); 170 private-imports:
##           приватные имена переименованы в публичные (U-07)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
from pathlib import Path

from core.internal import check_suite as cs
from core.internal.check_suite.manifest import parse_checks, validate_manifest
from core.internal.check_suite.report import format_report
from core.internal.check_suite.runner import run_pytest_check

logger = logging.getLogger(__name__)


# region RUN_SINGLE_AND_TEST_FILE


# region FUNC_run_single
## @purpose  `--only \<id\>` (DevPlan 165 W2): запуск ОДНОГО чека по id из манифеста.
##           Явное указание id обходит фильтры diagnostic/gate_modes (например,
##           integration — diagnostic: false, без gate_modes). Без кэша, без fix-фазы.
## @io       ⇥ root: Path, check_id: str → int (0 зелёный, 1 провал, 2 неизвестный id)
## @complexity O(C + t) где C = чеки манифеста, t = время исполнения чека
## @invariants
##   - Команда резолвится как в диагностике (cmds[fast] фолбэк); xdist применяется
##   - allow_no_tests (exit 5) → PASS; docker-чеки под процессным локом
##   - PYTEST_NO_ESCALATION=1 (паритет остальных режимов)
def run_single(root: Path, check_id: str) -> int:
    """Run a single check by manifest id (explicit override of diagnostic/gate filters)."""
    manifest = cs.load_manifest(root)  # late-binding: DI-HYG
    errors = validate_manifest(manifest)
    if errors:
        print(f"[IMP:10][check] Manifest invalid ({len(errors)} error(s)):\n" + "\n".join(f"  - {e}" for e in errors))
        return 2

    specs = parse_checks(manifest)
    spec = next((s for s in specs if s.id == check_id), None)
    if spec is None:
        known = ", ".join(s.id for s in specs)
        print(f"[IMP:9][check] ERROR: unknown check id {check_id!r}. Known: {known}", file=sys.stderr)
        return 2

    cmd_str = spec.resolve_command(None)
    if not cmd_str:
        print(f"[IMP:9][check] {spec.id}: команда не найдена (нет cmd/cmds)", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.setdefault("PYTEST_NO_ESCALATION", "1")
    print(f"[IMP:7][check] single: {spec.id} (timeout={spec.timeout}s, xdist={spec.xdist})...", file=sys.stderr)
    r = run_pytest_check(spec, cmd_str, spec.timeout, env, root, log_tag="check")

    report_str, report_dict = format_report([r], r.duration_ms)
    print(report_str)
    return 0 if report_dict["status"] == "green" else 1


# endregion FUNC_run_single


# region FUNC_run_test_file
## @purpose  `--test-file \<path\>` (DevPlan 165 W2): pytest одного тест-файла через
##           test_runner (compact-вывод <100 строк). Без кэша, без fix-фазы.
## @io       ⇥ root: Path, test_file: str → int (0/1)
## @complexity O(t)
## @invariants
##   - PYTEST_NO_ESCALATION=1; timeout = внутренний test_runner + запас executor'а
##   - Вывод test_runner пробрасывается полностью (stdout/stderr чека)
def run_test_file(root: Path, test_file: str) -> int:
    """Run pytest on a single test file via test_runner (compact agent-oriented output)."""
    env = os.environ.copy()
    env.setdefault("PYTEST_NO_ESCALATION", "1")
    cmd_str = f"{shlex.quote(sys.executable)} -m core.internal.test_runner --test-file {shlex.quote(test_file)}"
    print(f"[IMP:7][check] test-file: {test_file} (test_runner, compact)...", file=sys.stderr)
    r = cs.run_cmd(cmd_str, 1900, env, root)  # late-binding: DI-HYG
    print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    if r.passed:
        print(f"[IMP:9][check] test-file {test_file}: PASS ({r.duration_ms:.0f} ms)", file=sys.stderr)
    else:
        print(f"[IMP:9][check] test-file {test_file}: FAIL (exit {r.exit_code})", file=sys.stderr)
    return 0 if r.passed else 1


# endregion FUNC_run_test_file

# endregion RUN_SINGLE_AND_TEST_FILE
