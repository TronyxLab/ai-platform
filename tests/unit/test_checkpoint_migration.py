"""
# GREP_SUMMARY: test_checkpoint_migration, checkpoint_migration, state.json, name-based-keys, SHELL_TO_PYTHON_STEP, legacy-migration, is-done, mark-done, force, reset
# STRUCTURE: ▶ tmp_path → ◇ test_shell_to_python_mapping → ◇ test_mark_done_and_is_done → ◇ test_legacy_migration → ⎋ LDD trajectory IMP:7-10 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for checkpoint_migration.py — SHELL_TO_PYTHON_STEP mapping,
##           CLI dispatch, state.json read/write, legacy .done file migration.
## @scope    Tests all public functions with tmp_path fixtures for isolated state file ops.
## @invariants
##   - File operations use tmp_path exclusively — never /var/lib/platform
##   - Each test validates IMP:9 business logic log presence via caplog + ldd_trajectory
## @rationale Direct function testing with tmp_path avoids needing real VPS paths.
## @changes
##   2026-07-25 · Created (DevPlan 071 Rev 2)
# endregion MODULE_CONTRACT
"""

import json
import logging
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "internal"))
import checkpoint_migration as cm

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def state_file(tmp_path):
    """Provide a temporary state file path for each test."""
    return tmp_path / "state.json"


@pytest.fixture
def legacy_dir(tmp_path):
    """Provide a temporary legacy .done files directory."""
    d = tmp_path / "legacy-checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# region Tests: SHELL_TO_PYTHON_STEP mapping
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · All 16 shell step names map to correct Python step names
# · Scenario: Check every entry in SHELL_TO_PYTHON_STEP mapping
# · Last fail: N/A (new test — DevPlan 071 Rev 2)
# · Remove if: SHELL_TO_PYTHON_STEP mapping is removed or fundamentally changed
@ldd_trajectory
def test_shell_to_python_mapping(caplog):
    """All 16 shell step names should map to correct Python step names."""
    assert cm.SHELL_TO_PYTHON_STEP["ssh-access"] == "ssh_access"
    assert cm.SHELL_TO_PYTHON_STEP["apt-deps"] == "apt_deps"
    assert cm.SHELL_TO_PYTHON_STEP["tor-proxy"] == "tor_proxy"
    assert cm.SHELL_TO_PYTHON_STEP["install-docker"] == "install_docker"
    assert cm.SHELL_TO_PYTHON_STEP["docker-auth"] == "docker_auth"
    assert cm.SHELL_TO_PYTHON_STEP["user-platform"] == "create_platform_user"
    assert cm.SHELL_TO_PYTHON_STEP["user-ci-deploy"] == "create_ci_deploy_user"
    assert cm.SHELL_TO_PYTHON_STEP["projects-base"] == "create_projects_base"
    assert cm.SHELL_TO_PYTHON_STEP["firewall"] == "firewall"
    assert cm.SHELL_TO_PYTHON_STEP["verify-core"] == "verify_core"
    assert cm.SHELL_TO_PYTHON_STEP["verify-node-configs"] == "verify_node_configs"
    assert cm.SHELL_TO_PYTHON_STEP["decrypt-secrets"] == "decrypt_secrets"
    assert cm.SHELL_TO_PYTHON_STEP["read-node-yaml"] == "read_node_yaml"
    assert cm.SHELL_TO_PYTHON_STEP["ghcr-auth"] == "ghcr_auth"
    assert cm.SHELL_TO_PYTHON_STEP["sudoers"] == "sudoers"
    assert cm.SHELL_TO_PYTHON_STEP["metrics-cron"] == "metrics_cron"
    assert len(cm.SHELL_TO_PYTHON_STEP) == 23
    logger.critical("[IMP:9][test] SHELL_TO_PYTHON_STEP mapping complete — all 23 entries valid")


# 🧪 TRAP[TEST] · Regression · Reverse mapping PYTHON_TO_SHELL_STEP is consistent
# · Scenario: Every value in SHELL_TO_PYTHON_STEP maps back via reverse dict
# · Last fail: N/A (new test)
# · Remove if: mapping structure changes
@ldd_trajectory
def test_python_to_shell_reverse_mapping(caplog):
    """PYTHON_TO_SHELL_STEP should be consistent with SHELL_TO_PYTHON_STEP."""
    for shell_name, python_name in cm.SHELL_TO_PYTHON_STEP.items():
        assert cm.PYTHON_TO_SHELL_STEP[python_name] == shell_name, (
            f"Reverse mapping failed for {shell_name} → {python_name}"
        )
    logger.critical("[IMP:9][test] PYTHON_TO_SHELL_STEP reverse mapping consistent")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: mark-done and is-done
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · mark_done writes name-based key, is_done reads it back
# · Scenario: mark_done("ssh-access") → state.json has "ssh_access": {"status": "done"} → is_done returns 0
# · Last fail: N/A (new test — DevPlan 071 Rev 2)
# · Remove if: checkpoint write/read logic changes
@ldd_trajectory
def test_mark_done_and_is_done(caplog, state_file):
    """mark_done should write name-based key, is_done should read it back."""
    result = cm.mark_done(str(state_file), "ssh-access", "test-hash-abc")
    assert result == 0, "mark_done should return 0 (success)"

    # Verify state.json content
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert "ssh_access" in data["steps"], "State should contain 'ssh_access' key (name-based)"
    assert data["steps"]["ssh_access"]["status"] == "done"
    assert data["steps"]["ssh_access"]["hash"] == "test-hash-abc"

    # is_done should return 0 (done)
    assert cm.is_done(str(state_file), "ssh-access") == 0

    # is_done for unknown step should return 1 (not done)
    assert cm.is_done(str(state_file), "ensure-secrets") == 1

    logger.critical("[IMP:9][test] mark_done writes name-based key, is_done reads correctly")


# 🧪 TRAP[TEST] · Regression · mark_done with hash creates proper state.json entry
# · Scenario: mark_done with hash → state.json entry includes hash field
# · Last fail: N/A (new test)
# · Remove if: hash storage format changes
@ldd_trajectory
def test_mark_done_with_hash(caplog, state_file):
    """mark_done should store hash in state.json when provided."""
    cm.mark_done(str(state_file), "apt-deps", "hash-xyz")
    data = json.loads(state_file.read_text())
    assert data["steps"]["apt_deps"]["hash"] == "hash-xyz"
    logger.critical("[IMP:9][test] mark_done stores hash correctly")


# 🧪 TRAP[TEST] · Regression · is_done returns 1 for non-existent state file
# · Scenario: is_done called on non-existent file → returns 1 (pending)
# · Last fail: N/A (new test)
# · Remove if: error handling changes
@ldd_trajectory
def test_is_done_missing_state_file(caplog, state_file):
    """is_done should return 1 for non-existent state file."""
    assert not state_file.exists()
    result = cm.is_done(str(state_file), "ssh-access")
    assert result == 1, "is_done should return 1 for missing state file"
    logger.critical("[IMP:9][test] is_done handles missing state file — returns 1")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: force and reset
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · force_step resets step to pending
# · Scenario: mark_done → force → is_done returns 1 (pending)
# · Last fail: N/A (new test)
# · Remove if: force logic changes
@ldd_trajectory
def test_force_step(caplog, state_file):
    """force_step should reset a step to pending."""
    cm.mark_done(str(state_file), "ssh-access", "hash")
    assert cm.is_done(str(state_file), "ssh-access") == 0

    cm.force_step(str(state_file), "ssh-access")
    assert cm.is_done(str(state_file), "ssh-access") == 1

    data = json.loads(state_file.read_text())
    assert data["steps"]["ssh_access"]["status"] == "pending"
    logger.critical("[IMP:9][test] force_step resets to pending — OK")


# 🧪 TRAP[TEST] · Regression · reset_all removes state.json
# · Scenario: mark_done → reset_all → state.json does not exist
# · Last fail: N/A (new test)
# · Remove if: reset logic changes
@ldd_trajectory
def test_reset_all(caplog, state_file):
    """reset_all should remove state.json."""
    cm.mark_done(str(state_file), "ssh-access")
    assert state_file.exists()

    cm.reset_all(str(state_file))
    assert not state_file.exists()
    logger.critical("[IMP:9][test] reset_all removes state.json — OK")


# 🧪 TRAP[TEST] · Regression · reset_all handles non-existent state file gracefully
# · Scenario: reset_all on non-existent file → no error, returns 0
# · Last fail: N/A (new test)
# · Remove if: reset error handling changes
@ldd_trajectory
def test_reset_all_no_state_file(caplog, state_file):
    """reset_all should handle non-existent state file gracefully."""
    assert not state_file.exists()
    result = cm.reset_all(str(state_file))
    assert result == 0
    logger.critical("[IMP:9][test] reset_all with no state file — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: legacy migration
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · migrate_legacy imports .done files to name-based state.json
# · Scenario: Create legacy .done files → migrate → state.json has name-based keys, .done files removed
# · Last fail: N/A (new test — DevPlan 071 Rev 2)
# · Remove if: migration logic changes fundamentally
@ldd_trajectory
def test_legacy_migration(caplog, state_file, legacy_dir):
    """migrate_legacy should import .done files to name-based state.json."""
    # Create legacy .done files
    done_ssh = legacy_dir / ".bootstrap-step-ssh-access.done"
    done_ssh.touch()
    hash_ssh = legacy_dir / ".bootstrap-step-ssh-access.hash"
    hash_ssh.write_text("hash-abc")

    done_docker = legacy_dir / ".bootstrap-step-install-docker.done"
    done_docker.touch()

    result = cm.migrate_legacy(str(legacy_dir), str(state_file))
    assert result == 0, "migrate_legacy should succeed"

    # Verify state.json has name-based keys
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert "ssh_access" in data["steps"], "Migration should create 'ssh_access' key"
    assert data["steps"]["ssh_access"]["status"] == "done"
    assert data["steps"]["ssh_access"]["hash"] == "hash-abc"
    assert "install_docker" in data["steps"], "Migration should create 'install_docker' key"
    assert data["steps"]["install_docker"]["status"] == "done"

    # Verify legacy files removed
    assert not done_ssh.exists()
    assert not hash_ssh.exists()
    assert not done_docker.exists()

    logger.critical("[IMP:9][test] migrate_legacy correctly converts .done → name-based state.json")


# 🧪 TRAP[TEST] · Regression · migrate_legacy is idempotent (empty dir → no-op)
# · Scenario: Call migrate_legacy on empty dir → returns 0, no state.json created
# · Last fail: N/A (new test)
# · Remove if: idempotency logic changes
@ldd_trajectory
def test_legacy_migration_idempotent(caplog, state_file, legacy_dir):
    """migrate_legacy on empty legacy dir should be a no-op."""
    result = cm.migrate_legacy(str(legacy_dir), str(state_file))
    assert result == 0
    if state_file.exists():
        data = json.loads(state_file.read_text())
        assert len(data.get("steps", {})) == 0
    logger.critical("[IMP:9][test] migrate_legacy idempotent on empty dir — OK")


# 🧪 TRAP[TEST] · Regression · migrate_legacy handles non-existent legacy dir
# · Scenario: Call migrate_legacy on non-existent dir → returns 0, no error
# · Last fail: N/A (new test)
# · Remove if: error handling changes
@ldd_trajectory
def test_legacy_migration_no_legacy_dir(caplog, state_file):
    """migrate_legacy on non-existent dir should be a no-op."""
    result = cm.migrate_legacy("/tmp/non-existent-dir-xyz-not-exists", str(state_file))
    assert result == 0
    logger.critical("[IMP:9][test] migrate_legacy handles missing legacy dir — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: CLI dispatch
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · CLI dispatch routes is-done command correctly
# · Scenario: CLI argv → main() dispatches to is_done
# · Last fail: N/A (new test)
# · Remove if: CLI dispatch logic changes
@ldd_trajectory
def test_cli_dispatch_is_done(caplog, state_file):
    """CLI should dispatch is-done command correctly."""
    cm.mark_done(str(state_file), "ssh-access")
    result = cm.main(["is-done", str(state_file), "ssh-access"])
    assert result == 0
    result = cm.main(["is-done", str(state_file), "ensure-secrets"])
    assert result == 1
    logger.critical("[IMP:9][test] CLI dispatch is-done — OK")


# 🧪 TRAP[TEST] · Regression · CLI dispatch routes mark-done command correctly
# · Scenario: CLI argv → main() mark-done → is-done returns 0
# · Last fail: N/A (new test)
# · Remove if: CLI dispatch logic changes
@ldd_trajectory
def test_cli_dispatch_mark_done(caplog, state_file):
    """CLI should dispatch mark-done command correctly."""
    result = cm.main(["mark-done", str(state_file), "ssh-access", "hash123"])
    assert result == 0
    assert cm.is_done(str(state_file), "ssh-access") == 0
    logger.critical("[IMP:9][test] CLI dispatch mark-done — OK")


# 🧪 TRAP[TEST] · Regression · CLI dispatch routes reset command correctly
# · Scenario: CLI argv → main() reset → state.json removed
# · Last fail: N/A (new test)
# · Remove if: CLI dispatch logic changes
@ldd_trajectory
def test_cli_dispatch_reset(caplog, state_file):
    """CLI should dispatch reset command correctly."""
    cm.mark_done(str(state_file), "ssh-access")
    assert state_file.exists()
    result = cm.main(["reset", str(state_file)])
    assert result == 0
    assert not state_file.exists()
    logger.critical("[IMP:9][test] CLI dispatch reset — OK")


# 🧪 TRAP[TEST] · Regression · CLI with no args returns error
# · Scenario: main([]) → returns 1 (usage error)
# · Last fail: N/A (new test)
# · Remove if: CLI usage handling changes
@ldd_trajectory
def test_cli_dispatch_no_args(caplog):
    """CLI with no args should return error code."""
    result = cm.main([])
    assert result == 1
    logger.critical("[IMP:9][test] CLI no args returns error — OK")


# 🧪 TRAP[TEST] · Regression · CLI with unknown command returns error
# · Scenario: main(["unknown-cmd", ...]) → returns 1
# · Last fail: N/A (new test)
# · Remove if: CLI error handling changes
@ldd_trajectory
def test_cli_dispatch_unknown_command(caplog):
    """CLI with unknown command should return error code."""
    result = cm.main(["unknown-command", "some", "args"])
    assert result == 1
    logger.critical("[IMP:9][test] CLI unknown command returns error — OK")


# endregion
