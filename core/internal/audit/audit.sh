#!/usr/bin/env bash
# GREP_SUMMARY: audit-log stub backward-compat noop
# STRUCTURE: ▶ stub — audit_logging.sh removed (DevPlan 089) → no-op
# region MODULE_CONTRACT
## @purpose  Stub — audit_logging.sh was deleted in DevPlan 089 (replaced by Python audit_logger).
##           All audit logging now goes through core/internal/deploy/audit_logger.py.
##           This file remains as an empty stub for backwards compatibility with scripts
##           that may source it (transitive include). No-op on direct invocation.
## @changes  2026-07-30 | DevPlan 089 — Reduced to empty stub after audit_logging.sh deletion
# endregion MODULE_CONTRACT

echo "[IMP:7][audit][main] audit_logging.sh deleted — audit_log() is now in Python audit_logger.py" >&2
