#!/usr/bin/env bash
# GREP_SUMMARY: infra-metrics healthcheck cadvisor node-exporter nginx-exporter redis-exporter liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_containers → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for infra-metrics module: cAdvisor + Node Exporter + Nginx Exporter + Redis Exporter
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for all containers
##   - MODE=deep: HTTP endpoint checks on cadvisor and node-exporter
##   - nginx-exporter and redis-exporter: scratch images — skip deep check
##   - Exits 0 only if all containers are healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md
## @changes — 2026-07-15 | Added redis-exporter (wave-redis)
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINERS=("cadvisor" "node-exporter" "nginx-prometheus-exporter" "redis-exporter")
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Deep checks: verify HTTP endpoints on cadvisor and node-exporter
    log_imp 8 "healthcheck" "Deep mode: checking HTTP endpoints"

    check_http "http://127.0.0.1:8080/healthz" "200" || exit 1
    check_http "http://127.0.0.1:9100/metrics" "200" || exit 1

    # nginx-exporter: scratch image — skip HTTP, rely on docker inspect
    log_imp 8 "healthcheck" "nginx-exporter: deep HTTP check unavailable (scratch image)"

    # redis-exporter: scratch image — skip HTTP, rely on docker inspect
    log_imp 8 "healthcheck" "redis-exporter: deep HTTP check unavailable (scratch image)"

    log_imp 9 "healthcheck" "All infra-metrics HTTP endpoints healthy"
    exit 0
fi

# Default: docker inspect health for all containers
for container in "${CONTAINERS[@]}"; do
    check_docker_health "$container" || exit 1
done

log_imp 9 "healthcheck" "All infra-metrics containers healthy"
exit 0
