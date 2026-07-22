#!/usr/bin/env bash
# GREP_SUMMARY: deploy-context, entrypoint, thin-wrapper, context-deployer, standalone, make-deploy-context
# STRUCTURE: ▶ ┌NODE + CONTEXT┐ → ◇ resolve node.yaml → ◇ resolve context → python3 context_deployer.py → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Thin shell facade for `make deploy-context NODE=<n>`.
##           Resolves NODE_YAML and CONTEXT, delegates to context_deployer.py.
## @scope    Called from Makefile deploy-context target (makefiles/bootstrap.mk).
##           Standalone or post-bootstrap invocation.
## @invariants
##   - Thin wrapper: <50 LOC, no business logic
##   - Resolves node.yaml via node-resolver.sh if not passed directly
##   - CONTEXT: from CLI arg, env var, or auto-extracted from node.yaml
##   - Exits with context_deployer.py exit code (0 = success, 1 = errors)
## @rationale Strangler-Fig: shell remains thin facade, Python owns all business logic.
## @changes  2026-07-22 | DevPlan 047 Phase 4 — Created standalone deploy-context entrypoint
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
CORE_DIR="${PLATFORM_ROOT}/core"
DEPLOYER="${CORE_DIR}/internal/bootstrap/deploy/context_deployer.py"

# ─── Argument parsing ─────────────────────────────────────
NODE=""
CONTEXT=""
NODE_YAML=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node) NODE="$2"; shift 2 ;;
        --context) CONTEXT="$2"; shift 2 ;;
        --node-yaml) NODE_YAML="$2"; shift 2 ;;
        *) echo "[IMP:10][deploy-context][args] ERROR: Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# ─── Resolve NODE_YAML ────────────────────────────────────
if [[ -z "$NODE_YAML" ]]; then
    if [[ -n "$NODE" ]]; then
        source "${CORE_DIR}/lib/node-resolver.sh" 2>/dev/null || true
        if declare -f resolve_node_yaml &>/dev/null; then
            NODE_YAML="$(resolve_node_yaml "$NODE" 2>/dev/null || true)"
        fi
    fi
    # Fallback: standard path
    if [[ -z "$NODE_YAML" ]]; then
        NODE_YAML="/opt/node-configs/${NODE}/node.yaml"
    fi
fi

if [[ ! -f "$NODE_YAML" ]]; then
    echo "[IMP:10][deploy-context] ERROR: node.yaml not found: ${NODE_YAML}" >&2
    exit 1
fi

echo "[IMP:9][deploy-context] Deploying context projects (NODE=${NODE}, CONTEXT=${CONTEXT:-<auto>})" >&2

# ─── Delegate to Python context_deployer ──────────────────
python3 "$DEPLOYER" \
    --node-yaml "$NODE_YAML" \
    ${CONTEXT:+--context "$CONTEXT"}

exit $?
