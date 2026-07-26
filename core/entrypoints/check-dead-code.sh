#!/usr/bin/env bash
# GREP_SUMMARY: check-dead-code, gate, DEPRECATED, markers, stale, 30-days, CI
# STRUCTURE: ▶ find DEPRECATED markers in .sh/.py → ○ for each: git log --diff-filter=A → ◇ age > 30d ? → FAIL → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  CI gate: detect DEPRECATED markers older than 30 days in project .sh and .py files.
##           A DEPRECATED marker is a migration signal: "this will be removed." After 30 days,
##           the grace period is over and the marker itself is dead code.
## @scope    All .sh and .py files under project root (excluding .venv, .git, .ai, self)
## @io       stdout: list of stale DEPRECATED markers with age; exit 0 = clean, 1 = violations
## @invariants
##   - Uses grep -w "DEPRECATED" to match only whole-word occurrences (avoids compound identifiers)
##   - Self-excluding: check-dead-code.sh is not scanned (circular self-reference)
##   - Excludes .venv, .git, .ai directories
##   - For each hit, uses git blame to find when the line was added
##   - Untracked/new files use file mtime (not epoch) for age calculation
##   - 30-day threshold = 2592000 seconds
## @rationale  Dead code misleads agents — they read it as source of truth (RC-4).
##             Every DEPRECATED marker older than 30 days that hasn't been removed
##             is architectural debt with an expiry date already passed.
# endregion MODULE_CONTRACT

set -euo pipefail

SELF_REL="core/entrypoints/check-dead-code.sh"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VIOLATIONS=0

# ── Find DEPRECATED markers ──────────────────────────────────────────
echo "[IMP:8][check-dead-code] Scanning for DEPRECATED markers in .sh and .py files..." >&2

while IFS= read -r -d '' file; do
    rel="${file#$PROJECT_ROOT/}"

    # Skip self (circular self-reference)
    [[ "$rel" == "$SELF_REL" ]] && continue

    # Find lines containing whole-word "DEPRECATED" (avoids compound identifiers like _DEPRECATED_PATTERNS)
    while IFS= read -r line; do
        line_num="$(echo "$line" | cut -d: -f1)"
        line_text="$(echo "$line" | cut -d: -f2-)"

        # Determine when this line was added via git blame
        add_timestamp=""
        if git -C "$PROJECT_ROOT" log --oneline -- "$rel" 2>/dev/null | head -1 | grep -q .; then
            # File is tracked in git — use git blame to find the commit that added this line
            blame_output=$(git -C "$PROJECT_ROOT" blame -L "${line_num},${line_num}" --porcelain "$rel" 2>/dev/null || true)
            if [[ -n "$blame_output" ]]; then
                # Extract committer-timestamp from blame porcelain
                add_timestamp=$(echo "$blame_output" | grep "^committer-time " | head -1 | awk '{print $2}')
            fi
        fi

        # If git blame produced no timestamp, use file modification time
        if [[ -z "$add_timestamp" ]]; then
            add_timestamp=$(stat -f "%m" "$file" 2>/dev/null || echo "0")
        fi

        now=$(date +%s)
        age=$((now - add_timestamp))
        age_days=$((age / 86400))

        if [[ $age_days -gt 30 ]]; then
            echo "[IMP:10][check-dead-code] STALE: ${rel}:${line_num} — marker is ${age_days} days old (threshold: 30)"
            echo "  >>> ${line_text:0:120}"
            VIOLATIONS=$((VIOLATIONS + 1))
        else
            echo "[IMP:7][check-dead-code] OK: ${rel}:${line_num} — marker is ${age_days}d old (within 30d grace)"
        fi
    done < <(grep -wn "DEPRECATED" "$file" 2>/dev/null || true)

done < <(find "$PROJECT_ROOT" \( -name "*.sh" -o -name "*.py" \) \
    -not -path "$PROJECT_ROOT/.venv/*" \
    -not -path "$PROJECT_ROOT/.git/*" \
    -not -path "$PROJECT_ROOT/.ai/*" \
    -not -path "*/node_modules/*" \
    -print0 2>/dev/null)

# ── Report ───────────────────────────────────────────────────────────
if [[ $VIOLATIONS -gt 0 ]]; then
    echo "[IMP:10][check-dead-code] FAIL: ${VIOLATIONS} marker(s) exceed 30-day grace period" >&2
    echo "[IMP:10][check-dead-code] Fix: remove stale markers or update if still active" >&2
    exit 1
fi

echo "[IMP:9][check-dead-code] PASS: All DEPRECATED markers are within 30-day grace period" >&2
exit 0
