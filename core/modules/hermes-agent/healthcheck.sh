#!/usr/bin/env bash
# GREP_SUMMARY: hermes-agent healthcheck liveness readiness http /health /ready curl check_docker_health deep
# STRUCTURE: ▶ source lib/healthcheck.sh → ◇ MODE=deep ? liveness|readiness|deps → ⎋ check_docker_health → exit
# region MODULE_CONTRACT
## @purpose  Docker healthcheck for hermes-agent — uses check_docker_health for liveness,
##           deep mode supports liveness, readiness, and dependency checks
## @scope    Called by Docker HEALTHCHECK (in-container) and modules-healthcheck.sh (host-side)
## @invariants
##   - MODE=deep with sub-mode: liveness ( /health ), readiness ( /ready ), deps (PG, Redis, LiteLLM)
##   - Default: delegates to check_docker_health for liveness via docker inspect
##   - Container name: hermes-agent
##   - exit 0 = healthy; exit 1 = unhealthy
## @rationale Unified Docker healthcheck pattern per DevPlan §DD1; deep mode preserves
##   existing readiness and dependency checking logic
## @source ../../lib/healthcheck.sh
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][hermes-agent-hc][main] Starting hermes-agent healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="hermes-agent"
AGENT_URL="http://127.0.0.1:9119"
# ✅ TRAP[INCIDENT] · 2026-07-10 · P1 · Healthcheck port 8080 → 9119 fix
# · Symptom: Module healthcheck always fails via docker exec — curl http://localhost:8080/health → Connection refused
# · Root: The TRAP[INCIDENT] from 2026-07-03 misidentified the port. Inside the hermes-agent container,
# ·   the Hermes Agent listens on port 9119 (dashboard port), NOT 8080. Port 8080 is not used by the agent.
# ·   Docker compose healthcheck correctly uses 127.0.0.1:9119/ (dashboard root).
# · Fix: Use 127.0.0.1:9119 (internal dashboard port) — matches docker-compose and L1 Dockerfile healthcheck.
MODE="${1:-}"

# ── Deep check: multi-mode healthcheck (liveness, readiness, deps) ──
if [ "$MODE" = "deep" ]; then
    # shellcheck disable=SC2034
    DEEP_MODE="${2:-liveness}"

    case "$DEEP_MODE" in
        liveness)
            ENDPOINT="/health"
            CHECK_TYPE="LIVENESS"
            ;;
        readiness)
            ENDPOINT="/ready"
            CHECK_TYPE="READINESS"
            ;;
        deps)
            # Dependency check mode
            log_imp 8 "deps" "Starting dependency checks ..."

            PG_HOST="${POSTGRES_HOST:-pgbouncer}"
            PG_PORT="${POSTGRES_PORT:-6432}"
            REDIS_HOST="${REDIS_HOST:-redis}"
            REDIS_PORT="${REDIS_PORT:-6379}"
            LITELLM_URL="${LITELLM_HEALTH_URL:-http://litellm:4000/health}"

            PG_OK=false
            REDIS_OK=false
            LITELLM_OK=false

            # ── Check PostgreSQL ──
            if command -v pg_isready &>/dev/null; then
                if pg_isready -h "$PG_HOST" -p "$PG_PORT" &>/dev/null; then
                    log_imp 8 "deps" "PostgreSQL: ok (pg_isready)"
                    PG_OK=true
                else
                    log_imp 9 "deps" "PostgreSQL: FAIL (pg_isready)"
                fi
            else
                if timeout 3 bash -c "echo > /dev/tcp/${PG_HOST}/${PG_PORT}" 2>/dev/null; then
                    log_imp 8 "deps" "PostgreSQL: ok (TCP socket)"
                    PG_OK=true
                else
                    log_imp 9 "deps" "PostgreSQL: FAIL (TCP socket)"
                fi
            fi

            # ── Check Redis (optional — warn only) ──
            if command -v redis-cli &>/dev/null; then
                if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" PING 2>/dev/null | grep -q "PONG"; then
                    log_imp 8 "deps" "Redis: ok (redis-cli PONG)"
                    REDIS_OK=true
                else
                    log_imp 8 "deps" "Redis: warn (no PONG — optional)"
                fi
            else
                if timeout 3 bash -c "echo > /dev/tcp/${REDIS_HOST}/${REDIS_PORT}" 2>/dev/null; then
                    log_imp 8 "deps" "Redis: ok (TCP socket reachable)"
                    REDIS_OK=true
                else
                    log_imp 8 "deps" "Redis: warn (TCP unreachable — optional)"
                fi
            fi

            # ── Check LiteLLM ──
            if curl -sf --connect-timeout 5 --max-time 10 "$LITELLM_URL" > /dev/null 2>&1; then
                log_imp 8 "deps" "LiteLLM: ok (HTTP 200)"
                LITELLM_OK=true
            else
                log_imp 9 "deps" "LiteLLM: FAIL (HTTP error or timeout)"
            fi

            # ── Aggregate result ──
            if $PG_OK && $LITELLM_OK; then
                log_imp 9 "deps" "All required dependencies ok (PG+LiteLLM)"
                exit 0
            else
                log_imp 9 "deps" "FAIL: required dependencies not available (PG=${PG_OK} LiteLLM=${LITELLM_OK})"
                exit 1
            fi
            ;;
        *)
            log_imp 9 "deep" "unknown deep mode '$DEEP_MODE' — expected 'liveness', 'readiness', or 'deps'"
            echo "Usage: $0 deep {liveness|readiness|deps}" >&2
            exit 1
            ;;
    esac

    # ── Host-side check via docker exec ──
    if command -v docker &>/dev/null && docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
        if docker exec "$CONTAINER" curl -sf --connect-timeout 5 --max-time 10 "${AGENT_URL}${ENDPOINT}" > /dev/null 2>&1; then
            log_imp 8 "deep" "${CHECK_TYPE} PASS: ${ENDPOINT} responded 200 (docker exec)"
            exit 0
        else
            log_imp 9 "deep" "${CHECK_TYPE} FAIL: ${ENDPOINT} not reachable via docker exec"
            exit 1
        fi
    # ── In-container check via shared library ──
    else
        if check_http "${AGENT_URL}${ENDPOINT}" "200"; then
            log_imp 8 "deep" "${CHECK_TYPE} PASS: ${ENDPOINT} responded 200"
            exit 0
        else
            log_imp 9 "deep" "${CHECK_TYPE} FAIL: ${ENDPOINT} not reachable on ${AGENT_URL}${ENDPOINT}"
            exit 1
        fi
    fi
fi

# Default liveness check via docker inspect
echo "[IMP:8][hermes-agent-hc][liveness] Running default liveness check" >&2
check_docker_health "$CONTAINER" || exit 1
echo "[IMP:9][hermes-agent-hc][liveness] Container healthy" >&2
exit 0
