# shellcheck shell=bash
# GREP_SUMMARY: bootstrap remote-cmd build_ssh_cmd build_update_ssh_cmd ssh-command quoting printf or node-yaml owner-key age-key
# STRUCTURE: ▶ build_ssh_cmd(node, key, age, passthrough...) → ┌printf %q ┐ → ⚡ set -euo → ◇ age_key? → ⚡ export AGE_SECRET_KEY → ⚡ node-lifecycle.sh --mode init + flags → ⚡ --resume → ⚡ passthrough args → ⎋ echo cmd
# region MODULE_CONTRACT
## @purpose  Build the remote node-lifecycle.sh SSH command string with proper shell-safe quoting.
##           Two functions: build_ssh_cmd() for --mode init (bootstrap), build_update_ssh_cmd()
##           for --mode update (node-update).
## @scope    Sourced by core/entrypoints/bootstrap.sh and core/entrypoints/node-update.sh.
##           Provides build_ssh_cmd() and build_update_ssh_cmd().
##           Not intended for direct invocation.
## @invariants
##   - Each argument is independently quoted with printf '%q'
##   - AGE_SECRET_KEY is exported via environment on the remote side (not just CLI arg)
##   - --owner-key value may contain spaces (SSH public key) — handled by %q (build_ssh_cmd only)
##   - AGE_SECRET_KEY passed via env export ONLY (not CLI arg) — prevents ps aux visibility
##   - build_ssh_cmd: always appends --resume for idempotency
##   - build_update_ssh_cmd: intentionally does NOT append --resume (update steps are independent)
##   - Uses --mode init flag for node-lifecycle.sh dispatch (build_ssh_cmd) or --mode update (build_update_ssh_cmd)
## @rationale Extraction from bootstrap.sh to thin-wrapper entrypoint. Layer re-homing T15 (DevPlan 020).
##            build_update_ssh_cmd added per DevPlan 005 D2 — separate from build_ssh_cmd to avoid
##            dragging init-specific args (--owner-key, --resume) into update mode.
##            Uses printf '%q' for shell-safe quoting of each argument, preventing injection via
##            node name or AGE key. The entire command is wrapped in 'set -euo pipefail'
##            for strict error handling.
## @changes 2026-07-17 | T15 — Extracted from bootstrap.sh (pure extraction, identical logic)
##           2026-07-17 | T2  — Added build_update_ssh_cmd() for node-update SSH proxy
# endregion MODULE_CONTRACT

# ── source paths.sh for PLATFORM_ROOT ──────────────────────────────
# Guard: if paths.sh already sourced from entrypoint or scp-deliver.sh, skip
if [[ -z "${PATHS_LIB_DIR:-}" ]]; then
    # shellcheck disable=SC2128  # BASH_SOURCE[0] is correct in source-context
    _CMD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../../lib/paths.sh
    source "${_CMD_DIR}/../../lib/paths.sh"
    unset _CMD_DIR
fi

# ═══════════════════════════════════════════════════════════════════
# BUILD REMOTE SSH COMMAND
# ═══════════════════════════════════════════════════════════════════
# region FUNC_build_ssh_cmd
## @purpose  Build the remote orchestrator command string with proper quoting
## @param $1  Node name
## @param $2  Owner key (SSH public key string)
## @param $3  Detected AGE secret key (may be empty)
## @param @4+  Passthrough args for orchestrator
## @stdout   eval-safe remote command string
## @complexity O(1) — string concatenation with printf %q
## @rationale Uses printf '%q' for shell-safe quoting of each argument.
##           This prevents injection via node name, owner key, or AGE key.
##           The entire command is wrapped in 'set -euo pipefail' for strict error handling.
## @invariants
##   - Each argument is independently quoted with printf '%q'
##   - AGE_SECRET_KEY is exported via environment on the remote side (not just CLI arg)
##   - --owner-key value may contain spaces (SSH public key) — handled by %q
##   - AGE_SECRET_KEY passed via env export ONLY (not CLI arg) — prevents ps aux visibility
##   - node-lifecycle.sh retains --age-secret-key CLI for direct local invocation (backward compat)
##   - Always appends --resume for idempotency
build_ssh_cmd() {
    local node_name="$1"
    local owner_key="$2"
    local age_key="$3"
    shift 3
    local passthrough_args=("$@")

    local remote_orchestrator="${PLATFORM_ROOT:-/opt/platform}/core/internal/bootstrap/node-lifecycle.sh"
    local remote_node_yaml="/opt/node-configs/${node_name}/node.yaml"

    # Build command: set -euo pipefail (strict) + optional AGE export + orchestrator with flags
    local cmd="set -euo pipefail"

    # Export AGE_SECRET_KEY on remote before running orchestrator
    if [[ -n "${age_key}" ]]; then
        local quoted_age_key
        quoted_age_key="$(printf '%q' "${age_key}")"
        cmd+=" && export AGE_SECRET_KEY=${quoted_age_key}"
    fi

    cmd+=" && bash $(printf '%q' "${remote_orchestrator}")"
    cmd+=" $(printf '%q' '--mode') $(printf '%q' 'init')"
    cmd+=" $(printf '%q' '--node-name') $(printf '%q' "${node_name}")"
    cmd+=" $(printf '%q' '--node-yaml') $(printf '%q' "${remote_node_yaml}")"
    cmd+=" $(printf '%q' '--owner-key') $(printf '%q' "${owner_key}")"

    # NOTE: --age-secret-key CLI arg intentionally NOT included in remote SSH command.
    # AGE_SECRET_KEY is passed ONLY via env export (line above) — this prevents key
    # visibility in `ps aux` on the remote server. See DevPlan D-1.
    # node-lifecycle.sh retains --age-secret-key CLI support for direct local invocation (backward compat).

    # Always pass --resume for idempotency
    cmd+=" $(printf '%q' '--resume')"

    # Append any passthrough args
    local arg
    for arg in "${passthrough_args[@]}"; do
        cmd+=" $(printf '%q' "${arg}")"
    done

    echo "${cmd}"
}
# endregion FUNC_build_ssh_cmd

# ═══════════════════════════════════════════════════════════════════
# BUILD REMOTE SSH COMMAND — UPDATE MODE
# ═══════════════════════════════════════════════════════════════════
# region FUNC_build_update_ssh_cmd
## @purpose  Build the remote node-lifecycle.sh SSH command for --mode update.
##           Separate from build_ssh_cmd() per DevPlan D2 — no --owner-key, no --resume.
## @param $1  Node name
## @param $2  Detected AGE secret key (may be empty)
## @param @3+  Passthrough args for node-lifecycle.sh
## @stdout   eval-safe remote command string
## @complexity O(1) — string concatenation with printf %q
## @rationale Per DevPlan 005 D2: update-режим не требует --resume (checkpoint'ы update-шагов
##            независимы) и --owner-key (используется только для создания пользователя в init).
##            Новая функция чище, чем параметризация build_ssh_cmd() с 4 условными блоками.
## @invariants
##   - Each argument is independently quoted with printf '%q'
##   - AGE_SECRET_KEY is exported via environment on the remote side (not just CLI arg)
##   - --node-yaml путь = /opt/node-configs/<node>/node.yaml (VPS-путь)
##   - --mode update фиксирован (не параметризуется)
##   - --resume НЕ добавляется (update шаги независимы, D2)
##   - --owner-key НЕ передаётся (D2)
build_update_ssh_cmd() {
    local node_name="$1"
    local age_key="$2"
    shift 2
    local passthrough_args=("$@")

    local remote_orchestrator="${PLATFORM_ROOT:-/opt/platform}/core/internal/bootstrap/node-lifecycle.sh"
    local remote_node_yaml="/opt/node-configs/${node_name}/node.yaml"

    # Build command: set -euo pipefail (strict) + optional AGE export + orchestrator with flags
    local cmd="set -euo pipefail"

    # Export AGE_SECRET_KEY on remote before running orchestrator
    if [[ -n "${age_key}" ]]; then
        local quoted_age_key
        quoted_age_key="$(printf '%q' "${age_key}")"
        cmd+=" && export AGE_SECRET_KEY=${quoted_age_key}"
    fi

    cmd+=" && bash $(printf '%q' "${remote_orchestrator}")"
    cmd+=" $(printf '%q' '--mode') $(printf '%q' 'update')"
    cmd+=" $(printf '%q' '--node-name') $(printf '%q' "${node_name}")"
    cmd+=" $(printf '%q' '--node-yaml') $(printf '%q' "${remote_node_yaml}")"

    # NOTE: --age-secret-key CLI arg intentionally NOT included in remote SSH command.
    # AGE_SECRET_KEY is passed ONLY via env export (line above) — prevents key
    # visibility in ps aux on the remote server. Same pattern as build_ssh_cmd().

    # Append any passthrough args (e.g. --dry-run)
    local arg
    for arg in "${passthrough_args[@]}"; do
        cmd+=" $(printf '%q' "${arg}")"
    done

    echo "${cmd}"
}
# endregion FUNC_build_update_ssh_cmd
