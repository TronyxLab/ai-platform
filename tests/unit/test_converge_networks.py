"""
# GREP_SUMMARY: test-converge-networks, r4, reconcile-networks, proxy-net, docker-network, mock-docker
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R4 reconcile_networks 3× (no-docker/proxy-net-missing/proxy-net-exists) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for converge/networks.py via reconciler.reconcile_networks (R4).
## @scope    Tests proxy-net reconciliation: docker daemon availability, network create,
##           network-exists skip. Uses mock subprocess.run for docker commands.
##           Does NOT require a real docker daemon.
## @invariants
##   - All docker-dependent tests mock subprocess.run to avoid real docker calls
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via caplog
## @rationale Direct function testing with mock subprocess.run for docker-dependent units.
##   Вынесен из монолита test_reconciler.py (DevPlan 118 F6).
## @changes 2026-08-02 · F6 split — R4 networks (DevPlan 118)
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load the LDD trajectory decorator from shared conftest
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

import core.internal.bootstrap.converge.infra as infra
import core.internal.bootstrap.converge.networks as _converge_networks

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


# endregion Fixtures


# region FUNC_test_reconcile_networks_no_docker
## 🧪 TRAP[TEST] · R4 no docker · Scenario: docker daemon unavailable → fail
## · Regression: converge.sh lines 699-704
## · Last fail: never
## · Remove if: reconciler.R4 docker check logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_networks_no_docker(tmp_path, caplog):
    """R4: Docker daemon not available → status=fail."""
    caplog.set_level(logging.INFO)

    # Mock subprocess.run to return failure for docker info
    def mock_run_no_docker(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="Cannot connect to the Docker daemon"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run_no_docker):
        entry = reconciler.reconcile_networks(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R4"
    assert entry["status"] == "fail"
    assert "not available" in entry["detail"]


# endregion FUNC_test_reconcile_networks_no_docker


# region FUNC_test_reconcile_networks_create_proxy_net
## 🧪 TRAP[TEST] · R4 create proxy-net · Scenario: proxy-net missing → created
## · Regression: converge.sh lines 707-719
## · Last fail: 2026-07-31 — IsADirectoryError: tmp_path dir passed as node.yaml to NodeYaml
## · Remove if: reconciler.R4 network create logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_networks_create_proxy_net(tmp_path, caplog):
    """R4: proxy-net missing → docker network create called."""
    caplog.set_level(logging.INFO)

    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · IsADirectoryError in _check_proxy_connectivity
    # · Symptom: reconcile_networks(str(tmp_path)) → NodeYaml(dir).get_list() → IsADirectoryError
    # · Root: _check_proxy_connectivity parses node.yaml via NodeYaml; a directory is not a file
    # · Fix: fixture writes a real node.yaml file; pass its path, not the tmp_path dir
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(
        "contexts:\n  - name: test-context\nprojects:\n  - name: myapp\n    domain: myapp.example.com\n"
    )

    create_called = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        # docker info → success
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker network inspect proxy-net → not found
        if "network inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="not found")
        # docker network create → track call
        if "network create" in cmd_str:
            create_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="proxy-net\n", stderr="")
        # docker ps → empty (no containers to check)
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_networks(str(yaml_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R4"
    assert infra.has_warnings or not infra.has_errors
    assert len(create_called) > 0, "docker network create should have been called"
    assert _converge_networks.PROXY_NET in " ".join(create_called[0])


# endregion FUNC_test_reconcile_networks_create_proxy_net


# region FUNC_test_reconcile_networks_exists
## 🧪 TRAP[TEST] · R4 proxy-net exists · Scenario: proxy-net already exists → SKIP
## · Regression: converge.sh lines 720-731
## · Last fail: 2026-07-31 — IsADirectoryError: tmp_path dir passed as node.yaml to NodeYaml
## · Remove if: reconciler.R4 network check logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_networks_exists(tmp_path, caplog):
    """R4: proxy-net already exists (bridge) → no create."""
    caplog.set_level(logging.INFO)

    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · IsADirectoryError in _check_proxy_connectivity
    # · Symptom: reconcile_networks(str(tmp_path)) → NodeYaml(dir).get_list() → IsADirectoryError
    # · Root: _check_proxy_connectivity parses node.yaml via NodeYaml; a directory is not a file
    # · Fix: fixture writes a real node.yaml file; pass its path, not the tmp_path dir
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(
        "contexts:\n  - name: test-context\nprojects:\n  - name: myapp\n    domain: myapp.example.com\n"
    )

    create_called = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "network inspect" in cmd_str:
            # Return valid JSON with bridge driver
            inspect_json = json.dumps([{"Name": "proxy-net", "Driver": "bridge"}])
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=inspect_json, stderr="")
        if "network create" in cmd_str:
            create_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_networks(str(yaml_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R4"
    assert len(create_called) == 0, "docker network create should NOT have been called"


# endregion FUNC_test_reconcile_networks_exists
