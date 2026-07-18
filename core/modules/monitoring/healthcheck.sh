#!/usr/bin/env bash
# GREP_SUMMARY: monitoring healthcheck prometheus grafana liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_containers → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for monitoring module: Prometheus + Grafana
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for all containers
##   - MODE=deep: HTTP endpoint checks on prometheus and grafana
##   - Exits 0 only if all containers are healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][monitoring-hc][main] Starting monitoring healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINERS=("prometheus" "grafana")
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Deep checks: verify HTTP endpoints
    log_imp 8 "healthcheck" "Deep mode: checking HTTP endpoints"

    check_http "http://127.0.0.1:9090/-/healthy" "200" || exit 1
    check_http "http://127.0.0.1:3000/api/health" "200" || exit 1

    log_imp 9 "healthcheck" "All monitoring HTTP endpoints healthy"
    exit 0
fi

# Default: docker inspect health for all containers
for container in "${CONTAINERS[@]}"; do
    check_docker_health "$container" || exit 1
done

log_imp 9 "healthcheck" "All monitoring containers healthy"
exit 0
