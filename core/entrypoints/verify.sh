#!/usr/bin/env bash
# GREP_SUMMARY: verify, post-deploy, entrypoint, thin-wrapper, node-verify
# STRUCTURE: ▶ init → ⎋ delegate to internal/verify/domain_verifier.py → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin wrapper for post-deploy HTTPS verification — delegates to core/internal/verify/domain_verifier.py
## @scope    Called from Makefile (`make verify-domains NODE=<node>`, бывш. verify — План 175 W4.3).
## @invariants
##   - Requires node name (positional or NODE env) — резолв в domain_verifier.py (173 W1.5)
##   - Does NOT contain business logic (YAML parsing, curl) — all delegated to internal/
##   - LOC ≤ 150 (thin-wrapper contract)
## @rationale Двух-хоповый фасад (verify.sh → verify-domains.sh → .py) схлопнут (DevPlan 173 W1.5);
##            arg-парсинг/usage перенесены в domain_verifier.py (positional node/project + env fallback).
# endregion MODULE_CONTRACT
set -euo pipefail

_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ORIG_PLATFORM_ROOT="${PLATFORM_ROOT:-}"
source "${_EP_DIR}/../lib/paths.sh"
# Restore PLATFORM_ROOT if set by caller (paths.sh hardcodes /opt/platform)
if [[ -n "${_ORIG_PLATFORM_ROOT}" ]]; then
    PLATFORM_ROOT="${_ORIG_PLATFORM_ROOT}"
fi

# Delegate all business logic to domain_verifier.py (node/project — positional или env)
exec python3 "${_EP_DIR}/../internal/verify/domain_verifier.py" verify "$@"
