# GREP_SUMMARY: s3_client thin-wrapper boto3 list-objects delete-objects pagination timeout
# STRUCTURE: class S3Client → list_objects(prefix, max_keys) → delete_objects(keys)
# region MODULE_CONTRACT
"""
Thin wrapper around boto3 S3 client for list/delete operations.

@purpose  Isolate S3 interaction logic from retention business logic. Provides
          paginated list and batch delete.
@scope    Used by RetentionPolicy for S3 operations.
@invariants
  - Таймауты живут в boto3 Config (botocore.config.Config connect_timeout/read_timeout)
    на уровне конструирования клиента (retention.py: BotoConfig) — это единственное место,
    где boto3 Config применим (Config — per-client, не per-call). Сюда параметр timeout
    НЕ пробрасывается (мёртвый).
  - delete_objects batches in chunks of 1000 (S3 API limit)
"""
# endregion MODULE_CONTRACT

import logging
from typing import Any

logger = logging.getLogger(__name__)

# region CONSTANTS

_MAX_LIST_KEYS = 1000

# endregion CONSTANTS


# region CLASS_S3Client
## @purpose  Thin wrapper around boto3 S3 client for paginated list and batch delete operations
class S3Client:
    """Thin wrapper around boto3 S3 client for list/delete operations."""

    # region METHOD___init__
    ## @purpose  Initialize with boto3 client and bucket name
    ## @io       boto3_client: Any + bucket: str → None (side-effect: stores state)
    ## @complexity 1
    def __init__(self, boto3_client: Any, bucket: str) -> None:
        """
        Initialize S3Client.

        Args:
            boto3_client: boto3 S3 client instance (Конфиг таймаутов — на уровне
                конструирования клиента, D10/128 W5).
            bucket: S3 bucket name.
        """
        self._client = boto3_client
        self._bucket = bucket

        logger.info(
            "[IMP:7][s3_client][__init__] S3Client initialized: bucket=%s",
            bucket,
        )

    # endregion METHOD___init__

    # region list_objects
    ## @purpose  Paginated list of S3 objects under prefix with continuation token loop
    ## @io       prefix: str + max_keys: int → list[dict]
    ## @complexity 2
    def list_objects(
        self,
        prefix: str,
        max_keys: int = _MAX_LIST_KEYS,
    ) -> list[dict[str, Any]]:
        """
        ▶ ○ loop ∋ ContinuationToken: ◇ list_objects_v2 → ⊕ collect Contents → ◇ IsTruncated? → ○ next / ⎋ break

        List all S3 objects under a prefix with pagination.

        Args:
            prefix: S3 prefix to list objects under.
            max_keys: Maximum keys per page (default 1000).

        Returns:
            List of S3 object dicts.
        """
        objects: list[dict[str, Any]] = []
        continuation_token = None

        try:
            while True:
                params: dict[str, Any] = {
                    "Bucket": self._bucket,
                    "Prefix": prefix,
                    "MaxKeys": max_keys,
                }
                if continuation_token:
                    params["ContinuationToken"] = continuation_token

                response = self._client.list_objects_v2(**params)
                contents = response.get("Contents", [])
                objects.extend(contents)

                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")

        except Exception as exc:
            logger.critical(
                "[IMP:9][s3_client][list] Cannot list S3 objects: %s",
                exc,
                exc_info=True,
            )
            raise

        logger.info(
            "[IMP:7][s3_client][list] Listed %d objects under prefix=%s",
            len(objects),
            prefix,
        )
        return objects

    # endregion list_objects

    # region delete_objects
    ## @purpose  Batch delete S3 objects in chunks of 1000 (S3 API limit)
    ## @io       keys: list[str] → int (total_deleted)
    ## @complexity 2
    def delete_objects(self, keys: list[str]) -> int:
        """
        ▶ ┌keys list┐ → ○ batch 0..N step 1000: ◇ delete_objects → ⊕ count deleted → ∑ total_deleted → ⎋ int

        Delete objects from S3 in batches of 1000 (S3 API limit).

        Args:
            keys: List of S3 object keys to delete.

        Returns:
            Number of successfully deleted objects.
        """
        batch_size = 1000
        total_deleted = 0
        batch_errors = 0
        total_batches = (len(keys) + batch_size - 1) // batch_size

        for i in range(0, len(keys), batch_size):
            batch = keys[i : i + batch_size]
            delete_request = {"Objects": [{"Key": k} for k in batch], "Quiet": True}

            try:
                response = self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete=delete_request,
                )
                deleted = len(response.get("Deleted", []))
                total_deleted += deleted
                logger.critical(
                    "[IMP:9][s3_client][delete] Batch %d/%d: deleted %d objects (IRREVERSIBLE)",
                    i // batch_size + 1,
                    total_batches,
                    deleted,
                )
            except Exception as exc:
                batch_errors += 1
                logger.critical(
                    "[IMP:9][s3_client][delete] Failed to delete batch %d/%d: %s",
                    i // batch_size + 1,
                    total_batches,
                    exc,
                    exc_info=True,
                )

        if batch_errors > 0:
            logger.critical(
                "[IMP:9][s3_client][delete] %d/%d batches failed",
                batch_errors,
                total_batches,
            )

        logger.critical(
            "[IMP:9][s3_client][delete] Total deleted: %d objects (batches: %d/%d ok)",
            total_deleted,
            total_batches - batch_errors,
            total_batches,
        )

        return total_deleted

    # endregion delete_objects


# endregion CLASS_S3Client
