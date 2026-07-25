# Brief 083 — Healthcheck Complete Unification

## $ARTIFACT_CONTRACT
- **PURPOSE:** Unify 9 healthcheck mechanisms → 3 canonical primitives.
- **DESCRIPTION:** check_docker_health() (liveness), check_http() (HTTP verification), exec_check() (docker exec + service tool, NEW). Standardize start_period across 13+ compose files (15s default, 30s DB, 60s litellm, 5s nginx). Eliminate 5 docker exec copy-paste sites. Fix DRIFT-H6 (deep checks ≠ Docker HEALTHCHECK) — deep mode always runs check_docker_health first, then service checks. Fix DRIFT-H7 (raw docker inspect → check_docker_health). Document unified healthcheck contract.
- **RATIONALE:** 9 different healthcheck mechanisms = 9 different failure modes.
- **ACCEPTANCE_CRITERIA:** From DevPlan.md.
- **IMPLEMENTS:** DevPlan 083.
- **IMPACTS:** 29 files (28 modified + 2 new test files).
- **REQUIRES:** Nothing (independent).

## Current Status (Audit 2026-07-25)
- **Verdict:** STABLE — Plan well-structured, self-consistent, ready for implementation (with 1 HIGH finding to resolve).
- **Implementation:** 0%. 56 healthcheck tests pass.

## Key Findings (from 02-VerificationReport.md)
- **F1 (HIGH): DevPlan internal contradiction** — §7 table says litellm start_period target = 60s, but §11 TRAP reference says 'start_period=120s for Prisma migrate → PRESERVED'. Current value is 120s with explicit TRAP[DECISION]. Dropping to 60s without testing will cause flapping.
- **F7 (MEDIUM): T16 replaces raw docker inspect with check_docker_health()** but modules-healthcheck.sh has restart-loop detection (State.Restarting, RestartCount > 5) that check_docker_health() does NOT implement — semantic regression risk.
- **I1:** Compose file count mismatch — §8 says 12 files, filesystem has 13.
- Health score 88/100. Wave 2 (T2-T15) fully parallelizable per module.

## Required Actions
1. **FIX F1:** Resolve litellm start_period contradiction — either keep 120s (preserve TRAP) or test at 60s before committing.
2. **FIX F7:** Add restart-loop detection to check_docker_health() or keep raw docker inspect for modules-healthcheck.sh restart detection.
3. Update compose file count (12 → 13).
