#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint build hermes L1 L2 docker
# STRUCTURE: ▶ init → ◇ detect action (build-platform|build-context) → ⎋ delegate to core.internal.build.hermes_images → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Entry-point for `make hermes-build-platform` and `make hermes-build-context`
## @scope    Called ONLY from Makefile.
## @invariants
##   - Delegates to core.internal.build.hermes_images with action arg (прямой вызов, DevPlan 173 W1.1)
##   - action: build-platform (L1) or build-context (L2)
##   - DOCKER_BUILDKIT env экспортируется здесь (перенесено из hermes-images.sh, 173 W1.1)
## @rationale Thin wrapper — all build logic in core.internal.build.hermes_images.py.
##           Двух-хоповый фасад (build.sh → hermes-images.sh → .py) схлопнут (173 W1.1).
## @changes 2026-07-21 | W2-E3 — Added audit_step wrapper (replaced exec, source lib/audit.sh)
## @changes 2026-08-16 | DevPlan 173 W1.1 — схлопнут middle-hop hermes-images.sh; exec python3 напрямую
# endregion MODULE_CONTRACT
set -euo pipefail
echo "[IMP:7][build][main] Starting build entrypoint" >&2
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"
source "${_EP_DIR}/../lib/audit.sh"

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

export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
echo "[IMP:8][build][main] Building hermes image action=${ACTION}" >&2
audit_step "hermes-build:${ACTION}:${CONTEXT:-none}" python3 -m core.internal.build.hermes_images "$ACTION"
exit $?
