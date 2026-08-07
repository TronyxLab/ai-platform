# shellcheck shell=bash
# GREP_SUMMARY: build-ssh-cmd build_ssh_cmd build_update_ssh_cmd build_converge_ssh_cmd ssh-command quoting printf PLATFORM_ROOT export ci_deploy_key ci_root_key D3 remote-cmd
# STRUCTURE: ▶ ┌node_name+keys┐ → ○ export AGE/PLATFORM_ROOT/CI_DEPLOY_KEY/CI_ROOT_KEY/DOMAIN/CONTEXT (printf %q) → ○ bash node-lifecycle --mode {init,update} | bash converge.sh → ○ passthrough args → ⎋ echo remote cmd
# region MODULE_CONTRACT
## @purpose  printf %q SSH command builders (D3): build_ssh_cmd (init), build_update_ssh_cmd (update),
##           build_converge_ssh_cmd (converge/reconcile). Extracted VERBATIM from remote-cmd.sh:29-132
##           (DevPlan 101 TASK-1) — логика НЕ изменена, только локация.
## @scope    Sourced by remote-cmd.sh (execute wrappers) and bootstrap.sh (init REMOTE_CMD build).
##           Self-contained — не требует lib/ (только env vars: PLATFORM_REMOTE_BASE, PLATFORM_ROOT,
##           PLATFORM_CI_DEPLOY_KEY, PLATFORM_CI_ROOT_KEY, PLATFORM_DOMAIN, CONTEXT, AGE_SECRET_KEY).
## @invariants — D3: printf %q quoting — НЕПРИКОСНОВЕННО (TRAP[DECISION] 2026-07-26: shlex.quote() ≠ printf '%q')
##              — PLATFORM_ROOT export обязателен для remote core-скриптов (TRAP[BUG] P1, 2026-07-31)
##              — ci_deploy_key fallback chain: PLATFORM_CI_DEPLOY_KEY → param (TRAP[BUG] P2, 2026-07-17)
##              — ci_root_key (142 W1): ПУБЛИЧНАЯ часть VPS_SSH_KEY для root authorized_keys —
##                fallback chain PLATFORM_CI_ROOT_KEY → param (тот же паттерн, что ci_deploy_key)
## @rationale Извлечение build-функций (~102 LOC) из remote-cmd.sh позволяет фасаду стать ≤60 LOC
##            (DevPlan 101 D1). Shell-библиотека по образцу lib/ssh.sh, lib/paths.sh.
## @changes 2026-07-31 | DevPlan 101 TASK-1 — extracted from remote-cmd.sh:29-132 verbatim (diff-verified)
# endregion MODULE_CONTRACT

# ══════════════════════════════════════════════════════════════════════
# BUILD SSH CMD — init mode (printf %q)
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_ssh_cmd
build_ssh_cmd() {
    local node_name="$1" owner_key="$2" ci_deploy_key="$3" age_key="$4" ci_root_key="$5"
    shift 5; local passthrough_args=("$@")
    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · PLATFORM_ROOT не экспортировался на remote — фазы искали core по /opt/platform
    # · Symptom: state_machine.py:1333 берёт core_dir из PLATFORM_ROOT (default /opt/platform), но remote
    # ·   команда его не экспортировала → φ1 искал install-docker.sh/firewall.sh в /opt/platform/core (SKIP),
    # ·   φ2 падал: "group 'docker' does not exist" (docker не установлен — install-docker.sh пропущен).
    # · Root: remote_root = PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform (scp-deliver.sh:129),
    # ·   а remote-команда не знала этой базы. Core на VPS лежит по remote_root/core.
    # · Fix: экспортировать PLATFORM_ROOT=${remote_root} в remote-команде — state_machine/фазы резолвят
    # ·   core_dir из него (единый источник: scp-deliver.sh + remote-cmd.sh + overlay_deliverer.sync_core_to_vps).
    # · Prevention: любая remote-команда, выполняющая core-скрипты на VPS, обязана экспортировать
    # ·   PLATFORM_ROOT с той же базой, куда scp/sync доставил core.
    # · Source: обнаружено при верификации DevPlan 095 AC4 (cold-start bootstrap на test-VPS).
    local remote_root="${PLATFORM_REMOTE_BASE:-/opt/platform}"  # RC 121: PLATFORM_ROOT исключён из remote-цепочки
    local remote_orchestrator="${remote_root}/core/internal/bootstrap/node-lifecycle.sh"
    local remote_node_yaml="/opt/node-configs/${node_name}/node.yaml"
    local cmd="set -euo pipefail"

    if [[ -n "${age_key}" ]]; then
        local q; q="$(printf '%q' "${age_key}")"; cmd+=" && export AGE_SECRET_KEY=${q}"
    fi
    local q; q="$(printf '%q' "${remote_root}")"; cmd+=" && export PLATFORM_ROOT=${q}"
    # ⚠️ TRAP[BUG] · 2026-07-17 · P2 · ci_deploy_key from node.yaml not exported
    # · Fix: fallback to ci_deploy_key parameter when env var is unset.
    local effective_ci_key="${PLATFORM_CI_DEPLOY_KEY:-${ci_deploy_key:-}}"
    if [[ -n "${effective_ci_key}" ]]; then
        local q; q="$(printf '%q' "${effective_ci_key}")"; cmd+=" && export PLATFORM_CI_DEPLOY_KEY=${q}"
    fi
    # ⚠️ 142 W1 (A1): CI-root ключ — ПУБЛИЧНАЯ часть VPS_SSH_KEY. Без него core-deploy
    # (root-shell канал CI, core-deploy.yml C-8) падает на свежей ноде: authorized_keys
    # root не содержит ключа раннера → SSH denied. Fallback chain как у ci_deploy_key:
    # env PLATFORM_CI_ROOT_KEY → параметр (node.yaml node.ci_root_key, Q1).
    local effective_ci_root_key="${PLATFORM_CI_ROOT_KEY:-${ci_root_key:-}}"
    if [[ -n "${effective_ci_root_key}" ]]; then
        local q; q="$(printf '%q' "${effective_ci_root_key}")"; cmd+=" && export PLATFORM_CI_ROOT_KEY=${q}"
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
    if [[ -n "${ci_root_key}" ]]; then
        cmd+=" $(printf '%q' '--ci-root-key') $(printf '%q' "${ci_root_key}")"
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
    # PLATFORM_ROOT export — same convention as build_ssh_cmd (remote_root = scp-deliver base)
    local remote_root="${PLATFORM_REMOTE_BASE:-/opt/platform}"  # RC 121: PLATFORM_ROOT исключён из remote-цепочки
    local remote_orchestrator="${remote_root}/core/internal/bootstrap/node-lifecycle.sh"
    local remote_node_yaml="/opt/node-configs/${node_name}/node.yaml"
    local cmd="set -euo pipefail"

    if [[ -n "${age_key}" ]]; then
        local q; q="$(printf '%q' "${age_key}")"; cmd+=" && export AGE_SECRET_KEY=${q}"
    fi
    local q; q="$(printf '%q' "${remote_root}")"; cmd+=" && export PLATFORM_ROOT=${q}"
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
    # PLATFORM_ROOT export — same convention as build_ssh_cmd (remote_root = scp-deliver base)
    local remote_root="${PLATFORM_REMOTE_BASE:-/opt/platform}"  # RC 121: PLATFORM_ROOT исключён из remote-цепочки
    local remote_converge="${remote_root}/core/internal/bootstrap/converge.sh"
    local cmd="set -euo pipefail"
    local q; q="$(printf '%q' "${remote_root}")"; cmd+=" && export PLATFORM_ROOT=${q}"
    cmd+=" && bash $(printf '%q' "${remote_converge}")"
    cmd+=" $(printf '%q' '--node') $(printf '%q' "${node_name}")"
    for arg in "${passthrough_args[@]}"; do cmd+=" $(printf '%q' "${arg}")"; done
    echo "${cmd}"
}
# endregion FUNC_build_converge_ssh_cmd

# ══════════════════════════════════════════════════════════════════════
# BUILD CHECK-SECURITY SSH CMD (printf %q) — DevPlan 134 L2
# ══════════════════════════════════════════════════════════════════════
# region FUNC_build_check_security_ssh_cmd
build_check_security_ssh_cmd() {
    local node_name="$1"; shift 1; local passthrough_args=("$@")
    # PLATFORM_ROOT export — same convention as build_ssh_cmd (remote_root = scp-deliver base)
    local remote_root="${PLATFORM_REMOTE_BASE:-/opt/platform}"  # RC 121: PLATFORM_ROOT исключён из remote-цепочки
    local remote_posture="${remote_root}/core/internal/bootstrap/security_posture.py"
    local cmd="set -euo pipefail"
    local q; q="$(printf '%q' "${remote_root}")"; cmd+=" && export PLATFORM_ROOT=${q}"
    # ⚠️ security_posture.py импортирует core.internal.* (firewall, shared/timeouts) — PYTHONPATH
    # · канон TRAP[BUG] 2026-07-31 (converge.sh:66): shell-фасад/SSH-команда экспортирует PYTHONPATH.
    q="$(printf '%q' "${remote_root}")"; cmd+=" && export PYTHONPATH=${q}"
    cmd+=" && python3 $(printf '%q' "${remote_posture}")"
    cmd+=" $(printf '%q' '--node') $(printf '%q' "${node_name}")"
    for arg in "${passthrough_args[@]}"; do cmd+=" $(printf '%q' "${arg}")"; done
    echo "${cmd}"
}
# endregion FUNC_build_check_security_ssh_cmd
