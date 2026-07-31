$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Verification of DevPlan 073 — provision-environment.sh → Python
DESCRIPTION:           Plan self-consistency, implementation status, cross-reference audit, existing test landscape analysis
RATIONALE:             Ensure DevPlan is actionable, complete, and free of drift before implementation begins
ACCEPTANCE_CRITERIA:   All referenced files exist, ACs are measurable, prerequisites satisfied, plan self-consistent, existing tests accounted for
IMPLEMENTS:            DevPlan:.ai/plans/073-provision-python/01-DevPlan.md
IMPACTS:
  - core/internal/provisioner.py (NEW — planned)
  - core/internal/provision-environment.sh (REWRITE — planned)
  - tests/unit/test_provisioner.py (NEW — planned)
  - tests/test_unit_provision_environment.py (EXISTING — not mentioned in plan)
  - tests/test_smoke_provision_environment.py (EXISTING — not mentioned in plan)
  - core/internal/bootstrap/deploy-modules.sh (CONSUMER — not mentioned)
  - core/internal/bootstrap/lifecycle/state_machine.py (CONSUMER — not mentioned)
  - core/entrypoint-manifest.yaml (REGISTRY — needs acknowledgement)
  - makefiles/helpers.mk (UNCHANGED per plan)
  - makefiles/modules.mk (UNCHANGED per plan)
REQUIRES:
  - Pre-existing: pyyaml>=6.0 (in requirements.txt + pyproject.toml)
  - Pre-existing: platform-env.yaml (189 LOC, 13 networks, 17 volumes, 22 env_defaults, 14 profiles)
  - Pre-existing: 28 unit tests passing (test_unit_provision_environment.py)
  - Pre-existing: 2 smoke tests passing (test_smoke_provision_environment.py)
$END_ARTIFACT_CONTRACT

---

# Verification Report: DevPlan 073 — provision-environment.sh → Python

**Date:** 2026-07-25
**SHA:** `d37326afc64e505bb69f230465e83f9f5bef0d8a`
**Scope:** STANDARD (3 new/modified files + config/compose/entrypoint consumers)

---

> **STATUS UPDATE 2026-07-31:** SUPERSEDED — implementation committed (см. git log).
> `core/internal/provisioner.py` (389 LOC) реализован и закоммичен (`c8100e4`), `provision-environment.sh`
> мигрирован в тонкий wrapper (145 LOC, `audit_step`-диспетчер по scopes, ноль inline `python3 -c`).
> Прежний вердикт «Implementation not started» (см. ниже) отражает состояние ДО коммита реализации.
> ⚠️ Новая находка 2026-07-31: wrapper содержит stale `source core/lib/audit_logging.sh` (файл удалён
> коммитом `aa6bd61`, план 088/089) → `make provision` падает. Зарегистрировано в
> `.ai/debt/096-Residual-Debt.md` (COSMETIC C-5). Актуальный статус: DevPlan.md + новые VR.

---

## 1. Plan Self-Consistency Audit

### 1.1 File Existence Matrix

| File | Referenced in DevPlan | Exists on disk | Status |
|------|----------------------|----------------|--------|
| `core/internal/provision-environment.sh` | §1 (inline inventory), §2.2 (target) | ✅ 442 LOC | Exists — matches plan claim |
| `core/internal/provisioner.py` | §2.1 (planned NEW) | ❌ | Not yet created — expected |
| `tests/unit/test_provisioner.py` | §5.1 (planned NEW) | ❌ | Not yet created — expected |
| `platform-env.yaml` | §2 (data source) | ✅ 189 LOC | Exists |
| `core/lib/audit_logging.sh` | §2.2 (shell wrapper dependency) | ✅ | Exists |
| `core/lib/yaml_read.sh` | §1 (current fallback) | ✅ | Exists |
| `makefiles/helpers.mk` | §6 (unchanged) | ✅ | Exists — provision target at lines 63-73 |
| `makefiles/modules.mk` | §6 (unchanged) | ✅ | Exists — calls provision-environment.sh at lines 29-30 |
| `core/requirements.txt` | §8.2 (pyyaml dependency) | ✅ | Contains `pyyaml>=6.0` |
| `pyproject.toml` | §8.2 (pyyaml dependency) | ✅ | Contains `pyyaml>=6.0` + `types-pyyaml>=6.0.12` |

### 1.2 Acceptance Criteria Measurability

| AC # | Criterion | Measurable? | Evidence |
|------|-----------|-------------|----------|
| 1 | `provisioner.py` with dataclasses | ✅ | File existence + import check |
| 2 | Shell wrapper <50 LOC | ✅ | `wc -l` |
| 3 | Zero inline `python3 -c` / `<<PYEOF` | ✅ | `grep` |
| 4 | `make provision SCOPE=all` — identical behavior | ⚠️ | Requires behavioral parity tests (not specified) |
| 5 | `--dry-run` prints actions | ✅ | Output assertion |
| 6 | `SCOPE=env` exports to GITHUB_ENV | ✅ | File content assertion |
| 7 | `make up` (modules.mk) — no regression | ✅ | Smoke test |
| 8 | Unit tests (18 tests) | ✅ | `pytest` pass/fail |
| 9 | `make gate MODE=fast` — green | ✅ | Exit code |

**⚠️ Risk AC#4:** "Identical behavior" is underspecified. The current shell provisioner and new Python provisioner will produce different log format (`[IMP:9][provision]` vs `[IMP:9][provisioner]`). The DevPlan §8.1 specifies a different LDD block name (`provisioner` vs `provision`). Tests that grep for `[IMP:9][provision]` WILL break. See Finding #4.

### 1.3 Inline Python3 Count Verification

The DevPlan §1 claims 13 inline `python3 -c` blocks. Verified via `grep "python3 -c" core/internal/provision-environment.sh`: **13 matches confirmed**. All 13 are categorized correctly in the inventory table (§1).

---

## 2. Implementation Status

| Artifact | Status | Notes |
|----------|--------|-------|
| `core/internal/provisioner.py` | **NOT STARTED** | File does not exist |
| `core/internal/provision-environment.sh` (rewrite) | **NOT STARTED** | Current: 442 LOC shell with 13 inline python3 |
| `tests/unit/test_provisioner.py` | **NOT STARTED** | File does not exist |
| Pre-existing unit tests | **PASSING** | 28/28 pass (test_unit_provision_environment.py) |
| Pre-existing smoke tests | **PASSING** (2/2) | test_smoke_provision_environment.py |

**Verdict: Implementation not started.** All three planned artifacts are absent.

---

## 3. Prerequisites Check

| Prerequisite | Status | Evidence |
|-------------|--------|----------|
| `pyyaml>=6.0` installed | ✅ | `core/requirements.txt` line 5, `pyproject.toml` line 34 |
| `types-pyyaml` for type checking | ✅ | `pyproject.toml` line 45 |
| `platform-env.yaml` present and valid | ✅ | 189 LOC, 4 sections (networks, volumes, env_defaults, profiles) |
| `argparse`, `subprocess`, `pathlib`, `logging` (stdlib) | ✅ | No external deps needed beyond pyyaml |
| `audit_logging.sh` available for wrapper | ✅ | `core/lib/audit_logging.sh` |
| Docker available for runtime testing | ✅ | All 28 provision tests pass (2 use `requires_docker`) |

---

## 4. Cross-Reference Integrity

### 4.1 Unmentioned Consumers (DRIFT)

The DevPlan §6 lists only `helpers.mk` and `modules.mk` as consumers, stating "NO CHANGE" for both. However, two additional consumers directly call `provision-environment.sh`:

| Consumer | File | Line(s) | Call | DevPlan acknowledges? |
|----------|------|---------|------|----------------------|
| deploy-modules.sh | `core/internal/bootstrap/deploy-modules.sh` | 36-38 | `bash provision-environment.sh --scope networks \|\| true` | ❌ No |
| state_machine.py | `core/internal/bootstrap/lifecycle/state_machine.py` | 1164-1168 | `["bash", provision_script, "--scope", "networks", "--scope", "volumes"]` | ❌ No |

Both consumers call `provision-environment.sh` with the same interface (`--scope networks`, `--scope volumes`). Since the shell wrapper preserves this interface, they will continue to work unchanged. However, the DevPlan should acknowledge them to avoid confusion during implementation and testing.

### 4.2 Existing Test Coverage (DRIFT)

The DevPlan §5 proposes NEW test file `tests/unit/test_provisioner.py` (~180 LOC, 18 tests). However, **two existing test files** already cover the provisioner:

| Existing test file | LOC | Tests | What it tests |
|-------------------|-----|-------|---------------|
| `tests/test_unit_provision_environment.py` | 471 | 28 | Shell-based provisioner via subprocess: YAML parsing (9 tests), dry-run CLI (9 tests), Docker integration (2 tests), LDD logging (1 test), multi-scope (6 tests) |
| `tests/test_smoke_provision_environment.py` | 157 | 2 | End-to-end: network creation + volume creation + idempotency + GITHUB_ENV export |

**The DevPlan does NOT mention these existing tests or specify their fate after migration.** This is the most significant gap:

- `test_unit_provision_environment.py::TestYamlParsing` (9 tests) — tests `platform-env.yaml` structure directly via PyYAML. These tests validate the data, not the provisioner logic. They remain valid regardless of migration.
- `test_unit_provision_environment.py::TestProvisionerDryRun` (9 tests) — tests the shell CLI via subprocess. After migration, the shell wrapper preserves `--scope`, `--dry-run`, `--platform-env` flags, so these tests SHOULD still pass. HOWEVER, the log format changes (see Finding #4).
- `test_unit_provision_environment.py::TestProvisionerWithDocker` (2 tests) — Docker-dependent, should still pass (shell wrapper delegates to Python).
- `test_unit_provision_environment.py::TestProvisionerLDDLogging` (1 test) — checks for `[IMP:9][provision] Provision complete` which the new wrapper still emits. Should pass.
- `test_smoke_provision_environment.py` — end-to-end via subprocess. Should still pass (wrapper interface unchanged).

### 4.3 Test Dependency Graph Error (DRIFT)

DevPlan §9 states:
- TASK-1: dataclasses + YAML parsing
- TASK-2: provision functions + CLI `main()`
- TASK-4: unit tests

Then §10 (Parallel Groups) says:
> **Wave 2:** TASK-2, TASK-4 (parallel — TASK-4 can start after TASK-1 completes)

**This is contradictory.** TASK-4 tests `provision_networks()`, `provision_volumes()`, `provision_env()`, `provision_profiles()` — all implemented in TASK-2. Tests cannot be written against functions that don't exist. Correct dependency: TASK-4 depends on TASK-2 (not just TASK-1). The parallel group should be sequential: TASK-2 first, then TASK-4.

### 4.4 LDD Log Format Drift

DevPlan §8.1 specifies log format:
```
logger.info("[IMP:9][provisioner][networks] Networks provisioned: %d created, %d skipped", ...)
```

Current shell format:
```
[IMP:9][provision][networks] Networks provisioned: 13 created, 0 skipped
```

The block name changes from `provision` to `provisioner`. This affects:
- Existing tests that grep for `[IMP:9][provision]` — 5 assertions in `test_unit_provision_environment.py`
- CI gate tests in `test_gate_workflow_consistency.py`
- The shell wrapper's final log line (DevPlan shows `[IMP:9][provision] Provision complete` — needs to stay consistent)

**Recommendation:** Keep `provision` block name for backward compatibility, OR explicitly list all affected tests and update them.

### 4.5 LOC Estimate Mismatch

| Item | DevPlan estimate | Actual/Realistic |
|------|-----------------|-----------------|
| Shell wrapper target | ~45 LOC | DevPlan's own sample (§2.2) is **55 lines** (excluding shebang + GREP_SUMMARY) |
| provisioner.py | ~250 LOC | Dataclasses (~40) + 5 functions with full LDD logging (~250) + CLI with argparse (~50) = **~340 LOC** |
| test_provisioner.py | ~180 LOC | 18 tests with fixtures, monkeypatch, caplog assertions = **~250-300 LOC** |

### 4.6 entrypoint-manifest.yaml

`core/entrypoint-manifest.yaml` currently registers:
```yaml
- make_target: provision
  delegates_to: core/internal/provision-environment.sh
```
After migration, the delegation chain becomes `core/internal/provision-environment.sh → core/internal/provisioner.py`. The manifest `delegates_to` field should be updated to reflect this two-level delegation, or at minimum verified that gate tests still pass.

---

## 5. Test Execution Results

### 5.1 Existing Provision Tests

```
$ python -m pytest tests/test_unit_provision_environment.py -v
============================= 28 passed in 52.98s ==============================
```

All 28 tests pass against the current shell-based provisioner.

### 5.2 Gate Tests (provision-related)

```
$ python -m pytest tests/ -k "provision" -v
56 selected / 1783 deselected — all pass
```

Including:
- `test_gate_llm_provisioner.py` (2 tests) — LiteLLM key provisioning (different subsystem)
- `test_gate_workflow_consistency.py` (2 tests: `test_build_platform_uses_provisioner`, `test_push_gate_uses_provisioner`)

### 5.3 Smoke Tests

```
$ python -m pytest tests/test_smoke_provision_environment.py -v
2 passed
```

---

## 6. Findings

| # | Severity | Category | Finding | Recommendation |
|---|----------|----------|---------|----------------|
| 1 | **HIGH** | Coverage Gap | DevPlan does NOT mention existing test files (`test_unit_provision_environment.py` 471 LOC, `test_smoke_provision_environment.py` 157 LOC). After migration, these tests must be updated or at minimum verified that they still pass. | Add §6.1 "Existing Test Migration" to DevPlan specifying: which tests remain (YAML parsing), which need updating (log format assertions), which become redundant (direct subprocess-to-shell tests vs new Python unit tests). |
| 2 | **HIGH** | Consumer Drift | `deploy-modules.sh` (lines 36-38) and `state_machine.py` (lines 1164-1168) directly call `provision-environment.sh`. DevPlan §6 only lists `helpers.mk` and `modules.mk`. | Add these consumers to §6. Both are compatible with the wrapper (interface unchanged), but explicit acknowledgment prevents surprises during integration testing. |
| 3 | **MEDIUM** | Task Deps | Wave 2 groups TASK-2 and TASK-4 as parallel, but TASK-4 tests functions from TASK-2. Contradicts §9 dependency table. | Reorder: Wave 2 = TASK-2 only. Wave 3 = TASK-3 + TASK-4 (shell wrapper + tests — both depend on TASK-2). Wave 4 = TASK-5 (gate). |
| 4 | **MEDIUM** | Log Drift | DevPlan §8.1 specifies `[IMP:9][provisioner]` block name; current format is `[IMP:9][provision]`. 5+ existing tests grep for `[provision]`. | Either: (a) keep `provision` block name for backward compatibility, or (b) list all affected tests and update them in TASK-4. Option (a) preferred — reduces blast radius. |
| 5 | **MEDIUM** | LOC Estimates | DevPlan estimates shell wrapper at ~45 LOC (actual sample: 55), provisioner.py at ~250 LOC (realistic: ~340), test file at ~180 LOC (realistic: ~250). | Update estimates to realistic ranges. Not blocking but helpful for effort planning. |
| 6 | **WARNING** | Manifest | `entrypoint-manifest.yaml` delegates_to will remain `provision-environment.sh` but delegation chain becomes two-level. Manifest should reflect `provision-environment.sh → provisioner.py`. | Update `delegates_to` field post-implementation to include the Python module path, or add a `delegates_to_python` field. |
| 7 | **WARNING** | Test Data | DevPlan §5.1 sample YAML shows 2 networks; real `platform-env.yaml` has 13. Test assertions (e.g., `len(networks) >= 8`) are bare minimums that won't catch miss-counting. | Use `len(networks) == 13` for exact-match assertions in new tests, at least for the network count. Real data is stable enough for exact assertions. |
| 8 | **INFO** | Deps Verified | `pyyaml>=6.0` confirmed in both `requirements.txt` and `pyproject.toml`. All other deps are stdlib. Zero new dependency risk. | N/A — confirmed. |

---

## Final Verdict: **DRIFTED (WARNING)** — Plan needs revision before implementation

The DevPlan is structurally sound and correctly identifies the migration path, but has three gaps that should be resolved before implementation:

1. **Existing test files unaddressed** (Finding #1) — 628 LOC of existing tests need a migration strategy.
2. **Unmentioned consumers** (Finding #2) — `deploy-modules.sh` and `state_machine.py` are direct callers not listed.
3. **Task dependency error** (Finding #3) — Wave 2 parallelism is impossible (tests depend on functions from same wave).

These are plan-level issues, not code issues. They do not block starting TASK-1 (dataclasses + YAML parsing) — that task has no dependencies and is correctly specified. They block Wave 2 and beyond.

**Recommended action:** Architect revises DevPlan to add §6.1 (existing test migration), expand §6 (additional consumers), and correct §10 dependency graph. TASK-1 can proceed in parallel.

$END_VERIFICATION_REPORT
