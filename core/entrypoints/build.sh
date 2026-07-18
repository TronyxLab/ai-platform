#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint build hermes L1 L2 docker
# STRUCTURE: ▶ init → ◇ detect action (build-platform|build-context) → ⎋ delegate to internal/build/hermes-images.sh → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Entry-point for `make hermes-build-platform` and `make hermes-build-context`
## @scope    Called ONLY from Makefile.
## @invariants
##   - Delegates to internal/build/hermes-images.sh with action arg
##   - action: build-platform (L1) or build-context (L2)
## @rationale Thin wrapper — all build logic in internal/build/hermes-images.sh
# endregion MODULE_CONTRACT
set -euo pipefail
echo "[IMP:7][build][main] Starting build entrypoint" >&2
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

ACTION="${1:-}"
if [[ -z "$ACTION" ]]; then
    echo "Usage: $0 {build-platform|build-context}" >&2
    echo "  build-platform  — build L1 (hermes-agent-base, local only)" >&2
    echo "  build-context   — build L2 (hermes-agent-context, requires CONTEXT env)" >&2
    echo ""
    echo "Called via Makefile:" >&2
    echo "  make hermes-build-platform     → build.sh build-platform" >&2
    echo "  make hermes-build-context      → build.sh build-context" >&2
    exit 1
fi

echo "[IMP:8][build][main] Delegating to hermes-images.sh action=${ACTION}" >&2
exec "${PATHS_INTERNAL_DIR}/build/hermes-images.sh" "$ACTION"
