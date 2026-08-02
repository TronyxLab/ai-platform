#!/usr/bin/env bash
# GREP_SUMMARY: healthcheck library service-check dependency-check module-health check_docker_health check_http exec_check
# STRUCTURE: ┌module healthcheck┐ → ◇ check_docker_health → ◇ check_http → ◇ exec_check → ⊕ exit 0/1
# ⚠️ Errexit guard: warn if sourced without `set -e` (fail-fast on errors).
# Uses $- (portable across bash/zsh) instead of [ -o errexit ] (zsh-incompatible).
case $- in *e*) ;; *) echo "[WARN] healthcheck.sh sourced without set -e" >&2 ;; esac
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Healthcheck Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Provide composable healthcheck primitives for all platform
##           scripts. Eliminates duplicated poll-and-check boilerplate
##           across 4+ independent healthcheck implementations.
## @scope    — check_docker_health: docker container health status
##           — check_http: HTTP endpoint health check via curl
##           — exec_check: docker exec command check
##           — zero side-effects on source (pure function definitions only)
##           Волна 118 B6: poll_until_healthy/poll_docker_health/check_tcp УДАЛЕНЫ
##           (0 callers — поллинг через shared healthcheck_poller (Python, D5) и
##           модульные healthcheck.sh используют примитивы check_docker_health/check_http)
## @input    — __LOG_PREFIX (env var, set before source; default: "healthcheck")
## @output   — Structured stderr lines via log_imp (from logging.sh)
##           — Return codes: 0=healthy, 1=timeout/unhealthy, 2=starting,
##             3=not-found/error
## @links    — SOURCES: core/lib/logging.sh for LDD logging
##           — USED_BY: deploy_engine.py, all module/*/healthcheck.sh scripts
##           — REPLACES: inline healthcheck loops in deleted scripts (core-deploy.yml CI workflow,
##             deploy.sh); module healthcheck.sh scripts now source this library directly
## @invariants — MUST NOT execute any code on source (no side-effects)
##             — __LOG_PREFIX defaults to "healthcheck" if unset
##             — All stderr output MUST go through log_imp (from logging.sh)
##             - check_command exit code determines health (0=healthy, !=0=not)
## @rationale Q: Why composable primitives instead of one monolithic check?
##            A: Monolithic check functions force every consumer to accept
##            the same behavior. Primitives let deploy scripts compose
##            exactly the polling logic they need (custom interval, timeout,
##            check command) and reuse check_docker_health / check_http as
##            building blocks inside poll_until_healthy.
## @changes   LAST_CHANGE: 2026-07-07 · T1 — Initial implementation
##           2026-08-02 · Волна 118 B6 — poll_until_healthy/poll_docker_health/check_tcp удалены
## @modulemap — check_docker_health [W:30] Docker container health check
##             — check_http          [W:30] HTTP endpoint health check (with timeout)
##             — exec_check          [W:30] docker exec command check
## @usecases  — Developer: check_docker_health "my_container"
##             — Developer: check_http "http://example.com/health" "200,301,302"
## @changes   — 2026-07-09 · TASK-10 — Replaced eval with array-based execution for safety;
##             check_command string is split into array via IFS read -ra
## @changes   — 2026-07-26 · DevPlan 083 — Added check_tcp(), exec_check(); added timeout param to check_http()
##             (check_tcp удалён волна 118 B6)
## @verified  — 2026-07-26 · T1 — check_tcp() + exec_check() + check_http(timeout) added
##             (grep -rl "source.*lib/healthcheck.sh" core/modules/*/healthcheck.sh | wc -l → 6;
##             platform-secrets has no healthcheck.sh — uses systemd service, not Docker module)
# endregion MODULE_CONTRACT
# GREP_SUMMARY: healthcheck, poll, wait_for, docker health, HTTP check, curl, health, liveness, readiness, check_docker_health, check_http
# STRUCTURE: ▶ ┌SCRIPT_DIR┐ → source logging.sh → ◇ check_docker_health(id) → docker inspect --format ─ ◇ healthy?0 / unhealthy?1 / starting?2 / not-found?3
#            └─ ▶ check_http(url,codes,timeout) → curl -s -o /dev/null -w '%{http_code}' → ◇ code in expected? → ⎋ 0 | ⎋ 1
#            └─ ▶ exec_check(container,command) → ◇ docker inspect Running → ◇ docker exec command → ◇ exit 0? → ⎋ 0 | ⎋ 1

# ═══════════════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════════════

echo "[IMP:7][healthcheck][lib] Loading healthcheck library" >&2

# Determine script directory for sourcing sibling modules
# ⚠️ TRAP[BUG] · 2026-07-07 · P1 · SCRIPT_DIR collision with readonly from caller scripts
# · Root: library files are sourced by caller scripts that may declare SCRIPT_DIR
# ·   as readonly (via declare -r). Reassigning SCRIPT_DIR in the library fails.
# · Fix: use _HEALTHCHECK_LIB_DIR to avoid readonly variable collision
# · Prevention: library files must use unique variable names; avoid SCRIPT_DIR
_HEALTHCHECK_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source LDD logging library (must exist — fail fast if absent)
# shellcheck source=core/lib/logging.sh
source "${_HEALTHCHECK_LIB_DIR}/logging.sh"

# Set default log prefix if not already configured by consumer
: "${__LOG_PREFIX:=healthcheck}"

# ═══════════════════════════════════════════════════════════════════
# DOCKER HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════
# region FUNC_check_docker_health
## @purpose  Check the health status of a Docker container via docker inspect.
##           Works with containers that have a HEALTHCHECK directive.
##           Единый критерий «здоров» (DevPlan 116 B5 T9, D5): контейнер без healthcheck
##           (Health.Status == ""/none) в состоянии running → ЗДОРОВ (0), как в shared
##           healthcheck_poll (docker_compose.py). Parity: Python-критерий только в shared.
## @param $1  container_id: container name or ID
## @io       out: stderr via log_imp — IMP:7 on healthy, IMP:8 on non-healthy
## @return   0 — healthy, ИЛИ running-без-healthcheck (""/none + state=running)
##           1 — unhealthy
##           2 — starting (status "starting"), или ""/none + не-running, или unknown status
##           3 — container not found (docker inspect error)
## @complexity O(1) — single docker inspect call
## @invariants — Requires docker CLI in PATH
##             — Does NOT modify container state (read-only)
check_docker_health() {
    local container_id="$1"

    if [ -z "${container_id}" ]; then
        log_imp 10 "check_docker_health" "Invalid arguments: container_id is empty"
        return 3
    fi

    # Attempt to get health status via docker inspect
    local health_status
    health_status=$(docker inspect --format='{{.State.Health.Status}}' "${container_id}" 2>/dev/null) || {
        log_imp 8 "check_docker_health" "Container '${container_id}' not found"
        return 3
    }

    case "${health_status}" in
        "healthy")
            log_imp 7 "check_docker_health" "Container '${container_id}' is healthy"
            return 0
            ;;
        "unhealthy")
            log_imp 8 "check_docker_health" "Container '${container_id}' is unhealthy"
            return 1
            ;;
        "starting")
            log_imp 8 "check_docker_health" "Container '${container_id}' is starting (still probing)"
            return 2
            ;;
        ""|"none")
            # Нет HEALTHCHECK — контейнер ЗДОРОВ только если running (D5, канон shared healthcheck_poll)
            local state
            state="$(docker inspect --format='{{.State.Status}}' "${container_id}" 2>/dev/null)"
            if [ "${state}" = "running" ]; then
                log_imp 7 "check_docker_health" "Container '${container_id}' running without healthcheck — healthy (D5)"
                return 0
            fi
            log_imp 8 "check_docker_health" "Container '${container_id}' no healthcheck and NOT running (state='${state}')"
            return 2
            ;;
        *)
            log_imp 8 "check_docker_health" "Container '${container_id}' has unknown health status: '${health_status}'"
            return 2
            ;;
    esac
}
# endregion FUNC_check_docker_health

# ═══════════════════════════════════════════════════════════════════
# HTTP HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════
# region FUNC_check_http
## @purpose  Check an HTTP endpoint health by verifying the response code.
##           Lightweight curl-based check — no HTML parsing, no body fetch.
## @param $1  url: HTTP(S) endpoint to check
## @param $2  expected_codes: comma-separated list of acceptable HTTP codes
##           (default: "200")
## @param $3  timeout: max wait in seconds for curl (default: 10)
## @io       out: stderr via log_imp — IMP:7 on match, IMP:8 on mismatch/error
## @return   0 if response HTTP code is in expected_codes
##           1 if code is not in expected_codes or curl fails
## @complexity O(1) — single curl call with configurable timeout
## @invariants — Requires curl in PATH
##             — Curl follows redirects (use explicit codes if redirect expected)
##             — Response body is discarded (-o /dev/null) — only status matters
check_http() {
    local url="$1"
    local expected_codes="${2:-200}"
    local timeout="${3:-10}"

    if [ -z "${url}" ]; then
        log_imp 10 "check_http" "Invalid arguments: url is empty"
        return 1
    fi

    log_imp 8 "check_http" "Checking '${url}' — expecting codes: ${expected_codes}, timeout=${timeout}s"

    # Perform curl with configurable timeout, capture HTTP code only
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "${timeout}" "${url}" 2>/dev/null) || {
        log_imp 8 "check_http" "Curl failed for '${url}'"
        return 1
    }

    # Check if http_code is in expected_codes list
    local code
    local IFS=,
    for code in ${expected_codes}; do
        # Trim whitespace from code
        code="${code#"${code%%[![:space:]]*}"}"
        code="${code%"${code##*[![:space:]]}"}"
        if [ "${http_code}" = "${code}" ]; then
            log_imp 7 "check_http" "'${url}' responded with ${http_code} (expected)"
            return 0
        fi
    done

    log_imp 8 "check_http" "'${url}' responded with ${http_code} (not in: ${expected_codes})"
    return 1
}
# endregion FUNC_check_http

# ═══════════════════════════════════════════════════════════════════
# DOCKER EXEC CHECK
# ═══════════════════════════════════════════════════════════════════
# region FUNC_exec_check
## @purpose  Execute a command inside a Docker container and check its exit code.
##           Replaces copy-paste docker exec pattern across 5+ modules.
## @param $1  container: Docker container name or ID
## @param $2  command: command to run inside the container (string, split on IFS)
## @io       out: stderr via log_imp — IMP:7 on success, IMP:8 on container not running,
##           IMP:9 on command failure
## @return   0 if container is running and command exits 0
##           1 if container is not running or command fails
## @complexity O(1) — single docker inspect + single docker exec call
## @invariants — Requires docker CLI in PATH
##             — Container must be in "running" state for exec to succeed
##             — Command string is NOT eval'd — split into array via IFS (same as poll_until_healthy)
exec_check() {
    local container="$1"
    local command="$2"

    if [ -z "${container}" ] || [ -z "${command}" ]; then
        log_imp 10 "exec_check" "Invalid arguments: container='${container}' command='${command}'"
        return 1
    fi

    # Verify container is running
    log_imp 8 "exec_check" "Verifying container '${container}' is running"
    local running
    running=$(docker inspect "${container}" --format '{{.State.Running}}' 2>/dev/null) || {
        log_imp 8 "exec_check" "Container '${container}' not found"
        return 1
    }

    if [ "${running}" != "true" ]; then
        log_imp 8 "exec_check" "Container '${container}' is not running (state: ${running})"
        return 1
    fi

    # Split command string into array (safe execution, no eval)
    local cmd_arr=()
    IFS=' ' read -ra cmd_arr <<< "$command"

    log_imp 8 "exec_check" "Executing in '${container}': ${command}"
    if docker exec "${container}" "${cmd_arr[@]}"; then
        log_imp 7 "exec_check" "Container '${container}' — ${command} — OK"
        return 0
    else
        log_imp 9 "exec_check" "Container '${container}' — ${command} — FAILED (exit $?)"
        return 1
    fi
}
# endregion FUNC_exec_check
