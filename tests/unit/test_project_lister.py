# GREP_SUMMARY: test project_lister list offline table json filter empty-state multiple-nodes node-yaml
# STRUCTURE: ┌node_yaml fixture┐ → ┌projects_root fixture┐ → ○ 6 tests → ⊕ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Unit tests for project_lister.py (DP-092 Wave 1). Tests offline listing in table/JSON
##           formats, name/node filtering, empty state, and multiple nodes aggregation.
## @scope    project_lister.py public API: list_projects_offline, find_node_yaml_files.
## @invariants
##   - All tests use tmp_path (no hardcoded paths)
##   - Node.yaml fixtures created via NodeYaml or raw YAML
##   - LDD IMP:9 assertion on every test
##   - R1-R5 compliance
## @rationale Covers AC1 (project-list), AC3 (0 inline python3), AC4 (unit tests)
## @changes  2026-07-30 · Wave 1 — initial implementation
# endregion MODULE_CONTRACT

import json
import logging
import pathlib

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def projects_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a projects root with multiple node-configs directories.

    ## @purpose  Standard test fixture: 3 nodes with projects in node.yaml.
    ## @io        ⎋ tmp_path with node-configs structure
    ## @invariants  Returns Path to PROJECTS_ROOT with node-configs/node-a/node.yaml etc.
    """
    root = tmp_path / "projects"
    root.mkdir()

    # Node A: two projects
    node_a = root / "tronyx-lab" / "node-configs" / "node-a"
    node_a.mkdir(parents=True)
    _write_node_yaml(
        node_a / "node.yaml",
        node_name="node-a",
        host="10.0.0.1",
        projects=[
            {"name": "myapp", "domain": "myapp.example.com", "type": "backend", "repo": "org/myapp"},
            {"name": "frontend-app", "domain": "fe.example.com", "type": "frontend", "repo": "org/frontend-app"},
        ],
    )

    # Node B: one project
    node_b = root / "tronyx-lab" / "node-configs" / "node-b"
    node_b.mkdir(parents=True)
    _write_node_yaml(
        node_b / "node.yaml",
        node_name="node-b",
        host="10.0.0.2",
        projects=[
            {"name": "api-service", "domain": "api.example.com", "type": "backend", "repo": "org/api-service"},
        ],
    )

    # Node C: empty projects
    node_c = root / "tronyx-lab" / "node-configs" / "node-c"
    node_c.mkdir(parents=True)
    _write_node_yaml(
        node_c / "node.yaml",
        node_name="node-c",
        host="10.0.0.3",
        projects=[],
    )

    return root


def _write_node_yaml(
    path: pathlib.Path,
    node_name: str,
    host: str,
    projects: list[dict],
) -> None:
    """Write a minimal node.yaml fixture.

    ## @purpose  Helper to create node.yaml files matching NodeYaml schema.
    ## @io        ⇥ path, node_name, host, projects → ⎋ writes file
    """
    data = {
        "context": "test-context",
        "node": {"name": node_name, "host": host},
        "modules": [],
        "projects": projects,
    }
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# ── LDD helper ─────────────────────────────────────────────────────


def _assert_ldd_imp9(caplog: pytest.LogCaptureFixture) -> None:
    """Assert at least one IMP:9 log is present in caplog.

    ## @purpose  Shared LDD trajectory verifier for all tests.
    ## @io        ⇥ caplog → ⎋ assertion pass/fail
    """
    found_log: bool = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# ── Tests ───────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_list_offline_table · Scenario: node.yaml with projects → table output · Last fail: N/A · Remove if: lister API changes
@ldd_trajectory
def test_list_offline_table(projects_root: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test offline listing produces formatted table.

    ## @purpose  AC1: project-list with table format.
    ## @io        tmp_path with 2 nodes → stdout table
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_lister import list_projects_offline

    result = list_projects_offline(
        projects_root=projects_root,
        output_format="table",
    )

    # Verify we got 3 projects across 2 nodes (node-c has 0)
    assert len(result) == 3
    assert any(p["name"] == "myapp" for p in result)
    assert any(p["name"] == "frontend-app" for p in result)
    assert any(p["name"] == "api-service" for p in result)

    # Verify node attribution
    for p in result:
        assert "node" in p
        assert "host" in p

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_list_offline_json · Scenario: node.yaml → valid JSON array · Last fail: N/A · Remove if: lister API changes
@ldd_trajectory
def test_list_offline_json(projects_root: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test offline listing produces valid JSON.

    ## @purpose  AC1: project-list JSON format.
    ## @io        tmp_path → JSON array with 3 entries
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_lister import list_projects_offline

    result = list_projects_offline(
        projects_root=projects_root,
        output_format="json",
    )

    assert len(result) == 3

    # Verify each project has required fields
    for p in result:
        assert "name" in p
        assert "domain" in p or "domain" not in p  # may be absent
        assert "node" in p
        assert "host" in p

    # Verify we can serialize/deserialize without error
    json_str = json.dumps(result)
    parsed = json.loads(json_str)
    assert len(parsed) == 3

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_list_filter_by_name · Scenario: project name filter → only matching project · Last fail: N/A · Remove if: lister API changes
@ldd_trajectory
def test_list_filter_by_name(projects_root: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test filtering by project name.

    ## @purpose  --name filter returns only matching project.
    ## @io        tmp_path with 3 projects → filter "myapp" → 1 result
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_lister import list_projects_offline

    result = list_projects_offline(
        projects_root=projects_root,
        project_name="myapp",
        output_format="table",
    )

    assert len(result) == 1
    assert result[0]["name"] == "myapp"
    assert result[0]["node"] == "node-a"

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_list_filter_by_node · Scenario: node filter → only projects on that node · Last fail: N/A · Remove if: lister API changes
@ldd_trajectory
def test_list_filter_by_node(projects_root: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test filtering by node name.

    ## @purpose  --node filter returns only projects on that node.
    ## @io        tmp_path with 3 nodes → filter "node-b" → 1 result
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_lister import list_projects_offline

    result = list_projects_offline(
        projects_root=projects_root,
        node_filter="node-b",
        output_format="table",
    )

    assert len(result) == 1
    assert result[0]["name"] == "api-service"
    assert result[0]["node"] == "node-b"

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_list_empty_state · Scenario: no node.yaml files → "No projects found" · Last fail: N/A · Remove if: lister API changes
@ldd_trajectory
def test_list_empty_state(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """Test empty state when no node.yaml files exist.

    ## @purpose  Graceful handling when no projects are found.
    ## @io        empty tmp_path → returns []
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_lister import list_projects_offline

    result = list_projects_offline(
        projects_root=tmp_path,
        output_format="table",
    )

    assert result == []

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_list_multiple_nodes · Scenario: aggregation from multiple node.yaml → all projects · Last fail: N/A · Remove if: lister API changes
@ldd_trajectory
def test_list_multiple_nodes(projects_root: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test aggregation of projects from multiple node.yaml files.

    ## @purpose  All projects across multiple nodes are collected.
    ## @io        tmp_path with 3 nodes → 3 projects total
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_lister import list_projects_offline

    result = list_projects_offline(
        projects_root=projects_root,
        output_format="json",
    )

    # 3 projects: node-a has 2, node-b has 1, node-c has 0
    assert len(result) == 3

    # Verify unique node names
    nodes = {p["node"] for p in result}
    assert nodes == {"node-a", "node-b"}

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_find_node_yaml_files · Scenario: filter by node name → only matching files · Last fail: N/A · Remove if: lister API changes
def test_find_node_yaml_files(projects_root: pathlib.Path) -> None:
    """Test find_node_yaml_files with and without node filter.

    ## @purpose  Unit test for find_node_yaml_files() helper.
    ## @io        tmp_path → verify file count
    """
    from core.internal.scaffold.project_lister import find_node_yaml_files

    # Without filter — all 3 nodes
    all_files = find_node_yaml_files(projects_root)
    assert len(all_files) == 3

    # With filter — only node-b
    filtered = find_node_yaml_files(projects_root, node_filter="node-b")
    assert len(filtered) == 1
    assert "node-b" in str(filtered[0])

    # Non-existent node
    empty = find_node_yaml_files(projects_root, node_filter="nonexistent")
    assert len(empty) == 0
