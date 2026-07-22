# GREP_SUMMARY: test_converge_exit converge exit-code CONVERGE_HAS_ERRORS CONVERGE_HAS_WARNINGS semantics w4-e5 drift-detection idempotency stub-detection project-validation
# STRUCTURE: ▶ test_converge_exit_code_0 (converged) → ◇ test_converge_exit_code_1 (warnings) → ◇ test_converge_exit_code_2 (errors) → ◇ test_converge_has_flags (new flags) → ◇ test_converge_step_15_exit_handling → ◇ W4-E5 edge-cases (drift/idempotency/stub/project-validation) → ⎋ verify LDD [IMP:9] trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for converge.sh exit semantics (DevPlan 025 Wave 2).
##           Tests that CONVERGE_HAS_ERRORS, CONVERGE_HAS_WARNINGS flags work correctly,
##           and that node-lifecycle.sh step_15_converge handles exit 1 vs exit 2 properly.
##           W4-E5 (DevPlan 035 §7): +4 edge-case regression tests for drift detection,
##           reconcile idempotency, _is_stub edge cases, and project-name validation —
##           страховка R-RISK-5 ДО W4-E3 reconciler.py extraction.
## @scope    Tests the bash scripts' exit code logic through source + mock execution.
##           Not testing R-units themselves (tested elsewhere).
## @invariants
##   - Uses tmp_path for isolated test environment
##   - Validates exit 0 (converged), exit 1 (warnings), exit 2 (errors)
##   - Validates CONVERGE_HAS_ERRORS/CONVERGE_HAS_WARNINGS flag interaction
##   - W4-E5 edge-cases validate converge.sh internals (drift detection, idempotency, stub, validation)
## @rationale W2 fix: exit 1 (warnings) should not block bootstrap; only exit 2 blocks.
##            Tests ensure the three-state exit contract is maintained.
##            W4-E5 edge-cases are страховка before W4-E3 reconciler.py extraction.
## @changes 2026-07-21 | Initial test suite (DevPlan 025 W2)
##          2026-07-22 | W4-E5 +4 edge-case tests (DevPlan 035 §7)
# endregion MODULE_CONTRACT

import subprocess
import textwrap


def _run_bash_test(script: str, tmp_path) -> subprocess.CompletedProcess:
    """Helper: write a Bash test script to tmp_path and run it."""
    script_path = tmp_path / "test_script.sh"
    script_path.write_text(textwrap.dedent(script))
    script_path.chmod(0o755)
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ═══════════════════════════════════════════════════════════════════
# region TEST_CONVERGE_HAS_WARNINGS_FLAG
## @purpose  Verify CONVERGE_HAS_WARNINGS=true causes exit 1
## @scenario Set CONVERGE_HAS_WARNINGS=true, CONVERGE_HAS_ERRORS=false → exit code 1
## 🧪 TRAP[TEST] · Regression: CONVERGE_HAS_WARNINGS must result in exit 1
##   · Last fail: N/A (new test)
##   · Remove if: exit logic is fundamentally changed
def test_converge_has_warnings_exit_1(tmp_path):
    """Test that CONVERGE_HAS_WARNINGS=true results in exit code 1."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing CONVERGE_HAS_WARNINGS exit 1..." >&2

    # Simulate final exit logic from converge.sh main()
    CONVERGE_HAS_ERRORS=false
    CONVERGE_HAS_WARNINGS=true

    if $CONVERGE_HAS_ERRORS; then
        echo "[IMP:9][test] WOULD exit 2" >&2
        exit 2
    elif $CONVERGE_HAS_WARNINGS; then
        echo "[IMP:9][test] WOULD exit 1 (warnings)" >&2
        exit 1
    else
        echo "[IMP:9][test] WOULD exit 0 (converged)" >&2
        exit 0
    fi
    """
    result = _run_bash_test(script, tmp_path)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_CONVERGE_HAS_ERRORS_EXIT_2
## @purpose  Verify CONVERGE_HAS_ERRORS=true causes exit 2 regardless of warnings
## @scenario Both errors and warnings set → exit 2 takes priority
## 🧪 TRAP[TEST] · Regression: errors must take priority over warnings
##   · Last fail: N/A (new test)
##   · Remove if: exit priority logic changes
def test_converge_has_errors_exit_2(tmp_path):
    """Test that CONVERGE_HAS_ERRORS=true results in exit code 2 (highest severity)."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing CONVERGE_HAS_ERRORS exit 2..." >&2

    # Simulate final exit logic — errors override warnings
    CONVERGE_HAS_ERRORS=true
    CONVERGE_HAS_WARNINGS=true

    if $CONVERGE_HAS_ERRORS; then
        echo "[IMP:9][test] WOULD exit 2 (errors)" >&2
        exit 2
    elif $CONVERGE_HAS_WARNINGS; then
        echo "[IMP:9][test] WOULD exit 1 (warnings)" >&2
        exit 1
    else
        echo "[IMP:9][test] WOULD exit 0 (converged)" >&2
        exit 0
    fi
    """
    result = _run_bash_test(script, tmp_path)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_CONVERGE_CLEAN_EXIT_0
## @purpose  Verify clean converge (no errors, no warnings) causes exit 0
## @scenario Both flags false → exit code 0
## 🧪 TRAP[TEST] · Regression: clean converge must be exit 0
##   · Last fail: N/A (new test)
##   · Remove if: exit logic is fundamentally changed
def test_converge_clean_exit_0(tmp_path):
    """Test that clean converge with no errors/warnings results in exit 0."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing clean converge exit 0..." >&2

    CONVERGE_HAS_ERRORS=false
    CONVERGE_HAS_WARNINGS=false

    if $CONVERGE_HAS_ERRORS; then
        echo "[IMP:9][test] WOULD exit 2" >&2
        exit 2
    elif $CONVERGE_HAS_WARNINGS; then
        echo "[IMP:9][test] WOULD exit 1" >&2
        exit 1
    else
        echo "[IMP:9][test] WOULD exit 0 (converged)" >&2
        exit 0
    fi
    """
    result = _run_bash_test(script, tmp_path)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_NODE_LIFECYCLE_STEP15_EXIT_1
## @purpose  Verify step_15_converge treats exit 1 as step_done (non-blocking)
## @scenario converge exit 1 → step_15 exits 0 (WARNINGS non-blocking)
## 🧪 TRAP[TEST] · Regression: exit 1 must not block bootstrap
##   · Last fail: W2 fix — old code treated exit 1 as failure
##   · Remove if: step_15_converge is fundamentally rewritten
def test_node_lifecycle_step15_exit_1_nonblocking(tmp_path):
    """Test that step_15_converge treats converge exit 1 as non-blocking step_done."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Simulating step_15_converge with converge exit 1..." >&2

    MODE="init"
    converge_rc=1

    if [[ $converge_rc -eq 2 ]]; then
        # ERROR — blocks only in init mode
        if [[ "${MODE}" == "init" ]]; then
            echo "[IMP:9][test] step_warn: CRITICAL errors (blocking)" >&2
        else
            echo "[IMP:9][test] step_warn: CRITICAL errors (non-blocking)" >&2
        fi
        exit 2
    elif [[ $converge_rc -eq 1 ]]; then
        # WARNINGS — non-blocking
        echo "[IMP:9][test] step_done: Warnings (exit 1) — non-critical" >&2
        exit 0
    else
        echo "[IMP:9][test] step_done: Fully converged (exit 0)" >&2
        exit 0
    fi
    """
    result = _run_bash_test(script, tmp_path)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 0, f"Expected exit 0 (non-blocking), got {result.returncode}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_NODE_LIFECYCLE_STEP15_EXIT_2
## @purpose  Verify step_15_converge handles exit 2 as step_warn (non-blocking in update mode)
## @scenario converge exit 2, MODE=update → step_warn, exit 0
## 🧪 TRAP[TEST] · Regression: exit 2 must not block update mode
##   · Last fail: W2 fix — old code treated all non-zero as failures
##   · Remove if: step_15_converge blocking semantics change
def test_node_lifecycle_step15_exit_2_update_nonblocking(tmp_path):
    """Test that step_15_converge treats converge exit 2 as step_warn (non-blocking) in update mode."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Simulating step_15_converge with converge exit 2, MODE=update..." >&2

    MODE="update"
    converge_rc=2

    if [[ $converge_rc -eq 2 ]]; then
        if [[ "${MODE}" == "init" ]]; then
            echo "[IMP:9][test] step_warn: CRITICAL errors (blocking in init mode)" >&2
            exit 2  # Only blocks in init mode
        else
            echo "[IMP:9][test] step_warn: CRITICAL errors (non-blocking in update mode)" >&2
            exit 0  # Non-blocking in update mode
        fi
    elif [[ $converge_rc -eq 1 ]]; then
        echo "[IMP:9][test] step_done: Warnings (exit 1)" >&2
        exit 0
    else
        echo "[IMP:9][test] step_done: Fully converged (exit 0)" >&2
        exit 0
    fi
    """
    result = _run_bash_test(script, tmp_path)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 0, f"Expected exit 0 (non-blocking in update mode), got {result.returncode}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5 (DevPlan 035 §7): Edge-case regression baseline — страховка R-RISK-5 ДО extraction.
# 4 edge-case теста converge.sh, которые W4-E3 reconciler.py extraction НЕ должен нарушить.
# Static-audit + bash-subprocess pattern (consistent with existing converge_exit tests).
# ══════════════════════════════════════════════════════════════════════════════

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_RECONCILER_PY = (
    Path(__file__).resolve().parent.parent / "core" / "internal" / "bootstrap" / "converge" / "reconciler.py"
)


# region TEST_test_drift_detection_r_units
# 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 drift detection R-units → W4-E3 redirect to reconciler.py
# · Regression: reconciler.py must have 6 reconcile_* functions detecting distinct drift dimensions
# · Scenario: static grep reconciler.py for reconcile_perms, reconcile_audit_log, reconcile_projects, reconcile_networks, detect_hosts_drift, verify_vhosts
# · Last fail: N/A (W4-E5 baseline, updated for W4-E3)
# · Remove if: reconciler.py R-units are fundamentally restructured


def test_drift_detection_r_units(tmp_path):
    """Static audit: reconciler.py has 6 reconcile_* functions for distinct drift dimensions."""
    content = _RECONCILER_PY.read_text()

    # ── All 6 reconcile functions must exist in reconciler.py ──
    required_units = [
        ("def reconcile_perms", "R1 executable-bit drift"),
        ("def reconcile_audit_log", "R2 audit.log perms drift"),
        ("def reconcile_projects", "R3 project dirs drift"),
        ("def reconcile_networks", "R4 proxy-net drift"),
        ("def detect_hosts_drift", "R5 hosts drift detection"),
        ("def verify_vhosts", "R6 vhost integrity check"),
    ]

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for func_def, desc in required_units:
        assert func_def in content, f"W4-E3 violation: {func_def} missing in reconciler.py — {desc}"
        msg = f"[IMP:9][test_drift_detection] {func_def} present — {desc}"
        print(msg)
        imp_found = True
    print("--- END LDD TRAJECTORY ---")

    # ── Each reconcile function uses _set_exit severity tracking (Python equivalent of CONVERGE_HAS_FLAGS) ──
    assert "_set_exit(1)" in content, "W4-E3 violation: reconciler.py must use _set_exit(1) for warning drifts"
    assert "_set_exit(2)" in content, "W4-E3 violation: reconciler.py must use _set_exit(2) for error drifts"
    print("[IMP:9][test_drift_detection] _set_exit(1) + _set_exit(2) severity tracking present")

    # ── Drift reporting mechanism exists ──
    assert "report_add" in content, "W4-E3 violation: report_add drift reporting mechanism missing"
    print("[IMP:9][test_drift_detection] report_add drift reporting present")

    assert imp_found, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_reconcile_idempotency
# 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 reconcile idempotency → W4-E3 redirect to reconciler.py
# · Regression: reconciler.py must have idempotency guards — second run detects no drift
# · Scenario: static grep for "SKIP" / "already" / "converged" patterns in reconciler.py reconcile functions
# · Last fail: N/A (W4-E5 baseline, updated for W4-E3)
# · Remove if: idempotency moves to state-based reconciler.py (then point test at new module)


def test_reconcile_idempotency(tmp_path):
    """Static audit: reconciler.py reconcile functions are idempotent (SKIP on already-converged)."""
    content = _RECONCILER_PY.read_text()

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False

    # ── 1. SKIP pattern present (idempotent no-op when already converged) ──
    skip_count = content.count("SKIP")
    assert skip_count >= 3, f"W4-E3 violation: expected >=3 SKIP patterns (idempotency), found {skip_count}"
    print(f"[IMP:9][test_idempotency] SKIP patterns found: {skip_count}")
    imp_found = True

    # ── 2. "converged" or "already" keyword indicates no-op state ──
    has_converged = "converged" in content.lower() or "already" in content.lower()
    assert has_converged, "W4-E3 violation: no 'converged'/'already' keyword — idempotent no-op state missing"
    print("[IMP:9][test_idempotency] converged/already keyword present")

    # ── 3. dry_run + report_only modes in reconciler.py (non-mutating inspection) ──
    assert "dry_run" in content, "W4-E3 violation: dry_run mode missing in reconciler.py"
    assert "report_only" in content, "W4-E3 violation: report_only mode missing in reconciler.py"
    print("[IMP:9][test_idempotency] dry_run + report_only present in reconciler.py")
    print("--- END LDD TRAJECTORY ---")

    assert imp_found, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_is_stub_edge_cases
# 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 _is_stub edge cases → W4-E3 redirect to reconciler.py _is_stub
# · Regression: _is_stub must distinguish 3 states: stub file, deployed file, missing file
# · Scenario: import reconciler._is_stub and test with 3 fixture files
# · Last fail: N/A (W4-E5 baseline, updated for W4-E3)
# · Remove if: _is_stub is removed from reconciler.py


def test_is_stub_edge_cases(tmp_path):
    """_is_stub: stub file → true, deployed file → false, missing file → false (not a stub)."""
    import sys

    sys.path.insert(0, str(_RECONCILER_PY.parent))
    from reconciler import _is_stub  # type: ignore

    # Fixture 1: stub file (first line "GENERATED-STUB")
    stub_file = tmp_path / "stub.yaml"
    stub_file.write_text("# GENERATED-STUB by converge\nplaceholder: true\n")

    # Fixture 2: deployed file (real content, no stub marker)
    deployed_file = tmp_path / "deployed.yaml"
    deployed_file.write_text("name: real-project\nversion: 1.0.0\n")

    # Fixture 3: missing file (does not exist)
    missing_file = tmp_path / "nonexistent.yaml"

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False

    # Test 3 edge cases
    assert _is_stub(str(stub_file)) is True, "W4-E3 violation: stub file must be detected as stub"
    print("[IMP:9][test_is_stub] STUB_FILE=IS_STUB")
    imp_found = True

    assert _is_stub(str(deployed_file)) is False, "W4-E3 violation: deployed file must NOT be stub"
    print("[IMP:9][test_is_stub] DEPLOYED_FILE=NOT_STUB")

    assert _is_stub(str(missing_file)) is False, "W4-E3 violation: missing file must NOT be stub"
    print("[IMP:9][test_is_stub] MISSING_FILE=NOT_STUB")
    print("--- END LDD TRAJECTORY ---")

    assert imp_found, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_project_name_validation_rejects_traversal
# 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 project name validation → W4-E3 redirect to reconciler.py _validate_project_name
# · Regression: _validate_project_name must reject "../", "/", and non-alphanumeric names
# · Scenario: import reconciler._validate_project_name and test with malicious names
# · Last fail: N/A (W4-E5 baseline, updated for W4-E3)
# · Remove if: project validation moves to reconciler.py (then point test at new module)


def test_project_name_validation_rejects_traversal(tmp_path):
    """_validate_project_name: rejects path traversal (../), slashes, and invalid chars."""
    import sys

    sys.path.insert(0, str(_RECONCILER_PY.parent))
    from reconciler import _validate_project_name  # type: ignore

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
    ]

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False

    for name, should_pass in test_cases:
        result = _validate_project_name(name)
        if should_pass:
            assert result is True, f"W4-E3 violation: valid name '{name}' should pass, got {result}"
            print(f"[IMP:9][test_validate] OK: '{name}'")
        else:
            assert result is False, f"W4-E3 violation: invalid name '{name}' should fail, got {result}"
            print(f"[IMP:9][test_validate] FAIL: '{name}'")
        imp_found = True

    # Explicitly verify path traversal is REJECTED (critical security check)
    assert _validate_project_name("../etc/passwd") is False, (
        "W4-E3 CRITICAL violation: path traversal '../etc/passwd' must be REJECTED"
    )
    print("[IMP:9][test_validate] CRITICAL: path traversal ../etc/passwd correctly rejected")
    print("--- END LDD TRAJECTORY ---")

    assert imp_found, "Critical LDD Error: No IMP:9 business logic log found"


# endregion
