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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"
CONTAINER="minio"
MODE="${1:-}"
[ "$MODE" = "deep" ] && { check_http "http://127.0.0.1:9000/minio/health/live" "200" || exit 1; }
check_docker_health "$CONTAINER" || exit 1
exit 0
