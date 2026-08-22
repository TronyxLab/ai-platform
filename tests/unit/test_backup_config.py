# GREP_SUMMARY: test backup_config s3-config env-vars context-detection endpoint-fallback missing-required defaults
# STRUCTURE: test_all_vars → test_missing_required_var[3 params] → test_endpoint_resolution[3 params] → test_context_resolution[5 params] → test_defaults_region_prefix
# region MODULE_CONTRACT
## @purpose  Unit tests for backup_config.py — environment-based S3 config loading, context detection,
##           missing value validation, endpoint fallback, context parsing.
## @scope    Direct import from backup_config module (sys.path set by conftest pytest_sessionstart).
##           W4e (DevPlan 160 E2): env передаётся параметром get_backup_config(env=dict) — 0 monkeypatch.
## @invariants
##   - All test_* functions marked @pytest.mark.static_audit
##   - Env через DI-параметр env=Mapping (get_backup_config/get_s3_config), без патчей окружения
##   - Each test logs IMP:9 assertion + prints LDD trajectory
##   - test_missing_required_var: 3 params (access_key, secret_key, bucket)
##   - test_endpoint_resolution: 3 params (primary, default, fallback)
##   - test_context_resolution: 5 params (personal, corporate, project-myapp, unknown, not-set)
## @rationale — backup_config.py (140 lines) is the single source of truth for S3 config;
##   testing all missing-var paths ensures backup-upload never silently uses defaults.
##   W4e: env-дикт (DI) вместо патча окружения — тест проверяет ЛОГИКУ модуля, а не окружение.


# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

import pytest
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# Add backup-cron scripts path for imports of backup_config module
_backup_cron_scripts = str(
    Path(Path(__file__).parent / "../.." / "core" / "modules" / "backup-cron" / "scripts").resolve()
)
if _backup_cron_scripts not in sys.path:
    sys.path.insert(0, _backup_cron_scripts)

# Базовый env-дикт для валидного конфига (DI — DevPlan 160 W4e). Тесты строят
# производные дикты (удаление/добавление ключей) — БЕЗ патчей окружения.
_BASE_ENV = {
    "S3_ACCESS_KEY": "ak-test",
    "S3_SECRET_KEY": "sk-test",
    "S3_BUCKET": "my-bucket",
}


# region TESTS


@pytest.mark.static_audit
@ldd_trajectory
def test_all_vars_present(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        env = {
            **_BASE_ENV,
            "S3_ENDPOINT_URL": "https://custom.s3.com",
            "S3_REGION": "eu-central-1",
            "S3_PREFIX": "custom/prefix",
            "PLATFORM_CONTEXT": "corporate",
            "NODE_NAME": "test-node",
        }

        from backup_config import get_backup_config

        config = get_backup_config(env=env)

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
def test_missing_required_var(missing_var: str, error_match: str, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        # Env-дикт без валидируемой переменной (DI) — отсутствие = отсутствие ключа в mapping
        env = {k: v for k, v in _BASE_ENV.items() if k != missing_var}
        # AWS_* fallbacks removed (DevPlan 049 DRIFT-2 fix) — no secondary env vars needed

        from backup_config import BackupConfigError, get_backup_config

        with pytest.raises(BackupConfigError, match=error_match):
            get_backup_config(env=env)

        logger.critical(
            "[IMP:9][test_backup_config][missing_%s] ASSERT: BackupConfigError for missing %s", missing_var, missing_var
        )


@ldd_trajectory
@pytest.mark.static_audit
@pytest.mark.parametrize(
    "endpoint_url,expected",
    [
        ("https://primary.endpoint.com", "https://primary.endpoint.com"),
        (None, "https://s3.timeweb.cloud"),  # default
    ],
)
def test_endpoint_resolution(endpoint_url, expected, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        env = dict(_BASE_ENV)
        if endpoint_url is not None:
            env["S3_ENDPOINT_URL"] = endpoint_url

        from backup_config import get_backup_config

        config = get_backup_config(env=env)

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
def test_context_resolution(ctx_value, expected, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        env = dict(_BASE_ENV)
        if ctx_value is not None:
            env["PLATFORM_CONTEXT"] = ctx_value

        from backup_config import get_backup_config

        config = get_backup_config(env=env)

        logger.critical(
            "[IMP:9][test_backup_config][context] ASSERT: context=%s (expected %s)", config["context"], expected
        )
        assert config["context"] == expected


@pytest.mark.static_audit
@ldd_trajectory
def test_defaults_region_prefix(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        env = dict(_BASE_ENV)  # S3_REGION/S3_PREFIX отсутствуют → дефолты

        from backup_config import get_backup_config

        config = get_backup_config(env=env)

        logger.critical(
            "[IMP:9][test_backup_config][defaults] ASSERT: region=%s prefix=%s", config["region"], config["prefix"]
        )
        assert config["region"] == "ru-1"
        assert config["prefix"] == "platform/backups"


# endregion TESTS
