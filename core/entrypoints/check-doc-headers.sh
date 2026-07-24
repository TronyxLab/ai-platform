#!/usr/bin/env bash
# GREP_SUMMARY: check-doc-headers, pre-commit, hook, doc-validation, grep-summary, module-contract, structure, regions
# STRUCTURE: ┌staged files┐ → ◇ filter (py|yaml|yml|sh|md) → ◇ validate GREP_SUMMARY presence+keywords → ◇ validate MODULE_CONTRACT → ◇ validate STRUCTURE → ◇ check #region/#endregion balance → ◇ check YAML @purpose → ⊕ exit 0/1
##
## @purpose — Pre-commit hook that validates documentation headers across staged files:
##            - GREP_SUMMARY: present + each keyword appears in file content
##            - MODULE_CONTRACT: #region MODULE_CONTRACT present
##            - STRUCTURE: # STRUCTURE: present
##            - #region/#endregion balance
##            - YAML files: # @purpose present
##            - .sh references in .md files resolve
## @io — Staged files from pre-commit (stdin) → exit 0 (all pass) / exit 1 (failures)
## @complexity — O(N*K) where N=files, K=keywords per file
## @invariants
##   - Runs only on staged files matching .(py|yaml|yml|sh|md)$
##   - Non-matching files are silently skipped
##   - Exits on first error? No — reports all errors across files
## @rationale — Replaces former grepsummary+presence-check from lint.sh.
##              Combines all doc-header validations in one script for CI efficiency.
## @changes — 2026-07-10 | Created per TestsMetaDevPlan2.md TASK-1

set -euo pipefail
echo "[IMP:7][check-doc-headers][main] Starting doc header validation" >&2
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

HAS_ERROR=0

# ─── Check if file has balanced #region/#endregion ───
check_regions_balanced() {
    local file="$1"
    local opens closes
    opens=$(grep -c '^[[:space:]]*# region' "$file" 2>/dev/null || true)
    closes=$(grep -c '^[[:space:]]*# endregion' "$file" 2>/dev/null || true)
    if [ "$opens" -ne "$closes" ]; then
        echo "[FAIL] $file: #region count ($opens) != #endregion count ($closes)"
        return 1
    fi
    return 0
}

# ─── Validate GREP_SUMMARY line present and keywords exist in file ───
check_grep_summary() {
    local file="$1"

    # Check presence
    # 🧐 TRAP[BUG] · 2026-07-22 · pipefail + head|grep -qE race
    # · Reason: `set -euo pipefail` + `head -5 | grep -qE` — timing-dependent:
    # ·   head exits 0 before grep finds match → pipefail=0 → ! 0 = 1 → false [FAIL]
    # ·   (grep -q closes stdin on match; head may get SIGPIPE 141 or exit 0 first)
    # · Fix: `grep -cE || true` — same pattern as check_regions_balanced()
    if [ "$(head -10 "$file" | grep -cE '# GREP_SUMMARY:' || true)" -eq 0 ]; then
        echo "[FAIL] $file: Missing '# GREP_SUMMARY:' in first 10 lines"
        return 1
    fi

    # Extract keywords (everything after '# GREP_SUMMARY:')
    local summary_line
    summary_line=$(head -10 "$file" | grep -E '# GREP_SUMMARY:' | head -1)
    local keywords
    keywords=$(echo "$summary_line" | sed 's/.*# GREP_SUMMARY:[[:space:]]*//' | tr ',' ' ')

    # 🧐 TRAP[BUG] · 2026-07-11 · SIGPIPE fix · echo "$file_content" | grep -qiF → grep -qiF "$kw" "$file"
    # · Reason: `set -o pipefail` + `grep -q` causes SIGPIPE (141) on large files — grep -q
    # ·   closes stdin on first match, echo gets SIGPIPE (141), pipefail propagates 141 → ! 141 = 0 → false [FAIL]
    # · Fix: grep reads file directly — no pipe, no SIGPIPE
    for kw in $keywords; do
        if [ -n "$kw" ]; then
            if ! grep -qiF -- "$kw" "$file"; then
                echo "[FAIL] $file: GREP_SUMMARY keyword '$kw' not found in file content"
                return 1
            fi
        fi
    done

    return 0
}

# ─── Check MODULE_CONTRACT region present ───
check_module_contract() {
    local file="$1"
    if ! grep -qE '# region MODULE_CONTRACT' "$file" 2>/dev/null; then
        echo "[FAIL] $file: Missing '# region MODULE_CONTRACT'"
        return 1
    fi
    if ! grep -qE '# endregion MODULE_CONTRACT' "$file" 2>/dev/null; then
        echo "[FAIL] $file: Missing '# endregion MODULE_CONTRACT'"
        return 1
    fi
    return 0
}

# ─── Check STRUCTURE line present ───
check_structure() {
    local file="$1"
    # 🧐 TRAP[BUG] · 2026-07-22 · pipefail + head|grep -qE race (same bug as check_grep_summary)
    # · Fix: `grep -cE || true` — pipefail-safe, same pattern as check_regions_balanced()
    if [ "$(head -10 "$file" | grep -cE '# STRUCTURE:' || true)" -eq 0 ]; then
        echo "[FAIL] $file: Missing '# STRUCTURE:' in first 10 lines"
        return 1
    fi
    return 0
}

# ─── Check YAML files have @purpose tag ───
check_yaml_purpose() {
    local file="$1"
    if ! grep -qE '## @purpose' "$file" 2>/dev/null; then
        echo "[FAIL] $file: Missing '## @purpose' tag (required for YAML files)"
        return 1
    fi
    return 0
}

# ─── Check .sh references in .md files resolve ───
# 🧐 TRAP[DECISION] · 2026-07-11 · — · check-doc-headers: deleted script refs in .md
# · Rejected: add skip-list for deleted scripts
# · Reason: skip-list masks documentation drift. Fix: (a) remove backticks from deleted
# ·   script names in .md, or (b) fix resolution logic. Both are better than a skip-list.
# · Rev: if a backtick .sh ref in .md can't resolve, fix either the doc or the resolver.
check_md_sh_refs() {
    local file="$1"
    # Extract .sh references from markdown files (backtick or inline code)
    local sh_refs
    # BSD (macOS) grep does not support -P; capability guard mirrors lint.sh
    if echo | grep -P '' >/dev/null 2>&1; then
        sh_refs=$(grep -oP '`[^`]+\.sh`' "$file" 2>/dev/null | tr -d '`' | sort -u || true)
    else
        # BSD fallback: -oE is portable across GNU and BSD grep
        sh_refs=$(grep -oE '`[^`]+\.sh`' "$file" 2>/dev/null | tr -d '`' | sort -u || true)
    fi

    if [ -z "$sh_refs" ]; then
        return 0
    fi

    local has_err=0
    while IFS= read -r ref; do
        # Skip absolute paths — validated on target machine, not locally
        [[ "$ref" == /* ]] && continue

        # Resolve relative to project root via known script dirs
        if [ -f "$ref" ] || [ -f "core/entrypoints/$ref" ] || [ -f "core/lib/$ref" ] || [ -f "core/bootstrap/$ref" ]; then
            continue
        fi

        # 🧐 TRAP[BUG-FIX] · 2026-07-21 · Handle `lib/<name>.sh` references in prose
        # · Reason: AGENTS.md / core/AGENTS.md reference libs as `lib/ssh.sh`, `lib/logging.sh`.
        # ·   Validator above tries `core/lib/lib/ssh.sh` (wrong). Strip leading `lib/` prefix
        # ·   and retry against core/lib/ to match the actual filesystem layout.
        if [[ "$ref" == lib/* ]]; then
            local stripped="${ref#lib/}"
            if [ -f "core/lib/$stripped" ]; then
                continue
            fi
        fi

        # Recursive search in core/internal/ (scripts can be at any depth)
        # 🧐 TRAP[BUG] · 2026-07-11 · SIGPIPE fix · обёртка в subshell с +o pipefail
        # · Reason: `set -o pipefail` + `grep -q` может вызвать SIGPIPE (141) —
        # ·   pipefail локализован subshell'ом, exit code pipe = exit code grep
        if (set +o pipefail; find core/internal/ -name "$ref" -maxdepth 5 2>/dev/null | grep -q .); then
            continue
        fi

        echo "[FAIL] $file: Referenced script '$ref' not found"
        has_err=1
    done <<< "$sh_refs"

    return $has_err
}

# ─── Main ───
for file in "$@"; do
    # Determine file extension
    ext="${file##*.}"

    # Skip non-target files
    case "$ext" in
        py|sh|md|yaml|yml) ;;
        *) continue ;;
    esac

    # Skip files in .venv, node_modules, __pycache__
    case "$file" in
        .venv/*|node_modules/*|__pycache__/*) continue ;;
    esac

    echo "[CHECK] $file"

    # GREP_SUMMARY presence + keywords — all files
    if ! check_grep_summary "$file"; then
        HAS_ERROR=1
    fi

    # STRUCTURE — all files
    if ! check_structure "$file"; then
        HAS_ERROR=1
    fi

    # MODULE_CONTRACT — only .py, .sh, .yaml, .yml
    if [ "$ext" != "md" ]; then
        if ! check_module_contract "$file"; then
            HAS_ERROR=1
        fi
    fi

    # Region balance — all files
    if ! check_regions_balanced "$file"; then
        HAS_ERROR=1
    fi

    # YAML @purpose — only .yaml/.yml
    if [ "$ext" = "yaml" ] || [ "$ext" = "yml" ]; then
        if ! check_yaml_purpose "$file"; then
            HAS_ERROR=1
        fi
    fi

    # .sh references in .md files
    if [ "$ext" = "md" ]; then
        if ! check_md_sh_refs "$file"; then
            HAS_ERROR=1
        fi
    fi
done

if [ "$HAS_ERROR" -ne 0 ]; then
    echo "[FAIL] check-doc-headers: One or more files failed documentation header validation"
    echo "[IMP:9][check-doc-headers][main] Validation FAILED — ${HAS_ERROR} error(s)" >&2
    exit 1
fi

echo "[PASS] check-doc-headers: All staged files passed documentation header validation"
echo "[IMP:9][check-doc-headers][main] Validation PASS — all headers OK" >&2
exit 0
