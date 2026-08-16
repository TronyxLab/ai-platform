"""
# GREP_SUMMARY: test_project_reconciler, reconcile-projects, parse-node-yaml, stub-detection, ghcr-check, ssh-host-resolution, summary
# STRUCTURE: ▶ tmp_path + caplog + monkeypatch/mock → ◇ parse_node_yaml_projects 4x (dict/string/empty/missing) → ◇ is_stub_project 3x (stub/real/missing) → ◇ check_ghcr_image 2x (found/not-found) → ◇ resolve_ssh_host 3x (from-map/from-yaml/not-found) → ◇ reconcile_projects 2x (no-projects/stub-without-ghcr) → ⊕ ReconcileSummary is_success 2x
# region MODULE_CONTRACT
## @purpose  Unit tests for reconciler_projects.py — parse_node_yaml_projects, is_stub_project,
##           check_ghcr_image, resolve_ssh_host, reconcile_projects, and ReconcileSummary.
## @scope    Tests pure-business-logic layers: parsing, stub detection, host resolution,
##           summary aggregation. SSH operations (deliver_payload, deploy_project) are
##           tested at the integration level.
## @invariants
##   - All file operations use tmp_path exclusively
##   - GHCR check tests mock subprocess.run
##   - Each test validates IMP:9 business logic log presence via caplog
##   - No Docker, SSH, or root required
## @rationale Per DevPlan 076: unit tests cover parsing, stub detection, host resolution,
##            and summary — pure business logic. SSH delivery/deploy require integration tests.
## @changes 2026-07-25 | Created (DevPlan 076 Wave 2)
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import module under test
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal"
sys.path.insert(0, str(_MODULE_DIR))
import pytest
import reconciler_projects
from _conftest.ldd import _print_ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _make_node_yaml(tmp_path: Path, content: str) -> str:
    """Create a node.yaml file with given content and return its path."""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(content)
    return str(yaml_path)


def _make_project_dir(tmp_path: Path, name: str, ai_yaml_content: str) -> str:
    """Create a project directory with ai-platform.yaml and return its path."""
    proj_dir = tmp_path / "projects" / name
    proj_dir.mkdir(parents=True)
    ai_yaml = proj_dir / "ai-platform.yaml"
    ai_yaml.write_text(ai_yaml_content)
    return str(proj_dir)


# ═══════════════════════════════════════════════════════════════════
# region parse_node_yaml_projects
# ═══════════════════════════════════════════════════════════════════


class TestParseNodeYamlProjects:
    """Tests for parse_node_yaml_projects()."""

    def test_dict_entries(self, tmp_path, caplog):
        """Node.yaml with dict projects → list of ProjectSpec (org derived from repo, DevPlan 116 B6 T4.6)."""
        caplog.set_level(logging.INFO)
        content = """
projects:
  - name: myapp
    repo: myorg/myapp
    domain: myapp.example.com
  - name: anotherapp
"""
        yaml_path = _make_node_yaml(tmp_path, content)
        result = reconciler_projects.parse_node_yaml_projects(yaml_path)

        assert len(result) == 2
        assert result[0].name == "myapp"
        assert result[0].org == "myorg"  # derived from repo.split("/")[0]
        assert result[0].domain == "myapp.example.com"
        assert result[1].name == "anotherapp"
        assert not result[1].org
        assert not result[1].domain

        # LDD trajectory
        _print_ldd_trajectory(caplog)

    def test_string_entries_rejected(self, tmp_path, caplog):
        """Node.yaml with string projects → [] (fail-fast D3, DevPlan 116 B6 T4.6).

        str-форма отменена: node.schema.json требует dict-записи; malformed → ConfigValidationError,
        пойманный в parse_node_yaml_projects → [] (не тихий пропуск).
        """
        caplog.set_level(logging.INFO)
        content = """
projects:
  - simple-project
  - another-project
"""
        yaml_path = _make_node_yaml(tmp_path, content)
        result = reconciler_projects.parse_node_yaml_projects(yaml_path)

        assert result == [], "str-form project entries must be rejected (fail-fast D3)"

        _print_ldd_trajectory(caplog)

    def test_empty_list(self, tmp_path, caplog):
        """projects: [] → empty list."""
        caplog.set_level(logging.INFO)
        content = """
projects: []
"""
        yaml_path = _make_node_yaml(tmp_path, content)
        result = reconciler_projects.parse_node_yaml_projects(yaml_path)

        assert len(result) == 0

        _print_ldd_trajectory(caplog)

    def test_missing_section(self, tmp_path, caplog):
        """No projects key → empty list."""
        caplog.set_level(logging.INFO)
        content = """
node:
  host: myhost.example.com
"""
        yaml_path = _make_node_yaml(tmp_path, content)
        result = reconciler_projects.parse_node_yaml_projects(yaml_path)

        assert len(result) == 0

        _print_ldd_trajectory(caplog)

    def test_parse_error(self, tmp_path, caplog):
        """Invalid YAML → empty list, warning logged."""
        caplog.set_level(logging.INFO)
        content = "::: invalid yaml :::"
        yaml_path = _make_node_yaml(tmp_path, content)
        result = reconciler_projects.parse_node_yaml_projects(yaml_path)

        assert len(result) == 0
        # Should have warning log
        warnings = [r for r in caplog.records if "Failed to parse" in r.message]
        assert len(warnings) >= 1

        _print_ldd_trajectory(caplog)


# endregion parse_node_yaml_projects

# ═══════════════════════════════════════════════════════════════════
# region is_stub_project
# ═══════════════════════════════════════════════════════════════════


class TestIsStubProject:
    """Tests for is_stub_project()."""

    def test_is_stub_true(self, tmp_path, caplog):
        """ai-platform.yaml first line contains GENERATED-STUB → True."""
        caplog.set_level(logging.INFO)
        proj_dir = _make_project_dir(
            tmp_path,
            "stub-project",
            "## GENERATED-STUB — auto-generated by platform converge R3\n",
        )
        result = reconciler_projects.is_stub_project(proj_dir)
        assert result is True

        _print_ldd_trajectory(caplog)

    def test_is_stub_false_real_config(self, tmp_path, caplog):
        """ai-platform.yaml has real config (no GENERATED-STUB) → False."""
        caplog.set_level(logging.INFO)
        proj_dir = _make_project_dir(
            tmp_path,
            "real-project",
            "project: myapp\nservice: myapp\ntarget_node: prod-node\n",
        )
        result = reconciler_projects.is_stub_project(proj_dir)
        assert result is False

        _print_ldd_trajectory(caplog)

    def test_is_stub_false_missing_file(self, tmp_path, caplog):
        """ai-platform.yaml does not exist → False."""
        caplog.set_level(logging.INFO)
        proj_dir = str(tmp_path / "projects" / "empty-project")
        Path(proj_dir).mkdir(parents=True)
        result = reconciler_projects.is_stub_project(proj_dir)
        assert result is False

        _print_ldd_trajectory(caplog)

    def test_is_stub_empty_file(self, tmp_path, caplog):
        """ai-platform.yaml is empty file → False."""
        caplog.set_level(logging.INFO)
        proj_dir = _make_project_dir(tmp_path, "empty-file", "")
        result = reconciler_projects.is_stub_project(proj_dir)
        assert result is False

        _print_ldd_trajectory(caplog)

    def test_is_stub_stub_in_middle(self, tmp_path, caplog):
        """GENERATED-STUB appears not on the first line → False."""
        caplog.set_level(logging.INFO)
        content = "# Some comment\n## GENERATED-STUB — below first line\n"
        proj_dir = _make_project_dir(tmp_path, "stub-second-line", content)
        result = reconciler_projects.is_stub_project(proj_dir)
        assert result is False

        _print_ldd_trajectory(caplog)


# endregion is_stub_project

# ═══════════════════════════════════════════════════════════════════
# region check_ghcr_image
# ═══════════════════════════════════════════════════════════════════


class TestCheckGhcrImage:
    """Tests for check_ghcr_image()."""

    def test_image_found(self, caplog):
        """Mock docker manifest inspect success → True."""
        caplog.set_level(logging.INFO)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = reconciler_projects.check_ghcr_image("myorg", "myapp")
            assert result is True
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "ghcr.io/myorg/myapp:latest" in call_args

        _print_ldd_trajectory(caplog)

    def test_image_not_found(self, caplog):
        """Mock docker manifest inspect failure → False."""
        caplog.set_level(logging.INFO)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = reconciler_projects.check_ghcr_image("myorg", "myapp")
            assert result is False

        _print_ldd_trajectory(caplog)

    def test_default_org(self, caplog):
        """Empty org → defaults to tronyx-lab."""
        caplog.set_level(logging.INFO)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = reconciler_projects.check_ghcr_image("", "myapp")
            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "ghcr.io/tronyx-lab/myapp:latest" in call_args

        _print_ldd_trajectory(caplog)

    def test_timeout(self, caplog):
        """Mock subprocess.TimeoutExpired → False."""
        caplog.set_level(logging.INFO)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["docker"], timeout=30)):
            result = reconciler_projects.check_ghcr_image("myorg", "myapp")
            assert result is False

        _print_ldd_trajectory(caplog)


# endregion check_ghcr_image

# ═══════════════════════════════════════════════════════════════════
# region resolve_ssh_host
# ═══════════════════════════════════════════════════════════════════


class TestResolveSshHost:
    """Tests for resolve_ssh_host()."""

    def test_from_map(self, caplog):
        """NODE_HOST_MAP JSON provided → correct host returned."""
        caplog.set_level(logging.INFO)
        host_map = json.dumps({"test-node": "10.0.0.1"})
        result = reconciler_projects.resolve_ssh_host("test-node", "/nonexistent.yaml", host_map)
        assert result == "10.0.0.1"

        _print_ldd_trajectory(caplog)

    def test_from_node_yaml(self, tmp_path, caplog):
        """Fallback to node.yaml → node.host."""
        caplog.set_level(logging.INFO)
        content = """
node:
  host: myhost.example.com
  domain: example.com
"""
        yaml_path = _make_node_yaml(tmp_path, content)
        result = reconciler_projects.resolve_ssh_host("myhost", yaml_path, "")
        assert result == "myhost.example.com"

        _print_ldd_trajectory(caplog)

    def test_not_found(self, tmp_path, caplog):
        """Neither map nor node.yaml has host → None."""
        caplog.set_level(logging.INFO)
        content = """
node:
  domain: example.com
"""
        yaml_path = _make_node_yaml(tmp_path, content)
        result = reconciler_projects.resolve_ssh_host("unknown", yaml_path, "")
        assert result is None

        _print_ldd_trajectory(caplog)

    def test_map_has_other_node(self, caplog):
        """NODE_HOST_MAP has different node → falls back to node.yaml or None."""
        caplog.set_level(logging.INFO)
        host_map = json.dumps({"other-node": "10.0.0.2"})
        result = reconciler_projects.resolve_ssh_host("test-node", "/nonexistent.yaml", host_map)
        assert result is None

        _print_ldd_trajectory(caplog)

    def test_invalid_map_json(self, caplog):
        """Invalid JSON in NODE_HOST_MAP → warning logged, falls back."""
        caplog.set_level(logging.INFO)
        result = reconciler_projects.resolve_ssh_host("test-node", "/nonexistent.yaml", "{invalid}")
        assert result is None

        _print_ldd_trajectory(caplog)

    def test_map_takes_priority(self, tmp_path, caplog):
        """NODE_HOST_MAP takes priority over node.yaml host."""
        caplog.set_level(logging.INFO)
        content = """
node:
  host: node-yaml-host.example.com
"""
        yaml_path = _make_node_yaml(tmp_path, content)
        host_map = json.dumps({"test-node": "10.0.0.1"})
        result = reconciler_projects.resolve_ssh_host("test-node", yaml_path, host_map)
        assert result == "10.0.0.1"  # Map wins over node.yaml

        _print_ldd_trajectory(caplog)


# endregion resolve_ssh_host

# ═══════════════════════════════════════════════════════════════════
# region reconcile_projects (integration scenarios)
# ═══════════════════════════════════════════════════════════════════


class TestReconcileProjects:
    """Tests for reconcile_projects() main entry point."""

    def test_no_projects(self, tmp_path, caplog):
        """Empty projects list → summary with all zeros."""
        caplog.set_level(logging.INFO)
        content = """
projects: []
"""
        yaml_path = _make_node_yaml(tmp_path, content)
        summary = reconciler_projects.reconcile_projects("test-node", yaml_path)

        assert summary.node == "test-node"
        assert summary.deployed == 0
        assert summary.skipped == 0
        assert summary.warnings == 0
        assert summary.failures == 0
        assert summary.is_success() is True

        # IMP:9 log for "No projects"
        _assert_imp9(caplog)

        _print_ldd_trajectory(caplog)

    def test_missing_node_yaml(self, tmp_path, caplog):
        """Non-existent node.yaml → failures=1, not successful."""
        caplog.set_level(logging.INFO)
        yaml_path = str(tmp_path / "nonexistent.yaml")
        summary = reconciler_projects.reconcile_projects("test-node", yaml_path)

        assert summary.node == "test-node"
        assert summary.failures == 1
        assert summary.is_success() is False

        # IMP:10 FATAL log for missing node.yaml
        fatals = [r for r in caplog.records if "FATAL" in r.message]
        assert len(fatals) >= 1

        _print_ldd_trajectory(caplog)

    def test_stub_without_ghcr(self, tmp_path, caplog):
        """Stub project, no GHCR image → warn status."""
        caplog.set_level(logging.INFO)

        content = """
projects:
  - name: stub-app
"""
        yaml_path = _make_node_yaml(tmp_path, content)

        # reconcile_projects uses hardcoded /opt/projects path.
        # Mock Path.is_dir to simulate directory exists, and
        # is_stub_project to simulate GENERATED-STUB detection.
        original_is_dir = Path.is_dir

        def mock_is_dir(self):
            if str(self) == "/opt/projects/stub-app":
                return True
            return original_is_dir(self)

        with (
            patch.object(Path, "is_dir", mock_is_dir),
            patch.object(reconciler_projects, "is_stub_project", return_value=True),
            patch.object(reconciler_projects, "check_ghcr_image", return_value=False),
        ):
            summary = reconciler_projects.reconcile_projects("test-node", yaml_path)

        assert summary.warnings == 1
        assert summary.deployed == 0
        assert summary.skipped == 0
        assert summary.failures == 0
        assert summary.is_success() is True

        assert len(summary.results) == 1
        assert summary.results[0].project == "stub-app"
        assert summary.results[0].status == "warn"
        assert "GHCR" in summary.results[0].detail

        _assert_imp9(caplog)
        _print_ldd_trajectory(caplog)

    def test_already_deployed(self, tmp_path, caplog):
        """Real ai-platform.yaml (already deployed) → skipped."""
        caplog.set_level(logging.INFO)

        content = """
projects:
  - name: real-app
"""
        yaml_path = _make_node_yaml(tmp_path, content)

        # Mock directory exists but NOT a stub (real deployment)
        original_is_dir = Path.is_dir

        def mock_is_dir(self):
            if str(self) == "/opt/projects/real-app":
                return True
            return original_is_dir(self)

        with (
            patch.object(Path, "is_dir", mock_is_dir),
            patch.object(reconciler_projects, "is_stub_project", return_value=False),
        ):
            summary = reconciler_projects.reconcile_projects("test-node", yaml_path)

        assert summary.skipped == 1
        assert summary.deployed == 0
        assert summary.warnings == 0
        assert summary.failures == 0

        _assert_imp9(caplog)
        _print_ldd_trajectory(caplog)

    def test_directory_not_found(self, tmp_path, caplog):
        """Project directory not found on filesystem → skipped."""
        caplog.set_level(logging.INFO)

        content = """
projects:
  - name: missing-app
"""
        yaml_path = _make_node_yaml(tmp_path, content)

        summary = reconciler_projects.reconcile_projects("test-node", yaml_path)

        assert summary.skipped == 1
        assert summary.deployed == 0
        assert summary.warnings == 0
        assert summary.failures == 0

        _assert_imp9(caplog)
        _print_ldd_trajectory(caplog)

    def test_dry_run_mode(self, tmp_path, caplog):
        """Dry-run mode with stub + GHCR image → deployed count incremented but no actual deploy."""
        caplog.set_level(logging.INFO)

        content = """
projects:
  - name: stub-app
"""
        yaml_path = _make_node_yaml(tmp_path, content)

        # Mock: directory exists, is a stub, GHCR image found
        original_is_dir = Path.is_dir

        def mock_is_dir(self):
            if str(self) == "/opt/projects/stub-app":
                return True
            return original_is_dir(self)

        with (
            patch.object(Path, "is_dir", mock_is_dir),
            patch.object(reconciler_projects, "is_stub_project", return_value=True),
            patch.object(reconciler_projects, "check_ghcr_image", return_value=True),
        ):
            summary = reconciler_projects.reconcile_projects("test-node", yaml_path, dry_run=True)

        assert summary.deployed == 1
        assert summary.skipped == 0
        assert summary.warnings == 0
        assert summary.failures == 0
        assert summary.is_success() is True

        _assert_imp9(caplog)
        _print_ldd_trajectory(caplog)

    def test_projects_base_env_respected(self, tmp_path, caplog, monkeypatch):
        """PROJECTS_BASE env must drive project-dir resolution — no /opt/projects hardcode (A3).

        Regression: reconciler_projects.py:392 строил f"/opt/projects/..." без env-резолва,
        тогда как deploy_engine/payload_deliverer/orchestrator_cli резолвят PROJECTS_BASE.
        Если бы хардкод остался — проект под tmp_path не нашёлся бы → skipped (deployed=0).
        """
        caplog.set_level(logging.INFO)

        # PROJECTS_BASE → tmp_path; stub-проект физически под tmp_path (НЕ под /opt/projects)
        monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))
        proj_dir = tmp_path / "env-app"
        proj_dir.mkdir()
        (proj_dir / "ai-platform.yaml").write_text("GENERATED-STUB: true\n")

        content = """
projects:
  - name: env-app
"""
        yaml_path = _make_node_yaml(tmp_path, content)

        with (
            patch.object(reconciler_projects, "check_ghcr_image", return_value=True),
            patch.object(reconciler_projects, "resolve_ssh_host", return_value="test-host"),
            patch.object(reconciler_projects, "deploy_via_orchestrator", return_value=True),
        ):
            summary = reconciler_projects.reconcile_projects("test-node", yaml_path)

        assert summary.deployed == 1, (
            "A3 FAIL: reconciler не нашёл проект под PROJECTS_BASE — используется хардкод /opt/projects"
        )
        assert summary.failures == 0
        _assert_imp9(caplog)
        _print_ldd_trajectory(caplog)

    def test_projects_base_org_subdir(self, tmp_path, caplog, monkeypatch):
        """PROJECTS_BASE + org prefix: путь строится как PROJECTS_BASE/<org>/<name> (A3)."""
        caplog.set_level(logging.INFO)

        monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))
        proj_dir = tmp_path / "myorg" / "org-app"
        proj_dir.mkdir(parents=True)
        (proj_dir / "ai-platform.yaml").write_text("GENERATED-STUB: true\n")

        content = """
projects:
  - name: org-app
    repo: myorg/org-app
"""
        yaml_path = _make_node_yaml(tmp_path, content)

        with (
            patch.object(reconciler_projects, "check_ghcr_image", return_value=True),
            patch.object(reconciler_projects, "resolve_ssh_host", return_value="test-host"),
            patch.object(reconciler_projects, "deploy_via_orchestrator", return_value=True),
        ):
            summary = reconciler_projects.reconcile_projects("test-node", yaml_path)

        assert summary.deployed == 1, (
            "A3 FAIL: org-проект не найден под PROJECTS_BASE/<org>/ — резолвер не учитывает org-префикс"
        )
        assert summary.failures == 0
        _assert_imp9(caplog)
        _print_ldd_trajectory(caplog)


# endregion reconcile_projects

# ═══════════════════════════════════════════════════════════════════
# region ReconcileSummary
# ═══════════════════════════════════════════════════════════════════


class TestReconcileSummary:
    """Tests for ReconcileSummary dataclass."""

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({}, True),  # failures=0 → True
            ({"failures": 1}, False),  # failures=1 → False
            ({"warnings": 3}, True),  # warnings don't fail
        ],
    )
    def test_is_success(self, kwargs, expected):
        """Parametrized: ReconcileSummary.is_success() — failures vs warnings (F5-reduction)."""
        summary = reconciler_projects.ReconcileSummary(node="test", **kwargs)
        assert summary.is_success() is expected


# endregion ReconcileSummary

# ═══════════════════════════════════════════════════════════════════
# region ProjectSpec
# ═══════════════════════════════════════════════════════════════════


class TestProjectSpec:
    """Tests for ProjectSpec dataclass."""

    def test_defaults(self):
        """Default org and domain are empty strings."""
        spec = reconciler_projects.ProjectSpec(name="myapp")
        assert spec.name == "myapp"
        assert not spec.org
        assert not spec.domain

    def test_all_fields(self):
        """All fields populated."""
        spec = reconciler_projects.ProjectSpec(name="myapp", org="myorg", domain="example.com")
        assert spec.name == "myapp"
        assert spec.org == "myorg"
        assert spec.domain == "example.com"


# endregion ProjectSpec


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _assert_imp9(caplog, min_count: int = 1):
    """Assert at least min_count IMP:9+ logs are present."""
    imp9_count = sum(
        1 for r in caplog.records if "[IMP:" in r.message and int(r.message.split("[IMP:")[1].split("]")[0]) >= 9
    )
    assert imp9_count >= min_count, f"Expected at least {min_count} IMP:9+ log(s), found {imp9_count}"
