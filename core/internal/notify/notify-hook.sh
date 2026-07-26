#!/usr/bin/env bash
# GREP_SUMMARY: notify-hook telegram-notifier secrets deploy-events audit-log AR5-no-fallback
# STRUCTURE: load_secrets → validate_token_chat_id → curl_telegram(emoji+message) → audit_log → exit 0
# region MODULE_CONTRACT
## @purpose  Notification hook for deploy events — sends Telegram messages via direct curl.
##           Called by deploy-project.sh with optional --severity flag and pre-formatted 🚀 message.
## @scope    Moved to core/internal/notify/notify-hook.sh from core/scripts/notify-hook.sh.
## @invariants
##   - Always exits 0 — must never block the calling deploy pipeline
##   - Secrets loaded from /run/platform/secrets.env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
##   - Missing secrets → log warning, exit 0 (non-blocking)
##   - Network/API failure → log error, exit 0 (non-blocking)
##   - Messages prefixed with context from SECRETS env or "platform"
## @rationale Non-blocking by design — notification failure must not abort deploy.
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][notify-hook][main] Starting notification hook" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../internal/audit/audit.sh" 2>/dev/null || true
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")}"
__LOG_PREFIX="notify-hook"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

SEVERITY=""
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --severity) SEVERITY="$2"; shift 2 ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
set -- "${ARGS[@]}"

EMOJI="${1:-✅}"
MESSAGE="${2:-}"
SECRETS_FILE="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
CONTEXT="${PLATFORM_CONTEXT:-platform}"
TELEGRAM_PROXY_URL="${TELEGRAM_PROXY_URL:-http://127.0.0.1:8118}"
CURL_TIMEOUT=30

if [[ -z "$MESSAGE" ]]; then
    FULL_MESSAGE="${EMOJI}"
else
    FULL_MESSAGE="[${CONTEXT}] ${EMOJI} ${MESSAGE}"
fi

# region LOAD_SECRETS
load_secrets() {
    if [[ ! -f "$SECRETS_FILE" ]]; then
        log_imp 9 "secrets" "Secrets file not found: ${SECRETS_FILE} — notification skipped"
        return 1
    fi

    set +u
    # shellcheck disable=SC1090
    source "$SECRETS_FILE" 2>/dev/null || true
    set -u

    if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
        log_imp 9 "secrets" "TELEGRAM_BOT_TOKEN not set — notification skipped"
        return 1
    fi

    case "${SEVERITY}" in
        critical) CHAT_ID="${TELEGRAM_CHAT_ID_CRITICAL:-${TELEGRAM_CHAT_ID:-}}" ;;
        warning)  CHAT_ID="${TELEGRAM_CHAT_ID_WARNING:-${TELEGRAM_CHAT_ID:-}}" ;;
        info|*)   CHAT_ID="${TELEGRAM_CHAT_ID:-}" ;;
    esac

    if [[ -z "${CHAT_ID:-}" ]]; then
        log_imp 9 "secrets" "No TELEGRAM_CHAT_ID resolved (severity=${SEVERITY:-none})"
        return 1
    fi

    return 0
}
# endregion LOAD_SECRETS

# region SEND_TELEGRAM
send_telegram() {
    local text="$1"

    local encoded
    encoded="$(python3 "$SCRIPT_DIR/url_encoder.py" encode "${text}" 2>/dev/null || echo "${text}")"

    local response http_code
    response="$(curl -s -w "\n%{http_code}" -X POST \
        --proxy "$TELEGRAM_PROXY_URL" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}" \
        -d "text=${encoded}" \
        -d "parse_mode=HTML" \
        --max-time "$CURL_TIMEOUT" 2>/dev/null)" || {
        log_imp 9 "telegram" "ERROR: curl to Telegram API failed (network)"
        return 1
    }

    http_code="$(echo "$response" | tail -n1)"
    if [[ "$http_code" != "200" ]]; then
        log_imp 9 "telegram" "ERROR: Telegram API returned HTTP ${http_code}"
        return 1
    fi

    log_imp 8 "telegram" "Notification sent: ${text:0:80}..."
    return 0
}
# endregion SEND_TELEGRAM

echo "[IMP:8][notify-hook][main] Sending: ${FULL_MESSAGE:0:80}..." >&2
log_imp 8 "-" "${FULL_MESSAGE}"
audit_log "notify-hook" "INFO" "${FULL_MESSAGE}" 2>/dev/null || true

if load_secrets; then
    send_telegram "${FULL_MESSAGE}" || true
fi

exit 0
