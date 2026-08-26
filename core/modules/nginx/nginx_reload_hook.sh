#!/usr/bin/env bash
# GREP_SUMMARY: nginx-reload-hook nginx_t nginx_reload project-deploy hook on_project_deploy self-env F-023 plan-012-T11
# STRUCTURE: ▶ self-env [source secrets.env ∋ export NGINX_OVERLAY_DIR] → ◇ run_in_nginx [docker exec ‖ compose-exec fallback] → nginx -t → ┌if pass: nginx -s reload┐ → ⎋ exit 0/1
# region MODULE_CONTRACT
## @purpose  nginx reload hook — validate config with nginx -t, reload if valid. Called by _trigger_deploy_hooks() after project deploy
## @scope    Invoked as a hook script from project deploy hooks; runs in nginx module directory
## @io       ⇥ $1=PROJECT_DIR $2=PROJECT $3=NODE_NAME → ◇ nginx -t → ◇ nginx -s reload → ⎋ exit 0 if OK, exit 1 if config invalid
## @invariants
##   - NEVER reload if nginx -t fails (config error protection)
##   - Runs ONLY after successful project deploy + healthcheck (enforced by call site in project deploy)
##   - Hook failure is non-fatal to deploy (enforced by _trigger_deploy_hooks: HOOK-FAIL logged, deploy continues)
##   - LDD logs: IMP:7 for trace, IMP:9 for business logic, IMP:10 for errors
##   - plan 012 T11 (F-023): хук самодостаточен в env-less ReceiveFlow — сам source-ит
##     secrets.env и экспортирует NGINX_OVERLAY_DIR; исполнение через ПРЯМОЙ docker exec
##     (нулевая зависимость от compose-интерполяции стека); compose-exec с self-env — fallback
## @rationale
##   nginx -t before reload prevents restart-loop from invalid overlay config injected by project deploy.
##   Without this guard, a malformed vhost config would cause nginx to fail reload, leaving old config running.
##   The nginx -t check ensures atomic config switch: all-or-nothing.
##   F-023: docker compose exec требует интерполяцию ВСЕГО стека (secrets.env + overlay-dir)
##   → в env-less ReceiveFlow hook падал до exec → ложный FAILED при зелёном деплое.
## @changes 2026-07-18 · TASK-3 — Created nginx reload hook with nginx -t guard
##          2026-08-26 · plan 012 T11 — self-env + docker exec primary (F-023/D9)
# endregion MODULE_CONTRACT

set -euo pipefail
# lastpipe — bash ≥4.2; на старых bash (macOS 3.2) опция недоступна — не критична здесь
shopt -s lastpipe 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=core/lib/logging.sh
source "${SCRIPT_DIR}/../../lib/logging.sh"

PROJECT_DIR="${1:-}"
PROJECT="${2:-}"
NODE_NAME="${3:-}"

cd "$SCRIPT_DIR"

# ═══════════════════════════════════════════════════════════════════
# Step 0 (plan 012 T11 / F-023): self-env — hook работает в env-less ReceiveFlow
# ═══════════════════════════════════════════════════════════════════
# secrets.env — расшифрованная матрица ноды; overlay-dir — канон node-configs.
# 🧐 TRAP[DECISION] · 2026-08-26 · plan 012 T11/D9 · docker exec primary, compose-exec fallback
# · Rejected: compose-exec с self-env как primary (интерполяция стека хрупка:
#   любой новый ${VAR:?} в ЛЮБОМ включённом base.yml снова ломает хук в ReceiveFlow)
# · Reason: прямой docker exec не интерполирует compose вообще — класс F-023 устранён;
#   self-env (source + overlay export) остаётся для compose-fallback и последующих шагов
# · Rev: если появится потребность в compose-семантике (project-name/profiles) внутри хука —
#   перенести fallback на `docker compose --env-file` с явным профилем nginx
HOOK_SECRETS_ENV="${SECRETS_ENV_FILE:-/var/lib/platform/run/secrets.env}"
if [[ -f "$HOOK_SECRETS_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$HOOK_SECRETS_ENV"
    set +a
    log_imp 8 "nginx-hook" "Self-env: sourced ${HOOK_SECRETS_ENV}"
fi
NGINX_OVERLAY_DIR="${NGINX_OVERLAY_DIR:-/opt/node-configs/${NODE_NAME}/overlays/nginx}"
export NGINX_OVERLAY_DIR

run_in_nginx() {
    # Primary: docker exec по каноническому container_name (base.yml: container_name:nginx) —
    # БЕЗ compose-интерполяции. Fallback: compose exec c собранным self-env.
    if docker container inspect nginx >/dev/null 2>&1; then
        docker exec -T nginx "$@"
    else
        docker compose exec -T nginx "$@"
    fi
}

log_imp 7 "nginx-hook" "Starting nginx reload hook (project=${PROJECT}, node=${NODE_NAME})"

# ═══════════════════════════════════════════════════════════════════
# Step 1: Validate nginx config syntax (nginx -t)
# ═══════════════════════════════════════════════════════════════════
log_imp 7 "nginx-hook" "Checking nginx config syntax..."

nginx_test_output="$(run_in_nginx nginx -t 2>&1)" || {
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

reload_output="$(run_in_nginx nginx -s reload 2>&1)" || {
    log_imp 10 "nginx-hook" "nginx -s reload FAILED (unexpected — nginx -t passed)"
    while IFS= read -r line; do
        log_imp 7 "nginx-hook" "  ${line}"
    done <<< "$reload_output"
    exit 1
}

log_imp 9 "nginx-hook" "nginx reloaded successfully (project=${PROJECT}, node=${NODE_NAME})"
log_imp 7 "nginx-hook" "nginx reload hook completed"
