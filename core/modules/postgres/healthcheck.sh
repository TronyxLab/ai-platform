#!/usr/bin/env bash
# GREP_SUMMARY: postgres healthcheck check_docker_health lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → check_docker_health postgres/pgbouncer → exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose  LIVENESS check — delegates to check_docker_health for postgres and pgbouncer containers
## @scope    Called by modules-healthcheck.sh
## @invariants
##   - Default mode (no args): checks docker container health via check_docker_health (liveness)
##   - MODE=deep: checks container running state via docker inspect (non-duplicative of compose pg_isready)
##   - Returns 0 = all healthy; 1 = any unhealthy
## @rationale Deep mode does NOT duplicate compose HEALTHCHECK (pg_isready). Compose already verifies
##            pg_isready for both postgres and pgbouncer. Deep mode only checks what compose doesn't —
##            container process running state.
## @source ../../lib/healthcheck.sh — shared healthcheck primitives
# 📝 TRAP[DEBT] · 2026-07-15 · LO · Container names hardcoded — script unusable against -test stack
# · Observed: POSTGRES_CONTAINER/PGBOUNCER_CONTAINER захардкожены как postgres/pgbouncer
# · Suspected: нет параметризации через env (CONTAINER_SUFFIX или аргумент)
# · Impact: smoke-тесты не могут переиспользовать healthcheck.sh, дублируют его логику
# · When: during wave-postgres T5.2 — smoke test forced to replicate deep checks
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][postgres-hc][main] Starting postgres healthcheck" >&2

# ── Shared library ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

POSTGRES_CONTAINER="postgres"
PGBOUNCER_CONTAINER="pgbouncer"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Deep check: verify containers are running (compose HEALTHCHECK already handles pg_isready)
    # Non-duplicative: compose checks pg_isready, deep checks State.Running
    if ! command -v docker &>/dev/null; then
        log_imp 9 "deep" "docker not available"
        exit 1
    fi

    # Postgres container running state
    if docker inspect "$POSTGRES_CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
        log_imp 8 "deep" "postgres container is running"
    else
        log_imp 9 "deep" "postgres container not running"
        exit 1
    fi

    # PgBouncer container running state
    if docker inspect "$PGBOUNCER_CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
        log_imp 8 "deep" "pgbouncer container is running"
    else
        log_imp 9 "deep" "pgbouncer container not running"
        exit 1
    fi

    log_imp 9 "deep" "All postgres/pgbouncer containers running"
    echo "[IMP:9][postgres-hc][deep] All containers running" >&2
    exit 0
fi

# Default liveness: delegate to Docker's HEALTHCHECK status (compose runs pg_isready internally)
# check_docker_health reads State.Health.Status from docker inspect — non-duplicative of compose
echo "[IMP:8][postgres-hc][liveness] Running default liveness check" >&2
check_docker_health "$POSTGRES_CONTAINER" || exit 1
check_docker_health "$PGBOUNCER_CONTAINER" || exit 1
echo "[IMP:9][postgres-hc][liveness] All containers healthy" >&2
exit 0
