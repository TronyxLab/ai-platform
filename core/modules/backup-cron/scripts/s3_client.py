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
from datetime import datetime
from typing import Protocol, TypedDict, cast

from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# region CONSTANTS

_MAX_LIST_KEYS = 1000

# endregion CONSTANTS


# region DATA_Boto3S3
class Boto3S3(Protocol):
    """Минимальный контракт boto3 S3-клиента (W11: boto3 untyped → Protocol вместо Any).

    ## @purpose  Типизированная граница boto3.client("s3") для S3Client-обёртки:
    ##            list_objects_v2/delete_objects (retention) + head_object/put_object
    ##            (wal_sync напрямую через _client). DI-фейки реализуют тот же протокол.
    """

    def head_object(self, **kwargs: object) -> object: ...
    def put_object(self, **kwargs: object) -> object: ...
    def list_objects_v2(self, **kwargs: object) -> dict[str, object]: ...
    def delete_objects(self, **kwargs: object) -> dict[str, object]: ...


# endregion DATA_Boto3S3


# region DATA_S3Object
class S3Object(TypedDict, total=False):
    """Объект S3 (элемент Contents list_objects_v2) — граница boto3-ответа.

    ## @purpose  Key (удаление/retention), LastModified (stale-детекция по возрасту),
    ##            Size/ETag — справочные. total=False — boto3 может не вернуть часть полей.
    """

    Key: str
    LastModified: datetime
    Size: int
    ETag: str


# endregion DATA_S3Object


# region CLASS_S3Client
## @purpose  Thin wrapper around boto3 S3 client for paginated list and batch delete operations
class S3Client:
    """Thin wrapper around boto3 S3 client for list/delete operations."""

    # region METHOD___init__
    ## @purpose  Initialize with boto3 client and bucket name
    ## @io       boto3_client: Boto3S3 + bucket: str → None (side-effect: stores state)
    ## @complexity 1
    ## @changes  2026-08-15 | DevPlan 170 W11 — Boto3S3 (Protocol вместо Any)
    def __init__(self, boto3_client: Boto3S3, bucket: str) -> None:
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
    ## @io       prefix: str + max_keys: int → list[S3Object]
    ## @complexity 2
    ## @changes  2026-08-15 | DevPlan 170 W11 — list[S3Object] (TypedDict вместо list[dict[str, Any]])
    def list_objects(
        self,
        prefix: str,
        max_keys: int = _MAX_LIST_KEYS,
    ) -> list[S3Object]:
        """
        ▶ ○ loop ∋ ContinuationToken: ◇ list_objects_v2 → ⊕ collect Contents → ◇ IsTruncated? → ○ next / ⎋ break

        List all S3 objects under a prefix with pagination.

        Args:
            prefix: S3 prefix to list objects under.
            max_keys: Maximum keys per page (default 1000).

        Returns:
            List of S3 object dicts.
        """
        objects: list[S3Object] = []
        continuation_token: str | None = None

        # ruff: ignore[PLW0717] — внутри try есть break/continue/await/yield — извлечение ломает управляющий поток
        try:
            while True:
                params: dict[str, object] = {
                    "Bucket": self._bucket,
                    "Prefix": prefix,
                    "MaxKeys": max_keys,
                }
                if continuation_token:
                    params["ContinuationToken"] = continuation_token

                response = self._client.list_objects_v2(**params)
                contents_raw = response.get("Contents")
                contents = cast("list[object]", contents_raw) if isinstance(contents_raw, list) else []
                # W11: boto3-элементы (dict[Unknown, Unknown]) → S3Object через object-мост
                objects.extend(cast("S3Object", cast(object, item)) for item in contents if isinstance(item, dict))

                if not response.get("IsTruncated"):
                    break
                continuation_token = cast("str | None", response.get("NextContinuationToken"))

        except Exception as exc:  # noqa: EXC — log+re-raise (legit): диагностика перед пробросом — boto3-ошибки (ClientError/BotoCoreError/OSError) логируются здесь, обработка — у вызывающего (retention.apply error-dict)
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
            delete_request: dict[str, object] = {"Objects": [{"Key": k} for k in batch], "Quiet": True}

            try:
                response = self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete=delete_request,
                )
                deleted_raw = response.get("Deleted")
                deleted = len(cast("list[object]", deleted_raw)) if isinstance(deleted_raw, list) else 0
                total_deleted += deleted
                logger.critical(
                    "[IMP:9][s3_client][delete] Batch %d/%d: deleted %d objects (IRREVERSIBLE)",
                    i // batch_size + 1,
                    total_batches,
                    deleted,
                )
            # 170 W2-A2 (B3): `except Exception` сужен до (ClientError, OSError, BotoCoreError) —
            # delete_objects (boto3) бросает ClientError (API) / BotoCoreError-семейство (сеть) /
            # OSError; сужение сохраняет поведение (лог + batch_errors, continue) для операционных
            # ошибок. Программерские ошибки — fail-loud.
            # Примечание: ClientError — подкласс BotoCoreError (оба в кортеже явно для читаемости)
            except (ClientError, OSError, BotoCoreError) as exc:
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
