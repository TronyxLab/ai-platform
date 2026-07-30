#!/usr/bin/env bash
# GREP_SUMMARY: tor-proxy-healthcheck telegram telegram_notifier shared-module proxy monitoring audit-log cron
# STRUCTURE: check_tor_socks → check_privoxy_forward → check_telegram_api → log_result → exit
# region MODULE_CONTRACT
## @purpose  Healthcheck for Tor→Privoxy→Telegram proxy chain; logs to audit log, exits 0 on success
## @scope    Runs via cron */5 * * * * from /etc/cron.d/tor-proxy-healthcheck; standalone use supported
## @invariants
##   - Each check exits 1 immediately on failure with descriptive message
##   - Stage 3 (Telegram getMe) requires decrypted secrets at SECRETS_ENV_FILE
##   - TELEGRAM_PROXY_URL defaults to http://127.0.0.1:8118 if unset
##   - Non-fatal per check: first failure causes immediate exit 1
## @rationale Automated monitoring ensures Tor+Privoxy chain stays operational;
##   without healthcheck, IP-blocking of api.telegram.org would go undetected
##   until users report missing notifications.
## 🧐 TRAP[DECISION] · 2026-07-08 · — · Tor proxy DI dependency for Telegram
## ·   Hermes Telegram adapter requires Tor+Privoxy on port 8118 (HTTP_PROXY).
## ·   Without Tor: Telegram API blocked in Russia → ConnectError in adapter → bot dead.
## ·   Install: apt install tor privoxy (debian) or brew install tor privoxy (macOS).
## ·   Config: HTTP_PROXY=http://127.0.0.1:8118 in .env.
## ·   This healthcheck verifies the full chain: Tor SOCKS5 → Privoxy HTTP → Telegram API.
## ·   Rev: if alternative proxy (SOCKS5, VPN) is used, update PROXY_URL var.
## @changes  2026-07-30 | T14b — Replaced curl getMe with shared telegram_notifier module (get_me)
# endregion MODULE_CONTRACT

set -euo pipefail

PROXY_URL="${TELEGRAM_PROXY_URL:-http://127.0.0.1:8118}"
SECRETS_FILE="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
MAX_TIME=30

# Source audit.sh for audit_log if available
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=core/internal/audit/audit.sh
source "${SCRIPT_DIR}/../audit/audit.sh" 2>/dev/null || true
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")}"
__LOG_PREFIX="tor-proxy"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

log_result() {
    local check="$1" status="$2" msg="$3"
    log_imp 8 "${check}" "${status}: ${msg}"
    audit_log "tor-healthcheck:${check}" "${status}" "${msg}" 2>/dev/null || true
}

# region CHECK_TOR_SOCKS
# [IMP:9][tor-hc][tor-socks] Verify Tor SOCKS5 proxy by hitting check.torproject.org
check_tor_socks() {
    log_result "tor-socks" "START" "Checking Tor SOCKS5 at 127.0.0.1:9050"
    if curl --socks5-hostname 127.0.0.1:9050 -s --max-time "$MAX_TIME" -o /dev/null \
        -w "%{http_code}" "https://check.torproject.org/" 2>/dev/null | grep -q "200"; then
        log_result "tor-socks" "DONE" "Tor SOCKS5: connected"
    else
        log_result "tor-socks" "FAIL" "Tor SOCKS5: connection failed"
        exit 1
    fi
}
# endregion CHECK_TOR_SOCKS

# region CHECK_PRIVOXY
# [IMP:9][tor-hc][privoxy] Verify Privoxy HTTP proxy forwards to Tor → check.torproject.org
check_privoxy() {
    log_result "privoxy" "START" "Checking Privoxy forward at ${PROXY_URL}"
    if curl --proxy "$PROXY_URL" -s --max-time "$MAX_TIME" -o /dev/null \
        -w "%{http_code}" "https://check.torproject.org/" 2>/dev/null | grep -q "200"; then
        log_result "privoxy" "DONE" "Privoxy → Tor forward: working"
    else
        log_result "privoxy" "FAIL" "Privoxy → Tor forward: failed"
        exit 1
    fi
}
# endregion CHECK_PRIVOXY

# region CHECK_TELEGRAM_API
# [IMP:9][tor-hc][telegram-api] Verify Telegram Bot API getMe through proxy chain via shared module
check_telegram_api() {
    log_result "telegram-api" "START" "Checking Telegram getMe through proxy"

    if [[ ! -f "$SECRETS_FILE" ]]; then
        log_result "telegram-api" "SKIP" "Secrets file not found at ${SECRETS_FILE}"
        return 0
    fi

    # shellcheck disable=SC1090
    source "$SECRETS_FILE" 2>/dev/null || {
        log_result "telegram-api" "SKIP" "Failed to source secrets — skipping Telegram check"
        return 0
    }

    if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
        log_result "telegram-api" "SKIP" "TELEGRAM_BOT_TOKEN not set in secrets"
        return 0
    fi

    # Delegate to shared telegram_notifier.get_me() — uses urllib, no curl dependency
    if ! TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
         python3 -c "
import sys, os
sys.path.insert(0, '${PLATFORM_ROOT}/core/internal/shared')
from telegram_notifier import get_me
proxy = os.environ.get('PROXY_URL', '${PROXY_URL}')
sys.exit(0 if get_me(proxy_url=proxy) else 1)
" 2>/dev/null; then
        log_result "telegram-api" "FAIL" "Telegram getMe request failed"
        exit 1
    fi

    log_result "telegram-api" "DONE" "Telegram API reachable through proxy"
}
# endregion CHECK_TELEGRAM_API

# region MAIN
main() {
    log_imp 9 "main" "=============================="
    log_imp 9 "main" "Tor Proxy Healthcheck START"
    log_imp 9 "main" "Proxy: ${PROXY_URL}"
    log_imp 9 "main" "=============================="

    check_tor_socks
    check_privoxy
    check_telegram_api

    log_imp 9 "main" "All healthchecks PASSED — exit 0"
    exit 0
}
# endregion MAIN

main "$@"
