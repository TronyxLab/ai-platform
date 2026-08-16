"""
# GREP_SUMMARY: test_node_yaml_mixins, DomainsMixin, get_domain_config, get_context, domain, contexts, H1, get_domain_config, get_context, domain, contexts, H1
# STRUCTURE: ▶ tmp_path YAML → ◇ DomainsMixin.get_context() → ◇ get_domain_config() → ◇ add_context() → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for DomainsMixin (core/internal/shared/node_yaml/domains.py, DevPlan 119 H1).
##           Проверяет, что доменный миксин работает через NodeYaml-агрегатор и напрямую.
## @scope    Поддомены domain + contexts node.yaml: get_context() (contexts[] canon), get_domain_config()
##           (flat schema), add_context() (mutation). DevPlan $TEST_SPEC просил test_get_domain — метод
##           get_domain() УДАЛЁН волной 118 B3 (verify-then-delete: 0 потребителей); живой эквивалент —
##           get_domain_config() (потребитель preflight.py + CLI --domain-config). Адаптация зафлагана.
## @invariants
##   - All YAML files created via tmp_path (Zero Hardcode Rule)
##   - Each test validates LDD IMP:9 presence via @ldd_trajectory
##   - get_context() → "" on missing contexts (no-raise contract)
## @changes 2026-08-03 · DevPlan 119 H1 — создан (миксины node_yaml/domains.py)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from core.internal.shared.node_yaml import DomainsMixin, NodeYaml
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _write_yaml(tmp_path: Path, content: str) -> Path:
    """Write a node.yaml fixture to tmp_path (no hardcoded paths)."""
    path = tmp_path / "node.yaml"
    path.write_text(content)
    return path


# 🧪 TRAP[TEST] · Regression (H1) · get_context через DomainsMixin
# · Scenario: contexts[0].name dict → возвращает имя контекста
# · Last fail: N/A (canon contexts[] DevPlan 116 B6 T1)
# · Remove if: contexts[] canon semantics change
@ldd_trajectory
def test_get_context_via_domains_mixin(caplog, tmp_path):
    """get_context() через NodeYaml-агрегатор (DomainsMixin) — contexts[0].name canon."""
    yaml_path = _write_yaml(tmp_path, "contexts:\n  - name: myorg\nnode:\n  name: n\n  host: 1.2.3.4\n")
    node = NodeYaml(str(yaml_path))
    assert node.get_context() == "myorg"
    logger.critical("[IMP:9][test] get_context via DomainsMixin: context=%s — OK", node.get_context())


# 🧪 TRAP[TEST] · Regression (H1) · get_context пустые contexts → ""
# · Scenario: отсутствие contexts → "" (no raise)
# · Last fail: N/A (no-raise contract)
# · Remove if: no-raise contract changes
@ldd_trajectory
def test_get_context_empty_via_domains_mixin(caplog, tmp_path):
    """get_context() возвращает "" при отсутствии contexts (no-raise, DomainsMixin)."""
    yaml_path = _write_yaml(tmp_path, "domain: example.com\n")
    node = NodeYaml(str(yaml_path))
    assert not node.get_context()
    logger.critical("[IMP:9][test] get_context empty via DomainsMixin: '' — OK")


# 🧪 TRAP[TEST] · Regression (H1) · get_domain_config через DomainsMixin
# · Scenario: flat domain/email/acme_dns_plugin + project_domains из projects[].domain
# · Last fail: N/A (canon flat schema DevPlan 116 B6 T7)
# · Remove if: flat-domain schema changes
@ldd_trajectory
def test_get_domain_config_via_domains_mixin(caplog, tmp_path):
    """get_domain_config() через DomainsMixin — flat schema, project_domains агрегированы."""
    yaml_path = _write_yaml(
        tmp_path,
        "domain: example.com\nemail: admin@example.com\nacme_dns_plugin: cf\n"
        "projects:\n  - name: p1\n    repo: org/p1\n    type: backend\n    domain: p1.example.com\n",
    )
    node = NodeYaml(str(yaml_path))
    cfg = node.get_domain_config()
    assert cfg.platform_domain == "example.com"
    assert cfg.email == "admin@example.com"
    assert cfg.acme_dns_plugin == "cf"
    assert cfg.project_domains == ["p1.example.com"]
    logger.critical("[IMP:9][test] get_domain_config via DomainsMixin: %s — OK", cfg.platform_domain)


# 🧪 TRAP[TEST] · Regression (H1) · get_domain_config дефолты при отсутствии
# · Scenario: пустой YAML → DomainConfig с пустыми полями
# · Last fail: N/A (defaults contract)
# · Remove if: defaults contract changes
@ldd_trajectory
def test_get_domain_config_defaults_via_domains_mixin(caplog, tmp_path):
    """get_domain_config() возвращает дефолты при отсутствии ключей (DomainsMixin)."""
    yaml_path = _write_yaml(tmp_path, "contexts:\n  - name: c\n")
    node = NodeYaml(str(yaml_path))
    cfg = node.get_domain_config()
    assert not cfg.platform_domain
    assert not cfg.email
    assert not cfg.acme_dns_plugin
    assert cfg.project_domains == []
    logger.critical("[IMP:9][test] get_domain_config defaults — OK")


# 🧪 TRAP[TEST] · Regression (H1) · add_context через DomainsMixin
# · Scenario: add_context() записывает контекст и читается обратно
# · Last fail: N/A (mutation DevPlan 116 B6 D2/T6.3)
# · Remove if: mutation API changes
@ldd_trajectory
def test_add_context_via_domains_mixin(caplog, tmp_path):
    """add_context() через DomainsMixin — мутация contexts[] с записью на диск."""
    yaml_path = _write_yaml(tmp_path, "contexts:\n  - name: c1\n")
    node = NodeYaml(str(yaml_path))
    assert node.add_context(name="c2", description="second") is True
    # reload с диска — запись подтверждена
    fresh = NodeYaml(str(yaml_path))
    assert fresh.get_context() == "c1"  # contexts[0].name не изменился
    assert any(c.get("name") == "c2" for c in fresh.get_list("contexts"))
    logger.critical("[IMP:9][test] add_context via DomainsMixin: c2 added — OK")


# 🧪 TRAP[TEST] · Negative (R5, H1) · add_context duplicate → ConfigValidationError
# · Scenario: добавление контекста с существующим именем → ConfigValidationError
# · Last fail: N/A (duplicate contract DevPlan 116 B6 D2)
# · Remove if: duplicate contract changes
@ldd_trajectory
def test_add_context_duplicate_negative(caplog, tmp_path):
    """add_context() бросает ConfigValidationError на duplicate (R5 negative, DomainsMixin)."""
    import pytest

    from core.internal.shared.exceptions import ConfigValidationError

    yaml_path = _write_yaml(tmp_path, "contexts:\n  - name: c1\n")
    node = NodeYaml(str(yaml_path))
    with pytest.raises(ConfigValidationError):
        node.add_context(name="c1")
    logger.critical("[IMP:9][test] add_context duplicate → ConfigValidationError — OK")


# 🧪 TRAP[TEST] · Direct mixin instantiation (H1)
# · Scenario: DomainsMixin напрямую (без NodeYaml) — базовый класс, _load определён подклассом
# · Last fail: N/A (mixin pattern)
# · Remove if: mixin architecture changes
@ldd_trajectory
def test_domains_mixin_direct_instantiation(caplog, tmp_path):
    """DomainsMixin можно инстанцировать напрямую (mixin-паттерн, не абстрактный)."""
    yaml_path = _write_yaml(tmp_path, "contexts:\n  - name: direct\n")

    class _Minimal(DomainsMixin):
        def __init__(self, path):
            self._path = str(path)
            self._data = None

        def _load(self) -> dict:
            import yaml

            with Path(self._path).open(encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

    m = _Minimal(yaml_path)
    assert m.get_context() == "direct"
    logger.critical("[IMP:9][test] DomainsMixin direct instantiation: %s — OK", m.get_context())
