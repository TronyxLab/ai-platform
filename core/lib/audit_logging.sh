#!/usr/bin/env bash
# GREP_SUMMARY: audit-log logger platform-audit append-only timestamp step status
# STRUCTURE: ensure /var/log/platform/ → logger -t platform-audit "timestamp | step | status | msg"
# region MODULE_CONTRACT
## @purpose  Append-only audit log helper for bootstrap pipeline steps
## @scope    Sourced by bootstrap scripts and modules; provides audit_log(), _ensure_log_dir(), PLATFORM_LOG_DIR, PLATFORM_AUDIT_LOG
## @invariants
##   - Log directory /var/log/platform/ created with 0750 root:adm if missing
##   - Each entry: ISO8601 timestamp | step | status | message
##   - Uses `logger -t platform-audit` → goes to both syslog and audit.log via rsyslog rule
##   - Function never fails bootstrap (errors are warned, not fatal)
## @rationale Centralized audit trail enables post-hoc forensics and compliance review.
##   Extracted from internal/audit/audit.sh so modules/ can call audit_log() directly
##   without violating the modules→internal cross-layer rule.
# endregion MODULE_CONTRACT

PLATFORM_LOG_DIR="/var/log/platform"
PLATFORM_AUDIT_LOG="${PLATFORM_LOG_DIR}/audit.log"

# region ENSURE_LOG_DIR
_ensure_log_dir() {
    if [[ ! -d "$PLATFORM_LOG_DIR" ]]; then
        mkdir -p "$PLATFORM_LOG_DIR"
        chmod 0750 "$PLATFORM_LOG_DIR"
        chown root:adm "$PLATFORM_LOG_DIR" 2>/dev/null || chown root:root "$PLATFORM_LOG_DIR"
    fi
    # ═══════════════════════════════════════════════════════════════
    # M2/G1 fix (T1.3): после append гарантировать 0664 mode
    # Под root: create + chmod. Под ci-deploy: только запись,
    # без chown (нет прав — не фатально, log_imp 6).
    # ═══════════════════════════════════════════════════════════════
    if [[ ! -f "$PLATFORM_AUDIT_LOG" ]]; then
        if [[ "$(id -u)" -eq 0 ]]; then
            touch "$PLATFORM_AUDIT_LOG" 2>/dev/null || true
            chmod 0664 "$PLATFORM_AUDIT_LOG" 2>/dev/null || true
            chown root:adm "$PLATFORM_AUDIT_LOG" 2>/dev/null || true
        else
            # Under ci-deploy: best-effort write only, no chown
            touch "$PLATFORM_AUDIT_LOG" 2>/dev/null || \
                echo "[IMP:6][audit][_ensure_log_dir] WARN: Cannot create ${PLATFORM_AUDIT_LOG} as non-root" >&2
        fi
    elif [[ "$(id -u)" -eq 0 ]]; then
        # Under root: verify 0664
        local current_mode
        current_mode="$(stat -c '%a' "$PLATFORM_AUDIT_LOG" 2>/dev/null || echo "")"
        if [[ "$current_mode" != "0664" ]] && [[ -n "$current_mode" ]]; then
            chmod 0664 "$PLATFORM_AUDIT_LOG" 2>/dev/null || true
        fi
    fi
}
# endregion ENSURE_LOG_DIR

# region AUDIT_LOG
## @brief  Write an audit entry: timestamp | step | status | message
## @param  $1  step name (e.g. "docker:install")
## @param  $2  status (START | DONE | SKIP | FAIL | WARN)
## @param  $3  human-readable message
audit_log() {
    local step="${1:-unknown}"
    local status="${2:-INFO}"
    local msg="${3:-}"
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

    local entry="${ts} | ${step} | ${status} | ${msg}"

    # [IMP:9][audit][write] Write to syslog (persistent via journald/rsyslog)
    local logger_rc=0
    logger -t platform-audit "$entry" 2>/dev/null || logger_rc=$?

    # Direct append to audit.log (belt-and-suspenders in case syslog not configured)
    _ensure_log_dir 2>/dev/null || true
    local file_rc=0
    printf '%s\n' "$entry" >> "$PLATFORM_AUDIT_LOG" 2>/dev/null || file_rc=$?

    # Fallback to stderr when BOTH syslog AND file write fail
    if [[ $logger_rc -ne 0 && $file_rc -ne 0 ]]; then
        echo "[IMP:8][audit][fallback] ${entry}" >&2
    fi
}
# endregion AUDIT_LOG
