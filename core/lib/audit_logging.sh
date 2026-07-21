#!/usr/bin/env bash
# GREP_SUMMARY: audit-log logger platform-audit append-only timestamp step status audit_step wrapper START DONE FAIL entrypoint
# STRUCTURE: ensure /var/log/platform/ → logger -t platform-audit "timestamp | step | status | msg" → audit_step wrapper(START→exec→capture rc→DONE|FAIL→return)
# region MODULE_CONTRACT
## @purpose  Append-only audit log helper for bootstrap pipeline steps + entrypoint wrapper
## @scope    Sourced by bootstrap scripts, modules, and entrypoints; provides audit_log(), audit_step(), _ensure_log_dir(), PLATFORM_LOG_DIR, PLATFORM_AUDIT_LOG
## @invariants
##   - Log directory /var/log/platform/ created with 0750 root:adm if missing
##   - Each entry: ISO8601 timestamp | step | status | message
##   - Uses `logger -t platform-audit` → goes to both syslog and audit.log via rsyslog rule
##   - Function never fails bootstrap (errors are warned, not fatal)
##   - audit_step() is wrapper-style (NO trap-on-EXIT) — explicit capture $? → conditional DONE/FAIL emit
## @rationale Centralized audit trail enables post-hoc forensics and compliance review.
##   Extracted from internal/audit/audit.sh so modules/ can call audit_log() directly
##   without violating the modules→internal cross-layer rule.
##   audit_step() added in W2-E3 (DRIFT-7 fix): wrapper-style without trap-on-EXIT.
## @changes 2026-07-21 | W2-E3 | Added audit_step() wrapper (wrapper-style, NO trap) per DRIFT-7 fix
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

# region FUNC_audit_step
## @purpose  Wrapper-style entrypoint audit: emit START before exec, DONE/FAIL after, propagate exit code
## @param  $1  step_name — logical step identifier (e.g. "context-promote:prod", "hermes-build:build-platform:none")
## @param  $@  command — the actual command or function to execute (remaining args)
## @return Propagates command exit code (0=success, non-zero=failure)
## @invariants
##   - NO trap-on-EXIT — wrapper-style only (DRIFT-7 fix, see VerificationReport 03)
##   - START emitted synchronously BEFORE command execution (dual-write via audit_log())
##   - DONE emitted iff _audit_rc -eq 0
##   - FAIL emitted iff _audit_rc -ne 0, includes "exit=${_audit_rc}" in message
##   - command-preview truncated to 200 chars (avoids huge audit entries for long invocations)
##   - Returns command exit code, never generates its own error
##   - NO subshell — command executes in current shell (preserves env vars, exit codes, side effects)
## @rationale Bash EXIT-trap fires on ALL exits including success — makes START+FAIL dedup
##   impossible. Wrapper-style is simpler: explicit capture $? → conditional emit.
##   No subshell overhead (~1ms), no trap-scope leaking.
## @complexity O(1) — 1 audit_log call + 1 command execution + 1 conditional audit_log
audit_step() {
    local step_name="${1:-unknown}"
    shift
    local command_preview="$*"
    local truncated_preview="${command_preview:0:200}"

    # 🧐 TRAP[DECISION] · 2026-07-21 · — · Wrapper-style, NO trap-on-EXIT
    # · Rejected: trap-on-EXIT (fires on ALL exits including success — START+FAIL dedup impossible)
    # · Reason: explicit capture $? → conditional DONE (0) / FAIL (≠0) emit.
    #   Simpler, no subshell overhead, no trap-scope leaking.
    # · Rev: if a future use-case needs trap-based audit for functions that call `exit`
    #   internally, consider a hybrid approach: audit_step for return-based commands,
    #   audit_trap for exit-based functions.

    # Emit START synchronously BEFORE command execution
    audit_log "${step_name}" "START" "${truncated_preview}"

    # Execute command in current shell (NO subshell — preserves env vars, exit codes, side effects)
    "$@"
    local _audit_rc=$?

    # Conditional emit: DONE (=0) or FAIL (≠0)
    if [[ ${_audit_rc} -eq 0 ]]; then
        audit_log "${step_name}" "DONE" "${truncated_preview}"
    else
        audit_log "${step_name}" "FAIL" "exit=${_audit_rc}" "${truncated_preview}"
    fi

    # Propagate original exit code
    return ${_audit_rc}
}
# endregion FUNC_audit_step
