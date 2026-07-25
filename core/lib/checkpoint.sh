#!/usr/bin/env bash
# GREP_SUMMARY: checkpoint.sh, state.json, thin-facade, checkpoint_migration.py, name-based-keys, is-done, mark-done, force, reset, migrate-legacy
# STRUCTURE: ▶ ┌checkpoint_step┐ → ◇ FORCE? → exec → ◇ python3 checkpoint_migration.py is-done → ◇ step_func → ⊕ mark-done → ⎋ exit 0|1
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Checkpoint Library (Rev 2: thin facade over checkpoint_migration.py)
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @purpose  Thin shell facade delegating ALL JSON checkpoint logic to
##           core/internal/checkpoint_migration.py. Eliminates inline python3 -c
##           blocks (F2 fix) and step-name/key misalignment (F1 fix).
##           Uses Python step NAMES (underscores) as state.json keys — not
##           numeric indices. Shell step names (hyphens) are mapped via
##           SHELL_TO_PYTHON_STEP in checkpoint_migration.py.
## @scope    — checkpoint_step() with resume-mode + force-mode + verify_func
##           — checkpoint_force() — reset a single step to pending
##           — checkpoint_reset_all() — delete state.json entirely
##           — checkpoint_migrate_legacy() — import old .done files → state.json
##           — _checkpoint_is_done_json() — delegate to Python (name-based)
##           — _checkpoint_mark_done_json() — delegate to Python (name-based)
## @input    — CHECKPOINT_STATE_FILE (env var, default: /var/lib/platform/.bootstrap/state.json)
##           — CHECKPOINT_DIR (env var, default: /var/lib/platform/.bootstrap-checkpoints)
##             kept for legacy migration only
##           — RESUME_MODE (bool, set by consumer)
##           — FORCE_MODE (bool, set by consumer)
##           — SCRIPT_DIR (set by consumer via paths.sh)
## @output   — state.json with name-based keys (via checkpoint_migration.py)
##           — LDD log lines to stderr: [IMP:8-9][bootstrap][checkpoint] ...
## @links    — DELEGATES_TO: core/internal/checkpoint_migration.py
##           — USED_BY: core/internal/bootstrap/node-lifecycle.sh
## @invariants
##   - Functions MUST NOT write to stdout (only stderr)
##   - Zero inline python3 -c "..." blocks (language policy Tier 1 compliance)
##   - CHECKPOINT_STATE_FILE default: /var/lib/platform/.bootstrap/state.json
##   - checkpoint_step() with FORCE_MODE=true skips all checkpoint checks
##   - checkpoint_step() only creates checkpoint on step success (exit 0)
##   - verify_func failure → re-execute (not skip)
## @rationale Rev 2 (DevPlan 071): Extract all JSON logic to Python module for
##            testability, name-based keys for alignment, and compliance with
##            AGENTS.md language policy (no new inline python3 in shell).
## @changes  2026-07-25 | DevPlan 071 Rev 2 — Complete rewrite as thin facade
# endregion MODULE_CONTRACT
# GREP_SUMMARY: checkpoint, resume, force, name-based-keys, state.json, checkpoint_migration.py, facade
# STRUCTURE: ▶ ┌SCRIPT_DIR/../internal/checkpoint_migration.py┐ → ◇ is-done → ◇ mark-done → ◇ force → ◇ reset → ◇ migrate-legacy → ⎋ exit

# ── Internal helpers (delegate to Python) ────────────────────────

# region FUNC__checkpoint_is_done_json
## @purpose  Check if a shell step is done by delegating to checkpoint_migration.py is-done.
##           Returns 0 if done, 1 if pending/missing.
## @param $1  step_name — shell step label (hyphens, e.g. "ssh-access")
_checkpoint_is_done_json() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" is-done "$state_file" "$step_name"
}
# endregion FUNC__checkpoint_is_done_json

# region FUNC__checkpoint_mark_done_json
## @purpose  Mark a shell step as done via checkpoint_migration.py mark-done.
## @param $1  step_name — shell step label (hyphens)
## @param env CHECKPOINT_STEP_HASH — optional content hash
_checkpoint_mark_done_json() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    local hash="${CHECKPOINT_STEP_HASH:-}"
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" mark-done "$state_file" "$step_name" "$hash"
}
# endregion FUNC__checkpoint_mark_done_json

# ── Public API (unchanged signatures) ────────────────────────────

# region FUNC_checkpoint_step
## @purpose  Execute a bootstrap step with name-based state.json checkpoint tracking.
##           If RESUME_MODE is active and state.json shows the step done (and optional
##           verify_func passes), the step is skipped. If FORCE_MODE is active, step
##           always runs.
## @param $1  step_name — shell label (hyphens, mapped to Python name via SHELL_TO_PYTHON_STEP)
## @param $2  step_func — function to execute
## @param $3  verify_func — optional verification function for resume-mode
## @param $@  remaining args forwarded to step_func and verify_func
## @io       out: stderr → checkpoint status messages at IMP:8
##            effect: writes to state.json (name-based key) via checkpoint_migration.py
## @complexity O(1) + delegated func
## @invariants — FORCE_MODE=true bypasses all checks
##             — RESUME_MODE=true + step done in state.json → skip
##             — verify_func failure → re-execute
##             — Only marks done on step success (exit 0)
checkpoint_step() {
    local step_name="$1"
    local step_func="$2"
    local verify_func="${3:-}"
    shift 2
    [[ -n "$verify_func" ]] && shift 1

    if [[ "$FORCE_MODE" == "true" ]]; then
        "$step_func" "$@"
        return $?
    fi

    if [[ "$RESUME_MODE" == "true" ]] && _checkpoint_is_done_json "$step_name"; then
        # Optional verify_func check
        if [[ -n "$verify_func" ]]; then
            if ! "$verify_func" "$@"; then
                echo "[IMP:8][bootstrap][checkpoint] SKIP OVERRIDE: Checkpoint exists but verification failed for '${step_name}' — re-executing" >&2
                if "$step_func" "$@"; then
                    _checkpoint_mark_done_json "$step_name"
                    return 0
                else
                    local _rc=$?
                    echo "[IMP:9][bootstrap][checkpoint] FAIL: Step '${step_name}' exit ${_rc}" >&2
                    return $_rc
                fi
            fi
        fi
        echo "[IMP:8][bootstrap][checkpoint] SKIP: Step '${step_name}' already done (state.json)" >&2
        return 0
    fi

    if "$step_func" "$@"; then
        _checkpoint_mark_done_json "$step_name"
        return 0
    else
        local _rc=$?
        echo "[IMP:9][bootstrap][checkpoint] FAIL: Step '${step_name}' exit ${_rc} — checkpoint NOT saved" >&2
        return $_rc
    fi
}
# endregion FUNC_checkpoint_step

# region FUNC_checkpoint_force
## @purpose  Reset a single step to "pending" in state.json.
## @param $1  step_name — shell step label (hyphens)
checkpoint_force() {
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" force \
        "${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}" "$1"
}
# endregion FUNC_checkpoint_force

# region FUNC_checkpoint_reset_all
## @purpose  Delete state.json entirely (full force-reset).
checkpoint_reset_all() {
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" reset \
        "${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
}
# endregion FUNC_checkpoint_reset_all

# region FUNC_checkpoint_migrate_legacy
## @purpose  Migrate old .done files from CHECKPOINT_DIR to name-based state.json.
##           Idempotent: after first migration, .done files are gone → no-op.
checkpoint_migrate_legacy() {
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" migrate-legacy \
        "${CHECKPOINT_DIR:-/var/lib/platform/.bootstrap-checkpoints}" \
        "${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
}
# endregion FUNC_checkpoint_migrate_legacy
