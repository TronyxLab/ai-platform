# GREP_SUMMARY: test overlay deliverer resolve node-yaml extract host sync-core vhost rsync dry-run delivery-error strangler
# STRUCTURE: ▶ ┌resolve_node_yaml (3-path)┐ → ○ extract_node_host (with/without host) → ◇ sync_core_to_vps (dry/fail) → ◇ deliver_vhost_overlays (no/dry/mkdir-fail/mocked) → ⎋ 10 tests
# region MODULE_CONTRACT
## @purpose  Unit tests for overlay_deliverer.py — node resolution, host extraction,
##           core sync, and vhost overlay delivery. 10 tests per DevPlan §TEST_SPEC.
## @scope    All tests use tmp_path fixtures, no Docker/VPS/network required.
##           Mocked subprocess.run for rsync/SSH operations.
## @invariants — All tests use tmp_path — zero hardcoded paths
##              — mock.patch for subprocess.run (no real SSH/rsync calls)
##              — LDD trajectory verified (IMP:9 logs present on success paths)
## @rationale Unit tests for new Python module overlay_deliverer.py. Coverage of
##            resolve/extract/sync-core/deliver — 10 tests ≥80% coverage target.
## @usecases pytest tests/unit/test_overlay_deliverer.py -s -v
# endregion MODULE_CONTRACT

import logging
import pathlib
import subprocess
from unittest import mock

import pytest
import yaml

from core.internal.bootstrap.overlay_deliverer import (
    DeliveryError,
    NodeYamlNotFoundError,
    SyncCoreError,
    build_rsync_ssh_opts,
    deliver_vhost_overlays,
    extract_node_host,
    resolve_node_yaml,
    sync_core_to_vps,
)
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════

# region FIXTURES


@pytest.fixture
def platform_root(tmp_path):
    """Create platform-root/node-configs/test-node/node.yaml with host."""
    node_config = tmp_path / "platform-root" / "node-configs" / "test-node"
    node_config.mkdir(parents=True)
    yaml_path = node_config / "node.yaml"
    with pathlib.Path(yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump({"node": {"host": "1.2.3.4"}}, f)
    return str(tmp_path / "platform-root")


@pytest.fixture
def overlay_dir(tmp_path):
    """Create platform-root with overlays/nginx/*.conf directory."""
    overlay_nginx = tmp_path / "platform-root" / "node-configs" / "test-node" / "overlays" / "nginx"
    overlay_nginx.mkdir(parents=True)
    (overlay_nginx / "test.conf").write_text("server { listen 80; }")
    (overlay_nginx / "other.conf").write_text("server { listen 81; }")
    node_config = tmp_path / "platform-root" / "node-configs" / "test-node"
    yaml_path = node_config / "node.yaml"
    with pathlib.Path(yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump({"node": {"host": "1.2.3.4"}}, f)
    return str(tmp_path / "platform-root")


# endregion FIXTURES


# ═══════════════════════════════════════════════════════════════════
# _build_rsync_ssh_e
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_ssh_e
def test_ssh_e(caplog) -> None:
    """Verify build_rsync_ssh_opts constructs correct -e argument from SSH_OPTS (shared SoT, D1)."""
    caplog.set_level(logging.DEBUG)
    result = build_rsync_ssh_opts()
    assert "ssh" in result
    assert "StrictHostKeyChecking=accept-new" in result
    logger.info("[IMP:9][test_ssh_e][done] SSH -e arg verified: %s", result)
    # 🧪 TRAP[TEST] · Regression: SSH option formatting
    # · Scenario: any change to SSH option formatting
    # · Last fail: N/A (new test)
    # · Remove if: SSH option format changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_ssh_e


# ═══════════════════════════════════════════════════════════════════
# resolve_node_yaml
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_resolve_node_yaml_found
## @purpose  Verify resolve_node_yaml finds node.yaml via path 1 (platform-local).
## @scenario node.yaml at platform_root/node-configs/<node\>/node.yaml
def test_resolve_node_yaml_found(platform_root: str, caplog) -> None:
    """resolve_node_yaml: node.yaml found at platform-local path."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_resolve_node_yaml_found][start] BEGIN")
    result = resolve_node_yaml("test-node", platform_root=platform_root)
    assert result.endswith("node-configs/test-node/node.yaml"), f"Unexpected path: {result}"
    assert pathlib.Path(result).is_file(), f"Path does not exist: {result}"
    logger.info("[IMP:9][test_resolve_node_yaml_found][done] Path resolved: %s", result)
    # 🧪 TRAP[TEST] · Regression: resolve_node_yaml search order change
    # · Scenario: resolve_node_yaml returns wrong path or raises when path exists
    # · Last fail: N/A (new test)
    # · Remove if: resolve_node_yaml signature or search logic changes fundamentally
    assert_ldd_imp9(caplog)


# endregion FUNC_test_resolve_node_yaml_found


# region FUNC_test_resolve_node_yaml_not_found
## @purpose  Verify resolve_node_yaml raises NodeYamlNotFoundError when
##           node.yaml not found in any of 3 search paths.
## @scenario Non-existent node name, no matching paths.
def test_resolve_node_yaml_not_found(tmp_path: str, caplog) -> None:
    """resolve_node_yaml: raises NodeYamlNotFoundError when no node.yaml found."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_resolve_node_yaml_not_found][start] BEGIN")
    platform_root = str(tmp_path / "empty-root")
    pathlib.Path(platform_root).mkdir(parents=True)
    with pytest.raises(NodeYamlNotFoundError, match=r"node\.yaml not found"):
        resolve_node_yaml("nonexistent", platform_root=platform_root, projects_dir=str(tmp_path / "projects"))
    logger.info("[IMP:9][test_resolve_node_yaml_not_found][done] NodeYamlNotFoundError raised as expected")
    # 🧪 TRAP[TEST] · Regression: resolve_node_yaml fails silently instead of raising
    # · Scenario: non-existent node should raise, not return None or empty
    # · Last fail: N/A (new test)
    # · Remove if: error handling strategy changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_resolve_node_yaml_not_found


# ═══════════════════════════════════════════════════════════════════
# extract_node_host
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_extract_node_host
## @purpose  Verify extract_node_host returns host from node.yaml node.host field; "" when absent.
## @scenario node.yaml contains `node: {host: "1.2.3.4"}` → "1.2.3.4"; no host field → ""
# 🧪 TRAP[TEST] · Regression: extract_node_host parsing change
# · Scenario: node.host field parsed incorrectly; absent host → "" (not None/error)
# · Last fail: N/A (new test)
# · Remove if: YAML structure changes fundamentally
@pytest.mark.parametrize(
    ("host_value", "expected"),
    [
        pytest.param("1.2.3.4", "1.2.3.4", id="with-host"),
        pytest.param(None, "", id="no-host"),
    ],
)
def test_extract_node_host(tmp_path: str, caplog, host_value: str | None, expected: str) -> None:
    """extract_node_host: returns host when node.host is set in node.yaml, '' when absent."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_extract_node_host][start] BEGIN")
    node_config = pathlib.Path(tmp_path) / "platform-root" / "node-configs" / "test-node"
    node_config.mkdir(parents=True)
    yaml_path = node_config / "node.yaml"
    node_data = {"node": {"host": host_value}} if host_value is not None else {"node": {}}
    with pathlib.Path(yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump(node_data, f)
    host = extract_node_host(yaml_path)
    assert host == expected, f"Expected {expected!r}, got {host!r}"
    logger.info("[IMP:9][test_extract_node_host][done] Host extracted: %s", host)
    assert_ldd_imp9(caplog)


# endregion FUNC_test_extract_node_host


# ═══════════════════════════════════════════════════════════════════
# sync_core_to_vps
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_sync_core_dry_run
## @purpose  Verify sync_core_to_vps dry-run mode prints commands and returns True.
## @scenario dry_run=True, no subprocess execution.
def test_sync_core_dry_run(tmp_path: str, caplog) -> None:
    """sync_core_to_vps: dry-run mode prints commands, returns True without executing."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_sync_core_dry_run][start] BEGIN")
    core_src = str(tmp_path / "core")
    pathlib.Path(core_src).mkdir(parents=True)
    # Create a dummy file in core_src to make os.path.isdir pass
    (tmp_path / "core" / "test.py").write_text("# test")
    result = sync_core_to_vps(
        host="1.2.3.4",
        core_src=core_src,
        node_name="test-node",
        node_yaml=str(tmp_path / "nonexistent.yaml"),
        dry_run=True,
    )
    assert result is True, "dry-run should return True"
    logger.info("[IMP:9][test_sync_core_dry_run][done] Dry-run returned True")
    # 🧪 TRAP[TEST] · Regression: dry-run mode starts executing commands
    # · Scenario: dry-run should NOT run rsync/ssh
    # · Last fail: N/A (new test)
    # · Remove if: dry-run mode is removed
    assert_ldd_imp9(caplog)


# endregion FUNC_test_sync_core_dry_run


# region FUNC_test_sync_core_rsync_failure
## @purpose  Verify sync_core_to_vps raises SyncCoreError when rsync fails.
## @scenario Mocked subprocess.run returns non-zero exit code.
def test_sync_core_rsync_failure(tmp_path: str, caplog) -> None:
    """sync_core_to_vps: raises SyncCoreError when rsync fails."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_sync_core_rsync_failure][start] BEGIN")
    core_src = str(tmp_path / "core")
    pathlib.Path(core_src).mkdir(parents=True)
    (tmp_path / "core" / "test.py").write_text("# test")

    with (
        mock.patch.object(subprocess, "run", return_value=mock.MagicMock(returncode=1, stderr="rsync error")),
        pytest.raises(SyncCoreError, match="rsync core/ failed"),
    ):
        sync_core_to_vps(host="1.2.3.4", core_src=core_src, dry_run=False)
    logger.info("[IMP:9][test_sync_core_rsync_failure][done] SyncCoreError raised as expected")
    # 🧪 TRAP[TEST] · Regression: rsync failure silently ignored
    # · Scenario: failed rsync should raise, not return False
    # · Last fail: N/A (new test)
    # · Remove if: error handling strategy changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_sync_core_rsync_failure


# ═══════════════════════════════════════════════════════════════════
# deliver_vhost_overlays
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_no_overlays
## @purpose  Verify deliver_vhost_overlays gracefully skips when no overlays directory exists.
## @scenario platform_root without overlays/nginx/ directory.
# GUARD-PRESERVE (168): единственное покрытие graceful-skip ветки deliver_vhost_overlays (нет overlays dir)
def test_deliver_no_overlays(platform_root: str, caplog) -> None:
    """deliver_vhost_overlays: graceful skip when no overlays dir."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_no_overlays][start] BEGIN")
    result = deliver_vhost_overlays("test-node", platform_root=platform_root)
    assert result is True, "Should return True (graceful skip)"
    logger.info("[IMP:9][test_deliver_no_overlays][done] Graceful skip returned True")
    # 🧪 TRAP[TEST] · Regression: no overlays dir should be graceful, not fail
    # · Scenario: missing overlays dir should not raise an error
    # · Last fail: N/A (new test)
    # · Remove if: overlay delivery strategy changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_no_overlays


# region FUNC_test_deliver_vhost_overlays_success
## @purpose  Verify deliver_vhost_overlays success paths: dry-run печатает команды (0 exec) OR
##           полный pipeline с mocked subprocess (mkdir + rsync).
## @scenario dry_run=True с overlays → True без exec; dry_run=False + mock subprocess → True
# 🧪 TRAP[TEST] · Regression: dry-run starts executing commands / full delivery pipeline fails
# · Scenario: dry-run should NOT run rsync/ssh on real VPS; mkdir + rsync succeed (mocked subprocess)
# · Last fail: N/A (new test)
# · Remove if: dry-run mode removed / delivery pipeline architecture changes
@pytest.mark.parametrize(
    ("dry_run", "mock_exec"),
    [
        pytest.param(True, False, id="dry-run"),
        pytest.param(False, True, id="mocked-exec"),
    ],
)
def test_deliver_vhost_overlays_success(overlay_dir: str, caplog, dry_run: bool, mock_exec: bool) -> None:
    """deliver_vhost_overlays: dry-run печатает команды (0 subprocess) / полный pipeline (mock)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_vhost_overlays_success][start] BEGIN")
    if mock_exec:
        with mock.patch.object(subprocess, "run", return_value=mock.MagicMock(returncode=0, stdout="", stderr="")):
            result = deliver_vhost_overlays("test-node", platform_root=overlay_dir, dry_run=False)
    else:
        result = deliver_vhost_overlays("test-node", platform_root=overlay_dir, dry_run=True)
    assert result is True, "Success path should return True"
    logger.info("[IMP:9][test_deliver_vhost_overlays_success][done] dry_run=%s → True", dry_run)
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_vhost_overlays_success


# region FUNC_test_deliver_mkdir_failure
## @purpose  Verify deliver_vhost_overlays raises DeliveryError when mkdir fails.
## @scenario Mocked subprocess.run for mkdir returns non-zero exit code.
def test_deliver_mkdir_failure(overlay_dir: str, caplog) -> None:
    """deliver_vhost_overlays: raises DeliveryError when mkdir fails."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_mkdir_failure][start] BEGIN")

    # First mock call (mkdir) fails, second (rsync) would not be reached
    mock_fail = mock.MagicMock(returncode=1, stderr="mkdir: cannot create directory")
    with (
        mock.patch.object(subprocess, "run", return_value=mock_fail),
        pytest.raises(DeliveryError, match="mkdir failed on"),
    ):
        deliver_vhost_overlays("test-node", platform_root=overlay_dir, dry_run=False)
    logger.info("[IMP:9][test_deliver_mkdir_failure][done] DeliveryError raised as expected")
    # 🧪 TRAP[TEST] · Regression: mkdir failure silently ignored
    # · Scenario: failed mkdir should raise, not silently skip rsync
    # · Last fail: N/A (new test)
    # · Remove if: error handling strategy changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_mkdir_failure


# ═══════════════════════════════════════════════════════════════════
# HELPER
# ═══════════════════════════════════════════════════════════════════


# T2.16a: _assert_imp9 консолидирован в gate_helpers.assert_ldd_imp9
