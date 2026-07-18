#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint audit platform system
# STRUCTURE: ▶ init → ○ parse args → ⎋ delegate to internal/audit/audit.sh → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Entry-point for `make audit`: system audit of the platform
## @scope    Called ONLY from Makefile. Delegates to core/internal/audit/audit.sh
## @invariants
##   - All args passed through to audit.sh
## @rationale Transitional entrypoint — audit logic in core/internal/audit/audit.sh
# endregion MODULE_CONTRACT
set -euo pipefail
echo "[IMP:7][audit][main] Starting system audit entrypoint" >&2
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

echo "[IMP:8][audit][main] Delegating to audit.sh" >&2
exec "${PATHS_INTERNAL_DIR}/audit/audit.sh" "$@"
