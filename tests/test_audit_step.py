"""
# GREP_SUMMARY: test_audit_step, pytest, audit_step, wrapper, START, DONE, FAIL, exit-code-propagation, LDD, jsonl
# STRUCTURE: ⚡ tmp_path -> write bash wrapper -> subprocess.run -> parse audit.jsonl -> caplog LDD trajectory -> assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for audit_step() wrapper in core/lib/audit.sh (thin facade over shared/audit_logger)
## @scope    Covers: success, failure, exit-code propagation, JSON-lines format
## @invariants
##   - Uses tmp_path + AUDIT_LOG_FILE override to avoid touching /var/log/platform/audit.jsonl
##   - Overrides `logger` with no-op to avoid syslog noise in tests
##   - Each test creates a bash wrapper that sources the real audit.sh
##   - LDD trajectory printed before assertions (Anti-Loop protocol)
## @changes  2026-07-31 | Adapted to audit.sh contract (debt N-3): source path → audit.sh,
##            формат лога pipe-delimited → JSON-lines, exit-код "exit=N" → "rc=N" в msg,
##            truncation >200 удалён (новый контракт JSON не обрезает msg — см. TRAP в audit.sh/audit_logger.py)
## @rationale C-5 fix (debt 096) создал audit.sh; тест оставался stale на удалённый legacy shell audit — 5 failures
# endregion MODULE_CONTRACT
"""

import json
import os
import subprocess

# ── Resolve project root ──
# test file is at <project>/tests/test_audit_step.py
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_TEST_DIR)
_AUDIT_SH = os.path.join(_PROJECT_ROOT, "core", "lib", "audit.sh")


# ── Helper: create bash wrapper and run audit_step ──


def _run_audit_step(tmp_path, step_name, command):
    """Run audit_step via bash wrapper and return (exit_code, audit_log_entries)."""
    audit_log_path = tmp_path / "audit.jsonl"
    wrapper_path = tmp_path / "run_audit_step.sh"

    # Build the bash wrapper script
    # Use .format() to avoid f-string/triple-quote nesting issues with $ and {}
    script_template = """\
#!/usr/bin/env bash
set -uo pipefail

# Override: logger is a no-op (avoid syslog noise)
logger() {{ return 0; }}

# audit.sh экспортирует PYTHONPATH и включает set -e при source — выключаем после source.
# audit_step САМ включает set -e перед return $rc: прямой вызов как простой команды
# убил бы оболочку при rc≠0 → перехват через OR-идиому (set -e не срабатывает на ||).
source "{audit_src}"
set +e

# Override log path AFTER source (audit_log читает AUDIT_LOG_FILE в момент вызова)
export AUDIT_LOG_FILE="{audit_log}"

# Run audit_step and capture exit code (OR-идиома: левая часть || не триггерит set -e)
_AUDIT_STEP_RC=0
audit_step "{step}" {cmd} || _AUDIT_STEP_RC=$?
echo "AUDIT_STEP_EXIT_CODE=$_AUDIT_STEP_RC"
"""
    script = script_template.format(
        audit_log=str(audit_log_path),
        audit_src=_AUDIT_SH,
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

    # Read audit log (JSON-lines)
    if audit_log_path.exists():
        log_lines = audit_log_path.read_text().splitlines()
        entries = []
        for line in log_lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    else:
        entries = []

    return exit_code, entries, result


def _log_contains(entries, expected_status):
    """Return True if any audit entry has the given status field."""
    return any(entry.get("status") == expected_status for entry in entries)


def _assert_log_count(entries, expected_count):
    """Assert exact number of audit log entries."""
    actual = len(entries)
    assert actual == expected_count, "Expected {} log entries, got {}:\n{}".format(
        expected_count, actual, "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    )


# ── Tests ──


def test_audit_step_success(tmp_path, caplog):
    """AC18: Successful command -> 2 entries (START + DONE, no FAIL), returns 0."""
    caplog.set_level(0)

    rc, entries, result = _run_audit_step(tmp_path, "test:success", "true")

    # LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert rc == 0, f"Expected exit code 0, got {rc} — stderr: {result.stderr}"
    _assert_log_count(entries, 2)
    assert _log_contains(entries, "START"), "Missing START entry"
    assert _log_contains(entries, "DONE"), "Missing DONE entry"
    assert not _log_contains(entries, "FAIL"), "FAIL entry present despite success"

    print("  Audit log entries:")
    for entry in entries:
        print(f"    {json.dumps(entry, ensure_ascii=False)}")


def test_audit_step_failure(tmp_path, caplog):
    """AC18: Failing command (exit 1) -> 2 entries (START + FAIL), returns 1."""
    caplog.set_level(0)

    rc, entries, result = _run_audit_step(tmp_path, "test:failed", "false")

    # LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert rc == 1, f"Expected exit code 1, got {rc} — stderr: {result.stderr}"
    _assert_log_count(entries, 2)
    assert _log_contains(entries, "START"), "Missing START entry"
    assert _log_contains(entries, "FAIL"), "Missing FAIL entry"
    assert not _log_contains(entries, "DONE"), "DONE entry present despite failure"

    print("  Audit log entries:")
    for entry in entries:
        print(f"    {json.dumps(entry, ensure_ascii=False)}")


def test_audit_step_exit_124(tmp_path, caplog):
    """AC18: Command with exit 124 (timeout) -> START + FAIL with rc=124."""
    caplog.set_level(0)

    rc, entries, result = _run_audit_step(tmp_path, "test:timeout", "bash -c 'exit 124'")

    # LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert rc == 124, f"Expected exit code 124, got {rc} — stderr: {result.stderr}"
    _assert_log_count(entries, 2)
    assert _log_contains(entries, "START"), "Missing START entry"

    # Verify FAIL entry contains rc=124 in msg
    fail_found = False
    for entry in entries:
        if entry.get("status") == "FAIL" and "rc=124" in entry.get("msg", ""):
            fail_found = True
            break
    assert fail_found, "FAIL entry should contain rc=124 in msg.\n  Entries:\n{}".format(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    )

    print("  Audit log entries:")
    for entry in entries:
        print(f"    {json.dumps(entry, ensure_ascii=False)}")


def test_audit_step_exit_code_propagation(tmp_path, caplog):
    """AC18: Return code matches command exit code (test with exit 42)."""
    caplog.set_level(0)

    rc, entries, result = _run_audit_step(tmp_path, "test:propagation", "bash -c 'exit 42'")

    # LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert rc == 42, f"Expected exit code 42, got {rc} — stderr: {result.stderr}"
    _assert_log_count(entries, 2)

    # Verify FAIL entry contains rc=42 in msg
    fail_found = False
    for entry in entries:
        if entry.get("status") == "FAIL" and "rc=42" in entry.get("msg", ""):
            fail_found = True
            break
    assert fail_found, "FAIL entry should contain rc=42 in msg.\n  Entries:\n{}".format(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    )

    print("  Audit log entries:")
    for entry in entries:
        print(f"    {json.dumps(entry, ensure_ascii=False)}")
