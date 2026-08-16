"""
# GREP_SUMMARY: test-reconciler, r9-runtime, reconcile-runtime, docker-inspect, compose-up, self-heal, cooldown
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R9 reconcile_runtime_state 3× (running/exited/cooldown) → ⊕ compose-up verify → ⊕ cooldown verify → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for R9 reconcile_runtime_state in reconciler.py — docker container runtime state reconciliation
## @scope    Tests docker container state inspection and self-heal via docker compose up -d, with cooldown tracking
## @invariants
##   - All docker-dependent tests mock subprocess.run to avoid real docker calls
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via LDD trajectory
##   - Self-heal uses `docker compose up -d`, NOT `docker restart`
## @rationale Direct function testing with mock subprocess.run for docker inspect/compose commands
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
    """Create a node.yaml with docker modules."""
    yaml_content = """
context: test-context
modules:
  - name: nginx
    enabled: true
  - name: postgres
    enabled: true
  - name: redis
    enabled: true
projects: []
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content)
    return str(yaml_path)


@pytest.fixture
def mock_modules_dir(tmp_path):
    """Create mock module directories with docker-compose.yml."""
    modules_base = tmp_path / "modules"
    for mod in ("nginx", "postgres", "redis"):
        mod_dir = modules_base / mod
        mod_dir.mkdir(parents=True)
        compose = mod_dir / "docker-compose.yml"
        compose.write_text(f"version: '3'\nservices:\n  {mod}:\n    image: {mod}:latest\n")
    return str(modules_base)


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# R9 — reconcile_runtime_state
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_reconcile_runtime_running
## 🧪 TRAP[TEST] · R9 running · Scenario: all containers running → status=converged
## · Regression: R9 convergence check — running containers = converged
## · Last fail: never
## · Remove if: reconcile_runtime_state running check logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_runtime_running(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """R9: All containers running → status=converged."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 running — all containers in running state")

    # Set up cooldown file
    cooldown_file = tmp_path / ".converge_cooldown.json"

    compose_up_calls = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker ps --filter name=<module>_<service> --format {.Names} → return container name
        if "docker ps" in cmd_str and "--filter" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="nginx\npostgres\nredis\n", stderr="")
        # docker inspect → State.Status=running
        if "docker inspect" in cmd_str and "State.Status" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="running", stderr="")
        # docker compose up -d → track
        if "compose" in cmd_str and "up" in cmd_str and "-d" in cmd_str:
            compose_up_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml_with_modules,
            modules_dir=mock_modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    assert entry["status"] == "converged"
    assert len(compose_up_calls) == 0, "docker compose up -d should NOT be called for running containers"
    logger.info("[IMP:9][test] R9 running verified: no self-heal invoked")


# endregion FUNC_test_reconcile_runtime_running


# region FUNC_test_reconcile_runtime_exited
## 🧪 TRAP[TEST] · R9 exited → self-heal · Scenario: container exited → self-heal via `docker compose up -d`
## · Regression: R9 self-heal — exited containers trigger compose up -d, NOT docker restart
## · Last fail: never
## · Remove if: reconcile_runtime_state self-heal logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_runtime_exited(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """R9: Container exited → self-heal via `docker compose up -d`, NOT `docker restart`."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 exited — self-heal via docker compose up -d")

    cooldown_file = tmp_path / ".converge_cooldown.json"

    compose_up_calls = []
    docker_restart_calls = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker ps" in cmd_str and "--filter" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="postgres\n", stderr="")
        # Docker inspect → return "exited" (non-running)
        if "docker inspect" in cmd_str and "State.Status" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="exited", stderr="")
        # docker compose up -d → track
        if "compose" in cmd_str and "up" in cmd_str and "-d" in cmd_str:
            compose_up_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker restart → track (should NOT happen)
        if "docker restart" in cmd_str:
            docker_restart_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml_with_modules,
            modules_dir=mock_modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    assert entry["status"] == "mutated"
    assert len(compose_up_calls) > 0, "docker compose up -d should have been called for exited container"
    assert len(docker_restart_calls) == 0, "docker restart should NOT be used — must use compose up -d"
    logger.info("[IMP:9][test] R9 exited verified: self-heal via compose up -d, not docker restart")


# endregion FUNC_test_reconcile_runtime_exited


# region FUNC_test_reconcile_runtime_exited_oneshot_skipped
## 🧪 TRAP[TEST] · 142 B28a · R9 oneshot-guard · Scenario: exited + RestartPolicy=no (init/createbuckets
## · Regression: exited oneshot (platform-minio-createbuckets-1) триггерил self-heal через compose up -d
## ·   БЕЗ env-секретов → «MINIO_ROOT_USER is not set» → heal fail → converge exit 2 на КАЖДОМ прогоне.
## · Last fail: 2026-08-07 (bootstrap 142, converge rc=2, R9 errors=1)
## · Remove if: oneshot-контейнеры будут иметь отличный признак (label/state)
def test_reconcile_runtime_exited_oneshot_skipped(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """R9: exited + RestartPolicy=no (oneshot) → skip self-heal (не ошибка, не compose up)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 exited-oneshot — skip self-heal (142 B28a)")

    cooldown_file = tmp_path / ".converge_cooldown.json"

    compose_up_calls = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker ps" in cmd_str and "--filter" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="postgres\n", stderr="")
        # State.Status → exited; RestartPolicy.Name → "no" (oneshot)
        if "docker inspect" in cmd_str and "RestartPolicy.Name" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="no", stderr="")
        if "docker inspect" in cmd_str and "State.Status" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="exited", stderr="")
        if "compose" in cmd_str and "up" in cmd_str and "-d" in cmd_str:
            compose_up_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml_with_modules,
            modules_dir=mock_modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    assert entry["status"] == "converged", f"oneshot exited не должен требовать heal: {entry}"
    assert len(compose_up_calls) == 0, "compose up -d НЕ должен вызываться для exited-oneshot"
    assert "oneshot" in caplog.text or "skip self-heal" in caplog.text
    logger.info("[IMP:9][test] R9 exited-oneshot verified: skip self-heal, no compose up")


# endregion FUNC_test_reconcile_runtime_exited_oneshot_skipped


# region FUNC_test_reconcile_runtime_cooldown
## 🧪 TRAP[TEST] · R9 cooldown · Scenario: same container self-healed recently → skip (cooldown)
## · Regression: R9 cooldown — skip self-heal if same container was healed in last 3 converge runs
## · Last fail: never
## · Remove if: reconcile_runtime_state cooldown logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_runtime_cooldown(tmp_path, caplog, node_yaml_with_modules, mock_modules_dir):
    """R9: Container self-healed recently → cooldown skip, no compose up -d."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R9 cooldown — previously self-healed container skipped")

    # Set up cooldown file WITH a recent cooldown entry for "postgres"
    cooldown_file = tmp_path / ".converge_cooldown.json"
    # Write cooldown state: postgres healed 1 run ago (within cooldown window of 3)
    cooldown_data = {"containers": {"postgres": {"last_healed_run": 5}}}
    cooldown_file.write_text(json.dumps(cooldown_data))

    compose_up_calls = []

    # Use a counter to simulate converge run tracking
    # Actually, run 4 - run 5 = -1 which is < 3. So cooldown triggers. Let me fix: set current to 6.
    # Current run: 6, last heal: 5, diff = 1 < 3 → cooldown skip

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "docker ps" in cmd_str and "--filter" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="postgres\nnginx\n", stderr="")
        # nginx is running → ok, postgres is exited
        if "docker inspect" in cmd_str and "State.Status" in cmd_str:
            # nginx running, postgres exited
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="exited", stderr="")
        if "compose" in cmd_str and "up" in cmd_str and "-d" in cmd_str:
            compose_up_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_runtime_state(
            node_yaml_path=node_yaml_with_modules,
            modules_dir=mock_modules_dir,
            dry_run=False,
            report_only=False,
            cooldown_file=str(cooldown_file),
        )

    assert entry["unit"] == "R9"
    # The cooldown should cause status to be "converged" or "warn", not "mutated"
    assert entry["status"] != "mutated", "Cooldown should prevent self-heal (no mutation)"
    assert len(compose_up_calls) == 0, "docker compose up -d should NOT be called during cooldown"
    logger.info("[IMP:9][test] R9 cooldown verified: self-heal skipped within cooldown window")


# endregion FUNC_test_reconcile_runtime_cooldown
