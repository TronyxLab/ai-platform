#!/usr/bin/env bash
# GREP_SUMMARY: backup-restore-test restore-verification pg_restore audit-log non-fatal stub
# STRUCTURE: find_latest_backup → createdb → pg_dump --schema-only → count_tables → dropdb → audit_log
# region MODULE_CONTRACT
## @purpose  Stub backup restore verification — finds latest backup, restores to temp DB,
##           checks tables exist, drops temp DB, writes result to audit log.
## @scope    Runs weekly via crontab (Sunday 05:00); Phase 06 placeholder.
## @invariants
##   - Always exits 0 (informational only — must not block backup pipeline)
##   - Uses _restore_test_db as temporary database name
##   - Logs to /var/log/platform/audit.log
##   - Non-fatal: all errors logged but exit code is always 0
## @rationale Phase 06 stub — validates backup integrity minimally;
##           full row-count/constraint verification deferred to BL-4.
# endregion MODULE_CONTRACT

set -euo pipefail

TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
SPOOL_DIR="${BACKUP_SPOOL_DIR:-/var/lib/platform/backup-spool}/postgres"
TEST_DB="_restore_test_db"
PGHOST="${POSTGRES_HOST:-pgbouncer}"
PGUSER="${POSTGRES_USER:-postgres}"
RESULT="PASS"
MESSAGE=""

echo "[IMP:7][backup-restore-test][start] Starting backup restore test at ${TIMESTAMP}"

# region FIND_LATEST_BACKUP
LATEST_BACKUP="$(ls -t "${SPOOL_DIR}"/pgdumpall_*.sql.gz 2>/dev/null | head -1)" || true
if [[ -z "${LATEST_BACKUP}" ]]; then
    MESSAGE="No backup found in ${SPOOL_DIR}"
    echo "[IMP:9][backup-restore-test][find] FAIL: ${MESSAGE}" >&2
    RESULT="FAIL"
else
    echo "[IMP:8][backup-restore-test][find] Latest backup: ${LATEST_BACKUP}"
fi
# endregion FIND_LATEST_BACKUP

# region RESTORE_TEST
if [[ "${RESULT}" == "PASS" ]]; then
    echo "[IMP:7][backup-restore-test][restore] Creating test DB '${TEST_DB}' and restoring..."

    # Create test database (drop first if stale)
    PGPASSWORD="${POSTGRES_PASSWORD:-}" createdb \
        -h "${PGHOST}" \
        -U "${PGUSER}" \
        "${TEST_DB}" 2>/dev/null || \
    PGPASSWORD="${POSTGRES_PASSWORD:-}" dropdb \
        -h "${PGHOST}" \
        -U "${PGUSER}" \
        "${TEST_DB}" 2>/dev/null || true

    PGPASSWORD="${POSTGRES_PASSWORD:-}" createdb \
        -h "${PGHOST}" \
        -U "${PGUSER}" \
        "${TEST_DB}" 2>/dev/null || {
        MESSAGE="Cannot create test DB '${TEST_DB}'"
        echo "[IMP:9][backup-restore-test][restore] FAIL: ${MESSAGE}" >&2
        RESULT="FAIL"
    }

    if [[ "${RESULT}" == "PASS" ]]; then
        # Restore latest backup into test DB
        if gunzip -c "${LATEST_BACKUP}" | PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
            -h "${PGHOST}" \
            -U "${PGUSER}" \
            -d "${TEST_DB}" > /dev/null 2>&1; then

            # Check tables exist
            TABLE_COUNT="$(PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
                -h "${PGHOST}" \
                -U "${PGUSER}" \
                -d "${TEST_DB}" \
                --schema-only 2>/dev/null | grep -c "CREATE TABLE" || echo "0")"

            if [[ "${TABLE_COUNT}" -gt 0 ]]; then
                MESSAGE="Restore OK — ${TABLE_COUNT} tables verified"
                echo "[IMP:8][backup-restore-test][verify] ${MESSAGE}"
            else
                MESSAGE="Restore completed but no tables found"
                echo "[IMP:9][backup-restore-test][verify] WARN: ${MESSAGE}" >&2
            fi
        else
            MESSAGE="pg_restore failed"
            echo "[IMP:9][backup-restore-test][restore] FAIL: ${MESSAGE}" >&2
            RESULT="FAIL"
        fi
    fi
fi
# endregion RESTORE_TEST

# region CLEANUP
echo "[IMP:7][backup-restore-test][cleanup] Dropping test DB '${TEST_DB}'"
PGPASSWORD="${POSTGRES_PASSWORD:-}" dropdb \
    -h "${PGHOST}" \
    -U "${PGUSER}" \
    "${TEST_DB}" 2>/dev/null || \
    echo "[IMP:8][backup-restore-test][cleanup] Drop skipped (DB may not exist)" >&2
# endregion CLEANUP

# region AUDIT_LOG
FINAL_ENTRY="[${TIMESTAMP}] backup-restore-test RESULT=${RESULT} — ${MESSAGE} (latest_backup=${LATEST_BACKUP##*/})"
echo "${FINAL_ENTRY}" >> /var/log/platform/audit.log 2>/dev/null || \
    echo "[IMP:8][backup-restore-test][audit] Cannot write to /var/log/platform/audit.log" >&2

echo "[IMP:9][backup-restore-test][done] ${FINAL_ENTRY}"
# endregion AUDIT_LOG

exit 0
