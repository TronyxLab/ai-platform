# GREP_SUMMARY: test-secrets-env-parser, secrets-env-parser, parse, write, merge, export-shell, tmp-path, ldd
# STRUCTURE: ┌tmp_path fixtures┐ → ○ 12 test scenarios: parse/export/quotes/comments/write/merge/empty/inline/spaces/emptykey/unicode/prefix
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/secrets_env_parser.py
##           Verifies parse, write, merge, export_shell, and _parse_line functions
##           with all edge cases: export prefix, quotes, comments, atomic write, merge, etc.
## @scope    Tests: 12 scenarios covering all 4 public functions + 1 private function behavior.
##           All tests use tmp_path (no hardcoded paths). Pure Python — no Docker dependency.
## @invariants
##   - All tests use tmp_path (no hardcoded paths, no hardcoded absolute paths)
##   - No Docker dependency (pure Python)
##   - LDD: at least one IMP:9 log in each successful business-logic test
##   - No subprocess calls — native Python imports only
##   - TRAP[TEST] annotation on every test function
# endregion MODULE_CONTRACT

import logging
import stat
from pathlib import Path

import pytest

from core.internal.shared.secrets_env_parser import (
    _parse_line,
    export_shell,
    merge,
    parse,
    write,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

# ── LDD helper ────────────────────────────────────────────────────────────────


def _print_ldd(caplog: pytest.LogCaptureFixture) -> bool:
    """Print IMP:7-10 log trajectory and return True if any IMP:9 log found.

    ## @purpose — Centralized LDD trajectory printer for all test functions.
    ##            Prints filtered logs to stdout, returns IMP:9 presence flag.
    ## @io — ⇥ caplog → ⎋ bool (IMP:9 found)
    """
    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")
    return found_imp9


# ── Tests ─────────────────────────────────────────────────────────────────────


# region FUNC_test_parse_with_export
## @purpose — Verify parse() strips 'export ' prefix from lines.
##            AC: export VAR=value → {'VAR': 'value'}
def test_parse_with_export(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must strip 'export ' prefix from lines."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: 'export ' prefix stripping
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes export prefix handling

    env_file = tmp_path / "secrets.env"
    env_file.write_text("export VAR=value\n")

    result = parse(str(env_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {"VAR": "value"}, f"Expected {{'VAR': 'value'}}, got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_with_export


# region FUNC_test_parse_with_quotes
## @purpose — Verify parse() strips surrounding single and double quotes from values.
##            AC: VAR='val' → {'VAR': 'val'}, VAR="val" → {'VAR': 'val'}
def test_parse_with_quotes(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must strip surrounding single and double quotes from values."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: quote stripping from values
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes quote handling

    env_file = tmp_path / "secrets.env"
    env_file.write_text("SINGLE='hello'\nDOUBLE=\"world\"\n")

    result = parse(str(env_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {"SINGLE": "hello", "DOUBLE": "world"}, f"Expected both unquoted, got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_with_quotes


# region FUNC_test_parse_with_comments
## @purpose — Verify parse() skips full-line comments.
##            AC: only '# comment' lines → empty dict
def test_parse_with_comments(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must skip full-line comments and return empty dict if only comments."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: full-line comment filtering
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes comment handling

    env_file = tmp_path / "secrets.env"
    env_file.write_text("# This is a comment\n# ANOTHER=ignored\n")

    result = parse(str(env_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {}, f"Expected empty dict for comment-only file, got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_with_comments


# region FUNC_test_write_atomic
## @purpose — Verify write() atomically creates file with correct permissions and content.
##            AC: file created, mode == 0o600, content matches dict lines
def test_write_atomic(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """write() must atomically create file with correct content and permissions."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: atomic write with tempfile+rename
    # · Last fail: N/A (new test)
    # · Remove if: write() changes atomic write mechanism

    env_file = tmp_path / "secrets.env"
    data = {"KEY1": "val1", "KEY2": "val2"}

    assert not env_file.exists(), "Precondition: file must not exist before write"
    write(str(env_file), data)

    found_imp9 = _print_ldd(caplog)

    # File exists check
    assert env_file.exists(), "File must exist after write"

    # Content check
    content = env_file.read_text(encoding="utf-8")
    assert "KEY1=val1\n" in content, "Content must contain KEY1=val1"
    assert "KEY2=val2\n" in content, "Content must contain KEY2=val2"

    # Permission check (owner read/write only)
    file_stat = Path(env_file).stat()
    actual_mode = stat.S_IMODE(file_stat.st_mode)
    assert actual_mode == 0o600, f"Expected mode 0o600, got 0{actual_mode:o}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_write_atomic


# region FUNC_test_merge_override
## @purpose — Verify merge() with last-wins semantics.
##            AC: file1 {A:1, B:2}, file2 {B:3, C:4} → {A:1, B:3, C:4}
def test_merge_override(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """merge() must apply last-wins semantics for duplicate keys."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: last-wins merge of multiple files
    # · Last fail: N/A (new test)
    # · Remove if: merge() changes merge semantics

    base_file = tmp_path / "base.env"
    override_file = tmp_path / "override.env"
    base_file.write_text("A=1\nB=2\n")
    override_file.write_text("B=3\nC=4\n")

    result = merge(str(base_file), str(override_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {"A": "1", "B": "3", "C": "4"}, f"Expected last-wins merge, got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_merge_override


# region FUNC_test_empty_file
## @purpose — Verify parse() of empty file returns empty dict.
##            AC: empty file → {}
def test_empty_file(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must return empty dict for an empty file."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: empty file returns empty dict
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes empty file behavior

    env_file = tmp_path / "empty.env"
    env_file.write_text("")

    result = parse(str(env_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {}, f"Expected empty dict for empty file, got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_empty_file


# region FUNC_test_inline_comments
## @purpose — Verify parse() strips inline comments from values.
##            AC: 'VAR=value # comment' → {'VAR': 'value'}
def test_inline_comments(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must strip inline comments (# ...) from values."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: inline comment stripping
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes inline comment handling

    env_file = tmp_path / "secrets.env"
    env_file.write_text("VAR=value # this is a comment\n")

    result = parse(str(env_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {"VAR": "value"}, f"Expected {{'VAR': 'value'}} with comment stripped, got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_inline_comments


# region FUNC_test_mixed_quotes
## @purpose — Verify parse() handles both quote styles in one file with hash inside quotes.
##            AC: single/double quotes both work, hash inside quotes preserved
def test_mixed_quotes(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must handle both quote styles and preserve # inside quoted values."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: mixed single/double quotes with hash inside
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes quote handling

    env_file = tmp_path / "secrets.env"
    env_file.write_text("SINGLE_Q='value with # hash'\nDOUBLE_Q=\"value with # hash\"\nNO_QUOTE=plain\n")

    result = parse(str(env_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {
        "SINGLE_Q": "value with # hash",
        "DOUBLE_Q": "value with # hash",
        "NO_QUOTE": "plain",
    }, f"Expected # preserved inside quotes, got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_mixed_quotes


# region FUNC_test_spaces_around_eq
## @purpose — Verify parse() handles spaces around the '=' sign.
##            AC: 'VAR = value' → {'VAR': 'value'}
def test_spaces_around_eq(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must handle spaces around the = sign."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: spaces around '=' sign
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes whitespace handling around '='

    env_file = tmp_path / "secrets.env"
    env_file.write_text("VAR = spaced value\n")

    result = parse(str(env_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {"VAR": "spaced value"}, f"Expected key='VAR', value='spaced value', got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_spaces_around_eq


# region FUNC_test_empty_key_eq
## @purpose — Verify parse() handles KEY= with empty value.
##            AC: 'VAR=' → {'VAR': ''}
def test_empty_key_eq(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must handle VAR= with empty value."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: key with '=' and no value
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes empty-value handling

    env_file = tmp_path / "secrets.env"
    env_file.write_text("VAR=\n")

    result = parse(str(env_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {"VAR": ""}, f"Expected {{'VAR': ''}} for empty value, got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_empty_key_eq


# region FUNC_test_unicode
## @purpose — Verify parse() handles unicode characters in values.
##            AC: 'VAR=привет' → {'VAR': 'привет'}
def test_unicode(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must handle unicode characters in values."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: unicode characters in value
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes encoding handling

    env_file = tmp_path / "secrets.env"
    env_file.write_text("VAR=привет\nUNICODE=日本語\n")

    result = parse(str(env_file))

    found_imp9 = _print_ldd(caplog)
    assert result == {"VAR": "привет", "UNICODE": "日本語"}, f"Expected unicode values preserved, got {result}"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_unicode


# region FUNC_test_prefix_filter
## @purpose — Verify parse() with prefix_filter returns only matching keys.
##            AC: parse(prefix_filter='DB_') → only keys starting with 'DB_'
def test_prefix_filter(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse(prefix_filter='DB_') must return only keys matching the prefix."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: prefix_filter parameter filters output
    # · Last fail: N/A (new test)
    # · Remove if: prefix_filter parameter is removed from parse()

    env_file = tmp_path / "secrets.env"
    env_file.write_text("DB_HOST=localhost\nDB_PORT=5432\nAPI_KEY=secret\nDB_NAME=testdb\n")

    result = parse(str(env_file), prefix_filter="DB_")

    found_imp9 = _print_ldd(caplog)
    assert result == {"DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "testdb"}, (
        f"Expected only DB_ prefixed keys, got {result}"
    )
    assert "API_KEY" not in result, "API_KEY must be filtered out"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_prefix_filter


# region FUNC_test_export_shell
## @purpose — Verify export_shell() generates proper shell-compatible output.
##            AC: output lines start with 'export ', values single-quoted, embedded
##            quotes escaped as '\''
def test_export_shell(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """export_shell() must generate source-able shell output with proper escaping."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: export_shell output format
    # · Last fail: N/A (new test)
    # · Remove if: export_shell() changes output format

    env_file = tmp_path / "secrets.env"
    env_file.write_text("VAR=simple\nQUOTED=it's a test\n")

    output = export_shell(str(env_file))

    found_imp9 = _print_ldd(caplog)

    # Verify format
    assert output.startswith("export "), "Output must start with 'export '"
    assert "export VAR='simple'" in output, "Simple value must be export VAR='simple'"
    assert "export QUOTED='it'\\''s a test'" in output, "Single quote must be escaped as '\\''"
    assert output.endswith("\n"), "Output must end with newline"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_export_shell


# region FUNC_test_parse_file_not_found
## @purpose — Verify parse() raises FileNotFoundError for missing file.
##            AC: non-existent path → FileNotFoundError
def test_parse_file_not_found(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """parse() must raise FileNotFoundError when file does not exist."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: missing file raises FileNotFoundError
    # · Last fail: N/A (new test)
    # · Remove if: parse() changes missing-file behavior

    missing_path = tmp_path / "nonexistent.env"

    with pytest.raises(FileNotFoundError) as exc_info:
        parse(str(missing_path))

    found_imp9 = _print_ldd(caplog)
    assert "nonexistent.env" in str(exc_info.value), (
        f"Error message must reference the file path, got: {exc_info.value}"
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_file_not_found


# region FUNC_test_parse_line_comprehensive
## @purpose — Verify _parse_line() private function handles all edge cases directly.
##            AC: various inputs produce correct (key, value) tuples or None.
def test_parse_line_comprehensive(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """_parse_line() must handle all line-level edge cases correctly."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: comprehensive _parse_line edge cases
    # · Last fail: N/A (new test)
    # · Remove if: _parse_line() internal logic changes fundamentally

    # ── Empty / comment → None ──
    assert _parse_line("") is None, "Empty line must return None"
    assert _parse_line("   ") is None, "Whitespace-only line must return None"
    assert _parse_line("# comment") is None, "Comment line must return None"
    assert _parse_line("  # indented comment") is None, "Indented comment must return None"

    # ── Standard ──
    assert _parse_line("KEY=value") == ("KEY", "value"), "Standard key=value"
    assert _parse_line("export KEY=value") == ("KEY", "value"), "Export prefix stripped"
    assert _parse_line("export   KEY=value") == ("KEY", "value"), "Export prefix + extra spaces"

    # ── Quotes ──
    assert _parse_line("KEY='quoted'") == ("KEY", "quoted"), "Single quotes stripped"
    assert _parse_line('KEY="quoted"') == ("KEY", "quoted"), "Double quotes stripped"

    # ── Inline comment ──
    assert _parse_line("KEY=value # comment") == ("KEY", "value"), "Inline comment stripped"

    # ── Spaces ──
    assert _parse_line("KEY = value") == ("KEY", "value"), "Spaces around ="

    # ── Empty value ──
    assert _parse_line("KEY=") == ("KEY", ""), "Empty value"

    # ── No = sign ──
    assert _parse_line("NOEQUALS") is None, "Line without = must return None"

    # ── Hash inside quotes preserved ──
    assert _parse_line("KEY='val # ue'") == ("KEY", "val # ue"), "Hash inside single quotes"
    assert _parse_line('KEY="val # ue"') == ("KEY", "val # ue"), "Hash inside double quotes"

    # ── Unicode ──
    assert _parse_line("KEY=привет") == ("KEY", "привет"), "Unicode value"

    # ── Call parse() to generate an IMP:9 business logic log ──
    # _parse_line() only logs at IMP:7; parse() generates IMP:9.
    env_file = tmp_path / "comprehensive.env"
    env_file.write_text("ROUNDTRIP=ok\n")
    roundtrip = parse(str(env_file))
    assert roundtrip == {"ROUNDTRIP": "ok"}, "parse roundtrip must work"

    found_imp9 = _print_ldd(caplog)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_parse_line_comprehensive
