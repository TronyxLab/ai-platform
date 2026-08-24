#!/usr/bin/env bash
# GREP_SUMMARY: log-collector healthcheck alloy liveness lib/healthcheck no-loki-ready wal-self-heal
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_containers (alloy only) → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for log-collector module: Alloy (DevPlan 010 T3.1 split из logging)
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for all containers (alloy)
##   - MODE=deep: check_docker_health для alloy + собственная готовность коллектора
##   - ⚠️ НЕТ локального loki /ready (T3.1): alloy здоров БЕЗ loki (WAL self-heal) —
##     deep-проверка НЕ зависит от центрального Loki (кросс-нодово он на другой ноде)
##   - Имя контейнера env-параметризовано (паттерн infra-metrics, W10 T10.12):
##     ALLOY_CONTAINER_NAME
##   - Exits 0 only if all containers are healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md;
##            выделен из logging (010 T3.1) — healthcheck logging остаётся только loki /ready
## @changes 2026-08-22 | DevPlan 010 T3.1 — Created (split из core/modules/logging/)
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][log-collector-hc][main] Starting log-collector healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINERS=(
    "${ALLOY_CONTAINER_NAME}"
)
ALLOY_CONTAINER_NAME="${ALLOY_CONTAINER_NAME:-alloy}"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    log_imp 8 "healthcheck" "Deep mode: checking Docker health for alloy (без loki /ready — T3.1 WAL self-heal)"

    # Step 1: Check Docker health status for all containers
    for container in "${CONTAINERS[@]}"; do
        check_docker_health "$container" || exit 1
    done

    log_imp 9 "healthcheck" "All log-collector deep checks passed"
    exit 0
fi

# Default: docker inspect health for all containers
for container in "${CONTAINERS[@]}"; do
    check_docker_health "$container" || exit 1
done

log_imp 9 "healthcheck" "All log-collector containers healthy"
exit 0
