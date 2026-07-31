# GREP_SUMMARY: test-lib-healthcheck healthcheck.sh bash poll_until_healthy check_docker_health check_http docker curl subprocess stderr IMP timeout mock PATH
# STRUCTURE: ▶ _run_bash(script → subprocess.run) → ○ poll_until_healthy 4 tests: ◇ success/timeout/retry/interval → ○ check_docker_health 4 tests: ◇ healthy/unhealthy/starting/not-found → ○ check_http 3 tests: ◇ 200/404/301-multi → ○ poll_docker_health 2 tests: ◇ defined/success → ⎋ 13 test functions

# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(BASH-HEALTHCHECK):2; TECH(PYTEST):2]
## @purpose  Unit tests for core/lib/healthcheck.sh — the centralised healthcheck
##           library providing poll_until_healthy, check_docker_health, and check_http.
##           Tests verify polling logic, return codes, mock docker/curl via PATH
##           injection, and custom timeouts/intervals — all in isolated tmp_path.
## @scope    13 test functions covering:
##
##           - poll_until_healthy: success (exit 0), timeout (exit 1), retry
##             (counter-based 3rd attempt succeeds), custom interval
##           - check_docker_health: healthy, unhealthy, starting, not-found
##           - check_http: 200 success, 404 wrong code, 301 multi-code expected
##           - poll_docker_health: defined (function exists), success (mock docker healthy)
## @invariants
##
##   - Every test uses tmp_path for script isolation (Zero Hardcode Rule)
##   - LIB_DIR resolved via Path(__file__).resolve() — no hardcoded paths
##   - Mock docker and curl scripts created in tmp_path/mock-bin/ with PATH override
##   - All bash scripts run with subprocess.run (capture_output, text, timeout=10)
##   - No caplog fixture: bash logs go to stderr, not Python logging subsystem
##   - LDD verification via stderr assertion: [IMP:N] present for expected levels
##   - No set -e in test scripts (poll_until_healthy and docker/curl checks return
##     non-zero in failure scenarios, which would abort set -e scripts prematurely)
##   - poll_until_healthy tests use small timeout=2 and interval=0.1 for speed
## @rationale Q: Why subprocess.run instead of pure Python simulation?
##            A: healthcheck.sh is a pure bash library whose core logic (eval of
##            check_command, docker inspect subprocess, curl subprocess) can only
##            be tested in a real bash environment. Mock binaries in tmp_path via
##            PATH injection replace external dependencies without needing Docker
##            or network access.
##            Q: Why not @ldd_trajectory decorator?
##            A: @ldd_trajectory relies on caplog (Python logging capture). Bash
##            writes directly to stderr, bypassing Python logging entirely. LDD
##            verification is done by asserting stderr contains [IMP:N] for the
##            expected importance level.
##            Q: Why no set -euo pipefail in helper?
##            A: poll_until_healthy, check_docker_health, and check_http return
##            non-zero on failure. With set -e, the script would abort before
##            rc=$? can capture the return code, making return-code assertions
##            impossible. Tests manage error handling explicitly via || true or
##            rc=$? patterns.
## @changes LAST_CHANGE: 2026-07-07 · Initial implementation per DevPlan test spec
## @modulemap
##   - _run_bash                          [W:30] Helper: write temp script, source both libs, run bash, return result
##   - test_poll_until_healthy_success     [W:40] check_command=exit 0 → returns 0 instantly
##   - test_poll_until_healthy_timeout     [W:40] check_command=exit 1 → timeout 2s → returns 1
##   - test_poll_until_healthy_retry       [W:50] check fails twice then succeeds → poll waits and returns 0
##   - test_poll_until_healthy_custom_interval [W:40] interval=0.1 → poll succeeds with custom interval
##   - test_check_docker_health_healthy    [W:40] mock docker returns healthy → exit 0
##   - test_check_docker_health_unhealthy  [W:40] mock docker returns unhealthy → exit 1
##   - test_check_docker_health_starting   [W:40] mock docker returns starting → exit 2
##   - test_check_docker_health_no_container [W:40] mock docker returns exit 1 → exit 3
##   - test_check_http_success             [W:40] mock curl returns 200 → exit 0
##   - test_check_http_wrong_code          [W:40] mock curl returns 404, expected=200 → exit 1
##   - test_check_http_custom_codes        [W:40] mock curl returns 301, expected="200,301,302" → exit 0
##   - test_poll_docker_health_defined     [W:30] function exists in healthcheck.sh
##   - test_poll_docker_health_success     [W:40] mock docker healthy → poll returns 0
## @usecases
##   - Developer: run pytest after modifying healthcheck.sh → all 11 tests pass, no regressions
##   - Architect: verify poll-loop logic, docker/curl mock isolation, return-code contracts
##   - poll_docker_health: convenience wrapper around poll_until_healthy + check_docker_health
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import os
import subprocess
from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

# Resolve absolute path to core/lib/ once at module load time.
# Relies on: tests/test_lib_healthcheck.py → ../core/lib/
_LIB_DIR: Path = Path(__file__).resolve().parent.parent / "core" / "lib"


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


# region FUNC__run_bash
## @purpose  Write a bash script to a temp file that sources both logging.sh
##           and healthcheck.sh, then execute it via subprocess with optional
##           custom environment variables. Each call gets a fresh script in an
##           isolated tmp_path directory — no cross-test contamination.
## @io       ⇥ (tmp_path: Path, code: str, env: dict[str,str]|None) → ⎋ CompletedProcess
## @complexity O(1) — single subprocess.run with 10s timeout
## @invariants
##   - Script file is chmod 755 before execution
##   - Timeout set to 10 seconds (fail-fast on infinite loops)
##   - Does NOT add set -euo pipefail — healthcheck scripts intentionally
##     test error handling: non-zero exit codes are EXPECTED results
##     (e.g., unhealthy container → exit 1), not bash errors to abort on.
##   - Both logging.sh and healthcheck.sh are sourced automatically before code runs
##   - Custom env (e.g. PATH override for mock bins) merged on top of os.environ copy
def _run_bash(
    tmp_path: Path,
    code: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run bash code with logging.sh+healthcheck.sh sourced, return subprocess result.

    ## @purpose  Isolate bash script execution in a temp file for deterministic testing.
    ##            Sources both libraries under test (_LIB_DIR/logging.sh, _LIB_DIR/healthcheck.sh)
    ##            before executing user code. Returns CompletedProcess for stdout/stderr/rc assertions.
    ## @io       ⇥ tmp_path: Path — pytest fixture for temp dir
    ##             code: str — bash commands to execute after sourcing both libs
    ##             env: dict[str,str]|None — optional extra env vars (merged on top of os.environ)
    ##           ⎋ CompletedProcess with stdout, stderr, returncode attributes
    ## @complexity O(1)
    """
    script = tmp_path / "test_script.sh"
    lib_dir_escaped = str(_LIB_DIR)

    script_content = (
        "#!/usr/bin/env bash\n"
        f'LIB_DIR="{lib_dir_escaped}"\n'
        'source "$LIB_DIR/logging.sh"\n'
        'source "$LIB_DIR/healthcheck.sh"\n'
        f"{code}\n"
    )
    script.write_text(script_content)
    script.chmod(0o755)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=10,
        env=run_env,
    )


# endregion FUNC__run_bash


# ═══════════════════════════════════════════════════════════════════
# POLL_UNTIL_HEALTHY TESTS
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_poll_until_healthy_success
## @purpose  Verify poll_until_healthy exits 0 immediately when check_command
##           succeeds (exit 0) on first attempt. Small timeout/interval ensure
##           no real wait in tests.
## @io       ⇥ tmp_path → ⎋ assert returncode==0, stderr contains RC=0 and IMP:9
## @complexity O(1)
def test_poll_until_healthy_success(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: check_command=true must return 0 instantly
    # · Scenario: poll_until_healthy "svc" "true" 2 0.1 → RC=0, [IMP:9] healthy log
    # · Last fail: Never
    # · Remove if: poll_until_healthy signature changes (name, cmd, timeout, interval)
    # · NOTE: eval "exit 0" exits the entire shell — use "true" instead of "exit 0"
    result = _run_bash(
        tmp_path,
        """
__LOG_PREFIX="healthcheck"
poll_until_healthy "svc" "true" 2 0.1
rc=$?
echo "RC=$rc" >&2
exit $rc
""",
    )
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
    assert "RC=0" in result.stderr, f"Expected RC=0 in stderr\ngot: {result.stderr}"
    # IMP:9 is logged on success — business logic level
    assert "[IMP:9][healthcheck][poll_until_healthy]" in result.stderr, (
        f"Expected [IMP:9] healthy log in stderr\ngot: {result.stderr}"
    )


# endregion FUNC_test_poll_until_healthy_success


# region FUNC_test_poll_until_healthy_timeout
## @purpose  Verify poll_until_healthy returns 1 when check_command always fails
##           and timeout expires. Timeout=2s, interval=0.1s — test completes
##           in ~2 seconds.
## @io       ⇥ tmp_path → ⎋ assert returncode==1, stderr contains RC=1 and IMP:10
## @complexity O(1) — runs for exactly timeout seconds
def test_poll_until_healthy_timeout(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: always-failing check must timeout and return 1
    # · Scenario: poll_until_healthy "svc" "false" 2 0.1 → RC=1, [IMP:10] timeout log
    # · Last fail: Never
    # · Remove if: poll_until_healthy timeout logic changes
    # · NOTE: eval "exit 1" exits the entire shell — use "false" instead of "exit 1"
    result = _run_bash(
        tmp_path,
        """
__LOG_PREFIX="healthcheck"
poll_until_healthy "svc" "false" 2 0.1
rc=$?
echo "RC=$rc" >&2
exit $rc
""",
    )
    assert result.returncode == 1, f"Expected exit 1 (timeout), got {result.returncode}\nstderr: {result.stderr}"
    assert "RC=1" in result.stderr, f"Expected RC=1 in stderr\ngot: {result.stderr}"
    # IMP:10 is logged on timeout — critical level
    assert "[IMP:10][healthcheck][poll_until_healthy]" in result.stderr, (
        f"Expected [IMP:10] timeout log in stderr\ngot: {result.stderr}"
    )


# endregion FUNC_test_poll_until_healthy_timeout


# region FUNC_test_poll_until_healthy_retry
## @purpose  Verify poll_until_healthy retries a failing check until it succeeds.
##           Uses a counter file: check exits 1 for first 2 attempts, exits 0 on
##           the 3rd. Poll must wait for the 3rd attempt and return 0.
## @io       ⇥ tmp_path → ⎋ assert returncode==0, stderr contains RC=0
## @complexity O(1) — 2 intervals * 0.1s = ~0.2s runtime
## @invariants — Counter file path embedded in test script via f-string
def test_poll_until_healthy_retry(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: poll must retry until check succeeds
    # · Scenario: counter-based check fails 1st and 2nd call, succeeds on 3rd
    # · Last fail: Never
    # · Remove if: poll_until_healthy retry logic changes
    counter_file = tmp_path / "counter.txt"
    counter_file.write_text("0")
    counter_path = str(counter_file)

    code = (
        f'__LOG_PREFIX="healthcheck"\n'
        f'COUNTER_PATH="{counter_path}"\n'
        "check_with_counter() {\n"
        "    local c\n"
        '    c=$(<"$COUNTER_PATH")\n'
        "    c=$((c + 1))\n"
        '    printf "%s" "$c" > "$COUNTER_PATH"\n'
        '    [ "$c" -ge 3 ]\n'
        "}\n"
        'poll_until_healthy "retry-svc" check_with_counter 2 0.1\n'
        "rc=$?\n"
        'echo "RC=$rc" >&2\n'
        "exit $rc\n"
    )
    result = _run_bash(tmp_path, code)
    assert result.returncode == 0, (
        f"Expected exit 0 (retry succeeded), got {result.returncode}\nstderr: {result.stderr}"
    )
    assert "RC=0" in result.stderr, f"Expected RC=0 in stderr\ngot: {result.stderr}"
    # Verify exactly 3 attempts were made: counter in file should be 3
    final_count = counter_file.read_text().strip()
    assert final_count == "3", f"Expected counter=3 after 3 attempts, got counter={final_count}"


# endregion FUNC_test_poll_until_healthy_retry


# region FUNC_test_poll_until_healthy_custom_interval
## @purpose  Verify poll_until_healthy accepts a custom interval parameter and works
##           correctly with interval=0.1 (faster polling). The check succeeds
##           immediately — this tests the parameter plumbing, not timing accuracy.
## @io       ⇥ tmp_path → ⎋ assert returncode==0, RC=0 in stderr
## @complexity O(1)
def test_poll_until_healthy_custom_interval(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: custom interval parameter must not break polling
    # · Scenario: poll_until_healthy "svc" "true" 2 0.1 → succeeds with custom interval
    # · Last fail: Never
    # · Remove if: poll_until_healthy signature changes (interval parameter removed)
    # · NOTE: eval "exit 0" exits the entire shell — use "true" instead of "exit 0"
    result = _run_bash(
        tmp_path,
        """
__LOG_PREFIX="healthcheck"
poll_until_healthy "svc" "true" 2 0.1
rc=$?
echo "RC=$rc" >&2
exit $rc
""",
    )
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
    assert "RC=0" in result.stderr, f"Expected RC=0 in stderr\ngot: {result.stderr}"
    # The IMP:8 log should show the custom interval value
    assert "[IMP:8][healthcheck][poll_until_healthy]" in result.stderr, (
        f"Expected [IMP:8] polling log in stderr\ngot: {result.stderr}"
    )


# endregion FUNC_test_poll_until_healthy_custom_interval


# ═══════════════════════════════════════════════════════════════════
# CHECK_DOCKER_HEALTH TESTS
# ═══════════════════════════════════════════════════════════════════


# region FUNC__run_check_docker_health
## @purpose  Helper to create a mock docker script and run check_docker_health.
##           Sets up tmp_path/mock-bin/docker that echoes the given status and
##           optionally returns a custom exit code (for the not-found scenario).
## @io       ⇥ tmp_path: Path, status: str, docker_exit_code: int
##           ⎋ CompletedProcess(stdout, stderr, returncode)
## @complexity O(1)
def _run_check_docker_health(
    tmp_path: Path,
    status: str,
    docker_exit_code: int = 0,
) -> subprocess.CompletedProcess:
    """Create mock docker and run check_docker_health with PATH override.

    ## @purpose  Isolate check_docker_health test setup: creates a mock docker
    ##            script in tmp_path/mock-bin/, sets PATH to find it first,
    ##            and runs check_docker_health via _run_bash.
    ## @io       ⇥ tmp_path: Path — pytest fixture for temp dirs
    ##             status: str — what the mock docker echoes (healthy/unhealthy/etc)
    ##             docker_exit_code: int — exit code of the mock docker
    ##           ⎋ CompletedProcess(stdout, stderr, returncode)
    ## @complexity O(1)
    """
    mock_dir = tmp_path / "mock-bin"
    mock_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = mock_dir / "docker"
    mock_docker.write_text(f"#!/usr/bin/env bash\necho '{status}'\nexit {docker_exit_code}\n")
    mock_docker.chmod(0o755)

    code = f'export PATH="{mock_dir}:$PATH"\ncheck_docker_health "test-container"\nrc=$?\necho "RC=$rc" >&2\nexit $rc\n'
    return _run_bash(tmp_path, code)


# endregion FUNC__run_check_docker_health


@pytest.mark.parametrize(
    "status,docker_exit_code,expected_rc,imp_level,expected_imp_line",
    [
        ("healthy", 0, 0, 7, "[IMP:7][healthcheck][check_docker_health]"),
        ("unhealthy", 0, 1, 8, "[IMP:8][healthcheck][check_docker_health]"),
        ("starting", 0, 2, 8, "[IMP:8][healthcheck][check_docker_health]"),
        ("error", 1, 3, 8, "[IMP:8][healthcheck][check_docker_health]"),
    ],
)
def test_check_docker_health(status, docker_exit_code, expected_rc, imp_level, expected_imp_line, tmp_path, caplog):
    """Parametrized Docker health test: healthy/unhealthy/starting/not-found."""
    result = _run_check_docker_health(tmp_path, status, docker_exit_code)
    assert result.returncode == expected_rc, (
        f"Expected exit {expected_rc} ({status}), got {result.returncode}\nstderr: {result.stderr}"
    )
    assert f"RC={expected_rc}" in result.stderr, f"Expected RC={expected_rc} in stderr\ngot: {result.stderr}"
    assert expected_imp_line in result.stderr, f"Expected {expected_imp_line} in stderr\ngot: {result.stderr}"


# ═══════════════════════════════════════════════════════════════════
# CHECK_HTTP TESTS
# ═══════════════════════════════════════════════════════════════════


# region FUNC__run_check_http
## @purpose  Helper to create a mock curl script and run check_http.
##           Sets up tmp_path/mock-bin/curl that echoes the given HTTP code.
## @io       ⇥ tmp_path: Path, http_code: str, expected_codes: str|None
##           ⎋ CompletedProcess(stdout, stderr, returncode)
## @complexity O(1)
def _run_check_http(
    tmp_path: Path,
    http_code: str,
    expected_codes: str | None = None,
) -> subprocess.CompletedProcess:
    """Create mock curl and run check_http with PATH override.

    ## @purpose  Isolate check_http test setup: creates a mock curl script in
    ##            tmp_path/mock-bin/, sets PATH to find it first, and runs
    ##            check_http via _run_bash with optional expected_codes.
    ## @io       ⇥ tmp_path: Path — pytest fixture for temp dirs
    ##             http_code: str — HTTP status code the mock curl echoes
    ##             expected_codes: str|None — comma-separated expected codes
    ##           ⎋ CompletedProcess(stdout, stderr, returncode)
    ## @complexity O(1)
    """
    mock_dir = tmp_path / "mock-bin"
    mock_dir.mkdir(parents=True, exist_ok=True)
    mock_curl = mock_dir / "curl"
    mock_curl.write_text(f"#!/usr/bin/env bash\necho '{http_code}'\n")
    mock_curl.chmod(0o755)

    if expected_codes is not None:
        code = (
            f'export PATH="{mock_dir}:$PATH"\n'
            f'check_http "http://example.com/health" "{expected_codes}"\n'
            "rc=$?\n"
            'echo "RC=$rc" >&2\n'
            "exit $rc\n"
        )
    else:
        code = (
            f'export PATH="{mock_dir}:$PATH"\n'
            'check_http "http://example.com/health"\n'
            "rc=$?\n"
            'echo "RC=$rc" >&2\n'
            "exit $rc\n"
        )
    return _run_bash(tmp_path, code)


# endregion FUNC__run_check_http


@pytest.mark.parametrize(
    "http_code,expected_codes,expected_rc,imp_level,expected_imp_line",
    [
        ("200", None, 0, 7, "[IMP:7][healthcheck][check_http]"),
        ("404", "200", 1, 8, "[IMP:8][healthcheck][check_http]"),
        ("301", "200,301,302", 0, 7, "[IMP:7][healthcheck][check_http]"),
    ],
)
def test_check_http(http_code, expected_codes, expected_rc, imp_level, expected_imp_line, tmp_path):
    """Parametrized HTTP health test: success/wrong-code/custom-codes."""
    result = _run_check_http(tmp_path, http_code, expected_codes)
    assert result.returncode == expected_rc, (
        f"Expected exit {expected_rc}, got {result.returncode}\nstderr: {result.stderr}"
    )
    assert f"RC={expected_rc}" in result.stderr, f"Expected RC={expected_rc} in stderr\ngot: {result.stderr}"
    assert expected_imp_line in result.stderr, f"Expected {expected_imp_line} in stderr\ngot: {result.stderr}"


# ═══════════════════════════════════════════════════════════════════
# POLL_DOCKER_HEALTH TESTS
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_poll_docker_health_defined
## @purpose  Verify poll_docker_health() function exists in healthcheck.sh.
##           Poll_docker_health is a convenience wrapper around poll_until_healthy
##           that specifically checks Docker container health.
## @io       ⇥ (read healthcheck.sh from LIB_DIR) → ⎋ assert function presence
## @complexity O(1) — single file read + string check
def test_poll_docker_health_defined() -> None:
    # 🧪 TRAP[TEST] · Regression: poll_docker_health function must exist in healthcheck.sh
    # · Scenario: poll_docker_health() function definition check
    # · Last fail: Never
    # · Remove if: poll_docker_health is removed from healthcheck.sh
    healthcheck_path = _LIB_DIR / "healthcheck.sh"
    content = healthcheck_path.read_text()

    assert "poll_docker_health()" in content, "[IMP:9] FAIL: poll_docker_health() not found in healthcheck.sh"
    assert "check_docker_health" in content, "[IMP:9] FAIL: check_docker_health not found in healthcheck.sh"
    # Verify poll_docker_health wraps poll_until_healthy
    assert "poll_until_healthy" in content, "[IMP:9] FAIL: poll_until_healthy not found in healthcheck.sh"


# endregion FUNC_test_poll_docker_health_defined


# region FUNC_test_poll_docker_health_success
## @purpose  Verify poll_docker_health returns 0 when Docker container is healthy.
##           Uses a mock docker script that echoes "healthy" and mock date/printf
##           to control EPOCHSECONDS-like timing.
## @io       ⇥ tmp_path → ⎋ assert returncode==0, stderr contains IMP:9 healthy log
## @complexity O(1) — mock docker returns immediately, short timeout=2 interval=0.1
## @invariants
##   - Mock docker writes stderr: "healthy" and exits 0
##   - poll_docker_health checks check_docker_health which calls docker inspect
##   - Small timeout (2s) and interval (0.1) keep test runtime under 1s
def test_poll_docker_health_success(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: poll_docker_health must succeed when docker is healthy
    # · Scenario: poll_docker_health "test-container" 2 0.1 → mock docker healthy
    # · Last fail: Never
    # · Remove if: poll_docker_health signature changes

    # Create mock docker that returns healthy
    mock_dir = tmp_path / "mock-bin"
    mock_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = mock_dir / "docker"
    mock_docker.write_text(
        "#!/usr/bin/env bash\n"
        # Simulate docker inspect output for a healthy container
        # docker inspect --format='{{.State.Health.Status}}' container → "healthy"
        'echo "healthy"\n'
        "exit 0\n"
    )
    mock_docker.chmod(0o755)

    code = (
        f'export PATH="{mock_dir}:$PATH"\n'
        'poll_docker_health "test-container" 2 0.1\n'
        "rc=$?\n"
        'echo "RC=$rc" >&2\n'
        "exit $rc\n"
    )
    result = _run_bash(tmp_path, code, env={"__LOG_PREFIX": "healthcheck"})

    assert result.returncode == 0, f"Expected exit 0 (healthy), got {result.returncode}\nstderr: {result.stderr}"
    assert "RC=0" in result.stderr, f"Expected RC=0 in stderr\ngot: {result.stderr}"
    # IMP:9 logged by poll_until_healthy when check passes
    assert "[IMP:9][healthcheck][poll_until_healthy]" in result.stderr, (
        f"Expected [IMP:9] healthy log in stderr\ngot: {result.stderr}"
    )
    # IMP:8 logged by poll_docker_health wrapper
    assert "[IMP:8][healthcheck][poll_docker_health]" in result.stderr, (
        f"Expected [IMP:8] poll log in stderr\ngot: {result.stderr}"
    )


# endregion FUNC_test_poll_docker_health_success
