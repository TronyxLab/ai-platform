#!/usr/bin/env bash
# GREP_SUMMARY: monitoring healthcheck prometheus grafana liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_containers → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for monitoring module: Prometheus + Grafana
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for all containers
##   - MODE=deep: HTTP endpoint checks on prometheus and grafana
##   - Имена контейнеров и порты env-параметризованы (паттерн infra-metrics, W10 T10.12):
##     PROMETHEUS_CONTAINER_NAME/GRAFANA_CONTAINER_NAME/PROMETHEUS_PORT/GRAFANA_PORT
##     — docker-compose.test.yml переименовывает контейнеры (-test suffix) и смещает порты;
##     канонические значения — дефолты.
##   - Exits 0 only if all containers are healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md
## @changes — 2026-08-05 | DevPlan 136 W10 T10.12 — env-параметризация имён/портов
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][monitoring-hc][main] Starting monitoring healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINERS=(
    "${PROMETHEUS_CONTAINER_NAME:-prometheus}"
    "${GRAFANA_CONTAINER_NAME:-grafana}"
)
PROMETHEUS_PORT="${PROMETHEUS_PORT:-9090}"
GRAFANA_PORT="${GRAFANA_PORT:-3000}"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Deep checks: verify Docker health first, then HTTP endpoints
    log_imp 8 "healthcheck" "Deep mode: checking Docker health + HTTP endpoints (PROMETHEUS_PORT=${PROMETHEUS_PORT}, GRAFANA_PORT=${GRAFANA_PORT})"

    # Step 1: Check Docker health status for all containers
    for container in "${CONTAINERS[@]}"; do
        check_docker_health "$container" || exit 1
    done

    # Step 2: Service-specific diagnostics via check_http
    check_http "http://127.0.0.1:${PROMETHEUS_PORT}/-/healthy" "200" || exit 1
    check_http "http://127.0.0.1:${GRAFANA_PORT}/api/health" "200" || exit 1

    log_imp 9 "healthcheck" "All monitoring deep checks passed"
    exit 0
fi

# Default: docker inspect health for all containers
for container in "${CONTAINERS[@]}"; do
    check_docker_health "$container" || exit 1
done

log_imp 9 "healthcheck" "All monitoring containers healthy"
exit 0
