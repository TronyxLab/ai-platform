#!/usr/bin/env bash
# GREP_SUMMARY: nginx-reload-hook nginx_t nginx_reload project-deploy hook on_project_deploy
# STRUCTURE: ▶ cd nginx_module → ◇ docker compose exec nginx nginx -t → ┌if pass: nginx -s reload┐ → ⎋ exit 0/1
# region MODULE_CONTRACT
## @purpose  nginx reload hook — validate config with nginx -t, reload if valid. Called by _trigger_deploy_hooks() after project deploy
## @scope    Invoked as a hook script from deploy-project.sh _trigger_deploy_hooks(); runs in nginx module directory
## @io       ⇥ $1=PROJECT_DIR $2=PROJECT $3=NODE_NAME → ◇ nginx -t → ◇ nginx -s reload → ⎋ exit 0 if OK, exit 1 if config invalid
## @invariants
##   - NEVER reload if nginx -t fails (config error protection)
##   - Runs ONLY after successful project deploy + healthcheck (enforced by call site in deploy-project.sh)
##   - Hook failure is non-fatal to deploy (enforced by _trigger_deploy_hooks: HOOK-FAIL logged, deploy continues)
##   - LDD logs: IMP:7 for trace, IMP:9 for business logic, IMP:10 for errors
## @rationale
##   nginx -t before reload prevents restart-loop from invalid overlay config injected by project deploy.
##   Without this guard, a malformed vhost config would cause nginx to fail reload, leaving old config running.
##   The nginx -t check ensures atomic config switch: all-or-nothing.
## @changes 2026-07-18 · TASK-3 — Created nginx reload hook with nginx -t guard
# endregion MODULE_CONTRACT

set -euo pipefail
shopt -s lastpipe

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=core/lib/logging.sh
source "${SCRIPT_DIR}/../../lib/logging.sh"

PROJECT_DIR="${1:-}"
PROJECT="${2:-}"
NODE_NAME="${3:-}"

cd "$SCRIPT_DIR"

log_imp 7 "nginx-hook" "Starting nginx reload hook (project=${PROJECT}, node=${NODE_NAME})"

# ═══════════════════════════════════════════════════════════════════
# Step 1: Validate nginx config syntax (nginx -t)
# ═══════════════════════════════════════════════════════════════════
log_imp 7 "nginx-hook" "Checking nginx config syntax..."

nginx_test_output="$(docker compose exec -T nginx nginx -t 2>&1)" || {
    log_imp 10 "nginx-hook" "nginx -t FAILED — config error detected, NOT reloading"
    while IFS= read -r line; do
        log_imp 7 "nginx-hook" "  ${line}"
    done <<< "$nginx_test_output"
    log_imp 9 "nginx-hook" "nginx reload SKIPPED (config invalid, old config kept running)"
    exit 1
}

log_imp 9 "nginx-hook" "nginx -t OK — config is valid"

# ═══════════════════════════════════════════════════════════════════
# Step 2: Reload nginx with new config
# ═══════════════════════════════════════════════════════════════════
log_imp 7 "nginx-hook" "Reloading nginx..."

reload_output="$(docker compose exec -T nginx nginx -s reload 2>&1)" || {
    log_imp 10 "nginx-hook" "nginx -s reload FAILED (unexpected — nginx -t passed)"
    while IFS= read -r line; do
        log_imp 7 "nginx-hook" "  ${line}"
    done <<< "$reload_output"
    exit 1
}

log_imp 9 "nginx-hook" "nginx reloaded successfully (project=${PROJECT}, node=${NODE_NAME})"
log_imp 7 "nginx-hook" "nginx reload hook completed"
