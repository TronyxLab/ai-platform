#!/usr/bin/env bash
# GREP_SUMMARY: check-dead-code, gate, DEPRECATED, markers, stale, 30-days, CI
# STRUCTURE: ▶ python3 dead_code_checker.py "$@" → ⎋ exit-code passthrough
# region MODULE_CONTRACT
## @purpose  Thin facade for core/internal/lint/dead_code_checker.py — detect DEPRECATED markers older than 30 days
## @scope    Called from check-suite.yaml (`bash core/entrypoints/check-dead-code.sh`, суит
##           check-dead-code; План 175 W2.1 — make-таргет check-dead-code удалён).
##           All business logic lives in the Python module.
## @io       stdout/stderr passthrough; exit 0 = clean, 1 = violations
## @invariants — exit-code passthrough: `exit $?`; no business logic in shell (AGENTS.md языковая политика)
## @rationale Strangler-Fig Tier-1 (AGENTS.md): new/refactored logic lands in Python; shell remains thin facade
# endregion MODULE_CONTRACT
set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$_EP_DIR/../internal/lint/dead_code_checker.py" "$@"
exit $?
