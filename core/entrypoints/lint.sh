#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint lint grepsummary validation pre-commit
# STRUCTURE: ▶ init → ○ loop staged files → ◇ validate GREP_SUMMARY + .sh refs → ⊕ exit 0/1
# region MODULE_CONTRACT
## @purpose  Entry-point for pre-commit grepsummary/namelint hooks only
## @scope    Called from .pre-commit-config.yaml and potentially from Makefile
## @invariants
##   - grepsummary mode: validates GREP_SUMMARY keywords exist in their files
##   - .sh references in .md files must point to real files
##   - Exit 0 on success, exit 1 with descriptive messages on failure
## @rationale Self-validating codebase — every GREP_SUMMARY keyword must be real
# endregion MODULE_CONTRACT

set -euo pipefail

_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"
PLATFORM_ROOT="$(cd "$PATHS_CORE_DIR/.." && pwd)"
ERRORS=0

# ── Color helpers ──
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }

# ── grepsummary: validate GREP_SUMMARY lines ──
check_grepsummary() {
    local file="$1"
    local line="$2"
    # Extract keywords after "GREP_SUMMARY:" (comma/space separated)
    # shellcheck disable=SC2001
    local keywords; keywords=$(echo "$line" | sed 's/.*GREP_SUMMARY:\s*//' | tr ',' ' ')

    for kw in $keywords; do
        # Strip HTML comment markers, hash, punctuation
        kw="${kw//\#/}"
        kw="${kw//-->/}"
        kw="${kw//<!--/}"
        # Skip empty, flags (starts with -), or HTML artifacts
        [ -z "$kw" ] && continue
        [[ "$kw" == -* ]] && continue
        [[ "$kw" == "--"* ]] && continue
        # Treat keyword as literal string
        if ! grep -qiF "$kw" "$file"; then
            red "[FAIL] GREP_SUMMARY keyword '$kw' not found in $file"
            ERRORS=$((ERRORS + 1))
        fi
    done
}

# ── sh-refs: validate .sh references in .md files ──
check_sh_refs_in_md() {
    local file="$1"
    # Find all .sh references in markdown (excluding URLs, code blocks)
    local refs
    # Portable: detect GNU grep (supports -P) vs BSD grep (macOS)
    if echo | grep -P '' >/dev/null 2>&1; then
        refs=$(grep -oP '(?<!\S)([\w./-]+\.sh)(?!\S)' "$file" 2>/dev/null || true)
    else
        refs=$(grep -oE '[\w./-]+\.sh' "$file" 2>/dev/null | grep -v '^http' || true)
    fi
    [ -z "$refs" ] && return

    while IFS= read -r ref; do
        # Skip if it looks like a URL path (starts with http)
        [[ "$ref" =~ ^http ]] && continue
        # Skip deployment paths (/opt/...), prose mentions (no /), and parent-dir references (..)
        [[ "$ref" =~ ^/opt/ ]] && continue
        [[ "$ref" != */* ]] && continue
        [[ "$ref" == *..* ]] && continue
        # Resolve relative to platform root
        local resolved="$PLATFORM_ROOT/$ref"
        if [ ! -f "$resolved" ] && [ ! -f "$ref" ]; then
            red "[FAIL] .sh reference '$ref' in $file -> file not found"
            ERRORS=$((ERRORS + 1))
        fi
    done <<< "$refs"
}

# ── namelint: validate make target names against manifest allowed_verbs ──
## @purpose  Validate that every .PHONY make target in the root Makefile is either in the manifest's allowed_verbs list, matches a system exception pattern, or is not in the forbidden_verbs list.
## @io       Input: core/entrypoint-manifest.yaml (allowed_verbs + forbidden_verbs), root Makefile (.PHONY targets)
## @complexity O(n) — linear scan of targets against two YAML-derived lists
check_namelint() {
    local manifest="$PLATFORM_ROOT/core/entrypoint-manifest.yaml"
    local makefile="$PLATFORM_ROOT/Makefile"

    if [ ! -f "$manifest" ]; then
        red "[FAIL] Manifest not found: $manifest"
        ERRORS=$((ERRORS + 1))
        return
    fi

    if [ ! -f "$makefile" ]; then
        red "[FAIL] Root Makefile not found: $makefile"
        ERRORS=$((ERRORS + 1))
        return
    fi

    # Parse allowed_verbs from manifest YAML (list after `allowed_verbs:` key, lines starting with "  - ")
    local allowed
    allowed=$(awk '/^allowed_verbs:/{found=1; next} found && /^  - /{gsub(/^  - /,""); print; next} found && /^[^ ]/ && !/^  - /{found=0}' "$manifest")

    # Parse module_lifecycle from manifest YAML (G1.3 — module targets like start, stop, etc.)
    local module_lifecycle
    module_lifecycle=$(awk '/^module_lifecycle:/{found=1; next} found && /^  - /{gsub(/^  - /,""); print; next} found && /^[^ ]/ && !/^  - /{found=0}' "$manifest")

    # Parse forbidden_verbs from manifest YAML
    local forbidden
    forbidden=$(awk '/^forbidden_verbs:/{found=1; next} found && /^  - /{gsub(/^  - /,""); print; next} found && /^[^ ]/ && !/^  - /{found=0}' "$manifest")

    # Parse .PHONY targets from root Makefile (one or more .PHONY: lines)
    local targets
    targets=$(grep '^.PHONY:' "$makefile" 2>/dev/null | sed 's/^.PHONY: *//' | tr ' ' '\n' | sed '/^$/d')

    if [ -z "$targets" ]; then
        red "[FAIL] No .PHONY targets found in root Makefile"
        ERRORS=$((ERRORS + 1))
        return
    fi

    local target
    while IFS= read -r target; do
        [ -z "$target" ] && continue

        # Check if target is in forbidden_verbs
        if echo "$forbidden" | grep -qxF "$target"; then
            red "[FAIL] Target '$target' is FORBIDDEN (listed in manifest forbidden_verbs)"
            ERRORS=$((ERRORS + 1))
            continue
        fi

        # Check if target is in allowed_verbs
        if echo "$allowed" | grep -qxF "$target"; then
            continue
        fi

        # Check if target is in module_lifecycle (G1.3 — module targets like start, stop, ...)
        if echo "$module_lifecycle" | grep -qxF "$target"; then
            continue
        fi

        # Check system exceptions (always allowed by pattern)
        case "$target" in
            test-*|gate-*|pre-commit-*)
                continue
                ;;
            help|venv)
                continue
                ;;
        esac

        # Not allowed, not a system exception
        red "[FAIL] Target '$target' is not in allowed_verbs and not a system exception"
        ERRORS=$((ERRORS + 1))
    done <<< "$targets"
}

# ── Main ──
case "${1:-}" in
    --help|-h)
        echo "Usage: $0 {grepsummary|namelint}"
        echo ""
        echo "Lint validation modes:"
        echo "  grepsummary   Validate GREP_SUMMARY keywords and .sh references"
        echo "  namelint      Validate make target names against manifest allowed_verbs"
        exit 0
        ;;
    grepsummary)
        echo "[lint.sh] Running GREP_SUMMARY validation..."
        # Scan all tracked files for GREP_SUMMARY lines
        # Use git ls-files to stay within repo
        if git rev-parse --git-dir > /dev/null 2>&1; then
            files=$(git ls-files)
        else
            files=$(find "$PLATFORM_ROOT" -type f \
                -not -path '*/node_modules/*' \
                -not -path '*/.git/*' \
                -not -path '*/__pycache__/*' \
                -not -name '*.pyc' 2>/dev/null)
        fi

        while IFS= read -r f; do
            [ -z "$f" ] && continue
            [ ! -f "$f" ] && continue

            # Check GREP_SUMMARY header lines only (# GREP_SUMMARY: or <!-- GREP_SUMMARY:)
            while IFS= read -r gs_line; do
                check_grepsummary "$f" "$gs_line"
            done < <(grep -E '^# GREP_SUMMARY:|^<!-- GREP_SUMMARY:' "$f" 2>/dev/null || true)

            # Check .sh references in .md files
            if [[ "$f" == *.md ]]; then
                check_sh_refs_in_md "$f"
            fi
        done <<< "$files"

        if [ "$ERRORS" -gt 0 ]; then
            red "[lint.sh] FAILED — $ERRORS error(s) found"
            exit 1
        fi
        green "[lint.sh] PASS — all GREP_SUMMARY keywords verified, .sh references valid"
        exit 0
        ;;
    namelint)
        echo "[lint.sh] Running namelint validation..."
        check_namelint
        if [ "$ERRORS" -gt 0 ]; then
            red "[lint.sh] FAILED — $ERRORS error(s) found"
            exit 1
        fi
        green "[lint.sh] PASS — all make targets validated against manifest"
        exit 0
        ;;
    *)
        echo "Usage: $0 {grepsummary|namelint}"
        echo "  grepsummary   Validate GREP_SUMMARY keywords and .sh references"
        echo "  namelint      Validate make target names against manifest allowed_verbs"
        exit 1
        ;;
esac
