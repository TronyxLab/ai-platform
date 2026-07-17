#!/usr/bin/env bash
# GREP_SUMMARY: nginx healthcheck docker check_docker_health curl deep port-80
# STRUCTURE: ▶ source lib → ◇ MODE=deep? → docker exec curl :80 → ⎋ | ◇ check_docker_health nginx → ⎋ 0/1
# region MODULE_CONTRACT
## @purpose  Check nginx Docker container health — liveness (docker inspect) + deep (HTTP verification).
## @scope    Called via `make healthcheck` (liveness) and `make healthcheck MODE=deep` (diagnostic)
## @invariants
##   - check_docker_health for liveness (docker inspect State.Health.Status)
##   - MODE=deep: docker exec curl for HTTP response verification on port 80
##   - No systemd branches — nginx is now a Docker module
##   - All curl calls: --max-time 5 (never blocks)
## @rationale Docker healthcheck (nc -z) is liveness; deep check verifies nginx actually serves HTTP.
##            Two-tier: fast inspect for make healthcheck, full HTTP for MODE=deep.
# endregion MODULE_CONTRACT

set -euo pipefail

# ── Shared library ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="nginx"
MODE="${1:-}"

# ═══════════════════════════════════════════════════════════════════
# Deep check: verify nginx is serving HTTP inside the container
# ═══════════════════════════════════════════════════════════════════
if [ "$MODE" = "deep" ]; then
    if command -v docker &>/dev/null && docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
        if docker exec "$CONTAINER" curl -sf --max-time 5 http://localhost:80/ > /dev/null 2>&1; then
            log_imp 8 "deep" "nginx HTTP port 80 OK (docker exec)"
            exit 0
        fi
        log_imp 9 "deep" "nginx HTTP port 80 FAIL"
        exit 1
    fi
    exit 1
fi

# ═══════════════════════════════════════════════════════════════════
# Default (liveness): docker inspect via shared library
# ═══════════════════════════════════════════════════════════════════
check_docker_health "$CONTAINER" || exit 1
exit 0
