#!/usr/bin/env bash
# GREP_SUMMARY: internal-healthcheck module-orchestration docker-inspect iterate-modules
# STRUCTURE: ▶ init → iterate module.yaml → ◇ install_type:docker → docker inspect | ◇ install_type:system → healthcheck.sh liveness → ◇ MODE=deep → healthcheck.sh MODE=deep → ⊕ exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose  Оркестратор healthcheck всех модулей: docker inspect для docker-модулей,
##           healthcheck.sh liveness для system-модулей, MODE=deep — глубокая диагностика
## @scope    Вызывается ТОЛЬКО из core/entrypoints/healthcheck.sh (make healthcheck)
## @invariants
##   - Итерирует core/modules/*/module.yaml — единственный source of truth состава модулей
##   - exit 0 = все модули healthy; exit 1 = хотя бы один unhealthy
##   - Module healthcheck.sh вызывается через `bash` (не требует exec-бита)
## @rationale Единый агрегирующий healthcheck для make healthcheck и CI-gate'ов
# endregion MODULE_CONTRACT
set -euo pipefail

echo "[IMP:7][modules-healthcheck][main] Starting module healthcheck orchestration" >&2
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="$(cd "${_SCRIPT_DIR}/../../.." && pwd)"
unset _SCRIPT_DIR

_HEALTHCHECK_LIB_DIR="${PLATFORM_ROOT}/core/lib"
source "${_HEALTHCHECK_LIB_DIR}/logging.sh"

__LOG_PREFIX="modules-healthcheck"

if [[ "${1:-}" == "--help" ]]; then
    echo "Usage: $0 [MODE=deep]"
    echo ""
    echo "Iterate all platform module healthcheck scripts and run them."
    echo "Default: docker inspect for docker modules, healthcheck.sh liveness for system modules."
    echo "MODE=deep: run module-specific healthcheck.sh MODE=deep for all modules."
    echo ""
    echo "Returns 0 if all pass, 1 if any fail."
    exit 0
fi

MODE="${1:-}"

log_imp 9 "iterate" "Running all module healthchecks (MODE=${MODE:-default})..."
FAILED=0

for module_yaml in "${PLATFORM_ROOT}"/core/modules/*/module.yaml; do
    [[ -f "$module_yaml" ]] || continue

    MODULE="$(basename "$(dirname "$module_yaml")")"

    # Skip non-module directories
    [[ "$MODULE" =~ ^(observability)$ ]] && continue

    log_imp 8 "check" "Checking ${MODULE}..."

    # Detect install_type
    INSTALL_TYPE=$(grep -E '^install_type:' "$module_yaml" | awk '{print $2}' || echo "docker")

    if [ "$MODE" = "deep" ]; then
        # Deep mode: run module healthcheck.sh with MODE=deep
        hc_script="${PLATFORM_ROOT}/core/modules/${MODULE}/healthcheck.sh"
        if [[ -f "$hc_script" ]]; then
            if bash "$hc_script" deep 2>/dev/null; then
                log_imp 8 "check" "PASS (deep): ${MODULE}"
            else
                log_imp 9 "check" "FAIL (deep): ${MODULE}"
                FAILED=1
            fi
        else
            log_imp 8 "check" "SKIP (no healthcheck.sh): ${MODULE}"
        fi
    elif [ "$INSTALL_TYPE" = "docker" ]; then
        # Default mode for docker modules: docker inspect
        # Get ALL container_name entries from base.yml (no head -1 — all containers matter)
        # Anchored to line start: comment lines (## @purpose ... container_name: ...) must not match
        mapfile -t CONTAINER_NAMES < <(grep -E '^[[:space:]]*container_name:' "${PLATFORM_ROOT}/core/modules/${MODULE}/docker-compose.base.yml" 2>/dev/null | awk '{print $2}')
        if [ ${#CONTAINER_NAMES[@]} -eq 0 ]; then
            CONTAINER_NAMES=("$MODULE")
        fi

        for CONTAINER_NAME in "${CONTAINER_NAMES[@]}"; do
            # Inspect health status, restarting state, and restart count
            HEALTH_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "not-found")

            if [ "$HEALTH_STATUS" != "not-found" ]; then
                RESTARTING=$(docker inspect --format='{{.State.Restarting}}' "$CONTAINER_NAME" 2>/dev/null || echo "false")
                RESTART_COUNT=$(docker inspect --format='{{.RestartCount}}' "$CONTAINER_NAME" 2>/dev/null || echo "0")
            else
                RESTARTING="false"
                RESTART_COUNT="0"
            fi

            # Determine FAIL condition: restart loop overrides health status
            IS_RESTART_LOOP=false
            if [ "$RESTARTING" = "true" ]; then
                IS_RESTART_LOOP=true
            elif [ "$RESTART_COUNT" != "not-found" ] && [ "$RESTART_COUNT" -gt 5 ] 2>/dev/null; then
                IS_RESTART_LOOP=true
            fi

            case "$HEALTH_STATUS" in
                "healthy")
                    if $IS_RESTART_LOOP; then
                        log_imp 9 "check" "FAIL: ${MODULE} → ${CONTAINER_NAME} restart loop (restarting=${RESTARTING}, restarts=${RESTART_COUNT})"
                        FAILED=1
                    else
                        log_imp 8 "check" "PASS: ${MODULE} → ${CONTAINER_NAME} healthy"
                    fi
                    ;;
                "unhealthy")
                    log_imp 9 "check" "FAIL: ${MODULE} → ${CONTAINER_NAME} unhealthy"
                    FAILED=1
                    ;;
                "starting"|"")
                    if $IS_RESTART_LOOP; then
                        log_imp 9 "check" "FAIL: ${MODULE} → ${CONTAINER_NAME} restart loop (restarting=${RESTARTING}, restarts=${RESTART_COUNT})"
                        FAILED=1
                    else
                        log_imp 8 "check" "WARN: ${MODULE} → ${CONTAINER_NAME} starting or no healthcheck"
                    fi
                    ;;
                "not-found")
                    log_imp 8 "check" "SKIP: ${MODULE} → container ${CONTAINER_NAME} not found"
                    ;;
                *)
                    log_imp 8 "check" "WARN: ${MODULE} → ${CONTAINER_NAME} status=${HEALTH_STATUS}"
                    ;;
            esac
        done
    else
        # System module: run healthcheck.sh liveness
        hc_script="${PLATFORM_ROOT}/core/modules/${MODULE}/healthcheck.sh"
        if [[ -f "$hc_script" ]]; then
            if bash "$hc_script" liveness 2>/dev/null; then
                log_imp 8 "check" "PASS (liveness): ${MODULE}"
            else
                log_imp 9 "check" "FAIL (liveness): ${MODULE}"
                FAILED=1
            fi
        else
            log_imp 8 "check" "SKIP (no healthcheck.sh): ${MODULE}"
        fi
    fi
done

if [[ "$FAILED" -eq 0 ]]; then
    log_imp 9 "summary" "ALL MODULES HEALTHY"
    echo "[IMP:9][modules-healthcheck][summary] ALL MODULES HEALTHY" >&2
else
    log_imp 9 "summary" "SOME MODULES UNHEALTHY"
    echo "[IMP:9][modules-healthcheck][summary] SOME MODULES UNHEALTHY (exit ${FAILED})" >&2
fi
exit "$FAILED"
