"""
# GREP_SUMMARY: test_audit_step, pytest, audit_step, wrapper, START, DONE, FAIL, exit-code-propagation, LDD
# STRUCTURE: ⚡ tmp_path -> write bash wrapper -> subprocess.run -> read audit.log -> caplog LDD trajectory -> assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for audit_step() wrapper in core/lib/audit_logging.sh
## @scope    Covers: success, failure, timeout-124, exit-code propagation, preview truncation
## @invariants
##   - Uses tmp_path to avoid touching /var/log/platform/audit.log
##   - Overrides `logger` with no-op to avoid syslog noise in tests
##   - Each test creates a bash wrapper that sources the real audit_logging.sh
##   - LDD trajectory printed before assertions (Anti-Loop protocol)
## @rationale W2-E3 acceptance per DevPlan AC18
# endregion MODULE_CONTRACT
"""

import os
import subprocess

# ── Resolve project root ──
# test file is at <project>/tests/test_audit_step.py
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_TEST_DIR)
_AUDIT_LOGGING_SH = os.path.join(_PROJECT_ROOT, "core", "lib", "audit_logging.sh")


# ── Helper: create bash wrapper and run audit_step ──


def _run_audit_step(tmp_path, step_name, command):
    """Run audit_step via bash wrapper and return (exit_code, audit_log_lines)."""
    """Run audit_step via bash wrapper and return (exit_code, audit_log_lines)."""
    audit_log_path = tmp_path / "audit.log"
    wrapper_path = tmp_path / "run_audit_step.sh"

    # Build the bash wrapper script
    # Use .format() to avoid f-string/triple-quote nesting issues with $ and {}
    script_template = """\
#!/usr/bin/env bash
set -uo pipefail

# Override: logger is a no-op (avoid syslog noise)
logger() {{ return 0; }}

source "{audit_src}"

# Override paths AFTER source (source reassigns PLATFORM_LOG_DIR at module level)
AUDIT_LOG_DIR="{audit_dir}"
PLATFORM_LOG_DIR="$AUDIT_LOG_DIR"
PLATFORM_AUDIT_LOG="{audit_log}"
mkdir -p "$AUDIT_LOG_DIR"

# Run audit_step and capture exit code (no set -e — audit_step handles propagation)
audit_step "{step}" {cmd}
_AUDIT_STEP_RC=$?
echo "AUDIT_STEP_EXIT_CODE=$_AUDIT_STEP_RC"
"""
    script = script_template.format(
        audit_dir=str(tmp_path),
        audit_log=str(audit_log_path),
        audit_src=_AUDIT_LOGGING_SH,
        step=step_name,
        cmd=command,
    )

    wrapper_path.write_text(script)
    wrapper_path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(wrapper_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Parse exit code from wrapper output
    exit_code = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("AUDIT_STEP_EXIT_CODE="):
            exit_code = int(line.split("=", 1)[1])
            break

    # Read audit log
    if audit_log_path.exists():
        log_lines = audit_log_path.read_text().splitlines()
    else:
        log_lines = []

    return exit_code, log_lines, result


def _log_contains(log_lines, expected_status):
    """Return True if any audit log entry has the given status field."""
    for line in log_lines:
        parts = line.split(" | ")
        if len(parts) >= 3 and parts[2].strip() == expected_status:
            return True
    return False


def _assert_log_count(log_lines, expected_count):
    """Assert exact number of audit log entries."""
    actual = len(log_lines)
    assert actual == expected_count, "Expected {} log entries, got {}:\n{}".format(
        expected_count, actual, "\n".join(log_lines)
    )


# ── Tests ──


def test_audit_step_success(tmp_path, caplog):
    """AC18: Successful command -> 2 entries (START + DONE, no FAIL), returns 0."""
    caplog.set_level(0)

    rc, log_lines, result = _run_audit_step(tmp_path, "test:success", "true")

    # LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert rc == 0, f"Expected exit code 0, got {rc} — stderr: {result.stderr}"
    _assert_log_count(log_lines, 2)
    assert _log_contains(log_lines, "START"), "Missing START entry"
    assert _log_contains(log_lines, "DONE"), "Missing DONE entry"
    assert not _log_contains(log_lines, "FAIL"), "FAIL entry present despite success"

    print("  Audit log entries:")
    for line in log_lines:
        print(f"    {line}")


def test_audit_step_failure(tmp_path, caplog):
    """AC18: Failing command (exit 1) -> 2 entries (START + FAIL), returns 1."""
    caplog.set_level(0)

    rc, log_lines, result = _run_audit_step(tmp_path, "test:failed", "false")

    # LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert rc == 1, f"Expected exit code 1, got {rc} — stderr: {result.stderr}"
    _assert_log_count(log_lines, 2)
    assert _log_contains(log_lines, "START"), "Missing START entry"
    assert _log_contains(log_lines, "FAIL"), "Missing FAIL entry"
    assert not _log_contains(log_lines, "DONE"), "DONE entry present despite failure"

    print("  Audit log entries:")
    for line in log_lines:
        print(f"    {line}")


def test_audit_step_exit_124(tmp_path, caplog):
    """AC18: Command with exit 124 (timeout) -> START + FAIL with exit=124."""
    caplog.set_level(0)

    rc, log_lines, result = _run_audit_step(tmp_path, "test:timeout", "bash -c 'exit 124'")

    # LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert rc == 124, f"Expected exit code 124, got {rc} — stderr: {result.stderr}"
    _assert_log_count(log_lines, 2)
    assert _log_contains(log_lines, "START"), "Missing START entry"

    # Verify FAIL entry contains exit=124
    fail_found = False
    for line in log_lines:
        if "FAIL" in line and "exit=124" in line:
            fail_found = True
            break
    assert fail_found, "FAIL entry should contain exit=124 in message.\n  Lines:\n{}".format(
        "\n".join("    " + ln for ln in log_lines)
    )

    print("  Audit log entries:")
    for line in log_lines:
        print(f"    {line}")


def test_audit_step_exit_code_propagation(tmp_path, caplog):
    """AC18: Return code matches command exit code (test with exit 42)."""
    caplog.set_level(0)

    rc, log_lines, result = _run_audit_step(tmp_path, "test:propagation", "bash -c 'exit 42'")

    # LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert rc == 42, f"Expected exit code 42, got {rc} — stderr: {result.stderr}"
    _assert_log_count(log_lines, 2)

    # Verify FAIL entry contains exit=42
    fail_found = False
    for line in log_lines:
        if "FAIL" in line and "exit=42" in line:
            fail_found = True
            break
    assert fail_found, "FAIL entry should contain exit=42 in message.\n  Lines:\n{}".format(
        "\n".join("    " + ln for ln in log_lines)
    )

    print("  Audit log entries:")
    for line in log_lines:
        print(f"    {line}")


def test_audit_step_preview_truncation(tmp_path, caplog):
    """AC18: Long command >200 chars -> preview truncated in log entry."""
    caplog.set_level(0)

    # Build a command with a 250-char argument (total length > 200)
    long_arg = "x" * 250
    command = f"echo {long_arg}"

    rc, log_lines, result = _run_audit_step(tmp_path, "test:truncation", command)

    # LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert rc == 0, f"Expected exit code 0, got {rc} — stderr: {result.stderr}"
    _assert_log_count(log_lines, 2)

    # Verify each log entry's message part is <= 200 chars
    for line in log_lines:
        # The message is the part after the third " | " separator
        fields = line.split(" | ")
        msg = " | ".join(fields[3:]) if len(fields) > 3 else ""
        assert len(msg) <= 200, f"Log entry message exceeds 200 chars ({len(msg)}):\n  {line}"

    print("  Audit log entries:")
    for line in log_lines:
        print(f"    {line}")
    print("  All messages are <= 200 chars — truncation verified")
