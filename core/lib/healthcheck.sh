#!/usr/bin/env bash
# GREP_SUMMARY: healthcheck library service-check dependency-check module-health
# STRUCTURE: ┌module healthcheck┐ → ○ poll_until_healthy → ◇ check_docker_health → ◇ check_http → ⊕ exit 0/1
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
## @scope    — poll_until_healthy: universal poll loop with timeout/interval
##           — check_docker_health: docker container health status
##           — check_http: HTTP endpoint health check via curl
##           — zero side-effects on source (pure function definitions only)
## @input    — __LOG_PREFIX (env var, set before source; default: "healthcheck")
##           — check_command passed to poll_until_healthy as a string, internally
##             split into array for safe execution (no eval)
## @output   — Structured stderr lines via log_imp (from logging.sh)
##           — Return codes: 0=healthy, 1=timeout/unhealthy, 2=starting,
##             3=not-found/error
## @links    — SOURCES: core/lib/logging.sh for LDD logging
##           — USED_BY: deploy-project.sh, all module/*/healthcheck.sh scripts
##           — REPLACES: inline healthcheck loops in deleted scripts (core-deploy.yml CI workflow,
##             deploy.sh); module healthcheck.sh scripts now source this library directly
## @invariants — MUST NOT execute any code on source (no side-effects)
##             — __LOG_PREFIX defaults to "healthcheck" if unset
##             — All stderr output MUST go through log_imp (from logging.sh)
##             — poll_until_healthy MUST accept any bash command as
##               check_command (executed via array, not eval)
##             - check_command exit code determines health (0=healthy, !=0=not)
## @rationale Q: Why composable primitives instead of one monolithic check?
##            A: Monolithic check functions force every consumer to accept
##            the same behavior. Primitives let deploy scripts compose
##            exactly the polling logic they need (custom interval, timeout,
##            check command) and reuse check_docker_health / check_http as
##            building blocks inside poll_until_healthy.
## @changes   LAST_CHANGE: 2026-07-07 · T1 — Initial implementation
## @modulemap — poll_until_healthy  [W:50] Universal poll loop
##             — check_docker_health [W:30] Docker container health check
##             — check_http          [W:30] HTTP endpoint health check
## @usecases  — Developer: poll_until_healthy "nginx" "check_http http://localhost:80" 30 2
##             — Developer: check_docker_health "my_container"
##             — Developer: check_http "http://example.com/health" "200,301,302"
## @changes   — 2026-07-09 · TASK-10 — Replaced eval with array-based execution for safety;
##             check_command string is split into array via IFS read -ra
## @verified  — 2026-07-09 · TASK-4.5 — 6/6 existing healthcheck.sh modules source this library
##             (grep -rl "source.*lib/healthcheck.sh" core/modules/*/healthcheck.sh | wc -l → 6;
##             platform-secrets has no healthcheck.sh — uses systemd service, not Docker module)
# endregion MODULE_CONTRACT
# GREP_SUMMARY: healthcheck, poll, wait_for, docker health, HTTP check, curl, health, liveness, readiness, poll_until_healthy, check_docker_health, check_http
# STRUCTURE: ▶ ┌SCRIPT_DIR┐ → source logging.sh → ○ poll_until_healthy(name,cmd,timeout,interval) → ┌"${check_cmd[@]}"┐ → ◇ exit 0? → ⎋ 0 | ◇ timeout? → ⎋ 1
#            └─ ▶ check_docker_health(id) → docker inspect --format ─ ◇ healthy?0 / unhealthy?1 / starting?2 / not-found?3
#            └─ ▶ check_http(url,codes) → curl -s -o /dev/null -w '%{http_code}' → ◇ code in expected? → ⎋ 0 | ⎋ 1

# ═══════════════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════════════

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
# UNIVERSAL POLL LOOP
# ═══════════════════════════════════════════════════════════════════
# region FUNC_poll_until_healthy
## @purpose  Poll a check_command until it exits with 0 or timeout expires.
##           Universal primitive — accepts any bash command as check.
## @param $1  Name: human-readable label for logging (e.g. "nginx", "postgres")
## @param $2  check_command: bash command string to execute via array. Exit 0 = healthy.
## @param $3  timeout: max wait in seconds (default: 60)
## @param $4  interval: seconds between retries (default: 5)
## @io       out: stderr via log_imp — IMP:8 per attempt, IMP:9 on success, IMP:10 on timeout
## @return   0 if check_command exits 0 before timeout
##           1 if timeout expires without success
## @complexity O(n) where n = ceil(timeout / interval)
## @invariants — check_command is split into array and executed via "${check_cmd[@]}"
##               — supports simple commands, function names, pipes/redirects not supported
##                 (use wrapper function for complex pipelines)
##             — At least one check always runs before timeout comparison
##             — EPOCHSECONDS used if available (bash 5.0+), else date +%s
poll_until_healthy() {
    local name="$1"
    local check_command="$2"
    local timeout="${3:-60}"
    local interval="${4:-5}"

    # Validate required arguments
    if [ -z "${name}" ] || [ -z "${check_command}" ]; then
        log_imp 10 "poll_until_healthy" "Invalid arguments: name='${name}' command='${check_command}'"
        return 1
    fi

    # ⚠️ TRAP[DECISION] · 2026-07-09 · — · eval replaced with array-based execution
    # · Rejected: in-shell eval for command strings, complex word-splitting logic
    # · Reason: eval is a shell injection risk; check_command is trusted but the
    #   defensive approach is to always use "${check_cmd[@]}" array execution.
    #   The command string is split into an array via read -ra (simple word splitting
    #   on IFS). Complex commands with quotes/redirects should use a wrapper function.
    # · Rev: if check_command needs pipes or redirects, replace with a named function
    #   that encapsulates the logic and pass just the function name as check_command.
    local check_cmd=()
    IFS=' ' read -ra check_cmd <<< "$check_command"

    # Detect monotonic clock source (EPOCHSECONDS is bash 5.0+)
    local start now elapsed
    if [ -n "${EPOCHSECONDS+set}" ]; then
        start=${EPOCHSECONDS}
    else
        printf -v start '%(%s)T' -1
    fi

    log_imp 8 "poll_until_healthy" "Polling '${name}' — timeout=${timeout}s interval=${interval}s"

    while true; do
        # Run the check command via safe array execution (no eval)
        if "${check_cmd[@]}"; then
            log_imp 9 "poll_until_healthy" "'${name}' is healthy (check passed)"
            return 0
        fi

        # Compute elapsed time
        if [ -n "${EPOCHSECONDS+set}" ]; then
            now=${EPOCHSECONDS}
        else
            printf -v now '%(%s)T' -1
        fi
        elapsed=$(( now - start ))

        # Check timeout
        if [ "${elapsed}" -ge "${timeout}" ]; then
            log_imp 10 "poll_until_healthy" "'${name}' NOT healthy after ${timeout}s — giving up"
            return 1
        fi

        log_imp 8 "poll_until_healthy" "'${name}' not ready yet — elapsed=${elapsed}s, waiting ${interval}s"
        sleep "${interval}"
    done
}
# endregion FUNC_poll_until_healthy

# ═══════════════════════════════════════════════════════════════════
# DOCKER HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════
# region FUNC_check_docker_health
## @purpose  Check the health status of a Docker container via docker inspect.
##           Works with containers that have a HEALTHCHECK directive.
## @param $1  container_id: container name or ID
## @io       out: stderr via log_imp — IMP:7 on healthy, IMP:8 on non-healthy
## @return   0 — healthy
##           1 — unhealthy
##           2 — starting (no HEALTHCHECK defined, or status "starting")
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
        "starting"|"")
            # Empty string means no HEALTHCHECK defined, "starting" means still probing
            log_imp 8 "check_docker_health" "Container '${container_id}' is starting or has no HEALTHCHECK"
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
# DOCKER POLL HEALTH — poll until container is healthy or timeout
# ═══════════════════════════════════════════════════════════════════
# region FUNC_poll_docker_health
## @purpose  Poll a Docker container until it's healthy or timeout expires.
##           Convenience wrapper around poll_until_healthy + check_docker_health.
## @param $1  container_id: container name or ID
## @param $2  timeout: max wait in seconds (default: 60)
## @param $3  interval: seconds between retries (default: 5)
## @io       out: stderr via log_imp — IMP:8 per attempt, IMP:9 on success, IMP:10 on timeout
## @return   0 if container becomes healthy before timeout
##           1 if timeout expires without healthy status
## @complexity O(n) where n = ceil(timeout / interval)
poll_docker_health() {
    local container_id="$1"
    local timeout="${2:-60}"
    local interval="${3:-5}"

    if [ -z "${container_id}" ]; then
        log_imp 10 "poll_docker_health" "Invalid arguments: container_id is empty"
        return 1
    fi

    log_imp 8 "poll_docker_health" "Polling container '${container_id}' — timeout=${timeout}s interval=${interval}s"
    poll_until_healthy "${container_id}" "check_docker_health ${container_id}" "${timeout}" "${interval}"
}
# endregion FUNC_poll_docker_health

# ═══════════════════════════════════════════════════════════════════
# HTTP HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════
# region FUNC_check_http
## @purpose  Check an HTTP endpoint health by verifying the response code.
##           Lightweight curl-based check — no HTML parsing, no body fetch.
## @param $1  url: HTTP(S) endpoint to check
## @param $2  expected_codes: comma-separated list of acceptable HTTP codes
##           (default: "200")
## @io       out: stderr via log_imp — IMP:7 on match, IMP:8 on mismatch/error
## @return   0 if response HTTP code is in expected_codes
##           1 if code is not in expected_codes or curl fails
## @complexity O(1) — single curl call with 10s timeout
## @invariants — Requires curl in PATH
##             — Curl follows redirects (use explicit codes if redirect expected)
##             — Response body is discarded (-o /dev/null) — only status matters
check_http() {
    local url="$1"
    local expected_codes="${2:-200}"

    if [ -z "${url}" ]; then
        log_imp 10 "check_http" "Invalid arguments: url is empty"
        return 1
    fi

    log_imp 8 "check_http" "Checking '${url}' — expecting codes: ${expected_codes}"

    # Perform curl with 10s timeout, capture HTTP code only
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${url}" 2>/dev/null) || {
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
