#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint secrets sops age decrypt help
# STRUCTURE: ▶ init → ◇ --help? → ⊕ print usage → ⎋ exit 0 → ◇ require AGE_SECRET_KEY + SECRETS_FILE → ⎋ delegate to internal/secrets/decrypt-secrets.sh → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Entry-point for `make secrets-unlock`: decrypt SOPS/age secrets
## @scope    Called ONLY from Makefile. Delegates to internal/secrets/decrypt-secrets.sh
## @invariants
##   - --help prints usage and exits 0
##   - Requires AGE_SECRET_KEY and SECRETS_FILE env vars
##   - All args passed through to decrypt-secrets.sh
## @rationale Thin wrapper — all security logic in internal/secrets/decrypt-secrets.sh
# endregion MODULE_CONTRACT
set -euo pipefail
echo "[IMP:7][secrets][main] Starting secrets-unlock entrypoint" >&2
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

if [[ "${1:-}" == "--help" ]]; then
    echo "Usage: $0 [NODE]"
    echo ""
    echo "Decrypt SOPS/age secrets to SECRETS_ENV_FILE (default /var/lib/platform/run/secrets.env)."
    echo "Required env: AGE_SECRET_KEY (or SOPS_AGE_KEY), SECRETS_FILE (or fallback /opt/node-configs/secrets/*.enc.yaml)."
    echo ""
    echo "Implementation: delegates to core/internal/secrets/decrypt-secrets.sh"
    exit 0
fi

echo "[IMP:8][secrets][main] Delegating to decrypt-secrets.sh" >&2
exec "${PATHS_INTERNAL_DIR}/secrets/decrypt-secrets.sh" "$@"
