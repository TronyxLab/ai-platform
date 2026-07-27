#!/usr/bin/env bash
# GREP_SUMMARY: verify-domains, shell-facade, internal, curl, http-check, wave5a
# STRUCTURE: ▶ ┌args┐ → ○ parse → ▶ python3 domain_verifier verify → ⎋ exit with code
# region MODULE_CONTRACT
## @purpose  Shell facade for domain_verifier.py — parses args, calls Python, exits with its code
## @scope    Called from core/entrypoints/verify.sh. Never called directly.
## @invariants
##   - Thin wrapper: parse args → python3 call → exit, zero business logic
##   - Zero inline Python script blocks (enforced by AGENTS.md language policy)
##   - Maintains identical CLI interface (verify-domains.sh <node> <platform_root>)
## @changes  2026-07-26 | Wave 5a — Strangler-Fig: 281→46 LOC, business logic in domain_verifier.py
## @rationale AGENTS.md языковая политика: новый код — Python
## ⚠️ TRAP[DECISION] · 2026-07-26 · MED · Wave 5a: verify-domains.sh Strangler-Fig → domain_verifier.py
## · Rejected: keeping business logic in shell (281 LOC, 2 inline python3 blocks)
## · Reason: языковая политика (AGENTS.md), тестируемость, дедупликация resolve_node_yaml
## · @see DevPlan 036A D1
# endregion MODULE_CONTRACT
set -euo pipefail

__VERIFY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
__CORE_DIR="$(cd "${__VERIFY_DIR}/../.." && pwd)"
source "${__CORE_DIR}/lib/logging.sh"

CURL_TIMEOUT="${CURL_TIMEOUT:-10}"

# region FUNC_MAIN
## @purpose  Parse args, call Python domain_verifier, pipe exit code back
## @param $1  Node name
## @param $2  Platform root (optional, defaults to PLATFORM_ROOT or /opt/platform)
## @exitcode  0 all pass, 1 any fail (propagated from Python)
main() {
    local node_name="${1:-}" platform_root="${2:-${PLATFORM_ROOT:-/opt/platform}}"

    if [[ -z "${node_name}" ]]; then
        log_imp 10 "verify" "Node name required — usage: verify-domains.sh <node> [platform_root]"
        echo "Usage: $0 <node_name> [platform_root]" >&2
        exit 1
    fi

    log_imp 7 "verify" "Delegating to domain_verifier.py: node=${node_name} root=${platform_root}"
    python3 "${__VERIFY_DIR}/domain_verifier.py" verify \
        --node "${node_name}" \
        --platform-root "${platform_root}" \
        --curl-timeout "${CURL_TIMEOUT}"
    exit $?
}
# endregion FUNC_MAIN

main "$@"
