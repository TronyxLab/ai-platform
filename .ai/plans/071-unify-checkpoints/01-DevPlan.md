# DevPlan 071: Unify Checkpoints — state.json as Single Source of Truth

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

## Tasks

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
- IMPORTANT: checkpoint.sh sources paths.sh → STATE_JSON path from there

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
