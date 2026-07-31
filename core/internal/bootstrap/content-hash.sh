# shellcheck shell=bash
# GREP_SUMMARY: content-hash thin-wrapper python compute-step-hash step-hash-changed bootstrap unified-drift
# STRUCTURE: ▶ compute_step_hash(step, paths...) → delegate to python3 shared/content_hash.py → ⎋ hash
#            └ step_hash_changed(step, hash) → stored vs current comparison → 0|1
# region MODULE_CONTRACT
## @purpose  Thin shell wrapper over core.internal.shared.content_hash (DevPlan 079
##           DRIFT-B4 unification). Delegates hash computation to Python shared module;
##           retains step_hash_changed() in shell for CHECKPOINT_DIR filesystem access
##           without Python dependency.
## @scope    — compute_step_hash() → python3 -m core.internal.shared.content_hash
##           — step_hash_changed()  → compare stored hash vs current (shell only)
## @invariants
##   - Falls back to old sha256sum algorithm if Python module unavailable
##   - step_hash_changed remains in shell (requires CHECKPOINT_DIR access)
##   - All callers (node-lifecycle.sh, state_machine.py) work without changes
## @changes  2026-07-25 | DevPlan 079 — Reduced to thin wrapper (~40 LOC, was 127)
# endregion MODULE_CONTRACT

# ═══════════════════════════════════════════════════════════════════
# CONTENT HASH FUNCTIONS (thin wrappers)
# ═══════════════════════════════════════════════════════════════════

# region FUNC_compute_step_hash
## @purpose  Compute SHA-256 content hash — delegates to Python shared module.
##           Falls back to old sha256sum if Python module is unavailable.
## @param $1  step_label — unique step identifier (logging only)
## @param $@  script_paths — file paths to hash (passed to Python)
## @io       stdout: 64-char hex sha256 hash
##            stderr: LDD logs
##            return: 0 (always)
## @invariants
##   - Python check: verifies core/internal/shared/content_hash.py exists
##   - Fallback: if Python module unavailable, uses old sha256sum + cat
##   - Backward compatible: all callers see same output format
compute_step_hash() {
    local step_label="$1"
    shift

    local shared_py
    shared_py="$(cd "$(dirname "${BASH_SOURCE[0]}")/../shared" 2>/dev/null && pwd)/content_hash.py"
    if [[ -f "$shared_py" ]]; then
        # Delegates to Python shared module (DevPlan 079)
        python3 -m core.internal.shared.content_hash compute --files "$@" 2>/dev/null
    else
        # Fallback: old sha256sum algorithm
        echo "[IMP:8][content-hash][${step_label}] Python module not found — using fallback" >&2
        if [[ $# -eq 0 ]]; then
            echo "[IMP:8][content-hash][${step_label}] No files — using label hash" >&2
            echo -n "$step_label" | sha256sum | cut -d' ' -f1
            return 0
        fi
        cat "$@" 2>/dev/null | sha256sum | cut -d' ' -f1
    fi
}
# endregion FUNC_compute_step_hash

# region FUNC_step_hash_changed
## @purpose  Compare current hash against stored hash for a step.
##           Remains in shell — needs CHECKPOINT_DIR filesystem access.
## @param $1  step_label — unique step identifier
## @param $2  current_hash — the hash from compute_step_hash()
## @io       stderr: LDD logs; return: 0=unchanged, 1=changed/unknown
## @invariants
##   - Returns 1 if no stored .hash file exists (backward compat)
##   - Returns 0 on exact match, 1 on mismatch
step_hash_changed() {
    local step_label="$1"
    local current_hash="$2"
    local hash_file="${CHECKPOINT_DIR:-/var/lib/platform/.bootstrap-checkpoints}/.bootstrap-step-${step_label}.hash"

    if [[ ! -f "$hash_file" ]]; then
        echo "[IMP:8][content-hash][${step_label}] No stored hash — treating as changed" >&2
        return 1
    fi

    local stored_hash
    stored_hash="$(head -1 "$hash_file" 2>/dev/null)"

    if [[ "$stored_hash" != "$current_hash" ]]; then
        echo "[IMP:9][content-hash][${step_label}] Hash mismatch: stored=${stored_hash:0:12}... != current=${current_hash:0:12}..." >&2
        return 1
    fi

    echo "[IMP:8][content-hash][${step_label}] Hash match (${current_hash:0:12}...) — preserving checkpoint" >&2
    return 0
}
# endregion FUNC_step_hash_changed
