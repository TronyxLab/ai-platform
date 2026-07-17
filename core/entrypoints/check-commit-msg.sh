#!/usr/bin/env bash
# GREP_SUMMARY: check-commit-msg, conventional-commits, validation, format, hook
# STRUCTURE: ┌commit_msg_file ($1)┐ → ◇ read → ◇ regex match → ⊕ exit 0 (allow) | exit 1 (block + error)
# region MODULE_CONTRACT
## @purpose — Pre-commit hook that validates commit messages against Conventional Commits 1.0.0 format.
##            Blocks commits with messages that do not match the required pattern.
## @io — $1 = path to commit message file → exit 0 (allow) / exit 1 (block with format guide)
## @complexity — O(1)
## @rationale — Consistent commit message format enables automated changelog generation and semantic versioning.
## @invariants
##   - Reads message from file path ($1)
##   - Validates against single regex
##   - Exits 0 for merge commits and revert commits
## @changes — 2026-07-10 | Moved from .git/hooks/commit-msg to core/entrypoints/ per TestsMetaDevPlan2.md TASK-2
##            2025-06-05 | Initial implementation (0.4.0)
# endregion MODULE_CONTRACT

set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

COMMIT_MSG_FILE="$1"

# Read commit message from file
COMMIT_MSG=$(cat "$COMMIT_MSG_FILE" 2>/dev/null || echo "")

# Skip validation for auto-generated commits (git merge / revert)
if echo "$COMMIT_MSG" | grep -qE '^(Merge|Revert) '; then
    exit 0
fi

# Conventional Commits 1.0.0 pattern
# Format: type(scope): description
# type: feat|fix|docs|style|refactor|test|chore|perf|ci|build|revert
# scope: optional, alphanumeric + / - _ .
# description: required text
PATTERN='^(feat|fix|docs|style|refactor|test|chore|perf|ci|build|revert)(\([a-zA-Z0-9._/-]+\))?: .+'

if echo "$COMMIT_MSG" | head -1 | grep -qE "$PATTERN"; then
    # Valid conventional commit
    exit 0
fi

# Invalid format — print error and help
FIRST_LINE=$(echo "$COMMIT_MSG" | head -1)
echo ""
echo "ERROR: Invalid commit message format." >&2
echo "" >&2
echo "  First line: $FIRST_LINE" >&2
echo "" >&2
echo "Commit messages must follow the Conventional Commits 1.0.0 format:" >&2
echo "" >&2
echo "  type(scope): description" >&2
echo "" >&2
echo "Allowed types: feat, fix, docs, style, refactor, test, chore, perf, ci, build, revert" >&2
echo "" >&2
echo "Examples:" >&2
echo "  feat(scanner): add doc-coverage metrics output" >&2
echo "  fix(detector): handle missing file references gracefully" >&2
echo "  docs(rules): add Config-Living-Doc section" >&2
echo "  test(compiler): add merge_sections dedup test" >&2
echo "  refactor(cli): simplify argument parsing" >&2
echo "" >&2
echo "To bypass this check, use: git commit --no-verify" >&2
echo "" >&2
exit 1
