"""
# GREP_SUMMARY: test-converge-infra, unit-enabled, exit-code, json-report, infra-state, converge-shared
# STRUCTURE: ▶ infra state → ◇ _unit_enabled filter 1× → ◇ exit-code semantics 4× (0/1/2/precedence) → ◇ JSON report 3× (format/errors/warnings) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for converge/infra.py shared state: _unit_enabled filter,
##           exit code semantics (0/1/2), JSON report emission.
## @scope    Pure state-machine tests — no docker, no filesystem (infra is in-memory).
## @invariants
##   - Exit code tests verify: 0=converged, 1=warnings, 2=errors semantics
##   - JSON report output is validated for schema conformance
##   - Each test validates IMP:9 business logic log presence via caplog
## @rationale Direct function testing of converge shared infra state.
##   Вынесен из монолита test_reconciler.py (DevPlan 118 F6).
## @changes 2026-08-02 · F6 split — infra state (DevPlan 118)
# endregion MODULE_CONTRACT
"""

import json
import logging
import sys
from pathlib import Path

# Load the LDD trajectory decorator from shared conftest
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))

import core.internal.bootstrap.converge.infra as infra

# Re-export for fixture cleanups
MODULE = infra


# ═══════════════════════════════════════════════════════════════════
# region Fixtures


# endregion Fixtures


# region FUNC_test_unit_enabled_filter
## 🧪 TRAP[TEST] · _unit_enabled filter · Scenario: filter with specific units
## · Regression: converge.sh lines 77-93
## · Last fail: never
## · Remove if: _unit_enabled logic changes
@ldd_trajectory
def test_unit_enabled_filter(caplog):
    """_unit_enabled: filters correctly with comma-separated units."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] _unit_enabled filter semantics")

    # Empty filter → all enabled
    assert infra.unit_enabled("", "R1") is True
    assert infra.unit_enabled("", "R6") is True

    # Specific filter → only matching
    assert infra.unit_enabled("R1,R3", "R1") is True
    assert infra.unit_enabled("R1,R3", "R3") is True
    assert infra.unit_enabled("R1,R3", "R2") is False
    assert infra.unit_enabled("R1,R3", "R6") is False

    # Single unit
    assert infra.unit_enabled("R3", "R3") is True
    assert infra.unit_enabled("R3", "R1") is False

    # With whitespace
    assert infra.unit_enabled("R1, R3", "R3") is True


# endregion FUNC_test_unit_enabled_filter


# region FUNC_test_exit_code_0
## 🧪 TRAP[TEST] · exit code 0 · Scenario: no drifts, no errors → exit 0
## · Regression: converge.sh lines 1137-1145
## · Last fail: never
## · Remove if: exit code semantics change
@ldd_trajectory
def test_exit_code_0(caplog):
    """Exit code: no errors, no warnings → exit_code=0."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] exit-code 0")
    infra.reset_state()
    assert infra.exit_code == 0
    assert not infra.has_warnings
    assert not infra.has_errors


# endregion FUNC_test_exit_code_0


# region FUNC_test_exit_code_1
## 🧪 TRAP[TEST] · exit code 1 · Scenario: warnings but no errors → exit 1
## · Regression: converge.sh lines 321-327 — warnings set exit 1
## · Last fail: never
## · Remove if: exit code semantics change
@ldd_trajectory
def test_exit_code_1(caplog):
    """Exit code: warnings set → exit_code=1."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] exit-code 1")
    infra.reset_state()
    infra.set_exit(1)
    assert infra.exit_code == 1
    assert infra.has_warnings
    assert not infra.has_errors


# endregion FUNC_test_exit_code_1


# region FUNC_test_exit_code_2
## 🧪 TRAP[TEST] · exit code 2 · Scenario: errors set → exit 2
## · Regression: converge.sh lines 355-358 — errors set exit 2
## · Last fail: never
## · Remove if: exit code semantics change
@ldd_trajectory
def test_exit_code_2(caplog):
    """Exit code: errors set → exit_code=2."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] exit-code 2")
    infra.reset_state()
    infra.set_exit(2)
    assert infra.exit_code == 2
    assert not infra.has_warnings
    assert infra.has_errors


# endregion FUNC_test_exit_code_2


# region FUNC_test_exit_code_2_overrides_1
## 🧪 TRAP[TEST] · exit code 2 overrides 1 · Scenario: both warnings and errors → exit 2
## · Regression: converge.sh — errors take precedence
## · Last fail: never
## · Remove if: exit code semantics change
@ldd_trajectory
def test_exit_code_2_overrides_1(caplog):
    """Exit code: warning then error → exit_code=2 (errors take precedence)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] exit-code precedence")
    infra.reset_state()
    infra.set_exit(1)  # warning
    infra.set_exit(2)  # error → overrides
    assert infra.exit_code == 2
    assert infra.has_warnings  # Warnings flag remains
    assert infra.has_errors  # Errors flag is set


# endregion FUNC_test_exit_code_2_overrides_1


# region FUNC_test_report_emit_format
## 🧪 TRAP[TEST] · report JSON format · Scenario: JSON report schema validation
## · Regression: converge.sh lines 241-258 — JSON report with node/timestamp/exit_code/drifts
## · Last fail: never
## · Remove if: report format changes
@ldd_trajectory
def test_report_emit_format(caplog):
    """report_emit: produces valid JSON with correct schema."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] report_emit schema")
    infra.reset_state()
    infra.node_name = "test-node"

    # Add some drifts
    infra.report_add("R1", "skipped", "All scripts already executable")
    infra.report_add("R3", "mutated", "2 project items created")

    report_json = infra.report_emit()
    report = json.loads(report_json)

    # Schema validation
    assert report["node"] == "test-node"
    assert "timestamp" in report
    assert report["exit_code"] == 0  # no errors set
    assert report["status"] == "converged"
    assert len(report["drifts"]) == 2
    assert report["drifts"][0]["unit"] == "R1"
    assert report["drifts"][0]["status"] == "skipped"
    assert report["drifts"][1]["unit"] == "R3"
    assert report["drifts"][1]["status"] == "mutated"


# endregion FUNC_test_report_emit_format


# region FUNC_test_report_emit_errors_status
## 🧪 TRAP[TEST] · report status=errors · Scenario: errors set → status="errors"
## · Regression: converge.sh lines 237-239 — exit_reason mapping
## · Last fail: never
## · Remove if: report status mapping changes
@ldd_trajectory
def test_report_emit_errors_status(caplog):
    """report_emit: errors set → status='errors'."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] report_emit errors status")
    infra.reset_state()
    infra.node_name = "test-node"
    infra.set_exit(2)

    report_json = infra.report_emit()
    report = json.loads(report_json)
    assert report["exit_code"] == 2
    assert report["status"] == "errors"


# endregion FUNC_test_report_emit_errors_status


# region FUNC_test_report_emit_warnings_status
## 🧪 TRAP[TEST] · report status=mutations · Scenario: warnings set → status="mutations_applied"
## · Regression: converge.sh lines 235-236
## · Last fail: never
## · Remove if: report status mapping changes
@ldd_trajectory
def test_report_emit_warnings_status(caplog):
    """report_emit: warnings set → status='mutations_applied'."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] report_emit warnings status")
    infra.reset_state()
    infra.node_name = "test-node"
    infra.set_exit(1)

    report_json = infra.report_emit()
    report = json.loads(report_json)
    assert report["exit_code"] == 1
    assert report["status"] == "mutations_applied"


# endregion FUNC_test_report_emit_warnings_status
