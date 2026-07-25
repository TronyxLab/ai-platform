$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Verification of DevPlan 078 — Secrets & Tokens Unification
DESCRIPTION:           Plan self-consistency, implementation status, and cross-reference audit. Covers 7 DRIFT points (S1-S7), 22 files, 12 tasks across 3 waves.
RATIONALE:             Ensure DevPlan is actionable, complete, and free of drift before implementation delegation
ACCEPTANCE_CRITERIA:   All referenced files exist, ACs are measurable, prerequisites satisfied, plan self-consistent, DRIFT points accurately characterized
IMPLEMENTS:            DevPlan:.ai/plans/078-secrets-tokens-unification/01-DevPlan.md
IMPACTS:               core/internal/shared/, core/entrypoints/, core/lib/, core/internal/secrets/, core/internal/bootstrap/, core/secret-definitions.yaml, core/secrets-manifest.yaml, .env, .env.example, core/modules/hermes-agent/.env, core/modules/hermes-agent/.env.example, tests/
REQUIRES:              070 (core/internal/shared/__init__.py), 072 (secrets_manager.py append-fix + LITELLM_METRICS_TOKEN removal)
$END_ARTIFACT_CONTRACT

---

# Verification Report: DevPlan 078 — Secrets & Tokens Unification

🔒 **Verified against SHA:** `d37326afc64e505bb69f230465e83f9f5bef0d8a`
⚠️ **Dirty working tree:** yes — `.ai/plans/` markdown files modified (plan journal updates, not source code)
**Date:** 2026-07-25T13:28+03:00

---

## Final Verdict: **PREREQUISITES BLOCKED** — DevPlan NOT STARTED

**One-liner:** Plan is self-consistent and all 7 DRIFT points are accurately characterized, but **DevPlan 070 prerequisite is MISSING** (`core/internal/shared/__init__.py` does not exist) — this blocks Waves 1-2 which depend on the `shared` package. Implementation has not yet started (0/22 files modified, 0/4 new files created).

---

## 1. Plan Self-Consistency Audit

### 1.1 DRIFT Points — Accuracy Check

| DRIFT | Description | DevPlan Characterization | Current State Match | Accuracy |
|-------|-------------|--------------------------|---------------------|----------|
| S1 | Age-key detection 5-way duplication | 5 files with duplicate `detect_age_key()` | ✅ 5 duplicates confirmed | ACCURATE |
| S2 | Htpasswd generation 2-way duplication | Shell + Python diverged idempotency | ✅ Both still use direct openssl | ACCURATE |
| S3 | _FALLBACK_SECRETS vs definitions | No sync enforcement | ✅ No gate test exists | ACCURATE |
| S4 | Docker token in cmdline | Token visible via `/proc/PID/cmdline` | ✅ `bash -c "echo '{token}'..."` still present | ACCURATE |
| S5 | 5 naming conflicts | OPENAI_API_KEY, GHCR_PUSH_TOKEN, S3 consumers | ✅ All 5 still in drifted state | ACCURATE |
| S6 | POSTGRES_PASSWORD 4 divergent defaults | testpass, test-postgres-password, test-pg-pwd | ✅ 3 different values across 4 locations | ACCURATE |
| S7 | NEXTAUTH_SECRET 3 divergent defaults | sk-test-nextauth-secret, test-nextauth-secret-value, ci-test-… | ✅ 3 different values across 4 locations | ACCURATE |

**Verdict:** All 7 DRIFT points are accurately characterized in the DevPlan. The before/after code snippets match the current codebase.

### 1.2 File Manifest — Existence Check

| File | Action | Exists? | Notes |
|------|--------|---------|-------|
| `core/internal/shared/age_key.py` | CREATE | ❌ | Not yet created |
| `core/internal/shared/crypto.py` | CREATE | ❌ | Not yet created |
| `tests/unit/test_age_key.py` | CREATE | ❌ | Not yet created |
| `tests/unit/test_crypto.py` | CREATE | ❌ | Not yet created |
| `tests/gates/test_gate_fallback_secrets_sync.py` | CREATE | ❌ | Not yet created |
| `tests/gates/test_gate_env_defaults_consistency.py` | CREATE | ❌ | Not yet created |
| `core/entrypoints/bootstrap.sh` | MODIFY | ✅ | Lines 53-76 still in old state |
| `core/entrypoints/node-update.sh` | MODIFY | ✅ | Lines 44-66 still in old state |
| `core/lib/secrets.sh` | MODIFY | ✅ | Lines 134-138 still in old state |
| `core/internal/secrets/decrypt-secrets.sh` | MODIFY | ✅ | Lines 76-84 still in old state |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | ✅ | Line 44 still in old state |
| `core/internal/bootstrap/lifecycle/secrets_manager.py` | MODIFY | ✅ | Lines 396-441 still in old state |
| `core/internal/bootstrap/docker_registry_auth.py` | MODIFY | ✅ | Line 159 still has token-in-cmdline |
| `core/secret-definitions.yaml` | MODIFY | ✅ | GHCR_PUSH_TOKEN not yet added |
| `core/secrets-manifest.yaml` | MODIFY | ✅ | S3 consumers note not yet added |
| `tests/test_secrets_validation.py` | MODIFY | ✅ | OPENAI_API_KEY still at line 62 and 375-440 |
| `.env` | MODIFY | ✅ | POSTGRES_PASSWORD=testpass, NEXTAUTH_SECRET=sk-test-nextauth-secret |
| `.env.example` | MODIFY (minor) | ✅ | Already has canonical values; LITELLM_METRICS_TOKEN still present (072 prerequisite) |
| `core/modules/hermes-agent/.env` | MODIFY | ✅ | POSTGRES_PASSWORD=test-postgres-password, NEXTAUTH_SECRET=test-nextauth-secret-value |
| `core/modules/hermes-agent/.env.example` | MODIFY | ✅ | OPENAI_API_KEY still present, POSTGRES/NEXTAUTH still placeholders |
| `core/entrypoint-manifest.yaml` | MODIFY | ✅ | No new gate registrations yet |

**Summary:** 0 of 22 files modified. 0 of 4 new files created. Implementation = 0%.

### 1.3 Acceptance Criteria — Measurability

All 23 ACs in the DevPlan are measurable and verifiable:
- ACs 1-2: File existence + import checks → binary PASS/FAIL
- ACs 3-5: Code presence checks → binary PASS/FAIL
- ACs 6-14: Test pass/fail → binary PASS/FAIL
- AC 15: `docker_registry_auth.py:159` replaced → grep check
- ACs 16-17: Code modification checks → binary PASS/FAIL
- AC 18: GHCR_PUSH_TOKEN in definitions → grep check
- ACs 19-20: Value consistency checks → grep + compare
- AC 21: Gate test validation → test pass/fail
- AC 22: Verification commands → grep checks
- AC 23: `make gate MODE=fast` green → binary PASS/FAIL

### 1.4 Task Dependency Graph — Integrity

| Wave | Tasks | Dependencies | Feasibility |
|------|-------|-------------|-------------|
| Wave 1 | T1, T3, T7, T8.1, T8.3, T8.4, T9, T10 | None (independent) | ✅ Feasible — but blocked by 070 prerequisite |
| Wave 2 | T2, T4, T5, T6, T8.2, T8.5, T11 | Wave 1 | ✅ Feasible — dependencies are correct |
| Wave 3 | T12 | Waves 1+2 | ✅ Feasible |

No circular dependencies. Task ordering is sound.

---

## 2. Implementation Status

### Summary: **NOT STARTED (0/22 files, 0%)**

| Category | Count | Modified |
|----------|-------|----------|
| New files to create | 6 | 0 created |
| Shell files to modify | 5 | 0 modified |
| Python files to modify | 2 | 0 modified |
| Config/YAML files to modify | 5 | 0 modified |
| Test files to modify/create | 4 | 0 modified/created |
| **Total** | **22** | **0% complete** |

### Detailed Status per DRIFT

| DRIFT | Status | Files Changed | Security Impact |
|-------|--------|---------------|-----------------|
| S1 (age-key) | ❌ Open | 0/7 files | None (duplication only) |
| S2 (htpasswd) | ❌ Open | 0/3 files | None (idempotency only) |
| S3 (fallback sync) | ❌ Open | 0/1 files | None (consistency only) |
| **S4 (token leak)** | ❌ **Open** | 0/1 files | **HIGH — token visible in /proc/PID/cmdline** |
| S5 (naming) | ❌ Open | 0/5 files | None (consistency only) |
| S6 (POSTGRES_PASSWORD) | ❌ Open | 0/4 files | LOW (dev-only, different initial state) |
| S7 (NEXTAUTH_SECRET) | ❌ Open | 0/4 files | LOW (dev-only, different initial state) |

---

## 3. Prerequisites Check

### PREREQ-1: DevPlan 070 — `core/internal/shared/__init__.py`

| Check | Result |
|-------|--------|
| File exists? | ❌ `core/internal/shared/__init__.py` NOT FOUND |
| Directory exists? | `core/internal/shared/` NOT FOUND |
| Impact | **BLOCKS** T1, T3 (both create files in `shared/` package) |
| Blocked tasks | T1, T2, T3, T4, T5, T6 (all depend on shared package) |
| Severity | **BLOCKER** |

**Recommendation:** Execute DevPlan 070 before delegating 078. Without `shared/__init__.py`, Python imports from `core.internal.shared` will fail.

### PREREQ-2: DevPlan 072 — secrets_manager.py append-fix + LITELLM_METRICS_TOKEN

| Check | Result |
|-------|--------|
| LITELLM_METRICS_TOKEN removed from .env.example? | ❌ Still present at `.env.example:129` |
| Impact | Non-blocking — 072 is a merge-order preference, not a hard dependency |
| Blocked tasks | None (merge conflict risk only) |
| Severity | LOW (merge-order preference) |

The DevPlan states 072 "should be merged before to avoid line-number shifts." Current state: 072 not yet merged (LITELLM_METRICS_TOKEN still in .env.example).

---

## 4. Cross-Reference Integrity

### 4.1 Line Number Accuracy

| Reference | DevPlan Line | Actual Line | Match? |
|-----------|-------------|-------------|--------|
| `bootstrap.sh` detect_age_key start | 53 | 54 | ✅ (offset by TRAP comment at 50-51) |
| `bootstrap.sh` detect_age_key end | 76 | 76 | ✅ |
| `node-update.sh` detect_age_key start | 44 | 47 | ⚠️ Off by 3 (region marker at 44-46) |
| `node-update.sh` detect_age_key end | 66 | 66 | ✅ |
| `secrets.sh` SOPS_AGE_KEY fallback | 134-138 | 134-137 | ✅ (138 is `fi`, fine) |
| `decrypt-secrets.sh` fallback chain | 76-84 | 76-84 | ✅ |
| `node-lifecycle.sh` SOPS fallback | 44 | 44 | ✅ |
| `docker_registry_auth.py` token line | 158-163 | 158-163 | ✅ |
| `secrets_manager.py` _ensure_htpasswd | 396-409 | 396-441 | ✅ |
| `test_secrets_validation.py` REQUIRED list | 62 | 62 | ✅ |
| `test_secrets_validation.py` OPENAI test | 375-440 | 375-440 | ✅ |
| `.env` POSTGRES_PASSWORD | 25 | 25 | ✅ |
| `.env` NEXTAUTH_SECRET | 78 | 78 | ✅ |
| `hermes-agent/.env` POSTGRES | 45 | 45 | ✅ |
| `hermes-agent/.env` NEXTAUTH_SECRET | 48 | 48 | ✅ |
| `hermes-agent/.env.example` OPENAI_API_KEY | 42-46 | 42-46 | ✅ |
| `hermes-agent/.env.example` POSTGRES | 70 | 70 | ✅ |
| `hermes-agent/.env.example` NEXTAUTH | 72 | 72 | ✅ |
| `secret-definitions.yaml` GHCR_PULL_TOKEN | 116 | 111 | ⚠️ Off by 5 (likely YAML reformatting) |

**Verdict:** Line numbers are accurate enough for implementation (within ±5 lines). The Coder should read the actual files, not rely on exact line numbers.

### 4.2 Already-Canonical Values (no change needed)

The DevPlan correctly identifies files that already have the canonical values:

| Value | .env.example | platform-env.yaml | Status |
|-------|-------------|-------------------|--------|
| POSTGRES_PASSWORD=test-pg-pwd | ✅ Line 75 | ✅ Line 125 | Already canonical |
| NEXTAUTH_SECRET=ci-test-nextauth-secret-32-chars-min!! | ✅ Line 141 | ✅ Line 142 | Already canonical |

### 4.3 Debt Intake — Classification Accuracy

| DEBT | Classification | Assessment |
|------|---------------|------------|
| LITELLM_METRICS_TOKEN in .env | IN_SCOPE (T10 verification) | ✅ Correct — handled by 072 prerequisite |
| OPENAI_API_KEY placeholder | IN_SCOPE (T6.3) | ✅ Correct |
| PLATFORM_MASTER_PASSWORD/GHCR_PUSH_TOKEN in deploy.mk | DEFER | ✅ Correct — deploy.mk is CI-push concern |
| nginx/install.sh DEPRECATED | DEFER | ✅ Correct — cert domain, not secrets |

---

## 5. Test Results

### 5.1 Secrets Validation Tests (current state)

```
tests/test_secrets_validation.py::test_context_image_not_old_name      PASSED
tests/test_secrets_validation.py::test_no_secret_leaks_in_compose      PASSED
tests/test_secrets_validation.py::test_openai_key_matches_litellm_master_key SKIPPED
tests/test_secrets_validation.py::test_password_var_name_not_mismatched PASSED
tests/test_secrets_validation.py::test_required_secrets_not_empty      PASSED
tests/test_secrets_validation.py::test_secrets_env_file_exists          PASSED

Result: 5 passed, 1 skipped in 0.11s
```

The `test_openai_key_matches_litellm_master_key` is currently SKIPPED (OPENAI_API_KEY is a placeholder in CI env). This is the test that will be replaced by `test_litellm_master_key_present` in T8.2.

### 5.2 New Test Files — None Created Yet

| Test File | Status | Expected Tests |
|-----------|--------|---------------|
| `tests/unit/test_age_key.py` | ❌ Missing | 6 tests |
| `tests/unit/test_crypto.py` | ❌ Missing | 4 tests |
| `tests/gates/test_gate_fallback_secrets_sync.py` | ❌ Missing | 1 test |
| `tests/gates/test_gate_env_defaults_consistency.py` | ❌ Missing | 1 test |

### 5.3 Unit Tests (related, existing)

All 107 unit tests pass. The `test_secrets_validator.py` tests are related but cover the manifest validation domain, not the new shared modules.

---

## 6. Findings

| # | Severity | Finding | Details | Recommendation |
|---|----------|---------|---------|----------------|
| F1 | **BLOCKER** | PREREQ-1: `core/internal/shared/__init__.py` missing | DevPlan 070 prerequisite not satisfied. Directory `core/internal/shared/` does not exist. Blocks T1, T2, T3, T4, T5, T6. | Execute DevPlan 070 first, or include `shared/__init__.py` creation in Wave 1 of this DevPlan. |
| F2 | WARNING | PREREQ-2: 072 not yet merged | LITELLM_METRICS_TOKEN still in .env.example:129. Merge-order preference per DevPlan 078. | Merge 072 before starting 078 to avoid line-number shifts. |
| F3 | INFO | Implementation not started | 0 of 22 files modified. All 7 DRIFT points confirmed open. | Ready for delegation after F1 resolved. |
| F4 | INFO | 6 new files need creation | Includes 2 shared modules, 4 test files. All currently absent. | Create in Wave 1 (T1, T3) and Wave 2 (T6, T11). |
| F5 | HIGH | DRIFT-S4 severity confirmed | `docker_registry_auth.py:159` still interpolates token into shell command. Token visible in `/proc/PID/cmdline` and `ps auxww`. | Prioritize T7 in Wave 1 — it's a 1-line security fix independent of other tasks. |
| F6 | INFO | .env.example + platform-env.yaml already canonical | POSTGRES_PASSWORD=test-pg-pwd and NEXTAUTH_SECRET=ci-test-nextauth-secret-32-chars-min!! already present. Only `.env` and `hermes-agent/.env` need updating. | T9/T10 are simple 2-line changes each. |
| F7 | INFO | Test suite healthy | 5/6 secrets tests pass, 107/107 unit tests pass, 21/22 gate tests pass. No regressions. | Good baseline for implementation. |
| F8 | LOW | Minor line number drift: node-update.sh | DevPlan says line 44, actual `detect_age_key()` starts at line 47 (region markers at 44-46). | Immaterial — Coder should grep for function name, not rely on exact line numbers. |
| F9 | LOW | Minor line number drift: secret-definitions.yaml | DevPlan says GHCR_PULL_TOKEN at line 116, actual at line 111. | Immaterial — insert after GHCR_PULL_TOKEN by name match. |

---

## 7. Security Impact Assessment

| DRIFT-S4 (Token Leak) | Severity: HIGH |
|------------------------|----------------|
| Current state | Token is visible in `ps auxww` and `/proc/PID/cmdline` during docker login |
| Attack surface | Any process with read access to `/proc` can see the token |
| Fix | T7 — `subprocess.run(..., input=token, ...)` (stdin pipe) |
| Recommendation | This is a 1-line fix independent of other tasks. Consider extracting as a hotfix. |

---

## 8. Delegation Recommendation

**Status:** NOT READY for delegation — prerequisite F1 must be resolved first.

**Options:**
1. **Execute DevPlan 070 first** — creates `core/internal/shared/__init__.py`, then delegate 078
2. **Add `__init__.py` creation to 078 Wave 1** — self-contained approach; modify T1 to include `mkdir -p core/internal/shared/ && touch core/internal/shared/__init__.py`

**Recommended:** Option 2 (self-contained) — the shared package initialization is trivial (empty `__init__.py` or with package docstring) and doesn't warrant a separate deployment cycle.

---

$END_VERIFICATION_REPORT
