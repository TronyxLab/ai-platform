#!/usr/bin/env bash
# GREP_SUMMARY: postgres healthcheck check_docker_health lib/healthcheck exec_check pg_isready
# STRUCTURE: source lib/healthcheck.sh → liveness: check_docker_health → deep: check_docker_health + exec_check pg_isready → exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose  LIVENESS check — delegates to check_docker_health for postgres and pgbouncer containers
## @scope    Called by modules-healthcheck.sh
## @invariants
##   - Default mode (no args): checks docker container health via check_docker_health (liveness)
##   - MODE=deep: check_docker_health + exec_check pg_isready (postgres) / pg_isready -p 6432 (pgbouncer)
##   - Returns 0 = all healthy; 1 = any unhealthy
##   - Имена контейнеров параметризованы (CONTAINER_SUFFIX / POSTGRES_CONTAINER /
##     PGBOUNCER_CONTAINER) — пригодность для -test стека
##     (CONTAINER_SUFFIX="-test" → postgres-test/pgbouncer-test, docker-compose.test.yml контракт)
## @rationale Unified contract per DevPlan 083: deep mode ALWAYS runs check_docker_health FIRST,
##            THEN adds service-specific diagnostics via exec_check. This ensures deep is a strict
##            superset of liveness, not a parallel alternative (DRIFT-H6 fix).
## @source ../../lib/healthcheck.sh — shared healthcheck primitives
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][postgres-hc][main] Starting postgres healthcheck" >&2

# ── Shared library ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

# ── Имена контейнеров параметризованы (128 W5 D12-hc): env override ИЛИ суффикс.
#    -test стек: CONTAINER_SUFFIX="-test" → postgres-test/pgbouncer-test (docker-compose.test.yml).
#    Production: без суффикса → postgres/pgbouncer (обратная совместимость).
CONTAINER_SUFFIX="${CONTAINER_SUFFIX:-}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-postgres}${CONTAINER_SUFFIX}"
PGBOUNCER_CONTAINER="${PGBOUNCER_CONTAINER:-pgbouncer}${CONTAINER_SUFFIX}"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Step 1: Check Docker health status (same as liveness)
    check_docker_health "$POSTGRES_CONTAINER" || exit 1
    check_docker_health "$PGBOUNCER_CONTAINER" || exit 1

    # Step 2: Service-specific diagnostics via exec_check
    exec_check "$POSTGRES_CONTAINER" "pg_isready" || exit 1
    exec_check "$PGBOUNCER_CONTAINER" "pg_isready -p 6432" || exit 1

    log_imp 9 "deep" "All postgres/pgbouncer deep checks passed"
    echo "[IMP:9][postgres-hc][deep] All deep checks passed" >&2
    exit 0
fi

# Default liveness: delegate to Docker's HEALTHCHECK status (compose runs pg_isready internally)
# check_docker_health reads State.Health.Status from docker inspect — non-duplicative of compose
echo "[IMP:8][postgres-hc][liveness] Running default liveness check" >&2
check_docker_health "$POSTGRES_CONTAINER" || exit 1
check_docker_health "$PGBOUNCER_CONTAINER" || exit 1
echo "[IMP:9][postgres-hc][liveness] All containers healthy" >&2
exit 0
