"""Unit-тесты T1.1 DevPlan 010: placement-авторитетный резолв в deploy_orchestrator._parse_modules."""
# GREP_SUMMARY: test_deploy_placement_resolve parse_modules placement authoritative resolve drift warning single-node noop
# STRUCTURE: ▶ tmp tree ┌node.yaml+placement.yaml┐ → ◇ _parse_modules → ⊕ enabled==resolved | legacy → ∑ drift-WARNING → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 010 T1.1/T1.2: при наличии placement.yaml _parse_modules берёт enabled/all
##           из resolve_node_modules (placement авторитетен); без файла — легаси путь;
##           drift node.yaml↔placement даёт WARNING с repair-подсказкой, не ошибку
## @scope    core/internal/bootstrap/deploy/deploy_orchestrator.py::_parse_modules
## @invariants
##   - Single-node (нет placement.yaml) — байт-идентичное легаси поведение
##   - Multi-node: enabled == resolve_node_modules(placement, node), фильтр --modules
##     применяется К РЕЗОЛЬВУ, оверлеи остаются из node.yaml
##   - Drift = logger.warning [IMP:8] c repair-подсказкой; деплой не блокируется
## @rationale Test honesty R1/R2: содержательные assert'ы на резолв-множества и caplog;
##           LDD-траектория печатаается перед assert'ами.
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy.deploy_orchestrator import _parse_modules
from core.internal.shared.placement import load_placement, resolve_node_modules

NODE_YAML_MULTI = """\
node:
  name: data-1
  host: 10.8.0.11
contexts:
  - name: ctx-lab
modules:
  postgres: {enabled: true}
  hermes-agent: {enabled: true}
"""

NODE_YAML_SINGLE = """\
node:
  name: solo-1
  host: 10.8.0.99
contexts:
  - name: ctx-solo
modules:
  postgres: {enabled: true}
  redis: {enabled: false}
"""

PLACEMENT_YAML = """\
context: ctx-lab
vpn_enforced: true
nodes:
  - name: data-1
    host: 10.8.0.11
modules:
  postgres: {node: data-1}
  hermes-agent: {mode: "off"}
"""


def _make_tree(tmp_path: Path, *, with_placement: bool, node_yaml: str) -> str:
    """Создать tmp node-configs дерево: <root>/<node>/node.yaml [+ <root>/<ctx>/placement.yaml]."""
    node_dir = tmp_path / "data-1"
    node_dir.mkdir()
    node_file = node_dir / "node.yaml"
    node_file.write_text(node_yaml, encoding="utf-8")
    if with_placement:
        ctx_dir = tmp_path / "ctx-lab"
        ctx_dir.mkdir()
        (ctx_dir / "placement.yaml").write_text(PLACEMENT_YAML, encoding="utf-8")
    return str(node_file)


# region FUNC_test_multi_node_resolves_from_placement
def test_multi_node_resolves_from_placement(tmp_path: Path) -> None:
    """Placement есть → enabled/all из resolve_node_modules, а не из node.yaml."""
    node_yaml = _make_tree(tmp_path, with_placement=True, node_yaml=NODE_YAML_MULTI)
    placement = load_placement(tmp_path / "ctx-lab" / "placement.yaml")
    assert placement is not None
    expected = sorted(resolve_node_modules(placement, "data-1"))

    lists = _parse_modules(node_yaml, "", "")

    print("--- LDD TRAJECTORY ---")
    print(f"resolved={expected} enabled={lists.enabled_names} all={lists.all_names}")
    print("--- END LDD TRAJECTORY ---")
    # [IMP:9] бизнес-инвариант: placement авторитетен — postgres размещён, hermes-agent off
    assert sorted(lists.enabled_names) == ["postgres"]
    assert sorted(lists.all_names) == ["postgres"]
    assert "hermes-agent" not in lists.all_names
    assert expected == ["postgres"]


# endregion FUNC_test_multi_node_resolves_from_placement


# region FUNC_test_single_node_noop_legacy
def test_single_node_noop_legacy(tmp_path: Path) -> None:
    """Нет placement.yaml → легаси путь: enabled строго из node.yaml."""
    node_yaml = _make_tree(tmp_path, with_placement=False, node_yaml=NODE_YAML_SINGLE)

    lists = _parse_modules(node_yaml, "", "")

    # [IMP:9] легаси no-op: enabled==true только postgres
    assert lists.enabled_names == ["postgres"]
    assert sorted(lists.all_names) == ["postgres", "redis"]


# endregion FUNC_test_single_node_noop_legacy


# region FUNC_test_filter_applies_to_resolution
def test_filter_applies_to_resolution(tmp_path: Path) -> None:
    """--modules фильтр пересекается с РЕЗОЛЬВОМ placement (не с node.yaml)."""
    node_yaml = _make_tree(tmp_path, with_placement=True, node_yaml=NODE_YAML_MULTI)

    lists = _parse_modules(node_yaml, "", "postgres")

    assert lists.enabled_names == ["postgres"]


# endregion FUNC_test_filter_applies_to_resolution


# region FUNC_test_drift_is_warning_not_error
def test_drift_is_warning_not_error(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T1.2: модуль enabled в node.yaml но off в placement → WARNING, деплой не падает."""
    node_yaml = _make_tree(tmp_path, with_placement=True, node_yaml=NODE_YAML_MULTI)

    with caplog.at_level(logging.WARNING):
        lists = _parse_modules(node_yaml, "", "")

    warnings = [r.getMessage() for r in caplog.records if "[_parse_modules][drift]" in r.getMessage()]
    print("--- LDD TRAJECTORY (drift warnings) ---")
    print("\n".join(warnings))
    print("--- END LDD TRAJECTORY ---")
    # [IMP:9] drift зафиксирован с repair-подсказкой; резолв остался авторитетным
    assert warnings, "ожидался drift-WARNING для hermes-agent (off в placement)"
    assert any("placement" in w.lower() for w in warnings), "нет repair-подсказки в WARNING"
    assert lists.enabled_names == ["postgres"], "drift не должен менять авторитетный резолв"


# endregion FUNC_test_drift_is_warning_not_error
