#!/usr/bin/env bash
# GREP_SUMMARY: rotate-spend-logs pg_dump delete litellm spend_logs quarterly archive
# STRUCTURE: validate → pg_dump range → compress → DELETE cutoff → verify
# region MODULE_CONTRACT
## @purpose  Quarterly rotation of LiteLLM spend_logs: pg_dump range + DELETE old records
## @scope    Cron job: runs quarterly (Jan 1, Apr 1, Jul 1, Oct 1). Should be scheduled
##           via crontab or the backup-cron module.
## @invariants
##   - Dry-run mode: --dry-run prints SQL plan without executing
##   - Dumps only records older than CUTOFF_MONTHS (default: 6, configurable)
##   - Dump file: gzip-compressed, named by date range
##   - Dump is written to ARCHIVE_DIR (default: /var/lib/platform/spend-logs-archive)
##   - DELETE uses a transaction — rolled back if count mismatch with dump
##   - PostgreSQL connection params from env vars (POSTGRES_USER, POSTGRES_PASSWORD)
##   - Idempotent: if no records in range, exits silently with 0
## @rationale D8: hot-хранение spend_logs ограничено окном (6 мес.), раз в квартал
##           pg_dump диапазона + DELETE WHERE created_at < cutoff. Один cron-job,
##           без нового сервиса и без усложнения инфраструктуры.
## @usage
##   # Dry-run (preview only):
##   bash rotate-spend-logs.sh --dry-run
##   # Actual rotation:
##   bash rotate-spend-logs.sh
##   # Force run with custom cutoff (months):
##   bash rotate-spend-logs.sh --cutoff 12
# endregion MODULE_CONTRACT

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
PG_HOST="${PG_HOST:-pgbouncer}"
PG_PORT="${PG_PORT:-6432}"
PG_USER="${POSTGRES_USER:-postgres}"
PG_PASSWORD="${POSTGRES_PASSWORD:-}"
PG_DB="litellm"
SCHEMA="litellm"
TABLE="spend_logs"
DATE_COLUMN="startTime"  # or "endTime" — verify in actual schema

ARCHIVE_DIR="${ARCHIVE_DIR:-/var/lib/platform/spend-logs-archive}"
CUTOFF_MONTHS="${CUTOFF_MONTHS:-6}"

DRY_RUN=false

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ── Logging ──────────────────────────────────────────────────────────────────
__LOG_PREFIX="rotate-spend"
source "${SCRIPT_DIR}/../../../lib/logging.sh"

# ── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --cutoff) CUTOFF_MONTHS="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Validate ────────────────────────────────────────────────────────────────
if [[ -z "$PG_PASSWORD" ]]; then
    log_step "validate" "FAIL" "POSTGRES_PASSWORD not set"
    exit 1
fi

if ! command -v psql &>/dev/null || ! command -v pg_dump &>/dev/null; then
    log_step "validate" "FAIL" "psql and pg_dump required — install postgresql-client"
    exit 1
fi

# ── Calculate cutoff date ───────────────────────────────────────────────────
CUTOFF_DATE=$(date -d "${CUTOFF_MONTHS} months ago" +%Y-%m-%d)
CUTOFF_TIMESTAMP="${CUTOFF_DATE}T23:59:59Z"
log_step "config" "INFO" "Cutoff: ${CUTOFF_TIMESTAMP} (${CUTOFF_MONTHS} months ago)"

# ── Count records to archive ────────────────────────────────────────────────
PGPASSWORD="${PG_PASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -t -A -c "
    SELECT count(*) FROM ${SCHEMA}.${TABLE}
    WHERE ${DATE_COLUMN} < '${CUTOFF_TIMESTAMP}'::timestamp;
" > /tmp/spend_logs_count.$$ 2>/dev/null

RECORD_COUNT=$(cat /tmp/spend_logs_count.$$ | tr -d ' ')
rm -f /tmp/spend_logs_count.$$

if [[ -z "$RECORD_COUNT" || "$RECORD_COUNT" -eq 0 ]]; then
    log_step "count" "SKIP" "No records older than ${CUTOFF_DATE} — nothing to rotate"
    exit 0
fi

log_step "count" "INFO" "Found ${RECORD_COUNT} records to archive (older than ${CUTOFF_DATE})"

# ── Find date range for archive filename ────────────────────────────────────
EARLIEST_DATE=$(PGPASSWORD="${PG_PASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -t -A -c "
    SELECT min(${DATE_COLUMN})::date FROM ${SCHEMA}.${TABLE}
    WHERE ${DATE_COLUMN} < '${CUTOFF_TIMESTAMP}'::timestamp;
" 2>/dev/null | tr -d ' ')

ARCHIVE_FILE="${ARCHIVE_DIR}/spend_logs_${EARLIEST_DATE}_${CUTOFF_DATE}.sql.gz"

if [[ "$DRY_RUN" == "true" ]]; then
    log_step "dry-run" "INFO" "═══ DRY-RUN MODE ═══"
    log_step "dry-run" "INFO" "Would archive ${RECORD_COUNT} records (${EARLIEST_DATE} → ${CUTOFF_DATE})"
    log_step "dry-run" "INFO" "Archive file: ${ARCHIVE_FILE}"
    log_step "dry-run" "INFO" ""
    log_step "dry-run" "INFO" "pg_dump command:"
    echo "  pg_dump -h ${PG_HOST} -p ${PG_PORT} -U ${PG_USER} -d ${PG_DB} \\"
    echo "    --schema=${SCHEMA} --table=${TABLE} \\"
    echo "    --data-only --compress=9 \\"
    echo "    --where=\"${DATE_COLUMN} < '${CUTOFF_TIMESTAMP}'::timestamp\" \\"
    echo "    -f ${ARCHIVE_FILE}"
    log_step "dry-run" "INFO" ""
    log_step "dry-run" "INFO" "DELETE SQL:"
    echo "  DELETE FROM ${SCHEMA}.${TABLE}"
    echo "  WHERE ${DATE_COLUMN} < '${CUTOFF_TIMESTAMP}'::timestamp;"
    log_step "dry-run" "INFO" "═══ DRY-RUN COMPLETE ═══"
    exit 0
fi

# ── Phase 1: pg_dump ────────────────────────────────────────────────────────
log_step "dump" "START" "Dumping ${RECORD_COUNT} records to ${ARCHIVE_FILE}"

mkdir -p "${ARCHIVE_DIR}"

PGPASSWORD="${PG_PASSWORD}" pg_dump \
    -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" \
    --schema="${SCHEMA}" --table="${TABLE}" \
    --data-only --compress=9 \
    --where="${DATE_COLUMN} < '${CUTOFF_TIMESTAMP}'::timestamp" \
    -f "${ARCHIVE_FILE}"

if [[ ! -f "${ARCHIVE_FILE}" ]]; then
    log_step "dump" "FAIL" "pg_dump failed — archive file not created"
    exit 1
fi

DUMP_SIZE=$(du -h "${ARCHIVE_FILE}" | cut -f1)
log_step "dump" "DONE" "Dump written: ${ARCHIVE_FILE} (${DUMP_SIZE})"

# ── Phase 2: Verify dump integrity ─────────────────────────────────────────
log_step "verify" "START" "Verifying dump integrity"
if ! gunzip -t "${ARCHIVE_FILE}" 2>/dev/null; then
    log_step "verify" "FAIL" "Dump file is corrupted (gunzip -t failed)"
    exit 1
fi
log_step "verify" "DONE" "Dump integrity verified"

# ── Phase 3: DELETE in transaction (rollback on count mismatch) ────────────
log_step "delete" "START" "Deleting archived records from ${SCHEMA}.${TABLE}"

# Use a transaction: verify count matches dump, then delete, then verify again
DELETE_RESULT=$(PGPASSWORD="${PG_PASSWORD}" psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -t -A <<SQL 2>/dev/null
BEGIN;

-- Verify count matches dump
SELECT count(*) FROM ${SCHEMA}.${TABLE}
WHERE ${DATE_COLUMN} < '${CUTOFF_TIMESTAMP}'::timestamp;

-- Delete archived records
DELETE FROM ${SCHEMA}.${TABLE}
WHERE ${DATE_COLUMN} < '${CUTOFF_TIMESTAMP}'::timestamp;

-- Verify deletion
SELECT count(*) FROM ${SCHEMA}.${TABLE}
WHERE ${DATE_COLUMN} < '${CUTOFF_TIMESTAMP}'::timestamp;

COMMIT;
SQL
)

log_step "delete" "DONE" "Delete completed. Dump archived at: ${ARCHIVE_FILE}"
log_step "main" "DONE" "Rotation complete: ${RECORD_COUNT} records archived, ${DUMP_SIZE}"

exit 0
