#!/usr/bin/env bash
# GREP_SUMMARY: redis healthcheck liveness check_docker_health deep exec_check redis-cli ping
# STRUCTURE: ▶ source lib/healthcheck.sh → ◇ MODE=deep ? check_docker_health + exec_check redis-cli PING → ⎋ liveness: check_docker_health → exit
# region MODULE_CONTRACT
## @purpose  Docker healthcheck for redis — uses check_docker_health for liveness, exec_check redis-cli PING for deep check
## @scope    Called by modules-healthcheck.sh (host-side)
## @invariants
##   - MODE=deep: check_docker_health + exec_check redis-cli ping с auth ($REDIS_PASSWORD из
##     container env — DevPlan 010 T2.0a requirepass)
##   - Default: delegates to check_docker_health for liveness via docker inspect
##   - Container name: redis
##   - exit 0 = healthy; exit 1 = unhealthy
## @rationale Unified contract per DevPlan 083 — deep mode is strict superset of liveness (DRIFT-H6 fix).
##   exec_check replaces inline docker exec copy-paste (DRIFT-H4 fix).
## @source ../../lib/healthcheck.sh
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="redis"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Step 1: Check Docker health status (same as liveness)
    check_docker_health "$CONTAINER" || exit 1
    # Step 2: Service-specific diagnostics via exec_check
    # [IMP:8] DevPlan 010 T2.0a: аутентифицированный ping — $REDIS_PASSWORD раскрывается
    # ВНУТРИ контейнера из его environment (одинарные кавычки сохраняют раскрытие для exec);
    # --no-auth-warning — без warning-шума в логах healthcheck
    exec_check "$CONTAINER" 'redis-cli -h 127.0.0.1 -p 6379 --no-auth-warning -a "$REDIS_PASSWORD" ping' || exit 1
    log_imp 9 "deep" "redis deep check PASSED"
    exit 0  # ранний выход
fi

# Default liveness check via docker inspect
check_docker_health "$CONTAINER" || exit 1
exit 0
