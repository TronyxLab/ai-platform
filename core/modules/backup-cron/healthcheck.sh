#!/usr/bin/env bash
# GREP_SUMMARY: backup-cron healthcheck liveness check_docker_health deep exec_check pgrep cron
# STRUCTURE: ▶ source lib/healthcheck.sh → ◇ MODE=deep ? check_docker_health + exec_check pgrep -x cron → ⎋ liveness: check_docker_health → exit
# region MODULE_CONTRACT
## @purpose  Docker healthcheck for backup-cron — uses check_docker_health for liveness, exec_check pgrep for deep check
## @scope    Called by modules-healthcheck.sh (host-side)
## @invariants
##   - MODE=deep: check_docker_health + exec_check pgrep -x cron (inside container)
##   - Default: delegates to check_docker_health for liveness via docker inspect
##   - Container name: backup-cron
##   - exit 0 = healthy; exit 1 = unhealthy
## @rationale Unified contract per DevPlan 083 — deep mode is strict superset of liveness (DRIFT-H6 fix).
##   exec_check replaces inline docker exec copy-paste (DRIFT-H4 fix).
##   ⚠️ TRAP[BUG] · 2026-07-08 · HI · Fixed by exec_check: pgrep runs inside container, not on host.
## @source ../../lib/healthcheck.sh
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="backup-cron"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Step 1: Check Docker health status (same as liveness)
    check_docker_health "$CONTAINER" || exit 1
    # Step 2: Service-specific diagnostics via exec_check (runs pgrep inside container — no false positive)
    exec_check "$CONTAINER" "pgrep -x cron" || exit 1
    log_imp 9 "deep" "backup-cron deep check PASSED"
    exit 0  # ранний выход
fi

# Default liveness check via docker inspect
check_docker_health "$CONTAINER" || exit 1
exit 0
