# GREP_SUMMARY: content-hash sha256sum compute-step-hash step-hash-changed checkpoint content-invalidation per-step
# STRUCTURE: ▶ compute_step_hash(step, paths...) → sha256sum → ┌store .hash┐ → ○ step_hash_changed(step, hash) → ◇ match? → 0/1
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Per-step Content Hash Checkpoint Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Replace VERSION-based global checkpoint invalidation with
##           per-step content hash tracking. When step code changes, only
##           that step's checkpoint is invalidated — other steps retain
##           their checkpoints.
## @scope    — compute_step_hash() — sha256sum of step's script dependencies
##           — step_hash_changed()  — compare stored hash vs current → 0/1
## @input    — step_label (1st arg) — unique step identifier
##           — script_paths (2nd+ args) — files to hash for content verification
##           — CHECKPOINT_DIR (env var, set by consumer, default: /var/lib/platform/.bootstrap-checkpoints)
## @output   — stdout: computed sha256 hash from compute_step_hash()
##           — stderr: LDD log lines at IMP:8-9 with [content-hash][step] prefix
##           — return: 0 (unchanged) / 1 (changed) from step_hash_changed()
## @links    — USED_BY: core/lib/checkpoint.sh, core/internal/bootstrap/node-lifecycle.sh
##           — CALLS: sha256sum, cat, mkdir
##           — CALLED_BY: checkpoint_step() via CHECKPOINT_STEP_HASH env var
## @invariants — Functions assume CHECKPOINT_DIR is set by consumer
##             — compute_step_hash writes hash to stdout, logs to stderr
##             - step_hash_changed returns 1 if .hash file doesn't exist
##               (backward compat: old checkpoints without hash → re-run)
##             - hash is stored in CHECKPOINT_DIR/.bootstrap-step-<label>.hash
## @rationale Q: Why per-step content hash instead of global VERSION?
##            A: Global VERSION (V1) invalidates ALL checkpoints on ANY code change.
##            With 17 bootstrap steps, a 1-line fix in step 17 re-runs steps 1-17.
##            Per-step hash isolates invalidation: changing deploy-modules logic
##            only re-runs the deploy-modules step. This reduces bootstrap time
##            from ~30min to ~2min for routine updates with preserved checkpoints.
##            The trade-off is slightly more complex orchestration (each step
##            declares its script dependencies for hashing).
## @changes   LAST_CHANGE: 2026-07-17 · T20 — Per-step content hash checkpoints
## @modulemap — compute_step_hash       [W:25] sha256sum of step's script paths
##             — step_hash_changed       [W:20] Compare stored vs current hash
## @usecases  — node-lifecycle.sh --mode init: each checkpoint_step sets CHECKPOINT_STEP_HASH
##             - node-lifecycle.sh --mode update: verify_core step hashes core delivery files
##             - Any script using checkpoint_step with content hash tracking
# endregion MODULE_CONTRACT
# GREP_SUMMARY: content-hash, sha256sum, compute_step_hash, step_hash_changed, per-step, checkpoint, hash
# STRUCTURE: ▶ compute_step_hash ┌cat paths┐ → sha256sum → stdout ┌imp logs to stderr┐
#            └ step_hash_changed ┌hash file found?┐ → ◇ stored == current? → 0|1
#            └────────────────── fallback: no .hash → return 1 (re-run)

# ═══════════════════════════════════════════════════════════════════
# CONTENT HASH FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

# region FUNC_compute_step_hash
## @purpose  Compute SHA-256 content hash for a set of step scripts.
##           Used at checkpoint boundaries to detect code changes for a specific step.
## @param $1  step_label — unique step identifier (used for logging only)
## @param $@  script_paths — one or more file paths to hash (concatenated and sha256sum'd)
## @io       stdout: 64-char hex sha256 hash
##            stderr: LDD log at IMP:8
##            return: 0 (always — write to stderr on error, never fail)
## @complexity O(n) where n = total bytes of all script files
## @invariants — If no script_paths provided, falls back to hashing the label string itself
##             - cat "$@" 2>/dev/null suppresses file-not-found errors for individual paths
##             - The hash represents the COMBINED content of all scripts (not per-file)
##             - Any file read error logs to stderr but doesn't fail the function
## @rationale Concatenating all script files before hashing captures changes in any
##            dependency, including ordering effects. A per-file hash would miss
##            dependency reordering.
compute_step_hash() {
    local step_label="$1"
    shift

    if [[ $# -eq 0 ]]; then
        echo "[IMP:8][content-hash][${step_label}] WARN: No script paths provided — using label hash" >&2
        echo -n "$step_label" | sha256sum | cut -d' ' -f1
        return 0
    fi

    # Concatenate all scripts and hash the combined content
    # Uses 2>/dev/null to tolerate individual file read errors gracefully
    local hash
    hash="$(cat "$@" 2>/dev/null | sha256sum | cut -d' ' -f1)"
    echo "[IMP:8][content-hash][${step_label}] Computed hash: ${hash:0:12}... (${#} files)" >&2
    echo "$hash"
}
# endregion FUNC_compute_step_hash

# region FUNC_step_hash_changed
## @purpose  Compare a currently computed hash against the stored hash for a step.
##           If the hash differs (or no stored hash exists), the step's checkpoint
##           should be invalidated.
## @param $1  step_label — unique step identifier
## @param $2  current_hash — the hash computed by compute_step_hash()
## @io       stderr: LDD log at IMP:8-9 indicating match or mismatch
##            return: 0 = hash unchanged (step can use existing checkpoint)
##                    1 = hash changed or unknown (step must re-run)
## @complexity O(1) — file read + string comparison
## @invariants — Returns 1 if CHECKPOINT_DIR/.bootstrap-step-<label>.hash doesn't exist
##               (backward compatibility: old checkpoints without hash trigger re-run)
##             - Returns 1 on any mismatch (stored ≠ current)
##             - Returns 0 on exact match (stored == current)
##             - The stored hash is written by checkpoint_step() after step completion
## @rationale Returning 1 on missing hash ensures backward compatibility: after
##            upgrading to per-step content hash, all existing checkpoints (created
##            without hash) are treated as stale and re-run once to generate hashes.
step_hash_changed() {
    local step_label="$1"
    local current_hash="$2"
    local hash_file="${CHECKPOINT_DIR}/.bootstrap-step-${step_label}.hash"

    if [[ ! -f "$hash_file" ]]; then
        echo "[IMP:8][content-hash][${step_label}] No stored hash found — treating as changed (backward compat)" >&2
        return 1
    fi

    local stored_hash
    stored_hash="$(head -1 "$hash_file" 2>/dev/null)"

    if [[ "$stored_hash" != "$current_hash" ]]; then
        echo "[IMP:9][content-hash][${step_label}] Hash mismatch: stored=${stored_hash:0:12}... current=${current_hash:0:12}... — invalidating step" >&2
        return 1
    fi

    echo "[IMP:8][content-hash][${step_label}] Hash match (${current_hash:0:12}...) — step code unchanged, preserving checkpoint" >&2
    return 0
}
# endregion FUNC_step_hash_changed
