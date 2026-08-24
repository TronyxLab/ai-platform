#!/usr/bin/env bash
# GREP_SUMMARY: backup-cleanup thin-facade cleanup_spool sentinel-gated retention REF-0009
# STRUCTURE: exec python3 /usr/local/bin/cleanup_spool.py "$@" → ⎋ exit code passthrough
# region MODULE_CONTRACT
## @purpose  Thin wrapper around cleanup_spool.py (REF-0009, language policy) — all
##           retention/sentinel logic lives in the Python module.
## @scope    Run at 04:00 UTC by cron; also invoked post-dump by backup_postgres.py.
## @invariants
##   - Same path /usr/local/bin/backup-cleanup.sh — _CLEANUP_SCRIPT/crontab unchanged
##   - Sentinel-gated deletion contract in cleanup_spool.py: files WITHOUT .uploaded
##     sentinel are NEVER deleted by age (BUG-0802); .last_verified never touched
##   - Python exit code propagated (0 = ok, 1 = fatal)
##   - 0 inline python3/heredoc-блоков — thin facade only
## @rationale Strangler-Fig Tier-1: sentinel-ветвление (>3 бизнес-веток) — в Python;
##            shell остаётся фасадом (языковая политика)
## @changes
##   LAST_CHANGE: 2026-08-25 | REF-0009 (meta-refactoring W2) — логика перенесена в
##   cleanup_spool.py: cleanup удаляет ТОЛЬКО подтверждённо загруженное (.uploaded).
# endregion MODULE_CONTRACT

set -euo pipefail

exec python3 /usr/local/bin/cleanup_spool.py "$@"
