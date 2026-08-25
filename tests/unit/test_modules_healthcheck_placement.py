"""Unit-тесты DR-H4 fix: placement-awareness modules_healthcheck (DevPlan 010 follow-up).

# GREP_SUMMARY: test_modules_healthcheck_placement placement-awareness enabled-filter resolve-node-modules multi-node healthcheck dr-h4
# STRUCTURE: ▶ tmp node.yaml + placement.yaml → ◇ _resolve_enabled_modules → ⊕ node.yaml ∩ placed → ∑ counts → ⎋
# region MODULE_CONTRACT
## @purpose  DR-H4 fix: _resolve_enabled_modules пересекает node.yaml-фильтр с размещением ноды
##           (раньше читался только node.yaml → multi-node healthcheck проверял чужие модули).
## @scope    core/internal/healthcheck/modules_healthcheck.py::_resolve_enabled_modules (env-driven)
## @invariants
##   - Single-node (нет placement.yaml) → фильтр = node.yaml enabled (байт-совместимость)
##   - Multi-node: enabled ∩ resolve_node_modules(placement, NODE)
##   - Нода вне placement / нечитаемый placement → IMP:7 warning, fallback на node.yaml
##   - Нет NODE_YAML/NODE_NAME env и дефолтного пути → None (фильтр выключен)
## @rationale R1-R5: содержательные assert'ы на множества; caplog IMP:9 trajectory.
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.internal.healthcheck.modules_healthcheck import _resolve_enabled_modules

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_NODE_YAML_DATA1 = """\
node:
  name: data-1
  host: 10.8.0.11
contexts:
  - name: ctx-lab
modules:
  - name: postgres
    enabled: true
  - name: redis
    enabled: true
  - name: nginx
    enabled: true
"""

_PLACEMENT_YAML = """\
context: ctx-lab
vpn_enforced: true
nodes:
  - name: data-1
    host: 10.8.0.11
  - name: apps-1
    host: 10.8.0.13
modules:
  postgres: {node: data-1}
  redis: {mode: "off"}
  nginx: {node: apps-1}
  hermes-agent: {node: apps-1}
"""


def _make_tree(tmp_path: Path, *, with_placement: bool = True, node_yaml: str = _NODE_YAML_DATA1) -> Path:
    """tmp node-configs дерево: <root>/data-1/node.yaml [+ <root>/ctx-lab/placement.yaml]."""
    node_file = tmp_path / "data-1" / "node.yaml"
    node_file.parent.mkdir()
    node_file.write_text(node_yaml, encoding="utf-8")
    if with_placement:
        ctx_dir = tmp_path / "ctx-lab"
        ctx_dir.mkdir(exist_ok=True)
        (ctx_dir / "placement.yaml").write_text(_PLACEMENT_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NODE_YAML", raising=False)
    monkeypatch.delenv("NODE_NAME", raising=False)


# region TEST_single_node_byte_compat


def test_no_placement_node_yaml_filter_only(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-node: нет placement.yaml → фильтр строго из node.yaml (байт-совместимость)."""
    root = _make_tree(tmp_path, with_placement=False)
    monkeypatch.setenv("NODE_YAML", str(root / "data-1" / "node.yaml"))

    with caplog.at_level(logging.INFO):
        result = _resolve_enabled_modules()

    # [IMP:9] инвариант легаси-пути: nginx остаётся в фильтре, хотя в S3-подобном placement он был бы чужой
    assert result == {"postgres", "redis", "nginx"}, f"легаси-фильтр изменён: {result}"
    assert any("[IMP:8][modules-healthcheck][enabled] node.yaml filter" in r.getMessage() for r in caplog.records)


# endregion TEST_single_node_byte_compat


# region TEST_placement_aware_intersection


def test_placement_intersects_enabled(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DR-H4 fix: enabled ∩ placed(data-1) — nginx (чужой модуль) исключён, redis off исключён."""
    root = _make_tree(tmp_path, with_placement=True)
    monkeypatch.setenv("NODE_YAML", str(root / "data-1" / "node.yaml"))

    with caplog.at_level(logging.INFO):
        result = _resolve_enabled_modules()

    print("--- LDD TRAJECTORY ---")
    for record in caplog.records:
        if "[modules-healthcheck][enabled]" in record.getMessage():
            print(record.getMessage())
    print("--- END LDD TRAJECTORY ---")

    # [IMP:9] бизнес-инвариант: проверяются ТОЛЬКО модули, размещённые на этой ноде
    assert result == {"postgres"}, f"placement-awareness не применился: {result}"
    assert any("placement-aware filter" in r.getMessage() for r in caplog.records), (
        "нет IMP:9 лога placement-aware фильтра"
    )


# endregion TEST_placement_aware_intersection


# region TEST_fail_open_paths


def test_unknown_node_in_placement_falls_back(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нода вне placement → IMP:7 warning с repair-подсказкой, fallback на node.yaml."""
    root = _make_tree(
        tmp_path,
        with_placement=True,
        node_yaml=_NODE_YAML_DATA1.replace("name: data-1", "name: ghost-9", 1),
    )
    # ghost-9 директория/ноды нет в placement (там data-1/apps-1)
    monkeypatch.setenv("NODE_YAML", str(root / "ghost-9" / "node.yaml"))
    # переименовываем директорию чтобы путь совпал с именем ноды
    (root / "data-1").rename(root / "ghost-9")

    with caplog.at_level(logging.WARNING):
        result = _resolve_enabled_modules()

    assert result == {"postgres", "redis", "nginx"}, f"fallback должен вернуть node.yaml-фильтр: {result}"
    assert any("not in placement" in r.getMessage() for r in caplog.records), "нет warning о ноде вне placement"


def test_unreadable_placement_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Нечитаемый placement.yaml → fallback на node.yaml-фильтр без raise."""
    root = _make_tree(tmp_path, with_placement=True)
    (root / "ctx-lab" / "placement.yaml").write_text(":::: broken [\n", encoding="utf-8")
    monkeypatch.setenv("NODE_YAML", str(root / "data-1" / "node.yaml"))

    result = _resolve_enabled_modules()

    assert result == {"postgres", "redis", "nginx"}, f"fallback сломан: {result}"


def test_no_env_and_no_default_path_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет NODE_YAML/NODE_NAME и дефолтного пути → None (фильтр выключен, прежнее поведение)."""
    assert _resolve_enabled_modules() is None


# endregion TEST_fail_open_paths
