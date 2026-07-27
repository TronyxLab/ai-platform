$START_VERIFICATION_REPORT

# VerificationReport 02 — DevPlan 036E Quality Audit

🔒 **Verified against SHA:** `d6ba7d6c4d1f4ac5b7cbd9ec5bf492a4351c1b89`

**Date:** 2026-07-26T18:15:31+03:00
**Scope:** LARGE (architectural/schema/contract changes — VPS forced-command migration, 5 files modified/created, 1183→200 LOC shell facade)
**Phase coverage:** Phase 1 (static), Phase 2 (drift detection), Phase 3 (invariant verification), Phase 4 (test quality), Phase 5 (N/A — plan review), Phase 6 (N/A — plan review)

---

## Section 1 — Static Audit (Phase 1)

### DevPlan Protocol Compliance

| Check | Status | Evidence |
|-------|--------|----------|
| `$START_DEVPLAN` marker | ✅ PASS | L1 |
| `$END_DEVPLAN` marker | ✅ PASS | L1160 |
| `$ARTIFACT_CONTRACT` PURPOSE | ✅ PASS | L6 — декомпозиция deploy-project.sh по Strangler-Fig |
| `$ARTIFACT_CONTRACT` DESCRIPTION | ✅ PASS | L7 — полное описание 3 артефактов |
| `$ARTIFACT_CONTRACT` RATIONALE | ✅ PASS | L8 — языковая политика, устранение inline python3, DRY |
| `$ARTIFACT_CONTRACT` ACCEPTANCE_CRITERIA | ✅ PASS | L9-18 — 9 критериев (AC-1..AC-9) |
| `$ARTIFACT_CONTRACT` IMPLEMENTS | ✅ PASS | L19 — Wave 5e Strangler-Fig |
| `$ARTIFACT_CONTRACT` IMPACTS | ✅ PASS | L20-25 — 5 файлов, точные LOC |
| `$ARTIFACT_CONTRACT` REQUIRES | ✅ PASS | L26-33 — Python ≥3.10, 3 shared libs, 2 DevPlans |
| Version format compliance | ✅ PASS | Standard DevPlan format |
| Section completeness | ✅ PASS | All required DevPlan sections present |

### Content Audit

| Check | Status | Evidence |
|-------|--------|----------|
| Debt Intake section | ✅ PASS | L38-71 — TRAP-аудит + cross-wave debt |
| Superposition Analysis | ✅ PASS | L98-241 — 7 options (A-G), scoring matrix 9×7 |
| Step-by-Step Data Flow | ✅ PASS | L244-464 — before/after diagrams, shell facade verb dispatch |
| Design Decisions | ✅ PASS | L568-675 — D1-D8 с @rationale |
| $TASKS section | ✅ PASS | L678-753 — 6 tasks, complexity, checkpoints |
| $TEST_SPEC section | ✅ PASS | L814-854 — 35 tests specified |
| Risk Assessment | ✅ PASS | L858-870 — 8 risks, R1 CRITICAL production outage |
| Rollback Strategy | ✅ PASS | L945-986 — emergency <30 min, decision tree |
| Integration Test Plan | ✅ PASS | L873-941 — 5 staging scenarios |
| File Manifest | ✅ PASS | L1063-1090 — 4 new, 2 modified, 6 unchanged |
| Task Dependency Graph | ✅ PASS | L780-794 — clear wave structure |
| $PARALLEL_GROUPS | ✅ PASS | L757-777 — 4 waves with rationale |

### Summary: Phase 1 — 14/14 checks PASS. Structural compliance: STABLE.

---

## Section 2 — Drift Analysis (Phase 2)

### DRIFT-TRAP-01 [CRITICAL] — TRAP Annotation Count Mismatch: 16 claimed vs 11 actual autonomous

**Files involved:**
- `core/internal/deploy/deploy-project.sh` (source, 11 autonomous TRAP annotations)
- `.ai/plans/036-wave5e-deploy/01-DevPlan.md` (claims 16 TRAP annotations in debt intake table L44-61)
- DevPlan post-migration TRAP inventory L992-1037 (shows 11 docstrings)
- DevPlan final merge checklist L1155-1156 (CI gate: ≥12 in deploy_engine.py + ≥4 in payload_deliverer.py = 16)

**Expected:** 16 autonomous TRAP annotations in deploy-project.sh, all ported → ≥16 total in Python modules.

**Actual:**
- 11 autonomous TRAP annotations: L28, L31, L42, L81, L168, L413, L433, L460, L465, L510, L903
- 3 changelog references (L35-L37 @changes lines — duplicate, not autonomous)
- 1 inline comment (L1145 — references existing TRAP[BUG] B1, not autonomous)
- 4 implied design decisions (T13-T16, no line numbers `—`, never had formal TRAP annotations in the source file)

**Analysis of T13-T16:**
| TRAP ID | Description | Status in shell script | Migration target per DevPlan |
|---------|-------------|----------------------|------------------------------|
| T13 | PLATFORM_DEPLOY_DIRECT detection via env prefix | Not a TRAP annotation. Related to T6 (L413) — same bug fix already has a TRAP[BUG]. | `deploy-project.sh` (shell facade) |
| T14 | FQDN uniqueness check via validate.sh | Not a TRAP annotation. Design pattern, never formalized. | `deploy_engine.py` §_preflight_checks() |
| T15 | Port conflict detection via ss -tlnp | Not a TRAP annotation. Design pattern, never formalized. | `deploy_engine.py` §_preflight_checks() |
| T16 | STUB_AWARE_STATUS flag | Not a TRAP annotation. Feature flag, never formalized. | `deploy_engine.py` §status() |

**Severity: CRITICAL** — The CI gate is self-contradictory:
- Post-migration TRAP inventory (L992-1037) documents **8 TRAPs in deploy_engine.py + 3 TRAPs in payload_deliverer.py = 11 total**
- Final merge checklist (L1155-1156) requires **≥12 in deploy_engine.py + ≥4 in payload_deliverer.py = 16 total**
- The CI gate will FAIL because the documented post-migration TRAP inventory doesn't reach the required count
- The 4 "new" TRAPs (DECISION wave5e, DECISION two modules, DEBT docker ops in deploy_engine) bring the count to 11, still short of 16

**Fix:** Either (A) backfill T13-T16 as formal TRAP[DECISION] annotations in the shell script BEFORE migration, then count them toward 16; or (B) acknowledge the actual count is 11 autonomous + 4 new post-migration = 15 total, and adjust CI gate to `≥11` in deploy_engine.py + `≥4` in payload_deliverer.py; or (C) add 5 more post-migration TRAPs to reach 16 (e.g., TRAP[DECISION] for each of the 4 shell lib sourcing lines, TRAP[BUG] for preflight edge cases).

---

### DRIFT-TEST-01 [HIGH] — `_validate_project_name()` location inconsistency

**Files involved:**
- `.ai/plans/036-wave5e-deploy/01-DevPlan.md` D7 (L649-659): `validate_project_name()` extracted to `project_registry.py`
- `.ai/plans/036-wave5e-deploy/01-DevPlan.md` $TEST_SPEC (L818-821): 4 tests listed under `test_deploy_engine.py` with function-under-test `deploy_engine._validate_project_name()`
- `core/internal/shared/project_registry.py` (actual file): contains `register_project`, `deregister_project`, `list_projects` — NO `validate_project_name()`
- `core/internal/bootstrap/converge/reconciler.py:701`: contains `_validate_project_name()` (private, regex `^[a-zA-Z0-9_-]+$`)

**Expected:** Per D7, `validate_project_name()` is a shared function in `project_registry.py`, imported by deploy_engine, payload_deliverer, and reconciler. Tests should target `project_registry.validate_project_name()`.

**Actual:** $TEST_SPEC lists tests under `test_deploy_engine.py` testing `deploy_engine._validate_project_name()`. This implies the function is still a DeployEngine method — inconsistent with D7 DRY extraction.

**Severity: HIGH** — If Coder implements per $TEST_SPEC (deploy_engine._validate_project_name), the function stays local (DRY violation). If Coder implements per D7 (project_registry.validate_project_name), the $TEST_SPEC function-under-test column is wrong and tests must be in a different file or test the shared function through import. Either way, the DevPlan contains contradictory instructions.

**Fix:** Unify: move 4 validation tests to `test_project_registry.py` (or create it if absent), update function-under-test to `project_registry.validate_project_name()`, deploy_engine tests import and use the shared function. Add an integration test in `test_deploy_engine.py` that verifies DeployEngine calls the shared function (mocked).

---

### DRIFT-DEP-01 [HIGH] — REQUIRES lists DevPlan 036D with unsubstantiated dependency relationship

**Files involved:**
- `.ai/plans/036-wave5e-deploy/01-DevPlan.md` REQUIRES L32: `DevPlan 036D (overlay_deliverer.py — для vhost overlay delivery, используется entrypoint deploy-project.sh)`
- `core/internal/deploy/deploy-project.sh` (VPS forced-command): does NOT use overlay_deliverer
- `core/entrypoints/deploy-project.sh` (dev-machine): not in scope of this DevPlan per File Manifest L1085
- `.ai/plans/036-wave5d-remote/01-DevPlan.md`: overlay_deliverer.py is used by `remote-cmd.sh` → `node-update.sh`, NOT by deploy-project.sh

**Evidence:**
```bash
# deploy-project.sh (VPS forced-command) has no reference to overlay_deliverer
grep "overlay_deliver" core/internal/deploy/deploy-project.sh
# → No results
```

**Expected:** REQUIRES lists only dependencies that deploy-project.sh or the new Python modules actually need at runtime.

**Actual:** overlay_deliverer.py (vhost nginx overlay delivery) is used by remote-cmd.sh/node-update.sh — completely different execution path. The DevPlan's TASK-036E1 lists it as dependency with rationale "используется entrypoint", but:
- `core/entrypoints/deploy-project.sh` (the dev-machine entrypoint) is explicitly listed as "unchanged" in File Manifest (L1085)
- The VPS-side deploy-project.sh (the file being migrated) has no interaction with overlay_deliverer

**Severity: HIGH** — Listing a phantom dependency creates confusion for Coder (should they wait for 036D completion? coordinate with remote-cmd.sh migration?) and inflates the dependency graph. The overlay_deliverer and deploy-project.sh operate in completely independent execution paths (vhost overlays during node-update vs project deployment during CI-triggered forced-command).

**Fix:** Remove DevPlan 036D from REQUIRES, or clarify it's a cross-wave awareness dependency (not runtime), and update TASK-036E1 dependency note accordingly. The actual runtime dependencies are only: `ssh_command_parser.py`, `platform_deliver.py`, `deploy_paths.py`, `project_registry.py` (all already in REQUIRES).

---

### DRIFT-DOC-01 [MEDIUM] — DevPlan 081 referenced but no planning artifact exists

**Files involved:**
- `.ai/plans/036-wave5e-deploy/01-DevPlan.md` L28-30: references `DevPlan 081` as origin of `ssh_command_parser.py`, `platform_deliver.py`, `deploy_paths.py`
- Filesystem: `core/internal/shared/ssh_command_parser.py` ✅, `core/internal/shared/platform_deliver.py` ✅, `core/internal/shared/deploy_paths.py` ✅
- `.ai/plans/081-*`: DOES NOT EXIST

**Expected:** A referenced DevPlan should have a planning artifact.

**Actual:** The three shared modules exist and are functional (verified by reading their MODULE_CONTRACTs). The DevPlan 081 reference is the origin claim, but no `.ai/plans/081-*/` folder exists. Since the modules already exist and are described as "уже существует" (already exists), this is a documentation gap, not a runtime dependency issue.

**Severity: MEDIUM** — The DevPlan REQUIRES correctly says "уже существует" for these modules. The missing DevPlan 081 artifact is a provenance gap but doesn't affect implementation correctness. Coder can still import the modules.

**Fix:** Add a note in REQUIRES: "DevPlan 081 planning artifact not found — modules verified existing and functional via filesystem audit."

---

### DRIFT-TRAP-02 [MEDIUM] — Debt intake table doesn't distinguish autonomous vs inferred TRAPs

**Files involved:**
- `.ai/plans/036-wave5e-deploy/01-DevPlan.md` L44-61 (debt intake table)

**Expected:** Debt intake table clearly distinguishes TRAP annotations (grep-able, complete with Symptom/Root/Fix/Prevention) from design patterns that lack formal annotations.

**Actual:** Table mixes:
- Autonomous TRAPs with line numbers (T1-T11): ✅ verifiable
- Inline reference with line number (T12, L1145): actual annotation is T3, T12 is just a comment
- Implied decisions without line numbers (T13-T16): NOT verifiable, no formal TRAP annotation exists

This mixing makes it impossible for Coder to implement AC-4 (перенести все 16 TRAP-аннотаций) without ambiguity: 4 of the 16 don't have actual annotations to "перенести" (port).

**Severity: MEDIUM** — Coder will attempt to grep for T13-T16 TRAPs that don't exist, find nothing, and either create fresh TRAPs (scope creep) or skip them (AC-4 violation). The ambiguity opens a decision gap.

**Fix:** Split the table into two sections:
1. **Autonomous TRAP annotations** (11 rows with line numbers) → ported to Python docstrings
2. **Implied design decisions** (4 rows, T13-T16) → formalized as new TRAP[DECISION] annotations in Python modules (fresh annotations documenting design rationale that was previously implicit)

---

### DRIFT-IMPL-01 [MEDIUM] — Integration Test Plan doesn't test non-fatal step isolation

**Files involved:**
- `.ai/plans/036-wave5e-deploy/01-DevPlan.md` D3 (L594-605): non-fatal steps (tag_current, prune, hooks, audit, notify) remain in shell
- `.ai/plans/036-wave5e-deploy/01-DevPlan.md` Integration Test Plan (L873-933): 5 staging scenarios

**Expected:** Integration test verifies that failure of non-fatal steps (e.g., docker tag fails, prune-images fails) does NOT change deploy status from "success" to "failed" — this is the core B1 fix contract.

**Actual:** Integration Test Plan scenarios:
1. Deploy test project (happy path) ✅
2. Status check (JSON comparison) ✅
3. Rollback test (broken image) ✅
4. Remove test ✅
5. Payload deliver test ✅

None of the 5 scenarios test the B1 invariant: "DEPLOY_STATUS='success' после health-gate, даже если tag_current/prune/hooks/audit/notify упали". The staging scenarios only test happy-path deploy, happy-path status, rollback, remove, and deliver.

**Severity: MEDIUM** — The B1 fix is the most complex bug fix in deploy-project.sh (TRAP[BUG] P1). If the Python engine breaks this invariant during migration, the regression won't be caught by existing staging tests. The bug would manifest as: deploy succeeds (health green), but deploy-result.json shows "failed" because a non-fatal step threw an exception.

**Fix:** Add Scenario 6 to Integration Test Plan: "Non-fatal step failure isolation — simulate prune failure (e.g., no Docker images to prune) and verify DEPLOY_STATUS stays 'success' in deploy-result.json."

---

### DRIFT-SCOPE-01 [LOW] — Entrypoint deploy-project.sh not included in scope analysis

**Files involved:**
- `.ai/plans/036-wave5e-deploy/01-DevPlan.md` File Manifest L1085: `core/entrypoints/deploy-project.sh` listed as "НЕ VPS forced-command. Уже использует platform_deliver.py для build. Изменения не требуются."
- Actual file: `core/entrypoints/deploy-project.sh` (exists, handles `make deploy-project` from dev machine)

**Expected:** The DevPlan acknowledges the entrypoint and correctly identifies it as out-of-scope.

**Actual:** The entrypoint is correctly excluded from modification scope. However, the entrypoint does call `deploy-project.sh` (VPS-side) via SSH, and any change to the VPS-side verb dispatch (e.g., changing the `platform-deliver` argument format) could break the entrypoint. The DevPlan doesn't document a cross-check of the entrypoint's SSH command construction against the new shell facade's verb dispatch.

**Severity: LOW** — The entrypoint constructs SSH commands like `ssh ci-deploy@<host> "deploy.sh <project> <ref>"` which are forwarded to deploy-project.sh's parse_ssh_command(). Since verb dispatch is preserved (same verbs), backward compatibility is maintained. Still, an explicit cross-check note would reduce risk.

**Fix:** Add a note in File Manifest: "Entrypoint deploy-project.sh verified: SSH command format `deploy.sh <project> <ref>` compatible with new verb dispatch."

---

### Drift Summary (Phase 2)

| DRIFT-ID | Severity | Category | Files |
|----------|----------|----------|-------|
| DRIFT-TRAP-01 | CRITICAL | TRAP count mismatch (16 claimed vs 11 actual autonomous) | DevPlan L44-61, L992-1037, L1155-1156 |
| DRIFT-TEST-01 | HIGH | validate_project_name location inconsistency | D7 L649-659 vs $TEST_SPEC L818-821 |
| DRIFT-DEP-01 | HIGH | Phantom dependency on DevPlan 036D | REQUIRES L32, TASK-036E1 |
| DRIFT-DOC-01 | MEDIUM | Missing DevPlan 081 artifact | REQUIRES L28-30 |
| DRIFT-TRAP-02 | MEDIUM | Debt table mixes autonomous + inferred TRAPs | L44-61 |
| DRIFT-IMPL-01 | MEDIUM | Integration test missing B1 non-fatal isolation scenario | Integration Test Plan L873-933 |
| DRIFT-SCOPE-01 | LOW | Entrypoint cross-check not documented | File Manifest L1085 |

**Total: 1 CRITICAL, 2 HIGH, 3 MEDIUM, 1 LOW = 7 drift issues.**

---

## Section 3 — Invariant Status (Phase 3)

### Architectural Invariants from AGENTS.md (Root)

| # | Invariant | Status | Evidence | Risk if violated |
|---|-----------|--------|----------|------------------|
| 1 | Makefile — единый фасад. Все операции через `make <target>` | ✅ HELD | DevPlan preserves deploy/remove/status verbs via make targets. New Python modules are internal, not exposed as new make targets. | N/A |
| 2 | Модель деплоя: git push → CI. Forced-command preserved | ✅ HELD | D2 explicitly keeps SSH forced-command contract intact. Python engine called from within forced-command shell. | Production deploy path breakage |
| 3 | org = context | ✅ HELD | No changes to context model. PROJECTS_BASE path preserved. | N/A |
| 4 | AGENTS.md — 3 канонических файла | ✅ HELD | No changes to AGENTS.md files. | N/A |
| 5 | entrypoint-manifest.yaml — реестр | ✅ HELD | Deploy verbs already registered. No new verbs introduced. | N/A |
| 6 | make bootstrap-node — идемпотентный | ✅ HELD | No changes to bootstrap pipeline. | N/A |
| 7 | Полный локальный стек через docker compose up | ✅ HELD | No changes to local dev stack. | N/A |
| 8 | LiteLLM — PostgreSQL во всех окружениях | ✅ HELD | No changes to LiteLLM configuration. | N/A |
| 9 | Тестовый сервер может быть пересоздан | ✅ HELD | No changes to test infrastructure. | N/A |
| 10 | Сборка образов hermes | ✅ HELD | No changes to hermes build pipeline. | N/A |
| 11 | Manifest Generation Contract | ✅ HELD | No new generated files. | N/A |

### Language Policy Invariant (AGENTS.md §Языковая политика)

| Rule | Status | Evidence |
|------|--------|----------|
| Новый код — Python | ✅ HELD | deploy_engine.py + payload_deliverer.py = new Python code |
| Bash как тонкая обёртка над Python | ✅ HELD | Shell facade ≤200 LOC, verb dispatch → python3 -m |
| Inline Python — сигнал к извлечению | ✅ HELD | 3 inline blocks eliminated per AC-1 |
| Strangler-триггер Tier 1/Tier 2 | ✅ HELD | Strangler-Fig pattern applied (Option A) |
| Shell-библиотеки НЕ мигрируются | ✅ HELD | 7 libs preserved in shell facade |

### Invariant Summary

| Status | Count |
|--------|-------|
| HELD | 11/11 invariants |
| VIOLATED | 0 |
| AT_RISK | 0 |
| UNVERIFIABLE | 0 |

**Phase 3 verdict: STABLE.** All 11 architectural invariants and language policy rules are preserved by the DevPlan.

---

## Section 4 — Test Quality (Phase 4)

### Test Specification Audit

**Total tests specified:** 35 (26 deploy_engine + 9 payload_deliverer)

**Coverage by DeployEngine method:**

| Method | Test count | Coverage adequate? |
|--------|-----------|-------------------|
| `_validate_project_name()` | 4 (valid, traversal, slash, empty) | ✅ Full edge case coverage |
| `_save_previous_image()` | 2 (exists, first_deploy) | ✅ Both branches covered |
| `_pull_image_with_retry()` | 3 (success, rate_limit, fail_all) | ✅ All retry paths covered |
| `_atomic_up()` | 2 (success, failure) | ✅ Both outcomes |
| `_poll_health()` | 2 (healthy, timeout) | ✅ Both outcomes |
| `_perform_rollback()` | 2 (success, failure) | ✅ Both outcomes |
| `deploy()` | 3 (success, rollback, first_deploy_fail) | ✅ Critical paths covered |
| `remove()` | 2 (idempotent, not_found) | ✅ Idempotency covered |
| `status()` | 3 (not_found, stub, found) | ✅ All 3 states |
| `_capture_snapshot()` | 1 | ⚠️ WARNING — minimum coverage |
| `_prune_old_images()` | 2 (below_limit, above_limit) | ✅ Both branches |

**Coverage by PayloadDeliverer method:**

| Method | Test count | Coverage adequate? |
|--------|-----------|-------------------|
| `_validate_and_extract()` | 5 (whitelist ok, reject, symlink, traversal, missing compose) | ✅ Security edge cases |
| `_read_payload()` | 2 (size cap, empty) | ✅ Boundary conditions |
| `deliver()` | 2 (atomic extract, full flow) | ✅ Integration-level |

### Semantic Quality Checks

| Check | Status | Evidence |
|-------|--------|----------|
| Invariant coverage | ⚠️ GAP | B1 invariant (DEPLOY_STATUS="success" isolation) has no explicit test in $TEST_SPEC for non-fatal step failure isolation |
| Contract tests | ✅ ADEQUATE | Deploy/Remove/Status/Deliver contracts each have dedicated tests |
| Behavioral vs implementation | ✅ GOOD | All 35 tests are BEHAVIORAL (assert on return values, exceptions, subprocess call arguments — not string matching on code text) |
| Drift gate tests | ✅ PRESENT | AC-4 TRAP count gate, AC-1 shell LOC gate, AC-6 test+gate green |
| Negative tests (R5 anti-survivorship) | ⚠️ PARTIAL | TRAP[BUG] scenarios (B1 exit code, platform-deliver exit 1, env prefix, REF suffix, path prefix) have corresponding unit tests that verify the fix, but no explicit `_original_form` negative tests capturing the exact pre-fix input that triggered each bug |
| Skip rate | ✅ 0% | New tests, no skip markers |
| Fragility index | ✅ N/A | New tests, no staleness |

### Test Quality Issues

| ID | Severity | Description |
|----|----------|-------------|
| TQ-01 | WARNING | B1 non-fatal step isolation not explicitly tested (see DRIFT-IMPL-01) |
| TQ-02 | WARNING | 4 TRAP[BUG] fixes lack explicit anti-survivorship negative tests (R5) |
| TQ-03 | WARNING | `_capture_snapshot()` has only 1 test (happy path) — missing: snapshot on empty project, snapshot with no containers |
| TQ-04 | WARNING | `_validate_project_name()` test location inconsistent with D7 DRY extraction to project_registry.py |

### Test Health Score

```
score = 100
- 0 (no CRITICAL drift in tests)
- 0 (no HIGH drift in tests)
- 4 × 3 = 12 (4 WARNING test quality issues)
= 88/100
```

**Phase 4 verdict: ADEQUATE with WARNINGS.** Test count (35) and coverage design are strong. 4 warnings: missing anti-survivorship tests for TRAP[BUG] scenarios, missing B1 isolation test, thin snapshot coverage, validation test location inconsistency.

---

## Section 5 — Runtime Validation (Phase 5)

**Status: N/A** — This is a DevPlan quality audit, not implementation verification. Phase 5 (pytest, LDD trace, AC verification) will be performed by QA on the implemented code in TASK-036E5.

**Pre-audit note:** The AC verification methodology for the future Phase 5 is well-defined in the Integration Test Plan (L873-933) and the Acceptance Criteria Summary (L798-810). Each AC has a clear verification method documented.

---

## Section 6 — Config Sync Audit (Phase 6)

**Status: N/A** — The DevPlan does not modify any config files (no compose files, .env, CI workflows, or module contracts changed). File Manifest (L1063-1090) confirms only Python modules, shell script, and test files are affected.

**Pre-audit note:** When Phase 5 is executed, the QA should verify that `make gate MODE=fast` in TASK-036E6 passes the following gate checks that will be affected by the migration:
- Gate #8 (module contract validation): deploy-project.sh MODULE_CONTRACT must be preserved in shell facade
- Gate #10 (forbidden scripts): no new .sh files in forbidden directories
- Any gate checking `wc -l` limits on .sh files (deploy-project.sh ≤200)

---

## Semantic Verdict

```
VERDICT: DRIFTED (CRITICAL)
Health score: N/A (plan review, not implementation)
```

### Verdict justification

The DevPlan is **structurally complete** (all required sections, 7/7 $ARTIFACT_CONTRACT fields, 7-option superposition with scoring matrix, 35-test spec, 8 risk mitigations). Architectural invariants are preserved (11/11 HELD). The Strangler-Fig approach (Option A) is well-justified with precedent from Wave 4.

However, **one CRITICAL drift blocks the plan from STABLE status:**

**DRIFT-TRAP-01:** The TRAP annotation count is self-contradictory. The debt intake table claims 16 TRAPs, but only 11 are autonomous (grep-able annotations in the source file). The CI gate criteria (≥12 + ≥4 = 16) cannot be satisfied by the documented post-migration TRAP inventory (11 total). This is not just a documentation error — it means the CI gate will FAIL on merge, creating a blocker that the Coder cannot resolve without additional decisions from the Architect.

### Severity Matrix

| Finding | Severity | Blocks merge? |
|---------|----------|:---:|
| DRIFT-TRAP-01: TRAP count mismatch | CRITICAL | ✅ YES — CI gate criteria impossible to meet |
| DRIFT-TEST-01: validate_project_name location contradiction | HIGH | ⚠️ YES — Coder receives conflicting instructions |
| DRIFT-DEP-01: Phantom 036D dependency | HIGH | ❌ NO — but inflates dependency graph |
| DRIFT-DOC-01: Missing DevPlan 081 | MEDIUM | ❌ NO — modules exist |
| DRIFT-TRAP-02: Debt table mixing types | MEDIUM | ❌ NO — but causes implementation ambiguity |
| DRIFT-IMPL-01: Missing B1 isolation test | MEDIUM | ❌ NO — but regression risk |
| DRIFT-SCOPE-01: Entrypoint cross-check | LOW | ❌ NO |

### Required Fixes Before Implementation

1. **[CRITICAL] Resolve TRAP-TRAP-01**: Architect must decide:
   - Backfill T13-T16 as formal TRAP[DECISION] annotations in deploy-project.sh → 15 autonomous TRAPs + 1 new post-migration → 16 total
   - OR adjust CI gate to realistic count (≥11 + ≥4 = 15, reflecting actual autonomous + post-migration TRAPs)
   - OR add documentation-only TRAPs (TRAP[DOC]) for T13-T16 in Python modules to reach 16

2. **[HIGH] Resolve DRIFT-TEST-01**: Unify validate_project_name location:
   - Move 4 validation tests to `test_project_registry.py`
   - Add integration test in `test_deploy_engine.py` verifying shared function import

3. **[HIGH] Resolve DRIFT-DEP-01**: Remove or clarify DevPlan 036D dependency:
   - Remove from REQUIRES if not a runtime dependency
   - OR add explicit note: "cross-wave awareness only — overlay_deliverer operates in independent execution path (remote-cmd.sh), no runtime coupling with deploy-project.sh"

4. **[MEDIUM] Address DRIFT-IMPL-01**: Add Scenario 6 to Integration Test Plan for B1 non-fatal step isolation verification.

### Strengths (notable)

- **Safety architecture excellence:** D2 (trap handlers in shell) with explicit SIGSEGV/SIGKILL rationale is the strongest argument in the document. Preserving shell trap guarantees while migrating business logic to Python is exactly the right trade-off.
- **Dependency graph clarity:** Wave structure (1→2→3+4) with parallel tasks is well-designed. TASK-036E1 and TASK-036E2 are truly independent (zero file intersection).
- **Rollback strategy completeness:** Emergency rollback <30 min with both full-node and targeted SCP options, plus a decision tree covering all failure modes.
- **Precedent anchoring:** Wave 4 success metrics (4114→392 LOC, 204/210 tests) provide evidence-based justification for applying the same Strangler-Fig pattern to this critical component.
- **Integration test plan detail:** 5 staging scenarios with exact commands, pass criteria, and dry-run fallback. Production-ready.

### Delegation

The CRITICAL and HIGH findings require Architect intervention before Coder can implement:

```
task(subagent_type="Architect", description="Fix DevPlan 036E drift issues",
  prompt="Review VerificationReport 02 at .ai/plans/036-wave5e-deploy/02-VerificationReport.md.
         Resolve CRITICAL DRIFT-TRAP-01 (TRAP count mismatch),
         HIGH DRIFT-TEST-01 (validate_project_name location),
         HIGH DRIFT-DEP-01 (phantom 036D dependency),
         and MEDIUM DRIFT-IMPL-01 (missing B1 isolation test).
         Produce revised DevPlan or addendum (03-DevPlan-fix.md).
         Do NOT modify deploy-project.sh — only update DevPlan.")
```

$END_VERIFICATION_REPORT
