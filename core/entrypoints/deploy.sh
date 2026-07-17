#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint deploy git-push ci forced-command
# STRUCTURE: ▶ init → ◇ require PROJECT → ⎋ delegate to internal/deploy/deploy-project.sh → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Entry-point for `make deploy`: triggers git push → CI → SSH forced-command deploy-project.sh
## @scope    Called ONLY from Makefile. Delegates to internal/deploy/deploy-project.sh
## @invariants
##   - Called via SSH forced-command on VPS, or directly for local testing
##   - Requires PROJECT=<dir> [ENV=...] [BRANCH=...]
## @rationale Thin wrapper — all business logic in internal/deploy/deploy-project.sh
# endregion MODULE_CONTRACT
set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"
exec "${PATHS_INTERNAL_DIR}/deploy/deploy-project.sh" "$@"
