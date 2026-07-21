#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint deploy git-push ci forced-command verb-contract remove status lifecycle
# STRUCTURE: ▶ init → ◇ parse SSH_ORIGINAL_COMMAND → ◇ classify verb(remove|status|deploy) → ⊕ dispatch → ⎋ exec deploy-project.sh
# region MODULE_CONTRACT
## @purpose  Entry-point for `make deploy` and SSH forced-command on VPS.
##           Parses SSH_ORIGINAL_COMMAND for verb contract K1:
##           - <project> <sha> <env> → deploy (backward compat)
##           - remove <project> → deploy-project.sh --remove
##           - status <project> → deploy-project.sh --status
## @scope    Called from:
##           1. Makefile (local development)
##           2. SSH forced-command on VPS (via authorized_keys command="...")
##           3. appleboy/ssh-action in CI workflow
## @invariants
##   - Backward compatible: legacy <project> <sha> [env] works unchanged (K1)
##   - All verbs dispatched to internal/deploy/deploy-project.sh (DD12)
##   - "remove" never contains --purge or volume destruction (O7)
## @rationale Single entrypoint for all deploy verbs — no second SSH user (DD12)
## @changes 2026-07-17 · T6 — Added verb contract K1 (remove/status dispatch)
##           2026-07-21 · W3: status verb passes --stub-aware to deploy-project.sh for improved stub detection
# endregion MODULE_CONTRACT

set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"
source "${_EP_DIR}/../lib/logging.sh"

# ═══════════════════════════════════════════════════════════════════
# FUNCTION — parse_verb (K1 contract)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_parse_verb
## @purpose  Read SSH_ORIGINAL_COMMAND, classify verb (remove|status|deploy),
##           dispatch to deploy-project.sh with appropriate flags.
##           Backward compatible: <project> <sha> <env> → deploy.
## @param $@ CLI arguments (fallback if SSH_ORIGINAL_COMMAND not set)
## @invariants
##   - SSH_ORIGINAL_COMMAND first token determines verb
##   - remove → --remove flag (never --purge)
##   - status → --status flag
##   - anything else → deploy (positional args: <project> <ref> [env])
##   - "platform-deploy" prefix handled for legacy compat
echo "[IMP:7][deploy][main] Starting deploy entrypoint" >&2
parse_verb() {
    local raw="${SSH_ORIGINAL_COMMAND:-}"

    if [[ -z "$raw" ]]; then
        # Fallback: CLI args (local testing)
        if [[ $# -gt 0 ]]; then
            raw="$*"
        else
            log_imp 10 "entrypoint" "FATAL: SSH_ORIGINAL_COMMAND not set and no CLI args"
            exit 1
        fi
    fi

    log_imp 8 "entrypoint" "Raw SSH_ORIGINAL_COMMAND: ${raw}"

    # Strip script path prefix (appleboy/ssh-action sends full path)
    local cleaned="${raw#/opt/platform/core/entrypoints/deploy.sh }"
    # If raw was exactly the script path (no trailing space), strip without trailing
    if [[ "$cleaned" == "$raw" ]]; then
        cleaned="${raw#/opt/platform/core/entrypoints/deploy.sh}"
    fi

    # Strip legacy "platform-deploy " prefix
    cleaned="${cleaned#platform-deploy }"
    cleaned="${cleaned#platform-deploy}"

    # Trim whitespace
    cleaned="$(echo "$cleaned" | xargs)"

    log_imp 9 "entrypoint" "Cleaned command: ${cleaned}"

    if [[ -z "$cleaned" ]]; then
        log_imp 10 "entrypoint" "FATAL: empty command after stripping prefixes"
        exit 1
    fi

    # ── Ping verb — pre-flight connectivity check ──
    if [[ "$cleaned" == "ping" ]]; then
        echo "pong"
        exit 0
    fi

    # ── Exit verb — SSH connectivity test (no-op success) ──
    if [[ "$cleaned" == "exit" ]]; then
        exit 0
    fi

    local first_token="${cleaned%% *}"

    case "$first_token" in
        remove)
            local project_name="${cleaned#remove }"
            project_name="$(echo "$project_name" | xargs)"
            log_imp 9 "entrypoint" "Verb: remove project=${project_name}"
            exec "${PATHS_INTERNAL_DIR}/deploy/deploy-project.sh" --remove "$project_name"
            ;;
        status)
            local project_name="${cleaned#status }"
            project_name="$(echo "$project_name" | xargs)"
            log_imp 9 "entrypoint" "Verb: status project=${project_name}"
            # Pass --stub-aware flag for improved stub detection in status output
            exec "${PATHS_INTERNAL_DIR}/deploy/deploy-project.sh" --status "$project_name" --stub-aware
            ;;
        verify)
            # ⚠️ verb contract: "verify <node>" — runs verify.sh for post-deploy health validation
            # Added 2026-07-20 to support CI reusable workflow verification through forced-command
            local node="${cleaned#verify }"
            node="$(echo "$node" | xargs)"
            log_imp 9 "entrypoint" "Verb: verify node=${node}"
            # Delegate directly to internal/verify/verify-domains.sh (not via entrypoint — cross-layer rule)
            local platform_root="${PLATFORM_ROOT:-/opt/platform}"
            exec "${PATHS_INTERNAL_DIR}/verify/verify-domains.sh" "${node}" "${platform_root}"
            ;;
        *)
            # Deploy format (backward compat): <project> <sha> [environment]
            log_imp 9 "entrypoint" "Verb: deploy (backward compat): ${cleaned}"
            # shellcheck disable=SC2086
            exec "${PATHS_INTERNAL_DIR}/deploy/deploy-project.sh" $cleaned
            ;;
    esac
}
# endregion FUNC_parse_verb

parse_verb "$@"
