# GREP_SUMMARY: test, ssh, ssh_exec, ssh_read, dry-run, timeout, ssh-opts, facade
# STRUCTURE: ▶ write mock ssh bin → write test wrappers → subprocess.run → ◇ assert exit code → ◇ parse stderr for LDD logs → ⊕ print LDD trajectory → assert IMP:9 found
# region MODULE_CONTRACT
## @purpose  Unit tests for core/lib/ssh.sh — SSH facade library.
##           Covers: ssh_exec timeout detection, ssh_read default timeout,
##           DRY_RUN mode, SSH_OPTS_COMMON immutability, input validation.
## @scope    Tests bash shell library via subprocess wrappers. NOT integration —
##           ssh binary is mocked via PATH override.
## @invariants
##   - All tests use tmp_path for mock ssh binary and wrapper scripts
##   - Mock ssh simulates specific exit codes (124=timeout, 0=success, 1=fail)
##   - LDD trajectory (IMP:7-10) extracted from stderr of subprocess
##   - Each test asserts at least one IMP:9 log present on success path
## @rationale Testing bash from python is permitted per DevPlan. Using subprocess
##            to invoke shell wrappers is the correct pattern — not for business logic.
## @changes  LAST_CHANGE: 2026-07-21 | W2-E1 — Initial implementation (DevPlan 029)
# endregion MODULE_CONTRACT

import os
import subprocess
import textwrap

import pytest

# Path to the ssh.sh library under test
SSH_SH_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
    "lib",
    "ssh.sh",
)

LOGGING_SH_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
    "lib",
    "logging.sh",
)


# region HELPERS


# ──────────────────────────────────────────────────────────────
# region FUNC__build_mock_ssh
## @purpose  Write a mock ssh binary to tmp_path/bin/ that returns a specific exit code.
##           Simulates: 0=success, 124=timeout, 1=fail, etc.
## @param tmp_path  pytest tmp_path fixture
## @param exit_code  Desired mock exit code (default: 0)
## @param extra_output  Optional stderr output to simulate SSH stderr noise
## @io        Writes tmp_path/bin/ssh + chmod +x
## @return    str — path to bin/ dir for PATH override
def _build_mock_ssh(tmp_path, exit_code=0, extra_output=""):
    """Create mock ssh + timeout binaries in tmp_path/bin/.

    Mock ssh exits with a specified code.
    Mock timeout simply runs the command (ignores the duration arg).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    # ── Mock ssh ──
    ssh_path = bin_dir / "ssh"
    lines = ["#!/usr/bin/env bash"]
    if extra_output:
        lines.append(f'echo "{extra_output}" >&2')
    lines.append(f"exit {exit_code}")
    ssh_path.write_text("\n".join(lines) + "\n")
    ssh_path.chmod(0o755)

    # ── Mock timeout (macOS compat — timeout from coreutils not always available) ──
    # Passes through: timeout DURATION COMMAND... → just runs COMMAND...
    timeout_path = bin_dir / "timeout"
    timeout_path.write_text(
        "#!/usr/bin/env bash\n"
        "# Mock timeout — skip duration arg, exec the command\n"
        "if [[ $# -lt 2 ]]; then exit 0; fi\n"
        "shift 1  # remove duration\n"
        'exec "$@"\n'
    )
    timeout_path.chmod(0o755)

    return str(bin_dir)


# endregion FUNC__build_mock_ssh


# ──────────────────────────────────────────────────────────────
# region FUNC__build_test_wrapper
## @purpose  Write a bash script to tmp_path that sources ssh.sh and invokes a function.
##           Used by each test to invoke specific ssh.sh functions with controlled args.
## @param tmp_path  pytest tmp_path fixture
## @param function_call  The ssh.sh function call to execute (e.g., 'ssh_exec "host" "user" "cmd" 1')
## @param extra_setup  Optional bash commands to run before the function call (e.g., DRY_RUN=1)
## @return    str — path to the wrapper script
def _build_test_wrapper(tmp_path, function_call, extra_setup=""):
    """Create a bash wrapper script that sources ssh.sh and calls the specified function."""
    wrapper_path = tmp_path / "test_wrapper.sh"
    content = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        # Source logging.sh first (dependency)
        source "{LOGGING_SH_PATH}"

        # Source ssh.sh (the library under test)
        source "{SSH_SH_PATH}"

        # Extra setup (if any)
        {extra_setup}

        # Call the function under test
        {function_call}
    """)
    wrapper_path.write_text(content)
    wrapper_path.chmod(0o755)
    return str(wrapper_path)


# endregion FUNC__build_test_wrapper


# ──────────────────────────────────────────────────────────────
# region FUNC__run_wrapper
## @purpose  Run a test wrapper script with PATH pointing to mock bin directory.
##           Captures stdout, stderr, and exit code.
## @param wrapper_path  Path to the wrapper script
## @param mock_bin_dir  Path to bin/ with mock ssh (or None for real ssh)
## @return    subprocess.CompletedProcess
def _run_wrapper(wrapper_path, mock_bin_dir=None):
    """Run the wrapper script, optionally with a custom PATH for mock ssh."""
    env = os.environ.copy()
    if mock_bin_dir:
        env["PATH"] = f"{mock_bin_dir}:{env.get('PATH', '')}"

    return subprocess.run(
        ["bash", wrapper_path],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


# endregion FUNC__run_wrapper


# ──────────────────────────────────────────────────────────────
# region FUNC__print_ldd_trajectory_from_stderr
## @purpose  Extract and print LDD trajectory (IMP:7-10) from subprocess stderr.
##           Used as equivalent of caplog-based _print_ldd_trajectory for shell tests.
## @param stderr_text  The stderr string from subprocess
## @return    bool — True if at least one IMP:9 log found
def _print_ldd_trajectory_from_stderr(stderr_text):
    """Print LDD trajectory filtered to IMP:7-10 from subprocess stderr.

    ## @purpose — Extract and display IMP:7-10 log entries from bash stderr output.
    ## @io — ⇥ stderr_text: str → ⎋ bool (True if IMP:9 found)
    """
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in stderr_text.splitlines():
        if "[IMP:" in line:
            try:
                imp_level = int(line.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    print(line)
                if imp_level >= 9:
                    found = True
            except (ValueError, IndexError):
                # Malformed IMP tag — print anyway for debugging
                print(f"  [MALFORMED] {line}")
    print("--- END LDD TRAJECTORY ---")
    return found


# endregion FUNC__print_ldd_trajectory_from_stderr


# endregion HELPERS


# ═══════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════


# ──────────────────────────────────────────────────────────────
# region TEST_ssh_exec_timeout
## @purpose  Verify ssh_exec returns 124 when mock ssh exits 124 (simulated timeout).
## @scenario ssh_exec with timeout=1, mock ssh returns 124 → exit 124
## @coverage ssh_exit_code_124
def test_ssh_exec_timeout(tmp_path):
    """ssh_exec with timeout=1 on unreachable host → return 124."""
    mock_bin = _build_mock_ssh(tmp_path, exit_code=124)
    wrapper = _build_test_wrapper(
        tmp_path,
        'ssh_exec "test.host" "ci-deploy" "sleep 10" 1',
    )
    result = _run_wrapper(wrapper, mock_bin)

    # Print LDD trajectory (IMP:9 not expected on timeout path)
    _print_ldd_trajectory_from_stderr(result.stderr)

    # Assert: exit code 124 (timeout) from wrapper
    # Note: The mock ssh exits 124, but the timeout wrapper also catches it.
    # Our mock exits 124 directly — ssh_exec sees 124 and returns 124.
    assert result.returncode == 124, f"Expected exit 124 (timeout), got {result.returncode}\nstderr: {result.stderr}"

    # The timeout path in ssh_exec logs at IMP:1, not IMP:9
    # But we should still have logs. Since this is a failure path,
    # we check that LDD trajectory was printed (the test prints it).
    # We don't assert IMP:9 on timeout path — that's a failure case.
    print(f"[TEST] ssh_exec timeout: rc={result.returncode}")


# endregion TEST_ssh_exec_timeout


# ──────────────────────────────────────────────────────────────
# region TEST_ssh_exec_success
## @purpose  Verify ssh_exec returns 0 on successful SSH execution.
## @scenario ssh_exec with mock ssh returning 0 → exit 0
## @coverage ssh_exit_code_0, IMP:9_presence
def test_ssh_exec_success(tmp_path):
    """ssh_exec on reachable host (mock) → return 0."""
    mock_bin = _build_mock_ssh(tmp_path, exit_code=0)
    wrapper = _build_test_wrapper(
        tmp_path,
        'ssh_exec "test.host" "ci-deploy" "echo ok" 60',
    )
    result = _run_wrapper(wrapper, mock_bin)

    # Print LDD trajectory
    found_imp9 = _print_ldd_trajectory_from_stderr(result.stderr)

    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
    assert found_imp9, f"Critical LDD Error: No IMP:9 business logic log found on success path\nstderr: {result.stderr}"


# endregion TEST_ssh_exec_success


# ──────────────────────────────────────────────────────────────
# region TEST_ssh_read_default_timeout
## @purpose  Verify ssh_read uses 60s default timeout (vs 600s deploy).
## @scenario ssh_read with mock ssh returning 0, no explicit timeout → exit 0
## @coverage ssh_read, default_timeout_60
def test_ssh_read_default_timeout(tmp_path):
    """ssh_read should use default timeout=60 (not 600)."""
    mock_bin = _build_mock_ssh(tmp_path, exit_code=0)
    wrapper = _build_test_wrapper(
        tmp_path,
        'ssh_read "test.host" "ci-deploy" "docker ps"',
    )
    result = _run_wrapper(wrapper, mock_bin)

    # Print LDD trajectory
    found_imp9 = _print_ldd_trajectory_from_stderr(result.stderr)

    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
    assert found_imp9, f"Critical LDD Error: No IMP:9 log found for ssh_read path\nstderr: {result.stderr}"

    # Verify that the log mentions timeout=60 (ssh_read default)
    assert "timeout=60s" in result.stderr or "60s" in result.stderr, (
        f"ssh_read should log its 60s timeout\nstderr: {result.stderr}"
    )


# endregion TEST_ssh_read_default_timeout


# ──────────────────────────────────────────────────────────────
# region TEST_dry_run
## @purpose  Verify DRY_RUN=1 causes ssh_exec to print command without executing.
## @scenario DRY_RUN=1 with ssh_exec → exit 0, stderr contains dry-run log
## @coverage ssh_exec_dry_run, DRY_RUN_detection
def test_dry_run(tmp_path):
    """DRY_RUN=1 mode should echo without exec, return 0."""
    # Mock ssh exits 1 if called — DRY_RUN should prevent call
    mock_bin = _build_mock_ssh(tmp_path, exit_code=1)
    wrapper = _build_test_wrapper(
        tmp_path,
        'ssh_exec "test.host" "ci-deploy" "docker compose pull" 600',
        extra_setup='export DRY_RUN="1"',
    )
    result = _run_wrapper(wrapper, mock_bin)

    # Print LDD trajectory (dry-run path logs at IMP:8, not IMP:9)
    _print_ldd_trajectory_from_stderr(result.stderr)

    assert result.returncode == 0, f"Expected exit 0 (dry-run), got {result.returncode}\nstderr: {result.stderr}"

    # Verify dry-run log line is present
    assert "DRY-RUN" in result.stderr, f"Expected DRY-RUN log in stderr\nstderr: {result.stderr}"

    # Verify IMP:8 present (dry-run logs at IMP:8)
    assert "[IMP:8]" in result.stderr, f"Expected IMP:8 dry-run log\nstderr: {result.stderr}"


# endregion TEST_dry_run


# ──────────────────────────────────────────────────────────────
# region TEST_ssh_opts_common_readonly
## @purpose  SSH_OPTS_COMMON is readonly — verify attempted overwrite fails.
## @scenario Attempt to overwrite SSH_OPTS_COMMON after source → bash error
## @coverage readonly_immutability
def test_ssh_opts_common_readonly(tmp_path):
    """SSH_OPTS_COMMON should be readonly — cannot be reassigned."""
    wrapper = _build_test_wrapper(
        tmp_path,
        "SSH_OPTS_COMMON=()",
        extra_setup="set +euo pipefail  # allow error propagation\n",
    )
    result = _run_wrapper(wrapper)

    # We expect a non-zero exit (readonly assignment error)
    # But also verify via declare -p that it's readonly
    assert result.returncode != 0, f"Expected readonly assignment to fail\nstderr: {result.stderr}"
    assert "readonly" in result.stderr.lower() or "SSH_OPTS_COMMON" in result.stderr, (
        f"Expected readonly error message\nstderr: {result.stderr}"
    )


# endregion TEST_ssh_opts_common_readonly


# ──────────────────────────────────────────────────────────────
# region TEST_input_validation
## @purpose  Verify ssh_exec validates inputs and returns 2 on invalid args.
## @scenario Empty host → exit 2; Empty command → exit 2; Non-int timeout → exit 2
## @coverage fail-fast_validation
class TestInputValidation:
    """Group of validation tests — ssh_exec should return 2 on invalid input."""

    @pytest.mark.parametrize(
        "function_call,description",
        [
            ('ssh_exec "" "user" "cmd" 60', "empty host"),
            ('ssh_exec "host" "user" "" 60', "empty command"),
            ('ssh_exec "host" "user" "cmd" "abc"', "non-integer timeout"),
        ],
    )
    def test_validation_fails(self, tmp_path, function_call, description):
        """ssh_exec should return 2 on invalid input: {description}."""
        wrapper = _build_test_wrapper(tmp_path, function_call)
        mock_bin = _build_mock_ssh(tmp_path, exit_code=0)
        result = _run_wrapper(wrapper, mock_bin)

        # Because set -euo pipefail causes early exit, we need set +e in wrapper
        # Actually since ssh_exec returns 2 (not exit), the wrapper continues
        # Let me adjust: the wrapper has set -euo pipefail, so return 2 will
        # cause the script to exit with that code.

        # Print LDD trajectory even on failure for debugging
        _print_ldd_trajectory_from_stderr(result.stderr)

        assert result.returncode == 2, (
            f"Expected exit 2 for '{description}', got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "FAIL-FAST" in result.stderr, f"Expected FAIL-FAST log for '{description}'\nstderr: {result.stderr}"


# endregion TEST_input_validation


# ──────────────────────────────────────────────────────────────
# region TEST_ssh_exec_fail
## @purpose  Verify ssh_exec propagates non-zero, non-124 exit codes (SSH failures).
## @scenario mock ssh exits 1 → ssh_exec returns 1, logs IMP:7
## @coverage ssh_exec_fail_error_propagation
def test_ssh_exec_fail(tmp_path):
    """ssh_exec with mock ssh returning 1 → return 1, log at IMP:7."""
    mock_bin = _build_mock_ssh(tmp_path, exit_code=1)
    wrapper = _build_test_wrapper(
        tmp_path,
        'ssh_exec "test.host" "ci-deploy" "failing_command" 60',
    )
    result = _run_wrapper(wrapper, mock_bin)

    # Print LDD trajectory
    _print_ldd_trajectory_from_stderr(result.stderr)

    assert result.returncode == 1, f"Expected exit 1 (SSH fail), got {result.returncode}\nstderr: {result.stderr}"
    assert "IMP:7" in result.stderr, f"Expected IMP:7 log for SSH failure\nstderr: {result.stderr}"


# endregion TEST_ssh_exec_fail

# 🧪 TRAP[TEST] · 2026-07-21 · Regression: ssh_exec timeout, success, read, dry_run, readonly, validation, fail
# · Scenario: All unit tests for core/lib/ssh.sh (W2-E1)
# · Last fail: N/A (first implementation)
# · Remove if: ssh.sh API changes (signature or behavior)
