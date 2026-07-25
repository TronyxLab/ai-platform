# Brief 080 — Certs & SSL Complete Unification

## $ARTIFACT_CONTRACT
- **PURPOSE:** Eliminate 8 certificate/SSL drift points (DRIFT-C1 through C8 + B2).
- **DESCRIPTION:** Unify to single cert issuance pipeline via cert_orchestrator.py as single entry point for all TLS operations. Delete nginx/install.sh (1107 LOC dead code). Delete orphaned templates/platform-default.conf.template. Unify dev cert filenames to fullchain.pem/privkey.pem. Align platform-vhost.conf to wildcard cert. Add migrate_cron_if_needed(). Document template syntax contract + CI gate.
- **RATIONALE:** Multiple SSL subsystems drifted independently; nginx/install.sh is dead code since nginx is Docker-based.
- **ACCEPTANCE_CRITERIA:** From DevPlan.md.
- **IMPLEMENTS:** DevPlan 080.
- **IMPACTS:** 19 files (2 DELETE, 14 MODIFY, 3 NEW).
- **REQUIRES:** Nothing (independent, but 084 depends on completing 080 first).

## Current Status (Audit 2026-07-25)
- **Verdict:** STABLE — Plan validated, ready for implementation.
- **Implementation:** 0%. 5 environmental test failures (no Docker nginx locally).
- **Risk:** Low (TASK-1, 2, 6, 7, 8), Medium (TASK-3, 4), High (TASK-5 cross-cutting).

## Key Findings (from 02-VerificationReport.md)
- Pre-existing architecture already has _ssl_provision_via_orchestrator() as canonical path.
- 5 test failures are environmental (nginx container not running on Docker port 18080) — NOT code defects.
- 132 tests pass, 5 fail (all environmental).

## Required Actions
1. Implement 4 waves, 9 tasks per DevPlan.
2. Not blocked by any other plan — can proceed independently.
3. 084 (Dead Code Sweep) depends on this plan completing nginx/install.sh deletion.
