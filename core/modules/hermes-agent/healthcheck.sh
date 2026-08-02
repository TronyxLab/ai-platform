#!/usr/bin/env bash
# GREP_SUMMARY: hermes-agent healthcheck liveness readiness http /health /ready check_http check_docker_health deep
# STRUCTURE: ▶ source lib/healthcheck.sh → ◇ MODE=deep ? check_docker_health + (liveness|readiness|deps) → ⎋ liveness: check_docker_health → exit
# region MODULE_CONTRACT
## @purpose  Docker healthcheck for hermes-agent — uses check_docker_health for liveness,
##           deep mode supports liveness, readiness, and dependency checks
## @scope    Called by modules-healthcheck.sh (host-side)
## @invariants
##   - MODE=deep with sub-mode: liveness ( /health ), readiness ( /ready ), deps (PG, Redis, LiteLLM)
##   - MODE=deep ALWAYS runs check_docker_health first, THEN service-specific checks
##   - Default: delegates to check_docker_health for liveness via docker inspect
##   - Container name: hermes-agent
##   - exit 0 = healthy; exit 1 = unhealthy
## @rationale Unified contract per DevPlan 083 — deep mode is strict superset of liveness (DRIFT-H6 fix).
##   check_http replaces docker exec curl copy-paste for liveness/readiness checks (DRIFT-H4 fix).
##   deps mode → healthcheck_deps.py (DevPlan 119 D6, AUDIT-1 F8): required/optional агрегация в Python.
## @changes  2026-08-02 | DevPlan 119 D6 — deps-ветка (48-112) → exec python3 healthcheck_deps.py (test-first)
## @source ../../lib/healthcheck.sh
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][hermes-agent-hc][main] Starting hermes-agent healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="hermes-agent"
AGENT_URL="http://127.0.0.1:9119"
# Port 9119 — internal dashboard port (agent listens here, not 8080)
MODE="${1:-}"

# ── Deep check: multi-mode healthcheck (liveness, readiness, deps) ──
if [ "$MODE" = "deep" ]; then
    # Step 1: Check Docker health status (same as liveness)
    check_docker_health "$CONTAINER" || exit 1

    # shellcheck disable=SC2034
    DEEP_MODE="${2:-liveness}"

    case "$DEEP_MODE" in
        liveness)
            ENDPOINT="/health"
            CHECK_TYPE="LIVENESS"
            ;;
        readiness)
            ENDPOINT="/ready"
            CHECK_TYPE="READINESS"
            ;;
        deps)
            # Dependency check mode — DevPlan 119 D6: required/optional агрегация (PG required,
            # Redis optional, LiteLLM required) в Python healthcheck_deps.py (test-first, R5).
            # exec + exit passthrough: 0 = healthy, 1 = unhealthy.
            log_imp 8 "deps" "Starting dependency checks (healthcheck_deps.py)..."
            exec python3 "${SCRIPT_DIR}/healthcheck_deps.py" \
                --pg-host "${POSTGRES_HOST:-pgbouncer}" --pg-port "${POSTGRES_PORT:-6432}" \
                --redis-host "${REDIS_HOST:-redis}" --redis-port "${REDIS_PORT:-6379}" \
                --litellm-url "${LITELLM_HEALTH_URL:-http://litellm:4000/health}"
            ;;
        *)
            log_imp 9 "deep" "unknown deep mode '$DEEP_MODE' — expected 'liveness', 'readiness', or 'deps'"
            echo "Usage: $0 deep {liveness|readiness|deps}" >&2
            exit 1
            ;;
    esac

    # Step 2: Service-specific HTTP check via check_http (in-container or host-side)
    # ⚠️ TRAP[DECISION] · 2026-07-26 · — · check_http replaces docker exec curl
    # · Rejected: docker exec curl (copy-paste pattern, only works when docker CLI available)
    # · Reason: check_http via lib/healthcheck.sh is unified, works from host and inside container,
    # ·   respects timeout parameter, is grep-able and testable.
    # · Rev: if hermes-agent port is not exposed to host, container_name check_docker_health already
    # ·   verified container is running; check_http to 127.0.0.1:9119 relies on host port mapping.
    if check_http "${AGENT_URL}${ENDPOINT}" "200" 10; then
        log_imp 8 "deep" "${CHECK_TYPE} PASS: ${ENDPOINT} responded 200"
        exit 0
    else
        log_imp 9 "deep" "${CHECK_TYPE} FAIL: ${ENDPOINT} not reachable on ${AGENT_URL}${ENDPOINT}"
        exit 1
    fi
fi

# Default liveness check via docker inspect
echo "[IMP:8][hermes-agent-hc][liveness] Running default liveness check" >&2
check_docker_health "$CONTAINER" || exit 1
echo "[IMP:9][hermes-agent-hc][liveness] Container healthy" >&2
exit 0
