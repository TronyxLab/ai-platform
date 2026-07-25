#!/usr/bin/env bash
# GREP_SUMMARY: monitoring hook on-project-deploy thin-wrapper config-renderer
# STRUCTURE: parse_args(PROJECT_DIR,PROJECT,NODE_NAME) → python3 monitoring_config_renderer.py → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin wrapper for monitoring post-deploy hook — delegates all logic to
##           core/internal/monitoring_config_renderer.py (Python).
## @scope    Invoked by deploy-project.sh after successful project deploy;
##           receives PROJECT_DIR, PROJECT, NODE_NAME as positional args.
## @invariants
##   - PROJECT_DIR and PROJECT are required; missing → exit 0 (backward compat)
##   - NODE_NAME is optional (defaults to empty string)
##   - All monitoring logic is in the Python module — this is just a dispatch wrapper
##   - PLATFORM_ROOT resolved from script location (../../../..)
##   - Python's exit code is propagated
## @rationale Strangler-Fig extraction: 413 LOC shell → 30 LOC wrapper + 400 LOC Python.
##            Eliminates 19 inline python3 calls. See DevPlan 074.
## @changes
##   LAST_CHANGE: 2026-07-25 | Rewritten as thin wrapper (DevPlan 074 TASK-3)
# endregion MODULE_CONTRACT

set -euo pipefail

__LOG_PREFIX="monitoring-hook"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOOK_PROJECT_DIR="${1:-}"
HOOK_PROJECT="${2:-}"
HOOK_NODE_NAME="${3:-}"

if [[ -z "$HOOK_PROJECT_DIR" || -z "$HOOK_PROJECT" ]]; then
    echo "[IMP:6][monitoring][hook] Missing PROJECT_DIR or PROJECT — skipping monitoring hook" >&2
    exit 0
fi

PLATFORM_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

echo "[IMP:9][monitoring][hook] === monitoring on-project-deploy START: ${HOOK_PROJECT} ===" >&2

python3 "${PLATFORM_ROOT}/core/internal/monitoring_config_renderer.py" \
    --project-dir "$HOOK_PROJECT_DIR" \
    --project "$HOOK_PROJECT" \
    --node "$HOOK_NODE_NAME"

echo "[IMP:9][monitoring][hook] === monitoring on-project-deploy DONE: ${HOOK_PROJECT} ===" >&2
