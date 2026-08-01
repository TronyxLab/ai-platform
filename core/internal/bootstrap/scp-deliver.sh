# shellcheck shell=bash
# GREP_SUMMARY: bootstrap scp-deliver scp_to_server prepare_ssh_opts ssh-keygen ssh-opts thin-facade python3 core-deliverer sourced-library
# STRUCTURE: ▶ init SSH_OPTS → ⚡ prepare_ssh_opts(host, mode) → ⚡ scp_to_server(host, node, ncd, cd) → python3 core_deliverer deliver → ⎋ return $?
# region MODULE_CONTRACT
## @purpose  SCP delivery facade: prepare_ssh_opts (host-key cleanup, 4 active callers) + thin scp_to_server → python3 core_deliverer (DevPlan 108).
## @scope    Sourced by bootstrap.sh + remote-cmd.sh (prepare_ssh_opts ×3). Not for direct invocation.
## @invariants  SSH_OPTS init-guarded; exit 0|1 passthrough; ssh-keygen -R init-only. @rationale Strangler-Fig Tier 2 (108).
## ⚠️ TRAP[DECISION] · 2026-07-17 · HI · Rejected: entrypoint-manifest.yaml registration (sourced lib corrupts gate semantics)
## · Sibling convention: remote-cmd.sh. Rev: direct exec → restore shebang.
## @changes 2026-07-31 | DevPlan 108 — 251→≤60 LOC; rsync/ssh оркестрация → core_deliverer.py
# endregion MODULE_CONTRACT
if [[ -z "${PATHS_LIB_DIR:-}" ]]; then
    _SCP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../../lib/paths.sh
    source "${_SCP_DIR}/../../lib/paths.sh"
    unset _SCP_DIR
fi
# shellcheck source=../../lib/ssh.sh
source "${PATHS_LIB_DIR}/ssh.sh"
if [[ "$(declare -p SSH_OPTS 2>/dev/null || true)" != "declare -a SSH_OPTS="* ]]; then
    SSH_OPTS=()
fi
# region FUNC_prepare_ssh_opts
## @purpose  Populate SSH_OPTS array. Remove old SSH host key (mode=init). @param $1 host; $2 init|update
## ⚠️ TRAP[DECISION] · 2026-07-18 · HI · M7/G4: known_hosts init-only
## · Rejected: ssh-keygen -R at every deploy (MITM protection defeated)
## · Reason: per G4 resolution, ssh-keygen -R only in init mode (honest TOFU).
## · Rev: if reinstall-detection needed in CI, add a separate mechanism
prepare_ssh_opts() {
    local ssh_host="$1"
    local mode="${2:-init}"
    echo "[IMP:8][bootstrap][ssh] BACKWARD-COMPAT: prepare_ssh_opts() — use SSH_OPTS_COMMON from lib/ssh.sh" >&2
    if [[ "${mode}" == "init" ]]; then
        echo "[IMP:8][bootstrap][ssh] Cleaning SSH host key for ${ssh_host} (mode=init)"
        ssh-keygen -R "${ssh_host}" 2>/dev/null || true
    else
        echo "[IMP:8][bootstrap][ssh] Preserving known_hosts for ${ssh_host} (mode=${mode})"
    fi
    SSH_OPTS=("${SSH_OPTS_COMMON[@]}")
    echo "[IMP:7][bootstrap][ssh] SSH_OPTS populated from SSH_OPTS_COMMON" >&2
}
# endregion FUNC_prepare_ssh_opts
# region FUNC_scp_to_server
## @purpose  Thin wrapper → core_deliverer.py deliver (mkdir + 5 rsync фаз, exit 0|1 passthrough).
## 🧐 TRAP[DECISION] · 2026-07-31 · — · DRY_RUN guard [[ == "true" ]] вместо ${DRY_RUN:+--dry-run}
## · Rejected: bootstrap.sh DRY_RUN="false" (непустая строка) → bare ${...:+} всегда dry-run
## · Reason: intent AC5 — dry-run флаг только при фактическом dry-run; bootstrap-путь не меняется
scp_to_server() {
    local ssh_host="$1" node_name="$2" node_configs_dir="$3" core_dir="$4"
    local dry_arg=""; [[ "${DRY_RUN:-}" == "true" ]] && dry_arg="--dry-run"
    python3 -m core.internal.bootstrap.core_deliverer deliver \
        --host "${ssh_host}" \
        --node "${node_name}" \
        --node-configs-dir "${node_configs_dir}" \
        --core-dir "${core_dir}" \
        --remote-user "${REMOTE_SSH_USER:-root}" \
        ${dry_arg:+--dry-run}
}
# endregion FUNC_scp_to_server
