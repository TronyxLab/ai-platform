#!/usr/bin/env python3
# GREP_SUMMARY: test-project-registry-llm discover-llm-projects ai-platform.yaml llm.enabled filter node-yaml projects_root
# STRUCTURE: ▶ test_llm_enabled_only → test_llm_disabled_skipped → test_missing_ai_yaml_skipped → test_node_yaml_missing_empty → test_org_repo_resolution
# region MODULE_CONTRACT
## @purpose  Unit tests for project_registry.discover_llm_projects — реальная детекция LLM-проектов
##           по ai-platform.yaml llm.enabled=true (DevPlan 117 D24, замена хардкод-шима key_provisioner).
## @scope    Tests: фильтрация по llm.enabled, org-резолвинг из repo, graceful degradation.
## @invariants
##   - tmp_path для node.yaml и projects (Zero Hardcode Rule)
##   - llm.enabled=false / отсутствие ai-platform.yaml / отсутствие node.yaml → skip/empty
##   - Возвращает только {"name", "llm"} — формат key_provisioner consumers
##   - LDD: IMP:9 в успешных сценариях
## @changes 2026-08-01 | DevPlan 117 D24 — создан
# endregion MODULE_CONTRACT

import logging

import pytest
import yaml

from core.internal.shared.project_registry import discover_llm_projects

logger = logging.getLogger(__name__)


def _write_node_yaml(tmp_path, projects: list[dict]) -> str:
    """Write node.yaml with given projects list."""
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text(yaml.safe_dump({"node": {"name": "test-node"}, "projects": projects}))
    return str(node_yaml)


def _write_ai_yaml(project_dir, llm_enabled: bool, extra: dict | None = None) -> None:
    """Write ai-platform.yaml with llm config."""
    project_dir.mkdir(parents=True, exist_ok=True)
    data = {"name": project_dir.name, "llm": {"enabled": llm_enabled}}
    if extra:
        data["llm"].update(extra)
    (project_dir / "ai-platform.yaml").write_text(yaml.safe_dump(data))


# region TEST_llm_enabled_only
def test_llm_enabled_only(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Только проекты с llm.enabled=true попадают в результат."""
    caplog.set_level(logging.INFO)

    node_yaml = _write_node_yaml(
        tmp_path,
        [
            {"name": "llm-app", "repo": "org/llm-app"},
            {"name": "no-llm-app", "repo": "org/no-llm-app"},
        ],
    )
    projects_root = tmp_path / "projects"
    _write_ai_yaml(projects_root / "org" / "llm-app", llm_enabled=True, extra={"profile": "premium"})
    _write_ai_yaml(projects_root / "org" / "no-llm-app", llm_enabled=False)

    result = discover_llm_projects(node_yaml_path=node_yaml, projects_root=str(projects_root))

    names = [p["name"] for p in result]
    assert names == ["llm-app"], f"Expected only llm-app, got {names}"
    assert result[0]["llm"]["enabled"] is True
    assert result[0]["llm"]["profile"] == "premium"

    # LDD: IMP:9 log о найденном LLM-проекте
    assert any("[IMP:9]" in r.message for r in caplog.records), "No IMP:9 log found"


# endregion TEST_llm_enabled_only


# region TEST_llm_disabled_skipped
def test_llm_disabled_skipped(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """llm.enabled=false → проект пропускается."""
    caplog.set_level(logging.INFO)

    node_yaml = _write_node_yaml(tmp_path, [{"name": "legacy-app", "repo": "org/legacy-app"}])
    projects_root = tmp_path / "projects"
    _write_ai_yaml(projects_root / "org" / "legacy-app", llm_enabled=False)

    result = discover_llm_projects(node_yaml_path=node_yaml, projects_root=str(projects_root))
    assert result == []


# endregion TEST_llm_disabled_skipped


# region TEST_missing_ai_yaml_skipped
def test_missing_ai_yaml_skipped(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Проект без ai-platform.yaml → skip (graceful)."""
    caplog.set_level(logging.INFO)

    node_yaml = _write_node_yaml(tmp_path, [{"name": "no-yaml-app", "repo": "org/no-yaml-app"}])
    projects_root = tmp_path / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)

    result = discover_llm_projects(node_yaml_path=node_yaml, projects_root=str(projects_root))
    assert result == []


# endregion TEST_missing_ai_yaml_skipped


# region TEST_node_yaml_missing_empty
def test_node_yaml_missing_empty(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Несуществующий node.yaml → [] (никогда не raise)."""
    caplog.set_level(logging.INFO)
    result = discover_llm_projects(
        node_yaml_path=str(tmp_path / "nonexistent.yaml"),
        projects_root=str(tmp_path / "projects"),
    )
    assert result == []


# endregion TEST_node_yaml_missing_empty


# region TEST_org_repo_resolution
def test_org_repo_resolution(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """org резолвится из repo 'org/project' — путь projects_root/org/project/ai-platform.yaml."""
    caplog.set_level(logging.INFO)

    node_yaml = _write_node_yaml(tmp_path, [{"name": "nested", "repo": "my-org/nested"}])
    projects_root = tmp_path / "projects"
    _write_ai_yaml(projects_root / "my-org" / "nested", llm_enabled=True)

    result = discover_llm_projects(node_yaml_path=node_yaml, projects_root=str(projects_root))
    assert [p["name"] for p in result] == ["nested"]


# endregion TEST_org_repo_resolution


# region TEST_no_org_flat_path
def test_no_org_flat_path(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Проект без repo (или без org) → flat path projects_root/name/ai-platform.yaml."""
    caplog.set_level(logging.INFO)

    node_yaml = _write_node_yaml(tmp_path, [{"name": "flat-app", "repo": ""}])
    projects_root = tmp_path / "projects"
    _write_ai_yaml(projects_root / "flat-app", llm_enabled=True)

    result = discover_llm_projects(node_yaml_path=node_yaml, projects_root=str(projects_root))
    assert [p["name"] for p in result] == ["flat-app"]


# endregion TEST_no_org_flat_path
