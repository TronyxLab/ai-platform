"""
# GREP_SUMMARY: test_reconciler, converge, r1, reconcile-perms, _is_stub, stub-detection, mock-docker, subprocess
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R1 reconcile_perms 4× (skipped/mutated/lib-excluded/dry-run) → ◇ _is_stub 3× (stub/non-stub/missing) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for reconciler.py facade units: R1 reconcile_perms + _is_stub (shared stub_detection).
## @scope    Tests reconciler.reconcile_perms and is_stub_ai_platform_yaml with tmp_path fixtures.
##           Does NOT require a real docker daemon or root privileges.
## @invariants
##   - File operations use tmp_path exclusively — never /var/log, /opt, /etc
##   - Each test validates IMP:9 business logic log presence via caplog
## @rationale Direct function testing with tmp_path for file-based units (R1, stub).
##   Разбит из монолита test_reconciler.py (DevPlan 118 F6): 34 теста → 6 файлов по converge-подмодулям.
## @changes
##   2026-07-22 · Created (W4-E3 extraction from converge.sh)
##   2026-08-02 · F6 split — reconciled perms + stub остались (DevPlan 118)
# endregion MODULE_CONTRACT
"""

import logging
import os
import sys
from pathlib import Path

import pytest

# Load the LDD trajectory decorator from shared conftest
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

import core.internal.bootstrap.converge.infra as infra
from core.internal.shared.stub_detection import is_stub_ai_platform_yaml

# Re-export for fixture cleanups
MODULE = reconciler


# ═══════════════════════════════════════════════════════════════════
# region Fixtures


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    infra.reset_state()
    infra.node_name = "test-node"
    infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")
    yield


# endregion Fixtures


# region FUNC_test_reconcile_perms_skipped
## 🧪 TRAP[TEST] · R1 skipped · Scenario: all scripts already executable
## · Regression: converge.sh line 293-296 — no non-exec files → SKIP
## · Last fail: never
## · Remove if: reconciler.R1 logic fundamentally changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_perms_skipped(tmp_path, caplog):
    """R1: All scripts already executable → status=skipped."""
    caplog.set_level(logging.INFO)

    # Create a test script with u+x already set
    core_dir = tmp_path / "core"
    scripts_dir = core_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    test_script = scripts_dir / "test.sh"
    test_script.write_text("#!/bin/bash\necho hello\n")
    os.chmod(str(test_script), 0o755)  # already executable

    entry = reconciler.reconcile_perms(str(core_dir), dry_run=False, report_only=False)

    assert entry["unit"] == "R1"
    assert entry["status"] == "skipped"

    # LDD trajectory: IMP:9 business logic log
    found_imp9 = any("[IMP:9]" in r.message and "SKIP" in r.message for r in caplog.records)
    assert found_imp9, "No IMP:9 log for R1 skipped"


# endregion FUNC_test_reconcile_perms_skipped


# region FUNC_test_reconcile_perms_mutated
## 🧪 TRAP[TEST] · R1 mutated · Scenario: non-executable scripts found and fixed
## · Regression: converge.sh lines 309-318 — chmod ug+x applied
## · Last fail: never
## · Remove if: reconciler.R1 logic fundamentally changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_perms_mutated(tmp_path, caplog):
    """R1: Non-executable scripts found → status=mutated."""
    caplog.set_level(logging.INFO)

    core_dir = tmp_path / "core"
    scripts_dir = core_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    test_script = scripts_dir / "test.sh"
    test_script.write_text("#!/bin/bash\necho hello\n")
    # NOT setting executable bit — keep 644

    entry = reconciler.reconcile_perms(str(core_dir), dry_run=False, report_only=False)

    assert entry["unit"] == "R1"
    assert entry["status"] == "mutated"
    assert "1 files fixed" in entry["detail"]

    # Verify the file is now executable
    assert os.access(str(test_script), os.X_OK), "File should now be executable"

    found_imp9 = any("[IMP:9]" in r.message and "Fixed" in r.message for r in caplog.records)
    assert found_imp9, "No IMP:9 log for R1 mutated"


# endregion FUNC_test_reconcile_perms_mutated


# region FUNC_test_reconcile_perms_lib_skipped
## 🧪 TRAP[TEST] · R1 lib excluded · Scenario: files under core/lib/ are NOT modified
## · Regression: converge.sh lines 282 — -not -path '*/lib/*'
## · Last fail: never
## · Remove if: reconciler.R1 logic fundamentally changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_perms_lib_skipped(tmp_path, caplog):
    """R1: Files under core/lib/ are excluded from executable bit reconciliation."""
    caplog.set_level(logging.INFO)

    core_dir = tmp_path / "core"
    lib_dir = core_dir / "lib"
    lib_dir.mkdir(parents=True)
    # Create a non-executable .sh file under lib/ — should NOT be touched
    lib_script = lib_dir / "helper.sh"
    lib_script.write_text("#!/bin/bash\necho helper\n")
    # NOT setting executable bit

    entry = reconciler.reconcile_perms(str(core_dir), dry_run=False, report_only=False)

    # Should be skipped — lib files are excluded
    assert entry["status"] == "skipped"

    # Verify lib file is STILL not executable
    assert not os.access(str(lib_script), os.X_OK), "Lib file should remain non-executable"


# endregion FUNC_test_reconcile_perms_lib_skipped


# region FUNC_test_reconcile_perms_dry_run
## 🧪 TRAP[TEST] · R1 dry-run · Scenario: --dry-run reports but does not mutate
## · Regression: converge.sh lines 289-303
## · Last fail: never
## · Remove if: reconciler.R1 logic fundamentally changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_perms_dry_run(tmp_path, caplog):
    """R1: --dry-run reports would-fix but does not chmod."""
    caplog.set_level(logging.INFO)

    core_dir = tmp_path / "core"
    scripts_dir = core_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    test_script = scripts_dir / "test.sh"
    test_script.write_text("#!/bin/bash\necho hello\n")
    mode_before = os.stat(str(test_script)).st_mode

    entry = reconciler.reconcile_perms(str(core_dir), dry_run=True, report_only=False)

    assert entry["status"] == "mutated"
    assert "would get ug+x" in entry["detail"]

    # File should NOT have been modified
    mode_after = os.stat(str(test_script)).st_mode
    assert mode_before == mode_after, "File should not be modified in dry-run mode"


# endregion FUNC_test_reconcile_perms_dry_run


# region FUNC_test_is_stub_true
## 🧪 TRAP[TEST] · _is_stub true · Scenario: file contains GENERATED-STUB marker
## · Regression: converge.sh lines 655-663
## · Last fail: never
## · Remove if: _is_stub logic changes
@ldd_trajectory
def test_is_stub_true(tmp_path, caplog):
    """_is_stub: file with GENERATED-STUB → returns True."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] _is_stub positive detection")
    stub_file = tmp_path / "ai-platform.yaml"
    stub_file.write_text("# GENERATED-STUB by converge\nproject: myapp\nservice: myapp\n")
    assert is_stub_ai_platform_yaml(str(stub_file)) is True


# endregion FUNC_test_is_stub_true


# region FUNC_test_is_stub_false
## 🧪 TRAP[TEST] · _is_stub false · Scenario: file without GENERATED-STUB marker
## · Regression: converge.sh lines 655-663 — real config
## · Last fail: never
## · Remove if: _is_stub logic changes
@ldd_trajectory
def test_is_stub_false(tmp_path, caplog):
    """_is_stub: file without GENERATED-STUB → returns False."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] _is_stub negative detection")
    real_file = tmp_path / "ai-platform.yaml"
    real_file.write_text("project: myapp\nservice: myapp\ndomain: myapp.example.com\n")
    assert is_stub_ai_platform_yaml(str(real_file)) is False


# endregion FUNC_test_is_stub_false


# region FUNC_test_is_stub_missing
## 🧪 TRAP[TEST] · _is_stub missing · Scenario: file does not exist
## · Regression: converge.sh lines 659-661 — missing file is not a stub
## · Last fail: never
## · Remove if: _is_stub logic changes
@ldd_trajectory
def test_is_stub_missing(tmp_path, caplog):
    """_is_stub: missing file → returns False."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] _is_stub missing-file")
    missing = str(tmp_path / "nonexistent.yaml")
    assert is_stub_ai_platform_yaml(missing) is False


# endregion FUNC_test_is_stub_missing
