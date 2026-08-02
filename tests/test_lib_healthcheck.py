# GREP_SUMMARY: test-lib-healthcheck healthcheck.sh bash check_docker_health check_http docker curl subprocess stderr IMP timeout mock PATH
# STRUCTURE: ▶ _run_bash(script → subprocess.run) → ○ check_docker_health 4 tests: ◇ healthy/unhealthy/starting/not-found → ○ check_http 3 tests: ◇ 200/404/301-multi → ⎋ 7 test functions

# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(BASH-HEALTHCHECK):2; TECH(PYTEST):2]
## @purpose  Unit tests for core/lib/healthcheck.sh — the centralised healthcheck
##           library providing check_docker_health, and check_http.
##           Tests verify return codes, mock docker/curl via PATH injection,
##           and custom timeouts — all in isolated tmp_path.
##           Волна 118 B6: poll_until_healthy/poll_docker_health/check_tcp тесты УДАЛЕНЫ
##           (функции удалены из lib) — заменены R5 negative (type -t = пусто).
## @scope    7 test functions covering:
##
##           - check_docker_health: healthy, unhealthy, starting, not-found
##           - check_http: 200 success, 404 wrong code, 301 multi-code expected
##           - test_poll_until_healthy_removed (R5): poll функции удалены
## @invariants
##
##   - Every test uses tmp_path for script isolation (Zero Hardcode Rule)
##   - LIB_DIR resolved via Path(__file__).resolve() — no hardcoded paths
##   - Mock docker and curl scripts created in tmp_path/mock-bin/ with PATH override
##   - All bash scripts run with subprocess.run (capture_output, text, timeout=10)
##   - No caplog fixture: bash logs go to stderr, not Python logging subsystem
##   - LDD verification via stderr assertion: [IMP:N] present for expected levels
## @rationale Q: Why subprocess.run instead of pure Python simulation?
##            A: healthcheck.sh is a pure bash library whose core logic (docker
##            inspect subprocess, curl subprocess) can only be tested in a real
##            bash environment. Mock binaries in tmp_path via PATH injection replace
##            external dependencies without needing Docker or network access.
##            Q: Why not @ldd_trajectory decorator?
##            A: @ldd_trajectory relies on caplog (Python logging capture). Bash
##            writes directly to stderr, bypassing Python logging entirely. LDD
##            verification is done by asserting stderr contains [IMP:N] for the
##            expected importance level.
## @changes LAST_CHANGE: 2026-07-07 · Initial implementation per DevPlan test spec
##           2026-08-02 · Волна 118 B6 — poll/check_tcp tests → R5 negative
## @modulemap
##   - _run_bash                          [W:30] Helper: write temp script, source both libs, run bash, return result
##   - test_check_docker_health_healthy    [W:40] mock docker returns healthy → exit 0
##   - test_check_docker_health_unhealthy  [W:40] mock docker returns unhealthy → exit 1
##   - test_check_docker_health_starting   [W:40] mock docker returns starting → exit 2
##   - test_check_docker_health_no_container [W:40] mock docker returns exit 1 → exit 3
##   - test_check_http_success             [W:40] mock curl returns 200 → exit 0
##   - test_check_http_wrong_code          [W:40] mock curl returns 404, expected=200 → exit 1
##   - test_check_http_custom_codes        [W:40] mock curl returns 301, expected="200,301,302" → exit 0
##   - test_poll_until_healthy_removed     [W:30] R5: poll_until_healthy/poll_docker_health удалены
## @usecases
##   - Developer: run pytest after modifying healthcheck.sh → all 7 tests pass, no regressions
##   - Architect: verify docker/curl mock isolation, return-code contracts
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
# POLL_UNTIL_HEALTHY TESTS (REMOVED API — волна 118 B6)
# ═══════════════════════════════════════════════════════════════════
# Волна 118 B6: poll_until_healthy УДАЛЁН из healthcheck.sh (0 callers;
# поллинг — через Python shared healthcheck_poller / docker_compose.healthcheck_poll).
# R5 negative: type -t poll_until_healthy = пусто (и poll_docker_health).


# region FUNC_test_poll_until_healthy_removed
## @purpose  Verify poll_until_healthy/poll_docker_health are REMOVED (волна 118 B6, R5).
## @io       ⇥ tmp_path → ⎋ assert rc==0, stderr 'REMOVED'
## @complexity O(1)
def test_poll_until_healthy_removed(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · B6 — poll_until_healthy/poll_docker_health удалены
    # · Scenario: source healthcheck.sh → type -t poll_until_healthy → пусто (не функция)
    # · Last fail: poll_until_healthy существовал до волны 118 B6 (healthcheck.sh L104-161)
    # · Remove if: poll_until_healthy будет восстановлен
    result = _run_bash(
        tmp_path,
        """
if [[ "$(type -t poll_until_healthy)" == "function" ]] || [[ "$(type -t poll_docker_health)" == "function" ]]; then
    echo "[IMP:10][test] FAIL: poll functions still defined" >&2
    exit 1
fi
echo "[IMP:9][test] poll_until_healthy/poll_docker_health REMOVED — OK" >&2
exit 0
""",
    )

    assert result.returncode == 0, (
        f"[IMP:9][test_poll_until_healthy_removed] FAIL: poll не удалён, stderr: {result.stderr}"
    )
    assert "REMOVED" in result.stderr, f"[IMP:9][test] FAIL: no REMOVED marker: {result.stderr}"
    __import__("logging").getLogger(__name__).info(
        "[IMP:9][test_poll_until_healthy_removed] PASS: poll functions removed (B6 R5)"
    )


# endregion FUNC_test_poll_until_healthy_removed


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
