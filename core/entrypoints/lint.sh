#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint lint grepsummary namelint validation pre-commit facade python3
# STRUCTURE: ▶ init → ◇ case mode → ⊕ exec python3 -m (grepsummary|namelint) → ⎋ exit 0/1
# region MODULE_CONTRACT
## @purpose  Thin facade — delegates grepsummary/namelint to Python modules (DevPlan 106)
## @scope    Called from .pre-commit-config.yaml (name-linter hook) and manual/CI usage
## @invariants No business logic — delegation only; python3 -m (no -c/heredoc); exec passthrough
## @rationale Strangler-Fig: 238 LOC → ≤40 LOC facade; business logic in Python
# endregion MODULE_CONTRACT

set -euo pipefail
echo "[IMP:7][lint][main] Starting lint entrypoint" >&2
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/paths.sh"
PLATFORM_ROOT="$(cd "$PATHS_CORE_DIR/.." && pwd)"
cd "$PLATFORM_ROOT"

# ── Color helpers ──
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }

case "${1:-}" in
    --help|-h)
        echo "Usage: $0 {grepsummary|namelint}"
        echo "  grepsummary   Validate GREP_SUMMARY keywords and .sh references"
        echo "  namelint      Validate make target names against manifest allowed_verbs"
        exit 0
        ;;
    grepsummary)
        echo "[IMP:8][lint][grepsummary] Delegating to Python" >&2
        exec python3 -m core.internal.lint.grepsummary_validator scan-all
        ;;
    namelint)
        echo "[IMP:8][lint][namelint] Delegating to Python" >&2
        exec python3 -m core.internal.lint.doc_header_validator namelint
        ;;
    *)
        red "Usage: $0 {grepsummary|namelint}"
        exit 1
        ;;
esac
