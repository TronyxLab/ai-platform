#!/usr/bin/env bash
# GREP_SUMMARY: internal-healthcheck module-orchestration iterate-modules check_docker_health
# STRUCTURE: ▶ init → iterate module.yaml → ◇ install_type:docker → invoke_module_interface healthcheck liveness | ◇ install_type:system → invoke_module_interface healthcheck liveness → ◇ MODE=deep → invoke_module_interface healthcheck deep → ⊕ exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose  Оркестратор healthcheck всех модулей: healthcheck.sh liveness для docker-модулей (через invoke_module_interface),
##           healthcheck.sh liveness для system-модулей, MODE=deep — глубокая диагностика
## @scope    Вызывается ТОЛЬКО из core/entrypoints/healthcheck.sh (make healthcheck)
## @invariants
##   - Итерирует core/modules/*/module.yaml — единственный source of truth состава модулей
##   - exit 0 = все модули healthy; exit 1 = хотя бы один unhealthy
##   - Module healthcheck.sh вызывается через invoke_module_interface (typed contract)
##   - DRIFT-H7: replaced raw docker inspect with invoke_module_interface → check_docker_health()
##   - DRIFT-H1: consolidated 9 mechanisms → 3 primitives (check_docker_health, check_http, exec_check)
## @rationale Единый агрегирующий healthcheck для make healthcheck и CI-gate'ов.
##            DRIFT-H7 fix: All modules use the same invoke_module_interface pattern — eliminates
##            raw docker inspect duplication and ensures every module's healthcheck.sh is exercised.
# endregion MODULE_CONTRACT
set -euo pipefail

echo "[IMP:7][modules-healthcheck][main] Starting module healthcheck orchestration" >&2
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="$(cd "${_SCRIPT_DIR}/../../.." && pwd)"
unset _SCRIPT_DIR

_HEALTHCHECK_LIB_DIR="${PLATFORM_ROOT}/core/lib"
source "${_HEALTHCHECK_LIB_DIR}/logging.sh"

# Source paths + module-interface for typed contract cross-layer calls
source "${_HEALTHCHECK_LIB_DIR}/paths.sh"

__LOG_PREFIX="modules-healthcheck"

if [[ "${1:-}" == "--help" ]]; then
    echo "Usage: $0 [MODE=deep]"
    echo ""
    echo "Iterate all platform module healthcheck scripts and run them."
    echo "Default: healthcheck.sh liveness (via invoke_module_interface) for all modules."
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
        # Deep mode: run module healthcheck.sh with MODE=deep via typed contract
        if invoke_module_interface "$MODULE" healthcheck deep 2>/dev/null; then
            log_imp 8 "check" "PASS (deep): ${MODULE}"
        else
            log_imp 9 "check" "FAIL (deep): ${MODULE}"
            FAILED=1
        fi
    elif [ "$INSTALL_TYPE" = "docker" ]; then
        # Default mode for docker modules: healthcheck.sh liveness via typed contract
        # DRIFT-H7 fix: was raw docker inspect for Health.Status, now delegates to
        # invoke_module_interface which calls module healthcheck.sh → check_docker_health()
        MODULE_PASSED=true
        if ! invoke_module_interface "$MODULE" healthcheck liveness 2>/dev/null; then
            MODULE_PASSED=false
            FAILED=1
        fi

        # Restart loop detection: check State.Restarting and RestartCount
        # This is a SECONDARY check — independent of module healthcheck.sh liveness.
        # A container in restart loop may show "healthy" briefly between restarts.
        mapfile -t CONTAINER_NAMES < <(grep -E '^[[:space:]]*container_name:' "${PLATFORM_ROOT}/core/modules/${MODULE}/docker-compose.base.yml" 2>/dev/null | awk '{print $2}')
        if [ ${#CONTAINER_NAMES[@]} -eq 0 ]; then
            CONTAINER_NAMES=("$MODULE")
        fi

        for CONTAINER_NAME in "${CONTAINER_NAMES[@]}"; do
            RESTARTING=$(docker inspect --format='{{.State.Restarting}}' "$CONTAINER_NAME" 2>/dev/null || echo "false")
            RESTART_COUNT=$(docker inspect --format='{{.RestartCount}}' "$CONTAINER_NAME" 2>/dev/null || echo "0")

            IS_RESTART_LOOP=false
            if [ "$RESTARTING" = "true" ]; then
                IS_RESTART_LOOP=true
            elif [ "$RESTART_COUNT" != "not-found" ] && [ "$RESTART_COUNT" -gt 5 ] 2>/dev/null; then
                IS_RESTART_LOOP=true
            fi

            if $IS_RESTART_LOOP; then
                log_imp 9 "check" "FAIL: ${MODULE} → ${CONTAINER_NAME} restart loop (restarting=${RESTARTING}, restarts=${RESTART_COUNT})"
                MODULE_PASSED=false
                FAILED=1
            fi
        done

        if $MODULE_PASSED; then
            log_imp 8 "check" "PASS (liveness): ${MODULE}"
        fi
    else
        # System module: run healthcheck.sh liveness via typed contract
        if invoke_module_interface "$MODULE" healthcheck liveness 2>/dev/null; then
            log_imp 8 "check" "PASS (liveness): ${MODULE}"
        else
            log_imp 9 "check" "FAIL (liveness): ${MODULE}"
            FAILED=1
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
