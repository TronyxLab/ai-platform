$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Fix secrets_manager.py append-mode bug (creates duplicate lines on repeated calls) and remove leftover LITELLM_METRICS_TOKEN from .env.example. Complete token unification.
DESCRIPTION:           secrets_manager.py:312 uses `open(secrets_env, "a")` — append mode. On repeated bootstrap runs, the same generated secrets are appended again → duplicates in secrets.env → `source` reads last value → non-deterministic behavior. Fix: read existing env FIRST (via source_secrets_env), merge with newly generated secrets, write all at once using atomic overwrite. Also cleanup: remove LITELLM_METRICS_TOKEN from .env.example (already unified to LITELLM_MASTER_KEY — monitoring/docker-compose.base.yml line 39 confirms this migration).
RATIONALE:             Append-mode is a time-bomb. On third bootstrap with --force, secrets.env grows with duplicates. The `source` command reads the LAST occurrence, so the first bootstrap's value is lost. If that value was used to provision LiteLLM virtual keys, subsequent runs will use a different key → auth failures.
ACCEPTANCE_CRITERIA:
  1. `ensure_secrets()` reads existing secrets.env FIRST via `source_secrets_env()`
  2. Generated secrets are MERGED with existing entries, not appended
  3. File is written ONCE with `open(secrets_env, "w")` — atomic overwrite (tmp + rename)
  4. Non-generated secrets (decrypted from SOPS) are PRESERVED on overwrite
  5. Repeated calls produce identical secrets.env (idempotent)
  6. LITELLM_METRICS_TOKEN removed from .env.example:129
  7. No grep hits for LITELLM_METRICS_TOKEN in non-comment, non-doc contexts (except this DevPlan and Brief)
  8. `python3 -m pytest tests/unit/test_secrets_manager.py -v` — all tests pass + new idempotency test
  9. `make gate MODE=fast` — green
IMPLEMENTS:            Wave 6A — core unification P0, DRIFT-S3, DRIFT-S5 (partial)
IMPACTS:
  - core/internal/bootstrap/lifecycle/secrets_manager.py (ensure_secrets:252-358, overwrite logic)
  - .env.example:129 (remove line)
  - tests/unit/test_secrets_manager.py (extend: idempotency test + preserve-non-generated test)
REQUIRES:              None (standalone)
$END_ARTIFACT_CONTRACT

---

# DevPlan 072: Secrets Atomic Write + Token Cleanup — EXPANDED

## Source Analysis

### The append-mode bug

**Location:** `secrets_manager.py:310-313`
```python
# Persist to secrets.env file
try:
    secrets_path = Path(secrets_env)
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    with open(secrets_env, "a") as f:      # ← BUG: append mode
        f.write(f"{var_name}={value}\n")
    logger.info("[IMP:8][secrets_manager] Persisted %s to %s", var_name, secrets_env)
```

**Root cause:** The `for secret in secrets_to_process` loop (line 285) iterates over all generated secrets. For each one:
1. Checks `os.environ` — if set, skips (line 290-293) ✅
2. Checks `gen_command` — if missing, skips (line 295-297) ✅
3. Generates value (line 299)
4. Sets `os.environ` (line 305)
5. Appends to file (line 312-313) ← writes EACH secret individually with "a" mode

**The problem chain:**
1. First bootstrap → secrets.env gets 7 lines (one per generated secret)
2. Second bootstrap (--resume) → `os.environ` already has values from step 1 of `ensure_secrets` (line 261-265: `source_secrets_env` loads env vars into `os.environ`) → all 7 secrets skipped → file unchanged ✅
3. Third bootstrap (--force) → `monkeypatch.delenv` or new shell session → `os.environ` empty → all 7 generated AGAIN → appended to file → now 14 lines with DUPLICATES

**The silent corruption:**
```bash
# After 3 runs:
LITELLM_MASTER_KEY=sk-aaa111
LANGFUSE_INIT_ORG_ID=org_bbb222
...
LITELLM_MASTER_KEY=sk-ccc333   # ← source reads THIS one (LAST wins)
LANGFUSE_INIT_ORG_ID=org_ddd444
```

`source secrets.env` reads the **last** occurrence of each variable. If `sk-aaa111` was used to provision LiteLLM virtual keys, but the effective value is now `sk-ccc333`, provisioning will fail with auth errors.

### Current flow of `ensure_secrets()` (lines 252-358)

```
Step 1 (line 262-265): source_secrets_env(secrets_env) → env_vars dict
                       Load env_vars into os.environ (if not already set)
Step 2 (line 268):     _read_manifest(manifest_path) → manifest_generated (or [])
Step 3 (line 270-282): Select secrets_to_process: manifest_generated or _FALLBACK_SECRETS
Step 3 (line 285-326): FOR each secret:
                         - Check os.environ → skip if set
                         - _generate_secret() → value
                         - os.environ[var_name] = value
                         - generated.append(var_name)
                         - open(secrets_env, "a") + write  ← BUG HERE
Step 4 (line 328-341): _persist_to_sops for generated secrets
Step 5 (line 343-344): _ensure_htpasswd(secrets_env)
```

### Problem with direct overwrite

If we simply change `"a"` → `"w"`, we'd:
1. On first run: write 7 generated secrets → file has 7 lines ✅
2. On second run: `source_secrets_env` loads existing 7 → they're also in `os.environ` → skip generation → file still has 7 ✅ (because we write nothing)
3. BUT: if secrets.env has **non-generated** secrets (from SOPS decryption, like `WEBNAMES_API_KEY`, `POSTGRES_PASSWORD`), the overwrite would **DELETE** them because `ensure_secrets` only writes generated secrets

**Solution:** Collect ALL existing entries from secrets.env (from step 1) + newly generated entries → write ALL at once.

### The fix design

```
Step 1 (UNCHANGED):     source_secrets_env(secrets_env) → existing_vars dict
                         Load into os.environ
Step 2 (UNCHANGED):     _read_manifest(manifest_path)
Step 3 (UNCHANGED):     Select secrets_to_process
Step 3.5 (MODIFIED):    FOR each secret:
                           - Check os.environ → skip if set
                           - _generate_secret()
                           - os.environ[var_name] = value
                           - generated.append(var_name)
                           - NO FILE WRITE YET
Step 3.6 (NEW):         RE-READ secrets_env (to catch any changes made by other processes)
                         MERGE: existing_vars + generated_vars
                         ATOMIC WRITE: write tmp + rename
Step 4 (UNCHANGED):     _persist_to_sops
Step 5 (UNCHANGED):     _ensure_htpasswd
```

---

## TASK-1: Fix `ensure_secrets()` — atomic overwrite with merge

### Exact code change in `secrets_manager.py`

#### Location: Lines 285-326 — the generation loop

**CURRENT (lines 285-326):**
```python
    # ── Step 3: For each secret, check if present; if not, generate ──
    for secret in secrets_to_process:
        var_name: str = secret["name"]
        gen_command: str = secret.get("gen_command", "")

        # Check existing env var
        current = os.environ.get(var_name, "")
        if current:
            logger.info("[IMP:8][secrets_manager] %s already set — skipping", var_name)
            continue

        if not gen_command:
            logger.warning("[IMP:7][secrets_manager] %s has no gen_command — skipping", var_name)
            continue

        value = _generate_secret(var_name, gen_command)
        if value is None:
            logger.warning("[IMP:7][secrets_manager] Failed to generate %s — continuing", var_name)
            continue

        # Set in os.environ
        os.environ[var_name] = value
        generated.append(var_name)

        # Persist to secrets.env file
        try:
            secrets_path = Path(secrets_env)
            secrets_path.parent.mkdir(parents=True, exist_ok=True)
            with open(secrets_env, "a") as f:
                f.write(f"{var_name}={value}\n")
            logger.info("[IMP:8][secrets_manager] Persisted %s to %s", var_name, secrets_env)
        except OSError as e:
            logger.warning(
                "[IMP:7][secrets_manager] Cannot write %s to %s: %s",
                var_name,
                secrets_env,
                e,
            )

        logger.info(
            "[IMP:9][secrets_manager] Auto-generated %s (MUST be added to SOPS for production)",
            var_name,
        )
```

**NEW (replacing lines 285-326):**
```python
    # ── Step 3: For each secret, check if present; if not, generate ──
    generated_vars: dict[str, str] = {}
    for secret in secrets_to_process:
        var_name: str = secret["name"]
        gen_command: str = secret.get("gen_command", "")

        # Check existing env var
        current = os.environ.get(var_name, "")
        if current:
            logger.info("[IMP:8][secrets_manager] %s already set — skipping", var_name)
            continue

        if not gen_command:
            logger.warning("[IMP:7][secrets_manager] %s has no gen_command — skipping", var_name)
            continue

        value = _generate_secret(var_name, gen_command)
        if value is None:
            logger.warning("[IMP:7][secrets_manager] Failed to generate %s — continuing", var_name)
            continue

        # Set in os.environ
        os.environ[var_name] = value
        generated.append(var_name)
        generated_vars[var_name] = value

        logger.info(
            "[IMP:9][secrets_manager] Auto-generated %s (MUST be added to SOPS for production)",
            var_name,
        )

    # ── Step 3.5: Atomic overwrite — merge existing + generated → write once ──
    # 💼 TRAP[BUSINESS] · 2026-07-25 · HI · Secrets overwrite MUST preserve non-generated entries
    # · Source: Brief 077 DRIFT-S3 — append-mode creates duplicates, overwrite deletes SOPS secrets
    # · Fix: merge existing_vars (from Step 1) with generated_vars → atomic write (tmp + rename)
    # · Risk: if merge logic skips a non-generated secret, it's permanently lost from secrets.env
    if generated_vars:
        try:
            secrets_path = Path(secrets_env)
            secrets_path.parent.mkdir(parents=True, exist_ok=True)

            # Build the complete env file content: existing + newly generated
            # env_vars from Step 1 already contains ALL existing entries
            merged: dict[str, str] = dict(env_vars)  # copy existing (non-generated + previously generated)
            merged.update(generated_vars)             # add/overwrite newly generated

            # Atomic write: write to tmp, then rename
            tmp_path = secrets_path.with_suffix(".env.tmp")
            with open(tmp_path, "w") as f:
                for key, val in merged.items():
                    f.write(f"{key}={val}\n")

            # Preserve file permissions if file exists
            if secrets_path.exists():
                import stat
                existing_mode = secrets_path.stat().st_mode
                tmp_path.chmod(existing_mode)
            else:
                tmp_path.chmod(0o600)

            tmp_path.replace(secrets_path)
            logger.info(
                "[IMP:9][secrets_manager] Atomic write: %d entries → %s (%d new)",
                len(merged),
                secrets_env,
                len(generated_vars),
            )
        except OSError as e:
            logger.warning(
                "[IMP:7][secrets_manager] Cannot write secrets.env: %s — "
                "secrets are in os.environ but NOT persisted to file",
                e,
            )
```

### What the fix does

1. **Collects** all generated values into `generated_vars` dict (no file I/O in loop)
2. **Merges** with `env_vars` (all existing entries from secrets.env, read in Step 1)
3. **Writes once** — all entries in a single `open(..., "w")` call
4. **Atomic** — writes to `.env.tmp`, then `tmp_path.replace(secrets_path)` (atomic on Linux)
5. **Preserves permissions** — `stat` existing file mode, apply to tmp file (`0o600` default)
6. **Preserves non-generated secrets** — `env_vars` from Step 1 includes ALL existing entries, including SOPS-decrypted ones like `WEBNAMES_API_KEY`, `POSTGRES_PASSWORD`, `DOCKER_HUB_TOKEN`, etc.

### Edge case: file doesn't exist yet

On first bootstrap, `secrets_env` may not exist. In this case:
- `source_secrets_env()` returns empty dict (line 56-59: checks `os.path.isfile`)
- `env_vars` is empty
- Only generated secrets are written
- File is created with `0o600` permissions

### Edge case: concurrent access

The atomic write (tmp + rename) handles concurrent access gracefully:
- Other processes reading `secrets_env` see either the old file or the new file
- No process sees a partially-written file (tmp file is invisible until `replace`)

---

## TASK-2: Add `# ⚠️ TRAP[BUG]` annotation

Add a TRAP comment at the fix location (before the generation loop, lines 284-285):

```python
    # ⚠️ TRAP[BUG] · 2026-07-25 · P1 · Append-mode → duplicate secrets on repeated --force runs
    # · Symptom: secrets.env grew with duplicate lines (same VAR=value appended on each run).
    # ·   `source secrets.env` reads the LAST occurrence → first bootstrap's key lost.
    # · Root: `open(secrets_env, "a")` in per-secret loop (line 312, old code). Each generated
    # ·   secret was appended individually. On --force re-run, os.environ was empty → all 7
    # ·   secrets regenerated → appended AGAIN. After 3 runs: 21 lines, 3 values per key.
    # · Fix (DevPlan 072): collect all generated values → merge with existing env_vars →
    # ·   atomic write (tmp + rename). Single `open(..., "w")`, not per-secret append.
    # · Prevention: test_ensure_secrets_idempotent verifies file unchanged after 3 calls.
```

---

## TASK-3: Extend test suite

### File: `tests/unit/test_secrets_manager.py`

Add two new test functions (after line 327, before the end of tests section).

#### Test 1: `test_ensure_secrets_idempotent`

```python
# 🧪 TRAP[TEST] · Regression · ensure_secrets is idempotent on repeated calls
# · Scenario: Call ensure_secrets 3 times with same manifest → file unchanged after first call,
# ·   no duplicate lines, all non-generated secrets preserved
# · Last fail: N/A (new test — validates DevPlan 072 atomic write fix)
# · Remove if: ensure_secrets overwrite logic changes fundamentally
@ldd_trajectory
def test_ensure_secrets_idempotent(caplog, secrets_env, mock_subprocess_run, monkeypatch):
    """ensure_secrets should be idempotent — repeated calls produce identical secrets.env.

    ## @purpose  Verify that calling ensure_secrets multiple times does NOT
    ##           append duplicate lines. After the first call, subsequent calls
    ##           with the same manifest should leave secrets.env unchanged.
    ##           This validates the atomic overwrite fix (DevPlan 072).
    """
    manifest_secrets = [
        {"name": "LITELLM_MASTER_KEY", "gen_command": "echo sk-test", "tier": "generated"},
        {"name": "NEXTAUTH_SECRET", "gen_command": "echo hex-test", "tier": "generated"},
    ]

    # Ensure env vars are NOT set before test
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    secrets_env_path = Path(secrets_env)
    if secrets_env_path.exists():
        secrets_env_path.unlink()

    with patch.object(sm, "_read_manifest", return_value=manifest_secrets):
        with patch.object(sm, "_ensure_htpasswd", return_value=False):
            # First call — should generate and write
            generated1 = sm.ensure_secrets(
                manifest_path="/fake/manifest.yaml",
                secrets_env=secrets_env,
                persist_to_sops=False,
            )

    assert len(generated1) == 2
    first_content = secrets_env_path.read_text()
    first_lines = [l for l in first_content.split("\n") if l.strip() and not l.startswith("#")]
    assert len(first_lines) == 2, f"Expected 2 lines, got {len(first_lines)}: {first_lines}"

    # Verify no duplicate keys
    keys_in_file = [l.split("=", 1)[0] for l in first_lines]
    assert len(keys_in_file) == len(set(keys_in_file)), f"Duplicate keys found: {keys_in_file}"

    # Clean env for second call
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    with patch.object(sm, "_read_manifest", return_value=manifest_secrets):
        with patch.object(sm, "_ensure_htpasswd", return_value=False):
            # Second call — should skip (env vars loaded from file in Step 1)
            generated2 = sm.ensure_secrets(
                manifest_path="/fake/manifest.yaml",
                secrets_env=secrets_env,
                persist_to_sops=False,
            )

    # Second call should generate nothing
    assert len(generated2) == 0, f"Expected 0 generated on second call, got {len(generated2)}"
    second_content = secrets_env_path.read_text()
    assert second_content == first_content, (
        f"File changed on second call!\nFirst:\n{first_content}\nSecond:\n{second_content}"
    )

    # Third call — force-mode simulation (clear os.environ, file still exists)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("NEXTAUTH_SECRET", raising=False)

    with patch.object(sm, "_read_manifest", return_value=manifest_secrets):
        with patch.object(sm, "_ensure_htpasswd", return_value=False):
            generated3 = sm.ensure_secrets(
                manifest_path="/fake/manifest.yaml",
                secrets_env=secrets_env,
                persist_to_sops=False,
            )

    # Third call: env vars empty, but source_secrets_env reloads from file
    # (Step 1: lines 262-265) → those values go into os.environ → skip generation
    assert len(generated3) == 0, (
        f"Expected 0 generated (values reloaded from file), got {len(generated3)}. "
        f"File content: {secrets_env_path.read_text()}"
    )
    third_content = secrets_env_path.read_text()
    assert third_content == first_content, "File changed on third call!"

    # Clean up
    for g in generated1:
        monkeypatch.delenv(g, raising=False)

    logger.critical("[IMP:9][test] ensure_secrets idempotent after 3 calls — OK")
```

#### Test 2: `test_ensure_secrets_preserves_nongenerated`

```python
# 🧪 TRAP[TEST] · Regression · ensure_secrets preserves non-generated secrets on overwrite
# · Scenario: secrets.env has SOPS-decrypted secrets (WEBNAMES_API_KEY) →
#   ensure_secrets generates only tier=generated → non-generated entries unchanged in output
# · Last fail: N/A (new test — validates DevPlan 072 merge logic)
# · Remove if: merge logic changes
@ldd_trajectory
def test_ensure_secrets_preserves_nongenerated(caplog, secrets_env, mock_subprocess_run, monkeypatch):
    """ensure_secrets should preserve non-generated secrets (from SOPS) on overwrite.

    ## @purpose  Verify that when secrets.env contains non-generated secrets
    ##           (e.g., WEBNAMES_API_KEY from SOPS decryption), the atomic
    ##           overwrite preserves them while still generating missing ones.
    ##           This is the key invariant: overwrite mode must NOT delete
    ##           secrets that ensure_secrets doesn't manage.
    """
    # Pre-populate secrets.env with non-generated secrets (simulating SOPS decrypt)
    secrets_env_path = Path(secrets_env)
    secrets_env_path.write_text(
        "WEBNAMES_API_KEY=real-api-key-from-sops\n"
        "POSTGRES_PASSWORD=real-pg-pwd\n"
        "# This is a comment\n"
        "\n"
    )

    manifest_secrets = [
        {"name": "LITELLM_MASTER_KEY", "gen_command": "echo sk-test", "tier": "generated"},
    ]

    # Ensure generated secret is NOT in os.environ
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    with patch.object(sm, "_read_manifest", return_value=manifest_secrets):
        with patch.object(sm, "_ensure_htpasswd", return_value=False):
            generated = sm.ensure_secrets(
                manifest_path="/fake/manifest.yaml",
                secrets_env=secrets_env,
                persist_to_sops=False,
            )

    assert len(generated) == 1
    assert "LITELLM_MASTER_KEY" in generated

    # Verify file content
    content = secrets_env_path.read_text()
    assert "WEBNAMES_API_KEY=real-api-key-from-sops" in content, (
        f"Non-generated secret was DELETED!\nContent:\n{content}"
    )
    assert "POSTGRES_PASSWORD=real-pg-pwd" in content, "POSTGRES_PASSWORD was DELETED!"
    assert "LITELLM_MASTER_KEY=generated_value_abc123" in content, "Generated secret missing!"

    # Verify no duplicate lines
    lines = [l for l in content.split("\n") if l.strip() and not l.startswith("#")]
    keys = [l.split("=", 1)[0] for l in lines]
    assert len(keys) == len(set(keys)), f"Duplicate keys: {keys}"

    # Clean up
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    logger.critical("[IMP:9][test] Non-generated secrets preserved on atomic overwrite — OK")
```

---

## TASK-4: Remove `LITELLM_METRICS_TOKEN` from `.env.example`

### Location: `.env.example:129`

**CURRENT (lines 127-131):**
```
LITELLM_MASTER_KEY=sk-ci-test-master-key
# Метрики (prometheus exporter)
LITELLM_METRICS_TOKEN=
# Опциональная лицензия (оставить пустым для community-версии)
LITELLM_LICENSE=
```

**REMOVE line 128-129 (the `# Метрики` comment + `LITELLM_METRICS_TOKEN=`).**

**NEW (after fix):**
```
LITELLM_MASTER_KEY=sk-ci-test-master-key
# Опциональная лицензия (оставить пустым для community-версии)
LITELLM_LICENSE=
```

### Background

`LITELLM_METRICS_TOKEN` was originally intended for Prometheus→LiteLLM `/metrics` endpoint auth. However, `core/modules/monitoring/docker-compose.base.yml:39` confirms this was already unified to `LITELLM_MASTER_KEY`:

```yaml
# · Fix v3 (2026-07-24): LITELLM_METRICS_TOKEN → LITELLM_MASTER_KEY — единый токен для Prometheus→LiteLLM /metrics auth
```

### Verification

```bash
# After removal, only documentation references should remain
grep -rn "LITELLM_METRICS_TOKEN" --include="*.yml" --include="*.yaml" --include="*.sh" --include="*.py" --include="*.env" . \
  | grep -v ".ai/plans/" | grep -v ".ai/"

# Expected output: only the monitoring compose comment (line 39 — already migrated to LITELLM_MASTER_KEY)
# That comment is documentation and correctly explains the migration.
```

---

## TASK-5: Verification

### Commands

```bash
# 1. Run secrets_manager unit tests
python3 -m pytest tests/unit/test_secrets_manager.py -v -s

# 2. Verify specific test scenarios
python3 -m pytest tests/unit/test_secrets_manager.py::test_ensure_secrets_idempotent -v -s
python3 -m pytest tests/unit/test_secrets_manager.py::test_ensure_secrets_preserves_nongenerated -v -s
python3 -m pytest tests/unit/test_secrets_manager.py::test_ensure_secrets_skips_existing -v -s

# 3. Check LITELLM_METRICS_TOKEN references
grep -rn "LITELLM_METRICS_TOKEN" --include="*.env" --include="*.yml" --include="*.yaml" --include="*.sh" --include="*.py" . \
  | grep -v ".ai/plans/" | grep -v ".ai/"

# 4. Full fast gate
make fix-gate && make gate MODE=fast
```

### Rollback procedure

1. **Revert `secrets_manager.py`** — `git checkout -- core/internal/bootstrap/lifecycle/secrets_manager.py`
2. **Revert `.env.example`** — `git checkout -- .env.example`
3. **Remove new tests** — edit `tests/unit/test_secrets_manager.py` to remove `test_ensure_secrets_idempotent` and `test_ensure_secrets_preserves_nongenerated`
4. **On VPS** — if secrets.env was corrupted by the old append-mode bug, restore from backup or re-generate
5. **Run gate** — `make gate MODE=fast`

---

## Design Decisions

### ## @rationale (merge instead of selective write)
Q: Why merge ALL existing entries instead of only writing generated ones and preserving non-generated?
A: Two approaches:
- **Selective:** read file → filter to non-generated lines → append generated → write. Complex and fragile.
- **Merge:** read ALL existing via `source_secrets_env` (already done in Step 1) → merge with generated → write all. Simple, correct, and consistent with the existing `env_vars` dict already loaded.

The merge approach is simpler: `source_secrets_env` already returns a complete dict. We just need to add generated entries and write them all at once. No need to classify entries as "generated" vs "non-generated" for file writing.

### ## @rationale (tmp file + rename vs direct overwrite)
Q: Why `tmp_path.replace()` instead of `open(path, "w")`?
A: Atomicity. `tmp_path.replace()` is an atomic operation on Linux (same filesystem). If the process crashes during write, the original file is intact. With direct `open(path, "w")`, a crash mid-write leaves a truncated/corrupt file. The `state_machine.py` already uses this pattern (line 303-307: `tmp_path = self.state_file.with_suffix(".json.tmp")` → `tmp_path.replace(self.state_file)`).

### ## @rationale (LITELLM_METRICS_TOKEN removal)
Q: Why remove from `.env.example` but keep the monitoring compose comment?
A: The compose comment (`core/modules/monitoring/docker-compose.base.yml:39`) is documentation that correctly describes the migration from `LITELLM_METRICS_TOKEN` to `LITELLM_MASTER_KEY`. It's valuable for future readers. The `.env.example` line is a dead variable — it has no consumer and its presence misleads developers into thinking it's needed.

---

## File Manifest

| File | Action | Lines affected |
|------|--------|---------------|
| `core/internal/bootstrap/lifecycle/secrets_manager.py` | MODIFY: lines 285-326 (replace append loop with merge + atomic write) | -15 +45 |
| `core/internal/bootstrap/lifecycle/secrets_manager.py` | ADD: TRAP[BUG] annotation before generation loop (line ~284) | +9 |
| `.env.example` | REMOVE: line 129 + preceding comment line 128 | -2 |
| `tests/unit/test_secrets_manager.py` | EXTEND: 2 new test functions after line 327 | +130 |

**Total:** 1 modified Python file, 1 modified env file, 1 extended test file.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_secrets_manager.py` | `test_ensure_secrets_idempotent` | Call ensure_secrets 3× — file unchanged after first, no duplicates, env reload works | `secrets_manager.ensure_secrets` |
| `tests/unit/test_secrets_manager.py` | `test_ensure_secrets_preserves_nongenerated` | secrets.env has SOPS secrets → generate only tier=generated → non-generated preserved in output | `secrets_manager.ensure_secrets` |
| `tests/unit/test_secrets_manager.py` | `test_ensure_secrets_from_manifest` (existing) | Must pass with NO changes | `secrets_manager.ensure_secrets` |
| `tests/unit/test_secrets_manager.py` | `test_ensure_secrets_fallback_hardcoded` (existing) | Must pass with NO changes | `secrets_manager.ensure_secrets` |
| `tests/unit/test_secrets_manager.py` | `test_ensure_secrets_skips_existing` (existing) | Must pass with NO changes | `secrets_manager.ensure_secrets` |
| `tests/unit/test_secrets_manager.py` | `test_source_secrets_env` (existing) | Must pass with NO changes | `secrets_manager.source_secrets_env` |
| `tests/unit/test_secrets_manager.py` | `test_source_secrets_export_prefix` (existing) | Must pass with NO changes | `secrets_manager.source_secrets_env` |

---

## $PARALLEL_GROUPS

### Wave 1 (independent)
- TASK-1: Fix `ensure_secrets()` — atomic overwrite with merge
- TASK-4: Remove `LITELLM_METRICS_TOKEN` from `.env.example`
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1, TASK-4`

### Wave 2 (depends on Wave 1 — fix must be in place)
- TASK-2: Add TRAP[BUG] annotation
- TASK-3: Extend test suite with idempotency + preserve-non-generated tests
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-2, TASK-3`

### Wave 3 (verification)
- TASK-5: Run tests + gate + grep verification
- **Command:** `coder Read DevPlan.md, run verification: python3 -m pytest tests/unit/test_secrets_manager.py -v -s && make fix-gate && make gate MODE=fast`

$END_DEVPLAN
