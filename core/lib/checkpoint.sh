#!/usr/bin/env bash
# GREP_SUMMARY: checkpoint.sh, state.json, direct-json, is-done, mark-done, force, reset
# STRUCTURE: ▶ ┌checkpoint_step┐ → ◇ FORCE? → exec → ◇ python3 inline state.json check → ◇ step_func → ⊕ mark-done → ⎋ exit 0|1
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Checkpoint Library (Rev 3: direct state.json operations)
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @purpose  Shell facade for bootstrap checkpoint tracking using direct state.json
##           operations (read/write via python3 inline). checkpoint_migration.py bridge
##           removed per DevPlan 087 — all checkpoints through state.json directly.
## @scope    — checkpoint_step() with resume-mode + force-mode + verify_func
##           — checkpoint_force() — reset a single step to pending
##           — checkpoint_reset_all() — delete state.json entirely
##           — _checkpoint_is_done_json() — read state.json directly
##           — _checkpoint_mark_done_json() — write state.json directly
## @input    — CHECKPOINT_STATE_FILE (env var, default: /var/lib/platform/.bootstrap/state.json)
##           — RESUME_MODE (bool, set by consumer)
##           — FORCE_MODE (bool, set by consumer)
##           — SCRIPT_DIR (set by consumer via paths.sh)
## @output   — state.json with phase-based keys
##           — LDD log lines to stderr: [IMP:8-9][bootstrap][checkpoint] ...
## @invariants
##   - Functions MUST NOT write to stdout (only stderr)
##   - CHECKPOINT_STATE_FILE default: /var/lib/platform/.bootstrap/state.json
##   - checkpoint_step() with FORCE_MODE=true skips all checkpoint checks
##   - checkpoint_step() only creates checkpoint on step success (exit 0)
##   - verify_func failure → re-execute (not skip)
## @rationale Rev 3 (DevPlan 087): Remove checkpoint_migration.py dependency.
##            All state.json operations use direct python3 inline for shell compatibility.
##            migrate_state_to_phases() lives in state_migration.py for one-shot migration.
## @changes  2026-07-25 | DevPlan 071 Rev 2 — Thin facade over checkpoint_migration.py
##           2026-07-30 | DevPlan 087 Rev 3 — Direct state.json operations,
##           removed checkpoint_migration.py delegation, removed migrate-legacy
# endregion MODULE_CONTRACT

# ── Internal helpers (direct state.json operations) ──────────────

# region FUNC__checkpoint_is_done_json
## @purpose  Check if a step is done by reading state.json directly.
##           Returns 0 if done, 1 if pending/missing.
## @param $1  step_name — step key (underscores, e.g. "ssh_access")
_checkpoint_is_done_json() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    if [[ ! -f "$state_file" ]]; then
        return 1
    fi
    python3 -c "
import json, sys
try:
    with open('${state_file}') as f:
        data = json.load(f)
    steps = data.get('steps', {})
    step = steps.get('${step_name}', {})
    if isinstance(step, dict) and step.get('status') == 'done':
        sys.exit(0)
    if isinstance(step, dict) and step.get('done') is True:
        sys.exit(0)
except Exception:
    pass
sys.exit(1)
" && return 0 || return 1
}
# endregion FUNC__checkpoint_is_done_json

# region FUNC__checkpoint_mark_done_json
## @purpose  Mark a step as done in state.json directly.
## @param $1  step_name — step key (underscores)
## @param env CHECKPOINT_STEP_HASH — optional content hash
_checkpoint_mark_done_json() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    local hash="${CHECKPOINT_STEP_HASH:-}"
    python3 -c "
import json, os, sys
state_file = '${state_file}'
step_name = '${step_name}'
step_hash = '${hash}'
data = {}
if os.path.isfile(state_file):
    try:
        with open(state_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
if 'steps' not in data:
    data['steps'] = {}
entry = {'status': 'done'}
if step_hash:
    entry['hash'] = step_hash
data['steps'][step_name] = entry
tmp = state_file + '.tmp'
with open(tmp, 'w') as f:
    json.dump(data, f, indent=2)
os.replace(tmp, state_file)
"
}
# endregion FUNC__checkpoint_mark_done_json

# ── Public API ───────────────────────────────────────────────────

# region FUNC_checkpoint_step
## @purpose  Execute a bootstrap step with state.json checkpoint tracking.
##           If RESUME_MODE is active and state.json shows the step done (and optional
##           verify_func passes), the step is skipped. If FORCE_MODE is active, step
##           always runs.
## @param $1  step_name — step key (underscores)
## @param $2  step_func — function to execute
## @param $3  verify_func — optional verification function for resume-mode
## @param $@  remaining args forwarded to step_func and verify_func
## @io       out: stderr → checkpoint status messages at IMP:8
##            effect: writes to state.json
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
## @param $1  step_name — step key (underscores)
checkpoint_force() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    python3 -c "
import json, os, sys
state_file = '${state_file}'
step_name = '${step_name}'
if not os.path.isfile(state_file):
    sys.exit(0)
with open(state_file) as f:
    data = json.load(f)
steps = data.get('steps', {})
if step_name in steps:
    steps[step_name]['status'] = 'pending'
    tmp = state_file + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, state_file)
    print('[IMP:8][checkpoint][force] Reset step ' + step_name + ' to pending')
"
}
# endregion FUNC_checkpoint_force

# region FUNC_checkpoint_reset_all
## @purpose  Delete state.json entirely (full force-reset).
checkpoint_reset_all() {
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    if [[ -f "$state_file" ]]; then
        rm -f "$state_file"
        echo "[IMP:9][checkpoint][reset] Removed ${state_file}" >&2
    else
        echo "[IMP:7][checkpoint][reset] ${state_file} does not exist — no-op" >&2
    fi
}
# endregion FUNC_checkpoint_reset_all
