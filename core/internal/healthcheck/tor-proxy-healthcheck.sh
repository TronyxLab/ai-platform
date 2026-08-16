#!/usr/bin/env bash
# GREP_SUMMARY: tor-proxy-healthcheck thin-facade telegram telegram_notifier shared-module proxy monitoring audit-log cron
# STRUCTURE: resolve env → audit_log start (best-effort) → exec python3 -m core.internal.healthcheck.tor_proxy_check → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E5): 3-stage healthcheck (Tor SOCKS5, Privoxy forward, Telegram getMe)
##           — в core/internal/healthcheck/tor_proxy_check.py.
## @scope    Runs via cron */5 * * * * from /etc/cron.d/tor-proxy-healthcheck; standalone use supported
## @invariants
##   - <10 LOC thin facade — языковая политика: бизнес-логика в Python
##   - SECRETS_ENV_FILE / TELEGRAM_PROXY_URL передаются через env (как раньше)
## @rationale Strangler E5: 3-stage проверка + канон-таймауты → Python
## @changes  2026-08-02 | DevPlan 118 E5 — сокращён до фасада (было 121 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")}"
source "${PLATFORM_ROOT}/core/lib/audit.sh" 2>/dev/null || true
__LOG_PREFIX="tor-proxy"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

log_imp 9 "main" "=============================="
log_imp 9 "main" "Tor Proxy Healthcheck START"
log_imp 9 "main" "Proxy: ${TELEGRAM_PROXY_URL:-http://127.0.0.1:8118}"
log_imp 9 "main" "=============================="
audit_log "tor-healthcheck:main" "START" "Tor proxy healthcheck" 2>/dev/null || true

exec python3 -m core.internal.healthcheck.tor_proxy_check
