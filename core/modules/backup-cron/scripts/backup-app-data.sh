#!/usr/bin/env bash
# GREP_SUMMARY: backup-app-data file-volumes spool tar.gz phase-02 stub
# STRUCTURE: enumerate app volumes → tar.gz each → save to spool → upload-s3(STUB)
# region MODULE_CONTRACT
## @purpose  Backup application data volumes (file uploads, configs, etc.)
## @scope    Run at 03:30 UTC by cron; uploads via upload-s3.sh (stub in phase 02)
## @invariants
##   - In phase 02: stub implementation (no real projects yet)
##   - Real volume enumeration implemented in phase 07 when projects exist
##   - Output: /var/lib/platform/backup-spool/app-data/app_TIMESTAMP.tar.gz
##   - S3 upload delegated to upload-s3.sh (stub until phase 06)
## @rationale placeholder required for phase 02 contract; filled in phase 07 per project list
# endregion MODULE_CONTRACT

set -euo pipefail

# [IMP:8][backup-app-data][trap] Cleanup on interrupt (TASK-14)
cleanup_partial() {
    local exit_code=$?
    echo "[IMP:8][backup-app-data][trap] Backup interrupted — cleaning up" >&2
    exit "$exit_code"
}
trap cleanup_partial EXIT SIGINT SIGTERM

TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
SPOOL_DIR="${BACKUP_SPOOL_DIR:-/var/lib/platform/backup-spool}/app-data"

echo "[IMP:7][backup-app-data][start] Starting app-data backup at ${TIMESTAMP}"

mkdir -p "${SPOOL_DIR}"

# [IMP:8][backup-app-data][stub] Phase 02 stub — no projects deployed yet
# Phase 07 will enumerate /var/lib/platform/projects/ and back up each volume
echo "[IMP:8][backup-app-data][stub] STUB: No app-data volumes to back up in phase 02"
echo "[IMP:8][backup-app-data][stub] Phase 07 will implement per-project volume backup"

# Create an empty marker to confirm the job ran
touch "${SPOOL_DIR}/.backup_ran_${TIMESTAMP}"

echo "[IMP:9][backup-app-data][done] App-data backup job completed (stub) at ${TIMESTAMP}"
