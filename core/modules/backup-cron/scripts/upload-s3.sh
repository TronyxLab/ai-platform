#!/usr/bin/env bash
# GREP_SUMMARY: upload-s3 thin-wrapper boto3-upload retry spool-fallback phase-06
# STRUCTURE: validate args → call upload.py → preserve exit code → log result
# region MODULE_CONTRACT
## @purpose  Thin wrapper around upload.py — S3 upload with retry logic (phase 06).
## @scope    Called by backup-postgres.sh and backup-app-data.sh after local spool write.
## @invariants
##   - Phase 06: delegates to upload.py (Python/boto3 with 3 retries + spool fallback)
##   - Arguments: $1=local_file $2=s3_key
##   - Preserves backward compatibility with backup-postgres.sh call pattern
##   - Exit code: 0 on success, 1 on failure (file in spool), 2 on config error
##   - S3 credentials from environment (S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET, etc.) — canonical S3_* only (no AWS_* cross-chain fallback, DevPlan 049 DRIFT-2)
## @rationale Separate wrapper preserves the contract from backup-postgres.sh;
##          upload.py handles the actual boto3 logic for testability and code reuse.
# endregion MODULE_CONTRACT

set -euo pipefail

LOCAL_FILE="${1:-}"
S3_KEY="${2:-}"

if [[ -z "${LOCAL_FILE}" ]]; then
    echo "[IMP:9][upload-s3] ERROR: No local file specified" >&2
    exit 2
fi

if [[ -z "${S3_KEY}" ]]; then
    echo "[IMP:9][upload-s3] ERROR: No S3 key specified" >&2
    exit 2
fi

if [[ ! -f "${LOCAL_FILE}" ]]; then
    echo "[IMP:9][upload-s3] ERROR: Local file not found: ${LOCAL_FILE}" >&2
    exit 2
fi

# Validate S3 credentials are available (canonical S3_* only — no AWS_* cross-chain fallback, DevPlan 049 DRIFT-2)
S3_ACCESS_KEY="${S3_ACCESS_KEY:-}"
S3_SECRET_KEY="${S3_SECRET_KEY:-}"
S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-https://s3.timeweb.cloud}"

if [[ -z "${S3_BUCKET:-}" ]]; then
    echo "[IMP:9][upload-s3] ERROR: S3_BUCKET not set — cannot upload" >&2
    exit 2
fi

if [[ -z "${S3_ACCESS_KEY:-}" ]]; then
    echo "[IMP:9][upload-s3] ERROR: S3_ACCESS_KEY not set — cannot upload" >&2
    exit 2
fi

if [[ -z "${S3_SECRET_KEY:-}" ]]; then
    echo "[IMP:9][upload-s3] ERROR: S3_SECRET_KEY not set — cannot upload" >&2
    exit 2
fi

LOCAL_SIZE=$(stat -c%s "${LOCAL_FILE}" 2>/dev/null || stat -f%z "${LOCAL_FILE}" 2>/dev/null || echo "0")
echo "[IMP:7][upload-s3] Starting S3 upload: file=${LOCAL_FILE} size=${LOCAL_SIZE} key=${S3_KEY} bucket=${S3_BUCKET}"

# Delegate to upload.py (Python/boto3 with 3 retries + spool fallback)
# Pass all S3 vars explicitly so upload.py has them regardless of container env
S3_ENDPOINT_URL="${S3_ENDPOINT_URL}" \
S3_ACCESS_KEY="${S3_ACCESS_KEY}" \
S3_SECRET_KEY="${S3_SECRET_KEY}" \
S3_BUCKET="${S3_BUCKET}" \
S3_REGION="${S3_REGION:-ru-1}" \
S3_PREFIX="${S3_PREFIX:-platform/backups}" \
PLATFORM_CONTEXT="${PLATFORM_CONTEXT:-personal}" \
NODE_NAME="${NODE_NAME:-unknown}" \
python3 /usr/local/bin/upload.py "${LOCAL_FILE}" "${S3_KEY}"

EXIT_CODE=$?

if [[ ${EXIT_CODE} -eq 0 ]]; then
    echo "[IMP:9][upload-s3] UPLOAD COMPLETE: ${LOCAL_FILE} → s3://${S3_BUCKET}/${S3_PREFIX:-platform/backups}/${S3_KEY}"

    # Remove local spool file after successful upload
    rm -f "${LOCAL_FILE}"
    echo "[IMP:8][upload-s3] Removed spool file: ${LOCAL_FILE}"
else
    echo "[IMP:9][upload-s3] UPLOAD FAILED (exit=${EXIT_CODE}): ${LOCAL_FILE} remains in spool"
fi

exit ${EXIT_CODE}
