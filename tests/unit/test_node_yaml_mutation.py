"""
# GREP_SUMMARY: test_node_yaml_mutation, NodeYaml, add-project, remove-project, update-project, write-back, mutation
# STRUCTURE: ▶ 8 tests → ◇ add_project (success + duplicate) → ◇ remove_project (success + not_found) → ◇ update_project (success + not_found) → ◇ write-back (comments + pyyaml fallback) → ⎋ all pass
# region MODULE_CONTRACT
## @purpose  Unit tests for NodeYaml mutation API (add_project, remove_project, update_project, _write_back)
## @scope    Tests 8 scenarios covering the mutation API in core/internal/shared/node_yaml.py:
##           - add_project: success with write-back verification, duplicate detection
##           - remove_project: success with removed project gone, non-existent returns False
##           - update_project: success with field update, non-existent returns False
##           - _write_back: comment preservation (ruamel.yaml), PyYAML fallback
## @invariants
##   - All YAML files created via tmp_path (Zero Hardcode Rule)
##   - Each test validates LDD IMP:9 presence via @ldd_trajectory decorator
##   - No hardcoded paths, no subprocess.run for business logic
##   - Tests isolate write-back to tmp_path to prevent file system side effects
## @changes 2026-07-30 · DevPlan 088 DRIFT-088-2 — Created
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from core.internal.shared.exceptions import (
    ConfigParseError,
    ConfigValidationError,
)
from core.internal.shared.node_yaml import (
    NodeYaml,
    ProjectEntry,
)
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _write_yaml(tmp_path: Path, content: str) -> Path:
    """Write YAML content to a temp file.

    ## @purpose  Helper — creates a temporary YAML file for mutation testing.
    ## @io — ⇥ tmp_path: Path, content: str → ⎋ Path to written file
    ## @complexity — O(1)
    """
    path = tmp_path / "node.yaml"
    path.write_text(content)
    return path


def _read_yaml(path: Path) -> str:
    """Read a YAML file as string for content verification.

    ## @purpose  Helper — reads YAML file content for assertion checks.
    ## @io — ⇥ path: Path → ⎋ str file content
    ## @complexity — O(N) where N = file size
    """
    return path.read_text()


SAMPLE_NODE_YAML = """\
contexts:
  - name: myorg
node:
  name: test-node
  host: 1.2.3.4
projects:
  - name: existing-app
    repo: myorg/existing-app
    type: backend
"""


# ═══════════════════════════════════════════════════════════════════
# region Tests: add_project
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · add_project writes project to YAML and preserves existing data
# · Scenario: Add new project to node.yaml with projects → verify YAML contains both old and new
# · Last fail: N/A (new test)
# · Remove if: add_project() logic changes
@ldd_trajectory
def test_add_project_success(caplog, tmp_path):
    """NodeYaml.add_project() should add project and write back to YAML.

    ## @purpose  Verify add_project appends new project entry and data persists on re-read.
    """
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)
    node = NodeYaml(str(yaml_path))

    new_project = ProjectEntry(
        name="new-app",
        repo="myorg/new-app",
        type="backend",
        domain="new.example.com",
        database="new_app_db",
        context="",
    )

    node.add_project(new_project)

    # Re-read to verify write-back
    content = _read_yaml(yaml_path)
    assert "new-app" in content
    assert "existing-app" in content
    assert "new.example.com" in content
    assert "new_app_db" in content

    # Verify via get_projects
    node2 = NodeYaml(str(yaml_path))
    projects = node2.get_projects()
    names = [p.get("name") for p in projects if isinstance(p, dict)]
    assert "new-app" in names
    assert "existing-app" in names

    logger.critical("[IMP:9][test] add_project_success: added new-app, %d total projects — OK", len(projects))


# 🧪 TRAP[TEST] · Regression · add_project raises on duplicate name
# · Scenario: Add project with same name as existing → ConfigValidationError
# · Last fail: N/A (new test)
# · Remove if: add_project() duplicate detection changes
@ldd_trajectory
def test_add_project_duplicate(caplog, tmp_path):
    """NodeYaml.add_project() should raise ConfigValidationError for duplicate project name.

    ## @purpose  Verify duplicate project detection raises typed error.
    """
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)
    node = NodeYaml(str(yaml_path))

    dup_project = ProjectEntry(
        name="existing-app",
        repo="myorg/existing-app",
        type="backend",
    )

    with pytest.raises(ConfigValidationError) as exc_info:
        node.add_project(dup_project)
    assert "already exists" in str(exc_info.value).lower() or "duplicate" in str(exc_info.value).lower()

    logger.critical("[IMP:9][test] add_project_duplicate: raised ConfigValidationError — OK")


# endregion Tests: add_project


# ═══════════════════════════════════════════════════════════════════
# region Tests: remove_project
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · remove_project removes project and returns True
# · Scenario: Remove existing project → returns True, project gone from YAML
# · Last fail: N/A (new test)
# · Remove if: remove_project() logic changes
@ldd_trajectory
def test_remove_project_success(caplog, tmp_path):
    """NodeYaml.remove_project() should remove project and return True.

    ## @purpose  Verify remove_project deletes the correct project entry.
    """
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)
    node = NodeYaml(str(yaml_path))

    result = node.remove_project("existing-app")
    assert result is True

    # Re-read to verify removal
    content = _read_yaml(yaml_path)
    assert "existing-app" not in content

    # Verify via get_projects
    node2 = NodeYaml(str(yaml_path))
    projects = node2.get_projects()
    names = [p.get("name") for p in projects if isinstance(p, dict)]
    assert "existing-app" not in names

    logger.critical("[IMP:9][test] remove_project_success: removed existing-app, result=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · remove_project returns False for non-existent project
# · Scenario: Remove project that doesn't exist → returns False
# · Last fail: N/A (new test)
# · Remove if: remove_project() logic changes
@ldd_trajectory
def test_remove_project_not_found(caplog, tmp_path):
    """NodeYaml.remove_project() should return False for non-existent project name.

    ## @purpose  Verify remove_project gracefully handles missing project.
    """
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)
    node = NodeYaml(str(yaml_path))

    result = node.remove_project("nonexistent-project")
    assert result is False

    logger.critical("[IMP:9][test] remove_project_not_found: result=%s — OK", result)


# endregion Tests: remove_project


# ═══════════════════════════════════════════════════════════════════
# region Tests: update_project
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · update_project updates field and returns True
# · Scenario: Update existing project domain → returns True, YAML reflects change
# · Last fail: N/A (new test)
# · Remove if: update_project() logic changes
@ldd_trajectory
def test_update_project_success(caplog, tmp_path):
    """NodeYaml.update_project() should update project fields and return True.

    ## @purpose  Verify update_project modifies the correct project entry.
    """
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)
    node = NodeYaml(str(yaml_path))

    result = node.update_project("existing-app", domain="new-domain.example.com")
    assert result is True

    # Re-read to verify update
    content = _read_yaml(yaml_path)
    assert "new-domain.example.com" in content

    # Verify via typed API
    node2 = NodeYaml(str(yaml_path))
    projects = node2.get_projects()
    found = False
    for p in projects:
        if isinstance(p, dict) and p.get("name") == "existing-app":
            assert p.get("domain") == "new-domain.example.com"
            found = True
            break
    assert found

    logger.critical("[IMP:9][test] update_project_success: updated domain, result=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · update_project returns False for non-existent project
# · Scenario: Update project that doesn't exist → returns False
# · Last fail: N/A (new test)
# · Remove if: update_project() logic changes
@ldd_trajectory
def test_update_project_not_found(caplog, tmp_path):
    """NodeYaml.update_project() should return False for non-existent project name.

    ## @purpose  Verify update_project gracefully handles missing project.
    """
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)
    node = NodeYaml(str(yaml_path))

    result = node.update_project("nonexistent-project", domain="ignored.example.com")
    assert result is False

    logger.critical("[IMP:9][test] update_project_not_found: result=%s — OK", result)


# endregion Tests: update_project


# ═══════════════════════════════════════════════════════════════════
# region Tests: _write_back (comment preservation, fallback)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · write-back preserves YAML comments when ruamel available
# · Scenario: Add project to YAML with comments → ruamel.yaml preserves them (if installed)
# · Last fail: N/A (new test)
# · Remove if: _write_back() comment preservation logic changes
@ldd_trajectory
def test_write_back_preserves_comments(caplog, tmp_path):
    """NodeYaml add_project should preserve comments in YAML via ruamel.yaml.

    ## @purpose  Verify write-back via ruamel.yaml does not strip YAML comments.
    ##            If ruamel.yaml is not available, verify write-back still succeeds
    ##            (using PyYAML fallback, without comment preservation).
    """
    try:
        import ruamel.yaml  # noqa: F401

        _ruamel_available = True
    except ImportError:
        _ruamel_available = False

    yaml_with_comments = """\
# ═══════════════════════════════════════
# Node configuration for test-node
# ═══════════════════════════════════════
contexts:
  - name: myorg

node:
  name: test-node
  host: 1.2.3.4  # production host

# Project definitions
projects:
  # First project: existing-app
  - name: existing-app
    repo: myorg/existing-app
    type: backend
"""
    yaml_path = _write_yaml(tmp_path, yaml_with_comments)
    node = NodeYaml(str(yaml_path))

    new_project = ProjectEntry(
        name="new-app",
        repo="myorg/new-app",
        type="frontend",
        domain="new.example.com",
    )

    node.add_project(new_project)

    # Re-read to verify write-back
    content = _read_yaml(yaml_path)
    assert "new-app" in content
    assert "new.example.com" in content

    if _ruamel_available:
        # Ruamel preserves comments — verify them
        assert "# Node configuration" in content
        assert "# production host" in content
        assert "# First project: existing-app" in content
        logger.critical("[IMP:9][test] write_back_preserves_comments: ruamel available, comments preserved — OK")
    else:
        # PyYAML fallback — comments may be stripped, but write-back succeeded
        logger.critical("[IMP:9][test] write_back_preserves_comments: ruamel unavailable, PyYAML fallback — OK")


# 🧪 TRAP[TEST] · Regression · write-back falls back to PyYAML when ruamel is absent
# · Scenario: Simulate ruamel.yaml ImportError → _write_back uses PyYAML
# · Last fail: N/A (new test)
# · Remove if: _write_back() fallback logic changes
@ldd_trajectory
def test_write_back_pyyaml_fallback(caplog, tmp_path, monkeypatch):
    """NodeYaml _write_back should fall back to PyYAML when ruamel.yaml is unavailable.

    ## @purpose  Verify graceful degradation: without ruamel.yaml, PyYAML dump is used.
    """
    # Monkeypatch ruamel.yaml import to raise ImportError
    import builtins

    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "ruamel.yaml":
            raise ImportError("Mock: ruamel.yaml not available")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)
    node = NodeYaml(str(yaml_path))

    new_project = ProjectEntry(
        name="pyyaml-app",
        repo="myorg/pyyaml-app",
        type="backend",
    )

    # Should succeed via PyYAML fallback
    node.add_project(new_project)

    # Re-read to verify write-back succeeded
    content = _read_yaml(yaml_path)
    assert "pyyaml-app" in content
    assert "myorg/pyyaml-app" in content

    # Verify typed access still works
    node2 = NodeYaml(str(yaml_path))
    projects = node2.get_projects()
    names = [p.get("name") for p in projects if isinstance(p, dict)]
    assert "pyyaml-app" in names

    logger.critical("[IMP:9][test] write_back_pyyaml_fallback: PyYAML fallback write succeeded — OK")


# endregion Tests: _write_back


# ═══════════════════════════════════════════════════════════════════
# region Tests: cache-safety on disk error (DevPlan 116 B6 T6.1)
# ═══════════════════════════════════════════════════════════════════


def _patch_write_open_raises(monkeypatch):
    """Patch atomic-commit so ANY _write_back write raises OSError; reads pass through.

    ## @purpose  Simulates disk-full/permission-denied for _write_back write paths.
    ##           DevPlan 119 E5: _write_back использует shared/atomic_writer
    ##           (tempfile.NamedTemporaryFile + os.replace) — НЕ builtins.open.
    ##           Точка коммита = os.replace в atomic_writer → патчим его (той же
    ##           семантикой disk-full, что раньше ловил builtins.open).
    ## @io        ⇥ monkeypatch → ⎋ None (side-effect: patched os.replace)
    """
    import core.internal.shared.atomic_writer as atomic_writer

    original_replace = atomic_writer.os.replace

    def mock_replace(*args, **kwargs):
        raise OSError("Mock: disk full (write)")

    monkeypatch.setattr(atomic_writer.os, "replace", mock_replace)
    # keep reference to avoid lint-unused (documented simulation target)
    assert original_replace is not None


# 🧪 TRAP[TEST] · Regression · add_project disk error → ConfigParseError + cache clean
# · Scenario: write fails (OSError) → add_project raises ConfigParseError; reload() from disk
# ·   must NOT contain the added project (cache never poisoned — deepcopy mutation)
# · Last fail: N/A (DevPlan 116 B6 T6.1 — TRAP[BUG] 2026-07-30 fix verification)
# · Remove if: _write_back failure semantics change
@ldd_trajectory
def test_add_project_disk_error_cache_clean(caplog, tmp_path, monkeypatch):
    """add_project on disk write failure → ConfigParseError AND reload() shows no mutation."""
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)  # fixture written BEFORE patching open
    _patch_write_open_raises(monkeypatch)
    node = NodeYaml(str(yaml_path))

    with pytest.raises(ConfigParseError):
        node.add_project(ProjectEntry(name="new-app", repo="myorg/new-app", type="backend"))

    # Cache must be clean — reload from disk must NOT contain the added project
    node.reload()
    names = [p.get("name") for p in node.get_projects() if isinstance(p, dict)]
    assert "new-app" not in names, "add_project mutation leaked into cache after failed write"

    logger.critical("[IMP:9][test] add_project_disk_error: ConfigParseError raised, cache clean — OK")


# 🧪 TRAP[TEST] · Regression · remove_project disk error → ConfigParseError + project intact
# · Scenario: write fails → remove_project raises ConfigParseError; reload() from disk still
# ·   contains the project (removal not persisted, cache clean)
# · Last fail: N/A (DevPlan 116 B6 T6.1)
# · Remove if: _write_back failure semantics change
@ldd_trajectory
def test_remove_project_disk_error_cache_clean(caplog, tmp_path, monkeypatch):
    """remove_project on disk write failure → ConfigParseError AND reload() shows project intact."""
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)  # fixture written BEFORE patching open
    _patch_write_open_raises(monkeypatch)
    node = NodeYaml(str(yaml_path))

    with pytest.raises(ConfigParseError):
        node.remove_project("existing-app")

    node.reload()
    names = [p.get("name") for p in node.get_projects() if isinstance(p, dict)]
    assert "existing-app" in names, "remove_project mutation leaked into cache after failed write"

    logger.critical("[IMP:9][test] remove_project_disk_error: ConfigParseError raised, project intact — OK")


# 🧪 TRAP[TEST] · Regression · update_project disk error → ConfigParseError + original value
# · Scenario: write fails → update_project raises ConfigParseError; reload() shows OLD domain
# · Last fail: N/A (DevPlan 116 B6 T6.1)
# · Remove if: _write_back failure semantics change
@ldd_trajectory
def test_update_project_disk_error_cache_clean(caplog, tmp_path, monkeypatch):
    """update_project on disk write failure → ConfigParseError AND reload() shows old value."""
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)  # fixture written BEFORE patching open
    _patch_write_open_raises(monkeypatch)
    node = NodeYaml(str(yaml_path))

    with pytest.raises(ConfigParseError):
        node.update_project("existing-app", domain="hacked.example.com")

    node.reload()
    projects = node.get_projects()
    for p in projects:
        if isinstance(p, dict) and p.get("name") == "existing-app":
            assert p.get("domain") != "hacked.example.com", "update_project mutation leaked into cache"
            break

    logger.critical("[IMP:9][test] update_project_disk_error: ConfigParseError raised, old value intact — OK")


# endregion Tests: cache-safety on disk error


# ═══════════════════════════════════════════════════════════════════
# region Tests: add_context (DevPlan 116 B6 T6.3)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · add_context adds entry to contexts[]
# · Scenario: contexts[] exists → add_context appends {"name": ...} + optional fields
# · Last fail: N/A (DevPlan 116 B6 T6.3)
# · Remove if: add_context() logic changes
@ldd_trajectory
def test_add_context_success(caplog, tmp_path):
    """NodeYaml.add_context() should append a context entry and persist on disk."""
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)
    node = NodeYaml(str(yaml_path))

    result = node.add_context(
        name="prod",
        description="production",
        node_configs_repo="org/prod-node-configs",
        hermes_agent_repo="org/prod-hermes",
    )
    assert result is True

    node2 = NodeYaml(str(yaml_path))
    contexts = node2.get("contexts")
    names = [c.get("name") for c in contexts if isinstance(c, dict)]
    assert "prod" in names
    assert len(contexts) == 2  # myorg + prod

    logger.critical("[IMP:9][test] add_context_success: added 'prod', %d contexts total — OK", len(contexts))


# 🧪 TRAP[TEST] · Regression · add_context raises on duplicate name
# · Scenario: add context with same name as existing → ConfigValidationError
# · Last fail: N/A (DevPlan 116 B6 T6.3)
# · Remove if: add_context() duplicate detection changes
@ldd_trajectory
def test_add_context_duplicate_raises(caplog, tmp_path):
    """NodeYaml.add_context() should raise ConfigValidationError for duplicate context name."""
    yaml_path = _write_yaml(tmp_path, SAMPLE_NODE_YAML)
    node = NodeYaml(str(yaml_path))

    with pytest.raises(ConfigValidationError) as exc_info:
        node.add_context(name="myorg")
    assert "already exists" in str(exc_info.value).lower() or "duplicate" in str(exc_info.value).lower()

    logger.critical("[IMP:9][test] add_context_duplicate: raised ConfigValidationError — OK")


# 🧪 TRAP[TEST] · Regression · add_context creates contexts[] when missing
# · Scenario: node.yaml without contexts section → add_context creates it
# · Last fail: N/A (DevPlan 116 B6 T6.3)
# · Remove if: add_context() section-creation logic changes
@ldd_trajectory
def test_add_context_creates_section(caplog, tmp_path):
    """NodeYaml.add_context() should create a missing contexts section."""
    yaml_path = _write_yaml(tmp_path, "node:\n  name: test-node\n  host: 1.2.3.4\nprojects: []\n")
    node = NodeYaml(str(yaml_path))

    node.add_context(name="first-ctx")

    node2 = NodeYaml(str(yaml_path))
    contexts = node2.get("contexts")
    assert len(contexts) == 1
    assert contexts[0].get("name") == "first-ctx"

    logger.critical("[IMP:9][test] add_context_creates_section: contexts section created — OK")


# endregion Tests: add_context
