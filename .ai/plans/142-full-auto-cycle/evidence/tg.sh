#!/usr/bin/env bash
# GREP_SUMMARY: telegram helper notify dedup milestone night-session-141
# STRUCTURE: source tg.sh → tg_send "text" [severity] → load env (python, no shell source) → dedup 30min → notify-hook.sh → log → ⎋ 0
# Anti-spam: same text (dedup key = text|severity) not re-sent within 1800s; FAIL/CRITICAL always sent.
# Log: evidence/telegram-sent.log (ts, severity, text). Never prints secrets.
EVIDENCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Локальная отправка: вариант без TELEGRAM_PROXY_URL (tor-прокси — серверный канал, на dev-машине не запущен)
SECRETS_ENV_FILE="${SECRETS_ENV_FILE:-/var/folders/14/vtgwv6lj4g70fldm667f33lc0000gn/T/kilo/141-secrets/secrets.local.env}"
TG_LOG="${EVIDENCE_DIR}/telegram-sent.log"
TG_STATE="${EVIDENCE_DIR}/telegram-dedup.state"

_tg_env_loader() {
  python3 - "$SECRETS_ENV_FILE" <<'PYEOF'
import os, sys
p = sys.argv[1]
try:
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ[k] = v.strip().strip('"').strip("'")
except FileNotFoundError:
    pass
PYEOF
}

# tg_send "text" [severity=info] — sends via notify-hook.sh (non-blocking, exit 0 always)
tg_send() {
  local text="$1" severity="${2:-info}"
  local now ts key last_ts
  now=$(date +%s)
  key="${text}|${severity}"
  if [[ -f "$TG_STATE" ]]; then
    last_ts=$(grep -F "$key" "$TG_STATE" 2>/dev/null | tail -1 | cut -f1)
    if [[ -n "$last_ts" && $(( now - last_ts )) -lt 1800 && "$severity" != "critical" && "$severity" != "fail" ]]; then
      return 0  # dedup: same message within 30 min
    fi
  fi
  _tg_env_loader
  local out rc
  out=$(core/internal/notify/notify-hook.sh notify "$text" 2>&1)
  rc=$?
  printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$severity" "$text" >> "$TG_LOG"
  printf '%s\t%s\n' "$now" "$key" >> "$TG_STATE"
  echo "[tg][$severity] rc=$rc: $text" >&2
  return 0
}
