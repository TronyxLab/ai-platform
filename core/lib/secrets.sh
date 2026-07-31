#!/usr/bin/env bash
# GREP_SUMMARY: secrets library decrypt-secrets ensure-secrets sops age secrets.env auto-generate litellm langfuse nextauth
# STRUCTURE: ┌NODE_CONFIGS_DIR/secrets/*.enc.yaml┐ → step_10_decrypt_secrets → ◇ ┌age key┐ → ⊕ python3 decrypt_secrets.py → ☰ source secrets.env → ◇ ┌TOR_ENABLED?┐ → ⊕ sed proxy vars → ⎋ step_done
#            └ step_12b_ensure_secrets → ◇ ┌manifest + secrets_env┐ → ⚡ python3 secrets_manager.py ensure → ⎋ step_done
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
##           — step_12b_ensure_secrets(): CLI facade — delegates to
##             secrets_manager.py ensure (DevPlan 053 Wave 2 P1)
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
##           — step_12b delegates to secrets_manager.py ensure (DevPlan 053)
##           — LDD log lines to stderr: [IMP:5-10][bootstrap][...] ...
##           — On missing AGE_SECRET_KEY: exit 1 (critical)
## @links    — USED_BY: core/internal/bootstrap/node-lifecycle.sh
##           — EXTRACTED_FROM: same file, lines 340-445
##           — CALLS: python3 CORE_DIR/internal/secrets/decrypt_secrets.py
##           — CALLS: sed (proxy var cleanup from secrets.env)
##           — CALLS: python3 secrets_manager.py ensure (step_12b facade, DevPlan 053)
## @invariants — Functions assume step_start/done/skip/log_step are defined
##               in the sourcing script (orchestrator.sh or node-update)
##             - unset_platform_proxy is a private helper (name prefixed with
##               underscore at function definition, not in caller — preserved
##               as-is from orchestrator.sh where it was top-level)
##             - step_10_decrypt_secrets calls exit 1 if AGE_SECRET_KEY missing
##               and a secrets file exists (critical — bootstrap cannot continue
##               without decrypting real secrets)
##             - step_12b_ensure_secrets is a CLI facade — delegates all
##               business logic to secrets_manager.py ensure (DevPlan 053)
## @rationale Q: Why a separate library instead of staying in orchestrator.sh?
##            A: Pure extraction for modularity — secrets logic is self-contained
##            and reusable across multiple bootstrap scripts (orchestrator, node-update).
##            Keeping the functions identical ensures zero behavioral regressions.
## @changes   LAST_CHANGE: 2026-07-25 · P1 (DevPlan 053) — Reduced step_12b_ensure_secrets
##             to CLI facade calling secrets_manager.py ensure
## @modulemap — unset_platform_proxy     [W:3]   Private helper — unset host proxy vars
##             — step_10_decrypt_secrets [W:46]  Decrypt + source SOPS/age secrets
##             — step_12b_ensure_secrets [W:~10] CLI facade → secrets_manager.py ensure
## @usecases  — node-lifecycle.sh sources this file for step_10 and step_12b in main()
##             — node-update.sh can source this file to ensure secrets before deploy
# endregion MODULE_CONTRACT
# GREP_SUMMARY: secrets, decrypt, sops, age, encrypt, secrets.env, ensure, validate, litellm, langfuse, nextauth, salt, auto-generate
# STRUCTURE: ▶ ┌unset_platform_proxy → unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy┐
#            ▶ ┌step_10_decrypt_secrets → ◇ ┌enc_file?┐ → ◇ ┌AGE_SECRET_KEY? + SOPS_AGE_KEY fallback?┐ → ⊕ python3 decrypt_secrets.py → ☰ source secrets.env → ◇ ┌TOR_ENABLED?┐ → ⊕ sed HTTP_PROXY/HTTPS_PROXY┐ → ⎋ step_done
#            ▶ ┌step_12b_ensure_secrets → ◇ ┌manifest + secrets_env┐ → ⚡ python3 secrets_manager.py ensure → ⎋ step_done

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
## @dependencies — python3 CORE_DIR/internal/secrets/decrypt_secrets.py
##               — sed (for proxy var cleanup)
##               — step_start/step_done/step_skip/log_step from consumer
## @invariants — If no encrypted file exists → skip (return 0)
##             - AGE_SECRET_KEY fallback: SOPS_AGE_KEY accepted before aborting
##             - exit 1 if AGE_SECRET_KEY missing AND secrets file exists
##               (critical — cannot continue without decrypting)
##             - unset_platform_proxy() called before processing sourced vars
##             - sed -i.bak used for portable in-place editing (macOS/Linux)
## @rationale Pure extraction from orchestrator.sh — behavior preserved exactly.

# ⚠️ TRAP[BUG] · 2026-07-23 · P0 · step_start/done/skip undefined when sourced standalone
# · Symptom: state_machine.py _decrypt_secrets runs bash -c "source secrets.sh && step_10_decrypt_secrets"
#   without node-lifecycle.sh context → step_start/step_done/step_skip are undefined.
# · Fix: define fallback wrappers (no-ops) when not already defined.
#   When sourced from node-lifecycle.sh, the real definitions take precedence.
# · Rev: if more orchestrator-specific functions are needed, extract to shared lib.
if ! declare -f step_start >/dev/null 2>&1; then
    step_start() { log_step "$1" "START" "${2:-}"; }
    step_done()  { log_step "$1" "DONE"  "${2:-}"; }
    step_skip()  { log_step "$1" "SKIP"  "${2:-}"; }
fi

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
    log_step "decrypt-secrets" "INFO" "Delegating to decrypt_secrets.py (Python core)"
    python3 "${CORE_DIR}/internal/secrets/decrypt_secrets.py"

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
# HTPASSWD: Generate platform htpasswd from master credentials
# ═══════════════════════════════════════════════════════════════════
# region FUNC__ensure_htpasswd_generated
## @purpose  Generate /run/platform/.htpasswd-platform from PLATFORM_MASTER_EMAIL
##           and PLATFORM_MASTER_PASSWORD. Delegates APR1 hashing to shared/crypto.py
##           (DevPlan 078 T5). Idempotent — checks existing file before writing.
## @param    (none — depends on env vars)
## @io       in:  PLATFORM_MASTER_EMAIL (env), PLATFORM_MASTER_PASSWORD (env)
##            in:  CORE_DIR (env, for crypto.py path)
##            out: HTPASSWD_FILE exported
##            effect: creates/updates /run/platform/.htpasswd-platform
##            return: 0 on success, 1 if credentials missing or crypto.py fails
## @complexity O(1) + python3 subprocess
## @dependencies — python3 (for shared/crypto.py APR1 hashing)
## @invariants — See FUNC__ensure_htpasswd_generated
## @rationale DevPlan 078 T5: delegate openssl passwd -apr1 to shared/crypto.py,
##            eliminating duplicate APR1 hashing logic across shell and Python.
_ensure_htpasswd_generated() {
    local email="${PLATFORM_MASTER_EMAIL:-}"
    local password="${PLATFORM_MASTER_PASSWORD:-}"
    local htpasswd_file="${HTPASSWD_FILE:-/run/platform/.htpasswd-platform}"
    local crypto_script="${CORE_DIR:-/opt/platform/core}/internal/shared/crypto.py"

    if [[ -z "$email" ]]; then
        log_step "htpasswd" "WARN" "PLATFORM_MASTER_EMAIL not set — skipping htpasswd generation"
        return 1
    fi
    if [[ -z "$password" ]]; then
        log_step "htpasswd" "WARN" "PLATFORM_MASTER_PASSWORD not set — skipping htpasswd generation"
        return 1
    fi

    # ── Generate htpasswd entry via shared/crypto.py ──
    local entry
    entry=$(python3 "$crypto_script" entry "$email" "$password" 2>/dev/null) || {
        log_step "htpasswd" "FAIL" "shared/crypto.py entry failed — cannot generate htpasswd"
        return 1
    }

    mkdir -p "$(dirname "$htpasswd_file")" 2>/dev/null || true

    # ── Idempotency: check existing file ──
    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Случайная соль ломает идемпотентность
    # · Symptom: повторный _ensure_htpasswd_generated перезаписывает файл (md5 меняется) —
    #   test_htpasswd_generation_idempotent RED. crypto.py entry без соли = случайный salt
    #   каждый вызов → `existing == entry` никогда не равен → вечная перезапись.
    # · Fix: при существующем файле извлекаем соль ($apr1$SALT$...), пересчитываем entry
    #   с фиксированной солью (детерминировано, контракт crypto.py п.2) и сравниваем.
    if [[ -f "$htpasswd_file" ]]; then
        local existing
        existing=$(cat "$htpasswd_file" 2>/dev/null)
        local existing_salt
        existing_salt=$(printf '%s' "$existing" | cut -d'$' -f3)
        if [[ -n "$existing_salt" ]]; then
            local expected_entry
            expected_entry=$(python3 "$crypto_script" entry "$email" "$password" --salt "$existing_salt" 2>/dev/null) || {
                log_step "htpasswd" "FAIL" "shared/crypto.py entry failed — cannot verify htpasswd"
                return 1
            }
            if [[ "$existing" == "$expected_entry" ]]; then
                export HTPASSWD_FILE="$htpasswd_file"
                log_step "htpasswd" "INFO" "htpasswd file exists and matches credentials — no-op"
                return 0
            fi
            log_step "htpasswd" "INFO" "htpasswd file exists but credentials changed — regenerating"
        else
            # Соль не извлекается (повреждённая запись) — пересоздаём
            log_step "htpasswd" "WARN" "htpasswd file exists but salt unparseable — regenerating"
        fi
    fi

    # ── Write htpasswd file ──
    echo "$entry" > "$htpasswd_file"
    chmod 644 "$htpasswd_file" 2>/dev/null || true

    export HTPASSWD_FILE="$htpasswd_file"
    log_step "htpasswd" "INFO" "htpasswd file generated: ${htpasswd_file} (user: ${email})"
    return 0
}
# endregion FUNC__ensure_htpasswd_generated

# ═══════════════════════════════════════════════════════════════════
# STEP 12b: CLI facade — ensure required secrets (post-decrypt, pre-deploy)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_step_12b_ensure_secrets
## @purpose  CLI facade — delegates to secrets_manager.py ensure for validating
##           and generating required platform secrets. All business logic
##           ported to Python per DevPlan 053 Wave 2 P1.
##
##           Reusable pattern for node-update:
##             source lib/secrets.sh
##             step_12b_ensure_secrets
##
## @param    (none — depends on env vars)
## @io       in:  PATHS_CORE_DIR (env, set via paths.sh — for manifest/secrets_manager path)
##            in:  SECRETS_ENV_FILE (env, default: /run/platform/secrets.env)
##            out: delegated to secrets_manager.py ensure
##            return: 0 (always — non-critical warnings only)
## @complexity O(1) — delegation only
## @dependencies — python3 (for secrets_manager.py)
##               — core/internal/bootstrap/lifecycle/secrets_manager.py
##               — step_start/step_done/log_step from consumer
## @invariants — Delegates all secret validation/generation to secrets_manager.py
##             - Falls back to WARN log if secrets_manager.py is missing or fails
##             - Always returns 0 — non-critical warnings only
## @rationale DevPlan 053 Wave 2 P1 — reduce ~100 LOC shell logic to thin CLI facade.
##            Business logic ported to Python for testability and maintainability.
# P1: Reduced to CLI facade — logic ported to secrets_manager.py (DevPlan 053)
step_12b_ensure_secrets() {
    step_start "ensure-secrets" "Validating and generating required secrets"
    local manifest="${PATHS_CORE_DIR:-/opt/platform/core}/secrets-manifest.yaml"
    local secrets_env="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
    python3 "${PATHS_CORE_DIR:-/opt/platform/core}/internal/bootstrap/lifecycle/secrets_manager.py" \
        ensure --manifest "$manifest" --secrets-env "$secrets_env" 2>&1 || {
        log_step "ensure-secrets" "WARN" "secrets_manager.py failed"
    }
    step_done "ensure-secrets" "Secrets validation complete"
}
# endregion FUNC_step_12b_ensure_secrets
