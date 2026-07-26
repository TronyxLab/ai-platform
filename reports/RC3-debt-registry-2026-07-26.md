<!-- GREP_SUMMARY: RC3, debt-registry, deferred, open-items, H1, L2, L3, L4, PRE-01, PRE-02 -->
<!-- STRUCTURE: ┌debt-registry-header┐ → ◇ table: ID | Plan | Severity | Description | Status | Target → ◇ detailed-descriptions → ⎋ close-conditions -->
# RC3 Debt Registry — 2026-07-26

$ARTIFACT_CONTRACT
## @PURPOSE: Registry of all unresolved/open debt items from RC3 verification, deferred to RC4 or tracked as pre-existing
## @DESCRIPTION: Single source of truth for technical debt remaining after RC3 Phase 4 fixes. Covers: design issues, implementation gaps, formal violations, pre-existing CI failures. Each entry has ID, severity, description, status, and target resolution.
## @RATIONALE: Prevents silent drift between RC releases. Agents and operators use this registry to scope RC4 work without re-auditing all 15 plans.
## @ACCEPTANCE_CRITERIA: (1) All RC3 deferred issues captured, (2) Each entry has unambiguous target, (3) Pre-existing non-RC3 items separated, (4) Close conditions documented
## @IMPLEMENTS: RC3 verification §6 (Known Debt) — detailed breakdown
## @IMPACTS: RC4 planning, Architect revision (H1), Coder tasks (072, 073, 076), QA audit
## @REQUIRES: RC3 Verification Report (.reports/RC3-verification-2026-07-26.md), Gap Analysis (.ai/plans/085-rc3-verification/01-RC3-Gap-Analysis.md)
$END_ARTIFACT_CONTRACT

---

## 🔒 Verified at SHA `2aace31043aee1387fcfddc8f21dd54d6f5ce0d4`
**Date:** 2026-07-26T14:45+03:00
**Phase 4 fixes applied:** Working tree includes all verified Phase 4 changes

---

## Registry

### Design & Architecture Debt

| ID | Plan | Severity | Description | Status | Target | Close Condition |
|----|------|:--------:|-------------|:------:|--------|-----------------|
| DEBT-071-01 | 071 | 🔴 HIGH | Shell/Python step alignment design flaw. Shell writes steps 1-12 (keys aligned) but steps 13-16 diverge from Python's INIT_STEPS. Python-only steps (`ensure_secrets`, `secrets_init`) would be incorrectly skipped on resume because shell doesn't call checkpoint for them. | OPEN | RC4 — Architect revision | Architect-approved DevPlan revision fixing step inventory alignment |
| DEBT-IMPL-01 | 071 | 🔴 HIGH | Plan 071 not started (blocked by design flaw). Requires Architect revision of DevPlan before Coder can implement. | OPEN | RC4 | Architect-approved DevPlan 071 revision |

### Implementation Gaps

| ID | Plan | Severity | Description | Status | Target | Close Condition |
|----|------|:--------:|-------------|:------:|--------|-----------------|
| DEBT-IMPL-02 | 072 | 🟢 LOW | Plan 072 (secrets-atomic-write) not started. Pre-impl VR exists (STABLE). Independent, low risk — can be RC4 quick win. | OPEN | RC4 | Coder implementation + VR |
| DEBT-IMPL-03 | 073 | 🟡 MEDIUM | Plan 073 (provision-python) not started. Plan needs revision — scope gaps identified (unmentioned consumers, missing entrypoint-manifest acknowledgment). Pre-impl VR corrected to DRIFTED (WARNING). | OPEN | RC4 | Plan revision + Coder implementation |
| DEBT-IMPL-04 | 076 | 🟢 LOW | Plan 076 (reconcile-python) not started. reconcile-projects.sh not yet migrated. Pre-impl VR DRIFTED (WARNING). Low complexity — RC4 quick win. | OPEN | RC4 | Coder implementation + VR |
| DEBT-IMPL-05 | 077 | 🟢 LOW | Plan 077 (systemic-drift-unification) meta-plan only. Diagnostic brief — not an implementation plan. 070 deletion was transient git state. | CLOSED (diagnostic) | N/A | No further action needed |

### Formal Compliance Debt

| ID | Plan | Severity | Description | Status | Target | Close Condition |
|----|------|:--------:|-------------|:------:|--------|-----------------|
| DEBT-STYLE-01 | multiple | 🟢 LOW | VR/DevPlan naming conventions — 6 VR files and 12 DevPlan files lack NN prefix (should be `NN-VerificationReport.md` and `NN-DevPlan.md` per $ARTIFACT_REGISTRY). Renaming would break cross-references — deferred to RC4 coordinated rename. | OPEN | RC4 | Bulk rename with cross-reference audit |
| DEBT-STYLE-02 | 082 | 🟢 LOW | `gen_env_platform.py` missing IMP:9 business-logic logs. Shell facade provides IMP:9 at L105, but Python module violates Zero-Context Survival principle. 2 IMP: lines exist but none at IMP:9 level. | OPEN | RC4 | Add IMP:9 log entries to `gen_env_platform.py` |
| DEBT-STYLE-03 | 082 | 🟢 LOW | Test naming drift — DevPlan 082 $TEST_SPEC references `tests/unit/test_gen_env_platform.py` and `tests/unit/test_sync_env_defaults.py`. Actual gen_env_platform tests are in `tests/test_scaffold_env_platform.py` (different name, different location). sync_env_defaults tests are correctly placed. | OPEN | RC4 | Align DevPlan $TEST_SPEC with actual file locations or move test files |

### Pre-existing CI Failures (NOT RC3 Regressions)

| ID | Plan | Severity | Description | Status | Target | Close Condition |
|----|------|:--------:|-------------|:------:|--------|-----------------|
| DEBT-PRE-01 | N/A | 🟡 MEDIUM | ruff-check pre-existing lint issues (~30). Pre-dates RC3. Not caused by any Phase 4 fix. Blocks `make gate MODE=fast` but is independently tracked. | OPEN | RC4 — periodic cleanup | `make fix-ruff SCOPE=all` + review |
| DEBT-PRE-02 | N/A | 🟡 MEDIUM | check-doc-headers pre-existing failures. Blocks `make gate MODE=fast` but pre-dates RC3. | OPEN | RC4 | Fix doc headers or update check-doc-headers.sh |

---

## Detailed Descriptions

### DEBT-071-01: Shell/Python Step Alignment Design Flaw

**Source:** `071-unify-checkpoints/VerificationReport.md:22-24`
**Root cause:** Shell bootstrap pipeline writes checkpoint keys for steps 1-12 (aligned with Python's INIT_STEPS) but steps 13-16 diverge. Python-only steps `ensure_secrets` and `secrets_init` exist only in Python's state machine — shell does not write checkpoints for them.
**Impact on resume:** After checkpoint step 12, shell's `--resume` mode maps to step 13 (shell's numbering). Python's INIT_STEPS lists `ensure_secrets` at index 12 and `secrets_init` at index 13. These steps would be **silently skipped** because their checkpoint keys don't exist in shell's key space.
**Required Architect decision:** Either (a) add explicit checkpoint calls for Python-only steps to shell's step functions, or (b) revise INIT_STEPS to remove Python-only steps from the shared key space, requiring separate initialization on resume.
**Risk of inaction:** First production bootstrap with `--resume` after step 12 will silently skip secrets initialization. Secrets would be absent or stale — no error, no warning.

### DEBT-IMPL-01: Plan 071 Not Started

**Prequisite:** H1 (design flaw) must be resolved first. Coder cannot start until Architect revises DevPlan.
**DevPlan exists?** Yes — `071-unify-checkpoints/DevPlan.md` (43KB). But contains the step alignment design flaw.
**Recommendation:** Architect revision → updated DevPlan → Coder implementation in RC4 wave 1.

### DEBT-IMPL-02: Plan 072 Not Started

**Scope:** Secret atomic write (LITELLM_METRICS_TOKEN removal, NEXTAUTH_SECRET secret_definitions.yaml entry).
**Pre-impl VR:** STABLE — no blockers.
**Effort estimate:** Low (~1-2 Coder sessions). 5 tests already in place.
**Recommendation:** RC4 quick win after 071 design revision.

### DEBT-IMPL-03: Plan 073 Not Started

**Scope:** provision-environment.sh → Python migration (Python provisioner).
**Pre-impl VR:** DRIFTED (WARNING) — plan needs revision. Scope gaps: unmentioned consumers (docker-compose.yml:30 passes COMPOSE_PROFILES via env_file), missing entrypoint-manifest acknowledgment.
**Effort estimate:** Medium (~3-4 Coder sessions).
**Recommendation:** Plan revision → Coder implementation in RC4 wave 2.

### DEBT-IMPL-04: Plan 076 Not Started

**Scope:** reconcile-projects.sh → Python migration.
**Pre-impl VR:** DRIFTED (WARNING) — low complexity.
**Effort estimate:** Low (~1 Coder session).
**Recommendation:** RC4 quick win.

### DEBT-IMPL-05: Plan 077 (Diagnostic)

**Type:** Meta-Brief — systemic drift audit roadmap. Not an implementation plan.
**VR issue:** 03-VR reported "DRIFTED (CRITICAL)" because DevPlan 070 was deleted from working tree (`git status` showed `D`). Since restored — transient git state, not architectural problem.
**Status:** CLOSED — diagnostic only, no implementation needed.

### DEBT-STYLE-01: Naming Convention Violations

**Affected VR files (no NN prefix):** 071/VerificationReport.md, 074/VerificationReport.md, 075/VerificationReport.md, 076/VerificationReport.md, 078/VerificationReport.md, 082/VerificationReport.md
**Affected DevPlan files (no NN prefix):** 070/DevPlan.md, 072/DevPlan.md, 073/DevPlan.md, 074/DevPlan.md, 075/DevPlan.md, 076/DevPlan.md, 077/DevPlan.md, 079/DevPlan.md, 080/DevPlan.md, 082/DevPlan.md, 083/DevPlan.md, 084/DevPlan.md
**Risk of fix:** Renaming would break all cross-references in VRs, AGENTS.md files, and CI configurations that reference these paths. Requires coordinated bulk rename with cross-reference audit.
**Recommendation:** RC4 — single coordinated rename wave with complete cross-reference update.

### DEBT-STYLE-02: Missing IMP:9 Logs

**File:** `core/internal/scaffold/gen_env_platform.py`
**Current state:** Shell facade `gen-env-platform.sh` provides IMP:9 at L105 (`log_imp 9 "gen-env-platform" "dry-run: would generate .env.platform"`). Python module has 2 IMP: lines but none at IMP:9 level.
**Impact:** Agents inspecting the Python module without the shell facade will see no business-logic-level telemetry. Violates Zero-Context Survival principle.
**Fix:** Add `logger.info("[IMP:9][gen_env_platform] ...")` at key decision points (env generation start/end, dry-run mode, completion with file count).

### DEBT-STYLE-03: Test Naming Drift

**DevPlan reference:** `tests/unit/test_gen_env_platform.py` and `tests/unit/test_sync_env_defaults.py`
**Actual location:** `tests/test_scaffold_env_platform.py` — `gen_env_platform` tests are here, not in `tests/unit/`. `test_sync_env_defaults.py` is correctly at `tests/unit/test_sync_env_defaults.py`.
**Impact:** Agents following DevPlan $TEST_SPEC will not find the gen_env_platform test at the expected path. Test inventory tools (sync_inventory.py) may miss it.
**Fix:** Either (a) move `test_scaffold_env_platform.py` to `tests/unit/test_gen_env_platform.py` and update cross-references, or (b) update DevPlan $TEST_SPEC to reference the actual path.

### DEBT-PRE-01: ruff-check Pre-existing Failures

**Type:** Lint-style issues (~30 findings across the codebase).
**Source:** Not caused by RC3 or any DevPlan 070-084 implementation. Pre-dates RC3 audit.
**Impact:** Blocks `make gate MODE=fast` — but this is a pre-existing issue, not an RC3 regression.
**Mitigation:** `make fix-ruff SCOPE=all` applies auto-fix to ~80% of issues. Remaining ~20% require manual review (E501 line-too-long, F841 unused variables, etc.).
**Recommendation:** Include in RC4 periodic cleanup wave.

### DEBT-PRE-02: check-doc-headers Pre-existing Failures

**Type:** Documentation header format validation.
**Source:** Pre-dates RC3. Script `core/entrypoints/check-doc-headers.sh` validates shebang/GREP_SUMMARY/STRUCTURE headers on shell scripts.
**Impact:** Blocks `make gate MODE=fast`.
**Mitigation:** Either fix non-compliant script headers or update check-doc-headers.sh to align with current markup standard.
**Recommendation:** Include in RC4.

---

## Summary

| Category | Count | Open | Closed |
|----------|:-----:|:----:|:------:|
| Design & Architecture | 2 | 2 | 0 |
| Implementation Gaps | 4 | 3 | 1 (diagnostic) |
| Formal Compliance | 3 | 3 | 0 |
| Pre-existing CI Failures | 2 | 2 | 0 |
| **Total** | **11** | **10** | **1** |

**All 10 open items are non-blocking for RC3 production deployment.** No CRITICAL or HIGH severity issues remain unresolved for plans that were implemented. The highest-severity deferred item is H1 (071 design) — but plan 071 is not started, so no production code is affected.

$END_ARTIFACT_CONTRACT
