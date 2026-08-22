#!/usr/bin/env bash
# GREP_SUMMARY: audit-log logger platform-audit append-only jsonl status audit_log entrypoint shared-audit-logger
# STRUCTURE: ▶ init PYTHONPATH → ◇ audit_log (thin facade → shared audit_logger) → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Единственный shell-канал аудита — тонкий фасад над shared/audit_logger.
##           Все audit-записи идут через core/internal/shared/audit_logger.py
##           (write_audit_entry, JSON-lines, syslog, non-fatal).
## @scope    Sourced by bootstrap scripts, modules, and entrypoints; provides
##           audit_log(), PLATFORM_LOG_DIR, PLATFORM_AUDIT_LOG (compat).
##           (audit_step() удалён как мёртвый — аудит 2026-08-22; 0 callers.)
## @invariants
##   - audit_log()  — non-fatal: python failure is warned, never aborts caller
##   - PYTHONPATH exported with repo root (computed from own path) so
##     `python3 -m core.internal.shared.audit_logger` resolves in any cwd
##   - Формат: JSON-lines /var/log/platform/audit.jsonl
## @rationale Centralized audit trail enables post-hoc forensics and compliance review.
##   Python-first policy: shell facade stays a thin wrapper; all logic in Python module.
## @changes 2026-07-31 | DevPlan 089 follow-up (debt C-5) — recreated as thin facade
## @changes 2026-08-22 | Аудит simplify-refactor-waves T0.3 — audit_step удалён (0 callers)
## @links    DELEGATES_TO: core/internal/shared/audit_logger.py
# endregion MODULE_CONTRACT

set -euo pipefail

_AUDIT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_AUDIT_PY_MODULE="core.internal.shared.audit_logger"
# Repo root = core/lib/../..
export PYTHONPATH="${_AUDIT_LIB_DIR}/../..:${PYTHONPATH:-}"

# Compatibility constants (имена сохранены для обратной совместимости)
PLATFORM_LOG_DIR="/var/log/platform"
PLATFORM_AUDIT_LOG="${PLATFORM_LOG_DIR}/audit.log"

# region FUNC_audit_log
## @purpose  Write a single audit entry (thin facade → shared audit_logger).
## @param  $1  tag       — logical tag (e.g. "provision:networks")
## @param  $2  status    — status code (e.g. START | DONE | SKIP | FAIL | WARN)
## @param  $3  message   — human-readable description
## @io       side-effect: JSON-lines entry appended to audit log; syslog LOCAL6
## @complexity O(1)
## @invariants — never fails the caller (errors → stderr WARN, exit 0)
audit_log() {
    local tag="${1:-unknown}" status="${2:-INFO}" msg="${3:-}"
    # AUDIT_LOG_FILE env — тестовый override пути (иначе DEFAULT_LOG_FILE в Python-модуле)
    if [[ -n "${AUDIT_LOG_FILE:-}" ]]; then
        python3 -m "${_AUDIT_PY_MODULE}" write \
            --tag "${tag}" --status "${status}" --msg "${msg}" --log-file "${AUDIT_LOG_FILE}" 2>/dev/null || \
            echo "[IMP:6][audit][audit_log] WARN: audit entry dropped (tag=${tag} status=${status})" >&2
        return 0
    fi
    python3 -m "${_AUDIT_PY_MODULE}" write \
        --tag "${tag}" --status "${status}" --msg "${msg}" 2>/dev/null || \
        echo "[IMP:6][audit][audit_log] WARN: audit entry dropped (tag=${tag} status=${status})" >&2
}
# endregion FUNC_audit_log
