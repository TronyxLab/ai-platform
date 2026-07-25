# Brief 074 — Monitoring Hooks Python Migration

## $ARTIFACT_CONTRACT
- **PURPOSE:** Migrate on-project-deploy.sh (413 LOC, 19 inline python3 calls — WORST violator in codebase) to Python module `core/internal/monitoring_config_renderer.py`.
- **DESCRIPTION:** Eliminate ALL 19 inline python3 calls. Reduce shell to <30 LOC thin wrapper. Python: 3-level config merge, Prometheus SD targets, Grafana dashboards, Loki retention YAML, Langfuse project creation, alert rules, catalog refresh, service reload. 21 unit tests planned.
- **RATIONALE:** Worst inline python3 violator — 19 calls in single file.
- **ACCEPTANCE_CRITERIA:** From DevPlan.md.
- **IMPLEMENTS:** DevPlan 074.
- **IMPACTS:** on-project-deploy.sh, deploy-project.sh, module.yaml (NO changes to callers).
- **REQUIRES:** Nothing (self-contained).

## Current Status (Audit 2026-07-25)
- **Verdict:** STABLE — complete, self-consistent blueprint. Ready for Coder delegation.
- **Implementation:** 0% (не начата).

## Key Findings (from VerificationReport.md)
- **W1 (F1):** deep_merge doc inconsistency — says 'SHALLOW merge at the top level' but describes recursive deep merge. Fix: remove 'SHALLOW'.
- **F2 (HIGH):** Pre-existing test failure — test_module_yaml_contract (unrelated, stale EXPECTED_ENV_REQUIRES missing LITELLM_MASTER_KEY).
- **F3-F4:** INFO only, non-blocking.
- **Cross-references:** 9/9 verified.
- **Self-consistency:** 8/9.

## Required Actions
1. Fix W1: remove 'SHALLOW' language from deep_merge doc.
2. Fix F2: update EXPECTED_ENV_REQUIRES (separate from 074 scope, but document dependency).
