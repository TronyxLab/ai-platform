"""
# GREP_SUMMARY: test_test_runner, junit-xml-parse, testsuites-wrapper, format-summary, build-pytest-args, build-pytest-args-file, marker-map, compact-output, all-marker, compression, TRAP-regression, xdist-args, TEST_NO_XDIST
# STRUCTURE: ▶ tmp_path XML fixtures → ◇ parse_junit_xml (pass/fail/error/testsuites-wrapper) → ◇ format_summary compressed-bound (MAX_FAIL_DETAILS) → ◇ _build_pytest_args (static/all/unknown) → ◇ _build_pytest_args_file (AC6) → ◇ _xdist_args (DevPlan 120: enabled/TEST_NO_XDIST/unavailable/3 insertion sites) → ⎋ LDD IMP:9 trajectory
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
## @changes  2026-07-31 | DevPlan 098 close-out (VR 098): test_build_pytest_args_all (DRIFT-2),
##            test_format_summary_compact → сжатие MAX_FAIL_DETAILS (AC1)
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

# 🧐 TRAP[DECISION] · 2026-07-31 · — · format_summary: "<100 строк при 50 failures" — решено сжатием
# · Rejected: assert len(output.splitlines()) < 100 при 50 failures в старом формате
# ·   (2 строки на failure — арифметически невозможно: 6 header + 1 blank + 1 section + 2×50 = 108 строк)
# · Reason: DevPlan 098 close-out (VR 098 AC1) — внедрён MAX_FAIL_DETAILS=20: при >20 failures
# ·   вывод обрезается до первых 20 + "... and M more". 50 failures → 48 строк (< 100, AC1 держится
# ·   при ЛЮБОМ числе failures). 119 failures (реальный прогон VR 098) → 244 строк → 48 строк.
# · Rev: если формат снова сменится на 1 строку на failure → пересмотреть MAX_FAIL_DETAILS.


# 🧪 TRAP[TEST] · Regression · AC1: format_summary compressed — >MAX_FAIL_DETAILS failures → first 20 + "... and M more"
# · Scenario: TestSummary с 50 FAIL entries → 48 строк (6 header + 1 blank + 1 section + 2×20 + 1 more),
# ·   первый failure присутствует, последний отсутствует, "... and 30 more failures" в выводе
# · Last fail: N/A (new test — заменяет арифметику 8 + 2×F, VR 098 AC1)
# · Remove if: format_summary compression (MAX_FAIL_DETAILS) changes
@ldd_trajectory
def test_format_summary_compact(caplog):
    """format_summary must compress: first MAX_FAIL_DETAILS failures + \"... and M more\" (AC1)."""
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

    # Compressed layout: 6 counts header + 1 blank + 1 section header + 2×20 shown + 1 more-line
    assert len(lines) == 8 + 2 * 20 + 1
    # AC1: output < 100 lines even with 50 (and 119+) failures
    assert len(lines) < 100
    # First failure shown, last failure compressed away
    assert "tests.test_example::test_fail_0" in output
    assert "tests.test_example::test_fail_49" not in output
    assert "... and 30 more failures" in output
    logger.critical("[IMP:9][test] format_summary compressed: %d lines for %d failures", len(lines), failures)


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
    # DevPlan 095 AC9/AC12: requires_node (E2E pipeline) excluded from static_audit —
    # иначе tests/e2e/ подхватываются выражением и FAIL без NODE env (Rule R4)
    assert "not requires_node" in expr
    logger.critical(
        "[IMP:9][test] static→None special handler; static_audit→%d arg(s) with -m expression",
        len(static_audit_args),
    )


# 🧪 TRAP[TEST] · Regression · DRIFT-2 close-out: "all" → None special handler (НЕ Unknown MARKER)
# · Scenario: _build_pytest_args("all") == None (sequential suites + merge_junit dispatch в main()),
# ·   _ALL_SUITES_ORDER не содержит special handlers (static/all) — только pytest-суиты MARKER_MAP
# · Last fail: VR 098 DRIFT-2 — `_build_pytest_args("all")` → SystemExit(1) "Unknown MARKER"
# · Remove if: "all" stops being a special handler or _ALL_SUITES_ORDER changes
@ldd_trajectory
def test_build_pytest_args_all(caplog):
    """AC2/DRIFT-2: all → None (special handler); _ALL_SUITES_ORDER = pytest suites only."""
    all_args = _build_pytest_args("all")
    assert all_args is None

    from core.internal.test_runner import _ALL_SUITES_ORDER

    assert isinstance(_ALL_SUITES_ORDER, list)
    assert len(_ALL_SUITES_ORDER) >= 4
    # Каждый элемент — реальный pytest-маркер из MARKER_MAP, НЕ special handler (static/all)
    from core.internal.test_runner import MARKER_MAP

    for suite in _ALL_SUITES_ORDER:
        assert suite in MARKER_MAP
        assert MARKER_MAP[suite] is not None, f"{suite} — special handler не может быть в _ALL_SUITES_ORDER"
    # Canonical ci.mk order: contract → static_audit → predeploy → smoke → component → integration
    assert _ALL_SUITES_ORDER[0] == "contract"
    assert "static_audit" in _ALL_SUITES_ORDER
    logger.critical(
        "[IMP:9][test] all→None special handler; _ALL_SUITES_ORDER=%s",
        ",".join(_ALL_SUITES_ORDER),
    )


# 🧪 TRAP[TEST] · Regression · Unknown marker → SystemExit(1) + error message on stderr
# · Scenario: _build_pytest_args("nonexistent") → sys.exit(1), stderr contains "Unknown MARKER"
# · Last fail: N/A (new test)
# · Remove if: unknown-marker error handling changes
@ldd_trajectory
def test_build_pytest_args_unknown_marker(caplog, capsys):
    """Unknown marker must raise ConfigValidationError (T3.6: sys.exit → raise)."""
    from core.internal.shared.exceptions import ConfigValidationError

    with pytest.raises(ConfigValidationError) as excinfo:
        _build_pytest_args("nonexistent")

    err = str(excinfo.value)
    assert "Unknown MARKER" in err
    assert "nonexistent" in err
    assert "all" in err  # valid list включает all (DRIFT-2 close-out)
    logger.critical("[IMP:9][test] Unknown marker 'nonexistent' → ConfigValidationError")


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


# ═══════════════════════════════════════════════════════════════
# region Tests: xdist args (DevPlan 120 Wave 1 — §3.3)
# ═══════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · DevPlan 120 §3.3 · xdist: _xdist_args() возвращает -n auto при доступности
# · Scenario: _has_xdist(monkeypatch→True) → ["-n", "auto"]
# · Last fail: N/A (новый тест — корень ускорения static_audit 254s → ~60s)
# · Remove if: xdist-политика test_runner изменена
@ldd_trajectory
def test_xdist_args_enabled(caplog, monkeypatch):
    """DevPlan 120: _xdist_args() → ['-n', 'auto'] при доступном pytest-xdist."""
    from core.internal.test_runner import _xdist_args

    monkeypatch.setenv("TEST_NO_XDIST", "")
    monkeypatch.delenv("TEST_NO_XDIST", raising=False)
    monkeypatch.setattr("core.internal.test_runner._has_xdist", lambda python_path: True)

    args = _xdist_args()
    assert args == ["-n", "auto"], f"ожидался -n auto, got {args}"
    logger.critical("[IMP:9][test] _xdist_args → %s (xdist доступен)", args)


# 🧪 TRAP[TEST] · DevPlan 120 §3.3 · xdist: TEST_NO_XDIST=1 отключает -n auto
# · Scenario: TEST_NO_XDIST=1 → [] даже при доступном xdist
# · Last fail: N/A
# · Remove if: TEST_NO_XDIST удалён
@ldd_trajectory
def test_xdist_args_disabled_by_env(caplog, monkeypatch):
    """TEST_NO_XDIST=1 → [] (слабые машины, диагностика гонок)."""
    from core.internal.test_runner import _xdist_args

    monkeypatch.setenv("TEST_NO_XDIST", "1")
    monkeypatch.setattr("core.internal.test_runner._has_xdist", lambda python_path: True)

    assert _xdist_args() == []
    logger.critical("[IMP:9][test] _xdist_args → [] (TEST_NO_XDIST=1)")


# 🧪 TRAP[TEST] · DevPlan 120 §3.3 · xdist: недоступный xdist → без -n
# · Scenario: _has_xdist(→False) → []
# · Last fail: N/A
# · Remove if: fallback-семантика изменена
@ldd_trajectory
def test_xdist_args_unavailable(caplog, monkeypatch):
    """xdist недоступен → [] (graceful degradation)."""
    from core.internal.test_runner import _xdist_args

    monkeypatch.delenv("TEST_NO_XDIST", raising=False)
    monkeypatch.setattr("core.internal.test_runner._has_xdist", lambda python_path: False)

    assert _xdist_args() == []
    logger.critical("[IMP:9][test] _xdist_args → [] (xdist недоступен)")


# 🧪 TRAP[TEST] · DevPlan 124 T2a · docker-маркеры исключены из -n auto (single-process)
# · Scenario: _xdist_args("smoke"/"component"/"integration"/"predeploy-docker") == [] ДАЖЕ при
# ·   доступном xdist и TEST_NO_XDIST≠1; static_audit/contract — по-прежнему ["-n", "auto"]
# · Last fail: 2026-08-03 — эксперимент -n 2 → 5 passed / 7 errors (гонка docker-стека, факт 6)
# · Remove if: docker-маркеры вернутся в xdist (пересмотр A2+)
@ldd_trajectory
def test_xdist_args_docker_markers_excluded(caplog, monkeypatch):
    """DevPlan 124 T2a: docker-сьюты — single-process; статика/contract — -n auto."""
    from core.internal.test_runner import _DOCKER_MARKERS, _xdist_args

    monkeypatch.delenv("TEST_NO_XDIST", raising=False)
    monkeypatch.setattr("core.internal.test_runner._has_xdist", lambda python_path: True)

    for marker in sorted(_DOCKER_MARKERS):
        assert _xdist_args(marker) == [], f"{marker}: docker-сьют должен идти БЕЗ -n auto"
    # Статические/contract-сьюты — поведение DevPlan 120 без изменений
    assert _xdist_args("static_audit") == ["-n", "auto"]
    assert _xdist_args("contract") == ["-n", "auto"]
    assert _xdist_args("predeploy") == ["-n", "auto"]  # predeploy — НЕ docker-маркер (check-suite фильтрует)
    logger.critical(
        "[IMP:9][test] _xdist_args: docker-маркеры %s → [] ; static_audit/contract → -n auto",
        sorted(_DOCKER_MARKERS),
    )


# 🧪 TRAP[TEST] · DevPlan 124 T2a · docker-исключение действует и при TEST_NO_XDIST=1
# · Scenario: docker-маркер → [] независимо от TEST_NO_XDIST (агентский путь test_runner не
# ·   выставляет TEST_NO_XDIST — F11; исключение docker-маркеров — безусловное)
# · Last fail: N/A
# · Remove if: docker-исключение перестанет быть безусловным
@ldd_trajectory
def test_xdist_args_docker_exclusion_unconditional(caplog, monkeypatch):
    """Docker-исключение НЕ зависит от TEST_NO_XDIST (F11: agent-путь его не выставляет)."""
    from core.internal.test_runner import _xdist_args

    monkeypatch.setenv("TEST_NO_XDIST", "1")
    monkeypatch.setattr("core.internal.test_runner._has_xdist", lambda python_path: True)

    assert _xdist_args("smoke") == [], "docker-исключение должно работать и при TEST_NO_XDIST=1"
    logger.critical("[IMP:9][test] _xdist_args('smoke') → [] при TEST_NO_XDIST=1 (безусловное исключение)")


# 🧪 TRAP[TEST] · DevPlan 120 §3.3 · structural: все 3 pytest-инвокации используют *_xdist_args(marker)
# · Scenario: source-скан — marker-режим (_xdist_args(args.marker)), _run_static_full
# ·   (_xdist_args("static")), _run_all_suites (_xdist_args(marker)) — ПЕРЕД -m (DevPlan §3.3)
# · Last fail: N/A
# · Remove if: xdist-вставка вынесена в отдельный модуль (обновить скан)
@ldd_trajectory
def test_xdist_inserted_in_all_pytest_invocations(caplog):
    """Все pytest-инвокации test_runner получают *_xdist_args(marker) перед -m (DevPlan 124 T2a)."""
    from pathlib import Path

    import core.internal.test_runner as tr_module

    source = Path(tr_module.__file__).read_text(encoding="utf-8")

    # marker-режим main(): pytest_args = [*_xdist_args(args.marker), *pytest_args, "--junitxml", ...]
    assert "*_xdist_args(args.marker), *pytest_args" in source, "marker-режим main() не вставляет xdist"
    # _run_static_full: [*_xdist_args("static"), "-m", _STATIC_AUDIT_EXPR, ...]
    assert '*_xdist_args("static"), "-m"' in source, "_run_static_full не вставляет xdist перед -m"
    # _run_all_suites: [*_xdist_args(marker), *args, "--junitxml", ...]
    assert "*_xdist_args(marker), *args" in source, "_run_all_suites не вставляет xdist"
    logger.critical("[IMP:9][test] xdist вставлен во все 3 pytest-инвокации (marker/static_full/all_suites)")


# 🧪 TRAP[TEST] · DevPlan 124 T2c · процессный flock: _docker_suite_lock сериализует docker-процессы
# · Scenario: внутри with-контекста второй независимый open+flock(LOCK_EX|LOCK_NB) → BlockingIOError;
# ·   после выхода из контекста — лок свободен. Лок-файл tests/.docker-suite.lock в tmp_path.
# · Last fail: 2026-08-03 — межсессионная гонка F4 (два агента → master-клинер сносит чужой стек)
# · Remove if: docker-лок заменён другим механизмом (A3, отдельный DevPlan)
@ldd_trajectory
def test_docker_suite_lock_serializes(caplog, tmp_path):
    """DevPlan 124 T2c: flock на tests/.docker-suite.lock удерживается и освобождается."""
    import fcntl

    from core.internal.test_runner import _docker_suite_lock

    lock_path = tmp_path / "tests" / ".docker-suite.lock"

    with _docker_suite_lock(tmp_path):
        assert lock_path.exists(), "lock-файл должен быть создан"
        # Второй независимый open() (отдельный open-file-description) — лок обязан блокироваться
        with open(lock_path, "a+") as fd, pytest.raises(BlockingIOError):
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    # После выхода из контекста — неблокирующий захват обязан пройти
    with open(lock_path, "a+") as fd:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    logger.critical("[IMP:9][test] docker-suite flock: held → blocked 2nd acquire → released")


# 🧪 TRAP[TEST] · DevPlan 124 T2c · _run_docker_pytest оборачивает pytest-subprocess в лок
# · Scenario: docker-маркерный запуск идёт через _run_docker_pytest → _docker_suite_lock entered
# ·   (wiring-проверка; сама сериализация — test_docker_suite_lock_serializes)
# · Last fail: N/A
# · Remove if: docker-запуск перестанет использовать процессный лок
@ldd_trajectory
def test_run_docker_pytest_uses_lock(caplog, tmp_path, monkeypatch):
    """_run_docker_pytest выполняет pytest под _docker_suite_lock (DevPlan 124 T2c wiring)."""
    import contextlib

    from core.internal.test_runner import _run_docker_pytest

    entered: list[str] = []

    @contextlib.contextmanager
    def _fake_lock(platform_root):
        entered.append(str(platform_root))
        yield

    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("core.internal.test_runner._docker_suite_lock", _fake_lock)
    monkeypatch.setattr("core.internal.test_runner._has_xdist", lambda python_path: False)

    proc = _run_docker_pytest(["-h"], {}, 30, tmp_path)

    assert entered == [str(tmp_path)], f"лок должен оборачивать docker-pytest, entered={entered}"
    assert proc.returncode == 0, f"pytest -h должен завершиться 0, rc={proc.returncode}"
    logger.critical("[IMP:9][test] _run_docker_pytest: lock entered=%s, rc=%d", entered, proc.returncode)


# endregion Tests: xdist args (DevPlan 120)
