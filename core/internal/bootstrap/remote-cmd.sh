# shellcheck shell=bash
# GREP_SUMMARY: bootstrap remote-cmd execute-wrapper deliver-vhost-overlays thin-facade remote_executor ssh_cmd_builder python-cli
# STRUCTURE: ▶ ┌source paths.sh┐ → ○ execute_remote_{update,converge,reconcile,check_security,deploy_context} → ⚡ ssh_cmd_builder build + remote_executor execute → ⎋ return $?
# region MODULE_CONTRACT
## @purpose  Тонкий shell-фасад (DevPlan 164 W3.5-1) для remote SSH proxy: remote_cmd строится
##           core/internal/shared/ssh_cmd_builder.py (printf %q, D3), оркестрация (resolve → VPS
##           self-SSH detect → sync-core → ssh exec) — core/internal/bootstrap/remote_executor.py.
## @scope    Sourced by node-update.sh, converge.sh, check-security.sh, deploy-context.sh.
##           Предоставляет execute_remote_* + deliver_vhost_overlays().
## @invariants — printf %q builders — в shared/ssh_cmd_builder.py (D3, логика НЕ изменена, byte-parity);
##              — exit-коды remote_executor.py: 0=success, 1=fatal, 2=local fallback, 124=timeout;
##              — DRY_RUN→--dry-run; --passthrough-args= (форма =, 142 B32: argparse иначе съедает "--force")
## @rationale Strangler-Fig: 672→~60 LOC фасад (DevPlan 101) → W3.5-1: build-логика переехала в
##            shared/ssh_cmd_builder.py. НЕ создан shared/remote_cmd.py: remote_executor.py уже
##            покрывает execute-оркестрацию, а shared/ НЕ может импортировать bootstrap/ (AGENTS.md
##            shared инвариант 5 — слой зависимостей только вниз) — фасад связывает два Python-CLI
##            (build → execute) без слоевой инверсии. Имена/аргументы функций сохранены — вызывающие
##            стороны не меняются.
## ⚠️ TRAP[KEEP] · 173 W3.3 · remote-cmd.sh НЕ схлопывается: execute_remote_* функции vestigial
##   (потребители converge/node-update/check-security/deploy-context → remote_dispatch.py +
##   check_security_cli.py + deploy_context_cli.py); deliver_vhost_overlays → overlay_deliverer.py.
##   Файл — документированный SSH-канал; контракт-тест test_node_update_has_ssh_proxy требует
##   remote_executor/overlay_deliverer ссылки. Rev: при удалении теста — удалить файл.
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — build_*_ssh_cmd (source build-ssh-cmd.sh) → python3 -m shared.ssh_cmd_builder
## @changes 2026-08-16 | DevPlan 173 W3.3 — keep-решение (vestigial execute_remote_* документированы)
# endregion MODULE_CONTRACT
_CMD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${_CMD_DIR}/../../..:${PYTHONPATH:-}"
unset _CMD_DIR
OVERLAY_DELIVERER="python3 -m core.internal.bootstrap.overlay_deliverer"
# region FUNC_execute_remote_update
execute_remote_update() {
    local node_name="$1" detected_age_key="$2"; shift 2; local passthrough_args=("$@")
    local remote_cmd; remote_cmd="$(python3 -m core.internal.shared.ssh_cmd_builder update "${node_name}" "${detected_age_key}" "${passthrough_args[@]}")"
    local dry_flag=(); if ${DRY_RUN:-false}; then dry_flag=(--dry-run); fi
    python3 -m core.internal.bootstrap.remote_executor execute-update \
        --node "${node_name}" --remote-cmd "${remote_cmd}" \
        --passthrough-args="${passthrough_args[*]}" "${dry_flag[@]}"
    return $?
}
# endregion FUNC_execute_remote_update
# region FUNC_execute_remote_converge
execute_remote_converge() {
    local node_name="$1"; shift 1; local passthrough_args=("$@")
    local remote_cmd; remote_cmd="$(python3 -m core.internal.shared.ssh_cmd_builder converge "${node_name}" "${passthrough_args[@]}")"
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
    local remote_cmd; remote_cmd="$(python3 -m core.internal.shared.ssh_cmd_builder converge "${node_name}" "--reconcile" "${passthrough_args[@]}")"
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
    local remote_cmd; remote_cmd="$(python3 -m core.internal.shared.ssh_cmd_builder check-security "${node_name}" "${passthrough_args[@]}")"
    local dry_flag=(); if ${DRY_RUN:-false}; then dry_flag=(--dry-run); fi
    python3 -m core.internal.bootstrap.remote_executor execute-check-security \
        --node "${node_name}" --remote-cmd "${remote_cmd}" \
        --passthrough-args="${passthrough_args[*]}" "${dry_flag[@]}"
    return $?
}
# endregion FUNC_execute_remote_check_security
# region FUNC_execute_remote_deploy_context
## @purpose  Удалённый deploy-context (DevPlan 153 T7, N3): build_deploy_context_ssh_cmd →
##           remote_executor execute-deploy-context. Дефолт RC=2 → локальный fallback.
execute_remote_deploy_context() {
    local node_name="$1"; shift 1; local passthrough_args=("$@")
    local remote_cmd; remote_cmd="$(python3 -m core.internal.shared.ssh_cmd_builder deploy-context "${node_name}" "${passthrough_args[@]}")"
    local dry_flag=(); if ${DRY_RUN:-false}; then dry_flag=(--dry-run); fi
    python3 -m core.internal.bootstrap.remote_executor execute-deploy-context \
        --node "${node_name}" --remote-cmd "${remote_cmd}" \
        --passthrough-args="${passthrough_args[*]}" "${dry_flag[@]}"
    return $?
}
# endregion FUNC_execute_remote_deploy_context
# region FUNC_deliver_vhost_overlays
deliver_vhost_overlays() {
    local node_name="$1"
    ${OVERLAY_DELIVERER} deliver --node "${node_name}" ${DRY_RUN:+--dry-run}
}
# endregion FUNC_deliver_vhost_overlays
