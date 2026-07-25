#!/usr/bin/env bash
# GREP_SUMMARY: reconcile-projects launcher python3 reconciler_projects.py converge bootstrap
# STRUCTURE: ▶ source guard → ▶ parse args → ▶ python3 reconciler_projects.py "$@" → ⊕ rc → ⎋ return
# region MODULE_CONTRACT
## @purpose  Thin shell wrapper for reconciler_projects.py — preserves backward compatibility
##           for sourcing from converge.sh. All business logic in Python.
## @scope    <30 LOC — delegates to Python module. Sourced from converge.sh.
## @invariants
##   - Zero business logic — pure delegation
##   - Zero inline python3 -c or heredoc calls
##   - Defines reconcile_projects() bash function (backward compat with converge.sh source pattern)
##   - Direct invocation guard preserved
## @rationale Shell wrapper exists because converge.sh sources this file and calls
##            reconcile_projects() as a bash function. Python module handles all logic.
## @changes 2026-07-25 | Migrated to Python (DevPlan 076) — shell reduced to <30 LOC
# endregion MODULE_CONTRACT

set -euo pipefail

# region FUNC_reconcile_projects
reconcile_projects() {
    # ⚠️ TRAP[BUSINESS] · 2026-07-25 · HI · exec NOT used — sourced from converge.sh
    # · Root: exec replaces the parent process — would kill converge.sh after reconcile
    # · Fix: python3 + local rc=$?; return $rc — preserves converge.sh execution
    # · Prevention: Never use exec in a sourced function
    local node_name="$1"
    local node_yaml="$2"
    local dry_run="${3:-false}"
    local node_host_map="${4:-${NODE_HOST_MAP:-}}"
    local core_dir
    core_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

    python3 "${core_dir}/internal/reconciler_projects.py" \
        --node "${node_name}" \
        --node-yaml "${node_yaml}" \
        --node-host-map "${node_host_map}" \
        $([[ "${dry_run}" == "true" ]] && echo "--dry-run")
    local rc=$?
    return $rc
}
# endregion FUNC_reconcile_projects

# Direct invocation guard
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "[IMP:10][reconcile] FATAL: This script is NOT an entrypoint — source it from converge.sh or node-lifecycle.sh" >&2
    echo "[IMP:10][reconcile] Usage: source reconcile-projects.sh && reconcile_projects <node> <node_yaml> [dry_run]" >&2
    exit 1
fi
