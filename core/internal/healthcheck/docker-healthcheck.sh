#!/usr/bin/env bash
# GREP_SUMMARY: docker-daemon-healthcheck docker-info failure-count restart alert telegram
# STRUCTURE: ▶ docker info → ◇ success? → ⎋ exit 0 | ⊕ increment failure counter → ◇ 3 failures? → systemctl restart docker + alert → ⎋ exit 1

set -euo pipefail

# region MODULE_CONTRACT
## @purpose  Docker daemon healthcheck — monitors docker info, auto-restarts on 3 consecutive failures, sends Telegram alert
## @scope    Runs every minute via cron on the host; requires root for systemctl restart; idempotent
## @invariants
##   - Failure count persists in /var/lib/platform/docker-healthcheck-failures
##   - Counter resets to 0 on any successful docker info
##   - Counter resets to 0 after restarting Docker (prevents restart loops)
##   - Telegram alert requires TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS in /run/platform/secrets.env
## @rationale Self-healing daemon pattern — Docker 26+ supports live-restore + systemd watchdog;
##   healthcheck prevents silent daemon death (e.g. OOM, deadlock) from going unnoticed
# endregion MODULE_CONTRACT

FAILURE_FILE="/var/lib/platform/docker-healthcheck-failures"
MAX_FAILURES=3

# region CHECK_DOCKER_INFO
# [IMP:8][docker-healthcheck][check] Running docker info to verify daemon health
if docker info > /dev/null 2>&1; then
    # [IMP:9][docker-healthcheck][check] Docker daemon healthy — reset failure counter
    echo "0" > "$FAILURE_FILE"
    exit 0
fi
# endregion CHECK_DOCKER_INFO

# region HANDLE_FAILURE
# Read current failure count
count=$(cat "$FAILURE_FILE" 2>/dev/null || echo "0")
count=$((count + 1))
echo "$count" > "$FAILURE_FILE"

echo "[IMP:8][docker-healthcheck][failure] Consecutive failure ${count}/${MAX_FAILURES}"

if [ "$count" -ge "$MAX_FAILURES" ]; then
    echo "[IMP:9][docker-healthcheck][restart] ${count} consecutive failures — restarting docker"
    systemctl restart docker
    # [IMP:9][docker-healthcheck][alert] Sending Telegram alert about daemon restart
    secrets_env="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
    if [ -f "$secrets_env" ]; then
        source "$secrets_env"
        if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_ALLOWED_USERS:-}" ]; then
            CHAT_ID=$(echo "$TELEGRAM_ALLOWED_USERS" | cut -d',' -f1)
            curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
                -d "chat_id=${CHAT_ID}" \
                -d "text=⚠️ Docker daemon restarted after ${count} consecutive healthcheck failures on $(hostname)" \
                --max-time 10 || true
        fi
    fi
    echo "0" > "$FAILURE_FILE"
fi
# endregion HANDLE_FAILURE

exit 1
