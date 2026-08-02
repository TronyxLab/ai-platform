#!/usr/bin/env bash
# GREP_SUMMARY: litellm healthcheck litellm liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_container → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for litellm module
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for litellm container
##   - MODE=deep: delegates to check_docker_health (compose HEALTHCHECK already validates /health/liveliness)
##   - Exits 0 only if container is healthy
## @rationale Deep mode delegates to Docker HEALTHCHECK state instead of parallel HTTP check.
##            Compose HEALTHCHECK (python3 urllib → /health/liveliness) is the single source of truth
##            for HTTP liveness. check_docker_health reads that state — no duplication.
##            T7: replaced check_http (parallel curl) with check_docker_health delegation.
## @changes   2026-07-26 · DevPlan 083 — Verified: already conforms to unified contract (check_docker_health for both modes)
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][litellm-hc][main] Starting litellm healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="litellm"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Deep checks: delegate to Docker HEALTHCHECK state (compose already validates /health/liveliness via python-urllib)
    # Non-duplicative: compose runs python3 urllib internally; check_docker_health reads the resulting health state
    log_imp 8 "healthcheck" "Deep mode: verifying litellm container health via Docker"

    check_docker_health "$CONTAINER" || exit 1

    log_imp 9 "healthcheck" "LiteLLM deep healthcheck passed (delegated to Docker HEALTHCHECK)"
    exit 0
fi

# Default: docker inspect health
check_docker_health "$CONTAINER" || exit 1

log_imp 9 "healthcheck" "LiteLLM container healthy"
exit 0
