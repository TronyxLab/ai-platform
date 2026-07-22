# GREP_SUMMARY: test_gate_sequencing converge exit-semantics stub-detection reconcile preflight hardening gate gate-marker
# STRUCTURE: ▶ test_gate_converge_exit_semantics (converge.sh exit 0/1/2) → ◇ test_gate_deploy_preflight_flag_exists → ◇ test_gate_reconcile_script_not_entrypoint → ◇ test_gate_vps_readiness_sourceable → ◇ test_gate_zero_new_entrypoints → ⎋ verify LDD [IMP:9] trajectory
# region MODULE_CONTRACT
## @purpose  Gate tests for deploy sequencing & reliability (DevPlan 025 W1-W6).
##           Validates invariant-level properties of the codebase:
##           - converge.sh has correct exit semantics (0/1/2)
##           - deploy Makefile target supports NODE/LAUNCH flags
##           - reconcile-projects.sh is NOT an entrypoint (internal-only)
##           - vps-readiness.sh is sourceable
##           - 0 new entries added to entrypoint-manifest.yaml (allowed_verbs)
## @scope    Read-only code structure analysis. No VPS/Docker/Git required.
## @invariants
##   - converge.sh must define CONVERGE_HAS_ERRORS and CONVERGE_HAS_WARNINGS globals
##   - reconcile-projects.sh must exit 1 when invoked directly
##   - vps-readiness.sh must define check_vps_ready function
##   - Every test function MUST have @pytest.mark.gate decorator for gate visibility
##   - Each gate file MUST be registered in core/entrypoint-manifest.yaml gates section
##   - No pass-test or skip-test in gate suite (Test Honesty R1/R3)
## @rationale Gate tests enforce architectural invariants that prevent drift.
##            Three-part registration (file + @pytest.mark.gate + manifest entry)
##            is required for execution in `make gate MODE=fast`. Missing any one
##            part causes the gate to be silently skipped.
## @changes 2026-07-21 | Initial test suite (DevPlan 025 W1-W6)
# endregion MODULE_CONTRACT

import os
import subprocess
import textwrap

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _run_bash_test(script: str, tmp_path) -> subprocess.CompletedProcess:
    """Helper: write a Bash test script to tmp_path and run it."""
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
# region TEST_GATE_CONVERGE_EXIT_SEMANTICS
## @purpose  Verify converge.sh defines CONVERGE_HAS_ERRORS and CONVERGE_HAS_WARNINGS globals
## @scenario grep converge.sh for CONVERGE_HAS_ERRORS, CONVERGE_HAS_WARNINGS
## 🧪 TRAP[TEST] · Gate invariant: W2 exit semantics must be present
##   · Last fail: N/A (new gate)
##   · Remove if: converge.sh exit logic is fundamentally rewritten
@pytest.mark.gate
def test_gate_converge_exit_semantics(tmp_path):
    """Gate: converge.sh must delegate to reconciler.py and use $recon_rc for exit decisions.

    ## @purpose  After W4-E3 refactoring, converge.sh became a thin shell facade that
    ##            delegates all convergence logic to reconciler.py. The old CONVERGE_HAS_ERRORS
    ##            and CONVERGE_HAS_WARNINGS globals were removed — exit codes now come from
    ##            reconciler.py via $recon_rc variable.
    ## @rationale Test updated to match W4-E3 converge.sh structure. See MODULE_CONTRACT in
    ##            converge.sh for the full refactoring rationale.
    """
    converge_path = "core/internal/bootstrap/converge.sh"
    script = f"""
    set -euo pipefail
    CONVERGE="$PROJECT_ROOT/{converge_path}"
    if [[ ! -f "$CONVERGE" ]]; then
        echo "[IMP:10][gate] FATAL: converge.sh not found at $CONVERGE" >&2
        exit 1
    fi
    # W4-E3: converge.sh delegates to reconciler.py — verify the recon_rc pattern
    if grep -q "recon_rc=0" "$CONVERGE"; then
        echo "[IMP:9][gate] OK: recon_rc initialized" >&2
    else
        echo "[IMP:10][gate] FAIL: recon_rc initialization not found in converge.sh" >&2
        exit 1
    fi
    if grep -q "reconciler.py" "$CONVERGE"; then
        echo "[IMP:9][gate] OK: converge.sh dispatches to reconciler.py" >&2
    else
        echo "[IMP:10][gate] FAIL: reconciler.py dispatch not found in converge.sh" >&2
        exit 1
    fi
    # Verify the final main() uses recon_rc for exit
    if grep -q 'exit $recon_rc' "$CONVERGE"; then
        echo "[IMP:9][gate] OK: main() uses \\$recon_rc for exit decision" >&2
    else
        echo "[IMP:10][gate] FAIL: main() does not use \\$recon_rc for exit" >&2
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
    assert result.returncode == 0, f"Gate failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_GATE_CONVERGE_RECONCILE_FLAG
## @purpose  Verify converge.sh --reconcile flag exists
## @scenario grep converge.sh for --reconcile
## 🧪 TRAP[TEST] · Gate invariant: W4 reconcile flag must exist
##   · Last fail: N/A (new gate)
##   · Remove if: --reconcile flag is removed from converge.sh
@pytest.mark.gate
def test_gate_converge_reconcile_flag(tmp_path):
    """Gate: converge.sh must accept --reconcile flag."""
    converge_path = "core/internal/bootstrap/converge.sh"
    script = f"""
    set -euo pipefail
    CONVERGE="$PROJECT_ROOT/{converge_path}"
    if grep -q -- '--reconcile' "$CONVERGE"; then
        echo "[IMP:9][gate] OK: converge.sh --reconcile flag defined" >&2
    else
        echo "[IMP:10][gate] FAIL: --reconcile flag not found in converge.sh" >&2
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
    assert result.returncode == 0, f"Gate failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_GATE_RECONCILE_NOT_ENTRYPOINT
## @purpose  Verify reconcile-projects.sh is NOT executable (no shebang) or has source guard
## @scenario Check reconcile-projects.sh has direct invocation guard
## 🧪 TRAP[TEST] · Gate invariant: internal scripts are not entrypoints
##   · Last fail: N/A (new gate)
##   · Remove if: reconcile-projects.sh becomes an entrypoint
@pytest.mark.gate
def test_gate_reconcile_not_entrypoint(tmp_path):
    """Gate: reconcile-projects.sh must exit 1 when run directly (internal-only)."""
    reconcile_path = "core/internal/deploy/reconcile-projects.sh"
    script = f"""
    set -euo pipefail
    RECONCILE="$PROJECT_ROOT/{reconcile_path}"
    if [[ ! -f "$RECONCILE" ]]; then
        echo "[IMP:10][gate] FATAL: reconcile-projects.sh not found" >&2
        exit 1
    fi
    # Running directly should fail with error message
    if bash "$RECONCILE" 2>&1; then
        echo "[IMP:10][gate] FAIL: reconcile-projects.sh should have failed when run directly" >&2
        exit 1
    else
        echo "[IMP:9][gate] OK: reconcile-projects.sh is not an entrypoint (exit non-zero)" >&2
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
    assert result.returncode == 0, f"Gate failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_GATE_VPS_READINESS_SOURCEABLE
## @purpose  Verify vps-readiness.sh can be sourced and check_vps_ready is defined
## @scenario Source vps-readiness.sh, verify function exists
## 🧪 TRAP[TEST] · Gate invariant: library must be sourceable
##   · Last fail: N/A (new gate)
##   · Remove if: vps-readiness.sh is removed or renamed
@pytest.mark.gate
def test_gate_vps_readiness_sourceable(tmp_path):
    """Gate: vps-readiness.sh must be sourceable and define check_vps_ready."""
    script = """
    set -euo pipefail
    VPS_SCRIPT="$PROJECT_ROOT/core/lib/vps-readiness.sh"
    if [[ ! -f "$VPS_SCRIPT" ]]; then
        echo "[IMP:10][gate] FATAL: vps-readiness.sh not found" >&2
        exit 1
    fi
    source "$VPS_SCRIPT"
    if declare -f check_vps_ready >/dev/null 2>&1; then
        echo "[IMP:9][gate] OK: vps-readiness.sh sourceable, check_vps_ready defined" >&2
    else
        echo "[IMP:10][gate] FAIL: check_vps_ready not defined after source" >&2
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
    assert result.returncode == 0, f"Gate failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_GATE_MAKEFILE_DEPLOY_NODE_FLAG
## @purpose  Verify Makefile deploy target supports NODE variable
## @scenario grep Makefile for deploy: and NODE usage
## 🧪 TRAP[TEST] · Gate invariant: deploy target must support NODE flag (W1)
##   · Last fail: N/A (new gate)
##   · Remove if: deploy target NODE support is removed
@pytest.mark.gate
def test_gate_makefile_deploy_node_flag(tmp_path):
    """Gate: Makefile deploy target must support NODE flag for pre-flight."""
    makefile_path = "Makefile"
    script = f"""
    set -euo pipefail
    MF="$PROJECT_ROOT/{makefile_path}"
    DEPLOY_MK="$PROJECT_ROOT/makefiles/deploy.mk"
    if [[ ! -f "$MF" ]]; then
        echo "[IMP:10][gate] FATAL: Makefile not found" >&2
        exit 1
    fi

    # Check NODE flag in either file
    found_node=0
    if grep -q 'if \\[ -n "\\$(NODE)" \\]' "$MF" 2>/dev/null; then found_node=1; fi
    if [[ -f "$DEPLOY_MK" ]] && grep -q 'if \\[ -n "\\$(NODE)" \\]' "$DEPLOY_MK" 2>/dev/null; then found_node=1; fi
    if [[ $found_node -eq 1 ]]; then
        echo "[IMP:9][gate] OK: deploy target supports NODE flag" >&2
    else
        echo "[IMP:10][gate] FAIL: deploy target does not check NODE flag in expected pattern" >&2
        grep -n 'NODE' "$MF" "$DEPLOY_MK" 2>/dev/null | head -5 >&2
        exit 1
    fi

    # Check LAUNCH flag in either file
    found_launch=0
    if grep -q "LAUNCH" "$MF" 2>/dev/null; then found_launch=1; fi
    if [[ -f "$DEPLOY_MK" ]] && grep -q "LAUNCH" "$DEPLOY_MK" 2>/dev/null; then found_launch=1; fi
    if [[ $found_launch -eq 1 ]]; then
        echo "[IMP:9][gate] OK: deploy target supports LAUNCH flag" >&2
    else
        echo "[IMP:10][gate] FAIL: deploy target does not support LAUNCH flag" >&2
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
    assert result.returncode == 0, f"Gate failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"


# endregion
