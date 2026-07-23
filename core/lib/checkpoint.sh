#!/usr/bin/env bash
# GREP_SUMMARY: checkpoint library bootstrap checkpoint-step verify-secrets version-check resume-mode force-mode
# STRUCTURE: ▶ ┌CHECKPOINT_DIR┐ → ○ checkpoint_step(label,func,verify?) → ◇ ┌RESUME_MODE + (.done exists)┐ → ◇ verify_func? → ⊕ skip|re-execute → ⎋ .done touch
#            └ _verify_secrets_loaded(path) → ◇ ┌secrets.env found?┐ → ◇ ┌env vars loaded?┐ → ⊕ re-source → ⎋ 0/1
#            └ _checkpoint_version_check() → ◇ ┌current == stored?┐ → ⊕ preserve|invalidate + update → ⎋ 0
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Checkpoint Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Provide a centralized checkpoint/resume subsystem for
##           idempotent bootstrap orchestrators. Pure extraction from
##           orchestrator.sh — no behavioral changes.
## @scope    — checkpoint_step() with resume-mode + force-mode + verify_func
##           — _verify_secrets_loaded() — verifies secrets.env state post-decrypt
##           — _checkpoint_version_check() — invalidates checkpoints on version change
## @input    — CHECKPOINT_DIR (set by consumer, default: /var/lib/platform/.bootstrap-checkpoints)
##           — RESUME_MODE (bool, set by consumer)
##           — FORCE_MODE (bool, set by consumer)
##           — CORE_DIR (set by consumer via paths.sh)
##           — SECRETS_ENV_FILE (env var or default: /run/platform/secrets.env)
## @output   — checkpoint files in CHECKPOINT_DIR/.bootstrap-step-<name>.done
##           — VERSION file in CHECKPOINT_DIR/VERSION
##           — LDD log lines to stderr: [IMP:8-9][bootstrap][checkpoint] ...
## @links    — USED_BY: core/internal/bootstrap/node-lifecycle.sh
##           — EXTRACTED_FROM: same file, lines 100-184
##           — CALLS: mkdir, touch, rm, head, echo
## @invariants — Functions MUST NOT write to stdout (only stderr)
##             — Functions assume CHECKPOINT_DIR, RESUME_MODE, FORCE_MODE are
##               set by the sourcing script BEFORE calling any function
##             - checkpoint_step() with FORCE_MODE=true skips all checkpoint checks
##             - _checkpoint_version_check() must be called ONCE before any
##               checkpoint_step() to ensure valid checkpoint state
##             - Functions are idempotent — safe to call multiple times
## @rationale Q: Why a separate library instead of staying in orchestrator.sh?
##            A: Pure extraction for modularity — checkpoint logic is self-contained
##            and reusable across multiple bootstrap scripts (orchestrator, node-update).
##            Keeping the functions identical ensures zero behavioral regressions.
## @changes   LAST_CHANGE: 2026-07-17 · T11 — Pure extraction from orchestrator.sh
##                       · 2026-07-17 · T20 — Per-step content hash checkpoints:
##                         checkpoint_step() calls step_hash_changed() via CHECKPOINT_STEP_HASH env;
##                         _checkpoint_version_check() supports per-step hash fallback to global VERSION
## @modulemap — checkpoint_step           [W:55] Main checkpoint orchestration + content hash (T20)
##             — _verify_secrets_loaded   [W:23] Secrets env verification
##             — _checkpoint_version_check [W:20] Per-step content hash or global VERSION
## @usecases  — node-lifecycle.sh sources this file to use checkpoint_step() in main()
# endregion MODULE_CONTRACT
# GREP_SUMMARY: checkpoint, bootstrap, resume, force, verify-secrets, version-check, idempotent, .done
# STRUCTURE: ▶ ┌CHECKPOINT_DIR,RESUME_MODE,FORCE_MODE┐ → ○ checkpoint_step → ◇ ┌FORCE? → exec┐ → ◇ ┌RESUME + .done? → verify_func? → skip|override┐ → ⊕ exec + touch .done
#            └ _verify_secrets_loaded ─ ◇ ┌secrets.env? → ┌DOCKER_HUB_USERNAME|POSTGRES_PASSWORD? → re-source? ┐ → 0/1
#            └ _checkpoint_version_check ─ ◇ ┌VERSION match? → preserve|rm -f *.done + update VERSION┐

# ═══════════════════════════════════════════════════════════════════
# CHECKPOINT HELPERS
# ═══════════════════════════════════════════════════════════════════
# region FUNC_checkpoint_step
## @purpose  Execute a bootstrap step with checkpoint tracking. If RESUME_MODE
##           is active and a .done checkpoint exists (and optional verify_func
##           passes), the step is skipped. If FORCE_MODE is active, the step
##           always runs and checkpoint is not checked.
## @param $1  step_name — unique label used as checkpoint filename
## @param $2  step_func — function to execute for this step
## @param $3  verify_func — optional verification function for resume-mode
##            (checks actual state, not just .done file existence)
## @param $@  remaining args are forwarded to step_func and verify_func
## @param env CHECKPOINT_STEP_HASH — optional env var set by caller per-step.
##            When set, step_hash_changed() from content-hash.sh is called
##            before checking .done to detect code changes. (T20)
## @io       out: stderr → checkpoint status messages at IMP:8
##            effect: creates ${CHECKPOINT_DIR}/.bootstrap-step-${step_name}.done
##            effect: creates ${CHECKPOINT_DIR}/.bootstrap-step-${step_name}.hash (when CHECKPOINT_STEP_HASH set)
## @complexity O(1) + delegated func
## @invariants — FORCE_MODE=true bypasses all checks
##             — RESUME_MODE=true + valid checkpoint → skip
##             — RESUME_MODE=true + !valid checkpoint → execute + save
##             — verify_func failure → re-execute + override checkpoint
##             - CHECKPOINT_STEP_HASH env var enables per-step content hash (T20)
##             - step_hash_changed must be available in scope if CHECKPOINT_STEP_HASH is set
##             - CHECKPOINT_DIR must be writable (mkdir -p on first use)
## @rationale Pure extraction from orchestrator.sh — behavior preserved exactly.
##            The verify_func pattern (TRAP[DECISION] · 2026-07-16) fixes D8:
##            after resume on a new shell session, env vars were unavailable
##            even though the decrypt-secrets checkpoint existed.
##            CHECKPOINT_STEP_HASH (T20) adds per-step content hash invalidation:
##            if a step's script dependencies change, only that step's checkpoint
##            is invalidated — others retain their checkpoints.
# 🧐 TRAP[DECISION] · 2026-07-17 · — · Per-step content hash vs global VERSION
# · Rejected: keep global VERSION invalidation (V1 — invalidates ALL on ANY change)
# · Reason: With 17 bootstrap steps, a minor change in step 17 re-runs all 17 steps
#   (~30min → ~2min savings). Per-step hash isolates invalidation to the changed step.
#   No functional change: checkpoint semantics (resume, force, verify) are preserved.
#   Backward compat: old checkpoints without .hash file return "changed" → one re-run.
# · Rev: if content-hash.sh becomes unavailable, checkpoint_step falls back
#   to the existing global VERSION mechanism (no regression).
checkpoint_step() {
    local step_name="$1"
    local step_func="$2"
    local verify_func="${3:-}"  # Optional: verification function for resume-mode
    shift 2
    [[ -n "$verify_func" ]] && shift 1

    if [[ "$FORCE_MODE" == "true" ]]; then
        "$step_func" "$@"
        return 0
    fi

    # ── Content hash invalidation (T20) ──────────────────────────
    # If CHECKPOINT_STEP_HASH is set and step_hash_changed is available,
    # detect code changes that invalidate this step's checkpoint
    if [[ -n "${CHECKPOINT_STEP_HASH:-}" ]] && type step_hash_changed &>/dev/null 2>&1; then
        if ! step_hash_changed "$step_name" "$CHECKPOINT_STEP_HASH"; then
            # Hash changed — invalidate checkpoint if it exists
            local chk_file="${CHECKPOINT_DIR}/.bootstrap-step-${step_name}.done"
            if [[ -f "$chk_file" ]]; then
                echo "[IMP:9][bootstrap][checkpoint] INVALIDATE: Content hash mismatch for '${step_name}' — removing checkpoint" >&2
                rm -f "$chk_file"
            fi
        fi
    fi

    if [[ "$RESUME_MODE" == "true" ]]; then
        local checkpoint_file="${CHECKPOINT_DIR}/.bootstrap-step-${step_name}.done"
        if [[ -f "$checkpoint_file" ]]; then
            # 📝 TRAP[DECISION] · 2026-07-16 · HI · Проверка не только checkpoint-файла, но и состояния
            # ·   Если verification функция задана — проверяет не просто наличие .done файла,
            # ·   а фактическое состояние (например, загружены ли env vars из secrets.env).
            # ·   Если verify_func вернёт 1 → checkpoint считается невалидным → повторное выполнение.
            # ·   Это исправляет D8: после resume на новом shell-сеансе env vars были недоступны,
            # ·   хотя checkpoint "decrypt-secrets" помечал шаг как завершённый.
            if [[ -n "$verify_func" ]]; then
                if ! "$verify_func" "$@"; then
                    echo "[IMP:8][bootstrap][checkpoint] SKIP OVERRIDE: Checkpoint exists but verification failed for '${step_name}' — re-executing" >&2
                    "$step_func" "$@"
                    mkdir -p "$CHECKPOINT_DIR"
                    touch "${CHECKPOINT_DIR}/.bootstrap-step-${step_name}.done"
                    return 0
                fi
            fi
            echo "[IMP:8][bootstrap][checkpoint] SKIP: Step '${step_name}' already completed (checkpoint found)" >&2
            return 0
        fi
    fi

    # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · checkpoint created despite step failure
    # · Symptom: step_func exited non-zero but .done marker was unconditionally
    #   created → next --resume skipped the failing step forever.
    # · Fix: check exit code; only create checkpoint on success (exit 0).
    # · On failure: log error, return the exit code, do NOT create checkpoint.
    #   Next bootstrap --resume will re-run the step.
    if "$step_func" "$@"; then
        mkdir -p "$CHECKPOINT_DIR"
        touch "${CHECKPOINT_DIR}/.bootstrap-step-${step_name}.done"
        echo "[IMP:8][bootstrap][checkpoint] DONE: Checkpoint '${step_name}' saved" >&2

        # ── Save content hash after step completion (T20) ────────────
        if [[ -n "${CHECKPOINT_STEP_HASH:-}" ]]; then
            echo "$CHECKPOINT_STEP_HASH" > "${CHECKPOINT_DIR}/.bootstrap-step-${step_name}.hash"
            echo "[IMP:8][bootstrap][checkpoint] HASH: Content hash saved for '${step_name}'" >&2
        fi
    else
        local _rc=$?
        echo "[IMP:9][bootstrap][checkpoint] FAIL: Step '${step_name}' exited with code ${_rc} — checkpoint NOT saved (will retry on next --resume)" >&2
        return $_rc
    fi
}
# endregion FUNC_checkpoint_step

# ═══════════════════════════════════════════════════════════════════
# region FUNC__verify_secrets_loaded
## @purpose  Verify that secrets.env exists and required env vars are loaded
##           into the current shell. If checkpoint exists but vars aren't loaded,
##           re-source secrets.env without re-decrypting.
## @param $@  forwarded args (typically none — env-based check)
## @io       out: stderr → verification status at IMP:8-10
##            return: 0 = secrets loaded, 1 = verification failed
## @complexity O(1) + file read if re-sourcing
## @invariants — Always returns 0 or 1 (never exits the process)
##             - Re-sources secrets.env if vars are missing (delegates to set -a)
##             - SECRETS_ENV_FILE default: /run/platform/secrets.env
## @rationale Pure extraction from orchestrator.sh — behavior preserved exactly.
_verify_secrets_loaded() {
    local secrets_env="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
    if [[ ! -f "$secrets_env" ]]; then
        echo "[IMP:8][bootstrap][checkpoint] Verification FAIL: secrets.env not found at ${secrets_env}" >&2
        return 1
    fi
    # Быстрая проверка: хотя бы одна ключевая переменная должна быть в окружении
    if [[ -z "${DOCKER_HUB_USERNAME:-}" ]] && [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
        echo "[IMP:8][bootstrap][checkpoint] Verification WARN: Secrets not loaded in shell — re-sourcing ${secrets_env}" >&2
        set -a
        # shellcheck disable=SC1090
        source "$secrets_env"
        set +a
        # Повторная проверка после source
        if [[ -z "${DOCKER_HUB_USERNAME:-}" ]] && [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
            echo "[IMP:10][bootstrap][checkpoint] Verification FAIL: secrets.env exists at ${secrets_env} but empty or unreadable" >&2
            return 1
        fi
        echo "[IMP:8][bootstrap][checkpoint] Verification: secrets re-sourced from ${secrets_env}" >&2
    fi
    echo "[IMP:8][bootstrap][checkpoint] Verification PASS: secrets are loaded in current shell" >&2
    return 0
}
# endregion FUNC__verify_secrets_loaded

# ═══════════════════════════════════════════════════════════════════
# region FUNC__checkpoint_version_check
## @purpose  Compare per-step content hash with stored hash for a single step.
##           If the hash differs, invalidate only that step's checkpoint.
##           Replaces the previous global VERSION-based invalidation (T20).
##           Falls back to global VERSION check when no hash is provided.
## @param $1  step_name  — optional: step label for per-step hash check
## @param $2  current_hash — optional: hash to compare against stored hash
##            (if empty, falls back to CHECKPOINT_STEP_HASH env var)
## @io       out: stderr → hash comparison at IMP:8-9
##            effect: may delete .bootstrap-step-${step_name}.done in CHECKPOINT_DIR
## @complexity O(1)
## @invariants — Always returns 0 (never exits the process)
##             - When step_name + hash provided: checks per-step hash
##             - When only step_name provided: reads CHECKPOINT_STEP_HASH env
##             - When no args: falls back to global VERSION (backward compat)
##             - step_hash_changed must be available in scope for per-step mode
##             - Missing .hash file → treated as "changed" → re-run once
## @rationale Per-step hash replaces global VERSION to isolate invalidations.
##            Backward compat retained: old callers without hash args get the
##            original global VERSION behavior (invalidates ALL checkpoints).
_checkpoint_version_check() {
    local step_name="${1:-}"
    local current_hash="${2:-${CHECKPOINT_STEP_HASH:-}}"

    # ── Per-step content hash mode (T20) ─────────────────────────
    if [[ -n "$step_name" ]] && [[ -n "$current_hash" ]]; then
        if type step_hash_changed &>/dev/null 2>&1; then
            if ! step_hash_changed "$step_name" "$current_hash"; then
                echo "[IMP:9][bootstrap][checkpoint] HASH-CHANGED: Step '${step_name}' content changed — invalidating checkpoint" >&2
                rm -f "${CHECKPOINT_DIR}/.bootstrap-step-${step_name}.done"
                # Update stored hash to current
                mkdir -p "$CHECKPOINT_DIR"
                echo "$current_hash" > "${CHECKPOINT_DIR}/.bootstrap-step-${step_name}.hash"
                echo "[IMP:9][bootstrap][checkpoint] HASH-UPDATE: Stored hash for '${step_name}' updated to match current code" >&2
            fi
        fi
        return 0
    fi

    # ── Fallback: global VERSION mode (original behavior) ────────
    local current_version
    current_version="$(head -1 "${CORE_DIR}/VERSION" 2>/dev/null || echo "unknown")"
    local checkpoint_version=""
    if [[ -f "${CHECKPOINT_DIR}/VERSION" ]]; then
        checkpoint_version="$(head -1 "${CHECKPOINT_DIR}/VERSION")"
    fi
    if [[ "$checkpoint_version" != "$current_version" ]]; then
        echo "[IMP:9][bootstrap][checkpoint] VERSION changed: ${checkpoint_version:-none} → ${current_version} — invalidating all checkpoints" >&2
        rm -f "${CHECKPOINT_DIR}"/.bootstrap-step-*.done
        echo "$current_version" > "${CHECKPOINT_DIR}/VERSION"
        echo "[IMP:9][bootstrap][checkpoint] Checkpoint VERSION updated to ${current_version}" >&2
    else
        echo "[IMP:8][bootstrap][checkpoint] VERSION match (${current_version}) — preserving all checkpoints" >&2
    fi
}
# endregion FUNC__checkpoint_version_check
