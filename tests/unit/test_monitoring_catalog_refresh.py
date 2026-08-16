# GREP_SUMMARY: test-monitoring-catalog-refresh generate-catalog subprocess noop failed created
# STRUCTURE: ┌4 test functions┐ → ◇ script missing (1) → ◇ created (1) → ◇ CalledProcessError (1) → ◇ TimeoutExpired (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/monitoring/catalog_refresh.py — refresh_catalog()
#            (DevPlan 117 G T54 extraction).
## @scope    No real subprocess — mocked.
## @invariants
##   - All subprocess calls mocked
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T54 §TEST_SPEC — catalog_refresh direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T54 — created
# endregion MODULE_CONTRACT

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from monitoring.catalog_refresh import refresh_catalog

pytestmark = pytest.mark.static_audit


# 🧪 TRAP[TEST] · Regression · Scenario: script missing
# · Expect: noop
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: refresh_catalog logic changes
def test_catalog_script_missing(tmp_path: Path, caplog) -> None:
    """Catalog script not found → noop."""
    caplog.set_level(0)
    result = refresh_catalog(tmp_path)

    assert result.status == "noop"
    assert result.component == "catalog"


# 🧪 TRAP[TEST] · Regression · Scenario: script exists + success
# · Expect: created
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: refresh_catalog success path changes
def test_catalog_created(tmp_path: Path, caplog) -> None:
    """Script exists + subprocess 0 → created."""
    caplog.set_level(0)
    script_dir = tmp_path / "core" / "internal" / "catalog"
    script_dir.mkdir(parents=True)
    script = script_dir / "generate-catalog.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")

    with mock.patch("monitoring.catalog_refresh.subprocess.run", return_value=mock.MagicMock()):
        result = refresh_catalog(tmp_path)

    assert result.status == "created"


# 🧪 TRAP[TEST] · Regression · Scenario: CalledProcessError
# · Expect: failed
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: refresh_catalog error handling changes
def test_catalog_called_process_error(tmp_path: Path, caplog) -> None:
    """CalledProcessError → failed."""
    caplog.set_level(0)
    script_dir = tmp_path / "core" / "internal" / "catalog"
    script_dir.mkdir(parents=True)
    script = script_dir / "generate-catalog.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")

    with mock.patch(
        "monitoring.catalog_refresh.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "script", stderr="boom"),
    ):
        result = refresh_catalog(tmp_path)

    assert result.status == "failed"


# 🧪 TRAP[TEST] · Regression · Scenario: TimeoutExpired
# · Expect: failed
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: refresh_catalog timeout handling changes
def test_catalog_timeout(tmp_path: Path, caplog) -> None:
    """TimeoutExpired → failed."""
    caplog.set_level(0)
    script_dir = tmp_path / "core" / "internal" / "catalog"
    script_dir.mkdir(parents=True)
    script = script_dir / "generate-catalog.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")

    with mock.patch(
        "monitoring.catalog_refresh.subprocess.run",
        side_effect=subprocess.TimeoutExpired("script", 60),
    ):
        result = refresh_catalog(tmp_path)

    assert result.status == "failed"
