#!/usr/bin/env python3
# GREP_SUMMARY: contract-test deploy-project audit audit_log AUDIT_LOG format status START SUCCESS FAIL ROLLBACK HOOK-FAIL ordering bash subprocess fallback
# STRUCTURE: ▶ source deploy-project.sh → ∋ audit_log(step, status, msg) → ⊕ ts|step|status|msg → ◇ logger fail → ◇ stderr [IMP:8] fallback → ◇ format match → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Contract tests for deploy-project.sh audit_log() (from lib/audit_logging.sh).
##           Verifies log format "${ts} | ${step} | ${status} | ${msg}", all status
##           values (START, SUCCESS, FAIL, ROLLBACK, HOOK-FAIL), and ordering of entries.
##           Tests use the fallback stderr path by making mock logger fail.
## @scope    Four test groups: format verification, all status codes, ordering,
##           and stderr fallback output. Tests assert on stderr content because AUDIT_LOG
##           is a readonly hardcoded path (/var/log/platform/audit.log) that is not
##           writable in test environments. The mock logger is made to return non-zero so
##           audit_log()'s belt-and-suspenders fallback writes to stderr via
##           [IMP:8][audit][fallback].
## @invariants
##   - audit_log writes exactly 1 [IMP:8] line to stderr per call when both syslog
##     and file write fail
##   - Stderr line format: [IMP:8][audit][fallback] <ISO8601-ts> | <step> | <status> | <msg>
##   - All statuses produce correct entries
##   - Order of entries in stderr matches call order
##   - AUDIT_LOG is hardcoded readonly (/var/log/platform/audit.log) — file assertion
##     is replaced by stderr assertion for test isolation
##   - All tests use tmp_path for isolation (Zero Hardcode Rule)
## @rationale Q: Why assert on stderr fallback instead of auditing AUDIT_LOG file?
##            A: AUDIT_LOG is readonly and hardcoded to /var/log/platform/audit.log.
##            audit_log() writes to syslog (primary) and file (belt); the mock logger
##            is made to return non-zero so the fallback [IMP:8] triggers on stderr,
##            which is the capture channel in test environments.
## @changes MODIFIED: 2026-07-17 | T13 — Switched from audit_write to audit_log;
##           tests now verify [IMP:8][audit][fallback] instead of [IMP:9][platform-deploy][audit]
# endregion MODULE_CONTRACT

import os
import pathlib
import re
import subprocess

import pytest

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)
DEPLOY_SCRIPT_PATH: str = os.path.join(PLATFORM_ROOT, "core", "internal", "deploy", "deploy-project.sh")


# ── Golden format ──────────────────────────────────────────────────────────

# Expected stderr format (fallback when both syslog AND file append fail):
#   [IMP:8][audit][fallback] 2026-07-17T00:00:00Z | step | STATUS | msg
# Extract the audit entry part after "[IMP:8][audit][fallback] "
AUDIT_ENTRY_RE: re.Pattern = re.compile(
    r"\[IMP:8\]\[audit\]\[fallback\] "
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \| .+ \| (START|SUCCESS|FAIL|ROLLBACK|HOOK-FAIL|FIRST-DEPLOY-FAIL|ROLLBACK-FAIL|DONE|INFO) \| .+"
)

FALLBACK_PREFIX: str = "[IMP:8][audit][fallback] "


def _extract_audit_entries(stderr: str) -> list[str]:
    """Extract audit entry lines from stderr.

    ## @purpose  Parse [IMP:8][audit][fallback] lines from stderr and
    ##           return the entry portion (without the IMP:8 prefix).
    ## @io       ⇥ stderr: str → ⎋ list[str] of audit entries
    ## @complexity O(n) where n = stderr lines
    """
    entries = []
    for line in stderr.splitlines():
        m = AUDIT_ENTRY_RE.match(line)
        if m:
            idx = line.find(FALLBACK_PREFIX)
            if idx >= 0:
                entries.append(line[idx + len(FALLBACK_PREFIX):])
    return entries


# ── Helpers ─────────────────────────────────────────────────────────────────


def _assert_ldd_audit(result: subprocess.CompletedProcess,
                      expected_patterns: list[str] | None = None) -> None:
    """Print LDD trajectory from stderr, assert at least IMP:7+ AND an audit fallback entry.

    ## @purpose — Local assertion for audit tests (audit_log uses IMP:8 fallback, not IMP:9)
    ## @io       ⇥ result.CompletedProcess, expected_patterns: optional patterns to find in stderr
    ## @complexity O(n) where n = stderr lines
    """
    found_imp7 = False
    found_fallback = False
    print("--- LDD TRAJECTORY (IMP:7-10) [from stderr] ---")
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            try:
                imp_str = line.split("[IMP:")[1].split("]")[0]
                imp_level = int(imp_str)
                if imp_level >= 7:
                    print(line)
                    found_imp7 = True
                if "[audit][fallback]" in line:
                    found_fallback = True
            except (ValueError, IndexError):
                pass
    print("--- END LDD TRAJECTORY ---")
    assert found_imp7, "Critical LDD Error: No IMP:7+ log found in stderr"
    assert found_fallback, "Audit fallback entry not found in stderr — audit_log may not have been called"
    if expected_patterns:
        for pattern in expected_patterns:
            assert pattern in result.stderr, f"Expected '{pattern}' in stderr:\n{result.stderr}"


# region FUNC__run_bash
## @purpose  Source deploy-project.sh, remove traps, then run provided bash code.
##           Provides mock logger that FAILS (returns 1) so audit_log()'s fallback
##           writes [IMP:8][audit][fallback] to stderr for test capture.
##           AUDIT_LOG is readonly in deploy-project.sh — no override possible.
## @io       ⇥ (tmp_path, code, env) → ⎋ CompletedProcess
## @complexity O(1) — single subprocess.run with 15s timeout
def _run_bash(
    tmp_path: pathlib.Path,
    code: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    script = tmp_path / "test_audit.sh"
    deploy_path_escaped = str(DEPLOY_SCRIPT_PATH)

    script_content = (
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        # Mock logger that FAILS (returns 1) to trigger audit_log's fallback stderr path
        # NOTE: no export -f (macOS /bin/bash v3.2 doesn't support it + not needed —
        #   audit_log runs in the same shell process, function is in scope without export)
        'logger() { return 1; }\n'
        f'source "{deploy_path_escaped}"\n'
        'trap - ERR EXIT\n'
        # Override audit paths to guarantee fallback (mock logger fails + file write fails)
        'PLATFORM_LOG_DIR="/nonexistent-root-only/platform"\n'
        'PLATFORM_AUDIT_LOG="/nonexistent-root-only/platform/audit.log"\n'
        f'{code}\n'
    )
    script.write_text(script_content)
    script.chmod(0o755)

    full_env = os.environ.copy()
    full_env["__LOG_PREFIX"] = "test"
    # AUDIT_LOG is readonly and hardcoded — skip writing to it (will fail silently)
    if env:
        full_env.update(env)

    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
        env=full_env,
    )


# endregion FUNC__run_bash


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: audit_log format
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_audit_log_format
@pytest.mark.contract
## @purpose  audit_log writes entries in the canonical format via stderr IMP:8 fallback:
##           "[IMP:8][audit][fallback] ${ts} | ${step} | ${status} | ${msg}"
## @scenario  Single audit_log call → extract entry from stderr → assert format
def test_audit_log_format(tmp_path: pathlib.Path) -> None:
    """
    # ▶ audit_log "platform-deploy:test" "START" "Deploy started"
    #   → ◇ stderr matches [IMP:8][audit][fallback] ts | step | STATUS | msg
    #   → ⎋ pass
    """
    code = (
        'audit_log "platform-deploy:test" "START" "Deploy started"\n'
    )

    result = _run_bash(tmp_path, code)

    _assert_ldd_audit(result)

    entries = _extract_audit_entries(result.stderr)
    assert len(entries) == 1, f"Expected 1 audit entry, got {len(entries)}:\n{result.stderr}"
    entry = entries[0]

    # Verify format: ts | step | STATUS | msg
    assert " | " in entry, f"Audit entry missing '|' separators: {entry}"
    parts = entry.split(" | ")
    assert len(parts) >= 4, f"Expected at least 4 parts (ts, step, status, msg), got {len(parts)}: {entry}"
    assert parts[1] == "platform-deploy:test", f"Expected step='platform-deploy:test', got '{parts[1]}'"
    assert parts[2] == "START", f"Expected status=START, got '{parts[2]}'"

    print(f"[IMP:9][test_audit_log_format] PASS: format verified: {entry}")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_audit_log_format


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: all status codes
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_audit_log_all_statuses
@pytest.mark.contract
## @purpose  audit_log supports all required status values: START, SUCCESS, FAIL,
##           ROLLBACK, HOOK-FAIL (plus legacy DONE, ROLLBACK-FAIL, FIRST-DEPLOY-FAIL).
## @scenario  Write 1 entry per status → assert each present in stderr
def test_audit_log_all_statuses(tmp_path: pathlib.Path) -> None:
    """
    # ▶ audit_log(..., "START"), ..., audit_log(..., "HOOK-FAIL")
    #   → ◇ all statuses present in stderr → ⎋ pass
    """
    code = (
        'audit_log "step1" "START" "Starting deploy"\n'
        'audit_log "step2" "SUCCESS" "Deploy completed"\n'
        'audit_log "step3" "FAIL" "Deploy failed"\n'
        'audit_log "step4" "ROLLBACK" "Rolled back to previous"\n'
        'audit_log "step5" "HOOK-FAIL" "Post-deploy hook failed"\n'
        # Legacy statuses used by deploy-project.sh
        'audit_log "step6" "DONE" "Deploy done"\n'
        'audit_log "step7" "ROLLBACK-FAIL" "Rollback compose up failed"\n'
        'audit_log "step8" "FIRST-DEPLOY-FAIL" "First deploy failed"\n'
    )

    result = _run_bash(tmp_path, code)

    _assert_ldd_audit(result)

    entries = _extract_audit_entries(result.stderr)
    assert len(entries) == 8, f"Expected 8 audit entries, got {len(entries)}"

    statuses_found = set()
    for entry in entries:
        parts = entry.split(" | ")
        assert len(parts) >= 4, f"Malformed entry: {entry}"
        statuses_found.add(parts[2])

    required_statuses = {"START", "SUCCESS", "FAIL", "ROLLBACK", "HOOK-FAIL"}
    missing = required_statuses - statuses_found
    assert not missing, f"Missing statuses: {missing}\nFound: {statuses_found}"

    print(f"[IMP:9][test_audit_log_all_statuses] PASS: All statuses present: {statuses_found}")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_audit_log_all_statuses


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: ordering
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_audit_log_ordering
@pytest.mark.contract
## @purpose  audit_log preserves call order in the audit log — entries appear
##           in stderr in the same sequence they were written.
## @scenario  Write 3 entries with sequential steps → assert order in stderr
def test_audit_log_ordering(tmp_path: pathlib.Path) -> None:
    """
    # ▶ audit_log step1 START → audit_log step2 SUCCESS → audit_log step3 FAIL
    #   → ◇ stderr entries match call order → ⎋ pass
    """
    code = (
        'audit_log "step-1" "START" "First step"\n'
        'audit_log "step-2" "SUCCESS" "Second step"\n'
        'audit_log "step-3" "FAIL" "Third step"\n'
    )

    result = _run_bash(tmp_path, code)

    _assert_ldd_audit(result)

    entries = _extract_audit_entries(result.stderr)
    assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"

    # Check order: step-1, step-2, step-3
    for i, step in enumerate(["step-1", "step-2", "step-3"], start=1):
        assert step in entries[i - 1], f"Entry {i} should contain '{step}': {entries[i - 1]}"

    print("[IMP:9][test_audit_log_ordering] PASS: entries preserved in call order")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_audit_log_ordering


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: stderr fallback output
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_audit_log_fallback_stderr
@pytest.mark.contract
## @purpose  audit_log writes [IMP:8][audit][fallback] line to stderr when both syslog
##           and file append fail. This is the test-observable assertion channel.
## @scenario  audit_log with failing mock logger → assert stderr contains fallback entry
def test_audit_log_fallback_stderr(tmp_path: pathlib.Path) -> None:
    """
    # ▶ audit_log "platform-deploy:test" "SUCCESS" "Completed"
    #   → ◇ stderr has [IMP:8][audit][fallback] entry → ⎋ pass
    """
    code = (
        'audit_log "platform-deploy:test" "SUCCESS" "Completed"\n'
    )

    result = _run_bash(tmp_path, code)

    assert FALLBACK_PREFIX.strip() in result.stderr, (
        f"Expected audit fallback line in stderr:\n{result.stderr}"
    )
    # Verify entry content: step | status | msg
    assert "platform-deploy:test | SUCCESS | Completed" in result.stderr, (
        f"Expected audit entry content in stderr:\n{result.stderr}"
    )

    print("[IMP:9][test_audit_log_fallback_stderr] PASS: stderr contains audit fallback trajectory")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_audit_log_fallback_stderr
