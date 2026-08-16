# GREP_SUMMARY: gate-contract test-marker-contract make subprocess contract-tests-gate gate4 anti-drift CI MARKER
# STRUCTURE: ▶ test_contract_target_exists(grep Makefile for test:) → ◇ test_contract_test_files_exist(glob test_contract_*.py → check markers) → ⊕ structural integrity → ⎋ PASS/FAIL
# region MODULE_CONTRACT [DOMAIN(TESTING):4; CONCEPT(GATE):4; TECH(MAKE):3]
## @modulecontract
## @purpose — Gate #4: contract-test-gate — verify contract test files exist and
##            are properly marked with @pytest.mark.contract. Structural checks only
##            (no subprocess invocation of contract tests).
## @scope — Gate #4 of 7 anti-drift CI gates (TASK-5G4 Phase 5)
##
##          1. test_contract_test_files_exist — check contract test files exist and have markers
##          2. test_contract_target_exists — verify SoT-манифест core/check-suite.yaml
##             содержит чек contract (DevPlan 165: MARKER=contract-модель Makefile заменена)
## @invariants
##
##   - PROJECT_ROOT = parents[2] from this file (tests/gates/ → tests/ → project root)
##   - Contract test files must exist under tests/test_contract_*.py
##   - Each contract test file must have at least one @pytest.mark.contract decorator
##   - core/check-suite.yaml must contain id: contract (structural check, SoT)
## @rationale
##   Q: Why structural check instead of subprocess.run?
##   A: G1.4 requirement: `make gate` must invoke contract tests exactly once.
##      Previously, test_contract_tests_pass ran the contract suite via
##      subprocess, causing duplicate execution when `make gate` already triggered
##      contract tests. The structural check verifies test file integrity without
##      redundant execution.
##   Q: Why check the SoT manifest instead of a Makefile target (DevPlan 165)?
##   A: MARKER-модель `make test MARKER=...` удалена; единственный источник состава
##      прогонов — core/check-suite.yaml. Проверка манифеста даёт чёткий fail
##      («contract отсутствует в манифесте») вместо молчаливого неисполнения.
## @changes — LAST_CHANGE: 2026-07-10 | G1.4: replaced subprocess.run with structural check
## @modulemap
##   - test_contract_test_files_exist [Weight:3] — structural check: contract test files exist and have marker
##   - test_contract_target_exists [Weight:2] — grep Makefile for test: target
## @usecases
##   - Developer runs `make gate` → contract-test-gate → structural check → PASS/FAIL
##   - CI pipeline → gate step → contract-test-gate → blocks merge on structural integrity fail
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import pathlib
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MAKEFILE_PATH = _PROJECT_ROOT / "Makefile"

_logger = logging.getLogger(__name__)


# region FUNC_test_contract_target_exists
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · Regression · make check MARKER=contract диспатчится в --only
# · Scenario: repair.mk теряет проброс MARKER → contract-сьют перестаёт быть
# ·   запускаемым одиночно → RED (DevPlan 165: test-таргетная модель удалена)
# · Last fail: N/A (преемник test_contract_target_exists — искал удалённый target test:)
# · Remove if: make check MARKER-механизм заменяется
def test_contract_target_exists(caplog) -> None:
    """Verify canonical dispatch: make check MARKER=contract → check_suite --only.

    ## @purpose — Precondition gate (DevPlan 165): параметризованный запуск
    ##            contract-сьюта идёт через make check MARKER=<suite> →
    ##            check_suite --only. Структурная проверка: repair.mk пробрасывает
    ##            MARKER, а SoT-манифест содержит чек contract. Предотвращает
    ##            silent no-op при сломанной цепочке диспатча.
    ## @io — ⎋ None. Assert: цепочка make check MARKER → --only существует.
    ## @complexity — O(N) где N = строк makefiles + чеков манифеста
    """

    # region BLOCK_ReadRepairMk
    repair_mk = _PROJECT_ROOT / "makefiles" / "repair.mk"
    if not repair_mk.is_file():
        pytest.fail(f"[IMP:9][test_contract_target_exists] makefiles/repair.mk not found: {repair_mk}")
    repair_content = repair_mk.read_text()
    _logger.info("[IMP:7][test_contract_target_exists][BLOCK_ReadRepairMk] Read %s", repair_mk)
    # endregion BLOCK_ReadRepairMk

    # region BLOCK_CheckMarkerPassthrough
    # MARKER=<suite> → --only $(MARKER) (одна строка eval-проброса в check-таргете)
    marker_passthrough = "--only $(MARKER)" in repair_content
    if not marker_passthrough:
        pytest.fail(
            "[IMP:9][test_contract_target_exists][BLOCK_CheckMarkerPassthrough] "
            "repair.mk check-таргет должен пробрасывать MARKER → --only $(MARKER)"
        )
    _logger.info("[IMP:9][test_contract_target_exists] OK: make check MARKER=<suite> → check_suite --only")
    # endregion BLOCK_CheckMarkerPassthrough


# endregion FUNC_test_contract_target_exists


# region FUNC_test_contract_test_files_exist
@pytest.mark.gate
@ldd_trajectory
def test_contract_test_files_exist(caplog) -> None:
    """Verify contract test files exist and contain @pytest.mark.contract markers.

    ## @purpose — Structural check replacing the previous subprocess-based gate.
    ##            Instead of running the contract suite directly (which caused
    ##            duplicate contract test execution in `make gate`), this test
    ##            verifies that contract test files exist, each contains at least
    ##            one test function with @pytest.mark.contract, and the SoT-манифест
    ##            core/check-suite.yaml содержит чек contract (DevPlan 165).
    ## @io — ⎋ None. Assert: contract test files exist and are properly marked.
    ## @complexity — O(N * M) where N = test files, M = lines per file
    """

    _logger.info("[IMP:8][test_contract_test_files_exist] === Contract test files structural check ===")

    # 1. Find contract test files — рекурсивно (DevPlan 172 W3.1: contract-тесты
    # переехали из корня tests/ в tests/unit/; корень = только Docker-component)
    contract_pattern = str(_PROJECT_ROOT / "tests" / "**" / "test_contract_*.py")
    contract_files = sorted(Path(_PROJECT_ROOT / "tests").rglob("test_contract_*.py"))
    _logger.info("[IMP:8][test_contract_test_files_exist] Found %d contract test file(s)", len(contract_files))

    errors: list[str] = []

    if not contract_files:
        errors.append(f"No contract test files found matching pattern: {contract_pattern}")

    # 2. Verify each file has @pytest.mark.contract
    for cf in contract_files:
        rel_cf = os.path.relpath(cf, str(_PROJECT_ROOT))
        _logger.info("[IMP:7][test_contract_test_files_exist] Checking: %s", rel_cf)
        with pathlib.Path(cf).open(encoding="utf-8") as f:
            content = f.read()
        # Check for @pytest.mark.contract decorator
        if "@pytest.mark.contract" not in content and 'marker="contract"' not in content:
            errors.append(f"Contract test file '{rel_cf}' does not contain @pytest.mark.contract marker")
            _logger.warning("[IMP:7][test_contract_test_files_exist] MISSING marker: %s", rel_cf)
        else:
            _logger.info("[IMP:9][test_contract_test_files_exist] OK: %s has contract marker", rel_cf)

    # 3. Verify SoT-манифест содержит чек contract (DevPlan 165: MARKER-модель удалена)
    manifest_path = _PROJECT_ROOT / "core" / "check-suite.yaml"
    if manifest_path.is_file():
        import yaml

        with manifest_path.open(encoding="utf-8") as mf:
            manifest = yaml.safe_load(mf) or {}
        contract_ids = [c.get("id") for c in manifest.get("checks", []) if isinstance(c, dict)]
        if "contract" in contract_ids:
            _logger.info("[IMP:9][test_contract_test_files_exist] SoT-манифест содержит чек contract")
        else:
            errors.append("core/check-suite.yaml не содержит чек contract")
    else:
        errors.append(f"Manifest not found: {manifest_path}")

    assert not errors, "\n".join(errors)
    _logger.info(
        "[IMP:9][test_contract_test_files_exist] ALL PASS — %d contract test file(s) verified", len(contract_files)
    )


# endregion FUNC_test_contract_test_files_exist
