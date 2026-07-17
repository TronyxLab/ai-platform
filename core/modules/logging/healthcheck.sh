#!/usr/bin/env bash
# GREP_SUMMARY: logging healthcheck loki promtail liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_containers → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for logging module: Loki + Promtail
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for all containers
##   - MODE=deep: HTTP endpoint check on loki (port 3100 /ready)
##   - Exits 0 only if all containers are healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINERS=("loki" "promtail")
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # 🧐 TRAP[DECISION] · 2026-07-15 · — · Promtail HTTP /ready check replaced with docker inspect
    # · Rejected: check_http http://127.0.0.1:9080/ready
    # · Reason: Promtail is an internal collector — port 9080 NOT exposed to host (module.yaml).
    # ·   Promtail image is minimal — no wget/curl inside.
    # · Rev: if Promtail image adds wget/curl, restore HTTP check via docker exec.

    # Deep checks: verify Loki HTTP endpoint
    log_imp 8 "healthcheck" "Deep mode: checking Loki HTTP endpoint"
    check_http "http://127.0.0.1:3100/ready" "200" || exit 1

    # Deep checks: verify Promtail health via docker inspect (port 9080 is internal)
    log_imp 8 "healthcheck" "Deep mode: checking Promtail health"
    check_docker_health "promtail" || exit 1

    log_imp 9 "healthcheck" "All logging deep checks passed"
    exit 0
fi

# Default: docker inspect health for all containers
for container in "${CONTAINERS[@]}"; do
    check_docker_health "$container" || exit 1
done

log_imp 9 "healthcheck" "All logging containers healthy"
exit 0
