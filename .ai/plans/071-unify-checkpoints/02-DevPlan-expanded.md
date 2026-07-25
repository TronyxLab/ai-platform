$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Eliminate dual checkpoint system (shell `.done` files + Python `state.json`) that causes state divergence on `--resume`. Make `state.json` the ONLY checkpoint mechanism.
DESCRIPTION:           Current state: `node-lifecycle.sh` uses `core/lib/checkpoint.sh` (`checkpoint_step()` → touch `.done` files in `/var/lib/platform/.bootstrap-checkpoints/`) for steps 1-13 in init mode, then delegates to `state_machine.py` which uses `/var/lib/platform/.bootstrap/state.json`. Steps 1-13 are tracked in BOTH systems independently — with different content hashes (shell uses `content-hash.sh` hashing `node-lifecycle.sh`; Python uses `state_machine._step_hash()` hashing `state_machine.py`). When they disagree, steps are skipped by one system but executed by the other.
                     Fix: Rewrite `checkpoint.sh` to delegate to `state.json` via `python3` subprocess. All checkpoint operations read/write the same `state.json` file used by `state_machine.py`. Remove `.done`-based checkpointing entirely. Provide migration for existing `.done` files.
RATIONALE:             Dual system is the ROOT CAUSE of bootstrap resume bugs (DRIFT-B1, Brief 077). Shell sees `.done` marker → skips step → `state_machine` sees pending → re-executes. Or: state_machine marks done → shell `.done` missing → re-executes. One source of truth eliminates this class of bugs permanently.
ACCEPTANCE_CRITERIA:
  1. `checkpoint.sh` `checkpoint_step()` writes to `/var/lib/platform/.bootstrap/state.json` (same file used by state_machine.py)
  2. `checkpoint.sh` `checkpoint_is_done()` reads from state.json
  3. `checkpoint.sh` `checkpoint_force()` sets step status to `"pending"` in state.json
  4. `checkpoint.sh` `checkpoint_reset_all()` clears state.json
  5. Shell steps 1-13 now track state in the SAME file as Python state_machine
  6. `--resume` uses unified state — no divergence possible
  7. Legacy `.done` files are migrated on first run → imported into state.json → removed
  8. `make gate MODE=fast` — green
  9. `python3 -m pytest tests/unit/test_state_machine.py -v` — all pass
IMPLEMENTS:            Wave 6A — core unification P0, DRIFT-B1
IMPACTS:
  - core/lib/checkpoint.sh (REWRITE: delegate to state.json via python3 subprocess)
  - core/internal/bootstrap/node-lifecycle.sh (NO CHANGE — checkpoint_step() API preserved)
  - core/internal/bootstrap/content-hash.sh (UNCHANGED — compute_step_hash remains shell-based)
  - core/internal/bootstrap/lifecycle/state_machine.py (MINOR: ensure shell-written state.json is compatible)
  - core/internal/bootstrap/AGENTS.md (UPDATE checkpoint documentation)
  - tests/unit/test_state_machine.py (EXTEND: add test for shell-compatible state.json format)
REQUIRES:              None (standalone, but state_machine.py must be able to read state.json written by shell)
$END_ARTIFACT_CONTRACT

---

# DevPlan 071: Unify Checkpoints — EXPANDED

## Source Analysis

### Current dual system architecture

**Shell checkpoint path** (`node-lifecycle.sh` → `checkpoint.sh` → `content-hash.sh`):
```
CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
Files: .bootstrap-step-<step_name>.done, .bootstrap-step-<step_name>.hash
Format: touch (existence check), head -1 (hash check)
```
- `node-lifecycle.sh:135`: `CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"`
- `node-lifecycle.sh:141`: `[[ "$FORCE_MODE" == "true" ]] && rm -rf "$CHECKPOINT_DIR"`
- `node-lifecycle.sh:163-185`: 13+ calls to `checkpoint_step "label" step_func`, each with per-step `CHECKPOINT_STEP_HASH`
- `checkpoint.sh:95-165`: `checkpoint_step()` — checks FORCE_MODE, then RESUME_MODE + `.done` file existence, then calls `step_hash_changed()`, then executes step_func, then `touch .done` + save `.hash`
- `checkpoint.sh:110-119`: Content hash invalidation via `step_hash_changed()`
- `checkpoint.sh:227-261`: `_checkpoint_version_check()` — per-step hash or global VERSION fallback
- `content-hash.sh:69-85`: `compute_step_hash()` — `cat "$@" | sha256sum`
- `content-hash.sh:106-126`: `step_hash_changed()` — compares `.hash` file with current hash

**Python state machine path** (`state_machine.py`):
```
DEFAULT_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"
Format: JSON {mode, node, current_step, steps: {str: StepState}, errors, warnings}
```
- `state_machine.py:81`: `DEFAULT_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"`
- `state_machine.py:269-292`: `__init__()` — loads state.json or creates fresh BootstrapState
- `state_machine.py:300-311`: `save()` — atomic write (tmp + rename)
- `state_machine.py:315-327`: `start_step(n)` — sets status="running"
- `state_machine.py:331-345`: `complete_step(n, hash_val)` — sets status="done"
- `state_machine.py:349-361`: `skip_step(n, reason)` — sets status="skipped"
- `state_machine.py:365-378`: `fail_step(n, error)` — sets status="failed"
- `state_machine.py:382-409`: `get_current_step()` — finds next pending/failed step
- `state_machine.py:413-429`: `_step_hash()` — `hashlib.sha256` of `__file__` + extra paths
- `state_machine.py:511-518`: `_hash_changed(n, new_hash)` — compares stored hash

**StepState JSON format:**
```json
{
  "name": "ssh_access",
  "status": "done",
  "hash": "abc123...",
  "started_at": "2026-07-25T10:00:00Z",
  "error": null,
  "reason": null
}
```

### The divergence problem

Steps 1-13 (init mode) are tracked in BOTH systems:
- Shell `checkpoint_step()` creates `.done` files → `checkpoint_is_done()` reads them
- Python `state_machine.py` separately runs steps 1-23, writing its own state.json

When `--resume` is used:
- Shell steps 1-13: `checkpoint_step()` sees `.done` → SKIP
- `_delegate --mode init` (line 189-193): state_machine.py loads state.json, sees pending → EXECUTES steps 1-13 again

This means steps 1-13 ALWAYS re-execute in state_machine on resume, even when the shell says they're done. The shell's `.done` markers are invisible to Python.

### Content hash divergence

- Shell: `compute_step_hash "ssh-access" "${SCRIPT_DIR}/node-lifecycle.sh"` → hashes `node-lifecycle.sh`
- Python: `self._step_hash("ssh_access")` → hashes `state_machine.py` (via `os.path.abspath(__file__)`)

Different hash source files → different hash values → idempotency decisions diverge.

---

## TASK-1: Rewrite `checkpoint.sh` to delegate to state.json

### Design decision

The shell `checkpoint_step()` function will NOT call `state_machine.py` directly (would create circular dependency — `state_machine.py` calls back to shell scripts). Instead, `checkpoint.sh` will use a lightweight `python3 -c` approach to read/write `state.json`.

### New `checkpoint.sh` functions

#### 1. `checkpoint_step(name, step_func, verify_func?, ...)`

**CURRENT (checkpoint.sh:95-165):** Uses `.done` files

**NEW:** Reads/writes `state.json`

```bash
checkpoint_step() {
    local step_name="$1"
    local step_func="$2"
    local verify_func="${3:-}"
    shift 2
    [[ -n "$verify_func" ]] && shift 1

    if [[ "$FORCE_MODE" == "true" ]]; then
        "$step_func" "$@"
        return 0
    fi

    # ── Check if step is already done in state.json ──
    if [[ "$RESUME_MODE" == "true" ]]; then
        if _checkpoint_is_done_json "$step_name"; then
            echo "[IMP:8][bootstrap][checkpoint] SKIP: Step '${step_name}' already completed (state.json)" >&2
            return 0
        fi
    fi

    # ── Execute step ──
    if "$step_func" "$@"; then
        # Mark as done in state.json
        _checkpoint_mark_done_json "$step_name"
        echo "[IMP:8][bootstrap][checkpoint] DONE: Step '${step_name}' recorded in state.json" >&2
    else
        local _rc=$?
        echo "[IMP:9][bootstrap][checkpoint] FAIL: Step '${step_name}' exited with code ${_rc} — NOT recorded" >&2
        return $_rc
    fi
}
```

#### 2. `_checkpoint_is_done_json(step_name)` — NEW helper

Reads `state.json` and checks if step `step_name` has status `"done"`.

```bash
_checkpoint_is_done_json() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"

    if [[ ! -f "$state_file" ]]; then
        return 1
    fi

    # Use python3 to parse JSON (more reliable than jq across systems)
    python3 -c "
import json, sys
with open('$state_file') as f:
    data = json.load(f)
steps = data.get('steps', {})
for key, val in steps.items():
    if val.get('name') == '$step_name' and val.get('status') == 'done':
        sys.exit(0)
sys.exit(1)
" 2>/dev/null
}
```

#### 3. `_checkpoint_mark_done_json(step_name)` — NEW helper

Marks step as `done` in `state.json`, creates the file if missing. Preserves all existing step data.

```bash
_checkpoint_mark_done_json() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    local current_hash="${CHECKPOINT_STEP_HASH:-}"

    mkdir -p "$(dirname "$state_file")"

    python3 -c "
import json, os, sys
from datetime import datetime, timezone

state_file = '$state_file'
step_name = '$step_name'
current_hash = '$current_hash'

# Load existing state or create fresh
if os.path.isfile(state_file):
    try:
        with open(state_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError):
        data = {'mode': 'init', 'node': None, 'current_step': 0, 'steps': {}, 'errors': [], 'warnings': []}
else:
    data = {'mode': 'init', 'node': None, 'current_step': 0, 'steps': {}, 'errors': [], 'warnings': []}

# Find or create step entry by name
steps = data.setdefault('steps', {})
found_key = None
for key, val in steps.items():
    if val.get('name') == step_name:
        found_key = key
        break

if found_key is None:
    # Find next available numeric key
    used_keys = {int(k) for k in steps.keys() if k.isdigit()}
    next_key = max(used_keys) + 1 if used_keys else 1
    found_key = str(next_key)

step_entry = {
    'name': step_name,
    'status': 'done',
}
if current_hash:
    step_entry['hash'] = current_hash
steps[found_key] = step_entry

# Update current_step to the highest done step
max_done = 0
for key, val in steps.items():
    if val.get('status') == 'done' and key.isdigit():
        max_done = max(max_done, int(key))
data['current_step'] = max_done

# Atomic write
import tempfile, shutil
tmp = state_file + '.tmp'
with open(tmp, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
shutil.move(tmp, state_file)
" 2>/dev/null || {
        echo "[IMP:10][bootstrap][checkpoint] Failed to write state.json for '${step_name}'" >&2
        return 1
    }
}
```

#### 4. `checkpoint_force(step_name)` — Reset one step

```bash
checkpoint_force() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"

    [[ ! -f "$state_file" ]] && return 0

    python3 -c "
import json, os, sys
with open('$state_file') as f:
    data = json.load(f)
steps = data.get('steps', {})
for key, val in steps.items():
    if val.get('name') == '$step_name':
        val['status'] = 'pending'
        break
with open('$state_file', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
" 2>/dev/null

    echo "[IMP:9][bootstrap][checkpoint] FORCE: Step '${step_name}' reset to pending" >&2
}
```

#### 5. `checkpoint_reset_all()` — Clear state

```bash
checkpoint_reset_all() {
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    rm -f "$state_file"
    echo "[IMP:9][bootstrap][checkpoint] RESET: All checkpoints cleared (state.json removed)" >&2
}
```

#### 6. `checkpoint_migrate_legacy()` — Import `.done` files

Run ONCE: reads old `.done` and `.hash` files, creates state.json entries for them.

```bash
checkpoint_migrate_legacy() {
    local legacy_dir="${CHECKPOINT_DIR:-/var/lib/platform/.bootstrap-checkpoints}"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"

    if [[ ! -d "$legacy_dir" ]]; then
        echo "[IMP:8][bootstrap][checkpoint] No legacy checkpoint dir at ${legacy_dir} — nothing to migrate" >&2
        return 0
    fi

    local done_files=()
    for f in "$legacy_dir"/.bootstrap-step-*.done; do
        [[ -f "$f" ]] && done_files+=("$f")
    done

    if [[ ${#done_files[@]} -eq 0 ]]; then
        echo "[IMP:8][bootstrap][checkpoint] No legacy .done files found — nothing to migrate" >&2
        return 0
    fi

    echo "[IMP:9][bootstrap][checkpoint] Migrating ${#done_files[@]} legacy .done files to state.json" >&2

    for done_file in "${done_files[@]}"; do
        # Extract step name: .bootstrap-step-ssh-access.done → ssh-access
        local step_name="${done_file##*/.bootstrap-step-}"
        step_name="${step_name%.done}"

        # Read hash if available
        local hash_file="${done_file%.done}.hash"
        local step_hash=""
        [[ -f "$hash_file" ]] && step_hash="$(head -1 "$hash_file" 2>/dev/null)"

        # Mark as done in state.json
        CHECKPOINT_STEP_HASH="$step_hash" _checkpoint_mark_done_json "$step_name"

        echo "[IMP:8][bootstrap][checkpoint] Migrated: ${step_name} (hash=${step_hash:0:12}...)" >&2
    done

    # Remove legacy files after successful migration
    rm -f "$legacy_dir"/.bootstrap-step-*.done "$legacy_dir"/.bootstrap-step-*.hash
    echo "[IMP:9][bootstrap][checkpoint] Legacy .done files migrated and removed" >&2
}
```

### Preserved API surface

These existing functions keep their signatures but delegate internally:
- `_verify_secrets_loaded()` — **UNCHANGED** (no checkpoint dependency, only reads secrets.env)
- `_checkpoint_version_check()` — **REMOVED** (replaced by per-step hash in state.json; backward compat via `checkpoint_migrate_legacy`)

### Changes to function signatures

| Function | Old behavior | New behavior |
|----------|-------------|-------------|
| `checkpoint_step(name, func, verify?, ...)` | `.done` files | `state.json` |
| `checkpoint_is_done(name, hash)` | `.done` file check | `state.json` lookup |
| `checkpoint_force(name)` | rm `.done` + `.hash` | set status="pending" in state.json |
| `checkpoint_reset_all()` | rm -rf CHECKPOINT_DIR/*.done | rm state.json |
| `_verify_secrets_loaded()` | reads secrets.env | UNCHANGED |
| `_checkpoint_version_check()` | VERSION file compare | REMOVED (migration handles this) |
| `checkpoint_migrate_legacy()` | N/A | NEW — import .done → state.json |

---

## TASK-2: Update `node-lifecycle.sh` — MINIMAL changes

### Line 135 + 213: `CHECKPOINT_DIR` → `CHECKPOINT_STATE_FILE`

**CURRENT (line 135):**
```bash
CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
```

**NEW (line 135, init mode):**
```bash
CHECKPOINT_STATE_FILE="/var/lib/platform/.bootstrap/state.json"
CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"  # kept for legacy migration
```

**CURRENT (line 141-142):**
```bash
[[ "$FORCE_MODE" == "true" ]] && rm -rf "$CHECKPOINT_DIR"
mkdir -p "$CHECKPOINT_DIR"
```

**NEW:**
```bash
[[ "$FORCE_MODE" == "true" ]] && checkpoint_reset_all
# Migrate legacy .done files ONCE (idempotent — after migration, legacy dir is empty)
checkpoint_migrate_legacy
```

**CURRENT (line 213-215, update mode):**
```bash
CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
[[ "$FORCE_MODE" == "true" ]] && rm -rf "$CHECKPOINT_DIR"
mkdir -p "$CHECKPOINT_DIR"
```

**NEW:**
```bash
CHECKPOINT_STATE_FILE="/var/lib/platform/.bootstrap/state.json"
CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
[[ "$FORCE_MODE" == "true" ]] && checkpoint_reset_all
checkpoint_migrate_legacy
```

### Lines 163-185: checkpoint_step calls — NO CHANGE

The `checkpoint_step "label" step_func` calls remain identical — only the internal implementation changes. The `CHECKPOINT_STEP_HASH` env var is still set per-step and consumed by `_checkpoint_mark_done_json`.

### Line 189-193: `_delegate --mode init` call — NO CHANGE

The `state_machine.py` invocation after step 13 continues to work. Since steps 1-13 are now recorded in state.json (by shell), state_machine.py will load the state and see them as done → skip them (idempotent). The content hash from shell (`CHECKPOINT_STEP_HASH`) is stored in state.json, and state_machine.py will compare it with its own hash computation.

---

## TASK-3: Verify `state_machine.py` compatibility

### No code changes needed

`state_machine.py` already:
1. Loads state.json on init (line 276-284)
2. Uses `_hash_changed(n, new_hash)` to compare stored hash (line 511-518)
3. Skips done steps (line 492-497: `_is_step_done`)
4. Uses 1-based numeric keys (`"1"`, `"2"`, etc.)

Shell-written state.json will use the same numeric key scheme. The only difference: shell maps step names to indices using its own `_step_hash()` output, while Python uses `self._step_hash()`. Since both use the same `INIT_STEPS` ordering, the numeric keys will align.

### Potential hash mismatch — ACCEPTED

Shell `CHECKPOINT_STEP_HASH` hashes `node-lifecycle.sh`; Python `_step_hash` hashes `state_machine.py`. On first post-migration run:
- Shell creates state.json entries with hash of `node-lifecycle.sh`
- Python loads state.json, compares with hash of `state_machine.py` → mismatch → re-executes step

This is acceptable and correct behavior — it's a one-time re-execution that ensures the hash is correct going forward. After re-execution, Python writes its own hash.

---

## TASK-4: Cleanup old `.done` files on VPS

### Migration trigger

`checkpoint_migrate_legacy()` in `checkpoint.sh` runs:
- At the start of `node-lifecycle.sh --mode init` (after FORCE_MODE check, before step 1)
- At the start of `node-lifecycle.sh --mode update`
- Idempotent: after first migration, legacy dir is empty → no-op

### What gets migrated

For each `.bootstrap-step-<name>.done` file in `/var/lib/platform/.bootstrap-checkpoints/`:
1. Extract step name from filename
2. Read `.bootstrap-step-<name>.hash` if it exists
3. Call `_checkpoint_mark_done_json <name>` with hash
4. Remove `.done` and `.hash` files

### Verification on VPS

After first bootstrap with new code:
```bash
# Verify state.json has migrated entries
ssh $NODE "cat /var/lib/platform/.bootstrap/state.json | python3 -m json.tool"

# Verify legacy dir is empty
ssh $NODE "ls /var/lib/platform/.bootstrap-checkpoints/"

# Verify --resume works correctly
make bootstrap-node NODE=$NODE  # second run → all steps skipped
```

---

## TASK-5: Update test suite

### Extend `tests/unit/test_state_machine.py`

Add test for shell-compatible state.json format.

| Test function | Scenario | Input | Expected |
|---|---|---|---|
| `test_shell_written_state_json` | state.json written by shell checkpoint | JSON with steps indexed by name, not by index | StateMachine loads it, recognizes done steps |
| `test_shell_hash_mismatch_rerun` | Shell hash ≠ Python hash → step reruns | state.json with hash="old_shell_hash", Python computes "new_python_hash" | `_hash_changed` returns True, step re-executes |

**Test structure for `test_shell_written_state_json`:**
```python
@ldd_trajectory
def test_shell_written_state_json(caplog, state_file):
    """StateMachine should load state.json written by shell checkpoint_step().
    
    Shell checkpoint writes steps using numeric keys found by iterating
    existing steps and finding the next available key. This test verifies
    the StateMachine can load and resume from such a state.
    """
    # Simulate shell-written state.json
    shell_state = {
        "mode": "init",
        "node": "test-node",
        "current_step": 3,
        "steps": {
            "1": {"name": "ssh_access", "status": "done", "hash": "shell_hash_abc123"},
            "2": {"name": "apt_deps", "status": "done"},
            "3": {"name": "tor_proxy", "status": "done"},
        },
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(shell_state))
    
    machine = sm.StateMachine(state_file_path=str(state_file))
    
    # Should recognize 3 steps as done
    assert machine._is_step_done(1) is True
    assert machine._is_step_done(2) is True
    assert machine._is_step_done(3) is True
    assert machine._is_step_done(4) is False
    
    # Next step should be 4 (first pending)
    assert machine.get_current_step() == 4
    
    # Hash mismatch should trigger rerun
    assert machine._hash_changed(1, "different_hash") is True
    assert machine._hash_changed(1, "shell_hash_abc123") is False
    
    logger.critical("[IMP:9][test] StateMachine loaded shell-written state.json — OK")
```

---

## TASK-6: Documentation update

### `core/internal/bootstrap/AGENTS.md`

Update the "Идемпотентность (.done + content-hash)" section to reflect new state.json-based system:

**OLD:**
```
| Механизм | Где | Что делает |
|----------|-----|------------|
| `.done`-маркер | `/var/lib/platform/.bootstrap/<step>.done` | Сигнализирует что шаг выполнен |
| content-hash | `content-hash.sh` | Хеширует содержимое скрипта/конфига |
```

**NEW:**
```
| Механизм | Где | Что делает |
|----------|-----|------------|
| `state.json` | `/var/lib/platform/.bootstrap/state.json` | Единый source of truth для checkpoint'ов (DevPlan 071) |
| content-hash | `content-hash.sh` (shell) / `state_machine._step_hash()` (Python) | Хеширует содержимое скриптов для idempotency |
```

---

## Verification

### Commands

```bash
# 1. Unit tests for state machine (covers state.json load/save/compatibility)
python3 -m pytest tests/unit/test_state_machine.py -v -s

# 2. Verify checkpoint.sh syntax
bash -n core/lib/checkpoint.sh

# 3. Full fast gate
make fix-gate && make gate MODE=fast

# 4. Manual shell unit test (local, no VPS needed)
# Create a tmp state.json and verify checkpoint operations:
source core/lib/paths.sh
CHECKPOINT_STATE_FILE="/tmp/test-state.json"
source core/lib/checkpoint.sh
checkpoint_reset_all
_checkpoint_mark_done_json "test-step"
_checkpoint_is_done_json "test-step" && echo "PASS: step is done" || echo "FAIL"
checkpoint_force "test-step"
_checkpoint_is_done_json "test-step" && echo "FAIL: should be pending" || echo "PASS: step reset"
```

### Rollback procedure

1. **Revert `checkpoint.sh`** — `git checkout -- core/lib/checkpoint.sh`
2. **Revert `node-lifecycle.sh`** — `git checkout -- core/internal/bootstrap/node-lifecycle.sh`
3. **Revert AGENTS.md** — `git checkout -- core/internal/bootstrap/AGENTS.md`
4. **On VPS** — `rm /var/lib/platform/.bootstrap/state.json` (if was created by new code)
5. **On VPS** — re-create legacy checkpoints: `mkdir -p /var/lib/platform/.bootstrap-checkpoints/`
6. **Run gate** — `make gate MODE=fast`

---

## Design Decisions

### ## @rationale (python3 -c vs jq)
Q: Why use `python3 -c` instead of `jq` for JSON operations?
A: `jq` is not guaranteed on VPS (it's not installed by bootstrap). `python3` is guaranteed (it's installed by `install-docker.sh` step, which includes python3 deps). Using `python3 -c` with `json` module avoids adding a new dependency.

### ## @rationale (keep CHECKPOINT_DIR for legacy migration)
Q: Why keep `CHECKPOINT_DIR` variable after migration?
A: `checkpoint_migrate_legacy()` uses it to find old `.done` files. After migration, the directory is empty but the variable is harmless. Remove in a future cleanup wave.

### ## @rationale (hash divergence on first post-migration run)
Q: Won't the first run after migration re-execute all steps?
A: Yes — steps 1-13 will re-execute once because shell hash (hashes `node-lifecycle.sh`) ≠ Python hash (hashes `state_machine.py`). This is acceptable and correct — it's a one-time cost that ensures the hash is consistent going forward. The alternative (trying to map shell hashes to Python hashes) would be fragile and error-prone.

### ## @rationale (no content-hash.sh changes)
Q: Why keep `content-hash.sh` and `step_hash_changed()`?
A: `content-hash.sh` `compute_step_hash()` is still called by `node-lifecycle.sh` to set `CHECKPOINT_STEP_HASH` per step (line 163: `CHECKPOINT_STEP_HASH="$(_step_hash "ssh-access")"`). This hash is then stored in state.json. `step_hash_changed()` is no longer called by `checkpoint_step()` (removed), but `checkpoint_migrate_legacy()` reads `.hash` files. The file remains available for backward compatibility but isn't actively used in the new flow.

---

## File Manifest

| File | Action | Lines affected |
|------|--------|---------------|
| `core/lib/checkpoint.sh` | REWRITE: replace .done logic with state.json | ~100 lines replaced, ~80 new |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY: lines 135-142, 213-215 | ~15 lines changed |
| `core/internal/bootstrap/lifecycle/state_machine.py` | NO CHANGE | 0 |
| `core/internal/bootstrap/content-hash.sh` | NO CHANGE | 0 |
| `core/internal/bootstrap/AGENTS.md` | UPDATE: checkpoint documentation section | ~10 lines |
| `tests/unit/test_state_machine.py` | EXTEND: 2 new tests | ~60 lines |

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_state_machine.py` | `test_shell_written_state_json` | Shell-written state.json with done steps → StateMachine loads and resumes correctly | `state_machine.StateMachine` |
| `tests/unit/test_state_machine.py` | `test_shell_hash_mismatch_rerun` | Shell hash ≠ Python hash → `_hash_changed` returns True | `state_machine.StateMachine._hash_changed` |
| `tests/unit/test_state_machine.py` | Existing tests (18 tests) | All existing tests must pass with NO changes | `state_machine.StateMachine` |

---

## $PARALLEL_GROUPS

### Wave 1 (independent)
- TASK-1: Rewrite `checkpoint.sh`
- TASK-5: Extend test suite (`test_state_machine.py`)
- TASK-6: Update documentation (AGENTS.md)
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1, TASK-5, TASK-6`

### Wave 2 (depends on Wave 1 — checkpoint.sh must be ready)
- TASK-2: Update `node-lifecycle.sh`
- TASK-3: Verify `state_machine.py` compatibility
- TASK-4: Write migration logic (already in TASK-1 checkpoint.sh, verify end-to-end)
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4`

### Wave 3 (verification)
- Run all tests
- **Command:** `coder Read DevPlan.md, run verification: make fix-gate && make gate MODE=fast`

$END_DEVPLAN
