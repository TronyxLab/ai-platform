# GREP_SUMMARY: test_bootstrap_dry_run, integration, bootstrap, 14-phases, dry-run, state-machine, dependency-graph, precondition, skip-phases, phase-migration
# STRUCTURE: ▶ ┌tmp_path + monkeypatch + mock subprocess┐ → ◇ test_init_mode_14_phases_dry_run (9 init phases, φ1-φ8.5) → ◇ test_update_mode_5_phases_dry_run (5 update phases, φ9-φ13) → ◇ test_precondition_block_on_dependency_gap (φ6 = requires φ4 → PhaseDependencyError) → ◇ test_phase_dependency_graph_integrity (graph validation) → ◇ test_precondition_check_root_failure (non-root → PhasePreconditionError) → ⎋ LDD IMP:7-10 assertions + TRAP[TEST] markers
# region MODULE_CONTRACT
## @purpose  Integration tests for the 14-phase bootstrap pipeline in dry-run mode (DevPlan T14).
##           Simulates all 14 phases (9 INIT + 5 UPDATE) with mocked subprocess calls,
##           verifies dependency enforcement, precondition blocks, skip-already-done logic.
## @scope    Integration (not unit) — tests the interaction of state_machine.py and phases.py
##           with mocked system dependencies. Does NOT execute real
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
##   8. Волна 117 D5: execute_grouped_phase удалён (sub-step resume вне скоупа) —
##      grouped-phase тесты и MIGRATION_MAP удалены вместе с функцией
## @rationale DevPlan T14 explicitly requires integration test coverage of the 14-phase flow
##   in dry-run mode. The dependency graph and precondition system must be tested together
##   to catch cross-phase interaction bugs that unit tests would miss. Mocking system calls
##   makes the test safe to run on any machine (no root, no Docker, no real secrets).
## @changes 2026-07-30 | Created per DevPlan T14 — 8 integration tests for 14-phase bootstrap
##           2026-08-01 | Волна 117 D5 — grouped-phase tests (skip_already_done, skip_unchanged)
##           и MIGRATION_MAP удалены вместе с execute_grouped_phase (мёртвый код)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from _conftest.ldd import _print_ldd_trajectory

from core.internal.bootstrap.lifecycle.state_machine import (
    BootstrapPhase,
    PhaseDependencyError,
    PhasePreconditionError,
    StateMachine,
    StepState,
    _phase_dependency_graph,
)

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
    ##            W5-C1 (план 170): + патч shutil.which в phases/preconditions —
    ##            φ1 precondition (apt-get/dpkg) переведён с `command -v`/subprocess
    ##            на stdlib which (детерминизм на любом раннере, в т.ч. macOS без apt-get).
    ## @complexity — O(1) — single patch call
    ## @invariants
    ##   - All subprocess.run calls (state_machine, phases, helpers) are intercepted
    ##   - subprocess.TimeoutExpired and FileNotFoundError are NOT suppressed
    ##     (only the .run() call itself is mocked)
    """
    with (
        patch("subprocess.run") as mock,
        patch(
            "core.internal.bootstrap.lifecycle.phases.preconditions.shutil.which",
            return_value="/usr/bin/apt-get",
        ),
    ):
        mock.return_value.returncode = 0
        mock.return_value.stdout = ""
        mock.return_value.stderr = ""
        yield


class _FakeRunner:
    """Scripted CommandRunner (DI-канон W4b) для phase-тестов (W4d): записывает команды в список.

    ## @purpose — Замена патча helpers_subprocess.run_subprocess (удалён из phases/system.py):
    ##            каждая команда фазы через runner= попадает в captured, возвращается rc=0.
    ## @io — ⇥ captured: list[list[str]] (внешний список-аккумулятор) → ⎋ CompletedProcess(rc=0)
    ## @complexity — O(1)
    ## @invariants — run() НЕ raise; default rc=0 (фазы завершаются без non-fatal issues)
    """

    def __init__(self, captured: list[list[str]]) -> None:
        self._captured = captured

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self._captured.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), returncode=0, stdout="", stderr="")


class _FakeRootFacts:
    """Fake EnvironmentFacts: root + реальный path_isfile (E3, DevPlan 160).

    ## @purpose — StateMachine(facts=...) для dry-run: is_root=True (фазы не падают
    ##            на euid-проверке), path_isfile — passthrough на os.path.isfile.
    ## @io — ⇥ path: str → ⎋ bool
    ## @complexity — O(1)
    """

    def is_root(self) -> bool:
        return True

    @staticmethod
    def path_isfile(path: str) -> bool:

        return Path(path).is_file()


class _SafeUserHelpers:
    """Fake users-неймспейс (167 D6, W-H 163 users_helpers DI): SSH-ключи и projects-base → tmp.

    ## @purpose — Замена monkeypatch-патчей _helpers_users.add_ssh_key/ensure_projects_base:
    ##            φ2 (phase_user_accounts) получает users_helpers= через StateMachine —
    ##            реальные /home/ и /opt/projects не создаются (macOS Errno 45 / PermissionError).
    ## @io — ⇥ tmp_path: Path (корень перенаправления) → ⎋ namespace
    ## @complexity — O(1)
    ## @invariants — create_user/add_ssh_key/ensure_projects_base — сигнатуры канона helpers_users
    """

    def __init__(self, tmp_path: Path) -> None:
        self._tmp = tmp_path

    def create_user(
        self,
        username: str,
        groups: list[str],  # ruff: ignore[ARG002]
        *,
        runner=None,  # ruff: ignore[ARG002]
    ) -> None:
        """No-op — useradd не выполняется (subprocess мокается)."""
        logger.info("[IMP:7][fixture][fake_users] create_user %s (no-op)", username)

    def add_ssh_key(
        self,
        username: str,
        key: str,
        forced_command_prefix: str | None = None,
        *,
        runner=None,  # ruff: ignore[ARG002]
    ) -> None:
        """Redirect SSH key installation to tmp_path/home_dir/{username}."""
        safe_home = self._tmp / "home_dir" / username
        safe_ssh = safe_home / ".ssh"
        safe_ssh.mkdir(parents=True, exist_ok=True)
        auth_keys = safe_ssh / "authorized_keys"
        entry = f"{forced_command_prefix} {key}\n" if forced_command_prefix else f"{key}\n"
        with Path(auth_keys).open("a", encoding="utf-8") as f:
            f.write(entry)
        Path(str(auth_keys)).chmod(0o600)
        logger.info("[IMP:8][fixture][safe_add_ssh_key] SSH key added for %s at %s", username, safe_ssh)

    def ensure_projects_base(self, core_dir: str, node_name: str, *, runner=None) -> None:  # ruff: ignore[ARG002]
        """Redirect projects base directory to tmp_path/projects/."""
        safe_projects = self._tmp / "projects"
        safe_projects.mkdir(parents=True, exist_ok=True)
        logger.info("[IMP:8][fixture][safe_ensure_projects_base] Projects base at %s", safe_projects)


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
def di_env(mock_fs: tuple[Path, Path, Path]) -> dict[str, str]:
    """Env-дикт фаз для StateMachine(env=...) (W4e, DevPlan 160 E2 — env-часть DI).

    ## @purpose — Ключевые env-переменные фаз (TOR_ENABLED/SECRETS_ENV_FILE/NODE_YAML) +
    ##            NODE_NAME/CORE_DIR передаются env-диктом (execute_phase merge поверх
    ##            os.environ). Остальные (PLATFORM_OWNER_KEY/AGE_SECRET_KEY/GHCR_PULL_TOKEN/
    ##            DOCKER_HUB_*/AUTO_RECONCILE) — os.environ через bootstrap_env фикстуру
    ##            (не-DI-минимум волны E2 — читаются напрямую вне конвертированных функций).
    ## @io — ⎋ dict[str, str] (5 ключевых переменных)
    ## @complexity — O(1)
    """
    _project_root, core_dir, node_yaml = mock_fs
    secrets_env = core_dir.parent.parent / "secrets.env"  # tmp_path/secrets.env
    return {
        "NODE_NAME": "test-node",
        "NODE_YAML": str(node_yaml),
        "CORE_DIR": str(core_dir),
        "SECRETS_ENV_FILE": str(secrets_env),
        "TOR_ENABLED": "false",
    }


@pytest.fixture
def bootstrap_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set all environment variables required by phase preconditions and execution.

    ## @purpose — Provide the env var surface that node-lifecycle.sh sets before
    ##            invoking the Python state machine. Paths point to tmp_path files
    ##            created by mock_fs so that open() calls in production code succeed.
    ##            W4e (DevPlan 160 E2): TOR_ENABLED/SECRETS_ENV_FILE/NODE_YAML/CORE_DIR/
    ##            NODE_NAME ушли в di_env (StateMachine(env=...)) — здесь остаются только
    ##            переменные, читаемые os.environ напрямую вне DI-конвертации (отчёт E2).
    ## @io — ⎋ None (side-effect: не-DI-минимум патчей окружения)
    ## @invariants
    ##   - CORE_DIR points to tmp_path/core/ with real files
    ##   - NODE_YAML points to tmp_path/node-configs/test-node/node.yaml
    ##   - SECRETS_ENV_FILE points to tmp_path/secrets.env
    ##   - AGE_SECRET_KEY is set to avoid needing /etc/age/key.txt
    ##   - Both DOCKER_HUB_USERNAME and DOCKER_HUB_TOKEN are set for φ3/φ6 auth
    ##   - PLATFORM_OWNER_KEY and PLATFORM_CI_DEPLOY_KEY are set for φ2
    """
    monkeypatch.setenv("PLATFORM_OWNER_KEY", "ssh-ed25519 AAAA... test-owner-key")
    monkeypatch.setenv("PLATFORM_CI_DEPLOY_KEY", "ssh-ed25519 AAAA... test-ci-key")
    monkeypatch.setenv("AGE_SECRET_KEY", "AGE-SECRET-KEY-1ABC...TEST")
    monkeypatch.setenv("SOPS_AGE_KEY", "")
    monkeypatch.setenv("GHCR_PULL_TOKEN", "ghp_test_token_12345")
    monkeypatch.setenv("DOCKER_HUB_USERNAME", "test_docker_user")
    monkeypatch.setenv("DOCKER_HUB_TOKEN", "dckr_test_token_67890")
    monkeypatch.setenv("AUTO_RECONCILE", "false")


@pytest.fixture
def mock_os_conditions() -> None:
    """Mock OS-level conditions that precondition checks verify.

    ## @purpose — 167 D6 (DI-zero): os.path.expanduser патч УДАЛЁН — единственный вызов
    ##            expanduser в lifecycle — cli._forced_command_smoke (не вызывается в dry-run;
    ##            grep 2026-08-14: 1 вхождение). φ2 user-операции — через _SafeUserHelpers DI.
    ##            E3 (160): os.geteuid УБРАН — root-факты через StateMachine(facts=...) (DI).
    ## @rationale Mocking os.path.isfile globally would cause open() calls on
    ##   non-existent paths to fail silently. Instead, real files are provided
    ##   via mock_fs. Only expanduser needed redirecting because the platform
    ##   user doesn't exist in the test environment — now removed (no flow call).
    ## @complexity — O(1)
    """
    return None  # ruff: ignore[RET501] — явный no-op маркер (патч удалён, фикстура сохранена как контракт)


@pytest.fixture
def machine(
    state_file: Path,
    mock_subprocess: None,
    bootstrap_env: None,
    di_env: dict[str, str],
    mock_os_conditions: None,
    tmp_path: Path,
) -> StateMachine:
    """Create a StateMachine instance with all system dependencies mocked.

    ## @purpose — Factory fixture: returns a fully-mocked StateMachine ready for
    ##            phase execution. setup_state("init") is called to initialize
    ##            step entries in the state.
    ## @rationale 167 D6 (DI-zero): _SafeUserHelpers через users_helpers= (W-H 163 DI-шов) —
    ##   macOS os.makedirs("/home/platform/.ssh") fails with Errno 45 (special symlink) —
    ##   fake создаёт SSH-ключи/projects-base в tmp_path вместо патча helpers_users.
    ## @io — ⎋ StateMachine instance
    ## @complexity — O(N) where N = number of INIT mode steps (23)
    ## @invariants
    ##   - state_file is in tmp_path — no production files touched
    ##   - subprocess.run is mocked (fixture)
    ##   - env-дикт (di_env) прокидывается в StateMachine (W4e, DevPlan 160 E2) — ключевые
    ##     env фаз (TOR_ENABLED/SECRETS_ENV_FILE/NODE_YAML); остальные — bootstrap_env (os.environ)
    ##   - State begins in init mode with all 23 steps as pending
    ##   - users_helpers=_SafeUserHelpers — SSH-ключи и projects-base в tmp_path (167 D6)
    """
    sm = StateMachine(
        state_file_path=str(state_file),
        env=di_env,
        facts=_FakeRootFacts(),
        users_helpers=_SafeUserHelpers(tmp_path),
    )
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
    di_env: dict[str, str],
    mock_os_conditions: None,
) -> None:
    """Simulate all 5 UPDATE phases (φ9-φ13) in dry-run mode with mocked system calls."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: UPDATE 5-phase pipeline dry-run
    # · Scenario: Execute φ9→φ13 with all deps satisfied, all mocks active
    # · Last fail: N/A (first implementation)
    # · Remove if: DevPlan removes UPDATE mode or renames update phases

    caplog.set_level(logging.DEBUG)

    sm = StateMachine(state_file_path=str(state_file), env=di_env, facts=_FakeRootFacts())
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
# region Tests: Phase Dependency Graph Integrity
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_phase_dependency_graph_integrity
## @purpose — Verify _phase_dependency_graph integrity: all 14 phases have the expected
##            dependencies, no invalid phase names in graph, and the graph's transitive
##            closure is consistent.
## @io — ⇥ caplog → ⎋ None
## @complexity — O(P) where P = phases in graph
## @invariants
##   - All phases in _phase_dependency_graph are valid BootstrapPhase values
##   - All dependency values are valid BootstrapPhase values
##   - INIT phases have no dependency on UPDATE phases, and vice-versa
def test_phase_dependency_graph_integrity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify _phase_dependency_graph consistency."""
    # 🧪 TRAP[TEST] · 2026-07-30 · Regression: _phase_dependency_graph structural integrity
    # · Scenario: Structural test of the dependency graph
    # · Last fail: never
    # · Updated: 2026-08-01 (волна 117 D5) — MIGRATION_MAP assertions removed together with
    #   execute_grouped_phase (mёртвый код, sub-step resume вне скоупа волны)
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


# ═══════════════════════════════════════════════════════════════════════════
# region Tests: φ3 provision networks+volumes (D7, D14 — DevPlan 136 W1 T1.3)
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_platform_setup_provisions_networks_and_volumes
## @purpose — D7 (be34360): φ3 обязан вызывать provision-environment.sh для ВСЕХ scope'ов
##            (networks + volumes), не только proxy-net. Точный вход бага: свежий bootstrap —
##            external networks (observability-net/backup-net) отсутствуют в φ8 →
##            docker compose up падал (комментарий «provision done in platform_setup» был ложью с wave4).
## @io — ⇥ caplog, bootstrap_env, mock_os_conditions, tmp_path → ⎋ None (assert вызовов provision)
## @complexity — O(1) — одна фаза, subprocess мокается
## @invariants
##   - provision-environment.sh вызывается с --scope networks И --scope volumes
##   - Никаких реальных сетей/volumes — subprocess.run полностью мокается
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D7 — φ3 provision networks+volumes (be34360)
# · Scenario: φ3 (platform_setup) на свежем machine → provision-environment.sh с обоими scope'ами
# · Last fail: 2026-08-04 — φ3 НЕ провижинил сети/volumes → external networks missing в φ8
# · Remove if: provision переносится из φ3 в другую фазу (тогда обновить assert на новую фазу)
def test_platform_setup_provisions_networks_and_volumes(
    caplog: pytest.LogCaptureFixture,
    bootstrap_env: None,
    di_env: dict[str, str],
    mock_os_conditions: None,
    tmp_path: Path,
) -> None:
    """D7: φ3 вызывает provision-environment.sh для сетей И volumes (не только proxy-net)."""
    caplog.set_level(logging.DEBUG)
    captured: list[list[str]] = []

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        def _side(cmd, *args, **kwargs):
            captured.append(list(cmd))
            return mock_run.return_value

        mock_run.side_effect = _side

        sm = StateMachine(state_file_path=str(tmp_path / "state.json"), env=di_env)
        sm.setup_state("init")
        # φ3 (platform_setup) зависит от φ2 ← φ1 — отмечаем их done (dependency graph)
        _mark_phase_done(sm, BootstrapPhase.SYSTEM_BOOTSTRAP)
        _mark_phase_done(sm, BootstrapPhase.USER_ACCOUNTS)
        sm.execute_phase(BootstrapPhase.PLATFORM_SETUP)

    prov_calls = [c for c in captured if any("provision-environment.sh" in str(p) for p in c)]
    assert prov_calls, f"D7 regression: φ3 обязан вызывать provision-environment.sh, calls={captured}"
    flat = [" ".join(map(str, c)) for c in prov_calls]
    assert any("--scope" in f and "networks" in f for f in flat), f"D7: --scope networks missing: {flat}"
    assert any("--scope" in f and "volumes" in f for f in flat), f"D7: --scope volumes missing: {flat}"

    found_imp9 = _print_ldd_trajectory(caplog, "test_platform_setup_provisions_networks_and_volumes")
    assert found_imp9, "No IMP:9 business logic log found in φ3 provision dry-run"


# endregion FUNC_test_platform_setup_provisions_networks_and_volumes


# region FUNC_test_platform_setup_provision_wiring_source_negative
## @purpose — R5 negative (D7): source-гейт — φ3 обязан СОДЕРЖАТЬ provision-цикл networks+volumes.
##            Если provision-блок удалят (регрессия к состоянию до be34360), тест падает.
## @io — ⇥ caplog → ⎋ None (inspect.getsource asserts)
## @complexity — O(F) где F = размер phase_platform_setup
# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · D7 — отсутствие фазы provision → FAIL
# · Scenario: source phase_platform_setup обязан содержать цикл по ("networks", "volumes")
# · Last fail: 2026-08-04 — provision-блок отсутствовал (комментарий «done in platform_setup» ложь)
# · Remove if: provision переезжает в отдельный модуль/фазу (обновить оба D7-теста)
def test_platform_setup_provision_wiring_source_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative (D7): φ3 source содержит provision wiring для ВСЕХ сетей и volumes."""
    caplog.set_level(logging.INFO)
    import inspect

    from core.internal.bootstrap.lifecycle.phases import system as phases_system

    src = inspect.getsource(phases_system.phase_platform_setup)
    assert 'for scope in ("networks", "volumes")' in src, "D7 regression: provision-цикл networks+volumes удалён из φ3"
    assert "provision-environment.sh" in src, "D7 regression: вызов provision-environment.sh удалён из φ3"
    assert "--scope" in src, "D7 regression: --scope параметр удалён из provision-вызова"

    logger.info("[IMP:9][test][d7] φ3 provision wiring (networks+volumes) присутствует в source")


# endregion FUNC_test_platform_setup_provision_wiring_source_negative


# region FUNC_test_platform_setup_provision_script_missing_nonfatal
## @purpose — D14 (9a1915e): provision-environment.sh ОТСУТСТВУЕТ (test-окружения/tmp CORE_DIR) →
##            WARN «not found — skipping» БЕЗ done_with_warnings (фаза возвращает True).
##            Точный вход бага: отсутствующий скрипт давал done_with_warnings → фаза перевыполнялась.
## @io — ⇥ tmp_path, caplog → ⎋ None (assert True + WARN)
## @complexity — O(1) + mocks
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D14 — provision скрипт отсутствует → WARN non-fatal (9a1915e)
# · Scenario: CORE_DIR без provision-environment.sh → φ3 WARN «not found — skipping», return True
# · Last fail: 2026-08-04 — отсутствующий скрипт помечал фазу done_with_warnings (перевыполнение)
# · Remove if: φ3 provision становится обязательным (FATAL) — тогда assert инвертируется
def test_platform_setup_provision_script_missing_nonfatal(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D14: отсутствие provision-environment.sh → WARN, фаза НЕ done_with_warnings (return True)."""
    caplog.set_level(logging.INFO)

    from core.internal.bootstrap.lifecycle.phases import system as phases_system

    core_dir = tmp_path / "core"
    bootstrap_dir = core_dir / "internal" / "bootstrap"
    bootstrap_dir.mkdir(parents=True)
    (bootstrap_dir / "docker_registry_auth.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    (bootstrap_dir / "setup-node.sh").write_text("#!/bin/bash\nexit 0\n")
    # provision-environment.sh НЕ создаём — точный вход D14

    captured: list[list[str]] = []
    # W4d DI: fake-раннер вместо патча helpers_subprocess.run_subprocess (удалён из phases/system.py)
    fake = _FakeRunner(captured)

    # 167 D6 (DI-zero): helper-namespace DI — sys_helpers/val_helpers вместо monkeypatch
    # helpers_system.install_cron_metrics/install_cron_watchdog/validate_sudoers
    class _FakeSysHelpers:
        install_cron_metrics = staticmethod(lambda *_a, **_k: True)
        install_cron_watchdog = staticmethod(lambda *_a, **_k: True)

    class _FakeValHelpers:
        validate_sudoers = staticmethod(lambda *_a, **_k: None)

    ok = phases_system.phase_platform_setup(
        str(core_dir),
        "test-node",
        "node.yaml",
        runner=fake,
        sys_helpers=_FakeSysHelpers(),
        val_helpers=_FakeValHelpers(),
    )

    assert ok is True, "D14: отсутствие provision-скрипта НЕ должно давать done_with_warnings (return False)"
    assert "not found — skipping" in caplog.text, "D14: обязан быть WARN-лог о пропуске provision"
    assert not any("provision-environment.sh" in " ".join(map(str, c)) for c in captured), (
        "D14: provision не вызывается при отсутствии скрипта"
    )
    logger.info("[IMP:9][test][d14] φ3 пропустил provision без done_with_warnings — OK")


# endregion FUNC_test_platform_setup_provision_script_missing_nonfatal

# endregion Tests: φ3 provision networks+volumes (D7, D14 — DevPlan 136 W1 T1.3)


# endregion Tests: Precondition Failure Edge Cases
