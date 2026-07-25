"""
# GREP_SUMMARY: test_context_deployer, project-deploy, ghcr-pull, build-fallback, idempotent, healthcheck-gate, audit-log
# STRUCTURE: ▶ tmp_path + node.yaml + mock subprocess → ◇ filter projects → ◇ ghcr pull → ◇ build fallback → ◇ idempotent skip → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for context_deployer.py — context project deploy orchestration.
## @scope    Tests resolve_context_projects, deploy_context_projects, extract_context_from_node_yaml.
## @invariants
##   - All subprocess calls mocked (no real docker compose)
##   - node.yaml created in tmp_path
##   - Each test validates IMP:9 business logic log presence
## @rationale DevPlan 047 Phase 7: context deployer bridges bootstrap "last mile".
## @changes  2026-07-22 | DevPlan 047 Phase 7 — Created
# endregion MODULE_CONTRACT
"""

import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_MODULE_DIR))
import context_deployer as cd

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def node_yaml_file(tmp_path):
    """Create a node.yaml with test projects."""
    yaml_content = """\
node:
  name: test-node
  platform_domain: test.example.com
context: test-ctx
projects:
  - name: webapp
    repo: https://github.com/test/webapp
    type: backend
    domain: webapp.example.com
    context: test-ctx
  - name: api
    repo: https://github.com/test/api
    type: backend
    domain: api.example.com
    context: test-ctx
  - name: other-ctx-project
    repo: https://github.com/test/other
    type: frontend
    domain: other.other.com
    context: other-ctx
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content)
    return str(yaml_path)


@pytest.fixture
def mock_docker():
    """Mock all docker subprocess calls."""
    with patch("subprocess.run") as mock:
        mock.return_value.returncode = 0
        mock.return_value.stdout = ""
        mock.return_value.stderr = ""
        yield mock


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: resolve_context_projects
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · resolve_context_projects filters projects by context
# · Scenario: node.yaml with 3 projects (2 test-ctx, 1 other-ctx) → returns 2 for test-ctx
# · Last fail: N/A (new test)
# · Remove if: context filtering logic changes
@ldd_trajectory
def test_filter_projects_by_context(caplog, node_yaml_file):
    """resolve_context_projects should filter projects by context."""
    projects = cd.resolve_context_projects(node_yaml_file, "test-ctx")
    assert len(projects) == 2
    names = [p.name for p in projects]
    assert "webapp" in names
    assert "api" in names
    assert "other-ctx-project" not in names
    logger.critical("[IMP:9][test] Filter projects by context — 2 of 3 matched")


# 🧪 TRAP[TEST] · Regression · resolve_context_projects returns empty for non-matching context
# · Scenario: context="nonexistent" → returns 0 projects
# · Last fail: N/A (new test)
# · Remove if: context filtering logic changes
@ldd_trajectory
def test_filter_projects_no_match(caplog, node_yaml_file):
    """resolve_context_projects should return empty for non-matching context."""
    projects = cd.resolve_context_projects(node_yaml_file, "nonexistent-ctx")
    assert len(projects) == 0
    logger.critical("[IMP:9][test] No projects match nonexistent context")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: extract_context_from_node_yaml
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · extract_context_from_node_yaml reads context field
# · Scenario: node.yaml has context: test-ctx → returns "test-ctx"
# · Last fail: N/A (new test)
# · Remove if: context extraction logic changes
@ldd_trajectory
def test_extract_context_string(caplog, node_yaml_file):
    """extract_context_from_node_yaml should read context field."""
    ctx = cd.extract_context_from_node_yaml(node_yaml_file)
    assert ctx == "test-ctx"
    logger.critical("[IMP:9][test] Context extracted from string field")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: deploy_context_projects
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · deploy skips already-healthy projects (idempotent)
# · Scenario: _is_project_healthy returns True → project skipped
# · Last fail: N/A (new test)
# · Remove if: idempotent skip logic changes
@ldd_trajectory
def test_idempotent_skip_healthy(caplog, node_yaml_file, monkeypatch):
    """deploy_context_projects should skip healthy projects."""
    monkeypatch.setattr(cd, "_is_project_healthy", lambda name: True)
    monkeypatch.setattr(cd, "_write_audit", lambda p, r: None)

    results = cd.deploy_context_projects(node_yaml_file, "test-ctx")
    assert len(results) == 2
    for r in results:
        assert r.status == "skipped"
        assert r.channel == "skip"
    logger.critical("[IMP:9][test] Idempotent skip — healthy projects not re-deployed")


# 🧪 TRAP[TEST] · Regression · ghcr.io pull success path
# · Scenario: _docker_compose_pull returns True → channel="ghcr", project deployed
# · Last fail: N/A (new test)
# · Remove if: ghcr pull path changes
@ldd_trajectory
def test_ghcr_pull_success(caplog, node_yaml_file, monkeypatch, tmp_path):
    """deploy_context_projects should use ghcr channel on successful pull."""
    monkeypatch.setattr(cd, "_is_project_healthy", lambda name: False)
    monkeypatch.setattr(cd, "_docker_compose_pull", lambda d: True)
    monkeypatch.setattr(cd, "_docker_compose_build", lambda d: True)
    monkeypatch.setattr(cd, "_docker_compose_up", lambda d: True)
    monkeypatch.setattr(cd, "_wait_until_healthy", lambda n, timeout=60: "healthy")
    monkeypatch.setattr(cd, "_write_audit", lambda p, r: None)

    results = cd.deploy_context_projects(node_yaml_file, "test-ctx", projects_base=str(tmp_path))
    assert len(results) == 2
    for r in results:
        assert r.status == "deployed"
        assert r.channel == "ghcr"
        assert r.health == "healthy"
    logger.critical("[IMP:9][test] ghcr pull success — channel=ghcr")


# 🧪 TRAP[TEST] · Regression · ghcr.io pull fails → build fallback
# · Scenario: _docker_compose_pull returns False → _docker_compose_build called → channel="build"
# · Last fail: N/A (new test)
# · Remove if: build fallback logic changes
@ldd_trajectory
def test_ghcr_fails_fallback_build(caplog, node_yaml_file, monkeypatch, tmp_path):
    """deploy_context_projects should fall back to build when ghcr pull fails."""
    monkeypatch.setattr(cd, "_is_project_healthy", lambda name: False)
    monkeypatch.setattr(cd, "_docker_compose_pull", lambda d: False)  # ghcr fails
    monkeypatch.setattr(cd, "_docker_compose_build", lambda d: True)  # build succeeds
    monkeypatch.setattr(cd, "_docker_compose_up", lambda d: True)
    monkeypatch.setattr(cd, "_wait_until_healthy", lambda n, timeout=60: "healthy")
    monkeypatch.setattr(cd, "_write_audit", lambda p, r: None)

    results = cd.deploy_context_projects(node_yaml_file, "test-ctx", projects_base=str(tmp_path))
    assert len(results) == 2
    for r in results:
        assert r.status == "deployed"
        assert r.channel == "build"  # fallback to build
    logger.critical("[IMP:9][test] ghcr fail → build fallback — channel=build")


# 🧪 TRAP[TEST] · Regression · health-gate timeout marks unhealthy
# · Scenario: _wait_until_healthy returns "unhealthy" → health="unhealthy"
# · Last fail: N/A (new test)
# · Remove if: health-gate logic changes
@ldd_trajectory
def test_health_gate_timeout(caplog, node_yaml_file, monkeypatch, tmp_path):
    """deploy_context_projects should mark unhealthy on health-gate timeout."""
    monkeypatch.setattr(cd, "_is_project_healthy", lambda name: False)
    monkeypatch.setattr(cd, "_docker_compose_pull", lambda d: True)
    monkeypatch.setattr(cd, "_docker_compose_up", lambda d: True)
    monkeypatch.setattr(cd, "_wait_until_healthy", lambda n, timeout=60: "unhealthy")  # timeout
    monkeypatch.setattr(cd, "_write_audit", lambda p, r: None)

    results = cd.deploy_context_projects(node_yaml_file, "test-ctx", projects_base=str(tmp_path))
    for r in results:
        assert r.health == "unhealthy"
    logger.critical("[IMP:9][test] Health-gate timeout — unhealthy detected")


# 🧪 TRAP[TEST] · Regression · one project failure does not block others (non-fatal)
# · Scenario: First project deploy fails, second succeeds → both processed
# · Last fail: N/A (new test)
# · Remove if: non-fatal continuation logic changes
@ldd_trajectory
def test_non_fatal_continues_on_failure(caplog, node_yaml_file, monkeypatch, tmp_path):
    """deploy_context_projects should continue after one project fails."""
    call_count = {"pull": 0}

    def mock_pull(d):
        call_count["pull"] += 1
        return call_count["pull"] != 1  # First project fails, second succeeds

    def mock_build(d):
        return call_count["pull"] == 1 and False  # First project build also fails

    monkeypatch.setattr(cd, "_is_project_healthy", lambda name: False)
    monkeypatch.setattr(cd, "_docker_compose_pull", mock_pull)
    monkeypatch.setattr(cd, "_docker_compose_build", mock_build)
    monkeypatch.setattr(cd, "_docker_compose_up", lambda d: True)
    monkeypatch.setattr(cd, "_wait_until_healthy", lambda n, timeout=60: "healthy")
    monkeypatch.setattr(cd, "_write_audit", lambda p, r: None)

    results = cd.deploy_context_projects(node_yaml_file, "test-ctx", projects_base=str(tmp_path))
    assert len(results) == 2
    statuses = [r.status for r in results]
    assert "failed" in statuses  # At least one failed
    logger.critical("[IMP:9][test] Non-fatal — second project processed despite first failure")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: _ensure_bootstrap_compose
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _ensure_bootstrap_compose creates minimal docker-compose.yml
# · Scenario: project_dir with no docker-compose.yml → creates nginx:alpine compose with ai-platform.bootstrap label
# · Last fail: N/A (new test)
# · Remove if: bootstrap compose generation logic changes
@ldd_trajectory
def test_bootstrap_compose_generation(caplog, tmp_path):
    """_ensure_bootstrap_compose should create docker-compose.yml with nginx:alpine, correct labels, and healthcheck."""
    project = cd.ProjectInfo(
        name="test-webapp",
        repo="https://github.com/test/webapp",
        type="backend",
        domain="webapp.test.example.com",
        context="test-ctx",
    )
    project_dir = str(tmp_path / "projects" / "test-webapp")

    result = cd._ensure_bootstrap_compose(project_dir, project)

    assert result is True, "_ensure_bootstrap_compose should return True on success"
    compose_file = tmp_path / "projects" / "test-webapp" / "docker-compose.yml"
    assert compose_file.is_file(), "docker-compose.yml should be created"

    content = compose_file.read_text()
    assert "image: nginx:alpine" in content, "Should use nginx:alpine image"
    assert "ai-platform.bootstrap=true" in content, "Should have ai-platform.bootstrap label"
    assert "ai-platform.project=test-webapp" in content, "Should have ai-platform.project label"
    assert "healthcheck:" in content, "Should have healthcheck section"
    assert "restart: unless-stopped" in content, "Should have restart policy"
    assert "GENERATED-STUB" in content, "Should indicate it's a generated stub"
    logger.critical("[IMP:9][test] Bootstrap compose generated for %s — image=nginx:alpine, label=ai-platform.bootstrap=true, healthcheck present", project.name)


# 🧪 TRAP[TEST] · Regression · _ensure_bootstrap_compose does NOT overwrite existing docker-compose.yml
# · Scenario: docker-compose.yml already exists → returns True, does NOT modify file
# · Last fail: N/A (new test)
# · Remove if: idempotent skip logic changes
@ldd_trajectory
def test_bootstrap_compose_idempotent(caplog, tmp_path):
    """_ensure_bootstrap_compose should NOT overwrite an existing docker-compose.yml."""
    project = cd.ProjectInfo(
        name="test-webapp",
        repo="https://github.com/test/webapp",
        type="backend",
        domain="webapp.test.example.com",
        context="test-ctx",
    )
    project_dir = str(tmp_path / "projects" / "test-webapp")
    os.makedirs(project_dir, exist_ok=True)

    # Create a pre-existing docker-compose.yml with different content
    existing_content = "# REAL DELIVERY — this should NOT be overwritten\nversion: '3.8'\nservices:\n  webapp:\n    image: custom:latest\n"
    compose_file = os.path.join(project_dir, "docker-compose.yml")
    with open(compose_file, "w") as f:
        f.write(existing_content)

    result = cd._ensure_bootstrap_compose(project_dir, project)

    assert result is True, "_ensure_bootstrap_compose should return True (skip) when compose exists"
    # Verify the file was NOT overwritten
    content = Path(compose_file).read_text()
    assert content == existing_content, "Existing docker-compose.yml should NOT be overwritten"
    assert "REAL DELIVERY" in content, "Original content should be preserved intact"
    assert "nginx:alpine" not in content, "New content should NOT appear"
    logger.critical("[IMP:9][test] Bootstrap compose idempotent — existing file preserved unchanged")


# endregion
