$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Eliminate dual checkpoint system (shell `.done` files + Python `state.json`) that causes state divergence on `--resume`. Make `state.json` the ONLY checkpoint mechanism.
DESCRIPTION:           **Rev 1 (ORIGINAL, FLAWED):** Rewrite `checkpoint.sh` to delegate to `state.json` via `python3` subprocess with numeric keys. **FLAWED: step-name/key misalignment** — shell writes 16 steps (keys 1-16) but Python expects 23 steps (indices 1-23). After shell writes `read-node-yaml` at key 13, Python expects `ensure_secrets` at index 13 → `ensure_secrets` and `secrets_init` INCORRECTLY SKIPPED on resume. The original assertion "numeric keys will align" is FALSE (VerificationReport F1 CRITICAL).
                     **Rev 2 (FIXED):** Redesign state.json to use Python step NAMES (underscores) as dictionary keys instead of numeric indices. Shell `checkpoint.sh` delegates to a new `core/internal/checkpoint_migration.py` module (extracted inline Python per language policy Tier 1). The module maintains a `SHELL_TO_PYTHON_STEP` mapping table (e.g., `"ssh-access" → "ssh_access"`, `"read-node-yaml" → "read_node_yaml"`). `state_machine.py` is refactored to use step names as dict keys (from `int`-based `_is_step_done(n)` to name-based lookup). This eliminates ALL alignment ambiguity — shell writes `"read_node_yaml": {"status": "done"}`, Python checks `self.state.steps.get("read_node_yaml")` → CORRECT regardless of step inventory differences.
RATIONALE:             Dual system is the ROOT CAUSE of bootstrap resume bugs (DRIFT-B1, Brief 077). Shell sees `.done` marker → skips step → `state_machine` sees pending → re-executes. Or: state_machine marks done → shell `.done` missing → re-executes. One source of truth eliminates this class of bugs permanently.
ACCEPTANCE_CRITERIA:
  1. `checkpoint.sh` `checkpoint_step()` writes to `/var/lib/platform/.bootstrap/state.json` using Python step NAMES as keys (not numeric indices) — **F1 fix**
  2. `checkpoint.sh` `checkpoint_is_done()` reads from state.json by step name
  3. `checkpoint.sh` `checkpoint_force()` resets step by name in state.json
  4. `checkpoint.sh` `checkpoint_reset_all()` clears state.json
  5. Shell steps 1-16 are mapped to Python step names via `SHELL_TO_PYTHON_STEP` in `checkpoint_migration.py` — **F6 fix** (hyphens→underscores)
  6. `--resume` uses unified name-based state — `ensure_secrets` and `secrets_init` are **NOT incorrectly skipped** — **F1 verification**
  7. All inline `python3 -c "..."` blocks extracted to `core/internal/checkpoint_migration.py` — **F2 fix** (language policy Tier 1)
  8. Legacy `.done` files are migrated on first run → imported into state.json by name → removed
  9. `state_machine.py` uses name-based dict keys (`self.state.steps["ensure_secrets"]`), with backward-compat migration for old numeric-key state.json
  10. `make gate MODE=fast` — green
  11. `python3 -m pytest tests/unit/test_state_machine.py -v` — all pass
  12. No `paths.sh` `STATE_JSON` claim in documentation — **F3 fix**
IMPLEMENTS:            Wave 6A — core unification P0, DRIFT-B1
IMPACTS:
  - core/lib/checkpoint.sh (REWRITE: thin shell facade delegating to checkpoint_migration.py)
  - core/internal/checkpoint_migration.py (NEW: Python module with is_done/mark_done/force/migrate + SHELL_TO_PYTHON_STEP mapping)
  - core/internal/bootstrap/node-lifecycle.sh (MINOR: replace CHECKPOINT_DIR with CHECKPOINT_STATE_FILE, call checkpoint_migrate_legacy; update STRUCTURE comment — F5)
  - core/internal/bootstrap/lifecycle/state_machine.py (MODIFY: refactor from int-based _is_step_done(n) to name-based key lookup; add backward-compat migration for old numeric-key state.json)
  - core/internal/bootstrap/content-hash.sh (MINOR: remove CHECKPOINT_DIR reference in step_hash_changed — F4)
  - core/internal/bootstrap/AGENTS.md (UPDATE: document name-based state.json checkpoint system)
  - tests/unit/test_state_machine.py (EXTEND: 3 new tests for name-based keys, shell-compatible format, misalignment prevention)
REQUIRES:              state_machine.py MUST be refactored for name-based keys (breaking change to internal dict key format)
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

### CRITICAL: Step-name/key misalignment (F1 — Rev 1 flaw → fixed in Rev 2)

**The original Rev 1 design asserted: "Since both use the same INIT_STEPS ordering, the numeric keys will align." This is FALSE.**

Shell and Python track different step inventories:

| Shell step (hyphens) | Shell key | Python step (underscores) | Python index | Alignment in Rev 1 |
|----------------------|-----------|---------------------------|-------------|---------------------|
| `ssh-access` | 1 | `ssh_access` | 1 | ✅ Aligned |
| `apt-deps` | 2 | `apt_deps` | 2 | ✅ Aligned |
| `tor-proxy` | 3 | `tor_proxy` | 3 | ✅ Aligned |
| `install-docker` | 4 | `install_docker` | 4 | ✅ Aligned |
| `docker-auth` | 5 | `docker_auth` | 5 | ✅ Aligned |
| `user-platform` | 6 | `create_platform_user` | 6 | ✅ (different name, same index) |
| `user-ci-deploy` | 7 | `create_ci_deploy_user` | 7 | ✅ |
| `projects-base` | 8 | `create_projects_base` | 8 | ✅ |
| `firewall` | 9 | `firewall` | 9 | ✅ |
| `verify-core` | 10 | `verify_core` | 10 | ✅ |
| `verify-node-configs` | 11 | `verify_node_configs` | 11 | ✅ |
| `decrypt-secrets` | 12 | `decrypt_secrets` | 12 | ✅ |
| — | — | `ensure_secrets` | **13** | 🔴 **Shell writes `read-node-yaml` at key 13 instead** |
| — | — | `secrets_init` | **14** | 🔴 **Shell writes `ghcr-auth` at key 14 instead** |
| `read-node-yaml` | 13 | `read_node_yaml` | **15** | 🔴 Key mismatch: 13 ≠ 15 |
| `ghcr-auth` | 14 | `ghcr_auth` | **16** | 🔴 Key mismatch: 14 ≠ 16 |
| `sudoers` | 15 | `sudoers` | **17** | 🔴 Key mismatch: 15 ≠ 17 |
| `metrics-cron` | 16 | — | — | ⚠️ Shell-only step, Python has no equivalent |

**Devastating impact:** After shell writes steps 1-12 (aligned) + 13-16 (misaligned), state_machine loads state.json:
- `_is_step_done(13)` → True (key "13" is "done" — shell wrote `read-node-yaml` there) → **`ensure_secrets` INCORRECTLY SKIPPED**
- `_is_step_done(14)` → True (key "14" is "done" — shell wrote `ghcr-auth` there) → **`secrets_init` INCORRECTLY SKIPPED**

**Rev 2 fix:** Use step names as state.json keys. Shell maps its hyphenated names to Python underscore names via `SHELL_TO_PYTHON_STEP` mapping table. `state_machine.py` lookups use step names as dict keys, eliminating all index-based ambiguity. See TASK-1 and TASK-3 for full redesign.

---

## TASK-1: Create `core/internal/checkpoint_migration.py` + rewrite `checkpoint.sh` as thin facade

### Design decision (Rev 2)

**Rev 1 approach:** Shell `checkpoint.sh` embeds large `python3 -c "..."` blocks (60+ lines) to read/write state.json. Uses numeric indices as keys. **FLAWED:** (1) Violates language policy Tier 1 Strangler trigger — new `python3 -c "..."` must be extracted to `.py` module. (2) Numeric keys cause step-name misalignment (F1).

**Rev 2 approach:** Extract ALL JSON checkpoint logic into `core/internal/checkpoint_migration.py`. Shell `checkpoint.sh` becomes a THIN FACADE (<50 lines) that calls the Python module via `python3 checkpoint_migration.py <command> <args>`. Uses **Python step names** as state.json dictionary keys — no numeric indices. Maintains `SHELL_TO_PYTHON_STEP` mapping table for shell→Python name translation.

### Architecture: `checkpoint_migration.py`

```
core/internal/checkpoint_migration.py
├── SHELL_TO_PYTHON_STEP: dict[str, str]  — mapping: "ssh-access" → "ssh_access"
├── PYTHON_STEP_NAMES: frozenset[str]      — valid Python step names (for validation)
├── CLI dispatch: main(argv)               — subcommands: is-done, mark-done, force, reset, migrate-legacy
├── is_done(state_file, shell_step) → exit 0/1
├── mark_done(state_file, shell_step, hash) → writes state.json with Python step name as key
├── force_step(state_file, shell_step) → sets status="pending"
├── reset_all(state_file) → rm state.json
└── migrate_legacy(legacy_dir, state_file) → imports .done files → maps names → writes state.json
```

### State.json format (Rev 2 — name-based keys)

```json
{
  "mode": "init",
  "node": "test-node",
  "current_step": 0,
  "steps": {
    "ssh_access": {"name": "ssh_access", "status": "done", "hash": "abc123"},
    "apt_deps": {"name": "apt_deps", "status": "done", "hash": "def456"},
    "create_platform_user": {"name": "create_platform_user", "status": "done"},
    "ensure_secrets": {"name": "ensure_secrets", "status": "pending"},
    "read_node_yaml": {"name": "read_node_yaml", "status": "done", "hash": "xyz789"},
    "sudoers": {"name": "sudoers", "status": "done"}
  },
  "errors": [],
  "warnings": []
}
```

**Key change:** Dictionary keys are step NAMES (underscores), not numeric strings. Shell writes `"ssh_access"` (mapped from `"ssh-access"`). Python reads `self.state.steps["ssh_access"]` — no index ambiguity.

### SHELL_TO_PYTHON_STEP mapping table (F6 fix — unify naming convention)

```python
# In core/internal/checkpoint_migration.py
SHELL_TO_PYTHON_STEP: dict[str, str] = {
    "ssh-access":           "ssh_access",
    "apt-deps":             "apt_deps",
    "tor-proxy":            "tor_proxy",
    "install-docker":       "install_docker",
    "docker-auth":          "docker_auth",
    "user-platform":        "create_platform_user",
    "user-ci-deploy":       "create_ci_deploy_user",
    "projects-base":        "create_projects_base",
    "firewall":             "firewall",
    "verify-core":          "verify_core",
    "verify-node-configs":  "verify_node_configs",
    "decrypt-secrets":      "decrypt_secrets",
    "read-node-yaml":       "read_node_yaml",
    "ghcr-auth":            "ghcr_auth",
    "sudoers":              "sudoers",
    "metrics-cron":         "metrics_cron",  # Shell-only step, no Python equivalent
}
```

### Shell `checkpoint.sh` — thin facade (Rev 2)

```bash
# core/lib/checkpoint.sh — THIN FACADE over checkpoint_migration.py
# GREP_SUMMARY: checkpoint.sh, state.json, thin-facade, checkpoint_migration.py, name-based-keys
# STRUCTURE: ▶ ┌checkpoint_step┐ → ◇ python3 checkpoint_migration.py is-done → ◇ FORCE? → ⊕ python3 mark-done → ⎋ exit 0|1

# ── Internal helpers (delegate to Python) ──

_checkpoint_is_done_json() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" is-done "$state_file" "$step_name"
}

_checkpoint_mark_done_json() {
    local step_name="$1"
    local state_file="${CHECKPOINT_STATE_FILE:-/var/lib/platform/.bootstrap/state.json}"
    local hash="${CHECKPOINT_STEP_HASH:-}"
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" mark-done "$state_file" "$step_name" "$hash"
}

# ── Public API (unchanged signatures) ──

checkpoint_step() {
    local step_name="$1"; local step_func="$2"; local verify_func="${3:-}"
    shift 2; [[ -n "$verify_func" ]] && shift 1

    if [[ "$FORCE_MODE" == "true" ]]; then
        "$step_func" "$@"; return $?
    fi

    if [[ "$RESUME_MODE" == "true" ]] && _checkpoint_is_done_json "$step_name"; then
        echo "[IMP:8][bootstrap][checkpoint] SKIP: Step '${step_name}' already done (state.json)" >&2
        return 0
    fi

    if "$step_func" "$@"; then
        _checkpoint_mark_done_json "$step_name"
        return 0
    else
        local _rc=$?
        echo "[IMP:9][bootstrap][checkpoint] FAIL: Step '${step_name}' exit ${_rc}" >&2
        return $_rc
    fi
}

checkpoint_force() {
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" force \
        "${CHECKPOINT_STATE_FILE}" "$1"
}

checkpoint_reset_all() {
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" reset \
        "${CHECKPOINT_STATE_FILE}"
}

checkpoint_migrate_legacy() {
    python3 "${SCRIPT_DIR}/../internal/checkpoint_migration.py" migrate-legacy \
        "${CHECKPOINT_DIR:-/var/lib/platform/.bootstrap-checkpoints}" \
        "${CHECKPOINT_STATE_FILE}"
}
```

**Key properties of the facade:**
- `checkpoint.sh` is ~60 lines — **zero** inline `python3 -c "..."` blocks (F2 fix)
- All JSON logic in `checkpoint_migration.py` — unit-testable without bash
- Shell step names (hyphens) → Python step names (underscores) via `SHELL_TO_PYTHON_STEP` (F6 fix)
- `checkpoint.sh` sources `paths.sh` for SCRIPT_DIR resolution (NOT for STATE_JSON — F3 fix)

### Preserved API surface

| Function | Old (Rev 1) | New (Rev 2) |
|----------|------------|-------------|
| `checkpoint_step(name, func, verify?, ...)` | `.done` files (numeric keys) | `state.json` via `checkpoint_migration.py mark-done` (name-based keys) |
| `checkpoint_is_done(name)` | `.done` file check | `checkpoint_migration.py is-done` (name-based) |
| `checkpoint_force(name)` | rm `.done` + `.hash` | `checkpoint_migration.py force` |
| `checkpoint_reset_all()` | rm -rf CHECKPOINT_DIR/*.done | `checkpoint_migration.py reset` |
| `checkpoint_migrate_legacy()` | bash loop over .done files | `checkpoint_migration.py migrate-legacy` (F2 fix) |
| `_checkpoint_version_check()` | VERSION file compare | REMOVED entirely |

---

## TASK-2: Update `node-lifecycle.sh` — MINIMAL changes (+ F3, F5 fixes)

### Line 3: Update STRUCTURE comment (F5 fix)

**CURRENT:**
```bash
# STRUCTURE: ▶ --mode {init|update} → ... checkpoint_step preserves .done
```

**NEW:**
```bash
# STRUCTURE: ▶ --mode {init|update} → ┌arg parser┐ → ○ resolve NODE_YAML + TOR_ENABLED → ┌python3 state_machine.py --mode $MODE ...┐ → ⎋ exit 0|1; checkpoint_step delegates to checkpoint_migration.py → state.json (name-based keys)
```

### Line 135 + 213: `CHECKPOINT_DIR` → add `CHECKPOINT_STATE_FILE`

**CURRENT (line 135):**
```bash
CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
```

**NEW (line 135, init mode):**
```bash
CHECKPOINT_STATE_FILE="/var/lib/platform/.bootstrap/state.json"
CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"  # kept for legacy migration only
```

### Lines 141-142: Replace `rm -rf CHECKPOINT_DIR` with `checkpoint_reset_all` + `checkpoint_migrate_legacy`

**CURRENT:**
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

### Lines 213-215 (update mode): Same change

**NEW:**
```bash
CHECKPOINT_STATE_FILE="/var/lib/platform/.bootstrap/state.json"
CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
[[ "$FORCE_MODE" == "true" ]] && checkpoint_reset_all
checkpoint_migrate_legacy
```

### Lines 163-185: checkpoint_step calls — NO CHANGE

The `checkpoint_step "label" step_func` calls remain identical. Shell step names (hyphens) are passed through — `checkpoint_migration.py` maps them to Python step names (underscores) internally. The `CHECKPOINT_STEP_HASH` env var is still set per-step.

### Note on F3: `paths.sh` does NOT provide `STATE_JSON`

The Rev 1 DevPlan (01-DevPlan.md) incorrectly claimed "checkpoint.sh sources paths.sh → STATE_JSON path from there." This is FALSE — `paths.sh` has no `CHECKPOINT_STATE_FILE` or `STATE_JSON` variable. The correct approach (confirmed in Rev 2): `CHECKPOINT_STATE_FILE` is defined in `node-lifecycle.sh` (lines 135, 213) and consumed by `checkpoint.sh` as an env var with a safe default (`/var/lib/platform/.bootstrap/state.json`).

---

## TASK-3: Refactor `state_machine.py` for name-based keys (Rev 2)

### Why state_machine.py MUST change

Rev 1 claimed "NO CHANGE" to state_machine.py — this was based on the FALSE assumption that numeric keys would align. With name-based keys, state_machine.py's internal dict key mechanism changes from `str(n)` to `self._step_name(n)`. This is a **required refactor**, not optional.

### Changes required

#### 1. `_is_step_done(n: int) → bool` — lookup by step name

**CURRENT (line 492-497):**
```python
def _is_step_done(self, n: int) -> bool:
    key = str(n)
    if key not in self.state.steps:
        return False
    return self.state.steps[key].status == "done"
```

**NEW:**
```python
def _is_step_done(self, n: int) -> bool:
    """Check if step N is already completed (name-based key lookup)."""
    step_name = self._step_name(n)
    step = self.state.steps.get(step_name)
    return step is not None and step.status == "done"
```

#### 2. `_is_step_skipped(n: int) → bool` — same pattern

**NEW:**
```python
def _is_step_skipped(self, n: int) -> bool:
    step_name = self._step_name(n)
    step = self.state.steps.get(step_name)
    return step is not None and step.status == "skipped"
```

#### 3. `_hash_changed(n: int, new_hash: str) → bool` — name-based

**NEW:**
```python
def _hash_changed(self, n: int, new_hash: str) -> bool:
    step_name = self._step_name(n)
    step = self.state.steps.get(step_name)
    if step is None:
        return True
    return step.hash != new_hash
```

#### 4. `complete_step(n, hash_val)` — use step name as key

**CURRENT (line 335-345):**
```python
def complete_step(self, n: int, hash_val: str | None = None) -> None:
    key = str(n)
    if key not in self.state.steps:
        self.state.steps[key] = StepState(name=self._step_name(n))
    self.state.steps[key].status = "done"
    ...
```

**NEW:**
```python
def complete_step(self, n: int, hash_val: str | None = None) -> None:
    step_name = self._step_name(n)
    if step_name not in self.state.steps:
        self.state.steps[step_name] = StepState(name=step_name)
    self.state.steps[step_name].status = "done"
    if hash_val:
        self.state.steps[step_name].hash = hash_val
    logger.info("[IMP:9][StateMachine][complete_step] Step %d (%s) DONE", n, step_name)
    self.save()
```

#### 5. `start_step(n)`, `skip_step(n, reason)`, `fail_step(n, error)` — same pattern

All mutation methods change `key = str(n)` → `step_name = self._step_name(n)` and use `step_name` as dict key.

#### 6. `get_current_step() → int | None` — check by step name

**NEW:**
```python
def get_current_step(self) -> int | None:
    step_list = self._step_list()
    if not step_list:
        return None
    if self.state.current_step == 0:
        return 1
    for i in range(1, len(step_list) + 1):
        step_name = self._step_name(i)
        step = self.state.steps.get(step_name)
        if step is None:
            return i
        if step.status in ("pending", "failed", "running"):
            return i
    return None
```

#### 7. `_check_precondition` and `_check_postcondition` — use step names

These already used `str(n)` for key lookup. Change to `self._step_name(n)`.

#### 8. `BootstrapState.from_dict()` — backward-compat migration

Add detection of old numeric-key format and auto-migration:

```python
@classmethod
def from_dict(cls, data: dict[str, Any], step_list: list[str] | None = None) -> BootstrapState:
    steps = {}
    for k, v in data.get("steps", {}).items():
        # Detect old numeric-key format: keys like "1", "2", ...
        if k.isdigit() and step_list:
            idx = int(k)
            if 1 <= idx <= len(step_list):
                k = step_list[idx - 1]  # Migrate to name-based key
                logger.info("[IMP:8][StateMachine][from_dict] Migrated numeric key %d → %s", idx, k)
        steps[k] = StepState.from_dict(v)
    ...
```

Called from `StateMachine.__init__`:
```python
self.state = BootstrapState.from_dict(data, step_list=self._step_list())
```

#### Summary of state_machine.py changes

| Method | Lines affected | Change |
|--------|---------------|--------|
| `_is_step_done` | 492-497 | `str(n)` → `self._step_name(n)` |
| `_is_step_skipped` | 501-507 | Same pattern |
| `_hash_changed` | 511-518 | Same pattern |
| `complete_step` | 335-345 | `key = str(n)` → `step_name = self._step_name(n)` |
| `start_step` | 319-327 | Same pattern |
| `skip_step` | 353-361 | Same pattern |
| `fail_step` | 369-378 | Same pattern |
| `get_current_step` | 388-409 | Lookup via `self.state.steps.get(step_name)` |
| `_check_precondition` | 528-556 | Use step names for key lookup |
| `_check_postcondition` | 565+ | Same pattern |
| `BootstrapState.from_dict` | 228-240 | Add `step_list` param + auto-migration |
| `StateMachine.__init__` | 270-292 | Pass `step_list` to `from_dict` |

**Total: ~12 methods, ~40 lines changed.** No change to external API or step execution logic — only dict key format.

### Post-refactor step alignment guarantee

After refactoring, `_is_step_done(13)` looks up `self._step_name(13)` = `"ensure_secrets"` in `self.state.steps`. Shell wrote `"read_node_yaml": {"status": "done"}` (via SHELL_TO_PYTHON_STEP mapping). Since `self.state.steps.get("ensure_secrets")` is `None` (shell doesn't write `ensure_secrets`), → `_is_step_done(13)` returns `False` → `ensure_secrets` is CORRECTLY executed. The misalignment is eliminated because the shell and Python use the SAME KEY SPACE (step names).

---

## TASK-4: Cleanup old `.done` files + content-hash.sh fix (F4)

### Migration trigger

`checkpoint_migrate_legacy()` in `checkpoint_migration.py` runs:
- At the start of `node-lifecycle.sh --mode init` (after FORCE_MODE check, before step 1)
- At the start of `node-lifecycle.sh --mode update`
- Idempotent: after first migration, legacy dir is empty → no-op

### What gets migrated (in checkpoint_migration.py)

For each `.bootstrap-step-<name>.done` file in `/var/lib/platform/.bootstrap-checkpoints/`:
1. Extract step name from filename
2. Map shell step name → Python step name via `SHELL_TO_PYTHON_STEP`
3. Read `.bootstrap-step-<name>.hash` if it exists
4. Write entry to state.json with Python step name as key
5. Remove `.done` and `.hash` files

### F4 fix: `content-hash.sh` `step_hash_changed()` uses old `CHECKPOINT_DIR`

**Current (content-hash.sh:109):**
```bash
local hash_file="${CHECKPOINT_DIR}/.bootstrap-step-${step_label}.hash"
```

After migration, `.hash` files will no longer be created by new checkpoint code. `step_hash_changed()` will always return "changed" (no file = changed), triggering one re-execution per step. This is **acceptable** (one-time re-execution is documented as expected), but the `CHECKPOINT_DIR` dependency should be cleaned up.

**Fix:** In `content-hash.sh`, update `step_hash_changed()` to have a safe fallback when `CHECKPOINT_DIR` is unset or the `.hash` file is missing:

```bash
step_hash_changed() {
    local step_label="$1"
    local current_hash="$2"
    local hash_file="${CHECKPOINT_DIR:-/var/lib/platform/.bootstrap-checkpoints}/.bootstrap-step-${step_label}.hash"
    if [[ ! -f "$hash_file" ]]; then
        # No stored hash → always changed (first run or after migration)
        return 0
    fi
    local stored_hash
    stored_hash="$(head -1 "$hash_file" 2>/dev/null)"
    [[ "$stored_hash" != "$current_hash" ]]
}
```

The `CHECKPOINT_DIR` default provides backward compatibility during the transition period. In a future cleanup wave, `step_hash_changed()` can be removed entirely (replaced by name-based hash comparison in Python).

---

## TASK-5: Update test suite (name-based keys + misalignment prevention)

### Extend `tests/unit/test_state_machine.py`

Add 3 new tests:

| Test function | Scenario | Input | Expected |
|---|---|---|---|
| `test_name_based_keys_load` | state.json with name-based keys → StateMachine loads correctly | `{"steps": {"ssh_access": {"status": "done"}, "ensure_secrets": {"status": "pending"}}}` | `_is_step_done(1)` → True, `_is_step_done(13)` → False |
| `test_shell_written_state_json` | Shell-written state.json (via checkpoint_migration.py) → StateMachine loads and resumes correctly | Name-based keys: `{"ssh_access": {...}, "apt_deps": {...}}` | `_is_step_done` by index → correct, `get_current_step()` returns next pending |
| `test_name_key_misalignment_prevented` (NEW — F1 regression guard) | Shell writes `read_node_yaml` at numeric key 13 (old-style), Python expects `ensure_secrets` at index 13 → MUST detect mismatch and execute `ensure_secrets` | Old-format state.json with numeric keys "1".."16" where "13" = `read_node_yaml/done` | After from_dict migration: `_is_step_done(13)` for `ensure_secrets` → False (CORRECT — step not skipped); `_is_step_done(15)` for `read_node_yaml` → True |

### Test: `test_name_key_misalignment_prevented` (F1 regression guard)

```python
@ldd_trajectory
def test_name_key_misalignment_prevented(caplog, state_file):
    """Regression guard for F1: ensure_secrets is NOT incorrectly skipped
    when shell wrote read-node-yaml at numeric key 13.
    
    This test reproduces the EXACT scenario from the VerificationReport
    that would cause ensure_secrets + secrets_init to be skipped on resume.
    """
    # Simulate old numeric-key state.json as shell would have written it
    old_state = {
        "mode": "init", "node": "test-node", "current_step": 16,
        "steps": {
            "1":  {"name": "ssh_access", "status": "done", "hash": "abc"},
            "2":  {"name": "apt_deps", "status": "done"},
            "3":  {"name": "tor_proxy", "status": "done"},
            "4":  {"name": "install_docker", "status": "done"},
            "5":  {"name": "docker_auth", "status": "done"},
            "6":  {"name": "create_platform_user", "status": "done"},
            "7":  {"name": "create_ci_deploy_user", "status": "done"},
            "8":  {"name": "create_projects_base", "status": "done"},
            "9":  {"name": "firewall", "status": "done"},
            "10": {"name": "verify_core", "status": "done"},
            "11": {"name": "verify_node_configs", "status": "done"},
            "12": {"name": "decrypt_secrets", "status": "done"},
            "13": {"name": "read_node_yaml", "status": "done", "hash": "xyz"},  # ← MISPLACED
            "14": {"name": "ghcr_auth", "status": "done"},
            "15": {"name": "sudoers", "status": "done"},
            "16": {"name": "metrics_cron", "status": "done"},
        },
        "errors": [], "warnings": [],
    }
    state_file.write_text(json.dumps(old_state))

    machine = sm.StateMachine(state_file_path=str(state_file))

    # VERIFY: ensure_secrets (index 13) is NOT skipped
    # Old numeric-key lookup would have found key "13" = read_node_yaml → done
    # New name-based lookup: _step_name(13) = "ensure_secrets" → not in steps → pending
    assert machine._is_step_done(13) is False, \
        "F1 REGRESSION: ensure_secrets incorrectly skipped! Step 13 should be pending."

    # VERIFY: secrets_init (index 14) is NOT skipped
    assert machine._is_step_done(14) is False, \
        "F1 REGRESSION: secrets_init incorrectly skipped! Step 14 should be pending."

    # VERIFY: read_node_yaml (index 15) IS correctly recognized as done
    # After migration: key "13" → name "read_node_yaml" → _step_name(15) = "read_node_yaml"
    assert machine._is_step_done(15) is True, \
        "read_node_yaml should be done (migrated from numeric key 13)"

    # VERIFY: get_current_step returns 13 (ensure_secrets) — NOT 17 (sudoers+1)
    assert machine.get_current_step() == 13, \
        f"Expected next step 13 (ensure_secrets), got {machine.get_current_step()}"

    logger.critical("[IMP:9][test] F1 regression guard: ensure_secrets NOT incorrectly skipped — PASS")
```

### Existing tests compatibility

All 40 existing tests use `complete_step(n, hash)` and `_is_step_done(n)` with int indices. After the refactor to name-based keys, these tests MUST continue to pass — the int-based API is preserved (internally maps to step names via `_step_name(n)`). No existing test changes needed.

---

## TASK-6: Documentation update

### `core/internal/bootstrap/AGENTS.md`

Update the "Идемпотентность (.done + content-hash)" section:

**OLD:**
```
| Механизм | Где | Что делает |
|----------|-----|------------|
| `.done`-маркер | `/var/lib/platform/.bootstrap/<step>.done` | Сигнализирует что шаг выполнен |
| content-hash | `content-hash.sh` | Хеширует содержимое скрипта/конфига |
```

**NEW (Rev 2):**
```
| Механизм | Где | Что делает |
|----------|-----|------------|
| `state.json` (name-based keys) | `/var/lib/platform/.bootstrap/state.json` | Единый source of truth для checkpoint'ов. Ключи — имена шагов Python (underscores). Shell маппит свои hyphen-имена через `checkpoint_migration.py::SHELL_TO_PYTHON_STEP`. (DevPlan 071 Rev 2) |
| content-hash | `content-hash.sh` (shell) / `state_machine._step_hash()` (Python) | Хеширует содержимое скриптов для idempotency. Shell hash пишется в state.json через `checkpoint_migration.py`, Python проверяет через `_hash_changed()`. |
```

### `core/internal/checkpoint_migration.py` — module contract

New file must include full semantic markup per project standards:
- `# GREP_SUMMARY: checkpoint_migration.py, state.json, name-based-keys, SHELL_TO_PYTHON_STEP, legacy-migration`
- `# STRUCTURE: ▶ ┌CLI dispatch (is-done|mark-done|force|reset|migrate-legacy)┐ → ◇ SHELL_TO_PYTHON_STEP mapping → ⊕ state.json read/write → ∑ atomic save → ⎋ exit 0|1`
- `# region MODULE_CONTRACT` with `## @purpose`, `## @scope`, `## @invariants`
- LDD logs at IMP:8-10 for all state mutations
- Full function contracts for each CLI subcommand handler

---

## Verification

### Commands

```bash
# 1. Unit tests for state machine (covers name-based keys + F1 regression)
python3 -m pytest tests/unit/test_state_machine.py -v -s

# 2. New checkpoint_migration.py unit tests
python3 -m pytest tests/unit/test_checkpoint_migration.py -v -s

# 3. Verify checkpoint.sh syntax (thin facade, no inline python3 -c)
bash -n core/lib/checkpoint.sh

# 4. Full fast gate
make fix-gate && make gate MODE=fast

# 5. Manual end-to-end test (local)
CHECKPOINT_STATE_FILE="/tmp/test-state-071.json" \
CHECKPOINT_DIR="/tmp/test-legacy-071" \
python3 core/internal/checkpoint_migration.py reset /tmp/test-state-071.json
python3 core/internal/checkpoint_migration.py mark-done /tmp/test-state-071.json "ssh-access" "test-hash-abc"
python3 core/internal/checkpoint_migration.py is-done /tmp/test-state-071.json "ssh-access"
# → exit 0 (step is done)
python3 core/internal/checkpoint_migration.py is-done /tmp/test-state-071.json "ensure-secrets"
# → exit 1 (step not in state.json)
```

### Rollback procedure

1. **Revert `checkpoint.sh`** — `git checkout -- core/lib/checkpoint.sh`
2. **Remove `checkpoint_migration.py`** — `git checkout -- core/internal/checkpoint_migration.py` (if tracked) or `rm` (if new)
3. **Revert `state_machine.py`** — `git checkout -- core/internal/bootstrap/lifecycle/state_machine.py`
4. **Revert `node-lifecycle.sh`** — `git checkout -- core/internal/bootstrap/node-lifecycle.sh`
5. **Revert AGENTS.md** — `git checkout -- core/internal/bootstrap/AGENTS.md`
6. **On VPS** — `rm /var/lib/platform/.bootstrap/state.json` (if was created by new code)
7. **On VPS** — re-create legacy checkpoints: `mkdir -p /var/lib/platform/.bootstrap-checkpoints/`
8. **Run gate** — `make gate MODE=fast`

---

## Design Decisions

### ## @rationale (name-based keys vs numeric indices) — NEW (Rev 2, F1 fix)
Q: Why use Python step names as state.json keys instead of numeric indices?
A: Numeric indices cause critical misalignment because shell and Python have different step inventories (16 vs 23 steps). After shell writes key 13 (`read-node-yaml`), Python interprets key 13 as `ensure_secrets` → `ensure_secrets` and `secrets_init` are incorrectly skipped on resume. Name-based keys eliminate this class of bugs entirely — `self.state.steps["ensure_secrets"]` unambiguously refers to the `ensure_secrets` step regardless of step inventory differences. The cost is a ~40-line refactor of state_machine.py's dict key mechanism (from `str(n)` to `self._step_name(n)`), which is localized, mechanical, and test-covered.

### ## @rationale (checkpoint_migration.py extraction vs inline python3 -c) — NEW (Rev 2, F2 fix)
Q: Why create a separate `checkpoint_migration.py` instead of using `python3 -c "..."` in the shell?
A: Per AGENTS.md language policy Tier 1 Strangler trigger: "Добавление нового `python3 -c '...'` или heredoc-блока → вынести эту конкретную логику в отдельный `.py` модуль." The Rev 1 design embedded ~100 lines of inline Python across 4 `python3 -c "..."` blocks. Extracting to `checkpoint_migration.py` provides: (1) compliance with language policy, (2) unit-testability without bash, (3) cleaner shell facade (<60 lines, zero inline Python), (4) the SHELL_TO_PYTHON_STEP mapping table that solves F1.

### ## @rationale (python3 subprocess vs jq) — PRESERVED from Rev 1
Q: Why use `python3` subprocess instead of `jq` for JSON operations?
A: `jq` is not guaranteed on VPS (it's not installed by bootstrap). `python3` is guaranteed (it's installed by `install-docker.sh` step). The `checkpoint_migration.py` module uses `python3` CLI dispatch — no new dependency.

### ## @rationale (keep CHECKPOINT_DIR for legacy migration)
Q: Why keep `CHECKPOINT_DIR` variable after migration?
A: `checkpoint_migration.py migrate-legacy` uses it to find old `.done` files. After migration, the directory is empty but the variable is harmless. Remove in a future cleanup wave.

### ## @rationale (hash divergence on first post-migration run)
Q: Won't the first run after migration re-execute all steps?
A: Yes — steps will re-execute once because shell hash (hashes `node-lifecycle.sh`) ≠ Python hash (hashes `state_machine.py`). This is acceptable and correct — it's a one-time cost that ensures the hash is consistent going forward. The alternative (trying to map shell hashes to Python hashes) would be fragile and error-prone.

### ## @rationale (step name convention: underscores) — NEW (Rev 2, F6 fix)
Q: Why unify to Python underscores instead of shell hyphens?
A: Python uses underscores (`ssh_access`) throughout `state_machine.py` and `INIT_STEPS`. The shell historically used hyphens (`ssh-access`). Unifying to underscores as the canonical format means: (1) state.json keys match Python identifiers — grep-able and IDE-friendly, (2) the SHELL_TO_PYTHON_STEP mapping table is the single place that translates hyphens→underscores, (3) all new code uses underscores. Shell callers are unchanged — they continue passing hyphenated names to `checkpoint_step`, which maps them internally.

### ## @rationale (content-hash.sh step_hash_changed — deferred cleanup) — NEW (Rev 2, F4 fix)
Q: Why not remove `content-hash.sh`'s `CHECKPOINT_DIR` dependency entirely?
A: `step_hash_changed()` is still called by existing code paths. Removing it will cause shell errors on nodes that haven't been migrated yet. Adding a safe default (`${CHECKPOINT_DIR:-/var/lib/platform/.bootstrap-checkpoints}`) ensures backward compatibility. Full removal is deferred to a future cleanup wave when all VPS nodes have been migrated and the old `.hash` files no longer exist.

---

## File Manifest

| File | Action | Lines affected | Notes |
|------|--------|---------------|-------|
| `core/lib/checkpoint.sh` | REWRITE: thin facade (<60 lines) | ~200 lines deleted, ~60 new | Zero inline `python3 -c` — all delegated to `checkpoint_migration.py` |
| `core/internal/checkpoint_migration.py` | **NEW** — Python module with CLI dispatch | ~200 lines new | `SHELL_TO_PYTHON_STEP` mapping + `is_done`/`mark_done`/`force`/`reset`/`migrate_legacy` |
| `core/internal/bootstrap/lifecycle/state_machine.py` | **MODIFY** (Rev 2 — was "NO CHANGE" in Rev 1) | ~40 lines changed | Name-based key lookup in `_is_step_done`, `complete_step`, `start_step`, `skip_step`, `fail_step`, `_hash_changed`, `get_current_step`, `_check_precondition`, `_check_postcondition`; backward-compat migration in `from_dict` |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY: add `CHECKPOINT_STATE_FILE`, call `checkpoint_migrate_legacy`, update STRUCTURE comment | ~15 lines changed | F3: no `paths.sh` STATE_JSON claim; F5: STRUCTURE comment updated |
| `core/internal/bootstrap/content-hash.sh` | MINOR: add safe `CHECKPOINT_DIR` default in `step_hash_changed()` | ~3 lines changed | F4 fix |
| `core/internal/bootstrap/AGENTS.md` | UPDATE: checkpoint documentation for name-based keys | ~10 lines | Document `SHELL_TO_PYTHON_STEP` mapping, name-based keys |
| `tests/unit/test_state_machine.py` | EXTEND: 3 new tests | ~120 lines new | `test_name_based_keys_load`, `test_shell_written_state_json`, `test_name_key_misalignment_prevented` |
| `tests/unit/test_checkpoint_migration.py` | **NEW** — unit tests for the Python module | ~80 lines new | Test mapping, CLI dispatch, legacy migration |

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_state_machine.py` | `test_name_based_keys_load` | State.json with name-based keys → StateMachine loads, `_is_step_done()` works correctly | `state_machine.StateMachine` |
| `tests/unit/test_state_machine.py` | `test_shell_written_state_json` | Shell-written state.json with Python step names → StateMachine resumes correctly | `state_machine.StateMachine` |
| `tests/unit/test_state_machine.py` | `test_name_key_misalignment_prevented` | **F1 regression guard:** Old numeric-key format where `read_node_yaml` at key 13 → migrated to name-based → `ensure_secrets` NOT incorrectly skipped | `state_machine.StateMachine`, `BootstrapState.from_dict` |
| `tests/unit/test_state_machine.py` | Existing tests (40 tests) | All existing tests pass with NO changes (int-based API preserved) | `state_machine.StateMachine` |
| `tests/unit/test_checkpoint_migration.py` | `test_shell_to_python_mapping` | All 16 shell step names map to correct Python step names | `checkpoint_migration.SHELL_TO_PYTHON_STEP` |
| `tests/unit/test_checkpoint_migration.py` | `test_mark_done_and_is_done` | `mark_done` writes, `is_done` reads back correctly | `checkpoint_migration` |
| `tests/unit/test_checkpoint_migration.py` | `test_legacy_migration` | Old `.done` files → migrated to name-based state.json | `checkpoint_migration.migrate_legacy` |

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- TASK-1a: Create `core/internal/checkpoint_migration.py` (Python module)
- TASK-5a: Create `tests/unit/test_checkpoint_migration.py` (Python module tests)
- TASK-6: Update documentation (AGENTS.md)
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1a (checkpoint_migration.py), TASK-5a (test_checkpoint_migration.py), TASK-6 (AGENTS.md)`

### Wave 2 (depends on checkpoint_migration.py)
- TASK-1b: Rewrite `core/lib/checkpoint.sh` as thin facade
- TASK-3: Refactor `state_machine.py` for name-based keys
- TASK-5b: Add 3 tests to `tests/unit/test_state_machine.py`
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-1b (checkpoint.sh), TASK-3 (state_machine.py), TASK-5b (test_state_machine.py)`

### Wave 3 (depends on Wave 2 — integration)
- TASK-2: Update `node-lifecycle.sh` (+ F3, F4, F5 fixes)
- TASK-4: content-hash.sh CHECKPOINT_DIR fix (F4)
- **Command:** `coder Read DevPlan.md, implement Wave 3: TASK-2 (node-lifecycle.sh), TASK-4 (content-hash.sh)`

### Wave 4 (verification)
- Run all tests, gate, manual validation
- **Command:** `coder Read DevPlan.md, run verification: make fix-gate && make gate MODE=fast && python3 -m pytest tests/unit/test_state_machine.py tests/unit/test_checkpoint_migration.py -v -s`

$END_DEVPLAN
