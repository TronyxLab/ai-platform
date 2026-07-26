#!/usr/bin/env bash
# GREP_SUMMARY: clickhouse healthcheck liveness check_docker_health deep check_http ping
# STRUCTURE: ▶ source lib/healthcheck.sh → ◇ MODE=deep ? check_docker_health + check_http /ping → ⎋ liveness: check_docker_health → exit
# region MODULE_CONTRACT
## @purpose  Docker healthcheck for clickhouse — uses check_docker_health for liveness, check_http /ping for deep check
## @scope    Called by modules-healthcheck.sh (host-side)
## @invariants
##   - MODE=deep: check_docker_health + check_http http://127.0.0.1:8123/ping
##   - Default: delegates to check_docker_health for liveness via docker inspect
##   - Container name: clickhouse
##   - exit 0 = healthy; exit 1 = unhealthy
## @rationale Unified contract per DevPlan 083 — deep mode is strict superset of liveness (DRIFT-H6 fix).
##   check_http replaces docker exec wget copy-paste (DRIFT-H4 fix).
## @source ../../lib/healthcheck.sh
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="clickhouse"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Step 1: Check Docker health status (same as liveness)
    check_docker_health "$CONTAINER" || exit 1
    # Step 2: Service-specific diagnostics via check_http
    check_http "http://127.0.0.1:8123/ping" "200" 5 || exit 1
    log_imp 9 "deep" "clickhouse deep check PASSED"
    exit 0
fi

# Default liveness check via docker inspect
check_docker_health "$CONTAINER" || exit 1
exit 0
