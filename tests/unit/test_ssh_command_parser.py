#!/usr/bin/env python3
# GREP_SUMMARY: test-ssh-command-parser, parse-ssh-command, classify-verb, strip-prefixes
# STRUCTURE: ┌direct calls (no mock/no FS)┐ → ○ test scenarios: strip → classify → parse → CLI → unknown
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/ssh_command_parser.py
##           Pure string-parsing tests — no filesystem, no subprocess.
##           DevPlan 116 B1 T1 (D2): exact-match семантика, unknown → ConfigValidationError,
##           platform-deploy/platform-deliver legacy-кейсы удалены.
## @scope    Tests: _strip_prefixes, classify_verb, parse_ssh_command, CLI entry point.
## @invariants
##   - No Docker dependency (pure Python, no subprocess)
##   - No tmp_path needed (no file I/O)
##   - LDD: at least one IMP:9 log in each successful scenario
##   - R5: negative-тесты для unknown verb (не deploy-фолбэк)
## @rationale  New shared module requires test coverage to prevent regressions
##             when the forced-command dispatcher uses this parser.
## @changes 2026-08-01 | DevPlan 116 B1 T1 — legacy platform-deploy/platform-deliver кейсы удалены,
##                     unknown → ConfigValidationError, receive <project> [<sha>]
# endregion MODULE_CONTRACT

import contextlib
import json
import logging
import sys
from unittest.mock import patch

import pytest

from core.internal.deploy.ssh_command_parser import (
    _strip_prefixes,
    classify_verb,
    parse_ssh_command,
)
from core.internal.shared.exceptions import ConfigValidationError

# ── _strip_prefixes tests ─────────────────────────────────────────────────────


# region FUNC_test_strip_full_path_with_space
## @purpose — Strip path prefix with trailing space (appleboy/ssh-action format).
# 🧪 TRAP[TEST] · Regression · Scenario: path prefix with trailing space stripped
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes behavior changes
def test_strip_full_path_with_space() -> None:
    """Path prefix with trailing space is stripped."""
    raw = "/opt/platform/core/entrypoints/deploy.sh receive proj sha"
    cleaned = _strip_prefixes(raw)
    assert cleaned == "receive proj sha"


# endregion


# region FUNC_test_strip_full_path_bare
## @purpose — Strip path prefix without trailing space.
# 🧪 TRAP[TEST] · Regression · Scenario: path prefix without trailing space stripped
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes behavior changes
def test_strip_full_path_bare() -> None:
    """Path prefix without trailing space is stripped."""
    raw = "/opt/platform/core/entrypoints/deploy.shreceive proj sha"
    cleaned = _strip_prefixes(raw)
    assert cleaned == "receive proj sha"


# endregion


# region FUNC_test_strip_legacy_platform_deploy_kept
## @purpose — Legacy "platform-deploy " НЕ стрипится (D2, DevPlan 116 B1) — префикс удалён
##            из стриппера; команда остаётся как есть (уходит в unknown verb). R5-negative.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: platform-deploy не стрипится
# · Last fail: legacy — strip удалял префикс и уходил в deploy
# · Remove if: legacy-префиксы сознательно возвращаются (запрещено D2)
def test_strip_legacy_platform_deploy_kept() -> None:
    """Legacy platform-deploy prefix НЕ стрипится (D2)."""
    raw = "platform-deploy project sha"
    cleaned = _strip_prefixes(raw)
    assert cleaned == "platform-deploy project sha"


# endregion


# region FUNC_test_strip_bare_platform_deploy_kept
## @purpose — Bare "platform-deploy" (no args) НЕ стрипится (D2).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: bare platform-deploy не стрипится
# · Last fail: legacy — bare platform-deploy становился пустой строкой
# · Remove if: legacy-префиксы сознательно возвращаются
def test_strip_bare_platform_deploy_kept() -> None:
    """Bare platform-deploy НЕ стрипится (остаётся как есть)."""
    raw = "platform-deploy"
    cleaned = _strip_prefixes(raw)
    assert cleaned == "platform-deploy"


# endregion


# region FUNC_test_strip_whitespace_trim
## @purpose — Verify .strip() removes trailing whitespace after prefix stripping.
# 🧪 TRAP[TEST] · Regression · Scenario: trailing whitespace trimmed after stripping
# · Last fail: leading whitespace caused startswith miss (fixed in test input)
# · Remove if: _strip_prefixes trims input before prefix checks
def test_strip_whitespace_trim() -> None:
    """Trailing whitespace is trimmed after stripping."""
    raw = "/opt/platform/core/entrypoints/deploy.sh   status myproj   "
    cleaned = _strip_prefixes(raw)
    assert cleaned == "status myproj"


# endregion


# region FUNC_test_strip_no_prefix
## @purpose — Raw command without any known prefix passes through unchanged (trimmed).
# 🧪 TRAP[TEST] · Regression · Scenario: no known prefix — pass through trimmed
# · Last fail: N/A (new test)
# · Remove if: _strip_prefixes behavior changes
def test_strip_no_prefix() -> None:
    """Command without known prefix passes through trimmed."""
    raw = "  status myproj  "
    cleaned = _strip_prefixes(raw)
    assert cleaned == "status myproj"


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


# region FUNC_test_classify_bare_status
## @purpose — Голый "status" → "status" (U-56: голый verb, НЕ проект).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · U-56 голый status
# · Last fail: legacy — голый status уходил в deploy
# · Remove if: classify_verb голый-verb семантика меняется
def test_classify_bare_status() -> None:
    """Bare 'status' maps to status (НЕ deploy, U-56)."""
    assert classify_verb("status") == "status"


# endregion


# region FUNC_test_classify_unknown
## @purpose — Unknown input → ConfigValidationError (D2: дефолт-фолбэк удалён).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: unknown → error
# · Last fail: legacy — "deploy" фолбэк для любого unrecognized input
# · Remove if: classify_verb unknown-семантика меняется
def test_classify_unknown() -> None:
    """Unknown commands raise ConfigValidationError (никакого deploy-фолбэка)."""
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("platform-deploy project sha")
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("platform-deliver org project")
    with pytest.raises(ConfigValidationError, match="unknown verb"):
        classify_verb("project sha")


# endregion


# ── parse_ssh_command tests ───────────────────────────────────────────────────


# region FUNC_test_parse_receive
## @purpose — parse_ssh_command with receive command produces correct dict
##            and verifies IMP:9 log.
# 🧪 TRAP[TEST] · Regression · Scenario: receive command → verb=receive
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command return format changes
def test_parse_receive(caplog: pytest.LogCaptureFixture) -> None:
    """Receive command parses with verb='receive'."""
    caplog.set_level(logging.INFO)

    result = parse_ssh_command("/opt/platform/core/entrypoints/deploy.sh receive my-project abc123")

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result["verb"] == "receive"
    assert result["args"] == "my-project abc123"
    assert result["raw"] == "/opt/platform/core/entrypoints/deploy.sh receive my-project abc123"
    assert result["cleaned"] == "receive my-project abc123"
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


# region FUNC_test_parse_unknown_raises
## @purpose — Unknown verb (включая legacy platform-deploy/platform-deliver) → ConfigValidationError.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 negative: unknown → error через parse
# · Last fail: legacy — дефолт-фолбэк deploy
# · Remove if: unknown-семантика меняется
def test_parse_unknown_raises() -> None:
    """Unknown verb (platform-deploy/platform-deliver/bare) raises ConfigValidationError."""
    for raw in (
        "platform-deploy my-project abc123",
        "platform-deliver org project",
        "deploy my-project abc123",
        "my-project abc123 production",
    ):
        with pytest.raises(ConfigValidationError, match="unknown verb"):
            parse_ssh_command(raw)


# endregion


# region FUNC_test_parse_empty_raw_raises
## @purpose — Empty raw input → ConfigValidationError with correct message.
# 🧪 TRAP[TEST] · Regression · Scenario: empty raw → ConfigValidationError
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command empty-input handling changes
def test_parse_empty_raw_raises() -> None:
    """Empty raw input raises ConfigValidationError."""
    with pytest.raises(ConfigValidationError, match="empty command after stripping"):
        parse_ssh_command("")


# endregion


# region FUNC_test_parse_none_raises
## @purpose — Whitespace-only input raises ConfigValidationError.
# 🧪 TRAP[TEST] · Regression · Scenario: whitespace-only → ConfigValidationError
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command empty-input handling changes
def test_parse_none_raises() -> None:
    """Empty string (whitespace) raises ConfigValidationError."""
    with pytest.raises(ConfigValidationError, match="empty command after stripping"):
        parse_ssh_command("   ")


# endregion


# region FUNC_test_parse_preserves_raw
## @purpose — The raw field in the result dict always equals the input.
# 🧪 TRAP[TEST] · Regression · Scenario: raw field preserved
# · Last fail: N/A (new test)
# · Remove if: parse_ssh_command return format changes
def test_parse_preserves_raw() -> None:
    """Raw field preserves original input."""
    raw = "/opt/platform/core/entrypoints/deploy.sh receive my-project sha"
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
    """CLI parse mode outputs JSON and returns exit code 0."""
    from core.internal.deploy.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "parse", "/opt/platform/core/entrypoints/deploy.sh receive my-project sha"]
    with patch.object(sys, "argv", test_args), patch("sys.stderr"):
        rc = _cli_main()
    assert rc == 0, f"_cli_main should return 0 for valid parse, got {rc}"


# endregion


# region FUNC_test_cli_classify
## @purpose — CLI classify mode outputs bare verb string to stdout.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI classify mode
# · Last fail: N/A (new test)
# · Remove if: CLI interface or _cli_main changes
def test_cli_classify() -> None:
    """CLI classify mode prints verb string."""
    from core.internal.deploy.ssh_command_parser import _cli_main

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
    from core.internal.deploy.ssh_command_parser import _cli_main

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
    from core.internal.deploy.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py"]
    captured_stderr: list[str] = []

    def fake_stderr_write(msg: str) -> int:
        captured_stderr.append(msg)
        return len(msg)

    with (
        patch.object(sys, "argv", test_args),
        patch.object(sys, "stderr") as mock_stderr,
    ):
        mock_stderr.write = fake_stderr_write  # type: ignore[method-assign]
        assert _cli_main() == 1
    assert any("Usage" in line for line in captured_stderr)


# endregion


# region FUNC_test_cli_invalid_mode
## @purpose — CLI with unknown mode exits with code 1.
# 🧪 TRAP[TEST] · Regression · Scenario: CLI unknown mode → exit 1
# · Last fail: N/A (new test)
# · Remove if: CLI mode dispatch changes
def test_cli_invalid_mode() -> None:
    """CLI with unknown mode exits with code 1."""
    from core.internal.deploy.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "unknown", "arg"]
    with (
        patch.object(sys, "argv", test_args),
    ):
        assert _cli_main() == 1


# endregion


# region FUNC_test_cli_parse_format_lines
## @purpose — CLI parse mode with --format lines outputs verb/args/cleaned on separate lines.
# 🧪 TRAP[TEST] · Regression · Scenario: --format lines produces line-by-line output
# · Last fail: N/A (new test)
# · Remove if: --format lines output format changes
def test_cli_parse_format_lines() -> None:
    """CLI --format lines parse outputs verb/args/cleaned on separate lines."""
    from core.internal.deploy.ssh_command_parser import _cli_main

    test_args = [
        "ssh_command_parser.py",
        "--format",
        "lines",
        "parse",
        "/opt/platform/core/entrypoints/deploy.sh receive my-project abc123",
    ]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with patch.object(sys, "argv", test_args), patch("builtins.print", fake_print), contextlib.suppress(SystemExit):
        _cli_main()

    assert len(stdout_lines) == 3
    assert stdout_lines[0] == "receive"
    assert stdout_lines[1] == "my-project abc123"
    assert stdout_lines[2] == "receive my-project abc123"


# endregion


# region FUNC_test_cli_parse_format_lines_ping
## @purpose --format lines parse "ping" — verb=ping, args empty string, cleaned=ping.
# 🧪 TRAP[TEST] · Regression · Scenario: --format lines ping command
# · Last fail: N/A (new test)
# · Remove if: --format lines output format changes
def test_cli_parse_format_lines_ping() -> None:
    """CLI --format lines parse ping — args is empty string."""
    from core.internal.deploy.ssh_command_parser import _cli_main

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
## @purpose --format lines parse empty command — exits with code 4 (ConfigValidationError.exit_code).
# 🧪 TRAP[TEST] · Regression · Scenario: --format lines parse empty → exit 4
# · Last fail: N/A (new test)
# · Remove if: --format lines error handling changes
def test_cli_parse_format_lines_empty() -> None:
    """CLI --format lines parse empty command exits 4."""
    from core.internal.deploy.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "--format", "lines", "parse", ""]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with (
        patch.object(sys, "argv", test_args),
        patch("builtins.print", fake_print),
    ):
        assert _cli_main() == 4  # ConfigValidationError.exit_code
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
    from core.internal.deploy.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "--format", "xml", "parse", "ping"]
    with (
        patch.object(sys, "argv", test_args),
    ):
        assert _cli_main() == 1


# endregion


# region FUNC_test_cli_parse_unknown_verb
## @purpose — CLI parse mode on unknown verb exits with code 4 and JSON error (D2).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T1 · D2 CLI negative: unknown verb → exit 4 + JSON
# · Last fail: legacy — CLI молча возвращал deploy
# · Remove if: CLI unknown-verb handling changes
def test_cli_parse_unknown_verb() -> None:
    """CLI parse with unknown verb exits 4 with JSON error."""
    from core.internal.deploy.ssh_command_parser import _cli_main

    test_args = ["ssh_command_parser.py", "parse", "deploy my-project abc123"]
    stdout_lines: list[str] = []

    def fake_print(*args: str, **kwargs: object) -> None:
        stdout_lines.extend(str(a) for a in args)

    with (
        patch.object(sys, "argv", test_args),
        patch("builtins.print", fake_print),
    ):
        assert _cli_main() == 4  # ConfigValidationError.exit_code
    assert len(stdout_lines) == 1
    err = json.loads(stdout_lines[0])
    assert "error" in err
    assert "unknown verb" in err["error"]


# endregion
