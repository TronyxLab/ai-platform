# GREP_SUMMARY: date_parser static-methods extract-date sunday first-of-month
# STRUCTURE: class DateParser → extract_date_from_key → is_sunday → is_first_of_month
# region MODULE_CONTRACT
"""
Date parsing utilities for backup-cron retention logic.

@purpose  Extract dates from S3 object keys, check day-of-week/month properties.
          Pure static methods — no dependencies on boto3 or S3.
@scope    Used by RetentionPolicy for date-based retention classification.
@invariants
  - All methods are @staticmethod — no instance state
  - extract_date_from_key expects YYYYMMDDTHHMMSSZ pattern
  - Invalid dates return False/None (fail-safe)
"""
# endregion MODULE_CONTRACT

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)


# region CLASS_DateParser
class DateParser:
    """Static date parsing utilities for backup retention logic."""

    @staticmethod
    def extract_date_from_key(key: str) -> str | None:
        """
        Extract date (YYYYMMDD) from an S3 object key.

        Expected patterns:
          - pgdumpall_20260608T030015Z.sql.gz
          - app_20260608T033000Z.tar.gz

        Returns YYYYMMDD string or None.
        """
        match = re.search(r"(\d{8})T\d{6}Z", key)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def is_sunday(date_str: str) -> bool:
        """Check if a YYYYMMDD date falls on a Sunday."""
        try:
            dt = datetime.strptime(date_str, "%Y%m%d")
            return dt.weekday() == 6
        except ValueError:
            return False

    @staticmethod
    def is_first_of_month(date_str: str) -> bool:
        """Check if a YYYYMMDD date is the 1st day of the month."""
        try:
            dt = datetime.strptime(date_str, "%Y%m%d")
            return dt.day == 1
        except ValueError:
            return False


# endregion CLASS_DateParser
