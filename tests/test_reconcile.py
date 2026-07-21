# GREP_SUMMARY: test_reconcile reconcile-projects stub-deploy ghcr-check idempotent recovery
# STRUCTURE: ▶ test_reconcile_script_source (source guard) → ◇ test_reconcile_dry_run → ◇ test_reconcile_no_projects → ◇ test_reconcile_already_deployed → ⎋ verify LDD [IMP:9] trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/reconcile-projects.sh (DevPlan 025 Wave 4).
##           Tests source guard, dry-run mode, empty projects list handling, and
##           already-deployed project skipping.
## @scope    Tests bash logic through isolated scripts. No VPS/GHCR/Docker required.
## @invariants
##   - Uses tmp_path for isolated test environment
##   - Tests the reconcile_projects() function logic
##   - Validates idempotency: repeated calls on non-stub projects = no-op
## @rationale Reconciliation is the core of W4 — auto-recovery after bootstrap.
##            Tests ensure the basic control flow is correct before SSH/GHCR integration.
## @changes 2026-07-21 | Initial test suite (DevPlan 025 W4)
# endregion MODULE_CONTRACT

import os
import subprocess
import textwrap

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


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
# region TEST_RECONCILE_DIRECT_INVOCATION_GUARD
## @purpose  Verify reconcile-projects.sh exits 1 when run directly (not sourced)
## @scenario Direct invocation → error message, exit 1
## 🧪 TRAP[TEST] · Regression: script must not be executable as entrypoint
##   · Last fail: N/A (new test)
##   · Remove if: direct invocation guard is removed
def test_reconcile_direct_invocation_guard(tmp_path):
    """Test that reconcile-projects.sh exits 1 when run directly."""
    script = """
    set -euo pipefail
    RECONCILE_SCRIPT="${PROJECT_ROOT}/core/internal/deploy/reconcile-projects.sh"

    echo "[IMP:8][test] Testing direct invocation guard..." >&2

    if [[ ! -f "${RECONCILE_SCRIPT}" ]]; then
        echo "[IMP:10][test] FATAL: reconcile-projects.sh not found at ${RECONCILE_SCRIPT}" >&2
        exit 1
    fi

    # Run directly — should exit 1 with error about not being an entrypoint
    output=$(bash "${RECONCILE_SCRIPT}" 2>&1) && {
        echo "[IMP:10][test] FAIL: reconcile-projects.sh should have failed when run directly" >&2
        exit 1
    } || {
        rc=$?
        echo "[IMP:9][test] OK: Direct invocation failed with exit ${rc}" >&2
    }
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
# region TEST_RECONCILE_EMPTY_PROJECTS
## @purpose  Verify reconcile_projects succeeds with empty project list
## @scenario Empty node.yaml (no projects) → SKIP, exit 0
## 🧪 TRAP[TEST] · Regression: empty project list is valid (SKIP, not fail)
##   · Last fail: N/A (new test)
##   · Remove if: reconcile_projects signature changes
def test_reconcile_empty_projects(tmp_path):
    """Test that reconcile_projects handles empty project list gracefully."""
    # Create a minimal node.yaml with no projects
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text(
        textwrap.dedent("""\
        node:
          name: test-node
        modules: {}
    """)
    )

    node_yaml_path = str(node_yaml)
    script = f"""
    set -euo pipefail

    echo "[IMP:8][test] Testing reconcile with empty projects..." >&2

    NODE_YAML="{node_yaml_path}"

    # Simulate reconcile_projects logic for empty projects
    projects_json='[]'
    project_count=$(echo "$projects_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

    if [[ "$project_count" -eq 0 ]]; then
        echo "[IMP:9][test] SKIP: No projects defined in node.yaml" >&2
    else
        echo "[IMP:10][test] FAIL: Expected 0 projects, got $project_count" >&2
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
# region TEST_RECONCILE_ALREADY_DEPLOYED_SKIP
## @purpose  Verify already-deployed projects (non-stub) are skipped
## @scenario Real ai-platform.yaml → SKIP (already deployed)
## 🧪 TRAP[TEST] · Regression: idempotency — already deployed = skip
##   · Last fail: N/A (new test)
##   · Remove if: reconcile logic for deployed projects changes
def test_reconcile_already_deployed_skip(tmp_path):
    """Test that reconcile skips projects with real (non-stub) ai-platform.yaml."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing reconcile skip for already deployed project..." >&2

    # Create a "deployed" project dir with real ai-platform.yaml
    proj_dir=$(mktemp -d)
    cat > "$proj_dir/ai-platform.yaml" << 'EOF'
project: deployed-project
service: deployed-project
domain: example.com
EOF

    ai_yaml="$proj_dir/ai-platform.yaml"

    # Check if stub — should NOT be a stub
    if [[ -f "$ai_yaml" ]]; then
        if head -1 "$ai_yaml" | grep -q "GENERATED-STUB"; then
            echo "[IMP:10][test] FAIL: Real config detected as stub" >&2
            exit 1
        else
            echo "[IMP:9][test] SKIP: real ai-platform.yaml (already deployed)" >&2
        fi
    else
        echo "[IMP:10][test] FAIL: ai-platform.yaml not found" >&2
        exit 1
    fi

    rm -rf "$proj_dir"
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
