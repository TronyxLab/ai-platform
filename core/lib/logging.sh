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
##           — 5 semantic wrapper functions (log_info/ok/warn/fail/crit)
##           — zero side-effects on source (pure function definitions only)
## @input    — __LOG_PREFIX (env var, set before source; default: "unknown")
##           — FUNCNAME[1] via caller context for auto-block detection
## @output   — Structured stderr lines: [IMP:N][<prefix>][<block>] <msg>
## @links    — USED_BY: all platform scripts (core/entrypoints/*.sh, core/internal/*.sh,
##             core/lib/*.sh, core/modules/*/*.sh, etc.)
##           — REPLACES: inline log_imp() in 9+ scripts
## @invariants — log_imp() MUST never write to stdout (only stderr)
##             — Library MUST NOT execute any code on source (no side-effects)
##             — __LOG_PREFIX MUST be checked at call-time, not source-time
##             - Every wrapper MUST delegate to log_imp with auto-block ("-")
## @rationale Q: Why a shared library instead of source-level dedup via sed?
##            A: A shared library provides a single maintenance point, enables
##            consistent format evolution (e.g., adding timestamps, JSON mode),
##            and allows all scripts to benefit from fixes without individual edits.
## @changes   LAST_CHANGE: 2026-07-07 · T1 — Initial implementation
## @modulemap — log_imp     [W:100] Core LDD logger
##             — log_info    [W:20]  Semantic wrapper at IMP:6
##             — log_ok      [W:20]  Semantic wrapper at IMP:7
##             — log_warn    [W:20]  Semantic wrapper at IMP:8
##             — log_fail    [W:20]  Semantic wrapper at IMP:9
##             — log_crit    [W:20]  Semantic wrapper at IMP:10
## @usecases  — Developer: source logging.sh; set __LOG_PREFIX="deploy";
##              log_imp 7 "phase1" "configuration loaded" → stderr
##             — Developer: log_warn "disk space low" → auto-block from caller
# endregion MODULE_CONTRACT
# GREP_SUMMARY: logging, LDD, log_imp, log_info, log_ok, log_warn, log_fail, log_crit, structured logging, stderr, IMP
# STRUCTURE: ▶ ┌config(__LOG_PREFIX)┐ → ○ log_imp(level,block,msg) → ◇ ┌block=="-"|""?┐ → ⊕ auto(FUNCNAME[1])|explicit → ⊕ [IMP:N][prefix][block] msg → ⎋ stderr
#            └──────────────── ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┘
#            ⚡ wrappers: log_info→IMP:6, log_ok→IMP:7, log_warn→IMP:8,
#               log_fail→IMP:9, log_crit→IMP:10

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

# ═══════════════════════════════════════════════════════════════════
# region FUNC_log_info
## @purpose  Log an informational message at IMP:6
## @param $1  Message text
## @io       Delegates to log_imp with auto-block detection
## @complexity O(1)
log_info() { log_imp 6 "-" "$*"; }
# endregion FUNC_log_info

# region FUNC_log_ok
## @purpose  Log a success/ok message at IMP:7
## @param $1  Message text
## @io       Delegates to log_imp with auto-block detection
## @complexity O(1)
log_ok() { log_imp 7 "-" "$*"; }
# endregion FUNC_log_ok

# region FUNC_log_warn
## @purpose  Log a warning message at IMP:8
## @param $1  Message text
## @io       Delegates to log_imp with auto-block detection
## @complexity O(1)
log_warn() { log_imp 8 "-" "$*"; }
# endregion FUNC_log_warn

# region FUNC_log_fail
## @purpose  Log a failure/error message at IMP:9
## @param $1  Message text
## @io       Delegates to log_imp with auto-block detection
## @complexity O(1)
log_fail() { log_imp 9 "-" "$*"; }
# endregion FUNC_log_fail

# region FUNC_log_crit
## @purpose  Log a critical/severe message at IMP:10
## @param $1  Message text
## @io       Delegates to log_imp with auto-block detection
## @complexity O(1)
log_crit() { log_imp 10 "-" "$*"; }
# endregion FUNC_log_crit
