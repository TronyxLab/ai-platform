# GREP_SUMMARY: test date_parser extract-date sunday first-of-month static-methods
# STRUCTURE: test_extract_date_from_key_variants → test_is_sunday → test_is_first_of_month
# region MODULE_CONTRACT
## @purpose  Unit tests for DateParser — date extraction from keys, Sunday/first-of-month checks.
## @scope    Pure static method tests; no S3 dependencies; no mocks.
## @invariants
##   - All methods are @staticmethod
##   - Invalid inputs return None/False (fail-safe)
##   - All test_* functions print LDD trajectory
## @rationale — DateParser extracted from RetentionPolicy (H7 refactoring);
##   isolated testing ensures date parsing correctness without S3 mock overhead.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging

import pytest
from conftest import ldd_trajectory
from date_parser import DateParser

logger = logging.getLogger(__name__)


# region TESTS


@pytest.mark.static_audit
@ldd_trajectory
def test_extract_date_from_key_variants(caplog) -> None:
    """DateParser.extract_date_from_key handles all key variants."""
    with caplog.at_level(logging.DEBUG):
        test_cases = [
            ("platform/backups/postgres/pgdumpall_20260608T030015Z.sql.gz", "20260608"),
            ("app-data/app_20260608T033000Z.tar.gz", "20260608"),
            ("no_date_here/file.sql.gz", None),
            ("", None),
            ("20260608", None),  # No T...Z pattern
            ("backups/pgdumpall_20260101T000000Z.sql.gz", "20260101"),
            ("backups/pgdumpall_19991231T235959Z.sql.gz", "19991231"),
        ]

        for key, expected in test_cases:
            result = DateParser.extract_date_from_key(key)
            logger.info(
                "[IMP:7][test_date_parser][extract] key=%s → date=%s (expected=%s)",
                key,
                result,
                expected,
            )
            assert result == expected, f"For key={key!r}: expected {expected!r}, got {result!r}"

        logger.critical(
            "[IMP:9][test_date_parser][extract] ASSERT: All %d date extractions correct",
            len(test_cases),
        )


@pytest.mark.static_audit
@ldd_trajectory
@pytest.mark.parametrize(
    "date_str,expected",
    [
        ("20260607", True),  # Sunday
        ("20260614", True),  # Sunday
        ("20260608", False),  # Monday
        ("20260601", False),  # Monday
        ("20260615", False),  # Monday
        ("not-a-date", False),  # invalid
        ("20260230", False),  # invalid date
    ],
)
def test_is_sunday(date_str, expected, caplog) -> None:
    """DateParser.is_sunday correctly identifies Sundays."""
    with caplog.at_level(logging.DEBUG):
        result = DateParser.is_sunday(date_str)

        logger.critical(
            "[IMP:9][test_date_parser][is_sunday] date=%s result=%s (expected=%s)",
            date_str,
            result,
            expected,
        )
        assert result == expected


@pytest.mark.static_audit
@ldd_trajectory
@pytest.mark.parametrize(
    "date_str,expected",
    [
        ("20260601", True),  # 1st of June
        ("20260501", True),  # 1st of May
        ("20260602", False),  # 2nd
        ("20260615", False),  # 15th
        ("not-a-date", False),  # invalid
        ("20260230", False),  # invalid date
    ],
)
def test_is_first_of_month(date_str, expected, caplog) -> None:
    """DateParser.is_first_of_month correctly identifies 1st of month."""
    with caplog.at_level(logging.DEBUG):
        result = DateParser.is_first_of_month(date_str)

        logger.critical(
            "[IMP:9][test_date_parser][is_first] date=%s result=%s (expected=%s)",
            date_str,
            result,
            expected,
        )
        assert result == expected


# endregion TESTS
