#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-ssh-command-parser, parse-ssh-command, classify-verb, strip-prefixes, forced-command
# STRUCTURE: ▶ 10 test scenarios ┌parse + classify + strip + error + unknown┐ → ○ caplog LDD IMP:9 verification → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/ssh_command_parser.py — pure string-parsing
##           tests covering parse_ssh_command and classify_verb public API (DevPlan 116 B1 T1).
##           Exact-match семантика (D2): голые verb'ы классифицируются, unknown → ConfigValidationError,
##           platform-deploy strip удалён. Verb-словарь — shared/verbs.py.
## @scope    Tests public API only: parse_ssh_command(raw) → dict and classify_verb(cleaned) → str.
##           Does NOT test _strip_prefixes directly (private implementation detail).
## @invariants
##   - No Docker dependency (pure Python, no subprocess)
##   - No tmp_path needed (no file I/O)
##   - LDD: at least one IMP:9 log in each successful parse_ssh_command scenario
##   - Tests are independent — no shared mutable state
## @rationale  DevPlan 081 TASK-081B1: two duplicate SSH parsers consolidated into one
##             shared Python module. DevPlan 116 B1 T1 (D2): legacy-кейсы (platform-deploy/
##             platform-deliver, дефолт-фолбэк deploy) удалены из парсера и из тестов.
## @changes    2026-07-26 | Created — 14 tests for ssh_command_parser public API
##             2026-08-01 | DevPlan 116 B1 T1 — обновлено: unknown → error, receive \<project\> [\<sha\>],
##                         platform-deliver/deploy-фолбэк кейсы удалены
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.shared.exceptions import ConfigValidationError
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


# region FUNC_test_parse_receive
## @purpose — "receive myproject abc123" → verb=receive, args="myproject abc123" (D5: version через аргументы).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D5 receive \<project\> [\<sha\>]
# · Last fail: legacy — версия из ai-platform.yaml (phantom-поля)
# · Remove if: parse_ssh_command receive handling changes
def test_parse_receive(caplog: pytest.LogCaptureFixture) -> None:
    """Parse 'receive myproject abc123' — verb=receive, args='myproject abc123'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("receive myproject abc123")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "receive"
    assert result["args"] == "myproject abc123"
    assert result["cleaned"] == "receive myproject abc123"


# endregion FUNC_test_parse_receive


# region FUNC_test_parse_verify
## @purpose — "verify node1" → verb=verify, args="node1".
# 🧪 TRAP[TEST] · Regression · Scenario: verify → verb=verify, args=node
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command verify handling changes
def test_parse_verify(caplog: pytest.LogCaptureFixture) -> None:
    """Parse 'verify node1' — verb=verify, args=node1."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("verify node1")

    _assert_imp9_logged(caplog)

    assert result["verb"] == "verify"
    assert result["args"] == "node1"
    assert result["cleaned"] == "verify node1"


# endregion FUNC_test_parse_verify


# ── parse_ssh_command: strip tests ────────────────────────────────────────────


# region FUNC_test_strip_path_prefix
## @purpose — "/opt/platform/core/entrypoints/deploy.sh receive proj sha" →
##            cleaned="receive proj sha". Verifies path prefix stripping via
##            _strip_prefixes pipeline inside parse_ssh_command.
# 🧪 TRAP[TEST] · Regression · Scenario: full path prefix stripped in cleaned field
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes path stripping behavior changes
def test_strip_path_prefix(caplog: pytest.LogCaptureFixture) -> None:
    """Full path prefix is stripped; cleaned='receive proj sha'."""
    caplog.set_level(logging.INFO)

    raw = "/opt/platform/core/entrypoints/deploy.sh receive proj sha"
    result = parse_ssh_command(raw)

    _assert_imp9_logged(caplog)

    assert result["cleaned"] == "receive proj sha"
    assert result["verb"] == "receive"
    assert result["args"] == "proj sha"
    assert result["raw"] == raw


# endregion FUNC_test_strip_path_prefix


# region FUNC_test_strip_platform_deploy_unknown
## @purpose — "platform-deploy project sha" НЕ стрипится (D2) → unknown verb →
##            ConfigValidationError. R5-negative: legacy-префикс больше не префикс.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: platform-deploy → unknown
# · Last fail: legacy — strip удалял префикс и уходил в deploy
# · Remove if: legacy-префиксы сознательно возвращаются (запрещено D2)
def test_strip_platform_deploy_unknown(caplog: pytest.LogCaptureFixture) -> None:
    """'platform-deploy project sha' → ConfigValidationError (unknown verb, D2)."""
    caplog.set_level(logging.INFO)

    with pytest.raises(ConfigValidationError, match="unknown verb"):
        parse_ssh_command("platform-deploy project sha")


# endregion FUNC_test_strip_platform_deploy_unknown


# ── parse_ssh_command: error tests ────────────────────────────────────────────


# region FUNC_test_empty_command
## @purpose — "" → raises ValueError with message "empty command after stripping".
##            Verifies empty input rejection at the parse_ssh_command entry point.
# 🧪 TRAP[TEST] · Regression · Scenario: empty string → ValueError
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command empty-input validation changes
def test_empty_command() -> None:
    """Empty raw command raises ConfigValidationError."""
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


# region FUNC_test_classify_verb_bare_status
## @purpose — classify_verb("status") → "status" (голый verb, U-56 — НЕ deploy).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · U-56 голый status
# · Last fail: legacy — голый status уходил в deploy-фолбэк
# · Remove if: classify_verb голый-verb семантика меняется
def test_classify_verb_bare_status() -> None:
    """'status' (bare) maps to status — НЕ deploy (U-56)."""
    assert classify_verb("status") == "status"


# endregion FUNC_test_classify_verb_bare_status


# region FUNC_test_classify_verb_unknown
## @purpose — unknown input → ConfigValidationError (D2: дефолт-фолбэк удалён).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: unknown → error
# · Last fail: legacy — "deploy" фолбэк для любого unrecognized input
# · Remove if: classify_verb unknown-семантика меняется
def test_classify_verb_unknown() -> None:
    """Unknown command raises ConfigValidationError (никакого deploy-фолбэка)."""
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("someproject sha")
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("deploy proj sha")


# endregion FUNC_test_classify_verb_unknown
