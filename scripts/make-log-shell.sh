#!/usr/bin/env bash
# GREP_SUMMARY: make-log-shell make-recipe-output-logging tee pipefail makefile-shell-wrapper persist
# STRUCTURE: ┌args: -c '<recipe>'┐ → ◇ MAKE_LOG_FILE set+writable? → ◇ tee recipe output → log+console (pipefail) → ⎋ exec /bin/bash passthrough
# region MODULE_CONTRACT
## @purpose  Logging SHELL for make recipes — persists every recipe line's output into $MAKE_LOG_FILE
## @scope    Used exclusively as root Makefile SHELL (see «make output logging» block in Makefile).
##           Not a user-facing entrypoint; lives outside core/ so it never ships to VPS.
## @invariants
##   - Recipe exit code preserved (pipefail) — make failure semantics unchanged
##   - MAKE_LOG_FILE unset/unwritable → plain passthrough (exec /bin/bash "$@")
##   - Log appended per recipe line; marker line records the recipe command (pre-expansion text)
##   - Log + console output identical — the file is a faithful copy of the terminal stream
## @rationale  make check/gate/test output must ALWAYS be persisted — no reliance on agent-side
##             tee, on truncated console output, or on the fingerprint cache. A SHELL wrapper
##             covers every make target transparently, including recursive make invocations.
# endregion MODULE_CONTRACT

set -o pipefail

_cmd=""
if [ "$#" -ge 2 ] && [ "$1" = "-c" ]; then
    _cmd="$2"
fi

if [ -n "${MAKE_LOG_FILE:-}" ] && [ -n "$_cmd" ] \
    && [ -f "$MAKE_LOG_FILE" ] && [ -w "$MAKE_LOG_FILE" ]; then
    printf '\n### [make] %s\n' "$_cmd" >> "$MAKE_LOG_FILE"
    /bin/bash -c "$_cmd" 2>&1 | tee -a "$MAKE_LOG_FILE"
    exit $?
fi

# No log target (e.g. $(shell ...) parsed before the Makefile log block, or disabled)
exec /bin/bash "$@"
