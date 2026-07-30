#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint scaffold project context vhost sync-env remove adopt list status lifecycle
# STRUCTURE: ▶ init → ◇ detect subcmd (add-project|context-init|add-vhost|sync-env|remove|adopt|list|status) → ⎋ delegate → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Entry-point for `make new-project`, `make new-context`, and project lifecycle
##           operations (sync-env, remove, adopt, list, status).
## @scope    Called ONLY from Makefile.
## @invariants
##   - Detects subcommand from first argument
##   - Aliases: new-project/add-project/project → add-project.sh, new-context/context-init/context → context-init.sh
##   - Aliases: project-sync-env/sync-env → gen_env_platform.py
##   - Aliases: remove-project → remove-project.sh, adopt-project → adopt-project.sh
##   - Aliases: project-list/list → project-list.sh, project-status/status → project-list.sh --status
##   - All subcommands delegate to internal/scaffold/ scripts — scaffold.sh is a thin wrapper
##   - positional→named bridge for new-project: --org from ${PLATFORM_ORG:-}, --node from ${PLATFORM_DEFAULT_NODE:-}
## @rationale Thin wrapper — scaffold logic in internal/scaffold/ scripts.
##            Positional→named bridge provides backward compatibility for `make new-project NAME=foo TEMPLATE=bar`.
##            New lifecycle subcommands (sync-env, remove, adopt, list, status) complete the OBSERVE+REMOVE phases.
## @changes  2026-07-17 · T8 — Added lifecycle subcommands (project-sync-env, remove-project, adopt-project,
##           project-list, project-status) + positional→named bridge for new-project + --remove flag for add-vhost
## @changes  2026-07-30 · T9c — sync-env delegates to gen_env_platform.py (was gen-env-platform.sh)
# endregion MODULE_CONTRACT
set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

__LOG_PREFIX="scaffold"
source "${_EP_DIR}/../lib/logging.sh"

CMD="${1:-}"
case "$CMD" in
    new-project|add-project|project)
        shift
        # ── positional→named bridge (DD7): detect positional args and add --org/--node defaults ──
        args=()
        positional_count=0
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --*) args+=("$1");;
                *)
                    if [[ $positional_count -eq 0 ]]; then
                        args+=(--name "$1")
                    elif [[ $positional_count -eq 1 ]]; then
                        args+=(--template "$1")
                    else
                        args+=("$1")
                    fi
                    ((++positional_count))
                    ;;
            esac
            shift
        done
        # Inject --org and --node defaults from env if not specified
        has_org=false; has_node=false
        for a in "${args[@]}"; do
            [[ "$a" == "--org" ]] && has_org=true
            [[ "$a" == "--node" ]] && has_node=true
        done
        if [[ "$has_org" != "true" ]]; then
            org_default="${PLATFORM_ORG:-}"
            [[ -n "$org_default" ]] && args+=(--org "$org_default")
        fi
        if [[ "$has_node" != "true" ]]; then
            node_default="${PLATFORM_DEFAULT_NODE:-}"
            [[ -n "$node_default" ]] && args+=(--node "$node_default")
        fi
        echo "[IMP:8][scaffold][main] Bridge: positional→named new-project args: ${args[*]}" >&2
        exec "${PATHS_INTERNAL_DIR}/scaffold/add-project.sh" "${args[@]}"
        ;;
    new-context|context-init|context)
        shift
        exec "${PATHS_INTERNAL_DIR}/scaffold/context-init.sh" "$@"
        ;;
    add-vhost|vhost)
        shift
        exec "${PATHS_INTERNAL_DIR}/scaffold/add-vhost.sh" "$@"
        ;;
    project-sync-env|sync-env)
        shift
        log_imp 7 "-" "Delegating to gen_env_platform.py $*"
        exec python3 "${PATHS_INTERNAL_DIR}/scaffold/gen_env_platform.py" "$@"
        ;;
    remove-project)
        shift
        log_imp 7 "-" "Delegating to remove-project.sh $*"
        exec "${PATHS_INTERNAL_DIR}/scaffold/remove-project.sh" "$@"
        ;;
    adopt-project)
        shift
        log_imp 7 "-" "Delegating to adopt-project.sh $*"
        exec "${PATHS_INTERNAL_DIR}/scaffold/adopt-project.sh" "$@"
        ;;
    project-list|list)
        shift
        log_imp 7 "-" "Delegating to project-list.sh --list $*"
        exec "${PATHS_INTERNAL_DIR}/scaffold/project-list.sh" --list "$@"
        ;;
    project-status|status)
        shift
        log_imp 7 "-" "Delegating to project-list.sh --status $*"
        exec "${PATHS_INTERNAL_DIR}/scaffold/project-list.sh" --status "$@"
        ;;
    *)
        echo "Usage: $0 <command> [args...]" >&2
        echo "" >&2
        echo "Project lifecycle commands:" >&2
        echo "  new-project|add-project|project     — Create a new project from template" >&2
        echo "  project-sync-env|sync-env           — Sync .env.platform from platform-env.yaml" >&2
        echo "  remove-project                      — Remove project from lifecycle (safe, no data loss)" >&2
        echo "  adopt-project                       — Adopt existing project into platform lifecycle" >&2
        echo "  project-list|list                   — List registered projects" >&2
        echo "  project-status|status               — Show live status of project(s)" >&2
        echo "" >&2
        echo "Context commands:" >&2
        echo "  new-context|context-init|context    — Scaffold a new deployment context" >&2
        echo "  add-vhost|vhost                     — Generate nginx vhost config" >&2
        echo "" >&2
        echo "Called via Makefile:" >&2
        echo "  make new-project     → scaffold.sh new-project [args]" >&2
        echo "  make new-context     → scaffold.sh new-context [args]" >&2
        echo "  make project-sync-env     → scaffold.sh sync-env [args]" >&2
        echo "  make remove-project     → scaffold.sh remove-project [args]" >&2
        echo "  make adopt-project      → scaffold.sh adopt-project [args]" >&2
        echo "  make project-list       → scaffold.sh list [args]" >&2
        echo "  make project-status     → scaffold.sh status [args]" >&2
        exit 1
        ;;
esac
