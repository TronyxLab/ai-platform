# Brief 072 — Secrets Atomic Write

## $ARTIFACT_CONTRACT
- **PURPOSE:** Fix secrets_manager.py append-mode bug (`open(secrets_env, "a")` at line 312 — creates duplicate lines on repeated --force runs).
- **DESCRIPTION:** Read existing env FIRST via source_secrets_env, merge with newly generated secrets, atomic write (tmp + rename). Remove LITELLM_METRICS_TOKEN from .env.example:129. Add idempotency + preserve-non-generated tests.
- **RATIONALE:** Append-mode causes accumulation of duplicate lines on repeated runs — silent corruption.
- **ACCEPTANCE_CRITERIA:** From DevPlan-expanded.md.
- **IMPLEMENTS:** DevPlan 072.
- **IMPACTS:** secrets_manager.py, .env.example.
- **REQUIRES:** Nothing (independent, but should merge BEFORE 084 to avoid collision on .env.example:129).

## Current Status (Audit 2026-07-25)
- **Verdict:** STABLE — actionable immediately.
- **Implementation:** 0% (не начата).
- **Test baseline:** 5/5 tests pass.

## Key Findings (from 03-VerificationReport.md)
- **W1:** 01-DevPlan IMPLEMENTS line missing DRIFT qualifiers present in expanded version.
- **W2:** Stale invariant in MODULE_CONTRACT — says «Appends generated VAR=VALUE pairs» contradicts post-fix behavior.
- **W3:** Non-standard TRAP[BUSINESS] tag in proposed code — should be TRAP[BUG].
- **Cross-ref:** 084 has merge collision risk on .env.example:129 — 072 should merge first.
- **LITELLM_METRICS_TOKEN:** only dead reference — safe to remove.

## Required Actions
1. Implement atomic write for secrets_manager.py.
2. Fix MODULE_CONTRACT invariant post-fix.
3. Merge BEFORE 084 to avoid collision.
