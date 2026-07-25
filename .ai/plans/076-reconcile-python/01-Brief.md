# Brief 076 — Reconcile Python Migration

## $ARTIFACT_CONTRACT
- **PURPOSE:** Migrate reconcile-projects.sh (~278 LOC, 6 inline python3 calls) to Python module reconciler_projects.py.
- **DESCRIPTION:** Post-bootstrap stub→deployed recovery: detects stub projects in /opt/projects/, checks GHCR for Docker images, deploys if found via SSH payload delivery and docker compose up. Shell wrapper <30 LOC. Separate module from existing reconciler.py R3 (local stub creation vs remote deploy).
- **RATIONALE:** Eliminate inline python3, separate concerns (local vs remote).
- **ACCEPTANCE_CRITERIA:** From DevPlan-expanded.md.
- **IMPLEMENTS:** DevPlan 076 (01-DevPlan.md stub + 02-DevPlan.md authoritative, 1044 LOC).
- **IMPACTS:** reconcile-projects.sh, reconciler_projects.py (NEW), converge.sh (zero changes needed).
- **REQUIRES:** Nothing (independent, but can benefit from shared/ after 070).

## Current Status (2026-07-25 — post-VerificationReport fixes)
- **Verdict:** FIXED — ready for implementation.
- **Implementation:** 0% (не начата). DevPlan spec corrected.

## Key Findings (from VerificationReport.md — ALL FIXED in 02-DevPlan.md)
- ✅ **CRITICAL: `exec python3` FIXED** — replaced with `python3` + `local rc=$?; return $rc`. Added TRAP[BUSINESS] documenting why `exec` must never be used in a sourced function.
- ✅ **WARNING: NODE_HOST_MAP forwarding FIXED** — shell wrapper now accepts 4th optional positional arg `node_host_map` (falls back to `$NODE_HOST_MAP` env var) and passes `--node-host-map` to Python CLI.
- ✅ **WARNING: Return code propagation FIXED** — `local rc=$?; return $rc` after `python3` invocation.
- ✅ **WARNING: SSH user centralization FIXED** — `SSH_USER = "ci-deploy"` module constant; `deliver_payload` and `deploy_project` both reference it. Added TRAP[DECISION] documenting the centralization rationale.
- Design decision for separate module (not merge into reconciler.py R3) verified correct.
- 64/64 existing reconcile tests pass (baseline clean).

## Required Actions
No further actions — all VerificationReport findings resolved in 02-DevPlan.md. Ready for Coder implementation.
