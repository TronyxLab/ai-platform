#!/usr/bin/env bash
# GREP_SUMMARY: node-metrics healthcheck cadvisor node-exporter liveness deep lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_containers → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for node-metrics module: cAdvisor + Node Exporter
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for all containers (cadvisor, node-exporter)
##   - MODE=deep: HTTP endpoint checks on cadvisor (/healthz) and node-exporter (/metrics)
##   - Deep порты переопределяемы через env: CADVISOR_PORT, NODE_EXPORTER_PORT
##   - Exits 0 only if all containers are healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md;
##           унаследовано из infra-metrics при split (DevPlan 010 T3.2).
## @changes — 2026-08-22 | DevPlan 010 T3.2 — создан из infra-metrics healthcheck.sh
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][node-metrics-hc][main] Starting node-metrics healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINERS=(
    "${CADVISOR_CONTAINER_NAME:-cadvisor}"
    "${NODE_EXPORTER_CONTAINER_NAME:-node-exporter}"
)
MODE="${1:-}"

# ⚠️ TRAP[BUG] · 2026-07-18 · HIGH · hardcoded canonical порты ломали smoke (наследие infra-metrics)
# · Fix: порты через env с каноническими дефолтами; smoke передаёт shifted.
CADVISOR_PORT="${CADVISOR_PORT:-8080}"
NODE_EXPORTER_PORT="${NODE_EXPORTER_PORT:-9100}"

if [ "$MODE" = "deep" ]; then
    # Deep checks: verify Docker health first, then HTTP endpoints
    log_imp 8 "healthcheck" "Deep mode: checking Docker health + HTTP endpoints (CADVISOR_PORT=${CADVISOR_PORT}, NODE_EXPORTER_PORT=${NODE_EXPORTER_PORT})"

    # Step 1: Check Docker health status for all containers
    for container in "${CONTAINERS[@]}"; do
        check_docker_health "$container" || exit 1
    done

    # Step 2: Service-specific diagnostics via check_http
    check_http "http://127.0.0.1:${CADVISOR_PORT}/healthz" "200" || exit 1
    check_http "http://127.0.0.1:${NODE_EXPORTER_PORT}/metrics" "200" || exit 1

    log_imp 9 "healthcheck" "All node-metrics deep checks passed"
    exit 0
fi

# Default: docker inspect health for all containers
for container in "${CONTAINERS[@]}"; do
    check_docker_health "$container" || exit 1
done

log_imp 9 "healthcheck" "All node-metrics containers healthy"
exit 0
