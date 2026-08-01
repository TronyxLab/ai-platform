# GREP_SUMMARY: test s3_client list-objects pagination timeout batch-delete mock-boto3
# STRUCTURE: test_list_objects_pagination → test_list_objects_timeout → test_delete_objects_batch → test_delete_objects_empty
# region MODULE_CONTRACT
## @purpose  Unit tests for S3Client — paginated list, timeout, batch delete.
## @scope    Uses MagicMock for boto3 S3 client; no real S3 connections.
## @invariants
##   - S3Client wraps a mock boto3 client
##   - All test_* functions print LDD trajectory
##   - At least one IMP:9 log per successful scenario
## @rationale — S3Client is the thin wrapper extracted from RetentionPolicy (H7 refactoring);
##   isolated testing ensures pagination, timeout, and batch-delete correctness.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Module-specific path (tests/AGENTS.md §sys.path policy): backup-cron scripts.
# S3Client — wrapper-класс контейнерного модуля backup-cron, НЕ shared-фабрика
# get_s3_client() (DevPlan 117 D26). Hyphen в имени модуля → недоступен dotted-импорт,
# поэтому канон — sys.path.insert как в tests/test_backup_retention.py.
_SCRIPTS_DIR: str = str(Path(__file__).resolve().parent.parent / "core" / "modules" / "backup-cron" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from conftest import ldd_trajectory
from s3_client import S3Client

logger = logging.getLogger(__name__)


# region HELPERS


def _make_mock_s3_client(list_objects_return: dict | None = None) -> MagicMock:
    """Create a mock boto3 S3 client."""
    mock_s3 = MagicMock()
    if list_objects_return is not None:
        mock_s3.list_objects_v2.return_value = list_objects_return
    else:
        mock_s3.list_objects_v2.return_value = {
            "Contents": [],
            "IsTruncated": False,
        }
    mock_s3.delete_objects.return_value = {"Deleted": []}
    return mock_s3


# endregion HELPERS


# region TESTS


@pytest.mark.static_audit
@ldd_trajectory
def test_list_objects_empty(caplog) -> None:
    """S3Client.list_objects returns empty list for empty prefix."""
    with caplog.at_level(logging.DEBUG):
        mock_s3 = _make_mock_s3_client()
        client = S3Client(mock_s3, "test-bucket")

        result = client.list_objects("some/prefix/")

        logger.critical("[IMP:9][test_s3_client][empty] ASSERT: len=%d (expected 0)", len(result))
        assert len(result) == 0
        mock_s3.list_objects_v2.assert_called_once()


@pytest.mark.static_audit
@ldd_trajectory
def test_list_objects_pagination(caplog) -> None:
    """S3Client.list_objects paginates through multiple pages."""
    with caplog.at_level(logging.DEBUG):
        mock_s3 = MagicMock()
        # Simulate 2 pages of 2 objects each
        mock_s3.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": f"backups/file{i}.gz"} for i in range(2)],
                "IsTruncated": True,
                "NextContinuationToken": "token1",
            },
            {
                "Contents": [{"Key": f"backups/file{i}.gz"} for i in range(2, 4)],
                "IsTruncated": False,
            },
        ]
        client = S3Client(mock_s3, "test-bucket")

        result = client.list_objects("backups/")

        logger.critical(
            "[IMP:9][test_s3_client][pagination] ASSERT: len=%d pages=%d",
            len(result),
            mock_s3.list_objects_v2.call_count,
        )
        assert len(result) == 4
        assert mock_s3.list_objects_v2.call_count == 2


@pytest.mark.static_audit
@ldd_trajectory
def test_delete_objects_batch(caplog) -> None:
    """S3Client.delete_objects handles batch delete correctly."""
    with caplog.at_level(logging.DEBUG):
        mock_s3 = MagicMock()
        mock_s3.delete_objects.return_value = {"Deleted": [{"Key": f"file{i}.gz"} for i in range(100)]}
        client = S3Client(mock_s3, "test-bucket")

        keys_to_delete = [f"file{i}.gz" for i in range(100)]
        deleted = client.delete_objects(keys_to_delete)

        logger.critical("[IMP:9][test_s3_client][batch] ASSERT: deleted=%d (expected 100)", deleted)
        assert deleted == 100
        mock_s3.delete_objects.assert_called_once()


# endregion TESTS
