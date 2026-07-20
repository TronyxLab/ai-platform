#!/usr/bin/env bash
# GREP_SUMMARY: docker-daemon-healthcheck docker-info failure-count restart alert telegram docker-health-json status-page
# STRUCTURE: ▶ docker info → ◇ success? → ⊕ export docker-health.json → ⎋ exit 0 | ⊕ increment failure counter → ◇ 3 failures? → systemctl restart docker + alert → ⎋ exit 1

set -euo pipefail

# region MODULE_CONTRACT
## @purpose  Docker daemon healthcheck — monitors docker info, auto-restarts on 3 consecutive failures, sends Telegram alert.
##           Also exports /run/platform/docker-health.json for status-page consumption (container status snapshot).
## @scope    Runs every minute via cron on the host; requires root for systemctl restart; idempotent
## @invariants
##   - Failure count persists in /var/lib/platform/docker-healthcheck-failures
##   - Counter resets to 0 on any successful docker info
##   - Counter resets to 0 after restarting Docker (prevents restart loops)
##   - Telegram alert requires TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS in /run/platform/secrets.env
##   - docker-health.json written to /run/platform/ (tmpfs) on every successful run — consumed by status-page module
## @rationale Self-healing daemon pattern — Docker 26+ supports live-restore + systemd watchdog;
##   healthcheck prevents silent daemon death (e.g. OOM, deadlock) from going unnoticed.
##   docker-health.json export enables status-page to read container statuses without docker socket access.
# endregion MODULE_CONTRACT

FAILURE_FILE="/var/lib/platform/docker-healthcheck-failures"
MAX_FAILURES=3
HEALTH_JSON="${HEALTH_JSON_FILE:-/run/platform/docker-health.json}"

# region EXPORT_DOCKER_HEALTH_JSON
# [IMP:8][docker-healthcheck][export] Exporting container health statuses to ${HEALTH_JSON}
_export_docker_health_json() {
    mkdir -p "$(dirname "$HEALTH_JSON")" 2>/dev/null || true
    local generated_at
    generated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    # Build JSON array of container statuses
    # Using docker ps --format to get container_name and status, then assemble JSON
    local containers_json=""
    containers_json=$(docker ps --all --format '{{json .}}' 2>/dev/null | python3 -c "
import sys, json
containers = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        c = json.loads(line)
    except json.JSONDecodeError:
        continue
    # Determine running/healthy/exit_code from docker status string
    status = c.get('Status', '')
    running = 'Up' in status
    healthy = '(healthy)' in status
    exit_code = int(c.get('ExitCode', 0)) if not running else 0
    containers.append({
        'container_name': c.get('Names', ''),
        'running': running,
        'healthy': healthy,
        'exit_code': exit_code,
        'status_line': status
    })
result = {'generated_at': '${generated_at}', 'containers': containers}
print(json.dumps(result, indent=2))
" 2>/dev/null)
    if [[ -n "$containers_json" ]]; then
        echo "$containers_json" > "$HEALTH_JSON"
        echo "[IMP:7][docker-healthcheck][export] Container health status written to ${HEALTH_JSON} ($(echo "$containers_json" | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("containers",[])))') containers)"
    else
        # Fallback: minimal valid JSON
        echo "{\"generated_at\":\"${generated_at}\",\"containers\":[]}" > "$HEALTH_JSON"
        echo "[IMP:8][docker-healthcheck][export] No containers found — wrote empty health JSON"
    fi
}
# endregion EXPORT_DOCKER_HEALTH_JSON

# region CHECK_DOCKER_INFO
# [IMP:8][docker-healthcheck][check] Running docker info to verify daemon health
if docker info > /dev/null 2>&1; then
    # [IMP:9][docker-healthcheck][check] Docker daemon healthy — reset failure counter
    echo "0" > "$FAILURE_FILE"
    _export_docker_health_json
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
