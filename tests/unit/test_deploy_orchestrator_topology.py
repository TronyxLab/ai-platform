# GREP_SUMMARY: deploy-orchestrator-topology validate-topology projects-scan exposed-fqdn production-wiring DevPlan-16-T2A node-yaml-projects fail-fast
# STRUCTURE: ▶ tmp node-configs tree (node.yaml#projects) → ◇ _placement_for_node(modules_dir) → ◇ validate_topology kwargs → ⊕ scanner агрегирует проекты всех нод → ⎋ non-None DI
# region MODULE_CONTRACT
## @purpose  Wiring-тесты топо-валидации деплоя (DevPlan 16 T2.A / P1-1): прод-вызов
##           validate_topology в _placement_for_node получает projects_scan (DI-callable),
##           агрегирующий node.yaml#projects контекста — exposed target_node/FQDN-инварианты
##           проверяются деплоем, а не только тестами.
## @scope    tests/unit: _placement_for_node с monkeypatched validate_topology (запись kwargs);
##           без subprocess/docker.
## @invariants
##   - scanner агрегирует проекты ВСЕХ */node.yaml контекстного корня
##   - нечитаемый node.yaml внутри скана → ConfigValidationError (fail-fast)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy import deploy_orchestrator
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

_LOCK = (
    "version: 1\nlevel: auto\nstate: baseline\nlanguage: python\n"
    "generator_hash: sha256:test\nmaturity:\n  age_days: 1\n"
)


def _make_context_tree(tmp_path: Path) -> Path:
    """node-configs корень: нода data-1 c projects[] + placement.yaml (T2.A)."""
    nc_root = tmp_path / "node-configs"
    node_dir = nc_root / "data-1"
    node_dir.mkdir(parents=True)
    (node_dir / "node.yaml").write_text(
        "node:\n  name: data-1\n"
        "contexts: [{name: t2a}]\n"
        "projects:\n"
        "  - name: web-app\n    domain: web-app.example.com\n    expose: true\n    target_node: data-1\n",
        encoding="utf-8",
    )
    (nc_root / "t2a" / "placement.yaml").parent.mkdir(parents=True)
    (nc_root / "t2a" / "placement.yaml").write_text(
        "context: t2a\nvpn_enforced: true\n"
        "nodes: [{name: data-1, host: 10.8.0.11}]\n"
        "modules:\n  postgres: {node: data-1}\n  log-collector: {mode: all-nodes}\n",
        encoding="utf-8",
    )
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    return nc_root


# 🧪 TRAP[TEST] · SCENARIO · DevPlan 16 T2.A P1-1 · прод-deploy передаёт projects_scan
# · Last fail: аудит 15 P1-1 — прод-вызов validate_topology без projects_scan:
#   exposed target_node/FQDN-инварианты не проверялись нигде кроме тестов
# · Scenario: placement + modules_dir → _placement_for_node вызывает validate_topology с
#   non-None projects_scan; сканер агрегирует node.yaml#projects (имя/FQDN/target_node)
# · Remove if: exposed-валидация переезжает в отдельный verb
def test_validate_topology_receives_projects_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nc_root = _make_context_tree(tmp_path)
    node_yaml = nc_root / "data-1" / "node.yaml"
    captured: dict[str, object] = {}

    def _fake_validate_topology(placement, *, modules_dir, node_configs_dir, projects_scan=None):
        captured["projects_scan"] = projects_scan
        captured["modules_dir"] = modules_dir

    monkeypatch.setattr(deploy_orchestrator, "validate_topology", _fake_validate_topology)

    placement, node_name = deploy_orchestrator._placement_for_node(
        str(node_yaml), modules_dir=str(tmp_path / "modules")
    )

    assert node_name == "data-1"
    assert placement is not None and placement.context == "t2a"
    scanner = captured.get("projects_scan")
    assert callable(scanner), f"validate_topology обязан получить projects_scan (T2.A): {captured}"
    projects = scanner()
    assert any(
        isinstance(p, dict) and p.get("name") == "web-app" and p.get("target_node") == "data-1" for p in projects
    ), f"сканер обязан агрегировать node.yaml#projects: {projects}"

    logger.info("[IMP:9][test][assert] прод-wiring: validate_topology(projects_scan=...) ✓")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T2.A · нечитаемый node.yaml в скане → fail-fast
# · Scenario: битый YAML соседней ноды → ConfigValidationError при вызове сканера
#   (молчаливый partial-скан скрыл бы чужие exposed-проекты)
# · Remove if: скан переезжает в shared-слой с иным контрактом ошибок
def test_projects_scan_fail_fast_on_unreadable_node_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nc_root = _make_context_tree(tmp_path)
    broken = nc_root / "broken-1"
    broken.mkdir()
    (broken / "node.yaml").write_text("node: [unclosed", encoding="utf-8")

    captured: dict[str, object] = {}

    def _fake_validate_topology(placement, *, modules_dir, node_configs_dir, projects_scan=None):
        captured["projects_scan"] = projects_scan

    monkeypatch.setattr(deploy_orchestrator, "validate_topology", _fake_validate_topology)

    deploy_orchestrator._placement_for_node(
        str(nc_root / "data-1" / "node.yaml"), modules_dir=str(tmp_path / "modules")
    )
    scanner = captured["projects_scan"]
    with pytest.raises(ConfigValidationError, match="unreadable"):
        scanner()

    logger.info("[IMP:9][test][negative] scan fail-fast на битом node.yaml ✓")
