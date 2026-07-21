#!/usr/bin/env python3
# GREP_SUMMARY: upload s3-boto3 retry-3x-30min spool-fallback timeweb-s3
# STRUCTURE: parse_args → get_config → upload_file → retry(3x,30min) → success|spool → exit 0|1
# region MODULE_CONTRACT
"""
S3 upload script for backup-cron — uploads local spool files to Timeweb S3 bucket.

@purpose  Upload a local backup file to S3 with 3 retries (30 min intervals).
          On permanent failure, file remains in spool (no data loss).
@scope    Called by upload-s3.sh (thin wrapper) from backup-postgres.sh and backup-app-data.sh.
@input    CLI args: <local_file_path> <s3_key> [--config-source backup|ssl-cache]
          env: S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET, S3_REGION, S3_PREFIX.
@output   Exit 0 on success (upload OK), exit 1 on failure (file in spool).
@invariants
  - 3 retry attempts with ~30 min sleep between (03 §7)
  - botocore built-in retries (max_attempts=3) for transient network errors
  - File NEVER deleted from spool until confirmed S3 upload
  - IMP:9 logs for success and failure
  - All S3 credentials from env, never hardcoded
  - --config-source backup (default): uses BackupConfig (includes prefix)
  - --config-source ssl-cache: uses S3Config (no prefix — absolute S3 keys)
@rationale Python/boto3 chosen over aws-cli shell: boto3 provides typed API, botocore
          retries, and code reuse with retention.py and backup_monitor.py. The ~80MB
          image size increase is acceptable for a backup container.
          --config-source ssl-cache extends the same upload infrastructure for SSL
          certificate caching (DevPlan 024 Wave 1) without duplicating S3 logic.
"""

import argparse
import hashlib
import logging
import os
import sys
import time
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

# Import shared config from same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backup_config import S3Config, get_backup_config, get_s3_config  # pyright: ignore[reportImplicitRelativeImport]

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    stream=sys.stdout,
)

# endregion MODULE_CONTRACT

# region CONSTANTS

_MAX_RETRIES = 3
_RETRY_INTERVAL_SEC = 30 * 60  # 30 minutes between retry attempts
_BOTO_RETRIES = 3  # botocore built-in retry count

# 🧐 TRAP[DECISION] · 2026-06-11 · — · Retry: 3 попытки × 30 минут (total 90 min max)
# · Rejected: exponential backoff (1m, 5m, 15m) · Reason: файлы большие (>1GB), сеть нестабильная;
#   30-минутные интервалы дают время на восстановление сетевой связности;
#   для маленьких файлов overhead приемлем (редкий scheduled запуск, не latency-sensitive)
# · Rev: если появятся критические мелкие файлы (<10MB), добавить adaptive backoff

# endregion CONSTANTS


# region FUNC_compute_sha256
## @purpose  Compute SHA256 checksum of a local file (chunked for large files)
## @io       str → str
## @complexity 1
## @invariants
##   - Reads file in 64KB chunks to handle large backup files (>1GB)
##   - Returns hex digest string
def compute_sha256(filepath: str) -> str:
    """Compute SHA256 checksum of a file, reading in chunks for large files.

    Args:
        filepath: Path to the local file.

    Returns:
        SHA256 hex digest string.
    """
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256_hash.update(chunk)
    except OSError as exc:
        logger.critical(
            "[IMP:9][upload][sha256] Failed to read file for checksum: %s error=%s",
            filepath,
            exc,
        )
        raise
    digest = sha256_hash.hexdigest()
    logger.info(
        "[IMP:8][upload][sha256] Computed SHA256: file=%s digest=%s",
        filepath,
        digest,
    )
    return digest


# endregion FUNC_compute_sha256


# region FUNC_create_s3_client
# @purpose  Create boto3 S3 client with endpoint override for Timeweb S3.
# @io       Dict[str, str] → Any (boto3 S3 client)
# @complexity 2
def create_s3_client(config: S3Config) -> Any:  # boto3 S3 client (factory, not type)
    """
    Create a boto3 S3 client configured for Timeweb S3 endpoint.

    Args:
        config: Configuration dict from get_backup_config().

    Returns:
        boto3 S3 client instance.
    """
    boto_config = BotoConfig(
        retries={"max_attempts": _BOTO_RETRIES, "mode": "standard"},
        connect_timeout=30,
        read_timeout=60,
    )

    client = boto3.client(
        "s3",
        endpoint_url=config["endpoint_url"],
        aws_access_key_id=config["aws_access_key_id"],
        aws_secret_access_key=config["aws_secret_access_key"],
        region_name=config["region"],
        config=boto_config,
    )

    logger.info(
        "[IMP:7][upload][s3_client] S3 client created: endpoint=%s bucket=%s",
        config["endpoint_url"],
        config["bucket"],
    )
    return client


# endregion FUNC_create_s3_client


# region FUNC_upload_file
# 💼 TRAP[BUSINESS] · 2026-06-11 · HI · Spool never delete: локальная копия сохраняется после успешной загрузки
# · Source: phase-06 reliability requirement · Risk: диск может заполниться старыми бэкапами;
#   очистка spool — ответственность retention.py, не upload.py; separation of concerns
# @purpose  Upload a single file to S3 with a single attempt.
# @io       (client, bucket, local_path, s3_key) → tuple[bool, Exception | None]
# @complexity 2
def upload_file(
    client: Any,
    bucket: str,
    local_path: str,
    s3_key: str,
    sha256: str | None = None,
) -> tuple[bool, Exception | None]:
    """
    Upload a local file to S3, optionally with SHA256 checksum in metadata.

    Args:
        client: boto3 S3 client.
        bucket: S3 bucket name.
        local_path: Path to the local file.
        s3_key: S3 object key (path within bucket).
        sha256: Optional SHA256 hex digest to include in S3 metadata.

    Returns:
        Tuple of (success: bool, exception: Exception | None).
        On success, exception is None.
        On failure, exception is the caught exception.

    Raises:
        FileNotFoundError: If local_path does not exist.
    """
    if not os.path.isfile(local_path):
        logger.critical(
            "[IMP:9][upload][upload_file] File not found: %s",
            local_path,
        )
        raise FileNotFoundError(f"Local file not found: {local_path}")

    file_size = os.path.getsize(local_path)
    logger.info(
        "[IMP:7][upload][upload_file] Uploading: file=%s size=%d bucket=%s key=%s sha256=%s",
        local_path,
        file_size,
        bucket,
        s3_key,
        sha256 if sha256 else "none",
    )

    extra_args = None
    if sha256:
        extra_args = {"Metadata": {"sha256": sha256}}
        logger.info(
            "[IMP:8][upload][upload_file] Including SHA256 metadata: %s",
            sha256,
        )

    try:
        if extra_args:
            client.upload_file(local_path, bucket, s3_key, ExtraArgs=extra_args)
        else:
            client.upload_file(local_path, bucket, s3_key)
        logger.critical(
            "[IMP:9][upload][upload_file] UPLOAD OK: file=%s size=%d bucket=%s key=%s",
            local_path,
            file_size,
            bucket,
            s3_key,
        )
        return True, None
    except Exception as exc:
        logger.critical(
            "[IMP:9][upload][upload_file] UPLOAD FAIL: file=%s bucket=%s key=%s error=%s",
            local_path,
            bucket,
            s3_key,
            exc,
            exc_info=True,
        )
        return False, exc


# endregion FUNC_upload_file


# region FUNC_is_permanent_error
# 🧐 TRAP[DECISION] · 2026-06-11 · — · Fail fast on permanent S3 errors (403/404), retry only transient
# · Rejected: retry everything uniformly · Reason: 90 min wasted on 403 Forbidden — permissions don't self-heal;
#   transient errors (timeout, 5xx) benefit from retry; permanent errors need operator intervention
# · Rev: if S3 introduces temporary permission issues, add distinction between 403 (perm) and 503 (transient)
# @purpose  Determine if an S3 error is permanent (client error) or transient (server/network).
# @io       Exception → bool
# @complexity 1
def _is_permanent_error(exc: Exception) -> bool:
    """Return True if the error is a permanent client error (not worth retrying)."""
    from botocore.exceptions import ClientError

    if isinstance(exc, ClientError):
        metadata = exc.response.get("ResponseMetadata", {})
        status_code = metadata.get("HTTPStatusCode", 0) if isinstance(metadata, dict) else 0
        if 400 <= status_code < 500:
            return True
    return False


# endregion FUNC_is_permanent_error


# region FUNC_get_s3_metadata
## @purpose  Retrieve size and SHA256 metadata from S3 object with a single head_object call.
## @io       (client, bucket, s3_key) → dict[str, Any]
## @complexity 1
def get_s3_metadata(client: Any, bucket: str, s3_key: str) -> dict[str, Any]:
    """
    Retrieve both size and SHA256 checksum from S3 object metadata with a single head_object call.

    Args:
        client: boto3 S3 client.
        bucket: S3 bucket name.
        s3_key: S3 object key.

    Returns:
        Dict with keys: "size" (int | None) and "sha256" (str | None).

    Raises:
        Unexpected exceptions (non-ClientError) propagate up — no silent masking.
    """
    from botocore.exceptions import ClientError

    try:
        response = client.head_object(Bucket=bucket, Key=s3_key)
        size = response.get("ContentLength", 0)
        metadata = response.get("Metadata", {})
        sha256 = metadata.get("sha256")
        logger.info(
            "[IMP:7][upload][verify] S3 object verified: key=%s size=%d sha256=%s",
            s3_key,
            size,
            sha256 if sha256 else "none",
        )
        return {"size": size, "sha256": sha256}
    except ClientError as exc:
        logger.critical(
            "[IMP:9][upload][verify] Cannot verify S3 object: key=%s error=%s",
            s3_key,
            exc,
            exc_info=True,
        )
        return {"size": None, "sha256": None}


# endregion FUNC_get_s3_metadata


# region FUNC_upload_with_retries
# @purpose  Upload a file to S3 with retry logic (3 attempts, 30 min interval).
# @io       (client, bucket, local_path, s3_key, max_retries, interval_sec) → bool
# @complexity 3
def upload_with_retries(
    client: Any,
    bucket: str,
    local_path: str,
    s3_key: str,
    max_retries: int = _MAX_RETRIES,
    interval_sec: int = _RETRY_INTERVAL_SEC,
    sha256: str | None = None,
) -> bool:
    """
    Upload a file to S3 with retry logic.

    Retries up to max_retries times with interval_sec seconds between attempts.
    On all retries exhausted, file remains in spool (no deletion).

    Args:
        client: boto3 S3 client.
        bucket: S3 bucket name.
        local_path: Path to the local file.
        s3_key: S3 object key.
        max_retries: Maximum number of upload attempts (default 3).
        interval_sec: Seconds to wait between retries (default 1800 = 30 min).
        sha256: Optional SHA256 hex digest for S3 metadata.

    Returns:
        True if upload succeeded, False if all retries exhausted.
    """
    logger.info(
        "[IMP:7][upload][retry] Starting upload with retries: max=%d interval=%ds key=%s sha256=%s",
        max_retries,
        interval_sec,
        s3_key,
        sha256 if sha256 else "none",
    )

    for attempt in range(1, max_retries + 1):
        logger.info("[IMP:7][upload][retry] Attempt %d/%d", attempt, max_retries)
        success, exc = upload_file(client, bucket, local_path, s3_key, sha256=sha256)

        if success:
            logger.critical(
                "[IMP:9][upload][retry] UPLOAD SUCCESS on attempt %d/%d: key=%s",
                attempt,
                max_retries,
                s3_key,
            )
            return True

        # Fail fast on permanent errors (403/404), retry only transient
        if exc is not None and _is_permanent_error(exc):
            logger.critical(
                "[IMP:9][upload][retry] PERMANENT ERROR on attempt %d/%d: key=%s — not retrying",
                attempt,
                max_retries,
                s3_key,
            )
            return False

        if attempt < max_retries:
            logger.warning(
                "[IMP:8][upload][retry] Retry %d/%d failed — waiting %ds before next attempt",
                attempt,
                max_retries,
                interval_sec,
            )
            time.sleep(interval_sec)

    # All retries exhausted
    logger.critical(
        "[IMP:9][upload][retry] UPLOAD FAILED after %d attempts: key=%s — file remains in spool",
        max_retries,
        s3_key,
    )
    return False


# endregion FUNC_upload_with_retries


# region FUNC_parse_args
# @purpose  Parse CLI arguments for upload script
# @io       list[str] | None → argparse.Namespace
# @complexity 1
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parse CLI arguments. argv=None uses sys.argv.

    Returns:
        argparse.Namespace with fields: local_file, s3_key, config_source, retries, interval.
    """
    parser = argparse.ArgumentParser(description="Upload file to Timeweb S3 with retry logic.")
    parser.add_argument("local_file", help="Path to local file")
    parser.add_argument("s3_key", help="S3 object key (path within bucket)")
    parser.add_argument(
        "--config-source",
        choices=["backup", "ssl-cache"],
        default="backup",
        help="Config source: 'backup' uses backup prefix (default), 'ssl-cache' uses raw S3 keys",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=_MAX_RETRIES,
        help=f"Max retry attempts (default: {_MAX_RETRIES})",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=_RETRY_INTERVAL_SEC,
        help=f"Seconds between retries (default: {_RETRY_INTERVAL_SEC})",
    )

    args = parser.parse_args(argv)

    if not args.local_file:
        logger.critical("[IMP:9][upload][parse] ERROR: local_file not specified")
        sys.exit(2)

    if not args.s3_key:
        logger.critical("[IMP:9][upload][parse] ERROR: s3_key not specified")
        sys.exit(2)

    return args


# endregion FUNC_parse_args


# region FUNC_init_client
# @purpose  Create S3 client from backup config, with error handling
# @io       BackupConfig → boto3 S3 client
# @complexity 1
def _init_client(config: S3Config) -> Any:
    """
    Create boto3 S3 client from config. Exits with code 2 on failure.

    Args:
        config: S3 configuration dict from get_s3_config() or get_backup_config().

    Returns:
        boto3 S3 client instance.
    """
    try:
        client = create_s3_client(config)
    except Exception as exc:
        logger.critical(
            "[IMP:9][upload][init] Failed to create S3 client: %s",
            exc,
            exc_info=True,
        )
        sys.exit(2)
    return client


# endregion FUNC_init_client


# region FUNC_upload_and_verify
# @purpose  Upload file to S3 with retries, then verify checksum/size
# @io       (...) → bool (success)
# @complexity 4
def _upload_and_verify(
    client: Any,
    bucket: str,
    local_file: str,
    full_key: str,
    local_sha256: str,
    max_retries: int,
    interval_sec: int,
) -> bool:
    """
    Upload with retries + post-upload verification. Returns True on success.

    On verification failure (size or SHA256 mismatch), attempts one re-upload.
    If S3 metadata retrieval fails (s3_size is None), exits with code 1.

    Args:
        client: boto3 S3 client.
        bucket: S3 bucket name.
        local_file: Path to local backup file.
        full_key: Full S3 key with prefix.
        local_sha256: SHA256 hex digest of local file.
        max_retries: Maximum number of upload attempts.
        interval_sec: Seconds between retry attempts.

    Returns:
        True if upload and verification succeeded, False if all retries exhausted.
    """
    success = upload_with_retries(
        client,
        bucket,
        local_file,
        full_key,
        max_retries=max_retries,
        interval_sec=interval_sec,
        sha256=local_sha256,
    )

    if not success:
        return False

    # Verify upload — size + SHA256 checksum
    s3_meta = get_s3_metadata(client, bucket, full_key)
    local_size = os.path.getsize(local_file)
    s3_size = s3_meta["size"]
    s3_sha256 = s3_meta["sha256"]

    # If S3 metadata retrieval failed (None), treat as verification failure
    if s3_size is None:
        logger.critical(
            "[IMP:9][upload][verify] VERIFICATION FAILED: cannot read S3 metadata for key=%s",
            full_key,
        )
        sys.exit(1)

    size_mismatch = bool(s3_size and s3_size != local_size)
    # Only flag SHA256 mismatch if metadata exists (backward-compatible with old objects)
    sha256_mismatch = bool(s3_sha256 is not None and s3_sha256 != local_sha256)

    if size_mismatch or sha256_mismatch:
        # ⚠️ TRAP[BUG] · 2026-06-11 · P2 · Size/SHA256 mismatch after upload: corrupt S3 object, must re-upload
        # · Symptom: s3_size != local_size or s3_sha256 != local_sha256, but script previously exited 0
        # · Root: network truncation or S3 inconsistency during upload
        # · Fix: delete bad S3 object, re-upload once, exit non-zero if re-upload fails
        if size_mismatch:
            logger.critical(
                "[IMP:9][upload][verify] SIZE MISMATCH: local=%d s3=%d — deleting and re-uploading",
                local_size,
                s3_size,
            )
        if sha256_mismatch:
            logger.critical(
                "[IMP:9][upload][verify] SHA256 MISMATCH: local=%s s3=%s — deleting and re-uploading",
                local_sha256,
                s3_sha256,
            )
        # Delete bad S3 object
        try:
            client.delete_object(Bucket=bucket, Key=full_key)
            logger.critical(
                "[IMP:9][upload][verify] Deleted corrupt S3 object: key=%s",
                full_key,
            )
        except Exception as exc:
            logger.critical(
                "[IMP:9][upload][verify] Failed to delete corrupt S3 object: %s",
                exc,
                exc_info=True,
            )
        # Re-upload once (not full retry loop) — include SHA256 metadata
        if not upload_file(client, bucket, local_file, full_key, sha256=local_sha256)[0]:
            logger.critical(
                "[IMP:9][upload][verify] Re-upload FAILED after mismatch — file remains in spool",
            )
            sys.exit(1)
        # Verify re-upload — both size and SHA256
        s3_meta = get_s3_metadata(client, bucket, full_key)
        s3_size = s3_meta["size"]
        s3_sha256 = s3_meta["sha256"]
        re_issue = False
        if s3_size is None:
            logger.critical(
                "[IMP:9][upload][verify] Re-upload VERIFICATION FAILED: cannot read S3 metadata",
            )
            sys.exit(1)
        if s3_size != local_size:
            logger.critical(
                "[IMP:9][upload][verify] Re-upload SIZE MISMATCH again — giving up",
            )
            re_issue = True
        if s3_sha256 is not None and s3_sha256 != local_sha256:
            logger.critical(
                "[IMP:9][upload][verify] Re-upload SHA256 MISMATCH again — giving up",
            )
            re_issue = True
        if re_issue:
            sys.exit(1)
        logger.critical(
            "[IMP:9][upload][verify] Re-upload VERIFIED: size=%d sha256=%s match after re-upload",
            local_size,
            local_sha256,
        )
    else:
        logger.critical(
            "[IMP:9][upload][verify] UPLOAD VERIFIED: local=%d s3=%d match, SHA256=%s",
            local_size,
            s3_size if s3_size else 0,
            local_sha256,
        )

    return True


# endregion FUNC_upload_and_verify


# region FUNC_generate_report
# @purpose  Print final report and set exit code
# @io       (success, local_file, bucket, full_key) → None (calls sys.exit)
# @complexity 1
def _generate_report(success: bool, local_file: str, bucket: str, full_key: str) -> None:
    """
    Print final report and exit with appropriate code.

    Args:
        success: True if upload succeeded, False otherwise.
        local_file: Path to local backup file.
        bucket: S3 bucket name.
        full_key: Full S3 key with prefix.
    """
    if success:
        print(f"[IMP:9][upload] UPLOAD COMPLETE: {local_file} → s3://{bucket}/{full_key}")
        sys.exit(0)
    else:
        print(f"[IMP:9][upload] UPLOAD FAILED: {local_file} remains in spool")
        sys.exit(1)


# endregion FUNC_generate_report


# region FUNC_main
# @purpose  CLI entry point: parse args, upload file, report result.
# @io       sys.argv → exit 0|1|2
# @complexity 2
def main() -> None:
    """
    CLI entry point for upload.py.

    Usage: upload.py <local_file> <s3_key>
           upload.py --config-source ssl-cache <local_file> <s3_key>

    Exit codes:
        0 — upload successful
        1 — upload failed (file in spool, verification failure)
        2 — invalid arguments or config error
    """
    args = _parse_args()

    # Select config source based on --config-source flag
    # backup: uses BackupConfig (includes prefix for backup paths)
    # ssl-cache: uses S3Config (no prefix — absolute S3 keys)
    if args.config_source == "ssl-cache":
        config = get_s3_config()
        full_key = args.s3_key  # ssl-cache uses absolute S3 keys, no prefix
        logger.info(
            "[IMP:7][upload][main] Config source: ssl-cache (no prefix) — S3 key used as-is",
        )
    else:
        config = get_backup_config()
        full_key = f"{config['prefix']}/{args.s3_key}".replace("//", "/")
        logger.info(
            "[IMP:7][upload][main] Config source: backup (prefix=%s)",
            config["prefix"],
        )

    client = _init_client(config)
    local_sha256 = compute_sha256(args.local_file)

    logger.info(
        "[IMP:7][upload][main] Starting upload: file=%s bucket=%s key=%s",
        args.local_file,
        config["bucket"],
        full_key,
    )

    success = _upload_and_verify(
        client,
        config["bucket"],
        args.local_file,
        full_key,
        local_sha256,
        max_retries=args.retries,
        interval_sec=args.interval,
    )

    _generate_report(success, args.local_file, config["bucket"], full_key)


# endregion FUNC_main


if __name__ == "__main__":
    main()
