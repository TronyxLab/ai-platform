#!/usr/bin/env bash
# GREP_SUMMARY: hermes-images L1 L2 hermes-agent-base hermes-agent-context build thin-wrapper
# STRUCTURE: ▶ init → ◇ detect action (build-platform|build-context) → ○ build L1/L2 Docker images → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Build hermes-agent Docker images: L1 (hermes-agent-base) and L2 (hermes-agent-context)
## @scope    Called by entrypoints/build.sh; delegates to make hermes-build-platform / make hermes-build-context
## @location core/internal/build/hermes-images.sh — moved from core/scripts/build-hermes-images.sh
## @invariants
##   - L1 builds locally as hermes-agent-base — NEVER pushed to registry (R1)
##   - L2 requires CONTEXT env var — builds as hermes-agent-context
##   - No GHCR push logic — images are built locally only
## @rationale L1 images are pushed to ghcr.io as DR backup (hermes-push-l1). L2 images pushed by context CI pipeline.
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || true)}"
export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"

L1_IMAGE="hermes-agent-base"
L2_IMAGE="hermes-agent-context"

build_L1() {
    echo "[IMP:9][build-hermes][L1] === Building L1: ${L1_IMAGE} ==="
    # 🧐 TRAP[DECISION] · 2026-07-11 · — · --platform linux/amd64 for cross-platform builds
    # · Without explicit platform, docker build on ARM64 macOS produces linux/arm64 image
    # · which cannot run the x86_64-only upstream base image (nousresearch/hermes-agent).
    # · --platform linux/amd64 forces x86_64 build via QEMU emulation on ARM64 hosts.
    # · On Linux x86_64, this flag is native (no-op) — no emulation overhead.
    mkdir -p /tmp/.hermes-build-cache
    echo "[IMP:9][build-hermes][L1] BuildKit cache directory ready"
    docker build --platform linux/amd64 -t "${L1_IMAGE}" \
        --cache-from type=local,src=/tmp/.hermes-build-cache \
        --cache-to type=local,dest=/tmp/.hermes-build-cache,mode=max \
        -f "${PLATFORM_ROOT}/core/modules/hermes-agent/build/Dockerfile" \
        "${PLATFORM_ROOT}/core/modules/hermes-agent/build/"
    echo "[IMP:9][build-hermes][L1] === L1 build complete: ${L1_IMAGE} ==="
}

build_L2() {
    local context="${CONTEXT:-}"
    if [[ -z "$context" ]]; then
        echo "[IMP:10][build-hermes][L2] ERROR: CONTEXT env var is required for L2 build" >&2
        exit 1
    fi
    echo "[IMP:9][build-hermes][L2] === Building L2: ${L2_IMAGE} (context=${context}) ==="
    mkdir -p /tmp/.hermes-build-cache
    echo "[IMP:9][build-hermes][L2] BuildKit cache directory ready"
    docker build --platform linux/amd64 -t "${L2_IMAGE}" \
        --build-arg "CONTEXT=${context}" \
        --cache-from type=local,src=/tmp/.hermes-build-cache \
        --cache-to type=local,dest=/tmp/.hermes-build-cache,mode=max \
        -f "${PLATFORM_ROOT}/core/modules/hermes-agent/context/Dockerfile" \
        "${PLATFORM_ROOT}"
    echo "[IMP:9][build-hermes][L2] === L2 build complete: ${L2_IMAGE} ==="
}

main() {
    local action="${1:-}"
    case "$action" in
        build-platform|L1)
            build_L1
            ;;
        build-context|L2)
            build_L2
            ;;
        *)
            echo "Usage: $0 {build-platform|build-context}" >&2
            echo "  build-platform  — build L1 (hermes-agent-base)" >&2
            echo "  build-context   — build L2 (hermes-agent-context, requires CONTEXT env)" >&2
            exit 1
            ;;
    esac
}

main "$@"
