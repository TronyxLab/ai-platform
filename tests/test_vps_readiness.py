#!/usr/bin/env python3
# GREP_SUMMARY: test vps-readiness ping pong forced-command check
# STRUCTURE: ▶ test_ping_check_uses_pong → grep vps-readiness.sh → ⚡ assert "ping" command + "pong" grep
# region MODULE_CONTRACT
## @purpose  Tests for vps-readiness.sh forced-command ping check (DevPlan 001 TASK-4.2)
## @scope    Verifies that vps-readiness check 2 uses "ping" verb instead of
##           "platform-deliver --ping" and expects "pong" response.
## @invariants
##   - Uses static analysis (grep) on vps-readiness.sh
##   - Script path resolved relative to test file location
## @rationale TASK-4.2 replaces the forced-command call from "platform-deliver --ping"
##            to "ping" (the new verb added in TASK-3.1). The grep pattern also
##            changes from matching "pong|PONG|ready" to exact "pong".
## @changes 2026-07-21 | Initial implementation (DevPlan 001)
# endregion MODULE_CONTRACT

from pathlib import Path

import pytest


@pytest.fixture
def vps_readiness_script() -> Path:
    """Resolve path to vps-readiness.sh relative to project root."""
    return Path(__file__).parents[1] / "core" / "lib" / "vps-readiness.sh"


# region FUNC_test_ping_check_uses_pong
@pytest.mark.static_audit
def test_ping_check_uses_pong(caplog, vps_readiness_script: Path) -> None:
    """Verify vps-readiness check 2 uses "ping" command and expects "pong".

    Regression: P2 — make deploy pre-flight check used "platform-deliver --ping"
    which is not a registered forced-command verb. After TASK-3.1 added the
    "ping" verb, vps-readiness.sh must call "ping" instead.

    Expected changes:
    - SSH command uses "ping" instead of "platform-deliver --ping"
    - grep pattern matches exact "pong" (not case-insensitive multi-pattern)
    """
    # 🧪 TRAP[TEST] · Regression: P2 — vps-readiness uses wrong forced-command verb
    # · Scenario: check 2 calls "ping" not "platform-deliver --ping"
    # · Last fail: 2026-07-21 — orchestrator final report
    # · Remove if: vps-readiness.sh check_vps_ready is rewritten

    caplog.set_level(7)

    script_text = vps_readiness_script.read_text()

    # The SSH command in check 2 must use "ping" verb (not "platform-deliver --ping")
    # Look for the SSH call pattern: ssh ... "ping" 2>&1
    # The string "platform-deliver --ping" may still appear in remediation/messages
    # (error messages tell the user what command was attempted), so only assert on
    # the SSH command pattern itself.
    assert '"ping" 2>&1' in script_text, (
        "TASK-4.2 not implemented: SSH command should use 'ping' verb, not 'platform-deliver --ping'\n"
        'Expected pattern: ssh ... "ping" 2>&1'
    )

    # Verify grep expects exact "pong" (was case-insensitive multi-pattern grep -qi "pong|PONG|ready")
    assert 'grep -q "pong"' in script_text, (
        "TASK-4.2 not implemented: grep pattern should be exact 'pong' match\nExpected: grep -q \"pong\""
    )

    # The old grep pattern must NOT be present in the check 2 section
    # (grep -qi with multi-pattern was replaced with grep -q "pong")
    assert "grep -qi" not in script_text, (
        "TASK-4.2 not fully implemented: old case-insensitive grep pattern 'grep -qi' still present"
    )

    print("[IMP:9][test] vps-readiness check 2: OK — uses 'ping' verb and exact 'pong' match")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_ping_check_uses_pong
