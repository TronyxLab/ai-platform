# Findings 10 — Architectural hotspots

> Provenance: run-c (2026-08-24 re-run). Restored after run-a sweep (original lost in `attic/` sweep; rewritten verbatim from session record).

Method caveat: git history was reset (`feat(release): 1.0.0`, 2026-08-16) — 49 commits total, all 2026-08-16→18. Churn measures post-consolidation activity; TRAP annotations carry pre-reset pain history.

## TOP-10 hotspot table (score = Σ normalized churn + fixes + pain-TRAPs + suppressions)

| # | File | commits90d | fixes | TRAP | ignores | LOC | Score |
|---|------|-----------|-------|------|---------|-----|-------|
| 1 | `tests/_conftest/compose.py` | 5 | 3 | 12 | 6 | 1261 | 2.26 |
| 2 | `core/check-suite.yaml` | 9 | 6 | 2 | 0 | 252 | 2.17 |
| 3 | `.github/workflows/platform-test.yml` | 9 | 5 | 2 | 0 | 492 | 2.00 |
| 4 | `tests/test_component_hermes.py` | 3 | 1 | 12 | 0 | 1094 | 1.50 |
| 5 | `core/internal/shared/notifications.py` | 3 | 1 | 6 | 13 | 753 | 1.45 |
| 6 | `bootstrap/lifecycle/secrets_manager.py` | 1 | 0 | 7 | 15 | 973 | 1.21 |
| 7 | `bootstrap/lifecycle/phases/system.py` | 1 | 0 | 2 | 27 | 1236 | 1.21 |
| 8 | `tests/gates/test_gate_ci_env_vars.py` | 6 | 3 | 0 | 0 | 211 | 1.17 |
| 9 | `tests/test_smoke_infra_metrics.py` | 2 | 1 | 9 | 0 | 510 | 1.14 |
| 10 | `core/internal/scaffold/project_scaffolder.py` | 4 | 2 | 4 | 0 | 928 | 1.11 |

Churn-weighted (commits × LOC): `entrypoint-manifest.yaml` dominates (12,780; generated registry — expected).

## ARCH-0045 — Docker-smoke execution contract duplicated across 3 files
- **Severity:** P1 · **Confidence:** 0.85 · **Churn:** M · **Phase:** pre-launch
- **Files:** `core/check-suite.yaml:194-224` · `tests/_conftest/compose.py:152-157` · `.github/workflows/platform-test.yml:328-340`
- **Evidence:** the 2026-08-17 "900s-hang" incident series fixed ALL three files in one day (≥10 commits: xdist:false, per-test timeout=600, pre-cleanup, faulthandler probes, alloy OOM). In-repo admission: `check-suite.yaml:210` — `TRAP[BUG] xdist-hang: xdist: false — дрейф от канона`. DevPlan 007 = langfuse CI-crash localization.
- **Failure scenario:** "how docker test suites execute" (single-process, timeouts, pre-cleanup, log streaming) is coded independently in manifest, fixtures, and workflow; changing one without the other two reproduces the hang class.
- **Impact:** each CI regression costs ~an engineer-day (006/007 series); hangs invisible until the 40-min job timeout.
- **Minimal fix:** check-suite.yaml owns execution parameters (`xdist:`, add `timeout_s`, `pre_cleanup`); compose.py + workflow read them; parity gate (pattern exists: `test_gate_workflow_consistency`).

## ARCH-0046 — `tests/_conftest/compose.py` is a hidden module orchestrator disguised as a pytest fixture
- **Severity:** P2 · **Confidence:** 0.8 · **Churn:** M · **Phase:** pre-launch
- **Files:** `tests/_conftest/compose.py` (1261 LOC; self-listed "гигантский" in TEST_ALLOWLIST, limit 1300); 12 TRAPs with recurring failure classes: stale containers blocking startup (HI ×2), `down --remove-orphans` killing neighbor modules (HI), compose transient-failure retry, MinIO --wait workaround, cold-cache pre-build
- **Evidence:** `_pre_cleanup`, `_start_waves`, `_module_start_with_retry`, `_module_health_poll`, `_rm_stale` = full lifecycle-management cycle, not test setup.
- **Impact:** highest pain-TRAP score in repo; every new docker failure mode adds a patch to the fixture; changes break parallel/CI runs for all docker suites.
- **Minimal fix:** extract `_start_waves/_module_start_with_retry/_pre_cleanup/_rm_stale` → `tests/helpers/module_lifecycle.py` (or `core/internal/testinfra/`); fixtures stay thin.

## ARCH-0047 — Type-suppression concentration in bootstrap/lifecycle
- **Severity:** P2 · **Confidence:** 0.7 · **Churn:** M–L · **Phase:** post-launch
- **Files:** `phases/system.py` (27 ignores/1236 LOC), `deploy_orchestrator.py` (16/1059), `secrets_manager.py` (15/973), `monitoring/config_renderer.py` (14/796), `notifications.py` (13/753)
- **Evidence:** lifecycle phases handle untyped boundaries (subprocess output, YAML payloads, env) — each suppresses locally instead of shared typed result models. Suppressions hide exactly the integration-error class behind remote_executor (10 pain TRAPs) and core_deliverer (9).
- **Minimal fix:** `ExecResult`/`PhaseResult` protocol on `shared/subprocess_io.py`; remove suppressions file-by-file starting with system.py.

## ARCH-0048 — Manifest coordination friction (check-suite.yaml / entrypoint-manifest.yaml)
- **Severity:** P3 · **Confidence:** 0.7 · **Churn:** S · **Phase:** post-launch
- **Files:** `core/check-suite.yaml` (9 commits, 6 fixes — highest net churn; 2 in-file TRAP[DECISION] on gate modes) · `entrypoint-manifest.yaml` (2556 LOC, churn-weighted #1) · `tests/gates/test_gate_workflow_consistency.py` (churn 4)
- **Evidence:** every CI behavior change requires coordinated edits to manifest + golden + gate entry. Working as designed (gate blocks divergence) — coordination cost, not fragility. No structural fix needed; watch file size; keep `make generate-entrypoint-manifest` flow.

Minor: `tests/test_component_hermes.py` (12 TRAPs) and `test_smoke_infra_metrics.py` (9 TRAPs) — known documented pain, decomposition already scheduled in TEST_ALLOWLIST notes; execute the planned per-scenario/per-domain split. Empty signals verified: `fp_registry.yaml` → `rules: []`; `.trivyignore` absent/empty; LOC allowlist covers only 3 core + 6 test giants.
