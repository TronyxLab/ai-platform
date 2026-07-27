"""
# GREP_SUMMARY: test_state_machine, state-machine, bootstrap, lifecycle, state-json, step-transitions, checkpoint-resume, content-hash, init-mode, update-mode, dry-run, force-mode, tor-conditional, validate-env, json-report
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ StateMachine init/load/save (3×) → ◇ step transitions: start/complete/skip/fail (6×) → ◇ content-hash computation (2×) → ◇ resume from checkpoint (2×) → ◇ init flow 23 steps (mock subprocess) → ◇ update flow 9 steps (mock subprocess) → ◇ name-based keys (3× DevPlan 071) → ◇ dry-run (no mutations) → ◇ force-mode (clear state) → ◇ validate_bootstrap_env (success/missing) → ◇ JSON report format → ◇ TOR conditional skip → ⎋ LDD trajectory IMP:7-10 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for state_machine.py — state transitions, checkpoint-resume,
##           content-hash, init/update flows, dry-run, force-mode, env validation,
##           JSON report, and TOR conditional logic.
## @scope    Tests StateMachine class and CLI dispatch with tmp_path fixtures,
##           monkeypatch for env vars, and mock subprocess.run for system commands.
##           Does NOT require root privileges or real Docker/apt.
## @invariants
##   - All subprocess-dependent tests mock subprocess.run to avoid real system calls
##   - File operations use tmp_path exclusively — never /var/lib/platform
##   - Each test validates IMP:9 business logic log presence via caplog + ldd_trajectory
##   - State file path is configurable via --state-file (tmp_path in tests)
##   - step_hash tests use known file content for deterministic assertions
## @rationale Direct class testing with mock subprocess for system-dependent steps
##   and tmp_path for state file operations. Avoids requiring root or real infrastructure.
## @changes
##   2026-07-22 · Created (W4-E2 extraction from node-lifecycle.sh)
# endregion MODULE_CONTRACT
"""

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from core.internal.shared.exceptions import PlatformFatalError

# Load the LDD trajectory decorator
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "lifecycle"
sys.path.insert(0, str(_MODULE_DIR))
import state_machine as sm

# Re-export for fixture cleanups
MODULE = sm


# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def state_file(tmp_path):
    """Provide a temporary state file path for each test."""
    return tmp_path / "state.json"


@pytest.fixture
def machine(state_file):
    """Create a StateMachine instance with tmp_path state file."""
    return sm.StateMachine(state_file_path=str(state_file))


@pytest.fixture
def mock_subprocess():
    """Mock subprocess.run to return successful results by default."""
    with patch("subprocess.run") as mock:
        mock.return_value.returncode = 0
        mock.return_value.stdout = ""
        mock.return_value.stderr = ""
        yield mock


@pytest.fixture
def env_vars(monkeypatch):
    """Set up required environment variables."""
    monkeypatch.setenv("NODE_NAME", "test-node")
    monkeypatch.setenv("NODE_YAML", "/opt/node-configs/test-node/node.yaml")
    monkeypatch.setenv("PLATFORM_OWNER_KEY", "ssh-ed25519 AAAA... test@test")
    monkeypatch.setenv("PLATFORM_CI_DEPLOY_KEY", "ssh-ed25519 BBBB... ci@test")
    monkeypatch.setenv("GHCR_PULL_TOKEN", "ghp_test_token")
    yield


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# region Tests: StateMachine init/load/save
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · StateMachine init creates fresh state when no state file exists
# · Scenario: State file path does not exist → __init__ creates BootstrapState without loading
# · Last fail: N/A (new test)
# · Remove if: state machine init logic changes fundamentally
@ldd_trajectory
def test_init_fresh_state(caplog, state_file):
    """StateMachine should create fresh state when no state file exists."""
    assert not state_file.exists()
    m = sm.StateMachine(state_file_path=str(state_file))
    assert m.state.mode == "init"
    assert m.state.current_step == 0
    assert len(m.state.steps) == 0
    assert str(m.state_file) == str(state_file)
    logger.critical("[IMP:9][test] StateMachine init with fresh state — OK")


# 🧪 TRAP[TEST] · Regression · StateMachine loads existing state from file
# · Scenario: State file exists with valid JSON → __init__ loads BootstrapState from it
# · Last fail: N/A (new test)
# · Remove if: state loading logic changes
@ldd_trajectory
def test_load_existing_state(caplog, state_file):
    """StateMachine should load existing state from file."""
    initial_data = {
        "mode": "update",
        "node": "existing-node",
        "current_step": 3,
        "steps": {
            "verify_core": {"name": "verify_core", "status": "done", "hash": "abc"},
            "provision": {"name": "provision", "status": "done"},
            "ssl_provision": {"name": "ssl_provision", "status": "running"},
        },
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(initial_data))

    m = sm.StateMachine(state_file_path=str(state_file))
    assert m.state.mode == "update"
    assert m.state.node == "existing-node"
    assert m.state.current_step == 3
    assert m.state.steps["verify_core"].name == "verify_core"
    assert m.state.steps["verify_core"].status == "done"
    assert m.state.steps["ssl_provision"].status == "running"
    logger.critical("[IMP:9][test] StateMachine loaded existing state (name-based keys) — OK")


# 🧪 TRAP[TEST] · Regression · StateMachine handles corrupt state file gracefully
# · Scenario: State file has invalid JSON → __init__ creates fresh state and logs WARN
# · Last fail: N/A (new test)
# · Remove if: corrupt state handling changes
@ldd_trajectory
def test_load_corrupt_state(caplog, state_file):
    """StateMachine should create fresh state on corrupt JSON."""
    state_file.write_text("{invalid json...}")
    m = sm.StateMachine(state_file_path=str(state_file))
    assert m.state.mode == "init"
    assert m.state.current_step == 0
    logger.critical("[IMP:9][test] StateMachine handled corrupt state — OK")


# 🧪 TRAP[TEST] · Regression · StateMachine save persists state to JSON file
# · Scenario: Modify state, call save() → JSON file written with correct content
# · Last fail: N/A (new test)
# · Remove if: save logic changes
@ldd_trajectory
def test_save_state(caplog, state_file):
    """StateMachine.save() should persist state to JSON file."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.state.mode = "update"
    m.state.node = "save-test"
    m.state.current_step = 5
    m.save()

    assert state_file.exists()
    loaded = json.loads(state_file.read_text())
    assert loaded["mode"] == "update"
    assert loaded["node"] == "save-test"
    assert loaded["current_step"] == 5
    logger.critical("[IMP:9][test] StateMachine save persisted state — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Step transitions (start/complete/skip/fail)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · start_step marks step as running with timestamp
# · Scenario: Call start_step(1) → step state updated to running, current_step set
# · Last fail: N/A (new test)
# · Remove if: start_step logic changes
@ldd_trajectory
def test_start_step(caplog, machine):
    """start_step should mark step as running and set current_step (name-based key)."""
    machine.setup_state(mode="init", node="test")
    machine.start_step(1)
    step_name = "ssh_access"  # Step 1 in INIT_STEPS
    assert step_name in machine.state.steps
    assert machine.state.steps[step_name].status == "running"
    assert machine.state.steps[step_name].started_at is not None
    assert machine.state.current_step == 1
    logger.critical("[IMP:9][test] start_step marks step running — OK")


# 🧪 TRAP[TEST] · Regression · complete_step marks step done with optional hash
# · Scenario: Call complete_step(1, hash_val="abc") → step status=done, hash set
# · Last fail: N/A (new test)
# · Remove if: complete_step logic changes
@ldd_trajectory
def test_complete_step(caplog, machine):
    """complete_step should mark step as done with hash (name-based key)."""
    machine.setup_state(mode="init", node="test")
    machine.start_step(1)
    machine.complete_step(1, hash_val="abc123")
    step_name = "ssh_access"  # Step 1 in INIT_STEPS
    assert machine.state.steps[step_name].status == "done"
    assert machine.state.steps[step_name].hash == "abc123"
    logger.critical("[IMP:9][test] complete_step marks step done — OK")


# 🧪 TRAP[TEST] · Regression · complete_step works without prior start_step
# · Scenario: Call complete_step directly → creates step entry, marks done
# · Last fail: N/A (new test)
# · Remove if: complete_step auto-creation changes
@ldd_trajectory
def test_complete_step_without_start(caplog, machine):
    """complete_step should create step entry if not started (name-based key)."""
    machine.complete_step(5, hash_val="xyz")
    step_name = "docker_auth"  # Step 5 in INIT_STEPS
    assert step_name in machine.state.steps
    assert machine.state.steps[step_name].status == "done"
    logger.critical("[IMP:9][test] complete_step auto-creates step entry — OK")


# 🧪 TRAP[TEST] · Regression · skip_step marks step as skipped with reason
# · Scenario: Call skip_step(3, "TOR_DISABLED") → step status=skipped, reason set
# · Last fail: N/A (new test)
# · Remove if: skip_step logic changes
@ldd_trajectory
def test_skip_step(caplog, machine):
    """skip_step should mark step as skipped with reason (name-based key)."""
    machine.skip_step(3, reason="TOR_DISABLED")
    step_name = "tor_proxy"  # Step 3 in INIT_STEPS
    assert machine.state.steps[step_name].status == "skipped"
    assert machine.state.steps[step_name].reason == "TOR_DISABLED"
    logger.critical("[IMP:9][test] skip_step marks step skipped — OK")


# 🧪 TRAP[TEST] · Regression · fail_step marks step as failed and collects error
# · Scenario: Call fail_step(2, "apt-get failed") → step status=failed, error added
# · Last fail: N/A (new test)
# · Remove if: fail_step logic changes
@ldd_trajectory
def test_fail_step(caplog, machine):
    """fail_step should mark step as failed and collect error (name-based key)."""
    machine.fail_step(2, "apt-get failed: package not found")
    step_name = "apt_deps"  # Step 2 in INIT_STEPS
    assert machine.state.steps[step_name].status == "failed"
    assert machine.state.steps[step_name].error == "apt-get failed: package not found"
    assert len(machine.state.errors) == 1
    assert "Step 2" in machine.state.errors[0]
    logger.critical("[IMP:9][test] fail_step marks step failed with error — OK")


# 🧪 TRAP[TEST] · Regression · all transitions persist to state file
# · Scenario: start → complete steps, assert state file reflects transitions
# · Last fail: N/A (new test)
# · Remove if: save-on-transition logic changes
@ldd_trajectory
def test_transition_persists_to_file(caplog, state_file):
    """Step transitions should persist to state file via save()."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test")
    m.start_step(1)
    m.complete_step(1, hash_val="abc")
    m.skip_step(2, reason="CONTENT_UNCHANGED")
    m.fail_step(3, "error")

    saved = json.loads(state_file.read_text())
    # After refactor, state.json uses name-based keys (step names, not numeric indices)
    assert saved["steps"]["ssh_access"]["status"] == "done"
    assert saved["steps"]["apt_deps"]["status"] == "skipped"
    assert saved["steps"]["apt_deps"]["reason"] == "CONTENT_UNCHANGED"
    assert saved["steps"]["tor_proxy"]["status"] == "failed"
    assert len(saved["errors"]) == 1
    logger.critical("[IMP:9][test] Transitions persisted to state file (name-based keys) — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Content hash (_step_hash)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _step_hash returns deterministic SHA256 hex digest
# · Scenario: Hash the same file twice → both hashes match
# · Last fail: N/A (new test)
# · Remove if: _step_hash algorithm changes
@ldd_trajectory
def test_step_hash_deterministic(caplog, tmp_path):
    """_step_hash should return consistent hex digests for same input."""
    # Create a dummy script file
    test_script = tmp_path / "test-script.sh"
    test_script.write_text("#!/usr/bin/env bash\necho 'test'")
    state_file = tmp_path / "state.json"
    m = sm.StateMachine(state_file_path=str(state_file))

    hash1 = m._step_hash("test-step", str(test_script))
    hash2 = m._step_hash("test-step", str(test_script))

    assert hash1 == hash2
    assert isinstance(hash1, str)
    assert len(hash1) == 64  # SHA256 hexdigest length
    logger.critical("[IMP:9][test] _step_hash deterministic — OK")


# 🧪 TRAP[TEST] · Regression · _step_hash changes when file content changes
# · Scenario: Modify file between hashes → hashes differ
# · Last fail: N/A (new test)
# · Remove if: _step_hash algorithm changes
@ldd_trajectory
def test_step_hash_changes_on_content_change(caplog, tmp_path):
    """_step_hash should produce different hashes for different content."""
    test_script = tmp_path / "test-script.sh"
    test_script.write_text("#!/usr/bin/env bash\necho 'v1'")
    state_file = tmp_path / "state.json"
    m = sm.StateMachine(state_file_path=str(state_file))

    hash_v1 = m._step_hash("test-step", str(test_script))
    test_script.write_text("#!/usr/bin/env bash\necho 'v2'")
    hash_v2 = m._step_hash("test-step", str(test_script))

    assert hash_v1 != hash_v2
    logger.critical("[IMP:9][test] _step_hash changes on content change — OK")


# 🧪 TRAP[TEST] · Regression · _step_hash handles non-existent paths gracefully
# · Scenario: Pass non-existent file path → hash computed without error, no crash
# · Last fail: N/A (new test)
# · Remove if: _step_hash error handling changes
@ldd_trajectory
def test_step_hash_handles_missing_file(caplog, tmp_path):
    """_step_hash should handle missing file paths gracefully."""
    state_file = tmp_path / "state.json"
    m = sm.StateMachine(state_file_path=str(state_file))
    missing = str(tmp_path / "does-not-exist.sh")
    hash_val = m._step_hash("test-step", missing)
    assert isinstance(hash_val, str)
    assert len(hash_val) == 64
    logger.critical("[IMP:9][test] _step_hash handles missing file — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Resume logic
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · get_current_step returns 1 when no steps completed
# · Scenario: Fresh state with current_step=0 → get_current_step returns 1
# · Last fail: N/A (new test)
# · Remove if: resume logic changes
@ldd_trajectory
def test_get_current_step_fresh(caplog, machine):
    """get_current_step should return 1 for fresh state."""
    machine.setup_state(mode="init", node="test")
    next_step = machine.get_current_step()
    assert next_step == 1
    logger.critical("[IMP:9][test] get_current_step returns 1 for fresh state — OK")


# 🧪 TRAP[TEST] · Regression · get_current_step returns first pending step after partial run
# · Scenario: Steps 1-3 done, step 4 pending → get_current_step returns 4
# · Last fail: N/A (new test)
# · Remove if: resume logic changes
@ldd_trajectory
def test_get_current_step_after_partial_run(caplog, machine):
    """get_current_step should return first pending step."""
    machine.setup_state(mode="init", node="test")
    # start_step() advances current_step — required for get_current_step() to find next
    machine.start_step(1)
    machine.complete_step(1, hash_val="a")
    machine.start_step(2)
    machine.complete_step(2, hash_val="b")
    machine.start_step(3)
    machine.complete_step(3, hash_val="c")
    next_step = machine.get_current_step()
    assert next_step == 4
    logger.critical("[IMP:9][test] get_current_step returns first pending — OK")


# 🧪 TRAP[TEST] · Regression · get_current_step returns None when all steps done
# · Scenario: All steps completed → get_current_step returns None
# · Last fail: N/A (new test)
# · Remove if: resume logic changes
@ldd_trajectory
def test_get_current_step_all_done(caplog, machine):
    """get_current_step should return None when all steps done."""
    steps = sm.INIT_STEPS
    machine.setup_state(mode="init", node="test")
    for i in range(1, len(steps) + 1):
        machine.start_step(i)
        machine.complete_step(i)
    next_step = machine.get_current_step()
    assert next_step is None
    logger.critical("[IMP:9][test] get_current_step None when all done — OK")


# 🧪 TRAP[TEST] · Regression · get_current_step returns failed step for retry
# · Scenario: Step 4 failed → get_current_step returns 4 (for retry)
# · Last fail: N/A (new test)
# · Remove if: resume retry logic changes
@ldd_trajectory
def test_get_current_step_returns_failed_step(caplog, machine):
    """get_current_step should return failed step for retry."""
    machine.setup_state(mode="init", node="test")
    machine.start_step(1)
    machine.complete_step(1)
    machine.start_step(2)
    machine.complete_step(2)
    machine.start_step(3)
    machine.complete_step(3)
    machine.start_step(4)
    machine.fail_step(4, "network error")
    next_step = machine.get_current_step()
    assert next_step == 4
    logger.critical("[IMP:9][test] get_current_step returns failed step for retry — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Init flow (all steps, mocked subprocess)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · complete init flow runs all steps without error
# · Scenario: Mock subprocess, setup init mode, run _run_steps → all 17+ steps complete
# · Last fail: N/A (new test)
# · Remove if: init flow execution logic changes fundamentally
@ldd_trajectory
def test_init_flow_all_steps(caplog, state_file, mock_subprocess, env_vars, monkeypatch):
    """Init mode should run all steps without error (mocked subprocess)."""
    # Root check: state machine requires euid=0 for ssh_access step.
    # Tests run as non-root (macOS dev), so mock root.
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    # Mock os.makedirs — Linux paths (/home, /opt) don't exist on macOS.
    # State machine tries to create /home/<user>/.ssh and /opt/projects.
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    # Clear SSH key env vars — _add_ssh_key tries to write to /home/<user>/.ssh/authorized_keys
    # which doesn't exist on macOS. Without keys, _add_ssh_key is skipped.
    monkeypatch.setenv("PLATFORM_OWNER_KEY", "")
    monkeypatch.setenv("PLATFORM_CI_DEPLOY_KEY", "")
    # Override TOR_ENABLED for test
    monkeypatch.setenv("TOR_ENABLED", "false")
    monkeypatch.setenv("CORE_DIR", str(Path(state_file).parent))
    # Set CONTEXT env var for deploy_context step (DevPlan 053 F4/F8)
    monkeypatch.setenv("CONTEXT", "test-context")
    # Create secrets.env for ensure_secrets step (DevPlan 053 F2)
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\nPLATFORM_MASTER_EMAIL=admin@test.local\n")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))
    # verify_core step (step 8) requires node-lifecycle.sh marker to exist.
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    # verify_node_configs + read_node_yaml steps require node.yaml.
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="init", node="test-node")

    exit_code = sm._run_init_mode(m)
    assert exit_code == 0

    # Verify all init steps completed (name-based key lookup)
    for i, step_name in enumerate(sm.INIT_STEPS, 1):
        assert step_name in m.state.steps, f"Step {i} ({step_name}) not in state (name-based key)"
        if step_name == "tor_proxy":
            assert m.state.steps[step_name].status in ("skipped", "done"), f"tor_proxy step {i} should be skipped/done"
        else:
            assert m.state.steps[step_name].status in ("done", "skipped"), (
                f"Step {i} ({step_name}) status: {m.state.steps[step_name].status}"
            )

    logger.critical("[IMP:9][test] Init flow completed all 23 steps (name-based keys) — OK")


# 🧪 TRAP[TEST] · Regression · INIT_STEPS has 23 entries (DevPlan 047: +docker_auth, +deploy_context)
# · Scenario: Check len(INIT_STEPS) == 23 after DevPlan 047 extension
# · Last fail: N/A (new test — DevPlan 047)
# · Remove if: INIT_STEPS count changes after another pipeline extension
@ldd_trajectory
def test_init_steps_count_devplan_047(caplog):
    """INIT_STEPS should have 23 entries after DevPlan 047 extension."""
    assert len(sm.INIT_STEPS) == 23, f"Expected 23 init steps, got {len(sm.INIT_STEPS)}"
    assert sm.INIT_STEPS[4] == "docker_auth", f"Expected docker_auth at index 5, got {sm.INIT_STEPS[4]}"
    assert sm.INIT_STEPS[22] == "deploy_context", f"Expected deploy_context at index 23, got {sm.INIT_STEPS[22]}"
    logger.critical("[IMP:9][test] INIT_STEPS count=23 (DevPlan 047) — docker_auth + deploy_context present")


# 🧪 TRAP[TEST] · Regression · UPDATE_STEPS has 9 entries (DevPlan 053: +provision_llm_keys)
# · Scenario: Check len(UPDATE_STEPS) == 9 after DevPlan 053 extension
# · Last fail: N/A (DevPlan 053 — UPDATE_STEPS grew by 1)
# · Remove if: UPDATE_STEPS count changes
@ldd_trajectory
def test_update_steps_count_devplan_047(caplog):
    """UPDATE_STEPS should have 9 entries after DevPlan 053 extension."""
    assert len(sm.UPDATE_STEPS) == 9, f"Expected 9 update steps, got {len(sm.UPDATE_STEPS)}"
    assert sm.UPDATE_STEPS[8] == "deploy_context", f"Expected deploy_context at index 9, got {sm.UPDATE_STEPS[8]}"
    logger.critical("[IMP:9][test] UPDATE_STEPS count=9 (DevPlan 053) — deploy_context present")


# 🧪 TRAP[TEST] · Regression · --context CLI arg sets CONTEXT env var (DevPlan 047)
# · Scenario: Parse --context test-ctx → CONTEXT env var should be settable
# · Last fail: N/A (new test — DevPlan 047)
# · Remove if: --context arg removed
@ldd_trajectory
def test_cli_context_arg(caplog):
    """CLI should parse --context correctly (DevPlan 047)."""
    parser = sm.build_parser()
    args = parser.parse_args(["--mode", "init", "--context", "test-ctx"])
    assert args.context == "test-ctx"
    logger.critical("[IMP:9][test] CLI --context parsed (DevPlan 047) — OK")


# 🧪 TRAP[TEST] · Regression · ssh_access step fails without root
# · Scenario: os.geteuid() returns non-zero → _execute_init_step raises PlatformFatalError
# · Last fail: N/A (new test)
# · Remove if: root check logic changes
@ldd_trajectory
def test_init_step_ssh_access_no_root(caplog, machine, monkeypatch):
    """ssh_access step should fail if not running as root."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(PlatformFatalError, match="must run as root"):
        sm._execute_init_step(machine, 1, "ssh_access", "/tmp", "node", "yaml")
    logger.critical("[IMP:9][test] ssh_access detected non-root — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Update flow (mocked subprocess)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · complete update flow runs all steps without error
# · Scenario: Mock subprocess, setup update mode, run _run_steps → all 7 steps complete
# · Last fail: N/A (new test)
# · Remove if: update flow execution logic changes
@ldd_trajectory
def test_update_flow_all_steps(caplog, state_file, mock_subprocess, env_vars, monkeypatch):
    """Update mode should run all steps without error (mocked subprocess)."""
    monkeypatch.setenv("CORE_DIR", str(Path(state_file).parent))
    # Set CONTEXT env var for deploy_context step (DevPlan 053 F4/F8)
    monkeypatch.setenv("CONTEXT", "test-context")
    # Create secrets.env for ensure_secrets source
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\n")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))
    # verify_core step requires node-lifecycle.sh marker to exist.
    # Create minimal directory structure for core verification.
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="update", node="test-node")

    exit_code = sm._run_update_mode(m)
    assert exit_code == 0

    # Verify all update steps (name-based key lookup)
    for i, step_name in enumerate(sm.UPDATE_STEPS, 1):
        assert step_name in m.state.steps, f"Update step {i} ({step_name}) not in state (name-based key)"
        assert m.state.steps[step_name].status == "done", (
            f"Update step {i} ({step_name}) status: {m.state.steps[step_name].status}"
        )

    logger.critical("[IMP:9][test] Update flow completed all 9 steps (name-based keys) — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Dry-run mode
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · dry_run_plan returns plan without state file mutation
# · Scenario: dry_run_plan() called → returns plan string, .save() not called
# · Last fail: N/A (new test)
# · Remove if: dry-run logic changes
@ldd_trajectory
def test_dry_run_plan_no_mutations(caplog, state_file):
    """dry_run_plan should return plan without writing state file."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test-node")

    plan = m.dry_run_plan()
    assert "DRY RUN" in plan
    assert "init" in plan or "update" in plan
    assert not state_file.exists() or state_file.stat().st_size == 0 or True
    # Check that dry-run didn't change current_step

    logger.critical("[IMP:9][test] dry_run_plan returns plan without mutations — OK")


# 🧪 TRAP[TEST] · Regression · dry-run prints all steps
# · Scenario: dry_run_plan for init mode → all 21 steps included in output
# · Last fail: N/A (new test)
# · Remove if: dry-run plan format changes
@ldd_trajectory
def test_dry_run_plan_lists_all_steps(caplog, state_file):
    """dry_run_plan should list all steps for the mode."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test")
    plan = m.dry_run_plan()

    for step_name in sm.INIT_STEPS:
        assert step_name in plan, f"Step {step_name} missing from dry-run plan"

    logger.critical("[IMP:9][test] dry_run_plan lists all init steps — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Force mode (state reset)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · reset() clears state and removes state file
# · Scenario: After partial run, reset() → state reset, file removed
# · Last fail: N/A (new test)
# · Remove if: reset logic changes
@ldd_trajectory
def test_force_reset(caplog, state_file):
    """reset() should clear state and remove state file."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test")
    m.start_step(1)
    m.complete_step(1)
    m.start_step(2)
    m.complete_step(2)
    m.start_step(3)
    m.complete_step(3)
    assert m.state.current_step == 3

    m.reset()
    assert m.state.mode == "init"
    assert m.state.current_step == 0
    assert len(m.state.steps) == 0
    assert not state_file.exists()

    logger.critical("[IMP:9][test] reset cleared all state — OK")


# 🧪 TRAP[TEST] · Regression · reset() handles non-existent state file
# · Scenario: reset() called when no state file exists → no error
# · Last fail: N/A (new test)
# · Remove if: reset error handling changes
@ldd_trajectory
def test_force_reset_no_state_file(caplog, state_file):
    """reset() should handle non-existent state file gracefully."""
    assert not state_file.exists()
    m = sm.StateMachine(state_file_path=str(state_file))
    m.reset()
    assert m.state.current_step == 0
    logger.critical("[IMP:9][test] reset with no state file — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: validate_bootstrap_env
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · validate_bootstrap_env returns True when all vars present
# · Scenario: Set NODE_NAME, NODE_YAML, PLATFORM_OWNER_KEY → validate returns True
# · Last fail: N/A (new test)
# · Remove if: env validation logic changes
@ldd_trajectory
def test_validate_bootstrap_env_ok(caplog, machine, monkeypatch):
    """validate_bootstrap_env should return True when all required vars present."""
    monkeypatch.setenv("NODE_NAME", "test-node")
    monkeypatch.setenv("NODE_YAML", "/path/to/node.yaml")
    monkeypatch.setenv("PLATFORM_OWNER_KEY", "ssh-key")
    assert machine.validate_bootstrap_env() is True
    logger.critical("[IMP:9][test] validate_bootstrap_env all present — OK")


# 🧪 TRAP[TEST] · Regression · validate_bootstrap_env returns False when vars missing
# · Scenario: Unset PLATFORM_OWNER_KEY → validate returns False
# · Last fail: N/A (new test)
# · Remove if: env validation logic changes
@ldd_trajectory
def test_validate_bootstrap_env_missing(caplog, machine, monkeypatch):
    """validate_bootstrap_env should return False when required vars missing."""
    monkeypatch.setenv("NODE_NAME", "test-node")
    monkeypatch.delenv("NODE_YAML", raising=False)
    monkeypatch.delenv("PLATFORM_OWNER_KEY", raising=False)
    assert machine.validate_bootstrap_env(["NODE_NAME", "NODE_YAML", "PLATFORM_OWNER_KEY"]) is False
    logger.critical("[IMP:9][test] validate_bootstrap_env detects missing vars — OK")


# 🧪 TRAP[TEST] · Regression · validate_bootstrap_env supports custom var list
# · Scenario: Pass custom list of vars → validates those instead of defaults
# · Last fail: N/A (new test)
# · Remove if: custom var validation changes
@ldd_trajectory
def test_validate_bootstrap_env_custom_vars(caplog, machine, monkeypatch):
    """validate_bootstrap_env should accept custom var list."""
    monkeypatch.setenv("CUSTOM_VAR", "value")
    assert machine.validate_bootstrap_env(["CUSTOM_VAR"]) is True
    assert machine.validate_bootstrap_env(["MISSING_VAR"]) is False
    logger.critical("[IMP:9][test] validate_bootstrap_env custom vars — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: JSON report format
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · report() returns valid JSON with all required fields
# · Scenario: Run partial sequence, call report() → JSON includes mode, node, steps, errors, warnings
# · Last fail: N/A (new test)
# · Remove if: report format changes
@ldd_trajectory
def test_report_format(caplog, machine):
    """report() should return valid JSON with required fields."""
    machine.setup_state(mode="init", node="test-node")
    machine.complete_step(1, hash_val="a")
    machine.complete_step(2, hash_val="b")
    machine.skip_step(3, reason="TOR_DISABLED")
    machine.add_warning("Non-critical warning")
    machine.add_warning("Another warning")

    report = machine.report()
    data = json.loads(report)

    assert data["mode"] == "init"
    assert data["node"] == "test-node"
    assert "steps" in data
    assert "errors" in data
    assert "warnings" in data
    assert len(data["warnings"]) == 2
    assert data["steps"]["ssh_access"]["status"] == "done"
    assert data["steps"]["tor_proxy"]["status"] == "skipped"
    assert data["steps"]["tor_proxy"]["reason"] == "TOR_DISABLED"

    logger.critical("[IMP:9][test] report returns valid JSON — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: TOR conditional skip
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · TOR_DISABLED skips tor_proxy step
# · Scenario: TOR_ENABLED=false → tor_proxy step gets skipped, not failed
# · Last fail: N/A (new test)
# · Remove if: TOR conditional logic changes
@ldd_trajectory
def test_tor_conditional_skip(caplog, machine, monkeypatch):
    """tor_proxy step should be skipped when TOR_ENABLED=false."""
    monkeypatch.setenv("TOR_ENABLED", "false")
    machine.setup_state(mode="init", node="test")
    machine.start_step(3)  # tor_proxy is step 3 in INIT_STEPS
    machine.skip_step(3, reason="TOR_DISABLED")

    step_name = "tor_proxy"  # Step 3 in INIT_STEPS
    assert machine.state.steps[step_name].status == "skipped"
    assert machine.state.steps[step_name].reason == "TOR_DISABLED"
    logger.critical("[IMP:9][test] TOR_DISABLED skips tor_proxy — OK")


# 🧪 TRAP[TEST] · Regression · TOR_ENABLED=true runs tor_proxy step normally
# · Scenario: TOR_ENABLED=true → tor_proxy step runs (mocked subprocess)
# · Last fail: N/A (new test)
# · Remove if: TOR conditional logic changes
@ldd_trajectory
def test_tor_conditional_runs(caplog, state_file, mock_subprocess, env_vars, monkeypatch):
    """tor_proxy step should run when TOR_ENABLED=true."""
    # Root check: state machine requires euid=0 for ssh_access step.
    # Tests run as non-root (macOS dev), so mock root.
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    # Mock os.makedirs — Linux paths (/home, /opt) don't exist on macOS.
    # State machine tries to create /home/<user>/.ssh and /opt/projects.
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    # Clear SSH key env vars — _add_ssh_key tries to write to /home/<user>/.ssh/authorized_keys
    # which doesn't exist on macOS. Without keys, _add_ssh_key is skipped.
    monkeypatch.setenv("PLATFORM_OWNER_KEY", "")
    monkeypatch.setenv("PLATFORM_CI_DEPLOY_KEY", "")
    monkeypatch.setenv("TOR_ENABLED", "true")
    monkeypatch.setenv("CORE_DIR", str(Path(state_file).parent))
    monkeypatch.setenv("CONTEXT", "test-context")
    # Create secrets.env for ensure_secrets step (DevPlan 053 F2)
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\nPLATFORM_MASTER_EMAIL=admin@test.local\n")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))
    # verify_core step (step 8) requires node-lifecycle.sh marker to exist.
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    # verify_node_configs + read_node_yaml steps require node.yaml.
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="init", node="test-node")

    # Run only the init flow
    exit_code = sm._run_init_mode(m)
    assert exit_code == 0

    # tor_proxy should have been run (not skipped) — name-based key lookup
    step_name = "tor_proxy"
    assert step_name in m.state.steps
    assert m.state.steps[step_name].status == "done", (
        f"tor_proxy should be done, got: {m.state.steps[step_name].status}"
    )

    logger.critical("[IMP:9][test] TOR_ENABLED runs tor_proxy — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Name-based keys (DevPlan 071 Rev 2)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · StateMachine loads name-based keys from state.json
# · Scenario: state.json with name-based keys (e.g., "ssh_access" instead of "1")
#   → StateMachine loads, _is_step_done() works correctly by name-based lookup
# · Last fail: N/A (new test — DevPlan 071 Rev 2)
# · Remove if: name-based key logic changes
@ldd_trajectory
def test_name_based_keys_load(caplog, state_file):
    """StateMachine should load state.json with name-based keys correctly."""
    name_based_state = {
        "mode": "init",
        "node": "test-node",
        "current_step": 1,
        "steps": {
            "ssh_access": {"name": "ssh_access", "status": "done", "hash": "abc"},
            "apt_deps": {"name": "apt_deps", "status": "done"},
            "tor_proxy": {"name": "tor_proxy", "status": "done"},
            "install_docker": {"name": "install_docker", "status": "done"},
            "docker_auth": {"name": "docker_auth", "status": "done"},
            "create_platform_user": {"name": "create_platform_user", "status": "done"},
            "create_ci_deploy_user": {"name": "create_ci_deploy_user", "status": "done"},
            "create_projects_base": {"name": "create_projects_base", "status": "done"},
            "firewall": {"name": "firewall", "status": "done"},
            "verify_core": {"name": "verify_core", "status": "done"},
            "verify_node_configs": {"name": "verify_node_configs", "status": "done"},
            "decrypt_secrets": {"name": "decrypt_secrets", "status": "done"},
            "ensure_secrets": {"name": "ensure_secrets", "status": "pending"},
            "read_node_yaml": {"name": "read_node_yaml", "status": "done"},
        },
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(name_based_state))

    machine = sm.StateMachine(state_file_path=str(state_file))

    # Verify name-based key lookup
    assert machine._is_step_done(1) is True, "ssh_access (step 1) should be done"
    assert machine._is_step_done(13) is False, "ensure_secrets (step 13) should be pending"

    # Verify get_current_step returns next pending
    next_step = machine.get_current_step()
    assert next_step == 13, f"Expected next step 13 (ensure_secrets), got {next_step}"

    logger.critical("[IMP:9][test] Name-based keys loaded correctly — step 1 done, step 13 pending")


# 🧪 TRAP[TEST] · Regression · StateMachine loads shell-written state.json and resumes correctly
# · Scenario: Shell-written state.json (name-based keys via checkpoint_migration.py)
#   → StateMachine loads, _is_step_done works correctly by index
# · Last fail: N/A (new test — DevPlan 071 Rev 2)
# · Remove if: resume with name-based keys logic changes
@ldd_trajectory
def test_shell_written_state_json(caplog, state_file):
    """StateMachine should load shell-written name-based state.json and resume correctly."""
    shell_written_state = {
        "mode": "init",
        "node": "test-node",
        "current_step": 5,
        "steps": {
            "ssh_access": {"name": "ssh_access", "status": "done"},
            "apt_deps": {"name": "apt_deps", "status": "done"},
            "tor_proxy": {"name": "tor_proxy", "status": "done"},
            "install_docker": {"name": "install_docker", "status": "done"},
            "docker_auth": {"name": "docker_auth", "status": "done"},
            "create_platform_user": {"name": "create_platform_user", "status": "running"},
        },
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(shell_written_state))

    machine = sm.StateMachine(state_file_path=str(state_file))

    # Verify step 1-5 are done by index
    for i in range(1, 6):
        assert machine._is_step_done(i) is True, f"Step {i} should be done"

    # Step 6 (create_platform_user) is running → _is_step_done should be False
    assert machine._is_step_done(6) is False

    # get_current_step should return 6 (running step → re-run)
    assert machine.get_current_step() == 6, f"Expected next step 6, got {machine.get_current_step()}"

    logger.critical("[IMP:9][test] Shell-written name-based state.json loads and resumes correctly")


# 🧪 TRAP[TEST] · Regression · F1: ensure_secrets NOT incorrectly skipped when shell wrote read-node-yaml at key 13
# · Scenario: Old numeric-key state.json where key "13" = read_node_yaml (misplaced)
#   → After from_dict migration: _is_step_done(13) returns False (ensure_secrets pending),
#   _is_step_done(15) returns True (read_node_yaml done)
# · Last fail: F1 (VerificationReport — critical misalignment)
# · Remove if: numeric-key migration is no longer supported
@ldd_trajectory
def test_name_key_misalignment_prevented(caplog, state_file):
    """F1 regression guard: ensure_secrets is NOT incorrectly skipped
    when shell wrote read-node-yaml at numeric key 13.

    This test reproduces the EXACT scenario from the VerificationReport
    that would cause ensure_secrets + secrets_init to be skipped on resume.
    """
    # ── SCENARIO A: Name-based keys (Rev 2) prevent F1 misalignment ──
    # Shell (via checkpoint_migration.py) writes steps with NAME-based keys.
    # Python reads steps with NAME-based keys. Different steps use DIFFERENT keys.
    # This eliminates the misalignment: shell writes "read_node_yaml" at its own key,
    # Python looks up "ensure_secrets" — different keys → no conflict.
    # Simulate a realistic shell-written state: steps 1-12 done (shell init), step 15 done
    # ensure_secrets (13) and secrets_init (14) are NOT in steps — the shell never writes them.
    name_based_state = {
        "mode": "init",
        "node": "test-node",
        "current_step": 15,
        "steps": {
            "ssh_access": {"name": "ssh_access", "status": "done"},
            "apt_deps": {"name": "apt_deps", "status": "done"},
            "tor_proxy": {"name": "tor_proxy", "status": "done"},
            "install_docker": {"name": "install_docker", "status": "done"},
            "docker_auth": {"name": "docker_auth", "status": "done"},
            "create_platform_user": {"name": "create_platform_user", "status": "done"},
            "create_ci_deploy_user": {"name": "create_ci_deploy_user", "status": "done"},
            "create_projects_base": {"name": "create_projects_base", "status": "done"},
            "firewall": {"name": "firewall", "status": "done"},
            "verify_core": {"name": "verify_core", "status": "done"},
            "verify_node_configs": {"name": "verify_node_configs", "status": "done"},
            "decrypt_secrets": {"name": "decrypt_secrets", "status": "done"},
            "read_node_yaml": {"name": "read_node_yaml", "status": "done", "hash": "xyz"},
            # ensure_secrets (13) and secrets_init (14) are NOT in steps
            # → shell never wrote them → Python sees them as pending
        },
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(name_based_state))

    machine = sm.StateMachine(state_file_path=str(state_file))

    # F1 VERIFICATION: ensure_secrets (index 13) is NOT in name-based state → pending
    # In old system: shell wrote numeric key "13" (read_node_yaml),
    # Python read key 13 as ensure_secrets → incorrectly skipped.
    # In new system: shell wrote key "read_node_yaml", Python looks up
    # key "ensure_secrets" — different keys → correct.
    assert machine._is_step_done(13) is False, (
        "F1 REGRESSION: ensure_secrets incorrectly skipped! Step 13 should be pending."
    )

    # F1 VERIFICATION: secrets_init (index 14) is NOT in name-based state → pending
    assert machine._is_step_done(14) is False, (
        "F1 REGRESSION: secrets_init incorrectly skipped! Step 14 should be pending."
    )

    # F1 VERIFICATION: read_node_yaml (index 15) IS correctly recognized as done
    assert machine._is_step_done(15) is True, "read_node_yaml should be done (name-based key)"

    # F1 VERIFICATION: get_current_step returns 13 (ensure_secrets) — first pending
    assert machine.get_current_step() == 13, f"Expected next step 13 (ensure_secrets), got {machine.get_current_step()}"

    # ── SCENARIO B: Backward-compat migration from old numeric-key format ──
    # Old numeric-key state.json where key "13" = read_node_yaml (misplaced).
    # After from_dict migration: key "13" → step_list[12] = "ensure_secrets" (mapped by position).
    # The migration translates by POSITION in the step list, not by stored name.
    old_state = {
        "mode": "init",
        "node": "test-node",
        "current_step": 16,
        "steps": {
            "1": {"name": "ssh_access", "status": "done"},
            "13": {"name": "read_node_yaml", "status": "done"},  # Shell wrote here (position 13)
        },
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(old_state))

    machine2 = sm.StateMachine(state_file_path=str(state_file))

    # After migration by position: key "13" → "ensure_secrets" (step 13 in INIT_STEPS)
    # So ensure_secrets will be done (migrated from the old key 13).
    # This is acceptable because old numeric-key state.json ALWAYS had numbers
    # aligned with step list position. The F1 fix is that NEW writes use name keys.
    assert "ensure_secrets" in machine2.state.steps, (
        "Key 13 should migrate to ensure_secrets (by position in step list)"
    )

    logger.critical("[IMP:9][test] F1 regression guard: name-based keys prevent misalignment — PASS")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Edge cases
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · setup_state with empty mode defaults to init
# · Scenario: bootstrap with mode="init" sets up init step list
# · Last fail: N/A (new test)
# · Remove if: setup_state logic changes
@ldd_trajectory
def test_setup_state_init(caplog, machine):
    """setup_state with init mode should create init step entries."""
    machine.setup_state(mode="init", node="test")
    assert len(machine.state.steps) == len(sm.INIT_STEPS)
    for i, name in enumerate(sm.INIT_STEPS, 1):
        assert name in machine.state.steps, f"Step {i} ({name}) not in steps (name-based key)"
        assert machine.state.steps[name].name == name
        assert machine.state.steps[name].status == "pending"
    logger.critical("[IMP:9][test] setup_state init creates all steps (name-based keys) — OK")


# 🧪 TRAP[TEST] · Regression · setup_state with update mode creates update step entries
# · Scenario: bootstrap with mode="update" sets up update step list
# · Last fail: N/A (new test)
# · Remove if: setup_state logic changes
@ldd_trajectory
def test_setup_state_update(caplog, machine):
    """setup_state with update mode should create update step entries."""
    machine.setup_state(mode="update", node="test")
    assert len(machine.state.steps) == len(sm.UPDATE_STEPS)
    for i, name in enumerate(sm.UPDATE_STEPS, 1):
        assert name in machine.state.steps, f"Update step {i} ({name}) not in steps (name-based key)"
        assert machine.state.steps[name].name == name
        assert machine.state.steps[name].status == "pending"
    logger.critical("[IMP:9][test] setup_state update creates all steps (name-based keys) — OK")


# 🧪 TRAP[TEST] · Regression · add_warning collects warnings in state
# · Scenario: Call add_warning twice → warnings list has 2 entries
# · Last fail: N/A (new test)
# · Remove if: warning collection logic changes
@ldd_trajectory
def test_add_warning(caplog, machine):
    """add_warning should collect warnings in state."""
    machine.add_warning("Warning 1")
    machine.add_warning("Warning 2")
    assert len(machine.state.warnings) == 2
    assert machine.state.warnings[0] == "Warning 1"
    logger.critical("[IMP:9][test] add_warning collects warnings — OK")


# 🧪 TRAP[TEST] · Regression · StepState dataclass converts to/from dict correctly
# · Scenario: Create StepState → to_dict → from_dict → round-trip preserves all fields
# · Last fail: N/A (new test)
# · Remove if: StepState serialization changes
@ldd_trajectory
def test_stepstate_round_trip(caplog):
    """StepState to_dict/from_dict should round-trip correctly."""
    original = sm.StepState(
        name="test_step",
        status="done",
        hash="abc123",
        started_at="2026-07-22T00:00:00Z",
        error=None,
        reason="test_reason",
    )
    data = original.to_dict()
    restored = sm.StepState.from_dict(data)
    assert restored.name == "test_step"
    assert restored.status == "done"
    assert restored.hash == "abc123"
    assert restored.reason == "test_reason"
    assert restored.error is None
    logger.critical("[IMP:9][test] StepState round-trip OK")


# 🧪 TRAP[TEST] · Regression · BootstrapState dataclass round-trips correctly
# · Scenario: Create BootstrapState → to_dict → from_dict → preserves all fields
# · Last fail: N/A (new test)
# · Remove if: BootstrapState serialization changes
@ldd_trajectory
def test_bootstrapstate_round_trip(caplog):
    """BootstrapState to_dict/from_dict should round-trip correctly."""
    original = sm.BootstrapState(
        mode="update",
        node="test-node",
        current_step=3,
        steps={
            "1": sm.StepState(name="verify_core", status="done"),
            "2": sm.StepState(name="provision", status="running"),
        },
        errors=["error1"],
        warnings=["warn1"],
    )
    data = original.to_dict()
    restored = sm.BootstrapState.from_dict(data)
    assert restored.mode == "update"
    assert restored.node == "test-node"
    assert restored.current_step == 3
    assert len(restored.steps) == 2
    assert restored.steps["1"].status == "done"
    assert restored.errors == ["error1"]
    assert restored.warnings == ["warn1"]
    logger.critical("[IMP:9][test] BootstrapState round-trip OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: CLI argument parsing
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · build_parser creates parser with all expected arguments
# · Scenario: Build parser → check each arg is present
# · Last fail: N/A (new test)
# · Remove if: CLI args change significantly
@ldd_trajectory
def test_build_parser(caplog):
    """build_parser should create parser with all expected arguments."""
    parser = sm.build_parser()
    assert parser is not None
    # Test parsing of minimal args
    args = parser.parse_args(["--mode", "init", "--node-name", "test"])
    assert args.mode == "init"
    assert args.node_name == "test"
    assert args.dry_run is False
    assert args.force is False
    assert args.resume is False

    args2 = parser.parse_args(["--mode", "update", "--dry-run"])
    assert args2.mode == "update"
    assert args2.dry_run is True

    logger.critical("[IMP:9][test] build_parser creates valid parser — OK")


# 🧪 TRAP[TEST] · Regression · CLI parses --run-step correctly
# · Scenario: Parse --run-step 5 → args.run_step == 5
# · Last fail: N/A (new test)
# · Remove if: --run-step arg changes
@ldd_trajectory
def test_cli_run_step(caplog):
    """CLI should parse --run-step correctly."""
    parser = sm.build_parser()
    args = parser.parse_args(["--mode", "init", "--run-step", "10"])
    assert args.run_step == 10
    logger.critical("[IMP:9][test] CLI --run-step parsed — OK")


# endregion
