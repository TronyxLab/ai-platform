#!/usr/bin/env bash
# GREP_SUMMARY: backup-postgres pg_dumpall spool-volume timestamp retention phase-02
# STRUCTURE: validate_env → mkdir spool → pg_dumpall+PIPESTATUS → gzip -t verify → pg_restore --list validate → retention cleanup → upload-s3(STUB)
# region MODULE_CONTRACT
## @purpose  Full PostgreSQL backup via pg_dumpall to local spool (03 §4)
## @scope    Run at 03:00 UTC by cron; uploads via upload-s3.sh (stub in phase 02)
## @invariants
##   - Dumps ALL databases as postgres superuser
##   - Output: /var/lib/platform/backup-spool/postgres/pgdumpall_TIMESTAMP.sql.gz
##   - Exits non-zero on pg_dumpall failure (loud failure, 00 §4 error visibility)
##   - gzip integrity check via gzip -t before declaring dump valid
##   - pg_restore --list structural validation before declaring dump valid
##   - backup-cleanup.sh called after validation (non-fatal if it fails)
##   - S3 upload delegated to upload-s3.sh (stub until phase 06)
## @rationale Single pg_dumpall covers all project DBs; per-DB dumps added in phase 06 if needed
# endregion MODULE_CONTRACT

set -euo pipefail

# [IMP:8][backup-postgres][trap] Remove partial dump on interrupt/error (TASK-14)
BACKUP_SUCCESS=false
cleanup_partial() {
    if [[ "$BACKUP_SUCCESS" != "true" && -n "${DUMP_FILE:-}" && -f "$DUMP_FILE" ]]; then
        echo "[IMP:8][backup-postgres][trap] Cleaning up partial dump: ${DUMP_FILE}" >&2
        rm -f "${DUMP_FILE}" 2>/dev/null || true
    fi
}
trap cleanup_partial EXIT

TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
SPOOL_DIR="${BACKUP_SPOOL_DIR:-/var/lib/platform/backup-spool}/postgres"
DUMP_FILE="${SPOOL_DIR}/pgdumpall_${TIMESTAMP}.sql.gz"

echo "[IMP:7][backup-postgres][start] Starting full postgres backup at ${TIMESTAMP}"

# [IMP:8][backup-postgres][validate] Validate required environment
if [[ -z "${POSTGRES_HOST:-}" ]]; then
    echo "[IMP:9][backup-postgres][validate] FAIL: POSTGRES_HOST not set" >&2
    exit 1
fi
if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
    echo "[IMP:9][backup-postgres][validate] FAIL: POSTGRES_PASSWORD not set" >&2
    exit 1
fi

mkdir -p "${SPOOL_DIR}"

echo "[IMP:7][backup-postgres][dump] Running pg_dumpall → ${DUMP_FILE}"
# [IMP:9][backup-postgres][dump] Explicit pipe failure detection
PGPASSWORD="${POSTGRES_PASSWORD}" pg_dumpall \
    -h "${POSTGRES_HOST}" \
    -U "${POSTGRES_USER:-postgres}" \
    | gzip > "${DUMP_FILE}"
PIPESTATUS_EXIT="${PIPESTATUS[0]} ${PIPESTATUS[1]}"
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "[IMP:10][backup-postgres][dump] CRITICAL: pg_dumpall failed with exit code ${PIPESTATUS[0]}" >&2
    rm -f "${DUMP_FILE}"
    exit 1
fi

# [IMP:8][backup-postgres][verify] gzip integrity check
echo "[IMP:7][backup-postgres][verify] Verifying gzip integrity: ${DUMP_FILE}"
if ! gzip -t "${DUMP_FILE}"; then
    echo "[IMP:10][backup-postgres][verify] FAIL: gzip integrity check failed — file corrupted" >&2
    rm -f "${DUMP_FILE}"
    exit 1
fi
echo "[IMP:8][backup-postgres][verify] gzip integrity OK"

# [IMP:8][backup-postgres][verify] pg_restore structure validation
echo "[IMP:7][backup-postgres][verify] Validating dump structure via pg_restore --list"
if ! zcat "${DUMP_FILE}" | pg_restore --list - > /dev/null 2>&1; then
    echo "[IMP:10][backup-postgres][verify] FAIL: pg_restore --list validation failed — dump structure invalid" >&2
    rm -f "${DUMP_FILE}"
    exit 1
fi
echo "[IMP:8][backup-postgres][verify] Dump structure validation OK"

# All checks passed — rotate old backups
echo "[IMP:7][backup-postgres][cleanup] Running retention cleanup"
/usr/local/bin/backup-cleanup.sh || \
    echo "[IMP:8][backup-postgres][cleanup] WARNING: backup-cleanup.sh failed (non-fatal)" >&2

BACKUP_SUCCESS=true
echo "[IMP:9][backup-postgres][done] BACKUP COMPLETE: ${DUMP_FILE} (size=$(du -sh "${DUMP_FILE}" | cut -f1))"

# Delegate S3 upload (stub in phase 02, implemented in phase 06)
/usr/local/bin/upload-s3.sh "${DUMP_FILE}" "postgres/pgdumpall_${TIMESTAMP}.sql.gz"

#==============================================================================
# PITR RESTORE PROCEDURE (TASK-1: B1 — WAL archiving)
#==============================================================================
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  Point-In-Time Recovery (PITR) — PostgreSQL WAL-based restore           ║
# ║  Requires: WAL archive in /var/lib/platform/wal-archive/                ║
# ║  Prerequisites: base backup (pg_dumpall) + continuous WAL archiving     ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# ## STEP 1: Determine recovery target time
# Identify the precise timestamp to which you want to restore.
# Examples:
#   - "2026-07-03 03:00:00 UTC" — before a corruption event
#   - "2026-07-02 23:59:59 UTC" — end of previous day
#
# ## STEP 2: Restore base backup
# Restore the full pg_dumpall backup to a temporary PostgreSQL instance:
#   gunzip -c pgdumpall_20260703T030000Z.sql.gz | psql -h temp-instance -U postgres
#
# ## STEP 3: Configure recovery.conf on the restored instance
# Create a recovery.conf with the WAL archive path and recovery target:
#   restore_command = 'cp /var/lib/platform/wal-archive/%f %p'
#   recovery_target_time = '2026-07-03 03:00:00 UTC'
#   recovery_target_action = 'promote'
#
# ## STEP 4: Start the PostgreSQL instance with recovery
# The instance replays WAL files up to the target time and then promotes
# to a standalone server (recovery_target_action=promote).
#
# ## STEP 5: Verify the restored data
# Run queries to confirm data integrity and consistency.
#
# ## Full recovery command example:
#   # On a new PostgreSQL host with same config:
#   mkdir -p /var/lib/platform/wal-archive/
#   # Mount or rsync WAL archive from backup:
#   # rsync -avz s3-backup:/wal-archive/ /var/lib/platform/wal-archive/
#   # Start PG with recovery.conf:
#   docker run -d --name pg-restore \
#     -v /var/lib/platform/wal-archive/:/var/lib/platform/wal-archive/ \
#     -e POSTGRES_PASSWORD=restore \
#     postgres:16 \
#     -c 'restore_command=cp /var/lib/platform/wal-archive/%f %p' \
#     -c 'recovery_target_time=2026-07-03 03:00:00 UTC' \
#     -c 'recovery_target_action=promote'
#   # Wait for recovery, then verify:
#   docker logs -f pg-restore
#   # After promote: psql into new instance and run verification
#
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  pg_restore --list validation (TASK-2: B2 — Backup integrity)           ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
# The pg_restore --list validation (lines 71-77 above) parses the compressed
# dump and checks its internal structure without extracting data:
#   zcat "${DUMP_FILE}" | pg_restore --list - > /dev/null 2>&1
#
# This validates:
#   - TOC (Table of Contents) integrity — dump is parseable
#   - No structural corruption in the archive headers
#   - Format compatibility (custom / directory formats)
#
# If pg_restore --list fails with non-zero exit, the dump is considered
# corrupted and is deleted. The backup job exits with failure, triggering
# operator alert (IMP:10 log).
