#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint node-update update lifecycle provision deploy-modules healthcheck verify-core
# STRUCTURE: ▶ init → ◇ --node? → ◇ --dry-run? → ⚡ exec internal/bootstrap/node-lifecycle.sh --mode update → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Thin-wrapper entrypoint for `make node-update`: validates NODE parameter,
##           optionally runs in dry-run mode, delegates to internal/bootstrap/node-lifecycle.sh --mode update
## @scope    Called only from Makefile (`make node-update NODE=<name>`). Runs entirely on the
##           VPS (after core delivery via rsync). Thin-wrapper gate: ≤4 functions, ≤150 LOC.
## @invariants
##   - --node is REQUIRED; missing → usage error, exit 1
##   - --dry-run prints what would be done without executing
##   - Delegates all logic to internal/bootstrap/node-lifecycle.sh --mode update
##   - sources paths.sh for PLATFORM_ROOT, PATHS_CORE_DIR
##   - Must NOT contain direct rsync/ssh/scp/ssh-keygen calls
## @rationale Thin-wrapper per DevPlan 020 T17. Separates CLI interface from update logic.
##            The internal script runs the 5-step update flow (verify_core → provision →
##            deploy-modules docker → deploy-modules system → healthcheck).
## @changes 2026-07-17 | T17 — New entrypoint for node-update lifecycle verb
##           2026-07-17 | Lifecycle refactoring: delegates to node-lifecycle.sh --mode update
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../lib/paths.sh
source "${CORE_DIR}/lib/paths.sh"

NODE_NAME=""
DRY_RUN=false

# ═══════════════════════════════════════════════════════════════════
# region FUNC_usage
## @purpose  Print usage instructions and exit 0
## @io       stdout: usage text
## @complexity O(1)
usage() {
    cat <<'EOF'
Usage: node-update.sh --node <name> [--dry-run]

Update an already-provisioned node (regular CI lifecycle, not INIT).
Runs 5-step flow: verify_core → provision --scope networks --scope volumes → deploy docker modules
→ deploy system modules → healthcheck.

Required:
  --node <name>    Node name to update

Optional:
  --dry-run        Show actions without executing

Environment:
  PLATFORM_ROOT    Base platform directory (default: /opt/platform)

Examples:
  node-update.sh --node tronyx-vps
  node-update.sh --node tronyx-vps --dry-run
EOF
    exit 0
}
# endregion FUNC_usage

# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Parse CLI args and delegate to internal node-update script
## @param $@  --node NAME, --dry-run
## @io       stderr: LDD logs at IMP:8-9
##           exit 0 on success, 1 on missing --node
## @complexity O(1) — argument parsing + delegation
## @invariants
##   - --node is required; missing → exit 1 via usage error
##   - --dry-run is passed through to internal script
##   - All other args are forwarded as passthrough
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --node|--node-name) NODE_NAME="$2"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            --help|-h) usage ;;
            *) echo "[IMP:9][node-update][entrypoint] ERROR: Unknown option '$1'" >&2
               echo "  Usage: node-update.sh --node <name> [--dry-run]" >&2
               exit 1 ;;
        esac
    done

    if [[ -z "${NODE_NAME}" ]]; then
        echo "[IMP:10][node-update][entrypoint] FATAL: --node is required" >&2
        echo "  Usage: node-update.sh --node <name> [--dry-run]" >&2
        exit 1
    fi

    echo "[IMP:9][node-update][entrypoint] Starting node-update for NODE=${NODE_NAME}" >&2

    local internal="${PATHS_INTERNAL_DIR}/bootstrap/node-lifecycle.sh"
    if [[ ! -f "$internal" ]]; then
        echo "[IMP:10][node-update][entrypoint] FATAL: Internal script not found at ${internal}" >&2
        exit 1
    fi

    local args=("--node" "${NODE_NAME}")
    $DRY_RUN && args+=("--dry-run")

    echo "[IMP:8][node-update][entrypoint] Delegating to internal/bootstrap/node-lifecycle.sh --mode update" >&2
    exec bash "$internal" "--mode" "update" "${args[@]}"
}
# endregion FUNC_main

main "$@"
