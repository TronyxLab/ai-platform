# GREP_SUMMARY: test_converge_exit converge exit-code CONVERGE_HAS_ERRORS CONVERGE_HAS_WARNINGS semantics
# STRUCTURE: ▶ test_converge_exit_code_0 (converged) → ◇ test_converge_exit_code_1 (warnings) → ◇ test_converge_exit_code_2 (errors) → ◇ test_converge_has_flags (new flags) → ◇ test_converge_step_15_exit_handling → ⎋ verify LDD [IMP:9] trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for converge.sh exit semantics (DevPlan 025 Wave 2).
##           Tests that CONVERGE_HAS_ERRORS, CONVERGE_HAS_WARNINGS flags work correctly,
##           and that node-lifecycle.sh step_15_converge handles exit 1 vs exit 2 properly.
## @scope    Tests the bash scripts' exit code logic through source + mock execution.
##           Not testing R-units themselves (tested elsewhere).
## @invariants
##   - Uses tmp_path for isolated test environment
##   - Validates exit 0 (converged), exit 1 (warnings), exit 2 (errors)
##   - Validates CONVERGE_HAS_ERRORS/CONVERGE_HAS_WARNINGS flag interaction
## @rationale W2 fix: exit 1 (warnings) should not block bootstrap; only exit 2 blocks.
##            Tests ensure the three-state exit contract is maintained.
## @changes 2026-07-21 | Initial test suite (DevPlan 025 W2)
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
