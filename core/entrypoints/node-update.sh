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
##   - SSH proxy logic lives entirely in remote-cmd.sh (execute_remote_update, deliver_vhost_overlays)
##   - Without SSH_HOST: local exec (backward compatible)
##   - S2 (DevPlan 019): generated vhost overlays are delivered via remote-cmd.sh
## @rationale Thin-wrapper per DevPlan 020. SSH proxy extracted to remote-cmd.sh.
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
Usage: node-update.sh --node <name> [--dry-run] [--age-secret-key-file <file>]

Update an already-provisioned node (regular CI lifecycle, not INIT).

Required:
  --node <name>              Node name to update

Optional:
  --dry-run                  Show SSH command or local args without executing
  --age-secret-key-file <f>  Path to AGE secret key file
  --help, -h                 Print this help

Examples:
  node-update.sh --node tronyx-vps
  node-update.sh --node tronyx-vps --dry-run
EOF
    exit 0
}
# endregion FUNC_usage

# ═══════════════════════════════════════════════════════════════════
# region FUNC_detect_age_key
## @purpose  Detect AGE_SECRET_KEY from environment chain:
##           AGE_SECRET_KEY env → SOPS_AGE_KEY env → AGE_SECRET_KEY_FILE path
detect_age_key() {
    if [[ -n "${AGE_SECRET_KEY:-}" ]]; then
        local m; m="$(echo "${AGE_SECRET_KEY}" | cut -c1-8)"; echo "[IMP:8][node-update][age-key] AGE_SECRET_KEY found in environment (${m}...)" >&2
        echo "${AGE_SECRET_KEY}"; return 0
    fi
    if [[ -n "${SOPS_AGE_KEY:-}" ]]; then
        local m; m="$(echo "${SOPS_AGE_KEY}" | cut -c1-8)"; echo "[IMP:8][node-update][age-key] AGE_SECRET_KEY set from SOPS_AGE_KEY (${m}...)" >&2
        echo "${SOPS_AGE_KEY}"; return 0
    fi
    if [[ -n "${AGE_SECRET_KEY_FILE:-}" ]] && [[ -f "${AGE_SECRET_KEY_FILE}" ]]; then
        local key; key="$(head -1 "${AGE_SECRET_KEY_FILE}")"
        if [[ -n "${key}" ]]; then
            local m; m="$(echo "${key}" | cut -c1-8)"; echo "[IMP:8][node-update][age-key] AGE_SECRET_KEY read from file ${AGE_SECRET_KEY_FILE} (${m}...)" >&2
            echo "${key}"; return 0
        fi
        echo "[IMP:8][node-update][age-key] WARN: AGE_SECRET_KEY_FILE=${AGE_SECRET_KEY_FILE} is empty" >&2
    fi
    echo "[IMP:8][node-update][age-key] WARN: AGE_SECRET_KEY not found — Docker modules requiring secrets will fail to deploy" >&2
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
            --age-secret-key-file)
                AGE_SECRET_KEY_FILE="$2"; export AGE_SECRET_KEY_FILE; shift 2 ;;
            --help|-h) usage ;;
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
    execute_remote_update "${NODE_NAME}" "${detected_age_key}" "${PASSTHROUGH_ARGS[@]}"
    local remote_rc=$?
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
