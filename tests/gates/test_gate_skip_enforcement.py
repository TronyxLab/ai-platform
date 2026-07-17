# GREP_SUMMARY: gate skip-enforcement junit-xml runtime-validation execution-enforcement
# STRUCTURE: ┌load JUnit XML┐ → ◇ parse collected/executed/errors/failures/skipped → ⊕ assert
# region MODULE_CONTRACT
## @purpose — Gate test that validates SKIPPED enforcement at runtime:
##            JUnit XML shows executed > 0 (no silent all-skip test runs)
## @scope — Parses JUnit XML report from previous pytest run (JUNIT_XML env var or default)
## @invariants
##   - JUnit XML path from JUNIT_XML env var, default: tests/report.xml
##   - collected > 0 — at least one test was collected
##   - executed > 0 — at least one test actually ran (not all skipped)
##   - errors == 0 — no test errors
##   - failures == 0 — no test failures
## @rationale — Pytest returns exit code 0 when ALL tests are skipped.
##              This gate prevents silent CI erosion: if a CI job is green
##              but all tests are skipped, this gate catches it.
## @changes — 2026-07-10 | Created per TestsMetaDevPlan2.md TASK-11
##            2026-07-17 | DRIFT-2: removed known_ci_skips.yaml dependent tests
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import xml.etree.ElementTree as ET

import pytest

from tests.conftest import ldd_trajectory

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_DEFAULT_REPORT_PATH: pathlib.Path = _PROJECT_ROOT / "tests" / "report.xml"

logger = logging.getLogger(__name__)


def _get_junit_xml_path() -> pathlib.Path:
    """Get JUnit XML report path from env var or default.

    ## @purpose — Determine which JUnit XML file to parse.
    ## @io — ⎋ pathlib.Path to the JUnit XML report
    ## @complexity — O(1)
    """
    env_path = os.environ.get("JUNIT_XML")
    if env_path:
        return pathlib.Path(env_path)
    return _DEFAULT_REPORT_PATH


def _parse_junit_xml(report_path: pathlib.Path) -> dict:
    """Parse JUnit XML and return test run statistics.

    ## @purpose — Extract testsuite attributes: collected, executed, errors, failures, skipped.
    ## @io — ⎋ dict with keys: collected, executed, errors, failures, skipped, testcases
    ## @complexity — O(T) where T = number of testcases in XML
    """
    tree = ET.parse(report_path)
    root = tree.getroot()

    # Aggregate attributes from all <testsuite> elements.
    # Uses root.iter("testsuite") — handles both <testsuites> wrapper
    # (pytest --junitxml raw output) and standalone <testsuite> root
    # (merged output from merge_junit.py). Initial values are 0 to avoid
    # double-counting when root IS a <testsuite>.
    # ⚠️ TRAP[BUG] · 2026-07-11 · P1 · _parse_junit_xml double-counted attributes
    # · Symptom: after merge_junit fix, collected/executed/skipped were 2x actual
    # · Root: code read root.get("tests",0) first, then added again from
    # ·   root.iter("testsuite") loop. For merged output (root=<testsuite>),
    # ·   iter() returns root itself → double count.
    # · Fix: remove initial root.get(), initialise counters at 0, aggregate
    # ·   only from root.iter("testsuite") loop.
    # · Prevention: single source of truth — one aggregation loop, no pre-reads.
    collected = 0
    errors = 0
    failures = 0
    skipped_attr = 0

    # Count testcases
    testcases = []
    for testsuite in root.iter("testsuite"):
        collected += int(testsuite.get("tests", 0))
        errors += int(testsuite.get("errors", 0))
        failures += int(testsuite.get("failures", 0))
        skipped_attr += int(testsuite.get("skipped", 0))

        for tc in testsuite.iter("testcase"):
            tc_data = {
                "name": tc.get("name", ""),
                "classname": tc.get("classname", ""),
                "file": tc.get("file", ""),
                "line": tc.get("line", ""),
            }
            # Check for skipped, failure, error children
            skip_elem = tc.find("skipped")
            if skip_elem is not None:
                tc_data["status"] = "skipped"
                tc_data["message"] = skip_elem.get("message", "")
            failure_elem = tc.find("failure")
            if failure_elem is not None:
                tc_data["status"] = "failed"
                tc_data["message"] = failure_elem.get("message", "")
            error_elem = tc.find("error")
            if error_elem is not None:
                tc_data["status"] = "error"
                tc_data["message"] = error_elem.get("message", "")

            testcases.append(tc_data)

    executed = collected - skipped_attr

    logger.info(
        "[IMP:8][_parse_junit_xml] Report: collected=%d, executed=%d, errors=%d, failures=%d, skipped=%d, testcases=%d",
        collected,
        executed,
        errors,
        failures,
        skipped_attr,
        len(testcases),
    )

    return {
        "collected": collected,
        "executed": executed,
        "errors": errors,
        "failures": failures,
        "skipped": skipped_attr,
        "testcases": testcases,
    }


@pytest.mark.skip_enforcement
@pytest.mark.gate
@ldd_trajectory
def test_executed_tests_greater_than_zero(caplog) -> None:
    """Verify JUnit XML shows executed tests > 0.

    ## @purpose — Prevent silent all-skip test runs. If pytest collected 1, skipped 1,
    ##            executed 0, the CI job is green but no tests ran. This gate catches that.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(1)
    """

    report_path = _get_junit_xml_path()
    logger.info("[IMP:8][test_executed_tests_greater_than_zero] Parsing JUnit XML: %s", report_path)

    if not report_path.exists():
        logger.warning(
            "[IMP:7][test_executed_tests_greater_than_zero] JUnit XML not found at %s — skipping", report_path
        )
        pytest.skip(f"JUnit XML report not found at {report_path} — run tests with --junitxml first")

    stats = _parse_junit_xml(report_path)

    assert stats["collected"] > 0, (
        f"JUnit XML shows 0 collected tests — is --junitxml pointing to the right report? Report: {report_path}"
    )

    assert stats["executed"] > 0, (
        f"JUnit XML shows 0 executed tests (all {stats['collected']} tests were skipped). "
        f"This means ALL tests were skipped — no tests actually ran. "
        f"Collected: {stats['collected']}, Skipped: {stats['skipped']}. "
        f"Review test markers and CI environment."
    )

    assert stats["errors"] == 0, (
        f"JUnit XML shows {stats['errors']} test errors. Errors indicate test infrastructure failures."
    )

    assert stats["failures"] == 0, f"JUnit XML shows {stats['failures']} test failures."

    logger.critical(
        "[IMP:9][test_executed_tests_greater_than_zero] PASS — %d tests executed, %d collected, %d skipped, 0 errors/failures",
        stats["executed"],
        stats["collected"],
        stats["skipped"],
    )
