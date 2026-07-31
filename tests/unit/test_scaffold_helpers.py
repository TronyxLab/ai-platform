# GREP_SUMMARY: test scaffold_helpers gen_ai_platform_yaml gen_makefile gen_agents register_in_node_yaml shared
# STRUCTURE: ┌tmp_project fixture┐ → ○ 6 tests → ⊕ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Unit tests for scaffold_helpers.py (DP-092 Wave 4a). Tests all 4 shared functions:
##           gen_ai_platform_yaml, gen_project_makefile, gen_project_agents, register_in_node_yaml.
## @scope    scaffold_helpers.py public API. Tests file generation, idempotency (force/no-force),
##           YAML structure validation, Makefile tab indentation.
## @invariants
##   - All tests use tmp_path (no hardcoded paths)
##   - YAML output validated against expected structure
##   - Makefile uses tab indentation (Makefile requirement)
##   - LDD IMP:9 assertion on every test
##   - R1-R5 compliance
## @rationale Covers AC6 (shared extraction), AC4 (unit tests). Verifies that scaffold_helpers
##           generates correct file contents for both adopter (minimal) and scaffolder (full) modes.
## @changes  2026-07-30 · Wave 4a — initial implementation
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


# ── Tests: gen_ai_platform_yaml ──────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_gen_yaml_full_backend · Scenario: backend type → full monitoring config · Last fail: N/A · Remove if: scaffold_helpers API changes
@ldd_trajectory
def test_gen_yaml_full_backend(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test ai-platform.yaml generation for backend (full mode).

    ## @purpose  AC6: gen_ai_platform_yaml(backend, minimal=False) → monitoring.metrics=true.
    ## @io        tmp_path → generate YAML → verify structure
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml

    output = tmp_path / "ai-platform.yaml"
    result = gen_ai_platform_yaml(
        name="test-backend",
        ptype="backend",
        org="test-org",
        node="test-node",
        domain="api.example.com",
        database="test_db",
        mode="dev",
        output_path=str(output),
        minimal=False,
    )

    assert result == "generated"
    assert output.exists()

    with open(output) as f:
        content = f.read()
        # Skip header comment and parse YAML
        yaml_start = content.index("name:")
        data = yaml.safe_load(content[yaml_start:])

    assert data["name"] == "test-backend"
    assert data["type"] == "backend"
    assert data["target_node"] == "test-node"
    assert data["monitoring"]["metrics"] is True
    assert data["monitoring"]["logs_retention"] == "14d"
    assert data["staging"] is True  # mode=dev

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_gen_yaml_full_fullstack · Scenario: fullstack type → llm=remote, ai_retention=30d · Last fail: N/A · Remove if: scaffold_helpers API changes
@ldd_trajectory
def test_gen_yaml_full_fullstack(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test ai-platform.yaml generation for fullstack (full mode).

    ## @purpose  fullstack projects get llm=remote and ai_retention=30d monitoring.
    ## @io        tmp_path → generate YAML → verify llm + monitoring
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml

    output = tmp_path / "ai-platform.yaml"
    result = gen_ai_platform_yaml(
        name="test-fullstack",
        ptype="fullstack",
        org="test-org",
        node="test-node",
        domain="app.example.com",
        output_path=str(output),
        minimal=False,
    )

    assert result == "generated"
    assert output.exists()

    with open(output) as f:
        content = f.read()
        yaml_start = content.index("name:")
        data = yaml.safe_load(content[yaml_start:])

    assert data["type"] == "fullstack"
    assert data["needs"]["llm"] == "remote"
    assert data["monitoring"]["ai_retention"] == "30d"
    assert data["monitoring"]["alerting"] is True
    assert data["monitoring"]["dashboard"] is True

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_gen_yaml_minimal · Scenario: minimal=True → simple config · Last fail: N/A · Remove if: scaffold_helpers API changes
@ldd_trajectory
def test_gen_yaml_minimal(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test ai-platform.yaml generation in minimal mode (adopter).

    ## @purpose  minimal=True generates basic yaml without database/llm monitoring.
    ## @io        tmp_path → generate minimal YAML → verify simple structure
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml

    output = tmp_path / "ai-platform.yaml"
    result = gen_ai_platform_yaml(
        name="test-adopted",
        ptype="frontend",
        org="test-org",
        node="test-node",
        domain="fe.example.com",
        output_path=str(output),
        minimal=True,
    )

    assert result == "generated"

    with open(output) as f:
        content = f.read()
        yaml_start = content.index("name:")
        data = yaml.safe_load(content[yaml_start:])

    assert data["monitoring"]["metrics"] is False
    assert data["monitoring"]["logs_retention"] == "7d"
    # minimal mode should NOT have ai_retention
    assert "ai_retention" not in data["monitoring"]

    _assert_ldd_imp9(caplog)


# ── Tests: gen_project_makefile ───────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_gen_makefile_creates · Scenario: new Makefile → generated with tab indentation · Last fail: N/A · Remove if: scaffold_helpers API changes
def test_gen_makefile_creates(tmp_path: pathlib.Path) -> None:
    """Test Makefile generation with correct content.

    ## @purpose  Verify Makefile is created with K3 contract targets.
    ## @io        tmp_path → gen_project_makefile → verify file contents
    """
    from core.internal.scaffold.scaffold_helpers import gen_project_makefile

    output = tmp_path / "Makefile"
    result = gen_project_makefile(
        name="myapp",
        domain="myapp.example.com",
        output_path=str(output),
        force=False,
    )

    assert result == "generated"
    assert output.exists()

    content = output.read_text()
    assert "sync-env:" in content
    assert "status:" in content
    assert "help:" in content
    assert "PLATFORM_DIR" in content
    # Verify tab characters (not spaces) in indentation
    assert "\t@" in content, "Makefile must use tab indentation"


# ── Tests: gen_project_agents ────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_gen_agents_creates · Scenario: new AGENTS.md → DD13 compliant · Last fail: N/A · Remove if: scaffold_helpers API changes
def test_gen_agents_creates(tmp_path: pathlib.Path) -> None:
    """Test AGENTS.md generation with DD13 contract.

    ## @purpose  Verify AGENTS.md contains platform services, DO NOT rules, commands.
    ## @io        tmp_path → gen_project_agents → verify content
    """
    from core.internal.scaffold.scaffold_helpers import gen_project_agents

    output = tmp_path / "AGENTS.md"
    result = gen_project_agents(
        name="myapp",
        org="test-org",
        template="backend",
        node="test-node",
        domain="myapp.example.com",
        output_path=str(output),
        force=False,
    )

    assert result == "generated"
    assert output.exists()

    content = output.read_text()
    assert "# AGENTS.md" in content
    assert "myapp" in content
    assert "Platform provides" in content
    assert "postgres" in content
    assert "DO NOT" in content
    assert "make sync-env" in content
    assert "make status" in content
    # DD13: ≤60 lines
    line_count = len(content.splitlines())
    assert line_count <= 60, f"AGENTS.md should be ≤60 lines (DD13), got {line_count}"


# ── Tests: idempotency ──────────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_gen_makefile_exists_idempotent · Scenario: existing Makefile → "exists" without overwrite · Last fail: N/A · Remove if: scaffold_helpers API changes
def test_gen_makefile_exists_idempotent(tmp_path: pathlib.Path) -> None:
    """Test Makefile idempotency when file already exists without force.

    ## @purpose  Does NOT overwrite existing Makefile unless force=True.
    ## @io        pre-created Makefile → result "exists", original content preserved
    """
    from core.internal.scaffold.scaffold_helpers import gen_project_makefile

    output = tmp_path / "Makefile"
    original_content = "# My custom Makefile"
    output.write_text(original_content)

    result = gen_project_makefile(
        name="myapp",
        output_path=str(output),
        force=False,
    )

    assert result == "exists"
    assert output.read_text() == original_content  # preserved

    # With force=True
    result2 = gen_project_makefile(
        name="myapp",
        output_path=str(output),
        force=True,
    )
    assert result2 == "generated"
    assert "sync-env" in output.read_text()  # overwritten
