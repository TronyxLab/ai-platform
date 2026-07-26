#!/usr/bin/env python3
# GREP_SUMMARY: test-ssh-command-parser, parse-ssh-command, classify-verb, strip-prefixes
# STRUCTURE: ┌direct calls (no mock/no FS)┐ → ○ test scenarios: strip → classify → parse → CLI
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/ssh_command_parser.py
##           Pure string-parsing tests — no filesystem, no subprocess.
## @scope    Tests: _strip_prefixes, classify_verb, parse_ssh_command, CLI entry point.
## @invariants
##   - No Docker dependency (pure Python, no subprocess)
##   - No tmp_path needed (no file I/O)
##   - LDD: at least one IMP:9 log in each successful scenario
## @rationale  New shared module requires test coverage to prevent regressions
##             when deploy.sh/deploy-project.sh are migrated to use this parser.
# endregion MODULE_CONTRACT

import contextlib
import json
import logging
import sys
from unittest.mock import patch

import pytest

from core.internal.shared.ssh_command_parser import (
    _strip_prefixes,
    classify_verb,
    parse_ssh_command,
)

# ── _strip_prefixes tests ─────────────────────────────────────────────────────


# region FUNC_test_strip_full_path_with_space
## @purpose — Strip path prefix with trailing space (appleboy/ssh-action format).
##            deploy.sh: "${cleaned#/opt/platform/core/entrypoints/deploy.sh }"
# 🧪 TRAP[TEST] · Regression · Scenario: path prefix with trailing space stripped
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes behavior changes
def test_strip_full_path_with_space() -> None:
    """Path prefix with trailing space is stripped."""
    raw = "/opt/platform/core/entrypoints/deploy.sh project sha"
    cleaned = _strip_prefixes(raw)
    assert cleaned == "project sha"


# endregion


# region FUNC_test_strip_full_path_bare
## @purpose — Strip path prefix without trailing space.
##            deploy.sh: "${cleaned#/opt/platform/core/entrypoints/deploy.sh}"
# 🧪 TRAP[TEST] · Regression · Scenario: path prefix without trailing space stripped
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes behavior changes
def test_strip_full_path_bare() -> None:
    """Path prefix without trailing space is stripped."""
    raw = "/opt/platform/core/entrypoints/deploy.shproject sha"
    cleaned = _strip_prefixes(raw)
    assert cleaned == "project sha"


# endregion


# region FUNC_test_strip_legacy_platform_deploy_with_space
## @purpose — Strip legacy "platform-deploy " prefix via bash parameter expansion.
# 🧪 TRAP[TEST] · Regression · Scenario: legacy platform-deploy prefix with space
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes behavior changes
def test_strip_legacy_platform_deploy_with_space() -> None:
    """Legacy platform-deploy prefix with space is stripped."""
    raw = "platform-deploy project sha"
    cleaned = _strip_prefixes(raw)
    assert cleaned == "project sha"


# endregion


# region FUNC_test_strip_legacy_platform_deploy_bare
## @purpose — Strip bare "platform-deploy" (no args, no trailing space).
# 🧪 TRAP[TEST] · Regression · Scenario: bare platform-deploy (no args)
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes behavior changes
def test_strip_legacy_platform_deploy_bare() -> None:
    """Bare platform-deploy (no args) becomes empty after strip."""
    raw = "platform-deploy"
    cleaned = _strip_prefixes(raw)
    assert cleaned == ""


# endregion


# region FUNC_test_strip_whitespace_trim
## @purpose — Verify .strip() removes trailing whitespace after prefix stripping
##            (equivalent to deploy.sh: echo | xargs).
##            NOTE: Leading whitespace before path prefix is not expected in
##            real SSH_ORIGINAL_COMMAND — bash parameter expansion also requires
##            exact prefix match. Only trailing whitespace is tested here.
# 🧪 TRAP[TEST] · Regression · Scenario: trailing whitespace trimmed after stripping
# · Last fail: leading whitespace caused startswith miss (fixed in test input)
# · Remove if: _strip_prefixes trims input before prefix checks
def test_strip_whitespace_trim() -> None:
    """Trailing whitespace is trimmed after stripping."""
    raw = "/opt/platform/core/entrypoints/deploy.sh   project sha   "
    cleaned = _strip_prefixes(raw)
    assert cleaned == "project sha"


# endregion


# region FUNC_test_strip_no_prefix
## @purpose — Raw command without any known prefix passes through unchanged (trimmed).
# 🧪 TRAP[TEST] · Regression · Scenario: no known prefix — pass through trimmed
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes behavior changes
def test_strip_no_prefix() -> None:
    """Command without known prefix passes through trimmed."""
    raw = "  my-verb arg1 arg2  "
    cleaned = _strip_prefixes(raw)
    assert cleaned == "my-verb arg1 arg2"


# endregion


# region FUNC_test_strip_empty_input
## @purpose — Empty string stays empty after stripping.
# 🧪 TRAP[TEST] · Regression · Scenario: empty input stays empty
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes behavior changes
def test_strip_empty_input() -> None:
    """Empty input yields empty string."""
    assert _strip_prefixes("") == ""


# endregion


# ── classify_verb tests ───────────────────────────────────────────────────────


# region FUNC_test_classify_ping
## @purpose — Exact match "ping" → "ping".
# 🧪 TRAP[TEST] · Regression · Scenario: exact ping → ping
# · Last fail: N/A (new test)
# · Remove if: classify_verb verb map changes
def test_classify_ping() -> None:
    """Exact 'ping' maps to ping."""
    assert classify_verb("ping") == "ping"


# endregion


# region FUNC_test_classify_exit
## @purpose — Exact match "exit" → "exit".
# 🧪 TRAP[TEST] · Regression · Scenario: exact exit → exit
# · Last fail: N/A (new test)
# · Remove if: classify_verb verb map changes
def test_classify_exit() -> None:
    """Exact 'exit' maps to exit."""
    assert classify_verb("exit") == "exit"


# endregion


# region FUNC_test_classify_remove
## @purpose — Starts with "remove " → "remove".
# 🧪 TRAP[TEST] · Regression · Scenario: remove prefix → remove
# · Last fail: N/A (new test)
# · Remove if: classify_verb verb map changes
def test_classify_remove() -> None:
    """'remove project1' maps to remove."""
    assert classify_verb("remove project1") == "remove"


# endregion


# region FUNC_test_classify_status
## @purpose — Starts with "status " → "status".
# 🧪 TRAP[TEST] · Regression · Scenario: status prefix → status
# · Last fail: N/A (new test)
# · Remove if: classify_verb verb map changes
def test_classify_status() -> None:
    """'status project1' maps to status."""
    assert classify_verb("status project1") == "status"


# endregion


# region FUNC_test_classify_verify
## @purpose — Starts with "verify " → "verify".
# 🧪 TRAP[TEST] · Regression · Scenario: verify prefix → verify
# · Last fail: N/A (new test)
# · Remove if: classify_verb verb map changes
def test_classify_verify() -> None:
    """'verify node1' maps to verify."""
    assert classify_verb("verify node1") == "verify"


# endregion


# region FUNC_test_classify_platform_deliver
## @purpose — Starts with "platform-deliver " → "platform-deliver".
# 🧪 TRAP[TEST] · Regression · Scenario: platform-deliver prefix → platform-deliver
# · Last fail: N/A (new test)
# · Remove if: classify_verb verb map changes
def test_classify_platform_deliver() -> None:
    """'platform-deliver org project' maps to platform-deliver."""
    assert classify_verb("platform-deliver org project") == "platform-deliver"


# endregion


# region FUNC_test_classify_platform_deploy
## @purpose — Starts with "platform-deploy " → "platform-deploy".
##            Note: this is the direct classify_verb call, NOT through
##            parse_ssh_command which strips the legacy prefix first.
# 🧪 TRAP[TEST] · Regression · Scenario: platform-deploy prefix (direct classify)
# · Last fail: N/A (new test)
# · Remove if: classify_verb verb map changes
def test_classify_platform_deploy() -> None:
    """'platform-deploy project sha' maps to platform-deploy."""
    assert classify_verb("platform-deploy project sha") == "platform-deploy"


# endregion


# region FUNC_test_classify_deploy_default
## @purpose — Any unrecognized input → "deploy" (default fallback).
# 🧪 TRAP[TEST] · Regression · Scenario: unrecognized → deploy (default)
# · Last fail: N/A (new test)
# · Remove if: classify_verb default fallback changes
def test_classify_deploy_default() -> None:
    """Unknown command maps to deploy (default)."""
    assert classify_verb("project sha") == "deploy"
    assert classify_verb("") == "deploy"
    assert classify_verb("some random command") == "deploy"


# endregion


# region FUNC_test_classify_ping_precedence_over_prefix
## @purpose — Exact match "ping" takes precedence over any prefix match.
# 🧪 TRAP[TEST] · Regression · Scenario: exact ping not prefix-matched
# · Last fail: N/A (new test)
# · Remove if: classify_verb match order changes
def test_classify_ping_precedence_over_prefix() -> None:
    """Exact 'ping' is not matched as prefix of 'pingpong'."""
    assert classify_verb("ping") == "ping"
    assert classify_verb("ping something") == "deploy"


# endregion


# ── parse_ssh_command tests ───────────────────────────────────────────────────


# region FUNC_test_parse_deploy_default
## @purpose — parse_ssh_command with a deploy command produces correct dict
##            and verifies IMP:9 log.
# 🧪 TRAP[TEST] · Regression · Scenario: full deploy command → verb=deploy
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command return format changes
def test_parse_deploy_default(caplog: pytest.LogCaptureFixture) -> None:
    """Deploy command parses with verb='deploy'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("/opt/platform/core/entrypoints/deploy.sh my-project abc123")

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result["verb"] == "deploy"
    assert result["args"] == "my-project abc123"
    assert result["raw"] == "/opt/platform/core/entrypoints/deploy.sh my-project abc123"
    assert result["cleaned"] == "my-project abc123"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_parse_ping
## @purpose — "ping" command → verb="ping", args=None, and IMP:9 logged.
# 🧪 TRAP[TEST] · Regression · Scenario: ping → verb=ping, args=None, IMP:9
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command ping handling changes
def test_parse_ping(caplog: pytest.LogCaptureFixture) -> None:
    """Ping command parses with verb='ping' and args=None."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("ping")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result["verb"] == "ping"
    assert result["args"] is None
    assert result["cleaned"] == "ping"

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_parse_exit
## @purpose — "exit" command → verb="exit", args=None.
# 🧪 TRAP[TEST] · Regression · Scenario: exit → verb=exit, args=None
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command exit handling changes
def test_parse_exit() -> None:
    """Exit command parses with verb='exit'."""
    result = parse_ssh_command("exit")
    assert result["verb"] == "exit"
    assert result["args"] is None
    assert result["cleaned"] == "exit"


# endregion


# region FUNC_test_parse_remove
## @purpose — "remove project1" → verb="remove", args="project1".
# 🧪 TRAP[TEST] · Regression · Scenario: remove → verb=remove, args=project
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command remove handling changes
def test_parse_remove() -> None:
    """Remove command extracts args."""
    result = parse_ssh_command("remove my-project")
    assert result["verb"] == "remove"
    assert result["args"] == "my-project"


# endregion


# region FUNC_test_parse_status
## @purpose — "status project1" → verb="status", args="project1".
# 🧪 TRAP[TEST] · Regression · Scenario: status → verb=status, args=project
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command status handling changes
def test_parse_status() -> None:
    """Status command extracts args."""
    result = parse_ssh_command("status my-project")
    assert result["verb"] == "status"
    assert result["args"] == "my-project"


# endregion


# region FUNC_test_parse_verify
## @purpose — "verify node1" → verb="verify", args="node1".
# 🧪 TRAP[TEST] · Regression · Scenario: verify → verb=verify, args=node
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command verify handling changes
def test_parse_verify() -> None:
    """Verify command extracts args."""
    result = parse_ssh_command("verify node1")
    assert result["verb"] == "verify"
    assert result["args"] == "node1"


# endregion


# region FUNC_test_parse_platform_deliver
## @purpose — "platform-deliver org project" → verb="platform-deliver",
##            args="org project".
# 🧪 TRAP[TEST] · Regression · Scenario: platform-deliver → verb=platform-deliver
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command platform-deliver handling changes
def test_parse_platform_deliver() -> None:
    """platform-deliver command extracts args (org project)."""
    result = parse_ssh_command("platform-deliver my-org my-project")
    assert result["verb"] == "platform-deliver"
    assert result["args"] == "my-org my-project"


# endregion


# region FUNC_test_parse_platform_deploy_stripped
## @purpose — "platform-deploy project sha" → legacy prefix is stripped by
##            _strip_prefixes → classify_verb sees "my-project abc123" → "deploy".
##            The "platform-deploy" verb classification only fires when
##            classify_verb is called directly with an already-cleaned string
##            that still carries the "platform-deploy " prefix.
# 🧪 TRAP[TEST] · Regression · Scenario: platform-deploy stripped → verb=deploy
# · Last fail: initial test expected verb="platform-deploy" (incorrect — stripping
#   removes "platform-deploy " before classification)
# · Remove if: parse_ssh_command stripping order changes
def test_parse_platform_deploy_stripped() -> None:
    """platform-deploy legacy prefix is stripped; verb becomes deploy."""
    result = parse_ssh_command("platform-deploy my-project abc123")
    assert result["verb"] == "deploy"
    assert result["args"] == "my-project abc123"
    assert result["cleaned"] == "my-project abc123"


# endregion


# region FUNC_test_parse_full_path_platform_deliver
## @purpose — Full path prefix + platform-deliver → correct verb and args.
# 🧪 TRAP[TEST] · Regression · Scenario: full path + platform-deliver
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command multi-prefix stripping changes
def test_parse_full_path_platform_deliver() -> None:
    """Full path prefix with platform-deliver parses correctly."""
    result = parse_ssh_command("/opt/platform/core/entrypoints/deploy.sh platform-deliver org project")
    assert result["verb"] == "platform-deliver"
    assert result["args"] == "org project"


# endregion


# region FUNC_test_parse_empty_raw_raises
## @purpose — Empty raw input → ValueError with correct message.
# 🧪 TRAP[TEST] · Regression · Scenario: empty raw → ValueError
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command empty-input handling changes
def test_parse_empty_raw_raises() -> None:
    """Empty raw input raises ValueError."""
    with pytest.raises(ValueError, match="empty command after stripping"):
        parse_ssh_command("")


# endregion


# region FUNC_test_parse_none_raises
## @purpose — None-ish empty string raises ValueError.
# 🧪 TRAP[TEST] · Regression · Scenario: whitespace-only → ValueError
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command empty-input handling changes
def test_parse_none_raises() -> None:
    """Empty string (whitespace) raises ValueError."""
    with pytest.raises(ValueError, match="empty command after stripping"):
        parse_ssh_command("   ")


# endregion


# region FUNC_test_parse_legacy_platform_deploy
## @purpose — Legacy platform-deploy prefix (deprecated format) → verb="deploy".
# 🧪 TRAP[TEST] · Regression · Scenario: legacy platform-deploy → deploy
# · Last fail: initial test expected verb="platform-deploy" (incorrect)
# · Remove if: parse_ssh_command legacy prefix handling changes
def test_parse_legacy_platform_deploy() -> None:
    """Legacy 'platform-deploy project' without full path still parses."""
    result = parse_ssh_command("platform-deploy my-project")
    assert result["verb"] == "deploy"
    assert result["args"] == "my-project"


# endregion


# region FUNC_test_parse_preserves_raw
## @purpose — The raw field in the result dict always equals the input.
# 🧪 TRAP[TEST] · Regression · Scenario: raw field preserved
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command return format changes
def test_parse_preserves_raw() -> None:
    """Raw field preserves original input."""
    raw = "/opt/platform/core/entrypoints/deploy.sh my-project sha"
    result = parse_ssh_command(raw)
    assert result["raw"] == raw


# endregion


# ── CLI tests ─────────────────────────────────────────────────────────────────


# region FUNC_test_cli_parse
## @purpose — CLI parse mode outputs JSON to stdout.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI parse mode
# · Last fail: N/A (new test)
# · Remove if: CLI interface or _cli_main changes
def test_cli_parse() -> None:
    """CLI parse mode outputs JSON."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "parse", "/opt/platform/core/entrypoints/deploy.sh my-project sha"]
    with patch.object(sys, "argv", test_args), patch("sys.stderr"), contextlib.suppress(SystemExit):
        _cli_main()


# endregion


# region FUNC_test_cli_classify
## @purpose — CLI classify mode outputs bare verb string to stdout.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI classify mode
# · Last fail: N/A (new test)
# · Remove if: CLI interface or _cli_main changes
def test_cli_classify() -> None:
    """CLI classify mode prints verb string."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "classify", "remove my-project"]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with patch.object(sys, "argv", test_args), patch("builtins.print", fake_print), contextlib.suppress(SystemExit):
        _cli_main()

    assert stdout_lines == ["remove"], f"Expected ['remove'], got {stdout_lines}"


# endregion


# region FUNC_test_cli_parse_json_output
## @purpose — CLI parse mode produces valid JSON with expected fields.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI parse JSON output
# · Last fail: N/A (new test)
# · Remove if: CLI parse JSON format changes
def test_cli_parse_json_output() -> None:
    """CLI parse mode produces valid JSON."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "parse", "ping"]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with patch.object(sys, "argv", test_args), patch("builtins.print", fake_print), contextlib.suppress(SystemExit):
        _cli_main()

    assert len(stdout_lines) == 1
    output = json.loads(stdout_lines[0])
    assert output["verb"] == "ping"
    assert output["args"] is None
    assert output["raw"] == "ping"
    assert output["cleaned"] == "ping"


# endregion


# region FUNC_test_cli_no_args
## @purpose — CLI with no arguments prints usage and exits with code 1.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI no args → exit 1
# · Last fail: N/A (new test)
# · Remove if: CLI argument parsing changes
def test_cli_no_args() -> None:
    """CLI with no arguments exits with code 1."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py"]
    captured_stderr: list[str] = []

    def fake_stderr_write(msg: str) -> int:
        captured_stderr.append(msg)
        return len(msg)

    with (
        patch.object(sys, "argv", test_args),
        patch.object(sys, "stderr") as mock_stderr,
        pytest.raises(SystemExit) as exc_info,
    ):
        mock_stderr.write = fake_stderr_write  # type: ignore[method-assign]
        _cli_main()

    assert exc_info.value.code == 1
    assert any("Usage" in line for line in captured_stderr)


# endregion


# region FUNC_test_cli_invalid_mode
## @purpose — CLI with unknown mode exits with code 1.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI unknown mode → exit 1
# · Last fail: N/A (new test)
# · Remove if: CLI mode dispatch changes
def test_cli_invalid_mode() -> None:
    """CLI with unknown mode exits with code 1."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "unknown", "arg"]
    with (
        patch.object(sys, "argv", test_args),
        pytest.raises(SystemExit) as exc_info,
    ):
        _cli_main()

    assert exc_info.value.code == 1


# endregion


# region FUNC_test_cli_parse_format_lines
## @purpose — CLI parse mode with --format lines outputs verb/args/cleaned on separate lines.
##            Replaces inline python3 -c in deploy.sh (DevPlan 081 AC7).
# 🧪 TRAP[TEST] · Regression · Scenario: --format lines produces line-by-line output
# · Last fail: N/A (new test)
# · Remove if: --format lines output format changes
def test_cli_parse_format_lines() -> None:
    """CLI --format lines parse outputs verb/args/cleaned on separate lines."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = [
        "ssh_command_parser.py",
        "--format",
        "lines",
        "parse",
        "/opt/platform/core/entrypoints/deploy.sh my-project abc123",
    ]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with patch.object(sys, "argv", test_args), patch("builtins.print", fake_print), contextlib.suppress(SystemExit):
        _cli_main()

    assert len(stdout_lines) == 3
    assert stdout_lines[0] == "deploy"
    assert stdout_lines[1] == "my-project abc123"
    assert stdout_lines[2] == "my-project abc123"


# endregion


# region FUNC_test_cli_parse_format_lines_ping
## @purpose --format lines parse "ping" — verb=ping, args empty string, cleaned=ping.
# 🧪 TRAP[TEST] · Regression · Scenario: --format lines ping command
# · Last fail: N/A (new test)
# · Remove if: --format lines output format changes
def test_cli_parse_format_lines_ping() -> None:
    """CLI --format lines parse ping — args is empty string."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "--format", "lines", "parse", "ping"]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with patch.object(sys, "argv", test_args), patch("builtins.print", fake_print), contextlib.suppress(SystemExit):
        _cli_main()

    assert len(stdout_lines) == 3
    assert stdout_lines[0] == "ping"
    assert stdout_lines[1] == ""
    assert stdout_lines[2] == "ping"


# endregion


# region FUNC_test_cli_parse_format_lines_empty
## @purpose --format lines parse empty command — exits with code 1.
# 🧪 TRAP[TEST] · Regression · Scenario: --format lines parse empty → exit 1
# · Last fail: N/A (new test)
# · Remove if: --format lines error handling changes
def test_cli_parse_format_lines_empty() -> None:
    """CLI --format lines parse empty command exits 1."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "--format", "lines", "parse", ""]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with (
        patch.object(sys, "argv", test_args),
        patch("builtins.print", fake_print),
        pytest.raises(SystemExit) as exc_info,
    ):
        _cli_main()

    assert exc_info.value.code == 1
    assert len(stdout_lines) == 3
    assert stdout_lines[0] == "error"
    assert "empty command" in stdout_lines[1]


# endregion


# region FUNC_test_cli_format_lines_unknown_format
## @purpose --format with unknown format value exits with code 1.
# 🧪 TRAP[TEST] · Regression · Scenario: --format unknown → exit 1
# · Last fail: N/A (new test)
# · Remove if: --format argument parsing changes
def test_cli_format_lines_unknown_format() -> None:
    """CLI --format unknown exits 1."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "--format", "xml", "parse", "ping"]
    with (
        patch.object(sys, "argv", test_args),
        pytest.raises(SystemExit) as exc_info,
    ):
        _cli_main()

    assert exc_info.value.code == 1


# endregion


# region FUNC_test_cli_parse_empty
## @purpose — CLI parse mode on empty command exits with code 1 and JSON error.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI parse empty → exit 1 + JSON error
# · Last fail: N/A (new test)
# · Remove if: CLI empty-command handling changes
def test_cli_parse_empty() -> None:
    """CLI parse with empty command exits 1 with JSON error."""
    from core.internal.shared.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "parse", ""]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with (
        patch.object(sys, "argv", test_args),
        patch("builtins.print", fake_print),
        pytest.raises(SystemExit) as exc_info,
    ):
        _cli_main()

    assert exc_info.value.code == 1
    assert len(stdout_lines) == 1
    err = json.loads(stdout_lines[0])
    assert "error" in err
    assert "empty command after stripping" in err["error"]


# endregion
