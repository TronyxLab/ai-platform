# GREP_SUMMARY: test-shared-yaml-loader, yaml_loader, platform-env, secret-definitions, typed-reader, PlatformEnv, env_defaults, secrets, dedup
# STRUCTURE: ◇ load_platform_env 6× (sections/missing-file/malformed/missing-sections/None-normalize/identity) → ◇ load_secret_definitions 5× (valid/missing/not-list/str-path/skip-non-dict) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/yaml_loader.py — единый типизированный
##           SoT-YAML читатель (DevPlan 177 W3.5): load_platform_env → PlatformEnv,
##           load_secret_definitions → list[dict]. Проверяет консолидацию 3 локальных
##           парсеров (provisioner / sync_env_defaults / generate_secrets_manifest).
## @scope    tests/unit/ — без Docker, native imports (tmp_path, yaml).
## @invariants
##   - Все тесты используют tmp_path (zero hardcoded paths)
##   - LDD: каждый тест через ldd_trajectory (assert IMP:9) + caplog
##   - Identity-тест: provisioner.load_platform_env IS shared (дедупликация реальна)
##   - Identity-тест: generate_secrets_manifest.load_secret_definitions IS shared
## @rationale Инвентарь shared/ правило 3 — новый shared-модуль обязан иметь unit-тесты
##            (tests/unit/test_shared_MODULE.py). Кроет семантику, ранее жившую в 3 файлах.
## @changes  2026-08-16 | DevPlan 177 W3.5 — created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

from core.internal.provisioner import load_platform_env as provisioner_load_platform_env
from core.internal.scripts.generate_secrets_manifest import load_secret_definitions as gsm_load_secret_definitions
from core.internal.shared.yaml_loader import PlatformEnv, load_platform_env, load_secret_definitions
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit


# ═══════════════════════════════════════════════════════════════════
# region Tests: load_platform_env
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_platform_env parses all 4 sections
# · Scenario: valid platform-env.yaml → PlatformEnv with networks/volumes/env_defaults/profiles
# · Last fail: N/A (new test — W3.5 consolidation)
# · Remove if: load_platform_env logic changes
@ldd_trajectory
def test_load_platform_env_parses_sections(caplog, tmp_path: Path) -> None:
    """Parse full platform-env.yaml → all sections populated."""
    caplog.set_level(logging.DEBUG)
    yaml_path = tmp_path / "platform-env.yaml"
    yaml_path.write_text(
        "networks:\n"
        "  - name: proxy-net\n"
        "    driver: bridge\n"
        "volumes:\n"
        "  - path: /var/lib/platform/data\n"
        "env_defaults:\n"
        "  POSTGRES_PORT: 6432\n"
        "  POSTGRES_HOST: pgbouncer\n"
        "profiles:\n"
        "  - postgres\n"
        "  - monitoring\n",
        encoding="utf-8",
    )

    env = load_platform_env(yaml_path)

    assert isinstance(env, PlatformEnv)
    assert len(env.networks) == 1
    assert env.networks[0].name == "proxy-net"
    assert env.networks[0].driver == "bridge"
    assert len(env.volumes) == 1
    assert env.volumes[0].path == "/var/lib/platform/data"
    # W3.5: int значение нормализуется к str (общая семантика provisioner + sync_env_defaults)
    assert env.env_defaults == {"POSTGRES_PORT": "6432", "POSTGRES_HOST": "pgbouncer"}
    assert env.profiles == ["postgres", "monitoring"]

    logger.critical("[IMP:9][test] load_platform_env parsed %d sections", 4)


# 🧪 TRAP[TEST] · Regression · None env_defaults value → "" (not "None")
# · Scenario: env_defaults с None-значением → "" (прежняя семантика sync_env_defaults
# ·   сохранена в shared-читателе; provisioner не пишет "KEY=None" в GITHUB_ENV)
# · Last fail: N/A (new test — W3.5 normalization invariant)
# · Remove if: env_defaults normalization changes
@ldd_trajectory
def test_load_platform_env_none_normalized(caplog, tmp_path: Path) -> None:
    """None в env_defaults → '' (str-нормализация, без 'None'-мусора)."""
    caplog.set_level(logging.DEBUG)
    yaml_path = tmp_path / "platform-env.yaml"
    yaml_path.write_text("env_defaults:\n  NO_PROXY: null\n", encoding="utf-8")

    env = load_platform_env(yaml_path)

    assert env.env_defaults == {"NO_PROXY": ""}

    logger.critical("[IMP:9][test] None env_defaults → '' (normalization OK)")


# 🧪 TRAP[TEST] · Regression · missing file → FileNotFoundError
# · Scenario: несуществующий путь → FileNotFoundError (fail-fast, provisioner.main)
# · Last fail: N/A (new test — W3.5 consolidation)
# · Remove if: load_platform_env error semantics change
@ldd_trajectory
def test_load_platform_env_missing_file(caplog, tmp_path: Path) -> None:
    """Missing platform-env.yaml → FileNotFoundError."""
    caplog.set_level(logging.DEBUG)
    with pytest.raises(FileNotFoundError):
        load_platform_env(tmp_path / "does-not-exist.yaml")

    logger.critical("[IMP:9][test] load_platform_env missing file → FileNotFoundError")


# 🧪 TRAP[TEST] · Regression · malformed YAML → yaml.YAMLError
# · Scenario: битый YAML → yaml.YAMLError (main() ловит и возвращает exit 1)
# · Last fail: N/A (new test — W3.5 consolidation)
# · Remove if: load_platform_env error semantics change
@ldd_trajectory
def test_load_platform_env_malformed_yaml(caplog, tmp_path: Path) -> None:
    """Malformed YAML → yaml.YAMLError."""
    caplog.set_level(logging.DEBUG)
    yaml_path = tmp_path / "broken.yaml"
    yaml_path.write_text("networks:\n  - name: bad\n   driver: bridge\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        load_platform_env(yaml_path)

    logger.critical("[IMP:9][test] load_platform_env malformed YAML → yaml.YAMLError")


# 🧪 TRAP[TEST] · Regression · missing sections → empty defaults
# · Scenario: YAML только с profiles → пустые networks/volumes/env_defaults
# · Last fail: N/A (new test — W3.5 consolidation)
# · Remove if: missing-section handling changes
@ldd_trajectory
def test_load_platform_env_missing_sections(caplog, tmp_path: Path) -> None:
    """Missing networks/volumes/env_defaults → empty (graceful)."""
    caplog.set_level(logging.DEBUG)
    yaml_path = tmp_path / "minimal.yaml"
    yaml_path.write_text("profiles:\n  - backup-cron\n", encoding="utf-8")

    env = load_platform_env(yaml_path)

    assert env.networks == []
    assert env.volumes == []
    assert env.env_defaults == {}
    assert env.profiles == ["backup-cron"]

    logger.critical("[IMP:9][test] load_platform_env missing sections → empty defaults")


# 🧪 TRAP[TEST] · Regression · provisioner re-exports the SAME shared reader
# · Scenario: дедупликация реальна — provisioner.load_platform_env IS shared.load_platform_env
# · Last fail: дубль-парсер в provisioner.py (до W3.5)
# · Remove if: provisioner перестаёт re-export'ить shared-читатель
# GUARD-PRESERVE: единственный тест identity-инварианта консолидации (W3.5)
def test_provisioner_load_is_shared(caplog) -> None:
    """provisioner.load_platform_env — это shared/yaml_loader.load_platform_env (не дубль)."""
    caplog.set_level(logging.DEBUG)
    assert provisioner_load_platform_env is load_platform_env, (
        "W3.5 FAIL: provisioner должен re-export'ить shared-читатель, а не держать свой парсер"
    )

    logger.critical("[IMP:9][test] provisioner.load_platform_env IS shared (dedup verified)")


# endregion Tests: load_platform_env


# ═══════════════════════════════════════════════════════════════════
# region Tests: load_secret_definitions
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · valid secret-definitions → raw list preserving all fields
# · Scenario: секреты с полями tier/source/ci_default → list[dict] с ПОЛНЫМ сохранением полей
# · Last fail: N/A (new test — W3.5 consolidation)
# · Remove if: load_secret_definitions logic changes
@ldd_trajectory
def test_load_secret_definitions_valid(caplog, tmp_path: Path) -> None:
    """Valid secret-definitions.yaml → raw list with all fields preserved."""
    caplog.set_level(logging.DEBUG)
    secret_file = tmp_path / "secret-definitions.yaml"
    secret_file.write_text(
        "secrets:\n"
        "  - name: CLICKHOUSE_PASSWORD\n"
        "    tier: required\n"
        "    source: sops\n"
        "    ci_default: test-pwd\n"
        "    charset: '^[A-Za-z0-9._-]+$'\n",
        encoding="utf-8",
    )

    result = load_secret_definitions(secret_file)

    assert len(result) == 1
    entry = result[0]
    assert entry["name"] == "CLICKHOUSE_PASSWORD"
    # Все поля сохранены (не проекция!) — generate_secrets_manifest копирует их в манифест
    assert entry["tier"] == "required"
    assert entry["source"] == "sops"
    assert entry["ci_default"] == "test-pwd"
    assert entry["charset"] == "^[A-Za-z0-9._-]+$"

    logger.critical("[IMP:9][test] load_secret_definitions preserved all %d fields", len(entry))


# 🧪 TRAP[TEST] · Regression · str path accepted
# · Scenario: str-путь нормализуется в Path (прежний call-сайт generate_secrets_manifest)
# · Last fail: N/A (new test — W3.5 consolidation)
# · Remove if: path normalization changes
@ldd_trajectory
def test_load_secret_definitions_str_path(caplog, tmp_path: Path) -> None:
    """Str path → нормализуется в Path (call-семантика gsm сохранена)."""
    caplog.set_level(logging.DEBUG)
    secret_file = tmp_path / "secret-definitions.yaml"
    secret_file.write_text("secrets:\n  - name: POSTGRES_PASSWORD\n", encoding="utf-8")

    result = load_secret_definitions(str(secret_file))

    assert len(result) == 1
    assert result[0]["name"] == "POSTGRES_PASSWORD"

    logger.critical("[IMP:9][test] load_secret_definitions str path OK")


# 🧪 TRAP[TEST] · Regression · missing file → [] (не raise)
# · Scenario: несуществующий файл → [] + warning (семантика generate_secrets_manifest)
# · Last fail: N/A (new test — W3.5 consolidation)
# · Remove if: missing-file semantics change
@ldd_trajectory
def test_load_secret_definitions_missing_file(caplog, tmp_path: Path) -> None:
    """Missing file → [] (graceful, pre-flight проверяет существование до вызова)."""
    caplog.set_level(logging.DEBUG)
    result = load_secret_definitions(tmp_path / "missing.yaml")

    assert result == []

    logger.critical("[IMP:9][test] load_secret_definitions missing file → []")


# 🧪 TRAP[TEST] · Regression · 'secrets' not a list → []
# · Scenario: YAML без 'secrets' ключа → [] (не AttributeError)
# · Last fail: N/A (new test — W3.5 consolidation)
# · Remove if: non-list secrets handling changes
@ldd_trajectory
def test_load_secret_definitions_not_list(caplog, tmp_path: Path) -> None:
    """'secrets' не список → [] (грасeful degradation, семантика gsm)."""
    caplog.set_level(logging.DEBUG)
    secret_file = tmp_path / "secret-definitions.yaml"
    secret_file.write_text("secrets: not-a-list\n", encoding="utf-8")

    result = load_secret_definitions(secret_file)

    assert result == []

    logger.critical("[IMP:9][test] load_secret_definitions non-list secrets → []")


# 🧪 TRAP[TEST] · Regression · non-dict secret entries skipped
# · Scenario: строки в secrets-списке → пропускаются (isinstance-фильтр)
# · Last fail: N/A (new test — W3.5 consolidation)
# · Remove if: entry-type filtering changes
@ldd_trajectory
def test_load_secret_definitions_skips_non_dict(caplog, tmp_path: Path) -> None:
    """Non-dict записи в secrets → пропускаются."""
    caplog.set_level(logging.DEBUG)
    secret_file = tmp_path / "secret-definitions.yaml"
    secret_file.write_text("secrets:\n  - name: POSTGRES_PASSWORD\n  - just-a-string\n", encoding="utf-8")

    result = load_secret_definitions(secret_file)

    assert len(result) == 1
    assert result[0]["name"] == "POSTGRES_PASSWORD"

    logger.critical("[IMP:9][test] load_secret_definitions skipped non-dict entry")


# 🧪 TRAP[TEST] · Regression · generate_secrets_manifest re-exports the SAME shared reader
# · Scenario: дедупликация реальна — gsm.load_secret_definitions IS shared.load_secret_definitions
# · Last fail: дубль-парсер в generate_secrets_manifest.py (до W3.5)
# · Remove if: gsm перестаёт re-export'ить shared-читатель
# GUARD-PRESERVE: единственный тест identity-инварианта консолидации (W3.5)
def test_gsm_load_is_shared(caplog) -> None:
    """generate_secrets_manifest.load_secret_definitions — это shared-читатель (не дубль)."""
    caplog.set_level(logging.DEBUG)
    assert gsm_load_secret_definitions is load_secret_definitions, (
        "W3.5 FAIL: generate_secrets_manifest должен re-export'ить shared-читатель"
    )

    logger.critical("[IMP:9][test] gsm.load_secret_definitions IS shared (dedup verified)")


# endregion Tests: load_secret_definitions
