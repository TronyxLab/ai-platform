#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint bootstrap node orchestrator node-resolver resolve ssh scp age-key detect-age-key dry-run batch-get-many
# STRUCTURE: ▶ init → ◇ --help? → ◇ --resolve? → ○ resolve+fields (bootstrap_resolver) → ◇ host? → ⚡ SCP core+node-configs → ⚡ SSH 'bash -s' (stdin: secret-prelude REF-0007 + remote-cmd) | ⎋ exec orchestrator --resume
# region MODULE_CONTRACT
## @purpose  Entry-point for `make bootstrap-node`: resolve node.yaml + fields → detect SSH host → SCP
##           core+node-configs → SSH-exec orchestrator (or local). Thin-wrapper per language policy.
## @scope    Called ONLY from Makefile; delegates: scp-deliver.sh/build-ssh-cmd.sh/node_detect.py/bootstrap_resolver.py (170 W9-F1); owns usage+main.
## @invariants
##   - NODE optional in --resolve (auto-detect из /opt/node-configs/); ci_deploy_key: node.yaml SoT (D2)
##   - AGE_SECRET_KEY chain (node_detect.py): env → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE → default key ~/.config/age/keys.txt (age CLI default); missing = WARN (not fatal)
##   - Резолв полей + owner_key-валидация + host — bootstrap_resolver (exit 0/1/2, single source)
## @rationale Thin-wrapper per DevPlan 020 T4+T15; secrets/passthrough решения — DevPlan 118 B6 (закрыты).
## ⚠️ TRAP[KEEP] · 173 W2.5 · bootstrap.sh НЕ переписывается: SCP/SSH exec + age-chain — легитимная shell-оркестрация; бизнес-логика уже в bootstrap_resolver.py/node_detect.py/build-ssh-cmd · Rev: при остаточном парсинге вне resolver — извлечь.
## @changes 2026-08-15 170 W9-F1 — tab-парсинг → bootstrap_resolver.py (<100 LOC); 2026-08-03 RC 121; 2026-08-01 B3 T5/T6; 2026-07-31 DevPlan 104; 2026-07-21 W4; 2026-08-24 REF-0007 — ключи вне argv: ssh 'bash -s' + stdin prelude (masking-код dry-run удалён); 2026-09-02 DevPlan 029 T6 — overlay-key step: install-node-deploy-key (context_initializer) после SCP-фазы
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
        --age-secret-key-file) AGE_SECRET_KEY_FILE="$2"; export AGE_SECRET_KEY_FILE; shift 2 ;; # TRAP[BUG] 2026-08-03 P1: читается ЛОКАЛЬНО (node_detect-цепочка)
        *) PASSTHROUGH_ARGS+=("$1"); shift ;;
    esac
done
## @purpose  Execute bootstrap workflow — resolve mode or passthrough
main() {
    if ! $RESOLVE_MODE; then exec "${NODE_LIFECYCLE}" "--mode" "init" "$@"; fi
    if [[ -z "$NODE_NAME" ]]; then
        echo "[IMP:8][bootstrap][entrypoint] Auto-detecting node name"
        NODE_NAME=$(python3 -m core.internal.shared.node_detect --detect-node-name 2>/dev/null) || { echo "[IMP:10][bootstrap][entrypoint] FATAL: Cannot detect node — use make bootstrap-node NODE=<name>" >&2; exit 1; }
    fi
    echo "[IMP:8][bootstrap][entrypoint] Resolving node.yaml + fields via bootstrap_resolver (node=${NODE_NAME})"
    RESOLVE_OUTPUT="$(python3 -m core.internal.bootstrap.bootstrap_resolver resolve --node "${NODE_NAME}" --platform-root "${PLATFORM_ROOT}")" || {
        local _resolve_rc=$?; echo "[IMP:10][bootstrap][entrypoint] FATAL: bootstrap_resolver resolve failed (rc=${_resolve_rc})" >&2; exit "${_resolve_rc}"
    }
    while IFS='=' read -r _k _v; do
        case "$_k" in
            owner_key) OWNER_KEY="$_v" ;; ci_deploy_key) CI_DEPLOY_KEY="$_v" ;; ci_root_key) CI_ROOT_KEY="$_v" ;;
            platform_domain) PLATFORM_DOMAIN="$_v" ;; context) CONTEXT="$_v" ;; host) SSH_HOST="$_v" ;; node_yaml_path) NODE_YAML="$_v" ;;
        esac
    done <<< "${RESOLVE_OUTPUT}"
    [[ -n "$CI_DEPLOY_KEY" ]] && echo "[IMP:9][bootstrap][entrypoint] ci_deploy_key resolved" || echo "[IMP:8][bootstrap][entrypoint] ci_deploy_key not set — ci-deploy restricted key setup skipped"
    [[ -n "$CI_ROOT_KEY" ]] && echo "[IMP:9][bootstrap][entrypoint] ci_root_key resolved" || echo "[IMP:7][bootstrap][entrypoint] ci_root_key not set — CI root-shell канал (core-deploy) недоступен" # 142 W1 (A1)
    # node_detect exit contract (104 D3): 0=key found, 3=module OK+key absent (non-fatal), other=FATAL
    DETECTED_AGE_KEY="$(python3 -m core.internal.shared.node_detect --detect-age-key 2>/dev/null)" || { _detect_rc=$?; [[ ${_detect_rc} -eq 3 ]] && DETECTED_AGE_KEY="" || { echo "[IMP:10][bootstrap][entrypoint] FATAL: node_detect unavailable" >&2; exit 1; }; }
    if [[ -z "${SSH_HOST}" ]]; then
        local a=(--node-name "$NODE_NAME" --node-yaml "$NODE_YAML" --owner-key "$OWNER_KEY" --resume)
        [[ -n "${DETECTED_AGE_KEY}" ]] && a+=(--age-secret-key "${DETECTED_AGE_KEY}"); [[ -n "${CI_DEPLOY_KEY}" ]] && a+=(--ci-deploy-key "${CI_DEPLOY_KEY}")
        [[ -n "${CI_ROOT_KEY}" ]] && a+=(--ci-root-key "${CI_ROOT_KEY}"); [[ -n "${PLATFORM_DOMAIN:-}" ]] && a+=(--platform-domain "${PLATFORM_DOMAIN}")
        [[ -n "${CONTEXT:-}" ]] && a+=(--context "${CONTEXT}")
        a+=("${PASSTHROUGH_ARGS[@]}")
        $DRY_RUN && { echo "[IMP:8][bootstrap][dry-run] DRY-RUN: ${NODE_LIFECYCLE} ${a[*]}" >&2; exit 0; }
        exec "${NODE_LIFECYCLE}" "--mode" "init" "${a[@]}"
    fi
    NODE_CONFIGS_DIR="$(dirname "$(dirname "${NODE_YAML}")")"
    if $DRY_RUN; then
        echo "[IMP:8][bootstrap][dry-run] DRY-RUN: Would rsync core/ + platform-env.yaml + Makefile + node-configs/ → ${SSH_HOST}"
        [[ -d "${NODE_CONFIGS_DIR}/${NODE_NAME}/secrets" ]] && echo "[IMP:8][bootstrap][dry-run] DRY-RUN: Would rsync secrets/"
        echo "[IMP:8][bootstrap][dry-run] DRY-RUN: Would install context overlay deploy key + github.com-overlay ssh alias on node (T6)"
    else
        prepare_ssh_opts "${SSH_HOST}" "init"
        scp_to_server "${SSH_HOST}" "${NODE_NAME}" "${NODE_CONFIGS_DIR}" "${CORE_DIR}" || { echo "[IMP:10][bootstrap][entrypoint] FATAL: SCP phase failed" >&2; exit 1; }
        echo "[IMP:9][bootstrap][scp] SCP phase complete"
        # DevPlan 029 T6 (AC5): node-side overlay deploy key + ssh-алиас github.com-overlay
        # по SSH/core-каналу — нода достижима именно здесь (после SCP-фазы). Контексты без
        # alias repos.core или без dev-ключа → python exit 0 (skip, без шума).
        python3 -m core.internal.scaffold.context_initializer install-node-deploy-key --node-yaml "${NODE_YAML}" --ssh-host "${SSH_HOST}" || { echo "[IMP:10][bootstrap][overlay-key] FATAL: overlay deploy-key install failed" >&2; exit 1; }
    fi
    # 🧐 TRAP[DECISION] · 2026-08-24 · stdin→bash -s вместо SCP 0600 root-file+unset · Rejected: prelude-файл на ноде · Reason: crash между scp и rm оставляет plaintext-ключ на диске (SEC-0015 класс), stdin не оставляет артефактов · Rev: потоковый канал >1MB prelude (не ожидается) — пересмотреть
    REMOTE_CMD="$(build_ssh_cmd "${NODE_NAME}" "${OWNER_KEY}" "${CI_DEPLOY_KEY}" "${DETECTED_AGE_KEY}" "${CI_ROOT_KEY}" "${PASSTHROUGH_ARGS[@]}")"; SECRET_PRELUDE="$(build_init_secret_prelude "${CI_DEPLOY_KEY}" "${DETECTED_AGE_KEY}" "${CI_ROOT_KEY}")"
    # DRY-RUN печатает ТОЛЬКО тело (значения prelude НЕ логируются — размер в байтах)
    $DRY_RUN && { echo "[IMP:8][bootstrap][dry-run] DRY-RUN: ssh ${SSH_OPTS_COMMON[*]} root@${SSH_HOST} 'bash -s' <<< stdin(prelude=${#SECRET_PRELUDE}B [redacted] + remote-cmd)" >&2; echo "[IMP:8][bootstrap][dry-run] DRY-RUN remote cmd: ${REMOTE_CMD}" >&2; echo "[IMP:9][bootstrap][dry-run] DRY-RUN complete" >&2; exit 0; }
    echo "[IMP:9][bootstrap][entrypoint] SSH node-lifecycle.sh --mode init on root@${SSH_HOST} (keys via stdin prelude)"
    ssh_exec_stdin "${SSH_HOST}" "${SECRET_PRELUDE}" "${REMOTE_CMD}"
    # P0 (017): deliver context project payloads (operator sources ~/projects/<ctx>/<p>/) after SSH init — awaiting_deploy → live (orchestrator receive, compose up; rc: 0 ok / 2 failed)
    echo "[IMP:8][bootstrap][projects] Delivering context project payloads (node=${NODE_NAME})"
    python3 -m core.internal.deploy.project_payload_delivery --node "${NODE_NAME}" --node-yaml "${NODE_YAML}" || { _ppd_rc=$?; echo "[IMP:10][bootstrap][projects] FATAL: project payload delivery failed (rc=${_ppd_rc}) — ≥1 context project not live" >&2; exit "${_ppd_rc}"; }
}
main "$@"
