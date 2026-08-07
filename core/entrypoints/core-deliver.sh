#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint core-deliver fallback rsync ssh node-update provision git-outage age-key dry-run core-deploy-mirror
# STRUCTURE: ▶ init → ◇ --node required → ○ resolve node.yaml + SSH host → ○ detect AGE key (node_detect) → ◇ --dry-run? → ○ rsync core/+scripts/+makefiles+platform-env.yaml → /opt/platform (guards) → ⚡ ssh provision → ⚡ ssh node-update → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Entry-point for `make core-deliver` — ЛОКАЛЬНОЕ зеркало core-deploy.yml CI-воркфлоу
##           (142 W5, A6): rsync core/ + scripts/ + makefiles/ + platform-env.yaml → /opt/platform
##           (guard'ы как в workflow) → ssh `make provision SCOPE=networks,volumes` →
##           ssh `make node-update NODE=<n>` (AGE_SECRET_KEY из локальной цепочки node_detect).
##           Использование: GitHub Actions недоступны (Major Outage) / ручной деплой core.
## @scope    Called ONLY from Makefile (makefiles/bootstrap.mk core-deliver). Owns usage+main.
## @invariants
##   - НЕ трогает /opt/node-configs (орг-репозиторий, gitignored — доставлен bootstrap'ом)
##   - rsync-шаги с guard'ами (источник существует/непуст) — симметрия core-deploy.yml TRAP[BUG] 125 T4
##   - AGE_SECRET_KEY уходит в remote ТОЛЬКО как env (канон W4 DevPlan 140; --age-secret-key-file
##     читается ЛОКАЛЬНО, путь на remote НЕ передаётся)
##   - --dry-run: печатает rsync/ssh-команды без мутаций (R5 142 W5)
##   - Remote-команды используют ssh_opts SoT (lib/ssh.sh prepare_ssh_opts — канон bootstrap.sh)
## @rationale 142 W5 (Q4 «а»): fallback-таргет CI-канала. В циклах 1/2 141 GitHub Major Outage
##           (16:30-20:30) блокировал core-deploy — ручной эквивалент (rsync+provision+node-update)
##           воспроизводился вручную (A6). Имя core-deliver НЕ конфликтует с forbidden-глаголами.
## @changes 2026-08-06 | Created (142 W5)
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# repo_root — корень репозитория (источники rsync: core/, scripts/, makefiles/, platform-env.yaml,
# Makefile — зеркало core-deploy.yml, который rsync'ит из корня checkout на CI-раннере)
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${CORE_DIR}/lib/paths.sh"
source "${CORE_DIR}/lib/logging.sh"
source "${CORE_DIR}/lib/ssh.sh"
source "${CORE_DIR}/lib/node-resolver.sh"
source "${CORE_DIR}/lib/args.sh"

USAGE_SCRIPT="core-deliver.sh"
USAGE_DESC="Локальное зеркало core-deploy CI: rsync core → provision → node-update (fallback при GitHub Outage)."
USAGE_OPTIONS=(
    "--node <name>              Node name (required)"
    "--dry-run                  Print commands without executing"
    "--age-secret-key-file <f>  Path to AGE secret key (читается ЛОКАЛЬНО)"
)

NODE_NAME=""; DRY_RUN=false; AGE_SECRET_KEY_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node|--node-name) NODE_NAME="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --age-secret-key-file) AGE_SECRET_KEY_FILE="$2"; export AGE_SECRET_KEY_FILE; shift 2 ;;
        --help|-h) usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" ;;
        *) echo "[IMP:10][core-deliver][entrypoint] Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "${NODE_NAME}" ]]; then
    echo "[IMP:10][core-deliver][entrypoint] FATAL: --node is required" >&2
    echo "  Usage: core-deliver.sh --node <name> [--dry-run] [--age-secret-key-file <f>]" >&2
    exit 1
fi

## @purpose  Полный fallback-деплой core: resolve → rsync → provision → node-update.
main() {
    echo "[IMP:9][core-deliver][entrypoint] Starting core-deliver for NODE=${NODE_NAME}" >&2

    # ── Resolve node.yaml + SSH host ──
    local node_yaml ssh_host
    node_yaml="$(resolve_node_yaml "${NODE_NAME}" "${PLATFORM_ROOT}" "${HOME}/projects")" || {
        echo "[IMP:10][core-deliver][entrypoint] FATAL: Cannot resolve node.yaml for node=${NODE_NAME}" >&2
        exit 1
    }
    ssh_host="$(extract_node_host "${node_yaml}")" || ssh_host=""
    if [[ -z "${ssh_host}" ]]; then
        echo "[IMP:10][core-deliver][entrypoint] FATAL: No SSH host in node.yaml (${node_yaml}) — core-deliver требует remote VPS" >&2
        exit 1
    fi
    echo "[IMP:8][core-deliver][entrypoint] node.yaml=${node_yaml} host=${ssh_host}" >&2

    # ── Detect AGE key (локальная цепочка node_detect; exit 3 = key absent, non-fatal) ──
    local detected_age_key=""
    detected_age_key="$(python3 -m core.internal.shared.node_detect --detect-age-key 2>/dev/null)" || {
        local _detect_rc=$?
        if [[ ${_detect_rc} -eq 3 ]]; then
            detected_age_key=""
            echo "[IMP:7][core-deliver][entrypoint] WARN: AGE key not found — φ9 decrypt will be skipped" >&2
        else
            echo "[IMP:10][core-deliver][entrypoint] FATAL: python3 or node_detect unavailable" >&2
            exit 1
        fi
    }

    # ── Rsync core + config (guard'ы как core-deploy.yml, TRAP[BUG] 125 T4) ──
    if $DRY_RUN; then
        echo "[IMP:8][core-deliver][dry-run] WOULD rsync core/ + scripts/ + makefiles/ + platform-env.yaml → ${ssh_host}:/opt/platform/" >&2
    else
        prepare_ssh_opts "${ssh_host}" "update"
        # 1. core/ → /opt/platform/core/ (--delete, guard: непустой источник)
        if [[ -d "${REPO_ROOT}/core" ]] && [[ -n "$(ls -A "${REPO_ROOT}/core" 2>/dev/null)" ]]; then
            rsync -avz --delete \
                --exclude '.git/' --exclude '__pycache__/' --exclude '*.pyc' \
                --exclude 'default-user.xml' --exclude '.env' \
                "${REPO_ROOT}/core/" "root@${ssh_host}:/opt/platform/core/"
        else
            echo "[IMP:8][core-deliver][rsync] core/ absent/empty — rsync --delete SKIPPED (guard)" >&2
        fi
        # 2. platform-env.yaml + Makefile + makefiles/ → /opt/platform/ (guard, канон core-deploy.yml)
        if [[ -f "${REPO_ROOT}/platform-env.yaml" ]] && [[ -f "${REPO_ROOT}/Makefile" ]] && [[ -d "${REPO_ROOT}/makefiles" ]]; then
            rsync -avz \
                "${REPO_ROOT}/platform-env.yaml" "${REPO_ROOT}/Makefile" "${REPO_ROOT}/makefiles" \
                "root@${ssh_host}:/opt/platform/"
        else
            echo "[IMP:8][core-deliver][rsync] platform-env.yaml/Makefile/makefiles absent — skipped (guard)" >&2
        fi
        # 3. scripts/ → /opt/platform/scripts/ (REQ_FIX 141 r2 — синк с guard'ом)
        if [[ -d "${REPO_ROOT}/scripts" ]]; then
            ssh "${SSH_OPTS_COMMON[@]}" "root@${ssh_host}" "mkdir -p /opt/platform/scripts"
            rsync -avz "${REPO_ROOT}/scripts/" "root@${ssh_host}:/opt/platform/scripts/"
        else
            echo "[IMP:8][core-deliver][rsync] scripts/ absent — skipped (guard)" >&2
        fi
        echo "[IMP:9][core-deliver][rsync] Core + config + makefiles rsync complete" >&2
    fi

    # ── Provision networks + volumes (инвариант 1, канон core-deploy.yml step 5) ──
    if $DRY_RUN; then
        echo "[IMP:8][core-deliver][dry-run] WOULD ssh root@${ssh_host} 'cd /opt/platform && make provision SCOPE=networks,volumes'" >&2
    else
        echo "[IMP:9][core-deliver][provision] Provisioning networks+volumes on ${ssh_host}" >&2
        ssh "${SSH_OPTS_COMMON[@]}" "root@${ssh_host}" "cd /opt/platform && make provision SCOPE=networks,volumes" || {
            echo "[IMP:10][core-deliver][provision] FATAL: provision failed" >&2
            exit 1
        }
    fi

    # ── Node update (канон core-deploy.yml step 6: AGE_SECRET_KEY env + DEPLOY_PARALLEL) ──
    if $DRY_RUN; then
        local masked_key=""
        [[ -n "${detected_age_key}" ]] && masked_key="<AGE_KEY:${detected_age_key:0:8}...>"
        echo "[IMP:8][core-deliver][dry-run] WOULD ssh root@${ssh_host} 'cd /opt/platform && AGE_SECRET_KEY=${masked_key} DEPLOY_PARALLEL=true make node-update NODE=${NODE_NAME}'" >&2
        echo "[IMP:9][core-deliver][dry-run] DRY-RUN complete" >&2
        exit 0
    fi
    echo "[IMP:9][core-deliver][node-update] Running node-update NODE=${NODE_NAME} on ${ssh_host}" >&2
    ssh "${SSH_OPTS_COMMON[@]}" "root@${ssh_host}" \
        "cd /opt/platform && AGE_SECRET_KEY='${detected_age_key}' DEPLOY_PARALLEL=true make node-update NODE=${NODE_NAME}" || {
        echo "[IMP:10][core-deliver][node-update] FATAL: node-update failed" >&2
        exit 1
    }
    echo "[IMP:9][core-deliver][entrypoint] core-deliver COMPLETE (NODE=${NODE_NAME})" >&2
}

main "$@"
