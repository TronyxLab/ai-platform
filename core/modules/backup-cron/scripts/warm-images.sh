#!/usr/bin/env bash
# GREP_SUMMARY: warm-images docker-pull nightly-cron image-cache pre-warming
# STRUCTURE: for each module compose → docker compose pull → log result
# region MODULE_CONTRACT
## @purpose  Nightly Docker image pre-warming — pulls all platform images so morning deploy has zero pull time
## @scope    Run daily at 03:45 UTC via cron in backup-cron container
## @invariants
##   - Uses docker compose pull for each module (does NOT start containers)
##   - Requires docker.sock mounted (read-only) in the container
##   - Non-fatal: partial failures logged, script continues
##   - Logs to /var/log/platform/backup/warm-images.log
## @rationale Pre-pulling images at 03:45 UTC distributes bandwidth usage to low-traffic hours
##           and ensures deploy-modules.sh runs without pull latency.
## ⚠️ TRAP[DECISION] · 2026-07-03 · — · Pre-warming vs on-demand pull
## ·   Rejected: pull on deploy (deploy-modules.sh) — adds 30-60s per module
## ·   Reason: Nightly pre-warming shifts bandwidth to off-peak hours; deploy
## ·     becomes sub-second for unchanged images.
## ·   Rev: if image set grows beyond 10, consider registry mirror (Docker pull-through cache)
# endregion MODULE_CONTRACT

set -euo pipefail

log() {
    local level="$1" msg="$2"
    echo "[IMP:7][warm-images][$(date -u '+%H:%M:%S')] ${level}: ${msg}" >&2
}

COMPOSE_BASE_DIR="${COMPOSE_BASE_DIR:-/opt/platform/core/modules}"  # fallback: PLATFORM_ROOT not available in container context; see core/lib/paths.sh (SoT)
LOG_FILE="${LOG_FILE:-/var/log/platform/backup/warm-images.log}"

log "START" "Image pre-warming started"

# List of module compose files to pre-pull
MODULES=(
    "${COMPOSE_BASE_DIR}/observability/docker-compose.base.yml"
    "${COMPOSE_BASE_DIR}/postgres/docker-compose.base.yml"
    "${COMPOSE_BASE_DIR}/redis/docker-compose.base.yml"
    "${COMPOSE_BASE_DIR}/hermes-agent/docker-compose.base.yml"
    "${COMPOSE_BASE_DIR}/backup-cron/docker-compose.base.yml"
)

PULL_SUCCESS=0
PULL_FAILED=0

for compose_file in "${MODULES[@]}"; do
    if [[ ! -f "$compose_file" ]]; then
        log "WARN" "Compose file not found: ${compose_file} — skipping"
        continue
    fi

    log "PULL" "Pulling images from ${compose_file}"
    if docker compose -f "$compose_file" pull 2>&1 | tee -a "$LOG_FILE"; then
        log "DONE" "Pull success: ${compose_file}"
        PULL_SUCCESS=$(( PULL_SUCCESS + 1 ))
    else
        log "FAIL" "Pull failed: ${compose_file} (continuing)"
        PULL_FAILED=$(( PULL_FAILED + 1 ))
    fi
done

log "DONE" "Image pre-warming complete: success=${PULL_SUCCESS} failed=${PULL_FAILED}"
