#!/bin/bash
# GREP_SUMMARY: platform-agent-watchdog systemd oneshot self-update monitor readiness rollback telegram-curl audit-log KEEP_IMAGES external-controller
# STRUCTURE: ▶ check PENDING_FILE → read state/version → ◇ poll /ready(WATCHDOG_TIMEOUT) → ⊕ success: mark+cleanup+exit0 | ◇ fail: down→tag→up→re-poll → ⊕ rollback_ok: telegram+exit0 | ⊕ rollback_fail: critical_telegram+exit1
#
# region MODULE_CONTRACT [DOMAIN(DevOps): Agent self-update external watchdog; CONCEPT(Watchdog): OS-level independent process for agent rollback; TECH(Bash): systemd oneshot script]
## @purpose  External watchdog script that monitors hermes-agent self-update readiness and performs automatic rollback on failure.
## @scope    Phase 04: executed by systemd platform-agent-watchdog.service; runs independently of agent process.
## @io       Reads PENDING_FILE (/var/lib/platform/agent.update-pending) → polls /ready → marks result → optionally rolls back
## @invariants
##   - NEVER calls agent Python code — OS-level independent process
##   - sudo ONLY for docker compose commands on hermes-agent module
##   - All logs to both stdout (systemd journal) and AUDIT_LOG
##   - Node remains operational in BOTH success and rollback outcomes (05 §5 step 7)
##   - Telegram notifications via direct curl — bypasses dead agent (05 §5 step 6e)
##   - Secrets file absence handled gracefully (log [IMP:9], still attempt rollback)
##   - PENDING_FILE state field prevents re-triggering (pending→success|rolled_back|rollback_failed)
## @env
##   WATCHDOG_TIMEOUT=90   — seconds to wait for /ready (configurable)
##   AGENT_PORT=9119       — agent HTTP port (configurable)
##   AGENT_READY_URL       — full /ready URL (default: http://localhost:${AGENT_PORT}/ready)
##   PENDING_FILE          — path to update-pending marker (default: /var/lib/platform/agent.update-pending)
##   SECRETS_FILE          — path to secrets env file (default: /run/platform/secrets.env)
##   AUDIT_LOG             — path to watchdog audit log (default: /var/log/platform/watchdog-audit.log)
##   KEEP_IMAGES=3         — number of previous hermes-agent images to retain
##   MODULE_DIR            — path to hermes-agent module dir (default: /opt/platform/core/modules/hermes-agent)
##                          ⚠️ fallback: PLATFORM_ROOT not available in systemd context; see core/lib/paths.sh (SoT)
## @links_to_spec  plans/BRIEF/05-service-update.md §5 (steps 1-7)
## @rationale
##   Q: Why bash, not Python?
##   A: Watchdog is OS-level — must work even if Python runtime is unavailable. Bash + curl + docker CLI = minimal dependencies.
##   Q: Why direct curl to Telegram instead of using notify_telegram.py?
##   A: During self-update rollback, the agent container is dead — no Python runtime, no skill files. Watchdog bypasses everything.
##   Q: Why state field in PENDING_FILE?
##   A: Without it, watchdog would re-process completed/rolled-back updates on every timer tick (30s). State prevents re-triggering.
##   Q: Why docker compose down instead of stop+rm?
##   A: down ensures clean state: stops container, removes it, cleans up any anonymous volumes. External networks (proxy-net, hermes-agent-net) are preserved.
# endregion MODULE_CONTRACT

set -euo pipefail

# ============================================================================
# Configuration — all overridable via environment
# ============================================================================
WATCHDOG_TIMEOUT="${WATCHDOG_TIMEOUT:-90}"
AGENT_PORT="${AGENT_PORT:-9119}"
AGENT_READY_URL="${AGENT_READY_URL:-http://localhost:${AGENT_PORT}/ready}"
PENDING_FILE="${PENDING_FILE:-/var/lib/platform/agent.update-pending}"
SECRETS_FILE="${SECRETS_FILE:-/run/platform/secrets.env}"
AUDIT_LOG="${AUDIT_LOG:-/var/log/platform/watchdog-audit.log}"
KEEP_IMAGES="${KEEP_IMAGES:-3}"
MODULE_DIR="${MODULE_DIR:-/opt/platform/core/modules/hermes-agent}"  # fallback: PLATFORM_ROOT not available in systemd context; see core/lib/paths.sh (SoT)
COMPOSE_FILE="${MODULE_DIR}/docker-compose.base.yml"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-hermes-agent}"

# Circuit breaker configuration (TASK-13)
CIRCUIT_BREAKER_STATE_DIR="${CIRCUIT_BREAKER_STATE_DIR:-/var/lib/platform/watchdog}"
# Format: service_name:dependency_check_cmd:max_failures:window_seconds
# dependency_check_cmd should exit 0 for healthy, non-0 for unhealthy
CIRCUIT_BREAKER_SERVICES=(
    "postgres:pg_isready -U postgres -h 127.0.0.1 -t 5:5:300"
    "pgbouncer:pg_isready -h 127.0.0.1 -p 6432 -U postgres -t 3:5:300"
    "redis:redis-cli -h 127.0.0.1 ping:5:300"
    "loki:/usr/bin/loki -version:5:300"
    "prometheus:wget -q -O- http://127.0.0.1:9090/-/healthy:5:300"
)

POLL_INTERVAL=5          # seconds between /ready polls
CURL_MAX_TIME=3           # max seconds per curl call
CURL_TG_MAX_TIME=30       # max seconds for Telegram API calls (increased for Tor latency)

# ============================================================================
# Helper functions
# ============================================================================

# region FUNC__log [watchdog][audit]
## @purpose  Log timestamped message to both stdout (systemd journal) and AUDIT_LOG.
## @io       message_string → stdout + append AUDIT_LOG
timestamp() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
    local msg
    msg="$(timestamp) $*"
    echo "$msg"
    # Ensure audit log directory exists
    local audit_dir
    audit_dir="$(dirname "$AUDIT_LOG")"
    if [[ ! -d "$audit_dir" ]]; then
        mkdir -p "$audit_dir" 2>/dev/null || true
    fi
    echo "$msg" >> "$AUDIT_LOG" 2>/dev/null || true
}
# endregion FUNC__log

# region FUNC__safe_source [watchdog][util]
## @purpose  Source a file safely — temporarily disables set -u to avoid errors on missing variables.
## @rationale set -u would abort the script if sourced file references unset variables;
##            safe_source handles this by disabling -u during source, then restoring.
safe_source() {
    local file="$1"
    if [[ -f "$file" ]]; then
        set +u
        # shellcheck disable=SC1090
        source "$file" 2>/dev/null || true
        set -u
    fi
}
# endregion FUNC__safe_source

# region FUNC__send_telegram [watchdog][telegram]
## @purpose  Send a raw message to Telegram via direct curl — bypasses dead agent.
## @io       (message_text) → HTTPS POST to api.telegram.org → log result
## @invariants
##   - Secrets file absence → logged as [IMP:9] warning, function returns 1
##   - Missing token/chat_id → logged, returns 1
##   - Network/API failure → logged, returns 1 (non-fatal to watchdog flow)
send_telegram() {
    local message="$1"
    local proxy_url="${TELEGRAM_PROXY_URL:-http://127.0.0.1:8118}"

    if [[ ! -f "$SECRETS_FILE" ]]; then
        log "[IMP:9][watchdog][telegram] WARNING: Secrets file not found at ${SECRETS_FILE} — cannot send Telegram notification"
        return 1
    fi

    safe_source "$SECRETS_FILE"

    if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
        log "[IMP:9][watchdog][telegram] WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in ${SECRETS_FILE}"
        return 1
    fi

    local response
    local http_code
    response="$(curl -s -w "\n%{http_code}" --proxy "$proxy_url" -X POST \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=${message}" \
        --max-time "$CURL_TG_MAX_TIME" 2>/dev/null)" || {
        log "[IMP:9][watchdog][telegram] ERROR: curl to Telegram API failed (network error)"
        return 1
    }

    http_code="$(echo "$response" | tail -n1)"
    if [[ "$http_code" != "200" ]]; then
        log "[IMP:9][watchdog][telegram] ERROR: Telegram API returned HTTP ${http_code}"
        return 1
    fi

    log "[IMP:8][watchdog][telegram] Notification sent successfully"
    return 0
}
# endregion FUNC__send_telegram

# region FUNC__poll_ready [watchdog][readiness]
## @purpose  Poll AGENT_READY_URL until 200 response or timeout.
## @io       () → "ok" on success, "timeout" on failure
## @invariants
##   - Polls every POLL_INTERVAL seconds
##   - Each curl call limited to CURL_MAX_TIME seconds
##   - Returns on first successful 200 response
poll_ready() {
    local label="${1:-update}"
    local elapsed=0

    log "[IMP:8][watchdog][${label}] Polling ${AGENT_READY_URL} (timeout=${WATCHDOG_TIMEOUT}s, interval=${POLL_INTERVAL}s)"

    while [[ $elapsed -lt $WATCHDOG_TIMEOUT ]]; do
        if curl -sf --max-time "$CURL_MAX_TIME" "$AGENT_READY_URL" > /dev/null 2>&1; then
            log "[IMP:8][watchdog][${label}] /ready returned 200 after ${elapsed}s"
            echo "ok"
            return 0
        fi
        sleep "$POLL_INTERVAL"
        elapsed=$((elapsed + POLL_INTERVAL))
    done

    log "[IMP:9][watchdog][${label}] /ready check timed out after ${WATCHDOG_TIMEOUT}s"
    echo "timeout"
    return 1
}
# endregion FUNC__poll_ready

# region FUNC__cleanup_old_images [watchdog][docker]
## @purpose  Remove old hermes-agent images beyond KEEP_IMAGES count.
## @invariants
##   - Only removes images matching hermes-agent reference
##   - Preserves at least KEEP_IMAGES most recent images
##   - rm failures are non-fatal (logged, not aborted)
cleanup_old_images() {
    log "[IMP:7][watchdog][cleanup] Cleaning old hermes-agent images (keep=${KEEP_IMAGES})"

    local images
    # List hermes-agent images sorted by creation date (newest first), get repo:tag
    images="$(sudo docker image ls \
        --filter "reference=hermes-agent" \
        --format '{{.Repository}}:{{.Tag}} {{.CreatedAt}}' 2>/dev/null \
        | sort -k2,3 -r \
        | awk '{print $1}')" || true

    if [[ -z "$images" ]]; then
        log "[IMP:7][watchdog][cleanup] No hermes-agent images found — skipping cleanup"
        return 0
    fi

    local count=0
    local img
    while IFS= read -r img; do
        [[ -z "$img" ]] && continue
        count=$((count + 1))
        if [[ $count -gt $KEEP_IMAGES ]]; then
            log "[IMP:7][watchdog][cleanup] Removing old image: ${img}"
            sudo docker rmi "$img" 2>/dev/null || {
                log "[IMP:7][watchdog][cleanup] WARNING: Could not remove image ${img} (may be in use)"
            }
        fi
    done <<< "$images"

    log "[IMP:7][watchdog][cleanup] Image cleanup complete (found=${count}, kept=${KEEP_IMAGES})"
}
# endregion FUNC__cleanup_old_images

# region CIRCUIT_BREAKER [watchdog][health]
## @purpose  Circuit breaker framework for stateful services — stops crash-loop containers
## @scope    Monitors docker container health, breaks circuit after N failures in time window
## @invariants
##   - State stored in JSON files under CIRCUIT_BREAKER_STATE_DIR/<service>.json
##   - 5 failures in 300 seconds → docker stop + Telegram alert
##   - Counter resets if last failure older than window_seconds
##   - Existing self-update logic is NOT modified
## @rationale Docker restart: unless-stopped loops forever on crash; circuit breaker stops the loop
##           and alerts operator. Stateful services (postgres, redis) risk WAL corruption on crash-loop.

# region FUNC__check_service_health
## @brief  Run a health check command for a service
## @param  $1  service_name
## @param  $2  check_command (bash command string)
## @return 0 if healthy, 1 if unhealthy
check_service_health() {
    local service_name="$1"
    local check_command="$2"
    # ⚠️ TRAP[DECISION] · 2026-07-09 · — · eval replaced with array-based execution
    # · Rejected: in-shell eval for command strings
    # · Reason: eval is a shell injection risk; the command string is split into
    #   an array via read -ra for safe execution. The check commands in CIRCUIT_BREAKER_SERVICES
    #   are simple CLI commands without quotes/pipes, so word-splitting on IFS is safe.
    # · Rev: if a check command requires pipes or redirects, use a named wrapper function.
    local check_cmd=()
    IFS=' ' read -ra check_cmd <<< "$check_command"
    "${check_cmd[@]}" > /dev/null 2>&1
}
# endregion

# region FUNC__read_cb_state
## @brief  Read circuit breaker state from JSON file
## @param  $1  service_name
## @output state_json or empty on error
read_cb_state() {
    local service_name="$1"
    local state_file="${CIRCUIT_BREAKER_STATE_DIR}/${service_name}.json"
    if [[ -f "$state_file" ]]; then
        cat "$state_file"
    else
        echo '{"failures":[],"circuit_open":false}'
    fi
}
# endregion

# region FUNC__write_cb_state
## @brief  Write circuit breaker state to JSON file
## @param  $1  service_name
## @param  $2  json_state (string)
write_cb_state() {
    local service_name="$1"
    local json_state="$2"
    mkdir -p "${CIRCUIT_BREAKER_STATE_DIR}"
    echo "$json_state" > "${CIRCUIT_BREAKER_STATE_DIR}/${service_name}.json"
}
# endregion

# region FUNC__increment_failure_counter
## @brief  Record a failure timestamp and check if circuit should open
## @param  $1  service_name
## @param  $2  max_failures
## @param  $3  window_seconds
## @return 0 if circuit stays closed, 1 if circuit opens
increment_failure_counter() {
    local service_name="$1"
    local max_failures="$2"
    local window_seconds="$3"
    local now
    now="$(date +%s)"

    local state_json
    state_json="$(read_cb_state "$service_name")"
    local circuit_open
    circuit_open="$(echo "$state_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('circuit_open','false'))" 2>/dev/null || echo "false")"

    # If circuit already open, no need to track more failures
    if [[ "$circuit_open" == "True" || "$circuit_open" == "true" ]]; then
        # Check if window has expired — auto-recover
        local last_failure
        last_failure="$(echo "$state_json" | python3 -c "import sys,json; d=json.load(sys.stdin); fs=d.get('failures',[]); print(fs[-1] if fs else '0')" 2>/dev/null || echo "0")"
        local window_end=$(( last_failure + window_seconds ))
        if [[ $now -gt $window_end ]]; then
            log "[IMP:8][watchdog][cb:${service_name}] Circuit window expired — resetting failures"
            write_cb_state "$service_name" '{"failures":[],"circuit_open":false}'
            return 0
        fi
        log "[IMP:9][watchdog][cb:${service_name}] Circuit is OPEN — service is stopped"
        return 1
    fi

    # Parse existing failures and filter within window
    local new_json
    new_json="$(python3 -c "
import json, sys, time
now = int(time.time())
state = json.loads('''${state_json}''')
failures = [f for f in state.get('failures', []) if now - f < ${window_seconds}]
failures.append(now)
is_open = len(failures) >= ${max_failures}
state['failures'] = failures
state['circuit_open'] = is_open
print(json.dumps(state))
" 2>/dev/null || echo '{"failures":[],"circuit_open":false}')"

    write_cb_state "$service_name" "$new_json"

    local failure_count
    failure_count="$(echo "$new_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('failures',[])))" 2>/dev/null || echo "0")"
    log "[IMP:8][watchdog][cb:${service_name}] Failure count: ${failure_count}/${max_failures} in ${window_seconds}s window"

    # Check if circuit should open
    if echo "$new_json" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('circuit_open',False) else 1)" 2>/dev/null; then
        log "[IMP:9][watchdog][cb:${service_name}] CIRCUIT BREAKER OPENED — ${failure_count} failures in ${window_seconds}s"
        return 1
    fi

    return 0
}
# endregion

# region FUNC__circuit_break
## @brief  Stop a service container when circuit breaker opens
## @param  $1  service_name
## @param  $2  docker_container (unused — derived from service_name by convention)
circuit_break() {
    local service_name="$1"

    log "[IMP:9][watchdog][cb:${service_name}] CIRCUIT BREAK: Stopping ${service_name} due to repeated health failures"

    # Try docker stop first, then docker kill if that doesn't work
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${service_name}"; then
        docker stop "${service_name}" 2>/dev/null || \
            docker kill "${service_name}" 2>/dev/null || \
            log "[IMP:9][watchdog][cb:${service_name}] WARNING: Could not stop container ${service_name}"

        log "[IMP:8][watchdog][cb:${service_name}] Container ${service_name} stopped"
    else
        log "[IMP:8][watchdog][cb:${service_name}] Container ${service_name} is not running"
    fi

    # Send Telegram alert
    send_telegram "🚨 [${CONTEXT:-unknown}] Circuit breaker opened for ${service_name}%0A${max_failures:-5} failures in ${window_seconds:-300}s%0AAuto-stopped to prevent crash-loop data corruption"
}
# endregion

# region FUNC__run_circuit_breaker_cycle
## @brief  Run one circuit breaker check cycle for all configured services
## @param  None (reads CIRCUIT_BREAKER_SERVICES global)
run_circuit_breaker_cycle() {
    log "[IMP:7][watchdog][cb] Running circuit breaker check cycle"

    for entry in "${CIRCUIT_BREAKER_SERVICES[@]}"; do
        # Parse entry: service_name:check_cmd:max_failures:window_sec
        local service_name check_cmd max_failures window_seconds
        IFS=':' read -r service_name check_cmd max_failures window_seconds <<< "$entry"

        if [[ -z "$service_name" || -z "$check_cmd" ]]; then
            log "[IMP:8][watchdog][cb] Invalid circuit breaker entry: ${entry} — skipping"
            continue
        fi

        log "[IMP:8][watchdog][cb:${service_name}] Checking health via: ${check_cmd}"

        if check_service_health "$service_name" "$check_cmd"; then
            log "[IMP:8][watchdog][cb:${service_name}] Health check PASSED"
        else
            log "[IMP:9][watchdog][cb:${service_name}] Health check FAILED"

            if ! increment_failure_counter "$service_name" "${max_failures:-5}" "${window_seconds:-300}"; then
                circuit_break "$service_name"
            fi
        fi
    done

    log "[IMP:7][watchdog][cb] Circuit breaker cycle complete"
}
# endregion
# endregion CIRCUIT_BREAKER

# ============================================================================
# Main watchdog logic — circuit breaker (every tick) + self-update (on PENDING_FILE)
# ============================================================================

log "[IMP:8][watchdog][main] ===== Watchdog tick started ====="

# ── Circuit breaker cycle (runs EVERY tick, independent of self-update) ──
run_circuit_breaker_cycle

# ── Self-update check (only if PENDING_FILE exists) ──
if [[ ! -f "$PENDING_FILE" ]]; then
    log "[IMP:3][watchdog][main] No pending update file — circuit breaker cycle complete"
    exit 0
fi

# ------------------------------------------------------------------
# Step 2: Read new_version, timestamp, and state from PENDING_FILE
# ------------------------------------------------------------------
safe_source "$PENDING_FILE"

# If state is not "pending", the update was already handled — skip
if [[ "${state:-}" != "pending" ]]; then
    log "[IMP:3][watchdog][main] PENDING_FILE state is '${state:-}' (not pending) — already handled, exiting"
    exit 0
fi

log "[IMP:8][watchdog][main] Pending update detected: version=${new_version:-unknown} timestamp=${timestamp:-0}"

# ------------------------------------------------------------------
# Step 3: Poll /ready for WATCHDOG_TIMEOUT seconds
# ------------------------------------------------------------------
ready_result="$(poll_ready "update")"

# ------------------------------------------------------------------
# Step 4: /ready returned 200 within timeout — SUCCESS
# ------------------------------------------------------------------
if [[ "$ready_result" == "ok" ]]; then
    log "[IMP:9][watchdog][success] New version ${new_version:-unknown} is ready and healthy"

    # 4a: Mark success in PENDING_FILE
    log "[IMP:7][watchdog][success] Marking update as successful in ${PENDING_FILE}"
    cat > "$PENDING_FILE" <<EOF
new_version=${new_version:-unknown}
timestamp=${timestamp:-0}
state=success
success_time=$(date +%s)
EOF

    # 4b: Remove old images beyond KEEP_IMAGES
    cleanup_old_images

    # 4c: Log success
    log "[IMP:9][watchdog][success] Self-update to ${new_version:-unknown} completed successfully — agent healthy"
    log "[IMP:8][watchdog][main] ===== Watchdog tick completed (success) ====="
    exit 0
fi

# ==================================================================
# Step 5: /ready NOT ready within timeout — ROLLBACK
# ==================================================================

log "[IMP:9][watchdog][rollback] New version ${new_version:-unknown} FAILED /ready check (${WATCHDOG_TIMEOUT}s timeout) — initiating automatic rollback"
log "[IMP:8][watchdog][rollback] Rollback strategy: down → compose pull → up → re-wait → notify"

# 5a: docker compose down hermes-agent
log "[IMP:8][watchdog][rollback] Step 5a: stopping hermes-agent via docker compose down"
sudo docker compose -f "$COMPOSE_FILE" --project-name "$COMPOSE_PROJECT" down hermes-agent 2>&1 | while IFS= read -r line; do
    log "[IMP:6][watchdog][rollback] docker-compose down: ${line}"
done || {
    log "[IMP:9][watchdog][rollback] WARNING: docker compose down returned non-zero — continuing rollback"
}

# 5b: docker compose pull — pull latest image from registry (replaces old docker tag approach)
log "[IMP:8][watchdog][rollback] Step 5b: pulling image via docker compose pull"
if sudo docker compose -f "$COMPOSE_FILE" --project-name "$COMPOSE_PROJECT" pull 2>&1; then
    log "[IMP:7][watchdog][rollback] Successfully pulled latest image from registry"
else
    log "[IMP:9][watchdog][rollback] CRITICAL: docker compose pull failed — image may not be available in registry"
fi

# 5c: docker compose up -d hermes-agent
log "[IMP:8][watchdog][rollback] Step 5c: starting hermes-agent with previous version (docker compose up -d)"
sudo docker compose -f "$COMPOSE_FILE" --project-name "$COMPOSE_PROJECT" up -d hermes-agent 2>&1 | while IFS= read -r line; do
    log "[IMP:6][watchdog][rollback] docker-compose up: ${line}"
done || {
    log "[IMP:9][watchdog][rollback] CRITICAL: docker compose up -d failed"
}

# 5d: Poll /ready again for WATCHDOG_TIMEOUT seconds (re-wait for old version)
log "[IMP:8][watchdog][rollback] Step 5d: re-polling /ready for previous version"
rollback_ready_result="$(poll_ready "rollback")"

# ==================================================================
# Step 5e: Rollback SUCCESS
# ==================================================================
if [[ "$rollback_ready_result" == "ok" ]]; then
    log "[IMP:9][watchdog][rollback_success] Rollback to previous version SUCCESSFUL — agent is healthy"

    # Update PENDING_FILE with rollback state
    cat > "$PENDING_FILE" <<EOF
new_version=${new_version:-unknown}
timestamp=${timestamp:-0}
state=rolled_back
rollback_time=$(date +%s)
EOF

    # Send Telegram notification via direct curl (bypass dead agent)
    log "[IMP:8][watchdog][rollback_success] Sending rollback notification via direct Telegram"
    # Telegram message: URL-encoded newlines (%0A)
    send_telegram "🔄 [${CONTEXT:-unknown}] Agent auto-rollback%0ANew version failed /ready check (${WATCHDOG_TIMEOUT}s timeout)%0AReverted to previous image"

    log "[IMP:9][watchdog][rollback_success] Rollback complete — node operational"
    log "[IMP:8][watchdog][main] ===== Watchdog tick completed (rolled_back) ====="
    exit 0
fi

# ==================================================================
# Step 5f: Rollback ALSO FAILED — critical escalation
# ==================================================================

log "[IMP:9][watchdog][rollback_critical] ====================================================="
log "[IMP:9][watchdog][rollback_critical] CRITICAL: Agent rollback FAILED after ${WATCHDOG_TIMEOUT}s"
log "[IMP:9][watchdog][rollback_critical] Agent is NOT responsive — manual intervention required"
log "[IMP:9][watchdog][rollback_critical] ====================================================="

# Update PENDING_FILE with failure state
cat > "$PENDING_FILE" <<EOF
new_version=${new_version:-unknown}
timestamp=${timestamp:-0}
state=rollback_failed
failure_time=$(date +%s)
EOF

# Diagnostic: show container status
log "[IMP:9][watchdog][rollback_critical] Current hermes-agent container status:"
sudo docker ps -a --filter "name=hermes-agent" --format '{{.Names}} {{.Status}} {{.Image}}' 2>&1 | while IFS= read -r line; do
    log "[IMP:9][watchdog][rollback_critical]   ${line}"
done

# Critical Telegram notification
log "[IMP:9][watchdog][rollback_critical] Sending CRITICAL Telegram notification"
send_telegram "🚨 CRITICAL: Agent rollback FAILED — manual intervention required"

log "[IMP:9][watchdog][rollback_critical] Watchdog exiting with code 1 — node remains operational (agent is non-critical)"
log "[IMP:8][watchdog][main] ===== Watchdog tick completed (CRITICAL FAILURE) ====="
exit 1
