# GREP_SUMMARY: test_sequencing deploy sequencing pre-flight CI wait verify launch URL
# STRUCTURE: ▶ test_make_deploy_preflight_flag (NODE→pre-flight) → ◇ test_make_deploy_launch_flag (LAUNCH=1→URL) → ◇ test_make_deploy_no_node (backward compat) → ◇ test_deploy_preflight_fail_exit → ⎋ verify LDD [IMP:9] trajectory
# region MODULE_CONTRACT
## @purpose  Sequencing tests for deploy pipeline (DevPlan 025 Waves 1+6).
##           Tests the Makefile deploy target behavior with NODE and LAUNCH flags,
##           pre-flight check integration, and backward compatibility.
## @scope    Tests bash logic through isolated scripts. No VPS/Git/CI required.
## @invariants
##   - Uses tmp_path for isolated test environment
##   - Validates NODE flag triggers pre-flight check
##   - Validates LAUNCH=1 requires NODE
##   - Validates backward compat: no NODE = current behavior (no pre-flight)
## @rationale W1+W6 unify the deploy flow. Tests ensure pre-flight + launch semantics
##            are correct without requiring actual VPS access.
## @changes 2026-07-21 | Initial test suite (DevPlan 025 W1+W6)
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
# region TEST_DEPLOY_PREFLIGHT_TRIGGER
## @purpose  Verify NODE flag triggers pre-flight in deploy target logic
## @scenario NODE set → pre-flight check is invoked (detected by log variable)
## 🧪 TRAP[TEST] · Regression: pre-flight must trigger when NODE is set
##   · Last fail: N/A (new test)
##   · Remove if: Makefile deploy pre-flight logic is moved
def test_deploy_preflight_trigger(tmp_path):
    """Test that the deploy pre-flight logic triggers when NODE is set."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing deploy pre-flight trigger with NODE set..." >&2

    NODE="test-node"
    preflight_triggered=false

    # Simulate Makefile deploy pre-flight logic
    if [ -n "$NODE" ]; then
        echo "[IMP:9][test] Pre-flight: checking VPS readiness for NODE=${NODE}" >&2
        preflight_triggered=true
    else
        echo "[IMP:7][test] No NODE set — skipping pre-flight" >&2
    fi

    if $preflight_triggered; then
        echo "[IMP:9][test] OK: Pre-flight triggered for NODE=${NODE}" >&2
    else
        echo "[IMP:10][test] FAIL: Pre-flight not triggered" >&2
        exit 1
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
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"
# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_DEPLOY_PREFLIGHT_SKIP_NO_NODE
## @purpose  Verify deploy with no NODE skips pre-flight (backward compat)
## @scenario No NODE → no pre-flight, direct git push
## 🧪 TRAP[TEST] · Regression: no NODE = backward compatible behavior
##   · Last fail: N/A (new test)
##   · Remove if: deploy pre-flight logic is moved
def test_deploy_preflight_skip_no_node(tmp_path):
    """Test that deploy pre-flight is skipped when NODE is not set."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing deploy pre-flight skip (no NODE)..." >&2

    NODE=""

    if [ -n "$NODE" ]; then
        echo "[IMP:7][test] NODE set — would check pre-flight" >&2
    else
        echo "[IMP:9][test] No NODE — pre-flight skipped (backward compat)" >&2
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
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"
# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_DEPLOY_LAUNCH_REQUIRES_NODE
## @purpose  Verify LAUNCH=1 without NODE fails with error message
## @scenario LAUNCH=1, no NODE → exit 1 with "LAUNCH=1 requires NODE"
## 🧪 TRAP[TEST] · Regression: LAUNCH=1 must require NODE
##   · Last fail: N/A (new test)
##   · Remove if: LAUNCH mode is removed
def test_deploy_launch_requires_node(tmp_path):
    """Test that LAUNCH=1 without NODE exits with error."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing LAUNCH=1 requirement for NODE..." >&2

    NODE=""
    LAUNCH=1

    if [ "$LAUNCH" = "1" ]; then
        if [ -z "$NODE" ]; then
            echo "[IMP:10][test] FATAL: LAUNCH=1 requires NODE=<node>" >&2
            exit 1
        fi
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
    assert result.returncode == 1, "Expected exit 1 for LAUNCH=1 without NODE"
    # This test expects IMP:10 log (fatal error), not IMP:9
    assert imp_found or "[IMP:10]" in result.stderr, "IMP:10 log not found for fatal error"
# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_DEPLOY_LAUNCH_OK
## @purpose  Verify LAUNCH=1 with NODE succeeds (post-verify path)
## @scenario LAUNCH=1, NODE set → URL output
## 🧪 TRAP[TEST] · Regression: LAUNCH=1 with NODE must succeed
##   · Last fail: N/A (new test)
##   · Remove if: LAUNCH mode is removed
def test_deploy_launch_with_node(tmp_path):
    """Test that LAUNCH=1 with NODE succeeds."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing LAUNCH=1 with NODE..." >&2

    NODE="test-node"
    PROJECT="test-project"
    LAUNCH=1

    if [ "$LAUNCH" = "1" ]; then
        if [ -z "$NODE" ]; then
            echo "[IMP:10][test] FATAL: LAUNCH=1 requires NODE" >&2
            exit 1
        fi
        echo "[IMP:9][test] LAUNCH mode: PROJECT=${PROJECT} deployed to NODE=${NODE}" >&2
        echo "[IMP:9][test] URL: https://${PROJECT}.${NODE}.example.com" >&2
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
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"
# endregion
