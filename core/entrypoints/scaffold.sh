#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint scaffold project context vhost
# STRUCTURE: ▶ init → ◇ detect subcmd (add-project|context-init|add-vhost) → ⎋ delegate → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Entry-point for `make new-project` and `make new-context`
## @scope    Called ONLY from Makefile.
## @invariants
##   - Detects subcommand from first argument (new-project|new-context|add-vhost)
##   - Aliases: new-project/add-project/project → add-project.sh, new-context/context-init/context → context-init.sh
##   - Delegates to internal/scaffold/{add-project,context-init,add-vhost}.sh
## @rationale Thin wrapper — scaffold logic in internal/scaffold/ scripts
# endregion MODULE_CONTRACT
set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

CMD="${1:-}"
case "$CMD" in
    new-project|add-project|project)
        shift
        exec "${PATHS_INTERNAL_DIR}/scaffold/add-project.sh" "$@"
        ;;
    new-context|context-init|context)
        shift
        exec "${PATHS_INTERNAL_DIR}/scaffold/context-init.sh" "$@"
        ;;
    add-vhost|vhost)
        shift
        exec "${PATHS_INTERNAL_DIR}/scaffold/add-vhost.sh" "$@"
        ;;
    *)
        echo "Usage: $0 {new-project|new-context|add-vhost} [args...]" >&2
        echo "" >&2
        echo "  new-project   — Create a new project from template" >&2
        echo "  new-context   — Scaffold a new deployment context" >&2
        echo "  add-vhost     — Generate nginx vhost config" >&2
        echo "" >&2
        echo "Called via Makefile:" >&2
        echo "  make new-project     → scaffold.sh new-project [args]" >&2
        echo "  make new-context     → scaffold.sh new-context [args]" >&2
        exit 1
        ;;
esac
