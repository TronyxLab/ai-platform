# GREP_SUMMARY: test backup_config s3-config env-vars context-detection endpoint-fallback missing-required defaults
# STRUCTURE: test_all_vars → test_missing_required_var[3 params] → test_endpoint_resolution[3 params] → test_context_resolution[5 params] → test_defaults_region_prefix
# region MODULE_CONTRACT
## @purpose  Unit tests for backup_config.py — environment-based S3 config loading, context detection,
##           missing value validation, endpoint fallback, context parsing.
## @scope    Direct import from backup_config module (sys.path set by conftest pytest_sessionstart).
##           No mocks needed — uses os.environ manipulation via monkeypatch.
## @invariants
##   - All test_* functions marked @pytest.mark.static_audit
##   - monkeypatch.setenv / delenv for env var manipulation
##   - Each test logs IMP:9 assertion + prints LDD trajectory
##   - test_missing_required_var: 3 params (access_key, secret_key, bucket)
##   - test_endpoint_resolution: 3 params (primary, default, legacy fallback)
##   - test_context_resolution: 5 params (personal, corporate, project-myapp, unknown, not-set)
## @rationale — backup_config.py (140 lines) is the single source of truth for S3 config;
##   testing all missing-var paths ensures backup-upload never silently uses defaults.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging

import pytest
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)


# region TESTS


@pytest.mark.static_audit
@ldd_trajectory
def test_all_vars_present(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        monkeypatch.setenv("S3_ACCESS_KEY", "ak-test")
        monkeypatch.setenv("S3_SECRET_KEY", "sk-test")
        monkeypatch.setenv("S3_BUCKET", "my-bucket")
        monkeypatch.setenv("S3_ENDPOINT_URL", "https://custom.s3.com")
        monkeypatch.setenv("S3_REGION", "eu-central-1")
        monkeypatch.setenv("S3_PREFIX", "custom/prefix")
        monkeypatch.setenv("PLATFORM_CONTEXT", "corporate")
        monkeypatch.setenv("NODE_NAME", "test-node")

        from backup_config import get_backup_config

        config = get_backup_config()

        logger.critical(
            "[IMP:9][test_backup_config][all_vars] ASSERT: endpoint=%s bucket=%s region=%s prefix=%s context=%s",
            config["endpoint_url"],
            config["bucket"],
            config["region"],
            config["prefix"],
            config["context"],
        )
        assert config["aws_access_key_id"] == "ak-test"
        assert config["aws_secret_access_key"] == "sk-test"
        assert config["bucket"] == "my-bucket"
        assert config["endpoint_url"] == "https://custom.s3.com"
        assert config["region"] == "eu-central-1"
        assert config["prefix"] == "custom/prefix"
        assert config["context"] == "corporate"
        assert config["node_name"] == "test-node"


@ldd_trajectory
@pytest.mark.static_audit
@pytest.mark.parametrize(
    "missing_var,error_match",
    [
        ("S3_ACCESS_KEY", "S3_ACCESS_KEY"),
        ("S3_SECRET_KEY", "S3_SECRET_KEY"),
        ("S3_BUCKET", "S3_BUCKET"),
    ],
)
def test_missing_required_var(missing_var: str, error_match: str, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        # Set all vars first, then delete the one under test
        monkeypatch.setenv("S3_ACCESS_KEY", "ak-test")
        monkeypatch.setenv("S3_SECRET_KEY", "sk-test")
        monkeypatch.setenv("S3_BUCKET", "my-bucket")
        monkeypatch.delenv(missing_var, raising=False)
        # Also delete AWS_ prefixed fallbacks
        aws_map = {"S3_ACCESS_KEY": "AWS_ACCESS_KEY_ID", "S3_SECRET_KEY": "AWS_SECRET_ACCESS_KEY"}
        if missing_var in aws_map:
            monkeypatch.delenv(aws_map[missing_var], raising=False)

        from backup_config import get_backup_config

        with pytest.raises(RuntimeError, match=error_match):
            get_backup_config()

        logger.critical(
            "[IMP:9][test_backup_config][missing_%s] ASSERT: RuntimeError for missing %s", missing_var, missing_var
        )


@ldd_trajectory
@pytest.mark.static_audit
@pytest.mark.parametrize(
    "endpoint_url,endpoint_legacy,expected",
    [
        ("https://primary.endpoint.com", "https://legacy.endpoint.com", "https://primary.endpoint.com"),
        (None, None, "https://s3.timeweb.cloud"),  # default
        (None, "https://legacy.s3.com", "https://legacy.s3.com"),  # legacy fallback
    ],
)
def test_endpoint_resolution(endpoint_url, endpoint_legacy, expected, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        monkeypatch.setenv("S3_ACCESS_KEY", "ak")
        monkeypatch.setenv("S3_SECRET_KEY", "sk")
        monkeypatch.setenv("S3_BUCKET", "b")
        if endpoint_url is not None:
            monkeypatch.setenv("S3_ENDPOINT_URL", endpoint_url)
        else:
            monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
        if endpoint_legacy is not None:
            monkeypatch.setenv("S3_ENDPOINT", endpoint_legacy)
        else:
            monkeypatch.delenv("S3_ENDPOINT", raising=False)

        from backup_config import get_backup_config

        config = get_backup_config()

        logger.critical(
            "[IMP:9][test_backup_config][endpoint] ASSERT: endpoint=%s (expected %s)", config["endpoint_url"], expected
        )
        assert config["endpoint_url"] == expected


@ldd_trajectory
@pytest.mark.static_audit
@pytest.mark.parametrize(
    "ctx_value,expected",
    [
        ("personal", "personal"),
        ("corporate", "corporate"),
        ("project-myapp", "project-myapp"),
        ("unknown-value", "personal"),  # fallback
        pytest.param(None, "personal", id="not-set-default"),  # not set → default 'personal'
    ],
)
def test_context_resolution(ctx_value, expected, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        monkeypatch.setenv("S3_ACCESS_KEY", "ak")
        monkeypatch.setenv("S3_SECRET_KEY", "sk")
        monkeypatch.setenv("S3_BUCKET", "b")
        if ctx_value is not None:
            monkeypatch.setenv("PLATFORM_CONTEXT", ctx_value)
        else:
            monkeypatch.delenv("PLATFORM_CONTEXT", raising=False)

        from backup_config import get_backup_config

        config = get_backup_config()

        logger.critical(
            "[IMP:9][test_backup_config][context] ASSERT: context=%s (expected %s)", config["context"], expected
        )
        assert config["context"] == expected


@pytest.mark.static_audit
@ldd_trajectory
def test_defaults_region_prefix(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        monkeypatch.setenv("S3_ACCESS_KEY", "ak")
        monkeypatch.setenv("S3_SECRET_KEY", "sk")
        monkeypatch.setenv("S3_BUCKET", "b")
        monkeypatch.delenv("S3_REGION", raising=False)
        monkeypatch.delenv("S3_PREFIX", raising=False)

        from backup_config import get_backup_config

        config = get_backup_config()

        logger.critical(
            "[IMP:9][test_backup_config][defaults] ASSERT: region=%s prefix=%s", config["region"], config["prefix"]
        )
        assert config["region"] == "us-east-1"
        assert config["prefix"] == "platform/backups"


# endregion TESTS
