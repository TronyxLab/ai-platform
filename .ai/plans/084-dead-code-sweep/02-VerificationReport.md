$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Verification of DevPlan 084 — Dead Code Sweep
DESCRIPTION:           Plan self-consistency, dead-code verification (actually unused?), implementation status, prerequisites check
RATIONALE:             Ensure DevPlan is actionable, complete, and free of drift before implementation
ACCEPTANCE_CRITERIA:   All referenced files exist, dead-code claims verified, ACs are measurable, prerequisites satisfied, plan inaccuracies identified
IMPLEMENTS:            DevPlan:.ai/plans/084-dead-code-sweep/01-DevPlan.md
IMPACTS:               core/modules/nginx/install.sh, core/internal/bootstrap/ssl-provision.sh, .env.example, Makefile, tests/gates/test_gate_dead_code.py, tests/gates/test_gate_no_unregistered_entrypoint.py, core/internal/scripts-audit.sh, core/internal/bootstrap/node-lifecycle.sh
REQUIRES:              080-cert-unification (cert functions migrated to cert_orchestrator.py), 071-done-migration (.done files deprecated)
$END_ARTIFACT_CONTRACT

---

# Verification Report: DevPlan 084 — Dead Code Sweep

**Date:** 2026-07-25
**SHA:** `d37326afc64e505bb69f230465e83f9f5bef0d8a`
**Verifier:** QA (Kilo)

---

## Final Verdict: **PARTIAL** — Plan is actionable but contains 1 factual inaccuracy in §2.1 dependency analysis

The DevPlan 084 is well-structured with clear tasks, acceptance criteria, and parallel wave groups. All referenced files exist and the dead code claims are verified. However, **§2.1 claims `ssl-provision.sh` is sourced by `node-lifecycle.sh` for WEBNAMES_API_KEY loading — this migration has already occurred in the current codebase.** The `update_step_3_ssl_provision()` function now sources `$secrets_env` directly (line 84), not `ssl-provision.sh`. This makes T2 (Wave 2) partially redundant — the key-loading migration step can be skipped, simplifying the deletion of `ssl-provision.sh` to a straightforward file removal + reference cleanup.

**Implementation status: NOT STARTED.** All 11 files from the File Manifest are in their pre-implementation state.

---

## 1. Plan Self-Consistency Audit

| Check | Status | Detail |
|-------|--------|--------|
| **$ARTIFACT_CONTRACT** | ✅ PASS | All 7 fields present, well-formed |
| **AC measurability** | ✅ PASS | All 7 ACs have concrete verification commands |
| **File Manifest completeness** | ✅ PASS | 2 delete + 7 modify + 2 create = 11 files, all accounted |
| **Task dependency graph** | ✅ PASS | Critical path correctly identified: T4 ∥ (T1→T2→T3) ∥ (T5→T6) ∥ T7 → T8 |
| **PARALLEL_GROUPS** | ✅ PASS | 4 waves with no file intersections within Wave 1 |
| **$TASKS table** | ✅ PASS | All 8 tasks have ID, file count, complexity, deps, acceptance |
| **§1.2 Key Success Criteria** | ✅ PASS | 5 SCs map to 7 ACs with no gaps |
| **§2.1 Dependency Analysis accuracy** | ⚠️ WARNING | See Finding #1 — WEBNAMES_API_KEY claim is outdated |
| **§2.3 ssl-provision.sh analysis** | ⚠️ WARNING | Related to Finding #1 — analysis was correct at writing time but code has since changed |
| **§9 TEST_SPEC** | ✅ PASS | 6 test functions defined, correctly split between 2 test files |
| **§10 TRAP References** | ✅ PASS | 2 TRAPs marked RESOLVED, 1 DEPRECATED marker tracked |

---

## 2. Dead Code Verification (actually unused?)

### 2.1 nginx/install.sh (1107 LOC)

| Attribute | Value |
|-----------|-------|
| **Exists on disk** | ✅ YES — `core/modules/nginx/install.sh` |
| **DEPRECATED marker** | ✅ Line 25: `# ⚠️ DEPRECATED — install.sh is NOT called for docker-type nginx` |
| **Direct callers** | 0 — deploy-modules.sh guard checks `install_type: docker` → skips |
| **Indirect references** | 2 in `core/templates/template-manifest.yaml` (L52, L62 — consumers for template snippets) |
| **Verdict** | **GENUINELY DEAD** — nginx is `install_type: docker` in `module.yaml`; deploy-modules.sh only calls `install.sh` for `system` modules. Template-manifest references are consumer annotations that need cleanup (DevPlan T1 already handles this). |

### 2.2 ssl-provision.sh (40 LOC)

| Attribute | Value |
|-----------|-------|
| **Exists on disk** | ✅ YES — `core/internal/bootstrap/ssl-provision.sh` |
| **Content** | Thin backward-compat wrapper: sources `install-acme.sh` + `issue-cert.sh` |
| **Active references (file-level)** | `scripts-audit.sh` L43 (whitelist), `test_gate_dead_code.py` L98 (exception), `test_gate_no_unregistered_entrypoint.py` L67 (exception) |
| **String-level references (log labels)** | `node-lifecycle.sh` L85, L86, L220 — checkpoint name strings only, NOT file `source`/`exec` calls |
| **Comment references** | `issue-cert.sh` (5 lines), `install-acme.sh` (5 lines), `steps.py` L46, `test_node_lifecycle_static.py` L399, `nginx/install.sh` L29, L34 |
| **KEY FINDING** | `node-lifecycle.sh:update_step_3_ssl_provision()` sources `$secrets_env` directly (L84), NOT `ssl-provision.sh`. WEBNAMES_API_KEY loading was previously migrated. See §2.1 inaccuracy below. |
| **Verdict** | **GENUINELY DEAD (as a file)** — no code sources or executes it. All "references" are string identifiers or whitelist/exception entries. Deletion requires only removing the file + cleaning up exception lists + updating comments. |

### 2.3 LITELLM_METRICS_TOKEN

| Attribute | Value |
|-----------|-------|
| **Present in .env.example** | ✅ YES — Line 129: `LITELLM_METRICS_TOKEN=` |
| **Consumers** | 0 — `monitoring/docker-compose.base.yml` L39 uses `LITELLM_MASTER_KEY` (migration completed per v3 fix) |
| **secret-definitions.yaml** | NOT present (already removed per DevPlan 072) |
| **Verdict** | **GENUINELY DEAD** — 0 consumers, already unified to `LITELLM_MASTER_KEY`, safe to remove |

### 2.4 DEPRECATED Markers

| File | Line | Content | Status |
|------|------|---------|--------|
| `core/modules/nginx/install.sh` | 25 | `# ⚠️ DEPRECATED — install.sh is NOT called for docker-type nginx` | True DEPRECATED — removed by T1 deletion |
| `core/internal/bootstrap/scp-deliver.sh` | 84 | `echo "[IMP:8]...DEPRECATED: prepare_ssh_opts()..."` | Log message, not a marker — see Finding #3 |

**Note:** No `.py` files contain DEPRECATED markers. Both hits are in `.sh` files.

---

## 3. Implementation Status

| Task | Description | Status | Evidence |
|------|-------------|--------|----------|
| **T1** | Delete `nginx/install.sh` | ❌ NOT DONE | File exists: `ls core/modules/nginx/install.sh` → 1107 LOC |
| **T2** | Delete `ssl-provision.sh` | ❌ NOT DONE | File exists: `ls core/internal/bootstrap/ssl-provision.sh` → 40 LOC |
| **T3** | Update all ssl-provision references | ❌ NOT DONE | `scripts-audit.sh` L43 still whitelists; test exceptions still present |
| **T4** | Remove `LITELLM_METRICS_TOKEN` | ❌ NOT DONE | `.env.example` L129: `LITELLM_METRICS_TOKEN=` |
| **T5** | Create `check-dead-code.sh` | ❌ NOT DONE | File does not exist: `core/entrypoints/check-dead-code.sh` |
| **T6** | Add `make check-dead-code` to Makefile + CI | ❌ NOT DONE | `grep check-dead-code Makefile` → no matches; CI workflows: no matches |
| **T7** | Verify remaining DEPRECATED markers | ❌ NOT DONE | 2 DEPRECATED hits remain in core/ (see §2.4) |
| **T8** | Run full gate | ❌ NOT DONE | Blocked by T1-T7 |

**Overall: 0/8 tasks implemented. DevPlan is in pre-implementation state.**

---

## 4. Prerequisites Check

| Prerequisite | Required by | Status |
|-------------|-------------|--------|
| **080-cert-unification** | T1, T2 | ✅ DONE — `cert_orchestrator.py` is canonical; `nginx/install.sh` cert functions are divergent copies |
| **071-done-migration** | .done cleanup | ✅ DONE — `state_machine.py` uses `state.json`; `.done` checkpoint mechanism is legacy backup |

No blocking prerequisites. Both dependencies are satisfied.

---

## 5. Cross-Reference Integrity

| Cross-ref | Expected | Actual | Status |
|-----------|----------|--------|--------|
| **DevPlan → nginx/install.sh** | DEPRECATED at L25 | ✅ Confirmed L25 | MATCH |
| **DevPlan → ssl-provision.sh** | 40 LOC wrapper | ✅ Confirmed 40 lines | MATCH |
| **DevPlan → .env.example L129** | LITELLM_METRICS_TOKEN | ✅ Confirmed L129 | MATCH |
| **DevPlan → template-manifest.yaml** | 2 consumer refs | ✅ Confirmed L52, L62 | MATCH |
| **DevPlan → scripts-audit.sh L43** | Whitelist entry | ✅ Confirmed L43 | MATCH |
| **DevPlan → test_gate_dead_code.py L98** | Exception entry | ✅ Confirmed L98 | MATCH |
| **DevPlan → test_gate_no_unregistered_entrypoint.py L67** | Exception entry | ✅ Confirmed L67 | MATCH |
| **DevPlan → node-lifecycle.sh L85-86** | WEBNAMES_API_KEY source from ssl-provision.sh | ❌ Sources `$secrets_env` directly | **MISMATCH** (Finding #1) |
| **DevPlan → scp-deliver.sh** | DEPRECATED marker | ✅ Confirmed L84 (echo message) | MATCH |
| **DevPlan §9 → test_gate_dead_code.py** | 5 test functions | 2 test functions (existing gate tests only) | NOT YET WRITTEN |
| **DevPlan §9 → test_gate_no_unregistered_entrypoint.py** | 1 test function | 3 existing gate tests (new one not added) | NOT YET WRITTEN |

---

## 6. Findings

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 1 | **HIGH** | **Plan drift: §2.1 ssl-provision.sh dependency claim is outdated.** DevPlan states `node-lifecycle.sh` L85-86 sources `ssl-provision.sh` for WEBNAMES_API_KEY loading. Actual code: `update_step_3_ssl_provision()` sources `$secrets_env` directly (L84: `source "$secrets_env"`), NOT `ssl-provision.sh`. The migration described in T2 has already happened. | Simplify T2: skip the "move WEBNAMES_API_KEY loading" step. Proceed directly to deleting `ssl-provision.sh` + cleaning up whitelist/exception/test references. The T2 description should be updated to reflect current state. |
| 2 | **MEDIUM** | **nginx/install.sh has 2 references in template-manifest.yaml (L52, L62) as template consumers.** DevPlan T1 already accounts for cleaning these, but the grep results confirm they exist and must be removed. | Execute T1 as planned — delete file + remove consumer entries from template-manifest.yaml. Verify with `grep -r "nginx/install" core/templates/` → 0 hits after deletion. |
| 3 | **LOW** | **scp-deliver.sh L84: DEPRECATED in echo message, not a structural marker.** The line `echo "[IMP:8][bootstrap][ssh] DEPRECATED: prepare_ssh_opts()..."` is a runtime log message, not a code-level DEPRECATED annotation. The function `prepare_ssh_opts()` still exists and is called. | Decide in T7: either (a) remove the function if truly unused, or (b) remove the word "DEPRECATED" from the echo message since it's misleading — the function is still operational as a backward-compat path. |
| 4 | **INFO** | **test_gate_dead_code.py §9 TEST_SPEC defines 5 new tests not yet written.** Current file has only `test_all_internal_scripts_reachable` and `test_all_entrypoints_have_live_caller` (existing gate tests). The 5 new tests from §9 must be added during T8. | Implement DevPlan §9 test functions during Wave 3-4 implementation: `test_no_deprecated_markers_stale`, `test_nginx_install_sh_deleted`, `test_ssl_provision_sh_deleted`, `test_litellm_metrics_token_removed`, `test_no_ssl_provision_references`. Update `test_gate_no_unregistered_entrypoint.py` with `test_no_ssl_provision_exception`. |
| 5 | **INFO** | **check-dead-code.sh and Makefile target don't exist.** Both are new creations (T5, T6). | Implement as planned in Wave 1 (T5) and Wave 3 (T6). The `check-dead-code.sh` should use `git log` to determine DEPRECATED marker age (not just grep). |
| 6 | **INFO** | **All 5 existing gate tests pass** (`test_gate_dead_code.py`: 2 tests, `test_gate_no_unregistered_entrypoint.py`: 3 tests). Runtime validation confirms no pre-existing gate failures. | Baseline is clean. After DevPlan implementation, re-run `make gate MODE=fast` to verify AC7. |

---

## 7. Test Results

```
$ python3 -m pytest tests/gates/test_gate_dead_code.py tests/gates/test_gate_no_unregistered_entrypoint.py -s -v

tests/gates/test_gate_dead_code.py::test_all_internal_scripts_reachable PASSED
tests/gates/test_gate_dead_code.py::test_all_entrypoints_have_live_caller PASSED
tests/gates/test_gate_no_unregistered_entrypoint.py::test_no_unregistered_scripts_in_forbidden_dirs PASSED
tests/gates/test_gate_no_unregistered_entrypoint.py::test_all_registered_scripts_exist PASSED
tests/gates/test_gate_no_unregistered_entrypoint.py::test_no_unregistered_entrypoints PASSED

============================== 5 passed in 0.40s ===============================
```

**Note:** `ssl-provision.sh` is whitelisted in both test files (exceptions), so its presence does not cause test failures. After deletion, these exceptions must be removed — otherwise the tests would falsely report the script as "missing from disk" (in `test_all_registered_scripts_exist`).

---

## 8. Recommended Wave Execution Order (Updated)

Based on Finding #1 (WEBNAMES_API_KEY already migrated), the updated execution order simplifies:

1. **Wave 1 (unchanged):** T1 (delete nginx/install.sh), T4 (remove LITELLM_METRICS_TOKEN), T5 (create check-dead-code.sh), T7 (verify DEPRECATED markers)
2. **Wave 2 (SIMPLIFIED):** T2 (delete ssl-provision.sh — **skip** the key-loading migration, it's already done). Just `git rm` the file.
3. **Wave 3 (unchanged):** T3 (clean up references + test exceptions), T6 (add Makefile target)
4. **Wave 4 (unchanged):** T8 (full gate verification)

---

## 9. Summary

| Metric | Value |
|--------|-------|
| **Plan ACs** | 7 — all measurable, all verifiable |
| **Tasks not started** | 8/8 (0% complete) |
| **Dead files confirmed** | 2 — `nginx/install.sh` (1107 LOC), `ssl-provision.sh` (40 LOC) |
| **Dead env vars confirmed** | 1 — `LITELLM_METRICS_TOKEN` |
| **DEPRECATED markers** | 2 (1 true, 1 echo-message) |
| **Plan inaccuracies found** | 1 (HIGH) — §2.1 outdated dependency claim |
| **Prerequisites met** | 2/2 — 080-cert-unification ✅, 071-done-migration ✅ |
| **Existing tests passing** | 5/5 (100%) |
| **Overall readiness** | **READY WITH 1 CORRECTION** — update §2.1 to reflect that WEBNAMES_API_KEY loading was already migrated |

$END_VERIFICATION_REPORT
