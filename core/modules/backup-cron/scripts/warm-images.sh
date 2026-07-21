#!/usr/bin/env bash
# GREP_SUMMARY: warm-images docker-pull nightly-cron image-cache pre-warming profile-fix 13-modules
# STRUCTURE: ▶ 13 docker modules → for each: ┌resolve compose.yaml┐ → docker compose --profile <mod> pull → log result → ⎋ success/failed tally
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
##           Expansion from 5 to 13 modules + --profile flag fixes silent no-op pulls
##           (all docker modules declare profiles: [module-name]; without --profile,
##           docker compose pull resolves 0 services).
## ⚠️ TRAP[DECISION] · 2026-07-03 · — · Pre-warming vs on-demand pull
## ·   Rejected: pull on deploy (deploy-modules.sh) — adds 30-60s per module
## ·   Reason: Nightly pre-warming shifts bandwidth to off-peak hours; deploy
## ·     becomes sub-second for unchanged images.
## ·   Rev: if image set grows beyond 10, consider registry mirror (Docker pull-through cache)
## 🧐 TRAP[BUG] · 2026-07-21 · — · Without --profile, pulls silently pull 0 services
## ·   Symptom: warm-images.sh ran nightly but never actually pulled any images
## ·   Root: all module compose files use profiles: [module-name], but warm-images.sh
## ·     never passed --profile flag — docker compose pull matched no services
## ·   Fix: Added --profile $mod_name to docker compose pull
## ·   Second fix: module list expanded from 5 to 13 (was missing 8 modules entirely)
## ·   Third fix: old MODULES list had wrong directory name (observability vs monitoring)
# endregion MODULE_CONTRACT

set -euo pipefail

log() {
    local level="$1" msg="$2"
    echo "[IMP:7][warm-images][$(date -u '+%H:%M:%S')] ${level}: ${msg}" >&2
}

COMPOSE_BASE_DIR="${COMPOSE_BASE_DIR:-/opt/platform/core/modules}"  # fallback: PLATFORM_ROOT not available in container context; see core/lib/paths.sh (SoT)
LOG_FILE="${LOG_FILE:-/var/log/platform/backup/warm-images.log}"

log "START" "Image pre-warming started"

# List of all 13 docker module compose files to pre-pull
# (platform-secrets is system module — no compose file)
MODULES=(
    "backup-cron"
    "clickhouse"
    "hermes-agent"
    "infra-metrics"
    "langfuse"
    "litellm"
    "logging"
    "minio"
    "monitoring"
    "nginx"
    "postgres"
    "redis"
    "status-page"
)

PULL_SUCCESS=0
PULL_FAILED=0

for mod_name in "${MODULES[@]}"; do
    # Resolve compose file: try compose.yaml → docker-compose.yaml → docker-compose.base.yml
    compose_file="${COMPOSE_BASE_DIR}/${mod_name}/compose.yaml"
    [[ ! -f "$compose_file" ]] && compose_file="${COMPOSE_BASE_DIR}/${mod_name}/docker-compose.yaml"
    [[ ! -f "$compose_file" ]] && compose_file="${COMPOSE_BASE_DIR}/${mod_name}/docker-compose.base.yml"

    if [[ ! -f "$compose_file" ]]; then
        log "WARN" "Compose file not found for module '${mod_name}' — skipping"
        PULL_FAILED=$(( PULL_FAILED + 1 ))
        continue
    fi

    # Skip modules with local build (no registry image — pull would fail)
    if grep -q '^\s\+build:' "$compose_file" 2>/dev/null; then
        log "SKIP" "Local build module '${mod_name}' — skipping pull"
        PULL_SUCCESS=$(( PULL_SUCCESS + 1 ))
        continue
    fi

    log "PULL" "Pulling images for ${mod_name} (${compose_file})"
    # --profile required: all module compose files use profiles: [module-name]
    if docker compose -f "$compose_file" --profile "$mod_name" pull 2>&1 | tee -a "$LOG_FILE"; then
        log "DONE" "Pull success: ${mod_name} (${compose_file})"
        PULL_SUCCESS=$(( PULL_SUCCESS + 1 ))
    else
        log "FAIL" "Pull failed: ${mod_name} (${compose_file}) — continuing"
        PULL_FAILED=$(( PULL_FAILED + 1 ))
    fi
done

log "DONE" "Image pre-warming complete: success=${PULL_SUCCESS} failed=${PULL_FAILED}"
