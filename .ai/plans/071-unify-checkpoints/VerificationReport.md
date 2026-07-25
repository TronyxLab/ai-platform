$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Verification of DevPlan 071 — Unify Checkpoints
DESCRIPTION:           Plan self-consistency, implementation status, cross-reference audit, and drift detection for checkpoint unification (shell .done → state.json)
RATIONALE:             Ensure DevPlan is actionable, complete, and free of design-level drift before implementation begins
ACCEPTANCE_CRITERIA:   All referenced files exist, ACs are measurable, no circular dependencies, step-name alignment verified between shell and Python
IMPLEMENTS:            DevPlan:.ai/plans/071-unify-checkpoints/
IMPACTS:               core/lib/checkpoint.sh (REWRITE), core/internal/bootstrap/node-lifecycle.sh (MODIFY), core/internal/bootstrap/lifecycle/state_machine.py (VERIFY), core/internal/bootstrap/content-hash.sh (VERIFY), core/internal/bootstrap/AGENTS.md (UPDATE), tests/unit/test_state_machine.py (EXTEND)
REQUIRES:              state_machine.py must be able to read state.json written by shell
$END_ARTIFACT_CONTRACT

---

# Verification Report: DevPlan 071 — Unify Checkpoints

**Date:** 2026-07-25
**SHA:** `d37326afc64e505bb69f230465e83f9f5bef0d8a`

---

## Final Verdict: **DRIFTED (CRITICAL)**

The plan has a **critical design flaw**: shell and Python track different step inventories. Shell writes steps 1-12 (keys 1-12 aligned) but then steps 13-16 diverge from Python's INIT_STEPS, causing `ensure_secrets` and `secrets_init` to be incorrectly skipped on resume. The DevPlan's assertion that "numeric keys will align" is false — the alignment breaks after step 12 because the shell doesn't call checkpoint for Python-only steps (`ensure_secrets`, `secrets_init`).

Additionally, the plan introduces large inline `python3 -c "..."` blocks (violating the language policy's Tier 1 Strangler trigger) and has a documentation drift between the brief and expanded versions regarding `paths.sh`.

---

## 1. Plan Self-Consistency Audit

| Check | Status | Evidence |
|-------|--------|----------|
| All File Manifest paths exist | ✅ PASS | 6/6 files confirmed present |
| Acceptance criteria are measurable | ✅ PASS | 9/9 ACs have verifiable outputs |
| No circular dependencies | ✅ PASS | checkpoint.sh → paths.sh → node-lifecycle.sh; no cycle |
| Hash approach documented | ✅ PASS | python3 vs jq rationale provided |
| Rollback procedure present | ✅ PASS | 6-step rollback documented |
| Wave dependencies correct | ✅ PASS | Wave 1 (checkpoint.sh + tests + docs) independent; Wave 2 (node-lifecycle.sh) depends on Wave 1 |

### Step-name alignment: CRITICAL drift

| Shell step name | Shell key (est.) | Python step name | Python index | Alignment |
|-----------------|-------------------|-------------------|-------------|-----------|
| `ssh-access` | 1 | `ssh_access` | 1 | ✅ Name mismatch (hyphen vs underscore), key aligns |
| `apt-deps` | 2 | `apt_deps` | 2 | ✅ |
| `tor-proxy` | 3 | `tor_proxy` | 3 | ✅ |
| `install-docker` | 4 | `install_docker` | 4 | ✅ |
| `docker-auth` | 5 | `docker_auth` | 5 | ✅ |
| `user-platform` | 6 | `create_platform_user` | 6 | ⚠️ Name mismatch (different names) |
| `user-ci-deploy` | 7 | `create_ci_deploy_user` | 7 | ⚠️ Name mismatch |
| `projects-base` | 8 | `create_projects_base` | 8 | ⚠️ Name mismatch |
| `firewall` | 9 | `firewall` | 9 | ✅ |
| `verify-core` | 10 | `verify_core` | 10 | ✅ |
| `verify-node-configs` | 11 | `verify_node_configs` | 11 | ✅ |
| `decrypt-secrets` | 12 | `decrypt_secrets` | 12 | ✅ |
| — | — | `ensure_secrets` | **13** | 🔴 **Shell has no entry for key 13 — writes `read-node-yaml` instead** |
| — | — | `secrets_init` | **14** | 🔴 **Shell has no entry for key 14 — writes `ghcr-auth` instead** |
| `read-node-yaml` | 13 | `read_node_yaml` | **15** | 🔴 **Key mismatch: shell key 13 ≠ Python index 15** |
| `ghcr-auth` | 14 | `ghcr_auth` | **16** | 🔴 **Key mismatch: shell key 14 ≠ Python index 16** |
| `sudoers` | 15 | `sudoers` | **17** | 🔴 **Key mismatch: shell key 15 ≠ Python index 17** |
| `metrics-cron` | 16 | — | — | ⚠️ Shell-only step, no Python equivalent |

**Impact:** After shell writes steps 1-12 (keys 1-12 aligned) + steps 13-16 (keys 13-16 = `read-node-yaml`, `ghcr-auth`, `sudoers`, `metrics-cron`), the state machine loads this state.json and:
- `_is_step_done(13)` → True (key "13" exists and is "done") → **`ensure_secrets` incorrectly SKIPPED**
- `_is_step_done(14)` → True (key "14" exists and is "done") → **`secrets_init` incorrectly SKIPPED**
- `_is_step_done(15)` → True → `read_node_yaml` skipped (but key 15 was written by `sudoers`, not `read_node_yaml`)
- `_is_step_done(16)` → True → `ghcr_auth` skipped (but key 16 was written by `metrics-cron`, not `ghcr_auth`)

**Root cause:** Shell and Python step inventories differ. Shell has 16 checkpoints; Python has 23 steps. Shell skips `ensure_secrets`, `secrets_init`, `install_acme`, `node_update`, `converge`, `audit_log`, `telegram`, `deploy_context` — these are run only by state_machine.py. The numeric key assignment is sequential within each system independently, so alignment breaks where the inventories diverge.

**Fix required:** Either:
1. (Recommended) Use step NAMES as keys in state.json (not numeric indices), so `_is_step_done("ensure_secrets")` checks by name
2. Have the shell write entries for ALL Python-inventoried steps (not just its own 16)
3. Map shell step names to Python indices explicitly in the checkpoint layer

---

## 2. Implementation Status

**Status: NOT YET STARTED**

| File | Current state | DevPlan target | Gap |
|------|--------------|----------------|-----|
| `core/lib/checkpoint.sh` | 262 lines, `.done`-based (18x `.done` references) | ~180 lines, state.json-based | **Full rewrite needed** |
| `core/internal/bootstrap/node-lifecycle.sh` | 237 lines, `CHECKPOINT_DIR` at lines 135, 213 | Add `CHECKPOINT_STATE_FILE`, call `checkpoint_migrate_legacy` | **~15 lines to change** |
| `core/internal/bootstrap/lifecycle/state_machine.py` | 2081 lines, uses `state.json` with 1-based numeric keys | No code change (verify compatibility) | **No change needed** |
| `core/internal/bootstrap/content-hash.sh` | Unchanged, `compute_step_hash()` used by shell | No change | **No change needed** |
| `core/internal/bootstrap/AGENTS.md` | Still documents `.done`-based checkpoints | Update to state.json | **~10 lines to update** |
| `tests/unit/test_state_machine.py` | 993 lines, 40 tests all green | Add 2 new tests (`test_shell_written_state_json`, `test_shell_hash_mismatch_rerun`) | **~60 lines to add** |

### Test results (current state):
```
tests/unit/test_state_machine.py — 40 passed in 0.21s
100% PASS — counter reset to 0
```

No `test_shell_written_state_json` or `test_shell_hash_mismatch_rerun` exist yet. No `checkpoint_migrate_legacy()` or `_checkpoint_is_done_json()` exist anywhere in the codebase.

---

## 3. Prerequisites Check

| Prerequisite | Status | Evidence |
|-------------|--------|----------|
| `state_machine.py` can read state.json written by shell | ⚠️ AT RISK | Works mechanically (loads JSON, uses numeric keys), but step-name alignment breaks (see Section 1) |
| `paths.sh` exports `STATE_JSON` path | ❌ MISSING | `paths.sh` has no `CHECKPOINT_STATE_FILE` variable. DevPlan 01-DevPlan.md claims "checkpoint.sh sources paths.sh → STATE_JSON path from there" — this is incorrect. The expanded version correctly defines `CHECKPOINT_STATE_FILE` in `node-lifecycle.sh` directly. |
| `python3` available on VPS | ✅ | Installed by `install-docker.sh` step (includes python3 deps) |
| `jq` NOT required | ✅ | DevPlan explicitly avoids `jq` dependency — uses `python3 -c` instead |
| Tests pass before changes | ✅ | 40/40 state machine tests pass |
| `bash -n` syntax check on checkpoint.sh | ✅ | Current checkpoint.sh has valid bash syntax |

---

## 4. Cross-Reference Integrity

| Reference | Type | Status |
|-----------|------|--------|
| `core/lib/checkpoint.sh` (File Manifest) | Source file | ✅ Exists (262 lines) |
| `core/internal/bootstrap/node-lifecycle.sh` | Source file | ✅ Exists (237 lines) |
| `core/internal/bootstrap/lifecycle/state_machine.py` | Source file | ✅ Exists (2081 lines) |
| `core/internal/bootstrap/content-hash.sh` | Source file | ✅ Exists |
| `core/internal/bootstrap/AGENTS.md` | Doc file | ✅ Exists |
| `tests/unit/test_state_machine.py` | Test file | ✅ Exists (993 lines, 40 tests) |
| DevPlan 047 (referenced in step renumbering) | Prior DevPlan | ✅ Exists (bootstrap pipeline redesign) |
| `node-lifecycle.sh:109` `_checkpoint_version_check` call | Code reference | ⚠️ Function exists but is NOT called in current `main()` — safe to remove |
| `paths.sh` → `CHECKPOINT_STATE_FILE` | Cross-file dep | ❌ Missing (see Prerequisites) |
| `content-hash.sh:17` `CHECKPOINT_DIR` default | Cross-file ref | ⚠️ Still references old default `/var/lib/platform/.bootstrap-checkpoints` — will need update or coexist with migration |

---

## 5. Findings

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 1 | **CRITICAL** | Step-name/key misalignment after shell step 12: shell writes `read-node-yaml` at key 13, but Python expects `ensure_secrets` at index 13. This causes `ensure_secrets` and `secrets_init` to be incorrectly skipped on resume. | Redesign: use step names as state.json keys (not numeric indices), or map shell steps to Python step indices explicitly. See Section 1 for full alignment table. |
| 2 | **HIGH** | **Language policy violation**: DevPlan TASK-1 embeds large `python3 -c "..."` blocks (60+ lines in `_checkpoint_mark_done_json`, 15+ lines in `_checkpoint_is_done_json`). Per `AGENTS.md` §Языковая политика Tier 1 Strangler trigger: "Добавление нового `python3 -c '...'` или heredoc-блока → вынести эту конкретную логику в отдельный `.py` модуль". | Extract JSON checkpoint operations to `core/lib/checkpoint_json.py` (or `core/internal/bootstrap/checkpoint_json.py`). Shell functions become thin callers: `python3 checkpoint_json.py is-done "$step_name"`. This also improves testability. |
| 3 | **MEDIUM** | Documentation drift between 01-DevPlan.md (brief) and 02-DevPlan-expanded.md: brief claims `paths.sh` provides `STATE_JSON`, but `paths.sh` has no such variable. Expanded version correctly defines it in `node-lifecycle.sh`. | Update 01-DevPlan.md to remove inaccurate reference, or delete the brief (expanded is authoritative per R1). |
| 4 | **MEDIUM** | `content-hash.sh` still references `CHECKPOINT_DIR` for `.hash` file operations. After migration, `.hash` files will no longer be created by new checkpoint code. `step_hash_changed()` will always return "changed" (no file = changed), triggering one re-execution per step. | Acceptable (one-time re-execution is documented as expected). Consider removing `content-hash.sh`'s dependency on `CHECKPOINT_DIR` in a future cleanup wave. |
| 5 | **LOW** | `node-lifecycle.sh:3` STRUCTURE comment says "checkpoint_step preserves .done" — needs update to "checkpoint_step preserves state.json" after rewrite. | Update during implementation. |
| 6 | **LOW** | Shell step names use hyphens (`ssh-access`), Python uses underscores (`ssh_access`). While `_is_step_done()` uses numeric keys (so this doesn't break functionality), mismatched names will confuse debugging and audit logs. | Consider normalizing step-name convention (all-underscore or all-hyphen) across both systems. |
| 7 | **INFO** | `checkpoint_migrate_legacy()` is designed as idempotent (checks for files before migration). After first run, legacy dir is emptied → second call is no-op. Migration runs in both `init` and `update` modes, ensuring VPS nodes get migrated regardless of which bootstrap path they use. | Design is sound. |
| 8 | **INFO** | Hash divergence on first post-migration run is correctly identified and accepted (shell hashes `node-lifecycle.sh`, Python hashes `state_machine.py`). One-time re-execution of all 23 steps is expected. | Acceptable. Document this in release notes for operators. |

---

## 6. Additional Notes

### Scope expansion analysis (per QA STANDARD rules)

Files touched: 6 (within STANDARD range). Config files affected: none directly. CI workflows: no references to `.done` files or bootstrap-checkpoints found — no scope expansion needed.

### Test coverage gap

The DevPlan specifies 2 new tests (`test_shell_written_state_json`, `test_shell_hash_mismatch_rerun`), but these tests will NOT catch the step-name/key misalignment (Finding #1) because the test data uses aligned indices (as designed). Additional test needed: `test_shell_step_misalignment_resume` that verifies `ensure_secrets` is NOT skipped when shell wrote a different step at the same numeric key.

### Language policy analysis

The `checkpoint.sh` rewrite introduces 4 `python3 -c "..."` blocks totaling ~100 lines of inline Python. Per the two-level Strangler trigger:
- **Tier 1 triggers**: Adding new `python3 -c "..."` → must extract to `.py` module. All 4 blocks are NEW code. Violation.
- **Tier 2 trigger**: 4 inline blocks in one file → planned Strangler decomposition needed.

The DevPlan's rationale section addresses `python3 -c` vs `jq` but does not address the language policy's requirement to extract inline Python to separate modules. A separate `checkpoint_json.py` would:
- Make JSON operations unit-testable without bash
- Comply with language policy
- Be delivered via core-deploy alongside checkpoint.sh (same SCP mechanism)

---

$END_VERIFICATION_REPORT
