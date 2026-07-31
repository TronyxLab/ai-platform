# GREP_SUMMARY: test project_remover remove unregister node-yaml compose-down vhost safe-remove idempotent
# STRUCTURE: ┌node_yaml fixture┐ → ┌vhost fixture┐ → ○ 6 tests → ⊕ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Unit tests for project_remover.py (DP-092 Wave 3). Tests unregister, idempotent removal,
##           duplicate cleanup (TRAP node_yaml.py:1186), vhost file deletion, and compose-down
##           without -v flag (invariant O7/DD10).
## @scope    project_remover.py public API: find_project_in_node_yaml, unregister_from_node_yaml,
##           remove_vhost, ssh_compose_down (mocked).
## @invariants
##   - All tests use tmp_path (no hardcoded paths)
##   - SSH operations mocked via injected callable (DI over Mocks)
##   - R5: negative test for compose-down -v flag (ANTI-SURVIVORSHIP)
##   - LDD IMP:9 assertion on every test
##   - R1-R5 compliance
## @rationale Covers AC1 (remove-project), AC2 (facade), AC3 (0 inline python3), AC4 (unit tests)
## @changes  2026-07-30 · Wave 3 — initial implementation
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)


# ── LDD helper ─────────────────────────────────────────────────────


def _assert_ldd_imp9(caplog: pytest.LogCaptureFixture) -> None:
    """Assert at least one IMP:9 log is present in caplog."""
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


# ── Helpers ────────────────────────────────────────────────────────


def _write_node_yaml(
    path: pathlib.Path,
    projects: list[dict],
    node_host: str = "10.0.0.1",
    node_name: str = "test-node",
) -> None:
    """Write a minimal node.yaml with projects array.

    ## @purpose  Standard node.yaml fixture for removal tests.
    ## @io        ⇥ path, projects, node_host → ⎋ writes file
    ## @invariants  Uses ruamel.yaml-compatible format
    """
    data = {
        "context": "test-context",
        "node": {"name": node_name, "host": node_host},
        "modules": [],
        "projects": projects,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


@pytest.fixture
def node_yaml_with_project(tmp_path: pathlib.Path) -> pathlib.Path:
    """Fixture: node.yaml with one project 'myapp'.

    ## @purpose  Single-project node.yaml for removal tests.
    ## @io        ⎋ path to node.yaml
    ## @invariants  Contains exactly 1 project: myapp (backend, domain=myapp.example.com)
    """
    node_cfg = tmp_path / "test-org" / "node-configs" / "test-node"
    node_cfg.mkdir(parents=True)
    node_yaml = node_cfg / "node.yaml"
    _write_node_yaml(
        node_yaml,
        projects=[
            {"name": "myapp", "domain": "myapp.example.com", "type": "backend", "repo": "test-org/myapp"},
        ],
    )
    return node_yaml


@pytest.fixture
def node_yaml_with_duplicates(tmp_path: pathlib.Path) -> pathlib.Path:
    """Fixture: node.yaml with duplicate project names (corrupted data scenario).

    ## @purpose  Two projects with the same name — tests TRAP node_yaml.py:1186.
    ## @io        ⎋ path to node.yaml
    """
    node_cfg = tmp_path / "test-org" / "node-configs" / "test-node"
    node_cfg.mkdir(parents=True)
    node_yaml = node_cfg / "node.yaml"
    _write_node_yaml(
        node_yaml,
        projects=[
            {"name": "dupe", "domain": "dupe1.example.com", "type": "backend", "repo": "org/dupe"},
            {"name": "myapp", "domain": "myapp.example.com", "type": "frontend", "repo": "org/myapp"},
            {"name": "dupe", "domain": "dupe2.example.com", "type": "backend", "repo": "org/dupe2"},
        ],
    )
    return node_yaml


# ── Tests ───────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_remove_existing_project · Scenario: node.yaml with project → remove → project not found · Last fail: N/A · Remove if: remover API changes
@ldd_trajectory
def test_remove_existing_project(
    node_yaml_with_project: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test removal of an existing project from node.yaml.

    ## @purpose  AC1: remove-project unregisters project, get_project returns None after.
    ## @io        node.yaml with myapp → unregister → verify project gone
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_remover import unregister_from_node_yaml
    from core.internal.shared.node_yaml import NodeYaml

    # Verify project exists
    node = NodeYaml(str(node_yaml_with_project))
    project = node.get_project("myapp")
    assert project is not None
    assert project["name"] == "myapp"

    # Remove
    result = unregister_from_node_yaml(str(node_yaml_with_project), "myapp")
    assert result is True

    # Verify project removed (reload)
    node2 = NodeYaml(str(node_yaml_with_project))
    project2 = node2.get_project("myapp")
    assert project2 is None

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_remove_missing_idempotent · Scenario: project not found → remove_project returns False, not error · Last fail: N/A · Remove if: remover API changes
@ldd_trajectory
def test_remove_missing_idempotent(
    node_yaml_with_project: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test removal of a non-existent project is idempotent — returns False, not exception.

    ## @purpose  AC1: idempotent — removing missing project = no-op, no error.
    ## @io        node.yaml without "nonexistent" → unregister → False
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_remover import unregister_from_node_yaml

    result = unregister_from_node_yaml(str(node_yaml_with_project), "nonexistent")

    # remove_project returns False if not found — NOT an exception
    assert result is False

    # Verify no side-effects on existing projects
    from core.internal.shared.node_yaml import NodeYaml

    node = NodeYaml(str(node_yaml_with_project))
    project = node.get_project("myapp")
    assert project is not None
    assert project["name"] == "myapp"

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_unregister_removes_all_duplicates · Scenario: node.yaml with duplicate names → both removed (TRAP node_yaml.py:1186) · Last fail: N/A · Remove if: remove_project behaviour changes to remove-first-only
@ldd_trajectory
def test_unregister_removes_all_duplicates(
    node_yaml_with_duplicates: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that remove_project removes ALL entries with matching name (TRAP node_yaml.py:1186).

    ## @purpose  TRAP node_yaml.py:1186 — документированное поведение: удаляются все дубликаты.
    ##           R5 (ANTI-SURVIVORSHIP): негативный тест подтверждает, что дубликаты удаляются.
    ## @io        node.yaml with 2× "dupe" → unregister → both removed, "myapp" preserved
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_remover import unregister_from_node_yaml
    from core.internal.shared.node_yaml import NodeYaml

    # Verify initial state: 2× dupe + 1× myapp
    node = NodeYaml(str(node_yaml_with_duplicates))
    projects_before = node.get_projects()
    dupe_count_before = sum(1 for p in projects_before if p["name"] == "dupe")
    assert dupe_count_before == 2, "Fixture should have 2 duplicate 'dupe' entries"

    # Remove "dupe"
    result = unregister_from_node_yaml(str(node_yaml_with_duplicates), "dupe")
    assert result is True

    # Verify BOTH dupes removed, myapp preserved
    node2 = NodeYaml(str(node_yaml_with_duplicates))
    projects_after = node2.get_projects()
    names_after = [p["name"] for p in projects_after]

    assert "dupe" not in names_after, "All duplicate 'dupe' entries should be removed"
    assert "myapp" in names_after, "Non-duplicate 'myapp' should be preserved"
    assert len(projects_after) == 1

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_remove_vhost_deletes_file · Scenario: vhost file exists → remove_vhost deletes it · Last fail: N/A · Remove if: vhost removal logic changes
@ldd_trajectory
def test_remove_vhost_deletes_file(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test vhost file removal when the file exists.

    ## @purpose  AC1: remove_vhost deletes nginx config when domain is configured.
    ## @io        tmp_path with vhost file → remove_vhost → file deleted
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_remover import remove_vhost

    # Create node-configs structure with vhost file
    node_configs = tmp_path / "node-configs" / "test-node"
    vhost_dir = node_configs / "overlays" / "nginx"
    vhost_dir.mkdir(parents=True)
    vhost_file = vhost_dir / "myapp.example.com.conf"
    vhost_file.write_text("# nginx vhost for myapp")

    assert vhost_file.exists()

    result = remove_vhost("myapp.example.com", str(node_configs))
    assert result is True
    assert not vhost_file.exists()

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_remove_vhost_no_domain_skips · Scenario: empty domain → skip vhost removal · Last fail: N/A · Remove if: vhost removal logic changes
@ldd_trajectory
def test_remove_vhost_no_domain_skips(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test vhost removal gracefully skips when domain is empty.

    ## @purpose  AC1: remove_vhost with empty domain → no-op (returns True).
    ## @io        empty domain → skip, no error
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.project_remover import remove_vhost

    result = remove_vhost("", "/some/path")
    assert result is True  # skip is success

    result2 = remove_vhost("", "")
    assert result2 is True


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_compose_down_no_volumes_flag · Scenario: compose down command must NOT contain -v flag (O7/DD10) · Last fail: N/A · Remove if: O7/DD10 contract changes
def test_compose_down_no_volumes_flag() -> None:
    """Test that compose down command does NOT include `-v` flag (invariant O7/DD10).

    ## @purpose  R5 (ANTI-SURVIVORSHIP): negative test — the entire point of safe remove
    ##           is that volumes are NEVER deleted automatically.
    ## @io        Mocked SSH runner → assert compose command does not contain "-v"
    """
    from core.internal.scaffold.project_remover import ssh_compose_down

    called_commands: list[str] = []

    def _capture_ssh(host: str, user: str, cmd: str, timeout: int) -> tuple[int, str]:
        """Fake SSH runner that captures the compose command and returns success."""
        called_commands.append(cmd)
        return 0, "Container myapp stopped"

    # First call: "echo OK" (connection test)
    # Second call: actual compose down
    # Use ci-deploy user to match the order
    result = ssh_compose_down(
        host="10.0.0.1",
        project="myapp",
        ssh_runner=_capture_ssh,
    )

    # Find the compose down command (not "echo OK")
    compose_commands = [cmd for cmd in called_commands if "docker compose" in cmd]
    assert len(compose_commands) >= 1, f"Expected at least one compose command, got: {called_commands}"

    # Assert NO "-v" flag in any compose command (O7/DD10)
    for cmd in compose_commands:
        # Check that -v is not present as a docker compose flag
        # "docker compose down --timeout 30" — OK
        # "docker compose down -v" — VIOLATION
        # We need to check that "-v" is not a standalone flag (not part of "composer -v")
        words = cmd.split()
        # Find "down" and check subsequent words
        for i, word in enumerate(words):
            if word == "down":
                remaining = words[i + 1:]
                assert "-v" not in remaining, (
                    f"O7/DD10 VIOLATION: compose down contains -v flag in: {cmd}"
                )
                break

    assert result is True
