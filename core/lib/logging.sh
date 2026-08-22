#!/usr/bin/env bash
# GREP_SUMMARY: logging library ldd structured-logging log-levels log-format
# STRUCTURE: ┌__LOG_PREFIX┐ → ○ log_imp(level,msg) → ◇ auto block detection via FUNCNAME → ⊕ stderr [IMP:N][prefix][block] msg
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Structured LDD Logging Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Provide a centralized, reusable LDD (Log-Driven Development)
##           logging subsystem for all bash scripts in the platform.
##           Eliminates 9+ duplicated log_imp() definitions across the
##           codebase by offering a single source of truth.
## @scope    — log_imp() with configurable prefix and auto block detection
##           — log_step wrapper
##           — zero side-effects on source (pure function definitions only)
##           — все скрипты используют log_imp напрямую (log_warn/fail/crit удалены —
##             аудит 2026-08-22: 0 callers в production-скриптах)
## @input    — __LOG_PREFIX (env var, set before source; default: "unknown")
##           — FUNCNAME[1] via caller context for auto-block detection
## @output   — Structured stderr lines: [IMP:N][<prefix>][<block>] <msg>
## @links    — USED_BY: all platform scripts (core/entrypoints/*.sh, core/internal/*.sh,
##             core/lib/*.sh, core/modules/*/*.sh, etc.)
##           — REPLACES: inline log_imp() in 9+ scripts
## @invariants — log_imp() MUST never write to stdout (only stderr)
##             — Library MUST NOT execute any code on source (no side-effects)
##             — __LOG_PREFIX MUST be checked at call-time, not source-time
## @rationale Q: Why a shared library instead of source-level dedup via sed?
##            A: A shared library provides a single maintenance point, enables
##            consistent format evolution (e.g., adding timestamps, JSON mode),
##            and allows all scripts to benefit from fixes without individual edits.
## @changes   LAST_CHANGE: 2026-07-07 · T1 — Initial implementation
## @changes   2026-08-22 · аудит simplify-refactor-waves T0.3 — semantic wrappers
##            log_warn/log_fail/log_crit удалены (0 callers; тесты обёрток сняты)
## @modulemap — log_imp     [W:100] Core LDD logger
##             — log_step    [W:20]  Step status wrapper (bootstrap scripts)
## @usecases  — Developer: source logging.sh; set __LOG_PREFIX="deploy";
##              log_imp 7 "phase1" "configuration loaded" → stderr
# endregion MODULE_CONTRACT
# GREP_SUMMARY: logging, LDD, log_imp, structured logging, stderr, IMP
# STRUCTURE: ▶ ┌config(__LOG_PREFIX)┐ → ○ log_imp(level,block,msg) → ◇ ┌block=="-"|""?┐ → ⊕ auto(FUNCNAME[1])|explicit → ⊕ [IMP:N][prefix][block] msg → ⎋ stderr

# ═══════════════════════════════════════════════════════════════════
# CORE LOGGER
# ═══════════════════════════════════════════════════════════════════
# region FUNC_log_imp
## @purpose  Emit a structured LDD log line to stderr with importance
##           level, configurable prefix, and contextual block name.
##           Central function — all wrappers delegate here.
## @param $1  IMP level: integer 1–10
##            1-3: trace, 4-6: flow, 7-8: I/O, 9-10: business logic
## @param $2  Block name. If "-" or empty → auto-detected from
##            caller's function name (FUNCNAME[1]).
## @param $3  Message text (arbitrary string)
## @io       out: stderr → [IMP:<level>][<__LOG_PREFIX>][<block>] <msg>
## @complexity O(1)
## @invariants — Never writes to stdout
##             — Prefix falls back to "unknown" if __LOG_PREFIX not set
log_imp() {
    local imp="$1" block="$2" msg="$3"
    local prefix="${__LOG_PREFIX:-unknown}"

    # Auto-detect block from caller if "-" or empty
    if [ "${block}" = "-" ] || [ -z "${block}" ]; then
        block="${FUNCNAME[1]:-main}"
    fi

    echo "[IMP:${imp}][${prefix}][${block}] ${msg}" >&2
}
# endregion FUNC_log_imp

# ═══════════════════════════════════════════════════════════════════
# SEMANTIC WRAPPERS
# ═══════════════════════════════════════════════════════════════════
# region FUNC_log_step
## @purpose  Log a step status with [STEP] [STATUS] format (used by bootstrap scripts)
## @param $1  Step name
## @param $2  Status: START, DONE, SKIP, FAIL, CREATE, etc.
## @param $3  Message text
## @io       Delegates to echo with [IMP:8][<prefix>][<step>] <status>: <msg> format
## @complexity O(1)
log_step() {
    local step="$1" status="$2" msg="$3"
    echo "[IMP:8][${__LOG_PREFIX:-unknown}][${step}] ${status}: ${msg}" >&2
}
# endregion FUNC_log_step
