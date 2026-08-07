#!/usr/bin/env bash
# GREP_SUMMARY: sops, age, decrypt, secrets, env, facade, python-core
# STRUCTURE: ▶ resolve SECRETS_FILE → exec python3 decrypt_secrets.py → ⎋ exit code
# region MODULE_CONTRACT
## @purpose  Shell facade (<25 LOC) for decrypt_secrets.py. Resolves SECRETS_FILE
##           via env/fallback, delegates all decryption and cleanup to Python core.
## @changes  2026-07-30 | Rewritten as thin facade over decrypt_secrets.py
# endregion MODULE_CONTRACT

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve SECRETS_FILE: env var → exact path → /opt/node-configs/secrets/*.enc.yaml fallback
if [[ -z "${SECRETS_FILE:-}" || ! -f "$SECRETS_FILE" ]]; then
    shopt -s nullglob
    matches=(/opt/node-configs/secrets/*.enc.yaml)
    shopt -u nullglob
    [[ ${#matches[@]} -eq 0 ]] && { echo "[IMP:9][decrypt-secrets][fail] No *.enc.yaml found" >&2; exit 1; }
    SECRETS_FILE="${matches[0]}"
    export SECRETS_FILE
fi

exec python3 "$SCRIPT_DIR/decrypt_secrets.py" "$SECRETS_FILE" "${SECRETS_ENV_FILE:-/var/lib/platform/run/secrets.env}"
