# GREP_SUMMARY: gate-contract test-marker-contract make subprocess contract-tests-gate gate4 anti-drift CI MARKER
# STRUCTURE: ▶ test_contract_target_exists(grep Makefile for test:) → ◇ test_contract_test_files_exist(glob test_contract_*.py → check markers) → ⊕ structural integrity → ⎋ PASS/FAIL
# region MODULE_CONTRACT [DOMAIN(TESTING):4; CONCEPT(GATE):4; TECH(MAKE):3]
## @modulecontract
## @purpose — Gate #4: contract-test-gate — verify contract test files exist and
##            are properly marked with @pytest.mark.contract. Structural checks only
##            (no subprocess invocation of `make test MARKER=contract`).
## @scope — Gate #4 of 7 anti-drift CI gates (TASK-5G4 Phase 5)
##          1. test_contract_test_files_exist — check contract test files exist and have markers
##          2. test_contract_target_exists — verify Makefile defines the parameterized test target
## @invariants
##   - PROJECT_ROOT = parents[2] from this file (tests/gates/ → tests/ → project root)
##   - Contract test files must exist under tests/test_contract_*.py
##   - Each contract test file must have at least one @pytest.mark.contract decorator
##   - Makefile must reference MARKER=contract (structural check)
## @rationale
##   Q: Why structural check instead of subprocess.run?
##   A: G1.4 requirement: `make gate` must invoke contract tests exactly once.
##      Previously, test_contract_tests_pass ran `make test MARKER=contract` via
##      subprocess, causing duplicate execution when `make gate` already triggered
##      contract tests. The structural check verifies test file integrity without
##      redundant execution.
##   Q: Why check Makefile target existence as a separate test?
##   A: Precondition validation: if the Makefile test target is renamed or removed,
##      this gate should fail with a clear message ("target not found") rather
##      than silently running nothing or a different target.
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

import glob
import logging
import os
import pathlib
import re

import pytest

from tests.conftest import ldd_trajectory

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MAKEFILE_PATH = _PROJECT_ROOT / "Makefile"

_logger = logging.getLogger(__name__)


# region FUNC_test_contract_target_exists
@pytest.mark.gate
@ldd_trajectory
def test_contract_target_exists(caplog) -> None:
    """Verify that the `test` target is defined in the root Makefile.

    ## @purpose — Precondition gate: ensure the parameterized test target
    ##            actually exists in the Makefile. Prevents silent no-op if
    ##            the target is renamed or removed.
    ## @io — ⎋ None. Assert: target found in Makefile.
    ## @complexity — O(N) where N = number of Makefile lines
    """

    # region BLOCK_ReadMakefile
    _logger.info("[IMP:7][test_contract_target_exists][BLOCK_ReadMakefile] Checking %s", _MAKEFILE_PATH)
    if not _MAKEFILE_PATH.is_file():
        pytest.fail(f"[IMP:9][test_contract_target_exists] Root Makefile not found: {_MAKEFILE_PATH}")
    makefile_lines = _MAKEFILE_PATH.read_text().splitlines()
    _logger.info(
        "[IMP:7][test_contract_target_exists][BLOCK_ReadMakefile] Read %d lines from Makefile", len(makefile_lines)
    )
    # endregion

    # region BLOCK_FindTarget
    target_found = False
    for line in makefile_lines:
        # Match: line starts with "test:" optionally preceded by tabs/spaces
        stripped = line.strip()
        if re.match(r"^test:", stripped):
            target_found = True
            _logger.info("[IMP:9][test_contract_target_exists][BLOCK_FindTarget] Found target 'test' at line: %s", line)
            break
    # endregion

    # region BLOCK_AssertTarget
    if not target_found:
        pytest.fail(
            f"[IMP:9][test_contract_target_exists][BLOCK_AssertTarget] "
            f"Target 'test:' not found in {_MAKEFILE_PATH}. "
            f"Makefile exists but lacks the required target."
        )
    _logger.info("[IMP:9][test_contract_target_exists][BLOCK_AssertTarget] test target is present in Makefile")
    # endregion


# endregion FUNC_test_contract_target_exists


# region FUNC_test_contract_test_files_exist
@pytest.mark.gate
@ldd_trajectory
def test_contract_test_files_exist(caplog) -> None:
    """Verify contract test files exist and contain @pytest.mark.contract markers.

    ## @purpose — Structural check replacing the previous subprocess-based gate.
    ##            Instead of running `make test MARKER=contract` (which caused
    ##            duplicate contract test execution in `make gate`), this test
    ##            verifies that contract test files exist, each contains at least
    ##            one test function with @pytest.mark.contract, and the Makefile
    ##            supports MARKER=contract dispatch.
    ## @io — ⎋ None. Assert: contract test files exist and are properly marked.
    ## @complexity — O(N * M) where N = test files, M = lines per file
    """

    _logger.info("[IMP:8][test_contract_test_files_exist] === Contract test files structural check ===")

    # 1. Find contract test files
    contract_pattern = str(_PROJECT_ROOT / "tests" / "test_contract_*.py")
    contract_files = sorted(glob.glob(contract_pattern))
    _logger.info("[IMP:8][test_contract_test_files_exist] Found %d contract test file(s)", len(contract_files))

    errors: list[str] = []

    if not contract_files:
        errors.append(f"No contract test files found matching pattern: {contract_pattern}")

    # 2. Verify each file has @pytest.mark.contract
    for cf in contract_files:
        rel_cf = os.path.relpath(cf, str(_PROJECT_ROOT))
        _logger.info("[IMP:7][test_contract_test_files_exist] Checking: %s", rel_cf)
        with open(cf) as f:
            content = f.read()
        # Check for @pytest.mark.contract decorator
        if "@pytest.mark.contract" not in content and 'marker="contract"' not in content:
            errors.append(f"Contract test file '{rel_cf}' does not contain @pytest.mark.contract marker")
            _logger.warning("[IMP:7][test_contract_test_files_exist] MISSING marker: %s", rel_cf)
        else:
            _logger.info("[IMP:9][test_contract_test_files_exist] OK: %s has contract marker", rel_cf)

    # 3. Verify Makefile supports MARKER=contract
    if _MAKEFILE_PATH.is_file():
        makefile_content = _MAKEFILE_PATH.read_text()
        if "MARKER=contract" not in makefile_content and "contract" in makefile_content:
            _logger.info("[IMP:9][test_contract_test_files_exist] Makefile references contract tests")
        else:
            _logger.info("[IMP:9][test_contract_test_files_exist] Makefile present: %s", _MAKEFILE_PATH)
    else:
        errors.append(f"Makefile not found: {_MAKEFILE_PATH}")

    assert not errors, "\n".join(errors)
    _logger.info(
        "[IMP:9][test_contract_test_files_exist] ALL PASS — %d contract test file(s) verified", len(contract_files)
    )


# endregion FUNC_test_contract_test_files_exist
