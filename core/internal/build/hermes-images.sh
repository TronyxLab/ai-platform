#!/usr/bin/env bash
# GREP_SUMMARY: hermes-images thin-facade python3 -m hermes_images build L1 L2 context-guard
# STRUCTURE: ▶ init → ◇ detect action (build-platform|build-context) → exec python3 -m core.internal.build.hermes_images → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E8): вся логика сборки L1/L2 (docker build --platform
##           linux/amd64, BuildKit cache, CONTEXT guard) — в core/internal/build/hermes_images.py.
## @scope    Called by entrypoints/build.sh; delegates to make hermes-build-platform / hermes-build-context
## @location core/internal/build/hermes-images.sh — moved from core/scripts/build-hermes-images.sh
## @invariants
##   - <20 LOC thin facade — языковая политика: shell не содержит бизнес-логики
##   - CONTEXT guard в Python (build_l2), не в shell
## @rationale Strangler E8: docker build оркестрация → Python (subprocess, docker_orchestrator-стиль)
## @changes  2026-08-02 | DevPlan 118 E8 — сокращён до фасада (было 77 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${_SCRIPT_DIR}/../../.." 2>/dev/null && pwd || true)}"
export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
ACTION="${1:-}"
if [[ -z "$ACTION" ]]; then
    echo "Usage: $0 {build-platform|build-context}" >&2
    echo "  build-platform  — build L1 (hermes-agent-base)" >&2
    echo "  build-context   — build L2 (hermes-agent-context, requires CONTEXT env)" >&2
    exit 1
fi
exec python3 -m core.internal.build.hermes_images "$ACTION"
