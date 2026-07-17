#!/usr/bin/env bash
# GREP_SUMMARY: redis healthcheck liveness redis-cli ping check_docker_health deep docker-exec
# STRUCTURE: ▶ source lib/healthcheck.sh → ◇ MODE=deep ? redis-cli PING → ⎋ check_docker_health → exit
# region MODULE_CONTRACT
## @purpose  Docker healthcheck for redis — uses check_docker_health for liveness, redis-cli ping for deep check
## @scope    Called by Docker HEALTHCHECK (in-container) and modules-healthcheck.sh (host-side)
## @invariants
##   - MODE=deep: runs redis-cli PING via docker exec for service-specific diagnostics
##   - Default: delegates to check_docker_health for liveness via docker inspect
##   - Container name: redis
##   - exit 0 = healthy; exit 1 = unhealthy
## @rationale Unified Docker healthcheck pattern per DevPlan §DD1 — check_docker_health for fast liveness,
##   redis-cli ping for deep diagnostic check
## @source ../../lib/healthcheck.sh
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="redis"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # [IMP:9][redis-healthcheck][deep] Redis-specific deep check — redis-cli PING
    if command -v docker &>/dev/null && docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
        if docker exec "$CONTAINER" redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -q "PONG"; then
            log_imp 8 "deep" "redis PONG (docker exec)"
        else
            log_imp 9 "deep" "redis-cli ping did not return PONG"
            exit 1
        fi
    else
        if redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -q "PONG"; then
            log_imp 8 "deep" "redis PONG (direct)"
        else
            log_imp 9 "deep" "redis-cli ping did not return PONG"
            exit 1
        fi
    fi
    exit 0  # ранний выход: deep mode = diagnostics only, не fallthrough к liveness
fi

# Default liveness check via docker inspect
check_docker_health "$CONTAINER" || exit 1
exit 0
