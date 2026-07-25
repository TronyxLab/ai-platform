# Brief 071 — Unify Checkpoints (Rev 2: FIXED)

## $ARTIFACT_CONTRACT
- **PURPOSE:** Eliminate dual checkpoint system (shell .done files vs Python state.json) causing state divergence on --resume.
- **DESCRIPTION:** Rewrite checkpoint.sh to delegate to state.json via new `core/internal/checkpoint_migration.py` module. **Rev 2 redesign:** Use Python step NAMES (underscores) as state.json keys instead of numeric indices — eliminates CRITICAL step-name/key misalignment (F1). Extract all inline `python3 -c "..."` blocks to `checkpoint_migration.py` (F2 fix). Unify naming convention to underscores (F6 fix).
- **RATIONALE:** Rev 1 design had CRITICAL flaw: shell and Python use different step inventories (16 vs 23 steps), numeric key alignment breaks after key 12 → `ensure_secrets` and `secrets_init` incorrectly skipped on resume. Rev 2 name-based keys eliminate this class of bugs permanently.
- **ACCEPTANCE_CRITERIA:** From 02-DevPlan-expanded.md.
- **IMPLEMENTS:** DevPlan 071 Rev 2.
- **IMPACTS:** checkpoint.sh (thin facade), checkpoint_migration.py (NEW), state_machine.py (name-based refactor), node-lifecycle.sh, content-hash.sh, AGENTS.md, test_state_machine.py, test_checkpoint_migration.py (NEW).
- **REQUIRES:** state_machine.py MUST be refactored for name-based keys (breaking change to internal dict key format).

## Current Status (Rev 2 — 2026-07-25)
- **Verdict:** FIXED (ready for implementation)
- **Implementation:** 0% (design complete, not started).
- **Rev 2 changes:** All 6 VerificationReport findings addressed:
  - **F1 (CRITICAL):** Redesigned to name-based keys with SHELL_TO_PYTHON_STEP mapping — `ensure_secrets`/`secrets_init` no longer skipped.
  - **F2 (HIGH):** All inline `python3 -c` blocks extracted to `core/internal/checkpoint_migration.py`.
  - **F3 (MEDIUM):** Removed false `paths.sh` STATE_JSON claim; CHECKPOINT_STATE_FILE defined in node-lifecycle.sh.
  - **F4 (MEDIUM):** content-hash.sh `step_hash_changed()` gets safe CHECKPOINT_DIR default.
  - **F5 (LOW):** node-lifecycle.sh STRUCTURE comment updated.
  - **F6 (LOW):** Step names unified to underscores via SHELL_TO_PYTHON_STEP mapping.

## Required Actions (all addressed in 02-DevPlan-expanded.md Rev 2)
1. **DONE (F1):** Redesigned checkpoint key mapping — name-based keys with SHELL_TO_PYTHON_STEP.
2. **DONE (F2):** Inline python3 blocks extracted to checkpoint_migration.py.
3. **DONE (F3-F6):** Documentation drift fixed, content-hash.sh default added, STRUCTURE updated, naming unified.
