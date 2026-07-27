# shellcheck shell=bash
# GREP_SUMMARY: bootstrap remote-cmd build_ssh_cmd build_update_ssh_cmd ssh-command quoting printf or node-yaml owner-key age-key overlay_deliverer
# STRUCTURE: ▶ ┌build_{ssh,update,converge}_cmd (printf %q)┐ → ○ _resolve_and_extract (Python CLI) → ◇ execute_remote_{update,converge,reconcile} → ◇ deliver_vhost_overlays (Python facade)
# region MODULE_CONTRACT
## @purpose  Shell facade for remote SSH proxy operations. Delegates node resolution,
##           host extraction, vhost overlay delivery, and core rsync to Python
##           overlay_deliverer.py. Retains printf %q command builders per D3.
## @scope    Sourced by bootstrap.sh, node-update.sh, converge.sh. Provides build+execute functions.
## @invariants — printf %q for shell-safe quoting; AGE_SECRET_KEY exported via env only
##              — _resolve_and_extract() calls Python CLI for node resolution
##              — Returns 2 for local fallback (no SSH host), 1 for fatal errors
##              — DRY_RUN global variable controls dry-run mode
## @rationale Strangler-Fig: 672→~230 LOC shell facade + ~200 LOC Python module. D3: printf %q stays.
## @changes 2026-07-26 | TASK-036D — Wave 5d Strangler: migrated deliver/extract/resolve to Python
# endregion MODULE_CONTRACT

# ── Source paths.sh + ssh.sh ─────────────────────────────────────────
if [[ -z "${PATHS_LIB_DIR:-}" ]]; then
    _CMD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../../lib/paths.sh
    source "${_CMD_DIR}/../../lib/paths.sh"
    unset _CMD_DIR
fi
# shellcheck source=../../lib/ssh.sh
source "${PATHS_LIB_DIR}/ssh.sh"

OVERLAY_DELIVERER="python3 -m core.internal.bootstrap.overlay_deliverer"

# ══════════════════════════════════════════════════════════════════════
# BUILD SSH CMD — init mode (printf %q)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_ssh_cmd
build_ssh_cmd() {
    local node_name="$1" owner_key="$2" ci_deploy_key="$3" age_key="$4"
    shift 4; local passthrough_args=("$@")
    local remote_orchestrator="${PLATFORM_ROOT:-/opt/platform}/core/internal/bootstrap/node-lifecycle.sh"
    local remote_node_yaml="/opt/node-configs/${node_name}/node.yaml"
    local cmd="set -euo pipefail"

    if [[ -n "${age_key}" ]]; then
        local q; q="$(printf '%q' "${age_key}")"; cmd+=" && export AGE_SECRET_KEY=${q}"
    fi
    # ⚠️ TRAP[BUG] · 2026-07-17 · P2 · ci_deploy_key from node.yaml not exported
    # · Fix: fallback to ci_deploy_key parameter when env var is unset.
    local effective_ci_key="${PLATFORM_CI_DEPLOY_KEY:-${ci_deploy_key:-}}"
    if [[ -n "${effective_ci_key}" ]]; then
        local q; q="$(printf '%q' "${effective_ci_key}")"; cmd+=" && export PLATFORM_CI_DEPLOY_KEY=${q}"
    fi
    if [[ -n "${PLATFORM_DOMAIN:-}" ]]; then
        local q; q="$(printf '%q' "${PLATFORM_DOMAIN}")"; cmd+=" && export PLATFORM_DOMAIN=${q}"
    fi
    if [[ -n "${CONTEXT:-}" ]]; then
        local q; q="$(printf '%q' "${CONTEXT}")"; cmd+=" && export CONTEXT=${q}"
    fi

    cmd+=" && bash $(printf '%q' "${remote_orchestrator}")"
    cmd+=" $(printf '%q' '--mode') $(printf '%q' 'init')"
    cmd+=" $(printf '%q' '--node-name') $(printf '%q' "${node_name}")"
    cmd+=" $(printf '%q' '--node-yaml') $(printf '%q' "${remote_node_yaml}")"
    cmd+=" $(printf '%q' '--owner-key') $(printf '%q' "${owner_key}")"
    if [[ -n "${ci_deploy_key}" ]]; then
        cmd+=" $(printf '%q' '--ci-deploy-key') $(printf '%q' "${ci_deploy_key}")"
    fi
    cmd+=" $(printf '%q' '--resume')"
    for arg in "${passthrough_args[@]}"; do cmd+=" $(printf '%q' "${arg}")"; done
    echo "${cmd}"
}
# endregion FUNC_build_ssh_cmd

# ══════════════════════════════════════════════════════════════════════
# BUILD UPDATE SSH CMD — update mode (printf %q, no --owner-key, no --resume D2)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_update_ssh_cmd
build_update_ssh_cmd() {
    local node_name="$1" age_key="$2"
    shift 2; local passthrough_args=("$@")
    local remote_orchestrator="${PLATFORM_ROOT:-/opt/platform}/core/internal/bootstrap/node-lifecycle.sh"
    local remote_node_yaml="/opt/node-configs/${node_name}/node.yaml"
    local cmd="set -euo pipefail"

    if [[ -n "${age_key}" ]]; then
        local q; q="$(printf '%q' "${age_key}")"; cmd+=" && export AGE_SECRET_KEY=${q}"
    fi
    if [[ -n "${PLATFORM_DOMAIN:-}" ]]; then
        local q; q="$(printf '%q' "${PLATFORM_DOMAIN}")"; cmd+=" && export PLATFORM_DOMAIN=${q}"
    fi
    if [[ -n "${CONTEXT:-}" ]]; then
        local q; q="$(printf '%q' "${CONTEXT}")"; cmd+=" && export CONTEXT=${q}"
    fi

    cmd+=" && bash $(printf '%q' "${remote_orchestrator}")"
    cmd+=" $(printf '%q' '--mode') $(printf '%q' 'update')"
    cmd+=" $(printf '%q' '--node-name') $(printf '%q' "${node_name}")"
    cmd+=" $(printf '%q' '--node-yaml') $(printf '%q' "${remote_node_yaml}")"
    for arg in "${passthrough_args[@]}"; do cmd+=" $(printf '%q' "${arg}")"; done
    echo "${cmd}"
}
# endregion FUNC_build_update_ssh_cmd

# ══════════════════════════════════════════════════════════════════════
# BUILD CONVERGE SSH CMD (printf %q)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_converge_ssh_cmd
build_converge_ssh_cmd() {
    local node_name="$1"; shift 1; local passthrough_args=("$@")
    local remote_converge="${PLATFORM_ROOT:-/opt/platform}/core/internal/bootstrap/converge.sh"
    local cmd="set -euo pipefail && bash $(printf '%q' "${remote_converge}")"
    cmd+=" $(printf '%q' '--node') $(printf '%q' "${node_name}")"
    for arg in "${passthrough_args[@]}"; do cmd+=" $(printf '%q' "${arg}")"; done
    echo "${cmd}"
}
# endregion FUNC_build_converge_ssh_cmd

# ══════════════════════════════════════════════════════════════════════
# RESOLVE + EXTRACT HELPER (calls Python CLI)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_resolve_and_extract
## @globals RESOLVED_NODE_YAML RESOLVED_SSH_HOST
_resolve_and_extract() {
    local node_name="$1"
    RESOLVED_NODE_YAML="$(${OVERLAY_DELIVERER} resolve-node --node "${node_name}")" || {
        echo "[IMP:10][remote-cmd] FATAL: Cannot resolve node.yaml for node=${node_name}" >&2; return 1
    }
    echo "[IMP:9][remote-cmd] Resolved node.yaml: ${RESOLVED_NODE_YAML}" >&2
    RESOLVED_SSH_HOST="$(${OVERLAY_DELIVERER} extract-host --yaml "${RESOLVED_NODE_YAML}")" || RESOLVED_SSH_HOST=""
    if [[ -z "${RESOLVED_SSH_HOST}" ]]; then
        echo "[IMP:9][remote-cmd] No SSH host — local fallback" >&2; return 2
    fi
}
# endregion FUNC_resolve_and_extract

# ══════════════════════════════════════════════════════════════════════
# EXECUTE REMOTE UPDATE
# ══════════════════════════════════════════════════════════════════════
# region FUNC_execute_remote_update
execute_remote_update() {
    local node_name="$1" detected_age_key="$2"; shift 2; local passthrough_args=("$@")
    local _eru_dir; _eru_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=./scp-deliver.sh
    source "${_eru_dir}/scp-deliver.sh"

    _resolve_and_extract "${node_name}" || { local rc=$?; return ${rc}; }

    # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · VPS self-SSH loop: detect /opt/platform/ → local exec
    if [[ -f "/opt/platform/core/internal/bootstrap/node-lifecycle.sh" ]]; then
        echo "[IMP:9][node-update] Local VPS detected — skipping SSH proxy" >&2; return 2
    fi

    echo "[IMP:9][node-update] SSH host: ${RESOLVED_SSH_HOST} — REMOTE update" >&2
    prepare_ssh_opts "${RESOLVED_SSH_HOST}" "update"

    local core_src="${CORE_DIR:-$(cd "${_eru_dir}/../.." && pwd)}"
    if ${DRY_RUN:-false}; then
        ${OVERLAY_DELIVERER} sync-core --host "${RESOLVED_SSH_HOST}" --core-src "${core_src}" \
            --node "${node_name}" --node-yaml "${RESOLVED_NODE_YAML}" --dry-run
    else
        ${OVERLAY_DELIVERER} sync-core --host "${RESOLVED_SSH_HOST}" --core-src "${core_src}" \
            --node "${node_name}" --node-yaml "${RESOLVED_NODE_YAML}" || {
            echo "[IMP:10][node-update] FATAL: sync-core failed" >&2; return 1
        }
    fi

    local remote_cmd; remote_cmd="$(build_update_ssh_cmd "${node_name}" "${detected_age_key}" "${passthrough_args[@]}")"
    if ${DRY_RUN:-false}; then
        echo "[IMP:8][node-update][dry-run] DRY-RUN: ssh ... root@${RESOLVED_SSH_HOST}" >&2
        exit 0
    fi
    # ⚠️ TRAP[BUG] · 2026-07-24 · P4 · bare ssh_exec may silently fail under set -e
    echo "[IMP:9][node-update] Executing node-lifecycle.sh --mode update on root@${RESOLVED_SSH_HOST}" >&2
    ssh_exec "${RESOLVED_SSH_HOST}" "root" "${remote_cmd}" "" "deploy" || {
        local rc=$?; log_imp 1 "execute_remote_update" "SSH exec failed — exit=${rc}"; return "${rc}"
    }
}
# endregion FUNC_execute_remote_update

# ══════════════════════════════════════════════════════════════════════
# EXECUTE REMOTE CONVERGE
# ══════════════════════════════════════════════════════════════════════
# region FUNC_execute_remote_converge
execute_remote_converge() {
    local node_name="$1"; shift 1; local passthrough_args=("$@")
    local _erc_dir; _erc_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=./scp-deliver.sh
    source "${_erc_dir}/scp-deliver.sh"

    _resolve_and_extract "${node_name}" || { local rc=$?; return ${rc}; }
    echo "[IMP:9][converge] SSH host: ${RESOLVED_SSH_HOST} — REMOTE converge" >&2
    prepare_ssh_opts "${RESOLVED_SSH_HOST}" "update"

    local remote_cmd; remote_cmd="$(build_converge_ssh_cmd "${node_name}" "${passthrough_args[@]}")"
    if ${DRY_RUN:-false}; then
        echo "[IMP:8][converge][dry-run] DRY-RUN: ssh ... root@${RESOLVED_SSH_HOST}" >&2; exit 0
    fi
    # ⚠️ TRAP[BUG] · 2026-07-24 · P4 · bare ssh_exec may silently fail under set -e
    echo "[IMP:9][converge] Executing converge.sh on root@${RESOLVED_SSH_HOST}" >&2
    ssh_exec "${RESOLVED_SSH_HOST}" "root" "${remote_cmd}" "" "deploy" || {
        local rc=$?; log_imp 1 "execute_remote_converge" "SSH exec failed — exit=${rc}"; return "${rc}"
    }
}
# endregion FUNC_execute_remote_converge

# ══════════════════════════════════════════════════════════════════════
# DELIVER VHOST OVERLAYS (Python facade)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_deliver_vhost_overlays
deliver_vhost_overlays() {
    local node_name="$1"
    ${OVERLAY_DELIVERER} deliver --node "${node_name}" ${DRY_RUN:+--dry-run}
}
# endregion FUNC_deliver_vhost_overlays

# ══════════════════════════════════════════════════════════════════════
# EXECUTE REMOTE RECONCILE
# ══════════════════════════════════════════════════════════════════════
# region FUNC_execute_remote_reconcile
execute_remote_reconcile() {
    local node_name="$1"; shift 1; local passthrough_args=("$@")
    local _err_dir; _err_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=./scp-deliver.sh
    source "${_err_dir}/scp-deliver.sh"

    _resolve_and_extract "${node_name}" || { local rc=$?; return ${rc}; }
    echo "[IMP:9][reconcile] SSH host: ${RESOLVED_SSH_HOST} — REMOTE reconcile" >&2
    prepare_ssh_opts "${RESOLVED_SSH_HOST}" "update"

    local remote_cmd; remote_cmd="$(build_converge_ssh_cmd "${node_name}" "--reconcile" "${passthrough_args[@]}")"
    if ${DRY_RUN:-false}; then
        echo "[IMP:8][reconcile][dry-run] DRY-RUN: ssh ... root@${RESOLVED_SSH_HOST}" >&2; exit 0
    fi
    # ⚠️ TRAP[BUG] · 2026-07-24 · P4 · bare ssh_exec may silently fail under set -e
    echo "[IMP:9][reconcile] Executing converge --reconcile on root@${RESOLVED_SSH_HOST}" >&2
    ssh_exec "${RESOLVED_SSH_HOST}" "root" "${remote_cmd}" "" "deploy" || {
        local rc=$?; log_imp 1 "execute_remote_reconcile" "SSH exec failed — exit=${rc}"; return "${rc}"
    }
}
# endregion FUNC_execute_remote_reconcile

# ══════════════════════════════════════════════════════════════════════
# EXECUTE REMOTE RECONCILE ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════
# region FUNC_execute_remote_reconcile_entrypoint
execute_remote_reconcile_entrypoint() {
    local node_name="$1"; shift 1
    execute_remote_reconcile "${node_name}" "$@"
}
# endregion FUNC_execute_remote_reconcile_entrypoint
