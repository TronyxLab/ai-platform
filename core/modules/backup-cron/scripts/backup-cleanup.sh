#!/usr/bin/env bash
# GREP_SUMMARY: backup-cleanup spool retention 7-day find-delete phase-02 cron-04:00
# STRUCTURE: validate spool → find files older than 7d → delete → log count
# region MODULE_CONTRACT
## @purpose  Remove backup spool files older than retention period (03 §3: 7-day daily retention)
## @scope    Run at 04:00 UTC by cron; runs AFTER both backup jobs to avoid mid-run deletion
## @invariants
##   - 7-day retention for daily backups (03 §3 tier 1)
##   - find -mtime +7: files older than 7 days
##   - Only deletes from /var/lib/platform/backup-spool/ — never touches live data
##   - 28-day and 90-day retention tiers reserved for phase 06 (S3-managed)
##   - Collision with active backup: v1 allows parallel start (03 §8)
## @rationale 7d local spool prevents disk exhaustion while S3 holds longer retention (03 §3)
# endregion MODULE_CONTRACT

set -euo pipefail

SPOOL_DIR="${BACKUP_SPOOL_DIR:-/var/lib/platform/backup-spool}"
TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
RETENTION_DAYS=7

echo "[IMP:7][backup-cleanup][start] Starting spool cleanup at ${TIMESTAMP} (retention: ${RETENTION_DAYS}d)"

if [[ ! -d "${SPOOL_DIR}" ]]; then
    echo "[IMP:8][backup-cleanup][skip] Spool directory does not exist: ${SPOOL_DIR}"
    exit 0
fi

# [IMP:8][backup-cleanup][find] Find and delete files older than RETENTION_DAYS
DELETED_COUNT=0
while IFS= read -r -d '' old_file; do
    echo "[IMP:7][backup-cleanup][delete] Removing: ${old_file}"
    rm -f "${old_file}"
    DELETED_COUNT=$(( DELETED_COUNT + 1 ))
done < <(find "${SPOOL_DIR}" -type f -mtime "+${RETENTION_DAYS}" -print0)

echo "[IMP:9][backup-cleanup][done] Cleanup complete: deleted=${DELETED_COUNT} files (older than ${RETENTION_DAYS}d)"
