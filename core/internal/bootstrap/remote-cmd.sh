# shellcheck shell=bash
# GREP_SUMMARY: bootstrap remote-cmd build_ssh_cmd build_update_ssh_cmd ssh-command quoting printf or node-yaml owner-key age-key
# STRUCTURE: ▶ build_ssh_cmd(node, owner_key, ci_deploy_key, age_key, passthrough...) → ┌printf %q ┐ → ⚡ set -euo → ◇ age_key? → ⚡ export AGE_SECRET_KEY → ◇ ci_deploy_key? → ⚡ --ci-deploy-key flag → ⚡ node-lifecycle.sh --mode init + flags → ⚡ --resume → ⚡ passthrough args → ⎋ echo cmd
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
##           2026-07-21 | W4 — Added execute_remote_reconcile() + execute_remote_reconcile_entrypoint()
##           2026-07-21 | W2-E1 — Migrated to lib/ssh.sh: source ssh.sh, 4 inline ssh/exec → ssh_exec,
##                       dry-run blocks preserved for AGE-key masking
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

# ── Source lib/ssh.sh for SSH_OPTS_COMMON, ssh_exec, ssh_read ─────
# shellcheck source=../../lib/ssh.sh
source "${PATHS_LIB_DIR}/ssh.sh"

# ═══════════════════════════════════════════════════════════════════
# BUILD REMOTE SSH COMMAND
# ═══════════════════════════════════════════════════════════════════
# region FUNC_build_ssh_cmd
## @purpose  Build the remote orchestrator command string with proper quoting
## @param $1  Node name
## @param $2  Owner key (SSH public key string)
## @param $3  CI deploy key (SSH public key string for ci-deploy user, may be empty)
## @param $4  Detected AGE secret key (may be empty)
## @param @5+  Passthrough args for orchestrator
## @stdout   eval-safe remote command string
## @complexity O(1) — string concatenation with printf %q
## @rationale Uses printf '%q' for shell-safe quoting of each argument.
##           This prevents injection via node name, owner key, or AGE key.
##           The entire command is wrapped in 'set -euo pipefail' for strict error handling.
##           ci_deploy_key added as explicit 3rd parameter per DevPlan 008 Contract 1 (D1).
##           --ci-deploy-key flag added to SSH command only when key is non-empty.
## @invariants
##   - Each argument is independently quoted with printf '%q'
##   - AGE_SECRET_KEY is exported via environment on the remote side (not just CLI arg)
##   - --owner-key value may contain spaces (SSH public key) — handled by %q
##   - AGE_SECRET_KEY passed via env export ONLY (not CLI arg) — prevents ps aux visibility
##   - node-lifecycle.sh retains --age-secret-key CLI for direct local invocation (backward compat)
##   - --ci-deploy-key is added via CLI flag (not env-only) — follows same pattern as --owner-key
##   - PLATFORM_CI_DEPLOY_KEY env export retained for backward compatibility
##   - Always appends --resume for idempotency
build_ssh_cmd() {
    local node_name="$1"
    local owner_key="$2"
    local ci_deploy_key="$3"
    local age_key="$4"
    shift 4
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

    # Export PLATFORM_CI_DEPLOY_KEY on remote before running orchestrator (used by step_6_create_ci_deploy_user)
    # Falls back to ci_deploy_key parameter when key is from node.yaml (not env).
    # ⚠️ TRAP[BUG] · 2026-07-17 · P2 · ci_deploy_key from node.yaml not exported
    # · Symptom: when CI_DEPLOY_KEY extracted from node.yaml (not env), the remote
    #   PLATFORM_CI_DEPLOY_KEY env was empty even though --ci-deploy-key CLI flag was set.
    # · Root: line only checked ${PLATFORM_CI_DEPLOY_KEY:-} (local env), not the parameter.
    # · Fix: fallback to ci_deploy_key parameter when env var is unset.
    # · Prevention: always use effective_ci_key combining env + parameter fallback.
    local effective_ci_key="${PLATFORM_CI_DEPLOY_KEY:-${ci_deploy_key:-}}"
    if [[ -n "${effective_ci_key}" ]]; then
        local quoted_ci_key
        quoted_ci_key="$(printf '%q' "${effective_ci_key}")"
        cmd+=" && export PLATFORM_CI_DEPLOY_KEY=${quoted_ci_key}"
    fi

    cmd+=" && bash $(printf '%q' "${remote_orchestrator}")"
    cmd+=" $(printf '%q' '--mode') $(printf '%q' 'init')"
    cmd+=" $(printf '%q' '--node-name') $(printf '%q' "${node_name}")"
    cmd+=" $(printf '%q' '--node-yaml') $(printf '%q' "${remote_node_yaml}")"
    cmd+=" $(printf '%q' '--owner-key') $(printf '%q' "${owner_key}")"

    # Add --ci-deploy-key flag if key is non-empty (follows same pattern as --owner-key)
    if [[ -n "${ci_deploy_key}" ]]; then
        cmd+=" $(printf '%q' '--ci-deploy-key') $(printf '%q' "${ci_deploy_key}")"
    fi

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

# ═══════════════════════════════════════════════════════════════════
# EXECUTE REMOTE UPDATE VIA SSH
# ═══════════════════════════════════════════════════════════════════
# region FUNC_execute_remote_update
## @purpose  Resolve node.yaml → detect SSH host → prepare SSH opts → build remote cmd → exec ssh
##           on the remote VPS. Encapsulates the entire SSH proxy flow. Returns 2 if no SSH host
##           (caller handles local exec), otherwise exec ssh or exit 0 (DRY_RUN).
## @param $1  Node name
## @param $2  Detected AGE secret key (may be empty)
## @param @3+  Passthrough args for node-lifecycle.sh
## @exit 0   — DRY_RUN complete (printed command, script exits)
## @exit 1   — Fatal error (node.yaml not resolvable)
## @return 2 — No SSH host; caller should execute locally
## @complexity O(N) — node resolution + SSH exec
## @rationale Extraction from node-update.sh to restore thin-wrapper contract.
##            Encapsulates: resolve_node_yaml → extract_node_host → prepare_ssh_opts →
##            build_update_ssh_cmd → exec ssh. All SSH proxy logic in one place.
## @invariants
##   - Sources node-resolver.sh and scp-deliver.sh (function definition libs, idempotent)
##   - Returns 2 for local fallback (no SSH host); always returns 1 on fatal error
##   - On DRY_RUN: exit 0 (prints SSH command, stops script)
##   - On SSH exec: replaces process (never returns on success)
##   - DRY_RUN global variable controls dry-run mode (set by entrypoint)
execute_remote_update() {
    local node_name="$1"
    local detected_age_key="$2"
    shift 2
    local passthrough_args=("$@")

    # ── Source dependencies (function-only libs, idempotent) ────────
    local _eru_dir
    _eru_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../../lib/node-resolver.sh
    source "${_eru_dir}/../../lib/node-resolver.sh"
    # shellcheck source=./scp-deliver.sh
    source "${_eru_dir}/scp-deliver.sh"

    # ── Resolve node.yaml ──────────────────────────────────────────
    local node_yaml
    node_yaml="$(resolve_node_yaml "${node_name}" "${PLATFORM_ROOT}" "${HOME}/projects")" || {
        echo "[IMP:10][node-update][remote] FATAL: Cannot resolve node.yaml for node=${node_name}" >&2
        return 1
    }
    echo "[IMP:9][node-update][remote] Resolved node.yaml: ${node_yaml}" >&2

    # ── Extract SSH host ───────────────────────────────────────────
    local ssh_host
    ssh_host="$(extract_node_host "${node_yaml}")" || {
        echo "[IMP:8][node-update][remote] WARN: No SSH host in node.yaml — local mode" >&2
        ssh_host=""
    }

    if [[ -z "${ssh_host}" ]]; then
        echo "[IMP:9][node-update][remote] No SSH host — returning for local fallback" >&2
        return 2
    fi

    # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · VPS self-SSH loop: node-update SSH-proxy tries to SSH to itself
    # · Symptom: CI SSHes to VPS → make node-update → node-update.sh finds host in node.yaml →
    #   execute_remote_update → tries SSH from VPS to itself → fails (no SSH key on VPS for self)
    # · Fix: detect local execution — if /opt/platform/ exists, we're on the VPS, skip SSH proxy
    # · Detection: /opt/platform/core/internal/bootstrap/node-lifecycle.sh exists → local exec
    if [[ -f "/opt/platform/core/internal/bootstrap/node-lifecycle.sh" ]]; then
        echo "[IMP:9][node-update][remote] Local VPS detected (/opt/platform/ exists) — skipping SSH proxy, local exec" >&2
        return 2
    fi

    echo "[IMP:9][node-update][remote] SSH host: ${ssh_host} — REMOTE update via SSH" >&2

    # ── Prepare SSH opts (mode=update — preserve known_hosts, honest TOFU) ──
    prepare_ssh_opts "${ssh_host}" "update"

    # ⚠️ TRAP[BUG] · 2026-07-24 · P0 · node-update не доставлял core/ на VPS
    # · Symptom: stale state_machine.py/steps.py/converge.sh на VPS → баги из локальных
    #   исправлений не доезжают до продакшена. Bootstrap доставляет core/ через scp_to_server,
    #   но node-update — нет. Результат: node-update исполняет старый код.
    # · Fix: rsync core/ + node.yaml перед remote exec (только код, без secrets/Makefile).
    # ── Compute paths BEFORE dry-run check (both branches need them) ──
    local core_src="${CORE_DIR:-$(cd "${_eru_dir}/../.." && pwd)}"
    local node_configs_dir
    node_configs_dir="$(dirname "$(dirname "${node_yaml}")")"

    # ── Rsync core/ to VPS (incremental delivery, not full scp_to_server) ──
    if ${DRY_RUN:-false}; then
        echo "[IMP:8][node-update][dry-run] DRY-RUN: rsync ${core_src}/ → root@${ssh_host}:/opt/platform/core/" >&2
        echo "[IMP:8][node-update][dry-run] DRY-RUN: rsync ${node_yaml} → root@${ssh_host}:/opt/node-configs/${node_name}/node.yaml" >&2
    else
        echo "[IMP:9][node-update][remote] Rsyncing core/ → ${ssh_host}:/opt/platform/core/" >&2
        # shellcheck disable=SC2086  # SSH_OPTS_COMMON intentionally word-split for rsync -e
        if ! rsync -avz --delete \
            -e "ssh ${SSH_OPTS_COMMON[*]}" \
            --exclude=.git \
            --exclude=__pycache__ \
            --exclude=.pytest_cache \
            --exclude='default-user.xml' \
            --exclude='.env' \
            "${core_src}/" \
            "root@${ssh_host}:/opt/platform/core/"; then
            echo "[IMP:10][node-update][remote] FATAL: rsync core/ failed for ${ssh_host}" >&2
            return 1
        fi
        echo "[IMP:9][node-update][remote] core/ rsync complete" >&2

        # Rsync node.yaml for freshness
        if [[ -f "${node_yaml}" ]]; then
            echo "[IMP:9][node-update][remote] Rsyncing node.yaml → ${ssh_host}:/opt/node-configs/${node_name}/" >&2
            # shellcheck disable=SC2086  # SSH_OPTS_COMMON intentionally word-split for rsync -e
            if ! rsync -avz -e "ssh ${SSH_OPTS_COMMON[*]}" \
                "${node_yaml}" \
                "root@${ssh_host}:/opt/node-configs/${node_name}/node.yaml"; then
                echo "[IMP:10][node-update][remote] FATAL: rsync node.yaml failed for ${ssh_host}" >&2
                return 1
            fi
            echo "[IMP:9][node-update][remote] node.yaml rsync complete" >&2
        fi
    fi

    # ── Build remote command ───────────────────────────────────────
    local remote_cmd
    remote_cmd="$(build_update_ssh_cmd "${node_name}" "${detected_age_key}" "${passthrough_args[@]}")"

    # ── Mask AGE key in dry-run output (security) ──────────────────
    local masked_cmd="${remote_cmd}"
    if [[ -n "${detected_age_key}" ]]; then
        local m
        m="$(echo "${detected_age_key}" | cut -c1-8)"
        masked_cmd="${remote_cmd//${detected_age_key}/<AGE_KEY:${m}...>}"
    fi

    # ── DRY_RUN mode ───────────────────────────────────────────────
    if ${DRY_RUN:-false}; then
        echo "[IMP:8][node-update][dry-run] DRY-RUN: ssh ${SSH_OPTS[*]} root@${ssh_host} ${masked_cmd}" >&2
        echo "[IMP:9][node-update][dry-run] DRY-RUN complete" >&2
        exit 0
    fi

    # ── Execute SSH via ssh_exec (W2-E1: lib/ssh.sh facade) ────────
    echo "[IMP:9][node-update][remote] Executing node-lifecycle.sh --mode update on root@${ssh_host}" >&2
    # ⚠️ TRAP[BUG] · 2026-07-24 · P4 · DevPlan 065: bare ssh_exec may silently fail under set -e
    # · Symptom: under set -e, ssh_exec non-zero exit causes immediate function exit without error logging
    # · Fix: added || { local rc=$?; log_imp 1 ...; return $rc; } for explicit error logging
    ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy" || {
        local rc=$?
        log_imp 1 "execute_remote_update" "SSH exec failed on ${ssh_host} — exit=${rc}"
        return "${rc}"
    }
}
# endregion FUNC_execute_remote_update

# ═══════════════════════════════════════════════════════════════════
# BUILD REMOTE SSH COMMAND — CONVERGE MODE
# ═══════════════════════════════════════════════════════════════════
# region FUNC_build_converge_ssh_cmd
## @purpose  Build the remote converge.sh SSH command with proper shell-safe quoting.
##           Calls converge.sh directly (not through node-lifecycle.sh dispatch).
## @param $1  Node name
## @param @2+  Passthrough args (--dry-run, --report-only, etc.)
## @stdout   eval-safe remote command string
## @complexity O(1) — string concatenation with printf %q
## @rationale Converge mode is a standalone command — no mode dispatch needed.
##            No AGE key handling (converge R-units don't decrypt secrets).
##            Follows same printf %q quoting pattern as build_ssh_cmd / build_update_ssh_cmd.
## @invariants
##   - Each argument is independently quoted with printf '%q'
##   - Remote converge.sh path = /opt/platform/core/internal/bootstrap/converge.sh
##   - No --mode flag (converge.sh uses its own --node / --dry-run / --report-only args)
##   - No --resume (converge R-units are independent, idempotent by design)
##   - No AGE_SECRET_KEY export (converge does not need secrets)
build_converge_ssh_cmd() {
    local node_name="$1"
    shift 1
    local passthrough_args=("$@")

    local remote_converge="${PLATFORM_ROOT:-/opt/platform}/core/internal/bootstrap/converge.sh"

    local cmd="set -euo pipefail"
    cmd+=" && bash $(printf '%q' "${remote_converge}")"
    cmd+=" $(printf '%q' '--node') $(printf '%q' "${node_name}")"

    local arg
    for arg in "${passthrough_args[@]}"; do
        cmd+=" $(printf '%q' "${arg}")"
    done

    echo "${cmd}"
}
# endregion FUNC_build_converge_ssh_cmd

# ═══════════════════════════════════════════════════════════════════
# EXECUTE REMOTE CONVERGE VIA SSH
# ═══════════════════════════════════════════════════════════════════
# region FUNC_execute_remote_converge
## @purpose  Resolve node.yaml → detect SSH host → prepare SSH opts → build remote cmd → exec ssh
##           on the remote VPS. Returns 2 if no SSH host (caller handles local exec),
##           otherwise exec ssh or exit 0 (DRY_RUN).
## @param $1  Node name
## @param @2+  Passthrough args for converge.sh
## @exit 0   — DRY_RUN complete (printed command, script exits)
## @exit 1   — Fatal error (node.yaml not resolvable)
## @return 2 — No SSH host; caller should execute locally
## @complexity O(N) — node resolution + SSH exec
## @rationale Mirrors execute_remote_update() pattern for converge operations.
##            Encapsulates: resolve_node_yaml → extract_node_host → prepare_ssh_opts →
##            build_converge_ssh_cmd → exec ssh. All SSH proxy logic in one place.
## @invariants
##   - Sources node-resolver.sh and scp-deliver.sh (function definition libs, idempotent)
##   - Returns 2 for local fallback (no SSH host); always returns 1 on fatal error
##   - On DRY_RUN: exit 0 (prints SSH command, stops script)
##   - On SSH exec: replaces process (never returns on success)
##   - DRY_RUN global variable controls dry-run mode (set by entrypoint)
execute_remote_converge() {
    local node_name="$1"
    shift 1
    local passthrough_args=("$@")

    # ── Source dependencies (function-only libs, idempotent) ────────
    local _erc_dir
    _erc_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../../lib/node-resolver.sh
    source "${_erc_dir}/../../lib/node-resolver.sh"
    # shellcheck source=./scp-deliver.sh
    source "${_erc_dir}/scp-deliver.sh"

    # ── Resolve node.yaml ──────────────────────────────────────────
    local node_yaml
    node_yaml="$(resolve_node_yaml "${node_name}" "${PLATFORM_ROOT}" "${HOME}/projects")" || {
        echo "[IMP:10][converge][remote] FATAL: Cannot resolve node.yaml for node=${node_name}" >&2
        return 1
    }
    echo "[IMP:9][converge][remote] Resolved node.yaml: ${node_yaml}" >&2

    # ── Extract SSH host ───────────────────────────────────────────
    local ssh_host
    ssh_host="$(extract_node_host "${node_yaml}")" || {
        echo "[IMP:8][converge][remote] WARN: No SSH host in node.yaml — local mode" >&2
        ssh_host=""
    }

    if [[ -z "${ssh_host}" ]]; then
        echo "[IMP:9][converge][remote] No SSH host — returning for local fallback" >&2
        return 2
    fi

    echo "[IMP:9][converge][remote] SSH host: ${ssh_host} — REMOTE converge via SSH" >&2

    # ── Prepare SSH opts (mode=update — same host-key pattern) ──
    prepare_ssh_opts "${ssh_host}" "update"

    # ── Build remote command ───────────────────────────────────────
    local remote_cmd
    remote_cmd="$(build_converge_ssh_cmd "${node_name}" "${passthrough_args[@]}")"

    # ── DRY_RUN mode ───────────────────────────────────────────────
    if ${DRY_RUN:-false}; then
        echo "[IMP:8][converge][dry-run] DRY-RUN: ssh ${SSH_OPTS[*]} root@${ssh_host} ${remote_cmd}" >&2
        echo "[IMP:9][converge][dry-run] DRY-RUN complete" >&2
        exit 0
    fi

    # ── Execute SSH via ssh_exec (W2-E1: lib/ssh.sh facade) ────────
    echo "[IMP:9][converge][remote] Executing converge.sh on root@${ssh_host}" >&2
    # ⚠️ TRAP[BUG] · 2026-07-24 · P4 · DevPlan 065: bare ssh_exec may silently fail under set -e
    ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy" || {
        local rc=$?
        log_imp 1 "execute_remote_converge" "SSH exec failed on ${ssh_host} — exit=${rc}"
        return "${rc}"
    }
}
# endregion FUNC_execute_remote_converge

# ═══════════════════════════════════════════════════════════════════
# DELIVER VHOST OVERLAYS (S2 DevPlan 019)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_deliver_vhost_overlays
## @purpose  S2 (DevPlan 019): rsync generated vhost overlays from local
##           node-configs/<node>/overlays/nginx/*.conf to server before remote update.
##           Called from entrypoints/node-update.sh (thin wrapper — just the call).
## @param $1  Node name
## @sideeffect rsync to remote server; no-op if no .conf files or no SSH host
## @exit 0    Success or graceful skip (no overlays, no host)
## @exit 1    Fatal error (rsync failed)
## @invariants
##   - Sources node-resolver.sh and scp-deliver.sh (idempotent, function-libs)
##   - Uses prepare_ssh_opts "update" mode — preserves host keys (honest TOFU)
##   - --delete on rsync: removes stale vhosts from server
##   - Graceful skip if no overlays, no host, or dry-run mode
deliver_vhost_overlays() {
    local node_name="$1"

    # ── Source dependencies (function-only libs, idempotent) ────────
    local _dvo_dir
    _dvo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../../lib/node-resolver.sh
    source "${_dvo_dir}/../../lib/node-resolver.sh"
    # shellcheck source=./scp-deliver.sh
    source "${_dvo_dir}/scp-deliver.sh"

    # ── Resolve node.yaml ──────────────────────────────────────────
    local node_yaml
    node_yaml="$(resolve_node_yaml "${node_name}" "${PLATFORM_ROOT}" "${HOME}/projects")" || {
        echo "[IMP:8][node-update][overlays] WARN: Cannot resolve node.yaml for ${node_name} — skipping overlay delivery" >&2
        return 0
    }
    echo "[IMP:8][node-update][overlays] Resolved node.yaml: ${node_yaml}" >&2

    # ── Extract SSH host ───────────────────────────────────────────
    local ssh_host
    ssh_host="$(extract_node_host "${node_yaml}")" || ssh_host=""
    if [[ -z "${ssh_host}" ]]; then
        echo "[IMP:8][node-update][overlays] No SSH host — skipping overlay delivery (local mode)" >&2
        return 0
    fi

    # ── Check local overlay dir ────────────────────────────────────
    local local_overlay="${PLATFORM_ROOT}/node-configs/${node_name}/overlays/nginx"
    if [[ ! -d "${local_overlay}" ]]; then
        echo "[IMP:8][node-update][overlays] No local overlay dir: ${local_overlay} — skipping" >&2
        return 0
    fi
    local conf_count
    conf_count=$(find "${local_overlay}" -maxdepth 1 -name '*.conf' -type f 2>/dev/null | wc -l | tr -d ' ')
    if [[ "${conf_count}" -eq 0 ]]; then
        echo "[IMP:8][node-update][overlays] No .conf files in ${local_overlay} — skipping" >&2
        return 0
    fi

    echo "[IMP:9][node-update][overlays] Delivering ${conf_count} vhost overlay(s) to ${ssh_host}:/opt/node-configs/${node_name}/overlays/nginx/" >&2

    # ── DRY_RUN mode ───────────────────────────────────────────────
    if ${DRY_RUN:-false}; then
        echo "[IMP:8][node-update][dry-run] DRY-RUN: rsync ${local_overlay}/ → root@${ssh_host}:/opt/node-configs/${node_name}/overlays/nginx/" >&2
        return 0
    fi

    # ── Prepare SSH opts ───────────────────────────────────────────
    prepare_ssh_opts "${ssh_host}" "update"

    # ── Create remote dir via ssh_exec (W2-E1: lib/ssh.sh facade) ──
    ssh_exec "${ssh_host}" "root" \
        "mkdir -p /opt/node-configs/${node_name}/overlays/nginx" "" "deploy" || {
        echo "[IMP:10][node-update][overlays] FATAL: Cannot create remote overlay dir on ${ssh_host}" >&2
        return 1
    }

    # ── rsync overlays ─────────────────────────────────────────────
    # shellcheck disable=SC2086  # SSH_OPTS intentionally word-split
    rsync -avz --delete \
        -e "ssh ${SSH_OPTS[*]:--o StrictHostKeyChecking=accept-new -o ConnectTimeout=30}" \
        "${local_overlay}/" \
        "root@${ssh_host}:/opt/node-configs/${node_name}/overlays/nginx/" || {
        echo "[IMP:10][node-update][overlays] FATAL: rsync overlay delivery failed" >&2
        return 1
    }

    echo "[IMP:9][node-update][overlays] Overlay delivery complete" >&2
}
# endregion FUNC_deliver_vhost_overlays

# ═══════════════════════════════════════════════════════════════════
# EXECUTE REMOTE RECONCILE VIA SSH
# ═══════════════════════════════════════════════════════════════════
# region FUNC_execute_remote_reconcile
## @purpose  Resolve node.yaml → detect SSH host → prepare SSH opts → build remote
##           converge --reconcile command → exec ssh on the remote VPS.
##           Returns 2 if no SSH host (caller handles local exec).
## @param $1  Node name
## @param @2+  Passthrough args for converge.sh --reconcile
## @exit 0   — DRY_RUN complete (printed command, script exits)
## @exit 1   — Fatal error (node.yaml not resolvable)
## @return 2 — No SSH host; caller should execute locally
## @invariants
##   - Sources node-resolver.sh and scp-deliver.sh (function definition libs, idempotent)
##   - build_converge_ssh_cmd is reused for the remote command
##   - Adds --reconcile flag to the converge command
##   - Returns 2 for local fallback (no SSH host); always returns 1 on fatal error
##   - On DRY_RUN: exit 0 (prints SSH command, stops script)
##   - DRY_RUN global variable controls dry-run mode (set by entrypoint)
execute_remote_reconcile() {
    local node_name="$1"
    shift 1
    local passthrough_args=("$@")

    # ── Source dependencies (function-only libs, idempotent) ────────
    local _err_dir
    _err_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../../lib/node-resolver.sh
    source "${_err_dir}/../../lib/node-resolver.sh"
    # shellcheck source=./scp-deliver.sh
    source "${_err_dir}/scp-deliver.sh"

    # ── Resolve node.yaml ──────────────────────────────────────────
    local node_yaml
    node_yaml="$(resolve_node_yaml "${node_name}" "${PLATFORM_ROOT}" "${HOME}/projects")" || {
        echo "[IMP:10][reconcile][remote] FATAL: Cannot resolve node.yaml for node=${node_name}" >&2
        return 1
    }
    echo "[IMP:9][reconcile][remote] Resolved node.yaml: ${node_yaml}" >&2

    # ── Extract SSH host ───────────────────────────────────────────
    local ssh_host
    ssh_host="$(extract_node_host "${node_yaml}")" || {
        echo "[IMP:8][reconcile][remote] WARN: No SSH host in node.yaml — local mode" >&2
        ssh_host=""
    }

    if [[ -z "${ssh_host}" ]]; then
        echo "[IMP:9][reconcile][remote] No SSH host — returning for local fallback" >&2
        return 2
    fi

    echo "[IMP:9][reconcile][remote] SSH host: ${ssh_host} — REMOTE reconcile via SSH" >&2

    # ── Prepare SSH opts (mode=update — same host-key pattern) ──
    prepare_ssh_opts "${ssh_host}" "update"

    # ── Build remote command: converge --reconcile ─────────────────
    local remote_cmd
    remote_cmd="$(build_converge_ssh_cmd "${node_name}" "--reconcile" "${passthrough_args[@]}")"

    # ── DRY_RUN mode ───────────────────────────────────────────────
    if ${DRY_RUN:-false}; then
        echo "[IMP:8][reconcile][dry-run] DRY-RUN: ssh ${SSH_OPTS[*]} root@${ssh_host} ${remote_cmd}" >&2
        echo "[IMP:9][reconcile][dry-run] DRY-RUN complete" >&2
        exit 0
    fi

    # ── Execute SSH via ssh_exec (W2-E1: lib/ssh.sh facade) ────────
    echo "[IMP:9][reconcile][remote] Executing converge --reconcile on root@${ssh_host}" >&2
    # ⚠️ TRAP[BUG] · 2026-07-24 · P4 · DevPlan 065: bare ssh_exec may silently fail under set -e
    ssh_exec "${ssh_host}" "root" "${remote_cmd}" "" "deploy" || {
        local rc=$?
        log_imp 1 "execute_remote_reconcile" "SSH exec failed on ${ssh_host} — exit=${rc}"
        return "${rc}"
    }
}
# endregion FUNC_execute_remote_reconcile

# ═══════════════════════════════════════════════════════════════════
# EXECUTE REMOTE RECONCILE (ALTERNATE) — via converge.sh entrypoint
# ═══════════════════════════════════════════════════════════════════
# region FUNC_execute_remote_reconcile_entrypoint
## @purpose  Higher-level wrapper — calls converge.sh entrypoint with --reconcile
##           on the remote VPS. Uses execute_remote_reconcile internally.
## @param $1  Node name
## @param @2+  Passthrough args for converge.sh
execute_remote_reconcile_entrypoint() {
    local node_name="$1"
    shift 1
    execute_remote_reconcile "${node_name}" "$@"
}
# endregion FUNC_execute_remote_reconcile_entrypoint
