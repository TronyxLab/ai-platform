#!/usr/bin/env bash
# GREP_SUMMARY: disk-monitor usage-threshold alert telegram_notifier shared-module prune docker-system docker-volume loki-retention
# STRUCTURE: ▶ df /var/lib/platform → ◇ usage > 80%? → Telegram alert (shared module) → ◇ docker system prune (weekly) → ◇ docker volume prune (weekly) → ◇ loki data size check → ⎋ exit 0

set -euo pipefail

# region MODULE_CONTRACT
## @purpose  Disk space monitor — alerts on usage >80%, runs docker prune weekly, checks Loki data size
## @scope    Runs hourly via cron on the host; requires docker CLI + docker compose access; idempotent
## @invariants
##   - Threshold is 80% of /var/lib/platform disk usage
##   - Docker prune runs only on Sunday (day 7)
##   - Telegram alert requires TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS in /run/platform/secrets.env
##   - Loki dir check is best-effort (dir may not exist)
## @rationale Proactive disk management prevents platform outages due to full disk;
##   weekly prune prevents image bloat without destroying recent layers (48h filter)
## @changes  2026-07-30 | T14c — Replaced inline curl Telegram alert with shared telegram_notifier module
# endregion MODULE_CONTRACT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="$(cd "${SCRIPT_DIR}/../../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")")"

THRESHOLD=80
PLATFORM_DIR="/var/lib/platform"
LOKI_DIR="${PLATFORM_DIR}/loki-data"

# region CHECK_DISK_USAGE
# Check disk usage
usage=$(df -h "$PLATFORM_DIR" | tail -1 | awk '{print $5}' | sed 's/%//')
echo "[IMP:7][disk-monitor] /var/lib/platform usage: ${usage}%"

if [ "$usage" -gt "$THRESHOLD" ]; then
    echo "[IMP:9][disk-monitor] WARNING: disk usage ${usage}% exceeds ${THRESHOLD}% threshold"
    # [IMP:9][disk-monitor][alert] Telegram alert on threshold exceeded
    secrets_env="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
    if [ -f "$secrets_env" ]; then
        source "$secrets_env"
        if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_ALLOWED_USERS:-}" ]; then
            CHAT_ID=$(echo "$TELEGRAM_ALLOWED_USERS" | cut -d',' -f1)
            # Delegate to shared telegram_notifier module — urllib stdlib, no curl dependency
            TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
            TELEGRAM_CHAT_ID="${CHAT_ID}" \
            python3 -c "
import sys, os
sys.path.insert(0, '${PLATFORM_ROOT}/core/internal/shared')
from telegram_notifier import send_telegram
send_telegram(sys.argv[1])
sys.exit(0)
" "⚠️ Disk usage ${usage}% on $(hostname) — threshold ${THRESHOLD}%" 2>/dev/null || true
        fi
    fi
fi
# endregion CHECK_DISK_USAGE

# region DOCKER_PRUNE
# [IMP:7][disk-monitor][prune] Docker prune runs only on Sunday
if [ "$(date +%u)" = "7" ]; then
    echo "[IMP:7][disk-monitor] Running docker system prune..."
    # [IMP:8][disk-monitor][prune] docker system prune -af — removes unused images, containers, networks
    docker system prune -af --filter "until=48h" 2>&1 || true
    # [IMP:8][disk-monitor][prune] docker volume prune — removes unused volumes
    docker volume prune -f 2>&1 || true
fi
# endregion DOCKER_PRUNE

# region LOKI_CHECK
# [IMP:7][disk-monitor][loki] Check Loki data directory size
if [ -d "$LOKI_DIR" ]; then
    loki_size=$(du -sh "$LOKI_DIR" 2>/dev/null | cut -f1)
    echo "[IMP:7][disk-monitor] Loki data size: ${loki_size}"
else
    echo "[IMP:7][disk-monitor] Loki data dir not found: ${LOKI_DIR}"
fi
# endregion LOKI_CHECK
