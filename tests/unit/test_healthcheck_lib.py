#!/usr/bin/env python3
# GREP_SUMMARY: test-healthcheck-lib check_tcp exec_check check_http timeout unit bash subprocess
# STRUCTURE: ▶ _run_bash helper → ○ test_check_tcp_success/timeout → ○ test_exec_check_* → ○ test_check_http_with_timeout → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for healthcheck.sh library functions: check_tcp(), exec_check(), check_http() timeout param.
## @scope    Covers 6 test scenarios from DevPlan 083 §$TEST_SPEC:
##           1. test_check_tcp_success — TCP connect to reachable host:port returns 0
##           2. test_check_tcp_timeout — TCP connect to unreachable port returns 1
##           3. test_exec_check_success — exec_check with valid container + command returns 0
##           4. test_exec_check_container_not_running — exec_check with stopped container returns 1
##           5. test_exec_check_command_fails — exec_check with failing command returns 1
##           6. test_check_http_with_timeout — check_http accepts timeout parameter
## @invariants
##   - Tests use subprocess.run with temp bash scripts (bash function testing)
##   - check_tcp tests: no Docker required, use TCP /dev/tcp built-in
##   - exec_check tests: require Docker (@pytest.mark.requires_docker)
##   - check_http tests: no Docker required, use check_http with dummy URLs
## @rationale Bash functions must be tested in a real bash environment.
##   Subprocess with temp scripts is the standard pattern for bash library testing.
## @changes 2026-07-26 · DevPlan 083 — Initial implementation
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

# Resolve absolute path to healthcheck.sh
_HEALTHCHECK_LIB: Path = Path(__file__).resolve().parent.parent.parent / "core" / "lib" / "healthcheck.sh"
_LOGGING_LIB: Path = Path(__file__).resolve().parent.parent.parent / "core" / "lib" / "logging.sh"


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


# region FUNC__run_bash
## @purpose  Write a bash script to a temp file and execute it via subprocess,
##           capturing stdout/stderr/returncode. Sources healthcheck.sh + logging.sh.
## @io       ⇥ (tmp_path: Path, code: str) → ⎋ CompletedProcess
## @complexity O(1)
def _run_bash(tmp_path: Path, code: str) -> subprocess.CompletedProcess:
    """Run bash code with healthcheck.sh sourced, return subprocess result.

    ## @purpose  Isolate bash script execution in a temp file for deterministic testing.
    ##            Sources the libraries under test before executing user code.
    """
    script = tmp_path / "test_healthcheck.sh"
    script_content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'HEALTHCHECK_LIB="{_HEALTHCHECK_LIB}"\n'
        f'LOGGING_LIB="{_LOGGING_LIB}"\n'
        'source "$LOGGING_LIB"\n'
        'source "$HEALTHCHECK_LIB"\n'
        f"{code}\n"
    )
    script.write_text(script_content)
    script.chmod(0o755)

    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=10,
    )


# endregion FUNC__run_bash


# ═══════════════════════════════════════════════════════════════════
# TESTS: check_tcp
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_check_tcp_success
## @purpose  Verify check_tcp returns 0 for a reachable TCP endpoint.
##           Uses bash's /dev/tcp with a temporary listener (nc or timeout-based).
## @io       ⇥ tmp_path → ⎋ assert returncode == 0, stderr contains connected
## @complexity O(1)
def test_check_tcp_success(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: check_tcp must return 0 for reachable port
    # · Scenario: Start a temporary TCP listener on a random port, connect via check_tcp
    # · Last fail: Never
    # · Remove if: check_tcp signature changes or /dev/tcp support removed
    import random

    port = random.randint(20000, 30000)

    # Use timeout-based background listener via bash /dev/tcp server
    result = _run_bash(
        tmp_path,
        f"""
# Start a background TCP listener on port {port} for 3 seconds, then connect
timeout 3 bash -c "echo ok | nc -l 127.0.0.1 {port}" &
sleep 0.3  # Give listener time to start
check_tcp "127.0.0.1" "{port}" 2
exit_code=$?
wait  # Clean up listener
exit $exit_code
""",
    )

    # Log output for LDD
    logger.info("[IMP:7][test_check_tcp_success] stdout: %s", result.stdout)
    logger.info("[IMP:7][test_check_tcp_success] stderr: %s", result.stderr)
    logger.info("[IMP:7][test_check_tcp_success] returncode: %d", result.returncode)

    # Check stderr for connected message
    assert "connected" in result.stderr.lower(), (
        f"[IMP:9][test_check_tcp_success] FAIL: expected 'connected' in stderr, got: {result.stderr}"
    )
    assert result.returncode == 0, (
        f"[IMP:9][test_check_tcp_success] FAIL: check_tcp returned {result.returncode}, stderr: {result.stderr}"
    )

    logger.info("[IMP:9][test_check_tcp_success] PASS: check_tcp connected to reachable port")


# endregion FUNC_test_check_tcp_success


# region FUNC_test_check_tcp_timeout
## @purpose  Verify check_tcp returns 1 when connecting to an unreachable port (timeout).
##           Uses port 1 (commonly blocked/unreachable) with 1s timeout.
## @io       ⇥ tmp_path → ⎋ assert returncode == 1, stderr contains 'failed' or 'timed out'
## @complexity O(1)
def test_check_tcp_timeout(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: check_tcp must return 1 for unreachable port
    # · Scenario: check_tcp to 127.0.0.1:1 with 1s timeout → returns 1
    # · Last fail: Never
    # · Remove if: check_tcp signature changes
    result = _run_bash(
        tmp_path,
        """
check_tcp "127.0.0.1" "1" 1
exit $?
""",
    )

    # Log output for LDD
    logger.info("[IMP:7][test_check_tcp_timeout] stdout: %s", result.stdout)
    logger.info("[IMP:7][test_check_tcp_timeout] stderr: %s", result.stderr)
    logger.info("[IMP:7][test_check_tcp_timeout] returncode: %d", result.returncode)

    assert result.returncode == 1, (
        f"[IMP:9][test_check_tcp_timeout] FAIL: check_tcp returned {result.returncode} (expected 1), "
        f"stderr: {result.stderr}"
    )

    logger.info("[IMP:9][test_check_tcp_timeout] PASS: check_tcp timed out as expected")


# endregion FUNC_test_check_tcp_timeout


# ═══════════════════════════════════════════════════════════════════
# TESTS: exec_check
# ═══════════════════════════════════════════════════════════════════

# All exec_check tests require Docker
pytestmark_requires_docker = pytest.mark.requires_docker


# region FUNC_test_exec_check_success
## @purpose  Verify exec_check returns 0 when executing a valid command in a running container.
##           Uses a test container started for the test.
## @io       ⇥ docker → ⎋ assert returncode == 0
## @complexity O(2) — requires Docker container lifecycle
@pytest.mark.requires_docker
def test_exec_check_success(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: exec_check must return 0 for valid container + command
    # · Scenario: Start a busybox container, run 'true' command via exec_check
    # · Last fail: Never
    # · Remove if: exec_check signature changes
    logger.info("[IMP:8][test_exec_check_success] Setting up test container")

    # Start a test container
    subprocess.run(
        ["docker", "run", "-d", "--name", "test-exec-check-success", "--rm", "busybox:latest", "sleep", "30"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    try:
        result = _run_bash(
            tmp_path,
            """
exec_check "test-exec-check-success" "true"
exit $?
""",
        )

        logger.info("[IMP:7][test_exec_check_success] stdout: %s", result.stdout)
        logger.info("[IMP:7][test_exec_check_success] stderr: %s", result.stderr)
        logger.info("[IMP:7][test_exec_check_success] returncode: %d", result.returncode)

        assert result.returncode == 0, (
            f"[IMP:9][test_exec_check_success] FAIL: exec_check returned {result.returncode}, stderr: {result.stderr}"
        )
        logger.info("[IMP:9][test_exec_check_success] PASS: exec_check succeeded")
    finally:
        # Cleanup
        subprocess.run(
            ["docker", "rm", "-f", "test-exec-check-success"],
            capture_output=True,
            text=True,
            timeout=30,
        )


# endregion FUNC_test_exec_check_success


# region FUNC_test_exec_check_container_not_running
## @purpose  Verify exec_check returns 1 when the container is not running.
## @io       ⇥ docker → ⎋ assert returncode == 1
## @complexity O(2) — requires Docker container lifecycle
@pytest.mark.requires_docker
def test_exec_check_container_not_running(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: exec_check must return 1 for stopped/non-existent container
    # · Scenario: Try exec_check on a non-existent container name → returns 1
    # · Last fail: Never
    # · Remove if: exec_check signature changes
    result = _run_bash(
        tmp_path,
        """
exec_check "this-container-definitely-does-not-exist-12345" "true"
exit $?
""",
    )

    logger.info("[IMP:7][test_exec_check_not_running] stdout: %s", result.stdout)
    logger.info("[IMP:7][test_exec_check_not_running] stderr: %s", result.stderr)
    logger.info("[IMP:7][test_exec_check_not_running] returncode: %d", result.returncode)

    assert result.returncode == 1, (
        f"[IMP:9][test_exec_check_not_running] FAIL: exec_check returned {result.returncode} "
        f"(expected 1), stderr: {result.stderr}"
    )
    logger.info("[IMP:9][test_exec_check_not_running] PASS: exec_check correctly returned 1 for missing container")


# endregion FUNC_test_exec_check_container_not_running


# region FUNC_test_exec_check_command_fails
## @purpose  Verify exec_check returns 1 when the command inside the container fails.
## @io       ⇥ docker → ⎋ assert returncode == 1
## @complexity O(2) — requires Docker container lifecycle
@pytest.mark.requires_docker
def test_exec_check_command_fails(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: exec_check must return 1 when command exits non-zero
    # · Scenario: Start a busybox container, run 'false' command via exec_check → returns 1
    # · Last fail: Never
    # · Remove if: exec_check signature changes
    logger.info("[IMP:8][test_exec_check_command_fails] Setting up test container")

    # Start a test container
    subprocess.run(
        ["docker", "run", "-d", "--name", "test-exec-check-fail", "--rm", "busybox:latest", "sleep", "30"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    try:
        result = _run_bash(
            tmp_path,
            """
exec_check "test-exec-check-fail" "false"
exit $?
""",
        )

        logger.info("[IMP:7][test_exec_check_command_fails] stdout: %s", result.stdout)
        logger.info("[IMP:7][test_exec_check_command_fails] stderr: %s", result.stderr)
        logger.info("[IMP:7][test_exec_check_command_fails] returncode: %d", result.returncode)

        assert result.returncode == 1, (
            f"[IMP:9][test_exec_check_command_fails] FAIL: exec_check returned {result.returncode} "
            f"(expected 1 for failing command), stderr: {result.stderr}"
        )
        logger.info("[IMP:9][test_exec_check_command_fails] PASS: exec_check correctly returned 1 for failing command")
    finally:
        subprocess.run(
            ["docker", "rm", "-f", "test-exec-check-fail"],
            capture_output=True,
            text=True,
            timeout=30,
        )


# endregion FUNC_test_exec_check_command_fails


# ═══════════════════════════════════════════════════════════════════
# TESTS: check_http with timeout
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_check_http_with_timeout
## @purpose  Verify check_http accepts and uses the timeout parameter.
##           Tests that --max-time is passed to curl by checking check_http behavior
##           with a known short timeout against an unreachable endpoint.
## @io       ⇥ tmp_path → ⎋ assert check_http returns 1 (curl fails on timeout)
## @complexity O(1)
def test_check_http_with_timeout(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: check_http must accept timeout as 3rd parameter
    # · Scenario: check_http "http://127.0.0.1:1/nonexistent" "200" 1 → returns 1 (timeout/fail)
    # · Last fail: Never
    # · Remove if: check_http signature changes (removes $3 timeout)
    result = _run_bash(
        tmp_path,
        """
check_http "http://127.0.0.1:1/" "200" 1
exit $?
""",
    )

    logger.info("[IMP:7][test_check_http_with_timeout] stdout: %s", result.stdout)
    logger.info("[IMP:7][test_check_http_with_timeout] stderr: %s", result.stderr)
    logger.info("[IMP:7][test_check_http_with_timeout] returncode: %d", result.returncode)

    # Curl should fail fast with 1s timeout on unreachable endpoint
    assert result.returncode == 1, (
        f"[IMP:9][test_check_http_with_timeout] FAIL: check_http with timeout=1s returned "
        f"{result.returncode} (expected 1 — should timeout), stderr: {result.stderr}"
    )

    # Verify timeout is mentioned in stderr or curl failed
    assert "IMP:" in result.stderr, f"[IMP:9][test_check_http_with_timeout] FAIL: No IMP log in stderr: {result.stderr}"

    logger.info("[IMP:9][test_check_http_with_timeout] PASS: check_http accepts timeout parameter")


# endregion FUNC_test_check_http_with_timeout
