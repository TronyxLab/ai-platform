#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint converge reconcile remote-cmd auto-detect-node dry-run ssh-proxy
# STRUCTURE: ▶ init ┌parse --node --dry-run┐ → ◇ --node? → ┌auto_detect_node_name┐ → ⚡ execute_remote_converge() → ◇ RC=2? → └─ exec local converge.sh ─┘ → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make converge`: parses --node/--dry-run, delegates
##           to execute_remote_converge() in remote-cmd.sh for SSH proxy, or falls back
##           to local exec of core/internal/bootstrap/converge.sh when no SSH host.
## @scope    Called ONLY from Makefile.
##           Owns: usage, auto_detect_node_name, main.
## @invariants
##   - --node is recommended; if missing → auto_detect_node_name() fallback
##   - --dry-run prints SSH command or local args without executing
##   - SSH proxy logic lives entirely in remote-cmd.sh (execute_remote_converge)
##   - Without SSH_HOST: local exec (backward compatible)
##   - No AGE key handling (converge R-units don't decrypt secrets)
## @rationale Thin-wrapper per canonical operations table (core/AGENTS.md).
##            Mirrors node-update.sh pattern for SSH proxy dispatch (DevPlan 020).
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${CORE_DIR}/lib/paths.sh"
source "${CORE_DIR}/internal/bootstrap/remote-cmd.sh"

NODE_NAME=""
DRY_RUN=false
PASSTHROUGH_ARGS=()

# ═══════════════════════════════════════════════════════════════════
# region FUNC_usage
## @purpose  Print usage instructions and exit 0
usage() {
    cat <<'EOF'
Usage: converge.sh --node <name> [--dry-run] [--report-only]

Idempotent desired-state reconciler for platform VPS.

Required:
  --node <name>              Node name to reconcile (or auto-detect)

Optional:
  --dry-run                  Print planned mutations without executing
  --report-only              Check-only JSON drift report (passthrough)
  --help, -h                 Print this help

Exit codes:
  0 — fully converged (no drifts)
  1 — mutations applied
  2 — one or more R-units failed

Examples:
  converge.sh --node tronyx-vps
  converge.sh --node tronyx-vps --dry-run
  converge.sh --node tronyx-vps --report-only
EOF
    exit 0
}
# endregion FUNC_usage

# ═══════════════════════════════════════════════════════════════════
# region FUNC_auto_detect_node_name
## @purpose  Fallback when --node not provided: detect single node from
##           /opt/node-configs/ directories.
## @stdout   Node name on success
## @return 0  Node detected
## @return 1  No unique node found
auto_detect_node_name() {
    local d="/opt/node-configs"
    [[ -d "$d" ]] || {
        echo "[IMP:8][converge][detect] ${d} does not exist" >&2
        return 1
    }
    local candidates=() dir
    for dir in "$d"/*/; do
        [[ -d "$dir" ]] || continue
        local b; b="$(basename "$dir")"
        [[ "$b" == "scripts" || "$b" == "secrets" ]] && continue
        candidates+=("$b")
    done
    [[ ${#candidates[@]} -eq 0 ]] && {
        echo "[IMP:10][converge][detect] No node directories found" >&2
        return 1
    }
    [[ ${#candidates[@]} -gt 1 ]] && {
        echo "[IMP:10][converge][detect] Multiple nodes: ${candidates[*]} — use --node <name>" >&2
        return 1
    }
    echo "${candidates[0]}"
    return 0
}
# endregion FUNC_auto_detect_node_name

# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Parse CLI args, auto-detect node, delegate to SSH proxy or local exec
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --node|--node-name) NODE_NAME="$2"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            --help|-h) usage ;;
            *) PASSTHROUGH_ARGS+=("$1"); shift ;;
        esac
    done

    # ── Auto-detect if --node not provided ──
    if [[ -z "${NODE_NAME}" ]]; then
        echo "[IMP:9][converge][entrypoint] --node not provided — attempting auto-detect" >&2
        NODE_NAME="$(auto_detect_node_name)" || {
            echo "[IMP:10][converge][entrypoint] FATAL: --node is required" >&2
            echo "  Usage: converge.sh --node <name> [--dry-run]" >&2
            exit 1
        }
        echo "[IMP:9][converge][entrypoint] Auto-detected NODE=${NODE_NAME}" >&2
    fi

    echo "[IMP:9][converge][entrypoint] Starting converge for NODE=${NODE_NAME}" >&2

    # ── SSH proxy (preferred) ──
    execute_remote_converge "${NODE_NAME}" "${PASSTHROUGH_ARGS[@]}"
    local remote_rc=$?

    # ── Local exec fallback (no SSH host) ──
    if [[ $remote_rc -eq 2 ]]; then
        echo "[IMP:9][converge][entrypoint] No SSH host — executing converge.sh LOCALLY" >&2
        local internal="${PATHS_INTERNAL_DIR}/bootstrap/converge.sh"
        if [[ ! -f "$internal" ]]; then
            echo "[IMP:10][converge][entrypoint] FATAL: Internal script not found at ${internal}" >&2
            exit 1
        fi
        local args=("--node" "${NODE_NAME}")
        $DRY_RUN && args+=("--dry-run")
        args+=("${PASSTHROUGH_ARGS[@]}")
        if $DRY_RUN; then
            echo "[IMP:8][converge][dry-run] DRY-RUN: bash ${internal} ${args[*]}" >&2
            echo "[IMP:9][converge][dry-run] DRY-RUN complete" >&2
            exit 0
        fi
        echo "[IMP:8][converge][entrypoint] Delegating to ${internal}" >&2
        exec bash "${internal}" "${args[@]}"
    fi
}
# endregion FUNC_main

main "$@"
