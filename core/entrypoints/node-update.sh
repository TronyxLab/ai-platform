#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint node-update update lifecycle ssh-proxy node-resolver remote-cmd age-key dry-run verify-core
# STRUCTURE: ▶ init → ◇ --node? → ◇ --dry-run? → ┌resolve_node_yaml + extract_node_host┐ → ◇ SSH_HOST? → ├─ ⚡ prepare_ssh_opts + detect_age_key + build_update_ssh_cmd + ssh exec ─┤ └─ ⚡ exec local node-lifecycle.sh --mode update ─┘ → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Entrypoint for `make node-update`: resolves node.yaml → detects SSH host →
##           SSH-executes node-lifecycle.sh --mode update on VPS (or locally if no host).
##           Provides SSH proxy from macOS to VPS, eliminating "must run as root" error.
## @scope    Called from Makefile (`make node-update NODE=<name>`). Runs on local dev machine
##           or directly on VPS. Resolves node.yaml via lib/node-resolver.sh, delegates
##           SSH commands via remote-cmd.sh and scp-deliver.sh.
##           Thin-wrapper gate: ≤5 functions, ≤200 LOC.
## @invariants
##   - --node is REQUIRED; missing → usage error, exit 1
##   - --dry-run prints SSH command without executing
##   - SSH proxy: resolve_node_yaml → extract_node_host → prepare_ssh_opts + build_update_ssh_cmd → ssh exec
##   - No SCP: core-код уже на VPS (доставлен bootstrap'ом или core-deploy CI). D1.
##   - Without SSH_HOST: local exec (original behavior, backward compatible)
##   - AGE_SECRET_KEY передаётся через env export в SSH (не через CLI) — prevent ps aux visibility
##   --age-secret-key-file parsed to set AGE_SECRET_KEY_FILE env (consumed by detect_age_key)
## @rationale Thin-wrapper per DevPlan 020 T17. SSH proxy per DevPlan 005 T1 — портирование
##            SSH-логики из bootstrap.sh в node-update.sh, но без SCP. Позволяет `make node-update`
##            с macOS: не требует локального запуска node-lifecycle.sh (который падает с
##            «must run as root» на macOS).
## @changes 2026-07-17 | T17 — New entrypoint for node-update lifecycle verb
##           2026-07-17 | Lifecycle refactoring: delegates to node-lifecycle.sh --mode update
##           2026-07-17 | T1  — SSH proxy: resolve_node_yaml → extract_node_host → SSH exec
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../lib/paths.sh
source "${CORE_DIR}/lib/paths.sh"
# shellcheck source=../internal/bootstrap/remote-cmd.sh
source "${CORE_DIR}/internal/bootstrap/remote-cmd.sh"
# scp-deliver.sh sourced lazily when SSH_HOST detected (for prepare_ssh_opts)
# node-resolver.sh sourced lazily when needed (has its own guard)

NODE_NAME=""
DRY_RUN=false
PASSTHROUGH_ARGS=()

# ═══════════════════════════════════════════════════════════════════
# region FUNC_usage
## @purpose  Print usage instructions and exit 0
## @io       stdout: usage text
## @complexity O(1)
usage() {
    cat <<'EOF'
Usage: node-update.sh --node <name> [--dry-run] [--age-secret-key-file <file>]

Update an already-provisioned node (regular CI lifecycle, not INIT).
Resolves node.yaml, detects SSH host, and delegates to
core/internal/bootstrap/node-lifecycle.sh --mode update over SSH or locally.

5-step flow: verify_core → provision --scope networks --scope volumes
→ deploy docker modules → deploy system modules → healthcheck.

Required:
  --node <name>              Node name to update

Optional:
  --dry-run                  Show SSH command or local args without executing
  --age-secret-key-file <f>  Path to AGE secret key file
  --help, -h                 Print this help

Environment:
  PLATFORM_ROOT              Base platform directory (default: /opt/platform)

Examples:
  node-update.sh --node tronyx-vps
  node-update.sh --node tronyx-vps --dry-run
  node-update.sh --node tronyx-vps --age-secret-key-file ~/.age/key.txt
EOF
    exit 0
}
# endregion FUNC_usage

# ═══════════════════════════════════════════════════════════════════
# region FUNC_detect_age_key
## @purpose  Detect AGE_SECRET_KEY from environment chain:
##           AGE_SECRET_KEY env → SOPS_AGE_KEY env → AGE_SECRET_KEY_FILE path
## @stdout   The detected AGE key (if any), empty + return 1 if not found
## @complexity O(1)
## @invariants
##   - Checks AGE_SECRET_KEY first, then SOPS_AGE_KEY, then AGE_SECRET_KEY_FILE
##   - AGE_SECRET_KEY_FILE is read via head -1 (first line only)
##   - Missing AGE key = WARN (not fatal) — node-update may proceed without secrets
detect_age_key() {
    if [[ -n "${AGE_SECRET_KEY:-}" ]]; then
        local m; m="$(echo "${AGE_SECRET_KEY}" | cut -c1-8)"
        echo "[IMP:8][node-update][age-key] AGE_SECRET_KEY found in environment (${m}...)" >&2
        echo "${AGE_SECRET_KEY}"; return 0
    fi
    if [[ -n "${SOPS_AGE_KEY:-}" ]]; then
        local m; m="$(echo "${SOPS_AGE_KEY}" | cut -c1-8)"
        echo "[IMP:8][node-update][age-key] AGE_SECRET_KEY set from SOPS_AGE_KEY (${m}...)" >&2
        echo "${SOPS_AGE_KEY}"; return 0
    fi
    if [[ -n "${AGE_SECRET_KEY_FILE:-}" ]] && [[ -f "${AGE_SECRET_KEY_FILE}" ]]; then
        local key; key="$(head -1 "${AGE_SECRET_KEY_FILE}")"
        if [[ -n "${key}" ]]; then
            local m; m="$(echo "${key}" | cut -c1-8)"
            echo "[IMP:8][node-update][age-key] AGE_SECRET_KEY read from file ${AGE_SECRET_KEY_FILE} (${m}...)" >&2
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
## @purpose  Parse CLI args, resolve node.yaml, detect SSH host, and route to
##           SSH exec or local exec.
## @param $@  --node NAME, --dry-run, --age-secret-key-file FILE, rest → PASSTHROUGH_ARGS
## @io       stderr: LDD logs at IMP:8-10
##           exit 0 on success, 1 on missing --node or resolution failure
## @complexity O(N) — argument parsing + node resolution + SSH or local delegation
## @invariants
##   - --node is required; missing → exit 1 via usage error
##   - --dry-run prints SSH command or local args without executing
##   - --age-secret-key-file sets AGE_SECRET_KEY_FILE env var (consumed by detect_age_key)
##   - Unknown args are collected in PASSTHROUGH_ARGS and forwarded
##   - SSH proxy is the primary path; local exec is fallback if SSH_HOST absent
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --node|--node-name) NODE_NAME="$2"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            --age-secret-key-file)
                AGE_SECRET_KEY_FILE="$2"
                export AGE_SECRET_KEY_FILE
                shift 2 ;;
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

    # ── Resolve node.yaml ──────────────────────────────────────────
    # shellcheck source=../lib/node-resolver.sh
    source "${CORE_DIR}/lib/node-resolver.sh"
    local node_yaml
    node_yaml="$(resolve_node_yaml "${NODE_NAME}" "${PLATFORM_ROOT}" "${HOME}/projects")" || {
        echo "[IMP:10][node-update][entrypoint] FATAL: Cannot resolve node.yaml for node=${NODE_NAME}" >&2
        exit 1
    }
    echo "[IMP:9][node-update][entrypoint] Resolved node.yaml: ${node_yaml}" >&2

    # ── Extract SSH host ───────────────────────────────────────────
    local ssh_host
    ssh_host="$(extract_node_host "${node_yaml}")" || {
        echo "[IMP:8][node-update][entrypoint] WARN: No SSH host in node.yaml — local mode" >&2
        ssh_host=""
    }

    if [[ -n "${ssh_host}" ]]; then
        echo "[IMP:9][node-update][entrypoint] SSH host: ${ssh_host} — REMOTE update via SSH" >&2
        # ── SSH proxy path ──────────────────────────────────────────
        # shellcheck source=../internal/bootstrap/scp-deliver.sh
        source "${CORE_DIR}/internal/bootstrap/scp-deliver.sh"
        prepare_ssh_opts "${ssh_host}"

        local detected_age_key
        detected_age_key="$(detect_age_key)" || detected_age_key=""

        local remote_cmd
        remote_cmd="$(build_update_ssh_cmd "${NODE_NAME}" "${detected_age_key}" "${PASSTHROUGH_ARGS[@]}")"

        # Mask AGE key in dry-run output (security)
        local masked_cmd="${remote_cmd}"
        if [[ -n "${detected_age_key}" ]]; then
            local m; m="$(echo "${detected_age_key}" | cut -c1-8)"
            masked_cmd="${remote_cmd//${detected_age_key}/<AGE_KEY:${m}...>}"
        fi

        if $DRY_RUN; then
            echo "[IMP:8][node-update][dry-run] DRY-RUN: ssh ${SSH_OPTS[*]} root@${ssh_host} ${masked_cmd}" >&2
            echo "[IMP:9][node-update][dry-run] DRY-RUN complete" >&2
            exit 0
        fi

        echo "[IMP:9][node-update][entrypoint] Executing node-lifecycle.sh --mode update on root@${ssh_host}" >&2
        # shellcheck disable=SC2086,SC2048  # SSH_OPTS is intentionally word-split from array
        exec ssh ${SSH_OPTS[*]:--o StrictHostKeyChecking=accept-new -o ConnectTimeout=30} "root@${ssh_host}" "${remote_cmd}"
    fi

    # ── Local exec path (no SSH_HOST) ───────────────────────────────
    echo "[IMP:9][node-update][entrypoint] No SSH host — executing node-lifecycle.sh --mode update LOCALLY" >&2

    local internal="${PATHS_INTERNAL_DIR}/bootstrap/node-lifecycle.sh"
    if [[ ! -f "$internal" ]]; then
        echo "[IMP:10][node-update][entrypoint] FATAL: Internal script not found at ${internal}" >&2
        exit 1
    fi

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
}
# endregion FUNC_main

main "$@"
