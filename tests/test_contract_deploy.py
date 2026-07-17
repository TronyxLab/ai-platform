#!/usr/bin/env python3
# GREP_SUMMARY: contract-test deploy-project ssh forced-command subprocess bash-syntax
# STRUCTURE: ▶ platform_root → ∋ SCRIPT_PATH → ◇ os.path.isfile? → ◇ bash -n (syntax) → IMP:7-10 LDD → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Contract tests for core/internal/deploy/deploy-project.sh. Verify the real bash
##           script exists, is syntactically valid, and fails gracefully under expected conditions.
##           These are CONTRACT tests (NOT Simulators) — they call the real bash via subprocess.
## @scope    Tests operate on the real deploy-project.sh file in the project tree.
##           No mocking, no simulation. Docker not required for these checks.
## @invariants
##   - Script exists at core/internal/deploy/deploy-project.sh relative to platform root
##   - Script is a regular file (invoked via `bash script.sh`, not `./script.sh`)
##   - bash -n returns 0 for valid syntax
##   - Script requires SSH_ORIGINAL_COMMAND env var (SSH forced-command pattern)
## @rationale  Contract tests verify the integrity of deploy-project.sh without running Docker.
##             The script is the CI deploy point (make deploy → git push → SSH → deploy-project.sh).
##             Syntax regression would block all project deployments.
## @changes — CREATED: 2026-07-09 | TASK-4A: contract tests for deploy scripts
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess

import pytest

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)
DEPLOY_SCRIPT_REL: str = os.path.join("core", "internal", "deploy", "deploy-project.sh")
SCRIPT_PATH: str = os.path.join(PLATFORM_ROOT, DEPLOY_SCRIPT_REL)


# ── Test: File exists and is executable ────────────────────────────────────


# region FUNC_test_deploy_script_exists
@pytest.mark.contract
## @purpose  Verify the deploy-project.sh script file exists on disk as a regular file.
##            The script is invoked via `bash script.sh` (not `./script.sh`), so +x
##            permission is NOT required — bash reads the file as a text argument.
## @io       — (uses SCRIPT_PATH global) → ⎋ None (asserts)
## @complexity  O(1)
## @invariants
##   - SCRIPT_PATH must be a regular file (not directory)
##   - Script is invoked via `bash script.sh` which requires only read permission
##   - Failure means the script was moved or deleted

def test_deploy_script_exists() -> None:
    """
    # ▶ SCRIPT_PATH → ◇ os.path.isfile? → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_deploy_script_exists] Checking script: %s", SCRIPT_PATH)

    # region BLOCK_AssertFile
    assert os.path.isfile(SCRIPT_PATH), f"[IMP:9][test_deploy_script_exists] FAIL: script not found at {SCRIPT_PATH}"
    logger.info("[IMP:8][test_deploy_script_exists] File exists: %s", SCRIPT_PATH)
    # endregion

    logger.info("[IMP:9][test_deploy_script_exists] PASS: %s exists on disk", SCRIPT_PATH)


# endregion FUNC_test_deploy_script_exists


# ── Test: bash -n syntax check ─────────────────────────────────────────────


# region FUNC_test_deploy_script_syntax
@pytest.mark.contract
## @purpose  Verify deploy-project.sh has valid bash syntax via `bash -n`.
##           A syntax error in this script blocks ALL project deployments via CI.
## @io       — (calls bash -n via subprocess) → ⎋ None (asserts returncode == 0)
## @complexity  O(1)
## @invariants
##   - bash -n reads and parses the script WITHOUT executing it
##   - returncode == 0 means syntactically valid bash
##   - Any syntax error produces stderr output and exit code > 0
## @rationale  Syntax regression would silently break CI deployment pipelines.
##             bash -n catches missing braces, unclosed quotes, syntax errors.

def test_deploy_script_syntax() -> None:
    """
    # ▶ SCRIPT_PATH → ⚡ subprocess.run(["bash", "-n", SCRIPT_PATH]) → ◇ returncode == 0? → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_deploy_script_syntax] Running bash -n on: %s", SCRIPT_PATH)

    result: subprocess.CompletedProcess = subprocess.run(
        ["bash", "-n", SCRIPT_PATH],
        capture_output=True,
        text=True,
    )

    # Print LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print(f"[IMP:7][test_deploy_script_syntax] bash -n exit code: {result.returncode}")
    if result.stderr:
        for line in result.stderr.splitlines():
            print(f"[IMP:7][bash-n/stderr] {line}")
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 0, (
        f"[IMP:9][test_deploy_script_syntax] FAIL: bash syntax error in {SCRIPT_PATH}\nstderr: {result.stderr}"
    )
    logger.info("[IMP:9][test_deploy_script_syntax] PASS: %s is syntactically valid", SCRIPT_PATH)


# endregion FUNC_test_deploy_script_syntax


# ── Test: No silent error suppression (`|| true`) on critical docker commands ─


# region FUNC_test_deploy_no_silent_errors
@pytest.mark.contract
## @purpose  Verify deploy-project.sh does NOT use `|| true` on critical Docker commands
##           (docker compose up, docker compose images, docker exec psql). These commands
##           must be fail-fast — errors must propagate, not be silently suppressed.
## @rationale C6/C7/C8/C17: `|| true` masks errors in production, leading to inconsistent
##           state. Contract test ensures the fix is not regressed.
def test_deploy_no_silent_errors() -> None:
    """
    # ▶ SCRIPT_PATH → ◇ grep (docker compose up|docker compose images|docker exec psql)
    #   without `|| true` → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_deploy_no_silent_errors] Scanning for `|| true` on critical commands in: %s", SCRIPT_PATH)

    with open(SCRIPT_PATH) as f:
        content = f.read()

    # These critical commands MUST NOT use `|| true`
    critical_patterns = [
        r"docker compose up -d --no-recreate",
        r"docker compose images -q",
        r"docker compose up -d ",
        r"docker exec postgres psql",
        r"docker compose config",
    ]

    found_issues = []
    found_issues.extend(
        f"  line {i}: {stripped}"
        for i, line in enumerate(content.splitlines(), start=1)
        if (stripped := line.strip())
        and not stripped.startswith("#")
        and any(pattern in stripped and "|| true" in stripped for pattern in critical_patterns)
    )

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    if found_issues:
        print(f"[IMP:9][test_deploy_no_silent_errors] FAIL: {len(found_issues)} critical command(s) use `|| true`:")
        for issue in found_issues:
            print(f"[IMP:9][test_deploy_no_silent_errors] {issue}")
    else:
        print("[IMP:9][test_deploy_no_silent_errors] PASS: No `|| true` on critical commands")
    print("--- END LDD TRAJECTORY ---")

    assert len(found_issues) == 0, (
        f"[IMP:9][test_deploy_no_silent_errors] FAIL: {len(found_issues)} critical command(s) use `|| true`:\n"
        + "\n".join(found_issues)
    )
    logger.info("[IMP:9][test_deploy_no_silent_errors] PASS: No silent error suppression on critical commands")


# endregion FUNC_test_deploy_no_silent_errors


# ── Test: atomic_up saves output before pipe (no pipefail race) ─────────────


# region FUNC_test_deploy_pipefail_safe
@pytest.mark.contract
## @purpose  Verify atomic_up() does NOT pipe `docker compose up` directly into `while read`.
##           The fix (C12) captures output in a variable first, THEN pipes to while read.
##           This eliminates the SIGPIPE race condition where docker compose exit code is lost.
## @rationale C12: pipefail race masked real docker compose errors.
def test_deploy_pipefail_safe() -> None:
    """
    # ▶ SCRIPT_PATH → ◇ grep region ATOMIC_UP for `$(docker compose up` pattern
    #   → ◇ no `docker compose up.*| while` → ⎋ pass | fail
    """
    logger.info(
        "[IMP:7][test_deploy_pipefail_safe] Checking atomic_up for variable-capture pattern in: %s", SCRIPT_PATH
    )

    with open(SCRIPT_PATH) as f:
        content = f.read()

    # Extract atomic_up region
    lines = content.splitlines()
    in_atomic_up = False
    atomic_up_lines = []
    for line in lines:
        if "# region ATOMIC_UP" in line:
            in_atomic_up = True
            continue
        if in_atomic_up:
            if "# endregion ATOMIC_UP" in line:
                break
            atomic_up_lines.append(line)

    # Check: no `docker compose up ... | while` direct pipe pattern
    pipe_inline = any("docker compose up" in line and "| while" in line for line in atomic_up_lines)

    # Check: has `up_output="$(` capture pattern
    has_var_capture = any('up_output="$(docker compose up' in line for line in atomic_up_lines)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print(f"[IMP:7][test_deploy_pipefail_safe] pipe_inline={pipe_inline}, has_var_capture={has_var_capture}")
    if pipe_inline:
        print("[IMP:9][test_deploy_pipefail_safe] FAIL: atomic_up still has direct pipe from docker compose")
    elif has_var_capture:
        print("[IMP:9][test_deploy_pipefail_safe] PASS: atomic_up uses variable-capture pattern")
    else:
        print("[IMP:9][test_deploy_pipefail_safe] FAIL: atomic_up has neither pipe nor var-capture pattern")
    print("--- END LDD TRAJECTORY ---")

    assert not pipe_inline, (
        "[IMP:9][test_deploy_pipefail_safe] FAIL: atomic_up still pipes docker compose up directly into while read"
    )
    assert has_var_capture, "[IMP:9][test_deploy_pipefail_safe] FAIL: atomic_up does not use up_output variable capture"
    logger.info("[IMP:9][test_deploy_pipefail_safe] PASS: atomic_up uses variable-capture pattern")


# endregion FUNC_test_deploy_pipefail_safe


# ── Test: cleanup_on_error has fail-fast (exit on rollback failure) ─────────


# region FUNC_test_deploy_rollback_exit
@pytest.mark.contract
## @purpose  Verify cleanup_on_error() exits with CRITICAL log on rollback failure.
##           The fix (C6) replaces `|| true` with explicit error check and exit 1.
## @rationale C6: silent rollback failure leaves containers in inconsistent state.
def test_deploy_rollback_exit() -> None:
    """
    # ▶ SCRIPT_PATH → ◇ grep region TRAP_ROLLBACK for fail-fast behavior
    #   → ◇ has `CRITICAL` log level + `exit 1` → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_deploy_rollback_exit] Checking _rollback_on_error for fail-fast in: %s", SCRIPT_PATH)

    with open(SCRIPT_PATH) as f:
        content = f.read()

    # Extract TRAP_ROLLBACK region
    lines = content.splitlines()
    in_trap = False
    trap_lines = []
    for line in lines:
        if "# region TRAP_ROLLBACK" in line:
            in_trap = True
            continue
        if in_trap:
            if "# endregion TRAP_ROLLBACK" in line:
                break
            trap_lines.append(line)

    trap_content = "\n".join(trap_lines)

    has_critical_log = "CRITICAL" in trap_content
    has_exit = "exit 1" in trap_content

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print(f"[IMP:7][test_deploy_rollback_exit] has_critical_log={has_critical_log}, has_exit={has_exit}")
    if has_critical_log and has_exit:
        print("[IMP:9][test_deploy_rollback_exit] PASS: _rollback_on_error has fail-fast with CRITICAL log + exit")
    elif has_critical_log:
        print("[IMP:9][test_deploy_rollback_exit] WARN: has CRITICAL log but no exit 1")
    else:
        print("[IMP:9][test_deploy_rollback_exit] FAIL: _rollback_on_error missing CRITICAL log and exit 1")
    print("--- END LDD TRAJECTORY ---")

    assert has_critical_log and has_exit, (
        "[IMP:9][test_deploy_rollback_exit] FAIL: _rollback_on_error missing CRITICAL log or exit 1"
    )
    logger.info("[IMP:9][test_deploy_rollback_exit] PASS: _rollback_on_error has fail-fast behavior")


# endregion FUNC_test_deploy_rollback_exit
