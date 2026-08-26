# GREP_SUMMARY: s3_client thin-wrapper boto3 builder build-client timeouts retries list-objects delete-objects pagination
# STRUCTURE: ▶ build_boto3_s3_client (единственный boto3.client в backup-cron, AI-0073) → class S3Client → list_objects(prefix, max_keys) → delete_objects(keys)
# region MODULE_CONTRACT
"""
Thin wrapper around boto3 S3 client for list/delete operations + единый строитель клиента.

@purpose  Isolate S3 interaction logic from retention business logic. Provides
          paginated list and batch delete, а также ЕДИНСТВЕННУЮ точку конструирования
          boto3-клиента для всех модулей backup-cron (upload/wal_sync/retention — AI-0073).
@scope    Used by RetentionPolicy (list/delete) and upload/wal_sync/retention (builder).
@invariants
  - build_boto3_s3_client — единственное место в backup-cron, где вызывается
    boto3.client (AI-0073: раньше клиент строился в трёх модулях с расползающимися
    таймаутами 10/30 vs 30/60)
  - Таймауты/retries живут в botocore.config.Config при конструировании клиента
    (Config — per-client, не per-call); override — явными параметрами строителя
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

# Дефолтный бюджет S3-клиента (canon upload/retention до AI-0073): connect 30 / read 60 /
# standard retries ×3. wal_sync переопределяет жёстким RPO-бюджетом (см. WAL_SYNC_S3_TIMEOUTS).
_DEFAULT_CONNECT_TIMEOUT = 30
_DEFAULT_READ_TIMEOUT = 60
_DEFAULT_MAX_ATTEMPTS = 3


def build_boto3_s3_client(
    *,
    endpoint_url: str | None,
    access_key: str | None,
    secret_key: str | None,
    region: str | None,
    connect_timeout: int = _DEFAULT_CONNECT_TIMEOUT,
    read_timeout: int = _DEFAULT_READ_TIMEOUT,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> "Boto3S3":
    """Единый строитель boto3 S3-клиента backup-cron (AI-0073).

    ▶ ┌endpoint+creds+region┐ → ⚡ botocore Config(connect/read/retries-standard) → ⚡ boto3.client("s3") → ⎋ Boto3S3

    ## @purpose  Один строитель вместо трёх копий boto3.client с расползающимися
    ##            таймаутами; per-call override — ЯВНЫМИ параметрами.
    ## @io       ⇥ endpoint/keys/region (None-креды → botocore env-chain), бюджеты → ⎋ Boto3S3
    ## @invariants
    ##   - Единственный boto3.client("s3") в backup-cron (AC T2.4)
    ##   - None-креды пробрасываются как None (botocore сам резолвит env/session-chain)
    """
    import boto3  # lazy: тяжёлый импорт только там, где реально нужен клиент
    from botocore.config import Config as BotoConfig

    boto_config = BotoConfig(
        retries={"max_attempts": max_attempts, "mode": "standard"},
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
    )
    raw = boto3.client(  # pyright: ignore[reportUnknownMemberType] — W11 external boto3.client untyped-оверлоады
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=boto_config,
    )
    return cast("Boto3S3", cast(object, raw))


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
