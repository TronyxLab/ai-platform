# GREP_SUMMARY: test project_lister list projects offline table json ssh-status node-yaml filter empty-state multiple-nodes
# STRUCTURE: ┌fixture node_yaml┐ → ┌fixture multi_node_yamls┐ → ○ 9 tests → ⊕ LDD trajectory (IMP:9) → ⚡ anti-loop counter
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
## ⚠️ TRAP[DECISION] · 2026-07-31 · MED · Дедупликация: unit-версия тестов удалена (import file mismatch)
## · Rejected: оставить tests/unit/test_project_lister.py (риск: pytest import file mismatch —
##   одинаковый basename с tests/test_project_lister.py ломает collection всего сьюта)
## · Reason: корневая версия каноническая; уникальный сценарий
##   test_find_node_yaml_files (фильтр по ноде + nonexistent) перенесён сюда.
## · Rev: если unit-директория вернётся к полному покрытию — ресинхронизировать inventory.
## @changes 2026-07-31 · DevPlan 092 AC4 — initial implementation
## @changes 2026-07-31 · Dedup fix — test_find_node_yaml_files перенесён из tests/unit/
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
    find_node_yaml_files,
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
    with pathlib.Path(node_yaml).open("w", encoding="utf-8") as f:
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
            {"name": "oldapp", "domain": "old.example.com", "type": "frontend", "repo": "other-org/oldapp"},
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
            {"name": "app-c", "domain": "c.tronyx.ru", "type": "backend", "repo": "org/app-c"},
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
    assert project_names == {"myapp", "myapp2", "oldapp"}, f"Unexpected project names: {project_names}"
    for p in result:
        assert "node" in p, f"Missing 'node' key in project {p.get('name')}"
        assert p["node"] == "tronyx-vps", f"Wrong node: {p['node']}"


@ldd_trajectory
def test_list_offline_json(single_node_yaml: pathlib.Path, capfd, caplog) -> None:
    import json

    # ⚠️ TRAP[BUG] · 2026-07-31 · P2 · Lost list_projects_offline() call — capfd captured empty stdout
    # · Symptom: test_list_offline_json always failed ("Expected JSON output on stdout", assert '')
    #   while ruff flagged F841 (projects_root unused) — the call + print were lost in an earlier refactor
    # · Root: refactor deleted the invocation; capfd.readouterr() ran before any output existed
    # · Fix: restored canonical invocation (mirror of test_list_offline_table) + json.dumps print
    # · Prevention: capfd-based tests must call the target before readouterr()
    projects_root = single_node_yaml.parent.parent.parent.parent
    logger.info("[IMP:9][test][lister] test_list_offline_json — starting JSON listing")
    list_projects_offline(projects_root=projects_root, output_format="json")  # prints JSON to stdout (L148)
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
def test_find_node_yaml_files(multi_node_yamls: pathlib.Path, caplog) -> None:
    """find_node_yaml_files() helper: all files, node filter, nonexistent node.

    # 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_find_node_yaml_files · Scenario: filter by node name → only matching files · Last fail: N/A · Remove if: lister API changes
    ## @purpose — Unit coverage for the node.yaml discovery helper: unfiltered count,
    ##            node_filter narrowing, and empty result for a nonexistent node.
    ##            Persisted from tests/unit/ during dedup (import file mismatch fix).
    ## @io — ⇥ multi_node_yamls, caplog → ⎋ None (asserts)
    ## @complexity — O(N) — glob over node-configs
    """
    logger.info("[IMP:9][test][lister] test_find_node_yaml_files — helper coverage")
    all_files = find_node_yaml_files(multi_node_yamls)
    assert len(all_files) == 2, f"Expected 2 node.yaml files, got {len(all_files)}"

    filtered = find_node_yaml_files(multi_node_yamls, node_filter="dev-server")
    assert len(filtered) == 1, f"Expected 1 file for dev-server, got {len(filtered)}"
    assert "dev-server" in str(filtered[0])

    empty = find_node_yaml_files(multi_node_yamls, node_filter="nonexistent")
    assert len(empty) == 0, f"Expected 0 files for nonexistent node, got {len(empty)}"


@ldd_trajectory
def test_find_project_node_found(single_node_yaml: pathlib.Path, caplog) -> None:
    projects_root = single_node_yaml.parent.parent.parent.parent
    logger.info("[IMP:9][test][lister] test_find_project_node_found — searching for 'myapp'")
    node_yaml_path, ssh_host = find_project_node(name="myapp", projects_root=projects_root)
    assert node_yaml_path is not None, "Expected to find node.yaml for 'myapp'"
    assert ssh_host, "Expected non-empty SSH host"
    assert "test-context" in str(node_yaml_path), f"Expected path containing test-context, got {node_yaml_path}"


# 🧪 TRAP[TEST] · 2026-08-27 · F-11 (P2) · scan-root NODE_CONFIGS_DIR-layout → ≥1 node.yaml
# · Regression: F-11 — `make project-list` на dev давал «Found 0 node.yaml file(s)»:
# ·   scan-root резолвился в repo-root, glob `*/node-configs/*/node.yaml` кодировал
# ·   НЕ-каноничный layout `<context>/node-configs/`, а канонический dev-layout —
# ·   `node-configs/<node>/node.yaml` прямо в корне репо (NODE_CONFIGS_DIR из .env).
# · Last fail: session 014 — make project-list → «Found 0 node.yaml file(s)» (B5)
# · Remove if: find_node_yaml_files/_resolve_scan_root логика резолва scan-root меняется
@ldd_trajectory
def test_find_node_yaml_files_node_configs_dir(tmp_path: pathlib.Path, caplog, monkeypatch) -> None:
    """F-11: scan-root NODE_CONFIGS_DIR-layout → ≥1 node.yaml (dev/bare-NODE канон)."""
    from core.internal.scaffold.project_lister import _resolve_scan_root

    # Канонический dev-layout: node-configs/<node>/node.yaml (NODE_CONFIGS_DIR из .env)
    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "tronyx-vps").mkdir(parents=True)
    (node_configs_dir / "tronyx-vps" / "node.yaml").write_text("domain: tronyx.ru\n", encoding="utf-8")

    # 1. find_node_yaml_files с scan-root = node-configs → находит dev-layout
    files = find_node_yaml_files(node_configs_dir)
    logger.info("[IMP:8][test][lister] F-11: find_node_yaml_files(%s) → %d file(s)", node_configs_dir, len(files))
    assert len(files) >= 1, "F-11: dev-layout node-configs/<node>/node.yaml обязан находиться"
    assert files[0].parent.name == "tronyx-vps", f"Unexpected node dir: {files[0]}"

    # 2. Backward-compat: multi-context layout всё ещё находится (без регрессии)
    (tmp_path / "ctx-a" / "node-configs" / "dev-server").mkdir(parents=True)
    (tmp_path / "ctx-a" / "node-configs" / "dev-server" / "node.yaml").write_text(
        "domain: dev.example.com\n", encoding="utf-8"
    )
    compat = find_node_yaml_files(tmp_path)
    assert len(compat) >= 1, "F-11: backward-compat `*/node-configs/*/node.yaml` не должен регрессировать"
    assert any("dev-server" in str(f) for f in compat), f"Expected dev-server node.yaml in {compat}"

    # 3. _resolve_scan_root: NODE_CONFIGS_DIR env побеждает (F-11 цепочка)
    monkeypatch.setenv("NODE_CONFIGS_DIR", str(node_configs_dir))
    resolved = _resolve_scan_root(tmp_path)
    assert resolved == node_configs_dir, f"F-11: NODE_CONFIGS_DIR должен резолвиться в scan-root, got {resolved}"
    monkeypatch.delenv("NODE_CONFIGS_DIR")

    # 4. _resolve_scan_root: <base>/node-configs существует → этот каталог (dev-корень репо)
    resolved_repo = _resolve_scan_root(tmp_path)
    assert resolved_repo == node_configs_dir, f"F-11: <repo>/node-configs должен резолвиться, got {resolved_repo}"

    # 5. _resolve_scan_root: fallback на base_root (PROJECTS_BASE-режим)
    bare = tmp_path / "bare"
    bare.mkdir()
    resolved_bare = _resolve_scan_root(bare)
    assert resolved_bare == bare, "F-11: fallback base_root (PROJECTS_BASE-режим)"

    logger.critical("[IMP:9][test][lister] F-11: scan-root NODE_CONFIGS_DIR-layout найден (≥1 node.yaml)")
