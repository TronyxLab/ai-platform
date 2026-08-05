#!/usr/bin/env python3
# GREP_SUMMARY: test-bootstrap-system-phases phase-system-bootstrap phase-user-accounts phase-platform-setup root-precondition age-key execution-harness mock-shells idempotent
# STRUCTURE: fixtures(tmp core_dir + mock harness) → ◇ φ1 system_bootstrap (success, not-root raise, TOR_ENABLED, non-fatal scripts, idempotent re-run) → ◇ φ2 user_accounts (owner-key precondition, success + forced-command) → ◇ φ3 platform_setup (missing core raise, success) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Поведенческие unit-тесты фаз users/docker/system из lifecycle/phases/system.py
##            (DevPlan 139 W4.5 — закрытие blind spot phases/system, 655 LOC). Исполняются через
##            execution harness (мок-шелы/фикстуры, НЕ subprocess реальной ноды): успешный прогон,
##            precondition-fail (не-root, отсутствие AGE/owner-ключа), idempotent re-run.
## @scope    φ1 phase_system_bootstrap (root fail-fast, apt/tor/docker/firewall/journald/security
##           шаги, non-fatal семантика, идемпотентный re-run), φ2 phase_user_accounts
##           (PLATFORM_OWNER_KEY precondition, создание пользователей, ci-deploy forced-command
##           orchestrator_cli dispatch), φ3 phase_platform_setup (core_dir precondition, docker auth,
##           setup-node/sudoers, metrics+watchdog cron, validate_sudoers).
## @invariants
##   - Execution harness: helpers (system/users/validation) и run_subprocess мокаются; 0 subprocess
##     реальной ноды; os.geteuid мокается (0 для success, !=0 для precondition-fail)
##   - tmp_path-изоляция CORE_DIR (xdist); фазы НЕ пишут state.json (чистая функция, инвариант 5)
##   - Precondition-fail: не-root → PlatformFatalError; нет PLATFORM_OWNER_KEY → PlatformFatalError;
##     нет core_dir → ConfigNotFoundError
##   - Non-fatal: missing scripts → WARN + False (done_with_warnings), никогда не raise
##   - Idempotent re-run: повторный вызов фазы на тех же входных данных → тот же результат,
##     без исключений и без state-мутаций
##   - Test Honesty R1-R5: negative-тесты (precondition-fail, non-fatal scripts) — 0 pass-тестов
##   - LDD: каждый тест — IMP:9-траектория (ldd_trajectory)
## @rationale W4 (139): 655 LOC production без тестов — критические фазы bootstrap (φ1-φ3).
##            Поведенческие контракты из MODULE_CONTRACT phases/system — в исполняемые проверки
##            (запрещена синтетическая bash-симуляция — класс P0, инвариант 6 DevPlan 139).
## @changes  2026-08-05 | Created (DevPlan 139 W4.5)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from core.internal.bootstrap.lifecycle.helpers import system as helpers_system
from core.internal.bootstrap.lifecycle.helpers import users as helpers_users
from core.internal.bootstrap.lifecycle.helpers import validation as helpers_validation
from core.internal.bootstrap.lifecycle.phases import system as sys_phases
from core.internal.shared import subprocess_io as helpers_subprocess
from core.internal.shared.exceptions import ConfigNotFoundError, PlatformFatalError
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# Имена best-effort скриптов, которые должны присутствовать в tmp CORE_DIR для success-прогона φ1
_PHI1_REQUIRED_SCRIPTS = (
    "python_deps.py",
    "install-docker.sh",
    "firewall.sh",
    "security_updates.py",
    "security_posture.py",
)


# region FUNC__make_core_dir
## @purpose  Создать tmp core_dir с внутренней bootstrap-структурой (все best-effort скрипты φ1).
## @io       ⇥ tmp_path: Path → ⎋ Path (core_dir)
## @complexity O(N) где N = scripts
def _make_core_dir(tmp_path: Path, scripts: tuple[str, ...] = _PHI1_REQUIRED_SCRIPTS) -> Path:
    """Create a tmp core_dir with the bootstrap scripts expected by φ1/φ3."""
    core_dir = tmp_path / "core"
    bootstrap_dir = core_dir / "internal" / "bootstrap"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    for script in scripts:
        (bootstrap_dir / script).write_text("#!/bin/bash\necho ok\n")
    return core_dir


# endregion FUNC__make_core_dir


# region FUNC__ok_process
## @purpose  Завершённый subprocess с rc=0 (стандартный успех для run_subprocess mock).
## @io       ⇥ cmd: list[str] | None → ⎋ subprocess.CompletedProcess
## @complexity O(1)
def _ok_process(cmd: list[str] | None = None) -> subprocess.CompletedProcess:
    """Return a successful CompletedProcess for run_subprocess mocks."""
    return subprocess.CompletedProcess(cmd or ["ok"], 0, stdout="", stderr="")


# endregion FUNC__ok_process


# region FUNC__patch_phi1_helpers
## @purpose  Мокнуть все helpers φ1 (install_apt_packages, run_subprocess, ensure_sops,
##            ensure_journald_persistent) — execution harness для φ1.
## @io       ⇥ monkeypatch → ⎋ None (side-effect: патчи)
## @complexity O(1)
def _patch_phi1_helpers(monkeypatch) -> None:
    """Patch φ1 helpers: apt, subprocess canon, sops, journald (execution harness)."""
    monkeypatch.setattr(helpers_system, "install_apt_packages", mock.Mock())
    monkeypatch.setattr(helpers_system, "ensure_sops", mock.Mock())
    monkeypatch.setattr(helpers_system, "ensure_journald_persistent", mock.Mock(return_value=True))
    monkeypatch.setattr(
        helpers_subprocess,
        "run_subprocess",
        mock.Mock(return_value=_ok_process()),
    )


# endregion FUNC__patch_phi1_helpers


# ═══════════════════════════════════════════════════════════════════════════
# φ1 phase_system_bootstrap
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_phase_system_bootstrap_success
## @purpose  Успешный прогон φ1 (root, все скрипты присутствуют, helpers OK) → True + IMP:9 complete.
# 🧪 TRAP[TEST] · phase_system_bootstrap_success · Behavioral · Regression: φ1 не завершается успешно
# · Scenario: geteuid=0; все best-effort скрипты в core_dir; helpers mocked OK → True;
# ·   IMP:9 «φ1 complete» присутствует
# · Last fail: N/A (новый тест W4.5)
# · Remove if: контракт φ1 (success → True) меняется
@ldd_trajectory
def test_phase_system_bootstrap_success(tmp_path, monkeypatch, caplog) -> None:
    """φ1 success: root + все скрипты + helpers OK → True, IMP:9 complete."""
    core_dir = _make_core_dir(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setenv("TOR_ENABLED", "false")
    _patch_phi1_helpers(monkeypatch)

    result = sys_phases.phase_system_bootstrap(str(core_dir), node_name="test-node", node_yaml="")

    assert result is True, "Успешный φ1 обязан вернуть True"
    complete = [r.message for r in caplog.records if "φ1 complete" in r.message]
    assert complete, "Ожидался IMP:9 «φ1 complete»"
    logger.info("[IMP:9][test] φ1 system_bootstrap: success → True, IMP:9 complete ✓")


# endregion FUNC_test_phase_system_bootstrap_success


# region FUNC_test_phase_system_bootstrap_not_root_raises
## @purpose  Precondition-fail: euid != 0 → PlatformFatalError (fail-fast, до любых helper-вызовов).
# 🧪 TRAP[TEST] · phase_system_bootstrap_not_root · NEGATIVE (R5) · Regression: не-root прогон φ1 не блокируется
# · Scenario: geteuid=1000 → PlatformFatalError «must run as root»; install_apt_packages НЕ вызван
# · Last fail: N/A (новый negative-тест W4.5)
# · Remove if: root-прекондишен φ1 меняется
@ldd_trajectory
def test_phase_system_bootstrap_not_root_raises(tmp_path, monkeypatch, caplog) -> None:
    """Не-root → PlatformFatalError до выполнения шагов."""
    core_dir = _make_core_dir(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    apt_mock = mock.Mock()
    monkeypatch.setattr(helpers_system, "install_apt_packages", apt_mock)

    with pytest.raises(PlatformFatalError, match="must run as root"):
        sys_phases.phase_system_bootstrap(str(core_dir), node_name="test-node", node_yaml="")

    apt_mock.assert_not_called(), "Fail-fast: до helper-вызовов"
    logger.info("[IMP:9][test] φ1: не-root → PlatformFatalError (fail-fast) ✓")


# endregion FUNC_test_phase_system_bootstrap_not_root_raises


# region FUNC_test_phase_system_bootstrap_tor_enabled_packages
## @purpose  TOR_ENABLED=true → apt-пакеты включают tor/privoxy/obfs4proxy; tor-скрипт вызывается.
# 🧪 TRAP[TEST] · phase_system_bootstrap_tor_enabled · Behavioral · Regression: TOR_ENABLED игнорируется
# · Scenario: TOR_ENABLED=true; install-tor-proxy.sh в core_dir; install_apt_packages вызван с
# ·   tor/privoxy/obfs4proxy; run_subprocess вызван с bash install-tor-proxy.sh → True
# · Last fail: N/A (новый тест W4.5)
# · Remove if: условная установка Tor меняется
@ldd_trajectory
def test_phase_system_bootstrap_tor_enabled_packages(tmp_path, monkeypatch, caplog) -> None:
    """TOR_ENABLED=true → apt-пакеты tor/privoxy/obfs4proxy + tor-скрипт исполняется."""
    core_dir = _make_core_dir(tmp_path)
    (core_dir / "internal" / "bootstrap" / "install-tor-proxy.sh").write_text("#!/bin/bash\necho tor\n")
    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setenv("TOR_ENABLED", "true")
    monkeypatch.setenv("TOR_BRIDGES_FILE", "")
    monkeypatch.setenv("SKIP_TOR_VERIFY", "false")
    _patch_phi1_helpers(monkeypatch)

    result = sys_phases.phase_system_bootstrap(str(core_dir), node_name="test-node", node_yaml="")

    assert result is True
    apt_calls = helpers_system.install_apt_packages.call_args_list
    assert apt_calls, "install_apt_packages вызван"
    packages = apt_calls[0].args[0]
    for pkg in ("tor", "privoxy", "obfs4proxy"):
        assert pkg in packages, f"TOR-пакеты должны включать {pkg}, got {packages}"
    tor_logs = [r.message for r in caplog.records if "Tor proxy installed" in r.message]
    assert tor_logs, "Ожидался IMP:9 «Tor proxy installed»"
    logger.info("[IMP:9][test] φ1: TOR_ENABLED=true → tor/privoxy/obfs4proxy в apt-пакетах ✓")


# endregion FUNC_test_phase_system_bootstrap_tor_enabled_packages


# region FUNC_test_phase_system_bootstrap_missing_scripts_nonfatal
## @purpose  Non-fatal: отсутствие best-effort скриптов (python_deps, install-docker, firewall,
##            security_updates) → WARN + False (done_with_warnings), НЕ raise.
# 🧪 TRAP[TEST] · phase_system_bootstrap_missing_scripts_nonfatal · NEGATIVE (R5) · Regression: missing-скрипты роняют φ1
# · Scenario: пустой core_dir (без скриптов) → False + IMP:8 «Complete with non-fatal issues»;
# ·   исключений нет (non-fatal контракт)
# · Last fail: N/A (новый negative-тест W4.5)
# · Remove if: non-fatal семантика φ1 меняется (missing скрипт начинает raise)
@ldd_trajectory
def test_phase_system_bootstrap_missing_scripts_nonfatal(tmp_path, monkeypatch, caplog) -> None:
    """Отсутствие best-effort скриптов → WARN + False (done_with_warnings), не raise."""
    core_dir = _make_core_dir(tmp_path, scripts=())
    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setenv("TOR_ENABLED", "false")
    _patch_phi1_helpers(monkeypatch)

    result = sys_phases.phase_system_bootstrap(str(core_dir), node_name="test-node", node_yaml="")

    assert result is False, "Non-fatal: отсутствие скриптов → False (done_with_warnings)"
    nonfatal = [r.message for r in caplog.records if "Complete with non-fatal issues" in r.message]
    assert nonfatal, "Ожидался IMP:8 «Complete with non-fatal issues»"
    logger.info("[IMP:9][test] φ1: missing scripts → False (non-fatal), без raise ✓")


# endregion FUNC_test_phase_system_bootstrap_missing_scripts_nonfatal


# region FUNC_test_phase_system_bootstrap_idempotent_rerun
## @purpose  Идемпотентный re-run: повторный вызов φ1 на тех же входах → тот же результат (True),
##            без state.json-мутаций; helper-вызовы повторяются (идемпотентность — на уровне
##            phase-статусов state_machine, фаза безопасна для re-run — инвариант 1).
# 🧪 TRAP[TEST] · phase_system_bootstrap_idempotent_rerun · Behavioral (idempotency) · Regression: повторный φ1 небезопасен
# · Scenario: φ1 вызван дважды (одинаковые входы) → оба True; state.json не создан;
# ·   install_apt_packages.call_count == 2
# · Last fail: N/A (новый тест W4.5)
# · Remove if: фаза начинает мутировать состояние (state.json) между вызовами
@ldd_trajectory
def test_phase_system_bootstrap_idempotent_rerun(tmp_path, monkeypatch, caplog) -> None:
    """Повторный прогон φ1 → тот же результат, без state-мутаций."""
    core_dir = _make_core_dir(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setenv("TOR_ENABLED", "false")
    _patch_phi1_helpers(monkeypatch)

    first = sys_phases.phase_system_bootstrap(str(core_dir), node_name="test-node", node_yaml="")
    second = sys_phases.phase_system_bootstrap(str(core_dir), node_name="test-node", node_yaml="")

    assert first is True and second is True, "Повторный прогон → тот же результат"
    assert helpers_system.install_apt_packages.call_count == 2, "Фаза re-runnable (helpers перевызываются)"
    assert not (tmp_path / "state.json").exists(), "Фаза не пишет state.json (инвариант 5)"
    logger.info("[IMP:9][test] φ1: idempotent re-run → True/True, state.json не создан ✓")


# endregion FUNC_test_phase_system_bootstrap_idempotent_rerun


# ═══════════════════════════════════════════════════════════════════════════
# φ2 phase_user_accounts
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_phase_user_accounts_missing_owner_key_raises
## @purpose  Precondition-fail: PLATFORM_OWNER_KEY отсутствует → PlatformFatalError (до любых вызовов).
# 🧪 TRAP[TEST] · phase_user_accounts_missing_owner_key · NEGATIVE (R5) · Regression: φ2 без owner-ключа не блокируется
# · Scenario: env без PLATFORM_OWNER_KEY → PlatformFatalError «PLATFORM_OWNER_KEY is required»;
# ·   create_user НЕ вызван
# · Last fail: N/A (новый negative-тест W4.5)
# · Remove if: требование PLATFORM_OWNER_KEY меняется
@ldd_trajectory
def test_phase_user_accounts_missing_owner_key_raises(monkeypatch, caplog) -> None:
    """Нет PLATFORM_OWNER_KEY → PlatformFatalError (fail-fast)."""
    monkeypatch.delenv("PLATFORM_OWNER_KEY", raising=False)
    create_mock = mock.Mock()
    monkeypatch.setattr(helpers_users, "create_user", create_mock)

    with pytest.raises(PlatformFatalError, match="PLATFORM_OWNER_KEY is required"):
        sys_phases.phase_user_accounts("/tmp/core", node_name="test-node", node_yaml="")

    create_mock.assert_not_called(), "Fail-fast: до создания пользователей"
    logger.info("[IMP:9][test] φ2: нет PLATFORM_OWNER_KEY → PlatformFatalError ✓")


# endregion FUNC_test_phase_user_accounts_missing_owner_key_raises


# region FUNC_test_phase_user_accounts_success_forced_command
## @purpose  Успешный φ2: platform + ci-deploy пользователи, owner-key, ci-deploy ключ с
##            forced-command prefix orchestrator_cli dispatch (канон B1), projects base → True.
# 🧪 TRAP[TEST] · phase_user_accounts_success · Behavioral · Regression: φ2 не создаёт пользователей/ключи
# · Scenario: PLATFORM_OWNER_KEY/PLATFORM_CI_DEPLOY_KEY заданы; helpers mocked → True;
# ·   add_ssh_key для ci-deploy вызван с forced_command_prefix содержащим «orchestrator_cli dispatch»
# · Last fail: N/A (новый тест W4.5)
# · Remove if: контракт φ2 (пользователи/ключи/forced-command) меняется
@ldd_trajectory
def test_phase_user_accounts_success_forced_command(monkeypatch, caplog) -> None:
    """Успешный φ2: пользователи созданы, ci-deploy ключ с forced-command orchestrator_cli dispatch."""
    monkeypatch.setenv("PLATFORM_OWNER_KEY", "ssh-ed25519 AAAA owner")
    monkeypatch.setenv("PLATFORM_CI_DEPLOY_KEY", "ssh-ed25519 AAAA deploy")
    create_mock = mock.Mock()
    add_key_mock = mock.Mock()
    ensure_base_mock = mock.Mock()
    monkeypatch.setattr(helpers_users, "create_user", create_mock)
    monkeypatch.setattr(helpers_users, "add_ssh_key", add_key_mock)
    monkeypatch.setattr(helpers_users, "ensure_projects_base", ensure_base_mock)

    result = sys_phases.phase_user_accounts("/tmp/core", node_name="test-node", node_yaml="")

    assert result is True, "Успешный φ2 → True"
    assert create_mock.call_count == 2, "platform + ci-deploy пользователи"
    # ci-deploy ключ: forced-command prefix (B1 канон)
    deploy_key_calls = [c for c in add_key_mock.call_args_list if c.args[0] == "ci-deploy"]
    assert deploy_key_calls, "ci-deploy ключ добавлен"
    prefix = deploy_key_calls[0].kwargs.get("forced_command_prefix", "")
    assert "orchestrator_cli dispatch" in prefix, f"Forced-command dispatch ожидался, got {prefix!r}"
    assert "command=" in prefix and "restrict" in prefix
    logger.info("[IMP:9][test] φ2: users созданы, ci-deploy forced-command = orchestrator_cli dispatch ✓")


# endregion FUNC_test_phase_user_accounts_success_forced_command


# ═══════════════════════════════════════════════════════════════════════════
# φ3 phase_platform_setup
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_phase_platform_setup_missing_core_raises
## @purpose  Precondition-fail: core_dir не существует → ConfigNotFoundError.
# 🧪 TRAP[TEST] · phase_platform_setup_missing_core · NEGATIVE (R5) · Regression: φ3 без core_dir не блокируется
# · Scenario: core_dir не существует → ConfigNotFoundError «Core directory not found»
# · Last fail: N/A (новый negative-тест W4.5)
# · Remove if: требование существования core_dir меняется
@ldd_trajectory
def test_phase_platform_setup_missing_core_raises(tmp_path, caplog) -> None:
    """core_dir отсутствует → ConfigNotFoundError."""
    with pytest.raises(ConfigNotFoundError, match="Core directory not found"):
        sys_phases.phase_platform_setup(str(tmp_path / "no-core"), node_name="test-node", node_yaml="")
    logger.info("[IMP:9][test] φ3: нет core_dir → ConfigNotFoundError ✓")


# endregion FUNC_test_phase_platform_setup_missing_core_raises


# region FUNC_test_phase_platform_setup_success
## @purpose  Успешный φ3: docker_registry_auth (отсутствует → WARN, НЕ non_fatal), setup-node.sh
##            исполнен (sudoers), metrics+watchdog cron OK, validate_sudoers OK → True.
# 🧪 TRAP[TEST] · phase_platform_setup_success · Behavioral · Regression: φ3 не завершается успешно
# · Scenario: core_dir с setup-node.sh; cron-хелперы mocked True; validate_sudoers mocked OK;
# ·   docker_registry_auth.py отсутствует → WARN (не non_fatal) → True; IMP:9 «φ3 complete»
# · Last fail: N/A (новый тест W4.5)
# · Remove if: контракт φ3 (success → True) меняется
@ldd_trajectory
def test_phase_platform_setup_success(tmp_path, monkeypatch, caplog) -> None:
    """Успешный φ3: sudoers setup + cron OK → True, IMP:9 complete."""
    core_dir = _make_core_dir(tmp_path, scripts=("setup-node.sh",))
    monkeypatch.setattr(helpers_system, "install_cron_metrics", mock.Mock(return_value=True))
    monkeypatch.setattr(helpers_system, "install_cron_watchdog", mock.Mock(return_value=True))
    monkeypatch.setattr(helpers_validation, "validate_sudoers", mock.Mock())
    monkeypatch.setattr(
        helpers_subprocess,
        "run_subprocess",
        mock.Mock(return_value=_ok_process()),
    )

    result = sys_phases.phase_platform_setup(str(core_dir), node_name="test-node", node_yaml="")

    assert result is True, "Успешный φ3 → True"
    complete = [r.message for r in caplog.records if "φ3 complete" in r.message]
    assert complete, "Ожидался IMP:9 «φ3 complete»"
    logger.info("[IMP:9][test] φ3: platform_setup success → True, IMP:9 complete ✓")


# endregion FUNC_test_phase_platform_setup_success
