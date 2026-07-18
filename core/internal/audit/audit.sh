#!/usr/bin/env bash
# GREP_SUMMARY: audit-log logger platform-audit append-only timestamp step status
# STRUCTURE: source lib/audit_logging.sh → audit_log() delegated to lib/
# region MODULE_CONTRACT
## @purpose  Thin wrapper: forwards audit_log() to lib/audit_logging.sh (extracted in Phase 6)
## @scope    Sourced by internal/ scripts; provides backward-compatible audit_log()
## @invariants
##   - All definitions (PLATFORM_LOG_DIR, PLATFORM_AUDIT_LOG, _ensure_log_dir, audit_log)
##     are now in core/lib/audit_logging.sh — this file only sources it
##   - auditor.sh consumers (node-lifecycle.sh) continue to work via transitive source
## @rationale audit_log() extracted to lib/ so modules/ can call it without violating
##   the modules→internal cross-layer rule (TASK-6A)
# endregion MODULE_CONTRACT

echo "[IMP:7][audit][main] Starting audit wrapper" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/audit_logging.sh"

# If executed directly (not sourced), write a test entry
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "[IMP:8][audit][main] Direct invocation — writing audit entry" >&2
    audit_log "${1:-test}" "${2:-INFO}" "${3:-audit.sh direct invocation}"
fi
