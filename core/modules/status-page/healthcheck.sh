#!/usr/bin/env bash
# GREP_SUMMARY: status-page healthcheck liveness deep check_docker_health check_http
# STRUCTURE: ▶ source healthcheck.sh lib → liveness: check_docker_health status-page → deep: check_http http://127.0.0.1:8080/health 200 → ⎋ exit 0|1
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Healthcheck for status-page Docker module
## @scope    Two modes:
##            - liveness (default): docker inspect State.Health.Status for status-page container
##            - deep (MODE=deep): curl http://127.0.0.1:8080/health expecting 200
## @invariants
##   - MODE=deep performs live HTTP check against the status-page /health endpoint
##   - liveness mode delegates to healthcheck.sh library's check_docker_health
##   - Meets module healthcheck contract per core/modules/AGENTS.md
## @rationale Deep mode verifies the service is actually responding, not just running.
##            Liveness mode is fast and sufficient for docker health monitoring.
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

# ── Liveness: container health via docker inspect ──
echo "[IMP:9][status-page][healthcheck] Liveness check — docker health"
check_docker_health "status-page"
liveness_exit=$?
if [[ $liveness_exit -ne 0 ]]; then
    echo "[IMP:10][status-page][healthcheck] Liveness check FAILED (exit ${liveness_exit})"
    exit 1
fi

# ── Deep: HTTP health endpoint (only when MODE=deep) ──
if [[ "${MODE:-liveness}" == "deep" ]]; then
    echo "[IMP:9][status-page][healthcheck] Deep check — HTTP /health endpoint"
    check_http "http://127.0.0.1:8080/health" "200"
    deep_exit=$?
    if [[ $deep_exit -ne 0 ]]; then
        echo "[IMP:10][status-page][healthcheck] Deep check FAILED (exit ${deep_exit})"
        exit 1
    fi
    echo "[IMP:9][status-page][healthcheck] Deep check PASSED"
fi

echo "[IMP:9][status-page][healthcheck] Liveness check PASSED"
exit 0
