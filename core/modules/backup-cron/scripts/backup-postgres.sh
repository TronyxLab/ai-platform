#!/usr/bin/env bash
# GREP_SUMMARY: backup-postgres thin-wrapper pg_dumpall python-port
# STRUCTURE: exec python3 /usr/local/bin/backup_postgres.py "$@" → ⎋ exit code passthrough
# region MODULE_CONTRACT
## @purpose  Thin wrapper around backup_postgres.py (DevPlan 117 H D64) — all
##           backup logic (pg_dumpall, gzip -t, pg_restore --list, retention,
##           S3 upload) lives in the Python module.
## @scope    Run at 03:00 UTC by cron and by `make backup` via docker exec.
## @invariants
##   - Same path /usr/local/bin/backup-postgres.sh — crontab/Makefile unchanged
##   - Python exit code propagated (0 = ok, 1 = fatal)
##   - 0 inline python3/heredoc-блоков — thin facade only
## @rationale Strangler-Fig: 153 LOC shell → 10-line wrapper + ~180 LOC Python
##            (language policy: business logic in Python, shell = thin facade)
## @changes
##   LAST_CHANGE: 2026-08-02 | Rewritten as thin wrapper (DevPlan 117 Brief H D64)
# endregion MODULE_CONTRACT

set -euo pipefail

exec python3 /usr/local/bin/backup_postgres.py "$@"
