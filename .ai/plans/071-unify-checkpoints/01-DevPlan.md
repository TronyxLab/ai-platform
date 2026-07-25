# DevPlan 071: Unify Checkpoints — state.json as Single Source of Truth

> **⚠️ SUPERSEDED by [02-DevPlan-expanded.md](02-DevPlan-expanded.md) (AUTHORITATIVE per R1).**
> Rev 1 had a CRITICAL design flaw (F1): numeric key misalignment between shell (16 steps) and Python (23 steps) causing `ensure_secrets`/`secrets_init` to be incorrectly skipped on resume.
> Rev 2 (in 02-DevPlan-expanded.md) fixes this with name-based keys + `checkpoint_migration.py` module.
> This file retained for historical reference only.

$ARTIFACT_CONTRACT
PURPOSE: Eliminate dual checkpoint system (shell .done files vs Python state.json) that causes state divergence on --resume. Make state.json the ONLY checkpoint mechanism.
DESCRIPTION: Currently node-lifecycle.sh uses `core/lib/checkpoint.sh` (touch .done files) for steps 1-17, then delegates to state_machine.py which uses `state.json`. These systems are NOT synchronized — when one says "done", the other may say "pending", causing skipped or repeated steps on resume. Fix: checkpoint.sh becomes a thin wrapper that reads/writes state.json.
RATIONALE: Dual system is the ROOT CAUSE of bootstrap resume bugs. Shell sees .done marker → skips step → state_machine sees pending → re-executes. Or: state_machine marks done → shell .done missing → re-executes.
ACCEPTANCE_CRITERIA:
  - `core/lib/checkpoint.sh` reads/writes `state.json` (not .done files)
  - `checkpoint_step()` → state.json; `checkpoint_is_done()` → reads state.json
  - `state_machine.py` uses same state.json format (no format change needed)
  - All bootstrap steps use ONE checkpoint mechanism
  - `--resume` works correctly — no skipped or double-executed steps
  - `tests/unit/test_state_machine.py` — green
  - `make gate MODE=fast` — green
IMPLEMENTS: Wave 6A — core unification P0
IMPACTS:
  - core/lib/checkpoint.sh (rewrite to delegate to state.json)
  - core/internal/bootstrap/node-lifecycle.sh (update checkpoint calls)
  - core/internal/bootstrap/lifecycle/state_machine.py (no format change needed)
  - core/internal/bootstrap/AGENTS.md (update checkpoint documentation)
REQUIRES: None (standalone, but benefits from 070)

## ⚠️ KNOWN FLAWS (fixed in Rev 2 — see 02-DevPlan-expanded.md)

1. **F1 (CRITICAL):** Claims "checkpoint.sh sources paths.sh → STATE_JSON path from there" — FALSE. `paths.sh` has no `STATE_JSON` variable. Fixed in Rev 2: `CHECKPOINT_STATE_FILE` defined in `node-lifecycle.sh`.
2. **F1 (CRITICAL):** Asserts numeric keys will align between shell and Python — FALSE due to different step inventories. Fixed in Rev 2: name-based keys.
3. **F2 (HIGH):** Embeds inline `python3 -c "..."` blocks — violates language policy. Fixed in Rev 2: extracted to `checkpoint_migration.py`.

## Tasks (ORIGINAL Rev 1 — see expanded for Rev 2 tasks)

### T1: Define unified state.json schema
- Current state.json format in state_machine.py is already sufficient: {step_name: {status, hash, timestamp}}
- Document the schema in checkpoint.sh header
- Add `checkpoint_read_json()` function to read state.json path and parse it

### T2: Rewrite checkpoint.sh to delegate to state.json
- `checkpoint_step(name, hash)` → write to state.json: `{name: {status: "done", hash: "...", timestamp: "..."}}`
- `checkpoint_is_done(name, hash)` → read state.json, compare hash, return 0/1
- `checkpoint_force(name)` → set status to "pending" in state.json
- `checkpoint_reset_all()` → truncate state.json
- Shell functions call `python3 -c "import json..."` through a helper or direct file read with jq/python
- IMPORTANT: checkpoint.sh sources paths.sh → STATE_JSON path from there  ← FALSE (F3)

### T3: Update node-lifecycle.sh
- Remove .done file references
- Ensure all step functions call `_delegate` (already done in W4-E2, verify)
- Verify RESUME_MODE/FORCE_MODE propagation to state_machine.py

### T4: Cleanup old .done files
- Add migration: on first run, if old .done files exist → import their state into state.json → remove .done files
- `checkpoint_migrate_legacy()` in checkpoint.sh

### T5: Test
- `tests/unit/test_state_machine.py` — verify resume, force, checkpoint integrity
- `make gate MODE=fast` — green
