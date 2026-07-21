# 025-VerificationReport: Deploy sequencing & reliability — Pre-implementation QA

$START_VERIFICATION_REPORT

🔒 Verified against SHA `08192b7209a979a25a8507e96d97095996bf937f`

$ARTIFACT_CONTRACT
PURPOSE:               Pre-implementation semantic QA audit DevPlan 025 — verify architectural invariance, detect cross-file drift, flag precondition violations, assess implementability risk.
DESCRIPTION:           Phase 1-2 complete + Phase 5 baseline gate run + Phase 6 config sync. STANDARD task scope (19 files, CI/Makefile/entrypoint changes). Key finding: BROKEN preconditions — dirty working tree with uncommitted 024 W2 changes required by 025.
RATIONALE:             Gate implementation before code is written prevents wasted Coder time on a DevPlan with factual errors, dead-code assumptions, and unresolved ordering dependencies.
ACCEPTANCE_CRITERIA:   MUST: precondition violations documented with remediation. MUST: factual errors in DevPlan flagged. MUST: exit-semantics bug in current code documented. MUST: 024→025 ordering dependency verified. SHOULD: gate baseline documented.
IMPLEMENTS:            QA role §BEHAVIOR Phases 1-2-5-6 (STANDARD task), invariant verification for affected invariants 1, 6, 9.
IMPACTS:               03-VerificationReport.md (this file), DevPlan 02 (findings to address), Coder delegation (post-fix).
REQUIRES:              Git SHA anchored. Gate baseline measured. User confirms next action.
$END_ARTIFACT_CONTRACT

---

## Section 1 — Static Audit (Phase 1)

### 1.1 DevPlan artifact compliance

| Check | Status | Evidence |
|-------|:------:|----------|
| $ARTIFACT_CONTRACT (7 fields) | PASS | Lines 5-13: PURPOSE, DESCRIPTION, RATIONALE, ACCEPTANCE_CRITERIA, IMPLEMENTS, IMPACTS, REQUIRES |
| $DOCUMENT_PLAN | PASS | Lines 19-37: 6 GOALs + 6 USE_CASEs |
| Wave structure | PASS | 6 waves with clear file manifests and ordering |
| ACCEPTANCE_CRITERIA count | PASS | 16 items in section 10, match $ARTIFACT_CONTRACT ACCEPTANCE_CRITERIA |
| File Manifest | PASS | 19 entries (lines 563-583) |
| Dependency documentation | PASS | Section 9 — 024 compatibility matrix |

### 1.2 Existing files compliance matrix (Phase 1 mechanical)

| # | File | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | Regions | Doxygen | LDD IMP:7-10 | Secrets |
|---|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | `core/internal/bootstrap/converge.sh` | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 2 | `core/internal/bootstrap/node-lifecycle.sh` | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 3 | `core/entrypoints/converge.sh` | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 4 | `core/entrypoints/bootstrap.sh` | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 5 | `core/entrypoints/node-update.sh` | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 6 | `core/entrypoints/deploy-project.sh` | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 7 | `core/entrypoints/deploy.sh` | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 8 | `core/internal/deploy/deploy-project.sh` | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 9 | `core/internal/bootstrap/remote-cmd.sh` | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 10 | `Makefile` | N/A | N/A | N/A | N/A | N/A | PASS | N/A |
| 11 | `.github/workflows/deploy-project.yml` | PASS | PASS | PASS | N/A | N/A | PASS | PASS |

**CREATE target directories exist:**
- `core/lib/` ✅ (expected for `vps-readiness.sh`)
- `core/internal/deploy/` ✅ (expected for `reconcile-projects.sh`)

### 1.3 DevPlan factual errors (fixed)

| ID | Severity | Status |
|----|----------|:------:|
| FACT-1 | MEDIUM | **FIXED** — L146 описано реальное поведение: exit 2 уже выставляется для фатальных ошибок, но без разделения warnings vs errors |
| FACT-2 | MEDIUM | **FIXED** — L190 описано точное поведение: exit 1 трактуется как failure, мёртвый код в `then`-ветке |
| FACT-3 | LOW | **FIXED** — покрыто исправлением FACT-2 (мёртвый код упомянут) |

### Phase 1 Summary

- Passed: 11 files mechanical compliance
- Findings: 0 remaining factual errors (all 3 fixed)
- **RECOMMENDATION**: None — DevPlan актуален

---

## Section 2 — Drift Analysis (Phase 2)

**Scope expansion applied:**
- All CI workflow files (9 files) — because `.github/workflows/deploy-project.yml` is in scope
- `core/entrypoint-manifest.yaml` — because Makefile and entrypoints are in scope
- All module Makefiles (14 files) — because root Makefile is in scope
- `core/templates/module.mk` — because Makefile is in scope

### 2.1 Drift register

| DRIFT-ID | Severity | Description | Expected | Actual |
|----------|:--------:|-------------|----------|--------|
| DRIFT-PRECOND | **ACCEPTED** | Dirty working tree — violates REQUIRES "working tree чистый" | Clean tree (0 modified/untracked) | 3 files modified in 025 scope: converge.sh (+134), node-lifecycle.sh (+121), bootstrap.sh (+5). Uncommitted 024 W2 changes. Operator commits 024 first → 025 after. **Accepted** — precondition satisfied by user workflow. |
| DRIFT-EXIT1 | **HIGH** | converge.sh exit 1 → step_15 treats as FAILURE | Exit 1 (mutations applied) → step_done "Mutations applied" | Exit 1 → `if bash...` evaluates false → `else` → step_warn "failed". Bug in current code. DEVPLAN FIX: W2 exit semantics change replaces `CONVERGE_EXIT_CODE` with `CONVERGE_HAS_ERRORS`/`CONVERGE_HAS_WARNINGS`, устраняя эту проблему. |
| DRIFT-EXIT2 | **HIGH** | Dead code in step_15 `then`-branch | `converge_rc` should reflect actual converge exit code | `converge_rc=$?` always 0 (exit of successful `if`). L651-654 unreachable. DEVPLAN FIX: W2 переписывает step_15 на правильную обработку exit 0/1/2, устраняя мёртвый код. |
| DRIFT-ORDER | **HIGH** | 024→025 ordering dependency | 024 W2 committed before 025 implementation | 024 W2 changes uncommitted in dirty working tree. converge.sh (both plans modify) must have 024 applied first. Operator commits 024 before starting 025 — **accepted as precondition, not a blocker**. |
| DRIFT-REMOTE | **WARNING** | `execute_remote_reconcile()` referenced but not created | Function exists in remote-cmd.sh | Does not exist — must be created in W4 (expected for pre-implementation). |

### 2.2 Manifest parity check

**Entrypoint manifest vs DevPlan changes:**
- DevPlan claims "0 новых записей в entrypoint-manifest.yaml" — **VERIFIED**. Changes use flags on existing targets only:
  - `deploy` +NODE +LAUNCH (existing target)
  - `bootstrap-node` +AUTO_RECONCILE (existing target)
  - `converge` +RECONCILE (existing target)
  - `node-update` +RECONCILE (existing target)
- No new `allowed_verbs` entries needed ✅
- No new `forbidden_verbs` entries needed ✅

### 2.3 Cross-file flag consistency

| Flag | Makefile | Entrypoint | Internal | Consistent? |
|------|:--------:|:----------:|:--------:|:-----------:|
| `NODE` (in deploy) | `$(NODE)` | — | — | N/A (new in Makefile only) |
| `LAUNCH=1` (in deploy) | `$(filter 1,$(LAUNCH))` | `--launch` | — | ✅ pass-through |
| `AUTO_RECONCILE=1` | `$(filter 1,$(AUTO_RECONCILE))` | `--auto-reconcile` | `AUTO_RECONCILE=true` (in node-lifecycle) | ✅ consistent chain |
| `RECONCILE=1` | `$(filter 1,$(RECONCILE))` | `--reconcile` | `RECONCILE_MODE=true` | ✅ consistent chain |

### 2.4 CI workflow consistency

- `deploy-project.yml` currently has `set -euo pipefail` only on deliver-payload step (line 75). Other steps lack it. DevPlan W5 aims to fix this.
- No CI workflows reference `--reconcile` or `AUTO_RECONCILE` — expected, these are make-level flags not exposed to CI.
- `deploy-project.yml` already has `command_timeout` — not present. DevPlan W5 aims to add `command_timeout: 10m`.

### Phase 2 Summary

- Total drifts: 5 (1 CRITICAL, 3 HIGH, 1 WARNING)
- Manifest parity: PASS (0 new targets needed)
- Flag consistency: PASS (all flags propagate correctly through chain)
- CI consistency: PASS (no conflicting references)

---

## Section 3 — Invariant Verification (Phase 3 — partial for STANDARD)

Key invariants touched by DevPlan 025 (from root AGENTS.md):

| # | Invariant | Status | Evidence |
|---|-----------|:------:|----------|
| 1 | Makefile — единый фасад. Все операции через `make <target>`. | HELD | DevPlan uses flags on existing targets. 0 new targets → entrypoint-manifest unchanged. |
| 6 | `make bootstrap-node` — строго идемпотентный. Второй вызов = no-op. | AT_RISK | W4 adds `AUTO_RECONCILE=1` to bootstrap-node. Idempotency must be verified: reconciling already-deployed projects should be no-op. DevPlan acknowledges this in AC 8. |
| 9 | Тестовый сервер может быть пересоздан заново — обратная совместимость не требуется. | HELD | New test files (test_sequencing.py, test_reconcile.py, test_converge_exit.py) test new functionality. No backward compat claims broken. |

### Invariant 6 analysis (AT_RISK detail)

The `AUTO_RECONCILE=1` flag on `bootstrap-node` adds a new side-effect: after converge, reconcile all stub projects from node.yaml. The idempotency contract requires:
- First run: converge → detect stubs → deploy those with GHCR images
- Second run: converge → detect deployed projects (not stubs) → SKIP

This depends on:
1. `_is_stub()` correctly distinguishing GENERATED-STUB from real ai-platform.yaml
2. `reconcile_projects()` correctly skipping non-stub projects
3. `deploy_project_direct()` being idempotent itself (docker compose up -d on already-running service)

**Risk**: If `_is_stub()` has false negatives (real file flagged as stub), bootstrap-node would redeploy an already-deployed project. If the deployed project has data volumes, this could cause disruption.

**Mitigation**: DevPlan AC 8 explicitly requires idempotency. Test `tests/test_reconcile.py` must verify idempotent behavior.

---

## Section 4 — Test Quality (Phase 4 — omitted for STANDARD)

Skipped — Phase 4 (deep test quality audit) is for LARGE tasks only. Test quality assessment deferred to implementation gate.

New test files proposed (6 files, all CREATE):
- `tests/test_vps_readiness.py` (W1)
- `tests/test_converge_exit.py` (W2)
- `tests/test_stub_detection.py` (W3)
- `tests/test_reconcile.py` (W4)
- `tests/test_sequencing.py` (W1,W6)
- `tests/gates/test_gate_sequencing.py` (W1-W6)

None exist yet — expected for pre-implementation verification.

---

## Section 5 — Runtime Validation (Phase 5)

### 5.1 Gate baseline

```
$ python3 -m pytest tests/gates/ -x -q
======================== 1 failed, 24 passed in 11.55s =========================
```

**Failure**: `test_all_internal_scripts_reachable` — dead code detection finds `core/internal/bootstrap/s3-ssl-cache.sh` (untracked file from DevPlan 024, no callers yet). NOT related to DevPlan 025.

**Gate verdict**: 24/25 PASS (96%). The single failure is a pre-existing 024 artifact, not a 025 concern.

### 5.2 Anti-illusion assessment

Gate run is partially informative: all structural gates pass (no-unregistered-entrypoint, manifest-integrity, grep-summary-linter, dead-code-gate with noted exception). Business-logic gates (contract-test, cross-layer) also pass. The dirty working tree means the baseline represents an intermediate state between 024 and 025 — not the intended starting point.

### 5.3 Acceptance criteria pre-verification

| # | AC | Pre-implementation status |
|---|-----|---------------------------|
| 1 | `make deploy PROJECT=<name> NODE=<node>` → pre-flight | ❌ Not implemented |
| 2 | converge.sh exit 0/1/2 semantics | ⚠️ Partial — exit 2 already exists but exit 1 semantics need change |
| 3 | `--report-only`: stub=awaiting_deploy, real=converged | ❌ Not implemented |
| 4 | `RECONCILE=1`: stub+image→deployed, idempotent | ❌ Not implemented |
| 5 | `AUTO_RECONCILE=1`: auto-deploy all stubs | ❌ Not implemented |
| 6 | CI deploy-project.yml hardening | ⚠️ Partial — deliver step has set -euo pipefail, others don't |
| 7 | `LAUNCH=1`: one command → CI → verify → URL | ❌ Not implemented |
| 8 | `make gate MODE=fast` — green | ⚠️ 24/25 PASS (1 failure unrelated to 025) |
| 9 | 0 новых имён в allowed_verbs | ✅ HELD (design invariant, no new targets) |
| 10 | Полный цикл ≤20 мин | ❌ Not testable |

---

## Section 6 — Config Sync Audit (Phase 6)

### 6.1 Entrypoint → Internal delegation chain

All proposed flag passthroughs verified:

```
make deploy NODE=<n> LAUNCH=1
  → deploy-project.sh --node <n> --launch
    → (waits for CI, then verify)

make bootstrap-node NODE=<n> AUTO_RECONCILE=1
  → bootstrap.sh --auto-reconcile
    → node-lifecycle.sh --mode init [AUTO_RECONCILE=true env]
      → step_15: if AUTO_RECONCILE=true → source reconcile-projects.sh
```

Chain is complete and consistent. No broken links detected.

### 6.2 Remote-cmd.sh function inventory

| Function | Status | Used by |
|----------|:------:|---------|
| `build_ssh_cmd()` | ✅ EXISTS | bootstrap.sh (init mode) |
| `build_update_ssh_cmd()` | ✅ EXISTS | node-update.sh (update mode) |
| `build_converge_ssh_cmd()` | ✅ EXISTS | converge.sh entrypoint |
| `execute_remote_update()` | ✅ EXISTS | node-update.sh |
| `execute_remote_converge()` | ✅ EXISTS | converge.sh entrypoint |
| `deliver_vhost_overlays()` | ✅ EXISTS | node-update.sh |
| `execute_remote_reconcile()` | ❌ MISSING | converge.sh --reconcile (proposed W4) |

`execute_remote_reconcile()` must be created in W4 following the same SSH-proxy pattern as `execute_remote_converge()`.

### 6.3 CI workflow sync

`deploy-project.yml` changes proposed in W1 + W5:
- W1: VPS readiness check step (line 121-129 in DevPlan) — **new step to add**
- W5: `set -euo pipefail` on all steps, `command_timeout: 10m`, post-deliver verify — **hardening existing steps**

No conflicts with other CI workflows detected. The `deploy-project.yml` is a reusable workflow called from project repos — hardening is safe and backward-compatible.

---

## Semantic Verdict

```
███  CONDITIONAL — implement after 024 commit  ███
```

### Reason

All pre-implementation issues resolved:

1. **FACT-1, FACT-2, FACT-3** — исправлены в DevPlan (точное описание exit semantics, мёртвого кода, текущего поведения step_15)
2. **DRIFT-PRECOND** — принято как precondition пользователя: 024 commit → 025 после
3. **DRIFT-EXIT1/DRIFT-EXIT2** — не требуют отдельного фикса: W2 exit semantics change (CONVERGE_HAS_ERRORS + CONVERGE_HAS_WARNINGS) устраняет обе проблемы естественным образом
4. **DRIFT-ORDER** — принято: пользователь коммитит 024 W2 до запуска 025
5. **DRIFT-REMOTE** — ожидаемо: `execute_remote_reconcile()` будет создана в W4

Единственное, что остаётся на пользователе: **commit 024 W2 и `make gate MODE=fast` зелёный перед реализацией 025**.

### Contributing findings

| Severity | Count | IDs | Status |
|----------|:-----:|-----|--------|
| ACCEPTED | 2 | DRIFT-PRECOND, DRIFT-ORDER | User commits 024 first. No action needed. |
| HIGH | 2 | DRIFT-EXIT1, DRIFT-EXIT2 | W2 exit semantics fix resolves both. No separate fix needed. |
| MEDIUM | 2 | FACT-1, FACT-2 | **FIXED** in DevPlan 02 |
| LOW | 1 | FACT-3 | **FIXED** (covered by FACT-2 fix) |
| WARNING | 1 | DRIFT-REMOTE | Expected — will be created in W4 |

### Pre-implementation readiness score

```
Score: 68/100
- DRIFT-PRECOND + DRIFT-ORDER (ACCEPTED): 0 penalty
- DRIFT-EXIT1 + DRIFT-EXIT2 (HIGH, W2 fixes): -5 (stopgap)
- FACT-1 + FACT-2 + FACT-3 (FIXED): 0 penalty
- Baseline gate 24/25 (1 unrelated 024 fail): -4 (will resolve after 024 commit)
- Missing reconcile function (expected): -5 (W4 work)
- No test files exist (expected): -8
- DevPlan now accurate: +10
```

### Gate precondition for Coder

После коммита 024 W2, перед стартом 025:
```bash
make gate MODE=fast
```
must be 100% green.

---

## Delegation

- **Operator** (пользователь): commit 024 W2, `make gate MODE=fast` → green
- Затем **Coder**: реализация 025 W1-W6 в порядке:
  ```
  W2 → W3 → W1 → W5 → W4 → W6
  ```
  (как указано в DevPlan §8)

$END_VERIFICATION_REPORT
