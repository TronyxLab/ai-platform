#!/usr/bin/env bash
# GREP_SUMMARY: langfuse healthcheck langfuse liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_container → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for langfuse module
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for langfuse container
##   - MODE=deep: HTTP endpoint check on /api/public/health
##   - Exits 0 only if container is healthy
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][langfuse-hc][main] Starting langfuse healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="langfuse"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Deep checks: verify Docker health first, then HTTP endpoint
    log_imp 8 "healthcheck" "Deep mode: checking Docker health + HTTP endpoint"

    # Step 1: Check Docker health status (same as liveness)
    check_docker_health "$CONTAINER" || exit 1
    # Step 2: Service-specific diagnostics via check_http
    check_http "http://127.0.0.1:3001/api/public/health" "200" || exit 1

    log_imp 9 "healthcheck" "Langfuse deep check PASSED"
    exit 0
fi

# Default: docker inspect health
check_docker_health "$CONTAINER" || exit 1

log_imp 9 "healthcheck" "Langfuse container healthy"
exit 0
