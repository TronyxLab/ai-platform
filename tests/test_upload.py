# GREP_SUMMARY: test upload s3-boto3 mock retry ClientError permanent-error FileNotFound upload_with_retries
# STRUCTURE: fixtures(fake_s3) → test_create_s3_client_config → test_upload_file_success → test_upload_file_not_found → test_upload_file_boto_error → test_is_permanent_error_403 → test_is_permanent_error_500 → test_retry_exhausted → test_retry_succeeds_on_retry → test_upload_verify → test_main_success
# region MODULE_CONTRACT
## @purpose  Unit tests for upload.py — S3 upload with retry logic, FakeS3Client instead of mocks.
## @scope    Uses FakeS3Client for boto3; no real S3 connections; no unittest.mock for boto3.
## @invariants
##   - All test_* functions marked @pytest.mark.static_audit
##   - tmp_path for local file operations
##   - No subprocess.run for business logic
##   - Each test logs IMP:9 assertion + prints LDD trajectory
## @rationale — upload.py (406 lines, 3 retries × 30min) is critical for backup reliability;
##   fake-based testing covers error branching without 90-min wait or real S3.
## @changes — REFACTORED: 2026-07-08 | MagicMock→FakeS3Client (Wave 2.1)
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os

import pytest
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)


# region FAKE_S3_CLIENT


class FakeS3Client:
    """Fake S3 client with same interface as boto3 S3 client, no network calls."""

    def __init__(self, objects: dict[str, bytes] | None = None, fail_on: str | None = None, fail_count: int = 1):
        self.objects = objects or {}
        self.fail_on = fail_on  # key name that triggers error
        self.fail_count = fail_count  # how many times to fail before succeeding
        self._call_count: dict[str, int] = {}
        self._uploads: list[dict] = []

    def upload_file(self, Filename: str, Bucket: str, Key: str, **kwargs) -> None:
        self._call_count.setdefault("upload_file", 0)
        self._call_count["upload_file"] += 1
        if self.fail_on and self.fail_on in Key and self._call_count["upload_file"] <= self.fail_count:
            from botocore.exceptions import ClientError

            raise ClientError(
                {
                    "Error": {"Code": "500", "Message": "Simulated error"},
                    "ResponseMetadata": {"HTTPStatusCode": 500},
                },
                "upload_file",
            )
        self._uploads.append({"file": Filename, "bucket": Bucket, "key": Key})

    def head_object(self, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "head_object",
            )
        return {
            "ContentLength": len(self.objects[Key]),
            "Metadata": {"sha256": "simulated-sha256-for-testing"},
        }


# endregion FAKE_S3_CLIENT


# region HELPERS


def _make_config(**overrides) -> dict:
    """Create a standard backup config dict, overridable."""
    config = {
        "endpoint_url": "https://s3.timeweb.cloud",
        "aws_access_key_id": "test-access-key",
        "aws_secret_access_key": "test-secret-key",
        "bucket": "test-bucket",
        "region": "us-east-1",
        "prefix": "platform/backups",
        "context": "personal",
        "node_name": "test-node",
    }
    config.update(overrides)
    return config


_DEFAULT_BOTO_RETRIES = 3

# endregion HELPERS


# region TESTS


@pytest.mark.static_audit
@ldd_trajectory
def test_create_s3_client_config(caplog) -> None:
    """create_s3_client constructs boto3 client with correct config.
    Uses unittest.mock.patch only for boto3 import — legitimate use.
    """
    from unittest.mock import patch as mock_patch

    with caplog.at_level(logging.DEBUG), mock_patch("upload.boto3") as mock_boto3:
        from upload import create_s3_client

        config = _make_config()
        client = create_s3_client(config)

        mock_boto3.client.assert_called_once()
        call_kwargs = mock_boto3.client.call_args[1]
        logger.critical(
            "[IMP:9][test_upload][create_client] ASSERT: endpoint=%s bucket=%s region=%s",
            call_kwargs.get("endpoint_url"),
            config["bucket"],
            call_kwargs.get("region_name"),
        )
        assert call_kwargs["endpoint_url"] == config["endpoint_url"]
        assert call_kwargs["aws_access_key_id"] == config["aws_access_key_id"]
        assert call_kwargs["aws_secret_access_key"] == config["aws_secret_access_key"]
        assert call_kwargs["region_name"] == config["region"]
        assert client is not None


@pytest.mark.static_audit
@ldd_trajectory
def test_upload_file_success(tmp_path, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        fake = FakeS3Client()

        from upload import upload_file

        local_path = os.path.join(str(tmp_path), "test_backup.sql.gz")
        with open(local_path, "w") as f:
            f.write("test data")

        result = upload_file(fake, "test-bucket", local_path, "platform/backups/test.sql.gz")

        logger.critical("[IMP:9][test_upload][upload_success] ASSERT: result=%s exc=%s", result[0], result[1])
        assert result[0] is True
        assert result[1] is None
        assert fake._uploads == [{"file": local_path, "bucket": "test-bucket", "key": "platform/backups/test.sql.gz"}]


@pytest.mark.static_audit
@ldd_trajectory
def test_upload_file_not_found(tmp_path, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        from upload import upload_file

        fake = FakeS3Client()
        missing_path = os.path.join(str(tmp_path), "nonexistent.sql.gz")

        with pytest.raises(FileNotFoundError, match="Local file not found"):
            upload_file(fake, "test-bucket", missing_path, "some/key")

        logger.critical("[IMP:9][test_upload][file_not_found] ASSERT: FileNotFoundError raised")


@pytest.mark.static_audit
@ldd_trajectory
def test_upload_file_boto_error(tmp_path, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        fake = FakeS3Client(fail_on="some/key", fail_count=999)

        from upload import upload_file

        local_path = os.path.join(str(tmp_path), "test.sql.gz")
        with open(local_path, "w") as f:
            f.write("data")

        result = upload_file(fake, "test-bucket", local_path, "some/key")

        logger.critical(
            "[IMP:9][test_upload][boto_error] ASSERT: result=%s exc=%s",
            result[0],
            type(result[1]).__name__ if result[1] else None,
        )
        assert result[0] is False
        assert result[1] is not None


@pytest.mark.static_audit
@ldd_trajectory
def test_is_permanent_error_403(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        from botocore.exceptions import ClientError
        from upload import _is_permanent_error

        error_response = {"Error": {"Code": "403", "Message": "Forbidden"}, "ResponseMetadata": {"HTTPStatusCode": 403}}
        exc = ClientError(error_response, "upload_file")
        result = _is_permanent_error(exc)

        logger.critical("[IMP:9][test_upload][perm_403] ASSERT: permanent=%s (expected True)", result)
        assert result is True


@pytest.mark.static_audit
@ldd_trajectory
def test_is_permanent_error_500(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        from botocore.exceptions import ClientError
        from upload import _is_permanent_error

        error_response = {
            "Error": {"Code": "500", "Message": "Internal Error"},
            "ResponseMetadata": {"HTTPStatusCode": 500},
        }
        exc = ClientError(error_response, "upload_file")
        result = _is_permanent_error(exc)

        logger.critical("[IMP:9][test_upload][perm_500] ASSERT: permanent=%s (expected False)", result)
        assert result is False


@pytest.mark.static_audit
@ldd_trajectory
def test_retry_exhausted(tmp_path, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        import upload as upload_module

        fake = FakeS3Client(fail_on="some/key", fail_count=999)

        local_path = os.path.join(str(tmp_path), "test.sql.gz")
        with open(local_path, "w") as f:
            f.write("data")

        result = upload_module.upload_with_retries(
            fake,
            "test-bucket",
            local_path,
            "some/key",
            max_retries=3,
            interval_sec=0,
        )

        logger.critical(
            "[IMP:9][test_upload][retry_exhausted] ASSERT: result=%s (expected False) call_count=%d",
            result,
            fake._call_count.get("upload_file", 0),
        )
        assert result is False
        assert fake._call_count.get("upload_file", 0) >= 3, "Expected at least 3 upload attempts"


@pytest.mark.static_audit
@ldd_trajectory
def test_retry_succeeds_on_retry(tmp_path, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        import upload as upload_module

        fake = FakeS3Client(fail_on="some/key", fail_count=1)

        local_path = os.path.join(str(tmp_path), "test.sql.gz")
        with open(local_path, "w") as f:
            f.write("data")

        result = upload_module.upload_with_retries(
            fake,
            "test-bucket",
            local_path,
            "some/key",
            max_retries=3,
            interval_sec=0,
        )

        logger.critical(
            "[IMP:9][test_upload][retry_succeeds] ASSERT: result=%s call_count=%d",
            result,
            fake._call_count.get("upload_file", 0),
        )
        assert result is True
        assert fake._call_count.get("upload_file", 0) == 2, "Expected 2 attempts (fail then succeed)"


@pytest.mark.static_audit
@ldd_trajectory
def test_get_s3_metadata_success(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        fake = FakeS3Client(objects={"some/key": b"x" * 12345})

        from upload import get_s3_metadata

        meta = get_s3_metadata(fake, "test-bucket", "some/key")

        logger.critical(
            "[IMP:9][test_upload][s3_meta] ASSERT: size=%s sha256=%s",
            meta["size"],
            meta["sha256"],
        )
        assert meta["size"] == 12345
        assert meta["sha256"] == "simulated-sha256-for-testing"


@pytest.mark.static_audit
@ldd_trajectory
def test_main_upload_success(tmp_path, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        from unittest.mock import patch as mock_patch

        import upload as upload_module

        config = _make_config()
        full_key = f"{config['prefix']}/backups/test.sql.gz".replace("//", "/")
        bucket = config["bucket"]
        local_file = os.path.join(str(tmp_path), "fake.sql.gz")
        with open(local_file, "w") as f:
            f.write("x" * 1000)

        fake = FakeS3Client(objects={full_key: b"x" * 1000})

        with (
            mock_patch("upload.get_backup_config", return_value=config),
            mock_patch("upload.create_s3_client", return_value=fake),
            mock_patch("upload.os.path.isfile", return_value=True),
            mock_patch("upload.os.path.getsize", return_value=1000),
        ):
            client = upload_module.create_s3_client(config)
            assert client is fake

            success = upload_module.upload_with_retries(
                client,
                bucket,
                local_file,
                full_key,
            )
            assert success is True

            s3_meta = upload_module.get_s3_metadata(client, bucket, full_key)
            assert s3_meta["size"] == 1000
            assert s3_meta["sha256"] is not None

        logger.critical("[IMP:9][test_upload][main_success] ASSERT: upload+verify pipeline OK")


# region TESTS_NEW_036


@pytest.mark.static_audit
@ldd_trajectory
def test_get_s3_metadata_client_error(caplog) -> None:
    """get_s3_metadata returns {size: None, sha256: None} with CRITICAL log on ClientError."""
    with caplog.at_level(logging.DEBUG):
        from upload import get_s3_metadata

        # FakeS3Client raises ClientError(404) for missing keys
        fake = FakeS3Client(objects={})  # empty objects — head_object raises ClientError
        meta = get_s3_metadata(fake, "test-bucket", "nonexistent/key")

        logger.critical(
            "[IMP:9][test_upload][meta_client_error] ASSERT: size=%s sha256=%s (expected None)",
            meta["size"],
            meta["sha256"],
        )
        assert meta["size"] is None
        assert meta["sha256"] is None

        # Verify CRITICAL log was emitted
        found_critical = False
        for record in caplog.records:
            if record.levelname == "CRITICAL" and "Cannot verify S3 object" in record.message:
                found_critical = True
                break
        assert found_critical, "Expected CRITICAL log 'Cannot verify S3 object'"


@pytest.mark.static_audit
@ldd_trajectory
def test_get_s3_metadata_unexpected_error(caplog) -> None:
    """get_s3_metadata propagates non-ClientError exceptions (no silent masking)."""
    with caplog.at_level(logging.DEBUG):
        from upload import get_s3_metadata

        class BrokenClient:
            """Fake client that raises a non-ClientError on head_object."""

            def head_object(self, Bucket: str, Key: str) -> dict:
                raise ConnectionError("Simulated network failure")

        with pytest.raises(ConnectionError, match="Simulated network failure"):
            get_s3_metadata(BrokenClient(), "test-bucket", "some/key")

        logger.critical("[IMP:9][test_upload][meta_unexpected] ASSERT: ConnectionError propagated")


@pytest.mark.static_audit
@ldd_trajectory
def test_main_verification_fails_on_none_metadata(tmp_path, caplog) -> None:
    """_upload_and_verify exits with code 1 when s3_size is None."""
    with caplog.at_level(logging.DEBUG):
        from unittest.mock import patch as mock_patch

        import upload as upload_module

        config = _make_config()
        full_key = f"{config['prefix']}/test_verify_fail.sql.gz".replace("//", "/")
        local_file = os.path.join(str(tmp_path), "test_verify_fail.sql.gz")
        with open(local_file, "w") as f:
            f.write("x" * 500)

        # Fake client with empty objects — upload succeeds but head_object returns None metadata
        fake = FakeS3Client(objects={})

        with (
            mock_patch("upload.get_backup_config", return_value=config),
            mock_patch("upload.create_s3_client", return_value=fake),
        ):
            client = upload_module.create_s3_client(config)
            assert client is fake

            # Upload should succeed (no fail_on set)
            success = upload_module.upload_with_retries(
                client,
                config["bucket"],
                local_file,
                full_key,
                max_retries=1,
                interval_sec=0,
            )
            assert success is True

            # Now verify — should exit 1 because s3_size is None (key not in objects dict)
            local_sha256 = upload_module.compute_sha256(local_file)
            with pytest.raises(SystemExit) as exc_info:
                upload_module._upload_and_verify(
                    client,
                    config["bucket"],
                    local_file,
                    full_key,
                    local_sha256,
                    max_retries=1,
                    interval_sec=0,
                )

            logger.critical(
                "[IMP:9][test_upload][verify_fail] ASSERT: exit code=%s (expected 1)",
                exc_info.value.code,
            )
            assert exc_info.value.code == 1


# endregion TESTS_NEW_036

# endregion TESTS
