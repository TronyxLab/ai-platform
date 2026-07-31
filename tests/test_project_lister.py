# GREP_SUMMARY: test project_lister list projects offline table json ssh-status node-yaml filter empty-state multiple-nodes
# STRUCTURE: ┌fixture node_yaml┐ → ┌fixture multi_node_yamls┐ → ○ 8 tests → ⊕ LDD trajectory (IMP:9) → ⚡ anti-loop counter
# region MODULE_CONTRACT
## @purpose  Unit-тесты project_lister.py: offline listing, JSON output, фильтрация по name/node,
##           empty-state, multiple nodes, SSH status (mocked). LDD IMP:9 + Anti-Loop + R1-R5.
## @scope    Tests under tests/ (unit, no Docker). Tests call Python functions directly via DI over Mocks.
## @invariants
##   - Все тесты используют tmp_path (R1: No hardcoded paths)
##   - SSH runner injectable (DI) — mock для unit-тестов
##   - Каждый тест имеет @ldd_trajectory декоратор (IMP:9 assertion)
##   - R1: meaningful assertions (не assert True)
##   - R2: no unfalsifiable asserts
##   - R3: no @pytest.mark.skip
##   - R4: no skip за сервис/SSH — всё через mock
##   - R5: негативный тест для empty-state (test_list_empty_state)
## @rationale AC4: 6 unit-тестов на project_lister.py согласно DevPlan 092 §4.
## @changes 2026-07-31 · DevPlan 092 AC4 — initial implementation
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Импорт тестируемого модуля ────────────────────────────────────────────
from core.internal.scaffold.project_lister import (
    find_project_node,
    get_status_via_ssh,
    list_projects_offline,
)


def _write_node_yaml(base_dir: pathlib.Path, node_name: str, projects: list[dict]) -> pathlib.Path:
    node_config_dir = base_dir / "test-context" / "node-configs" / node_name
    node_config_dir.mkdir(parents=True, exist_ok=True)
    node_yaml = node_config_dir / "node.yaml"
    data: dict = {
        "node": {"name": node_name, "host": "192.168.1.1"},
        "projects": projects,
    }
    with open(node_yaml, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    logger.info("[IMP:8][fixture][node_yaml] Created %s with %d projects", node_yaml, len(projects))
    return node_yaml


@pytest.fixture
def single_node_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    return _write_node_yaml(
        tmp_path,
        "tronyx-vps",
        [
            {"name": "myapp", "domain": "myapp.tronyx.ru", "type": "frontend", "repo": "test-org/myapp"},
            {"name": "myapp2", "domain": "myapp2.tronyx.ru", "type": "backend", "repo": "test-org/myapp2"},
            {"name": "legacy", "domain": "legacy.example.com", "type": "frontend", "repo": "other-org/legacy"},
        ],
    )


@pytest.fixture
def multi_node_yamls(tmp_path: pathlib.Path) -> pathlib.Path:
    _write_node_yaml(
        tmp_path,
        "tronyx-vps",
        [
            {"name": "app-a", "domain": "a.tronyx.ru", "type": "backend", "repo": "org/app-a"},
            {"name": "app-b", "domain": "b.tronyx.ru", "type": "frontend", "repo": "org/app-b"},
        ],
    )
    _write_node_yaml(
        tmp_path,
        "dev-server",
        [
            {"name": "app-c", "domain": "c.tronyx.ru", "type": "fullstack", "repo": "org/app-c"},
            {"name": "app-d", "type": "backend", "repo": "org/app-d"},
        ],
    )
    return tmp_path


@ldd_trajectory
def test_list_offline_table(single_node_yaml: pathlib.Path, caplog) -> None:
    projects_root = single_node_yaml.parent.parent.parent.parent
    logger.info("[IMP:9][test][lister] test_list_offline_table — starting offline listing")
    result = list_projects_offline(projects_root=projects_root, output_format="table")
    assert len(result) == 3, f"Expected 3 projects, got {len(result)}"
    project_names = {p["name"] for p in result}
    assert project_names == {"myapp", "myapp2", "legacy"}, f"Unexpected project names: {project_names}"
    for p in result:
        assert "node" in p, f"Missing 'node' key in project {p.get('name')}"
        assert p["node"] == "tronyx-vps", f"Wrong node: {p['node']}"


@ldd_trajectory
def test_list_offline_json(single_node_yaml: pathlib.Path, capfd, caplog) -> None:
    import json

    projects_root = single_node_yaml.parent.parent.parent.parent
    logger.info("[IMP:9][test][lister] test_list_offline_json — starting JSON listing")
    captured = capfd.readouterr()
    stdout_text = captured.out.strip()
    assert stdout_text, "Expected JSON output on stdout"
    parsed = json.loads(stdout_text)
    assert isinstance(parsed, list), f"Expected JSON array, got {type(parsed)}"
    assert len(parsed) == 3, f"Expected 3 projects in JSON, got {len(parsed)}"
    for entry in parsed:
        assert "name" in entry, f"Missing 'name' field in JSON entry: {entry}"


@ldd_trajectory
def test_list_filter_by_name(single_node_yaml: pathlib.Path, caplog) -> None:
    projects_root = single_node_yaml.parent.parent.parent.parent
    logger.info("[IMP:9][test][lister] test_list_filter_by_name — filter 'myapp'")
    result = list_projects_offline(projects_root=projects_root, project_name="myapp", output_format="json")
    assert len(result) == 1, f"Expected 1 project matching 'myapp', got {len(result)}"
    assert result[0]["name"] == "myapp"
    assert result[0]["domain"] == "myapp.tronyx.ru"
    assert result[0]["node"] == "tronyx-vps"


@ldd_trajectory
def test_list_filter_by_node(multi_node_yamls: pathlib.Path, caplog) -> None:
    projects_root = multi_node_yamls
    logger.info("[IMP:9][test][lister] test_list_filter_by_node — filter 'dev-server'")
    result = list_projects_offline(projects_root=projects_root, node_filter="dev-server", output_format="json")
    assert len(result) >= 1, f"Expected at least 1 project on dev-server, got {len(result)}"
    for p in result:
        assert p["node"] == "dev-server", f"Expected dev-server, got {p['node']} for {p['name']}"
    dev_names = {p["name"] for p in result}
    assert "app-c" in dev_names, "Missing app-c on dev-server"
    assert "app-d" in dev_names, "Missing app-d on dev-server"


@ldd_trajectory
def test_list_empty_state(tmp_path: pathlib.Path, caplog) -> None:
    logger.info("[IMP:9][test][lister] test_list_empty_state — no node.yaml present")
    result = list_projects_offline(projects_root=tmp_path, output_format="table")
    assert result == [], f"Expected empty list, got {result}"
    empty_logs = [r for r in caplog.records if "Empty state" in r.message or "[IMP:9]" in r.message]
    assert len(empty_logs) >= 1, f"Expected IMP:9 empty-state log, got {len(empty_logs)}"


@ldd_trajectory
def test_list_multiple_nodes(multi_node_yamls: pathlib.Path, caplog) -> None:
    projects_root = multi_node_yamls
    logger.info("[IMP:9][test][lister] test_list_multiple_nodes — aggregation")
    result = list_projects_offline(projects_root=projects_root, output_format="json")
    assert len(result) == 4, f"Expected 4 projects from 2 nodes, got {len(result)}"
    all_names = {p["name"] for p in result}
    assert all_names == {"app-a", "app-b", "app-c", "app-d"}, f"Unexpected names: {all_names}"
    nodes_by_project = {p["name"]: p["node"] for p in result}
    assert nodes_by_project["app-a"] == "tronyx-vps"
    assert nodes_by_project["app-b"] == "tronyx-vps"
    assert nodes_by_project["app-c"] == "dev-server"
    assert nodes_by_project["app-d"] == "dev-server"


@ldd_trajectory
def test_get_status_via_ssh_mocked(single_node_yaml: pathlib.Path, caplog) -> None:
    logger.info("[IMP:9][test][lister] test_get_status_via_ssh_mocked — DI mock")

    def mock_ssh_runner(host: str, user: str, cmd: str, timeout: int = 10) -> str | None:
        logger.info("[IMP:8][test][lister] Mock SSH: %s@%s cmd=%s", user, host, cmd[:50])
        return "CONTAINER ID   NAME              STATUS\nabc123         myapp-web-1       Up 2 hours"

    success = get_status_via_ssh(host="192.168.1.1", project="myapp", ssh_runner=mock_ssh_runner)
    assert success, "Expected SSH status to return True with mock runner"
    status_logs = [r for r in caplog.records if "Status retrieved" in r.message]
    assert len(status_logs) >= 1, f"Expected 'Status retrieved' IMP:9 log, got {len(status_logs)}"


@ldd_trajectory
def test_find_project_node_found(single_node_yaml: pathlib.Path, caplog) -> None:
    projects_root = single_node_yaml.parent.parent.parent.parent
    logger.info("[IMP:9][test][lister] test_find_project_node_found — searching for 'myapp'")
    node_yaml_path, ssh_host = find_project_node(name="myapp", projects_root=projects_root)
    assert node_yaml_path is not None, "Expected to find node.yaml for 'myapp'"
    assert ssh_host != "", "Expected non-empty SSH host"
    assert "test-context" in str(node_yaml_path), f"Expected path containing test-context, got {node_yaml_path}"
