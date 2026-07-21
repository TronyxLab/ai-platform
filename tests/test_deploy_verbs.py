#!/usr/bin/env python3
# GREP_SUMMARY: test deploy-verbs ping exit parse_verb forced-command
# STRUCTURE: ▶ test_ping_verb_returns_pong → bash SSH_ORIGINAL_COMMAND=ping → ⚡ assert "pong" stdout
# ▶ test_exit_verb_returns_zero → bash SSH_ORIGINAL_COMMAND=exit → ⚡ assert exit 0
# region MODULE_CONTRACT
## @purpose  Tests for deploy.sh ping/exit verb handling (DevPlan 001 TASK-3.1)
## @scope    Verifies that:
##           1. parse_verb "ping" returns "pong" and exits 0
##           2. parse_verb "exit" exits 0 (no-op success)
## @invariants
##   - Tests use isolated bash subprocess with SSH_ORIGINAL_COMMAND env var
##   - deploy.sh must exist and be locatable via relative path
##   - No actual SSH connection needed — function exits before dispatch
## @rationale The ping/exit verbs are pre-dispatch filters in parse_verb().
##            They execute before any case-switch or exec call, making them
##            testable in isolation via SSH_ORIGINAL_COMMAND environment variable.
## @changes 2026-07-21 | Initial implementation (DevPlan 001)
# endregion MODULE_CONTRACT

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def deploy_script() -> Path:
    """Resolve path to deploy.sh relative to project root."""
    return Path(__file__).parents[1] / "core" / "entrypoints" / "deploy.sh"


# region FUNC_test_ping_verb_returns_pong
@pytest.mark.static
def test_ping_verb_returns_pong(caplog, deploy_script: Path) -> None:
    """Verify parse_verb "ping" returns "pong" and exits 0.

    Regression: P2 — make deploy pre-flight broken, ssh ci-deploy@host "exit"
    was interpreted as deploy project "exit". ping/exit verbs added as
    pre-dispatch filters before the case-statement.

    Expected behavior:
    - SSH_ORIGINAL_COMMAND=ping → stdout contains "pong"
    - Exit code 0
    """
    # 🧪 TRAP[TEST] · Regression: P2 — ping verb missing from forced-command parser
    # · Scenario: SSH_ORIGINAL_COMMAND=ping → parse_verb returns "pong"
    # · Last fail: 2026-07-21 — orchestrator final report, make deploy pre-flight broken
    # · Remove if: deploy.sh parse_verb() is rewritten or removed

    caplog.set_level(7)

    script_path = deploy_script.resolve()
    assert script_path.exists(), f"deploy.sh not found at {script_path}"

    result = subprocess.run(
        ["bash", "-c", f"""
set -euo pipefail
SSH_ORIGINAL_COMMAND="ping" \
PATHS_INTERNAL_DIR=/tmp \
source "{script_path}" 2>&1
"""],
        capture_output=True, text=True, timeout=10,
    )

    stdout = result.stdout
    stderr = result.stderr

    print(f"[IMP:9][test] ping stdout: {stdout.strip()}")
    if stderr:
        print(f"[IMP:8][test] ping stderr: {stderr.strip()}")

    assert "pong" in stdout, (
        f"parse_verb did not return 'pong' for ping verb\n"
        f"stdout: {stdout}\n"
        f"stderr: {stderr}"
    )

    # Exit code 0 — parse_verb calls exit 0 after printing "pong"
    assert result.returncode == 0, (
        f"parse_verb exit code for ping was {result.returncode}, expected 0\n"
        f"stdout: {stdout}"
    )

    print("[IMP:9][test] ping verb: OK — returned 'pong' with exit 0")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
# endregion FUNC_test_ping_verb_returns_pong


# region FUNC_test_exit_verb_returns_zero
@pytest.mark.static
def test_exit_verb_returns_zero(caplog, deploy_script: Path) -> None:
    """Verify parse_verb "exit" exits 0 (no-op success).

    Regression: P2 — exit verb needed for SSH connectivity checks.
    Without the exit verb, 'ssh ci-deploy@host "exit"' was interpreted
    as deploying a project named "exit".

    Expected behavior:
    - SSH_ORIGINAL_COMMAND=exit → exit code 0
    - No stdout output (exit is silent)
    """
    # 🧪 TRAP[TEST] · Regression: P2 — exit verb missing from forced-command parser
    # · Scenario: SSH_ORIGINAL_COMMAND=exit → parse_verb exits 0
    # · Last fail: 2026-07-21 — orchestrator final report
    # · Remove if: deploy.sh parse_verb() is rewritten or removed

    caplog.set_level(7)

    script_path = deploy_script.resolve()
    assert script_path.exists(), f"deploy.sh not found at {script_path}"

    result = subprocess.run(
        ["bash", "-c", f"""
set -euo pipefail
SSH_ORIGINAL_COMMAND="exit" \
PATHS_INTERNAL_DIR=/tmp \
source "{script_path}" 2>&1
"""],
        capture_output=True, text=True, timeout=10,
    )

    stdout = result.stdout
    stderr = result.stderr

    print(f"[IMP:9][test] exit stdout: {stdout.strip()}")
    if stderr:
        print(f"[IMP:8][test] exit stderr: {stderr.strip()}")

    # Exit code 0 — parse_verb calls exit 0 (no-op success, no stdout)
    assert result.returncode == 0, (
        f"parse_verb exit code for 'exit' was {result.returncode}, expected 0\n"
        f"stdout: {stdout}"
    )

    print("[IMP:9][test] exit verb: OK — exit code 0")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
# endregion FUNC_test_exit_verb_returns_zero
