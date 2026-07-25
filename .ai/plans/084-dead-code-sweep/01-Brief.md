# Brief 084 — Dead Code Sweep

## $ARTIFACT_CONTRACT
- **PURPOSE:** Remove all dead/deprecated code remaining after waves 071, 072, and 080.
- **DESCRIPTION:** Delete nginx/install.sh (1107 LOC). Delete ssl-provision.sh (40 LOC backward-compat wrapper). Remove LITELLM_METRICS_TOKEN from .env.example. Add CI gate make check-dead-code. Clean up all referencing files.
- **RATIONALE:** Dead code clutters the codebase, confuses agents. Post-cleanup wave.
- **ACCEPTANCE_CRITERIA:** From DevPlan.md.
- **IMPLEMENTS:** DevPlan 084.
- **IMPACTS:** 11 files (2 delete, 7 modify, 2 create).
- **REQUIRES:** DevPlans 071 (done-migration), 072, 080 (cert functions migrated).

## Current Status (Audit 2026-07-25)
- **Verdict:** PARTIAL — Plan actionable but contains factual inaccuracy in dependency analysis.
- **Implementation:** 0%. 5/5 existing gate tests pass.

## Key Findings (from 02-VerificationReport.md)
- **F1 (HIGH): Plan drift** — §2.1 claims node-lifecycle.sh sources ssl-provision.sh for WEBNAMES_API_KEY loading. Actual code: update_step_3_ssl_provision() sources $secrets_env directly (L84), NOT ssl-provision.sh. The 'migrate key loading' step (T2) has ALREADY been done. Simplify T2: skip key-loading migration, proceed directly to git rm + cleanup.
- **F2 (MEDIUM):** nginx/install.sh has 2 references in template-manifest.yaml (L52, L62) — T1 accounts for these.
- **F3 (LOW):** scp-deliver.sh L84 has 'DEPRECATED' in echo log message, not structural marker — function prepare_ssh_opts() still exists and is called.
- All dead code claims verified genuine — nginx/install.sh truly dead (0 direct callers), ssl-provision.sh truly dead, LITELLM_METRICS_TOKEN truly dead (0 consumers).

## Required Actions
1. **FIX F1:** Update §2.1 — skip key-loading migration, simplify T2.
2. Decide on scp-deliver.sh DEPRECATED log message in T7 (keep or remove).
