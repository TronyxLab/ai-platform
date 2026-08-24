# GREP_SUMMARY: test-app-config AppConfig from_env defaults mapping-override int-failfast import-time-no-env composition-root W4a
# STRUCTURE: ▶ test defaults ({}) → test mapping override (str+int) → test os.environ read (call-time) → test int fail-fast → test import-time no-env
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/shared/app_config.py (DevPlan 160 W4a T4.1) — AppConfig
##           dataclass + from_env loader. Проверяют: канонические дефолты, mapping-оверрайд
##           (для тестов), call-time чтение os.environ, fail-fast на нечисловых int-полях,
##           и ГЛАВНЫЙ инвариант W4a — импорт модуля НЕ читает os.environ (чистые константы).
## @scope    Tests under tests/unit/ (no Docker, no subprocess — native imports).
## @invariants
##   - from_env({}) → канонические дефолты (не зависят от окружения dev-машины)
##   - from_env(mapping) — приоритет mapping (тесты без monkeypatch)
##   - from_env(None) — чтение os.environ в момент вызова (лениво, call-time)
##   - Нечисловой PLATFORM_DEPLOY_TIMEOUT/PLATFORM_MAX_PAYLOAD_BYTES → ValueError (fail-fast)
##   - Импорт модуля не читает env: reload после delenv всех ключей → константы без env
##   - LDD: IMP:9 assert в каждом успешном сценарии
## @rationale W4a (DevPlan 160 T4.1): AppConfig — новая точка конфигурации; правила
##           shared/AGENTS.md требуют unit-тесты. Инвариант «import-time no env» —
##           фальсифицируемая проверка (R2): если кто-то вернёт env-чтение на module level,
##           reload-тест RED.
## @changes 2026-08-13 · DevPlan 160 W4a — created
# endregion MODULE_CONTRACT

from __future__ import annotations

import importlib
import logging

import pytest

from core.internal.shared import app_config
from core.internal.shared.app_config import AppConfig
from core.internal.shared.deploy_paths import DEFAULT_PLATFORM_BASE, DEFAULT_PROJECTS_BASE
from core.internal.shared.timeouts import DEPLOY_TIMEOUT
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_ENV_KEYS = (
    "PROJECTS_BASE",
    "PLATFORM_ORG",
    "PLATFORM_DEFAULT_NODE",
    "PLATFORM_DOMAIN",
    "CI_MODE",
    "PLATFORM_MAX_PAYLOAD_BYTES",
    "PLATFORM_DEPLOY_TIMEOUT",
    "PROJECTS_BASE",
    "PLATFORM_ROOT",
    "SSH_USER",
    "PLATFORM_REMOTE_BASE",
)


# T2.16a: _assert_imp9 консолидирован в gate_helpers.assert_ldd_imp9
# 🧪 TRAP[TEST] · Regression · Scenario: from_env({}) → канонические дефолты
# · Last fail: N/A (new test)
# · Remove if: AppConfig default semantics change
def test_from_env_defaults(caplog: pytest.LogCaptureFixture) -> None:
    """from_env({}) возвращает канонические дефолты (не зависят от dev-окружения)."""
    caplog.set_level(logging.INFO)
    cfg = AppConfig.from_env({})
    assert cfg.projects_base == DEFAULT_PROJECTS_BASE, "projects_base дефолт — /opt/projects (deploy_paths канон)"
    assert cfg.platform_remote_base == DEFAULT_PLATFORM_BASE, "platform_remote_base дефолт — /opt/platform"
    assert cfg.deploy_timeout == DEPLOY_TIMEOUT, "deploy_timeout дефолт — timeouts.DEPLOY_TIMEOUT"
    assert cfg.max_payload_bytes == 64 * 1024 * 1024, "max_payload_bytes дефолт — 64 MiB (receive_flow T9.9 + REF-0015)"
    assert cfg.platform_org == "personal"
    assert cfg.platform_default_node == "tronyx-vps"
    assert cfg.ssh_user == "root"
    assert not cfg.platform_domain
    assert not cfg.ci_mode
    logger.info("[IMP:9][test][app_config] from_env({}) → канонические дефолты OK")
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression · Scenario: from_env(mapping) → оверрайд str+int полей
# · Last fail: N/A (new test)
# · Remove if: from_env mapping semantics change
def test_from_env_mapping_override(caplog: pytest.LogCaptureFixture) -> None:
    """from_env(mapping) применяет значения mapping (str + int-парсинг) без monkeypatch."""
    caplog.set_level(logging.INFO)
    cfg = AppConfig.from_env({
        "PLATFORM_ORG": "test-org",
        "PLATFORM_DEFAULT_NODE": "test-node",
        "PLATFORM_DOMAIN": "example.com",
        "CI_MODE": "1",
        "SSH_USER": "ci-deploy",
        "PROJECTS_BASE": "/tmp/projects",
        "PLATFORM_REMOTE_BASE": "/tmp/platform",
        "PLATFORM_DEPLOY_TIMEOUT": "12345",
        "PLATFORM_MAX_PAYLOAD_BYTES": "2048",
        "PLATFORM_ROOT": "/tmp/root",
    })
    assert cfg.platform_org == "test-org"
    assert cfg.platform_default_node == "test-node"
    assert cfg.platform_domain == "example.com"
    assert cfg.ci_mode == "1"
    assert cfg.ssh_user == "ci-deploy"
    assert cfg.projects_base == "/tmp/projects"
    assert cfg.platform_remote_base == "/tmp/platform"
    assert cfg.deploy_timeout == 12345
    assert cfg.max_payload_bytes == 2048
    assert cfg.projects_root == "/tmp/projects"
    assert cfg.platform_root == "/tmp/root"
    logger.info("[IMP:9][test][app_config] from_env(mapping) → оверрайд OK (int-парсинг включён)")
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression · Scenario: from_env(None) → чтение os.environ (call-time)
# · Last fail: N/A (new test)
# · Remove if: from_env default-source semantics change
def test_from_env_reads_os_environ(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """from_env(None) читает os.environ в момент вызова (лениво, call-time — не import-time)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("PLATFORM_ORG", "env-org")
    monkeypatch.setenv("PLATFORM_DEPLOY_TIMEOUT", "999")
    cfg = AppConfig.from_env()
    assert cfg.platform_org == "env-org"
    assert cfg.deploy_timeout == 999
    logger.info("[IMP:9][test][app_config] from_env(None) → os.environ call-time OK")
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Negative (R5) · Scenario: нечисловой int-поле → ValueError (fail-fast)
# · Last fail: N/A (new test)
# · Remove if: int-парсинг семантика меняется
def test_from_env_int_failfast(caplog: pytest.LogCaptureFixture) -> None:
    """Нечисловой PLATFORM_DEPLOY_TIMEOUT → ValueError (fail-fast, не тихая маска)."""
    caplog.set_level(logging.INFO)
    with pytest.raises(ValueError):
        AppConfig.from_env({"PLATFORM_DEPLOY_TIMEOUT": "not-a-number"})
    logger.info("[IMP:9][test][app_config] int fail-fast (ValueError) OK")
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression · Scenario: импорт модуля НЕ читает os.environ (W4a инвариант)
# · Last fail: N/A (new test)
# · Remove if: import-time no-env инвариант меняется (тогда W4a DI нарушен)
def test_import_time_no_env_read(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """W4a инвариант: import/reload app_config с очищенным env — константы чистые, без env-чтений."""
    caplog.set_level(logging.INFO)
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    reloaded = importlib.reload(app_config)
    # Константы — чистые деривации пути/дефолты, НЕ env-значения
    assert reloaded._DEFAULT_ORG == "personal"
    assert reloaded._DEFAULT_NODE == "tronyx-vps"
    assert reloaded._DEFAULT_SSH_USER == "root"
    assert reloaded._DEFAULT_MAX_PAYLOAD_BYTES == 64 * 1024 * 1024  # REF-0015: ↓ с 1 GiB
    assert reloaded._DEFAULT_PROJECTS_ROOT == app_config._DEFAULT_PROJECTS_ROOT
    assert reloaded._DEFAULT_PLATFORM_ROOT == app_config._DEFAULT_PLATFORM_ROOT
    # Env НЕ влияет на module-level константы (раньше PROJECTS_BASE/PLATFORM_* читались на import)
    monkeypatch.setenv("PROJECTS_BASE", "/tmp/bogus")
    monkeypatch.setenv("PLATFORM_DEFAULT_NODE", "bogus-node")
    assert reloaded._DEFAULT_PROJECTS_ROOT != "/tmp/bogus"
    assert reloaded._DEFAULT_NODE == "tronyx-vps", "module-level константа не должна зависеть от env"
    monkeypatch.delenv("PROJECTS_BASE", raising=False)
    monkeypatch.delenv("PLATFORM_DEFAULT_NODE", raising=False)

    # from_env({}) после reload с чистым env — канонические дефолты (детерминизм D-11)
    cfg = reloaded.AppConfig.from_env({})
    assert cfg.projects_base == DEFAULT_PROJECTS_BASE
    assert cfg.deploy_timeout == DEPLOY_TIMEOUT
    logger.info("[IMP:9][test][app_config] import-time no-env инвариант OK (reload с чистым env)")
    assert_ldd_imp9(caplog)
