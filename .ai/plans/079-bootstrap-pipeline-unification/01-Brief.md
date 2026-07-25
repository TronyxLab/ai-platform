# Brief 079 — Bootstrap Pipeline Unification

## $ARTIFACT_CONTRACT
- **PURPOSE:** Unify three duplicate bootstrap pipeline subsystems.
- **DESCRIPTION:** (DRIFT-B3) 4 entrypoints for deploy context → single deploy_context() in context_deployer.py; (DRIFT-B4) 3 content hash implementations → shared/content_hash.py; (DRIFT-B6) 2 docker compose orchestration implementations → shared/docker_compose.py (pull, build, up, healthcheck_poll, retry_pull, check_image_exists). content-hash.sh reduced to thin wrapper (127→~40 LOC). state_machine.py and steps.py refactored.
- **RATIONALE:** Triple-duplicate subsystems cause divergence in bootstrap behavior.
- **ACCEPTANCE_CRITERIA:** 10 ACs from DevPlan.md.
- **IMPLEMENTS:** DevPlan 079.
- **IMPACTS:** 10 files (4 NEW, 6 MODIFY).
- **REQUIRES:** DevPlan 070 (shared/ directory) — BLOCKED.

## Current Status (Audit 2026-07-25)
- **Verdict:** BLOCKED — prerequisite DevPlan 070 missing, implementation not started.
- **Implementation:** 0%. shared/ directory does not exist.

## Key Findings (from 01-VerificationReport.md)
- **BLOCKER: Prerequisite DevPlan 070 does NOT exist** — `core/internal/shared/` directory missing.
- **HIGH: `_extract_context_from_node_yaml()` has 3 copies** (not 2 as documented). state_machine.py:2002 has undocumented dead-code copy. TASK-10 only removes steps.py copy.
- **HIGH: `_extract_domains()` / `_extract_domains_for_context()` has 3 copies** (not 2). state_machine.py:2038 copy is actively used — TASK-10 only migrates steps.py copy, leaving state_machine copy duplicated.
- **MEDIUM:** s3_ssl_cache.py:318 has 4th `_extract_domains_from_yaml()` copy (DRIFT-B5 related, outside DevPlan scope).
- Plan structurally sound. 114 related tests pass. Task dependency graph acyclic. 17 test functions specified.

## Required Actions
1. **UNBLOCK:** Implement DevPlan 070 first (shared/ directory).
2. **FIX:** Update TASK-10 to address all 3 copies of `_extract_context_from_node_yaml()`, not just steps.py.
3. **FIX:** Update TASK-10 to migrate state_machine.py:2038 `_extract_domains()` copy.
