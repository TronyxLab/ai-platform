#!/usr/bin/env bash
# GREP_SUMMARY: upload-s3 thin-facade python3 upload.py boto3-upload retry spool-fallback phase-06
# STRUCTURE: validate args (2) → exec python3 upload.py → ⎋ preserve exit code (0|1|2)
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E9): вся логика (валидация CLI/S3-кредов, размер,
##           boto3-upload с 3 retries, spool rm после успеха) — в upload.py.
## @scope    Called by backup-postgres.sh and backup-app-data.sh after local spool write.
## @invariants
##   - Phase 06: delegates to upload.py (Python/boto3 with 3 retries + spool fallback)
##   - Arguments: $1=local_file $2=s3_key — exit 2 on missing args (config error)
##   - Exit code: 0 on success (spool rm выполнен upload.py), 1 on failure, 2 on config error
##   - S3 credentials from environment (S3_*) — canonical S3_* only (DevPlan 049 DRIFT-2)
##   - <20 LOC thin facade — языковая политика: бизнес-логика в Python
## @rationale Separate wrapper preserves the contract from backup-postgres.sh;
##          upload.py handles the actual boto3 logic for testability and code reuse.
##          Strangler E9: валидация+размер+rm merged в upload.py (0 дублей).
## @changes  2026-08-02 | DevPlan 118 E9 — сокращён до фасада (было 84 LOC)
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

# Pass all S3 vars explicitly so upload.py has them regardless of container env
S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-https://s3.timeweb.cloud}" \
S3_ACCESS_KEY="${S3_ACCESS_KEY:-}" \
S3_SECRET_KEY="${S3_SECRET_KEY:-}" \
S3_BUCKET="${S3_BUCKET:-}" \
S3_REGION="${S3_REGION:-ru-1}" \
S3_PREFIX="${S3_PREFIX:-platform/backups}" \
PLATFORM_CONTEXT="${PLATFORM_CONTEXT:-personal}" \
NODE_NAME="${NODE_NAME:-unknown}" \
exec python3 /usr/local/bin/upload.py "${LOCAL_FILE}" "${S3_KEY}"
