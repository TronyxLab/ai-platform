# Brief 081 — Deploy Pipeline Unification

## $ARTIFACT_CONTRACT
- **PURPOSE:** Unify deploy pipeline: document 7 code delivery paths, add retry+rollback, unify SSH_ORIGINAL_COMMAND parser, extract platform-deliver builder, unify audit log format.
- **DESCRIPTION:** Closes DRIFT-D1, D3, D4, D5, D6. Creates 4 new shared modules (ssh_command_parser.py, platform_deliver.py, audit_logger.py, deploy_paths.py) + 6 test files + 1 gate test. Refactors deploy.sh, deploy-project.sh, reconcile-projects.sh, context_deployer.py, docker_orchestrator.py.
- **RATIONALE:** 7 different code delivery paths with no unified audit trail.
- **ACCEPTANCE_CRITERIA:** From DevPlan.md.
- **IMPLEMENTS:** DevPlan 081.
- **IMPACTS:** 14 files (4 NEW shared, 6 tests, 1 gate, 3 refactored).
- **REQUIRES:** DevPlan 079 (shared/docker_compose.py with retry_pull()) — BLOCKED.

## Current Status (Audit 2026-07-25)
- **Verdict:** DRIFTED (CRITICAL) — Prerequisite DevPlan 079 not implemented, plan is blocked.
- **Implementation:** 0%. 236 passed, 3 failed (hermes init — unrelated).

## Key Findings (from 02-VerificationReport.md)
- **CRITICAL: DevPlan 079 NOT implemented** — core/internal/shared/ does not exist. All 4 NEW shared module files cannot be created.
- **CRITICAL: TASK-11 (retry_pull) и TASK-12 (audit_logger) require shared/docker_compose.py with retry_pull()** — does not exist.
- **CRITICAL: DevPlan 081 CANNOT PROCEED until DevPlan 079 Wave 1 is implemented** (at minimum TASK-6).
- **MEDIUM:** TASK-11 and TASK-12 have no planned unit tests — coverage gap.
- Plan self-consistent but BLOCKED. DEPRECATED_DEPLOY_PATHS missing explicit removal plan for 'Bootstrap compose stub'.

## Required Actions
1. **UNBLOCK:** Implement DevPlan 079 Wave 1 first (minimum TASK-6 → docker_compose.py with retry_pull).
2. Add unit tests for TASK-11 and TASK-12.
3. Add explicit removal plan for 'Bootstrap compose stub' in DEPRECATED_DEPLOY_PATHS.
