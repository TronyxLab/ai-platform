#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-ssh-command-parser, parse-ssh-command, classify-verb, strip-prefixes, forced-command
# STRUCTURE: ▶ 14 test scenarios ┌parse + classify + strip + error┐ → ○ caplog LDD IMP:9 verification → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/ssh_command_parser.py — pure string-parsing
##           tests covering parse_ssh_command and classify_verb public API.
##           14 tests: parse variants, strip variants, classify variants, empty error.
## @scope    Tests public API only: parse_ssh_command(raw) → dict and classify_verb(cleaned) → str.
##           Does NOT test _strip_prefixes directly (private implementation detail).
## @invariants
##   - No Docker dependency (pure Python, no subprocess)
##   - No tmp_path needed (no file I/O)
##   - LDD: at least one IMP:9 log in each successful parse_ssh_command scenario
##   - Tests are independent — no shared mutable state
## @rationale  DevPlan 081 TASK-081B1: two duplicate SSH parsers consolidated into one
##             shared Python module. These tests cover the unified public API.
## @changes    2026-07-26 | Created — 14 tests for ssh_command_parser public API
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.shared.ssh_command_parser import classify_verb, parse_ssh_command

# ── LDD helper ─────────────────────────────────────────────────────────────────


def _assert_imp9_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Print IMP:7-10 trajectory and assert at least one IMP:9 log present.

    ## @purpose — LDD telemetry verification helper. Prints the execution log
    ##            trajectory before assertions so test failure shows the actual path.
    ## @io — ⇥ caplog: LogCaptureFixture → ⎋ None (raises AssertionError if no IMP:9)
    ## @complexity — O(N) where N = number of captured log records
    """
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# ── parse_ssh_command: verb tests ─────────────────────────────────────────────


# region FUNC_test_parse_ping
## @purpose — "ping" → verb=ping, args=None. Verifies exact-match classification.
# 🧪 TRAP[TEST] · Regression · Scenario: ping command → verb=ping, args=None
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command ping handling changes
def test_parse_ping(caplog: pytest.LogCaptureFixture) -> None:
    """Parse 'ping' — verb=ping, args=None."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("ping")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "ping"
    assert result["args"] is None
    assert result["raw"] == "ping"
    assert result["cleaned"] == "ping"


# endregion FUNC_test_parse_ping


# region FUNC_test_parse_remove
## @purpose — "remove myproject" → verb=remove, args="myproject".
##            Verifies prefix-match extraction and argument preservation.
# 🧪 TRAP[TEST] · Regression · Scenario: remove command → verb=remove, args=myproject
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command remove handling changes
def test_parse_remove(caplog: pytest.LogCaptureFixture) -> None:
    """Parse 'remove myproject' — verb=remove, args=myproject."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("remove myproject")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "remove"
    assert result["args"] == "myproject"
    assert result["cleaned"] == "remove myproject"


# endregion FUNC_test_parse_remove


# region FUNC_test_parse_status
## @purpose — "status myproject" → verb=status, args="myproject".
##            Verifies status prefix-match and argument extraction.
# 🧪 TRAP[TEST] · Regression · Scenario: status command → verb=status, args=myproject
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command status handling changes
def test_parse_status(caplog: pytest.LogCaptureFixture) -> None:
    """Parse 'status myproject' — verb=status, args=myproject."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("status myproject")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "status"
    assert result["args"] == "myproject"
    assert result["cleaned"] == "status myproject"


# endregion FUNC_test_parse_status


# region FUNC_test_parse_platform_deliver_org
## @purpose — "platform-deliver org project" (2 args) → verb=platform-deliver,
##            args="org project". Standard CI format with org prefix.
# 🧪 TRAP[TEST] · Regression · Scenario: platform-deliver with org + project
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command platform-deliver handling changes
def test_parse_platform_deliver_org(caplog: pytest.LogCaptureFixture) -> None:
    """Parse 'platform-deliver org project' — verb=platform-deliver, args='org project'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("platform-deliver my-org my-project")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "platform-deliver"
    assert result["args"] == "my-org my-project"
    assert result["cleaned"] == "platform-deliver my-org my-project"


# endregion FUNC_test_parse_platform_deliver_org


# region FUNC_test_parse_platform_deliver_legacy
## @purpose — "platform-deliver project" (1 arg) → verb=platform-deliver,
##            args="project". Legacy format without org prefix.
# 🧪 TRAP[TEST] · Regression · Scenario: platform-deliver with single arg (legacy)
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command single-arg platform-deliver handling changes
def test_parse_platform_deliver_legacy(caplog: pytest.LogCaptureFixture) -> None:
    """Parse 'platform-deliver project' (1 arg) — verb=platform-deliver, args=project."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("platform-deliver my-project")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "platform-deliver"
    assert result["args"] == "my-project"
    assert result["cleaned"] == "platform-deliver my-project"


# endregion FUNC_test_parse_platform_deliver_legacy


# region FUNC_test_parse_deploy_legacy
## @purpose — "project sha env" (bare, no prefix) → verb=deploy (default),
##            args="project sha env". Legacy format with project+sha+env.
# 🧪 TRAP[TEST] · Regression · Scenario: bare command → verb=deploy (default)
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command fallback deploy classification changes
def test_parse_deploy_legacy(caplog: pytest.LogCaptureFixture) -> None:
    """Parse 'project sha env' — verb=deploy (default), args='project sha env'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("my-project abc123 production")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "deploy"
    assert result["args"] == "my-project abc123 production"
    assert result["cleaned"] == "my-project abc123 production"


# endregion FUNC_test_parse_deploy_legacy


# ── parse_ssh_command: strip tests ────────────────────────────────────────────


# region FUNC_test_strip_path_prefix
## @purpose — "/opt/platform/core/entrypoints/deploy.sh project sha" →
##            cleaned="project sha". Verifies path prefix stripping via
##            _strip_prefixes pipeline inside parse_ssh_command.
# 🧪 TRAP[TEST] · Regression · Scenario: full path prefix stripped in cleaned field
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes path stripping behavior changes
def test_strip_path_prefix(caplog: pytest.LogCaptureFixture) -> None:
    """Full path prefix is stripped; cleaned='project sha'."""
    caplog.set_level(logging.INFO)

    raw = "/opt/platform/core/entrypoints/deploy.sh project sha"
    result = parse_ssh_command(raw)

    _assert_imp9_logged(caplog)

    assert result["cleaned"] == "project sha"
    assert result["verb"] == "deploy"
    assert result["args"] == "project sha"
    assert result["raw"] == raw


# endregion FUNC_test_strip_path_prefix


# region FUNC_test_strip_platform_deploy
## @purpose — "platform-deploy project sha" → cleaned="project sha".
##            Verifies legacy platform-deploy prefix is stripped before
##            classification, resulting in verb=deploy (not platform-deploy).
# 🧪 TRAP[TEST] · Regression · Scenario: legacy platform-deploy prefix stripped
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes legacy platform-deploy stripping changes
def test_strip_platform_deploy(caplog: pytest.LogCaptureFixture) -> None:
    """Legacy 'platform-deploy' prefix is stripped; cleaned='project sha'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("platform-deploy project sha")

    _assert_imp9_logged(caplog)

    assert result["cleaned"] == "project sha"
    assert result["verb"] == "deploy"
    assert result["args"] == "project sha"


# endregion FUNC_test_strip_platform_deploy


# ── parse_ssh_command: error tests ────────────────────────────────────────────


# region FUNC_test_empty_command
## @purpose — "" → raises ValueError with message "empty command after stripping".
##            Verifies empty input rejection at the parse_ssh_command entry point.
# 🧪 TRAP[TEST] · Regression · Scenario: empty string → ValueError
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command empty-input validation changes
def test_empty_command() -> None:
    """Empty raw command raises ValueError."""
    from core.internal.shared.exceptions import ConfigValidationError

    with pytest.raises(ConfigValidationError, match="empty command after stripping"):
        parse_ssh_command("")


# endregion FUNC_test_empty_command


# ── classify_verb tests ───────────────────────────────────────────────────────


# region FUNC_test_classify_verb_ping
## @purpose — classify_verb("ping") → "ping". Exact match takes highest priority.
# 🧪 TRAP[TEST] · Regression · Scenario: exact 'ping' → 'ping'
# · Last fail: N/A (new test)
# · Remove if: classify_verb exact-match logic changes
def test_classify_verb_ping() -> None:
    """Exact 'ping' maps to ping."""
    assert classify_verb("ping") == "ping"


# endregion FUNC_test_classify_verb_ping


# region FUNC_test_classify_verb_remove
## @purpose — classify_verb("remove myproject") → "remove".
##            Prefix match on "remove " extracts the verb.
# 🧪 TRAP[TEST] · Regression · Scenario: 'remove ...' prefix → 'remove'
# · Last fail: N/A (new test)
# · Remove if: classify_verb prefix match for 'remove' changes
def test_classify_verb_remove() -> None:
    """'remove myproject' maps to remove."""
    assert classify_verb("remove myproject") == "remove"


# endregion FUNC_test_classify_verb_remove


# region FUNC_test_classify_verb_verify
## @purpose — classify_verb("verify node") → "verify".
##            Prefix match on "verify " extracts the verb.
# 🧪 TRAP[TEST] · Regression · Scenario: 'verify ...' prefix → 'verify'
# · Last fail: N/A (new test)
# · Remove if: classify_verb prefix match for 'verify' changes
def test_classify_verb_verify() -> None:
    """'verify node' maps to verify."""
    assert classify_verb("verify node") == "verify"


# endregion FUNC_test_classify_verb_verify


# region FUNC_test_classify_verb_platform_deliver
## @purpose — classify_verb("platform-deliver org proj") → "platform-deliver".
##            Multi-word prefix match on "platform-deliver ".
# 🧪 TRAP[TEST] · Regression · Scenario: 'platform-deliver ...' prefix → 'platform-deliver'
# · Last fail: N/A (new test)
# · Remove if: classify_verb prefix match for 'platform-deliver' changes
def test_classify_verb_platform_deliver() -> None:
    """'platform-deliver org proj' maps to platform-deliver."""
    assert classify_verb("platform-deliver org proj") == "platform-deliver"


# endregion FUNC_test_classify_verb_platform_deliver


# region FUNC_test_classify_verb_default_deploy
## @purpose — classify_verb("someproject sha") → "deploy" (default fallback).
##            Any input that doesn't match exact or prefix verbs defaults to deploy.
# 🧪 TRAP[TEST] · Regression · Scenario: unrecognized input → 'deploy' (default)
# · Last fail: N/A (new test)
# · Remove if: classify_verb default fallback changes
def test_classify_verb_default_deploy() -> None:
    """Unrecognized command maps to deploy (default)."""
    assert classify_verb("someproject sha") == "deploy"


# endregion FUNC_test_classify_verb_default_deploy
