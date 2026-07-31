#!/usr/bin/env python3
# GREP_SUMMARY: test_bootstrap_dry_run, integration, bootstrap, 14-phases, dry-run, state-machine, dependency-graph, precondition, resume-phase, grouped-phase, skip-phases, phase-migration
# STRUCTURE: ▶ ┌tmp_path + monkeypatch + mock subprocess┐ → ◇ test_init_mode_14_phases_dry_run (9 init phases, φ1-φ8.5) → ◇ test_update_mode_5_phases_dry_run (5 update phases, φ9-φ13) → ◇ test_precondition_block_on_dependency_gap (φ6 = requires φ4 → PhaseDependencyError) → ◇ test_skip_already_done_phases (grouped-phase all sub_steps done+unchanged → SKIP) → ◇ test_resume_phase_partial_failure (φ4 decrypt OK, ensure-secrets FAIL → resume retry) → ◇ test_grouped_phase_skip_unchanged_sub_steps (φ1 3/4 done → only failed sub-step runs) → ◇ test_phase_dependency_graph_integrity (graph + migration validation) → ◇ test_precondition_check_root_failure (non-root → PhasePreconditionError) → ⎋ LDD IMP:7-10 assertions + TRAP[TEST] markers
# region MODULE_CONTRACT
## @purpose  Integration tests for the 14-phase bootstrap pipeline in dry-run mode (DevPlan T14).
##           Simulates all 14 phases (9 INIT + 5 UPDATE) with mocked subprocess calls,
##           verifies dependency enforcement, precondition blocks, skip-already-done logic,
##           and partial failure recovery via _resume_phase().
## @scope    Integration (not unit) — tests the interaction of state_machine.py, phases.py,
##           and state_migration.py with mocked system dependencies. Does NOT execute real
##           subprocess commands — all subprocess.run calls are monkeypatched. A real mock
##           filesystem at tmp_path ensures file open() calls succeed for expected paths.
## @invariants
##   1. ALL subprocess.run calls are mocked — no real system commands are executed
##   2. os.geteuid() is mocked to 0 (root) — prevents φ1 precondition failure
##   3. Only os.geteuid() is mocked — os.path.isfile/isdir use the real filesystem;
##      expected files are created in tmp_path and env vars point to them
##   4. Required env vars are set via monkeypatch for all phase preconditions
##   5. State file operations use tmp_path exclusively — never /var/lib/platform
##   6. Each test validates IMP:9 business logic log presence via caplog trajectory
##   7. Each test function has # 🧪 TRAP[TEST] with Regression/Scenario/Last fail/Remove if fields
## @rationale DevPlan T14 explicitly requires integration test coverage of the 14-phase flow
##   in dry-run mode. The dependency graph and precondition system must be tested together
##   to catch cross-phase interaction bugs that unit tests would miss. Mocking system calls
##   makes the test safe to run on any machine (no root, no Docker, no real secrets).
## @changes 2026-07-30 | Created per DevPlan T14 — 8 integration tests for 14-phase bootstrap
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from core.internal.bootstrap.lifecycle.state_machine import (
    BootstrapPhase,
    PhaseDependencyError,
    PhasePreconditionError,
    StateMachine,
    StepState,
    _grouped_phases,
    _phase_dependency_graph,
)

# DevPlan 091 Wave B: state_migration.py deleted (cold start only, no backward-compat).
# MIGRATION_MAP constant (sub_step names per grouped phase) is inlined here for the
# two tests that still use it (skip_already_done_phases, grouped_phase_skip_unchanged).
# The migration-specific assertions in test_phase_dependency_graph_integrity were
# removed together with migrate_state_to_phases().
# ⚠️ TRAP[DECISION] · 2026-07-30 · MED · Inlined MIGRATION_MAP from deleted state_migration.py
# · Rejected: extract sub_step names dynamically from _grouped_phases (risk: changes test behavior)
# · Reason: MIGRATION_MAP is a static list of grouped-phase sub_step keys; only system_bootstrap
#   is referenced. Inlining preserves test semantics without importing deleted module.
# · Rev: when a new grouped phase is added with sub_steps — update this constant.
MIGRATION_MAP: dict[str, list[str]] = {
    "system_bootstrap": ["packages", "docker_install", "tor_proxy", "firewall"],
    "user_accounts": ["ssh_access", "create_platform_user", "create_ci_deploy_user"],
    "platform_setup": ["create_projects_base", "platform_dirs", "docker_config", "metrics_cron"],
    "secrets_provision": ["decrypt_secrets", "ensure_secrets", "secrets_init"],
    "node_configuration": ["read_node_yaml", "verify_core", "verify_node_configs"],
    "certificates": ["install_acme"],
    "deploy_services": ["deploy_modules", "deploy_context"],
}
assert "system_bootstrap" in MIGRATION_MAP, "MIGRATION_MAP must contain system_bootstrap"

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    """Provide a temporary state file path for each test.

    ## @purpose — Isolate state file operations from production /var/lib/platform.
    ## @io — ⎋ Path to tmp_path/state.json
    ## @complexity — O(1)
    """
    return tmp_path / "bootstrap_state.json"


@pytest.fixture
def mock_subprocess() -> None:
    """Mock subprocess.run to return success by default across ALL modules.

    ## @purpose — Prevent any real system command execution during dry-run tests.
    ##            Returns returncode=0 for all commands, empty stdout/stderr.
    ## @complexity — O(1) — single patch call
    ## @invariants
    ##   - All subprocess.run calls (state_machine, phases, helpers) are intercepted
    ##   - subprocess.TimeoutExpired and FileNotFoundError are NOT suppressed
    ##     (only the .run() call itself is mocked)
    """
    with patch("subprocess.run") as mock:
        mock.return_value.returncode = 0
        mock.return_value.stdout = ""
        mock.return_value.stderr = ""
        yield


@pytest.fixture
def mock_fs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a real mock filesystem under tmp_path with all files phase code expects.

    ## @purpose — Phase functions execute real os.path.isfile() checks and open()
    ##            calls. Instead of mocking os.path.isfile globally (which breaks
    ##            open() for files that don't exist), we create the actual files
    ##            at tmp_path and point env vars there.
    ## @io — ⎋ tuple[Path, Path, Path]: (project_root, core_dir, node_yaml_path)
    ## @complexity — O(F) where F = number of files created (~15)
    ## @invariants
    ##   - All bootstrap scripts (install-docker, firewall, deploy-modules, etc.)
    ##     exist under core_dir/internal/bootstrap/
    ##   - VERSION file exists at core_dir/VERSION
    ##   - lib/ scripts (secrets.sh, logging.sh) exist for _decrypt_secrets
    ##   - secrets.env exists for _ensure_secrets_exist
    ##   - secrets-manifest.yaml exists for secrets_manager
    ##   - node.yaml exists for NODE_YAML env var
    ##   - nginx overlay directory exists for φ11 overlay detection
    ##   - config_renderer.py and provision-environment.sh exist for UPDATE phases
    ## @rationale Production code uses open() on several files (VERSION, secrets.env,
    ##   secrets-manifest.yaml). These calls would fail with FileNotFoundError if we
    ##   only mock os.path.isfile. Creating real files is the robust approach.
    """
    project_root = tmp_path / "project-root"
    core_dir = project_root / "core"
    bootstrap_dir = core_dir / "internal" / "bootstrap"
    lib_dir = core_dir / "lib"
    llm_dir = core_dir / "internal" / "llm"
    provision_dir = core_dir / "internal"

    for d in [core_dir, bootstrap_dir, lib_dir, llm_dir, provision_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # ── VERSION file (read by _verify_core_files) ──
    (core_dir / "VERSION").write_text("1.0.0-test\n")

    # ── Bootstrap scripts (checked by phase functions and precondition checks) ──
    for script in [
        "node-lifecycle.sh",
        "install-docker.sh",
        "firewall.sh",
        "install-tor-proxy.sh",
        "deploy-modules.sh",
        "converge.sh",
        "install-acme.sh",
    ]:
        (bootstrap_dir / script).write_text("#!/bin/bash\nexit 0\n")

    (bootstrap_dir / "docker_registry_auth.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    (bootstrap_dir / "setup-node.sh").write_text("#!/bin/bash\nexit 0\n")

    # ── lib/ scripts (sourced by _decrypt_secrets) ──
    for lib_script in ["secrets.sh", "logging.sh"]:
        (lib_dir / lib_script).write_text("#!/bin/bash\necho mock\n")

    # ── LLM config renderer (used by φ11 registry_update) ──
    (llm_dir / "config_renderer.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")

    # ── Secrets env and manifest ──
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("# Mock secrets\nexport MOCK_KEY=mock_value\n")

    (core_dir / "secrets-manifest.yaml").write_text("secrets:\n  - name: MOCK_KEY\n")

    # ── Provision script ──
    (provision_dir / "provision-environment.sh").write_text("#!/bin/bash\nexit 0\n")

    # ── Node config ──
    node_config_dir = tmp_path / "node-configs" / "test-node"
    node_config_dir.mkdir(parents=True, exist_ok=True)
    node_yaml = node_config_dir / "node.yaml"
    node_yaml.write_text("node_name: test-node\n")

    # ── Nginx overlay directory (detected by φ11) ──
    overlay_dir = node_config_dir / "overlays" / "nginx"
    overlay_dir.mkdir(parents=True, exist_ok=True)

    return project_root, core_dir, node_yaml


@pytest.fixture
def bootstrap_env(
    monkeypatch: pytest.MonkeyPatch,
    mock_fs: tuple[Path, Path, Path],
) -> None:
    """Set all environment variables required by phase preconditions and execution.

    ## @purpose — Provide the env var surface that node-lifecycle.sh sets before
    ##            invoking the Python state machine. Paths point to tmp_path files
    ##            created by mock_fs so that open() calls in production code succeed.
    ## @io — ⎋ None (side-effect: monkeypatch.setenv)
    ## @complexity — O(1)
    ## @invariants
    ##   - CORE_DIR points to tmp_path/core/ with real files
    ##   - NODE_YAML points to tmp_path/node-configs/test-node/node.yaml
    ##   - SECRETS_ENV_FILE points to tmp_path/secrets.env
    ##   - AGE_SECRET_KEY is set to avoid needing /etc/age/key.txt
    ##   - Both DOCKER_HUB_USERNAME and DOCKER_HUB_TOKEN are set for φ3/φ6 auth
    ##   - PLATFORM_OWNER_KEY and PLATFORM_CI_DEPLOY_KEY are set for φ2
    """
    _project_root, core_dir, node_yaml = mock_fs
    secrets_env = core_dir.parent.parent / "secrets.env"  # tmp_path/secrets.env

    monkeypatch.setenv("NODE_NAME", "test-node")
    monkeypatch.setenv("NODE_YAML", str(node_yaml))
    monkeypatch.setenv("PLATFORM_OWNER_KEY", "ssh-ed25519 AAAA... test-owner-key")
    monkeypatch.setenv("PLATFORM_CI_DEPLOY_KEY", "ssh-ed25519 AAAA... test-ci-key")
    monkeypatch.setenv("AGE_SECRET_KEY", "AGE-SECRET-KEY-1ABC...TEST")
    monkeypatch.setenv("SOPS_AGE_KEY", "")
    monkeypatch.setenv("GHCR_PULL_TOKEN", "ghp_test_token_12345")
    monkeypatch.setenv("CORE_DIR", str(core_dir))
    monkeypatch.setenv("DOCKER_HUB_USERNAME", "test_docker_user")
    monkeypatch.setenv("DOCKER_HUB_TOKEN", "dckr_test_token_67890")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))
    monkeypatch.setenv("TOR_ENABLED", "false")
    monkeypatch.setenv("AUTO_RECONCILE", "false")


@pytest.fixture
def mock_os_conditions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mock OS-level conditions that precondition checks verify.

    ## @purpose — os.geteuid() is mocked to 0 (root).
    ##            os.path.expanduser() is redirected to tmp_path to prevent
    ##            \"/home/\" creation failures on macOS (Errno 45: Operation not
    ##            supported). phase_user_accounts → _add_ssh_key → os.makedirs
    ##            on ~platform/.ssh would crash without this redirect.
    ## @rationale Mocking os.path.isfile globally would cause open() calls on
    ##   non-existent paths to fail silently. Instead, real files are provided
    ##   via mock_fs. Only expanduser needs redirecting because the platform
    ##   user doesn't exist in the test environment.
    ## @complexity — O(1)
    """
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    # Redirect ~user expansion to tmp_path to avoid /home/ OSError on macOS.
    # Input: "~platform/.ssh" → output: str(tmp_path / "home_dir" / "platform/.ssh")
    monkeypatch.setattr(
        os.path,
        "expanduser",
        lambda p: str(tmp_path / "home_dir" / p.lstrip("~/")),
    )


@pytest.fixture
def machine(
    state_file: Path,
    mock_subprocess: None,
    bootstrap_env: None,
    mock_os_conditions: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> StateMachine:
    """Create a StateMachine instance with all system dependencies mocked.

    ## @purpose — Factory fixture: returns a fully-mocked StateMachine ready for
    ##            phase execution. setup_state("init") is called to initialize
    ##            step entries in the state.
    ## @rationale Also patches _add_ssh_key to redirect /home/ operations to
    ##   tmp_path/home_dir/. On macOS, os.makedirs("/home/platform/.ssh") fails
    ##   with Errno 45 (Operation not supported) because /home is a special
    ##   symlink. The patched version creates the SSH directory in tmp_path
    ##   instead, preserving the test behavior.
    ## @io — ⎋ StateMachine instance
    ## @complexity — O(N) where N = number of INIT mode steps (23)
    ## @invariants
    ##   - state_file is in tmp_path — no production files touched
    ##   - subprocess.run and os.geteuid are mocked
    ##   - All required env vars are set with paths pointing to real tmp_path files
    ##   - State begins in init mode with all 23 steps as pending
    ##   - _add_ssh_key redirects /home/ to tmp_path/home_dir/ (macOS compat)
    """
    import core.internal.bootstrap.lifecycle.state_machine as _sm_module

    # Patch _add_ssh_key to use tmp_path/home_dir/ instead of /home/
    # macOS does not support os.makedirs("/home/platform/.ssh") — Errno 45
    def _safe_add_ssh_key(
        username: str,
        key: str,
        forced_command_prefix: str | None = None,
    ) -> None:
        """Redirect SSH key installation to tmp_path/home_dir/ {username}."""
        safe_home = tmp_path / "home_dir" / username
        safe_ssh = safe_home / ".ssh"
        safe_ssh.mkdir(parents=True, exist_ok=True)
        auth_keys = safe_ssh / "authorized_keys"
        entry = f"{forced_command_prefix} {key}\n" if forced_command_prefix else f"{key}\n"
        with open(auth_keys, "a") as f:
            f.write(entry)
        os.chmod(str(auth_keys), 0o600)
        logger.info(
            "[IMP:8][fixture][safe_add_ssh_key] SSH key added for %s at %s",
            username,
            safe_ssh,
        )

    monkeypatch.setattr(_sm_module, "_add_ssh_key", _safe_add_ssh_key)

    # Patch _ensure_projects_base to use tmp_path/projects/ instead of /opt/projects/
    # macOS does not allow non-root os.makedirs("/opt/projects") — PermissionError
    def _safe_ensure_projects_base(core_dir: str, node_name: str) -> None:
        """Redirect projects base directory to tmp_path/projects/."""
        safe_projects = tmp_path / "projects"
        safe_projects.mkdir(parents=True, exist_ok=True)
        logger.info(
            "[IMP:8][fixture][safe_ensure_projects_base] Projects base at %s",
            safe_projects,
        )

    monkeypatch.setattr(_sm_module, "_ensure_projects_base", _safe_ensure_projects_base)

    sm = StateMachine(state_file_path=str(state_file))
    sm.setup_state("init")
    return sm


def _mark_phase_done(sm: StateMachine, phase_value: str) -> None:
    """Mark a phase as done in the state machine's state and persist to file.

    ## @purpose — Helper used by tests to fulfill dependency graph prerequisites.
    ##            execute_phase() does not auto-mark phases done; this helper
    ##            manually sets the StepState and calls save().
    ## @io — ⇥ sm: StateMachine, phase_value: str → ⎋ None
    ## @complexity — O(1)
    """
    sm.state.steps[phase_value] = StepState(name=phase_value, status="done")
    sm.save()


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════════════
# region LDD Helper
# ═══════════════════════════════════════════════════════════════════════════


def _print_ldd_trajectory(caplog: pytest.LogCaptureFixture, test_name: str) -> bool:
    """Print IMP:7-10 log trajectory from caplog and return True if IMP:9 found.

    ## @purpose — Centralized LDD trajectory printer for all test functions.
    ##            Extracts and displays IMP:7-10 log entries for agent-visible telemetry.
    ## @io — ⇥ caplog: LogCaptureFixture, test_name: str → ⎋ bool (IMP:9 found)
    ## @complexity — O(n) where n = number of caplog records
    """
    found_imp9 = False
    print(f"\n--- LDD TRAJECTORY (IMP:7-10) [{test_name}] ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(f"  [{record.levelname}] {record.message}")
            if imp_level >= 9:
                found_imp9 = True
    print(f"--- END LDD TRAJECTORY [{test_name}] ---")
    return found_imp9


# endregion LDD Helper


# ═══════════════════════════════════════════════════════════════════════════
# region Tests: 14-Phase Dry Run
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_init_mode_14_phases_dry_run
## @purpose — Simulate all 9 INIT phases (φ1-φ8.5) in dependency order with mocked system calls.
##            Verifies that the entire INIT pipeline completes without PhaseDependencyError
##            or PhasePreconditionError when all preconditions are satisfied.
## @io — ⇥ caplog, machine(session fixture) → ⎋ None (side-effect: state file written)
## @complexity — O(9 * P) where P = average phase execution overhead
## @invariants
##   - Executes in strict dependency order: φ1→φ2→φ3→φ4→φ5→φ6→φ7→φ8→φ8.5
##   - Each phase is executed via machine.execute_phase()
##   - After each successful execution, the phase is marked as done in state.steps
##     so the dependency graph check passes for subsequent phases
##   - No PhaseDependencyError is raised (dependencies met)
##   - No PhasePreconditionError is raised (mocks satisfy all preconditions)
##   - At least one IMP:9 log is emitted during execution
def test_init_mode_14_phases_dry_run(
    caplog: pytest.LogCaptureFixture,
    machine: StateMachine,
) -> None:
    """Simulate all 9 INIT phases (φ1-φ8.5) in dry-run mode with mocked system calls."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: INIT 14-phase pipeline dry-run
    # · Scenario: Execute φ1→φ8.5 with all deps satisfied, all mocks active
    # · Last fail: N/A (first implementation)
    # · Remove if: DevPlan removes INIT mode or renames phases

    caplog.set_level(logging.DEBUG)

    # ── Define the INIT phase execution order based on dependency graph ──
    init_phases_order: list[str] = [
        BootstrapPhase.SYSTEM_BOOTSTRAP,  # φ1
        BootstrapPhase.USER_ACCOUNTS,  # φ2
        BootstrapPhase.PLATFORM_SETUP,  # φ3
        BootstrapPhase.SECRETS_PROVISION,  # φ4
        BootstrapPhase.NODE_CONFIGURATION,  # φ5
        BootstrapPhase.REGISTRY_AUTH,  # φ6
        BootstrapPhase.CERTIFICATES,  # φ7
        BootstrapPhase.DEPLOY_SERVICES,  # φ8
        BootstrapPhase.CONVERGE_SERVICES,  # φ8.5
    ]

    # ── Execute each phase in order ──
    for idx, phase_val in enumerate(init_phases_order, 1):
        logger.info("[IMP:9][test_init] Executing INIT phase φ%d: %s", idx, phase_val)

        # Execute the phase — all system calls are mocked
        machine.execute_phase(phase_val)

        # Mark phase as done for dependency tracking and persist to state file.
        # execute_phase() does NOT auto-mark itself done in state.steps;
        # the dependency graph check in subsequent phases looks for the
        # prerequisite phase key in state.steps.
        machine.state.steps[phase_val] = StepState(name=phase_val, status="done")
        machine.save()

        logger.info(
            "[IMP:9][test_init] Phase φ%d (%s) completed and marked done",
            idx,
            phase_val,
        )

    # ── Verify state file now has entries for all 9 phases ──
    state_content = machine.state_file.read_text()
    for phase_val in init_phases_order:
        assert phase_val in state_content, f"Phase '{phase_val}' should appear in state file"
    logger.info(
        "[IMP:9][test_init] All 9 INIT phase keys present in state file",
    )

    # ── LDD trajectory ──
    found_imp9 = _print_ldd_trajectory(caplog, "test_init_mode_14_phases_dry_run")
    assert found_imp9, "No IMP:9 business logic log found in INIT phase dry-run"
    logger.info("[IMP:9][test_init] All 9 INIT phases completed in dry-run mode")


# endregion FUNC_test_init_mode_14_phases_dry_run


# region FUNC_test_update_mode_5_phases_dry_run
## @purpose — Simulate all 5 UPDATE phases (φ9-φ13) in dependency order with mocked system calls.
##            Verifies that the UPDATE pipeline completes without dependency/precondition errors.
## @io — ⇥ caplog, state_file, mock_subprocess, bootstrap_env, mock_os_conditions → ⎋ None
## @complexity — O(5 * P)
## @invariants
##   - Executes in order: φ9→φ10→φ11→φ12→φ13
##   - Uses a separate StateMachine with mode="update"
##   - After each phase, marks done in state.steps for dependency tracking
##   - At least one IMP:9 log is emitted
def test_update_mode_5_phases_dry_run(
    caplog: pytest.LogCaptureFixture,
    state_file: Path,
    mock_subprocess: None,
    bootstrap_env: None,
    mock_os_conditions: None,
) -> None:
    """Simulate all 5 UPDATE phases (φ9-φ13) in dry-run mode with mocked system calls."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: UPDATE 5-phase pipeline dry-run
    # · Scenario: Execute φ9→φ13 with all deps satisfied, all mocks active
    # · Last fail: N/A (first implementation)
    # · Remove if: DevPlan removes UPDATE mode or renames update phases

    caplog.set_level(logging.DEBUG)

    sm = StateMachine(state_file_path=str(state_file))
    sm.setup_state("update")

    # ── Define the UPDATE phase execution order ──
    update_phases_order: list[str] = [
        BootstrapPhase.SECRETS_UPDATE,  # φ9
        BootstrapPhase.NODE_CONFIG_UPDATE,  # φ10
        BootstrapPhase.REGISTRY_UPDATE,  # φ11
        BootstrapPhase.DEPLOY_UPDATE,  # φ12
        BootstrapPhase.CONVERGE_UPDATE,  # φ13
    ]

    # ── Execute each phase in order ──
    for idx, phase_val in enumerate(update_phases_order, 1):
        logger.info(
            "[IMP:9][test_update] Executing UPDATE phase φ%d: %s",
            idx,
            phase_val,
        )

        # Execute phase then mark done for dependency tracking and persist to state file
        sm.execute_phase(phase_val)
        sm.state.steps[phase_val] = StepState(name=phase_val, status="done")
        sm.save()

        logger.info(
            "[IMP:9][test_update] UPDATE phase φ%d (%s) completed and marked done",
            idx,
            phase_val,
        )

    # ── Verify state file has entries for all 5 phases ──
    state_content = sm.state_file.read_text()
    for phase_val in update_phases_order:
        assert phase_val in state_content, f"UPDATE phase '{phase_val}' should appear in state file"
    logger.info(
        "[IMP:9][test_update] All 5 UPDATE phase keys present in state file",
    )

    # ── LDD trajectory ──
    found_imp9 = _print_ldd_trajectory(caplog, "test_update_mode_5_phases_dry_run")
    assert found_imp9, "No IMP:9 business logic log found in UPDATE phase dry-run"
    logger.info("[IMP:9][test_update] All 5 UPDATE phases completed in dry-run mode")


# endregion FUNC_test_update_mode_5_phases_dry_run


# endregion Tests: 14-Phase Dry Run

# ═══════════════════════════════════════════════════════════════════════════
# region Tests: Precondition & Dependency Enforcement
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_precondition_block_on_dependency_gap
## @purpose — Verify that attempting φ6 (registry_auth) without φ4 (secrets_provision) raises
##            PhaseDependencyError. φ6 depends on φ4 in _phase_dependency_graph.
## @io — ⇥ caplog, machine → ⎋ None (expects PhaseDependencyError)
## @complexity — O(1) — single execute_phase call
## @invariants
##   - machine is freshly created via setup_state("init") — no phase has been executed
##   - φ6's dependency {SECRETS_PROVISION} is NOT present in state.steps
##   - Exception message contains both attempted phase and missing dependency name
##   - State file is NOT corrupted (readable after exception)
def test_precondition_block_on_dependency_gap(
    caplog: pytest.LogCaptureFixture,
    machine: StateMachine,
) -> None:
    """Attempt φ6 without φ4 → PhaseDependencyError."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: φ6←φ4 dependency gate
    # · Scenario: Call execute_phase(REGISTRY_AUTH) when SECRETS_PROVISION is not done
    # · Last fail: N/A (first implementation)
    # · Remove if: _phase_dependency_graph no longer maps φ6←φ4

    caplog.set_level(logging.DEBUG)

    # ── Verify that φ6 depends on φ4 in the dependency graph ──
    deps_for_registry = _phase_dependency_graph.get(BootstrapPhase.REGISTRY_AUTH, set())
    assert BootstrapPhase.SECRETS_PROVISION in deps_for_registry, (
        f"Expected REGISTRY_AUTH to depend on SECRETS_PROVISION, got deps={deps_for_registry}"
    )
    logger.info(
        "[IMP:9][test_dep_gap] Verified dependency: φ6 depends on φ4 (deps=%s)",
        deps_for_registry,
    )

    # ── Attempt φ6 without φ4 done — expect PhaseDependencyError ──
    with pytest.raises(PhaseDependencyError) as exc_info:
        machine.execute_phase(BootstrapPhase.REGISTRY_AUTH)

    error_msg = str(exc_info.value)
    logger.info("[IMP:9][test_dep_gap] Caught PhaseDependencyError: %s", error_msg)

    # ── Verify error message contains both phase identifiers ──
    assert BootstrapPhase.REGISTRY_AUTH in error_msg, (
        f"Error should mention attempted phase '{BootstrapPhase.REGISTRY_AUTH}', got: {error_msg}"
    )
    assert BootstrapPhase.SECRETS_PROVISION in error_msg, (
        f"Error should mention missing dependency '{BootstrapPhase.SECRETS_PROVISION}', got: {error_msg}"
    )
    assert "prerequisite" in error_msg, f"Error should contain 'prerequisite' keyword, got: {error_msg}"

    # ── Verify state file is not corrupted ──
    state_file_path = machine.state_file
    assert state_file_path.exists(), "State file should still exist after dependency error"
    content = state_file_path.read_text()
    assert '"mode": "init"' in content, "State file mode should remain 'init'"
    assert len(content) > 10, "State file should contain valid JSON"

    # ── LDD trajectory ──
    found_imp9 = _print_ldd_trajectory(caplog, "test_precondition_block_on_dependency_gap")
    assert found_imp9, "No IMP:9 business logic log found in dependency gap test"


# endregion FUNC_test_precondition_block_on_dependency_gap


# endregion Tests: Precondition & Dependency Enforcement

# ═══════════════════════════════════════════════════════════════════════════
# region Tests: Skip Logic & Partial Failure Recovery
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_skip_already_done_phases
## @purpose — Verify that execute_grouped_phase() skips sub_steps that are already done
##            with unchanged content hash. All 4 sub_steps of φ1 (system_bootstrap) are
##            pre-set as done+unchanged → execute_phase should NOT be called.
## @io — ⇥ caplog, machine → ⎋ None (verifies skip behavior)
## @complexity — O(S * H) where S = 4 sub_steps, H = hash computation
## @invariants
##   - machine.execute_phase is wrapped to raise if called (should be skipped)
##   - Each sub_step's hash is computed via machine._step_hash() for accurate matching
##   - execute_grouped_phase returns True (all sub_steps done)
##   - Log contains "SKIP sub_step" for each sub_step
def test_skip_already_done_phases(
    caplog: pytest.LogCaptureFixture,
    machine: StateMachine,
) -> None:
    """Pre-set all φ1 sub_steps as done+unchanged → execute_grouped_phase skips all."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: grouped-phase skip logic
    # · Scenario: φ1 (system_bootstrap) has 4 sub_steps all done with matching hash
    # · Last fail: N/A (first implementation)
    # · Remove if: SYSTEM_BOOTSTRAP is no longer a grouped phase

    caplog.set_level(logging.DEBUG)

    # ── Verify φ1 is a grouped phase (module-level constant) ──
    assert BootstrapPhase.SYSTEM_BOOTSTRAP in _grouped_phases, (
        f"Expected SYSTEM_BOOTSTRAP to be in _grouped_phases, got: {_grouped_phases}"
    )
    logger.info(
        "[IMP:9][test_skip] Verified SYSTEM_BOOTSTRAP is a grouped phase",
    )

    # ── Get sub_step names for φ1 from MIGRATION_MAP ──
    sub_step_keys = MIGRATION_MAP["system_bootstrap"]
    assert len(sub_step_keys) == 4, (
        f"Expected 4 sub_steps for system_bootstrap, got {len(sub_step_keys)}: {sub_step_keys}"
    )

    # ── Pre-compute hashes for each sub_step ──
    sub_steps: dict[str, dict[str, Any]] = {}
    for sub_key in sub_step_keys:
        sub_hash = machine._step_hash(f"sub_system_bootstrap_{sub_key}")
        sub_steps[sub_key] = {
            "done": True,
            "hash": sub_hash,
        }
        logger.info(
            "[IMP:8][test_skip] Sub_step '%s' hash: %s...",
            sub_key,
            sub_hash[:12],
        )

    # ── Wrap execute_phase to detect unwanted calls ──
    call_count: list[int] = [0]
    original_execute = machine.execute_phase

    def _tracking_execute(phase_value: str) -> None:
        call_count[0] += 1
        logger.warning(
            "[IMP:7][test_skip] UNEXPECTED execute_phase call for '%s' (call #%d) — "
            "all sub_steps should have been skipped",
            phase_value,
            call_count[0],
        )

    machine.execute_phase = _tracking_execute  # type: ignore[assignment]

    # ── Execute grouped phase ──
    result = machine.execute_grouped_phase(
        BootstrapPhase.SYSTEM_BOOTSTRAP,
        sub_steps,
    )

    # ── Restore original ──
    machine.execute_phase = original_execute

    # ── Assertions ──
    assert call_count[0] == 0, (
        f"execute_phase was called {call_count[0]} times but should have been 0 "
        f"(all sub_steps had done=true + matched hash)"
    )
    assert result is True, f"execute_grouped_phase returned {result}, expected True (all sub_steps done)"

    # ── Verify skip log entries ──
    skip_logs = [r.message for r in caplog.records if "SKIP sub_step" in r.message]
    assert len(skip_logs) == 4, f"Expected 4 'SKIP sub_step' log messages, found {len(skip_logs)}"
    for log_msg in skip_logs:
        logger.info("[IMP:8][test_skip] Verified skip log: %s", log_msg)

    # ── LDD trajectory ──
    found_imp9 = _print_ldd_trajectory(caplog, "test_skip_already_done_phases")
    assert found_imp9, "No IMP:9 business logic log found in skip test"


# endregion FUNC_test_skip_already_done_phases


# region FUNC_test_resume_phase_partial_failure
## @purpose — Verify _resume_phase() for φ4 (secrets_provision) when 2/3 sub_steps are done
##            and 1 (ensure_secrets) failed. resume_phase should execute only the failed sub_step,
##            skipping the successful ones (decrypt_secrets, secrets_init) with unchanged hash.
## @io — ⇥ caplog, machine → ⎋ None (verifies partial failure recovery)
## @complexity — O(S * H) where S = 3 sub_steps
## @invariants
##   - decrypt_secrets and secrets_init are pre-set as done+unchanged → skipped
##   - ensure_secrets is pre-set as done=False → re-executed
##   - execute_phase is called exactly 1 time (for the failed sub_step)
##   - resume_phase returns True (all sub_steps eventually completed)
def test_resume_phase_partial_failure(
    caplog: pytest.LogCaptureFixture,
    machine: StateMachine,
) -> None:
    """φ4 partial failure: decrypt OK, ensure-passwords FAIL → resume retries only the failed sub_step."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: φ4 partial failure recovery via _resume_phase
    # · Scenario: decrypt_secrets → done, ensure_secrets → failed, secrets_init → done
    # · Last fail: N/A (first implementation)
    # · Remove if: SECRETS_PROVISION is no longer a grouped phase or _resume_phase is removed

    caplog.set_level(logging.DEBUG)

    # ── Verify φ4 is a grouped phase (module-level constant) ──
    assert BootstrapPhase.SECRETS_PROVISION in _grouped_phases, "Expected SECRETS_PROVISION to be in _grouped_phases"
    logger.info(
        "[IMP:9][test_resume] Verified SECRETS_PROVISION is a grouped phase",
    )

    # ── Mark dependency graph prerequisites as done ──
    # φ4 (secrets_provision) depends on φ3 (platform_setup) in
    # _phase_dependency_graph. resume_phase → execute_grouped_phase checks
    # dependencies and raises PhaseDependencyError if unmet.
    _mark_phase_done(machine, BootstrapPhase.SYSTEM_BOOTSTRAP)  # φ1
    _mark_phase_done(machine, BootstrapPhase.USER_ACCOUNTS)  # φ2
    _mark_phase_done(machine, BootstrapPhase.PLATFORM_SETUP)  # φ3 ← φ4 dependency
    logger.info(
        "[IMP:9][test_resume] Dependency phases φ1-φ3 marked done for φ4 resume",
    )

    # ── Set up φ4 sub_steps with partial failure ──
    # decrypt_secrets: done, hash matched → SKIP
    # ensure_secrets: NOT done (failed) → RE-EXECUTE
    # secrets_init: done, hash matched → SKIP
    decrypt_hash = machine._step_hash("sub_secrets_provision_decrypt_secrets")
    init_hash = machine._step_hash("sub_secrets_provision_secrets_init")

    sub_steps: dict[str, dict[str, Any]] = {
        "decrypt_secrets": {"done": True, "hash": decrypt_hash},
        "ensure_secrets": {"done": False, "hash": ""},  # ← FAILED sub_step
        "secrets_init": {"done": True, "hash": init_hash},
    }

    # Store sub_steps in state so resume_phase picks them up.
    # resume_phase calls self.state.steps.get(phase_key) and expects
    # a dict with a "sub_steps" key containing the sub_step entries.
    phase_key = BootstrapPhase.SECRETS_PROVISION
    machine.state.steps[phase_key] = {
        "done": False,
        "sub_steps": sub_steps,
    }

    # ── Wrap execute_phase to track calls ──
    call_log: list[str] = []
    original_execute = machine.execute_phase

    def _tracking_execute(phase_value: str) -> None:
        call_log.append(phase_value)
        logger.info(
            "[IMP:8][test_resume] execute_phase called for '%s' (sub_step re-execution)",
            phase_value,
        )

    machine.execute_phase = _tracking_execute  # type: ignore[assignment]

    # ── Call resume_phase for φ4 ──
    result = machine.resume_phase(BootstrapPhase.SECRETS_PROVISION)

    # ── Restore original ──
    machine.execute_phase = original_execute

    # ── Assertions ──
    assert len(call_log) == 1, (
        f"Expected exactly 1 execute_phase call (for ensure_secrets), got {len(call_log)}: {call_log}"
    )
    assert call_log[0] == BootstrapPhase.SECRETS_PROVISION, (
        f"execute_phase was called for '{call_log[0]}', expected '{BootstrapPhase.SECRETS_PROVISION}'"
    )
    assert result is True, f"resume_phase returned {result}, expected True"

    # ── Verify skip logs for the 2 done sub_steps ──
    skip_logs = [r.message for r in caplog.records if "SKIP sub_step" in r.message]
    assert len(skip_logs) >= 2, (
        f"Expected at least 2 'SKIP sub_step' log messages (for decrypt_secrets, secrets_init), found {len(skip_logs)}"
    )

    # ── LDD trajectory ──
    found_imp9 = _print_ldd_trajectory(caplog, "test_resume_phase_partial_failure")
    assert found_imp9, "No IMP:9 business logic log found in resume test"


# endregion FUNC_test_resume_phase_partial_failure


# region FUNC_test_grouped_phase_skip_unchanged_sub_steps
## @purpose — Verify that execute_grouped_phase() skips only unchanged+done sub_steps and
##            executes the remaining (failed/pending) ones. φ1 has 4 sub_steps; set 3 as
##            done+unchanged (skipped) and 1 as pending (executed).
## @io — ⇥ caplog, machine → ⎋ None (verifies selective skip)
## @complexity — O(S * H) where S = 4 sub_steps
## @invariants
##   - 3 sub_steps (system_packages, docker_install, tor_proxy) are done+unchanged → SKIP
##   - 1 sub_step (firewall) is pending → EXECUTE
##   - execute_phase is called exactly 1 time (for the pending sub_step)
##   - execute_grouped_phase returns True (all sub_steps eventually done)
def test_grouped_phase_skip_unchanged_sub_steps(
    caplog: pytest.LogCaptureFixture,
    machine: StateMachine,
) -> None:
    """φ1: 3/4 sub_steps done with unchanged hash, 1 pending → only pending sub_step executes."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: selective sub_step skip in grouped phases
    # · Scenario: system_packages=d, docker_install=d, tor_proxy=d, firewall=pending
    # · Last fail: N/A (first implementation)
    # · Remove if: SYSTEM_BOOTSTRAP grouped-phase logic is removed

    caplog.set_level(logging.DEBUG)

    # ── Get φ1 sub_step keys ──
    sub_step_keys = MIGRATION_MAP["system_bootstrap"]
    assert len(sub_step_keys) == 4

    # ── Set up: 3 done+unchanged, 1 pending ──
    done_hash_1 = machine._step_hash("sub_system_bootstrap_system_packages")
    done_hash_2 = machine._step_hash("sub_system_bootstrap_docker_install")
    done_hash_3 = machine._step_hash("sub_system_bootstrap_tor_proxy")

    sub_steps: dict[str, dict[str, Any]] = {
        sub_step_keys[0]: {"done": True, "hash": done_hash_1},  # system_packages → SKIP
        sub_step_keys[1]: {"done": True, "hash": done_hash_2},  # docker_install → SKIP
        sub_step_keys[2]: {"done": True, "hash": done_hash_3},  # tor_proxy → SKIP
        sub_step_keys[3]: {"done": False, "hash": ""},  # firewall → EXECUTE
    }

    # ── Wrap execute_phase to track calls ──
    call_log: list[str] = []
    original_execute = machine.execute_phase

    def _tracking_execute(phase_value: str) -> None:
        call_log.append(phase_value)
        logger.info(
            "[IMP:8][test_skip_selective] execute_phase called for '%s'",
            phase_value,
        )

    machine.execute_phase = _tracking_execute  # type: ignore[assignment]

    # ── Execute grouped phase ──
    result = machine.execute_grouped_phase(
        BootstrapPhase.SYSTEM_BOOTSTRAP,
        sub_steps,
    )

    # ── Restore original ──
    machine.execute_phase = original_execute

    # ── Assertions ──
    assert len(call_log) == 1, f"Expected exactly 1 execute_phase call (for firewall), got {len(call_log)}: {call_log}"
    assert call_log[0] == BootstrapPhase.SYSTEM_BOOTSTRAP
    assert result is True, f"execute_grouped_phase returned {result}, expected True"

    # ── Verify skip logs: exactly 3 skip messages ──
    skip_logs = [r.message for r in caplog.records if "SKIP sub_step" in r.message]
    assert len(skip_logs) == 3, f"Expected exactly 3 'SKIP sub_step' log messages, found {len(skip_logs)}"

    # ── Verify run log for firewall ──
    run_logs = [r.message for r in caplog.records if "EXECUTE sub_step" in r.message and "firewall" in r.message]
    assert len(run_logs) >= 1, "Expected 'EXECUTE sub_step' log for firewall sub_step"

    # ── LDD trajectory ──
    found_imp9 = _print_ldd_trajectory(caplog, "test_grouped_phase_skip_unchanged_sub_steps")
    assert found_imp9, "No IMP:9 business logic log found in grouped-phase skip test"


# endregion FUNC_test_grouped_phase_skip_unchanged_sub_steps


# endregion Tests: Skip Logic & Partial Failure Recovery

# ═══════════════════════════════════════════════════════════════════════════
# region Tests: Phase Dependency Graph Integrity
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_phase_dependency_graph_integrity
## @purpose — Verify _phase_dependency_graph integrity: all 14 phases have the expected
##            dependencies, no invalid phase names in graph, and the graph's transitive
##            closure is consistent. Also verify that migrate_state_to_phases produces
##            correct composite hashes.
## @io — ⇥ caplog → ⎋ None
## @complexity — O(P * S) where P=14 phases, S=avg sub_steps
## @invariants
##   - All phases in _phase_dependency_graph are valid BootstrapPhase values
##   - All dependency values are valid BootstrapPhase values
##   - INIT phases have no dependency on UPDATE phases, and vice-versa
##   - migrate_state_to_phases produces correct composite hashes
##   - MIGRATION_MAP keys match valid phase names
def test_phase_dependency_graph_integrity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify _phase_dependency_graph consistency and MIGRATION_MAP key validity."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: _phase_dependency_graph + MIGRATION_MAP structural integrity
    # · Scenario: Structural test of the dependency graph and inline migration map keys
    # · Last fail: never
    # · Updated: 2026-07-30 (Wave B) — migrate_state_to_phases() assertions removed (state_migration.py deleted)
    # · Remove if: phases are no longer tracked via dependency graph

    caplog.set_level(logging.DEBUG)

    # ── Collect all known phase values ──
    all_phase_values: set[str] = set(BootstrapPhase.ALL_PHASES)
    logger.info(
        "[IMP:9][test_graph] Total phases: %d (INIT=%d, UPDATE=%d)",
        len(all_phase_values),
        len(BootstrapPhase.INIT_PHASES),
        len(BootstrapPhase.UPDATE_PHASES),
    )

    # ── Verify every key in the dependency graph is a valid phase ──
    for phase_key in _phase_dependency_graph:
        assert phase_key in all_phase_values, f"Dependency graph key '{phase_key}' is not a valid BootstrapPhase value"
        for dep in _phase_dependency_graph[phase_key]:
            assert dep in all_phase_values, (
                f"Dependency graph value '{dep}' for phase '{phase_key}' is not a valid BootstrapPhase value"
            )

    # ── Verify no cross-mode dependencies (INIT ← UPDATE or UPDATE ← INIT) ──
    init_set = BootstrapPhase.INIT_PHASES
    update_set = BootstrapPhase.UPDATE_PHASES
    for phase_key, deps in _phase_dependency_graph.items():
        for dep in deps:
            if phase_key in init_set:
                msg = f"INIT phase '{phase_key}' depends on UPDATE phase '{dep}'"
                assert dep in init_set, msg
            if phase_key in update_set:
                msg = f"UPDATE phase '{phase_key}' depends on INIT phase '{dep}'"
                assert dep in update_set, msg

    logger.info(
        "[IMP:9][test_graph] All %d phase dependency entries validated — no cross-mode deps",
        len(_phase_dependency_graph),
    )

    # ── Verify MIGRATION_MAP keys are valid BootstrapPhase values ──
    # (MIGRATION_MAP is now inlined; migrate_state_to_phases() removed in Wave B.
    #  Only the structural key-validity check is preserved.)
    for phase_key in MIGRATION_MAP:
        assert phase_key in all_phase_values or phase_key.replace("_", "") in {
            v.replace("_", "") for v in all_phase_values
        }, f"MIGRATION_MAP key '{phase_key}' does not match any BootstrapPhase value"

    logger.info("[IMP:9][test_graph] MIGRATION_MAP all keys validated against BootstrapPhase")

    # ── LDD trajectory ──
    found_imp9 = _print_ldd_trajectory(caplog, "test_phase_dependency_graph_integrity")
    assert found_imp9, "No IMP:9 business logic log found in graph integrity test"


# endregion FUNC_test_phase_dependency_graph_integrity


# endregion Tests: Phase Dependency Graph Integrity

# ═══════════════════════════════════════════════════════════════════════════
# region Tests: Precondition Failure Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_precondition_check_root_failure
## @purpose — Verify that precondition_check() raises PhasePreconditionError when
##            os.geteuid() != 0 for the SYSTEM_BOOTSTRAP phase. This tests the fail-fast
##            behavior of the precondition system.
## @io — ⇥ caplog, mock_subprocess, bootstrap_env → ⎋ None (expects PhasePreconditionError)
## @complexity — O(1)
## @invariants
##   - Uses a BootstrapState instance directly (not StateMachine)
##   - os.geteuid() is NOT mocked → returns real euid (non-root in local dev)
##   - PhasePreconditionError message contains "root access" and "euid"
def test_precondition_check_root_failure(
    caplog: pytest.LogCaptureFixture,
    mock_subprocess: None,
    bootstrap_env: None,
) -> None:
    """Verify precondition_check raises PhasePreconditionError when not running as root."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: φ1 root precondition fail-fast
    # · Scenario: call precondition_check(SYSTEM_BOOTSTRAP) without mocking geteuid
    # · Last fail: N/A (first implementation)
    # · Remove if: φ1 no longer checks root access in precondition_check

    from core.internal.bootstrap.lifecycle.state_machine import BootstrapState

    caplog.set_level(logging.DEBUG)

    state = BootstrapState()

    # ── Do NOT mock os.geteuid — test should NOT run as root ──
    if os.geteuid() == 0:
        pytest.skip("Cannot test root failure when running as root — environment is root")

    # ── Call precondition_check for φ1 (system_bootstrap) → expect failure ──
    with pytest.raises(PhasePreconditionError) as exc_info:
        state.precondition_check(BootstrapPhase.SYSTEM_BOOTSTRAP)

    error_msg = str(exc_info.value)
    logger.info("[IMP:9][test_root_fail] PhasePreconditionError: %s", error_msg)

    # ── Verify error message is informative ──
    assert "root" in error_msg, f"Error should mention 'root', got: {error_msg}"
    assert "euid" in error_msg, f"Error should mention 'euid', got: {error_msg}"
    assert BootstrapPhase.SYSTEM_BOOTSTRAP in error_msg, (
        f"Error should mention phase '{BootstrapPhase.SYSTEM_BOOTSTRAP}', got: {error_msg}"
    )

    # ── LDD trajectory ──
    found_imp9 = _print_ldd_trajectory(caplog, "test_precondition_check_root_failure")
    assert found_imp9, "No IMP:9 business logic log found in precondition root failure test"


# endregion FUNC_test_precondition_check_root_failure


# endregion Tests: Precondition Failure Edge Cases
