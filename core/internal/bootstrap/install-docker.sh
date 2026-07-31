#!/usr/bin/env bash
# GREP_SUMMARY: install-docker idempotent docker compose-plugin apt no-ports
# STRUCTURE: guard(docker installed?) → skip | install apt-deps → add repo → apt install docker-ce → verify
# region MODULE_CONTRACT
## @purpose  Idempotent installation of Docker Engine + Compose plugin from official apt repo; enables live-restore
## @scope    Called once during bootstrap step ③; safe to re-run on already-provisioned nodes
## @invariants
##   - docker --version check prevents re-installation
##   - Docker daemon ports (2375/2376) are NEVER opened by this script (00 §10)
##   - /etc/docker/daemon.json enforces live-restore=true for zero-downtime daemon restarts
## @rationale Docker manages its own iptables chains; we must not open docker ports in ufw
## ⚠️ TRAP[DECISION] · 2026-07-01 · — · Docker ports NOT exposed externally
## ·   Docker daemon ports (2375/2376) are NEVER opened by this script (00 §10).
## ·   /etc/docker/daemon.json sets iptables=true but does NOT publish host ports.
## ·   Container port exposure is managed per-compose, never at daemon level.
## ·   Rejected: expose Docker API on TCP for remote management
## ·   Reason: security — Docker API without TLS is trivially exploitable; with TLS,
## ·     adds complexity without benefit (SSH is the management channel).
## ·   Rev: if fleet management requires Docker API, add TLS cert + firewall restrict
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../audit/audit.sh" 2>/dev/null || true

__LOG_PREFIX="install-docker"
source "${SCRIPT_DIR}/../../lib/logging.sh"

# region GUARD_ALREADY_INSTALLED
guard_already_installed() {
    # [IMP:9][install-docker][guard] Check if Docker is already installed
    if command -v docker &>/dev/null && docker --version &>/dev/null; then
        log_step "guard" "SKIP" "Docker already installed: $(docker --version)"
        return 0
    fi
    if dpkg -s docker-ce &>/dev/null 2>&1; then
        log_step "guard" "SKIP" "docker-ce package already present via dpkg"
        return 0
    fi
    return 1
}
# endregion GUARD_ALREADY_INSTALLED

# region INSTALL_APT_DEPS
install_apt_deps() {
    log_step "apt-deps" "START" "Installing prerequisite packages"
    local deps=(ca-certificates curl gnupg lsb-release)
    local to_install=()
    for pkg in "${deps[@]}"; do
        if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
            to_install+=("$pkg")
        fi
    done
    if [[ ${#to_install[@]} -gt 0 ]]; then
        apt-get update -qq
        apt-get install -y -qq "${to_install[@]}"
        log_step "apt-deps" "DONE" "Installed: ${to_install[*]}"
    else
        log_step "apt-deps" "SKIP" "All prerequisite packages already present"
    fi
}
# endregion INSTALL_APT_DEPS

# region ADD_DOCKER_REPO
add_docker_repo() {
    log_step "docker-repo" "START" "Adding Docker official apt repository"
    local keyring="/etc/apt/keyrings/docker.gpg"
    local list_file="/etc/apt/sources.list.d/docker.list"

    if [[ -f "$keyring" ]] && [[ -f "$list_file" ]]; then
        log_step "docker-repo" "SKIP" "Docker apt repo already configured"
        return 0
    fi

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o "$keyring"
    chmod a+r "$keyring"

    local arch
    arch="$(dpkg --print-architecture)"
    local codename
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"

    echo \
        "deb [arch=${arch} signed-by=${keyring}] https://download.docker.com/linux/ubuntu ${codename} stable" \
        > "$list_file"

    apt-get update -qq
    log_step "docker-repo" "DONE" "Docker apt repo configured for ${codename}/${arch}"
}
# endregion ADD_DOCKER_REPO

# region INSTALL_DOCKER_PACKAGES
install_docker_packages() {
    log_step "docker-install" "START" "Installing Docker CE + Compose plugin"
    apt-get install -y -qq \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
    log_step "docker-install" "DONE" "Docker installed: $(docker --version)"
}
# endregion INSTALL_DOCKER_PACKAGES

# region CONFIGURE_DAEMON
configure_daemon() {
    log_step "daemon-config" "START" "Writing /etc/docker/daemon.json with live-restore"
    local daemon_json="/etc/docker/daemon.json"

    if [[ -f "$daemon_json" ]]; then
        log_step "daemon-config" "MERGE" "daemon.json exists — merging live-restore: true"
        # Strangler 2026-07-31: inline python3 -c (с TRAP[BUG]: f.write после закрытия файла)
        # → docker_daemon.py merge-live-restore (atomic write, дефект устранён)
        python3 "${SCRIPT_DIR}/docker_daemon.py" merge-live-restore "$daemon_json" || {
            log_fail "Failed to merge live-restore into ${daemon_json}"
            return 1
        }
        log_step "daemon-config" "DONE" "live-restore: true merged into existing daemon.json"
        # [IMP:8][install-docker][daemon] Restart Docker daemon to apply live-restore config
        systemctl restart docker
        return 0
    fi

    # [IMP:10][install-docker][daemon] CRITICAL: no host-ports binding, iptables managed by Docker, live-restore enabled
    cat > "$daemon_json" <<'EOF'
{
  "iptables": true,
  "ip-forward": true,
  "live-restore": true,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "5"
  }
}
EOF
    log_step "daemon-config" "DONE" "daemon.json written — live-restore: true enabled"
}
# endregion CONFIGURE_DAEMON

# region CONFIGURE_SYSTEMD_OVERRIDE
configure_systemd_override() {
    log_step "systemd-override" "START" "Creating systemd override for docker.service — Restart=always, RestartSec=10s"
    local override_dir="/etc/systemd/system/docker.service.d"
    local override_file="${override_dir}/restart.conf"

    mkdir -p "$override_dir"

    if [[ -f "$override_file" ]]; then
        log_step "systemd-override" "SKIP" "Override already exists at ${override_file}"
        return 0
    fi

    # [IMP:10][install-docker][systemd] CRITICAL: Docker daemon auto-restart with 10s delay
    cat > "$override_file" <<'OVERRIDE'
[Service]
Restart=always
RestartSec=10s
OVERRIDE

    systemctl daemon-reload
    log_step "systemd-override" "DONE" "Systemd override written — Restart=always, RestartSec=10s"
}
# endregion CONFIGURE_SYSTEMD_OVERRIDE

# region ENABLE_SERVICE
enable_service() {
    log_step "service" "START" "Enabling and starting Docker service"
    systemctl enable docker --quiet
    systemctl start docker
    log_step "service" "DONE" "Docker service active"
}
# endregion ENABLE_SERVICE

# region VERIFY
verify_installation() {
    log_step "verify" "START" "Verifying Docker installation"
    if ! docker --version &>/dev/null; then
        log_step "verify" "FAIL" "docker --version failed after installation"
        exit 1
    fi
    if ! docker compose version &>/dev/null; then
        log_step "verify" "FAIL" "docker compose version failed — Compose plugin missing"
        exit 1
    fi
    # [IMP:9][install-docker][verify] Docker ports MUST NOT be open on host
    if ss -tlnp 2>/dev/null | grep -qE ':2375|:2376'; then
        log_step "verify" "FAIL" "SECURITY: Docker API ports 2375/2376 are exposed — aborting"
        exit 1
    fi
    log_step "verify" "DONE" "Docker $(docker --version) + Compose $(docker compose version) — ports secure"
}
# endregion VERIFY

main() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[IMP:10][install-docker][main] ERROR: must run as root" >&2
        exit 1
    fi

    if guard_already_installed; then
        verify_installation
        exit 0
    fi

    install_apt_deps
    add_docker_repo
    install_docker_packages
    configure_daemon
    configure_systemd_override
    enable_service
    verify_installation
}

main "$@"
