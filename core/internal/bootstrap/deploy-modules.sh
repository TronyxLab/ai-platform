#!/usr/bin/env bash
# GREP_SUMMARY: deploy-modules docker system sudoers orphan context-overlay python-delegation thin-facade
# STRUCTURE: ▶ argparse → ⚡ validate → ◇ provision → ◇ spool_dirs(verify-only) → ▶ deploy/*.py per-module → ⊕ severity exit → ⎋ {0,1,2}
# region MODULE_CONTRACT
## @purpose  Thin shell facade — delegates all module deploy logic to Python modules in deploy/
## @scope    Called from node-lifecycle.sh --mode init/update. Arg parsing + env setup + delegation + exit
## @location core/internal/bootstrap/deploy-modules.sh — W4-E1 Strangler-Fig decomposition (1664→<100 LOC)
## @invariants Python handles: secrets, metadata, docker deploy, sudoers, orphans, context overlay, spool validation. Shell handles: arg parsing, root check, NODE_YAML validation, provisioner, docker login, system deploy, exit aggregation.
## @rationale W4-E1 extraction. Each Python module has unit tests in tests/unit/.
## @changes   2026-07-24 · W5.T5.2 — added IMP:7 log for sequential path (DEPLOY_PARALLEL=false)
##             W5.T5.3 — added HC_DONE_MARKER flag file after parallel deploy groups
##            2026-07-30 · DevPlan 089 T14 — added DeployOrchestrator CLI support.
##             When DEPLOY_ORCHESTRATOR=true, uses orchestrator_cli.py deploy-many
##             instead of docker_orchestrator.py for group-based deploy.
## @invariants HC_DONE_MARKER at /var/lib/platform/.bootstrap/.hc_done_in_deploy signals
##             to state_machine.py that healthcheck was already done during parallel deploy.
##             Sequential path (DEPLOY_PARALLEL != true) does NOT set this marker.
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

# ── Spool dirs verify (verify-only runtime check — restored from W4-E1 extraction) ──
python3 "${SCRIPT_DIR}/deploy/spool_validator.py" --action verify --modules-dir "${PATHS_MODULES_DIR}" || true

# ── Pre-create status-metrics.json as empty valid JSON file (P1 fix) ──
# Prevents Docker from creating it as a directory during bind mount
if [ ! -f /run/platform/status-metrics.json ]; then
    mkdir -p /run/platform
    echo '{"schema_version":2,"generated_at":null,"containers":[],"certs":[],"projects":[],"host":{}}' \
        > /run/platform/status-metrics.json
    echo "[IMP:8][deploy-modules][pre-create] Created /run/platform/status-metrics.json placeholder" >&2
fi

# ── Validate secrets ──
python3 "${SCRIPT_DIR}/deploy/secrets_validator.py" --action validate-charsets \
    --secrets-manifest "${PATHS_CORE_DIR}/secrets-manifest.yaml" || {
    echo "[IMP:8][deploy-modules][charset] WARNING: Charset validation failed — continuing with deploy" >&2
}

# ── Parse modules (Python) → comma-separated names for downstream ──
MODULES_RAW="$(python3 "${SCRIPT_DIR}/deploy/secrets_validator.py" --action parse-node-yaml --node-yaml "$NODE_YAML")"
[ -z "$MODULES_RAW" ] && { log_step "modules" "SKIP" "No modules declared"; exit 0; }
ALL_NAMES="$(echo "$MODULES_RAW" | cut -d: -f1 | paste -sd, -)"
ENABLED_NAMES="$(echo "$MODULES_RAW" | grep ':true:' | cut -d: -f1 | paste -sd, -)"

# ── T1.1+T1.2: DEPLOY_PARALLEL feature flag — topo_sort + pre-pull (Wave 1) ──
# Invariant: when DEPLOY_PARALLEL is not set or false, this entire block is skipped
# and the old sequential for-loop below runs unchanged.
TOPO_JSON=""; TOPO_GROUPS=""; TOPO_MODULES=""
if [ "${DEPLOY_PARALLEL:-false}" = "true" ]; then
    echo "[IMP:7][deploy-modules][parallel] DEPLOY_PARALLEL=true — enabling topo_sort + pre-pull + batch subprocess" >&2

    # T1.1: Call _topo_sort.py for enriched groups + modules JSON
    FILTER_NAMES="${ENABLED_NAMES//,/ }"
    TOPO_JSON="$(python3 "${SCRIPT_DIR}/_topo_sort.py" \
        --modules-dir "${PATHS_MODULES_DIR}" \
        --filter-names $FILTER_NAMES 2>/dev/null)" || TOPO_JSON=""

    if [ -n "$TOPO_JSON" ]; then
        TOPO_GROUPS="$(echo "$TOPO_JSON" | python3 "${SCRIPT_DIR}/json_field_extractor.py" --dump groups)"
        TOPO_MODULES="$(echo "$TOPO_JSON" | python3 "${SCRIPT_DIR}/json_field_extractor.py" --dump modules)"
        N_GROUPS="$(echo "$TOPO_GROUPS" | python3 "${SCRIPT_DIR}/json_field_extractor.py" --count)"
        echo "[IMP:9][deploy-modules][topo_sort] Topo-sorted into ${N_GROUPS} deploy groups" >&2
    else
        echo "[IMP:5][deploy-modules][topo_sort] WARNING: topo_sort failed — continuing without groups" >&2
    fi

    # T1.2: Pre-pull docker images (best-effort — non-fatal)
    echo "[IMP:7][deploy-modules][pre-pull] Pre-pulling images for enabled modules" >&2
    python3 "${SCRIPT_DIR}/deploy/docker_orchestrator.py" \
        --action pre-pull \
        --module-entries $FILTER_NAMES \
        --parallel-limit 4 \
        --modules-dir "${PATHS_MODULES_DIR}" || {
        echo "[IMP:8][deploy-modules][pre-pull] WARNING: Pre-pull had failures — compose up will pull images" >&2
    }

    # ── T4.2: batch-check-env (one call replaces per-module check-env calls) ──
    BATCH_ENV_RESULTS="$(python3 "${SCRIPT_DIR}/deploy/secrets_validator.py" --action batch-check-env \
        --modules-dir "${PATHS_MODULES_DIR}" \
        --secrets-manifest "${PATHS_CORE_DIR}/secrets-manifest.yaml")" || BATCH_ENV_RESULTS=""
    echo "[IMP:9][deploy-modules][batch-check-env] batch-check-env completed" >&2

    # ── W2.T2.2+W2.T2.3: Group-based deploy via deploy_docker_group + system module handling ──
    # Invariant: groups are deployed SEQUENTIALLY (respects depends_on between groups).
    # Modules WITHIN a group are deployed in PARALLEL (via os.fork in deploy_docker_group).
    # System modules (install_type != docker) are deployed sequentially via invoke_module_interface.
    FAILED=(); DEPLOYED=0

    # Extract system module names from TOPO_MODULES enriched output
    SYSTEM_NAMES="$(echo "$TOPO_MODULES" | python3 "${SCRIPT_DIR}/json_field_extractor.py" --filter install_type=system)"

    # ── DevPlan 089 T14: DeployOrchestrator CLI (when DEPLOY_ORCHESTRATOR=true) ──
    if [ "${DEPLOY_ORCHESTRATOR:-false}" = "true" ] && [ -n "$TOPO_MODULES" ]; then
        echo "[IMP:9][deploy-modules][orchestrator] Using DeployOrchestrator CLI for deploy-many" >&2
        # Build comma-separated module names
        MOD_NAMES=""
        while IFS= read -r mod_name; do
            [ -z "$mod_name" ] && continue
            [ -n "$MOD_NAMES" ] && MOD_NAMES="${MOD_NAMES},"
            MOD_NAMES="${MOD_NAMES}${mod_name}"
        done < <(echo "$TOPO_MODULES" | python3 "${SCRIPT_DIR}/json_field_extractor.py" --items 2>/dev/null)

        if [ -n "$MOD_NAMES" ]; then
            python3 -m core.internal.deploy.orchestrator_cli deploy-many \
                --projects "$MOD_NAMES" \
                --scp || {
                echo "[IMP:5][deploy-modules][orchestrator] WARNING: Orchestrator deploy-many had failures" >&2
            }
            # Skip the rest of the parallel deploy section
            HC_DONE_MARKER="/var/lib/platform/.bootstrap/.hc_done_in_deploy"
            mkdir -p "$(dirname "$HC_DONE_MARKER")"
            touch "$HC_DONE_MARKER"
        fi
    fi

    # ── Deploy docker groups SEQUENTIALLY ──
    if [ -n "$TOPO_GROUPS" ] && [ "$TOPO_GROUPS" != "[]" ] && [ "${N_GROUPS:-0}" -gt 0 ]; then
        echo "[IMP:7][deploy-modules][groups] Deploying ${N_GROUPS} docker group(s) sequentially" >&2
        # Resolve context once for overlay path
        ctx=$(grep "^context:" "$NODE_YAML" | awk '{print $2}' 2>/dev/null || echo "")
        for (( g_idx=0; g_idx<N_GROUPS; g_idx++ )); do
            # Extract group as JSON array
            group_json="$(echo "$TOPO_GROUPS" | python3 "${SCRIPT_DIR}/json_field_extractor.py" --index "${g_idx}")"
            [ -z "$group_json" ] || [ "$group_json" = "[]" ] && continue

            # Build module:overlay entries for this group
            group_entries=""
            while IFS= read -r mod_name; do
                [ -z "$mod_name" ] && continue
                m_overlay=""
                [ -n "$ctx" ] && [ -d "/opt/${ctx}/platform/modules/${mod_name}" ] && m_overlay="/opt/${ctx}/platform/modules/${mod_name}"
                entry="$mod_name"
                [ -n "$m_overlay" ] && entry="${mod_name}:${m_overlay}"
                group_entries="$group_entries $entry"
            done < <(echo "$group_json" | python3 "${SCRIPT_DIR}/json_field_extractor.py" --items 2>/dev/null)

            echo "[IMP:8][deploy-modules][group] Deploying group ${g_idx}/$((N_GROUPS-1)): ${group_entries}" >&2
            python3 "${SCRIPT_DIR}/deploy/docker_orchestrator.py" \
                --action deploy-group \
                --module-entries $group_entries \
                --modules-dir "${PATHS_MODULES_DIR}" || {
                echo "[IMP:5][deploy-modules][group] Group ${g_idx} deploy had failures — continuing with next group" >&2
            }
        done
    else
        echo "[IMP:5][deploy-modules][groups] No docker groups from topo_sort — skipping group-based deploy" >&2
    fi

    # ── W2.T2.3: Handle system modules separately (sequential via invoke_module_interface) ──
    if [ -n "$SYSTEM_NAMES" ]; then
        echo "[IMP:7][deploy-modules][system] Deploying system modules sequentially: ${SYSTEM_NAMES}" >&2
        IFS=',' read -ra SYS_NAMES <<< "$SYSTEM_NAMES"
        for m_name in "${SYS_NAMES[@]}"; do
            [ -z "$m_name" ] && continue
            echo "[IMP:8][deploy-modules][system] Deploying system module: ${m_name}" >&2
            invoke_module_interface "$m_name" install || FAILED+=("$m_name")
            invoke_module_interface "$m_name" healthcheck liveness 2>/dev/null || true
        done
    fi

    # ── T5.3: Set marker for healthcheck-done-during-deploy ──
    # When DEPLOY_PARALLEL is enabled, deploy_docker_group() runs healthcheck
    # inside each group. This marker tells state_machine.py to skip the
    # standalone healthcheck step to avoid duplication.
    HC_DONE_MARKER="/var/lib/platform/.bootstrap/.hc_done_in_deploy"
    mkdir -p "$(dirname "$HC_DONE_MARKER")"
    touch "$HC_DONE_MARKER"
    echo "[IMP:9][deploy-modules][hc_marker] Created ${HC_DONE_MARKER} — standalone healthcheck will be skipped" >&2
fi

# ── Render litellm-config.yml before deploying modules ──
echo "[IMP:7][deploy-modules] Rendering litellm-config.yml from policy.yaml..." >&2
python3 "${PATHS_CORE_DIR}/internal/llm/config_renderer.py" \
    --output "${PATHS_CORE_DIR}/modules/litellm/config/litellm-config.yml" || {
    echo "[IMP:8][deploy-modules][render] WARNING: litellm-config render failed — continuing with existing config" >&2
}

# ── Deploy each enabled module ──
# Sequential path (legacy — unchanged): only runs when parallel mode is NOT active or topo_sort failed
if [ "${DEPLOY_PARALLEL:-false}" != "true" ] || [ -z "$TOPO_MODULES" ]; then
    echo "[IMP:7][deploy-modules][sequential] DEPLOY_PARALLEL=false — using sequential for-loop (legacy path)" >&2
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
fi

# ⚠️ TRAP[CROSS-LAYER] provision-llm.sh call REMOVED — internal/ must not call entrypoints/
# Provisioning happens in state_machine.py (post-deploy lifecycle step), not here.
# ── Post-deploy: sudoers + orphans (Python) ──
python3 "${SCRIPT_DIR}/deploy/sudoers_generator.py" --action batch-generate \
    --module-names "$ALL_NAMES" --modules-dir "${PATHS_MODULES_DIR}" \
    --templates-dir "${PATHS_TEMPLATES_DIR}" || true
python3 "${SCRIPT_DIR}/deploy/orphan_reconciler.py" \
    --module-entries "$ENABLED_NAMES" --modules-dir "${PATHS_MODULES_DIR}" || true

# ── Severity-based exit ──
CRIT=0; WARN=0
for fm in "${FAILED[@]}"; do
    if [ -n "$TOPO_MODULES" ]; then
        # T1.3: Use enriched modules dict from _topo_sort.py (no per-module Python call)
        sev="$(echo "$TOPO_MODULES" | python3 "${SCRIPT_DIR}/json_field_extractor.py" --default warn "${fm}.severity")"
    else
        # Legacy: per-module metadata call (sequential code path, or fallback if topo_sort failed)
        sev="$(python3 "${SCRIPT_DIR}/deploy/secrets_validator.py" --action module-metadata --module-name "$fm" --modules-dir "${PATHS_MODULES_DIR}")"
    fi
    [ "$sev" = "critical" ] && CRIT=$((CRIT+1)) || WARN=$((WARN+1))
done
[ "$CRIT" -gt 0 ] && { log_step "main" "FAIL" "Critical:${CRIT} Warn:${WARN}"; exit 2; }
if [ "$WARN" -gt 0 ]; then
    log_step "main" "WARN" "Warn:${WARN} (non-critical — continuing)"
fi
log_step "main" "DONE" "Deploy complete: ${DEPLOYED} modules (warnings: ${WARN})"
exit 0
