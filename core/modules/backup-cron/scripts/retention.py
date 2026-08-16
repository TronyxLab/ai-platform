#!/usr/bin/env python3
# GREP_SUMMARY: retention s3-rotation 7-28-90 daily-weekly-monthly boto3 timeweb-s3
# STRUCTURE: parse_args → get_config → list_objects → group_by_date → classify(daily|weekly|monthly) → delete_outside_window → log kept/deleted
# region MODULE_CONTRACT
"""
S3 retention rotation script — applies 7/28/90 day retention policy to backup objects.

@purpose  Enforce 3-tier retention: daily (7 days), weekly (4 Sundays, 28 days),
          monthly (3 first-of-month, ~90 days). Deletes objects outside retention
          windows while preserving weekly and monthly markers.
@scope    Run daily at 05:00 UTC by cron (after all backups and cleanup).
@input    Env: S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET, S3_REGION, S3_PREFIX.
          CLI: --dry-run (optional, no actual deletion).
@output   Exit 0; IMP:9 log "RETENTION: kept=N deleted=M".
@invariants
  - Daily retention: keep objects from last 7 days (03 §3)
  - Weekly retention: keep 4 most recent Sunday-marked files (28 days window)
  - Monthly retention: keep 3 most recent first-of-month files (~90 days window)
  - Objects matching both weekly AND monthly markers are kept (MAXIMUM retention)
  - Dry-run mode available (--dry-run flag)
  - All S3 credentials from env, never hardcoded
  - Idempotent: running multiple times produces same result
@rationale Q: why Python instead of S3 Lifecycle rules?
          A: Timeweb S3 may not support complex rules with weekly/monthly markers.
          Python gives full control over the logic "keep 4 Sundays and 3 first-of-month
          dates". S3 Lifecycle rules — v2 option if provider supports them.
"""

import argparse
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict, cast

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_config import BackupConfigError, get_backup_config  # pyright: ignore[reportImplicitRelativeImport]
from date_parser import DateParser  # pyright: ignore[reportImplicitRelativeImport]
from s3_client import Boto3S3, S3Client, S3Object  # pyright: ignore[reportImplicitRelativeImport]

logger = logging.getLogger(__name__)


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    stream=sys.stdout,
)


# endregion MODULE_CONTRACT

# region CONSTANTS

# Retention tiers (03 §3)
_DAILY_RETENTION_DAYS = 7
_WEEKLY_RETENTION_COUNT = 4  # Keep 4 most recent Sundays (~28 days)
_MONTHLY_RETENTION_COUNT = 3  # Keep 3 most recent first-of-month (~90 days)

# 💼 TRAP[BUSINESS] · 2026-06-11 · HI · Retention tiers: 7 daily, 28 weekly, 90 monthly
# · Source: phase-06 §3 retention policy · Risk: изменение правил retention требует синхронизации
#   с upload.py (формат имени файла), backup_monitor.py (алерты) и документацией restore

# Boto3 config
_BOTO_RETRIES = 3
# D10 (128 W5): таймауты boto3 Config — именованные константы модуля (модуль НЕ может
# импортировать core/internal/shared/timeouts — cross-layer: modules → lib/templates only).
_BOTO_CONNECT_TIMEOUT = 30  # seconds
_BOTO_READ_TIMEOUT = 60  # seconds

# endregion CONSTANTS


# region DATA_RetentionResult
class RetentionResult(TypedDict, total=False):
    """Результат применения retention (граница вывода apply/main).

    ## @purpose  kept/deleted счётчики + ключи; error — при S3-отказе листинга
    ##            (non-fatal, exit 2 в main); timestamp — время прогона.
    """

    error: str
    kept: int
    deleted: int
    kept_keys: list[str]
    deleted_keys: list[str]
    dry_run: bool
    timestamp: str
    unparseable_kept: int


# endregion DATA_RetentionResult


# region DATA_CliArgs
@dataclass
class CliArgs:
    """Типизированные аргументы CLI (W11: argparse.Namespace → dataclass-namespace)."""

    dry_run: bool = False


# endregion DATA_CliArgs


# region CLASS_RetentionPolicy
## @purpose  Apply 7/28/90 retention to S3 backup objects
## @uses     boto3 S3 client, backup_config
## @io       S3 bucket + prefix → kept/deleted counts
## @complexity 4
class RetentionPolicy:
    """
    Apply 3-tier retention to S3 backup objects.

    Usage:
        policy = RetentionPolicy(s3, parser, bucket, prefix)
        result = policy.apply()
        # result = {"kept": 14, "deleted": 5, "kept_keys": [...], "deleted_keys": [...]}
    """

    # region CTOR
    ## @purpose  Initialize retention policy with S3 client, parser, bucket/prefix, and tier counts
    ## @io       s3 + parser + bucket + prefix + tiers + now → None (side-effect: stores state)
    ## @complexity 1
    def __init__(
        self,
        s3: S3Client,
        parser: DateParser,
        bucket: str,
        prefix: str,
        daily_days: int = _DAILY_RETENTION_DAYS,
        weekly_count: int = _WEEKLY_RETENTION_COUNT,
        monthly_count: int = _MONTHLY_RETENTION_COUNT,
        now: datetime | None = None,
    ) -> None:
        """
        Initialize retention policy.

        Args:
            s3: S3Client wrapper for S3 operations.
            parser: DateParser for date extraction and classification.
            bucket: S3 bucket name.
            prefix: S3 prefix for backup objects (e.g., "platform/backups/").
            daily_days: Number of days for daily retention (default 7).
            weekly_count: Number of weekly (Sunday) markers to keep (default 4).
            monthly_count: Number of monthly (1st of month) markers to keep (default 3).
            now: Current time for deterministic testing.
        """
        self._s3: S3Client = s3
        self._parser: DateParser = parser
        self._bucket: str = bucket
        self._prefix: str = prefix
        self._daily_days: int = daily_days
        self._weekly_count: int = weekly_count
        self._monthly_count: int = monthly_count
        self._now: datetime = now or datetime.now(timezone.utc)
        self._unparseable_keys: list[str] = []

        logger.info(
            "[IMP:7][retention][__init__] Retention policy: daily=%dd weekly=%d monthly=%d bucket=%s prefix=%s",
            daily_days,
            weekly_count,
            monthly_count,
            bucket,
            prefix,
        )

    # endregion CTOR

    # region FUNC_apply
    ## @purpose  Main entry: scan S3 objects, group by date, classify, determine deletions
    ## @io       dry_run: bool → dict (kept, deleted, kept_keys, deleted_keys, dry_run, timestamp)
    ## @complexity 4
    def apply(self, dry_run: bool = False) -> RetentionResult:
        """
        ▶ ┌S3 prefix┐ → ◇ list objects → ⊕ group_by_date → ⊕ compute_retention (daily/weekly/monthly) → ⊕ delete outside windows → ⎋ result dict

        Apply retention policy to S3 objects.

        Args:
            dry_run: If True, only simulate (no actual deletions).

        Returns:
            Dict with keys: kept, deleted, kept_keys, deleted_keys, dry_run, timestamp.
        """
        logger.info(
            "[IMP:7][retention][apply] Starting retention scan (dry_run=%s)",
            dry_run,
        )

        # 1. List all objects under prefix
        # 170 W2-A2 (B3): `except Exception` сужен до (ClientError, OSError, BotoCoreError) —
        # boto3-операции (list_objects_v2) бросают ClientError (API) / BotoCoreError-семейство
        # (сеть: ConnectTimeoutError и т.п.) / OSError. Суждение до (ClientError, OSError)
        # НЕдостаточно: botocore-сетевые транзиенты (BotoCoreError) — тоже операционные и должны
        # давать error-dict (non-fatal retention), а не падать. Программерские ошибки
        # (ValueError/TypeError) — fail-loud.
        # Примечание: ClientError — подкласс BotoCoreError (оба в кортеже явно для читаемости)
        try:
            all_objects = self._s3.list_objects(self._prefix)
        except (ClientError, OSError, BotoCoreError) as exc:
            logger.critical(
                "[IMP:9][retention][apply] Failed to list S3 objects: %s",
                exc,
                exc_info=True,
            )
            return {
                "error": str(exc),
                "kept": 0,
                "deleted": 0,
                "kept_keys": [],
                "deleted_keys": [],
                "dry_run": dry_run,
                "timestamp": self._now.isoformat(),
                "unparseable_kept": 0,
            }
        if not all_objects:
            logger.info(
                "[IMP:9][retention][apply] RETENTION: no objects found under prefix=%s — skipping retention (normal for new modules)",
                self._prefix,
            )
            return {
                "kept": 0,
                "deleted": 0,
                "kept_keys": [],
                "deleted_keys": [],
                "dry_run": dry_run,
                "timestamp": self._now.isoformat(),
                "unparseable_kept": 0,
            }

        logger.info(
            "[IMP:7][retention][apply] Found %d objects under prefix",
            len(all_objects),
        )

        # 2. Group objects by date extracted from key
        date_groups = self._group_by_date(all_objects)

        # 3. Compute retention windows
        keep_keys = self._compute_retention(date_groups)

        # 3a. Add unparseable keys to keep set (safety: keep unknown files)
        unparseable_count = len(self._unparseable_keys)
        keep_keys.update(self._unparseable_keys)

        # 4. Determine deletions (keys NOT in keep set)
        all_keys = {cast("str", obj.get("Key")) for obj in all_objects}
        delete_keys = all_keys - keep_keys

        logger.info(
            "[IMP:7][retention][apply] Retention result: total=%d keep=%d delete=%d",
            len(all_keys),
            len(keep_keys),
            len(delete_keys),
        )

        # 5. Execute deletions (unless dry-run)
        if delete_keys and not dry_run:
            self._s3.delete_objects(list(delete_keys))

        logger.critical(
            "[IMP:9][retention][apply] RETENTION: kept=%d deleted=%d unparseable_kept=%d (dry_run=%s)",
            len(keep_keys),
            len(delete_keys),
            unparseable_count,
            dry_run,
        )

        return {
            "kept": len(keep_keys),
            "deleted": len(delete_keys),
            "kept_keys": sorted(keep_keys),
            "deleted_keys": sorted(delete_keys),
            "dry_run": dry_run,
            "timestamp": self._now.isoformat(),
            "unparseable_kept": unparseable_count,
        }

    # endregion FUNC_apply

    # region FUNC_group_by_date
    ## @purpose  Group S3 objects by date extracted from key name
    ## @io       list[dict] → dict[str, list[dict]]
    ## @complexity 2
    def _group_by_date(self, objects: list[S3Object]) -> dict[str, list[S3Object]]:
        """
        Group objects by date extracted from key filename.

        Expected key pattern: .../pgdumpall_YYYYMMDDTHHMMSSZ.sql.gz
        Date extracted from the YYYYMMDD portion.
        """
        groups: dict[str, list[S3Object]] = defaultdict(list)
        self._unparseable_keys = []

        for obj in objects:
            key = obj.get("Key", "")
            date_str = self._parser.extract_date_from_key(key)
            if date_str:
                groups[date_str].append(obj)
            else:
                logger.warning(
                    "RETENTION: skipping object with unparseable date, keeping: %s",
                    key,
                )
                self._unparseable_keys.append(key)

        logger.info(
            "[IMP:7][retention][group] Grouped into %d date buckets",
            len(groups),
        )
        return dict(groups)

    # endregion FUNC_group_by_date

    # 🧐 TRAP[DECISION] · 2026-06-11 · — · Date extraction из имени файла (YYYYMMDD pattern), не из S3 метаданных
    # · Rejected: S3 LastModified · Reason: LastModified — время загрузки, а не время создания бэкапа;
    #   при повторной загрузке (retry) LastModified обновляется, ломая retention-логику
    # · Rev: если S3 Object Lock/Tags станут доступны, хранить дату в тегах как авторитетный источник

    # region FUNC_compute_retention
    ## @purpose  Compute which objects to keep based on 7/28/90 retention tiers
    ## @io       dict[str, list[dict]] → set[str] (keys to keep)
    ## @complexity 3
    def _compute_retention(self, date_groups: dict[str, list[S3Object]]) -> set[str]:
        """
        ▶ ┌date_groups sorted desc┐ → ◇ Tier1: daily (last 7d) → ◇ Tier2: weekly (4 Sundays) → ◇ Tier3: monthly (3 first-of-month) → ⊕ union all kept keys → ⎋ set[str]

        Compute the set of S3 keys to keep based on retention policy.
        """
        keep_keys: set[str] = set()
        now = self._now

        # Sort dates descending (newest first)
        sorted_dates = sorted(date_groups.keys(), reverse=True)

        # --- Tier 1: Daily retention (last N days) ---
        daily_cutoff = now - timedelta(days=self._daily_days)
        daily_cutoff_str = daily_cutoff.strftime("%Y%m%d")

        for date_str in sorted_dates:
            if date_str >= daily_cutoff_str:
                for obj in date_groups[date_str]:
                    keep_keys.add(cast("str", obj.get("Key")))
                logger.info(
                    "[IMP:7][retention][daily] Keeping date=%s (%d objects) — within %dd window",
                    date_str,
                    len(date_groups[date_str]),
                    self._daily_days,
                )

        # --- Tier 2: Weekly retention (N most recent Sundays) ---
        # Find all Sundays in the date groups
        sunday_dates = [d for d in sorted_dates if self._parser.is_sunday(d)]
        # Keep the N most recent
        sundays_to_keep = sunday_dates[: self._weekly_count]

        for date_str in sundays_to_keep:
            for obj in date_groups[date_str]:
                keep_keys.add(cast("str", obj.get("Key")))
            logger.info(
                "[IMP:7][retention][weekly] Keeping Sunday date=%s (%d objects) — weekly marker %d/%d",
                date_str,
                len(date_groups[date_str]),
                sundays_to_keep.index(date_str) + 1,
                self._weekly_count,
            )

        # --- Tier 3: Monthly retention (N most recent 1st-of-month) ---
        first_of_month_dates = [d for d in sorted_dates if self._parser.is_first_of_month(d)]
        firsts_to_keep = first_of_month_dates[: self._monthly_count]

        for date_str in firsts_to_keep:
            for obj in date_groups[date_str]:
                keep_keys.add(cast("str", obj.get("Key")))
            logger.info(
                "[IMP:7][retention][monthly] Keeping 1st-of-month date=%s (%d objects) — monthly marker %d/%d",
                date_str,
                len(date_groups[date_str]),
                firsts_to_keep.index(date_str) + 1,
                self._monthly_count,
            )

        logger.info(
            "[IMP:7][retention][compute] Retention computed: daily=%d/%dd weekly=%d/%d monthly=%d/%d total_keep=%d",
            len([d for d in sorted_dates if d >= daily_cutoff_str]),
            self._daily_days,
            len(sundays_to_keep),
            self._weekly_count,
            len(firsts_to_keep),
            self._monthly_count,
            len(keep_keys),
        )

        return keep_keys

    # endregion FUNC_compute_retention


# endregion CLASS_RetentionPolicy


# region FUNC_main
## @purpose  CLI entry point: parse args → load config → create S3 client → apply retention → exit
## @io       sys.argv → exit 0 (success) | exit 2 (config error)
## @complexity 2
def main() -> None:
    """
    ▶ ┌sys.argv --dry-run┐ → ◇ get_backup_config → ⊕ boto3 S3 client → ◇ RetentionPolicy.apply → ◇ print result → ⎋ exit 0|2

    CLI entry point for retention.py.

    Usage: retention.py [--dry-run]

    Exit codes:
        0 — retention scan completed (success or no changes)
        2 — configuration error
    """
    parser = argparse.ArgumentParser(description="Apply 7/28/90 retention policy to S3 backup objects.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without deleting objects",
    )

    args = parser.parse_args(namespace=CliArgs())  # W11: dataclass-namespace

    # Load configuration
    try:
        config = get_backup_config()
    except BackupConfigError as exc:
        logger.critical("[IMP:9][retention][main] Config error: %s", exc)
        sys.exit(2)

    # Create S3 client (D10/128 W5: таймауты — именованные константы модуля)
    boto_config = BotoConfig(
        retries={"max_attempts": _BOTO_RETRIES, "mode": "standard"},
        connect_timeout=_BOTO_CONNECT_TIMEOUT,
        read_timeout=_BOTO_READ_TIMEOUT,
    )

    # W11: boto3.client → Any (boto3 untyped) → cast к Boto3S3-протоколу
    s3_client = cast(
        "Boto3S3",
        boto3.client(  # pyright: ignore[reportUnknownMemberType] — W11 external boto3.client untyped-оверлоады
            "s3",
            endpoint_url=config["endpoint_url"],
            aws_access_key_id=config["aws_access_key_id"],
            aws_secret_access_key=config["aws_secret_access_key"],
            region_name=config["region"],
            config=boto_config,
        ),
    )

    # Wrap S3 client and create date parser
    s3_wrapper = S3Client(boto3_client=s3_client, bucket=config["bucket"])
    date_parser = DateParser()

    # Apply retention policy
    policy = RetentionPolicy(
        s3=s3_wrapper,
        parser=date_parser,
        bucket=config["bucket"],
        prefix=config["prefix"],
    )

    result = policy.apply(dry_run=args.dry_run)

    # W11: RetentionResult total=False — .get() вместо getitem (error-ключ не во всех ветках)
    if result.get("error"):
        print(f"[IMP:9][retention] ERROR: {result.get('error')}")
        sys.exit(2)

    if args.dry_run:
        print(f"[IMP:9][retention] DRY-RUN: would keep {result.get('kept')}, delete {result.get('deleted')}")
        print(f"[IMP:9][retention] Would delete keys: {', '.join((result.get('deleted_keys') or [])[:10])}...")
    else:
        print(f"[IMP:9][retention] RETENTION APPLIED: kept={result.get('kept')} deleted={result.get('deleted')}")

    sys.exit(0)


# endregion FUNC_main


if __name__ == "__main__":
    main()
