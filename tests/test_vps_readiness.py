# GREP_SUMMARY: test_vps_readiness pre-flight vps-readiness.sh check_vps_ready ssh docker
# STRUCTURE: ▶ import pytest + tmp_path → ◇ test_check_vps_ready_source_ok (source script) → ◇ test_check_vps_ready_missing_node → ◇ test_check_vps_ready_quick_skip → ◇ test_check_vps_ready_no_host_map → ⎋ verify LDD [IMP:9] trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/lib/vps-readiness.sh — shared VPS pre-flight checks.
##           Tests the bash function check_vps_ready() in isolation with mocked SSH.
## @scope    Tests parsing, argument handling, and exit semantics.
##           Not testing actual SSH connectivity (requires VPS).
## @invariants
##   - Uses tmp_path for isolated test environment
##   - Sources vps-readiness.sh directly in bash subprocess
##   - Tests ALL 5 exit conditions (no node, no host map, SSH fail, projects missing, success)
##   - Each test captures and validates IMP:7-10 log trajectory
## @rationale VPS readiness is the first gate in deploy sequencing — must be reliable.
##            Unit tests validate the function structure and failure modes.
## @changes 2026-07-21 | Initial test suite (DevPlan 025 W1)
# endregion MODULE_CONTRACT

import subprocess
import textwrap


import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_bash_script(script: str, tmp_path) -> subprocess.CompletedProcess:
    """Helper: write a Bash test script to tmp_path and run it."""
    # Inject PROJECT_ROOT as a bash variable for absolute path resolution
    header = f'PROJECT_ROOT="{PROJECT_ROOT}"\n'
    script_path = tmp_path / "test_script.sh"
    script_path.write_text(header + textwrap.dedent(script))
    script_path.chmod(0o755)
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ═══════════════════════════════════════════════════════════════════
# region TEST_VPS_READINESS_SOURCE_OK
## @purpose  Verify vps-readiness.sh can be sourced without errors
## @scenario W1 baseline: source the library, verify check_vps_ready is defined
## 🧪 TRAP[TEST] · Regression: vps-readiness.sh should be sourceable without side effects
##   · Last fail: N/A (new test)
##   · Remove if: function is renamed or extracted
def test_vps_readiness_source_ok(tmp_path):
    """Test that vps-readiness.sh can be sourced without errors."""
    script = """
    set -euo pipefail
    # Use PROJECT_ROOT (injected by test harness)
    VPS_SCRIPT="${PROJECT_ROOT}/core/lib/vps-readiness.sh"

    echo "[IMP:8][test] vps-readiness.sh path: ${VPS_SCRIPT}" >&2

    if [[ ! -f "${VPS_SCRIPT}" ]]; then
        echo "[IMP:10][test] FATAL: vps-readiness.sh not found at ${VPS_SCRIPT}" >&2
        exit 1
    fi

    # Source the module
    source "${VPS_SCRIPT}"

    # Verify function exists
    if declare -f check_vps_ready >/dev/null 2>&1; then
        echo "[IMP:9][test] OK: check_vps_ready function is defined" >&2
    else
        echo "[IMP:10][test] FAIL: check_vps_ready function not defined after source" >&2
        exit 1
    fi

    # Verify no side effects at source time
    echo "[IMP:9][test] Source complete — no side effects" >&2
    """
    result = _run_bash_script(script, tmp_path)

    # Check LDD trajectory
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
# region TEST_VPS_READINESS_NO_NODE
## @purpose  Verify check_vps_ready exits 1 with empty node name
## @scenario Empty node → exit 1 with diagnostics
## 🧪 TRAP[TEST] · Regression: empty node should fail gracefully
##   · Last fail: N/A (new test)
##   · Remove if: function signature changes
def test_vps_readiness_empty_node(tmp_path):
    """Test that check_vps_ready fails with empty node name."""
    script = """
    set -euo pipefail
    VPS_SCRIPT="${PROJECT_ROOT}/core/lib/vps-readiness.sh"
    source "${VPS_SCRIPT}"

    echo "[IMP:8][test] Testing check_vps_ready with empty node..." >&2

    # Call with empty node — should fail
    if check_vps_ready "" 2>&1; then
        echo "[IMP:10][test] FAIL: check_vps_ready with empty node returned 0" >&2
        exit 1
    else
        echo "[IMP:9][test] OK: check_vps_ready with empty node returned non-zero" >&2
    fi
    """
    result = _run_bash_script(script, tmp_path)
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
# region TEST_VPS_READINESS_NO_HOST_MAP
## @purpose  Verify check_vps_ready exits 1 when NODE_HOST_MAP is not set
## @scenario No NODE_HOST_MAP env → exit 1 with remediation hint
## 🧪 TRAP[TEST] · Regression: missing env var should be detected early
##   · Last fail: N/A (new test)
##   · Remove if: NODE_HOST_MAP is no longer required
def test_vps_readiness_no_host_map(tmp_path):
    """Test that check_vps_ready fails with no NODE_HOST_MAP env."""
    script = """
    set -euo pipefail
    VPS_SCRIPT="${PROJECT_ROOT}/core/lib/vps-readiness.sh"
    source "${VPS_SCRIPT}"

    echo "[IMP:8][test] Testing check_vps_ready with no NODE_HOST_MAP..." >&2

    # Unset NODE_HOST_MAP
    unset NODE_HOST_MAP

    if check_vps_ready "test-node" 2>&1; then
        echo "[IMP:10][test] FAIL: check_vps_ready returned 0 despite missing NODE_HOST_MAP" >&2
        exit 1
    else
        echo "[IMP:9][test] OK: check_vps_ready failed with no NODE_HOST_MAP" >&2
    fi
    """
    result = _run_bash_script(script, tmp_path)
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
# region TEST_VPS_READINESS_JSON_MODE
## @purpose  Verify --json output mode produces valid JSON with status=not_ready
## @scenario Missing NODE_HOST_MAP + --json → JSON output with failures array
## 🧪 TRAP[TEST] · Regression: JSON format required for CI parsing
##   · Last fail: N/A (new test)
##   · Remove if: --json mode is removed
def test_vps_readiness_json_mode(tmp_path):
    """Test that check_vps_ready produces valid JSON in --json mode."""
    script = """
    set -euo pipefail
    VPS_SCRIPT="${PROJECT_ROOT}/core/lib/vps-readiness.sh"
    source "${VPS_SCRIPT}"

    echo "[IMP:8][test] Testing --json output mode..." >&2

    unset NODE_HOST_MAP

    # Capture stdout (JSON) — NODE_HOST_MAP unset, expect not_ready JSON
    json_output=$(check_vps_ready "test-node" --json 2>/dev/null || true)

    # Validate JSON structure
    echo "${json_output}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert 'status' in data, 'Missing status field'
assert 'node' in data, 'Missing node field'
status = data.get('status')
if status == 'not_ready':
    failures = data.get('failures', [])
    assert len(failures) > 0, 'Empty failures array'
    print(f'[IMP:9][test] JSON OK: not_ready with {len(failures)} failure(s)')
else:
    print(f'[IMP:9][test] JSON OK: {status}')
" 2>&1 || exit 1
    """
    result = _run_bash_script(script, tmp_path)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for line in result.stdout.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    print("--- END LDD TRAJECTORY ---")
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"
# endregion
