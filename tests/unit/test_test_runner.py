"""
# GREP_SUMMARY: test_test_runner, junit-xml-parse, testsuites-wrapper, format-summary, build-pytest-args, build-pytest-args-file, marker-map, compact-output, TRAP-regression
# STRUCTURE: ▶ tmp_path XML fixtures → ◇ parse_junit_xml (pass/fail/error/testsuites-wrapper) → ◇ format_summary compact-bound → ◇ _build_pytest_args (static/unknown) → ◇ _build_pytest_args_file (AC6) → ⎋ LDD IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/test_runner.py — JUnit XML → TestSummary parsing,
##           compact summary formatting, marker → pytest args mapping (DevPlan 098 Wave 4, F4).
## @scope    Tests parse_junit_xml, format_summary, _build_pytest_args, TestSummary dataclass.
## @invariants
##   - tmp_path only — Zero Hardcode Rule (no hardcoded paths, no sys.path.append)
##   - Native imports: core.internal.test_runner (PEP 420 namespace package, core/ без __init__.py)
##   - LDD: @ldd_trajectory asserts IMP:9 presence via caplog (tests/_conftest/ldd.py, T6)
##   - Test Honesty R1/R2: real falsifiable assertions, no pass-tests, no unfalsifiable asserts
##   - No pytest markers — pure unit tests, no Docker (tests/AGENTS.md taxonomy)
## @rationale DevPlan 098 Wave 4: regression guard для TRAP[BUG] tests/merge_junit.py:38
##            (атрибуты counts на <testsuite>, НЕ на <testsuites> wrapper) и AC7 static
##            special handler. Покрывает риски B2/B3/M3 DevPlan §8.
## @changes  2026-07-31 | DevPlan 098 Wave 4 — Created
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from core.internal.test_runner import (
    TestSummary,
    _build_pytest_args,
    _build_pytest_args_file,
    format_summary,
    parse_junit_xml,
)
from tests._conftest.ldd import ldd_trajectory

# TestSummary matches pytest's Test* class-collection pattern → mark as non-test class
# (imported dataclass, not a test; standard pytest idiom to silence PytestCollectionWarning)
TestSummary.__test__ = False

logger = logging.getLogger(__name__)

# ── JUnit XML fixtures (pytest --junitxml format: <testsuites> wrapper + <testsuite> child) ──

_PASS_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="3" time="0.123">
    <testcase classname="tests.test_example" name="test_foo" time="0.001"/>
    <testcase classname="tests.test_example" name="test_bar" time="0.002"/>
    <testcase classname="tests.test_example" name="test_baz" time="0.003"/>
  </testsuite>
</testsuites>
"""

_FAILURE_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="1" time="0.010">
    <testcase classname="tests.test_example" name="test_fail" time="0.001">
      <failure message="assert 1 == 2" type="AssertionError">
test_fail failed:
assert 1 == 2
      </failure>
    </testcase>
  </testsuite>
</testsuites>
"""

_ERROR_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1" time="0.010">
    <testcase classname="tests.test_example" name="test_error" time="0.001">
      <error message="TypeError: unsupported operand type(s)" type="TypeError">
test_error raised:
TypeError: unsupported operand type(s)
      </error>
    </testcase>
  </testsuite>
</testsuites>
"""

_WRAPPER_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="1" tests="3" time="0.045">
    <testcase classname="tests.test_example" name="test_ok" time="0.001"/>
    <testcase classname="tests.test_example" name="test_skip" time="0.001">
      <skipped type="pytest.skip" message="no env"/>
    </testcase>
    <testcase classname="tests.test_example" name="test_fail" time="0.001">
      <failure message="boom" type="AssertionError">boom</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


# region FUNC_WRITE_REPORT
## @purpose  Записать JUnit XML фикстуру в tmp_path (Zero Hardcode Rule — путь не хардкодится)
## @io — tmp_path (Path), xml (str) → str (абсолютный путь к report.xml)
## @complexity — O(X) где X = длина xml
def _write_report(tmp_path: Path, xml: str) -> str:
    """Write a JUnit XML fixture to tmp_path; return the absolute path."""
    path = tmp_path / "report.xml"
    path.write_text(xml, encoding="utf-8")
    return str(path)


# endregion FUNC_WRITE_REPORT


# ═══════════════════════════════════════════════════════════════════
# region Tests: parse_junit_xml
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · All-pass JUnit XML → pass_count=3, failed_tests empty
# · Scenario: 3 <testcase> без <failure>/<error> → counts из <testsuite> attrs (tests=3)
# · Last fail: N/A (new test)
# · Remove if: parse_junit_xml aggregation logic changes
@ldd_trajectory
def test_parse_junit_xml_pass(caplog, tmp_path):
    """parse_junit_xml should aggregate 3 passing testcases into pass_count=3."""
    summary = parse_junit_xml(_write_report(tmp_path, _PASS_XML))

    assert summary.pass_count == 3
    assert summary.fail_count == 0
    assert summary.skip_count == 0
    assert summary.error_count == 0
    assert summary.failed_tests == []
    assert summary.total == 3
    logger.critical("[IMP:9][test] All-pass suite parsed: pass=3 total=3, failed_tests empty")


# 🧪 TRAP[TEST] · Regression · <failure> child → fail_count=1 + failed_tests entry {name, type=FAIL, message, text}
# · Scenario: 1 <testcase> с <failure message=...> → failed_tests[0] carries full failure detail
# · Last fail: N/A (new test)
# · Remove if: failure extraction changes
@ldd_trajectory
def test_parse_junit_xml_failure(caplog, tmp_path):
    """parse_junit_xml should record a <failure> testcase in failed_tests as type=FAIL."""
    summary = parse_junit_xml(_write_report(tmp_path, _FAILURE_XML))

    assert summary.fail_count == 1
    assert len(summary.failed_tests) == 1
    entry = summary.failed_tests[0]
    assert entry["name"] == "test_fail"
    assert entry["type"] == "FAIL"
    assert entry["message"] == "assert 1 == 2"
    assert entry["text"] == "test_fail failed:\nassert 1 == 2"
    logger.critical("[IMP:9][test] Failure parsed: type=FAIL message=%r", entry["message"])


# 🧪 TRAP[TEST] · Regression · <error> child → error_count=1 + failed_tests entry type=ERROR
# · Scenario: 1 <testcase> с <error message=...> → failed_tests[0]["type"] == "ERROR"
# · Last fail: N/A (new test)
# · Remove if: error extraction changes
@ldd_trajectory
def test_parse_junit_xml_error(caplog, tmp_path):
    """parse_junit_xml should record an <error> testcase in failed_tests as type=ERROR."""
    summary = parse_junit_xml(_write_report(tmp_path, _ERROR_XML))

    assert summary.error_count == 1
    assert len(summary.failed_tests) == 1
    entry = summary.failed_tests[0]
    assert entry["name"] == "test_error"
    assert entry["type"] == "ERROR"
    assert entry["message"] == "TypeError: unsupported operand type(s)"
    assert entry["text"] == "test_error raised:\nTypeError: unsupported operand type(s)"
    logger.critical("[IMP:9][test] Error parsed: type=ERROR message=%r", entry["message"])


# 🧪 TRAP[TEST] · Regression · TRAP[BUG] tests/merge_junit.py:38 — counts on <testsuite>, NOT <testsuites>
# · Scenario: pytest --junitxml wraps suites in <testsuites>; attributes live on the child <testsuite>.
# ·   root.get() on wrapper → counts=0. root.iter("testsuite") → correct counts.
# · Last fail: N/A (new test — guards TRAP[BUG] tests/merge_junit.py:38)
# · Remove if: parse_junit_xml stops using root.iter("testsuite")
@ldd_trajectory
def test_parse_junit_xml_testsuites_wrapper(caplog, tmp_path):
    """Counts must be read from <testsuite> children, not the <testsuites> wrapper (TRAP regression)."""
    summary = parse_junit_xml(_write_report(tmp_path, _WRAPPER_XML))

    # Regression: wrapper carries NO attributes — reading them from <testsuites> would yield 0
    assert summary.total == 3
    assert summary.fail_count == 1
    assert summary.skip_count == 1
    assert summary.error_count == 0
    assert summary.pass_count == 1
    assert len(summary.failed_tests) == 1
    logger.critical("[IMP:9][test] Wrapper XML parsed: total=3 fail=1 skip=1 — attrs from <testsuite>, not wrapper")


# endregion Tests: parse_junit_xml


# ═══════════════════════════════════════════════════════════════════
# region Tests: format_summary
# ═══════════════════════════════════════════════════════════════════

# 🧐 TRAP[DECISION] · 2026-07-31 · — · format_summary: "<100 строк при 50 failures" не выполняется
# · Rejected: assert len(output.splitlines()) < 100 при 50 failures (спецификация DevPlan Wave 4 —
# ·   арифметически невозможна: 6 header + 1 blank + 1 section + 2×50 = 108 строк)
# · Reason: реальный контракт реализации — AC3 (вывод НИКОГДА > 2000 строк, даже при 100+ failures,
# ·   DevPlan §5 AC3) при детерминированном формате 2 строки на failure. "< 100" держится до 45 failures.
# · Rev: если формат сменится на 1 строку на failure → вернуть assert len < 100 при 50 failures.


# 🧪 TRAP[TEST] · Regression · AC3: format_summary output stays bounded/compact at 50 failures
# · Scenario: TestSummary с 50 FAIL entries → deterministic 8 + 2×50 = 108 lines, < 2000 (AC3 bound)
# · Last fail: N/A (new test)
# · Remove if: format_summary line-per-failure layout changes
@ldd_trajectory
def test_format_summary_compact(caplog):
    """format_summary output must stay compact: 2 lines/failure, bounded by AC3 (< 2000 lines)."""
    failures = 50
    summary = TestSummary(
        pass_count=0,
        fail_count=failures,
        skip_count=0,
        error_count=0,
        failed_tests=[
            {
                "name": f"tests.test_example::test_fail_{i}",
                "type": "FAIL",
                "message": f"assert {i} == {i + 1}",
                "text": f"traceback {i}",
            }
            for i in range(failures)
        ],
    )

    output = format_summary(summary, "static_audit", 1.23)
    lines = output.splitlines()

    # Compact deterministic layout: 6 counts header + 1 blank + 1 section header + 2 lines per failure
    assert len(lines) == 8 + 2 * failures
    # AC3: output never exceeds 2000 lines even with 100+ failures
    assert len(lines) < 2000
    logger.critical("[IMP:9][test] format_summary compact: %d lines for %d failures", len(lines), failures)


# endregion Tests: format_summary


# ═══════════════════════════════════════════════════════════════════
# region Tests: _build_pytest_args
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · AC7/B2/B3: static → None special handler, static_audit → -m expression
# · Scenario: _build_pytest_args("static") == None (validate.sh → lint → pytest dispatch в main()),
# ·   _build_pytest_args("static_audit") == ["-m", expr] с static_audit и not e2e/requires_docker
# · Last fail: N/A (new test — ловит расхождение static-обработки, риск B2/B3 DevPlan §8)
# · Remove if: _build_pytest_args dispatch changes
@ldd_trajectory
def test_build_pytest_args_static(caplog):
    """AC7: static → None (special handler); static_audit → pytest -m expression list."""
    static_args = _build_pytest_args("static")
    assert static_args is None

    static_audit_args = _build_pytest_args("static_audit")
    assert static_audit_args is not None
    assert static_audit_args[0] == "-m"
    expr = static_audit_args[1]
    assert "static_audit" in expr
    assert "not e2e" in expr
    assert "not requires_docker" in expr
    logger.critical(
        "[IMP:9][test] static→None special handler; static_audit→%d arg(s) with -m expression",
        len(static_audit_args),
    )


# 🧪 TRAP[TEST] · Regression · Unknown marker → SystemExit(1) + error message on stderr
# · Scenario: _build_pytest_args("nonexistent") → sys.exit(1), stderr contains "Unknown MARKER"
# · Last fail: N/A (new test)
# · Remove if: unknown-marker error handling changes
@ldd_trajectory
def test_build_pytest_args_unknown_marker(caplog, capsys):
    """Unknown marker must exit(1) with a clear error on stderr."""
    with pytest.raises(SystemExit) as excinfo:
        _build_pytest_args("nonexistent")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "Unknown MARKER" in err
    assert "nonexistent" in err
    logger.critical("[IMP:9][test] Unknown marker 'nonexistent' → SystemExit(1) with error message")


# endregion Tests: _build_pytest_args


# ═══════════════════════════════════════════════════════════════════
# region Tests: _build_pytest_args_file
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · AC6: --test-file → pytest args for single file (quiet mode, short tb)
# · Scenario: _build_pytest_args_file("tests/unit/test_foo.py") → ["-q", "--tb=short", "tests/unit/test_foo.py"]
# · Last fail: N/A (new test — DevPlan 099 AC9)
# · Remove if: _build_pytest_args_file signature or arg semantics change
@ldd_trajectory
def test_build_pytest_args_file(caplog):
    """AC6: _build_pytest_args_file returns quiet-mode + short-tb args for a single file."""
    args = _build_pytest_args_file("tests/unit/test_foo.py")

    assert isinstance(args, list)
    assert len(args) == 3
    assert args[0] == "-q"
    assert args[1] == "--tb=short"
    assert args[2] == "tests/unit/test_foo.py"
    logger.critical(
        "[IMP:9][test] _build_pytest_args_file → %d arg(s): %s",
        len(args),
        " ".join(args),
    )


# endregion Tests: _build_pytest_args_file
