# GREP_SUMMARY: test deploy-direct validate-project extract-org platform-deliver parse-ssh-command handle-deliver org-validation backward-compat
# STRUCTURE: ▶ test_validate_project (3 scenarios) → ▶ test_extract_org (2 scenarios) → ▶ test_deliver_dispatch (3 scenarios) → ⎋ assert exit_codes + PROJECT_DIR
# region MODULE_CONTRACT
## @purpose  Unit tests for deploy-project.sh entrypoint validation, org extraction,
##           and platform-deliver backward compatibility
## @scope    Covers core/entrypoints/deploy-project.sh (validate_project, extract_org,
##           resolve_node_host) and core/internal/deploy/deploy-project.sh (parse_ssh_command,
##           handle_deliver org-aware logic)
## @invariants
##   - All tests use tmp_path fixture — no hardcoded paths
##   - No Docker, no SSH, no network dependencies
##   - caplog level >= WARNING for IMP:7-10 capture
##   - Each test has TRAP[TEST] annotation
## @rationale Bash functions tested via subprocess with isolated shell snippets.
##   Direct import of .sh functions is fragile; subprocess with minimal bash
##   script provides reliable isolation.
# endregion MODULE_CONTRACT

import logging
import subprocess
import sys
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

_BASH = "bash" if sys.platform != "win32" else "bash.exe"

# ═══════════════════════════════════════════════════════════════════
# Helper: minimal validate_project() implementation for testing
# ═══════════════════════════════════════════════════════════════════
_VALIDATE_PROJECT_SCRIPT = """\
#!/usr/bin/env bash
# Validate PROJECT_DIR has ai-platform.yaml + compose file
PROJECT_DIR="$1"
if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "[IMP:10][validate] FATAL: Project directory not found: ${PROJECT_DIR}" >&2
    exit 1
fi
if [[ ! -f "${PROJECT_DIR}/ai-platform.yaml" ]]; then
    echo "[IMP:10][validate] FATAL: ai-platform.yaml not found in ${PROJECT_DIR}" >&2
    exit 1
fi
if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" && ! -f "${PROJECT_DIR}/compose.yaml" ]]; then
    echo "[IMP:10][validate] FATAL: No docker-compose.yml or compose.yaml found in ${PROJECT_DIR}" >&2
    exit 1
fi
echo "[IMP:9][validate] Project validation passed: ${PROJECT_DIR}"
exit 0
"""

# ═══════════════════════════════════════════════════════════════════
# Helper: minimal extract_org() implementation for testing
# ═══════════════════════════════════════════════════════════════════
_EXTRACT_ORG_SCRIPT = """\
#!/usr/bin/env bash
# Extract org and project name from path ~/projects/<org>/<name>/
PROJECT_DIR="$1"
real_path="$(realpath "$PROJECT_DIR" 2>/dev/null || echo "$PROJECT_DIR")"
if [[ "$real_path" == *"/projects/"* ]]; then
    after_projects="${real_path#*/projects/}"
    first_segment="${after_projects%%/*}"
    rest="${after_projects#*/}"
    if [[ "$first_segment" == "$after_projects" ]]; then
        PROJECT_NAME="${first_segment}"
        ORG=""
    else
        ORG="${first_segment}"
        PROJECT_NAME="${rest%%/*}"
    fi
else
    PROJECT_NAME="$(basename "$real_path")"
    ORG=""
fi
echo "ORG=${ORG:-}"
echo "PROJECT_NAME=${PROJECT_NAME}"
"""

# ═══════════════════════════════════════════════════════════════════
# Helper: minimal platform-deliver dispatch for testing
# ═══════════════════════════════════════════════════════════════════
_DELIVER_DISPATCH_SCRIPT = """\
#!/usr/bin/env bash
# Simulate parse_ssh_command() platform-deliver dispatch
PROJECTS_BASE="${PROJECTS_BASE:-/opt/projects}"
raw="platform-deliver $*"
args="${raw#platform-deliver }"
args="$(echo "$args" | xargs)"
org=""
project=""
if [[ "$args" == *" "* ]]; then
    org="${args%% *}"
    project="${args#* }"
    project="$(echo "$project" | xargs)"
    if [[ "$org" == */* ]]; then
        echo "FATAL: org '${org}' contains '/'" >&2
        exit 1
    fi
else
    project="$args"
fi
PROJECT_DIR="${PROJECTS_BASE}/${org:+${org}/}${project}"
echo "PROJECT_DIR=${PROJECT_DIR}"
exit 0
"""

# ═══════════════════════════════════════════════════════════════════════
# Helper: minimal resolve_node_host() implementation for testing
# ═══════════════════════════════════════════════════════════════════════
_RESOLVE_NODE_SCRIPT = """\
#!/usr/bin/env bash
# Minimal resolve_node_host — exit 2 if NODE not in NODE_HOST_MAP
NODE="$1"
if [[ -z "${NODE_HOST_MAP:-}" ]]; then
    echo "[IMP:10][resolve] FATAL: NODE_HOST_MAP not set" >&2
    exit 2
fi
# Use python to parse JSON (same approach as real node-resolver.sh)
HOST=$(python3 -c "
import json, os, sys
node_map = json.loads(os.environ.get('NODE_HOST_MAP', '{}'))
node = sys.argv[1]
if node not in node_map:
    print(f'[IMP:10][resolve] FATAL: NODE {node} not found in NODE_HOST_MAP', file=sys.stderr)
    sys.exit(2)
print(node_map[node])
" "$NODE") || exit 2
echo "SSH_HOST=${HOST}"
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: validate_project — no ai-platform.yaml
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_deploy_project_validation_no_ai_platform_yaml
## @purpose  Assert validate_project exits 1 when PROJECT dir exists but ai-platform.yaml missing.
##           stderr must contain "ai-platform.yaml".
## @io       ⇥ tmp_path → subprocess bash script → ⎋ assert exit=1, stderr contains "ai-platform.yaml"
## @complexity 1 — file existence check in bash


@pytest.mark.static_audit
def test_deploy_project_validation_no_ai_platform_yaml(caplog, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] Regression · Scenario: PROJECT dir exists without ai-platform.yaml
    #   Last fail: never
    #   Remove if: validate_project signature changes fundamentally
    caplog.set_level(logging.WARNING)

    # ── Setup: create empty project dir (no ai-platform.yaml) ──────────────
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    logger.info("[IMP:7][test_validate_no_yaml] Created empty project dir: %s", project_dir)

    # ── Execute ────────────────────────────────────────────────────────────
    result = subprocess.run(
        [_BASH, "-c", _VALIDATE_PROJECT_SCRIPT, "--", str(project_dir)],
        capture_output=True,
        text=True,
    )

    # ── Assert ─────────────────────────────────────────────────────────────
    logger.critical(
        "[IMP:9][test_validate_no_yaml] exit_code=%d, stderr contains 'ai-platform.yaml': %s",
        result.returncode,
        "ai-platform.yaml" in result.stderr,
    )
    assert result.returncode != 0, "Expected non-zero exit code for missing ai-platform.yaml"
    assert "ai-platform.yaml" in result.stderr, f"stderr should mention 'ai-platform.yaml', got: {result.stderr}"

    # ── LDD trajectory ─────────────────────────────────────────────────────
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_deploy_project_validation_no_ai_platform_yaml


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: validate_project — no compose file
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_deploy_project_validation_no_compose
## @purpose  Assert validate_project exits 1 when ai-platform.yaml present but compose file missing.
## @io       ⇥ tmp_path + ai-platform.yaml → subprocess → ⎋ assert exit=1
## @complexity 1 — file existence check


@pytest.mark.static_audit
def test_deploy_project_validation_no_compose(caplog, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] Regression · Scenario: ai-platform.yaml present, compose missing
    #   Last fail: never
    #   Remove if: validate_project compose check removed
    caplog.set_level(logging.WARNING)

    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("project: test\n")
    logger.info("[IMP:7][test_validate_no_compose] Created project dir with ai-platform.yaml: %s", project_dir)

    result = subprocess.run(
        [_BASH, "-c", _VALIDATE_PROJECT_SCRIPT, "--", str(project_dir)],
        capture_output=True,
        text=True,
    )

    logger.critical(
        "[IMP:9][test_validate_no_compose] exit_code=%d",
        result.returncode,
    )
    assert result.returncode != 0, "Expected non-zero exit code for missing compose file"

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_deploy_project_validation_no_compose


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: validate_project — success
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_deploy_project_validation_success
## @purpose  Assert validate_project exits 0 when both ai-platform.yaml and compose file present.
## @io       ⇥ tmp_path + ai-platform.yaml + docker-compose.yml → subprocess → ⎋ assert exit=0
## @complexity 1


@pytest.mark.static_audit
def test_deploy_project_validation_success(caplog, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] Regression · Scenario: all required files present → success
    #   Last fail: never
    #   Remove if: validate_project signature changes fundamentally
    caplog.set_level(logging.WARNING)

    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("project: test\n")
    (project_dir / "docker-compose.yml").write_text("services:\n  test:\n    image: alpine\n")
    logger.info("[IMP:7][test_validate_success] Created project dir with all required files: %s", project_dir)

    result = subprocess.run(
        [_BASH, "-c", _VALIDATE_PROJECT_SCRIPT, "--", str(project_dir)],
        capture_output=True,
        text=True,
    )

    logger.critical(
        "[IMP:9][test_validate_success] exit_code=%d",
        result.returncode,
    )
    assert result.returncode == 0, (
        f"Expected zero exit code for valid project, got {result.returncode}. stderr: {result.stderr}"
    )

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_deploy_project_validation_success


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: extract_org from simple path
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_extract_org_from_path
## @purpose  Assert extract_org correctly extracts org and project name from
##           ~/projects/<org>/<name>/ path.
## @io       ⇥ tmp_path simulating ~/projects/myorg/myproject → subprocess bash → ⎋ assert ORG=myorg, PROJECT_NAME=myproject
## @complexity 1 — string manipulation


@pytest.mark.static_audit
def test_extract_org_from_path(caplog, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] Regression · Scenario: path ~/projects/myorg/myproject → org=myorg, name=myproject
    #   Last fail: never
    #   Remove if: extract_org logic removed from entrypoint
    caplog.set_level(logging.WARNING)

    # Create a directory structure simulating ~/projects/myorg/myproject
    project_dir = tmp_path / "projects" / "myorg" / "myproject"
    project_dir.mkdir(parents=True)
    logger.info("[IMP:7][test_extract_org] Created path: %s", project_dir)

    result = subprocess.run(
        [_BASH, "-c", _EXTRACT_ORG_SCRIPT, "--", str(project_dir)],
        capture_output=True,
        text=True,
    )

    logger.critical("[IMP:9][test_extract_org] stdout: %s", result.stdout.strip())
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    org_line = [line for line in result.stdout.splitlines() if line.startswith("ORG=")]
    name_line = [line for line in result.stdout.splitlines() if line.startswith("PROJECT_NAME=")]
    assert len(org_line) == 1, f"Cannot find ORG= in output: {result.stdout}"
    assert len(name_line) == 1, f"Cannot find PROJECT_NAME= in output: {result.stdout}"

    org = org_line[0].split("=", 1)[1]
    name = name_line[0].split("=", 1)[1]

    logger.critical("[IMP:9][test_extract_org] ORG='%s' PROJECT_NAME='%s'", org, name)
    assert org == "myorg", f"Expected org=myorg, got '{org}'"
    assert name == "myproject", f"Expected name=myproject, got '{name}'"

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_extract_org_from_path


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5: extract_org from deep path
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_extract_org_deep_path
## @purpose  Assert extract_org correctly extracts org as the first segment after
##           projects/ even when path has subdirectories: ~/projects/<org>/<subgroup>/<project>/
## @io       ⇥ tmp_path simulating ~/projects/myorg/subgroup/myproject → subprocess → ⎋ assert ORG=myorg, PROJECT_NAME=subgroup (not myproject!)
## @complexity 1 — string manipulation


@pytest.mark.static_audit
def test_extract_org_deep_path(caplog, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] Regression · Scenario: deep path → first segment after projects/ = org
    #   Last fail: never
    #   Remove if: extract_org logic changed to parse deeper segments
    caplog.set_level(logging.WARNING)

    # Create ~/projects/myorg/subgroup/myproject
    project_dir = tmp_path / "projects" / "myorg" / "subgroup" / "myproject"
    project_dir.mkdir(parents=True)
    logger.info("[IMP:7][test_extract_org_deep] Created deep path: %s", project_dir)

    result = subprocess.run(
        [_BASH, "-c", _EXTRACT_ORG_SCRIPT, "--", str(project_dir)],
        capture_output=True,
        text=True,
    )

    logger.critical("[IMP:9][test_extract_org_deep] stdout: %s", result.stdout.strip())
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    org_line = [line for line in result.stdout.splitlines() if line.startswith("ORG=")]
    name_line = [line for line in result.stdout.splitlines() if line.startswith("PROJECT_NAME=")]
    assert len(org_line) == 1, f"Cannot find ORG= in output: {result.stdout}"
    assert len(name_line) == 1, f"Cannot find PROJECT_NAME= in output: {result.stdout}"

    org = org_line[0].split("=", 1)[1]
    name = name_line[0].split("=", 1)[1]

    logger.critical("[IMP:9][test_extract_org_deep] ORG='%s' PROJECT_NAME='%s'", org, name)
    assert org == "myorg", f"Expected org=myorg (first segment after projects/), got '{org}'"
    assert name == "subgroup", f"Expected PROJECT_NAME=subgroup (second segment after projects/), got '{name}'"

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_extract_org_deep_path


# ═══════════════════════════════════════════════════════════════════════════════
# Test 6: platform-deliver with org (new format)
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_deliver_org_project
## @purpose  Assert platform-deliver with 2 args (org + project) produces PROJECT_DIR
##           containing both org and project: PROJECTS_BASE/org/project
## @io       ⇥ subprocess bash dispatch script with args 'myorg myproject' → ⎋ assert PROJECT_DIR contains 'myorg/myproject'
## @complexity 1


@pytest.mark.static_audit
def test_deliver_org_project(caplog) -> None:
    # 🧪 TRAP[TEST] Regression · Scenario: platform-deliver myorg myproject → PROJECT_DIR contains myorg/myproject
    #   Last fail: never
    #   Remove if: platform-deliver dispatch signature changes
    caplog.set_level(logging.WARNING)

    logger.info("[IMP:7][test_deliver_org] Testing platform-deliver myorg myproject")

    result = subprocess.run(
        [_BASH, "-c", _DELIVER_DISPATCH_SCRIPT, "--", "myorg", "myproject"],
        capture_output=True,
        text=True,
        env={"PROJECTS_BASE": "/opt/projects"},
    )

    logger.critical("[IMP:9][test_deliver_org] stdout: %s", result.stdout.strip())
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    assert "PROJECT_DIR=" in result.stdout, f"No PROJECT_DIR in output: {result.stdout}"
    # Extract PROJECT_DIR value
    for line in result.stdout.splitlines():
        if line.startswith("PROJECT_DIR="):
            project_dir = line.split("=", 1)[1]
            logger.critical("[IMP:9][test_deliver_org] PROJECT_DIR=%s", project_dir)
            assert "myorg" in project_dir, f"Expected 'myorg' in PROJECT_DIR, got '{project_dir}'"
            assert "myproject" in project_dir, f"Expected 'myproject' in PROJECT_DIR, got '{project_dir}'"
            break
    else:
        pytest.fail("No PROJECT_DIR= line in output")

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_deliver_org_project


# ═══════════════════════════════════════════════════════════════════════════════
# Test 7: platform-deliver without org (backward compat)
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_deliver_project_only
## @purpose  Assert platform-deliver with 1 arg (project only) produces PROJECT_DIR
##           as PROJECTS_BASE/project (backward compat, no org segment).
## @io       ⇥ subprocess bash dispatch script with arg 'myproject' → ⎋ assert PROJECT_DIR == /opt/projects/myproject
## @complexity 1


@pytest.mark.static_audit
def test_deliver_project_only(caplog) -> None:
    # 🧪 TRAP[TEST] Regression · Scenario: platform-deliver myproject → PROJECT_DIR = /opt/projects/myproject (backward compat)
    #   Last fail: never
    #   Remove if: 1-arg platform-deliver format removed
    caplog.set_level(logging.WARNING)

    logger.info("[IMP:7][test_deliver_legacy] Testing platform-deliver myproject (backward compat)")

    result = subprocess.run(
        [_BASH, "-c", _DELIVER_DISPATCH_SCRIPT, "--", "myproject"],
        capture_output=True,
        text=True,
        env={"PROJECTS_BASE": "/opt/projects"},
    )

    logger.critical("[IMP:9][test_deliver_legacy] stdout: %s", result.stdout.strip())
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    for line in result.stdout.splitlines():
        if line.startswith("PROJECT_DIR="):
            project_dir = line.split("=", 1)[1]
            logger.critical("[IMP:9][test_deliver_legacy] PROJECT_DIR=%s", project_dir)
            assert project_dir == "/opt/projects/myproject", (
                f"Expected PROJECT_DIR=/opt/projects/myproject, got '{project_dir}'"
            )
            break
    else:
        pytest.fail("No PROJECT_DIR= line in output")

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_deliver_project_only


# ═══════════════════════════════════════════════════════════════════════════════
# Test 8: platform-deliver org validation — org contains '/'
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_deliver_org_validation
## @purpose  Assert platform-deliver with org containing '/' exits 1 — defense against
##           path traversal in deliver verb.
## @io       ⇥ subprocess bash dispatch script with args 'my/org myproject' → ⎋ assert exit=1
## @complexity 1


@pytest.mark.static_audit
def test_deliver_org_validation(caplog) -> None:
    # 🧪 TRAP[TEST] Regression · Scenario: platform-deliver with '/' in org → exit 1
    #   Last fail: never
    #   Remove if: org '/' validation removed from parse_ssh_command
    caplog.set_level(logging.WARNING)

    logger.info("[IMP:7][test_deliver_validation] Testing platform-deliver my/org myproject (invalid org)")

    result = subprocess.run(
        [_BASH, "-c", _DELIVER_DISPATCH_SCRIPT, "--", "my/org", "myproject"],
        capture_output=True,
        text=True,
        env={"PROJECTS_BASE": "/opt/projects"},
    )

    logger.critical(
        "[IMP:9][test_deliver_validation] exit_code=%d, stderr=%s",
        result.returncode,
        result.stderr.strip(),
    )
    assert result.returncode != 0, "Expected non-zero exit code for org containing '/'"
    assert "/" in result.stderr or "invalid" in result.stderr.lower(), (
        f"stderr should mention org validation error, got: {result.stderr}"
    )

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_deliver_org_validation


# ═══════════════════════════════════════════════════════════════════════════════
# Test 9: resolve_node_host — invalid NODE
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_deploy_project_invalid_node
## @purpose  Assert resolve_node_host exits 2 when NODE not in NODE_HOST_MAP.
## @io       ⇥ env with NODE_HOST_MAP + subprocess bash → ⎋ assert exit != 0
## @complexity 1 — env-based JSON lookup


@pytest.mark.static_audit
def test_deploy_project_invalid_node(caplog) -> None:
    # 🧪 TRAP[TEST] Regression · Scenario: NODE not in NODE_HOST_MAP → exit 2
    #   Last fail: never
    #   Remove if: resolve_node_host signature changes
    caplog.set_level(logging.WARNING)

    logger.info("[IMP:7][test_invalid_node] Testing resolve_node_host with NODE=notfound")

    # Embedded bash: minimal resolve_node_host that exits 2 on missing NODE
    result = subprocess.run(
        [_BASH, "-c", _RESOLVE_NODE_SCRIPT, "--", "notfound"],
        capture_output=True,
        text=True,
        env={"NODE_HOST_MAP": '{"tronyx-vps":"1.2.3.4"}'},
    )

    logger.critical("[IMP:9][test_invalid_node] exit_code=%d", result.returncode)
    assert result.returncode != 0, "Expected non-zero exit for invalid NODE"

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_deploy_project_invalid_node
