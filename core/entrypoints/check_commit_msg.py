#!/usr/bin/env python3
# GREP_SUMMARY: check_commit_msg, conventional-commits, validation, format, hook
# STRUCTURE: ▶ ┌commit_msg_file (argv[1])┐ → ○ read → ◇ regex match → ⊕ exit 0 (allow) | exit 1 (block + error)
# region MODULE_CONTRACT
## @purpose  Git commit-msg hook — validates commit messages against Conventional Commits 1.0.0 format.
##           Blocks commits with messages that do not match the required pattern.
## @scope    Called as git commit-msg hook: python3 core/entrypoints/check_commit_msg.py <file>
##           Also importable: validate_commit_message(text) → (is_valid, error_message)
## @io       argv[1] = path to commit message file → exit 0 (allow) / exit 1 (block with format guide)
## @complexity — O(1) — single regex match on first line
## @invariants
##   - Reads message from file path (argv[1])
##   - Validates first non-empty line against Conventional Commits regex
##   - Exit 0 for merge commits (^Merge ) and revert commits (^Revert )
##   - Exit 1 with format guide on invalid message
##   - All diagnostic output goes to stderr
## @rationale  Python rewrite per Strangler-Fig language policy (AGENTS.md §Языковая политика).
##             Eliminates bash pattern: fragile echo|grep pipelines, set -euo pipefail edge cases.
##             Importable validate_commit_message() enables pytest unit-tests without subprocess.
## @changes — 2026-07-24 | Python rewrite: bash 70 LOC → Python ~100 LOC, importable API, unit-tests
##            2026-07-10 | Moved from .git/hooks/commit-msg to core/entrypoints/ (TestsMetaDevPlan2 TASK-2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

# Conventional Commits 1.0.0 pattern
# Format: type(scope): description
# type: feat|fix|docs|style|refactor|test|chore|perf|ci|build|revert
# scope: optional, alphanumeric + / - _ .
# description: required text
_PATTERN: re.Pattern = re.compile(
    r"^(feat|fix|docs|style|refactor|test|chore|perf|ci|build|revert)"
    r"(\([a-zA-Z0-9._/-]+\))?: .+"
)

# Auto-generated commits to skip
_SKIP_PREFIXES: tuple[str, ...] = ("Merge ", "Revert ")

_ALLOWED_TYPES: str = "feat, fix, docs, style, refactor, test, chore, perf, ci, build, revert"

_FORMAT_GUIDE: str = (
    "\n"
    "ERROR: Invalid commit message format.\n"
    "\n"
    "  First line: {first_line}\n"
    "\n"
    "Commit messages must follow the Conventional Commits 1.0.0 format:\n"
    "\n"
    "  type(scope): description\n"
    "\n"
    "Allowed types: {allowed_types}\n"
    "\n"
    "Examples:\n"
    "  feat(scanner): add doc-coverage metrics output\n"
    "  fix(detector): handle missing file references gracefully\n"
    "  docs(rules): add Config-Living-Doc section\n"
    "  test(compiler): add merge_sections dedup test\n"
    "  refactor(cli): simplify argument parsing\n"
    "\n"
    "To bypass this check, use: git commit --no-verify\n"
)


# region FUNC_validate_commit_message
def validate_commit_message(text: str) -> tuple[bool, str | None]:
    """Validate a commit message against Conventional Commits 1.0.0 format.

    ## @purpose  Pure function — validates the first non-empty line of a commit message.
    ##           Callable from both CLI (as git hook) and pytest (unit tests).
    ## @io       ⇥ text: str (full commit message) → ⎋ (is_valid: bool, error_message: str|None)
    ## @complexity — O(N) where N = length of first line
    ## @invariants
    ##   - Merge commits (^Merge ) → always valid
    ##   - Revert commits (^Revert ) → always valid
    ##   - Valid conventional commit format → valid
    ##   - Empty/malformed input → invalid with format guide
    """
    logger.info("[IMP:7][validate_commit_message] Validating commit message (%d chars)", len(text))

    first_line = text.split("\n")[0].strip()

    if not first_line:
        logger.info("[IMP:7][validate_commit_message] Empty message — invalid")
        err = _FORMAT_GUIDE.format(first_line=first_line, allowed_types=_ALLOWED_TYPES)
        return False, err

    # Skip validation for auto-generated commits (git merge / revert)
    if first_line.startswith(_SKIP_PREFIXES):
        logger.info("[IMP:7][validate_commit_message] Merge/revert commit — skipping validation")
        return True, None

    if _PATTERN.match(first_line):
        logger.info("[IMP:9][validate_commit_message] Commit message valid: %s", first_line)
        return True, None

    logger.info("[IMP:9][validate_commit_message] Commit message invalid — blocking: %s", first_line)
    err = _FORMAT_GUIDE.format(first_line=first_line, allowed_types=_ALLOWED_TYPES)
    return False, err


# endregion FUNC_validate_commit_message


# region FUNC_main
def main() -> None:
    """CLI entrypoint — git commit-msg hook interface.

    ## @purpose  Read commit message file from argv[1], validate, print diagnostics, exit 0/1.
    ## @io       argv[1] = path to commit message file → exit 0 (allow) / exit 1 (block)
    ## @invariants
    ##   - Exactly one argument required (commit message file path)
    ##   - All diagnostic output goes to stderr
    ##   - Exit code 0 = allow commit, 1 = block commit
    """
    if len(sys.argv) != 2:
        print("[IMP:10][check-commit-msg][main] Usage: check-commit-msg.py <commit-msg-file>", file=sys.stderr)
        sys.exit(1)

    commit_msg_file = Path(sys.argv[1])

    logger.info("[IMP:7][check-commit-msg][main] Validating commit message from: %s", commit_msg_file)

    # Read commit message from file
    try:
        text = commit_msg_file.read_text()
    except FileNotFoundError:
        logger.info("[IMP:9][check-commit-msg][main] Commit message file not found — allowing (empty commit?)")
        sys.exit(0)
    except OSError as exc:
        logger.info("[IMP:10][check-commit-msg][main] Cannot read commit message file: %s", exc)
        sys.exit(1)

    is_valid, error_msg = validate_commit_message(text)

    if is_valid:
        sys.exit(0)

    # Print error to stderr (git hook convention)
    print(error_msg, file=sys.stderr)
    sys.exit(1)


# endregion FUNC_main

if __name__ == "__main__":
    # Configure minimal logging for hook execution
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s][check-commit-msg] %(message)s",
        stream=sys.stderr,
    )
    main()
