# GREP_SUMMARY: gate no-simulators AST-scan Simulator class-definition anti-drift T2.1 anti-regression
# STRUCTURE: ▶ glob tests/**/*.py → ◇ ast.parse → ◇ walk for ClassDef with "Simulator" in name → ⟦assert 0 violations⟧
# region MODULE_CONTRACT
## @purpose — Gate test: verify NO Simulator class definitions exist anywhere in tests/.
##            Uses Python AST to parse every .py file in tests/ and check for class
##            definitions whose name contains "Simulator". This is the canonical
##            anti-regression test for T2.1 (AcmeSimulator removal).
## @scope — Scans all Python files under tests/ recursively via pathlib.rglob("*.py").
##          Uses AST (not regex) to detect actual class definitions, avoiding false
##          positives from string literals or comments containing "Simulator".
## @invariants
##   - No file under tests/ may define a class with "Simulator" in the name
##   - Uses AST ClassDef nodes — not affected by string/comment false positives
##   - FAIL with file path and class name for each violation
## @rationale — Prevents regression: if a developer re-introduces a Simulator class
##              (e.g., for debugging), this gate catches it at commit time. Complements
##              the regex-based test_no_simulator_code in test_tls_wildcard.py.
# endregion MODULE_CONTRACT

import ast
import logging
import pathlib

import pytest

from tests.conftest import ldd_trajectory

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_TESTS_DIR: pathlib.Path = _PROJECT_ROOT / "tests"

logger = logging.getLogger(__name__)


def _scan_file_for_simulator_classes(filepath: pathlib.Path) -> list[tuple[str, int, str]]:
    """Scan a single Python file for Simulator class definitions using AST.

    ## @purpose — Parse the AST of a Python file and find ClassDef nodes whose
    ##            name contains 'Simulator'. This detects actual class definitions,
    ##            NOT string literals or comments mentioning Simulator.
    ## @io — ⇥ filepath: Path → ⎋ list of (filename, lineno, class_name) tuples
    ## @complexity — O(N) where N = AST nodes in file
    """
    violations: list[tuple[str, int, str]] = []
    try:
        with open(filepath) as f:
            tree = ast.parse(f.read())
    except SyntaxError:
        logger.warning("[IMP:4][_scan_file] Syntax error parsing %s", filepath.name)
        return violations

    violations.extend(
        (filepath.name, node.lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and "Simulator" in node.name
    )

    return violations


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-10 · gate/no-simulators · T2.1 anti-regression
def test_no_simulator_classes_in_test_suite(caplog) -> None:
    """Verify no test file defines a class with 'Simulator' in its name.

    ## @purpose — T2.1 anti-regression gate: scans all tests/**/*.py files using
    ##            AST for ClassDef nodes containing 'Simulator' in the class name.
    ##            If any exist, the test fails with the exact file and class name.
    ## @io — ⎋ None (assert side-effect via pytest.fail on violations)
    ## @complexity — O(F * N) where F = test files, N = AST nodes per file
    """
    logger.info(
        "[IMP:8][test_no_simulator_classes_in_test_suite] Scanning tests/ for Simulator class definitions via AST"
    )

    all_py_files = sorted(_TESTS_DIR.rglob("*.py"))
    logger.info("[IMP:8][test_no_simulator_classes_in_test_suite] Found %d Python files to scan", len(all_py_files))

    all_violations: list[tuple[str, int, str]] = []
    for py_file in all_py_files:
        violations = _scan_file_for_simulator_classes(py_file)
        if violations:
            logger.warning(
                "[IMP:7][test_no_simulator_classes_in_test_suite] %s: %d violation(s)", py_file.name, len(violations)
            )
            all_violations.extend(violations)
        else:
            logger.info("[IMP:9][test_no_simulator_classes_in_test_suite] CLEAN: %s", py_file.name)

    assert not all_violations, (
        f"Simulator class definitions found in {len(all_violations)} location(s):\n"
        + "\n".join(f"  {fname}:{lineno} — class {cls_name}" for fname, lineno, cls_name in all_violations)
        + "\n\nAll Simulator classes must be removed. Use subprocess/contract tests instead."
    )

    logger.info(
        "[IMP:9][test_no_simulator_classes_in_test_suite] ALL PASS — no Simulator class definitions in %d Python files",
        len(all_py_files),
    )
