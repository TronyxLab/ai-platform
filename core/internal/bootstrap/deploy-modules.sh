#!/usr/bin/env bash
# GREP_SUMMARY: deploy-modules docker system sudoers orphan context-overlay python-delegation thin-facade
# STRUCTURE: ▶ argparse → ⚡ validate → ◇ provision → ◇ spool_dirs → ▶ deploy/*.py per-module → ⊕ severity exit → ⎋ {0,1,2}
# region MODULE_CONTRACT
## @purpose  Thin shell facade — delegates all module deploy logic to Python modules in deploy/
## @scope    Called from node-lifecycle.sh --mode init/update. Arg parsing + env setup + delegation + exit
## @location core/internal/bootstrap/deploy-modules.sh — W4-E1 Strangler-Fig decomposition (1664→<100 LOC)
## @invariants Python handles: secrets, metadata, docker deploy, sudoers, orphans, context overlay. Shell handles: arg parsing, root check, NODE_YAML validation, provisioner, spool dirs, docker login, system deploy, exit aggregation.
## @rationale W4-E1 extraction. Each Python module has unit tests in tests/unit/.
# endregion MODULE_CONTRACT

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/paths.sh"
source "${SCRIPT_DIR}/../../lib/docker.sh"
source "${SCRIPT_DIR}/../../lib/logging.sh"
source "${SCRIPT_DIR}/../../lib/module-interface.sh"
__LOG_PREFIX="deploy-modules"

# ── Arg parsing ──
MODULES_FILTER=""; SKIP_PROVISION=false
while [[ $# -gt 0 ]]; do case "$1" in
    --modules) MODULES_FILTER="$2"; shift 2 ;; --skip-provision) SKIP_PROVISION=true; shift ;;
    *) break ;; esac
done
[[ "$(id -u)" -eq 0 ]] || { echo "[IMP:10][deploy-modules] ERROR: must run as root" >&2; exit 1; }
NODE_YAML="${NODE_YAML:-}"; [[ -n "$NODE_YAML" && -f "$NODE_YAML" ]] || { echo "[IMP:10][deploy-modules] ERROR: NODE_YAML not set" >&2; exit 1; }

# ── Network provision (bash — system-level, must stay) ──
if [[ "${SKIP_PROVISION}" != "true" ]]; then
    if [[ -f "${PATHS_INTERNAL_DIR}/provision-environment.sh" ]]; then
        bash "${PATHS_INTERNAL_DIR}/provision-environment.sh" --scope networks || true
        bash "${PATHS_INTERNAL_DIR}/provision-environment.sh" --scope volumes || true
    else
        docker network inspect proxy-net &>/dev/null || docker network create proxy-net --driver bridge
    fi
fi

# ── Docker login, context overlay ──
docker_login; ghcr_login
python3 "${SCRIPT_DIR}/deploy/context_overlay.py" --action ensure --node-yaml "$NODE_YAML" || true

# ── Validate secrets ──
python3 "${SCRIPT_DIR}/deploy/secrets_validator.py" --action validate-charsets \
    --secrets-manifest "${PATHS_CORE_DIR}/secrets-manifest.yaml" || exit 2

# ── Parse modules (Python) → comma-separated names for downstream ──
MODULES_RAW="$(python3 "${SCRIPT_DIR}/deploy/secrets_validator.py" --action parse-node-yaml --node-yaml "$NODE_YAML")"
[ -z "$MODULES_RAW" ] && { log_step "modules" "SKIP" "No modules declared"; exit 0; }
ALL_NAMES="$(echo "$MODULES_RAW" | cut -d: -f1 | paste -sd, -)"
ENABLED_NAMES="$(echo "$MODULES_RAW" | grep ':true:' | cut -d: -f1 | paste -sd, -)"

# ── Deploy each enabled module ──
FAILED=(); DEPLOYED=0
IFS=',' read -ra NAMES <<< "$ENABLED_NAMES"
for m_name in "${NAMES[@]}"; do
    [ -z "$m_name" ] && continue
    # Resolve context overlay
    ctx=$(grep "^context:" "$NODE_YAML" | awk '{print $2}' 2>/dev/null || echo "")
    m_overlay=""; [ -n "$ctx" ] && [ -d "/opt/${ctx}/platform/modules/${m_name}" ] && m_overlay="/opt/${ctx}/platform/modules/${m_name}"
    # Env check
    python3 "${SCRIPT_DIR}/deploy/secrets_validator.py" --action check-env --module-name "$m_name" \
        --secrets-manifest "${PATHS_CORE_DIR}/secrets-manifest.yaml" || { FAILED+=("$m_name"); continue; }
    # Test
    itype="$(python3 "${SCRIPT_DIR}/deploy/secrets_validator.py" --action detect-type --module-name "$m_name" --modules-dir "${PATHS_MODULES_DIR}")"
    if [ "$itype" = "system" ]; then
        invoke_module_interface "$m_name" install && DEPLOYED=$((DEPLOYED+1)) || FAILED+=("$m_name")
        invoke_module_interface "$m_name" healthcheck liveness 2>/dev/null || true
    else
        python3 "${SCRIPT_DIR}/deploy/docker_orchestrator.py" --action deploy --module-name "$m_name" \
            --modules-dir "${PATHS_MODULES_DIR}" && DEPLOYED=$((DEPLOYED+1)) || FAILED+=("$m_name")
    fi
done

# ── Post-deploy: sudoers + orphans (Python) ──
python3 "${SCRIPT_DIR}/deploy/sudoers_generator.py" --action batch-generate \
    --module-names "$ALL_NAMES" --modules-dir "${PATHS_MODULES_DIR}" \
    --templates-dir "${PATHS_TEMPLATES_DIR}" || true
python3 "${SCRIPT_DIR}/deploy/orphan_reconciler.py" \
    --module-entries "$ENABLED_NAMES" --modules-dir "${PATHS_MODULES_DIR}" || true

# ── Severity-based exit ──
CRIT=0; WARN=0
for fm in "${FAILED[@]}"; do
    sev="$(python3 "${SCRIPT_DIR}/deploy/secrets_validator.py" --action module-metadata --module-name "$fm" --modules-dir "${PATHS_MODULES_DIR}")"
    [ "$sev" = "critical" ] && CRIT=$((CRIT+1)) || WARN=$((WARN+1))
done
[ "$CRIT" -gt 0 ] && { log_step "main" "FAIL" "Critical:${CRIT} Warn:${WARN}"; exit 2; }
[ "$WARN" -gt 0 ] && { log_step "main" "WARN" "Warn:${WARN}"; exit 1; }
log_step "main" "DONE" "Deploy complete: ${DEPLOYED} modules"
exit 0
