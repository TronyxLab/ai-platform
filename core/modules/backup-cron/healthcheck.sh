#!/usr/bin/env bash
# GREP_SUMMARY: backup-cron healthcheck liveness pgrep cron check_docker_health deep docker-exec
# STRUCTURE: ▶ source lib/healthcheck.sh → ◇ MODE=deep ? pgrep cron → ⎋ check_docker_health → exit
# region MODULE_CONTRACT
## @purpose  Docker healthcheck for backup-cron — uses check_docker_health for liveness, pgrep cron for deep check
## @scope    Called by Docker HEALTHCHECK (in-container) and modules-healthcheck.sh (host-side)
## @invariants
##   - MODE=deep: runs pgrep -x cron to verify cron daemon is running inside the container
##   - Default: delegates to check_docker_health for liveness via docker inspect
##   - Container name: backup-cron
##   - exit 0 = healthy; exit 1 = unhealthy
## @rationale Unified Docker healthcheck pattern per DevPlan §DD1
## @source ../../lib/healthcheck.sh
## ⚠️ TRAP[BUG] · 2026-07-08 · HI · pgrep -x cron on host finds host cron process (false positive)
# · Host-side pgrep matches the host's own cron daemon, giving false positive liveness.
# · Fix: Use docker exec for host-side calls; in-container Docker healthcheck uses direct call.
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="backup-cron"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # [IMP:9][backup-cron-healthcheck][deep] Verify cron daemon is running inside container
    if command -v docker &>/dev/null && docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
        if docker exec "$CONTAINER" pgrep -x cron > /dev/null 2>&1; then
            log_imp 8 "deep" "cron daemon is running (docker exec)"
        else
            log_imp 9 "deep" "cron process not found in container"
            exit 1
        fi
    else
        if pgrep -x cron > /dev/null 2>&1; then
            log_imp 8 "deep" "cron daemon is running (direct)"
        else
            log_imp 9 "deep" "cron process not found"
            exit 1
        fi
    fi
    exit 0  # ранний выход: deep mode = diagnostics only, не fallthrough к liveness
fi

# Default liveness check via docker inspect
check_docker_health "$CONTAINER" || exit 1
exit 0
