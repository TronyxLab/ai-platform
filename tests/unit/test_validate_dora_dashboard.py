"""
# GREP_SUMMARY: test_validate_dora_dashboard, dora, grafana, dashboard-validation, unit-tests, CI
# STRUCTURE: ▶ tmp_path JSON creation → ◇ call validate() → ⊕ assert True/False → ⎋ LDD IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for validate_dora_dashboard.py — Dora dashboard structure validator.
## @scope    Tests validate() API function and CLI main() entrypoint.
## @invariants
##   - All tests use tmp_path for JSON files (no filesystem pollution)
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
##   - CLI tests use subprocess.run to verify exit codes
## @rationale DevPlan 048 TASK-2: CI/CD gap closure — unit tests for DORA dashboard validator
## @changes  2026-07-22 | DevPlan 048 TASK-2 — Created
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_MODULE_DIR))
import validate_dora_dashboard as vdd

# Path to the script itself for CLI tests
_SCRIPT_PATH = _MODULE_DIR / "validate_dora_dashboard.py"

# ═══════════════════════════════════════════════════════════════════
# region Tests: validate() — Valid dashboard
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Valid DORA dashboard with all 4 panels returns True
# · Scenario: Create valid JSON with correct uid + all 4 required panels → validate returns True
# · Last fail: N/A (new test)
# · Remove if: validate() logic changes
@ldd_trajectory
def test_valid_dashboard_all_panels(caplog, tmp_path):
    """validate() should return True for a valid DORA dashboard (uid + 4 panels)."""
    data = {
        "uid": "dora-ci-cd",
        "title": "DORA Metrics",
        "panels": [
            {"title": "Deploy Frequency", "type": "stat"},
            {"title": "Lead Time for Changes", "type": "stat"},
            {"title": "Mean Time to Recovery (MTTR)", "type": "stat"},
            {"title": "Change Failure Rate (CFR)", "type": "stat"},
        ],
    }
    path = tmp_path / "valid_dashboard.json"
    path.write_text(json.dumps(data))

    result = vdd.validate(path)
    assert result is True
    logger.critical("[IMP:9][test] test_valid_dashboard_all_panels — valid dashboard accepted")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: validate() — Missing / wrong UID
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Wrong dashboard UID returns False with IMP:10 diagnostic
# · Scenario: JSON with uid != "dora-ci-cd" → validate returns False
# · Last fail: N/A (new test)
# · Remove if: validate() UID check changes
@ldd_trajectory
def test_missing_uid(caplog, tmp_path):
    """validate() should return False when dashboard UID is wrong."""
    data = {
        "uid": "wrong-uid",
        "title": "DORA Metrics",
        "panels": [
            {"title": "Deploy Frequency", "type": "stat"},
            {"title": "Lead Time for Changes", "type": "stat"},
            {"title": "Mean Time to Recovery (MTTR)", "type": "stat"},
            {"title": "Change Failure Rate (CFR)", "type": "stat"},
        ],
    }
    path = tmp_path / "wrong_uid.json"
    path.write_text(json.dumps(data))

    result = vdd.validate(path)
    assert result is False
    logger.critical("[IMP:9][test] test_missing_uid — wrong UID correctly rejected")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: validate() — Missing panel
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Dashboard missing one of 4 required panels returns False
# · Scenario: JSON with only 3 of 4 required panels → validate returns False
# · Last fail: N/A (new test)
# · Remove if: validate() panel check changes
@ldd_trajectory
def test_missing_panel(caplog, tmp_path):
    """validate() should return False when one of 4 required panels is missing."""
    data = {
        "uid": "dora-ci-cd",
        "title": "DORA Metrics",
        "panels": [
            {"title": "Deploy Frequency", "type": "stat"},
            {"title": "Lead Time for Changes", "type": "stat"},
            {"title": "Change Failure Rate (CFR)", "type": "stat"},
            # Missing: "Mean Time to Recovery (MTTR)"
        ],
    }
    path = tmp_path / "missing_panel.json"
    path.write_text(json.dumps(data))

    result = vdd.validate(path)
    assert result is False
    logger.critical("[IMP:9][test] test_missing_panel — missing panel correctly rejected")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: validate() — File not found
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Non-existent file path returns False
# · Scenario: Path to non-existent file → validate returns False
# · Last fail: N/A (new test)
# · Remove if: validate() file-existence check changes
@ldd_trajectory
def test_file_not_found(caplog):
    """validate() should return False when the dashboard file does not exist."""
    non_existent = Path("/tmp/non_existent_dora_dashboard_12345.json")
    assert not non_existent.exists()

    result = vdd.validate(non_existent)
    assert result is False
    logger.critical("[IMP:9][test] test_file_not_found — non-existent file correctly rejected")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: validate() — Invalid JSON content
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Non-JSON file content returns False
# · Scenario: File with plain text (not JSON) → validate returns False
# · Last fail: N/A (new test)
# · Remove if: validate() JSON parse error handling changes
@ldd_trajectory
def test_invalid_json(caplog, tmp_path):
    """validate() should return False when the file contains non-JSON content."""
    path = tmp_path / "invalid.json"
    path.write_text("this is not json")

    result = vdd.validate(path)
    assert result is False
    logger.critical("[IMP:9][test] test_invalid_json — malformed JSON correctly rejected")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: validate() — Non-dict JSON root
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · JSON array as root returns False
# · Scenario: JSON root is an array (not object) → validate returns False
# · Last fail: N/A (new test)
# · Remove if: validate() root-type check changes
@ldd_trajectory
def test_non_dict_root(caplog, tmp_path):
    """validate() should return False when the JSON root is an array, not an object."""
    data = [
        {"uid": "dora-ci-cd"},
        {"panels": []},
    ]
    path = tmp_path / "array_root.json"
    path.write_text(json.dumps(data))

    result = vdd.validate(path)
    assert result is False
    logger.critical("[IMP:9][test] test_non_dict_root — array root correctly rejected")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: CLI exit codes
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · CLI exits 0 on valid JSON, 1 on invalid JSON
# · Scenario: Run script via subprocess with valid → exit 0; with invalid → exit 1
# · Last fail: N/A (new test)
# · Remove if: CLI main() exit logic changes
@ldd_trajectory
def test_cli_exit_code(caplog, tmp_path):
    """CLI main() should exit 0 on valid dashboard and 1 on invalid."""
    # Valid dashboard
    valid_path = tmp_path / "valid_cli.json"
    valid_data = {
        "uid": "dora-ci-cd",
        "title": "DORA Metrics",
        "panels": [
            {"title": "Deploy Frequency", "type": "stat"},
            {"title": "Lead Time for Changes", "type": "stat"},
            {"title": "Mean Time to Recovery (MTTR)", "type": "stat"},
            {"title": "Change Failure Rate (CFR)", "type": "stat"},
        ],
    }
    valid_path.write_text(json.dumps(valid_data))

    # Invalid dashboard (wrong uid)
    invalid_path = tmp_path / "invalid_cli.json"
    invalid_data = {"uid": "wrong", "panels": []}
    invalid_path.write_text(json.dumps(invalid_data))

    # Valid → exit 0
    result_valid = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), str(valid_path)],
        capture_output=True,
        text=True,
    )
    assert result_valid.returncode == 0, f"Expected exit 0, got {result_valid.returncode}. stderr: {result_valid.stderr}"

    # Invalid → exit 1
    result_invalid = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), str(invalid_path)],
        capture_output=True,
        text=True,
    )
    assert result_invalid.returncode == 1, f"Expected exit 1, got {result_invalid.returncode}. stderr: {result_invalid.stderr}"

    logger.critical("[IMP:9][test] test_cli_exit_code — CLI exit codes correct (0=valid, 1=invalid)")


# endregion
