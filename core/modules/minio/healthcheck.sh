#!/usr/bin/env bash
# GREP_SUMMARY: minio healthcheck minio liveness lib/healthcheck
# STRUCTURE: source lib/healthcheck.sh → healthcheck_mode → check_container → exit0/1
# region MODULE_CONTRACT
## @purpose  Liveness healthcheck for minio module
## @scope    Called by module system or Docker HEALTHCHECK override
## @invariants
##   - Default mode: check_docker_health for minio container
##   - MODE=deep: HTTP endpoint check on /minio/health/live
## @rationale Standard module healthcheck contract per core/modules/AGENTS.md
# endregion MODULE_CONTRACT

set -euo pipefail
echo "[IMP:7][minio-hc][main] Starting minio healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"
CONTAINER="minio"
MODE="${1:-}"
[ "$MODE" = "deep" ] && {
    echo "[IMP:8][minio-hc][deep] Running deep HTTP healthcheck" >&2
    check_http "http://127.0.0.1:9000/minio/health/live" "200" || exit 1
}
echo "[IMP:8][minio-hc][liveness] Running default liveness check" >&2
check_docker_health "$CONTAINER" || exit 1
echo "[IMP:9][minio-hc][liveness] Container healthy" >&2
exit 0
