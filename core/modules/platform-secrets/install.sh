#!/usr/bin/env bash
# GREP_SUMMARY: platform-secrets systemd oneshot decrypt sops age secrets env boot tmpfs install enable
# STRUCTURE: guard(root≥0) → check prereqs(age-key,secrets-enc) → copy service → daemon-reload → enable service → log done
# region MODULE_CONTRACT
## @purpose  Idempotent systemd oneshot service installation for platform secrets decryption on boot
## @scope    Called during bootstrap step ⑪ for system-type modules; generates /run/platform/secrets.env
## @invariants
##   - Service runs before docker.service (Before=docker.service in unit file)
##   - Service does NOT start during install — runs on next boot (Type=oneshot, RemainAfterExit=yes)
##   - /etc/platform/age-key.txt must exist with mode 600 and root:root ownership
##   - ${PLATFORM_ROOT}/secrets/secrets.enc.yaml must exist (encrypted SOPS file)
## @rationale Secrets must be available before Docker starts so containers receive env vars from
##   tmpfs-based secrets.env at boot; systemd oneshot guarantees ordering without blocking boot
##   (Type=oneshot with RemainAfterExit=yes runs once and records success)
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/audit_logging.sh" 2>/dev/null || true

__LOG_PREFIX="platform-secrets"
source "${SCRIPT_DIR}/../../lib/logging.sh"
# shellcheck source=core/lib/paths.sh
source "${SCRIPT_DIR}/../../lib/paths.sh"

# region CHECK_PREREQUISITES
## @brief  Validate/create age key file and encrypted secrets file before installing the service.
## @detail
##   - /etc/platform/age-key.txt: created from AGE_SECRET_KEY env if missing; mode 600 root:root enforced
##   - KEY=VALUE format: writes AGE_SECRET_KEY=<value> (not bare value) — systemd EnvironmentFile
##     requires KEY=VALUE lines; bare values without '=' are silently ignored (B3 fix)
##   - ${PLATFORM_ROOT}/secrets/secrets.enc.yaml: must exist (encrypted SOPS file);
##     falls back to /opt/node-configs/secrets/*.enc.yaml during bootstrap
##   - During bootstrap, AGE_SECRET_KEY is set from --age-secret-key CLI arg
##   - On first provision, the secrets file may be at a node-configs subpath
##   - If age-key.txt exists with wrong permissions, auto-fixes (chown root:root, chmod 600)
##     instead of failing — ensures idempotent repeat-run behavior (# T5)
##   - If age-key.txt exists in old bare format (no AGE_SECRET_KEY= prefix), auto-migrates
##     to KEY=VALUE format — ensures idempotent migration on existing deployments (B3)
check_prerequisites() {
    local age_key="/etc/platform/age-key.txt"
    local secrets_enc="${PLATFORM_ROOT}/secrets/secrets.enc.yaml"
    local ok=true

    # [IMP:9][platform-secrets-install][prereqs] Age key: create from env if missing
    if [[ ! -f "$age_key" ]]; then
        if [[ -n "${AGE_SECRET_KEY:-}" ]]; then
            log_step "prereqs" "INFO" "Age key file not found — creating from AGE_SECRET_KEY env: ${age_key}"
            mkdir -p "$(dirname "$age_key")"
            # ⚠️ TRAP[BUG] · 2026-07-15 · bare key in EnvironmentFile — systemd ignores lines without '='
            # · Root: install.sh wrote bare key with printf '%%s\\n' "$AGE_SECRET_KEY" but
            #   systemd EnvironmentFile= requires KEY=VALUE format. Lines without '=' are
            #   silently ignored → AGE_SECRET_KEY not exported → decrypt fails at boot.
            # · Fix: changed to printf 'AGE_SECRET_KEY=%%s\\n' to match EnvironmentFile format.
            # · Prevention: static test test_agekey_environmentfile_format_contract
            #   (cross-file invariant B3) catches regression.
            printf 'AGE_SECRET_KEY=%s\n' "$AGE_SECRET_KEY" > "$age_key"
            chmod 0600 "$age_key"
            chown root:root "$age_key"
            log_step "prereqs" "OK" "Age key file created: ${age_key} (mode 600, root:root, EnvironmentFile format)"
        else
            log_step "prereqs" "FAIL" "Age key file not found: ${age_key} and AGE_SECRET_KEY not set"
            ok=false
        fi
    else
        # [IMP:9][platform-secrets-install][prereqs] Age key exists — verify/auto-fix permissions
        # 🧐 TRAP[DECISION] · 2026-07-12 · — · Auto-fix age-key.txt permissions instead of failing
        # · Rejected: fail-fast on wrong permissions (requires manual intervention on every repeat-run)
        # · Reason: bootstrap is designed to be idempotent — second run should not fail on
        #   something the module itself created. Auto-fix makes repeat-run = no-op for the
        #   permission check too.
        # · Rev: if a security audit requires explicit failure on wrong permissions, remove the
        #   auto-fix and add a dedicated audit check in core/internal/audit/ instead.
        local need_fix=false
        local owner_mode
        owner_mode="$(stat -c '%U:%G %a' "$age_key" 2>/dev/null || stat -f '%Su:%Sg %Lp' "$age_key" 2>/dev/null || echo "unknown")"
        local owner="${owner_mode% *}"
        local mode="${owner_mode#* }"

        if [[ "$owner" != "root:root" ]]; then
            log_step "prereqs" "INFO" "Age key file owner is ${owner}, fixing to root:root"
            chown root:root "$age_key"
            need_fix=true
        fi
        if [[ "$mode" != "600" ]]; then
            log_step "prereqs" "INFO" "Age key file mode is ${mode}, fixing to 600"
            chmod 0600 "$age_key"
            need_fix=true
        fi

        # [IMP:9][platform-secrets-install][prereqs] Auto-migrate bare key → KEY=VALUE format
        # If the file exists but first non-empty line does NOT start with kv_prefix=,
        # assume it is a bare key and rewrite in EnvironmentFile-compatible format.
        local kv_prefix="AGE_SECRET_KEY"
        local first_line
        first_line="$(head -1 "$age_key" 2>/dev/null || true)"
        if [[ -n "$first_line" && "$first_line" != "${kv_prefix}="* ]]; then
            log_step "prereqs" "INFO" "Age key file has bare format — migrating to EnvironmentFile KEY=VALUE format"
            # Read the bare key (first line), rewrite in new format
            local bare_key
            bare_key="$(head -1 "$age_key" | tr -d '\n\r')"
            printf '%s=%s\n' "$kv_prefix" "$bare_key" > "$age_key"
            chmod 0600 "$age_key"
            log_step "prereqs" "OK" "Age key file migrated to EnvironmentFile format"
        fi

        if [[ "$need_fix" == "true" ]]; then
            log_step "prereqs" "OK" "Age key file permissions auto-fixed: ${age_key} (root:root, 600)"
        else
            log_step "prereqs" "OK" "Age key file: ${age_key} (owner: ${owner}, mode: ${mode})"
        fi
    fi

    # [IMP:9][platform-secrets-install][prereqs] Encrypted secrets: check expected path + fallback
    if [[ ! -f "$secrets_enc" ]]; then
        # 🧐 TRAP[BUG] · 2026-07-01 · — · VPS stores secrets at node-configs path, not ${PLATFORM_ROOT}/secrets/
        # · Fallback: search node-configs secrets dir for any .enc.yaml file
        local node_secrets_dir="/opt/node-configs/secrets"
        local found_enc
        found_enc="$(find "$node_secrets_dir" -name '*.enc.yaml' -maxdepth 1 2>/dev/null | head -1 || true)"
        if [[ -n "$found_enc" ]]; then
            log_step "prereqs" "INFO" "Secrets file not at expected path — found at ${found_enc}, symlinking"
            mkdir -p "$(dirname "$secrets_enc")"
            ln -sf "$found_enc" "$secrets_enc"
            log_step "prereqs" "OK" "Symlink created: ${secrets_enc} → ${found_enc}"
        else
            log_step "prereqs" "FAIL" "Encrypted secrets file not found: ${secrets_enc} (checked /opt/node-configs/secrets/*.enc.yaml too)"
            ok=false
        fi
    else
        log_step "prereqs" "OK" "Encrypted secrets file found: ${secrets_enc}"
    fi

    if [[ "$ok" != "true" ]]; then
        log_step "prereqs" "ABORT" "Prerequisites not met — refusing to install platform-secrets service"
        exit 1
    fi

    log_step "prereqs" "DONE" "All prerequisites satisfied"
}
# endregion CHECK_PREREQUISITES

# region ENSURE_PLATFORM_DIRS
## @brief  Create ${PLATFORM_ROOT}/ directory structure with platform-group write access.
## @detail
##   - Creates ${PLATFORM_ROOT}/ (if missing) with 2775 (setgid + rwx for owner+group)
##   - Creates ${PLATFORM_ROOT}/prometheus-targets/ subdirectory
##   - Creates ${PLATFORM_ROOT}/secrets/ subdirectory
##   - Sets group to a common platform group so platform/ci-deploy users can write
##   - This ensures platform-deploy.sh can generate catalog.json and prometheus targets
ensure_platform_dirs() {
    local platform_dir="${PLATFORM_ROOT}"
    local platform_gid
    platform_gid="$(id -g platform 2>/dev/null || echo 0)"

    if [[ ! -d "$platform_dir" ]]; then
        log_step "dirs" "INFO" "Creating ${platform_dir} with setgid for platform group"
        mkdir -p "${platform_dir}/prometheus-targets" "${platform_dir}/secrets"
        chown "root:${platform_gid}" "$platform_dir"
        chmod 2775 "$platform_dir"       # setgid + rwx for owner/group
        chown "root:${platform_gid}" "${platform_dir}/prometheus-targets"
        chmod 2775 "${platform_dir}/prometheus-targets"
        log_step "dirs" "DONE" "Platform dirs created at ${platform_dir}"
    else
        # Ensure prometheus-targets exists
        mkdir -p "${platform_dir}/prometheus-targets"
        log_step "dirs" "SKIP" "${platform_dir} already exists"
    fi
}
# endregion ENSURE_PLATFORM_DIRS

# region INSTALL_SERVICE
## @brief  Copy systemd unit file, daemon-reload, and enable the service.
## @detail
##   - Copies platform-secrets.service to /etc/systemd/system/
##   - Runs systemctl daemon-reload to pick up new unit
##   - Enables the service (WantedBy=multi-user.target) — does NOT start it
##   - Does NOT start the service — it runs on boot (oneshot with RemainAfterExit=yes)
install_service() {
    local unit_src="${SCRIPT_DIR}/platform-secrets.service"
    local unit_dst="/etc/systemd/system/platform-secrets.service"

    # [IMP:9][platform-secrets-install][install] Copy systemd unit file
    log_step "install" "START" "Installing systemd unit: ${unit_dst}"
    if [[ ! -f "$unit_src" ]]; then
        log_step "install" "FAIL" "Unit file not found at source: ${unit_src}"
        exit 1
    fi

    cp "$unit_src" "$unit_dst"
    chmod 0644 "$unit_dst"
    log_step "install" "INFO" "Unit file copied: ${unit_src} → ${unit_dst}"

    # [IMP:8][platform-secrets-install][install] daemon-reload to register new unit
    systemctl daemon-reload
    log_step "install" "OK" "systemctl daemon-reload completed"

    # [IMP:9][platform-secrets-install][install] Enable service (does NOT start it)
    systemctl enable platform-secrets.service --quiet
    log_step "install" "OK" "platform-secrets.service enabled (WantedBy=multi-user.target)"

    log_step "install" "DONE" "Systemd unit installed and enabled — will run on next boot"
}
# endregion INSTALL_SERVICE

main() {
    # [IMP:10][platform-secrets-install][main] Root required — service installs to /etc/systemd/system
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[IMP:10][platform-secrets-install][main] ERROR: must run as root" >&2
        exit 1
    fi

    log_step "main" "START" "platform-secrets module installation"

    check_prerequisites
    ensure_platform_dirs
    install_service

    log_step "main" "DONE" "platform-secrets module installation complete"
}

main "$@"
