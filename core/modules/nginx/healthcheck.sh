#!/usr/bin/env bash
# GREP_SUMMARY: nginx healthcheck check_docker_health deep check_http port-80
# STRUCTURE: ▶ source lib → ◇ MODE=deep? → check_docker_health + check_http localhost:80 → ⎋ | ◇ liveness: check_docker_health nginx → ⎋ 0/1
# region MODULE_CONTRACT
## @purpose  Check nginx Docker container health — liveness (docker inspect) + deep (HTTP verification).
## @scope    Called via `make healthcheck` (liveness) and `make healthcheck MODE=deep` (diagnostic)
## @invariants
##   - check_docker_health for liveness (docker inspect State.Health.Status)
##   - MODE=deep: check_docker_health + check_http for HTTP response verification on port 80
##   - No systemd branches — nginx is now a Docker module
## @rationale Unified contract per DevPlan 083 — deep mode is strict superset of liveness (DRIFT-H6 fix).
##            check_http replaces docker exec curl copy-paste (DRIFT-H4 fix).
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][nginx-hc][main] Starting nginx healthcheck" >&2

# ── Shared library ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="nginx"
MODE="${1:-}"

# ═══════════════════════════════════════════════════════════════════
# Deep check: verify nginx is serving HTTP
# ═══════════════════════════════════════════════════════════════════
if [ "$MODE" = "deep" ]; then
    # Step 1: Check Docker health status (same as liveness)
    check_docker_health "$CONTAINER" || exit 1
    # Step 2: Service-specific diagnostics via check_http
    check_http "http://127.0.0.1:80/" "200" 5 || exit 1
    log_imp 9 "deep" "nginx deep check PASSED"
    echo "[IMP:9][nginx-hc][deep] HTTP port 80 OK" >&2
    exit 0
fi

# ═══════════════════════════════════════════════════════════════════
# Default (liveness): docker inspect via shared library
# ═══════════════════════════════════════════════════════════════════
check_docker_health "$CONTAINER" || exit 1
exit 0
