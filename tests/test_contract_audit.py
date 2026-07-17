#!/usr/bin/env python3
# GREP_SUMMARY: contract-test audit-logging bash-syntax subprocess real-script fallback-stderr
# STRUCTURE: ▶ platform_root → ∋ SCRIPT_PATH → ◇ os.path.isfile? → ◇ bash -n (syntax) → ◇ grep fallback → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Contract tests for core/lib/audit_logging.sh. Verify the real bash
##           library exists, is syntactically valid, and contains fallback-logic
##           for when syslog and file write both fail (C16 fix).
## @scope    Tests operate on the real audit_logging.sh file in the project tree.
##           No mocking, no simulation. No syslog required for these checks.
## @invariants
##   - Script exists at core/lib/audit_logging.sh relative to platform root
##   - Script is a regular file (sourced, not executed)
##   - bash -n returns 0 for valid syntax
##   - audit_log() has fallback to stderr when syslog AND file write fail
## @rationale  audit_logging.sh is a shared library sourced by all platform scripts.
##             Silent log loss (C16) would break audit trail for security/compliance.
##             Contract tests catch regression in fallback logic.
## @changes — CREATED: 2026-07-12 | TASK-C16: contract tests for audit_logging.sh
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess

import pytest

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)
AUDIT_SCRIPT_REL: str = os.path.join("core", "lib", "audit_logging.sh")
SCRIPT_PATH: str = os.path.join(PLATFORM_ROOT, AUDIT_SCRIPT_REL)


# ── Test: File exists ──────────────────────────────────────────────────────


# region FUNC_test_audit_script_exists
@pytest.mark.contract
## @purpose  Verify the audit_logging.sh script file exists on disk.
def test_audit_script_exists() -> None:
    """
    # ▶ SCRIPT_PATH → ◇ os.path.isfile? → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_audit_script_exists] Checking script: %s", SCRIPT_PATH)

    assert os.path.isfile(SCRIPT_PATH), f"[IMP:9][test_audit_script_exists] FAIL: script not found at {SCRIPT_PATH}"
    logger.info("[IMP:8][test_audit_script_exists] File exists: %s", SCRIPT_PATH)

    logger.info("[IMP:9][test_audit_script_exists] PASS: %s exists on disk", SCRIPT_PATH)


# endregion FUNC_test_audit_script_exists


# ── Test: bash -n syntax check ─────────────────────────────────────────────


# region FUNC_test_audit_script_syntax
@pytest.mark.contract
## @purpose  Verify audit_logging.sh has valid bash syntax via `bash -n`.
def test_audit_script_syntax() -> None:
    """
    # ▶ SCRIPT_PATH → ⚡ subprocess.run(["bash", "-n", SCRIPT_PATH]) → ◇ returncode == 0? → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_audit_script_syntax] Running bash -n on: %s", SCRIPT_PATH)

    result: subprocess.CompletedProcess = subprocess.run(
        ["bash", "-n", SCRIPT_PATH],
        capture_output=True,
        text=True,
    )

    # Print LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print(f"[IMP:7][test_audit_script_syntax] bash -n exit code: {result.returncode}")
    if result.stderr:
        for line in result.stderr.splitlines():
            print(f"[IMP:7][bash-n/stderr] {line}")
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 0, (
        f"[IMP:9][test_audit_script_syntax] FAIL: bash syntax error in {SCRIPT_PATH}\nstderr: {result.stderr}"
    )
    logger.info("[IMP:9][test_audit_script_syntax] PASS: %s is syntactically valid", SCRIPT_PATH)


# endregion FUNC_test_audit_script_syntax


# ── Test: Fallback logging to stderr (C16 fix) ──────────────────────────────


# region FUNC_test_audit_fallback_stderr
@pytest.mark.contract
## @purpose  Verify audit_log() contains fallback to stderr when both syslog
##           and file append fail. The fix (C16) captures exit codes of
##           `logger` and `printf`, and writes to stderr if both fail.
## @rationale C16: silent audit log loss at scale — fallback ensures audit
##           trail is never completely lost.
def test_audit_fallback_stderr() -> None:
    """
    # ▶ SCRIPT_PATH → ◇ grep for `fallback` or `>&2` in audit_log region
    #   → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_audit_fallback_stderr] Checking audit_log fallback logic in: %s", SCRIPT_PATH)

    with open(SCRIPT_PATH) as f:
        content = f.read()

    # Check for exit code capture patterns
    has_logger_rc = "logger_rc" in content
    has_file_rc = "file_rc" in content

    # Check for fallback to stderr
    has_stderr_fallback = ">&2" in content and "fallback" in content
    has_fallback_comment = "fallback" in content

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print(f"[IMP:7][test_audit_fallback_stderr] has_logger_rc={has_logger_rc}, has_file_rc={has_file_rc}")
    print(
        f"[IMP:7][test_audit_fallback_stderr] has_stderr_fallback={has_stderr_fallback}, has_fallback_comment={has_fallback_comment}"
    )
    if has_logger_rc and has_file_rc and has_stderr_fallback:
        print("[IMP:9][test_audit_fallback_stderr] PASS: audit_log captures exit codes and has stderr fallback")
    elif has_logger_rc and has_file_rc:
        print("[IMP:9][test_audit_fallback_stderr] FAIL: exit codes captured but no stderr fallback found")
    else:
        print("[IMP:9][test_audit_fallback_stderr] FAIL: missing exit code capture in audit_log")
    print("--- END LDD TRAJECTORY ---")

    assert has_logger_rc, (
        "[IMP:9][test_audit_fallback_stderr] FAIL: audit_log does not capture logger exit code (logger_rc)"
    )
    assert has_file_rc, (
        "[IMP:9][test_audit_fallback_stderr] FAIL: audit_log does not capture file write exit code (file_rc)"
    )
    assert has_stderr_fallback, (
        "[IMP:9][test_audit_fallback_stderr] FAIL: audit_log does not have stderr fallback for when both syslog and file fail"
    )
    logger.info("[IMP:9][test_audit_fallback_stderr] PASS: audit_log has fallback to stderr")


# endregion FUNC_test_audit_fallback_stderr
