# GREP_SUMMARY: test project_remover remove lifecycle unregister node-yaml compose-down vhost-safe report idempotent no-data-deletion duplicates
# STRUCTURE: ┌fixture node_yaml┐ → ○ 8 tests → ⊕ LDD trajectory (IMP:9) → ⚡ anti-loop counter
# region MODULE_CONTRACT
## @purpose  Unit-тесты project_remover.py: поиск проекта, unregister из node.yaml,
##           удаление vhost, SSH compose down (mocked), print_report, идемпотентность,
##           дубликаты (TRAP node_yaml.py:1186), инвариант NO -v. LDD IMP:9 + Anti-Loop + R1-R5.
## @scope    Tests under tests/ (unit, no Docker). DI over Mocks для SSH.
## @invariants  Все тесты используют tmp_path (R1). SSH runner injectable (DI).
##   R1-R5 compliance. R5: негативный тест для TRAP node_yaml.py:1186.
## @rationale AC4: 6 unit-тестов на project_remover.py согласно DevPlan 092 §4.
## @changes 2026-07-31 · DevPlan 092 AC4 — initial implementation
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

from core.internal.scaffold.project_remover import (
    find_project_in_node_yaml,
    print_report,
    remove_vhost,
    ssh_compose_down,
    unregister_from_node_yaml,
)


def _write_remover_fixture(base_dir: pathlib.Path, projects: list[dict]) -> pathlib.Path:
    node_config_dir = base_dir / "test-context" / "node-configs" / "tronyx-vps"
    node_config_dir.mkdir(parents=True, exist_ok=True)
    node_yaml = node_config_dir / "node.yaml"
    data: dict = {"node": {"name": "tronyx-vps", "host": "10.0.0.1"}, "projects": projects}
    with open(node_yaml, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    logger.info("[IMP:8][fixture][remover] Created %s with %d projects", node_yaml, len(projects))
    return node_yaml


@pytest.fixture
def remover_node_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    return _write_remover_fixture(
        tmp_path,
        [
            {"name": "myapp", "domain": "myapp.tronyx.ru", "type": "frontend", "repo": "test-org/myapp"},
            {"name": "myapp2", "domain": "myapp2.tronyx.ru", "type": "backend", "repo": "test-org/myapp2"},
        ],
    )


@pytest.fixture
def remover_duplicate_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    return _write_remover_fixture(
        tmp_path,
        [
            {"name": "dup-app", "domain": "dup.tronyx.ru", "type": "frontend", "repo": "org/dup-app"},
            {"name": "other-app", "domain": "other.tronyx.ru", "type": "backend", "repo": "org/other-app"},
            {"name": "dup-app", "domain": "dup-alt.tronyx.ru", "type": "frontend", "repo": "org/dup-app-v2"},
        ],
    )


@ldd_trajectory
def test_remove_existing_project(remover_node_yaml: pathlib.Path, caplog) -> None:
    projects_root = remover_node_yaml.parent.parent.parent.parent
    logger.info("[IMP:9][test][remover] test_remove_existing_project — find 'myapp'")
    info = find_project_in_node_yaml(name="myapp", projects_root=projects_root)
    assert info, "Expected to find project 'myapp'"
    assert info["project_entry"]["name"] == "myapp"
    assert info["host"] == "10.0.0.1"
    logger.info("[IMP:9][test][remover] Unregistering 'myapp'")
    removed = unregister_from_node_yaml(node_yaml_path=info["node_yaml"], name="myapp")
    assert removed, "Expected unregister to return True"
    from core.internal.shared.node_yaml import NodeYaml

    node = NodeYaml(info["node_yaml"])
    remaining = node.get_project("myapp")
    assert remaining is None, "Project should be None after unregister"


@ldd_trajectory
def test_remove_missing_idempotent(remover_node_yaml: pathlib.Path, caplog) -> None:
    from core.internal.shared.node_yaml import NodeYaml

    node = NodeYaml(str(remover_node_yaml))
    initial_count = len(node.get_projects())
    logger.info("[IMP:9][test][remover] test_remove_missing_idempotent — remove 'nonexistent'")
    removed = unregister_from_node_yaml(node_yaml_path=str(remover_node_yaml), name="nonexistent")
    assert not removed, "Expected unregister to return False for non-existent project"
    node2 = NodeYaml(str(remover_node_yaml))
    assert len(node2.get_projects()) == initial_count, (
        f"Projects count changed: {initial_count} -> {len(node2.get_projects())}"
    )


@ldd_trajectory
def test_unregister_removes_all_duplicates(remover_duplicate_fixture: pathlib.Path, caplog) -> None:
    from core.internal.shared.node_yaml import NodeYaml

    node = NodeYaml(str(remover_duplicate_fixture))
    initial = node.get_projects()
    dup_count = sum(1 for p in initial if p.get("name") == "dup-app")
    assert dup_count == 2, f"Expected 2 'dup-app' entries, found {dup_count}"
    logger.info("[IMP:9][test][remover] test_unregister_removes_all_duplicates — removing 'dup-app'")
    removed = unregister_from_node_yaml(node_yaml_path=str(remover_duplicate_fixture), name="dup-app")
    assert removed, "Expected remove to succeed"
    node2 = NodeYaml(str(remover_duplicate_fixture))
    remaining = node2.get_projects()
    assert len(remaining) == 1, f"Expected 1 project remaining, got {len(remaining)}"
    assert remaining[0]["name"] == "other-app", f"Expected 'other-app', got {remaining[0]['name']}"


@ldd_trajectory
def test_remove_vhost_deletes_file(tmp_path: pathlib.Path, caplog) -> None:
    node_configs = tmp_path / "node-configs" / "tronyx-vps"
    overlays = node_configs / "overlays" / "nginx"
    overlays.mkdir(parents=True)
    vhost_file = overlays / "myapp.tronyx.ru.conf"
    vhost_file.write_text("# nginx vhost for myapp")
    logger.info("[IMP:9][test][remover] test_remove_vhost_deletes_file")
    result = remove_vhost(domain="myapp.tronyx.ru", node_configs_dir=str(node_configs))
    assert result, "Expected remove_vhost to return True"
    assert not vhost_file.exists(), f"Vhost file still exists: {vhost_file}"


@ldd_trajectory
def test_remove_vhost_no_domain_skips(caplog) -> None:
    logger.info("[IMP:9][test][remover] test_remove_vhost_no_domain_skips")
    result = remove_vhost(domain="", node_configs_dir="/nonexistent")
    assert result, "Expected remove_vhost to return True for empty domain (skip=success)"
    skip_logs = [r for r in caplog.records if "skipping" in r.message.lower() or "no domain" in r.message.lower()]
    assert len(skip_logs) >= 1, f"Expected skip log, got {len(skip_logs)}"


@ldd_trajectory
def test_compose_down_no_volumes_flag(caplog) -> None:
    captured_commands: list[str] = []

    def mock_ssh_runner(host: str, user: str, cmd: str, timeout: int = 120) -> tuple[int, str]:
        captured_commands.append(cmd)
        return 0, "Stopped"

    logger.info("[IMP:9][test][remover] test_compose_down_no_volumes_flag — checking for -v absence")
    success = ssh_compose_down(host="10.0.0.1", project="test-project", ssh_runner=mock_ssh_runner)
    assert success, "Expected ssh_compose_down to succeed with mock runner"
    compose_cmds = [c for c in captured_commands if "compose" in c and "down" in c]
    assert len(compose_cmds) >= 1, f"No compose down command captured: {captured_commands}"
    for cmd in compose_cmds:
        assert "-v" not in cmd, f"VIOLATION of O7/DD10: compose down command contains -v flag!\n  Command: {cmd}"
        logger.info("[IMP:9][test][remover] Verified NO -v in command: %s", cmd[:100])


@ldd_trajectory
def test_print_report_output(capsys, caplog) -> None:
    logger.info("[IMP:9][test][remover] test_print_report_output")
    print_report(name="myapp", vhost_removed=True, ssh_done=True)
    captured = capsys.readouterr()
    stdout_text = captured.out
    assert "remove-project: myapp" in stdout_text, "Missing project name in report"
    assert "Unregistered from node.yaml" in stdout_text, "Missing unregister message"
    assert "NOT deleted" in stdout_text, "Missing NOT deleted section"


@ldd_trajectory
def test_find_project_not_found(tmp_path: pathlib.Path, caplog) -> None:
    logger.info("[IMP:9][test][remover] test_find_project_not_found")
    result = find_project_in_node_yaml(name="nonexistent", projects_root=tmp_path)
    assert result == {}, f"Expected empty dict for missing project, got {result}"
