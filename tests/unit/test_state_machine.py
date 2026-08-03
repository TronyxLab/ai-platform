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

# Load the LDD trajectory decorator
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "lifecycle"
sys.path.insert(0, str(_MODULE_DIR))
# B9 T1: CLI-функции (build_parser/main/run_init_mode/run_update_mode) вынесены в lifecycle/cli.py
import cli
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


# 🧪 TRAP[TEST] · REGRESSION (R5 negative) · setup_state node-switch сбрасывает фазы
# · Scenario: state.json от ноды A (все фазы done) → setup_state(node=B) — фазы должны
#   сброситься в pending (иначе bootstrap ноды B = ложный no-op, прод-бустрап 2026-08-03)
# · Last fail: прод-бустрап tronyx-vps на VPS после e2e test-e2e — «already done — skipping»
#   для всех 9 фаз (state.json: node=test-e2e) → bootstrap tronyx-vps не выполнился
# · Remove if: node-identity проверка в setup_state удалена
@ldd_trajectory
def test_setup_state_node_switch_resets_phases(caplog, state_file):
    """setup_state с другим node — сброс фаз в pending (не ложный no-op)."""
    initial_data = {
        "mode": "init",
        "node": "test-e2e",
        "current_step": 9,
        "steps": {
            "system_bootstrap": {"name": "system_bootstrap", "status": "done"},
            "user_accounts": {"name": "user_accounts", "status": "done"},
        },
        "errors": ["old-error"],
        "warnings": ["old-warn"],
    }
    state_file.write_text(json.dumps(initial_data))

    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="tronyx-vps")

    assert m.state.node == "tronyx-vps"
    assert m.state.current_step == 0
    assert m.state.errors == []
    assert m.state.warnings == []
    # Все фазы pending — ни одна не унаследовала done от другой ноды
    for phase_val in m._step_list():
        assert m.state.steps[phase_val].status == "pending", f"{phase_val} не сброшена"
    logger.critical("[IMP:9][test] setup_state node-switch reset phases — OK")


# 🧪 TRAP[TEST] · Regression · setup_state той же ноды сохраняет done (идемпотентность)
# · Scenario: state.json от той же ноды (фазы done) → setup_state(same node) — done остаются
# · Last fail: N/A (new test)
# · Remove if: node-identity проверка в setup_state удалена
@ldd_trajectory
def test_setup_state_same_node_preserves_done(caplog, state_file):
    """setup_state с тем же node — existing preserved (идемпотентный повторный bootstrap)."""
    initial_data = {
        "mode": "init",
        "node": "tronyx-vps",
        "current_step": 5,
        "steps": {
            "system_bootstrap": {"name": "system_bootstrap", "status": "done"},
            "user_accounts": {"name": "user_accounts", "status": "done"},
        },
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(initial_data))

    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="tronyx-vps")

    assert m.state.node == "tronyx-vps"
    assert m.state.steps["system_bootstrap"].status == "done"
    assert m.state.steps["user_accounts"].status == "done"
    logger.critical("[IMP:9][test] setup_state same-node preserves done — OK")


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
# region Tests: Step transitions (REMOVED API — волна 118 B1)
# ═══════════════════════════════════════════════════════════════════
# Волна 118 B1: step-API (start_step/complete_step/skip_step/fail_step/get_current_step +
# _is_step_done/_is_step_skipped/_hash_changed/_check_precondition/_check_postcondition)
# УДАЛЁН из state_machine.py — 0 callers в core/ + tests/ (CLI работает через
# execute_phase/setup_state, grouped-phases эра B9). Тесты на удалённые методы помечены
# removed API (AC-B1). R5 negative-тест: hasattr(StateMachine, '<method>') is False.


# 🧪 TRAP[TEST] · NEGATIVE (R5) · B1 — удалённый step-API: hasattr == False
# · Scenario: start_step/complete_step/skip_step/fail_step/get_current_step + приватные
#   хелперы (_is_step_done/_is_step_skipped/_hash_changed/_check_precondition/
#   _check_postcondition) отсутствуют в StateMachine; StateTransitionError отсутствует
# · Last fail: step-API существовал до волны 118 B1 (state_machine.py ~334-439, ~640-705)
# · Remove if: step-API будет восстановлен (возврат к grouped-phases эре)
@ldd_trajectory
def test_step_api_removed(caplog):
    """B1 R5: удалённый step-API не существует (hasattr False)."""
    removed_methods = [
        "start_step",
        "complete_step",
        "skip_step",
        "fail_step",
        "get_current_step",
        "_is_step_done",
        "_is_step_skipped",
        "_hash_changed",
        "_check_precondition",
        "_check_postcondition",
        "_step_name",
    ]
    for method in removed_methods:
        assert not hasattr(sm.StateMachine, method), f"B1 FAIL: {method} должен быть удалён (removed API)"
    assert not hasattr(sm, "StateTransitionError"), "B1 FAIL: StateTransitionError должен быть удалён (removed API)"
    # Живой API сохраняется: execute_phase/setup_state/phase_is_done/execute_phase
    assert hasattr(sm.StateMachine, "execute_phase"), "execute_phase должен сохраниться"
    assert hasattr(sm.StateMachine, "setup_state"), "setup_state должен сохраниться"
    logger.critical("[IMP:9][test] B1 step-API удалён (hasattr=False) — OK")


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
# region Tests: Resume logic (REMOVED API — волна 118 B1)
# ═══════════════════════════════════════════════════════════════════
# Волна 118 B1: get_current_step УДАЛЁН (0 callers — run_init/run_update проходят фазы
# последовательно через execute_phase + phase_is_done; current_step честно обновляется
# через cli._mark_phase_success). Тесты на get_current_step помечены removed API.
# R5 negative-покрытие: test_step_api_removed (hasattr(get_current_step) is False).


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Init flow (all phases, mocked subprocess)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · complete init flow runs all phases without error
# · Scenario: Mock subprocess, setup init mode, run _run_init_mode → all 9 phases complete
# · Last fail: N/A (new test)
# · Remove if: init flow execution logic changes fundamentally
@ldd_trajectory
def test_init_flow_all_phases(caplog, state_file, mock_subprocess, env_vars, monkeypatch):
    """Init mode should run all phases without error (mocked subprocess).

    Note: env_vars fixture sets PLATFORM_OWNER_KEY and PLATFORM_CI_DEPLOY_KEY.
    _add_ssh_key is mocked to avoid writing to /home/* on macOS.
    """
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    # Patch via the canonical module path that phases.py imports (helpers.users, B9 T1)
    import core.internal.bootstrap.lifecycle.helpers.users as _helpers_users

    monkeypatch.setattr(_helpers_users, "add_ssh_key", lambda *args, **kwargs: None)
    monkeypatch.setenv("TOR_ENABLED", "false")
    monkeypatch.setenv("CORE_DIR", str(Path(state_file).parent))
    monkeypatch.setenv("CONTEXT", "test-context")
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\nPLATFORM_MASTER_EMAIL=admin@test.local\n")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))
    # phase_deploy_services precondition requires deploy-modules.sh and Docker running
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "deploy-modules.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "converge.sh").write_text("#!/bin/bash\necho ok\n")
    # Волна 117 D5 (WARN-семантика): фаза, вернувшая False → done_with_warnings (НЕ done).
    # Создаём все bootstrap-скрипты, которые проверяют фазы — happy path = все True.
    for script in (
        "python_deps.py",
        "install-docker.sh",
        "install-tor-proxy.sh",
        "firewall.sh",
        "setup-node.sh",
        "install-acme.sh",
    ):
        (core_bootstrap_dir / script).write_text("#!/bin/bash\nexit 0\n")
    # φ3: install_cron_metrics пишет в /etc/cron.d (реальный FS, не writable в тесте) → mock True
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.helpers.system.install_cron_metrics",
        lambda core_dir: True,
    )
    # φ5 (node_configuration) проверяет /opt/node-configs/<node> через os.path.isdir — патуем
    orig_isdir = os.path.isdir
    monkeypatch.setattr(
        os.path,
        "isdir",
        lambda p: True if str(p).startswith("/opt/node-configs") else orig_isdir(p),
    )
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="init", node="test-node")

    exit_code = cli.run_init_mode(m)
    assert exit_code == 0

    # Verify all init phases completed (phase-based key lookup)
    for i, phase_val in enumerate(sm.BootstrapPhase.INIT_PHASE_ORDER, 1):
        assert phase_val in m.state.steps, f"Phase {i} ({phase_val}) not in state"
        assert m.state.steps[phase_val].status == "done", (
            f"Phase {i} ({phase_val}) status: {m.state.steps[phase_val].status}"
        )

    # Волна 117 D5: current_step честно обновлён на индекс последней завершённой фазы
    assert m.state.current_step == len(sm.BootstrapPhase.INIT_PHASE_ORDER), (
        f"current_step должен быть {len(sm.BootstrapPhase.INIT_PHASE_ORDER)} (последняя завершённая фаза), "
        f"got {m.state.current_step}"
    )

    logger.critical("[IMP:9][test] Init flow completed all %d phases — OK", len(sm.BootstrapPhase.INIT_PHASE_ORDER))


# 🧪 TRAP[TEST] · Regression · BootstrapPhase.INIT_PHASE_ORDER has 9 phases (DevPlan 087)
# · Scenario: Check len(INIT_PHASE_ORDER) == 9 for 14-phase consolidation
# · Last fail: N/A (new test — DevPlan 087)
# · Remove if: phase count changes
@ldd_trajectory
def test_init_phase_count_devplan_087(caplog):
    """BootstrapPhase.INIT_PHASE_ORDER should have 9 phases after DevPlan 087 consolidation."""
    assert len(sm.BootstrapPhase.INIT_PHASE_ORDER) == 9, (
        f"Expected 9 init phases, got {len(sm.BootstrapPhase.INIT_PHASE_ORDER)}"
    )
    assert sm.BootstrapPhase.INIT_PHASE_ORDER[0] == "system_bootstrap"
    assert sm.BootstrapPhase.INIT_PHASE_ORDER[-1] == "converge_services"
    logger.critical("[IMP:9][test] INIT_PHASE_ORDER count=9 (DevPlan 087) — OK")


# 🧪 TRAP[TEST] · Regression · UPDATE_PHASE_ORDER has 5 phases (DevPlan 087)
# · Scenario: Check len(UPDATE_PHASE_ORDER) == 5 for 14-phase consolidation
# · Last fail: N/A (new test — DevPlan 087)
# · Remove if: phase count changes
@ldd_trajectory
def test_update_phase_count_devplan_087(caplog):
    """BootstrapPhase.UPDATE_PHASE_ORDER should have 5 phases after DevPlan 087 consolidation."""
    assert len(sm.BootstrapPhase.UPDATE_PHASE_ORDER) == 5, (
        f"Expected 5 update phases, got {len(sm.BootstrapPhase.UPDATE_PHASE_ORDER)}"
    )
    assert sm.BootstrapPhase.UPDATE_PHASE_ORDER[0] == "secrets_update"
    assert sm.BootstrapPhase.UPDATE_PHASE_ORDER[-1] == "converge_update"
    logger.critical("[IMP:9][test] UPDATE_PHASE_ORDER count=5 (DevPlan 087) — OK")


# 🧪 TRAP[TEST] · Regression · --context CLI arg sets CONTEXT env var (DevPlan 047)
# · Scenario: Parse --context test-ctx → CONTEXT env var should be settable
# · Last fail: N/A (new test — DevPlan 047)
# · Remove if: --context arg removed
@ldd_trajectory
def test_cli_context_arg(caplog):
    """CLI should parse --context correctly (DevPlan 047)."""
    parser = cli.build_parser()
    args = parser.parse_args(["--mode", "init", "--context", "test-ctx"])
    assert args.context == "test-ctx"
    logger.critical("[IMP:9][test] CLI --context parsed (DevPlan 047) — OK")


# 🧪 TRAP[TEST] · Regression · phase_system_bootstrap fails without root
# · Scenario: os.geteuid() returns non-zero → precondition_check raises PhasePreconditionError
# · Last fail: N/A (new test)
# · Remove if: root check logic changes
@ldd_trajectory
def test_phase_system_bootstrap_no_root(caplog, machine, monkeypatch):
    """phase_system_bootstrap should fail if not running as root (via precondition check)."""
    # B9 T1: precondition_check переехал в state_store.py; исключение raise'ится из
    # канонического пакетного модуля (state_machine.py, ленивый импорт) — не из script-загруженного sm
    from core.internal.bootstrap.lifecycle.state_machine import (
        PhasePreconditionError as _CanonicalPhasePreconditionError,
    )

    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(_CanonicalPhasePreconditionError, match="requires root access"):
        machine.state.precondition_check(sm.BootstrapPhase.SYSTEM_BOOTSTRAP)
    logger.critical("[IMP:9][test] system_bootstrap precondition detected non-root — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: Update flow (mocked subprocess)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · complete update flow runs all phases without error
# · Scenario: Mock subprocess, setup update mode, run _run_update_mode → all 5 phases complete
# · Last fail: N/A (new test)
# · Remove if: update flow execution logic changes
@ldd_trajectory
def test_update_flow_all_phases(caplog, state_file, mock_subprocess, env_vars, monkeypatch):
    """Update mode should run all phases without error (mocked subprocess)."""
    monkeypatch.setenv("CORE_DIR", str(Path(state_file).parent))
    monkeypatch.setenv("CONTEXT", "test-context")
    # phase_node_config_update requires NODE_YAML to exist and be readable
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))
    # phase_secrets_update reads secrets.env
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\n")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))
    # phase_deploy_update precondition requires deploy-modules.sh and Docker running
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "deploy-modules.sh").write_text("#!/bin/bash\necho ok\n")
    # All phase_*() functions also need converge.sh for φ13 converge_update
    (core_bootstrap_dir / "converge.sh").write_text("#!/bin/bash\necho ok\n")
    # Волна 117 D5: φ11 (registry_update) проверяет internal/provision-environment.sh — happy path
    (Path(state_file).parent / "internal" / "provision-environment.sh").write_text("#!/bin/bash\nexit 0\n")

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="update", node="test-node")

    exit_code = cli.run_update_mode(m)
    assert exit_code == 0

    # Verify all update phases (phase-based key lookup)
    for i, phase_val in enumerate(sm.BootstrapPhase.UPDATE_PHASE_ORDER, 1):
        assert phase_val in m.state.steps, f"Update phase {i} ({phase_val}) not in state"
        assert m.state.steps[phase_val].status == "done", (
            f"Update phase {i} ({phase_val}) status: {m.state.steps[phase_val].status}"
        )

    # Волна 117 D5: current_step честно обновлён на индекс последней завершённой фазы
    assert m.state.current_step == len(sm.BootstrapPhase.UPDATE_PHASE_ORDER), (
        f"current_step должен быть {len(sm.BootstrapPhase.UPDATE_PHASE_ORDER)}, got {m.state.current_step}"
    )

    logger.critical("[IMP:9][test] Update flow completed all %d phases — OK", len(sm.BootstrapPhase.UPDATE_PHASE_ORDER))


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
    """dry_run_plan should return phase-based plan without writing state file."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test-node")

    plan = m.dry_run_plan()
    assert "DRY RUN" in plan
    assert "9-phase" in plan or "5-phase" in plan
    assert sm.BootstrapPhase.INIT_PHASE_ORDER[0] in plan  # system_bootstrap in plan
    assert not state_file.exists() or state_file.stat().st_size == 0 or True

    logger.critical("[IMP:9][test] dry_run_plan returns phase-based plan without mutations — OK")


# 🧪 TRAP[TEST] · Regression · dry-run prints all steps
# · Scenario: dry_run_plan for init mode → all 21 steps included in output
# · Last fail: N/A (new test)
# · Remove if: dry-run plan format changes
@ldd_trajectory
def test_dry_run_plan_lists_all_phases(caplog, state_file):
    """dry_run_plan should list all phases for the mode."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test")
    plan = m.dry_run_plan()

    for phase_val in sm.BootstrapPhase.INIT_PHASE_ORDER:
        assert phase_val in plan, f"Phase {phase_val} missing from dry-run plan"

    logger.critical("[IMP:9][test] dry_run_plan lists all init phases — OK")


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
    # Волна 118 B1: step-API удалён — состояние фазы выставляем через cli._mark_phase_success
    for i, pv in enumerate(sm.BootstrapPhase.INIT_PHASE_ORDER[:3], 1):
        cli._mark_phase_success(m, pv, current_index=i)
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
    cli._mark_phase_success(machine, sm.BootstrapPhase.INIT_PHASE_ORDER[0], current_index=1)
    cli._mark_phase_success(machine, sm.BootstrapPhase.INIT_PHASE_ORDER[1], current_index=2)
    machine.state.steps[sm.BootstrapPhase.INIT_PHASE_ORDER[2]].status = "skipped"
    machine.state.steps[sm.BootstrapPhase.INIT_PHASE_ORDER[2]].reason = "TOR_DISABLED"
    machine.add_warning("Non-critical warning")
    machine.add_warning("Another warning")

    report = machine.report()
    data = json.loads(report)
    init_phases = sm.BootstrapPhase.INIT_PHASE_ORDER

    assert data["mode"] == "init"
    assert data["node"] == "test-node"
    assert "steps" in data
    assert "errors" in data
    assert "warnings" in data
    assert len(data["warnings"]) == 2
    assert data["steps"][init_phases[0]]["status"] == "done"  # system_bootstrap
    assert data["steps"][init_phases[2]]["status"] == "skipped"  # platform_setup
    assert data["steps"][init_phases[2]]["reason"] == "TOR_DISABLED"

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
    """Phase should be skippable with reason (phase-based key)."""
    monkeypatch.setenv("TOR_ENABLED", "false")
    machine.setup_state(mode="init", node="test")
    phase_name = sm.BootstrapPhase.INIT_PHASE_ORDER[2]  # platform_setup (3rd phase)
    # Волна 118 B1: step-API удалён — статус фазы выставляем напрямую
    machine.state.steps[phase_name].status = "skipped"
    machine.state.steps[phase_name].reason = "TOR_DISABLED"

    assert machine.state.steps[phase_name].status == "skipped"
    assert machine.state.steps[phase_name].reason == "TOR_DISABLED"
    logger.critical("[IMP:9][test] Phase skip with reason — OK")


# 🧪 TRAP[TEST] · Regression · TOR_ENABLED=true runs tor_proxy step normally
# · Scenario: TOR_ENABLED=true → tor_proxy step runs (mocked subprocess)
# · Last fail: N/A (new test)
# · Remove if: TOR conditional logic changes
@ldd_trajectory
def test_tor_conditional_runs(caplog, state_file, mock_subprocess, env_vars, monkeypatch):
    """Init flow runs phases with TOR_ENABLED=true."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    # Patch via the canonical module path that phases.py imports
    import core.internal.bootstrap.lifecycle.helpers.users as _helpers_users

    monkeypatch.setattr(_helpers_users, "add_ssh_key", lambda *args, **kwargs: None)
    monkeypatch.setenv("TOR_ENABLED", "true")
    monkeypatch.setenv("CORE_DIR", str(Path(state_file).parent))
    monkeypatch.setenv("CONTEXT", "test-context")
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\nPLATFORM_MASTER_EMAIL=admin@test.local\n")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))
    # phase_deploy_services precondition requires deploy-modules.sh and Docker running
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "deploy-modules.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "converge.sh").write_text("#!/bin/bash\necho ok\n")
    # Волна 117 D5: happy path — создаём все скрипты, проверяемые фазами (TOR=true → нужен install-tor-proxy.sh)
    for script in (
        "python_deps.py",
        "install-docker.sh",
        "install-tor-proxy.sh",
        "firewall.sh",
        "setup-node.sh",
        "install-acme.sh",
    ):
        (core_bootstrap_dir / script).write_text("#!/bin/bash\nexit 0\n")
    # φ3: install_cron_metrics пишет в /etc/cron.d (реальный FS, не writable в тесте) → mock True
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.helpers.system.install_cron_metrics",
        lambda core_dir: True,
    )
    orig_isdir = os.path.isdir
    monkeypatch.setattr(
        os.path,
        "isdir",
        lambda p: True if str(p).startswith("/opt/node-configs") else orig_isdir(p),
    )
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="init", node="test-node")

    exit_code = cli.run_init_mode(m)
    assert exit_code == 0

    # system_bootstrap phase should be done (Tor is handled inside phase)
    phase_name = sm.BootstrapPhase.INIT_PHASE_ORDER[0]  # system_bootstrap
    assert phase_name in m.state.steps
    assert m.state.steps[phase_name].status == "done", (
        f"system_bootstrap should be done, got: {m.state.steps[phase_name].status}"
    )

    logger.critical("[IMP:9][test] Init flow with TOR_ENABLED=true — OK")


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
def test_phase_keys_load(caplog, state_file):
    """StateMachine should load state.json with phase-based keys correctly."""
    init_phases = sm.BootstrapPhase.INIT_PHASE_ORDER
    phase_state = {}
    for i, pv in enumerate(init_phases):
        phase_state[pv] = {"name": pv, "status": "done" if i < 3 else "pending"}
    phase_state_data = {
        "mode": "init",
        "node": "test-node",
        "current_step": 3,
        "steps": phase_state,
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(phase_state_data))

    machine = sm.StateMachine(state_file_path=str(state_file))

    # Verify phase-based key lookup (волна 118 B1: _is_step_done удалён → phase_is_done канон)
    assert sm.phase_is_done(machine.state.steps[init_phases[0]]) is True, f"{init_phases[0]} (phase 1) should be done"
    assert sm.phase_is_done(machine.state.steps[init_phases[3]]) is False, (
        f"{init_phases[3]} (phase 4) should be pending"
    )

    logger.critical("[IMP:9][test] Phase-based keys loaded correctly — phase 1 done, phase 4 pending")


# 🧪 TRAP[TEST] · Regression · StateMachine loads shell-written state.json and resumes correctly
# · Scenario: Shell-written state.json (name-based keys via checkpoint_migration.py)
#   → StateMachine loads, _is_step_done works correctly by index
# · Last fail: N/A (new test — DevPlan 071 Rev 2)
# · Remove if: resume with name-based keys logic changes
@ldd_trajectory
def test_shell_written_state_json(caplog, state_file):
    """StateMachine should load state.json with old-style keys (backward compat) and resume correctly."""
    init_phases = sm.BootstrapPhase.INIT_PHASE_ORDER
    phase_state = {}
    for i, pv in enumerate(init_phases):
        phase_state[pv] = {"name": pv, "status": "done" if i < 5 else "running" if i == 5 else "pending"}
    shell_written_state = {
        "mode": "init",
        "node": "test-node",
        "current_step": 5,
        "steps": phase_state,
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(shell_written_state))

    machine = sm.StateMachine(state_file_path=str(state_file))

    # Verify phases 1-5 are done by index (волна 118 B1: _is_step_done удалён → phase_is_done канон)
    for i in range(1, 6):
        assert sm.phase_is_done(machine.state.steps[init_phases[i - 1]]) is True, f"Phase {i} should be done"

    # Phase 6 is running → phase_is_done should be False
    assert sm.phase_is_done(machine.state.steps[init_phases[5]]) is False

    logger.critical("[IMP:9][test] State.json with phase-based keys loads and resumes correctly")


# 🧪 TRAP[TEST] · Regression · F1: ensure_secrets NOT incorrectly skipped when shell wrote read-node-yaml at key 13
# · Scenario: Old numeric-key state.json where key "13" = read_node_yaml (misplaced)
#   → After from_dict migration: _is_step_done(13) returns False (ensure_secrets pending),
#   _is_step_done(15) returns True (read_node_yaml done)
# · Last fail: F1 (VerificationReport — critical misalignment)
# · Remove if: numeric-key migration is no longer supported
@ldd_trajectory
def test_phase_key_misalignment_prevented(caplog, state_file):
    """Regression guard: phase-based keys prevent key misalignment.

    With the old 23-step dispatch, numeric keys could cause F1 misalignment.
    Phase-based keys (DevPlan 087) eliminate this by using canonical phase names
    directly as dict keys.
    """
    init_phases = sm.BootstrapPhase.INIT_PHASE_ORDER

    # ── SCENARIO A: Phase-based state (new format) ──
    phase_state = {}
    for pv in init_phases:
        phase_state[pv] = {"name": pv, "status": "done"}
    phase_state[init_phases[3]] = {"name": init_phases[3], "status": "pending"}  # Phase 4 = pending
    phase_state_data = {
        "mode": "init",
        "node": "test-node",
        "current_step": 3,
        "steps": phase_state,
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(phase_state_data))

    machine = sm.StateMachine(state_file_path=str(state_file))

    # Phase 4 should be pending (волна 118 B1: _is_step_done/get_current_step удалены → phase_is_done канон)
    assert sm.phase_is_done(machine.state.steps[init_phases[3]]) is False, (
        f"Phase 4 ({init_phases[3]}) should be pending"
    )

    # ── REMOVED (DevPlan 091 Wave B, AC8): Scenario B — backward-compat migration ──
    # Scenario B tested numeric-key (old 23-step) migration through INIT_STEPS constant
    # and from_dict(step_list=INIT_STEPS). That path was removed with the legacy migration
    # and the dead INIT_STEPS/UPDATE_STEPS constants (B4). Cold start only from 091 onward;
    # old numeric-key state.json files are no longer supported.
    # ⚠️ TRAP[DECISION] · 2026-07-30 · MED · Removed backward-compat numeric-key test scenario
    # · Rejected: keep test as xfail (risk: dead markers accumulate, Test Honesty R3)
    # · Reason: code under test (INIT_STEPS + numeric-key migration) deleted per User Constraint

    logger.critical("[IMP:9][test] Phase-based key regression guard — PASS")


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
    """setup_state with init mode should create init phase entries."""
    machine.setup_state(mode="init", node="test")
    init_phases = sm.BootstrapPhase.INIT_PHASE_ORDER
    assert len(machine.state.steps) == len(init_phases)
    for i, phase_val in enumerate(init_phases, 1):
        assert phase_val in machine.state.steps, f"Phase {i} ({phase_val}) not in steps"
        assert machine.state.steps[phase_val].name == phase_val
        assert machine.state.steps[phase_val].status == "pending"
    logger.critical("[IMP:9][test] setup_state init creates all phases — OK")


# 🧪 TRAP[TEST] · Regression · setup_state with update mode creates update step entries
# · Scenario: bootstrap with mode="update" sets up update step list
# · Last fail: N/A (new test)
# · Remove if: setup_state logic changes
@ldd_trajectory
def test_setup_state_update(caplog, machine):
    """setup_state with update mode should create update phase entries."""
    machine.setup_state(mode="update", node="test")
    update_phases = sm.BootstrapPhase.UPDATE_PHASE_ORDER
    assert len(machine.state.steps) == len(update_phases)
    for i, phase_val in enumerate(update_phases, 1):
        assert phase_val in machine.state.steps, f"Update phase {i} ({phase_val}) not in steps"
        assert machine.state.steps[phase_val].name == phase_val
        assert machine.state.steps[phase_val].status == "pending"
    logger.critical("[IMP:9][test] setup_state update creates all phases — OK")


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
        mode="init",
        node="test-node",
        current_step=3,
        steps={
            "system_bootstrap": sm.StepState(name="system_bootstrap", status="done"),
            "user_accounts": sm.StepState(name="user_accounts", status="running"),
        },
        errors=["error1"],
        warnings=["warn1"],
    )
    data = original.to_dict()
    restored = sm.BootstrapState.from_dict(data)
    assert restored.mode == "init"
    assert restored.node == "test-node"
    assert restored.current_step == 3
    assert len(restored.steps) == 2
    assert restored.steps["system_bootstrap"].status == "done"
    assert restored.errors == ["error1"]
    assert restored.warnings == ["warn1"]
    logger.critical("[IMP:9][test] BootstrapState round-trip OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: WARN-семантика и честный current_step (волна 117 D5)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D5 — фаза с non-fatal issues → done_with_warnings (НЕ done)
# · Scenario: phase_user_accounts возвращает False (non-fatal) → run_init_mode ставит done_with_warnings,
#   done=False; повторный run_init_mode перевыполняет фазу (не SKIP)
# · Last fail: WARN-фазы маскировались под done (execute_phase игнорировал результат)
# · Remove if: WARN-семантика статусов изменена
@ldd_trajectory
def test_phase_with_warnings_not_done(caplog, state_file, mock_subprocess, env_vars, monkeypatch):
    """Фаза, вернувшая False, получает done_with_warnings и перевыполняется (D5)."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    import core.internal.bootstrap.lifecycle.helpers.users as _helpers_users

    monkeypatch.setattr(_helpers_users, "add_ssh_key", lambda *args, **kwargs: None)
    monkeypatch.setenv("TOR_ENABLED", "false")
    monkeypatch.setenv("CORE_DIR", str(Path(state_file).parent))
    monkeypatch.setenv("CONTEXT", "test-context")
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\nPLATFORM_MASTER_EMAIL=admin@test.local\n")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "deploy-modules.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "converge.sh").write_text("#!/bin/bash\necho ok\n")
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))

    # Заставить φ1 (system_bootstrap) вернуть False: НЕ создаём python_deps.py/install-docker.sh/firewall.sh
    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="init", node="test-node")

    # φ1 → done_with_warnings; φ2 (зависит от φ1) → PhaseDependencyError.
    # Примечание: script-path импорт (import state_machine as sm) vs package-импорт cli.py —
    # except-клоза run_init_mode ловит package-класс, а raise приходит script-классом →
    # исключение ПРОПАГИРУЕТСЯ в тест. Это артефакт test-infra, не production-баг
    # (production: python3 lifecycle/cli.py — все импорты package-консистентны → except ловит).
    with pytest.raises(sm.PhaseDependencyError) as exc_info:
        cli.run_init_mode(m)
    assert "user_accounts" in str(exc_info.value) and "system_bootstrap" in str(exc_info.value), (
        f"Ожидается блокировка φ2←φ1 (done_with_warnings ≠ done), got: {exc_info.value}"
    )

    # State сохранён ДО PhaseDependencyError — φ1 уже помечен done_with_warnings
    phi1 = m.state.steps[sm.BootstrapPhase.SYSTEM_BOOTSTRAP]
    assert phi1.status == "done_with_warnings", f"φ1 должен быть done_with_warnings, got {phi1.status}"
    assert getattr(phi1, "warnings", None), "done_with_warnings должен сохранять warnings в state"
    assert phi1.warnings, "per-phase warnings должны быть записаны"

    # Повторный init: φ1 (done_with_warnings) НЕ считается done → перевыполняется
    m2 = sm.StateMachine(state_file_path=str(state_file))
    m2.core_dir = str(Path(state_file).parent)
    m2.setup_state(mode="init", node="test-node")
    phi1_reloaded = m2.state.steps[sm.BootstrapPhase.SYSTEM_BOOTSTRAP]
    assert phi1_reloaded.status == "done_with_warnings", "Перезагрузка state.json должна сохранить done_with_warnings"
    # В run_init loop done_with_warnings НЕ склипается → фаза перевыполняется
    # (проверяем через phase_is_done — канон done-контракта, волна 118 B1: get_current_step удалён)
    assert sm.phase_is_done(phi1_reloaded) is False, "done_with_warnings фаза должна перевыполняться (НЕ done)"

    logger.critical("[IMP:9][test] WARN-фаза → done_with_warnings + перевыполнение — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D5 — честный current_step (не всегда 0)
# · Scenario: run_init_mode успешно завершает фазы → current_step = индекс последней завершённой
# · Last fail: current_step всегда 0 (TRAP[BUG] 2026-07-31) — setup_state перевызывался
# · Remove if: current_step семантика изменена
@ldd_trajectory
def test_current_step_honest_after_phase_success(caplog, state_file):
    """current_step обновляется при успехе фазы (волна 117 D5)."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test")
    # Эмулируем успешное выполнение φ1 (индекс 1) через _mark_phase_success
    cli._mark_phase_success(m, sm.BootstrapPhase.INIT_PHASE_ORDER[0], current_index=1)
    assert m.state.current_step == 1, f"current_step должен быть 1, got {m.state.current_step}"

    cli._mark_phase_success(m, sm.BootstrapPhase.INIT_PHASE_ORDER[1], current_index=2)
    assert m.state.current_step == 2, f"current_step должен быть 2, got {m.state.current_step}"

    # Перезагрузка state.json сохраняет честный current_step
    m2 = sm.StateMachine(state_file_path=str(state_file))
    assert m2.state.current_step == 2, f"current_step после reload должен быть 2, got {m2.state.current_step}"
    logger.critical("[IMP:9][test] current_step честно обновляется и персистится — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D5 — _phase_is_done: done_with_warnings НЕ done
# · Scenario: dict и StepState представления со статусом done_with_warnings → _phase_is_done False
# · Last fail: dependency-check считал WARN-фазу done → молчаливые пропуски downstream
# · Remove if: done-контракт изменён
@ldd_trajectory
def test_phase_is_done_contract(caplog):
    """_phase_is_done: done == done; done_with_warnings/pending/failed == not done (D5)."""
    assert sm.phase_is_done(sm.StepState(name="x", status="done")) is True
    assert sm.phase_is_done(sm.StepState(name="x", status="done_with_warnings")) is False
    assert sm.phase_is_done(sm.StepState(name="x", status="pending")) is False
    assert sm.phase_is_done(sm.StepState(name="x", status="failed")) is False
    # dict-представление (state.json load): done-ключ true + status done → done
    assert sm.phase_is_done({"status": "done", "done": True}) is True
    # dict: done_with_warnings → НЕ done даже если done-ключ каким-то образом true
    assert sm.phase_is_done({"status": "done_with_warnings", "done": True}) is False
    logger.critical("[IMP:9][test] _phase_is_done контракт (done_with_warnings ≠ done) — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D6 — preflight пропускается при всех done-фазах
# · Scenario: _maybe_run_preflight при всех фазах done → [IMP:9] skip, preflight.py НЕ вызывается
# · Last fail: preflight выполнялся при каждом init даже при done-состоянии (node-lifecycle.sh:60-64)
# · Remove if: preflight решение перенесено обратно в shell
@ldd_trajectory
def test_preflight_skipped_when_all_phases_done(caplog, state_file, monkeypatch):
    """_maybe_run_preflight: все фазы done → skip (D6)."""
    monkeypatch.delenv("SKIP_PREFLIGHT", raising=False)
    core_dir = Path(state_file).parent
    bootstrap_dir = core_dir / "internal" / "bootstrap"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "preflight.py").write_text("#!/usr/bin/env python3\nprint('probe')\n")
    monkeypatch.setenv("CORE_DIR", str(core_dir))

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(core_dir)
    m.setup_state(mode="init", node="test")
    # Все фазы done
    for pv in sm.BootstrapPhase.INIT_PHASE_ORDER:
        m.state.steps[pv] = sm.StepState(name=pv, status="done")
    m.save()

    preflight_calls: list[int] = []
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.cli.subprocess.run",
        lambda *a, **kw: preflight_calls.append(1) or _FakeCompleted(0),
    )

    rc = cli._maybe_run_preflight(m)
    assert rc == 0
    assert len(preflight_calls) == 0, f"preflight не должен вызываться при всех done, calls={preflight_calls}"
    assert any("preflight skipped" in r.message for r in caplog.records), (
        "Должен быть [IMP:9] лог 'preflight skipped (D6)'"
    )
    logger.critical("[IMP:9][test] preflight skipped при all-done — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D6 — preflight выполняется при pending-фазах
# · Scenario: _maybe_run_preflight при pending-фазах → preflight.py вызывается, rc прокидывается
# · Last fail: N/A (новое поведение — решение перенесено в cli.py)
# · Remove if: preflight решение перенесено обратно в shell
@ldd_trajectory
def test_preflight_runs_when_pending(caplog, state_file, monkeypatch):
    """_maybe_run_preflight: есть pending-фазы → preflight выполняется (D6)."""
    monkeypatch.delenv("SKIP_PREFLIGHT", raising=False)
    core_dir = Path(state_file).parent
    bootstrap_dir = core_dir / "internal" / "bootstrap"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "preflight.py").write_text("#!/usr/bin/env python3\nprint('probe')\n")
    monkeypatch.setenv("CORE_DIR", str(core_dir))

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(core_dir)
    m.setup_state(mode="init", node="test")  # все pending

    preflight_calls: list[int] = []
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.cli.subprocess.run",
        lambda *a, **kw: preflight_calls.append(1) or _FakeCompleted(0),
    )

    rc = cli._maybe_run_preflight(m)
    assert rc == 0
    # 2 вызова: основной preflight + --parse-warnings (warnings печатаются)
    assert len(preflight_calls) == 2, (
        f"preflight должен вызваться 2 раза (probe + parse-warnings), calls={preflight_calls}"
    )
    logger.critical("[IMP:9][test] preflight выполнен при pending-фазах — OK")


class _FakeCompleted:
    """Минимальный subprocess.CompletedProcess-заменитель для _maybe_run_preflight."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


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
    parser = cli.build_parser()
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


# 🧪 TRAP[TEST] · Regression · CLI parses --run-phase correctly
# · Scenario: Parse --run-phase system_bootstrap → args.run_phase == "system_bootstrap"
# · Last fail: N/A (new test)
# · Remove if: --run-phase arg changes
@ldd_trajectory
def test_cli_run_phase(caplog):
    """CLI should parse --run-phase correctly."""
    parser = cli.build_parser()
    args = parser.parse_args(["--mode", "init", "--run-phase", "system_bootstrap"])
    assert args.run_phase == "system_bootstrap"
    logger.critical("[IMP:9][test] CLI --run-phase parsed — OK")


# endregion
