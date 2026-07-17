#!/usr/bin/env bash
# GREP_SUMMARY: clickhouse healthcheck liveness wget-ping check_docker_health deep docker-exec
# STRUCTURE: ▶ source lib/healthcheck.sh → ◇ MODE=deep ? wget /ping → ⎋ check_docker_health → exit
# region MODULE_CONTRACT
## @purpose  Docker healthcheck for clickhouse — uses check_docker_health for liveness, wget /ping for deep check
## @scope    Called by Docker HEALTHCHECK (in-container) and modules-healthcheck.sh (host-side)
## @invariants
##   - MODE=deep: runs wget --spider http://127.0.0.1:8123/ping for service-specific diagnostics
##   - Default: delegates to check_docker_health for liveness via docker inspect
##   - Container name: clickhouse
##   - exit 0 = healthy; exit 1 = unhealthy
## @rationale Unified Docker healthcheck pattern per DevPlan §DD1
## @source ../../lib/healthcheck.sh
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="clickhouse"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # [IMP:9][clickhouse-healthcheck][deep] ClickHouse-specific deep check — HTTP /ping
    if command -v docker &>/dev/null && docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
        if docker exec "$CONTAINER" wget --no-verbose --tries=1 --timeout=5 --spider http://127.0.0.1:8123/ping &>/dev/null; then
            log_imp 8 "deep" "clickhouse ping Ok (docker exec)"
        else
            log_imp 9 "deep" "clickhouse ping failed via docker exec"
            exit 1
        fi
    else
        if wget --no-verbose --tries=1 --timeout=5 --spider http://127.0.0.1:8123/ping &>/dev/null; then
            log_imp 8 "deep" "clickhouse ping Ok (direct)"
        else
            log_imp 9 "deep" "clickhouse ping failed"
            exit 1
        fi
    fi
    exit 0  # ранний выход: deep mode = diagnostics only, не fallthrough к liveness
fi

# Default liveness check via docker inspect
check_docker_health "$CONTAINER" || exit 1
exit 0
