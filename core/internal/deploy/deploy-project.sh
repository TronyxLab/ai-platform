#!/usr/bin/env bash
# GREP_SUMMARY: deploy-project ci-deploy rollback docker-compose healthcheck audit forced-command hook-invocation remove status lifecycle platform-deliver deliver-verb
# STRUCTURE: source_libs(7) → trap(ERR→rollback|EXIT→finalize) → parse_ssh_command → verb_dispatch(platform-deliver→payload_deliverer.py|deploy/remove/status→deploy_engine.py) → post_deploy_nonfatal(tag|prune|hooks|audit|notify)
# region MODULE_CONTRACT
## @purpose  Shell facade for VPS-side forced-command. Trap handlers, lib sourcing, notify_hook,
##           verb dispatch to Python modules. 1183→~200 LOC via Strangler-Fig (Wave 5e).
## @scope    Executed via SSH authorized_keys command="..."
## @invariants — trap handlers (ERR→rollback, EXIT→finalize) REMAIN in shell (D2); 0 inline python3 -c/<<PYEOF;
##              deploy/remove/status → deploy_engine.py; platform-deliver → payload_deliverer.py;
##              post-deploy non-fatal steps (tag,prune,hooks,audit,notify) in shell (D3)
## @rationale 🧐 TRAP[DECISION] SSH forced-command instead of shell (T2)
## @changes 2026-07-26 · DevPlan 036E — Full Strangler-Fig (1183→~200 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail
shopt -s lastpipe 2>/dev/null || true

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly CORE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
readonly PROJECTS_BASE="${PROJECTS_BASE:-/opt/projects}"
readonly MAX_WAIT_SEC="${PLATFORM_DEPLOY_TIMEOUT:-60}"
readonly KEEP_IMAGES="${PLATFORM_DEPLOY_KEEP_IMAGES:-3}"

DEPLOY_STATUS="failed"
PLATFORM_ROOT="${PLATFORM_ROOT:-${CORE_DIR}}"

source "${SCRIPT_DIR}/../../lib/audit_logging.sh" 2>/dev/null || true
__LOG_PREFIX="platform-deploy"
source "${SCRIPT_DIR}/../../lib/logging.sh"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"
source "${SCRIPT_DIR}/../../lib/docker.sh"
source "${SCRIPT_DIR}/../../lib/paths.sh"
source "${SCRIPT_DIR}/../../lib/yaml_read.sh"
source "${SCRIPT_DIR}/../../lib/module-interface.sh"

_rollback_on_error() { local rc=$?; log_imp 10 "rollback" "CRITICAL: error (exit=$rc) at line ${BASH_LINENO[0]}"; DEPLOY_STATUS="failed"; exit 1; }
_finalize_deploy() {
    local status="${DEPLOY_STATUS:-unknown}" rf="${PROJECT_DIR:-.}/.deploy-snapshots/deploy-result.json"
    mkdir -p "$(dirname "$rf")" 2>/dev/null || true
    cat > "$rf" <<EOF 2>/dev/null || true
{"status":"${status}","timestamp":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","project":"${PROJECT:-unknown}","ref":"${REF:-unknown}"}
EOF
    log_imp 9 "deploy" "Deploy result: $status"
}
trap '_rollback_on_error' ERR; trap '_finalize_deploy' EXIT

notify_hook() {
    local hs="${PLATFORM_ROOT}/core/internal/notify/notify-hook.sh"
    [[ -x "$hs" ]] && { "$hs" "${1:-}" 2>/dev/null || true; log_imp 7 "notify" "Hook: ${1:-}"; } || log_imp 6 "notify" "Hook unavailable"
}

# ── parse_ssh_command → delegation to shared modules ──
parse_ssh_command() {
    local raw="${SSH_ORIGINAL_COMMAND:-}"
    [[ -z "$raw" ]] && { log_imp 10 "args" "FATAL: SSH_ORIGINAL_COMMAND not set"; exit 1; }
    while [[ "$raw" =~ ^[A-Z_][A-Z0-9_]*= ]]; do raw="${raw#* }"; raw="$(echo "$raw" | xargs)"; done
    [[ "${SSH_ORIGINAL_COMMAND:-}" == *"PLATFORM_DEPLOY_DIRECT=1"* ]] && PLATFORM_DEPLOY_DIRECT=1 && log_imp 8 "args" "DEPLOY-DIRECT detected"

    # Verb classification via shared ssh_command_parser (single py3 call, --format lines)
    local v a c _parser_output
    _parser_output="$(python3 -m core.internal.shared.ssh_command_parser --format lines parse "$raw")" || { log_imp 10 "args" "parser failed"; exit 1; }
    { IFS= read -r v; IFS= read -r a; IFS= read -r c; } <<< "$_parser_output"
    log_imp 8 "args" "verb=${v} args=${a}"

    case "$v" in
        platform-deliver)
            local p o _pd_output
            _pd_output="$(python3 -m core.internal.shared.platform_deliver parse --format lines "$a")" || { log_imp 10 "args" "deliver parse failed"; exit 1; }
            { IFS= read -r p; IFS= read -r o; } <<< "$_pd_output"
            audit_log "platform-deliver:${p}" "START" "Delivering payload"
            python3 -m core.internal.deploy.payload_deliverer deliver "$p" ${o:+$o}
            exit $?
            ;;
        verify) exec "${PLATFORM_ROOT}/core/entrypoints/verify.sh" "$a" ;;
    esac

    PROJECT="${c%% *}"; REF="${c#* }"; REF="${REF%% *}"
    [[ "$PROJECT" == "$REF" ]] && REF=""
    [[ -z "$PROJECT" || -z "$REF" ]] && { log_imp 10 "args" "FATAL: expects <project> <ref>"; exit 1; }
    PROJECT_DIR="${PROJECTS_BASE}/${PROJECT}"
    [[ ! -d "$PROJECT_DIR" ]] && { log_imp 10 "args" "FATAL: no dir ${PROJECT_DIR}"; exit 1; }
    [[ ! -f "${PROJECT_DIR}/docker-compose.yml" && ! -f "${PROJECT_DIR}/compose.yaml" ]] && { log_imp 10 "args" "FATAL: no compose"; exit 1; }
    SERVICE_NAME="${PROJECT}"
    [[ -f "${PROJECT_DIR}/ai-platform.yaml" ]] && {
        local s; s="$(grep -m1 '^[[:space:]]*service:' "${PROJECT_DIR}/ai-platform.yaml" 2>/dev/null | awk '{print $2}' || true)"
        [[ -n "$s" ]] && SERVICE_NAME="$s"
    }
    log_imp 8 "args" "PROJECT=${PROJECT} REF=${REF} SERVICE=${SERVICE_NAME}"
}

# ── Non-fatal post-deploy helpers (остаются в shell — D3) ──
tag_current() { local id; id="$(docker compose images -q "$SERVICE_NAME" 2>/dev/null)" || return 0; [[ -n "$id" ]] && docker tag "$id" "${SERVICE_NAME}:current" 2>/dev/null || true; }
prune_old_images() { export COMPOSE_PROFILES="${COMPOSE_PROFILES:-postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page}"; local imgs; imgs="$(docker images --format '{{.ID}}' | grep -c . || echo 0)"; local keep="${KEEP_IMAGES}"; [[ "$imgs" -le "$keep" ]] && return 0; docker images --format '{{.ID}}' | grep -i "${SERVICE_NAME}" | tail -n +$((keep+1)) | xargs -r docker rmi 2>/dev/null || true; }
_trigger_deploy_hooks() { for my in "${CORE_DIR}"/modules/*/module.yaml; do [[ -f "$my" ]] || continue; local mn; mn="$(basename "$(dirname "$my")")"; invoke_module_interface "$mn" deploy-hook "$PROJECT_DIR" "$PROJECT" "$(hostname)" && audit_log "hook:${mn}" "SUCCESS" || audit_log "hook:${mn}" "HOOK-FAIL" || true; done; }

# ── Main ──
main() {
    echo "[IMP:7][deploy-project][main] Starting" >&2
    if [[ $# -gt 0 ]]; then
        case "$1" in
            --remove)
                PROJECT="${2:-}"; PROJECT_DIR="${PROJECTS_BASE}/${PROJECT}"
                log_imp 9 "remove" "=== Remove: ${PROJECT} ==="
                python3 -m core.internal.deploy.deploy_engine remove --project "$PROJECT" --project-dir "$PROJECT_DIR" || true
                for my in "${CORE_DIR}"/modules/*/module.yaml; do [[ -f "$my" ]] || continue; local mn; mn="$(basename "$(dirname "$my")")"; invoke_module_interface "$mn" remove-hook "$PROJECT_DIR" "$PROJECT" "$(hostname)" || true; done
                audit_log "remove:${PROJECT}" "DONE" "Project removed (data preserved)"
                exit 0 ;;
            --status)
                local pa="${2:-}" sf=""; [[ "$*" == *"--stub-aware"* ]] && sf="--stub-aware"
                python3 -m core.internal.deploy.deploy_engine status --project "$pa" --project-dir "${PROJECTS_BASE}/${pa}" $sf
                exit 0 ;;
            --help|-h) echo "Usage: deploy-project.sh [--remove|--status <name>|<project> <ref>]"; exit 0 ;;
        esac
    fi
    [[ $# -ge 2 ]] && { PROJECT="${1:-}"; REF="${2:-}"; PROJECT_DIR="${PROJECTS_BASE}/${PROJECT}"; }
    log_imp 9 "main" "=== platform-deploy START ==="; parse_ssh_command
    audit_log "deploy:${PROJECT}" "START" "Deploy ${PROJECT}/${SERVICE_NAME} → ${REF}"

    # DeployEngine call
    python3 -m core.internal.deploy.deploy_engine deploy \
        --project "$PROJECT" --ref "$REF" --service "$SERVICE_NAME" \
        --project-dir "$PROJECT_DIR" --node "$(hostname)" \
        --max-wait "$MAX_WAIT_SEC" --keep-images "$KEEP_IMAGES" || { log_imp 10 "main" "Deploy engine failed"; exit 1; }

    # B1: DEPLOY_STATUS immediately after health-gate; non-fatal housekeeping
    DEPLOY_STATUS="success"; trap - ERR
    tag_current || true; prune_old_images || true; _trigger_deploy_hooks || true
    audit_log "deploy:${PROJECT}" "DONE" "Deploy success: ${SERVICE_NAME} → ${REF}" || true
    notify_hook "🚀 Deploy ✅ ${PROJECT}/${SERVICE_NAME} → ${REF}"
    log_imp 9 "main" "=== platform-deploy DONE (success) ==="
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
