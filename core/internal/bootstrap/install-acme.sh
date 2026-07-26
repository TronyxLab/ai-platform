#!/usr/bin/env bash
# GREP_SUMMARY: install-acme, acme.sh, clone, dnsapi, dns-extensions, git, letsencrypt, idempotent
# STRUCTURE: ▶ ┌ACME_HOME env┐ → ◇ binary exists? → SKIP → ⚡ git clone acme.sh + dnsapi_ext → ⎋ return 0|1
# region MODULE_CONTRACT
## @purpose  Install acme.sh and DNS API extensions for DNS-01 certificate issuance.
##           Standalone script — called once at bootstrap/init, not at each update.
##           Extracted from original monolithic ssl-provision script (T3, DevPlan 005).
## @scope    Called from node-lifecycle.sh --mode init (before node-update step).
##           Clones acme.sh to /opt/acme.sh (configurable via ACME_HOME env).
##           Clones regtime-ltd/dnsapi extensions for Russian registrars (webnames, reg.ru).
## @invariants
##   - Idempotent: if acme.sh binary exists at ACME_HOME → SKIP
##   - dnsapi extension clone is non-fatal (WARN if fails)
##   - Proxy vars are expected to be clean at this stage (unset_platform_proxy already ran)
## @rationale acme.sh is the only ACME client with DNS-01 support for Russian registrars.
##            Git clone — not apt — ensures latest version with dnsapi plugin support.
##            Split from original monolithic ssl-provision script per D3: install-acme needed once at bootstrap,
##            issue-cert.sh needed at each update. Decoupling reduces update latency.
## @changes  CREATED: 2026-07-17 · T3 — Extracted from original monolithic ssl-provision script (DevPlan 005)
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][install-acme][main] Starting acme.sh installation" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/paths.sh"
__LOG_PREFIX="install-acme"
source "${SCRIPT_DIR}/../../lib/logging.sh"

# ═══════════════════════════════════════════════════════════════════
# NOTE: This script is extracted from core/internal/bootstrap/original monolithic ssl-provision script.
# The install_acme() function was the first logical part of original monolithic ssl-provision script.
# The second part (certificate issuance) lives in issue-cert.sh.
# All original TRAP[BUG], TRAP[BUSINESS], TRAP[DECISION] comments are preserved.
# ═══════════════════════════════════════════════════════════════════

# region INSTALL_ACME
## @purpose  Install acme.sh and DNS API extensions for DNS-01 certificate issuance
## @scope    Clones acme.sh to /opt/acme.sh (configurable via ACME_HOME env).
##           Clones regtime-ltd/dnsapi extensions for Russian registrars (webnames, reg.ru).
## @invariants
##   - Idempotent: if acme.sh binary exists at ACME_HOME → SKIP
##   - dnsapi extension clone is non-fatal (WARN if fails)
##   - Proxy vars are expected to be clean at this stage (unset_platform_proxy already ran)
## @rationale acme.sh is the only ACME client with DNS-01 support for Russian registrars.
##   Git clone — not apt — ensures latest version with dnsapi plugin support.
install_acme() {
    local acme_home="${ACME_HOME:-/opt/acme.sh}"
    local acme_sh="${acme_home}/acme.sh"

    if [[ -x "$acme_sh" ]]; then
        log_step "acme" "SKIP" "acme.sh already installed at ${acme_home}"
        echo "[IMP:9][install-acme][acme] SKIP — already installed at ${acme_home}" >&2
        return 0
    fi

    log_step "acme" "START" "Installing acme.sh to ${acme_home}"
    apt-get install -y -qq git 2>/dev/null || true

    # Proxy vars not needed here: unset_platform_proxy() in bootstrap.sh ran before any
    # module install step, so HTTP_PROXY/HTTPS_PROXY are already clean on the host level.
    if ! git clone --depth 1 https://github.com/acmesh-official/acme.sh.git "$acme_home" 2>&1; then
        log_step "acme" "FAIL" "Failed to clone acme.sh repository"
        return 1
    fi

    # Clone dnsapi extensions for Russian registrars (webnames, reg.ru, etc.)
    local dnsapi_ext="${acme_home}/dnsapi_ext"
    if [[ ! -d "$dnsapi_ext" ]]; then
        git clone --depth 1 https://github.com/regtime-ltd/dnsapi.git "$dnsapi_ext" 2>/dev/null || \
            log_step "acme" "WARN" "Failed to clone regtime-ltd/dnsapi — webnames TLS will not work"
    fi

    log_step "acme" "DONE" "acme.sh installed at ${acme_home}"
    echo "[IMP:9][install-acme][acme] Installation complete: ${acme_home}" >&2
}
# endregion INSTALL_ACME

install_acme "$@"
