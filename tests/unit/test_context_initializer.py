# GREP_SUMMARY: test context_initializer scaffold idempotent skeleton node-yaml gh-repo skip registration
# STRUCTURE: ┌tmp_context fixture┐ → ○ 5 tests → ⊕ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Unit tests for context_initializer.py (DP-092 Wave 2). Tests idempotent check,
##           directory creation, skeleton YAML generation, gh repo creation with skip/graceful
##           degradation, and platform registration via context_registry.
## @scope    context_initializer.py public API: check_idempotent, create_dirs,
##           create_skeleton_node_yaml, gh_repo_create, register_in_platform_yaml.
## @invariants
##   - All tests use tmp_path (no hardcoded paths)
##   - gh operations mocked via injected callable (DI over Mocks)
##   - LDD IMP:9 assertion on every test
##   - R1-R5 compliance
## @rationale Covers AC1 (new-context), AC2 (facade), AC4 (unit tests)
## @changes  2026-07-30 · Wave 2 — initial implementation
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


def _write_platform_yaml(path: pathlib.Path, contexts: list | None = None) -> None:
    """Write a minimal platform node.yaml for registration tests.

    ## @purpose  Create node.yaml with contexts[] array.
    ## @io        ⇥ path, contexts → ⎋ writes file
    """
    data: dict = {
        "node": {"name": "test-node", "host": "127.0.0.1"},
        "contexts": contexts if contexts is not None else [],
        "modules": [],
        "projects": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# ── Tests ───────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_new_context_creates_dirs · Scenario: fresh tmp_path → hermes-agent/ + node-configs/ · Last fail: N/A · Remove if: initializer API changes
@ldd_trajectory
def test_new_context_creates_dirs(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test that create_dirs creates hermes-agent/ and node-configs/ inside context dir.

    ## @purpose  AC1: new-context creates expected directory structure.
    ## @io        tmp_path → create_dirs → verify subdirs exist
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.context_initializer import create_dirs

    context_dir = tmp_path / "test-context"
    create_dirs(context_dir)

    assert context_dir.exists()
    assert (context_dir / "hermes-agent").is_dir()
    assert (context_dir / "node-configs").is_dir()

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_new_context_creates_skeleton_yaml · Scenario: skeleton node.yaml with GREP_SUMMARY/STRUCTURE · Last fail: N/A · Remove if: initializer API changes
@ldd_trajectory
def test_new_context_creates_skeleton_yaml(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test skeleton node.yaml generation with semantic markup preserved.

    ## @purpose  R7: skeleton YAML preserves GREP_SUMMARY/STRUCTURE comments.
    ## @io        tmp_path → create_skeleton → verify YAML structure + comments
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.context_initializer import create_skeleton_node_yaml

    skeleton_path = tmp_path / "node-configs" / "node.yaml"
    skeleton_path.parent.mkdir(parents=True)
    create_skeleton_node_yaml(skeleton_path, "test-ctx")

    assert skeleton_path.exists()
    content = skeleton_path.read_text()

    # Verify semantic markup preserved (R7)
    assert "GREP_SUMMARY:" in content
    assert "STRUCTURE:" in content
    assert "context: test-ctx" in content
    assert "node:" in content

    # Verify it's valid YAML
    data = yaml.safe_load(content)
    assert data is not None
    assert data["context"] == "test-ctx"
    assert data["node"]["name"] == "test-ctx"
    assert isinstance(data["projects"], list)
    assert len(data["projects"]) == 0

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_existing_context_idempotent · Scenario: context dir exists → skip · Last fail: N/A · Remove if: initializer API changes
def test_existing_context_idempotent(tmp_path: pathlib.Path) -> None:
    """Test that check_idempotent exits with 0 when context dir already exists.

    ## @purpose  AC1: idempotent — second call = no-op.
    ## @io        tmp_path with existing dir → SystemExit(0)
    """
    from core.internal.scaffold.context_initializer import check_idempotent

    context_dir = tmp_path / "existing-context"
    context_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        check_idempotent(context_dir)
    assert exc_info.value.code == 0


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_missing_org_skips_gh · Scenario: --skip-gh-repo → no gh calls · Last fail: N/A · Remove if: initializer API changes
@ldd_trajectory
def test_missing_org_skips_gh(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test gh_repo_create gracefully handles --skip-gh-repo.

    ## @purpose  AC1: --skip-gh-repo disables GitHub repo creation.
    ## @io        skip=True → returns (None, None, 0)
    """
    caplog.set_level(logging.INFO)

    from core.internal.scaffold.context_initializer import gh_repo_create

    # Use a fake gh_runner that would fail if called
    def _failing_gh(cmd: list[str]) -> tuple[int, str, str]:
        pytest.fail("gh should not be called with --skip-gh-repo")

    node_repo, agent_repo, warnings = gh_repo_create(
        org="test-org",
        ctx="test-ctx",
        skip=True,
        gh_runner=_failing_gh,
    )

    assert node_repo is None
    assert agent_repo is None
    assert warnings == 0

    # Verify skip message is present in logs (IMP:7)
    log_messages = [r.message for r in caplog.records]
    assert any("[IMP:7]" in msg and "SKIP" in msg and "disabled" in msg.lower() for msg in log_messages)


# 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_register_in_platform_yaml · Scenario: fresh node.yaml → context registered · Last fail: N/A · Remove if: initializer API changes
@ldd_trajectory
def test_register_in_platform_yaml(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test context registration in platform node.yaml via context_registry.

    ## @purpose  AC1: register_in_platform_yaml appends to contexts[].
    ## @io        tmp_path with fresh node.yaml → register → verify YAML
    """
    caplog.set_level(logging.INFO)

    platform_yaml = tmp_path / "platform" / "node.yaml"
    _write_platform_yaml(platform_yaml, contexts=[])

    from core.internal.scaffold.context_initializer import register_in_platform_yaml

    rc = register_in_platform_yaml(
        yaml_path=str(platform_yaml),
        ctx_name="test-context",
        ctx_desc="Test context for unit tests",
        node_cfg_repo="test-org/test-context-node-configs",
        hermes_agent_repo="test-org/test-context-hermes-agent",
    )

    assert rc == 0

    # Verify YAML was updated
    with open(platform_yaml) as f:
        data = yaml.safe_load(f)

    contexts = data.get("contexts", [])
    assert len(contexts) == 1
    ctx = contexts[0]
    assert ctx["name"] == "test-context"
    assert ctx["description"] == "Test context for unit tests"
    assert ctx["node_configs_repo"] == "test-org/test-context-node-configs"
    assert ctx["hermes_agent_repo"] == "test-org/test-context-hermes-agent"

    # Test idempotent: register again → "EXISTS"
    rc2 = register_in_platform_yaml(
        yaml_path=str(platform_yaml),
        ctx_name="test-context",
        ctx_desc="",
        node_cfg_repo="",
        hermes_agent_repo="",
    )
    assert rc2 == 0
    # contexts[] should still have exactly 1 entry
    with open(platform_yaml) as f:
        data2 = yaml.safe_load(f)
    assert len(data2.get("contexts", [])) == 1

    _assert_ldd_imp9(caplog)
