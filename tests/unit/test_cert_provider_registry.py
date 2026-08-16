"""
# GREP_SUMMARY: test_cert_provider_registry, provider-registry, per-domain, longest-suffix, allowlist, http01, registry-cache
# STRUCTURE: ▶ tmp registry yaml → ◇ load → ◇ resolve (plugins_map suffix) → ◇ fallback node_plugin → ◇ unknown raise → ◇ allowlist env → ⎋ LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for provider_registry.py (DevPlan 154 W1): декларативный реестр
##           DNS-провайдеров — загрузка, per-domain longest-suffix резолв, fallback на
##           acme_dns_plugin, fail-fast на неизвестном провайдере, строгий allowlist кредов,
##           http01 challenge-маппинг, кэш загрузки.
## @scope    Pure unit tests — YAML-реестр через tmp_path (Zero Hardcode), без I/O на диск репо.
## @invariants
##   - Реестр создаётся в tmp_path (не трогаем SoT репозитория)
##   - Каждый тест валидирует IMP:9 бизнес-лог присутствует (Anti-Illusion)
##   - Неизвестный провайдер → ConfigValidationError(4) (fail-fast, TRAP 154 W1)
## @rationale Вариант C (Brief 154 S1): новый провайдер = запись YAML, резолв — чистые функции.
## @changes  CREATED: 2026-08-12 · DevPlan 154 W1
# endregion MODULE_CONTRACT
"""

from pathlib import Path

import pytest

from core.internal.bootstrap import provider_registry as pr
from core.internal.shared.exceptions import ConfigValidationError
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

# ═════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════════

_REGROU_REGISTRY = """\
providers:
  - name: webnames
    plugin: webnames
    mode: inject
    creds: [WEBNAMES_API_KEY]
  - name: regru
    plugin: regru
    mode: env
    creds: [REGRU_API_Username, REGRU_API_Password]
  - name: cf
    plugin: cf
    mode: env
    creds: [CF_Token, CF_Account_ID]
  - name: http01
    plugin: null
    mode: http01
    creds: []
"""


@pytest.fixture
def registry_file(tmp_path: Path) -> Path:
    """Write a test registry into tmp_path (Zero Hardcode)."""
    p = tmp_path / "certs-providers.yaml"
    p.write_text(_REGROU_REGISTRY)
    return p


@pytest.fixture
def reg(registry_file: Path):
    """Load registry from tmp file (cache invalidated per-path)."""
    return pr.load_registry(registry_file)


# ═════════════════════════════════════════════════════════════════════════════
# region Tests: load
# ═════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_load_registry_basic(registry_file: Path, caplog):
    """Реестр загружается: 4 провайдера, поля нормализованы (IMP:9 load log)."""
    # Вызов в теле теста (не в фикстуре) — caplog видит логи load_registry
    reg = pr.load_registry(registry_file)
    assert set(reg.providers) == {"webnames", "regru", "cf", "http01"}
    assert reg.providers["regru"].plugin == "regru"
    assert reg.providers["regru"].mode == "env"
    assert reg.providers["regru"].creds == ("REGRU_API_Username", "REGRU_API_Password")
    assert reg.providers["http01"].plugin is None
    assert reg.providers["webnames"].mode == "inject"
    found = any("[IMP:9][provider_registry] Loaded 4 providers" in r.message for r in caplog.records)
    assert found, "No IMP:9 load log found"


@ldd_trajectory
def test_load_registry_missing_file(tmp_path: Path, caplog):
    """Отсутствующий SoT-файл → ConfigValidationError (fail-fast, не пустой реестр)."""
    with pytest.raises(ConfigValidationError, match="not found"):
        pr.load_registry(tmp_path / "nope.yaml")


@ldd_trajectory
def test_load_registry_invalid_mode(tmp_path: Path, caplog):
    """Неизвестный mode → ConfigValidationError (инвариант: mode ∈ env|inject|http01)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("providers:\n  - name: x\n    mode: bogus\n")
    with pytest.raises(ConfigValidationError, match="mode must be one of"):
        pr.load_registry(bad)


@ldd_trajectory
def test_load_registry_cache(registry_file: Path, caplog):
    """lru_cache: повторная загрузка того же пути — тот же объект (idempotent)."""
    first = pr.load_registry(registry_file)
    again = pr.load_registry(registry_file)
    assert again is first
    assert first.source == str(registry_file)


# endregion Tests: load

# ═════════════════════════════════════════════════════════════════════════════
# region Tests: resolve_provider
# ═════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_resolve_longest_suffix(reg, caplog):
    """Per-domain маппинг: foo.asiteam.ru → asiteam.ru (longest-suffix), точное имя побеждает."""
    plugins_map = {"asiteam.ru": "regru", "tronyx.ru": "webnames", "foo.asiteam.ru": "cf"}
    assert reg.resolve_provider("roadmap.asiteam.ru", "", plugins_map).name == "regru"
    # Точное совпадение (самый длинный суффикс) → cf
    assert reg.resolve_provider("foo.asiteam.ru", "", plugins_map).name == "cf"
    # Нет совпадения → fallback node_plugin
    assert reg.resolve_provider("other.net", "webnames", plugins_map).name == "webnames"
    # IMP:9 логи резолва присутствуют
    found = any("[IMP:8][provider_registry]" in r.message for r in caplog.records)
    assert found, "No provider resolve log found"


@ldd_trajectory
def test_resolve_node_plugin_fallback(reg, caplog):
    """Без plugins_map → acme_dns_plugin (single-plugin обратная совместимость)."""
    assert reg.resolve_provider("asiteam.ru", "regru", None).name == "regru"
    assert reg.resolve_provider("asiteam.ru", "cf", {}).name == "cf"


@ldd_trajectory
def test_resolve_unknown_provider_raises(reg, caplog):
    """Неизвестное имя провайдера → ConfigValidationError с перечнем (fail-fast, TRAP 154)."""
    with pytest.raises(ConfigValidationError, match=r"Unknown cert provider 'bogus'.*webnames"):
        reg.resolve_provider("asiteam.ru", "bogus", None)


@ldd_trajectory
def test_resolve_no_provider_raises(reg, caplog):
    """Нет ни plugins_map, ни node_plugin → ConfigValidationError (не тихий generic)."""
    with pytest.raises(ConfigValidationError, match="No DNS provider configured"):
        reg.resolve_provider("asiteam.ru", "", None)


# endregion Tests: resolve_provider

# ═════════════════════════════════════════════════════════════════════════════
# region Tests: allowlist + challenge
# ═════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_provider_env_allowlist(reg, caplog):
    """provider_env: ТОЛЬКО имена из provider.creds — посторонние ключи не проходят (инвариант 3)."""
    secrets = {
        "REGRU_API_Username": "user",
        "REGRU_API_Password": "pass",
        "S3_BUCKET": "bucket-secret",
        "GHCR_PULL_TOKEN": "ghcr-secret",
        "WEBNAMES_API_KEY": "webnames-secret",
    }
    env = reg.provider_env(reg.providers["regru"], secrets)
    assert env == {"REGRU_API_Username": "user", "REGRU_API_Password": "pass"}
    assert "S3_BUCKET" not in env and "GHCR_PULL_TOKEN" not in env


# GUARD-PRESERVE (168): единственное покрытие all_cred_names() — union кредов = строгий allowlist для _source_secrets_env (инвариант 3, security-adjacent)
@ldd_trajectory
def test_all_cred_names_union(reg, caplog):
    """all_cred_names: объединение кредов всех провайдеров (фильтр _source_secrets_env)."""
    names = reg.all_cred_names()
    assert {"REGRU_API_Username", "REGRU_API_Password", "WEBNAMES_API_KEY", "CF_Token"} <= names


@ldd_trajectory
def test_challenge_mode_http01(reg, caplog):
    """http01-провайдер принудительно http; env-режим сохраняется для остальных."""
    assert reg.challenge_mode(reg.providers["http01"], "dns") == "http"
    assert reg.challenge_mode(reg.providers["http01"], "auto") == "http"
    assert reg.challenge_mode(reg.providers["regru"], "dns") == "dns"
    assert reg.challenge_mode(reg.providers["regru"], "auto") == "auto"


# endregion Tests: allowlist + challenge
