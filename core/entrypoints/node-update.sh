#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint node-update update lifecycle remote-cmd age-key dry-run
# STRUCTURE: ▶ init → ◇ --node? → ◇ --dry-run? → ┌detect_age_key + execute_remote_update┐ → ◇ RC=2? → └─ ⚡ exec local node-lifecycle.sh --mode update ─┘ → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Thin entrypoint for `make node-update`: parses args, detects AGE key,
##           delegates to execute_remote_update() in remote-cmd.sh, or falls back to
##           local exec of node-lifecycle.sh --mode update.
## @invariants
##   - --node is REQUIRED; missing → usage error, exit 1
##   - --dry-run prints command without executing
##   - --reconcile: passthrough flag — after converge, reconcile stub projects (DevPlan 025 W4)
##   - SSH proxy logic lives entirely in remote-cmd.sh (execute_remote_update, deliver_vhost_overlays)
##   - Without SSH_HOST: local exec (backward compatible)
##   - S2 (DevPlan 019): generated vhost overlays are delivered via remote-cmd.sh
## @rationale Thin-wrapper per DevPlan 020. SSH proxy extracted to remote-cmd.sh.
## @changes 2026-07-21 | +--reconcile passthrough (DevPlan 025 W4)
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

USAGE_SCRIPT="node-update.sh"
USAGE_DESC="Update an already-provisioned node (regular CI lifecycle, not INIT)."
USAGE_OPTIONS=(
    "--node <name>              Node name to update"
    "--dry-run                  Show SSH command or local args without executing"
    "--age-secret-key-file <f>  Path to AGE secret key file"
)

# 🧐 TRAP[DECISION] · 2026-07-21 · — · node-update.sh passthrough arg pattern
# · Rejected: full parse_args adoption (passthrough pattern incompatible)
# · Reason: minimal W1 scope, forwards unknown args via PASSTHROUGH_ARGS
# · Rev: Wave 4 — redesign passthrough into parse_args spec

# ═══════════════════════════════════════════════════════════════════
# region FUNC_detect_age_key
## @purpose  Detect AGE_SECRET_KEY from env chain via shared/age_key.py (DevPlan 078 T2).
##           Python single-source-of-truth eliminates duplicate shell logic.
##           Returns: key to stdout + exit 0 (found) / exit 1 (not found).
detect_age_key() {
    local age_key_script="${CORE_DIR}/internal/shared/age_key.py"
    if [[ -f "$age_key_script" ]]; then
        python3 "$age_key_script" 2>/dev/null && return 0 || return 1
    fi
    # Fallback: direct env check if Python module unavailable
    if [[ -n "${AGE_SECRET_KEY:-}" ]]; then
        echo "${AGE_SECRET_KEY}"; return 0
    fi
    if [[ -n "${SOPS_AGE_KEY:-}" ]]; then
        echo "${SOPS_AGE_KEY}"; return 0
    fi
    return 1
}
# endregion FUNC_detect_age_key

# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Parse CLI args, detect AGE key, delegate to SSH proxy or local exec
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --node|--node-name) NODE_NAME="$2"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            --reconcile) PASSTHROUGH_ARGS+=("--reconcile"); shift ;;
            --age-secret-key-file)
                AGE_SECRET_KEY_FILE="$2"; export AGE_SECRET_KEY_FILE; shift 2 ;;
            --help|-h) usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" ;;
            *) PASSTHROUGH_ARGS+=("$1"); shift ;;
        esac
    done
    if [[ -z "${NODE_NAME}" ]]; then
        echo "[IMP:10][node-update][entrypoint] FATAL: --node is required" >&2
        echo "  Usage: node-update.sh --node <name> [--dry-run]" >&2
        exit 1
    fi
    echo "[IMP:9][node-update][entrypoint] Starting node-update for NODE=${NODE_NAME}" >&2
    local detected_age_key
    detected_age_key="$(detect_age_key)" || detected_age_key=""
    # ── S2 (DevPlan 019): deliver generated vhost overlays to server ──
    if ! $DRY_RUN; then
        source "${CORE_DIR}/internal/bootstrap/remote-cmd.sh"
        deliver_vhost_overlays "${NODE_NAME}" || {
            echo "[IMP:10][node-update][entrypoint] FATAL: Vhost overlay delivery failed" >&2
            exit 1
        }
    fi
    # ── SSH proxy (preferred) ──
    # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · set -e kills script on return 2 (local fallback signal)
    # · Symptom: execute_remote_update returns 2 (local VPS detected) → set -e exits
    #   before local remote_rc=$? can capture the code → node-lifecycle.sh never runs
    # · Fix: || remote_rc=$? pattern — captures non-zero exit without triggering set -e
    local remote_rc=0
    execute_remote_update "${NODE_NAME}" "${detected_age_key}" "${PASSTHROUGH_ARGS[@]}" || remote_rc=$?
    # ── Local exec fallback (no SSH host) ──
    if [[ $remote_rc -eq 2 ]]; then
        echo "[IMP:9][node-update][entrypoint] No SSH host — executing node-lifecycle.sh --mode update LOCALLY" >&2
        local internal="${PATHS_INTERNAL_DIR}/bootstrap/node-lifecycle.sh"
        if [[ ! -f "$internal" ]]; then
            echo "[IMP:10][node-update][entrypoint] FATAL: Internal script not found at ${internal}" >&2
            exit 1
        fi
        source "${CORE_DIR}/lib/node-resolver.sh"
        local node_yaml
        node_yaml="$(resolve_node_yaml "${NODE_NAME}" "${PLATFORM_ROOT}" "${HOME}/projects")" || {
            echo "[IMP:10][node-update][entrypoint] FATAL: Cannot resolve node.yaml for node=${NODE_NAME}" >&2
            exit 1
        }
        local args=("--node-name" "${NODE_NAME}" "--node-yaml" "${node_yaml}")
        $DRY_RUN && args+=("--dry-run")
        args+=("${PASSTHROUGH_ARGS[@]}")
        if $DRY_RUN; then
            echo "[IMP:8][node-update][dry-run] DRY-RUN: bash ${internal} --mode update ${args[*]}" >&2
            echo "[IMP:9][node-update][dry-run] DRY-RUN complete" >&2
            exit 0
        fi
        echo "[IMP:8][node-update][entrypoint] Delegating to ${internal} --mode update" >&2
        exec bash "${internal}" "--mode" "update" "${args[@]}"
    fi
}
# endregion FUNC_main

main "$@"
