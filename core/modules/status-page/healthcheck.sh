#!/usr/bin/env bash
# GREP_SUMMARY: status-page healthcheck liveness deep check_docker_health check_http
# STRUCTURE: ▶ source lib/healthcheck.sh → ◇ MODE=deep ? check_docker_health + check_http /health → ⎋ liveness: check_docker_health → exit 0|1
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Healthcheck for status-page Docker module
## @scope    Two modes:
##            - liveness (default): check_docker_health for status-page container
##            - deep (MODE=deep): check_docker_health + check_http /health
## @invariants
##   - MODE=deep: runs check_docker_health FIRST, THEN check_http service-specific check
##   - liveness mode delegates to check_docker_health
##   - Meets unified healthcheck contract per DevPlan 083
## @rationale Unified contract per DevPlan 083 — deep mode is strict superset of liveness (DRIFT-H6 fix).
##            Deep mode verifies the service is actually responding, not just running.
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="status-page"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Step 1: Check Docker health status (same as liveness)
    check_docker_health "$CONTAINER" || exit 1
    # Step 2: Service-specific diagnostics — HTTP /health ВНУТРИ контейнера.
    # ⚠️ TRAP[BUG] · 2026-08-06 · HI · B24 (141 r2): check_http на 127.0.0.1:8080 с хоста —
    # ·   status-page НЕ публикует порты (доступ через nginx proxy_pass), 8080 на хосте занят
    # ·   cadvisor/test-project → deep-check бился об чужой сервис.
    # · Fix: exec_check с busybox wget ВНУТРИ контейнера (alpine, D5-канон: без raw curl).
    # · Rev: если образ сменится на scratch — пересмотреть механизм.
    exec_check "$CONTAINER" "wget -q -O /dev/null http://127.0.0.1:8080/health" || exit 1
    log_imp 9 "healthcheck" "status-page deep check PASSED"
    exit 0
fi

# Default liveness: container health via docker inspect
log_imp 8 "healthcheck" "Running default liveness check"
check_docker_health "$CONTAINER" || exit 1
log_imp 9 "healthcheck" "status-page container healthy"
exit 0
