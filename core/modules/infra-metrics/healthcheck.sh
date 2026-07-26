#!/usr/bin/env bash
# GREP_SUMMARY: infra-metrics healthcheck cadvisor node-exporter nginx-exporter redis-exporter liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_containers → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for infra-metrics module: cAdvisor + Node Exporter + Nginx Exporter + Redis Exporter
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for all containers
##   - MODE=deep: HTTP endpoint checks on cadvisor and node-exporter
##   - Deep порты переопределяемы через env: CADVISOR_PORT, NODE_EXPORTER_PORT
##   - nginx-exporter and redis-exporter: scratch images — skip deep check
##   - Exits 0 only if all containers are healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md
## @changes — 2026-07-15 | Added redis-exporter (wave-redis)
## @changes — 2026-07-18 | Deep порты параметризованы через env (F-7: smoke использует shifted порты 18081/19100)
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][infra-metrics-hc][main] Starting infra-metrics healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINERS=("cadvisor" "node-exporter" "nginx-prometheus-exporter" "redis-exporter")
MODE="${1:-}"

# ⚠️ TRAP[BUG] · 2026-07-18 · HIGH · hardcoded canonical порты ломали smoke
# · Root: F-7 rolled out !override — test containers bind shifted ports (18081/19100),
# ·   но healthcheck.sh deep ходил на canonical (8080/9100).
# · Fix: порты через env с каноническими дефолтами; smoke передаёт shifted.
# · Rev: если добавится новый deep-проверяемый сервис — по тому же паттерну.
CADVISOR_PORT="${CADVISOR_PORT:-8080}"
NODE_EXPORTER_PORT="${NODE_EXPORTER_PORT:-9100}"

if [ "$MODE" = "deep" ]; then
    # Deep checks: verify Docker health first, then HTTP endpoints on cadvisor and node-exporter
    log_imp 8 "healthcheck" "Deep mode: checking Docker health + HTTP endpoints (CADVISOR_PORT=${CADVISOR_PORT}, NODE_EXPORTER_PORT=${NODE_EXPORTER_PORT})"

    # Step 1: Check Docker health status for all containers
    for container in "${CONTAINERS[@]}"; do
        check_docker_health "$container" || exit 1
    done

    # Step 2: Service-specific diagnostics via check_http
    check_http "http://127.0.0.1:${CADVISOR_PORT}/healthz" "200" || exit 1
    check_http "http://127.0.0.1:${NODE_EXPORTER_PORT}/metrics" "200" || exit 1

    # nginx-exporter: scratch image — skip HTTP, rely on docker inspect
    log_imp 8 "healthcheck" "nginx-exporter: deep HTTP check unavailable (scratch image)"

    # redis-exporter: scratch image — skip HTTP, rely on docker inspect
    log_imp 8 "healthcheck" "redis-exporter: deep HTTP check unavailable (scratch image)"

    log_imp 9 "healthcheck" "All infra-metrics deep checks passed"
    exit 0
fi

# Default: docker inspect health for all containers
for container in "${CONTAINERS[@]}"; do
    check_docker_health "$container" || exit 1
done

log_imp 9 "healthcheck" "All infra-metrics containers healthy"
exit 0
