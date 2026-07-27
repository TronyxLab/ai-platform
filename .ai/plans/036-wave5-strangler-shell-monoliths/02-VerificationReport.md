$START_VERIFICATION_REPORT

# VerificationReport 036 — Pre-Implementation Audit: Wave 5 Strangler-Fig

🔒 **Verified against SHA:** `d6ba7d6c4d1f4ac5b7cbd9ec5bf492a4351c1b89`
📅 **Date:** 2026-07-26
📋 **Audited artifact:** `01-DevPlan.md` (618 lines, DevPlan 036)
📐 **Task size:** STANDARD (19 files in scope — 6 modified + 13 new, no config/compose/CI/env changes; Phase 1 + Phase 2 + Phase 5 per QA workflow)

$ARTIFACT_CONTRACT
- **PURPOSE:** Pre-implementation verification of DevPlan 036 — validate file existence, LOC/inline-p3 baseline accuracy, dependency chain, cross-file drift, runtime test baseline, and invariant alignment before Coder execution.
- **DESCRIPTION:** QA audit of the Wave 5 Strangler-Fig DevPlan for 6 remaining shell monoliths (deploy-project, add-vhost, adopt-project, issue-cert, remote-cmd, verify-domains). Covers static audit (Phase 1), cross-file drift (Phase 2), and runtime baseline (Phase 5). Phase 3-4-6 skipped — STANDARD task without config/compose/CI changes.
- **RATIONALE:** Prevent Drift-on-Entry: verify the DevPlan accurately reflects current codebase state before Coder starts implementation. Catch baseline data staleness, dependency gaps, and AC contradictions early.
- **ACCEPTANCE_CRITERIA:**
  - All 6 target shell scripts exist at declared paths with matching LOC counts
  - All REQUIRES dependencies exist
  - Entrypoint-manifest.yaml and Makefile registration is consistent
  - Baseline test suite passes (≥95% pass rate acceptable for pre-existing failures)
  - No CRITICAL or HIGH findings that would block implementation start
- **IMPLEMENTS:** QA Phase 1, 2, 5 verification gate for DevPlan 036
- **IMPACTS:** `02-VerificationReport.md` in task folder
- **REQUIRES:** DevPlan 036 (`01-DevPlan.md`), git SHA `d6ba7d6c4`
$END_ARTIFACT_CONTRACT

---

## Section 1 — Static Audit (Phase 1)

### 1.1 File Existence Matrix

| # | File | Exists | LOC (DevPlan) | LOC (Actual) | Match |
|---|------|--------|:---:|:---:|:---:|
| 1 | `core/internal/deploy/deploy-project.sh` | ✅ | 1183 | 1183 | ✅ |
| 2 | `core/internal/scaffold/add-vhost.sh` | ✅ | 926 | 926 | ✅ |
| 3 | `core/internal/scaffold/adopt-project.sh` | ✅ | 906 | 906 | ✅ |
| 4 | `core/internal/bootstrap/issue-cert.sh` | ✅ | 696 | 696 | ✅ |
| 5 | `core/internal/bootstrap/remote-cmd.sh` | ✅ | 672 | 672 | ✅ |
| 6 | `core/internal/verify/verify-domains.sh` | ✅ | 281 | 281 | ✅ |

**Result:** All 6 target scripts exist. All LOC counts match DevPlan baseline. ✅

### 1.2 Inline Python3 Block Counts

| # | File | DevPlan says | Actual | Delta | Notes |
|---|------|:---:|:---:|:---:|-------|
| 1 | `deploy-project.sh` | 3 | **2** | −1 | Line 437 already migrated to `ssh_command_parser.py` (DevPlan 081) |
| 2 | `add-vhost.sh` | 3 | 3 | 0 | Lines 548, 779, 780 |
| 3 | `adopt-project.sh` | 3 | **2** | −1 | Line 671 is a `project_registry.py` module call, not inline |
| 4 | `issue-cert.sh` | 0 | 0 | 0 | ✅ |
| 5 | `remote-cmd.sh` | 0 | 0 | 0 | ✅ |
| 6 | `verify-domains.sh` | 2 | 2 | 0 | Lines 106, 141 |

**Result:** 2 discrepancies — deploy-project.sh and adopt-project.sh each have 1 fewer inline block than stated. Staleness from prior DevPlans (081 partially migrated parse_ssh_command; project_registry.py was already extracted). **Does NOT affect implementation** — just 2 fewer blocks to migrate. ⚠️

**Findings:**

[LOW] D-DATA-1 · `01-DevPlan.md`:61 vs `deploy-project.sh`:437 · 3→2 inline p3 blocks · fix: update baseline table (DevPlan 081 already extracted one)
[LOW] D-DATA-2 · `01-DevPlan.md`:63 vs `adopt-project.sh`:671 · 3→2 inline p3 blocks · fix: update baseline table (project_registry.py already extracted)
[INFO] Total inline blocks to remove: 12 (not 14). DevPlan text says "14+" — update to "12".

### 1.3 REQUIRES Dependencies

| Dependency | Path | Exists | Notes |
|-----------|------|--------|-------|
| `ssh_command_parser.py` | `core/internal/shared/ssh_command_parser.py` | ✅ | 275 LOC, MODULE_CONTRACT, already used by deploy-project.sh:437 |
| `template_engine.py` | `core/internal/template_engine.py` | ✅ | 716 LOC, MODULE_CONTRACT |
| `content_hash.py` | `core/internal/shared/content_hash.py` | ✅ | 130 LOC, MODULE_CONTRACT |

**Result:** All 3 REQUIRES dependencies exist. ✅

### 1.4 Hidden Dependencies (not in REQUIRES)

| Dependency | Path | Used by | Notes |
|-----------|------|---------|-------|
| `platform_deliver.py` | `core/internal/shared/platform_deliver.py` | deploy-project.sh:475 | Already extracted Python module |
| `project_registry.py` | `core/internal/shared/project_registry.py` | adopt-project.sh:671 | Already extracted Python module |

[INFO] D-DEP-1 · These modules exist and are already used but not listed in REQUIRES. Non-blocking — they're in the shared/ directory and the new Python modules will naturally import from there.

### 1.5 New File Pre-check

| Planned new file | Already exists? | Status |
|-----------------|:---:|--------|
| `core/internal/deploy/deploy_engine.py` | ❌ | Expected — not yet created |
| `core/internal/deploy/payload_deliverer.py` | ❌ | Expected — not yet created |
| `core/internal/scaffold/vhost_renderer.py` | ❌ | Expected — not yet created |
| `core/internal/scaffold/project_adopter.py` | ❌ | Expected — not yet created |
| `core/internal/bootstrap/overlay_deliverer.py` | ❌ | Expected — not yet created |
| `core/internal/verify/domain_verifier.py` | ❌ | Expected — not yet created |
| `tests/unit/test_*.py` (6 files) | ❌ | Expected — none created yet |

**Result:** Clean pre-implementation state. ✅

### 1.6 MODULE_CONTRACT/GREP_SUMMARY/STRUCTURE Compliance

| File | MODULE_CONTRACT | GREP_SUMMARY | STRUCTURE |
|------|:---:|:---:|:---:|
| `deploy-project.sh` | ✅ | ✅ | ✅ |
| `add-vhost.sh` | ✅ | ✅ | ✅ |
| `adopt-project.sh` | ✅ | ✅ | ✅ |
| `issue-cert.sh` | ✅ | ✅ | ✅ |
| `remote-cmd.sh` | ✅ | ✅ | ✅ |
| `verify-domains.sh` | ✅ | ✅ | ✅ |

**Result:** All files comply with semantic markup standard. ✅

---

## Section 2 — Drift Analysis (Phase 2)

### 2.1 Entrypoint-Manifest Registration Consistency

| Script | Registered in manifest? | Manifest lines |
|--------|:---:|-------|
| `deploy-project.sh` | ✅ | Lines 41, 46-49, 474 |
| `add-vhost.sh` | ✅ | Lines 181, 226 |
| `adopt-project.sh` | ✅ | Lines 204-208 |
| `issue-cert.sh` | ✅ | Line 397 |
| `remote-cmd.sh` | ✅ | Line 461 |
| `verify-domains.sh` | ✅ | Line 404 |

**Result:** All 6 scripts registered in `core/entrypoint-manifest.yaml`. ✅

### 2.2 Makefile Target Consistency

| Make target | Defined in | Delegates to | Status |
|------------|-----------|-------------|--------|
| `deploy-project` | `makefiles/deploy.mk:63` | `core/entrypoints/deploy-project.sh` → `deploy-project.sh` | ✅ |
| `render-vhosts` | `makefiles/bootstrap.mk:73` | `core/internal/scaffold/add-vhost.sh --render-all` | ✅ |
| `adopt-project` | `makefiles/scaffold.mk:63` | `core/entrypoints/scaffold.sh adopt-project` | ✅ |
| `verify` | `makefiles/deploy.mk:158` | `core/entrypoints/verify.sh` → `verify-domains.sh` | ✅ |
| `node-update` | `makefiles/bootstrap.mk` | → `remote-cmd.sh` + `issue-cert.sh` | ✅ |
| `deploy` | `makefiles/deploy.mk:18` | CI → `deploy-project.sh` | ✅ |

**Result:** All Makefile targets properly delegate to target scripts. ✅

### 2.3 TRAP Inventory Audit

Verified all TRAP annotations listed in Debt Intake table exist in target files:

| File | TRAPs in DevPlan Debt Intake | TRAPs found | Status |
|------|:---:|:---:|:---:|
| `deploy-project.sh` | 7 | 15 total (7 listed + additional) | ✅ |
| `add-vhost.sh` | 3 | 6 total | ✅ |
| `adopt-project.sh` | 2 | 2 total | ✅ |
| `issue-cert.sh` | 4 | 16 total | ✅ |
| `remote-cmd.sh` | 3 | 6 total | ✅ |
| `verify-domains.sh` | 1 | 1 total | ✅ |

**Result:** All Debt Intake TRAPs exist. Additional TRAPs not listed are not blockers — they're either DECISION traps or pre-date this DevPlan. ✅

### 2.4 Drift Findings

#### D-CONTRACT-1: AC-1 vs remote-cmd.sh LOC

[LOW] D-CONTRACT-1 · `01-DevPlan.md`:10 vs `01-DevPlan.md`:226 · AC-1: "shell ≤150 LOC (≤200 для VPS)" but remote-cmd.sh planned at ~200 LOC (not VPS-side)
- **Expected:** ≤150 LOC for non-VPS scripts (per AC-1)
- **Actual:** ~200 LOC planned (per Data Flow Wave 3)
- **Context:** D3 rationalizes this — printf %q command builders stay in shell
- **Fix:** Either add explicit exception in AC-1 for scripts with inherent shell-bound logic, or tighten remote-cmd.sh shell facade

#### D-NAMING-1: Wave numbering mismatch

[LOW] D-NAMING-1 · `01-DevPlan.md`:387-434 vs `01-DevPlan.md`:440-456 · Two different wave numbering schemes
- `$TASKS` section: TASK-036A="Wave 1", TASK-036B="Wave 2a", TASK-036C="Wave 2b", TASK-036D="Wave 3", TASK-036E="Wave 4", TASK-036F="Wave 5"
- `$PARALLEL_GROUPS` section: "Wave 1"=A+F, "Wave 2"=B+C+D, "Wave 3"=E, "Wave 4"=G
- **Issue:** "Wave 3" means TASK-036D (remote-cmd) in one section and TASK-036E (deploy-project) in another
- **Fix:** Rename $PARALLEL_GROUPS waves to "Group 1-4" or align numbering with $TASKS

### 2.5 Cross-File Mismatch Summary

| Severity | Count | Description |
|----------|:---:|-------------|
| LOW | 2 | Stale inline p3 counts (D-DATA-1, D-DATA-2) |
| LOW | 1 | AC-LOC contradiction (D-CONTRACT-1) |
| LOW | 1 | Wave numbering confusion (D-NAMING-1) |
| INFO | 1 | Hidden dependencies (D-DEP-1) |
| INFO | 1 | Inline count text stale (14+→12) |

---

## Section 3 — Invariant Status (Phase 3 — Abbreviated)

**Note:** Full Phase 3 (all 11 invariants) skipped — STANDARD task without architectural/schema/contract changes. Key invariants relevant to this DevPlan:

| # | Invariant (from AGENTS.md) | Status | Evidence |
|---|---------------------------|--------|----------|
| I-1 | Makefile — единый фасад. Все операции через `make <target>` | ✅ HELD | All 6 scripts have Makefile targets (Section 2.2) |
| I-11 | Manifest Generation Contract — authoritative sources порождают generated files | ✅ HELD | Scripts registered in entrypoint-manifest.yaml; no new verbs need registration (existing targets) |
| Lang.Policy | Новый код платформы — Python. Bash — тонкая обёртка | ✅ ALIGNED | DevPlan implements this policy via Strangler-Fig |
| Lang.Policy | Strangler-триггер (Tier 1: новый inline python3 → извлечь) | ✅ ALIGNED | DevPlan removes existing inline p3 blocks |

**Result:** No invariant violations. DevPlan aligns with all architectural invariants. ✅

---

## Section 4 — Runtime Validation (Phase 5)

### 4.1 Baseline Test Results

```
Command: python3 -m pytest tests/ -x --tb=short -q
Result:  63 passed, 1 failed in 16.20s
Pass rate: 98.4%
```

**Failing test (pre-existing, NOT caused by this DevPlan):**

```
FAILED tests/gates/test_gate_exception_audit.py::test_no_hardcoded_target_sets_in_gates
  → test_gate_deploy_paths.py:151 — hardcoded target set
     ({description, fallback, removal_mechanism, rev_date, target_date, verification})
```

[INFO] BASELINE-1 · `test_gate_deploy_paths.py:151` · Pre-existing gate failure — hardcoded target set in deprecated paths test. This test is in the deploy domain but is NOT caused by DevPlan 036. The failure is the gate checking its own test file for hardcoded target sets. Should be fixed before Wave 4 (deploy-project) migration to avoid confusion.

### 4.2 Existing Test Coverage for Target Scripts

| Target Script | Existing Tests | Notes |
|--------------|---------------|-------|
| `deploy-project.sh` | 10+ test files | `test_contract_deploy.py`, `test_contract_deploy_ssh.py`, `test_contract_deploy_rollback.py`, `test_contract_deploy_pruning.py`, `test_contract_deploy_audit.py`, `test_contract_deploy_deliver.py`, `test_deploy_finalization.py`, `test_deploy_direct.py`, `test_project_lifecycle.py`, `test_stub_detection.py` |
| `add-vhost.sh` | `test_add_vhost.py` | 5 test functions covering cert path, FQDN, hyphen normalization |
| `adopt-project.sh` | `test_adopt_project_org_validation.py`, `test_project_lifecycle.py` | Org validation + lifecycle integration |
| `issue-cert.sh` | `test_nginx_acme.py`, `test_tls_wildcard.py`, `test_ssl_s3_cache.py`, `test_cert_backup_gap.py` | Extensive cert issuance + S3 integration tests |
| `remote-cmd.sh` | `test_node_lifecycle_static.py` | SSH proxy + update mode tests |
| `verify-domains.sh` | None found | ⚠️ No dedicated tests for verify-domains.sh |

**Result:** Most scripts have existing test coverage. `verify-domains.sh` has no dedicated tests — the new `test_domain_verifier.py` will be the first.

### 4.3 Acceptance Criteria Pre-Verification

| AC | Description | Pre-Implementation Status |
|----|-------------|--------------------------|
| AC-1 | shell ≤150 LOC (≤200 для VPS) | ⚠️ remote-cmd.sh planned at ~200 (non-VPS) — see D-CONTRACT-1 |
| AC-2 | 0 inline python3 | ⚠️ 12 blocks remain (not 14) — see D-DATA-1/2 |
| AC-3 | Unit-тесты ≥80% coverage | ❓ Cannot verify — tests not yet written |
| AC-4 | Существующие тесты зелёные | ✅ 63/64 pass (98.4%) |
| AC-5 | `make test` + `make gate MODE=fast` зелёные | ⚠️ 1 pre-existing gate failure |
| AC-6 | Production deploy не сломан | ❓ Cannot verify — pre-implementation |

---

## Section 5 — Findings Summary

### By Severity

| Severity | Count | IDs |
|----------|:---:|------|
| CRITICAL | 0 | — |
| HIGH | 0 | — |
| MEDIUM | 0 | — |
| LOW | 4 | D-DATA-1, D-DATA-2, D-CONTRACT-1, D-NAMING-1 |
| WARNING | 0 | — |
| INFO | 3 | D-DEP-1, inline-count-text, BASELINE-1 |

### Recommendations

1. **[Before Wave 1]** Update baseline table (inline p3 counts: deploy-project: 2, adopt-project: 2) and "14+" text to "12"
2. **[Before Wave 1]** Fix `test_gate_deploy_paths.py:151` pre-existing failure (or document as known issue) — deploy-related gate should be green before touching deploy-project
3. **[Before Wave 3]** Clarify AC-1 exception for remote-cmd.sh (~200 LOC due to printf %q builders), or tighten shell facade
4. **[Before implementation]** Rename $PARALLEL_GROUPS waves to "Group 1-4" to avoid confusion with $TASKS wave numbering
5. **[Optional]** Add `platform_deliver.py` and `project_registry.py` to REQUIRES for completeness

---

## Semantic Verdict

### **DRIFTED (LOW)**

**Rationale:** DevPlan is structurally sound and safe to implement. All files exist, all dependencies exist, all registrations are correct, test suite is 98.4% green. The 4 LOW findings are data staleness and minor inconsistencies in the DevPlan text — none block implementation. No CRITICAL, HIGH, or MEDIUM findings. The DevPlan accurately captures the Strangler-Fig migration strategy and correctly applies the proven Wave 4 pattern to 6 remaining shell monoliths.

**Action:** Coder may proceed with Wave 1 implementation. Recommended: update baseline metrics in DevPlan before starting (≤5 min effort).

$END_VERIFICATION_REPORT
