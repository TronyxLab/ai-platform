"""
# GREP_SUMMARY: test_context_overlay, ensure_context_repo, s9-cache, git-clone, git-pull, context-overlay
# STRUCTURE: ▶ tmp_path(node.yaml) + mock(subprocess + os.path + time + Path) → ◇ 7 scenarios ∋ (no-context / cached-pull / expired-pull / clone / no-repo / pull-fail / clone-fail) → ⎋ assert exit_code + LDD telemetry
# region MODULE_CONTRACT
## @purpose  Unit tests for context_overlay.ensure_context_repo() — git clone/pull with S9 caching
## @scope    Direct Python import of context_overlay.py; tests all branches of ensure_context_repo()
## @invariants
##   - No context field → SKIP (return 0, no git operation)
##   - Context path exists + cached (<300s) → SKIP (return 0, no git pull)
##   - Context path exists + cache expired (>=300s) → git pull --ff-only (return 0)
##   - Context path absent + repo URL present → git clone (return 0)
##   - Context path absent + no repo URL → WARN (return 0, no git clone)
##   - Git pull failure is non-fatal → WARN (return 0)
##   - Git clone failure → WARN (return 1)
## @rationale Ensures the Strangler-extracted Python module preserves all shell behavior.
##            Uses tmp_path for real YAML file I/O; mocks for git subprocess and filesystem paths.
## @changes
##   2026-07-22 · Created (W4-E1 extraction)
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# Direct import of the Python module under test
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"),
)
from context_overlay import ensure_context_repo

pytestmark = pytest.mark.static_audit

# region FIXTURES
## @purpose  Fixtures: node.yaml variants with/without context and repos.core


@pytest.fixture
def node_yaml_with_context(tmp_path):
    """Create node.yaml with contexts[0].name and `repos.core:`. (clone-branch fixture, DevPlan 116 B6 T1)"""
    path = tmp_path / "node.yaml"
    path.write_text("contexts:\n  - name: testctx\nrepos:\n  core: https://github.com/org/test-context.git\n")
    return str(path)


@pytest.fixture
def node_yaml_with_context_no_repo(tmp_path):
    """Create node.yaml with contexts[0].name but NO `repos.core:`. (no-clone-branch fixture)"""
    path = tmp_path / "node.yaml"
    path.write_text("contexts:\n  - name: testctx\nrepos: {}\n")
    return str(path)


@pytest.fixture
def node_yaml_without_context(tmp_path):
    """Create node.yaml WITHOUT `context:` field. (skip-branch fixture)"""
    path = tmp_path / "node.yaml"
    path.write_text("other_field: value\n")
    return str(path)


# endregion FIXTURES


# region FUNC_test_ensure_no_context
## @purpose  No context field in node.yaml → SKIP (return 0, no git operation)
## @io       node_yaml_without_context → assert 0 + SKIP log
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: No context field → skip · Last fail: N/A · Remove if: ensure_context_repo behavior changed
@ldd_trajectory
def test_ensure_no_context(node_yaml_without_context, caplog):
    """node.yaml without context: should skip immediately with return 0."""
    result = ensure_context_repo(node_yaml_without_context)

    assert result == 0, "Expected 0 when no context field"
    assert "SKIP" in caplog.text or "No context" in caplog.text, "Expected SKIP log message"
    logger.info("[IMP:9][test][no_context] ensure_context_repo returned %d — verified SKIP", result)


# endregion FUNC_test_ensure_no_context


# region FUNC_test_ensure_context_pull_cached
## @purpose  Context path exists + timestamp <300s → SKIP pull (S9 cache hit)
## @io       node_yaml_with_context + mocks → assert 0 + no subprocess.run call
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: Pull cached (<300s) → skip · Last fail: N/A · Remove if: S9 cache logic changed
@ldd_trajectory
@patch("context_overlay.subprocess.run")
@patch("context_overlay.os.path.isdir")
@patch("context_overlay.time.time")
@patch("context_overlay.Path")
def test_ensure_context_pull_cached(
    mock_path_cls,
    mock_time,
    mock_isdir,
    mock_run,
    node_yaml_with_context,
    caplog,
):
    """Existing context path with recent pull (<300s): should skip git pull (S9 cache)."""
    # Setup: context path exists, last pull was 50s ago, now=1000 → elapsed=950
    # CONTEXT_PULL_CACHE_SECONDS is typically 300, so 50 < 300 → cache hit
    mock_isdir.return_value = True
    mock_time.return_value = 1000

    # Mock Path for timestamp file
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "950"  # last pull at epoch 950
    mock_path_cls.return_value = mock_path

    result = ensure_context_repo(node_yaml_with_context)

    assert result == 0, "Expected 0 when pull is cached"
    mock_run.assert_not_called(), "git pull should NOT be called when cached"
    assert "SKIP" in caplog.text or "cache" in caplog.text.lower(), "Expected cache/SKIP log message"
    logger.info("[IMP:9][test][cached] ensure_context_repo returned %d — verified S9 cache hit", result)


# endregion FUNC_test_ensure_context_pull_cached


# region FUNC_test_ensure_context_pull_executed
## @purpose  Context path exists + cache expired (>=300s) → git pull --ff-only
## @io       node_yaml_with_context + mocks → assert 0 + subprocess.run called with git pull
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: Cache expired (>=300s) → git pull · Last fail: N/A · Remove if: _pull_with_cache logic changed
@ldd_trajectory
@patch("context_overlay.subprocess.run")
@patch("context_overlay.os.path.isdir")
@patch("context_overlay.time.time")
@patch("context_overlay.Path")
def test_ensure_context_pull_executed(
    mock_path_cls,
    mock_time,
    mock_isdir,
    mock_run,
    node_yaml_with_context,
    caplog,
):
    """Existing context path with expired cache: should execute git pull --ff-only."""
    # Setup: context path exists, last pull at 0, now=1000 → elapsed=1000 >= 300
    mock_isdir.return_value = True
    mock_time.return_value = 1000

    # Mock Path for timestamp file
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "0"  # last pull at epoch 0 (= expired)
    mock_path_cls.return_value = mock_path

    # Mock successful git pull
    mock_run.return_value = subprocess.CompletedProcess(
        args=["git", "-C", "/opt/testctx/platform", "pull", "--ff-only"],
        returncode=0,
        stdout="Already up to date.",
        stderr="",
    )

    result = ensure_context_repo(node_yaml_with_context)

    assert result == 0, "Expected 0 on successful pull"
    mock_run.assert_called_once()
    # Verify the git command was a pull
    call_args = mock_run.call_args[0][0]
    assert "pull" in call_args, "Expected git pull command"
    assert "--ff-only" in call_args, "Expected --ff-only flag"
    assert "git pull successful" in caplog.text, "Expected success log for git pull"
    logger.info("[IMP:9][test][pull_ok] ensure_context_repo returned %d — verified git pull executed", result)


# endregion FUNC_test_ensure_context_pull_executed


# region FUNC_test_ensure_context_clone
## @purpose  Context path absent + repo URL present → git clone (return 0)
## @io       node_yaml_with_context + mocks → assert 0 + subprocess.run called with git clone
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: Context absent + repo URL → git clone · Last fail: N/A · Remove if: _clone_context_repo logic changed
@ldd_trajectory
@patch("context_overlay.subprocess.run")
@patch("context_overlay.os.path.isdir")
def test_ensure_context_clone(
    mock_isdir,
    mock_run,
    node_yaml_with_context,
    caplog,
):
    """Context path absent, repo URL present: should execute git clone."""
    # Setup: context path does NOT exist
    mock_isdir.return_value = False

    # Mock successful git clone
    mock_run.return_value = subprocess.CompletedProcess(
        args=["git", "clone", "https://github.com/org/test-context.git", "/opt/testctx/platform"],
        returncode=0,
        stdout="Cloning into '/opt/testctx/platform'...",
        stderr="",
    )

    result = ensure_context_repo(node_yaml_with_context)

    assert result == 0, "Expected 0 on successful clone"
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "clone" in call_args, "Expected git clone command"
    assert "https://github.com/org/test-context.git" in call_args, "Expected repo URL in clone command"
    assert "Context repo cloned" in caplog.text, "Expected success log for git clone"
    logger.info("[IMP:9][test][clone] ensure_context_repo returned %d — verified git clone executed", result)


# endregion FUNC_test_ensure_context_clone


# region FUNC_test_ensure_context_no_repo_url
## @purpose  Context path absent + no repos.core → WARN (return 0)
## @io       node_yaml_with_context_no_repo + mocks → assert 0 + WARN log
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: Context absent + no repo URL → WARN · Last fail: N/A · Remove if: _clone_context_repo missing-repo handling changed
@ldd_trajectory
@patch("context_overlay.subprocess.run")
@patch("context_overlay.os.path.isdir")
def test_ensure_context_no_repo_url(
    mock_isdir,
    mock_run,
    node_yaml_with_context_no_repo,
    caplog,
):
    """Context path absent, no repos.core: should WARN and return 0 without cloning."""
    mock_isdir.return_value = False

    result = ensure_context_repo(node_yaml_with_context_no_repo)

    assert result == 0, "Expected 0 when repo URL is missing (no-op warning)"
    mock_run.assert_not_called(), "git clone should NOT be called without repo URL"
    assert "No repos.core" in caplog.text, "Expected WARN about missing repos.core"
    logger.info(
        "[IMP:9][test][no_repo] ensure_context_repo returned %d — verified WARN + no clone",
        result,
    )


# endregion FUNC_test_ensure_context_no_repo_url


# region FUNC_test_ensure_context_pull_fail_nonfatal
## @purpose  Context path exists, pull fails → non-fatal WARN (return 0)
## @io       node_yaml_with_context + mocks → assert 0 + WARN log + timestamp updated
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: git pull fails → non-fatal WARN · Last fail: N/A · Remove if: _pull_with_cache failure handling changed
@ldd_trajectory
@patch("context_overlay.subprocess.run")
@patch("context_overlay.os.path.isdir")
@patch("context_overlay.time.time")
@patch("context_overlay.Path")
def test_ensure_context_pull_fail_nonfatal(
    mock_path_cls,
    mock_time,
    mock_isdir,
    mock_run,
    node_yaml_with_context,
    caplog,
):
    """Existing context path, git pull fails: should WARN (non-fatal) and return 0."""
    mock_isdir.return_value = True
    mock_time.return_value = 1000

    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "0"
    mock_path_cls.return_value = mock_path

    # Mock failed git pull
    mock_run.return_value = subprocess.CompletedProcess(
        args=["git", "-C", "/opt/testctx/platform", "pull", "--ff-only"],
        returncode=1,
        stdout="",
        stderr="error: failed to pull",
    )

    result = ensure_context_repo(node_yaml_with_context)

    assert result == 0, "Expected 0 even when pull fails (non-fatal)"
    mock_run.assert_called_once()
    assert "git pull failed" in caplog.text, "Expected WARN log for failed git pull"
    logger.info(
        "[IMP:9][test][pull_fail] ensure_context_repo returned %d — verified pull failure is non-fatal",
        result,
    )


# endregion FUNC_test_ensure_context_pull_fail_nonfatal


# region FUNC_test_ensure_context_clone_fail
## @purpose  Context path absent, clone fails → WARN (return 1)
## @io       node_yaml_with_context + mocks → assert 1 + WARN log
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: git clone fails → WARN + return 1 · Last fail: N/A · Remove if: _clone_context_repo failure handling changed
@ldd_trajectory
@patch("context_overlay.subprocess.run")
@patch("context_overlay.os.path.isdir")
def test_ensure_context_clone_fail(
    mock_isdir,
    mock_run,
    node_yaml_with_context,
    caplog,
):
    """Context path absent, git clone fails: should WARN and return 1."""
    mock_isdir.return_value = False

    # Mock failed git clone
    mock_run.return_value = subprocess.CompletedProcess(
        args=["git", "clone", "https://github.com/org/test-context.git", "/opt/testctx/platform"],
        returncode=128,
        stdout="",
        stderr="fatal: repository not found",
    )

    result = ensure_context_repo(node_yaml_with_context)

    assert result == 1, "Expected 1 when clone fails"
    mock_run.assert_called_once()
    assert "git clone failed" in caplog.text, "Expected WARN log for failed git clone"
    logger.info(
        "[IMP:9][test][clone_fail] ensure_context_repo returned %d — verified clone failure returns 1",
        result,
    )


# endregion FUNC_test_ensure_context_clone_fail


# region FUNC_test_ensure_context_pull_timestamp_update
## @purpose  After pull (even failed one), timestamp file is updated
## @io       node_yaml_with_context + mocks → assert write_text called with correct timestamp
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: Pull failure still updates timestamp · Last fail: N/A · Remove if: _update_timestamp not called on pull fail
@ldd_trajectory
@patch("context_overlay.subprocess.run")
@patch("context_overlay.os.path.isdir")
@patch("context_overlay.time.time")
@patch("context_overlay.Path")
def test_ensure_context_pull_timestamp_update(
    mock_path_cls,
    mock_time,
    mock_isdir,
    mock_run,
    node_yaml_with_context,
    caplog,
):
    """After pull (even failed), timestamp file should be updated."""
    mock_isdir.return_value = True
    mock_time.return_value = 2000

    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "500"  # old timestamp, expired
    mock_path_cls.return_value = mock_path

    # Mock failed pull (to verify timestamp update happens regardless)
    mock_run.return_value = subprocess.CompletedProcess(
        args=["git", "-C", "/opt/testctx/platform", "pull", "--ff-only"],
        returncode=1,
        stdout="",
        stderr="merge conflict",
    )

    result = ensure_context_repo(node_yaml_with_context)

    assert result == 0, "Expected 0 even on failed pull"
    # Verify write_text was called on the timestamp path mock
    mock_path.write_text.assert_called_once_with("2000")
    logger.info(
        "[IMP:9][test][ts_update] ensure_context_repo returned %d — verified timestamp updated to %d",
        result,
        2000,
    )


# endregion FUNC_test_ensure_context_pull_timestamp_update


# region FUNC_test_ensure_context_invalid_name_negative
## @purpose — R5 negative (M13a, security hardening): context_name с path traversal/инжекцией
##            блокируется ДО построения /opt/{name}/platform и git clone.
# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · M13a — валидация context_name
# · Scenario: node.yaml contexts[0].name="../../etc" → /opt/../../etc/platform = path traversal.
# · Last fail: N/A (new security validation, аудит 2026-08-15 M13a)
# · Remove if: context_name перестаёт валидироваться
@patch("context_overlay.subprocess.run")
def test_ensure_context_invalid_name_negative(mock_run, tmp_path, caplog):
    """R5 negative (M13a): context_name с `../` → return 1, БЕЗ git clone."""
    path = tmp_path / "node.yaml"
    path.write_text("contexts:\n  - name: ../../etc\nrepos:\n  core: https://github.com/org/x.git\n")

    result = ensure_context_repo(str(path))

    assert result == 1, "M13a: невалидный context_name должен вернуть 1"
    mock_run.assert_not_called()


# endregion FUNC_test_ensure_context_invalid_name_negative
