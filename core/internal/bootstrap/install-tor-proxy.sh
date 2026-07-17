#!/usr/bin/env bash
# GREP_SUMMARY: install-tor-proxy idempotent tor privoxy obfs4proxy bootstrap systemd
# STRUCTURE: check_packages → write_torrc(bridges) → write_privoxy_config → enable_start_services → verify_tor_circuit → exit
# region MODULE_CONTRACT
## @purpose  Idempotent installation of Tor + Privoxy chain for Telegram Bot API proxy
## @scope    Called from bootstrap.sh step_install_tor_proxy; safe to re-run on provisioned node
## @invariants
##   - Idempotent: repeated runs do not corrupt configuration
##   - Base torrc written first; bridges appended from file if --tor-bridges-file provided
##   - Privoxy config lines are inserted only if absent (grep guard)
##   - Tor circuit verification via check.torproject.org with up to 60s retry
##   - Verification failure (optional via --skip-tor-verify) returns 1, non-fatal to bootstrap
## @rationale Tor+Privoxy chain is the only reliable way to bypass IP-blocking of api.telegram.org
##   from Russia-hosted VPS (Selectel). Privoxy provides HTTP_PROXY interface that works with
##   curl, Python httpx/requests, and Docker containers without protocol-specific configuration.
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../audit/audit.sh" 2>/dev/null || true

BRIDGES_FILE=""
SKIP_VERIFY=false
TOR_CONFIG="/etc/tor/torrc"
PRIVOXY_CONFIG="/etc/privoxy/config"

__LOG_PREFIX="tor-proxy"
source "${SCRIPT_DIR}/../../lib/logging.sh"

# region CLI_ARGS
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --tor-bridges-file)
                BRIDGES_FILE="$2"; shift 2 ;;
            --skip-tor-verify)
                SKIP_VERIFY=true; shift ;;
            *)
                echo "[IMP:10][tor-install][args] ERROR: Unknown argument: $1" >&2
                exit 1 ;;
        esac
    done
}
# endregion CLI_ARGS

# region INSTALL_PACKAGES
install_packages() {
    local missing=()
    for pkg in tor privoxy obfs4proxy; do
        if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
            missing+=("$pkg")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_step "packages" "START" "Installing packages: ${missing[*]}"
        apt-get update -qq
        local apt_err; apt_err="$(apt-get install -y -qq "${missing[@]}" 2>&1)" || {
            log_step "packages" "FAIL" "apt-get install failed: ${apt_err}"
            exit 1
        }
        log_step "packages" "DONE" "Installed: ${missing[*]}"
    else
        log_step "packages" "SKIP" "All packages already installed"
    fi
}
# endregion INSTALL_PACKAGES

# region WRITE_TORRC
write_torrc() {
    log_step "torrc" "START" "Writing ${TOR_CONFIG}"

    # Write base torrc from template
    local torrc_template="${SCRIPT_DIR}/../../bootstrap/tor/torrc.template"
    if [[ -f "$torrc_template" ]]; then
        cp "$torrc_template" "$TOR_CONFIG"
        log_step "torrc" "INFO" "Base config from template: ${torrc_template}"
    else
        # Fallback inline config if template missing
        cat > "$TOR_CONFIG" <<'BASE'
SOCKSPort 127.0.0.1:9050
Log notice file /var/log/tor/notices.log
DataDirectory /var/lib/tor
BASE
        log_step "torrc" "WARN" "Template not found — wrote inline base config"
    fi

    # Append bridges if file provided
    if [[ -n "$BRIDGES_FILE" ]] && [[ -f "$BRIDGES_FILE" ]]; then
        cat >> "$TOR_CONFIG" <<'BRIDGE_HEADER'

UseBridges 1
ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy
BRIDGE_HEADER
        cat "$BRIDGES_FILE" >> "$TOR_CONFIG"
        log_step "torrc" "INFO" "Bridges appended from ${BRIDGES_FILE}"
    else
        log_step "torrc" "INFO" "No bridges file — Tor will connect directly"
    fi

    log_step "torrc" "DONE" "${TOR_CONFIG} written"
}
# endregion WRITE_TORRC

# region WRITE_PRIVOXY_CONFIG
write_privoxy_config() {
    log_step "privoxy-config" "START" "Configuring ${PRIVOXY_CONFIG}"

    # Try template first
    local privoxy_template="${SCRIPT_DIR}/../../bootstrap/tor/privoxy-config.template"
    if [[ -f "$privoxy_template" ]]; then
        cp "$privoxy_template" "$PRIVOXY_CONFIG"
        log_step "privoxy-config" "INFO" "Config from template: ${privoxy_template}"
    else
        # ⚠️ TRAP[BUGFIX] · 2026-06-24 · HI · listen-address 0.0.0.0:8118 + permit-access
        # · Причина: Docker-контейнеры не могут достучаться до Privoxy на 127.0.0.1:8118
        # · Fix: 0.0.0.0:8118 (все интерфейсы) + permit-access для Docker bridge и localhost
        # Ensure listen-address is set
        if ! grep -q '^listen-address' "$PRIVOXY_CONFIG" 2>/dev/null; then
            echo 'listen-address 0.0.0.0:8118' >> "$PRIVOXY_CONFIG"
            log_step "privoxy-config" "INFO" "Added listen-address 0.0.0.0:8118"
        else
            # Upgrade existing 127.0.0.1
            sed -i.bak 's/^listen-address 127.0.0.1:8118/listen-address 0.0.0.0:8118/' "$PRIVOXY_CONFIG" && rm "${PRIVOXY_CONFIG}.bak"
            log_step "privoxy-config" "INFO" "Upgraded listen-address to 0.0.0.0:8118"
        fi
        # Add permit-access for localhost (idempotent)
        if ! grep -q '^permit-access 127.0.0.1' "$PRIVOXY_CONFIG" 2>/dev/null; then
            sed -i.bak '/^forward-socks5t/i permit-access 127.0.0.1' "$PRIVOXY_CONFIG" && rm "${PRIVOXY_CONFIG}.bak"
            log_step "privoxy-config" "INFO" "Added permit-access 127.0.0.1"
        fi
        # Add permit-access for Docker bridge networks (172.16.0.0/12)
        if ! grep -q '^permit-access 172.16.0.0/12' "$PRIVOXY_CONFIG" 2>/dev/null; then
            sed -i.bak '/^forward-socks5t/i permit-access 172.16.0.0/12' "$PRIVOXY_CONFIG" && rm "${PRIVOXY_CONFIG}.bak"
            log_step "privoxy-config" "INFO" "Added permit-access 172.16.0.0/12 (Docker bridges)"
        fi
        # Add Tor forward (idempotent: check first)
        if ! grep -q 'forward-socks5t / 127.0.0.1:9050' "$PRIVOXY_CONFIG" 2>/dev/null; then
            echo 'forward-socks5t / 127.0.0.1:9050 .' >> "$PRIVOXY_CONFIG"
            log_step "privoxy-config" "INFO" "Added forward-socks5t to Tor"
        fi
    fi

    log_step "privoxy-config" "DONE" "${PRIVOXY_CONFIG} ready"
}
# endregion WRITE_PRIVOXY_CONFIG

# region ENABLE_SERVICES
enable_services() {
    log_step "services" "START" "Enabling and starting services"

    systemctl enable tor --quiet 2>/dev/null || true
    systemctl enable privoxy --quiet 2>/dev/null || true

    log_step "services" "INFO" "Restarting Tor..."
    systemctl restart tor
    # [IMP:8][tor-install][services] Give Tor time to bootstrap its directory info
    # before Privoxy tries to forward through it. 3s minimum for local Tor start.
    sleep 3

    log_step "services" "INFO" "Restarting Privoxy..."
    systemctl restart privoxy

    log_step "services" "DONE" "Both services restarted"
}
# endregion ENABLE_SERVICES

# region VERIFY_SERVICES_ACTIVE
verify_services_active() {
    log_step "verify-active" "START" "Checking Tor and Privoxy are active"
    local fail=0

    if systemctl is-active --quiet tor 2>/dev/null; then
        log_step "verify-active" "OK" "Tor: active"
    else
        log_step "verify-active" "FAIL" "Tor: NOT active"
        fail=1
    fi

    if systemctl is-active --quiet privoxy 2>/dev/null; then
        log_step "verify-active" "OK" "Privoxy: active"
    else
        log_step "verify-active" "FAIL" "Privoxy: NOT active"
        fail=1
    fi

    if [[ "$fail" -eq 1 ]]; then
        return 1
    fi
    log_step "verify-active" "DONE" "Both services active"
}
# endregion VERIFY_SERVICES_ACTIVE

# region VERIFY_TOR_CIRCUIT
verify_tor_circuit() {
    if [[ "$SKIP_VERIFY" == true ]]; then
        log_step "verify-tor" "SKIP" "Tor verification skipped (--skip-tor-verify)"
        return 0
    fi

    log_step "verify-tor" "START" "Waiting for Tor circuit (up to 60s)"

    # [IMP:9][tor-install][verify-tor] Check via SOCKS5 against check.torproject.org
    # Retry loop: Tor may need time to bootstrap directory info and build circuit.
    local attempt=0 max_attempts=12 sleep_sec=5
    while [[ $attempt -lt $max_attempts ]]; do
        if curl --socks5-hostname 127.0.0.1:9050 -s --max-time 10 \
            https://check.torproject.org/ 2>/dev/null | grep -q "Congratulations"; then
            log_step "verify-tor" "DONE" "Tor circuit established after $((attempt + 1))x${sleep_sec}s"
            return 0
        fi
        attempt=$(( attempt + 1 ))
        if [[ $attempt -lt $max_attempts ]]; then
            sleep "$sleep_sec"
        fi
    done

    log_step "verify-tor" "FAIL" "Tor failed to establish circuit within 60s"
    return 1
}
# endregion VERIFY_TOR_CIRCUIT

# region CRON_HEALTHCHECK
install_cron_healthcheck() {
    local cron_file="/etc/cron.d/tor-proxy-healthcheck"
    local CORE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
    local hc_script="${CORE_DIR}/internal/healthcheck/tor-proxy-healthcheck.sh"

    if [[ ! -f "$hc_script" ]]; then
        log_step "cron-hc" "SKIP" "Healthcheck script not found at ${hc_script} — cron not installed"
        return 0
    fi

    if [[ -f "$cron_file" ]]; then
        log_step "cron-hc" "SKIP" "Cron healthcheck already installed"
        return 0
    fi

    # ⚠️ TRAP[BUG] heredoc без кавычек — переменная CORE_DIR раскрывается
    #   Раньше было:  'CRON'  и  ${PLATFORM_ROOT}/core/  — не работало после rsync в /opt/core/
    #   PLATFORM_ROOT from core/lib/paths.sh (SoT)
    cat > "$cron_file" <<CRON
*/5 * * * * root ${CORE_DIR}/internal/healthcheck/tor-proxy-healthcheck.sh
CRON
    chmod 0644 "$cron_file"
    log_step "cron-hc" "DONE" "Healthcheck cron installed: ${cron_file}"
}
# endregion CRON_HEALTHCHECK

# region CONFIGURE_FIREWALL
## @purpose  Adds iptables/UFW rules to allow Docker containers to reach Privoxy:8118
## @scope    Single catch-all rule for all Docker bridge networks (172.16.0.0/12) +
##           idempotent iptables rule; UFW skipped (catch-all via iptables)
## @invariants
##   - Idempotent: skips existing rules (iptables -C guard)
##   - Uses 172.16.0.0/12 (RFC 1918) to cover ALL Docker bridge subnets (172.16-31.x.x)
##   - ✅ TRAP[INCIDENT] · 2026-06-27 · P0 · Per-interface rules missed new Docker networks · RESOLVED 2026-07-07 — iptables catch-all on line 285
##     Root: Docker networks created AFTER install-tor-proxy.sh (e.g., hermes-agent_default 172.23.0.0/16)
##     were not covered by per-interface iptables rules added at install time
##     Fix: Single catch-all rule for 172.16.0.0/12 → all Docker bridges permanently covered
## @rationale  Q: why 172.16.0.0/12 vs per-interface? A: Docker creates networks dynamically;
##             per-interface rules miss new networks. 172.16.0.0/12 covers all RFC 1918
##             Docker default bridge subnets in one rule. Privoxy permit-access provides
##             the second layer of access control.
configure_firewall_docker() {
    log_step "firewall" "START" "Configuring firewall for Docker bridge → Privoxy:8118 (catch-all 172.16.0.0/12)"

    local comment="hermes-proxy-docker-bridges"
    local src_net="172.16.0.0/12"
    local added=0

    # ---- iptables: single catch-all rule for all Docker bridge networks ----
    # Idempotency check: -C fails if rule doesn't exist → we add it
    if ! iptables -C INPUT -p tcp --dport 8118 -s "$src_net" -j ACCEPT -m comment --comment "$comment" 2>/dev/null; then
        iptables -I INPUT -p tcp --dport 8118 -s "$src_net" -j ACCEPT -m comment --comment "$comment"
        log_step "firewall" "INFO" "iptables: allowed all Docker bridges ($src_net → :8118)"
        added=1
    else
        log_step "firewall" "INFO" "iptables rule already exists for $src_net → :8118"
    fi

    # 🧐 TRAP[DECISION] · 2026-06-27 · — · UFW rules skipped: iptables catch-all covers all Docker bridges
    # · Rejected: per-interface UFW rules · Reason: UFW per-interface rules don't cover networks
    #   created after bootstrap; iptables catch-all + Privoxy permit-access is sufficient
    # · Rev: if UFW is the only firewall (no iptables fallback), add ufw route allow

    if [[ "$added" -eq 1 ]]; then
        log_step "firewall" "DONE" "Firewall: Docker bridges → Privoxy:8118 allowed (172.16.0.0/12)"
    else
        log_step "firewall" "INFO" "Firewall rule already present"
    fi
}
# endregion CONFIGURE_FIREWALL

# region MAIN
main() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[IMP:10][tor-install][main] ERROR: must run as root" >&2
        exit 1
    fi

    echo "[IMP:9][tor-install][main] ====================================" >&2
    echo "[IMP:9][tor-install][main] Tor + Privoxy Installer START" >&2
    echo "[IMP:9][tor-install][main] ====================================" >&2

    install_packages
    write_torrc
    write_privoxy_config
    enable_services
    verify_services_active
    configure_firewall_docker

    install_cron_healthcheck

    if verify_tor_circuit; then
        echo "[IMP:9][tor-install][main] Tor + Privoxy installation complete — circuit verified" >&2
        exit 0
    else
        echo "[IMP:10][tor-install][main] CRITICAL: Tor circuit failed to establish" >&2
        echo "[IMP:10][tor-install][main] Telegram notifications will be unavailable until bridges are configured" >&2
        exit 1
    fi
}
# endregion MAIN

main "$@"
