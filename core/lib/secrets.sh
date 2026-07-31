#!/usr/bin/env bash
# GREP_SUMMARY: secrets library decrypt-secrets ensure-secrets sops age secrets.env auto-generate htpasswd apr1 salt cleanup-proxy thin-facade
# STRUCTURE: ┌NODE_CONFIGS_DIR/secrets/*.enc.yaml┐ → step_10 → ◇ age key → ⊕ decrypt_secrets.py → ⊕ secrets_manager.py cleanup → ⎋ step_done
#            └ _ensure_htpasswd_generated → ⊕ secrets_manager.py htpasswd (apr1, salt-idempotent) → ⎋ export HTPASSWD_FILE
#            └ step_12b_ensure_secrets → ⊕ secrets_manager.py ensure → ⎋ step_done
# region MODULE_CONTRACT
## @purpose  Thin shell facades over Python secrets core (DevPlan 102): step_10 decrypt
##           (decrypt_secrets.py + proxy cleanup via secrets_manager.py cleanup), htpasswd
##           generation, step_12b ensure. All business logic lives in Python.
## @scope    Three facades: step_10_decrypt_secrets, _ensure_htpasswd_generated, step_12b_ensure_secrets
## @invariants
##   - source-safe: declare -f stub-guard (TRAP[BUG] 2026-07-23) — no-op step_start/done/skip (AC4)
##   - AGE_SECRET_KEY ← SOPS_AGE_KEY fallback, exit 1 when missing (AC5/AC6)
##   - htpasswd idempotency delegated to secrets_manager.py salt-extraction (DevPlan 102)
## @rationale  DevPlan 102: 291→≤85 LOC — Python-first language policy (AGENTS.md).
## @changes   LAST_CHANGE: 2026-07-31 · DevPlan 102 — thin facades, legacy proxy-helper removed
## @modulemap — step_10_decrypt_secrets      [W:12] Facade → decrypt + cleanup
##             — _ensure_htpasswd_generated  [W:12] Facade → secrets_manager.py htpasswd
##             — step_12b_ensure_secrets     [W:10] Facade → secrets_manager.py ensure
# endregion MODULE_CONTRACT

# ⚠️ TRAP[BUG] · 2026-07-23 · P0 · step_start/done/skip undefined when sourced standalone
# · Symptom: state_machine.py runs bash -c "source secrets.sh && step_10..." without node-lifecycle.sh
# · Fix: define fallback no-op wrappers when not already defined (real defs take precedence).
# · Rev: if more orchestrator-specific functions are needed, extract to shared lib.
if ! declare -f step_start >/dev/null 2>&1; then
    step_start() { log_step "$1" "START" "${2:-}"; }
    step_done()  { log_step "$1" "DONE"  "${2:-}"; }
    step_skip()  { log_step "$1" "SKIP"  "${2:-}"; }
fi

# region FUNC_step_10_decrypt_secrets
## @purpose  Decrypt SOPS/age secrets + cleanup proxy vars. Thin facade (DevPlan 102 TASK-4).
## @io       in: NODE_CONFIGS_DIR, NODE_NAME, AGE_SECRET_KEY/SOPS_AGE_KEY, SECRETS_ENV_FILE, TOR_ENABLED, CORE_DIR
##           out: secrets.env written; SECRETS_FILE exported; exit 1 if AGE_SECRET_KEY missing (AC5)
step_10_decrypt_secrets() {
    step_start "decrypt-secrets" "Decrypting SOPS/age secrets"
    local enc_file="${NODE_CONFIGS_DIR:-/opt/node-configs}/secrets/${NODE_NAME}.enc.yaml"
    [[ -f "$enc_file" ]] || { step_skip "decrypt-secrets" "No encrypted secrets file at ${enc_file}"; return 0; }
    [[ -z "${AGE_SECRET_KEY:-}" ]] && [[ -n "${SOPS_AGE_KEY:-}" ]] && export AGE_SECRET_KEY="$SOPS_AGE_KEY"
    [[ -z "${AGE_SECRET_KEY:-}" ]] && { log_step "decrypt-secrets" "FAIL" "AGE_SECRET_KEY not set but secrets file exists — aborting"; exit 1; }
    export SECRETS_FILE="$enc_file"
    python3 "${CORE_DIR}/internal/secrets/decrypt_secrets.py" || exit 1
    python3 "${CORE_DIR}/internal/bootstrap/lifecycle/secrets_manager.py" cleanup \
        --secrets-env "${SECRETS_ENV_FILE:-/run/platform/secrets.env}" --tor-enabled "${TOR_ENABLED:-false}" || exit 1
    step_done "decrypt-secrets" "Secrets decrypted (key wiped)"
}
# endregion FUNC_step_10_decrypt_secrets

# region FUNC__ensure_htpasswd_generated
## @purpose  Generate /run/platform/.htpasswd-platform from master credentials. Thin facade
##           → secrets_manager.py htpasswd (APR1 salt-extraction idempotency, DevPlan 102 TASK-5).
## @io       in: PLATFORM_MASTER_EMAIL, PLATFORM_MASTER_PASSWORD, HTPASSWD_FILE, CORE_DIR
##           out: htpasswd file written; HTPASSWD_FILE exported; return 1 if credentials missing
_ensure_htpasswd_generated() {
    local email="${PLATFORM_MASTER_EMAIL:-}" password="${PLATFORM_MASTER_PASSWORD:-}"
    if [[ -z "$email" || -z "$password" ]]; then
        log_step "htpasswd" "WARN" "Credentials not set — skipping htpasswd"
        return 1
    fi
    python3 "${CORE_DIR:-/opt/platform/core}/internal/bootstrap/lifecycle/secrets_manager.py" htpasswd \
        --email "$email" --password "$password" \
        --htpasswd-file "${HTPASSWD_FILE:-/run/platform/.htpasswd-platform}" \
        || { log_step "htpasswd" "FAIL" "secrets_manager.py htpasswd failed"; return 1; }
    export HTPASSWD_FILE="${HTPASSWD_FILE:-/run/platform/.htpasswd-platform}"
}
# endregion FUNC__ensure_htpasswd_generated

# region FUNC_step_12b_ensure_secrets
## @purpose  CLI facade → secrets_manager.py ensure (DevPlan 053 Wave 2 P1). Reusable for node-update.
## @io       in: PATHS_CORE_DIR, SECRETS_ENV_FILE → out: delegated; always returns 0 (WARN on failure)
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
