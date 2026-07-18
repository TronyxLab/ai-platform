#!/usr/bin/env bash
# GREP_SUMMARY: ssl-provision, backward-compat, wrapper, acme.sh, letsencrypt, tls, dns-01
# STRUCTURE: ▶ delegates to install-acme.sh → delegates to issue-cert.sh → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  BACKWARD-COMPAT WRAPPER for install-acme.sh + issue-cert.sh.
##           Delegates to both scripts in order: install then issue.
##           Preserved for backward compatibility — any code referencing
##           core/internal/bootstrap/ssl-provision.sh continues to work.
## @scope    Thin dispatcher only (<50 lines). See install-acme.sh and issue-cert.sh
##           for actual logic. Will be removed when all callers are migrated.
## @invariants
##   - Delegates to install-acme.sh first, then issue-cert.sh
##   - Both sub-scripts set their own __LOG_PREFIX and source libs
##   - Behavior identical to the original monolithic ssl-provision.sh
## @rationale The original ssl-provision.sh (551 lines) was split into two logical
##   scripts per D3 (DevPlan 005). This wrapper maintains backward compat for any
##   code that references ssl-provision.sh by path. New code should call the
##   sub-scripts directly.
## @changes  CREATED: 2026-07-17 · T3 — Split from original ssl-provision.sh (DevPlan 005)
##   Now a backward-compat wrapper; actual logic in install-acme.sh + issue-cert.sh
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-07-17 · — · Backward-compat wrapper for ssl-provision.sh
# · Rejected: deleting ssl-provision.sh entirely (breaks external callers by path)
# · Reason: deferred — all internal callers migrated to install-acme.sh / issue-cert.sh,
#   but ssl-provision.sh is referenced in manifest + may be called by deployed nodes.
#   Thin wrapper ensures zero behavioral regression during transition period.
# · Rev: when all deployed nodes run code ≥ this version, remove this wrapper
#   and update entrypoint-manifest.yaml to reference sub-scripts directly.

set -euo pipefail

echo "[IMP:7][ssl-provision][main] Starting SSL provision (backward-compat wrapper)" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[IMP:8][ssl-provision][main] Delegating to install-acme.sh" >&2
bash "${SCRIPT_DIR}/install-acme.sh" "$@" && {
    echo "[IMP:8][ssl-provision][main] Delegating to issue-cert.sh" >&2
    bash "${SCRIPT_DIR}/issue-cert.sh" "$@"
}
