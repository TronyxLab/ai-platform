$START_VERIFICATION_REPORT

# VerificationReport 036C — DevPlan 036C Quality Audit

$ARTIFACT_CONTRACT
- **PURPOSE:** Semantic quality audit of DevPlan 036C (`.ai/plans/036-wave5c-adopt/01-DevPlan.md`) — Strangler-Fig adopt-project.sh → project_adopter.py
- **DESCRIPTION:** Static protocol compliance check + cross-reference drift detection + dependency verification against TASK-036B (DevPlan 086). No runtime validation (DevPlan review, not code).
- **RATIONALE:** Verify DevPlan correctness before Coder implementation — catch structural issues, drift in cross-references, and dependency contradictions that could derail TASK-036C.
- **ACCEPTANCE_CRITERIA:**
  - AC-P1: All 7 $ARTIFACT_CONTRACT fields present and semantically valid
  - AC-P2: $START_DEVPLAN/$END_DEVPLAN boundary markers present
  - AC-P3: All mandatory DevPlan sections present
  - AC-P4: Cross-references to TASK-036B verified — no stale/broken references
  - AC-P5: Superposition analysis: ≥3 options with scoring matrix
  - AC-P6: Design Decisions cover all critical integration points (vhost_renderer, project_registry, gen_env_platform)
  - AC-P7: All inline issues flagged with severity and fix recommendations
- **IMPLEMENTS:** QA verification of DevPlan 036C before Coder delegation
- **IMPACTS:** None (read-only audit)
- **REQUIRES:**
  - `.ai/plans/036-wave5c-adopt/01-DevPlan.md` (audit target)
  - `.ai/plans/086-wave5b-vhost/01-DevPlan.md` (dependency verification)
  - `core/internal/scaffold/adopt-project.sh` (TRAP audit source)
$END_ARTIFACT_CONTRACT

🔒 **Verified against SHA:** `d6ba7d6c4d1f4ac5b7cbd9ec5bf492a4351c1b89`
⚠️ **Warning:** 14 files have uncommitted changes (`.env.example`, `core/internal/bootstrap/*.py`, tests, etc.). Changes appear unrelated to adopt-project, but represent verification environment noise.

---

## Section 1 — Static Audit (Phase 1)

### Compliance Matrix

| Check | 01-DevPlan.md | Status |
|-------|:---:|:---:|
| $START_DEVPLAN / $END_DEVPLAN | Lines 1, 674 | ✅ PASS |
| $ARTIFACT_CONTRACT (7 fields) | Lines 5-29 | ✅ PASS |
| PURPOSE | Line 6 | ✅ PASS |
| DESCRIPTION | Line 7 | ✅ PASS |
| RATIONALE | Line 8 | ✅ PASS |
| ACCEPTANCE_CRITERIA | Lines 9-16 | ✅ PASS |
| IMPLEMENTS | Line 17 | ✅ PASS |
| IMPACTS | Lines 18-22 | ✅ PASS |
| REQUIRES | Lines 23-28 | ⚠️ PASS (with drift — see Section 2) |
| $END_ARTIFACT_CONTRACT | Line 29 | ✅ PASS |
| Debt Intake section | Lines 33-51 | ✅ PASS |
| Requirements Analysis | Lines 55-74 | ✅ PASS |
| Superposition Analysis | Lines 78-147 | ✅ PASS |
| Architecture Overview | Lines 150-185 | ✅ PASS |
| Design Decisions | Lines 344-422 | ✅ PASS |
| $TASKS section | Lines 425-443 | ✅ PASS |
| Acceptance Criteria Summary | Lines 447-457 | ✅ PASS |
| $TEST_SPEC section | Lines 461-483 | ✅ PASS |
| Risk Assessment | Lines 487-500 | ✅ PASS |
| File Manifest | Lines 558-581 | ✅ PASS |
| Shell Facade Structure | Lines 585-634 | ✅ PASS |
| Next Steps | Lines 637-673 | ✅ PASS |
| TRAP Inventory | Lines 516-554 | ✅ PASS |

### Summary
- **Total checks:** 22
- **PASS:** 21
- **WARNINGS:** 1 (REQUIRES cross-reference — detailed in Section 2)

---

## Section 2 — Drift Analysis (Phase 2)

### DRIFT-1: Cross-reference to vhost_renderer.py [HIGH]

| Field | Value |
|-------|-------|
| **DRIFT-ID** | DRIFT-REF-001 |
| **Severity** | HIGH |
| **Location** | Line 27: `**TASK-036B (DevPlan 036B)**` |
| **Expected** | Reference to actual DevPlan: `TASK-036B (DevPlan 086, .ai/plans/086-wave5b-vhost/)` |
| **Actual** | `DevPlan 036B` — conflates task identifier (TASK-036B) with DevPlan number (086) |
| **Evidence** | TASK-036B is NOT implemented by any artifact named "DevPlan 036B". The implementing DevPlan is at `.ai/plans/086-wave5b-vhost/01-DevPlan.md` (title: "DevPlan 086 — Wave 5b: Strangler-Fig add-vhost.sh → vhost_renderer.py", line 3) |
| **Impact** | Agent searching for `036B*DevPlan*` will find nothing — blocked from reading vhost_renderer contract. DevPlan 086's line 17 correctly references: `IMPLEMENTS: TASK-036B (Wave 2a) из DevPlan 036` |
| **Fix** | Replace `DevPlan 036B` with `TASK-036B (DevPlan 086, .ai/plans/086-wave5b-vhost/01-DevPlan.md)` in REQUIRES field (line 27). Alternatively, reference by canonical folder: `.ai/plans/086-wave5b-vhost/` |

### DRIFT-2: Internal contradiction — BLOCKS vs parallel start [MEDIUM]

| Field | Value |
|-------|-------|
| **DRIFT-ID** | DRIFT-CONTRADICT-002 |
| **Severity** | MEDIUM |
| **Location** | Line 27 vs Line 440 |
| **Line 27 (REQUIRES):** | `**БЛОКИРУЕТ**: TASK-036C не может стартовать до завершения TASK-036B.` |
| **Line 440 (Dependencies):** | `но может стартовать параллельно с fallback-режимом` |
| **Analysis** | These two statements contradict. Line 27 says BLOCKED (cannot start until 036B done). Line 440 says can start in parallel with fallback. The D4 mitigation correctly describes the fallback mechanism, making line 27 overly strict. |
| **Impact** | Agent reading only line 27 would refuse to start TASK-036C; agent reading only line 440 would proceed. Execution depends on which line is encountered first. |
| **Fix** | Update line 27: remove `**БЛОКИРУЕТ**`, replace with `**ЗАВИСИТ** (с fallback — D4): TASK-036C может стартовать параллельно через subprocess add-vhost.sh; прямой import vhost_renderer требует завершения TASK-036B.` |

### DRIFT-3: Forward contract — configure_vhost_for_project not defined in 086 [LOW]

| Field | Value |
|-------|-------|
| **DRIFT-ID** | DRIFT-CONTRACT-003 |
| **Severity** | LOW |
| **Location** | Lines 310-323 (contract definition) vs 086-wave5b-vhost (no matching API) |
| **Analysis** | DevPlan 036C defines `configure_vhost_for_project(project_dir, domain, node_configs_dir)` as the expected vhost_renderer Python API. DevPlan 086 defines vhost_renderer with CLI subcommands (`add`, `remove`, `render-all`) but does NOT explicitly define a library-level `configure_vhost_for_project` function. The 086 plan acknowledges the dependency (line 297: `adopt-project.sh → вызовет vhost_renderer для configure_vhost`) but leaves the API contract unspecified. |
| **Impact** | Risk that 086 and 036C implementations diverge on function signature. Mitigated by D4 fallback (subprocess add-vhost.sh). |
| **Fix** | Document in 086's Pre-implementation Checklist: ensure `configure_vhost_for_project(project_dir, domain, node_configs_dir) → bool` is part of the Python public API. Alternatively, 036C should accept that `subprocess.run(['python3', '-m', 'core.internal.scaffold.vhost_renderer', 'add', ...])` is an acceptable permanent integration (not just fallback), if 086 is designed as CLI-only. |

### Cross-reference verification summary

| Reference in 036C | Type | Target exists? | Status |
|-------------------|------|:---:|:---:|
| `DevPlan 036B` (line 27) | REQUIRES | ❌ No such artifact; TASK-036B = DevPlan 086 | **DRIFT** |
| `DevPlan 070` (line 25) | REQUIRES | ✅ `.ai/plans/070-extract-shared-libs/` | PASS |
| `Plan 082` (line 26) | REQUIRES | ✅ `.ai/plans/082-config-env-unification/` | PASS |
| `TASK-036B (DevPlan 036B)` (line 27) | REQUIRES | ⚠️ Task ID correct, DevPlan ID wrong | **DRIFT** |
| `086` as number | — | ❌ Not referenced in 036C at all | GAP |

---

## Section 3 — Invariant Verification (Phase 3)

### Architectural invariants check (AGENTS.md)

| # | Invariant | Status | Evidence |
|---|-----------|:---:|----------|
| 1 | Makefile — единый фасад | HELD | Line 438: `make adopt-project DIR=<test-project>` — uses make target, not direct shell |
| 11 | Manifest Generation Contract | HELD | adopt-project won't affect generated manifests; not in manifest scope |
| — | Language policy (Python-first) | HELD | Core goal of this plan: 906→120 LOC shell, 0 inline python3 |
| — | Strangler-Fig discipline | HELD | Option A (full SF) selected, D1 documents why parse_args stays in shell |
| — | Dual mechanism prohibition | HELD | DUAL_MECHANISM_DETECTION section (lines 167-174): no duplicates found; migration converges subprocess→import |

### Forward contract risk

| Contract | Defined by | Implemented by | Status |
|----------|:----------:|:--------------:|:---:|
| `configure_vhost_for_project()` | 036C (lines 310-323) | 086 (TASK-036B) | AT_RISK — 086 acknowledges but doesn't define this exact signature |
| `register_project()` import | 036C (lines 293-308) | 070 (project_registry.py) | HELD — exists, contract is known |
| `gen_env_platform` subprocess | 036C (lines 326-340) | 082 (gen_env_platform.py) | HELD — CLI-first, subprocess is correct |

---

## Section 4 — Test Quality (Phase 4) — abbreviated

### Test spec coverage

| Test category | Count | Coverage |
|---------------|:-----:|----------|
| YAML generation (generate_minimal_yaml) | 3 tests (#1-3) | ✅ |
| Compose validation (validate_compose_networks) | 4 tests (#4-7) | ✅ |
| Org validation (validate_org_against_node_yaml) | 2 tests (#8-9) | ✅ |
| CI rewriting (simplify_deploy_yml) | 2 tests (#10-11) | ✅ |
| Registration (register_in_node_yaml) | 1 test (#12) | ✅ |
| Vhost (configure_vhost) | 1 test (#13, mocked) | ✅ |
| Templates (generate_makefile, generate_agents) | 2 tests (#14-15) | ✅ |
| **Total** | **15 tests** | **≥7 required → well above threshold** |

### Anti-Illusion check (test spec)
- Test #13 (`test_configure_vhost_mocked`) — uses mock for vhost_renderer. If vhost_renderer API changes, this test won't catch the mismatch since it mocks the import. **INFO**: Consider adding an integration test that exercises the real fallback path (subprocess add-vhost.sh) when vhost_renderer is unavailable.

---

## Section 5 — Runtime Validation (Phase 5) — N/A

**Not applicable.** This is a DevPlan quality audit, not a code verification. Runtime validation (pytest, LDD traces, AC verification) is deferred to Coder implementation phase.

---

## Section 6 — Config Sync (Phase 6) — N/A

**Not applicable.** This DevPlan does not modify configuration files (compose, .env, CI workflows). No config propagation chains to verify.

---

## Detailed Findings Register

### [HIGH] DRIFT-REF-001 — Cross-reference "DevPlan 036B" doesn't exist
- **File:** `.ai/plans/036-wave5c-adopt/01-DevPlan.md:27`
- **Expected:** `TASK-036B (DevPlan 086, .ai/plans/086-wave5b-vhost/01-DevPlan.md)`
- **Actual:** `TASK-036B (DevPlan 036B)`
- **Fix:** Replace "DevPlan 036B" with canonical reference including folder path. See DRIFT-1 in Section 2.

### [MEDIUM] DRIFT-CONTRADICT-002 — BLOCKS vs parallel start contradiction
- **File:** `.ai/plans/036-wave5c-adopt/01-DevPlan.md:27` vs `:440`
- **Expected:** Consistent dependency statement
- **Actual:** Line 27: "**БЛОКИРУЕТ**: не может стартовать до завершения TASK-036B"; Line 440: "может стартовать параллельно с fallback-режимом"
- **Fix:** Resolve contradiction — use "ЗАВИСИТ (с fallback)" in line 27. See DRIFT-2 in Section 2.

### [LOW] DRIFT-CONTRACT-003 — Forward contract risk with vhost_renderer
- **File:** `.ai/plans/036-wave5c-adopt/01-DevPlan.md:310-323` vs `.ai/plans/086-wave5b-vhost/01-DevPlan.md`
- **Expected:** `configure_vhost_for_project` defined in 086's Python API
- **Actual:** 086 defines CLI subcommands only, acknowledges but doesn't specify library API
- **Fix:** Either (a) 086 adds explicit library API or (b) 036C accepts subprocess as permanent integration. See DRIFT-3 in Section 2.

### [LOW] 14 uncommitted files — SHA anchor noise
- **File:** git working directory
- **Impact:** SHA d6ba7d6 doesn't represent clean state. Files modified appear unrelated to adopt-project (bootstrap, certs, tests).
- **Fix:** Consider `git stash` before verification or verify that none of the uncommitted changes affect adopt-project.sh.

### [INFO] TRAP[DECISION] emoji: 🧐 vs ⚠️ (pre-existing)
- **File:** `core/internal/scaffold/adopt-project.sh:60` (source of DevPlan TRAP inventory line 521)
- **Observation:** adopt-project.sh uses 🧐 for TRAP[DECISION] (line 60). AGENTS.md canonical format uses ⚠️ for all TRAP types. The DevPlan accurately reproduces the source file's format — this is pre-existing, not introduced by the plan.

### [INFO] Shell facade pseudocode — relative source paths
- **File:** `.ai/plans/036-wave5c-adopt/01-DevPlan.md:592`
- **Observation:** `source logging.sh, args.sh, python_deps.sh` uses conceptual relative names. Coder should use actual paths: `source core/lib/logging.sh`, etc.

### [INFO] Design Decision heading format
- **File:** `.ai/plans/036-wave5c-adopt/01-DevPlan.md:346-422`
- **Observation:** Design decisions use `### ## @rationale D1:` (double heading `###` + `##`). Works in markdown but slightly non-standard — canonical AGENTS.md uses `## @rationale (N)`.

---

## Semantic Verdict

**Verdict: DRIFTED (HIGH)**

**Justification:**
- DRIFT-REF-001 (HIGH): Cross-reference "DevPlan 036B" is a non-existent artifact — agents resolving this dependency will fail to locate the vhost_renderer contract at `.ai/plans/086-wave5b-vhost/01-DevPlan.md`. This is a navigational hazard that MUST be fixed before delegating to Coder.
- DRIFT-CONTRADICT-002 (MEDIUM): Internal contradiction between BLOCKS and parallel-start weakens execution clarity.

**Score (project health not computed — PERIODIC AUDIT mode not active):**
N/A — DevPlan review, not project health audit.

**Mitigations present (prevent implementation blockage):**
- D4 fallback (subprocess add-vhost.sh) enables parallel development regardless of reference error.
- Contract is formally defined (lines 310-323) — Coder can implement against the contract even without reading 086.

**Recommendation:** Fix DRIFT-REF-001 before delegation. The fix is a one-line change in the REQUIRES field.

---

## Remediation Delegation Proposals

### Proposal 1: Fix cross-reference drift (Architect)
```
task(subagent_type="Plan", description="Fix DevPlan 036C cross-reference drift",
prompt="Review VerificationReport at .ai/plans/036-wave5c-adopt/02-VerificationReport.md. Fix DRIFT-REF-001: update line 27 of 01-DevPlan.md to reference TASK-036B correctly (DevPlan 086, .ai/plans/086-wave5b-vhost/01-DevPlan.md). Fix DRIFT-CONTRADICT-002: resolve BLOCKS/parallel contradiction. Update line 27 to say 'ЗАВИСИТ (с fallback — D4)' instead of 'БЛОКИРУЕТ'.")
```

### Proposal 2 (optional): Document forward contract in 086
```
task(subagent_type="Plan", description="Add configure_vhost_for_project to 086 API",
prompt="Review .ai/plans/086-wave5b-vhost/01-DevPlan.md. Add explicit library-level API function configure_vhost_for_project(project_dir, domain, node_configs_dir) -> bool to the Python module contract to match what 036C expects (see .ai/plans/036-wave5c-adopt/01-DevPlan.md lines 310-323).")
```

$END_VERIFICATION_REPORT
