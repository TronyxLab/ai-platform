#!/usr/bin/env bash
# GREP_SUMMARY: sops age decrypt secrets env no-disk-persist fail-fast fallback resolve
# STRUCTURE: validate env(AGE_SECRET_KEY→SOPS_AGE_KEY) → resolve SECRETS_FILE(path│fallback) → write temp key → sops decrypt → export env_file → wipe temp key + plaintext
# region MODULE_CONTRACT
## @purpose  Decrypt SOPS/age-encrypted secrets file; export values as env vars for compose/bootstrap
## @scope    Called during bootstrap step ⑨; age key NEVER persisted to disk after function returns
## @location core/internal/secrets/decrypt-secrets.sh — moved from core/scripts/decrypt-secrets.sh
## @invariants
##   - AGE_SECRET_KEY or SOPS_AGE_KEY must be set in environment (fallback chain: AGE_SECRET_KEY → SOPS_AGE_KEY)
##   - SECRETS_FILE resolved via: exact path → /opt/node-configs/secrets/*.enc.yaml fallback → fail
##   - Temp key file is written to /tmp with 0600, wiped immediately after decryption
##   - Decryption failure causes immediate exit 1 (fail-fast)
##   - No secret values appear in audit log or stdout
## @rationale Key on disk = permanent compromise; transient env/tmpfile eliminates exposure. Fallback paths enable bootstrap without pre-configured env vars.
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][decrypt-secrets][main] Starting secrets decryption" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../internal/audit/audit.sh" 2>/dev/null || true
if [[ -z "${PLATFORM_ROOT:-}" ]]; then
    PLATFORM_ROOT="$(
        cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd \
        || echo "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")"
    )"
fi

__LOG_PREFIX="decrypt"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

# region RESOLVE_SECRETS_FILE
## @purpose  Search /opt/node-configs/secrets/*.enc.yaml as fallback when SECRETS_FILE is unset or missing
## @param    (none) — reads global search path
## @output   Prints resolved file path to stdout
## @io       Calls log_step for status reporting
## @complexity O(n) where n = number of matching .enc.yaml files
resolve_secrets_file() {
    local search_path="/opt/node-configs/secrets"
    local matches=()

    if [[ ! -d "$search_path" ]]; then
        log_step "resolve-secrets" "FAIL" "Fallback dir not found: ${search_path}"
        exit 1
    fi

    # Temporarily enable nullglob so unmatched glob yields empty array (not literal pattern)
    shopt -s nullglob
    matches=( "$search_path"/*.enc.yaml )
    shopt -u nullglob

    if [[ ${#matches[@]} -eq 0 ]]; then
        log_step "resolve-secrets" "FAIL" "No *.enc.yaml files found in ${search_path}"
        exit 1
    fi

    if [[ ${#matches[@]} -gt 1 ]]; then
        log_step "resolve-secrets" "WARN" "Multiple *.enc.yaml files found in ${search_path} — using first: ${matches[0]}"
    fi

    log_step "resolve-secrets" "OK" "Resolved: ${matches[0]}"
    echo "${matches[0]}"
}
# endregion RESOLVE_SECRETS_FILE

# region VALIDATE_ENV
validate_env() {
    if ! command -v sops &>/dev/null; then
        log_step "validate" "FAIL" "sops command not found"
        exit 1
    fi

    # Fallback chain: AGE_SECRET_KEY → SOPS_AGE_KEY
    if [[ -z "${AGE_SECRET_KEY:-}" ]]; then
        if [[ -n "${SOPS_AGE_KEY:-}" ]]; then
            export AGE_SECRET_KEY="$SOPS_AGE_KEY"
            log_step "validate" "OK" "AGE_SECRET_KEY resolved via SOPS_AGE_KEY fallback"
        else
            log_step "validate" "FAIL" "Neither AGE_SECRET_KEY nor SOPS_AGE_KEY is set"
            exit 1
        fi
    else
        log_step "validate" "OK" "AGE_SECRET_KEY is set directly"
    fi

    # SECRETS_FILE: exact match → fallback search → fail
    if [[ -n "${SECRETS_FILE:-}" ]]; then
        if [[ -f "$SECRETS_FILE" ]]; then
            log_step "validate" "OK" "SECRETS_FILE found: ${SECRETS_FILE}"
        else
            log_step "validate" "WARN" "SECRETS_FILE=${SECRETS_FILE} not found — searching /opt/node-configs/secrets/"
            SECRETS_FILE="$(resolve_secrets_file)"
            export SECRETS_FILE
        fi
    else
        log_step "validate" "INFO" "SECRETS_FILE not set — searching /opt/node-configs/secrets/"
        SECRETS_FILE="$(resolve_secrets_file)"
        export SECRETS_FILE
    fi

    log_step "validate" "OK" "Env validated: SECRETS_FILE=${SECRETS_FILE}"
}
# endregion VALIDATE_ENV

# region WRITE_TEMP_KEY
write_temp_key() {
    local tmp_key
    tmp_key="$(mktemp /tmp/platform-age-key.XXXXXX)"
    chmod 0600 "$tmp_key"
    printf '%s\n' "$AGE_SECRET_KEY" > "$tmp_key"
    echo "$tmp_key"
}
# endregion WRITE_TEMP_KEY

# region WIPE_TEMP_KEY
wipe_temp_key() {
    local key_file="$1"
    if [[ -f "$key_file" ]]; then
        dd if=/dev/zero of="$key_file" bs=1 count="$(stat -c%s "$key_file" 2>/dev/null || echo 64)" &>/dev/null 2>&1 || true
        rm -f "$key_file"
        log_step "wipe-key" "DONE" "Temp age key file wiped: ${key_file}"
    fi
}
# endregion WIPE_TEMP_KEY

# region DECRYPT
decrypt_secrets() {
    local tmp_key="$1"
    local output_file="$2"

    log_step "decrypt" "START" "Decrypting ${SECRETS_FILE}"

    if ! SOPS_AGE_KEY_FILE="$tmp_key" sops --decrypt "$SECRETS_FILE" > "$output_file"; then
        log_step "decrypt" "FAIL" "sops decryption failed"
        wipe_temp_key "$tmp_key"
        rm -f "$output_file" 2>/dev/null || true
        exit 1
    fi

    log_step "decrypt" "DONE" "Secrets decrypted (${SECRETS_FILE} → ${output_file})"
}
# endregion DECRYPT

# region EXPORT_ENV
export_secrets_to_env() {
    local decrypted_file="$1"
    local export_script="${2:-/run/platform/secrets.env}"

    mkdir -p "$(dirname "$export_script")"
    chmod 0700 "$(dirname "$export_script")"
    : > "$export_script"
    chmod 0600 "$export_script"

    local line key value q safe_value
    q="'\\''"
    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line// }" ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*):[[:space:]]*(.+)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
            safe_value="${value//\'/$q}"
            printf "%s='%s'\n" "$key" "$safe_value" >> "$export_script"
        fi
    done < "$decrypted_file"

    log_step "export" "DONE" "Secrets exported to env file: ${export_script}"
    echo "$export_script"
}
# endregion EXPORT_ENV

# region CLEANUP_ALL
## @purpose  Unified cleanup: wipe temp age key AND remove plaintext output file
## @brief     Called on EXIT/INT/TERM via single trap. Guarantees key is NEVER
##            left on disk regardless of exit path (success, error, signal).
cleanup_all() {
    local key_file="$1"
    local out_file="$2"
    wipe_temp_key "${key_file}"
    rm -f "${out_file}" 2>/dev/null || true
}
# endregion CLEANUP_ALL

main() {
    echo "[IMP:8][decrypt-secrets][main] Validating environment" >&2
    validate_env

    local tmp_key output_file
    tmp_key="$(write_temp_key)"
    output_file="$(mktemp /tmp/platform-secrets-plain.XXXXXX)"
    chmod 0600 "$output_file"

    # Single trap — NEVER overwritten inside main()
    trap 'cleanup_all "${tmp_key}" "${output_file}"' EXIT INT TERM

    decrypt_secrets "$tmp_key" "$output_file"

    # Explicit wipe after decrypt, before export (trap still covers export failure)
    wipe_temp_key "$tmp_key"

    local env_file
    env_file="$(export_secrets_to_env "$output_file" "${SECRETS_ENV_FILE:-/run/platform/secrets.env}")"

    rm -f "$output_file"
    trap - EXIT INT TERM

    log_step "main" "DONE" "Secrets ready at: ${env_file} (key and plaintext wiped)"
    echo "[IMP:9][decrypt-secrets][main] Secrets ready: ${env_file}" >&2
}

main "$@"
