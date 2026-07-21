# VerificationReport.md — Makefile Include-Split (W4-E4)

<!-- $START_VERIFICATION_REPORT -->
## $ARTIFACT_CONTRACT
- **PURPOSE:** Semantic QA verification of uncommitted changes splitting root Makefile (747 LOC) into 6 thematic `makefiles/*.mk` includes.
- **DESCRIPTION:** W4-E4 Makefile include-split — 747→41 LOC root, 6 .mk files + new gate test. Modified test files with W4-E5 edge-case regression baselines.
- **RATIONALE:** Prevent target loss, recipe drift, tab-violation regression, and gate test incompatibility after structural Makefile change.
- **ACCEPTANCE_CRITERIA:**
  - AC-1: No target loss — 45 .PHONY targets preserved from old Makefile
  - AC-2: All 6 .mk files follow semantic markup (GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT)
  - AC-3: `make -n <target>` exits 0 for all simple targets
  - AC-4: Recipe lines in .mk files use TAB (not spaces)
  - AC-5: Root Makefile < 150 lines (AC-5b: ✓ 41 LOC)
  - AC-6: Gate tests pass (or have documented, non-blocking failures)
  - AC-7: No config drift between entrypoint-manifest.yaml and .PHONY targets
- **IMPLEMENTS:** N/A (verification-only artifact)
- **IMPACTS:** tests/gates/test_gate_makefile_targets.py, tests/gates/test_gate_manifest_integrity.py, tests/gates/test_gate_ci_coverage.py, tests/gates/test_gate_contract.py, tests/gates/test_gate_sequencing.py, tests/gates/test_gate_workflow_consistency.py, tests/gates/test_gate_exception_audit.py
- **REQUIRES:** N/A

## 🔒 Verified against SHA 6d74817ee8e37bc994c02eb0e5ba72550a431dfc

## Scope
- **Modified:** `Makefile`, `core/entrypoint-manifest.yaml`, `tests/test_bootstrap_auto.py`, `tests/test_converge_exit.py`, `tests/test_deploy_modules.py`, `tests/test_node_lifecycle_static.py`
- **New:** `makefiles/bootstrap.mk`, `makefiles/deploy.mk`, `makefiles/scaffold.mk`, `makefiles/modules.mk`, `makefiles/ci.mk`, `makefiles/helpers.mk`, `tests/gates/test_gate_makefile_targets.py`
- **Task size:** STANDARD (14 files, config/compose referenced)

---

## Section 1 — Static Audit (Phase 1)

### Compliance Matrix

| File | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | #region paired | Doxygen @tags | LDD IMP:7-10 | Bare except | Secrets |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Makefile | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| makefiles/bootstrap.mk | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| makefiles/deploy.mk | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| makefiles/scaffold.mk | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| makefiles/modules.mk | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| makefiles/ci.mk | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| makefiles/helpers.mk | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| tests/gates/test_gate_makefile_targets.py | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |

### Findings

| # | Severity | File:Line | Issue |
|---|----------|-----------|-------|
| F1 | INFO | Makefile:1 | Root Makefile has no `.PHONY:` declaration — targets are declared in included .mk files only. This is intentional and correct for include-split pattern. |
| F2 | INFO | helpers.mk:37 | TRAP[BUG] for PLATFORM_DOMAIN env-chain preserved from original Makefile. ✓ |

### Summary
- **CRITICAL:** 0
- **HIGH:** 0
- **MEDIUM:** 0
- **LOW:** 0
- **WARNING:** 0
- **INFO:** 2

---

## Section 2 — Drift Analysis (Phase 2)

### Target Preservation Check

| Category | Old Makefile (.PHONY) | New includes (.PHONY) | Delta |
|----------|----------------------|----------------------|-------|
| bootstrap | bootstrap-node, node-update, converge, render-vhosts (4) | bootstrap.mk: 4 | ✓ identical |
| deploy | deploy, deploy-project, context-promote, hermes-build-platform, hermes-build-context, hermes-push-l1, verify (7) | deploy.mk: 7 | ✓ identical |
| scaffold | new-project, new-context, project-sync-env, remove-project, adopt-project, project-list, project-status (7) | scaffold.mk: 7 | ✓ identical |
| modules | up, down, restart, status, healthcheck, backup, restore, discover-modules, validate-modules (9) | modules.mk: 9 | ✓ identical |
| ci | test, gate, validate, lint, check-file-lines, pre-commit-*, scripts-audit, audit, secrets-unlock (10) | ci.mk: 10 | ✓ identical |
| helpers | venv, templates-check, templates-render, dev-certs, provision, test-inventory-sync, help, _get_all_profiles (8) | helpers.mk: 8 | ✓ identical |
| **Total** | **45** | **45** | **✓ ZERO LOSS** |

### Drift Register

| DRIFT-ID | Severity | Type | Files | Detail |
|----------|----------|------|-------|--------|
| DRIFT-1 | WARNING | MANIFEST_PARITY | entrypoint-manifest.yaml §allowed_verbs | 4 .PHONY targets not in allowed_verbs: `venv`, `pre-commit-install`, `pre-commit-run`, `help`. **Pre-existing** — same 4 were missing before split. |
| DRIFT-2 | HIGH | GATE_INCOMPAT | tests/gates/ (6 gate tests) | 6 gate tests grep root Makefile for target definitions that now live in includes. See Section 5. |
| DRIFT-3 | MEDIUM | HARDCODED_TARGETS | tests/gates/test_gate_makefile_targets.py:185,226,234 | New gate test has 3 hardcoded target sets (45-target `expected` set, 4-target NODE-required set, 3-target NAME-required set). G1.2 requires reading from entrypoint-manifest.yaml. |

### Cross-File Value Integrity

| Value | Files | Status |
|-------|-------|--------|
| COMPOSE_PROFILES (13 modules) | Makefile:30, helpers.mk:79 (_get_all_profiles) | ✓ Identical |
| _platform_root | Makefile:25, all .mk files | ✓ Propagated via root |
| VENV/PYTHON/PIP | Makefile:20-22, helpers.mk:13-16, ci.mk | ✓ Propagated |
| converge.sh path | bootstrap.mk:63 vs old Makefile:717 | ✓ Identical (`bash core/entrypoints/converge.sh`) |
| render-vhosts NODE_CONFIGS_DIR | bootstrap.mk:73 vs old Makefile:733 | ✓ Identical (pre-existing undefined var) |

### Summary
- **CRITICAL drifts:** 0
- **HIGH drifts:** 1 (DRIFT-2: 6 gate tests incompatible with include-split)
- **MEDIUM drifts:** 1 (DRIFT-3: hardcoded target sets in new gate test)
- **WARNING drifts:** 1 (DRIFT-1: pre-existing manifest gap)

---

## Section 3 — Invariant Status (Phase 3)

N/A — STANDARD task (architectural invariants not in scope). No constitution-level changes.

---

## Section 4 — Test Quality (Phase 4)

N/A — STANDARD task (deep test audit only for LARGE/PERIODIC). Key observations:

| Finding | Severity | Detail |
|---------|----------|--------|
| TQ-1 | HIGH | `tests/test_deploy_modules.py:934` — NameError: variable `l` referenced but iteration variable is `line`. Line should be `[line for line in stdout.splitlines() if line.startswith("RESULT:")]` |
| TQ-2 | WARNING | `tests/test_converge_exit.py:525` — SyntaxWarning: invalid escape sequence `\.` |
| TQ-3 | INFO | `tests/gates/test_gate_makefile_targets.py:267` — legitimate skip on macOS (GNU Make 3.81 limitation with `$(eval ...)`). Verified on CI (Ubuntu, GNU Make 4.x). |

---

## Section 5 — Runtime Validation (Phase 5)

### New Gate Test Results — `test_gate_makefile_targets.py`

```
7 passed, 1 skipped in 0.62s
```

| Test | Result | Note |
|------|--------|------|
| test_root_makefile_line_limit | PASS | 41 lines ✓ (AC-5b: < 150) |
| test_makefiles_directory_exists | PASS | 6 .mk files present ✓ |
| test_all_phony_targets_discovered | PASS | All 45 targets found ✓ |
| test_make_n_for_simple_targets | PASS | 43 targets `make -n` exit 0 ✓ |
| test_make_n_for_complex_targets | SKIPPED | Known macOS GNU Make 3.81 limitation |
| test_recipe_lines_use_tabs | PASS | No space-indented recipes ✓ |
| test_include_directives_in_root | PASS | All 6 includes present ✓ |
| test_dot_default_goal_is_help | PASS | `.DEFAULT_GOAL := help` ✓ |

### W4-E5 Edge-Case Tests Results

```
11 passed, 1 failed in 0.46s
```

| Test | Result |
|------|--------|
| test_resolve_node_yaml_multi_path_search | PASS |
| test_resolve_node_yaml_empty_name_fails_fast | PASS |
| test_drift_detection_r_units | PASS |
| test_reconcile_idempotency | PASS |
| test_batch_sudoers | PASS |
| test_parallel_deploy_failure_isolates_modules | **FAIL** (NameError bug, see TQ-1) |
| test_orphan_reconciliation_marks_foreign | PASS |
| test_batch_sudoers_determinism | PASS |
| test_expand_transitive_deps_cycle_terminates | PASS |
| test_parse_modules_from_node_yaml_edge_cases | PASS |
| test_mode_dispatch_init_update | PASS |
| test_checkpoint_step_uses_content_hash | PASS |

### Full Gate Suite Results

```
8 failed, 188 passed, 14 skipped, 29 deselected in 25.13s
```

#### Gate Failures — Root Cause Analysis

All 8 failures share the same root cause: **gate tests grep only the root Makefile** (`_MAKEFILE_PATH`), but targets now live in included `makefiles/*.mk` files.

| # | Test File | Test | Root Cause |
|---|-----------|------|------------|
| G1 | test_gate_ci_coverage.py | test_mode_fast_excludes_requires_docker | Regex for `MODE=fast` section only scans root Makefile. Target is now in `ci.mk`. |
| G2 | test_gate_ci_coverage.py | test_marker_all_includes_contract | Regex for `MARKER=all` section only scans root Makefile. Target is now in `ci.mk`. |
| G3 | test_gate_contract.py | test_contract_target_exists | Grep for `test:` target only scans root Makefile. Target is now in `ci.mk`. |
| G4 | test_gate_exception_audit.py | test_no_hardcoded_target_sets_in_gates | New gate test has 3 hardcoded target sets (DRIFT-3). |
| G5 | test_gate_manifest_integrity.py | test_allowed_verbs_match_makefile | Target parser extracts `.DEFAULT_GOAL` as if it were a target (false positive from root Makefile line 41). |
| G6 | test_gate_manifest_integrity.py | test_no_forbidden_verbs_in_makefiles | Same `.DEFAULT_GOAL` false positive (UNKNOWN_VERB). |
| G7 | test_gate_sequencing.py | test_gate_makefile_deploy_node_flag | Grep for `deploy` target NODE check only scans root Makefile. Target is now in `deploy.mk`. |
| G8 | test_gate_workflow_consistency.py | test_core_deploy_auto_detects_node | Grep for `bootstrap-node` NODE handling only scans root Makefile. Target is now in `bootstrap.mk`. |

### LDD Trace Analysis

- New gate test: IMP:9 logs present in all passing tests ✓
- W4-E5 edge-case tests: IMP:9 logs present in all passing tests ✓
- Anti-illusion: No false positives — all failing tests have real root causes identified

### Acceptance Criteria Verification

| AC | Status | Evidence |
|----|--------|----------|
| AC-1: No target loss | **PASS** | 45 old = 45 new, verified by `test_all_phony_targets_discovered` |
| AC-2: Semantic markup | **PASS** | All 6 .mk files + root have GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT |
| AC-3: `make -n` works | **PASS** | `test_make_n_for_simple_targets`: 43/43 pass, 2 skipped (known macOS limitation) |
| AC-4: TAB in recipes | **PASS** | `test_recipe_lines_use_tabs`: zero space-indented recipes |
| AC-5: Root < 150 LOC | **PASS** | `test_root_makefile_line_limit`: 41 lines |
| AC-6: Gate tests | **DEGRADED** | 8 gate failures — all pre-existing tests that need include-following update |
| AC-7: No config drift | **PASS** | No new drift introduced (DRIFT-1 pre-existing) |

---

## Section 6 — Config Sync Audit (Phase 6)

### Env Variable Propagation Chain

Not in scope — no .env/CI workflow/env changes in this diff.

### Compose Override Consistency

Not in scope — no compose file changes.

### Manifest Parity

| Check | Status |
|-------|--------|
| entrypoint-manifest `allowed_verbs` completeness | WARNING — 4 verbs missing (pre-existing) |
| Gate `makefile-targets` registered in manifest | ✓ PASS (entrypoint-manifest.yaml:512-515) |
| Gate test file exists at `tests/gates/test_gate_makefile_targets.py` | ✓ PASS |
| Gate test has `@pytest.mark.gate` | ✓ PASS (class-level decorator) |

### Network/Volume Consistency

Not in scope — no network/volume changes.

---

## Semantic Verdict

**Verdict: DEGRADED (MEDIUM)**

**Justification:** The Makefile split itself is structurally correct — zero target loss, all semantic markup present, TAB enforcement verified, root Makefile well under 150 LOC. However, 8 gate tests are incompatible with the new structure because they grep only the root Makefile instead of following `include` directives. Additionally, the new gate test violates G1.2 (hardcoded target sets), and one W4-E5 edge-case test has a code bug (NameError).

### Blocking Issues
- **G1-G3, G7-G8** (6 gate tests): Must update to follow `makefiles/*.mk` includes. These tests grep for target definitions that now live in included files.
- **TQ-1** (test_deploy_modules.py:934): NameError bug — `l` vs `line` variable mismatch.

### Non-Blocking Issues
- **G4** (DRIFT-3): Hardcoded target sets in new gate test. Should read from entrypoint-manifest.yaml per G1.2.
- **G5-G6**: `.DEFAULT_GOAL` false positive in manifest integrity gate — parser bug exposed by new root Makefile content.
- **DRIFT-1**: 4 utility targets missing from allowed_verbs (pre-existing).

### Recommendations

1. **Fix 6 gate tests** to follow `include` directives:
   - `test_gate_ci_coverage.py`: `test_mode_fast_excludes_requires_docker`, `test_marker_all_includes_contract`
   - `test_gate_contract.py`: `test_contract_target_exists`
   - `test_gate_sequencing.py`: `test_gate_makefile_deploy_node_flag`
   - `test_gate_workflow_consistency.py`: `test_core_deploy_auto_detects_node`
   - Likely approach: extend `_read_included_contents` / `_get_all_targets` pattern already used by `test_gate_manifest_integrity.py`

2. **Fix NameError** in `test_deploy_modules.py:934` — `l` → `line`

3. **Fix G1.2 violation** in `test_gate_makefile_targets.py` — replace hardcoded `expected` set with entrypoint-manifest.yaml read

4. **Fix `.DEFAULT_GOAL` false positive** in `test_gate_manifest_integrity.py` target parser — add `.DEFAULT_GOAL` to exclusion list (it's a special make variable, not a target)

<!-- $END_VERIFICATION_REPORT -->
