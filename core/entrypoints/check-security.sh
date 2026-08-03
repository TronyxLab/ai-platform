#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint check-security posture remote-cmd auto-detect-node dry-run json local-fallback ssh-proxy DevPlan-134
# STRUCTURE: ▶ init ┌parse --node --dry-run --json┐ → ◇ --node? → ┌python3 -m node_detect --detect-node-name┐ → ⚡ execute_remote_check_security() → ◇ RC=2? → └─ exec local security_posture.py ─┘ → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make check-security` (DevPlan 134 L2): parses --node/--dry-run/--json,
##           delegates to execute_remote_check_security() in remote-cmd.sh for SSH proxy, or falls back
##           to local exec of core/internal/bootstrap/security_posture.py when no SSH host.
## @scope    Called ONLY from Makefile. Owns: usage, main.
## @invariants
##   - --node is recommended; if missing → python3 -m core.internal.shared.node_detect fallback
##   - --dry-run prints SSH command without executing
##   - --json passthrough — JSON-отчёт security_posture.py (L5-мониторинг)
##   - SSH proxy logic lives entirely in remote-cmd.sh (execute_remote_check_security)
##   - Without SSH_HOST: local exec (backward compatible, rc=2 signal)
##   - Exit codes: 0=healthy 1=warnings 2=errors (НЕ маскируются — это check, не reconcile)
## @rationale Thin-wrapper per canonical operations table (core/AGENTS.md). Mirrors converge.sh
##            SSH proxy dispatch pattern (DevPlan 020) + TRAP[BUG] 2026-08-03 (|| rc=$? под set -e).
## @changes 2026-08-04 | DevPlan 134 W2 — Created
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${CORE_DIR}/lib/paths.sh"
source "${CORE_DIR}/internal/bootstrap/remote-cmd.sh"
source "${CORE_DIR}/lib/args.sh"

NODE_NAME=""
DRY_RUN=false
PASSTHROUGH_ARGS=()

USAGE_SCRIPT="check-security.sh"
USAGE_DESC="Security posture check for platform VPS (S1-S7, DevPlan 134)."
USAGE_OPTIONS=(
    "--node <name>              Node name to check (or auto-detect)"
    "--dry-run                  Print SSH command without executing"
    "--json                     Emit JSON report (L5 monitoring)"
)

# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Parse CLI args, auto-detect node, delegate to SSH proxy or local exec
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --node|--node-name) NODE_NAME="$2"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            --json) PASSTHROUGH_ARGS+=("--json"); shift ;;
            --help|-h) usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" ;;
            *) PASSTHROUGH_ARGS+=("$1"); shift ;;
        esac
    done

    # ── Auto-detect if --node not provided ──
    if [[ -z "${NODE_NAME}" ]]; then
        echo "[IMP:9][check-security][entrypoint] --node not provided — attempting auto-detect" >&2
        NODE_NAME="$(python3 -m core.internal.shared.node_detect --detect-node-name 2>/dev/null)" || {
            echo "[IMP:10][check-security][entrypoint] FATAL: --node is required" >&2
            echo "  Usage: check-security.sh --node <name> [--dry-run] [--json]" >&2
            exit 1
        }
        echo "[IMP:9][check-security][entrypoint] Auto-detected NODE=${NODE_NAME}" >&2
    fi

    echo "[IMP:9][check-security][entrypoint] Starting security check for NODE=${NODE_NAME}" >&2

    # ── SSH proxy (preferred) ──
    # ⚠️ set -e убивал скрипт на rc=2 (local fallback-сигнал) — идиома `|| rc=$?` (TRAP 2026-08-03 converge)
    local remote_rc=0
    execute_remote_check_security "${NODE_NAME}" "${PASSTHROUGH_ARGS[@]}" || remote_rc=$?

    # ── Local exec fallback (no SSH host) ──
    if [[ $remote_rc -eq 2 ]]; then
        echo "[IMP:9][check-security][entrypoint] No SSH host — executing security_posture.py LOCALLY" >&2
        local internal="${PATHS_INTERNAL_DIR}/bootstrap/security_posture.py"
        if [[ ! -f "$internal" ]]; then
            echo "[IMP:10][check-security][entrypoint] FATAL: Internal script not found at ${internal}" >&2
            exit 1
        fi
        export PYTHONPATH="${CORE_DIR}/..:${PYTHONPATH:-}"
        local args=("--node" "${NODE_NAME}")
        args+=("${PASSTHROUGH_ARGS[@]}")
        echo "[IMP:8][check-security][entrypoint] Delegating to ${internal}" >&2
        exec python3 "${internal}" "${args[@]}"
    fi
    exit $remote_rc
}
# endregion FUNC_main

main "$@"
