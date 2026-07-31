#!/usr/bin/env bash
# GREP_SUMMARY: audit-log backward-compat facade lib audit.sh wrapper
# STRUCTURE: ▶ source lib/audit.sh (canonical facade) → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Backward-compat shim: `make audit` entrypoint delegates here, and legacy
##           scripts may source this file transitively. Re-exports the canonical
##           thin facade core/lib/audit.sh (audit_log/audit_step over Python
##           shared/audit_logger). No-op on direct invocation with args.
## @scope    Sourced by legacy scripts + executed by core/entrypoints/audit.sh (make audit)
## @invariants — non-fatal source: `|| true` semantics preserved (never aborts caller)
## @changes  2026-07-31 | DevPlan 089 follow-up (debt C-5) — repointed at lib/audit.sh
# endregion MODULE_CONTRACT

set -euo pipefail
_THIS_AUDIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# lib/audit.sh is one level up + /lib: internal/audit → ../lib
source "${_THIS_AUDIT_DIR}/../lib/audit.sh" 2>/dev/null || \
    echo "[IMP:6][audit][shim] WARN: core/lib/audit.sh unavailable — audit calls become no-ops" >&2

# Preserve legacy invocation behaviour (direct exec with args = informational only)
if [[ $# -gt 0 ]]; then
    echo "[IMP:7][audit][main] audit facade loaded — use make audit or Python audit_logger directly" >&2
fi
