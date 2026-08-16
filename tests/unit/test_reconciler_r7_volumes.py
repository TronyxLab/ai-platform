"""
# GREP_SUMMARY: test-reconciler, r7-volumes, reconcile-volumes, detect-only, docker-volumes, named-volumes, compose-config
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R7 reconcile_volumes 4× (no-docker/exist/missing/bind-mount) → ⊕ status assert → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for R7 reconcile_volumes in reconciler.py — detect-only volume reconciliation
## @scope    Tests docker volume detection for named volumes from compose config per node.yaml modules
## @invariants
##   - All docker-dependent tests mock subprocess.run to avoid real docker calls
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via LDD trajectory
##   - O7 invariant: NEVER create volumes, detect-only
## @rationale Direct function testing with mock subprocess.run for docker compose config/volume inspect
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

from core.internal.bootstrap.converge import infra

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    infra.reset_state()
    infra.node_name = "test-node"
    infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")


@pytest.fixture
def node_yaml_with_modules(tmp_path):
    """Create a node.yaml with enabled docker modules."""
    yaml_content = """
context: test-context
modules:
  - name: postgres
    enabled: true
  - name: redis
    enabled: true
  - name: nginx
    enabled: true
projects: []
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    return str(yaml_path)


@pytest.fixture
def compose_config_json():
    """Return a mock docker compose config --format json output with named volumes."""
    config = {
        "services": {
            "postgres": {
                "volumes": [
                    {"type": "volume", "source": "pgdata", "target": "/var/lib/postgresql/data"},
                    {"type": "bind", "source": "/host/backup", "target": "/backup"},
                ]
            },
            "redis": {
                "volumes": [
                    {"type": "volume", "source": "redisdata", "target": "/data"},
                ]
            },
        },
        "volumes": {
            "pgdata": {"name": "postgres_pgdata"},
            "redisdata": {"name": "redis_redisdata"},
        },
    }
    return json.dumps(config)


@pytest.fixture
def mock_modules_dir(tmp_path):
    """Create mock module directories with docker-compose.yml for docker modules."""
    # Create module dirs under a 'modules' subdirectory
    modules_base = tmp_path / "modules"
    for mod in ("postgres", "redis", "nginx"):
        mod_dir = modules_base / mod
        mod_dir.mkdir(parents=True)
        # postgres and redis have docker-compose.yml; nginx is system module
        if mod != "nginx":
            compose = mod_dir / "docker-compose.yml"
            compose.write_text(f"version: '3'\nservices:\n  {mod}:\n    image: {mod}:latest\n", encoding="utf-8")
            if mod == "postgres":
                # Add volume config
                compose.write_text(
                    f"version: '3'\nservices:\n  {mod}:\n    image: {mod}:latest\n    volumes:\n"
                    f"      - pgdata:/var/lib/postgresql/data\n      - /host/backup:/backup\n"
                    f"volumes:\n  pgdata:\n",
                    encoding="utf-8",
                )
    return str(modules_base)


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# R7 — reconcile_volumes
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_reconcile_volumes_no_docker
## 🧪 TRAP[TEST] · R7 no docker · Scenario: docker daemon unavailable → status=fail
## · Regression: R7 must gracefully handle missing docker daemon (no crash, fail entry)
## · Last fail: never
## · Remove if: reconcile_volumes docker check logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_volumes_no_docker(tmp_path, caplog, node_yaml_with_modules):
    """R7: Docker daemon not available → status=fail."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R7 no-docker edge case — docker info fails")

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="Cannot connect to the Docker daemon"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_volumes(node_yaml_with_modules)

    assert entry["unit"] == "R7"
    assert entry["status"] == "fail"
    assert "not available" in entry["detail"] or "docker" in entry["detail"].lower()


# endregion FUNC_test_reconcile_volumes_no_docker


# region FUNC_test_reconcile_volumes_exist
## 🧪 TRAP[TEST] · R7 volumes exist · Scenario: all named volumes exist → status=converged
## · Regression: R7 detect-only convergence check
## · Last fail: never
## · Remove if: reconcile_volumes detection logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_volumes_exist(tmp_path, caplog, node_yaml_with_modules, compose_config_json, mock_modules_dir):
    """R7: All named volumes exist via docker volume inspect → status=converged."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R7 volumes-exist happy path — all volumes present")

    # Set reconciler core_dir so modules path resolves
    infra.core_dir = str(Path(mock_modules_dir).parent)  # tmp_path which contains modules/

    volume_inspect_calls = []

    volume_inspect_calls = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker compose --project-directory <dir> config --format json
        if "docker compose" in cmd_str and "config --format json" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=compose_config_json, stderr="")
        if "volume inspect" in cmd_str:
            volume_inspect_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='[{"Name": "test"}]', stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_volumes(node_yaml_with_modules)

    assert entry["unit"] == "R7"
    assert entry["status"] == "converged"
    assert len(volume_inspect_calls) > 0, "volume inspect should have been called for named volumes"


# endregion FUNC_test_reconcile_volumes_exist


# region FUNC_test_reconcile_volumes_missing
## 🧪 TRAP[TEST] · R7 volume missing · Scenario: named volume missing → detect-only, status=warn
## · Regression: R7 detect-only — NEVER creates volumes (O7 invariant)
## · Last fail: never
## · Remove if: reconcile_volumes detection logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_volumes_missing(tmp_path, caplog, node_yaml_with_modules, compose_config_json, mock_modules_dir):
    """R7: Named volume missing → detect-only (warn), NO volume create."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R7 volume-missing detect-only — warn, no create")

    infra.core_dir = str(Path(mock_modules_dir).parent)

    volume_create_calls = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker compose" in cmd_str and "config --format json" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=compose_config_json, stderr="")
        if "volume inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="No such volume")
        if "volume create" in cmd_str:
            volume_create_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="test", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_volumes(node_yaml_with_modules)

    assert entry["unit"] == "R7"
    assert entry["status"] == "warn"
    assert len(volume_create_calls) == 0, "R7 is detect-only — MUST NOT create volumes (O7 invariant)"
    assert not infra.has_errors, "Missing volumes should be warning, not error"


# endregion FUNC_test_reconcile_volumes_missing


# region FUNC_test_reconcile_volumes_bind_mount_excluded
## 🧪 TRAP[TEST] · R7 bind-mount excluded · Scenario: bind-mounts NOT checked (O7 invariant)
## · Regression: R7 must exclude bind-mounts, only check named volumes with type:volume or no type
## · Last fail: never
## · Remove if: reconcile_volumes bind-mount filter logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_volumes_bind_mount_excluded(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """R7: Bind-mounts excluded from inspection — only named volumes checked."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R7 bind-mount exclusion — O7 invariant")

    infra.core_dir = str(Path(mock_modules_dir).parent)

    # Build a compose config that has ONLY bind mounts (no named volumes)
    bind_only_config = json.dumps({
        "services": {
            "postgres": {
                "volumes": [
                    {"type": "bind", "source": "/host/data", "target": "/var/lib/postgresql/data"},
                    {"type": "bind", "source": "/host/config", "target": "/etc/postgresql"},
                ]
            },
        },
        "volumes": {},
    })

    all_cmds = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        all_cmds.append(cmd_str)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker compose" in cmd_str and "config --format json" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=bind_only_config, stderr="")
        if "volume inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_volumes(node_yaml_with_modules)

    assert entry["unit"] == "R7"
    # No named volumes → should be converged
    assert entry["status"] in {"converged", "skipped"}

    # Ensure NO volume inspect was called for bind mount sources
    volume_inspect_cmds = [c for c in all_cmds if "volume inspect" in c]
    for cmd_str in volume_inspect_cmds:
        assert "/host/" not in cmd_str, "Bind-mount paths should NOT be inspected"


# endregion FUNC_test_reconcile_volumes_bind_mount_excluded
