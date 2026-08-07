#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint bootstrap node orchestrator node-resolver resolve ssh scp age-key detect-age-key dry-run batch-get-many
# STRUCTURE: ▶ init → ◇ --help? → ◇ --resolve? → ○ resolve_node_yaml → ○ batch-extract node.yaml (--get-many) → ○ extract host → ◇ host? → ⚡ SCP core+node-configs → ⚡ SSH orchestrator | ⎋ exec orchestrator --resume
# region MODULE_CONTRACT
## @purpose  Entry-point for `make bootstrap-node`: resolve node.yaml → detect SSH host → SCP
##           core+node-configs → SSH-exec orchestrator (or local). Thin-wrapper per language policy.
## @scope    Called ONLY from Makefile. Owns usage+main. Delegates: scp/ssh → scp-deliver.sh,
##           build-ssh-cmd.sh; AGE key + node detection → node_detect.py (DevPlan 104); node.yaml → --get-many.
## @invariants
##   - 2 functions max: usage, main
##   - NODE=<name> optional in --resolve mode (auto-detection from /opt/node-configs/)
##   - AGE_SECRET_KEY chain (node_detect.py): env → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE →
##     default key file ~/.config/age/keys.txt (age CLI default, symlink-конвенция); missing = WARN (not fatal)
##   - ci_deploy_key: node.yaml единственный SoT (D2, B3 T6); --dry-run печатает SCP+SSH; --resume всегда
## 🧐 TRAP[DECISION] · 2026-07-21 · — · secrets: <node-configs-dir>/secrets/<NODE>.enc.yaml (без symlink-поиска); подтверждено 2026-08-05 (Rev CI auto-deploy наступил и закрыт, DevPlan 118 B6)
## 🧐 TRAP[DECISION] · 2026-07-21 · — · passthrough arg pattern (не полный parse_args); подтверждено 2026-08-05 (Rev Wave 4 — parse_args spec наступил и закрыт, DevPlan 118 B6)
## @rationale Thin-wrapper per DevPlan 020 T4+T15 — auto-SSH+SCP eliminates manual rsync/SSH steps.
## @changes 2026-07-17 T15 layer re-homing; 2026-07-21 W4 --auto-reconcile; 2026-07-31 DevPlan 104;
##           2026-08-01 B3 T5/T6 — --get-many, env-override удалён (D2); 2026-08-03 RC 121 — age-key-file локально
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${CORE_DIR}/lib/paths.sh"
source "${CORE_DIR}/lib/logging.sh"
source "${CORE_DIR}/internal/bootstrap/scp-deliver.sh"
source "${CORE_DIR}/internal/bootstrap/build-ssh-cmd.sh"
source "${CORE_DIR}/lib/args.sh"
NODE_LIFECYCLE="${PATHS_INTERNAL_DIR}/bootstrap/node-lifecycle.sh"
USAGE_SCRIPT="bootstrap.sh"
USAGE_DESC="Entry-point for idempotent node bootstrap. Resolves node.yaml, detects SSH host, SCPs core+node-configs, delegates to node-lifecycle.sh."
USAGE_OPTIONS=(
    "--node <name>       Node name to bootstrap"
    "--resolve           Extract node.yaml fields + host from node.yaml"
    "--dry-run           Print SCP+SSH commands without executing"
    "--auto-reconcile    Passthrough to node-lifecycle.sh --reconcile"
    "--age-secret-key-file <f>  Path to AGE secret key (читается ЛОКАЛЬНО, ключ уходит в remote как env)"
)
NODE_NAME=""; RESOLVE_MODE=false; DRY_RUN=false; PASSTHROUGH_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --node|--node-name) NODE_NAME="$2"; shift 2 ;;
        --help|-h) usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" ;;
        --resolve) RESOLVE_MODE=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --auto-reconcile) PASSTHROUGH_ARGS+=("--auto-reconcile"); shift ;;
        # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · --age-secret-key-file уходил в remote passthrough
        # (локальный путь на VPS). Фикс: локальное чтение через node_detect-цепочку (как node-update.sh).
        --age-secret-key-file) AGE_SECRET_KEY_FILE="$2"; export AGE_SECRET_KEY_FILE; shift 2 ;;
        *) PASSTHROUGH_ARGS+=("$1"); shift ;;
    esac
done

## @purpose  Execute bootstrap workflow — resolve mode or passthrough
main() {
    if ! $RESOLVE_MODE; then exec "${NODE_LIFECYCLE}" "--mode" "init" "$@"; fi

    # ── Validate/resolve node name ──────────────────────────────────
    if [[ -z "$NODE_NAME" ]]; then
        echo "[IMP:8][bootstrap][entrypoint] Auto-detecting node name"
        NODE_NAME=$(python3 -m core.internal.shared.node_detect --detect-node-name 2>/dev/null) || {
            echo "[IMP:10][bootstrap][entrypoint] FATAL: Cannot detect node — use make bootstrap-node NODE=<name>" >&2; exit 1
        }
        echo "[IMP:9][bootstrap][entrypoint] Auto-detected NODE_NAME=${NODE_NAME}"
    fi
    source "${CORE_DIR}/lib/node-resolver.sh"
    echo "[IMP:8][bootstrap][entrypoint] Resolving node.yaml for node=${NODE_NAME}"
    NODE_YAML=$(resolve_node_yaml "$NODE_NAME" "${PLATFORM_ROOT}" "${HOME}/projects") || {
        echo "[IMP:10][bootstrap][entrypoint] FATAL: Cannot resolve node.yaml" >&2; exit 1
    }

    # ── Batch-extract node.yaml fields (B3 T5, U-52): ONE --get-many call ──
    echo "[IMP:8][bootstrap][entrypoint] Batch-extracting node.yaml fields (--get-many)"
    # Волна 117 D8: stderr не глотаем — отсутствующий ключ = rc 0 + пустое значение (OK), файл не читается = rc 2/3/4 (WARN)
    local _batch_err; _batch_err="$(mktemp)"
    if BATCH_OUTPUT="$(python3 -m core.internal.shared.node_yaml --file "${NODE_YAML}" --get-many owner_key:node.owner_key,ci_deploy_key:node.ci_deploy_key,ci_root_key:node.ci_root_key,platform_domain:domain,context:context,context0:contexts.0.name 2>"${_batch_err}")"; then
        : # rc=0 — ключи могут отсутствовать → пустые значения (легитимно)
    else
        local _batch_rc=$?
        log_imp 7 "node-yaml" "Batch field extraction failed (rc=${_batch_rc}): $(tr '\n' ' ' < "${_batch_err}" | cut -c1-300)"
    fi
    rm -f "${_batch_err}"
    OWNER_KEY=""; CI_DEPLOY_KEY=""; CI_ROOT_KEY=""; PLATFORM_DOMAIN=""; CONTEXT=""; CONTEXT0=""
    while IFS=$'\t' read -r alias value; do
        case "$alias" in
            owner_key)      OWNER_KEY="$value" ;;
            ci_deploy_key)  CI_DEPLOY_KEY="$value" ;;
            ci_root_key)    CI_ROOT_KEY="$value" ;;
            platform_domain) PLATFORM_DOMAIN="$value" ;;
            context)        CONTEXT="$value" ;;
            context0)       CONTEXT0="$value" ;;
        esac
    done <<< "$BATCH_OUTPUT"
    [[ -z "$CONTEXT" ]] && CONTEXT="$CONTEXT0"   # fallback: top-level context > contexts.0.name

    [[ -n "$OWNER_KEY" ]] || { echo "[IMP:10][bootstrap][entrypoint] FATAL: owner_key not found" >&2; exit 1; }
    echo "[IMP:9][bootstrap][entrypoint] Resolved: node=${NODE_NAME}"

    # ⚠️ TRAP[BUG] 2026-07-17 P1 RESOLVED 2026-08-01 (B3 T5/T6): ci_deploy_key — batch --get-many, env-override удалён (D2)
    if [[ -n "$CI_DEPLOY_KEY" ]]; then
        echo "[IMP:9][bootstrap][entrypoint] ci_deploy_key resolved"
    else
        echo "[IMP:8][bootstrap][entrypoint] ci_deploy_key not set — ci-deploy restricted key setup will be skipped"
    fi
    # 142 W1 (A1): ci_root_key — ПУБЛИЧНАЯ часть VPS_SSH_KEY (core-deploy root-канал).
    # Поле node.ci_root_key добавлено префлайтом P0.1; отсутствие → WARN (не FATAL —
    # CI-root ключ не блокирует локальный bootstrap, только core-deploy после него).
    if [[ -n "$CI_ROOT_KEY" ]]; then
        echo "[IMP:9][bootstrap][entrypoint] ci_root_key resolved"
    else
        echo "[IMP:7][bootstrap][entrypoint] ci_root_key not set — CI root-shell канал (core-deploy) будет недоступен"
    fi
    [[ -n "$PLATFORM_DOMAIN" ]] && echo "[IMP:9][bootstrap][entrypoint] PLATFORM_DOMAIN=${PLATFORM_DOMAIN}"
    [[ -n "$CONTEXT" ]] && echo "[IMP:9][bootstrap][entrypoint] CONTEXT=${CONTEXT}"

    SSH_HOST="$(extract_node_host "${NODE_YAML}")" || { echo "[IMP:8][bootstrap][entrypoint] WARN: No SSH host — local mode" >&2; SSH_HOST=""; }
    # node_detect exit contract (DevPlan 104 D3): 0=key found, 3=module OK+key absent (non-fatal), other=FATAL
    DETECTED_AGE_KEY="$(python3 -m core.internal.shared.node_detect --detect-age-key 2>/dev/null)" || {
        _detect_rc=$?
        if [[ ${_detect_rc} -eq 3 ]]; then
            DETECTED_AGE_KEY=""
        else
            echo "[IMP:10][bootstrap][entrypoint] FATAL: python3 or core.internal.shared.node_detect unavailable" >&2; exit 1
        fi
    }

    # ── Local bootstrap ─────────────────────────────────────────────
    if [[ -z "${SSH_HOST}" ]]; then
        echo "[IMP:9][bootstrap][entrypoint] No SSH host — executing node-lifecycle.sh --mode init LOCALLY"
        local a=(--node-name "$NODE_NAME" --node-yaml "$NODE_YAML" --owner-key "$OWNER_KEY" --resume)
        [[ -n "${DETECTED_AGE_KEY}" ]] && a+=(--age-secret-key "${DETECTED_AGE_KEY}")
        [[ -n "${CI_DEPLOY_KEY}" ]] && a+=(--ci-deploy-key "${CI_DEPLOY_KEY}")
        [[ -n "${CI_ROOT_KEY}" ]] && a+=(--ci-root-key "${CI_ROOT_KEY}")
        [[ -n "${PLATFORM_DOMAIN:-}" ]] && a+=(--platform-domain "${PLATFORM_DOMAIN}")
        [[ -n "${CONTEXT:-}" ]] && a+=(--context "${CONTEXT}")
        a+=("${PASSTHROUGH_ARGS[@]}")
        $DRY_RUN && { echo "[IMP:8][bootstrap][dry-run] DRY-RUN: ${NODE_LIFECYCLE} ${a[*]}" >&2; exit 0; }
        exec "${NODE_LIFECYCLE}" "--mode" "init" "${a[@]}"
    fi

    echo "[IMP:9][bootstrap][entrypoint] SSH host: ${SSH_HOST} — REMOTE bootstrap"
    NODE_CONFIGS_DIR="$(dirname "$(dirname "${NODE_YAML}")")"
    if $DRY_RUN; then
        echo "[IMP:8][bootstrap][dry-run] DRY-RUN: Would rsync core/ + platform-env.yaml + Makefile + node-configs/ → ${SSH_HOST}"
        [[ -d "${NODE_CONFIGS_DIR}/${NODE_NAME}/secrets" ]] && echo "[IMP:8][bootstrap][dry-run] DRY-RUN: Would rsync secrets/"
    else
        prepare_ssh_opts "${SSH_HOST}" "init"
        scp_to_server "${SSH_HOST}" "${NODE_NAME}" "${NODE_CONFIGS_DIR}" "${CORE_DIR}" || { echo "[IMP:10][bootstrap][entrypoint] FATAL: SCP phase failed" >&2; exit 1; }
        echo "[IMP:9][bootstrap][scp] SCP phase complete"
    fi

    REMOTE_CMD="$(build_ssh_cmd "${NODE_NAME}" "${OWNER_KEY}" "${CI_DEPLOY_KEY}" "${DETECTED_AGE_KEY}" "${CI_ROOT_KEY}" "${PASSTHROUGH_ARGS[@]}")"
    local masked_remote_cmd="${REMOTE_CMD}"
    [[ -n "${DETECTED_AGE_KEY}" ]] && { local m; m="$(echo "${DETECTED_AGE_KEY}" | cut -c1-8)"; masked_remote_cmd="${REMOTE_CMD//${DETECTED_AGE_KEY}/<AGE_KEY:${m}...>}"; }
    $DRY_RUN && { echo "[IMP:8][bootstrap][dry-run] DRY-RUN: ssh ${SSH_OPTS_COMMON[*]} root@${SSH_HOST} ${masked_remote_cmd}" >&2; echo "[IMP:9][bootstrap][dry-run] DRY-RUN complete" >&2; exit 0; }
    echo "[IMP:9][bootstrap][entrypoint] SSH node-lifecycle.sh --mode init on root@${SSH_HOST}"
    # Волна 117 D7: exec ssh → SSH_OPTS_COMMON (lib/ssh.sh, Python SoT ssh_opts.py); exec + DRY_RUN семантика сохранены
    exec ssh "${SSH_OPTS_COMMON[@]}" "root@${SSH_HOST}" "${REMOTE_CMD}"
}

main "$@"
