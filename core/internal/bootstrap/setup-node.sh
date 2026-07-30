#!/usr/bin/env bash
# GREP_SUMMARY: setup-node users platform ci-deploy sudoers visudo atomic SSH authorized_keys
# STRUCTURE: create_user(platform) → add_owner_key → create_user(ci-deploy) → add_ci_deploy_command(restrict) → generate_sudoers(visudo-c+atomic-mv)
# region MODULE_CONTRACT
## @purpose  Create platform/ci-deploy users, manage SSH authorized_keys, generate sudoers safely
## @scope    Called from bootstrap.sh steps ④⑤⑬; idempotent via id guard + key grep
##          add_ci_deploy_command configures forced-command SSH + docker/adm groups + separate sudoers
## @invariants
##   - useradd skipped if `id <user>` succeeds (idempotency)
##   - authorized_keys entry added only if grep finds it absent
##   - ci-deploy SSH key uses command="python3 -m core.internal.deploy.orchestrator_cli receive",restrict (no shell access, 00 §4)
##   - ci-deploy is in docker group → no sudo for docker commands; sudoers only: nginx reload/status
##   - ci-deploy role is SEPARATE from ci role — different scope and sudoers entries (06 §4.2)
##   - sudoers generated via temp file → visudo -c → atomic mv (lockout-safe, SC5)
##   - On visudo -c failure: original sudoers untouched, bootstrap aborted
## @rationale visudo -c guard: sudoers syntax error = root lockout; temp+validate+mv prevents this (00 §12)
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../audit/audit.sh" 2>/dev/null || true

__LOG_PREFIX="setup-node"
source "${SCRIPT_DIR}/../../lib/logging.sh"
# shellcheck source=core/lib/paths.sh
source "${SCRIPT_DIR}/../../lib/paths.sh"

# region CREATE_USER
create_user() {
    local username="$1"
    local shell="${2:-/bin/bash}"
    local groups="${3:-}"

    # [IMP:9][setup-node][users] Idempotency guard: skip if user already exists
    if id "$username" &>/dev/null 2>&1; then
        log_step "user:${username}" "SKIP" "User '${username}' already exists"
        return 0
    fi

    log_step "user:${username}" "START" "Creating system user '${username}'"
    local useradd_args=("--system" "--shell" "$shell" "--create-home" "--home-dir" "/home/${username}")
    if [[ -n "$groups" ]]; then
        useradd_args+=(--groups "$groups")
    fi
    useradd "${useradd_args[@]}" "$username"
    log_step "user:${username}" "DONE" "User '${username}' created"
}
# endregion CREATE_USER

# region ADD_SSH_KEY
add_ssh_key() {
    local username="$1"
    local pubkey="$2"
    local key_options="${3:-}"  # e.g. 'command="platform-deploy node1",restrict'

    local home_dir="/home/${username}"
    local auth_keys="${home_dir}/.ssh/authorized_keys"

    mkdir -p "${home_dir}/.ssh"
    chmod 0700 "${home_dir}/.ssh"
    chown "${username}:${username}" "${home_dir}/.ssh"

    # [IMP:9][setup-node][ssh-key] Idempotency: add key only if not already present
    if grep -qF "$pubkey" "$auth_keys" 2>/dev/null; then
        log_step "ssh-key:${username}" "SKIP" "SSH key already in authorized_keys"
        return 0
    fi

    if [[ -n "$key_options" ]]; then
        printf '%s %s\n' "$key_options" "$pubkey" >> "$auth_keys"
    else
        printf '%s\n' "$pubkey" >> "$auth_keys"
    fi

    chmod 0600 "$auth_keys"
    chown "${username}:${username}" "$auth_keys"
    log_step "ssh-key:${username}" "DONE" "SSH key added to ${auth_keys}"
}
# endregion ADD_SSH_KEY

# region ADD_CI_DEPLOY_COMMAND
## @brief  Configure ci-deploy user: forced command SSH key + docker/adm group membership
## @param  $1  node_name (for logging context)
## @param  $2  deploy_key (public SSH key for ci-deploy)
## @detail Authorized_keys entry: command="python3 -m core.internal.deploy.orchestrator_cli receive",restrict
##         No shell access; only platform-deploy.sh can be executed.
##         ci-deploy added to docker group (no sudo for docker) and adm group (audit log write).
##         Separate sudoers entry from ci role (06 §4.2).
add_ci_deploy_command() {
    local node_name="$1"
    local deploy_key="$2"

    log_step "ci-deploy-command" "START" "Configuring ci-deploy with forced command=python3 -m core.internal.deploy.orchestrator_cli receive"

    # Idempotent user creation with docker + adm groups
    # docker group: direct docker socket access (no sudo) — principle of least privilege
    # adm group: write access to /var/log/platform/audit.log (see audit.sh)
    create_user "ci-deploy" "/bin/bash" "docker,adm"

    # Ensure adm group membership even on existing users (idempotent: -aG won't duplicate)
    usermod -aG adm "ci-deploy" 2>/dev/null || true
    usermod -aG docker "ci-deploy" 2>/dev/null || true

    if [[ -z "$deploy_key" ]]; then
        log_step "ci-deploy-command" "WARN" "No deploy key provided; SSH key setup skipped"
        return 0
    fi

    # [IMP:9][setup-node][ci-deploy-command] Forced command restricts ci-deploy to ONLY platform-deploy.sh
    # SSH_ORIGINAL_COMMAND will carry <project> <ref> — see platform-deploy.sh parse_ssh_command
    local restrict_opts="command=\"python3 -m core.internal.deploy.orchestrator_cli receive\",restrict"
    add_ssh_key "ci-deploy" "$deploy_key" "$restrict_opts"

    log_step "ci-deploy-command" "DONE" "ci-deploy: forced_command=platform-deploy.sh, restrict enabled, groups=docker,adm"
}
# endregion ADD_CI_DEPLOY_COMMAND

# region GENERATE_SUDOERS
generate_sudoers() {
    local node_name="$1"

    log_step "sudoers" "START" "Generating sudoers for platform node: ${node_name}"

    local sudoers_file="/etc/sudoers.d/platform-${node_name}"
    local tmp_sudoers
    tmp_sudoers="$(mktemp /tmp/platform-sudoers-XXXXXX)"
    chmod 0440 "$tmp_sudoers"

    # Write sudoers content
    cat > "$tmp_sudoers" <<EOF
# core sudoers — ${node_name}
# Generated by setup-node.sh at $(date -u '+%Y-%m-%dT%H:%M:%SZ')
# DO NOT edit manually — managed by core bootstrap

# platform user: nginx management
platform ALL=(root) NOPASSWD: /bin/systemctl start nginx
platform ALL=(root) NOPASSWD: /bin/systemctl stop nginx
platform ALL=(root) NOPASSWD: /bin/systemctl restart nginx
platform ALL=(root) NOPASSWD: /bin/systemctl reload nginx
platform ALL=(root) NOPASSWD: /bin/systemctl status nginx
platform ALL=(root) NOPASSWD: /usr/sbin/nginx -t

# platform user: docker management
platform ALL=(root) NOPASSWD: /usr/bin/docker compose *
platform ALL=(root) NOPASSWD: /usr/bin/docker ps *
platform ALL=(root) NOPASSWD: /usr/bin/docker logs *
platform ALL=(root) NOPASSWD: /usr/bin/docker restart *
platform ALL=(root) NOPASSWD: /usr/bin/docker exec *
platform ALL=(root) NOPASSWD: /usr/bin/docker stats *

# platform user: platform operations
platform ALL=(root) NOPASSWD: ${PLATFORM_ROOT}/core/internal/bootstrap/node-lifecycle.sh
platform ALL=(root) NOPASSWD: /usr/bin/rsync *

# platform user: diagnostic commands
platform ALL=(root) NOPASSWD: /usr/sbin/ufw status verbose
platform ALL=(root) NOPASSWD: /usr/bin/cat /var/log/platform/audit.log
platform ALL=(root) NOPASSWD: /usr/sbin/ss -tlnp
platform ALL=(root) NOPASSWD: /usr/sbin/iptables -t nat -L -n

# ci-deploy user: nginx reload only — NO docker commands via sudo
# ci-deploy is in docker group → direct docker socket access (no sudo needed)
# /usr/bin/docker compose * intentionally NOT granted — principle of least privilege (06 §4.2)
# Role ci-deploy is SEPARATE from role ci — different scope, different sudoers entries
ci-deploy ALL=(root) NOPASSWD: /bin/systemctl reload nginx
ci-deploy ALL=(root) NOPASSWD: /bin/systemctl status nginx
EOF

    # [IMP:10][setup-node][sudoers] CRITICAL: validate before atomic replace — lockout-safe
    if ! visudo -c -f "$tmp_sudoers" &>/dev/null 2>&1; then
        local visudo_err
        visudo_err="$(visudo -c -f "$tmp_sudoers" 2>&1 || true)"
        log_step "sudoers" "FAIL" "visudo -c FAILED: ${visudo_err} — original sudoers NOT touched"
        rm -f "$tmp_sudoers"
        exit 1
    fi

    mv "$tmp_sudoers" "$sudoers_file"
    log_step "sudoers" "DONE" "sudoers generated and validated: ${sudoers_file}"
}
# endregion GENERATE_SUDOERS

main() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[IMP:10][setup-node][main] ERROR: must run as root" >&2
        exit 1
    fi

    local node_name="${NODE_NAME:-$(hostname)}"
    local owner_key="${PLATFORM_OWNER_KEY:-}"
    local ci_deploy_key="${PLATFORM_CI_DEPLOY_KEY:-}"

    # Step ④: Create 'platform' user
    create_user "platform" "/bin/bash" "docker"
    if [[ -n "$owner_key" ]]; then
        add_ssh_key "platform" "$owner_key"
    fi

    # Step ⑤: Create 'ci-deploy' user with restricted command key
    # [IMP:9][setup-node][ci-deploy] authorized_keys MUST use full path + restrict — no shell access (00 §4)
    # add_ci_deploy_command: forced command, docker+adm groups, separate sudoers from ci role (06 §4.2)
    if [[ -n "$ci_deploy_key" ]]; then
        add_ci_deploy_command "$node_name" "$ci_deploy_key"
    else
        log_step "ci-deploy" "WARN" "PLATFORM_CI_DEPLOY_KEY not set — ci-deploy SSH key setup skipped"
    fi

    # Step ⑬: Generate sudoers safely
    generate_sudoers "$node_name"

    log_step "main" "DONE" "Node setup complete: users=platform,ci-deploy sudoers=${node_name}"
}

main "$@"
