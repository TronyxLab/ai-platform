# shellcheck shell=bash
# GREP_SUMMARY: bootstrap remote-cmd execute-wrapper deliver-vhost-overlays thin-facade remote_executor python-cli
# STRUCTURE: ▶ ┌source paths+ssh+build-ssh-cmd┐ → ○ execute_remote_{update,converge,reconcile} → ⚡ python3 -m remote_executor → ⎋ return $?
# region MODULE_CONTRACT
## @purpose  Thin shell facade for remote SSH proxy operations: build_*_ssh_cmd via build-ssh-cmd.sh (printf %q, D3), orchestration → remote_executor.py
## @scope    Sourced by node-update.sh, converge.sh. Provides execute_remote_* + deliver_vhost_overlays(). build-ssh-cmd.sh sourced separately by bootstrap.sh.
## @invariants — printf %q builders live in build-ssh-cmd.sh (D3, logic untouched); exit: 2=local fallback, 1=fatal, 124=timeout; DRY_RUN→--dry-run
## @rationale Strangler-Fig: 672→~60 LOC facade + build-ssh-cmd.sh (~100 LOC) + remote_executor.py (~200 LOC).
## @changes 2026-07-31 | DevPlan 101 — execute_* orchestration (resolve/VPS-detect/sync-core/ssh_exec) → Python
# endregion MODULE_CONTRACT
if [[ -z "${PATHS_LIB_DIR:-}" ]]; then
    _CMD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../../lib/paths.sh
    source "${_CMD_DIR}/../../lib/paths.sh"
    unset _CMD_DIR
fi
# shellcheck source=../../lib/ssh.sh
source "${PATHS_LIB_DIR}/ssh.sh"
# shellcheck source=./build-ssh-cmd.sh
source "$(dirname "${BASH_SOURCE[0]}")/build-ssh-cmd.sh"
OVERLAY_DELIVERER="python3 -m core.internal.bootstrap.overlay_deliverer"
# region FUNC_execute_remote_update
execute_remote_update() {
    local node_name="$1" detected_age_key="$2"; shift 2; local passthrough_args=("$@")
    local remote_cmd; remote_cmd="$(build_update_ssh_cmd "${node_name}" "${detected_age_key}" "${passthrough_args[@]}")"
    local dry_flag=(); if ${DRY_RUN:-false}; then dry_flag=(--dry-run); fi
    # 142 B32: --passthrough-args=… (форма =): argparse иначе съедает значение "--force"
    # как опцию → «expected one argument» → remote-execute не выполнялся.
    python3 -m core.internal.bootstrap.remote_executor execute-update \
        --node "${node_name}" --remote-cmd "${remote_cmd}" \
        --passthrough-args="${passthrough_args[*]}" "${dry_flag[@]}"
    return $?
}
# endregion FUNC_execute_remote_update
# region FUNC_execute_remote_converge
execute_remote_converge() {
    local node_name="$1"; shift 1; local passthrough_args=("$@")
    local remote_cmd; remote_cmd="$(build_converge_ssh_cmd "${node_name}" "${passthrough_args[@]}")"
    local dry_flag=(); if ${DRY_RUN:-false}; then dry_flag=(--dry-run); fi
    python3 -m core.internal.bootstrap.remote_executor execute-converge \
        --node "${node_name}" --remote-cmd "${remote_cmd}" \
        --passthrough-args="${passthrough_args[*]}" "${dry_flag[@]}"
    return $?
}
# endregion FUNC_execute_remote_converge
# region FUNC_execute_remote_reconcile
execute_remote_reconcile() {
    local node_name="$1"; shift 1; local passthrough_args=("$@")
    local remote_cmd; remote_cmd="$(build_converge_ssh_cmd "${node_name}" "--reconcile" "${passthrough_args[@]}")"
    local dry_flag=(); if ${DRY_RUN:-false}; then dry_flag=(--dry-run); fi
    python3 -m core.internal.bootstrap.remote_executor execute-reconcile \
        --node "${node_name}" --remote-cmd "${remote_cmd}" \
        --passthrough-args="${passthrough_args[*]}" "${dry_flag[@]}"
    return $?
}
# endregion FUNC_execute_remote_reconcile
# region FUNC_execute_remote_check_security
execute_remote_check_security() {
    local node_name="$1"; shift 1; local passthrough_args=("$@")
    local remote_cmd; remote_cmd="$(build_check_security_ssh_cmd "${node_name}" "${passthrough_args[@]}")"
    local dry_flag=(); if ${DRY_RUN:-false}; then dry_flag=(--dry-run); fi
    python3 -m core.internal.bootstrap.remote_executor execute-check-security \
        --node "${node_name}" --remote-cmd "${remote_cmd}" \
        --passthrough-args="${passthrough_args[*]}" "${dry_flag[@]}"
    return $?
}
# endregion FUNC_execute_remote_check_security
# region FUNC_deliver_vhost_overlays
deliver_vhost_overlays() {
    local node_name="$1"
    ${OVERLAY_DELIVERER} deliver --node "${node_name}" ${DRY_RUN:+--dry-run}
}
# endregion FUNC_deliver_vhost_overlays
