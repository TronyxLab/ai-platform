# 05-VerificationReport: Deploy sequencing & reliability — Re-audit (Gate visibility)

$START_VERIFICATION_REPORT

🔒 Verified against SHA `08192b7209a979a25a8507e96d97095996bf937f`
⚠️ Working tree dirty: 26 modified + 8 untracked files (all 025 implementation)

$ARTIFACT_CONTRACT
PURPOSE:               Re-audit DevPlan 025 after 04-VerificationReport fixes. Detect invisible gate tests
                       (missing @pytest.mark.gate marker + missing manifest registration) that prior
                       report did not catch. Verify fusion S7 constraint, flag propagation, and test quality.
DESCRIPTION:           LARGE task (>20 files, architectural changes). All phases 1-6 executed.
                       **Critical finding**: `tests/gates/test_gate_sequencing.py` has ZERO `@pytest.mark.gate`
                       decorations and is NOT registered in entrypoint-manifest.yaml gates section.
                       Gate tests exist on disk but are invisible to `make gate` — collected 0/6 when
                       filtered by `-m "gate"`. This means **sequencing invariants are NOT enforced in CI**.
RATIONALE:             The gate registration protocol (gates/AGENTS.md) requires all three: file in tests/gates/,
                       @pytest.mark.gate decorator, and manifest registration. Missing any = gate doesn't run.
                       04-VerificationReport missed this critical gap, claiming 205/205 gate green — the
                       sequencing gate tests weren't even collected.
ACCEPTANCE_CRITERIA:   MUST: expose gate visibility gap (✅ — documented below). MUST: verify fusion S7
                       (✅ — 0 new allowed_verbs). MUST: verify all flag propagation chains (✅ — 5/5).
                       MUST: verify test quality for new test files (✅ — 25/26 pass, IMP:9 coverage).
IMPLEMENTS:            QA role §BEHAVIOR Phases 1-6 (LARGE task), invariant verification, gate protocol audit.
IMPACTS:               05-VerificationReport.md (this file). Coder delegation for gate marker + manifest fix.
REQUIRES:              Git SHA anchored. User confirms delegation to Coder for gate fix.
$END_ARTIFACT_CONTRACT

---

## Section 1 — Static Audit (Phase 1)

### 1.1 File existence matrix

| # | File | Action | Exists | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | LDD IMP:7-10 | Secrets |
|---|------|:------:|:------:|:------------:|:---------:|:---------------:|:------------:|:-------:|
| 1 | `core/lib/vps-readiness.sh` | CREATE | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 2 | `core/internal/deploy/reconcile-projects.sh` | CREATE | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 3 | `core/internal/bootstrap/converge.sh` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4 | `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 5 | `core/entrypoints/converge.sh` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 6 | `core/entrypoints/bootstrap.sh` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 7 | `core/entrypoints/node-update.sh` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 8 | `core/entrypoints/deploy-project.sh` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 9 | `core/entrypoints/deploy.sh` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 10 | `core/internal/deploy/deploy-project.sh` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 11 | `core/internal/bootstrap/remote-cmd.sh` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 12 | `Makefile` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 13 | `.github/workflows/deploy-project.yml` | MODIFY | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 14 | `tests/test_vps_readiness.py` | CREATE | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 15 | `tests/test_converge_exit.py` | CREATE | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 16 | `tests/test_stub_detection.py` | CREATE | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 17 | `tests/test_reconcile.py` | CREATE | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 18 | `tests/test_sequencing.py` | CREATE | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 19 | `tests/gates/test_gate_sequencing.py` | CREATE | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Phase 1 Summary:** 19/19 files present with complete semantic markup. No missing files, no exposed secrets, no bare `except:`.

### 1.2 Compliance issues found

| Check | File:Line | Severity | Issue |
|-------|-----------|:--------:|-------|
| Gate marker | `tests/gates/test_gate_sequencing.py:54-271` | **CRITICAL** | 0 of 6 test functions decorated with `@pytest.mark.gate`. Gate test invisible to `make gate`. |
| Manifest registration | `core/entrypoint-manifest.yaml` | **CRITICAL** | `test_gate_sequencing.py` not registered in `gates:` section. Violates gates/AGENTS.md invariant 1. |
| Pass-test | `tests/gates/test_gate_sequencing.py:216-222` | HIGH | `test_gate_zero_new_entrypoints` = `@pytest.mark.skip` + `pass` — violates Test Honesty Rules R1 (NO pass-tests). |

---

## Section 2 — Drift Analysis (Phase 2)

### 2.1 Gate Registration Drift — CRITICAL

Gate registration protocol per `tests/gates/AGENTS.md` §инварианты:

> 1. Каждый gate-файл ДОЛЖЕН быть зарегистрирован в core/entrypoint-manifest.yaml (секция gates)
> 2. Каждый gate-тест ДОЛЖЕН иметь декоратор @pytest.mark.gate

**Current state:**

| Requirement | Status | Evidence |
|-------------|:------:|----------|
| File in `tests/gates/` | ✅ | `tests/gates/test_gate_sequencing.py` exists |
| `@pytest.mark.gate` on test functions | **❌** | 0/6 functions decorated. Only `@pytest.mark.skip` on line 216 (skip decoration). |
| Registered in manifest `gates:` | **❌** | `grep "test_gate_sequencing" core/entrypoint-manifest.yaml` = 0 matches |
| Collected by `-m "gate"` | **❌** | `python3 -m pytest tests/gates/test_gate_sequencing.py -m "gate"` → `0 selected, 6 deselected` |

**Impact:** 6 gate tests (converge exit semantics, --reconcile flag check, internal-only guard, vps-readiness sourceability, zero new entrypoints, deploy NODE/LAUNCH flags) exist on disk but are NEVER executed in `make gate`. This means:

- No CI enforcement of W2 exit semantics at gate level
- No CI enforcement of W4 reconcile flag at gate level
- No CI enforcement of fusion S7 constraint at gate level
- No CI enforcement of W1 pre-flight flag at gate level

**This is DRIFT-CATEGORY: gate invisibility — gate tests provide zero protection because they're not wired in.**

### 2.2 Flag propagation chain

All 5 chains verified — consistent with 04 report findings:

| Flag | Makefile | Entrypoint | Internal | Status |
|------|:--------:|:----------:|:--------:|:------:|
| `NODE` (deploy W1) | `$(NODE)` → pre-flight | — | — | ✅ |
| `LAUNCH=1` (deploy W6) | `$(filter 1,$(LAUNCH))` | `--launch` | `LAUNCH_MODE=1` | ✅ |
| `AUTO_RECONCILE=1` (bootstrap W4) | `$(filter 1,$(AUTO_RECONCILE))` | `--auto-reconcile` | `AUTO_RECONCILE=true` in node-lifecycle | ✅ |
| `RECONCILE=1` (converge W4) | `$(filter 1,$(RECONCILE))` | `--reconcile` | `CONVERGE_RECONCILE=true` | ✅ |
| `RECONCILE=1` (node-update W4) | `$(filter 1,$(RECONCILE))` | `--reconcile` | `PASSTHROUGH_ARGS+=--reconcile` | ✅ |

### 2.3 Fusion S7 constraint

| Check | Status | Evidence |
|-------|:------:|----------|
| `entrypoint-manifest.yaml` unchanged | ✅ | `git diff HEAD -- core/entrypoint-manifest.yaml` = empty |
| 0 new `allowed_verbs` entries | ✅ | Still 36 entries — no new verbs |
| 0 new Makefile `.PHONY` targets | ✅ | Same 36 targets — `reconcile` is a flag, not a target |
| `reconcile-projects.sh` is internal, not entrypoint | ✅ | Source guard: exits 1 with FATAL message when run directly |

### 2.4 CI workflow hardening (W5)

| Check | Spec | Actual | Status |
|-------|:----:|--------|:------:|
| `set -euo pipefail` on all run steps | W5 §5.1 | All 4 run steps: resolve-node (58), validate (78), deliver (97), verify-deliver (117) | ✅ |
| `command_timeout: 10m` on SSH deploy | W5 §5.1 | Line 133 | ✅ |
| Post-deliver verify step (compose file exists) | W5 §5.1 | Lines 114-124 | ✅ |
| VPS readiness check step | W1 §1.4 | Lines 83-93 | ✅ |
| Validate project payload step | Not in DevPlan W5 spec | Lines 76-81: `make gate MODE=fast PROJECT=...` | ✅ (bonus) |

### Phase 2 Summary

- Total drifts: 1 CRITICAL (gate invisibility), 0 WARNING
- Flag consistency: 5/5 chains verified
- Manifest parity: HELD (0 new targets)
- CI hardening: 5/5 checks (including bonus validate-payload)

---

## Section 3 — Invariant Status (Phase 3)

### 3.1 Root AGENTS.md invariants touched by 025

| # | Invariant | Status | Evidence |
|---|-----------|:------:|----------|
| 1 | Makefile — единый фасад | **HELD** | All operations through existing make targets with flags; entrypoint-manifest.yaml unchanged; 0 new PHONY targets |
| 6 | `bootstrap-node` — идемпотентный | **HELD** | `AUTO_RECONCILE` adds idempotent reconcile; `_is_stub()` skips deployed projects; converge R3 skips non-stub files |
| 9 | Тестовый сервер recreatable | **HELD** | All tests use tmp_path; no persistent state |

### 3.2 Gates AGENTS.md invariants

| # | Invariant | Status | Evidence |
|---|-----------|:------:|----------|
| 1 | Каждый gate-файл зарегистрирован в manifest | **VIOLATED** | `test_gate_sequencing.py` not in `gates:` section |
| 2 | Каждый gate-тест имеет `@pytest.mark.gate` | **VIOLATED** | 0/6 functions decorated |
| 3 | Каждый gate-файл в `tests/gates/` | HELD | ✅ |
| 4 | Триединое соответствие (файл + маркер + manifest) | **VIOLATED** | File exists but marker + manifest missing |

### 3.3 Test Honesty Rules

| Rule | Test | Status | Evidence |
|------|------|:------:|----------|
| R1 (NO pass-tests) | `test_gate_zero_new_entrypoints` | **RED** | `@pytest.mark.skip` + `pass` body — no assertions, unfalsifiable |
| R3 (STALE SKIP) | `test_gate_zero_new_entrypoints` | N/A | Created 2026-07-21 (<90 days) |

---

## Section 4 — Test Quality (Phase 4)

### 4.1 Test inventory & results

| Test file | Tests | PASS | SKIP | IMP:9 | Assertion type |
|-----------|:-----:|:----:|:----:|:-----:|----------------|
| `test_vps_readiness.py` | 4 | 4 | 0 | ✅ 4/4 | Behavioral (exit codes, JSON output) |
| `test_converge_exit.py` | 5 | 5 | 0 | ✅ 5/5 | Behavioral (exit codes 0/1/2) |
| `test_stub_detection.py` | 4 | 4 | 0 | ✅ 4/4 | Behavioral (stub detection, reporting) |
| `test_reconcile.py` | 3 | 3 | 0 | ✅ 3/3 | Behavioral (guard, idempotency) |
| `test_sequencing.py` | 4 | 4 | 0 | ✅ 4/4 | Behavioral (flag logic, backward compat) |
| `test_gate_sequencing.py` | 6 | 5 | 1 | ✅ 5/5 | Behavioral (grep checks, source guards) |
| **Total** | **26** | **25** | **1** | **100%** | **All behavioral** |

### 4.2 Anti-Illusion verdict

**PASS** ✅ — All 25 passing tests emit IMP:9 business-logic logs. LDD trajectory complete.

### 4.3 Invariant coverage gaps

| Invariant | Covered by test? | Gap? |
|-----------|:----------------:|------|
| 1 (Makefile facade) | ⚠️ `test_gate_zero_new_entrypoints` is skipped + pass-only | **GAP** — no active gate enforcement |
| 6 (idempotency) | ✅ `test_reconcile_already_deployed_skip` | No gap |
| 9 (recreatable server) | ✅ All tests use tmp_path | No gap |
| W2 exit semantics | ✅ 5 tests in test_converge_exit.py | No gap |
| W3 stub detection | ✅ 4 tests in test_stub_detection.py | No gap |
| W4 reconciliation | ✅ 3 tests in test_reconcile.py | No gap |
| W1/W6 sequencing | ✅ 4 tests in test_sequencing.py | No gap |
| Gate registration protocol | ❌ Not covered | **GAP** — no test verifies that new gate files have markers + manifest entries |

### 4.4 Test fragility index

- **Skip rate**: 3.8% (1/26) — acceptable (intentional skip with reason)
- **Stale tests**: 0 — all created 2026-07-21
- **Implementation tests**: 0% — all tests are behavioral (verify behavior, not code structure)
- **TRAP[TEST]**: Present on every test function

### 4.5 Semantic assertion classification

All 26 tests verify **behavior** (exit codes, output patterns, function existence), not code structure. No substring-match-on-code assertions. ✅

---

## Section 5 — Runtime Validation (Phase 5)

### 5.1 Unit test results

```
tests/test_vps_readiness.py .............. 4 passed
tests/test_converge_exit.py .............. 5 passed
tests/test_stub_detection.py .............. 4 passed
tests/test_reconcile.py .................. 3 passed
tests/test_sequencing.py ................. 4 passed
tests/gates/test_gate_sequencing.py ...... 5 passed, 1 skipped
============================== 25 passed, 1 skipped in 0.53s ==============================
```

**LDD Trajectory Analysis**: All 25 passing tests emit IMP:9 business-logic logs. Anti-Illusion: PASS ✅

### 5.2 Gate test invisibility — runtime proof

```bash
# Tests run WITHOUT gate marker filter (all collected):
$ python3 -m pytest tests/gates/test_gate_sequencing.py --collect-only -q
collected 6 items
  test_gate_converge_exit_semantics
  test_gate_converge_reconcile_flag
  test_gate_reconcile_not_entrypoint
  test_gate_vps_readiness_sourceable
  test_gate_zero_new_entrypoints
  test_gate_makefile_deploy_node_flag

# Tests run WITH gate marker filter (-- how make gate invokes them):
$ python3 -m pytest tests/gates/test_gate_sequencing.py -m "gate" --collect-only -q
collected 6 items / 6 deselected / 0 selected
```

**Result**: 0 of 6 gate tests are collected when filtered by `-m "gate"`. Gate tests exist but provide **zero protection**.

### 5.3 Acceptance criteria verification

| # | Acceptance Criterion | Status | Evidence |
|---|---------------------|:------:|----------|
| 1 | `make deploy + NODE` → pre-flight check | ✅ | Makefile lines 458-466 |
| 2 | converge.sh exit 0/1/2 semantics | ✅ | converge.sh CONVERGE_HAS_ERRORS + CONVERGE_HAS_WARNINGS; final exit uses flags |
| 3 | `--report-only`: stub→"awaiting_deploy", real→"converged" | ✅ | converge.sh lines 594-598 |
| 4 | `RECONCILE=1`: stub+image→deployed, idempotent | ✅ | reconcile-projects.sh line 131, converge.sh lines 1105-1119 |
| 5 | `AUTO_RECONCILE=1`: auto-deploy all stubs | ✅ | bootstrap.sh line 97, node-lifecycle.sh lines 638-671 |
| 6 | CI hardening: set -euo pipefail, verify deliver, command_timeout | ✅ | deploy-project.yml lines 58,78,97,117 + line 133 |
| 7 | `LAUNCH=1`: one command → CI → verify → URL | ✅ | Makefile lines 471-481, deploy-project.sh lines 362-383 |
| 8 | `make gate MODE=fast` — green | ⚠️ **MISLEADING** | Gate is green but sequencing gate tests are invisible — they're not running. See §2.1. |
| 9 | 0 новых имён в allowed_verbs | ✅ | entrypoint-manifest.yaml unchanged |
| 10 | Полный цикл ≤20 мин | ⚠️ | Not testable locally (requires VPS + CI) |

### 5.4 AC #8 detailed analysis

04-VerificationReport reported `make gate MODE=fast` = 205/205 green after fixes. However:

1. The sequencing gate tests (`test_gate_sequencing.py`) were never running because they lack `@pytest.mark.gate`
2. The "205 passed" count is accurate for the **existing** gate suite — but does not include sequencing gates
3. If the marker + manifest registration were added, these 5 active tests would have to pass to maintain gate green status

**This is a SEMANTIC test gap, not a mechanical test failure. The gate is green only because the new tests aren't wired in.**

---

## Section 6 — Config Sync Audit (Phase 6)

### 6.1 Entrypoint → Internal delegation chain (W1-W6 complete flow)

```
make deploy PROJECT=<p> NODE=<n> LAUNCH=1
  → Makefile: source vps-readiness.sh → check_vps_ready "$(NODE)"  [W1]
  → git push origin main
  → Makefile: deploy-project.sh --project "$(PROJECT)" --node "$(NODE)" --launch  [W6]
    → entrypoints/deploy-project.sh: resolve_node_host → check_vps_ready --quick [W1]
    → deliver_payload → ssh_deploy → verify_deploy → launch: print URL [W6]

make bootstrap-node NODE=<n> AUTO_RECONCILE=1
  → entrypoints/bootstrap.sh: --auto-reconcile → PASSTHROUGH_ARGS
    → build_ssh_cmd → SSH → node-lifecycle.sh --mode init
      → step_6b: converge --units R3 (project scaffold)
      → step_15: if AUTO_RECONCILE=true → converge --reconcile [W4]
        → source reconcile-projects.sh → for each stub: GHCR check → deliver + compose up
      → step_15 post-converge: source reconcile-projects.sh → reconcile_projects [W4]

make converge NODE=<n> RECONCILE=1
  → entrypoints/converge.sh: parse --reconcile → PASSTHROUGH_ARGS
    → execute_remote_converge → converge.sh --reconcile
      → after R-units: source reconcile-projects.sh → reconcile_projects [W4]

make node-update NODE=<n> RECONCILE=1
  → entrypoints/node-update.sh: parse --reconcile → PASSTHROUGH_ARGS
    → execute_remote_update → node-lifecycle.sh --mode update
      → step_6: converge with passthrough args (--reconcile) [W4]
```

Chain complete and consistent. No broken links. ✅

### 6.2 Remote-cmd.sh function inventory (post-W4)

| Function | Status | Used by |
|----------|:------:|---------|
| `build_ssh_cmd()` | EXISTS | bootstrap.sh |
| `build_update_ssh_cmd()` | EXISTS | node-update.sh |
| `build_converge_ssh_cmd()` | EXISTS | converge.sh entrypoint |
| `execute_remote_update()` | EXISTS | node-update.sh |
| `execute_remote_converge()` | EXISTS | converge.sh entrypoint |
| `deliver_vhost_overlays()` | EXISTS | node-update.sh |
| `execute_remote_reconcile()` | ✅ added W4 | converge.sh --reconcile, bootstrap.sh --auto-reconcile |
| `execute_remote_reconcile_entrypoint()` | ✅ added W4 | Higher-level wrapper |

All 8 functions present with clear delegation paths. ✅

### 6.3 NODE_HOST_MAP propagation

| Location | Pattern | Status |
|----------|---------|:------:|
| `.github/workflows/deploy-project.yml` | `vars.NODE_HOST_MAP` (env) | ✅ |
| `Makefile` (deploy) | Passed via env to vps-readiness.sh | ✅ |
| `core/lib/vps-readiness.sh` | `NODE_HOST_MAP` env var → JSON parse | ✅ |
| `core/entrypoints/deploy-project.sh` | `NODE_HOST_MAP="${NODE_HOST_MAP:-...}"` export | ✅ |
| `core/internal/deploy/reconcile-projects.sh` | `NODE_HOST_MAP` → resolve SSH host | ✅ |

Consistent propagation chain. ✅

---

## Semantic Verdict

```
███  DRIFTED (CRITICAL) + DEGRADED (HIGH)  ███
```

### Verdict breakdown

| Verdict | Severity | Reason |
|---------|:--------:|--------|
| **DRIFTED** | **CRITICAL** | Gate registration protocol violated. `test_gate_sequencing.py` has 0 `@pytest.mark.gate` markers and is not registered in entrypoint-manifest.yaml `gates:` section. Gate tests exist but provide zero CI protection. 6 critical invariant checks invisible to `make gate`. |
| **DEGRADED** | HIGH | `test_gate_zero_new_entrypoints` is a pass-test (R1 violation). Gate test quality degraded by invisible tests + meaningless skip. |

### Non-blocking findings

| Severity | ID | Description |
|----------|-----|-------------|
| INFO | — | All 19 files implemented with full semantic markup |
| INFO | — | 25/26 tests pass with IMP:9 coverage |
| INFO | — | Fusion S7 constraint HELD: 0 new make targets, 0 new allowed_verbs |
| INFO | — | All flag propagation chains verified (5/5) |
| INFO | — | CI hardening complete: set -euo pipefail, verify deliver, command_timeout, validate payload |

### What's broken vs what's actually correct

**Broken (gate invisibility):**
- `tests/gates/test_gate_sequencing.py` line 54-271: missing `@pytest.mark.gate` on all 6 functions
- `core/entrypoint-manifest.yaml`: missing `gates:` registration entry for `test_gate_sequencing.py`
- `tests/gates/test_gate_sequencing.py` line 216-222: pass-test `test_gate_zero_new_entrypoints`

**Correct (implementation):**
- All 19 files exist with proper semantic markup
- W1 pre-flight: vps-readiness.sh, Makefile deploy+NODE, deploy-project.sh preflight, CI check-vps-readiness
- W2 exit semantics: CONVERGE_HAS_ERRORS/CONVERGE_HAS_WARNINGS, step_15 exit 0/1/2 handling
- W3 stub detection: _is_stub(), R3 stub-vs-deployed, deploy.sh --stub-aware, STUB_AWARE_STATUS
- W4 reconciliation: reconcile-projects.sh, --reconcile flag, --auto-reconcile flag, execute_remote_reconcile()
- W5 CI hardening: set -euo pipefail, verify deliver, command_timeout: 10m, validate payload
- W6 process unification: LAUNCH=1, --launch flag in deploy-project.sh
- Fusion S7: 0 new make targets, 0 new allowed_verbs entries

### Fix required

1. **P0**: Add `@pytest.mark.gate` decorator to all 5 active test functions in `tests/gates/test_gate_sequencing.py` (lines 54, 105, 139, 178, 233)
2. **P0**: Register `test_gate_sequencing.py` in `core/entrypoint-manifest.yaml` `gates:` section with id `sequencing`
3. **P1**: Replace `test_gate_zero_new_entrypoints` (skip + pass) with actual assertion verifying allowed_verbs count, or remove the test entirely
4. **After fix**: Run `make gate MODE=fast` to confirm gate stays green with newly visible tests

### Score

```
Score: 70/100
  -5: gate test invisible (no @pytest.mark.gate) → integrity gap
  -5: gate test not registered in manifest → protocol violation
  -10: gate registration invariant VIOLATED (tests/gates/AGENTS.md inv 1+2+4)
  -5: pass-test (R1 violation) in test_gate_zero_new_entrypoints
  -5: sub-optimal skip — should be active test or removed
  ---
  70: Code implementation is correct, but gate integration is broken
```

### Delegation

Delegating to Coder for the 3 fixes above. After fix:
```bash
make gate MODE=fast
# Must be 100% green with sequencing gate tests visible and passing
git add -A && git commit -m "fix(025): add @pytest.mark.gate + manifest registration for test_gate_sequencing"
```

$END_VERIFICATION_REPORT
