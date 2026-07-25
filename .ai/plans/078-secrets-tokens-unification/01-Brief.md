# Brief 078 — Secrets & Tokens Unification

## $ARTIFACT_CONTRACT
- **PURPOSE:** Eliminate 7 systemic drift points in secrets/tokens domain (DRIFT-S1 through S7).
- **DESCRIPTION:** Unify age-key detection (5→1 copies via shared/age_key.py), htpasswd generation (2→1 via shared/crypto.py), sync _FALLBACK_SECRETS with secret-definitions.yaml, fix Docker token leak in /proc/cmdline (S4, CRITICAL), resolve 5 naming conflicts, unify POSTGRES_PASSWORD (6→1 default) and NEXTAUTH_SECRET (4→1 default).
- **RATIONALE:** Token leak (DRIFT-S4) is CRITICAL security issue — Docker Hub token exposed in /proc/cmdline via bash -c.
- **ACCEPTANCE_CRITERIA:** 23 ACs from DevPlan.md.
- **IMPLEMENTS:** DevPlan 078.
- **IMPACTS:** 22 files (4 CREATE, 18 MODIFY).
- **REQUIRES:** DevPlan 070 (shared/__init__.py) — BLOCKED.

## Current Status (Audit 2026-07-25)
- **Verdict:** PREREQUISITES BLOCKED — DevPlan NOT STARTED.
- **Implementation:** 0%. core/internal/shared/ directory missing.

## Key Findings (from VerificationReport.md)
- **BLOCKER: DevPlan 070 (shared/__init__.py) does NOT exist** — core/internal/shared/ directory missing. Blocks T1, T2, T3, T4, T5, T6.
- **WARNING:** DevPlan 072 not yet merged — LITELLM_METRICS_TOKEN still in .env.example (merge order preference).
- **HIGH: DRIFT-S4 (token leak)** still open — docker_registry_auth.py:159 uses bash -c exposing token in /proc/cmdline. 1-line fix independent of other tasks, could be extracted as hotfix.
- Plan self-consistent. All 7 DRIFT points verified accurate. Test suite: 107/107 unit tests pass, 21/22 gate tests pass.

## Required Actions
1. **UNBLOCK:** Implement DevPlan 070 first (shared/ directory).
2. **HOTFIX candidate:** Extract DRIFT-S4 (token leak) as independent security hotfix — не требует shared/.
3. Merge 072 before 078 (merge order preference).
