#!/usr/bin/env bash
# GREP_SUMMARY: converge reconciler reconcile-perms reconcile-audit-log reconcile-projects reconcile-networks detect-hosts-drift verify-vhosts unit-filter idempotent drift-detection desired-state
# STRUCTURE: ▶ argparse ┌--node --dry-run --report-only --units --reconcile┐ → ⚡ lock → ▶ reconciler.py R1-R6 ─exit→ ⊕ aggregate {0,1,2} → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin shell facade for idempotent desired-state reconciler (W4-E3). Delegates all R1-R6 convergence logic to reconciler.py.
## @scope    Arg parsing → env setup → flock → reconciler.py dispatch → --reconcile (reconcile-projects.sh) → exit code mapping {0,1,2}
## @location core/internal/bootstrap/converge.sh
## @invariants  All R1-R6 delegated to converge/reconciler.py; Exit: 0=converged 1=warnings 2=errors; flock /var/lock/platform-converge.lock; --reconcile sources reconcile-projects.sh for stub→deployed recovery
## @rationale  W4-E3 Strangler Fig: 1149→<150 LOC. Shell retains orchestration (env, lock, reconcile flag); reconciler.py handles all convergence logic with typed Python contracts.
## @changes 2026-07-22 | W4-E3: Extracted R1-R6 to reconciler.py; shell reduced to thin facade
## ⚠️ TRAP[DECISION] · 2026-07-22 · HI · W4-E3 extraction: shell kept for flock + reconcile-projects.sh orchestration only
## · Rejected: Full Python rewrite including flock (risk: losing POSIX flock semantics on the node)
## · Reason: flock is per-process (fd-level) — Python implementation would require different mechanism. Shell retains lock orchestration.
## · Rev: if reconciler.py ever needs its own lock, implement at Python level with fcntl.flock.
# endregion MODULE_CONTRACT
set -euo pipefail
CONVERGE_NODE=""; CONVERGE_DRY_RUN=false; CONVERGE_REPORT_ONLY=false; CONVERGE_UNITS=""; CONVERGE_RECONCILE=false
LOCK_FILE="/var/lock/platform-converge.lock"; CORE_DIR=""; NODE_YAML_PATH=""

# region FUNC_unit_enabled
## @purpose  Check --units filter membership. Empty filter = all enabled. Kept for backward compat (sourced callers).
## @param $1  Unit name (e.g. "R1", "R3")
## @return   0 if enabled, 1 if filtered out
_unit_enabled() {
    local unit_name="$1"
    [[ -z "${CONVERGE_UNITS}" ]] && return 0
    local -a unit_list; IFS=',' read -ra unit_list <<< "${CONVERGE_UNITS}"
    for u in "${unit_list[@]}"; do
        u="$(echo "${u}" | xargs)"
        [[ "${u}" == "${unit_name}" ]] && return 0
    done
    return 1
}
# endregion FUNC_unit_enabled

# region FUNC_usage
## @purpose  Print usage and exit
usage() { cat <<'EOF'
Usage: converge.sh --node <name> [--dry-run] [--report-only] [--reconcile] [--units <R..>]
Idempotent desired-state reconciler for platform VPS (thin facade → reconciler.py).
Required: --node <name>  Optional: --dry-run --report-only --reconcile --units --help
Exit codes: 0=converged 1=warnings 2=errors
EOF
    exit 0; }
# endregion FUNC_usage

# region FUNC_setup_environment
## @purpose  Resolve paths, source libs, validate node.yaml exists
## @globals  CORE_DIR, NODE_YAML_PATH
## @exit 2   If node.yaml not found
setup_environment() {
    local script_dir; script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    CORE_DIR="$(cd "${script_dir}/../.." && pwd)"
    source "${CORE_DIR}/lib/paths.sh"
    source "${CORE_DIR}/lib/node-resolver.sh"
    __LOG_PREFIX="converge"; source "${CORE_DIR}/lib/logging.sh"
    NODE_YAML_PATH="$(resolve_node_yaml "${CONVERGE_NODE}" "${PLATFORM_ROOT}" "${HOME}/projects")" || {
        echo "[IMP:10][converge][setup] FATAL: Cannot resolve node.yaml for node=${CONVERGE_NODE}" >&2; exit 2; }
    if [[ ! -f "${NODE_YAML_PATH}" ]]; then
        echo "[IMP:10][converge][setup] FATAL: node.yaml not found at ${NODE_YAML_PATH}" >&2; exit 2; fi
    echo "[IMP:8][converge][setup] Node: ${CONVERGE_NODE} node.yaml: ${NODE_YAML_PATH}" >&2
}
# endregion FUNC_setup_environment

# region FUNC_acquire_lock
## @purpose  Acquire flock to prevent concurrent converge/node-update runs
## @exit 3   If lock cannot be acquired (already running)
acquire_lock() {
    if [[ "${CONVERGE_DRY_RUN}" == "true" ]] || [[ "${CONVERGE_REPORT_ONLY}" == "true" ]]; then
        echo "[IMP:7][converge][lock] SKIP: flock not needed in dry-run/report-only mode" >&2; return 0; fi
    if ! command -v flock &>/dev/null; then
        echo "[IMP:7][converge][lock] WARN: flock not available — skipping (non-Linux)" >&2; return 0; fi
    if ! mkdir -p "$(dirname "${LOCK_FILE}")" 2>/dev/null; then LOCK_FILE="/tmp/platform-converge.lock"; fi
    exec 200>"${LOCK_FILE}"
    if ! flock -n 200; then
        echo "[IMP:10][converge][lock] FATAL: Another converge or node-update is already running" >&2; exit 3; fi
    echo "[IMP:7][converge][lock] Acquired exclusive lock: ${LOCK_FILE}" >&2
}
# endregion FUNC_acquire_lock

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Entry point: parse args → setup env → lock → reconciler.py → --reconcile → exit
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --node) CONVERGE_NODE="$2"; shift 2 ;;
            --dry-run) CONVERGE_DRY_RUN=true; shift ;;
            --report-only) CONVERGE_REPORT_ONLY=true; shift ;;
            --units) CONVERGE_UNITS="$2"; shift 2 ;;
            --reconcile) CONVERGE_RECONCILE=true; shift ;;
            --help|-h) usage ;;
            *) echo "[IMP:10][converge][args] ERROR: Unknown argument: $1" >&2; usage ;;
        esac
    done
    [[ -z "${CONVERGE_NODE}" ]] && { echo "[IMP:10][converge][args] FATAL: --node is required" >&2; usage; }
    setup_environment
    acquire_lock
    echo "[IMP:9][converge][main] ==============================" >&2
    echo "[IMP:9][converge][main] Platform Converge START — Node: ${CONVERGE_NODE} — Mode: $(if [[ "${CONVERGE_DRY_RUN}" == "true" ]]; then echo DRY-RUN; elif [[ "${CONVERGE_REPORT_ONLY}" == "true" ]]; then echo REPORT-ONLY; else echo CONVERGE; fi) — node.yaml: ${NODE_YAML_PATH}" >&2
    echo "[IMP:9][converge][main] ==============================" >&2
    # ── Dispatch R1-R6 to reconciler.py ──
    local script_dir recon_rc=0
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    echo "[IMP:9][converge][main] Dispatching to reconciler.py..." >&2
    local -a recon_cmd=("python3" "${script_dir}/converge/reconciler.py" "--node-yaml" "${NODE_YAML_PATH}" "--node-name" "${CONVERGE_NODE}" "--core-dir" "${CORE_DIR}")
    [[ "${CONVERGE_DRY_RUN}" == "true" ]] && recon_cmd+=("--dry-run")
    [[ "${CONVERGE_REPORT_ONLY}" == "true" ]] && recon_cmd+=("--report-only")
    [[ -n "${CONVERGE_UNITS}" ]] && recon_cmd+=("--units" "${CONVERGE_UNITS}")
    "${recon_cmd[@]}" || recon_rc=$?
    # ── Optional: --reconcile stub → deployed (W4) ──
    if [[ "${CONVERGE_RECONCILE}" == "true" ]]; then
        local reconcile_script="${CORE_DIR}/internal/deploy/reconcile-projects.sh"
        if [[ -f "$reconcile_script" ]]; then
            source "$reconcile_script"
            reconcile_projects "${CONVERGE_NODE}" "${NODE_YAML_PATH}" "${CONVERGE_DRY_RUN}" || {
                echo "[IMP:10][converge][main] Reconcile step failed" >&2; [[ 2 -gt $recon_rc ]] && recon_rc=2; }
        else
            echo "[IMP:8][converge][main] WARN: reconcile-projects.sh not found" >&2
        fi
    fi
    # ── Final summary and exit ──
    echo "[IMP:9][converge][main] ==============================" >&2
    if [[ $recon_rc -eq 2 ]]; then
        echo "[IMP:9][converge][main] ERRORS DETECTED — some R-units failed (exit 2)" >&2
    elif [[ $recon_rc -eq 1 ]]; then
        echo "[IMP:9][converge][main] WARNINGS DETECTED — non-critical drift (exit 1)" >&2
    else
        echo "[IMP:9][converge][main] FULLY CONVERGED — all R-units converged (exit 0)" >&2
    fi
    echo "[IMP:9][converge][main] ==============================" >&2
    exit $recon_rc
}
# endregion FUNC_main
main "$@"
