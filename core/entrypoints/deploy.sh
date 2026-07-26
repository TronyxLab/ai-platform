#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint deploy git-push ci forced-command verb-contract remove status lifecycle shared ssh-command-parser
# STRUCTURE: ▶ init → ◇ python3 ssh_command_parser parse(raw) → ◇ dispatch_verb(verb, args, cleaned) → ⊕ exec deploy-project.sh → ⎋
#            ▶ parse_verb → ssh_command_parser (DevPlan 081 Phase B — shared module imported)
# region MODULE_CONTRACT
## @purpose  Entry-point for `make deploy` and SSH forced-command on VPS.
##           Parses SSH_ORIGINAL_COMMAND via shared ssh_command_parser (DevPlan 081 Phase B),
##           dispatches verb contract K1:
##           - <project> <sha> <env> → deploy (backward compat)
##           - remove <project> → deploy-project.sh --remove
##           - status <project> → deploy-project.sh --status
##           - verify <node> → verify-domains.sh
## @scope    Called from:
##           1. Makefile (local development)
##           2. SSH forced-command on VPS (via authorized_keys command="...")
##           3. appleboy/ssh-action in CI workflow
## @invariants
##   - Backward compatible: legacy <project> <sha> [env] works unchanged (K1)
##   - All verbs dispatched to internal/deploy/deploy-project.sh (DD12)
##   - "remove" never contains --purge or volume destruction (O7)
##   - Parsing delegated to core.internal.shared.ssh_command_parser.parse_ssh_command()
## @rationale Single entrypoint for all deploy verbs — no second SSH user (DD12)
## @changes 2026-07-17 · T6 — Added verb contract K1 (remove/status dispatch)
##           2026-07-21 · W3: status verb passes --stub-aware to deploy-project.sh
##           2026-07-26 · DevPlan 081 Phase A — Structural refactoring:
##             extracted _dispatch_verb()
##           2026-07-26 · DevPlan 081 Phase B (TASK-081B7) — replaced local
##             stripping+classification with shared ssh_command_parser module.
##             DRIFT-D4 resolved: unified SSH_ORIGINAL_COMMAND parser.
##           2026-07-26 · DevPlan 081 AC7 (H4) — replaced inline python3 -c JSON
##             parsing with ssh_command_parser --format lines. Eliminates last
##             inline python3 in deploy.sh facade (Tier 1 Strangler trigger).
# endregion MODULE_CONTRACT

set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"
source "${_EP_DIR}/../lib/logging.sh"

# ═══════════════════════════════════════════════════════════════════
# FUNCTION — _dispatch_verb (K1 verb contract)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_dispatch_verb
## @purpose  Route a parsed verb+args to the correct handler.
##           Receives parsed data from shared ssh_command_parser.
##           Verb dispatch table:
##           ┌──────────┬──────────────────────────────────────────────────────┐
##           │ ping     │ Echo "pong" and exit 0 (pre-flight connectivity)     │
##           │ exit     │ Exit 0 (SSH connectivity test, no-op success)        │
##           │ remove   │ exec deploy-project.sh --remove <project>            │
##           │ status   │ exec deploy-project.sh --status <project> --stub-aware│
##           │ verify   │ exec verify-domains.sh <node>                        │
##           │ * (other)│ exec deploy-project.sh <project> <sha> [env] (deploy) │
##           └──────────┴──────────────────────────────────────────────────────┘
## @param $1 verb string (from shared ssh_command_parser)
## @param $2 args string (project name or full deploy args, from shared)
## @param $3 cleaned string (original cleaned command, from shared)
## @invariants
##   - Ping/exit are handled in-place (no exec)
##   - Remove/status/verify/deploy use exec — replaces current process
##   - Unknown verb falls through to legacy deploy (backward compat)
_dispatch_verb() {
    local verb="$1"
    local args="${2:-}"
    local cleaned="${3:-}"

    case "$verb" in
        ping)
            echo "pong"
            exit 0
            ;;
        exit)
            exit 0
            ;;
        remove)
            local project_name="$args"
            log_imp 9 "entrypoint" "Verb: remove project=${project_name}"
            exec "${PATHS_INTERNAL_DIR}/deploy/deploy-project.sh" --remove "$project_name"
            ;;
        status)
            local project_name="$args"
            log_imp 9 "entrypoint" "Verb: status project=${project_name}"
            exec "${PATHS_INTERNAL_DIR}/deploy/deploy-project.sh" --status "$project_name" --stub-aware
            ;;
        verify)
            local node="$args"
            log_imp 9 "entrypoint" "Verb: verify node=${node}"
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
# endregion FUNC_dispatch_verb

# ═══════════════════════════════════════════════════════════════════
# FUNCTION — parse_verb (K1 contract orchestrator, Phase B)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_parse_verb
## @purpose  Orchestrate SSH_ORIGINAL_COMMAND parsing via shared ssh_command_parser.
##           Phase B (DevPlan 081 TASK-081B7): strip+classify replaced by:
##             python3 -m core.internal.shared.ssh_command_parser --format lines parse "$raw"
##           --format lines outputs verb/args/cleaned each on own line, eliminating
##           the need for inline python3 -c JSON parsing.
## @param $@ CLI arguments (fallback if SSH_ORIGINAL_COMMAND not set)
## 🧐 TRAP[DECISION] · 2026-07-26 · — · --format lines vs inline python3 -c
## · Rejected: keep inline python3 -c to avoid modifying ssh_command_parser
## · Reason: Tier-1 Strangler trigger — inline python3 in shell facade must be
##   extracted. --format lines is the canonical extraction path.
## · Rev: if ssh_command_parser grows additional output modes, keep --format
##   consistent (json|lines|yaml).
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

    # ── Phase B: shared ssh_command_parser via python3 -m CLI ──
    # Uses --format lines to output verb/args/cleaned each on own line,
    # eliminating the need for inline python3 -c JSON parsing (DevPlan 081 AC7).
    local verb args cleaned lines_output
    lines_output=$(python3 -m core.internal.shared.ssh_command_parser --format lines parse "$raw") || {
        log_imp 10 "entrypoint" "FATAL: ssh_command_parser --format lines failed"
        exit 1
    }
    {
        read -r verb
        read -r args
        read -r cleaned
    } <<< "$lines_output"

    log_imp 9 "entrypoint" "Parsed: verb=${verb} args=${args} cleaned=${cleaned}"

    _dispatch_verb "$verb" "$args" "$cleaned"
}
# endregion FUNC_parse_verb

parse_verb "$@"
