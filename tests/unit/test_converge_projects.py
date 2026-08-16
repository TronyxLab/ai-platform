"""
# GREP_SUMMARY: test-converge-projects, r3, reconcile-projects, parse-projects-yaml, stub-creation, PROJECTS_BASE
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R3 reconcile_projects 4× (no-projects/valid-name/invalid-name/dry-run) → ◇ parse_projects_yaml 2× (dict-form/str-form-rejected) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for converge/projects.py via reconciler.reconcile_projects (R3)
##           and parse_projects_yaml (canonical NodeYaml parser, DevPlan 116 B6 T4).
## @scope    Tests project directory/stub creation, name validation, dry-run semantics,
##           and the canonical projects-yaml parser (dict-form + str-form rejection).
##           Does NOT require a real docker daemon.
## @invariants
##   - File operations use tmp_path exclusively
##   - PROJECTS_BASE monkeypatched to tmp_path subdirectory
##   - Each test validates IMP:9 business logic log presence via caplog
## @rationale Direct function testing with tmp_path for file-based units (R3, parser).
##   Вынесен из монолита test_reconciler.py (DevPlan 118 F6).
## @changes 2026-08-02 · F6 split — R3 projects + parser (DevPlan 118)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

# Load the LDD trajectory decorator from shared conftest
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

import core.internal.bootstrap.converge.projects as _converge_projects
from core.internal.bootstrap.converge import infra
from core.internal.bootstrap.converge.projects import parse_projects_yaml as _converge_parse_projects_yaml

pytestmark = pytest.mark.static_audit

# Re-export for fixture cleanups
MODULE = reconciler


# ═══════════════════════════════════════════════════════════════════
# region Fixtures


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    infra.reset_state()
    infra.node_name = "test-node"
    infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")
    yield


def empty_node_yaml(tmp_path):
    """Create node.yaml with no projects."""
    yaml_content = "contexts:\n  - name: test-context\nprojects: []\n"
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content)
    return str(yaml_path)


# endregion Fixtures


# region FUNC_test_reconcile_projects_no_projects
## 🧪 TRAP[TEST] · R3 no projects · Scenario: node.yaml has no projects → SKIP
## · Regression: converge.sh lines 523-527
## · Last fail: never
## · Remove if: reconciler.R3 project parsing logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_projects_no_projects(tmp_path, caplog, monkeypatch):
    """R3: No projects in node.yaml → status=skipped."""
    caplog.set_level(logging.INFO)

    entry = reconciler.reconcile_projects(str(empty_node_yaml(tmp_path)), dry_run=False, report_only=False)

    assert entry["status"] == "skipped"


# endregion FUNC_test_reconcile_projects_no_projects


# region FUNC_test_reconcile_projects_valid_names
## 🧪 TRAP[TEST] · R3 valid names · Scenario: valid project names → directories + stubs created
## · Regression: converge.sh lines 533-636
## · Last fail: never
## · Remove if: reconciler.R3 project creation logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_projects_valid_names(tmp_path, caplog, monkeypatch):
    """R3: Valid project names → directories and stubs created."""
    caplog.set_level(logging.INFO)

    # Create node.yaml in tmp_path
    yaml_path = tmp_path / "node.yaml"
    yaml_content = """
projects:
  - name: myapp
    domain: myapp.example.com
  - name: api-service
"""
    yaml_path.write_text(yaml_content)

    # Monkeypatch PROJECTS_BASE to a tmp_path subdirectory
    projects_base = tmp_path / "projects"
    projects_base.mkdir()
    _converge_projects.PROJECTS_BASE = str(projects_base)

    # Set _core_dir for gen-env-platform.sh fallback
    infra.core_dir = str(tmp_path)

    entry = reconciler.reconcile_projects(str(yaml_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R3"
    # Should have mutated directories/stubs
    assert (projects_base / "myapp").is_dir(), "myapp directory should exist"
    assert (projects_base / "myapp" / "ai-platform.yaml").is_file(), "myapp stub should exist"
    assert (projects_base / "api-service").is_dir(), "api-service directory should exist"

    # Verify stub content
    stub_content = (projects_base / "myapp" / "ai-platform.yaml").read_text()
    assert "GENERATED-STUB" in stub_content


# endregion FUNC_test_reconcile_projects_valid_names


# region FUNC_test_reconcile_projects_invalid_name
## 🧪 TRAP[TEST] · R3 invalid name · Scenario: project name with / → fail
## · Regression: converge.sh lines 539-544
## · Last fail: never
## · Remove if: reconciler.R3 name validation logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_projects_invalid_name(tmp_path, caplog, monkeypatch):
    """R3: Invalid project name with '/' → fail entry."""
    caplog.set_level(logging.INFO)

    yaml_path = tmp_path / "node.yaml"
    yaml_content = """
projects:
  - name: myapp/subdir
"""
    yaml_path.write_text(yaml_content)

    projects_base = tmp_path / "projects"
    projects_base.mkdir()
    _converge_projects.PROJECTS_BASE = str(projects_base)

    entry = reconciler.reconcile_projects(str(yaml_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R3"
    # The unit should have errors
    assert infra.has_errors
    assert infra.exit_code >= 2


# endregion FUNC_test_reconcile_projects_invalid_name


# region FUNC_test_reconcile_projects_dry_run
## 🧪 TRAP[TEST] · R3 dry-run · Scenario: --dry-run does not create directories
## · Regression: converge.sh lines 550-552
## · Last fail: never
## · Remove if: reconciler.R3 dry-run logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_projects_dry_run(tmp_path, caplog, monkeypatch):
    """R3: --dry-run reports but does not create directories."""
    caplog.set_level(logging.INFO)

    yaml_path = tmp_path / "node.yaml"
    yaml_content = """
projects:
  - name: myapp
"""
    yaml_path.write_text(yaml_content)

    projects_base = tmp_path / "projects"
    projects_base.mkdir()
    _converge_projects.PROJECTS_BASE = str(projects_base)

    entry = reconciler.reconcile_projects(str(yaml_path), dry_run=True, report_only=False)

    assert entry["unit"] == "R3"
    # Directory should NOT have been created
    assert not (projects_base / "myapp").is_dir(), "Directory should not exist in dry-run mode"


# endregion FUNC_test_reconcile_projects_dry_run


# region FUNC_test_parse_projects_yaml
## 🧪 TRAP[TEST] · _parse_projects_yaml · Scenario: parse dict project entries via canonical parser
## · Regression: converge.sh inline python3 lines 502-518; DevPlan 116 B6 T4 (canonical parser)
## · Last fail: never
## · Remove if: reconciler yaml parsing logic changes
@ldd_trajectory
def test_parse_projects_yaml(tmp_path, caplog):
    """_parse_projects_yaml: canonical NodeYaml.get_project_entries() (DevPlan 116 B6 T4)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] _parse_projects_yaml parsing")

    yaml_path = tmp_path / "node.yaml"
    yaml_content = """
projects:
  - name: myapp
    domain: myapp.example.com
  - name: api
"""
    yaml_path.write_text(yaml_content)

    projects = _converge_parse_projects_yaml(str(yaml_path))
    assert len(projects) == 2

    # Dict entry with domain
    assert projects[0]["name"] == "myapp"
    assert projects[0]["domain"] == "myapp.example.com"

    # Dict entry without domain
    assert projects[1]["name"] == "api"
    assert not projects[1]["domain"]


# endregion FUNC_test_parse_projects_yaml


# region FUNC_test_parse_projects_yaml_str_form_rejected
## 🧪 TRAP[TEST] · _parse_projects_yaml str-form · Scenario: str project entry → fail-fast [] (D3)
## · Regression: DevPlan 116 B6 D3 — str-форма отменена (schema требует dict), парсер не пропускает malformed
## · Last fail: never
## · Remove if: fail-fast parser semantics change
@ldd_trajectory
def test_parse_projects_yaml_str_form_rejected(tmp_path, caplog):
    """_parse_projects_yaml: str-form entry → ConfigValidationError caught → [] (fail-fast D3)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] _parse_projects_yaml str-form rejection")

    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text("projects:\n  - simple-project\n")

    projects = _converge_parse_projects_yaml(str(yaml_path))
    assert projects == [], "str-form project entries must be rejected (fail-fast D3)"

    logger.critical("[IMP:9][test] _parse_projects_yaml str-form rejected → [] — OK")


# endregion FUNC_test_parse_projects_yaml_str_form_rejected
