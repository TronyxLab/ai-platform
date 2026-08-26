#!/usr/bin/env bash
# GREP_SUMMARY: notify-hook thin-facade telegram-notifier telegram_notifier shared-module secrets deploy-events audit-log AR5-no-fallback
# STRUCTURE: parse --severity → audit_log (best-effort) → exec python3 -m core.internal.shared.telegram_notifier notify → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E10): вся логика (severity→CHAT_ID mapping, formatting,
##           secrets sourcing, non-blocking send) — в shared/telegram_notifier notify().
##           Called with optional --severity flag and pre-formatted 🚀 message.
## @scope    Moved to core/internal/notify/notify-hook.sh from core/scripts/notify-hook.sh.
## @invariants
##   - Always exits 0 — must never block the calling deploy pipeline
##   - Secrets loaded from /var/lib/platform/run/secrets.env by Python notify() (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID*) (142 W2)
##   - Missing secrets → log warning, exit 0 (non-blocking)
##   - Messages prefixed with context (default "platform")
##   - <25 LOC thin facade — языковая политика: бизнес-логика в Python
## @rationale Non-blocking by design — notification failure must not abort deploy.
## @changes  2026-07-30 | T14a — Replaced curl POST with shared telegram_notifier module (send_telegram)
##           2026-08-02 | DevPlan 118 E10 — сокращён до фасада (severity-mapping merged в Python, было 108 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][notify-hook][main] Starting notification hook" >&2
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${_SCRIPT_DIR}/../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$_SCRIPT_DIR")")")")}"
source "${PLATFORM_ROOT}/core/lib/audit.sh" 2>/dev/null || true

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
CONTEXT="${PLATFORM_CONTEXT:-platform}"
# 142 W2 (B21): persistent /var/lib/platform/run (tmpfs /run/platform не переживает reboot)
# AI-0024 (DevPlan 17 T4.1): PLATFORM_RUN_BASE relocates run-артефакты единообразно
SECRETS_FILE="${SECRETS_ENV_FILE:-${PLATFORM_RUN_BASE:-/var/lib/platform/run}/secrets.env}"

FULL_MESSAGE="${EMOJI}"
[[ -n "$MESSAGE" ]] && FULL_MESSAGE="[${CONTEXT}] ${EMOJI} ${MESSAGE}"

echo "[IMP:8][notify-hook][main] Sending: ${FULL_MESSAGE:0:80}..." >&2
audit_log "notify-hook" "INFO" "${FULL_MESSAGE}" 2>/dev/null || true

exec python3 -m core.internal.shared.telegram_notifier notify \
    --severity "${SEVERITY}" --context "${CONTEXT}" --secrets-file "${SECRETS_FILE}" \
    "${EMOJI}" "${MESSAGE}"
