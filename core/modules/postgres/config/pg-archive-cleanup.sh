#!/usr/bin/env bash
# GREP_SUMMARY: pg-archive-cleanup WAL archive retention cleanup rotation
# STRUCTURE: parse_args → validate_args → find_wals → filter_by_age → delete_old → exit_0
# region MODULE_CONTRACT
## @purpose  Clean up WAL archive files older than retention-days from WAL archive directory.
## @scope    Runs daily via cron or manually; idempotent — if no files match, exits 0.
## @invariants
##   - Default retention: 7 days
##   - Only deletes files matching WAL naming pattern (*.gz, *.partial, 000000*)
##   - Never deletes the archive directory itself
##   - Idempotent: empty directory = exit 0, no errors
##   - IMP:8 logging for all operations
## @rationale WAL files accumulate in /var/lib/platform/wal-archive/ and consume disk space;
##            after PITR retention window (7 days), old WALs are safe to remove.
##            Cron schedule: 0 2 * * * (02:00 UTC, before backup window).
# endregion MODULE_CONTRACT

set -euo pipefail

# region DEFAULT_CONFIG
DEFAULT_RETENTION_DAYS=7
DEFAULT_WAL_DIR="/var/lib/platform/wal-archive"
# endregion DEFAULT_CONFIG

# region FUNC_usage
## @purpose  Print usage information
## @io       none → stdout
## @complexity 1
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Clean up WAL archive files older than retention-days.

Options:
  --wal-dir PATH         WAL archive directory (default: ${DEFAULT_WAL_DIR})
  --retention-days N     Keep WAL files newer than N days (default: ${DEFAULT_RETENTION_DAYS})
  --help                 Show this help message and exit

Examples:
  pg-archive-cleanup.sh
  pg-archive-cleanup.sh --wal-dir /mnt/wal-archive --retention-days 14
EOF
}
# endregion FUNC_usage

# region FUNC_parse_args
## @purpose  Parse CLI arguments into wal_dir and retention_days variables
## @io       sys.argv → (wal_dir, retention_days)
## @complexity 2
## @invariants
##   - Default wal_dir: /var/lib/platform/wal-archive
##   - Default retention_days: 7
##   - Retention must be positive integer
parse_args() {
    WAL_DIR="${DEFAULT_WAL_DIR}"
    RETENTION_DAYS="${DEFAULT_RETENTION_DAYS}"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --wal-dir)
                if [[ -z "${2:-}" ]]; then
                    echo "[IMP:10][parse_args] FATAL: --wal-dir requires a path argument" >&2
                    exit 1
                fi
                WAL_DIR="$2"
                shift 2
                ;;
            --retention-days)
                if [[ -z "${2:-}" ]]; then
                    echo "[IMP:10][parse_args] FATAL: --retention-days requires a number" >&2
                    exit 1
                fi
                if ! [[ "$2" =~ ^[0-9]+$ ]]; then
                    echo "[IMP:10][parse_args] FATAL: --retention-days must be a positive integer, got: $2" >&2
                    exit 1
                fi
                RETENTION_DAYS="$2"
                shift 2
                ;;
            --help)
                usage
                exit 0
                ;;
            *)
                echo "[IMP:10][parse_args] FATAL: Unknown option: $1" >&2
                usage >&2
                exit 1
                ;;
        esac
    done

    echo "[IMP:8][parse_args] Config: wal_dir=${WAL_DIR} retention_days=${RETENTION_DAYS}"
}
# endregion FUNC_parse_args

# region FUNC_validate_args
## @purpose  Validate that WAL_DIR exists and is a directory
## @io       WAL_DIR → exit 1 if invalid
## @complexity 1
validate_args() {
    if [[ ! -d "${WAL_DIR}" ]]; then
        echo "[IMP:8][validate] WAL directory does not exist, nothing to clean: ${WAL_DIR}"
        exit 0
    fi
    echo "[IMP:8][validate] WAL directory exists: ${WAL_DIR}"
}
# endregion FUNC_validate_args

# region FUNC_cleanup_wals
## @purpose  Find and delete WAL files older than retention-days
## @io       (wal_dir, retention_days) → stdout logs + file deletions
## @complexity 2
## @invariants
##   - Uses find -mtime +N to select files older than N days
##   - Deletes WAL files matching common PostgreSQL WAL naming patterns
##   - Idempotent: if no matching files, logs it and exits 0
cleanup_wals() {
    local wal_dir="$1"
    local retention_days="$2"
    local count=0
    local freed_bytes=0

    echo "[IMP:8][cleanup] Scanning ${wal_dir} for WAL files older than ${retention_days} days"

    # Find WAL files matching PostgreSQL naming patterns
    # WAL segments: 000000010000000000000001 (hex, 24 chars)
    # Also: .gz, .partial, .history files
    while IFS= read -r -d '' wal_file; do
        local file_size
        file_size=$(stat -f%z "${wal_file}" 2>/dev/null || stat -c%s "${wal_file}" 2>/dev/null || echo 0)
        echo "[IMP:8][cleanup] Deleting old WAL: ${wal_file} (age>${retention_days}d, size=${file_size})"
        rm -f "${wal_file}"
        count=$((count + 1))
        freed_bytes=$((freed_bytes + file_size))
    done < <(find "${wal_dir}" -type f -mtime "+${retention_days}" \( \
        -name '????????????????????????' -o \
        -name '????????????????????????*' -o \
        -name '*.gz' -o \
        -name '*.partial' -o \
        -name '*.history' \) -print0 2>/dev/null || true)

    if [[ "${count}" -eq 0 ]]; then
        echo "[IMP:8][cleanup] No WAL files older than ${retention_days} days found in ${wal_dir}"
    else
        local freed_mb
        freed_mb=$(( freed_bytes / 1048576 ))
        echo "[IMP:8][cleanup] Cleanup complete: removed ${count} files, freed ~${freed_mb}MB"
    fi
}
# endregion FUNC_cleanup_wals

# region FUNC_main
## @purpose  Entry point: parse args, validate, cleanup, exit 0
## @io       sys.argv → exit 0
## @complexity 2
main() {
    echo "[IMP:8][main] Starting pg-archive-cleanup at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

    parse_args "$@"
    validate_args
    cleanup_wals "${WAL_DIR}" "${RETENTION_DAYS}"

    echo "[IMP:8][main] pg-archive-cleanup completed at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
}
# endregion FUNC_main

main "$@"
