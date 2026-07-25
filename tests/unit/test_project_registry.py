"""
# GREP_SUMMARY: test_project_registry, register-project, deregister-project, list-projects, node-yaml, idempotent
# STRUCTURE: ▶ tmp_path + caplog → ◇ register: new/idempotent-name/idempotent-repo/with-optional → ◇ deregister: existing/nonexistent/empty → ◇ list: empty/multiple/missing-file → ⎋ LDD IMP:9 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/project_registry.py — register_project(), deregister_project(), list_projects()
## @scope    Tests all CLI-level functions with tmp_path fixtures, using subprocess for sys.exit isolation
## @invariants
##   - All YAML files created via tmp_path (no hardcoded paths)
##   - Tests that trigger sys.exit(0) use subprocess to avoid terminating the test runner
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
## @changes  2026-07-25 · DevPlan 070 — Created
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
import sys
from pathlib import Path

# Load the LDD trajectory decorator
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Path to shared module under test ──
_SHARED_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "shared"
sys.path.insert(0, str(_SHARED_DIR))


# ═══════════════════════════════════════════════════════════════════
# region Helpers
# ═══════════════════════════════════════════════════════════════════


def _run_via_subprocess(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    """Run project_registry.py CLI via subprocess (for sys.exit isolation).

    Since register/deregister/list call sys.exit(), tests that verify
    behavior after sys.exit must invoke via subprocess.
    """
    script = _SHARED_DIR / "project_registry.py"
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )


def _create_node_yaml(tmp_path: Path, content: str) -> str:
    """Create a node.yaml file and return its path string."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text(content)
    return str(yaml_file)


# endregion Helpers


# ═══════════════════════════════════════════════════════════════════
# region Tests: register_project
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · register new project in empty node.yaml
# · Scenario: Empty node.yaml → register_project adds entry to projects[]
# · Last fail: N/A (new test)
# · Remove if: register_project logic changes
@ldd_trajectory
def test_register_new_project(caplog, tmp_path):
    """register_project should add a new project to empty node.yaml.

    ## @purpose  Verify that registration writes a new project entry with
    ##           all provided fields into the YAML projects array.
    """
    yaml_path = _create_node_yaml(tmp_path, "domain: example.com\n")

    # Must use subprocess because register_project calls sys.exit(0)
    result = _run_via_subprocess(
        [
            "register",
            "--name",
            "myproject",
            "--repo",
            "org/myproject",
            "--type",
            "backend",
            "--node-yaml",
            yaml_path,
            "--log-prefix",
            "test",
        ],
        tmp_path,
    )

    # Verify exit code and stderr message
    assert result.returncode == 0
    assert "[IMP:9][test][register] Registered myproject" in result.stderr

    # Verify the YAML was updated
    yaml_content = Path(yaml_path).read_text()
    assert "myproject" in yaml_content
    assert "org/myproject" in yaml_content
    assert "backend" in yaml_content

    logger.critical(
        "[IMP:9][test] register_new_project: entry added with name=%s repo=%s — OK", "myproject", "org/myproject"
    )


# 🧪 TRAP[TEST] · Regression · register idempotent by name
# · Scenario: Same name registered twice → second call skips
# · Last fail: N/A (new test)
# · Remove if: idempotency logic changes
@ldd_trajectory
def test_register_idempotent_by_name(caplog, tmp_path):
    """register_project should skip when same name already exists.

    ## @purpose  Verify idempotency by name: second registration with the
    ##           same name should produce SKIP and exit(0).
    """
    yaml_path = _create_node_yaml(
        tmp_path, "projects:\n  - name: myproject\n    repo: org/myproject\n    type: backend\n"
    )

    result = _run_via_subprocess(
        [
            "register",
            "--name",
            "myproject",
            "--repo",
            "org/other",
            "--type",
            "frontend",
            "--node-yaml",
            yaml_path,
            "--log-prefix",
            "test",
        ],
        tmp_path,
    )

    assert result.returncode == 0
    assert "[IMP:9][test][register] Idempotent SKIP — myproject already in node.yaml" in result.stderr

    logger.critical("[IMP:9][test] register_idempotent_by_name: SKIP on duplicate name — OK")


# 🧪 TRAP[TEST] · Regression · register idempotent by repo
# · Scenario: Same repo registered twice → second call skips
# · Last fail: N/A (new test)
# · Remove if: idempotency logic changes
@ldd_trajectory
def test_register_idempotent_by_repo(caplog, tmp_path):
    """register_project should skip when same repo already exists.

    ## @purpose  Verify idempotency by repo: second registration with the
    ##           same repo URL should produce SKIP and exit(0).
    """
    yaml_path = _create_node_yaml(
        tmp_path, "projects:\n  - name: existing\n    repo: org/myproject\n    type: backend\n"
    )

    result = _run_via_subprocess(
        [
            "register",
            "--name",
            "newproject",
            "--repo",
            "org/myproject",
            "--type",
            "frontend",
            "--node-yaml",
            yaml_path,
            "--log-prefix",
            "test",
        ],
        tmp_path,
    )

    assert result.returncode == 0
    assert "[IMP:9][test][register] Idempotent SKIP — newproject already in node.yaml" in result.stderr

    logger.critical("[IMP:9][test] register_idempotent_by_repo: SKIP on duplicate repo — OK")


# 🧪 TRAP[TEST] · Regression · register with domain and database fields
# · Scenario: domain and database included in registration
# · Last fail: N/A (new test)
# · Remove if: optional field handling changes
@ldd_trajectory
def test_register_with_domain_and_database(caplog, tmp_path):
    """register_project should include domain and database when provided.

    ## @purpose  Verify that optional domain and database fields are
    ##           correctly written to the YAML entry.
    """
    yaml_path = _create_node_yaml(tmp_path, "domain: example.com\n")

    result = _run_via_subprocess(
        [
            "register",
            "--name",
            "myproject",
            "--repo",
            "org/myproject",
            "--type",
            "backend",
            "--node-yaml",
            yaml_path,
            "--domain",
            "ex.com",
            "--database",
            "pg",
            "--log-prefix",
            "test",
        ],
        tmp_path,
    )

    assert result.returncode == 0

    # Verify the YAML has the optional fields
    yaml_content = Path(yaml_path).read_text()
    assert "ex.com" in yaml_content
    assert "pg" in yaml_content

    logger.critical("[IMP:9][test] register_with_domain_and_database: domain=%s database=%s — OK", "ex.com", "pg")


# endregion Tests: register_project


# ═══════════════════════════════════════════════════════════════════
# region Tests: deregister_project
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · deregister existing project
# · Scenario: 3 projects → deregister middle → 2 remain, removed=1
# · Last fail: N/A (new test)
# · Remove if: deregister_project logic changes
@ldd_trajectory
def test_deregister_existing(caplog, tmp_path):
    """deregister_project should remove existing project by name.

    ## @purpose  Verify removal of a project from a populated projects list.
    """
    yaml_path = _create_node_yaml(
        tmp_path,
        "projects:\n"
        "  - name: project-a\n    repo: org/a\n    type: backend\n"
        "  - name: project-b\n    repo: org/b\n    type: frontend\n"
        "  - name: project-c\n    repo: org/c\n    type: backend\n",
    )

    result = _run_via_subprocess(
        ["deregister", "--name", "project-b", "--node-yaml", yaml_path, "--log-prefix", "test"],
        tmp_path,
    )

    assert result.returncode == 0
    assert "[IMP:9][test][unregister] Removed 'project-b'" in result.stderr
    assert "(1 entries removed)" in result.stderr

    # Verify YAML content
    yaml_content = Path(yaml_path).read_text()
    assert "project-a" in yaml_content
    assert "project-c" in yaml_content
    assert "project-b" not in yaml_content

    logger.critical("[IMP:9][test] deregister_existing: removed=1 — OK")


# 🧪 TRAP[TEST] · Regression · deregister non-existing project
# · Scenario: Remove name not in list → exit(0), removed=0
# · Last fail: N/A (new test)
# · Remove if: deregister_project logic changes
@ldd_trajectory
def test_deregister_nonexistent(caplog, tmp_path):
    """deregister_project should succeed (exit 0) when removing non-existing project.

    ## @purpose  Verify idempotent behavior: removing a non-existent name
    ##           is not an error — exits 0 with removed=0.
    """
    yaml_path = _create_node_yaml(tmp_path, "projects:\n  - name: project-a\n    repo: org/a\n    type: backend\n")

    result = _run_via_subprocess(
        ["deregister", "--name", "nonexistent", "--node-yaml", yaml_path, "--log-prefix", "test"],
        tmp_path,
    )

    assert result.returncode == 0
    assert "(0 entries removed)" in result.stderr

    logger.critical("[IMP:9][test] deregister_nonexistent: removed=0 — OK")


# 🧪 TRAP[TEST] · Regression · deregister when projects key missing
# · Scenario: node.yaml has no `projects` key → exit(0), no error
# · Last fail: N/A (new test)
# · Remove if: deregister_project logic changes
@ldd_trajectory
def test_deregister_empty_projects(caplog, tmp_path):
    """deregister_project should exit 0 when node.yaml has no projects key.

    ## @purpose  Verify behavior when the YAML has no projects section at all.
    """
    yaml_path = _create_node_yaml(tmp_path, "domain: example.com\n")

    result = _run_via_subprocess(
        ["deregister", "--name", "myproject", "--node-yaml", yaml_path, "--log-prefix", "test"],
        tmp_path,
    )

    assert result.returncode == 0
    assert "[IMP:8][test][unregister] No projects section" in result.stderr

    logger.critical("[IMP:9][test] deregister_empty_projects: no projects section — OK")


# endregion Tests: deregister_project


# ═══════════════════════════════════════════════════════════════════
# region Tests: list_projects
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · list projects when empty
# · Scenario: node.yaml has no projects key → exit 0, no stdout
# · Last fail: N/A (new test)
# · Remove if: list_projects logic changes
@ldd_trajectory
def test_list_projects_empty(caplog, tmp_path):
    """list_projects should exit 0 with no stdout when projects empty.

    ## @purpose  Verify that listing an empty projects list produces
    ##           no stdout output and exits 0.
    """
    yaml_path = _create_node_yaml(tmp_path, "domain: example.com\n")

    result = _run_via_subprocess(
        ["list", "--node-yaml", yaml_path, "--log-prefix", "test"],
        tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == ""  # No stdout output
    assert "[IMP:9][test][list] Listed 0 project(s)" in result.stderr

    logger.critical("[IMP:9][test] list_projects_empty: 0 projects — OK")


# 🧪 TRAP[TEST] · Regression · list multiple projects
# · Scenario: 3 projects in node.yaml → 3 stdout lines with "name repo type domain" format
# · Last fail: N/A (new test)
# · Remove if: list_projects logic changes
@ldd_trajectory
def test_list_projects_multiple(caplog, tmp_path):
    """list_projects should output all projects in 'name repo type domain' format.

    ## @purpose  Verify the CLI-consumable output format with 3 projects.
    """
    yaml_path = _create_node_yaml(
        tmp_path,
        "projects:\n"
        "  - name: proj-a\n    repo: org/a\n    type: backend\n    domain: a.com\n"
        "  - name: proj-b\n    repo: org/b\n    type: frontend\n    domain: b.com\n"
        "  - name: proj-c\n    repo: org/c\n    type: backend\n    domain: c.com\n",
    )

    result = _run_via_subprocess(
        ["list", "--node-yaml", yaml_path, "--log-prefix", "test"],
        tmp_path,
    )

    assert result.returncode == 0
    lines = result.stdout.strip().split("\n")
    assert len(lines) == 3
    assert lines[0] == "proj-a org/a backend a.com"
    assert lines[1] == "proj-b org/b frontend b.com"
    assert lines[2] == "proj-c org/c backend c.com"
    assert "[IMP:9][test][list] Listed 3 project(s)" in result.stderr

    logger.critical("[IMP:9][test] list_projects_multiple: 3 projects listed — OK")


# 🧪 TRAP[TEST] · Regression · list projects when file missing
# · Scenario: path to nonexistent file → exit 1, error to stderr
# · Last fail: N/A (new test)
# · Remove if: list_projects error handling changes
@ldd_trajectory
def test_list_projects_missing_file(caplog, tmp_path):
    """list_projects should exit 1 with error when file is missing.

    ## @purpose  Verify error handling: missing node.yaml path returns
    ##           exit code 1 and error message to stderr.
    """
    missing = str(tmp_path / "nonexistent.yaml")

    result = _run_via_subprocess(
        ["list", "--node-yaml", missing, "--log-prefix", "test"],
        tmp_path,
    )

    assert result.returncode == 1
    assert "[IMP:8][test][list] Failed to read" in result.stderr

    logger.critical("[IMP:9][test] list_projects_missing_file: exit=1 — OK")


# endregion Tests: list_projects
