#!/usr/bin/env bash
# GREP_SUMMARY: deploy-modules docker system sudoers orphan context-overlay python-delegation thin-facade
# STRUCTURE: ▶ argparse → ⚡ validate(root+NODE_YAML) → ◇ provision → ◇ docker_login → ⚡ exec python3 deploy_orchestrator.py → ⎋ {0,1,2}
# region MODULE_CONTRACT
## @purpose  Thin shell facade (≤50 LOC) — ALL module deploy logic (routing, deploy, sudoers, orphans, severity) → deploy/deploy_orchestrator.py (DevPlan 100)
## @scope    node-lifecycle.sh --mode init/update: arg parsing, root/NODE_YAML check, provisioner, docker login, exec python3 orchestrator
## @invariants Shell: args/root/NODE_YAML/provision/docker-login. Python: routing/deploy/sudoers/orphans/severity.
##   - exec python3 replaces shell (same PID) — exit {0,1,2} auto-propagates (D2); docker_login writes ~/.docker/config.json (R2)
##   - PYTHONPATH exported for core.* imports (converge.sh:64 pattern); set -euo pipefail preserved
## @changes   2026-07-31 · DevPlan 100 TASK-2 — routing+severity extracted to deploy_orchestrator.py (260→≤50 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/paths.sh"
source "${SCRIPT_DIR}/../../lib/docker.sh"
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

docker_login; ghcr_login

# ⚠️ TRAP[CROSS-LAYER] provision-llm.sh call REMOVED — internal/ must not call entrypoints/
# Provisioning happens in state_machine.py (post-deploy lifecycle step), not here.
# ── Exec Python orchestrator (DevPlan 100): routing + deploy + severity in Python ──
export PYTHONPATH="${SCRIPT_DIR}/../../..:${PYTHONPATH:-}"
exec python3 "${SCRIPT_DIR}/deploy/deploy_orchestrator.py" \
    --node-yaml "$NODE_YAML" \
    --modules-dir "$PATHS_MODULES_DIR" \
    --core-dir "$PATHS_CORE_DIR" \
    --templates-dir "$PATHS_TEMPLATES_DIR" \
    --modules-filter "$MODULES_FILTER" \
    --deploy-parallel "${DEPLOY_PARALLEL:-false}" --deploy-orchestrator "${DEPLOY_ORCHESTRATOR:-false}"
