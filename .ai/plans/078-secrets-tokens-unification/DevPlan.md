$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Restructured DevPlan 078 with Phase A/B split — separates 070-independent tasks (security hotfix, naming, defaults) from 070-dependent tasks (shared/age_key.py, shared/crypto.py, shell wrappers, htpasswd unification).
DESCRIPTION:           Supersedes 01-DevPlan.md. Phase A (10 tasks) is INDEPENDENT of DevPlan 070 — can be implemented immediately. Phase B (5 tasks) requires `core/internal/shared/__init__.py` from DevPlan 070. DRIFT-S4 token leak fix (T7) is the highest-priority task in Phase A — a 1-line security hotfix.
RATIONALE:             VerificationReport F1: DevPlan 070 BLOCKER prevents Waves 1-2 from starting. Splitting Phase A out unblocks security-critical DRIFT-S4 (token leak) and 5 naming/defaults fixes that don't need shared/. Phase B waits until shared/ package exists.
ACCEPTANCE_CRITERIA:
  - Phase A: 10 tasks completable with ZERO dependency on core/internal/shared/
  - Phase B: 5 tasks require precondition `core/internal/shared/__init__.py` exists
  - DRIFT-S4 token leak fixed — docker_registry_auth.py:159 uses Popen stdin pipe
  - OPENAI_API_KEY removed from platform-level enforcement (REQUIRED_SECRET_KEYS + test + .env.example)
  - GHCR_PUSH_TOKEN formalized in secret-definitions.yaml
  - POSTGRES_PASSWORD unified to `test-pg-pwd` across .env and hermes-agent/.env
  - NEXTAUTH_SECRET unified to `ci-test-nextauth-secret-32-chars-min!!` across .env and hermes-agent/.env
  - 2 gate tests: fallback secrets sync (T6) + env defaults consistency (T11)
  - `make gate MODE=fast` — green after each phase
IMPLEMENTS:            Brief 077 Chapter 2 — Secrets & Tokens domain; 7 drift points (S1-S7)
IMPACTS:               22 files — same as 01-DevPlan.md §File Manifest
REQUIRES:              Phase A: nothing. Phase B: DevPlan 070 (core/internal/shared/__init__.py). Merge order: 072 before 078 (avoids .env.example:129 collision).
$END_ARTIFACT_CONTRACT

---

# 02-DevPlan: Secrets & Tokens Unification — Phase A/B Split

**Supersedes:** `01-DevPlan.md` (full implementation details, before/after snippets, test specs — refer to 01 for these)
**Severity:** HIGH — security (DRIFT-S4), correctness (DRIFT-S1/S2/S3), consistency (DRIFT-S5/S6/S7)
**Created:** 2026-07-25
**Author:** Kilo (architect agent)
**Source:** Brief 077 Chapter 2 + VerificationReport 078
**Dependencies:** Phase A = NONE, Phase B = DevPlan 070

---

## Debt Intake

Identical to 01-DevPlan.md §Debt Intake. No new debt found during phase-split analysis.

---

## Phase Split Rationale

**Problem:** VerificationReport 078 F1 (BLOCKER) — `core/internal/shared/__init__.py` does not exist. All 6 tasks that create or import from `core/internal/shared/` are blocked. However, 10 of 12 tasks do NOT touch shared/ — they are blocked only by being colocated in the same DevPlan.

**Solution:** Split into Phase A (independent) and Phase B (dependent on 070). Phase A can be delegated to Coder NOW. Phase B waits until DevPlan 070 creates the shared package.

### Dependency Classification

| Task | Depends on 070? | Why |
|------|:---:|------|
| T1 — age_key.py | **YES** | Creates file IN shared/ directory |
| T2 — shell wrappers | **YES** | Calls age_key.py (needs T1) |
| T3 — crypto.py | **YES** | Creates file IN shared/ directory |
| T4 — secrets_manager.py htpasswd | **YES** | Imports from shared.crypto (needs T3) |
| T5 — secrets.sh htpasswd | **YES** | Delegates to crypto.py (needs T3) |
| T6 — fallback sync test | NO | Reads existing files, imports existing secrets_manager |
| T7 — DRIFT-S4 token leak | NO | 1-line fix in docker_registry_auth.py, no shared/ import |
| T8.1 — OPENAI_API_KEY from REQUIRED | NO | Edit test file constant |
| T8.2 — replace OPENAI test | NO | Edit test file, imports existing helpers |
| T8.3 — OPENAI_API_KEY from .env.example | NO | Edit .env.example |
| T8.4 — GHCR_PUSH_TOKEN to definitions | NO | Edit YAML file |
| T8.5 — S3 consumers note | NO | Edit YAML file, documentation only |
| T9 — POSTGRES_PASSWORD unified | NO | Edit .env files |
| T10 — NEXTAUTH_SECRET unified | NO | Edit .env files |
| T11 — env defaults gate test | NO | Reads YAML/.env files, no shared/ import |
| T12 — gate + verification | **YES** | Depends on Phase B tasks (T1-T5) |

### Merge Order: 072 before 078

DevPlan 072 removes LITELLM_METRICS_TOKEN from `.env.example:129`. Phase A T8.3 modifies `hermes-agent/.env.example` (lines 42-46) which is a different region of the same file. A merge conflict would arise if 078 lands first (072 would shift line numbers). **Recommendation:** merge 072 before starting Phase A T8.3. Other Phase A tasks are unaffected.

---

## Phase A — INDEPENDENT (no shared/ required)

**Can run NOW. No dependency on DevPlan 070.**

### Tasks

| Task | DRIFT | Description | Files | Complexity |
|------|-------|-------------|-------|------------|
| **T7** | **S4** | **Fix docker_registry_auth.py:159 — token via stdin pipe (CRITICAL)** | 1 | 1 |
| T8.1 | S5 | Remove OPENAI_API_KEY from REQUIRED_SECRET_KEYS list | 1 | 1 |
| T8.2 | S5 | Replace test_openai_key_matches_litellm_master_key → test_litellm_master_key_present | 1 | 2 |
| T8.3 | S5 | Remove OPENAI_API_KEY from hermes-agent/.env.example | 1 | 1 |
| T8.4 | S5 | Add GHCR_PUSH_TOKEN to secret-definitions.yaml (tier:optional) | 1 | 1 |
| T8.5 | S5 | Add S3_ACCESS_KEY/S3_SECRET_KEY consumers documentation note | 1 | 1 |
| T9 | S6 | POSTGRES_PASSWORD unified → `test-pg-pwd` (.env + hermes-agent/.env + hermes-agent/.env.example) | 3 | 1 |
| T10 | S7 | NEXTAUTH_SECRET unified → `ci-test-nextauth-secret-32-chars-min!!` (.env + hermes-agent/.env + hermes-agent/.env.example) | 3 | 1 |
| T6 | S3 | Create fallback secrets sync gate test (reads secret-definitions.yaml + imports _FALLBACK_SECRETS) | 1 NEW | 2 |
| T11 | S6/S7 | Create env defaults consistency gate test (reads 3 config layers) | 1 NEW | 2 |

**Total Phase A:** 10 tasks, 14 file operations (12 MODIFY, 2 CREATE). No shared/ dependency.

### Pre-Merge Constraint

Merge 072 before running T8.3 (hermes-agent/.env.example edit). All other Phase A tasks have no merge-order constraint.

### $PARALLEL_GROUPS — Phase A

#### Wave A1 (fully independent, no shared files)
- Tasks: T7, T8.1, T8.3, T8.4, T8.5, T9, T10
- These edit disjoint files — zero merge conflict risk within Wave A1

| Task | Files touched |
|------|--------------|
| T7 | docker_registry_auth.py |
| T8.1 | test_secrets_validation.py (REQUIRED_SECRET_KEYS list) |
| T8.3 | hermes-agent/.env.example |
| T8.4 | secret-definitions.yaml |
| T8.5 | secrets-manifest.yaml |
| T9 | .env, hermes-agent/.env, hermes-agent/.env.example |
| T10 | .env, hermes-agent/.env, hermes-agent/.env.example |

**Note:** T9 and T10 touch the same 3 files — they should be executed sequentially by the same Coder (or combined into one commit) to avoid conflicts.

#### Wave A2 (depends on Wave A1 values)
- Tasks: T8.2 (needs T8.1 done), T6 (independent, but registers gate after test exists), T11 (needs T9 + T10 done)

### Implementation Commands — Phase A

```
# Wave A1
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/078-secrets-tokens-unification/02-DevPlan.md
  AND /Users/tronyx/projects/ai-platform/.ai/plans/078-secrets-tokens-unification/01-DevPlan.md (for exact before/after code)
implement Phase A Wave A1: T7 (docker login stdin pipe), T8.1 (remove OPENAI_API_KEY from REQUIRED list),
T8.3 (remove OPENAI_API_KEY from hermes-agent/.env.example),
T8.4 (add GHCR_PUSH_TOKEN to secret-definitions.yaml), T8.5 (S3 consumers note),
T9 (POSTGRES_PASSWORD unified), T10 (NEXTAUTH_SECRET unified)

# Wave A2
coder Read 02-DevPlan.md AND 01-DevPlan.md
implement Phase A Wave A2: T8.2 (replace OPENAI test with LITELLM_MASTER_KEY check),
T6 (fallback secrets sync gate test), T11 (env defaults consistency gate test)

# Verification after Wave A2
python3 -m pytest tests/gates/test_gate_fallback_secrets_sync.py tests/gates/test_gate_env_defaults_consistency.py -v
python3 -m pytest tests/test_secrets_validation.py -v
make fix-gate && make gate MODE=fast
```

### Acceptance Criteria — Phase A

- [ ] `docker_registry_auth.py` uses `subprocess.run(..., input=token, ...)` — no `bash -c "echo '{token}'..."` pattern
- [ ] `grep OPENAI_API_KEY tests/test_secrets_validation.py | grep REQUIRED_SECRET_KEYS` → no match
- [ ] `grep OPENAI_API_KEY core/modules/hermes-agent/.env.example` → no match (line was `OPENAI_API_KEY=sk-your-...`)
- [ ] `grep GHCR_PUSH_TOKEN core/secret-definitions.yaml` → found with tier:optional
- [ ] `grep "POSTGRES_PASSWORD=test-pg-pwd" .env core/modules/hermes-agent/.env` → both match
- [ ] `grep "NEXTAUTH_SECRET=ci-test-nextauth-secret-32-chars-min!!" .env core/modules/hermes-agent/.env` → both match
- [ ] `test_gate_fallback_secrets_sync.py` PASSES
- [ ] `test_gate_env_defaults_consistency.py` PASSES
- [ ] `test_secrets_validation.py` — all tests PASS (5/5, no skip for OPENAI test — replaced)

---

## Phase B — DEPENDENT (requires shared/ from DevPlan 070)

### Precondition Check (BLOCKS Phase B if false)

```bash
# Coder MUST verify BEFORE implementing any Phase B task:
test -f core/internal/shared/__init__.py && echo "PRECONDITION SATISFIED" || echo "BLOCKED: DevPlan 070 not implemented — core/internal/shared/__init__.py missing"
```

If BLOCKED: STOP Phase B. Report "Phase B blocked — DevPlan 070 prerequisite not satisfied." Do NOT attempt to create shared/ directory as a workaround.

### Tasks

| Task | DRIFT | Description | Depends on | Complexity |
|------|-------|-------------|------------|------------|
| T1 | S1 | Create `core/internal/shared/age_key.py` (detect_age_key) | 070 | 3 |
| T3 | S2 | Create `core/internal/shared/crypto.py` (hash_apr1, generate_htpasswd_entry) | 070 | 3 |
| T2 | S1 | Replace shell AGE key detection with Python wrappers (5 files) | T1 | 4 |
| T4 | S2 | Update secrets_manager.py _ensure_htpasswd() to use shared crypto | T3 | 3 |
| T5 | S2 | Update secrets.sh _ensure_htpasswd_generated() to delegate to Python | T3 | 2 |
| T12 | — | Gate + verification (full suite) | T1-T11 | 2 |

**Total Phase B:** 5 tasks (T12 depends on Phase A T6+T11 also). File details, before/after snippets, and test specs are in 01-DevPlan.md — NOT duplicated here.

### $PARALLEL_GROUPS — Phase B

#### Wave B1 (independent, no inter-task deps)
- Tasks: T1, T3
- These create 2 separate files in shared/ — no conflict

#### Wave B2 (depends on Wave B1)
- Tasks: T2 (depends T1), T4 (depends T3), T5 (depends T3)

#### Wave B3 (gate — depends on ALL prior waves, including Phase A)
- Tasks: T12

### Implementation Commands — Phase B

```
# PRECONDITION CHECK (Coder must run first)
test -f core/internal/shared/__init__.py || { echo "BLOCKED: DevPlan 070 not done"; exit 1; }

# Wave B1
coder Read 02-DevPlan.md AND 01-DevPlan.md
implement Phase B Wave B1: T1 (age_key.py), T3 (crypto.py)

# Wave B2
coder Read 02-DevPlan.md AND 01-DevPlan.md
implement Phase B Wave B2: T2 (shell age-key wrappers in 5 files),
T4 (secrets_manager.py htpasswd with shared crypto),
T5 (secrets.sh htpasswd delegation)

# Wave B3 — final gate
coder Read 02-DevPlan.md AND 01-DevPlan.md
implement Phase B Wave B3: T12 (registration in entrypoint-manifest.yaml for new gates,
run full gate, verify no leftover drift patterns)
```

### Verification Commands — Phase B completion

```bash
# Unit tests for shared modules
python3 -m pytest tests/unit/test_age_key.py tests/unit/test_crypto.py -v -s

# Gate tests
python3 -m pytest tests/gates/test_gate_fallback_secrets_sync.py tests/gates/test_gate_env_defaults_consistency.py -v -s

# Existing secrets validation
python3 -m pytest tests/test_secrets_validation.py -v -s

# Full gate
make fix-gate && make gate MODE=fast

# Drift closure verification
grep -rn "detect_age_key" --include="*.sh" core/ | grep -v "age_key.py"
# Expected: zero matches (all shell duplicates replaced by thin Python-calling wrappers)

grep -n "echo.*token.*docker login" core/internal/bootstrap/docker_registry_auth.py
# Expected: zero matches

grep "POSTGRES_PASSWORD=" .env core/modules/hermes-agent/.env .env.example platform-env.yaml
# Expected: all show "test-pg-pwd"

grep "NEXTAUTH_SECRET=" .env core/modules/hermes-agent/.env .env.example platform-env.yaml
# Expected: all show "ci-test-nextauth-secret-32-chars-min!!"
```

---

## Updated $TEST_SPEC

Identical to 01-DevPlan.md §$TEST_SPEC. New tests:

| Test file | Test function | Scenario | Module | Phase |
|-----------|---------------|----------|--------|-------|
| tests/unit/test_age_key.py | 6 tests (env, SOPS, file, empty, missing, log_tag) | AGE key detection chain | age_key.py | B |
| tests/unit/test_crypto.py | 4 tests (random, fixed-salt, entry, idempotent) | htpasswd generation | crypto.py | B |
| tests/gates/test_gate_fallback_secrets_sync.py | test_fallback_secrets_match_definitions | _FALLBACK_SECRETS ≡ definitions | secrets_manager.py | A |
| tests/gates/test_gate_env_defaults_consistency.py | test_env_defaults_consistency | Defaults across 3 layers | secret-definitions.yaml | A |

---

## Design Decisions

Identical to 01-DevPlan.md §Design Decisions. One addition:

### DD6: Phase A/B split — @rationale

**Q:** Why split instead of waiting for DevPlan 070?
**A:** DRIFT-S4 (token leak) is a CRITICAL security issue — token visible in `/proc/PID/cmdline`. It's a 1-line fix. There is zero reason to block it on a package-directory prerequisite that it doesn't use. Similarly, 5 naming conflicts (S5) and 2 default unifications (S6/S7) are pure config/test edits with no shared/ dependency. Blocking 10 tasks on a single missing `__init__.py` creates unnecessary security exposure.

**Q:** Why not include `__init__.py` creation in Phase A (self-contained approach from VerificationReport)?
**A:** DevPlan 070 is a separate concern (shared/ package initialization with `node_yaml.py` and `project_registry.py`). Creating a partial shared/ in 078 would create a dual-mechanism problem — 070 would need to merge with 078's `__init__.py`, or 078 would create a package skeleton that 070 doesn't expect. Clean separation: 070 owns the package, 078 adds modules to it.

---

## Complete Execution Sequence

```
1. 072 MERGE (removes LITELLM_METRICS_TOKEN from .env.example:129)
        ↓
2. PHASE A Wave A1 (T7, T8.1, T8.3, T8.4, T8.5, T9, T10)
        ↓
3. PHASE A Wave A2 (T8.2, T6, T11)
        ↓
4. [WAIT for DevPlan 070 — core/internal/shared/__init__.py]
        ↓
5. PRECONDITION CHECK: test -f core/internal/shared/__init__.py
        ↓
6. PHASE B Wave B1 (T1, T3)
        ↓
7. PHASE B Wave B2 (T2, T4, T5)
        ↓
8. PHASE B Wave B3 (T12 — gate + verification)
        ↓
9. DONE — all 7 DRIFT points closed
```

---

## Next Steps

### Before anything else — merge 072
```bash
# 072 removes LITELLM_METRICS_TOKEN from .env.example:129
# Prevents collision with 078 Phase A T8.3
```

### Phase A — Wave A1 (implement NOW)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/078-secrets-tokens-unification/02-DevPlan.md \
     AND /Users/tronyx/projects/ai-platform/.ai/plans/078-secrets-tokens-unification/01-DevPlan.md
implement Phase A Wave A1: T7 (docker_registry_auth pipe fix — CRITICAL security hotfix),
T8.1 (remove OPENAI_API_KEY from REQUIRED_SECRET_KEYS),
T8.3 (remove OPENAI_API_KEY from hermes-agent/.env.example),
T8.4 (add GHCR_PUSH_TOKEN to secret-definitions.yaml),
T8.5 (S3 consumers documentation note),
T9 (POSTGRES_PASSWORD → test-pg-pwd), T10 (NEXTAUTH_SECRET → ci-test-nextauth-secret-32-chars-min!!)
```

### Phase A — Wave A2
```
coder Read 02-DevPlan.md AND 01-DevPlan.md
implement Phase A Wave A2: T8.2 (replace OPENAI test with LITELLM_MASTER_KEY check),
T6 (fallback secrets sync gate test), T11 (env defaults consistency gate test)
```

### Phase B — Wave B1 (after DevPlan 070)
```
# Verify precondition FIRST
test -f core/internal/shared/__init__.py || { echo "BLOCKED"; exit 1; }

coder Read 02-DevPlan.md AND 01-DevPlan.md
implement Phase B Wave B1: T1 (age_key.py), T3 (crypto.py)
```

### Phase B — Wave B2
```
coder Read 02-DevPlan.md AND 01-DevPlan.md
implement Phase B Wave B2: T2 (shell age-key wrappers), T4 (secrets_manager.py htpasswd), T5 (secrets.sh htpasswd)
```

### Phase B — Wave B3
```
coder Read 02-DevPlan.md AND 01-DevPlan.md
implement Phase B Wave B3: T12 (gate registration + full verification)
```

$END_DEVPLAN
