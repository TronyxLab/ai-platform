#!/usr/bin/env bash
# GREP_SUMMARY: spool-retry-upload thin-facade spool_retry daily-rescan REF-0009
# STRUCTURE: exec python3 /usr/local/bin/spool_retry.py "$@" → ⎋ exit code passthrough
# region MODULE_CONTRACT
## @purpose  Thin wrapper around spool_retry.py (REF-0009) — rescan/retry logic in Python.
## @scope    Run at 01:30 UTC daily by cron (flock-guarded); re-attempts off-site upload
##           for spool files left without .uploaded sentinel by failed nightly runs.
## @invariants
##   - Same-path facade contract (crontab unchanged on rename-free deploys)
##   - Exit 0 = nothing pending / all confirmed; 1 = some files remain unuploaded
##   - Fail-closed encryption contract lives in spool_retry.py/age_cipher.py
##   - 0 inline python3/heredoc-блоков — thin facade only
## @rationale Language policy: business logic in Python; shell = thin facade
## @changes  LAST_CHANGE: 2026-08-25 | REF-0009 (meta-refactoring W2) — created
# endregion MODULE_CONTRACT

set -euo pipefail

exec python3 /usr/local/bin/spool_retry.py "$@"
