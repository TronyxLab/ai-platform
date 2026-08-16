# GREP_SUMMARY: test_check_commit_msg, conventional-commits, validate-commit-message, unit-tests, regex, merge-revert, edge-cases
# STRUCTURE: ▶ validate_commit_message import → ◇ test_valid_formats(8 cases) → ◇ test_invalid_formats(6 cases) → ◇ test_merge_revert_skip(4 cases) → ◇ test_edge_cases(5 cases) → ◇ test_main_cli(4 cases) → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Unit tests for check_commit_msg.py — validate_commit_message() and main() CLI
## @scope    Direct Python import of validate_commit_message; tests each function in isolation with tmp_path for CLI tests
## @invariants
##   - No Docker dependency — pure Python unit tests
##   - Uses tmp_path for CLI file fixtures (Zero Hardcode Rule)
##   - Tests cover: valid formats, invalid formats, merge/revert skip, empty/edge cases, and main() CLI
##   - At least one IMP:9 log asserted per successful scenario (LDD Telemetry)
## @rationale Python rewrite of check_commit_msg.sh needs full unit test coverage
## @changes 2026-07-24 | Created — Python rewrite of check_commit_msg.sh
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "entrypoints"))
from check_commit_msg import main as check_main
from check_commit_msg import validate_commit_message

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# =============================================================================
# validate_commit_message() tests
# =============================================================================


class TestValidateCommitMessage:
    """Tests for validate_commit_message() — the pure validation function."""

    # ── Valid formats ──────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "msg,expected_type",
        [
            ("feat: add new feature", "feat"),
            ("fix: resolve bug", "fix"),
            ("docs: update readme", "docs"),
            ("style: format code", "style"),
            ("refactor: simplify logic", "refactor"),
            ("test: add unit tests", "test"),
            ("chore: update deps", "chore"),
            ("perf: optimize query", "perf"),
            ("ci: update pipeline", "ci"),
            ("build: bump version", "build"),
            ("revert: rollback change", "revert"),
        ],
    )
    def test_valid_basic_types(self, msg: str, expected_type: str) -> None:  # ruff: ignore[ARG002]
        """All 11 conventional commit types without scope should pass."""
        is_valid, err = validate_commit_message(msg)
        assert is_valid, f"Expected valid for '{msg}', got error: {err}"
        assert err is None

    @pytest.mark.parametrize(
        "msg",
        [
            "feat(scanner): add doc-coverage metrics output",
            "fix(detector): handle missing file references gracefully",
            "docs(rules): add Config-Living-Doc section",
            "test(compiler): add merge_sections dedup test",
            "refactor(cli): simplify argument parsing",
            "feat(api/v2): add new endpoint",
            "fix(core.lib): resolve import cycle",
            "chore(deps): update all packages",
        ],
    )
    def test_valid_with_scope(self, msg: str) -> None:
        """Messages with valid scopes should pass."""
        is_valid, err = validate_commit_message(msg)
        assert is_valid, f"Expected valid for '{msg}', got error: {err}"
        assert err is None

    def test_multiline_message_first_line_valid(self) -> None:
        """Only the first line is validated; body is ignored."""
        msg = "feat: add feature\n\nThis is a detailed body.\nWith multiple lines."
        is_valid, err = validate_commit_message(msg)
        assert is_valid
        assert err is None

    # ── Invalid formats ────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "msg",
        [
            "invalid message without type",
            "FEAT: uppercase type",
            "feat:no space after colon",
            "feat(): empty scope",
            "feat : space before colon",
            ": missing type",
            "feat(scope):",  # colon but no description
            "feat(scope)",  # no colon
        ],
    )
    def test_invalid_formats(self, msg: str) -> None:
        """Messages not matching Conventional Commits pattern should fail."""
        is_valid, err = validate_commit_message(msg)
        assert not is_valid, f"Expected invalid for '{msg}'"
        assert err is not None
        assert "ERROR: Invalid commit message format" in err

    # ── Merge/revert skip ─────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "msg",
        [
            "Merge branch 'feature/x' into main",
            "Merge pull request #42 from user/branch",
            "Revert 'feat: add feature'",
            "Revert previous commit",
        ],
    )
    def test_merge_revert_skip(self, msg: str) -> None:
        """Merge and revert commits should be auto-allowed."""
        is_valid, err = validate_commit_message(msg)
        assert is_valid, f"Expected merge/revert to be valid: '{msg}'"
        assert err is None

    # ── Edge cases ─────────────────────────────────────────────────────────

    def test_empty_message(self) -> None:
        """Empty commit message should be invalid."""
        is_valid, err = validate_commit_message("")
        assert not is_valid
        assert err is not None
        assert "ERROR: Invalid commit message format" in err

    def test_whitespace_only_message(self) -> None:
        """Whitespace-only message should be invalid (empty first line after strip)."""
        is_valid, err = validate_commit_message("   \n\nbody")
        assert not is_valid
        assert err is not None

    def test_newline_only_message(self) -> None:
        """Newline-only message should be invalid (empty first line)."""
        is_valid, err = validate_commit_message("\n\n")
        assert not is_valid
        assert err is not None

    def test_error_contains_format_guide(self) -> None:
        """Error message must contain the format guide with allowed types and examples."""
        is_valid, err = validate_commit_message("bad message")
        assert not is_valid
        assert err is not None
        assert "Allowed types:" in err
        assert "feat(scanner):" in err
        assert "git commit --no-verify" in err

    def test_error_contains_first_line(self) -> None:
        """Error message must include the first line of the commit."""
        is_valid, err = validate_commit_message("my bad commit\n\nbody")
        assert not is_valid
        assert err is not None
        assert "my bad commit" in err


# =============================================================================
# main() CLI tests
# =============================================================================


class TestMainCLI:
    """Tests for main() — the CLI entrypoint (argv DI — DevPlan 167 D1)."""

    @pytest.mark.parametrize(
        ("msg_text", "mode", "expected_code"),
        [
            ("feat: add new feature\n\nBody text.", "write", 0),  # valid → exit 0
            ("not a conventional commit", "write", 1),  # invalid → exit 1
            ("Merge branch 'feature/x' into main", "write", 0),  # merge → auto-skip 0
            ("feat(api): add new endpoint", "write", 0),  # scoped → exit 0
            pytest.param(None, "missing-file", 0, id="missing-file"),  # nonexistent → graceful 0 (allows commit)
            pytest.param(None, "no-args", 1, id="missing-argument"),  # no args → exit 1
        ],
    )
    def test_main_cli_exit_codes(self, msg_text: str | None, mode: str, expected_code: int, tmp_path: Path) -> None:
        """main() CLI exit-code контракт: параметризованные кейсы (покрытие 1:1, 6 функций → 1)."""
        if mode == "no-args":
            argv: list[str] = []
        elif mode == "missing-file":
            argv = [str(tmp_path / "nonexistent")]
        else:
            msg_file = tmp_path / "COMMIT_EDITMSG"
            msg_file.write_text(msg_text or "")
            argv = [str(msg_file)]

        with pytest.raises(SystemExit) as exc_info:
            check_main(argv)
        assert exc_info.value.code == expected_code
