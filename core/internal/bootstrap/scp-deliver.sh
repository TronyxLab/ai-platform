# shellcheck shell=bash
# GREP_SUMMARY: bootstrap scp-deliver scp_to_server prepare_ssh_opts rsync ssh ssh-keygen ssh-opts mkdir-p remote-transfer
# STRUCTURE: ▶ init SSH_OPTS → ⚡ prepare_ssh_opts(ssh_host) → ⚡ scp_to_server(host, node, ncd, cd) → ┌mkdir -p┐ → ⚡ phase 1/4 rsync core/ → ⚡ phase 1b/4 rsync platform-env.yaml → ⚡ phase 1c/4 rsync Makefile → ⚡ phase 2/4 rsync node-configs/ → ◇ secrets? → ⚡ phase 3/4 rsync secrets/ → ⎋ return 0|1
# region MODULE_CONTRACT
## @purpose  SCP delivery functions for bootstrap — rsync core/ + node-configs/ to remote server,
##           and prepare SSH options (host key cleanup + opts array)
## @scope    Sourced by core/entrypoints/bootstrap.sh. Provides scp_to_server() and prepare_ssh_opts().
##           Not intended for direct invocation.
## @invariants
##   - SSH_OPTS global array is initialized in this file (readonly initialization guard)
##   - All rsync operations use --delete for idempotent sync
##   - Remote directories are created via ssh mkdir -p before rsync (bare metal safe)
##   - scp_to_server returns 0 on success, 1 on any rsync/ssh failure
##   - prepare_ssh_opts runs ssh-keygen -R to gracefully handle server recreation
##   - Phase order: core/ → platform-env.yaml → Makefile → node-configs/ → secrets/
## @rationale Extraction from bootstrap.sh to thin-wrappen entrypoint. Layer re-homing T15 (DevPlan 020).
##            scp_to_server carries Makefile rsync (Phase 1c, T6 from DevPlan 020).
## @changes 2026-07-17 | T15 — Extracted from bootstrap.sh (pure extraction, identical logic)
##           ｜           Multi-line SSH_OPTS spanning removed, SSH_OPTS init moved to this file
## @rationale Q: Why 3 separate rsync calls for root-level files (platform-env.yaml, Makefile) instead of one?
##            A: Different remote destinations — platform-env.yaml → /opt/platform/, Makefile → /opt/platform/.
##            Same destination = two separate rsync calls (conservative pattern from original bootstrap.sh).
##            If more root-level files need SCP, implement a manifest-based sync approach.
## @rationale Q: Why not use UserKnownHostsFile=/dev/null in prepare_ssh_opts?
##            A: That completely disables host key checking (MITM risk). Removing the old key first + accept-new
##            is safer — it only adds new keys, doesn't blindly accept all.
## ⚠️ TRAP[DECISION] · 2026-07-17 · HI · Rejected: registration in entrypoint-manifest.yaml
## · Rejected: entrypoint-manifest.yaml — registry of executable operations; sourced library
##   would corrupt gate semantics (gate checks for shebang + manifest registration).
## · Follows sibling convention: remote-cmd.sh, content-hash.sh also non-shebang sourced libs.
## · Rev: if a direct exec invocation of scp-deliver.sh is added — restore shebang and register.
# endregion MODULE_CONTRACT

# ── source paths.sh for PLATFORM_ROOT ──────────────────────────────
# Guard: if paths.sh already sourced from entrypoint, skip
if [[ -z "${PATHS_LIB_DIR:-}" ]]; then
    # Resolve our own path when sourced from different SCRIPT_DIR contexts
    # shellcheck disable=SC2128  # BASH_SOURCE[0] is correct in source-context
    _SCP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../../lib/paths.sh
    source "${_SCP_DIR}/../../lib/paths.sh"
    unset _SCP_DIR
fi

# ── SSH_OPTS global array ──────────────────────────────────────────
# Must be declared BEFORE prepare_ssh_opts and scp_to_server use it.
# Guard against re-sourcing (second source doesn't reset)
if [[ "$(declare -p SSH_OPTS 2>/dev/null || true)" != "declare -a SSH_OPTS="* ]]; then
    SSH_OPTS=()
fi

# ═══════════════════════════════════════════════════════════════════
# SSH: Clean host key + build opts
# ═══════════════════════════════════════════════════════════════════
# region FUNC_prepare_ssh_opts
## @purpose  Remove old SSH host key (server recreation) and populate SSH_OPTS array
## @param $1  SSH host (IP or domain)
## @globals  SSH_OPTS — array of -o flags for ssh
## @complexity O(1)
## @invariants
##   - ssh-keygen -R is always run first (may fail if host not in known_hosts)
##   - StrictHostKeyChecking=accept-new allows new host keys without manual prompt
##   - ConnectTimeout=30 avoids hanging on unreachable hosts
##   - ServerAliveInterval=30 + ServerAliveCountMax=10 for long-running SSH sessions
prepare_ssh_opts() {
    local ssh_host="$1"

    # Remove old host key to gracefully handle server recreation
    echo "[IMP:8][bootstrap][ssh] Cleaning SSH host key for ${ssh_host} (server recreation safe)"
    ssh-keygen -R "${ssh_host}" 2>/dev/null || true

    # Populate global SSH_OPTS array
    SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o ServerAliveInterval=30 -o ServerAliveCountMax=10)
}
# endregion FUNC_prepare_ssh_opts

# ═══════════════════════════════════════════════════════════════════
# SCP PHASE: Rsync core/ and node-configs/ to remote server
# ═══════════════════════════════════════════════════════════════════
# region FUNC_scp_to_server
## @purpose  Rsync core/ + platform-env.yaml + Makefile + node-configs/<node>/ + node-configs/secrets/ to remote server
## @param $1  SSH host (IP or domain)
## @param $2  Node name
## @param $3  Node configs root directory (local)
## @param $4  Core directory (local)
## @globals  SSH_OPTS — array of -o flags for ssh (populated by prepare_ssh_opts)
##           REMOTE_SSH_USER — remote user (default: root)
##           PLATFORM_REMOTE_BASE — remote platform base (default: /opt/platform)
##           NODE_CONFIGS_REMOTE_BASE — remote node-configs base (default: /opt/node-configs)
## @io       stdout/stderr: rsync progress + LDD logs
##           exit 0: all rsyncs succeeded, exit 1: any rsync failed
## @complexity O(F) where F = number of files to transfer
## @rationale Q: Why 3 separate rsync calls instead of one big one?
##            A: Different remote destinations: core/ → ${PLATFORM_ROOT}/core/,
##            node-configs/<node>/ → /opt/node-configs/<node>/,
##            secrets/ → /opt/node-configs/secrets/.
##            They arrive in different directory subtrees.
scp_to_server() {
    local ssh_host="$1"
    local node_name="$2"
    local node_configs_dir="$3"
    local core_dir="$4"

    local remote_user="${REMOTE_SSH_USER:-root}"
    local remote_platform_base="${PLATFORM_REMOTE_BASE:-${PLATFORM_ROOT:-/opt/platform}}"
    local remote_node_configs_base="${NODE_CONFIGS_REMOTE_BASE:-/opt/node-configs}"

    # ── Ensure target directories exist on remote server ──────────────
    # ⚠️ TRAP[BUG] · 2026-07-16 · FIXED (D2) · Bare server: mkdir -p отсутствовал
    # · Symptom: `rsync: mkdir /opt/platform/core/ failed: No such file or directory` на bare VPS
    # · Root: rsync не создаёт родительские директории на удалённом сервере без --rsync-path="mkdir -p ..."
    # ·   Сисадмин вручную создавал /opt/platform/ — правка не попала в diff.
    # · Fix: явный ssh mkdir -p для всех целевых директорий перед rsync.
    # ·   Каждый bootstrap начинается с создания иерархии — bare metal safe.
    # · Rev: если появятся новые target-директории — добавить их сюда.
    echo "[IMP:8][bootstrap][scp] Ensuring remote directories exist on ${ssh_host}"
    # Use SSH_OPTS for StrictHostKeyChecking and timeouts — populated by prepare_ssh_opts
    # shellcheck disable=SC2086  # SSH_OPTS is intentionally word-split from array
    ssh ${SSH_OPTS[*]:--o StrictHostKeyChecking=accept-new -o ConnectTimeout=30} "${remote_user}@${ssh_host}" \
        "mkdir -p ${remote_platform_base}/core ${remote_node_configs_base}/${node_name} ${remote_node_configs_base}/secrets" || {
        echo "[IMP:10][bootstrap][scp] FATAL: ssh mkdir -p failed for ${ssh_host}" >&2
        return 1
    }
    echo "[IMP:9][bootstrap][scp] Remote directories confirmed"

    # ── SCP 1: core/ → ${PLATFORM_ROOT:-/opt/platform}/core/ ───────────────────
    echo "[IMP:9][bootstrap][scp] Phase 1/4: Rsyncing core/ → ${ssh_host}:${remote_platform_base}/core/"
    local core_src="${core_dir}/"
    local core_dst="${remote_user}@${ssh_host}:${remote_platform_base}/core/"
    if ! rsync -avz --delete \
        --exclude=.git \
        --exclude=__pycache__ \
        --exclude=.pytest_cache \
        --exclude='default-user.xml' \
        --exclude='.env' \
        "${core_src}" \
        "${core_dst}"; then
        echo "[IMP:10][bootstrap][scp] FATAL: rsync core/ failed for ${ssh_host}" >&2
        return 1
    fi
    echo "[IMP:9][bootstrap][scp] Phase 1/4: core/ rsync complete"

    # 🧐 TRAP[DECISION] · 2026-07-16 · — · SCP platform-env.yaml from project root to /opt/platform/
    # · Rejected: duplicating platform-env.yaml into core/ (cross-layer violation)
    # · Reason: bootstrap only SCPs core/ and node-configs/; root-level platform-env.yaml is separate
    # · Rev: if more root-level files need SCP, implement a manifest-based sync approach
    # ── SCP 1b: platform-env.yaml → ${PLATFORM_ROOT:-/opt/platform}/ ──
    local platform_env_src="${core_dir}/../platform-env.yaml"
    if [[ -f "$platform_env_src" ]]; then
        echo "[IMP:9][bootstrap][scp] Phase 1b/4: Rsyncing platform-env.yaml → ${ssh_host}:${remote_platform_base}/"
        local env_dst="${remote_user}@${ssh_host}:${remote_platform_base}/platform-env.yaml"
        if ! rsync -avz "${platform_env_src}" "${env_dst}"; then
            echo "[IMP:10][bootstrap][scp] FATAL: rsync platform-env.yaml failed for ${ssh_host}" >&2
            return 1
        fi
        echo "[IMP:9][bootstrap][scp] Phase 1b/4: platform-env.yaml rsync complete"
    else
        echo "[IMP:8][bootstrap][scp] Phase 1b/4: SKIP — platform-env.yaml not found at ${platform_env_src}"
    fi

    # 🧐 TRAP[DECISION] · 2026-07-17 · — · SCP Makefile from project root to /opt/platform/
    # · Rejected: manifest-based sync approach (over-engineering for one extra file)
    # · Reason: follows same pattern as platform-env.yaml (Phase 1b) — explicit rsync call
    # · Rev: if more root-level files need SCP, implement a manifest-based sync approach
    # ── SCP 1c: Makefile → ${PLATFORM_ROOT:-/opt/platform}/ ──
    local makefile_src="${core_dir}/../Makefile"
    if [[ -f "$makefile_src" ]]; then
        echo "[IMP:9][bootstrap][scp] Phase 1c/4: Rsyncing Makefile → ${ssh_host}:${remote_platform_base}/"
        local makefile_dst="${remote_user}@${ssh_host}:${remote_platform_base}/Makefile"
        if ! rsync -avz "${makefile_src}" "${makefile_dst}"; then
            echo "[IMP:10][bootstrap][scp] FATAL: rsync Makefile failed for ${ssh_host}" >&2
            return 1
        fi
        echo "[IMP:9][bootstrap][scp] Phase 1c/4: Makefile rsync complete"
    else
        echo "[IMP:8][bootstrap][scp] Phase 1c/4: SKIP — Makefile not found at ${makefile_src}"
    fi

    # ── SCP 2: node-configs/<node>/ → /opt/node-configs/<node>/ ──
    echo "[IMP:9][bootstrap][scp] Phase 2/4: Rsyncing node-configs/${node_name}/ → ${ssh_host}:${remote_node_configs_base}/${node_name}/"
    local node_src="${node_configs_dir}/${node_name}/"
    local node_dst="${remote_user}@${ssh_host}:${remote_node_configs_base}/${node_name}/"
    if ! rsync -avz --delete \
        --exclude=.git \
        --exclude=__pycache__ \
        --exclude=.pytest_cache \
        "${node_src}" \
        "${node_dst}"; then
        echo "[IMP:10][bootstrap][scp] FATAL: rsync node-configs/${node_name}/ failed for ${ssh_host}" >&2
        return 1
    fi
    echo "[IMP:9][bootstrap][scp] Phase 2/4: node-configs/${node_name}/ rsync complete"

    # ── SCP 3: node-configs/secrets/ → /opt/node-configs/secrets/ ──
    if [[ -d "${node_configs_dir}/secrets" ]]; then
        echo "[IMP:9][bootstrap][scp] Phase 3/4: Rsyncing node-configs/secrets/ → ${ssh_host}:${remote_node_configs_base}/secrets/"
        local secrets_src="${node_configs_dir}/secrets/"
        local secrets_dst="${remote_user}@${ssh_host}:${remote_node_configs_base}/secrets/"
        if ! rsync -avz --delete \
            --exclude=.git \
            "${secrets_src}" \
            "${secrets_dst}"; then
            echo "[IMP:10][bootstrap][scp] FATAL: rsync node-configs/secrets/ failed for ${ssh_host}" >&2
            return 1
        fi
        echo "[IMP:9][bootstrap][scp] Phase 3/4: node-configs/secrets/ rsync complete"
    else
        echo "[IMP:8][bootstrap][scp] Phase 3/4: SKIP — no secrets/ directory at ${node_configs_dir}/secrets"
    fi

    return 0
}
# endregion FUNC_scp_to_server
