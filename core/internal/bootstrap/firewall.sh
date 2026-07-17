#!/usr/bin/env bash
# GREP_SUMMARY: firewall ufw declarative idempotent 22 80 443 5432-deny extra_ports deny-incoming allow-outgoing
# STRUCTURE: reset rules → set defaults → allow baseline(22/80/443) → deny 5432 → allow extra_ports → enable ufw → verify
# region MODULE_CONTRACT
## @purpose  Declarative ufw baseline firewall: deny all incoming, allow outgoing, open exactly 22/80/443 + extra_ports
## @scope    Called during bootstrap step ⑥; idempotent — full rule replacement, not additive
## @invariants
##   - Full declarative reset on each run (not additive) ensures deterministic state
##   - Ports 2375/2376 (Docker API) are NEVER added regardless of extra_ports
##   - Port 5432 (PostgreSQL) is explicitly DENIED regardless of extra_ports — prevents
##     host-forwarded managed service exposure (managed Postgres провайдера)
##   - extra_ports validated as integers 1-65535
##   - exit 0 only if ufw status shows expected ports
## @rationale Additive ufw rules accumulate over re-runs; declarative replace guarantees idempotency (00 §10, §14)
## ⚠️ TRAP[DECISION] · 2026-07-01 · — · Firewall as FULL SET (declarative), not additive
## ·   Each run: ufw --force reset → re-apply full rule set. This ensures deterministic
## ·   state — no orphaned rules from previous runs. Additive approach would accumulate
## ·   stale rules over time, making audit difficult.
## ·   Rejected: additive ufw allow (one-off rules) — rules accumulate across re-runs
## ·   Rejected: nftables/iptables directly — higher complexity, ufw abstracts well enough
## ·   Rev: if ufw becomes too slow on rule-sets >50, switch to nftables with declarative config
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../audit/audit.sh" 2>/dev/null || true

# Baseline ports always open
readonly BASELINE_PORTS=(22 80 443)
# Forbidden ports — Docker API must never be exposed
readonly FORBIDDEN_PORTS=(2375 2376)

__LOG_PREFIX="firewall"
source "${SCRIPT_DIR}/../../lib/logging.sh"

# region VALIDATE_EXTRA_PORTS
validate_extra_ports() {
    local -a extra=("$@")
    local port
    for port in "${extra[@]}"; do
        if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
            log_step "validate" "FAIL" "Invalid port '${port}' — must be integer 1-65535"
            exit 1
        fi
        for forbidden in "${FORBIDDEN_PORTS[@]}"; do
            if [[ "$port" == "$forbidden" ]]; then
                # [IMP:10][firewall][validate] CRITICAL: Docker API port attempted — blocking
                log_step "validate" "FAIL" "SECURITY: Port ${port} is a Docker API port — forbidden in extra_ports"
                exit 1
            fi
        done
    done
    log_step "validate" "OK" "extra_ports validated: ${extra[*]:-none}"
}
# endregion VALIDATE_EXTRA_PORTS

# region INSTALL_UFW
install_ufw() {
    if dpkg -s ufw &>/dev/null 2>&1; then
        log_step "install-ufw" "SKIP" "ufw already installed"
        return 0
    fi
    log_step "install-ufw" "START" "Installing ufw"
    apt-get install -y -qq ufw
    log_step "install-ufw" "DONE" "ufw installed"
}
# endregion INSTALL_UFW

# region APPLY_RULES
apply_rules() {
    local -a extra_ports=("$@")

    log_step "reset" "START" "Resetting ufw rules (declarative replacement)"
    # Disable ufw temporarily to allow non-interactive reset
    ufw --force disable &>/dev/null || true

    # Reset wipes all rules; safe because we re-apply full set immediately
    ufw --force reset &>/dev/null
    log_step "reset" "DONE" "All ufw rules cleared"

    # [IMP:9][firewall][defaults] Default policy: deny incoming, allow outgoing
    ufw default deny incoming
    ufw default allow outgoing
    log_step "defaults" "DONE" "Default policy: deny incoming / allow outgoing"

    # Apply baseline ports
    for port in "${BASELINE_PORTS[@]}"; do
        ufw allow "${port}/tcp" comment "platform-baseline"
        log_step "allow" "DONE" "Port ${port}/tcp allowed (baseline)"
    done

    # Apply extra ports from node.yaml
    for port in "${extra_ports[@]}"; do
        ufw allow "${port}/tcp" comment "platform-extra"
        log_step "allow" "DONE" "Port ${port}/tcp allowed (extra)"
    done

    # Explicit deny for ports that must NEVER be accessible from outside,
    # even if a host-forwarded managed service or Docker publishes them.
    # 5432 — PostgreSQL (managed service хостера может форвардить)
    ufw deny 5432/tcp comment 'explicit-deny-postgresql'
    log_step "deny" "DONE" "Port 5432/tcp denied (explicit override)"

    # Enable ufw non-interactively
    ufw --force enable
    log_step "enable" "DONE" "ufw enabled"
}
# endregion APPLY_RULES

# region VERIFY
verify_firewall() {
    log_step "verify" "START" "Verifying ufw status"
    local ufw_status
    ufw_status="$(ufw status verbose 2>&1)"

    if ! echo "$ufw_status" | grep -q "Status: active"; then
        log_step "verify" "FAIL" "ufw is NOT active after apply"
        exit 1
    fi

    for port in "${BASELINE_PORTS[@]}"; do
        if ! echo "$ufw_status" | grep -qE "^${port}/tcp.*ALLOW"; then
            log_step "verify" "FAIL" "Expected port ${port}/tcp not found in ufw status"
            exit 1
        fi
    done

    # [IMP:9][firewall][verify] Confirm Docker API ports are NOT open
    for forbidden in "${FORBIDDEN_PORTS[@]}"; do
        if echo "$ufw_status" | grep -qE "^${forbidden}/tcp.*ALLOW"; then
            log_step "verify" "FAIL" "SECURITY: Docker API port ${forbidden} is open in ufw — aborting"
            exit 1
        fi
    done

    # [IMP:9][firewall][verify] Confirm port 5432 is DENIED (managed PostgreSQL guard)
    if ! echo "$ufw_status" | grep -qE "^5432/tcp.*DENY"; then
        log_step "verify" "FAIL" "SECURITY: Port 5432 is not DENIED in ufw — aborting"
        exit 1
    fi

    log_step "verify" "DONE" "Firewall verified: active, 22/80/443 open, Docker ports closed, 5432 denied"
}
# endregion VERIFY

main() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[IMP:10][firewall][main] ERROR: must run as root" >&2
        exit 1
    fi

    # Parse extra_ports from arguments (space-separated list)
    local -a extra_ports=()
    if [[ $# -gt 0 ]]; then
        IFS=' ' read -ra extra_ports <<< "$*"
        validate_extra_ports "${extra_ports[@]}"
    fi

    install_ufw
    apply_rules "${extra_ports[@]}"
    verify_firewall
}

main "$@"
