# Brief 082 — Configuration & Env Defaults Unification

## $ARTIFACT_CONTRACT
- **PURPOSE:** Eliminate 7 systemic configuration drift points (DRIFT-E1 through E8, E3 deferred to 078) plus 1 language policy violation (F4).
- **DESCRIPTION:** Establish platform-infra.yaml as Source-of-Truth for non-secret env defaults. Extend generate_platform_env.py. Create sync_env_defaults.py for .env.example auto-generation. Fix: POSTGRES_PASSWORD (4 conflicting defaults → 1), S3_ENDPOINT_URL (cyclic fallback + 2 hosts, 5 Python files cleaned), NEXTAUTH_SECRET (deferred to 078 with precondition skip), 3 Jinja2 mechanisms documented, NODE vs NODE_NAME naming, PLATFORM_DOMAIN default divergence, NO_PROXY drift, GF_SECURITY_ADMIN_USER chain fallback. Extract inline python3 heredoc from gen-env-platform.sh (Tier 1 Strangler). Add CI gate test_gate_env_example_drift.py.
- **RATIONALE:** 8 different configuration drift points cause silent env misconfiguration in CI and production.
- **ACCEPTANCE_CRITERIA:** From DevPlan.md.
- **IMPLEMENTS:** DevPlan 082.
- **IMPACTS:** 25 files (5 new/modified logic, 5 auto-regenerated, 12 drift-fixed, 2 new Python modules, 1 new test file).
- **REQUIRES:** DevPlan 078 (secret defaults unified, NEXTAUTH_SECRET → ci_default) — NOT a hard dependency: gate test skips NEXTAUTH_SECRET validation if 078 marker absent (exit 0).

## Current Status (Audit 2026-07-25, Updated)
- **Verdict:** READY — All PARTIAL gaps fixed. E3 scoped out with precondition skip. Scope gaps (F2, F3, F4) addressed in TASK-5, TASK-4, TASK-6 respectively.
- **Implementation:** 0%. Health score 92/100.
- **POSTGRES_PASSWORD drift:** 4 different values (documented as 4, not 6).

## Key Findings (from VerificationReport.md) — ALL FIXED
- **F1 (HIGH) — FIXED:** DRIFT-E3 (NEXTAUTH_SECRET) scoped out of 082, deferred to 078 with gate precondition skip (exit 0 if 078 marker absent).
- **F2 (MEDIUM) — FIXED:** TASK-5 extended to cover all 5 Python files with S3_ENDPOINT fallbacks (backup_config.py, preflight.py, s3_ssl_cache.py, test_backup_config.py, test_ssl_s3_cache.py).
- **F3 (MEDIUM) — FIXED:** hermes-agent/.env.example:70 added to TASK-4 File list for POSTGRES_PASSWORD alignment.
- **F4 (MEDIUM) — FIXED:** Inline python3 heredoc in gen-env-platform.sh extracted to gen_env_platform.py (Tier 1 Strangler), added to TASK-6.
- POSTGRES_PASSWORD drift count corrected: 4 different values (not 6).

## Required Actions (ALL COMPLETE)
1. ~~UNBLOCK: Implement DevPlan 078 first (or remove DRIFT-E3 from scope).~~ → E3 scoped out with precondition skip.
2. ~~FIX F2: Extend TASK-5 to cover all 5 Python files with S3_ENDPOINT fallbacks.~~ → Done.
3. ~~FIX F3: Add hermes-agent/.env.example:70 to TASK-4.~~ → Done.
4. ~~FIX F4: Extract inline python3 heredoc from gen-env-platform.sh (language policy).~~ → Added to TASK-6.
