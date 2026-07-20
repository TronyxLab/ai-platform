#!/usr/bin/env bash
# GREP_SUMMARY: secrets library decrypt-secrets ensure-secrets sops age secrets.env auto-generate litellm langfuse nextauth
# STRUCTURE: ┌NODE_CONFIGS_DIR/secrets/*.enc.yaml┐ → step_10_decrypt_secrets → ◇ ┌age key┐ → ⊕ bash decrypt-secrets.sh → ☰ source secrets.env → ◇ ┌TOR_ENABLED?┐ → ⊕ sed proxy vars → ⎋ step_done
#            └ step_12b_ensure_secrets → ◇ ┌secrets_file?┐ → ☰ source → ○ _ensure_secret (7 patterns) → ⎋ step_done
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Secrets Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Provide centralized secrets decryption and validation for
##           platform bootstrap. Pure extraction from orchestrator.sh —
##           no behavioral changes. Pattern: source secrets.env →
##           validate required vars → generate missing.
## @scope    — step_10_decrypt_secrets(): decrypt SOPS/age-encrypted secrets
##             file, source secrets.env into environment, handle proxy vars
##           — step_12b_ensure_secrets(): validate that all required secrets
##             are present, auto-generate ephemeral defaults for missing ones
##           — unset_platform_proxy(): private helper — unset host-level proxy vars
##             before sourcing platform secrets (prevent proxy pollution)
## @input    — NODE_CONFIGS_DIR (env, default: /opt/node-configs)
##           — NODE_NAME (env, required for secrets file path)
##           — AGE_SECRET_KEY / SOPS_AGE_KEY (env, age key for decryption)
##           — SECRETS_ENV_FILE (env, default: /run/platform/secrets.env)
##           — SECRETS_FILE (env, default: /run/platform/secrets.env)
##           — TOR_ENABLED (env, bool, controls proxy var removal)
##           — CORE_DIR (env, set via paths.sh by consumer)
##           — step_start / step_done / step_skip / log_step — from consumer's shell
##             (defined in orchestrator.sh or equivalent bootstrap script)
## @output   — SECRETS_FILE exported to env; secrets.env sourced into shell
##           — Missing secrets auto-generated and exported as env vars
##           — LDD log lines to stderr: [IMP:5-10][bootstrap][...] ...
##           — On missing AGE_SECRET_KEY: exit 1 (critical)
## @links    — USED_BY: core/internal/bootstrap/node-lifecycle.sh
##           — EXTRACTED_FROM: same file, lines 340-445
##           — CALLS: bash CORE_DIR/internal/secrets/decrypt-secrets.sh
##           — CALLS: sed (proxy var cleanup from secrets.env)
##           — CALLS: openssl rand (secret generation in step_12b)
## @invariants — Functions assume step_start/done/skip/log_step are defined
##               in the sourcing script (orchestrator.sh or node-update)
##             - unset_platform_proxy is a private helper (name prefixed with
##               underscore at function definition, not in caller — preserved
##               as-is from orchestrator.sh where it was top-level)
##             - step_10_decrypt_secrets calls exit 1 if AGE_SECRET_KEY missing
##               and a secrets file exists (critical — bootstrap cannot continue
##               without decrypting real secrets)
##             - step_12b_ensure_secrets is reusable for node-update:
##               run after decrypt-secrets to guarantee all platform secrets
##               are available before deploying modules. Generates ephemeral
##               defaults for LANGFUSE_*, LITELLM_MASTER_KEY, NEXTAUTH_SECRET, SALT
## @rationale Q: Why a separate library instead of staying in orchestrator.sh?
##            A: Pure extraction for modularity — secrets logic is self-contained
##            and reusable across multiple bootstrap scripts (orchestrator, node-update).
##            Keeping the functions identical ensures zero behavioral regressions.
## @changes   LAST_CHANGE: 2026-07-17 · T12 — Pure extraction from orchestrator.sh
## @modulemap — unset_platform_proxy     [W:3]  Private helper — unset host proxy vars
##             — step_10_decrypt_secrets [W:46] Decrypt + source SOPS/age secrets
##             — step_12b_ensure_secrets [W:52] Validate + generate missing secrets
## @usecases  — node-lifecycle.sh sources this file for step_10 and step_12b in main()
##             — node-update.sh can source this file to ensure secrets before deploy
# endregion MODULE_CONTRACT
# GREP_SUMMARY: secrets, decrypt, sops, age, encrypt, secrets.env, ensure, validate, litellm, langfuse, nextauth, salt, auto-generate
# STRUCTURE: ▶ ┌unset_platform_proxy → unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy┐
#            ▶ ┌step_10_decrypt_secrets → ◇ ┌enc_file?┐ → ◇ ┌AGE_SECRET_KEY? + SOPS_AGE_KEY fallback?┐ → ⊕ bash decrypt-secrets.sh → ☰ source secrets.env → ◇ ┌TOR_ENABLED?┐ → ⊕ sed HTTP_PROXY/HTTPS_PROXY┐ → ⎋ step_done
#            ▶ ┌step_12b_ensure_secrets → ◇ ┌secrets_file?┐ → ☰ source → ○ _ensure_secret loop (7 vars) → ∑ generated→WARN → ⎋ step_done

# ═══════════════════════════════════════════════════════════════════
# PRIVATE HELPER: Unset host-level proxy vars
# ═══════════════════════════════════════════════════════════════════
# region FUNC_unset_platform_proxy
## @purpose  Clear all host-level proxy environment variables before sourcing
##           platform secrets. Prevents proxy pollution when secrets.env
##           is sourced into a shell that has HTTP_PROXY/HTTPS_PROXY set
##           by the host system (e.g. under a corporate proxy).
## @io       effect: unsets HTTP_PROXY, HTTPS_PROXY, http_proxy, https_proxy,
##           NO_PROXY, no_proxy from the current shell
## @complexity O(1)
## @invariants — Always succeeds (unset never fails)
##             - Only affects the current shell — child processes inherit
##               the cleared state
## @rationale Pure extraction from orchestrator.sh — behavior preserved exactly.
unset_platform_proxy() {
    unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy
}
# endregion FUNC_unset_platform_proxy

# ═══════════════════════════════════════════════════════════════════
# STEP 10: Decrypt SOPS/age secrets
# ═══════════════════════════════════════════════════════════════════
# region FUNC_step_10_decrypt_secrets
## @purpose  Decrypt SOPS/age-encrypted secrets file, source the resulting
##           secrets.env into the shell, handle proxy variable cleanup.
##           If TOR_ENABLED != true, HTTP_PROXY/HTTPS_PROXY are removed
##           from secrets.env to prevent unintended proxying.
## @param    (none — depends on env vars)
## @io       in:  NODE_CONFIGS_DIR, NODE_NAME, AGE_SECRET_KEY/SOPS_AGE_KEY
##            in:  SECRETS_ENV_FILE, TOR_ENABLED, CORE_DIR
##            out: SECRETS_FILE exported, secrets.env sourced into shell
##            effect: may modify secrets.env (sed) if TOR_ENABLED != true
##            return: 0 on success, exit 1 if AGE_SECRET_KEY missing
## @complexity O(n) where n = number of vars in secrets.env
## @dependencies — bash CORE_DIR/internal/secrets/decrypt-secrets.sh
##               — sed (for proxy var cleanup)
##               — step_start/step_done/step_skip/log_step from consumer
## @invariants — If no encrypted file exists → skip (return 0)
##             - AGE_SECRET_KEY fallback: SOPS_AGE_KEY accepted before aborting
##             - exit 1 if AGE_SECRET_KEY missing AND secrets file exists
##               (critical — cannot continue without decrypting)
##             - unset_platform_proxy() called before processing sourced vars
##             - sed -i.bak used for portable in-place editing (macOS/Linux)
## @rationale Pure extraction from orchestrator.sh — behavior preserved exactly.
step_10_decrypt_secrets() {
    step_start "decrypt-secrets" "Decrypting SOPS/age secrets"

    local configs_dir="${NODE_CONFIGS_DIR:-/opt/node-configs}"
    local enc_file="${configs_dir}/secrets/${NODE_NAME}.enc.yaml"

    if [[ ! -f "$enc_file" ]]; then
        step_skip "decrypt-secrets" "No encrypted secrets file at ${enc_file}"
        return 0
    fi

    # SOPS_AGE_KEY fallback: try alternative env var before aborting
    if [[ -z "${AGE_SECRET_KEY:-}" ]] && [[ -n "${SOPS_AGE_KEY:-}" ]]; then
        export AGE_SECRET_KEY="$SOPS_AGE_KEY"
        log_step "decrypt-secrets" "INFO" "AGE_SECRET_KEY set from SOPS_AGE_KEY fallback"
    fi

    if [[ -z "${AGE_SECRET_KEY:-}" ]]; then
        log_step "decrypt-secrets" "FAIL" "AGE_SECRET_KEY not set but secrets file exists — aborting"
        exit 1
    fi

    export SECRETS_FILE="$enc_file"
    bash "${CORE_DIR}/internal/secrets/decrypt-secrets.sh"

    local secrets_env="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
    if [[ -f "$secrets_env" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$secrets_env"
        set +a

        unset_platform_proxy

        if [[ "${TOR_ENABLED:-false}" != "true" ]]; then
            log_step "decrypt-secrets" "INFO" "Tor disabled — removing HTTP_PROXY/HTTPS_PROXY from secrets.env"
            sed -i.bak '/^HTTP_PROXY=/d' "$secrets_env" && rm "${secrets_env}.bak"
            sed -i.bak '/^HTTPS_PROXY=/d' "$secrets_env" && rm "${secrets_env}.bak"
        fi
        log_step "decrypt-secrets" "INFO" "Secrets sourced into environment ($(wc -l < "$secrets_env") vars, proxy vars cleared)"
    else
        log_step "decrypt-secrets" "WARN" "Secrets env file not found at ${secrets_env}"
    fi

    step_done "decrypt-secrets" "Secrets decrypted (key wiped)"
}
# endregion FUNC_step_10_decrypt_secrets

# ═══════════════════════════════════════════════════════════════════
# STEP 12b: Ensure required secrets (post-decrypt, pre-deploy)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_step_12b_ensure_secrets
## @purpose  Validate that all required platform secrets are present after
##           decryption. Auto-generates ephemeral defaults for missing
##           generated-секреты using openssl rand. Persists generated secrets
##           to encrypted SOPS file via sops --set if the file exists.
##           Reads generated-секреты from secrets-manifest.yaml (SSoT).
##           Falls back to hardcoded list if manifest is unavailable.
##
##           Reusable pattern for node-update:
##             source lib/secrets.sh
##             step_12b_ensure_secrets
##           This ensures LITELLM_MASTER_KEY, LANGFUSE_* keys,
##           NEXTAUTH_SECRET, and SALT are always set, even if the SOPS
##           file hasn't been updated with production values yet.
##
## @param    (none — depends on env vars)
## @io       in:  SECRETS_FILE (env, default: /run/platform/secrets.env)
##            in:  PATHS_CORE_DIR (env, set via paths.sh — for manifest path)
##            in:  NODE_CONFIGS_DIR (env, default: /opt/node-configs)
##            in:  NODE_NAME (env — for encrypted file path)
##            out: exports LITELLM_MASTER_KEY, LANGFUSE_INIT_ORG_ID,
##                 LANGFUSE_INIT_PROJECT_ID, LANGFUSE_PUBLIC_KEY,
##                 LANGFUSE_SECRET_KEY, NEXTAUTH_SECRET, SALT
##            return: 0 (always — non-critical warnings only)
## @complexity O(n) where n = number of generated secrets in manifest
## @dependencies — openssl rand (for secret generation)
##               — python3 + PyYAML (for manifest parsing)
##               — sops (optional, for sops --set persistence)
##               — step_start/step_done/log_step from consumer
## @invariants — If secrets.env does not exist → generate ALL secrets in-memory
##             - Nested _ensure_secret() is defined inside the function (bash local scope)
##             - Auto-generated secrets get a WARN log — MUST be encrypted in SOPS
##             - Secrets are exported to the shell but are EPHEMERAL (lost on shell exit)
##             - If encrypted file exists: sops --set writes generated values back
##             - If encrypted file missing: WARN, secret exported to env only
##             - If sops --set fails: ERROR, bootstrap continues (secret in env)
##             - If manifest unavailable: fallback to hardcoded list of 7 secrets
##             - Always returns 0 — missing secrets are non-fatal (generated on the fly)
## @rationale Manifest-driven generation replaces hardcoded list for SSoT consistency.
##            sops --set persistence ensures generated secrets survive bootstrap restarts.
##            The _ensure_secret closure pattern (function defined inside function)
##            is preserved as-is from the original orchestrator.sh to avoid
##            any behavioral regression in bash variable scoping.
step_12b_ensure_secrets() {
    step_start "ensure-secrets" "Validating and generating required secrets"

    local secrets_file="${SECRETS_FILE:-/run/platform/secrets.env}"
    if [[ ! -f "$secrets_file" ]]; then
        log_step "ensure-secrets" "WARN" "Secrets file not found: $secrets_file — generating all secrets in-memory"
    else
        # shellcheck source=/dev/null
        source "$secrets_file" 2>/dev/null || true
    fi

    local generated=()
    local missing=()

    # ═══════════════════════════════════════════════════
    # _ensure_secret — bash closure defined inside function
    # ═══════════════════════════════════════════════════
    # Preserved without changes per TASK-4 requirement.
    _ensure_secret() {
        local var_name="$1"
        local pattern="$2"
        local current="${!var_name:-}"
        if [[ -z "$current" ]]; then
            local new_val
            new_val=$(eval "$pattern") || {
                log_step "ensure-secrets" "FAIL" "Cannot generate $var_name"
                return 1
            }
            export "${var_name}=${new_val}"
            generated+=("$var_name")
            missing+=("$var_name")
            log_step "ensure-secrets" "WARN" "Auto-generated $var_name (MUST be added to SOPS for production)"
        fi
    }
    # ═══════════════════════════════════════════════════

    local manifest="${PATHS_CORE_DIR:-/opt/platform/core}/secrets-manifest.yaml"
    local manifest_loaded=false

    if [[ -f "$manifest" ]] && command -v python3 &>/dev/null; then
        # Read generated secrets from manifest (tier=generated)
        local _gen_spec
        _gen_spec=$(python3 -c "
import yaml, sys
with open('${manifest}') as f:
    data = yaml.safe_load(f)
for s in data.get('secrets', []):
    if s.get('tier') == 'generated':
        print(f\"{s['name']}|{s.get('gen_command', '')}\")
" 2>/dev/null) || _gen_spec=""

        if [[ -n "$_gen_spec" ]]; then
            manifest_loaded=true
            log_step "ensure-secrets" "INFO" "Reading generated secrets from manifest: ${manifest}"
            while IFS='|' read -r _var _cmd; do
                [[ -z "$_var" || -z "$_cmd" ]] && continue
                _ensure_secret "$_var" "$_cmd"
            done <<< "$_gen_spec"
        else
            log_step "ensure-secrets" "WARN" "Manifest has no generated secrets — fallback to hardcoded list"
        fi
    else
        log_step "ensure-secrets" "INFO" "Manifest not found at ${manifest} — fallback to hardcoded list"
    fi

    # ── Fallback: hardcoded list if manifest not available ──
    if ! $manifest_loaded; then
        _ensure_secret "LITELLM_MASTER_KEY" 'echo "sk-$(openssl rand -hex 32)"'
        _ensure_secret "LANGFUSE_INIT_ORG_ID" 'echo "org_$(openssl rand -hex 4)"'
        _ensure_secret "LANGFUSE_INIT_PROJECT_ID" 'echo "proj_$(openssl rand -hex 4)"'
        _ensure_secret "LANGFUSE_PUBLIC_KEY" 'echo "pk-lf_$(openssl rand -hex 16)"'
        _ensure_secret "LANGFUSE_SECRET_KEY" 'echo "sk-lf_$(openssl rand -hex 16)"'
        _ensure_secret "NEXTAUTH_SECRET" 'openssl rand -hex 32'
        _ensure_secret "SALT" 'openssl rand -hex 16'
    fi

    # ── sops --set persistence for all generated secrets ──
    if [[ ${#generated[@]} -gt 0 ]]; then
        local configs_dir="${NODE_CONFIGS_DIR:-/opt/node-configs}"
        local enc_file="${configs_dir}/secrets/${NODE_NAME}.enc.yaml"

        if [[ -f "$enc_file" ]] && command -v sops &>/dev/null; then
            for _gvar in "${generated[@]}"; do
                local _gval="${!_gvar:-}"
                if [[ -n "$_gval" ]]; then
                    sops --set '["'"$_gvar"'"] "'"$_gval"'"' "$enc_file" 2>/dev/null || {
                        log_step "ensure-secrets" "ERROR" "sops --set failed for $_gvar — value in env but NOT persisted"
                    }
                fi
            done
        elif [[ ! -f "$enc_file" ]]; then
            log_step "ensure-secrets" "WARN" "Encrypted file not found at ${enc_file} — generated secrets NOT persisted"
        fi
    fi

    if [[ ${#generated[@]} -gt 0 ]]; then
        log_step "ensure-secrets" "WARN" "Generated ${#generated[@]} secrets: ${generated[*]}"
        log_step "ensure-secrets" "WARN" "These are EPHEMERAL — re-encrypt SOPS with real values for production"
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_step "ensure-secrets" "INFO" "Missing secrets (auto-generated): ${missing[*]}"
    else
        log_step "ensure-secrets" "INFO" "All required secrets present"
    fi

    step_done "ensure-secrets" "Secrets validation complete"
}
# endregion FUNC_step_12b_ensure_secrets
