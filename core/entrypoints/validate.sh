#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint validate schema FQDN lint
# STRUCTURE: ▶ init → ◇ --lint flag? → ⎋ delegate to internal/validate/validate.sh → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Entry-point for `make validate` and `make lint`
## @scope    Called ONLY from Makefile. Delegates to core/internal/validate/validate.sh
## @invariants
##   - All args passed through to validate.sh
##   - --lint flag triggers lint mode
## @rationale Transitional entrypoint — validates schema, checks FQDN uniqueness
# endregion MODULE_CONTRACT
set -euo pipefail
echo "[IMP:7][validate][main] Starting validate entrypoint" >&2
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

echo "[IMP:8][validate][main] Delegating to validate.sh" >&2
exec "${PATHS_INTERNAL_DIR}/validate/validate.sh" "$@"
