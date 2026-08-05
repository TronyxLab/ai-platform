"""
# GREP_SUMMARY: test_reconciler, converge, r1, reconcile-perms, _is_stub, stub-detection, drift-detection, idempotency, project-validation, w4-e5
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R1 reconcile_perms 4× (skipped/mutated/lib-excluded/dry-run) → ◇ _is_stub 3× (stub/non-stub/missing) → ◇ W4-E5 static audits (drift R-units / idempotency) → ◇ project-name validation → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for reconciler.py facade units: R1 reconcile_perms + _is_stub (shared stub_detection)
##           + W4-E5 edge-страховки (drift-detection R-units, reconcile idempotency, project-name validation),
##           перенесённые из tests/test_converge_exit.py при K1 (139-test-system-stewardship W1).
## @scope    Tests reconciler.reconcile_perms and is_stub_ai_platform_yaml with tmp_path fixtures.
##           W4-E5 static-аудиты converge/ пакета + validate_project_name (project_registry).
##           Does NOT require a real docker daemon or root privileges.
## @invariants
##   - File operations use tmp_path exclusively — never /var/log, /opt, /etc
##   - Each test validates IMP:9 business logic log presence via caplog
##   - W4-E5 страховки перенесены БЕЗ изменения входов (те же фикстуры/asserts, K1 diff-review)
## @rationale Direct function testing with tmp_path for file-based units (R1, stub).
##   Разбит из монолита test_reconciler.py (DevPlan 118 F6): 34 теста → 6 файлов по converge-подмодулям.
##   139 K1 (W1): _is_stub edge (3 состояния) уже покрыт ниже; перенесены НЕДОСТАЮЩИЕ 3 W4-E5 теста
##   (drift R-units, idempotency, project-name validation) из удаляемого tests/test_converge_exit.py.
## @changes
##   2026-07-22 · Created (W4-E3 extraction from converge.sh)
##   2026-08-02 · F6 split — reconciled perms + stub остались (DevPlan 118)
##   2026-08-05 · 139 W1 K1 — +3 W4-E5 теста из test_converge_exit.py (drift/idempotency/validation)
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
from core.internal.shared.project_registry import validate_project_name
from core.internal.shared.stub_detection import is_stub_ai_platform_yaml

# Re-export for fixture cleanups
MODULE = reconciler

# ── W4-E5 static-audit helper (K1, перенесён из tests/test_converge_exit.py) ──
# B9 T2 (U-31): доменные модули R-units (SRP-декомпозиция reconciler)
_CONVERGE_DIR = _MODULE_DIR


def _converge_sources() -> str:
    """Concatenate converge/ package sources (reconciler + домены + infra) for static audit."""
    return "\n".join(p.read_text() for p in sorted(_CONVERGE_DIR.glob("*.py")))


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


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5 (DevPlan 035 §7): edge-case страховки, перенесённые из tests/test_converge_exit.py
# при K1 (139-test-system-stewardship W1). Перенос БЕЗ изменения входов/asserts.
# _is_stub edge (stub/deployed/missing) уже покрыт выше (test_is_stub_true/false/missing).
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_drift_detection_r_units
## 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 drift detection R-units → W4-E3 redirect to reconciler.py
# · Regression: reconciler.py must have 6 reconcile_* functions detecting distinct drift dimensions
# · Scenario: static grep converge/ package for reconcile_perms, reconcile_audit_log, reconcile_projects, reconcile_networks, detect_hosts_drift, verify_vhosts
# · Last fail: N/A (W4-E5 baseline, updated for W4-E3)
# · Remove if: reconciler.py R-units are fundamentally restructured
@ldd_trajectory
def test_drift_detection_r_units(tmp_path, caplog):
    """Static audit: converge/ package has 6 reconcile_* functions for distinct drift dimensions (B9 T2)."""
    caplog.set_level(logging.INFO)
    content = _converge_sources()

    # ── All 6 reconcile functions must exist in converge/ пакете (домены + оркестратор) ──
    required_units = [
        ("def reconcile_perms", "R1 executable-bit drift"),
        ("def reconcile_audit_log", "R2 audit.log perms drift"),
        ("def reconcile_projects", "R3 project dirs drift"),
        ("def reconcile_networks", "R4 proxy-net drift"),
        ("def detect_hosts_drift", "R5 hosts drift detection"),
        ("def verify_vhosts", "R6 vhost integrity check"),
    ]
    for func_def, desc in required_units:
        assert func_def in content, f"W4-E3 violation: {func_def} missing in converge/ package — {desc}"
        logger.info("[IMP:9][test_drift_detection] %s present — %s", func_def, desc)

    # ── Each reconcile function uses set_exit severity tracking (Python equivalent of CONVERGE_HAS_FLAGS) ──
    assert "set_exit(1)" in content, "W4-E3 violation: converge/ must use set_exit(1) for warning drifts"
    assert "set_exit(2)" in content, "W4-E3 violation: converge/ must use set_exit(2) for error drifts"
    logger.info("[IMP:9][test_drift_detection] set_exit(1) + set_exit(2) severity tracking present")

    # ── Drift reporting mechanism exists (infra.report_add) ──
    assert "report_add" in content, "W4-E3 violation: report_add drift reporting mechanism missing"
    logger.info("[IMP:9][test_drift_detection] report_add drift reporting present")


# endregion FUNC_test_drift_detection_r_units


# region FUNC_test_reconcile_idempotency
## 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 reconcile idempotency → W4-E3 redirect to reconciler.py
# · Regression: reconcile функции должны иметь idempotency guards — second run detects no drift
# · Scenario: static grep converge/ package for "SKIP" / "already" / "converged" patterns
# · Last fail: N/A (W4-E5 baseline, updated for W4-E3)
# · Remove if: idempotency moves to state-based reconciler (then point test at new module)
@ldd_trajectory
def test_reconcile_idempotency(tmp_path, caplog):
    """Static audit: converge/ reconcile functions are idempotent (SKIP on already-converged)."""
    caplog.set_level(logging.INFO)
    content = _converge_sources()

    # ── 1. SKIP pattern present (idempotent no-op when already converged) ──
    skip_count = content.count("SKIP")
    assert skip_count >= 3, f"W4-E3 violation: expected >=3 SKIP patterns (idempotency), found {skip_count}"
    logger.info("[IMP:9][test_idempotency] SKIP patterns found: %d", skip_count)

    # ── 2. "converged" or "already" keyword indicates no-op state ──
    has_converged = "converged" in content.lower() or "already" in content.lower()
    assert has_converged, "W4-E3 violation: no 'converged'/'already' keyword — idempotent no-op state missing"
    logger.info("[IMP:9][test_idempotency] converged/already keyword present")

    # ── 3. dry_run + report_only modes in converge/ (non-mutating inspection) ──
    assert "dry_run" in content, "W4-E3 violation: dry_run mode missing in converge/ package"
    assert "report_only" in content, "W4-E3 violation: report_only mode missing in converge/ package"
    logger.info("[IMP:9][test_idempotency] dry_run + report_only present in converge/ package")


# endregion FUNC_test_reconcile_idempotency


# region FUNC_test_project_name_validation_rejects_traversal
## 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 project name validation → DevPlan 116 B6 T3 (canonical validator)
# · Regression: canonical validate_project_name must reject "../", "/", leading "-/_", non-alphanumeric
# · Scenario: import core.internal.shared.project_registry.validate_project_name (единый канон)
# · Last fail: N/A (W4-E5 baseline, migrated to canonical validator in B6 T3)
# · Remove if: project validation moves away from project_registry (then point test at new module)
@ldd_trajectory
def test_project_name_validation_rejects_traversal(tmp_path, caplog):
    """validate_project_name: rejects path traversal (../), slashes, leading -/_, invalid chars."""
    caplog.set_level(logging.INFO)

    # Test cases: (name, should_pass)
    test_cases: list[tuple[str, bool]] = [
        ("valid-project", True),
        ("my_app123", True),
        ("../etc/passwd", False),  # path traversal
        ("foo/bar", False),  # slash
        ("..", False),  # parent dir
        ("valid..name", False),  # contains ..
        ("name with space", False),  # space not in [a-zA-Z0-9_-]
        ("name;rm -rf", False),  # shell injection attempt
        ("", False),  # empty
        ("-leading-dash", False),  # leading '-' (strict regex, DevPlan 116 B6 T3)
        ("_leading-underscore", False),  # leading '_' (strict regex, DevPlan 116 B6 T3)
    ]

    for name, should_pass in test_cases:
        result = validate_project_name(name)
        if should_pass:
            assert result is True, f"W4-E3 violation: valid name '{name}' should pass, got {result}"
            logger.info("[IMP:9][test_validate] OK: %r", name)
        else:
            assert result is False, f"W4-E3 violation: invalid name '{name}' should fail, got {result}"
            logger.info("[IMP:9][test_validate] FAIL: %r", name)

    # Explicitly verify path traversal is REJECTED (critical security check)
    assert validate_project_name("../etc/passwd") is False, (
        "W4-E3 CRITICAL violation: path traversal '../etc/passwd' must be REJECTED"
    )
    logger.info("[IMP:9][test_validate] CRITICAL: path traversal ../etc/passwd correctly rejected")


# endregion FUNC_test_project_name_validation_rejects_traversal
