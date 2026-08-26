# GREP_SUMMARY: test-shared-s3-client boto3 factory endpoint access-key secret-key retries env-fallback region
# STRUCTURE: ▶ test_explicit_params → test_env_fallback → test_max_attempts → test_region_fallback
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/s3_client.py — единая фабрика boto3 S3-клиента (DevPlan 117 D26).
## @scope    Tests: явные параметры, env-fallback (S3_*/AWS_*), max_attempts retries, region fallback.
## @invariants
##   - boto3.client мокается (нет реальных S3-вызовов)
##   - env-fallback цепочка: explicit → S3_* env → AWS_* env → defaults
##   - LDD: IMP:8 log присутствует при создании клиента
## @changes 2026-08-01 | DevPlan 117 D26 — создан
## @changes 2026-08-27 | DevPlan 015 F-08 — патч-таргет: s3_client.boto3.client → boto3.client
##            (lazy-импорт boto3 внутри get_s3_client — module-level атрибута boto3 больше нет)
# endregion MODULE_CONTRACT

import logging
import os
from unittest.mock import patch

import pytest

from core.internal.shared.s3_client import DEFAULT_S3_ENDPOINT_URL, get_s3_client

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Очистить S3_*/AWS_* env для изоляции тестов."""
    for key in [
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)


# region TEST_explicit_params
def test_explicit_params_override_env(clean_env, caplog: pytest.LogCaptureFixture) -> None:
    """Явные параметры имеют приоритет над env."""
    caplog.set_level(logging.INFO)
    os.environ["S3_ENDPOINT_URL"] = "https://env.example"
    os.environ["S3_ACCESS_KEY"] = "env-key"
    os.environ["S3_SECRET_KEY"] = "env-secret"
    os.environ["S3_REGION"] = "env-region"

    with patch("boto3.client") as mock_boto:
        get_s3_client(
            endpoint="https://explicit.example",
            access_key="explicit-key",
            secret_key="explicit-secret",
            max_attempts=3,
            region="explicit-region",
        )
        kwargs = mock_boto.call_args.kwargs
        assert kwargs["endpoint_url"] == "https://explicit.example"
        assert kwargs["aws_access_key_id"] == "explicit-key"
        assert kwargs["aws_secret_access_key"] == "explicit-secret"
        assert kwargs["region_name"] == "explicit-region"
        assert kwargs["config"].retries == {"max_attempts": 3, "mode": "standard"}


# endregion TEST_explicit_params


# region TEST_env_fallback
def test_env_fallback_s3_prefix(clean_env, caplog: pytest.LogCaptureFixture) -> None:
    """S3_* env предшествуют AWS_* env (совместимость с s3_ssl_cache)."""
    caplog.set_level(logging.INFO)
    os.environ["S3_ACCESS_KEY"] = "s3-key"
    os.environ["S3_SECRET_KEY"] = "s3-secret"
    os.environ["AWS_ACCESS_KEY_ID"] = "aws-key"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "aws-secret"
    os.environ["S3_REGION"] = "ru-1"

    with patch("boto3.client") as mock_boto:
        get_s3_client(max_attempts=3)
        kwargs = mock_boto.call_args.kwargs
        assert kwargs["aws_access_key_id"] == "s3-key"
        assert kwargs["aws_secret_access_key"] == "s3-secret"
        assert kwargs["region_name"] == "ru-1"
        assert kwargs["endpoint_url"] == DEFAULT_S3_ENDPOINT_URL


def test_env_fallback_aws_aliases(clean_env, caplog: pytest.LogCaptureFixture) -> None:
    """AWS_* env используются когда S3_* отсутствуют (AWS SDK совместимость)."""
    caplog.set_level(logging.INFO)
    os.environ["AWS_ACCESS_KEY_ID"] = "aws-key"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "aws-secret"

    with patch("boto3.client") as mock_boto:
        get_s3_client(max_attempts=3)
        kwargs = mock_boto.call_args.kwargs
        assert kwargs["aws_access_key_id"] == "aws-key"
        assert kwargs["aws_secret_access_key"] == "aws-secret"


def test_env_fallback_empty_when_unset(clean_env, caplog: pytest.LogCaptureFixture) -> None:
    """Без env — пустые ключи + default endpoint (graceful)."""
    caplog.set_level(logging.INFO)
    with patch("boto3.client") as mock_boto:
        get_s3_client(max_attempts=3)
        kwargs = mock_boto.call_args.kwargs
        assert kwargs["endpoint_url"] == DEFAULT_S3_ENDPOINT_URL
        assert not kwargs["aws_access_key_id"]
        assert not kwargs["aws_secret_access_key"]


# endregion TEST_env_fallback


# region TEST_max_attempts
def test_max_attempts_passthrough(clean_env, caplog: pytest.LogCaptureFixture) -> None:
    """max_attempts пробрасывается в BotoConfig retries (preflight probe = 1)."""
    caplog.set_level(logging.INFO)
    with patch("boto3.client") as mock_boto:
        get_s3_client(max_attempts=1)
        kwargs = mock_boto.call_args.kwargs
        assert kwargs["config"].retries == {"max_attempts": 1, "mode": "standard"}


# endregion TEST_max_attempts
