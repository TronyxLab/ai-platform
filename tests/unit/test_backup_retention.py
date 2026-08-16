# GREP_SUMMARY: test-retention 7-28-90 daily-weekly-monthly mock-boto3 rotate-delete ldd-trace
# STRUCTURE: fixtures(mock_s3) → test_daily_7d → test_weekly_4_sundays → test_monthly_3_firsts → test_combined → test_dry_run → test_empty → test_idempotent → test_imp9
"""
Tests for retention.py — 7/28/90 S3 retention rotation.

@purpose  Verify retention policy logic: daily 7d, weekly 4 Sundays, monthly 3 first-of-month.
          Ensure correct objects are kept/deleted without real S3 operations.
@scope    Unit tests; mock boto3 S3 client.
@invariants
  - No real S3 connection required (mock boto3 S3 client)
  - caplog fixture for IMP:7-10 trace
  - Prints LDD TRAJECTORY block before assertions
  - At least one IMP:9 log present per successful scenario
  - All dates relative to a fixed "now" for deterministic testing
  - PYTHONPATH must include core/modules/backup-cron/scripts/ for RetentionPolicy import
"""


# region MODULE_CONTRACT
## @purpose  Verify retention policy logic: daily 7d, weekly 4 Sundays, monthly 3 first-of-month.
##           Ensure correct objects are kept/deleted without real S3 operations.
## @scope    Unit tests; mock boto3 S3 client.
## @invariants
##   - No real S3 connection required (mock boto3 S3 client)
##   - caplog fixture for IMP:7-10 trace
##   - Prints LDD TRAJECTORY block before assertions
##   - At least one IMP:9 log present per successful scenario
##   - All dates relative to a fixed "now" for deterministic testing
##   - PYTHONPATH must include core/modules/backup-cron/scripts/ for RetentionPolicy import
## @rationale Q: Why unit test retention separately? A: Retention policy is a critical correctness
##             concern — a single off-by-one in date arithmetic could delete active backups.
##             Isolating tests ensures deterministic coverage without real S3.
## @changes — LAST_CHANGE: 2026-07-01 | Added MODULE_CONTRACT region for pre-commit compliance
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add backup-cron scripts to sys.path for DateParser and S3Client imports
_SCRIPTS_DIR: str = str(Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "backup-cron" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from date_parser import DateParser
from s3_client import S3Client

logger = logging.getLogger(__name__)

# region HELPERS
from conftest import ldd_trajectory


def _make_s3_object(key: str, size: int = 1000, last_modified: str = "2026-06-08T03:00:15Z") -> dict:
    """Create a mock S3 object dict."""
    return {
        "Key": key,
        "Size": size,
        "LastModified": last_modified,
    }


def _make_mock_s3(objects: list) -> MagicMock:
    """Create a mock boto3 S3 client returning specified object list."""
    mock_s3 = MagicMock()
    mock_s3.list_objects_v2.return_value = {
        "Contents": objects,
        "IsTruncated": False,
    }
    mock_s3.delete_objects.return_value = {"Deleted": [{"Key": k} for k in [o["Key"] for o in objects]]}
    return mock_s3


# Fixed "now" for deterministic testing: 2026-06-08 (Monday)
FIXED_NOW = datetime(2026, 6, 8, 5, 0, 0, tzinfo=timezone.utc)

# endregion HELPERS


# region RETENTION_TESTS


def _make_daily_test_objects():
    """Create 21 daily backup objects from 20 days ago to today."""
    objects = []
    for days_ago in range(20, -1, -1):
        dt = FIXED_NOW - timedelta(days=days_ago)
        date_str = dt.strftime("%Y%m%d")
        key = f"platform/backups/postgres/pgdumpall_{date_str}T030015Z.sql.gz"
        objects.append(_make_s3_object(key, last_modified=f"{date_str}T03:00:15Z"))
    return objects


def _make_weekly_test_objects():
    """Create 8 Sunday backup objects (back from 2026-06-07)."""
    sunday_dates = ["20260607", "20260531", "20260524", "20260517", "20260510", "20260503", "20260426", "20260419"]
    objects = []
    for date_str in sunday_dates:
        key = f"platform/backups/postgres/pgdumpall_{date_str}T030015Z.sql.gz"
        objects.append(_make_s3_object(key, last_modified=f"{date_str}T03:00:15Z"))
    return objects


def _make_monthly_test_objects():
    """Create 6 monthly backup objects (1st of each month)."""
    first_dates = ["20260601", "20260501", "20260401", "20260301", "20260201", "20260101"]
    objects = []
    for date_str in first_dates:
        key = f"platform/backups/postgres/pgdumpall_{date_str}T030015Z.sql.gz"
        objects.append(_make_s3_object(key, last_modified=f"{date_str}T03:00:15Z"))
    return objects


@ldd_trajectory
@pytest.mark.parametrize(
    "label,objects,policy_kwargs,expected_kept,expected_deleted",
    [
        ("daily", _make_daily_test_objects(), {"daily_days": 7, "weekly_count": 4, "monthly_count": 3}, 10, 11),
        ("weekly", _make_weekly_test_objects(), {"daily_days": 1, "weekly_count": 4, "monthly_count": 0}, 4, 4),
        ("monthly", _make_monthly_test_objects(), {"daily_days": 1, "weekly_count": 0, "monthly_count": 3}, 3, 3),
    ],
)
def test_retention_policy(label, objects, policy_kwargs, expected_kept, expected_deleted, caplog) -> None:
    """Retention policy: keeps correct number of backups."""
    with caplog.at_level(logging.DEBUG):
        from retention import RetentionPolicy

        mock_s3 = _make_mock_s3(objects)
        policy = RetentionPolicy(
            s3=S3Client(mock_s3, "backup-personal"),
            parser=DateParser(),
            bucket="backup-personal",
            prefix="platform/backups/",
            now=FIXED_NOW,
            **policy_kwargs,
        )

        result = policy.apply(dry_run=False)

        assert result["kept"] == expected_kept, f"[{label}] Expected kept={expected_kept}, got {result['kept']}"
        assert result["deleted"] == expected_deleted, (
            f"[{label}] Expected deleted={expected_deleted}, got {result['deleted']}"
        )
        assert result["kept"] + result["deleted"] == len(objects), f"[{label}] Total mismatch"

        logger.critical("[IMP:9][test_retention][%s] kept=%d deleted=%d", label, result["kept"], result["deleted"])


# GUARD-PRESERVE (168): единственное покрытие инварианта dry_run — delete_objects НЕ вызывается (0 мутаций S3)
@ldd_trajectory
def test_dry_run_does_not_delete(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        from retention import RetentionPolicy

        objects = [
            _make_s3_object("platform/backups/postgres/pgdumpall_20260601T030015Z.sql.gz"),
            _make_s3_object("platform/backups/postgres/pgdumpall_20260501T030015Z.sql.gz"),
            _make_s3_object("platform/backups/postgres/pgdumpall_20260401T030015Z.sql.gz"),
        ]
        mock_s3 = _make_mock_s3(objects)

        policy = RetentionPolicy(
            s3=S3Client(mock_s3, "backup-personal"),
            parser=DateParser(),
            bucket="backup-personal",
            prefix="platform/backups/",
            daily_days=1,
            weekly_count=0,
            monthly_count=1,
            now=FIXED_NOW,
        )

        result = policy.apply(dry_run=True)

        logger.critical(
            "[IMP:9][test_retention][dry_run] ASSERT: dry_run=%s kept=%d deleted=%d (delete_objects NOT called)",
            result["dry_run"],
            result["kept"],
            result["deleted"],
        )
        assert result["dry_run"] is True
        # delete_objects should NOT have been called
        mock_s3.delete_objects.assert_not_called()


@ldd_trajectory
def test_empty_bucket(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        from retention import RetentionPolicy

        mock_s3 = _make_mock_s3([])

        policy = RetentionPolicy(
            s3=S3Client(mock_s3, "backup-personal"),
            parser=DateParser(),
            bucket="backup-personal",
            prefix="platform/backups/",
            now=FIXED_NOW,
        )

        result = policy.apply(dry_run=False)

        logger.critical(
            "[IMP:9][test_retention][empty] ASSERT: kept=%d deleted=%d",
            result["kept"],
            result["deleted"],
        )
        assert result["kept"] == 0
        assert result["deleted"] == 0


@ldd_trajectory
def test_sunday_and_first_of_month_same_day(caplog) -> None:
    """When a date is BOTH a Sunday AND 1st of month, it should be kept
    (both weekly and monthly markers converge).
    """
    with caplog.at_level(logging.DEBUG):
        from retention import RetentionPolicy

        # 2026-03-01 is a Sunday AND 1st of March
        objects = [
            _make_s3_object("platform/backups/postgres/pgdumpall_20260301T030015Z.sql.gz"),
            _make_s3_object("platform/backups/postgres/pgdumpall_20260222T030015Z.sql.gz"),
            _make_s3_object("platform/backups/postgres/pgdumpall_20260215T030015Z.sql.gz"),
            _make_s3_object("platform/backups/postgres/pgdumpall_20260208T030015Z.sql.gz"),
            _make_s3_object("platform/backups/postgres/pgdumpall_20260201T030015Z.sql.gz"),
        ]
        mock_s3 = _make_mock_s3(objects)

        policy = RetentionPolicy(
            s3=S3Client(mock_s3, "backup-personal"),
            parser=DateParser(),
            bucket="backup-personal",
            prefix="platform/backups/",
            daily_days=1,
            weekly_count=1,
            monthly_count=1,
            now=FIXED_NOW,
        )

        result = policy.apply(dry_run=False)

        logger.critical(
            "[IMP:9][test_retention][combined_sunday_first] ASSERT: kept=%d deleted=%d (Sunday+1st same day)",
            result["kept"],
            result["deleted"],
        )
        # 2026-03-01 is both Sunday and 1st — kept once (dedup)
        # 2026-02-22 is the most recent Sunday before 03-01 — kept
        # 2026-02-01 is 1st — kept
        # Wait: weekly_count=1 keeps only 1 Sunday — 2026-03-01
        # monthly_count=1 keeps only 1 first — 2026-03-01 (same)
        # Daily=1 keeps nothing extra (only today)
        # So only 2026-03-01 should be kept (both tiers point to same date)
        assert result["kept"] == 1, f"Expected 1 kept (single date matching both tiers), got {result['kept']}"
        assert result["deleted"] == 4


@ldd_trajectory
@pytest.mark.parametrize(
    "key,expected",
    [
        ("platform/backups/postgres/pgdumpall_20260608T030015Z.sql.gz", "20260608"),
        ("app-data/app_20260608T033000Z.tar.gz", "20260608"),
        ("no_date_here/file.sql.gz", None),
        ("", None),
        ("20260608", None),  # No T...Z pattern
    ],
)
def test_date_extraction_from_key(key: str, expected: str | None, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        from date_parser import DateParser

        result = DateParser.extract_date_from_key(key)
        logger.info(
            "[IMP:7][test_retention][extract] key=%s → date=%s (expected=%s)",
            key,
            result,
            expected,
        )
        assert result == expected, f"For key={key!r}: expected {expected!r}, got {result!r}"

        logger.critical("[IMP:9][test_retention][extract] ASSERT: key=%s → date=%s", key, result)


@ldd_trajectory
def test_retention_idempotent(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        from retention import RetentionPolicy

        objects = []
        for days_ago in range(10, -1, -1):
            dt = FIXED_NOW - timedelta(days=days_ago)
            date_str = dt.strftime("%Y%m%d")
            key = f"platform/backups/postgres/pgdumpall_{date_str}T030015Z.sql.gz"
            objects.append(_make_s3_object(key, last_modified=f"{date_str}T03:00:15Z"))

        mock_s3_1 = _make_mock_s3(list(objects))
        policy1 = RetentionPolicy(
            s3=S3Client(mock_s3_1, "backup-personal"),
            parser=DateParser(),
            bucket="backup-personal",
            prefix="platform/backups/",
            now=FIXED_NOW,
        )
        result1 = policy1.apply(dry_run=True)

        # Second run with same input (minus deleted objects from first run)
        remaining_keys = set(result1["kept_keys"])
        remaining_objects = [o for o in objects if o["Key"] in remaining_keys]
        mock_s3_2 = _make_mock_s3(remaining_objects)
        policy2 = RetentionPolicy(
            s3=S3Client(mock_s3_2, "backup-personal"),
            parser=DateParser(),
            bucket="backup-personal",
            prefix="platform/backups/",
            now=FIXED_NOW,
        )
        result2 = policy2.apply(dry_run=True)

        logger.critical(
            "[IMP:9][test_retention][idempotent] ASSERT: run1 kept=%d deleted=%d run2 kept=%d deleted=%d",
            result1["kept"],
            result1["deleted"],
            result2["kept"],
            result2["deleted"],
        )
        # Second run should delete 0 (nothing left outside retention)
        assert result2["deleted"] == 0, f"Second run should delete nothing (idempotent), got {result2['deleted']}"
        assert result2["kept"] == len(remaining_objects)


# endregion RETENTION_TESTS
