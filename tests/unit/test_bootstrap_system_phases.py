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
## @changes  2026-08-13 | DevPlan 160 E3 — φ1/φ2 env через env= дикты (0 setenv);
##            /tmp/core → tmp_path (Zero Hardcode); φ2 missing-key: runner.calls assert вместо
##            create_user-mock (helpers_users остаются мок-харнессом — реальные FS-операции /home, /opt)
## @changes  2026-08-13 | DevPlan 162 — φ1 helpers harness +install_zram/install_cron_prune/purge_cruft
##           (W4-1/W4-4/W10-1); +timezone tests (W7-3); +converge rc-mapping tests (W7-2)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from core.internal.bootstrap.lifecycle.phases import system as sys_phases
from core.internal.shared.exceptions import ConfigNotFoundError, PlatformFatalError
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# Имена best-effort скриптов, которые должны присутствовать в tmp CORE_DIR для success-прогона φ1
_PHI1_REQUIRED_SCRIPTS = (
    "python_deps.py",
    "install-docker.sh",
    "firewall.sh",
    "security_updates.py",
    "security_posture.py",
)


# region CLASS_FakeRunner
class FakeRunner:
    """CommandRunner-fake (W4d DI): запись вызовов + scripted rc; 0 subprocess-патчей.

    ## @purpose — DI для runner= параметра фаз: runner.run(cmd, check=True) бросает
    ##            PlatformFatalError при rc!=0; вызовы записываются для ассертов.
    ## @io — ⇥ rc: int, out: str → ⎋ fake (calls: list[list[str]])
    """

    def __init__(self, rc: int = 0, out: str = "") -> None:
        self.calls: list[list[str]] = []
        self._rc = rc
        self._out = out

    def run(self, cmd, *, timeout=None, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        """Записать вызов; check-семантика: rc!=0 → PlatformFatalError."""
        self.calls.append(list(cmd))
        if check and self._rc != 0:
            msg = f"Command {' '.join(cmd)} failed (exit={self._rc}): {self._out}"
            raise PlatformFatalError(msg)
        return subprocess.CompletedProcess(list(cmd), self._rc, stdout=self._out, stderr="")


# endregion CLASS_FakeRunner


# region CLASS_FakeFacts
class FakeFacts:
    """EnvironmentFacts-fake (W4d DI): is_root/which/path_isfile — 0 os-патчей.

    ## @purpose — DI для facts= параметра фаз: is_root управляет root-guard'ом
    ##            (вместо патча os.geteuid); path_isfile —
    ##            реальный os.path.isfile (tmp_path-файлы видны без настройки).
    ## @io — ⇥ is_root: bool → ⎋ fake
    """

    def __init__(self, is_root: bool = True) -> None:
        self._is_root = is_root

    def is_root(self) -> bool:
        return self._is_root

    def which(self, binary: str) -> str | None:
        return binary

    def path_isfile(self, path) -> bool:
        return Path(path).is_file()


# endregion CLASS_FakeFacts


# region CLASS_FakeSystemHelpers
class FakeSystemHelpers:
    """System-хелперы fake (W-H DevPlan 163, DI): recording-моки вместо патча helpers.

    ## @purpose — DI для helpers= параметра φ1/φ3: install_apt_packages/ensure_sops/
    ##            ensure_journald_persistent/install_zram/install_cron_prune/purge_cruft/
    ##            purge_provider_repos/ensure_fstab_policy/
    ##            install_cron_metrics/install_cron_watchdog — все вызываются как mock-заглушки
    ##            (реальный код писал бы в /etc, /home — не writable в unit-тесте).
    ##            Реализация = простые call-recording mock-объекты (unittest.mock.Mock).
    ## @io — ⇥ None → ⎋ namespace (атрибуты — mock.Mock)
    ## @invariants
    ##   - Каждый метод — mock.Mock (call_args_list/call_count доступны для ассертов)
    ##   - return_value=True по умолчанию для bool-хелперов (non-fatal контракт happy path)
    """

    def __init__(self) -> None:
        self.install_apt_packages = mock.Mock()
        self.ensure_sops = mock.Mock()
        self.ensure_journald_persistent = mock.Mock(return_value=True)
        self.install_zram = mock.Mock(return_value=True)
        self.install_cron_prune = mock.Mock(return_value=True)
        self.purge_cruft = mock.Mock(return_value=True)
        self.purge_provider_repos = mock.Mock(return_value=True)
        self.ensure_fstab_policy = mock.Mock(return_value=True)
        self.install_cron_metrics = mock.Mock(return_value=True)
        self.install_cron_watchdog = mock.Mock(return_value=True)


# endregion CLASS_FakeSystemHelpers


# region CLASS_FakeUserHelpers
class FakeUserHelpers:
    """User-хелперы fake (W-H DevPlan 163, DI): create_user/add_ssh_key/ensure_projects_base.

    ## @purpose — DI для users_helpers= параметра φ2 (тесты без патча helpers_users).
    ## @io — ⇥ None → ⎋ namespace (атрибуты — mock.Mock)
    """

    def __init__(self) -> None:
        self.create_user = mock.Mock()
        self.add_ssh_key = mock.Mock()
        self.ensure_projects_base = mock.Mock()


# endregion CLASS_FakeUserHelpers


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
def test_phase_system_bootstrap_success(tmp_path, caplog) -> None:
    """φ1 success: root + все скрипты + helpers OK → True, IMP:9 complete."""
    core_dir = _make_core_dir(tmp_path)
    # W-H (163): helpers-неймспейс DI (fake вместо патча helpers)
    hs = FakeSystemHelpers()

    result = sys_phases.phase_system_bootstrap(
        str(core_dir),
        node_name="test-node",
        node_yaml="",
        runner=FakeRunner(),
        facts=FakeFacts(is_root=True),
        env={"TOR_ENABLED": "false"},
        helpers=hs,
    )

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
def test_phase_system_bootstrap_not_root_raises(tmp_path, caplog) -> None:
    """Не-root → PlatformFatalError до выполнения шагов."""
    core_dir = _make_core_dir(tmp_path)
    hs = FakeSystemHelpers()

    with pytest.raises(PlatformFatalError, match="must run as root"):
        # W5 T5.3: facts= DI (is_root=False) вместо патча os.geteuid
        sys_phases.phase_system_bootstrap(
            str(core_dir),
            node_name="test-node",
            node_yaml="",
            runner=FakeRunner(),
            facts=FakeFacts(is_root=False),
            helpers=hs,
        )

    hs.install_apt_packages.assert_not_called(), "Fail-fast: до helper-вызовов"
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
def test_phase_system_bootstrap_tor_enabled_packages(tmp_path, caplog) -> None:
    """TOR_ENABLED=true → apt-пакеты tor/privoxy/obfs4proxy + tor-скрипт исполняется."""
    core_dir = _make_core_dir(tmp_path)
    (core_dir / "internal" / "bootstrap" / "install-tor-proxy.sh").write_text("#!/bin/bash\necho tor\n")
    hs = FakeSystemHelpers()

    result = sys_phases.phase_system_bootstrap(
        str(core_dir),
        node_name="test-node",
        node_yaml="",
        runner=FakeRunner(),
        facts=FakeFacts(is_root=True),
        # E3 (160): TOR_* — env-дикт (паттерн E2), 0 setenv
        env={"TOR_ENABLED": "true", "TOR_BRIDGES_FILE": "", "SKIP_TOR_VERIFY": "false"},
        helpers=hs,
    )

    assert result is True
    apt_calls = hs.install_apt_packages.call_args_list
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
def test_phase_system_bootstrap_missing_scripts_nonfatal(tmp_path, caplog) -> None:
    """Отсутствие best-effort скриптов → WARN + False (done_with_warnings), не raise."""
    core_dir = _make_core_dir(tmp_path, scripts=())
    hs = FakeSystemHelpers()

    result = sys_phases.phase_system_bootstrap(
        str(core_dir),
        node_name="test-node",
        node_yaml="",
        runner=FakeRunner(),
        facts=FakeFacts(is_root=True),
        env={"TOR_ENABLED": "false"},
        helpers=hs,
    )

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
def test_phase_system_bootstrap_idempotent_rerun(tmp_path, caplog) -> None:
    """Повторный прогон φ1 → тот же результат, без state-мутаций."""
    core_dir = _make_core_dir(tmp_path)
    hs = FakeSystemHelpers()

    runner = FakeRunner()
    first = sys_phases.phase_system_bootstrap(
        str(core_dir),
        node_name="test-node",
        node_yaml="",
        runner=runner,
        facts=FakeFacts(is_root=True),
        env={"TOR_ENABLED": "false"},
        helpers=hs,
    )
    second = sys_phases.phase_system_bootstrap(
        str(core_dir),
        node_name="test-node",
        node_yaml="",
        runner=runner,
        facts=FakeFacts(is_root=True),
        env={"TOR_ENABLED": "false"},
        helpers=hs,
    )

    assert first is True and second is True, "Повторный прогон → тот же результат"
    assert hs.install_apt_packages.call_count == 2, "Фаза re-runnable (helpers перевызываются)"
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
# ·   create_user НЕ вызван (E3: runner.calls == [] — фаза fail-fast до любых user-операций)
# · Last fail: N/A (новый negative-тест W4.5)
# · Remove if: требование PLATFORM_OWNER_KEY меняется
@ldd_trajectory
def test_phase_user_accounts_missing_owner_key_raises(tmp_path, caplog) -> None:
    """Нет PLATFORM_OWNER_KEY → PlatformFatalError (fail-fast), 0 вызовов runner (DI)."""
    runner = FakeRunner()
    with pytest.raises(PlatformFatalError, match="PLATFORM_OWNER_KEY is required"):
        # E3 (160): env-дикт (пустой) — фаза читает PLATFORM_* через env=
        sys_phases.phase_user_accounts(
            str(tmp_path / "core"), node_name="test-node", node_yaml="", runner=runner, env={}
        )

    assert runner.calls == [], "Fail-fast: до любых user-операций (runner не вызывался)"
    logger.info("[IMP:9][test] φ2: нет PLATFORM_OWNER_KEY → PlatformFatalError ✓")


# endregion FUNC_test_phase_user_accounts_missing_owner_key_raises


# region FUNC_test_phase_user_accounts_success_forced_command
## @purpose  Успешный φ2: platform + ci-deploy пользователи, owner-key, ci-deploy ключ с
##            forced-command prefix orchestrator_cli dispatch (канон B1), projects base → True.
# 🧪 TRAP[TEST] · phase_user_accounts_success · Behavioral · Regression: φ2 не создаёт пользователей/ключи
# · Scenario: PLATFORM_OWNER_KEY/PLATFORM_CI_DEPLOY_KEY заданы (env-дикт E3); helpers mocked → True;
# ·   add_ssh_key для ci-deploy вызван с forced_command_prefix содержащим «orchestrator_cli dispatch»
# · Last fail: N/A (новый тест W4.5)
# · Remove if: контракт φ2 (пользователи/ключи/forced-command) меняется
@ldd_trajectory
def test_phase_user_accounts_success_forced_command(tmp_path, caplog) -> None:
    """Успешный φ2: пользователи созданы, ci-deploy ключ с forced-command orchestrator_cli dispatch."""
    hu = FakeUserHelpers()

    # E3 (160): PLATFORM_* — env-дикт (фаза читает через env=), 0 setenv
    result = sys_phases.phase_user_accounts(
        str(tmp_path / "core"),
        node_name="test-node",
        node_yaml="",
        runner=FakeRunner(),
        env={"PLATFORM_OWNER_KEY": "ssh-ed25519 AAAA owner", "PLATFORM_CI_DEPLOY_KEY": "ssh-ed25519 AAAA deploy"},
        users_helpers=hu,
    )

    assert result is True, "Успешный φ2 → True"
    assert hu.create_user.call_count == 2, "platform + ci-deploy пользователи"
    # ci-deploy ключ: forced-command prefix (B1 канон)
    deploy_key_calls = [c for c in hu.add_ssh_key.call_args_list if c.args[0] == "ci-deploy"]
    assert deploy_key_calls, "ci-deploy ключ добавлен"
    prefix = deploy_key_calls[0].kwargs.get("forced_command_prefix", "")
    assert "orchestrator_cli dispatch" in prefix, f"Forced-command dispatch ожидался, got {prefix!r}"
    assert "command=" in prefix and "restrict" in prefix
    logger.info("[IMP:9][test] φ2: users созданы, ci-deploy forced-command = orchestrator_cli dispatch ✓")


# endregion FUNC_test_phase_user_accounts_success_forced_command


# region FUNC_test_phase_user_accounts_ci_root_key_added
## @purpose  142 W1 (A1): PLATFORM_CI_ROOT_KEY задан → add_ssh_key("root", key, home_dir="/root")
##           вызывается (root authorized_keys для core-deploy root-канала). Фаза True.
# 🧪 TRAP[TEST] · phase_user_accounts_ci_root_key · Behavioral · Regression: CI-root ключ не доставлялся
# · Scenario: PLATFORM_CI_ROOT_KEY=ssh-ed25519 AAAA ci-root → add_ssh_key вызывается с
# ·   username="root", home_dir="/root" (passwd-резолв root = /root, не /home/root);
# ·   результат True; WARN-лога «not set» нет
# · Last fail: 2026-08-06 (цикл 1/2 141) — ci-core-deploy ключ добавлялся ВРУЧНУЮ в authorized_keys
# ·   после bootstrap (A1 из реестра ручных действий 142 §2)
# · Remove if: φ2 перестанет доставлять CI-root ключ
@ldd_trajectory
def test_phase_user_accounts_ci_root_key_added(tmp_path, caplog) -> None:
    """142 W1: PLATFORM_CI_ROOT_KEY → add_ssh_key("root", key, home_dir="/root")."""
    caplog.set_level(logging.INFO)
    hu = FakeUserHelpers()

    # E3 (160): PLATFORM_* — env-дикт (0 setenv)
    result = sys_phases.phase_user_accounts(
        str(tmp_path / "core"),
        node_name="test-node",
        node_yaml="",
        runner=FakeRunner(),
        env={
            "PLATFORM_OWNER_KEY": "ssh-ed25519 AAAA owner",
            "PLATFORM_CI_DEPLOY_KEY": "ssh-ed25519 AAAA deploy",
            "PLATFORM_CI_ROOT_KEY": "ssh-ed25519 AAAA ci-root",
        },
        users_helpers=hu,
    )

    assert result is True
    root_calls = [c for c in hu.add_ssh_key.call_args_list if c.args[0] == "root"]
    assert root_calls, "add_ssh_key('root', ...) обязан вызываться (142 W1)"
    assert root_calls[0].args[1] == "ssh-ed25519 AAAA ci-root", "передан именно ci_root_key"
    assert root_calls[0].kwargs.get("home_dir") == "/root", (
        f"root home_dir должен быть /root (не /home/root), got {root_calls[0].kwargs.get('home_dir')!r}"
    )
    assert "CI-root SSH key added" in caplog.text
    logger.critical("[IMP:9][test] φ2: CI-root ключ → add_ssh_key(root, /root) ✓ (142 W1)")


# endregion FUNC_test_phase_user_accounts_ci_root_key_added


# region FUNC_test_phase_user_accounts_ci_root_key_missing_warns
## @purpose  142 W1: PLATFORM_CI_ROOT_KEY отсутствует → WARN (semi-optional), фаза НЕ падает,
##           add_ssh_key("root", ...) не вызывается.
# 🧪 TRAP[TEST] · phase_user_accounts_ci_root_key_missing · NEGATIVE (R5) · Regression: отсутствие ключа молчит
# · Scenario: env БЕЗ PLATFORM_CI_ROOT_KEY → WARN «PLATFORM_CI_ROOT_KEY not set», результат True,
# ·   root-вызовов add_ssh_key нет (не блокирует bootstrap — core-deploy канал падает позже, на CI)
# · Last fail: N/A (новый negative-тест 142 W1)
# · Remove if: CI-root ключ становится обязательным (fatal)
@ldd_trajectory
def test_phase_user_accounts_ci_root_key_missing_warns(tmp_path, caplog) -> None:
    """142 W1: без PLATFORM_CI_ROOT_KEY → WARN + True, root-ключ не добавляется."""
    caplog.set_level(logging.INFO)
    hu = FakeUserHelpers()

    # E3 (160): env-дикт БЕЗ PLATFORM_CI_ROOT_KEY (0 setenv/delenv)
    result = sys_phases.phase_user_accounts(
        str(tmp_path / "core"),
        node_name="test-node",
        node_yaml="",
        runner=FakeRunner(),
        env={
            "PLATFORM_OWNER_KEY": "ssh-ed25519 AAAA owner",
            "PLATFORM_CI_DEPLOY_KEY": "ssh-ed25519 AAAA deploy",
        },
        users_helpers=hu,
    )

    assert result is True, "semi-optional: отсутствие ключа не роняет φ2"
    root_calls = [c for c in hu.add_ssh_key.call_args_list if c.args[0] == "root"]
    assert not root_calls, "root-ключ не добавляется при отсутствии env"
    assert "PLATFORM_CI_ROOT_KEY not set" in caplog.text, "WARN обязан быть"
    logger.critical("[IMP:9][test] φ2: без CI-root ключа → WARN + True ✓ (semi-optional)")


# endregion FUNC_test_phase_user_accounts_ci_root_key_missing_warns


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
def test_phase_platform_setup_success(tmp_path, caplog) -> None:
    """Успешный φ3: sudoers setup + cron OK → True, IMP:9 complete."""
    core_dir = _make_core_dir(tmp_path, scripts=("setup-node.sh",))
    hs = FakeSystemHelpers()
    hv = mock.Mock()  # validate_sudoers fake (W-H DI)

    # W5 T5.3: subprocess-канал через runner= DI (FakeRunner) вместо run_subprocess-патча
    result = sys_phases.phase_platform_setup(
        str(core_dir),
        node_name="test-node",
        node_yaml="",
        runner=FakeRunner(),
        sys_helpers=hs,
        val_helpers=hv,
    )

    assert result is True, "Успешный φ3 → True"
    complete = [r.message for r in caplog.records if "φ3 complete" in r.message]
    assert complete, "Ожидался IMP:9 «φ3 complete»"
    logger.info("[IMP:9][test] φ3: platform_setup success → True, IMP:9 complete ✓")


# endregion FUNC_test_phase_platform_setup_success


# ═══════════════════════════════════════════════════════════════════════════
# DevPlan 162 W7-3: timezone из node.yaml (φ1 шаг 1.6)
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_phase_system_bootstrap_applies_timezone
## @purpose  W7-3: node.yaml с node.timezone → φ1 вызывает timedatectl set-timezone (tz).
##           Нода в UTC при Europe/Moscow (аудит 162) — timezone из конфига применяется.
# 🧪 TRAP[TEST] · phase_system_bootstrap_timezone_applied · Behavioral · Regression: timezone из node.yaml не применялся
# · Scenario: node.yaml с node.timezone=Europe/Moscow; helpers mocked OK → True;
# ·   run_subprocess вызван с ["timedatectl", "set-timezone", "Europe/Moscow"]; IMP:9 «Timezone set»
# · Last fail: N/A (новый тест 162 W7-3)
# · Remove if: применение timezone из node.yaml удаляется из φ1
@ldd_trajectory
def test_phase_system_bootstrap_applies_timezone(tmp_path, caplog) -> None:
    """φ1: timezone из node.yaml → timedatectl set-timezone вызывается."""
    caplog.set_level(logging.INFO)
    core_dir = _make_core_dir(tmp_path)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("node:\n  name: test-node\n  timezone: Europe/Moscow\n", encoding="utf-8")
    hs = FakeSystemHelpers()
    runner = FakeRunner()

    result = sys_phases.phase_system_bootstrap(
        str(core_dir),
        node_name="test-node",
        node_yaml=str(node_yaml),
        runner=runner,
        facts=FakeFacts(is_root=True),
        env={"TOR_ENABLED": "false"},
        helpers=hs,
    )

    assert result is True
    tz_calls = [c for c in runner.calls if c[:2] == ["timedatectl", "set-timezone"]]
    assert tz_calls, "timedatectl set-timezone обязан вызываться"
    assert tz_calls[0][2] == "Europe/Moscow", f"tz из node.yaml, got {tz_calls}"
    assert "Timezone set to Europe/Moscow" in caplog.text
    logger.info("[IMP:9][test] φ1: timezone Europe/Moscow applied via timedatectl ✓ (W7-3)")


# endregion FUNC_test_phase_system_bootstrap_applies_timezone


# region FUNC_test_phase_system_bootstrap_timezone_unset_skips
## @purpose  W7-3 negative: node.yaml без timezone (или отсутствует) → timedatectl НЕ вызывается,
##           системный default остаётся (INFO skip).
# 🧪 TRAP[TEST] · phase_system_bootstrap_timezone_unset · NEGATIVE (R5) · Regression: unset timezone ломает φ1
# · Scenario: node.yaml без node.timezone → True, 0 вызовов timedatectl set-timezone,
# ·   INFO «timezone not set» присутствует
# · Last fail: N/A (новый negative-тест 162 W7-3)
# · Remove if: skip-семантика unset timezone меняется
@ldd_trajectory
def test_phase_system_bootstrap_timezone_unset_skips(tmp_path, caplog) -> None:
    """φ1: timezone unset в node.yaml → skip (timedatectl не вызывается)."""
    caplog.set_level(logging.INFO)
    core_dir = _make_core_dir(tmp_path)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("node:\n  name: test-node\n", encoding="utf-8")
    hs = FakeSystemHelpers()
    runner = FakeRunner()

    result = sys_phases.phase_system_bootstrap(
        str(core_dir),
        node_name="test-node",
        node_yaml=str(node_yaml),
        runner=runner,
        facts=FakeFacts(is_root=True),
        env={"TOR_ENABLED": "false"},
        helpers=hs,
    )

    assert result is True
    tz_calls = [c for c in runner.calls if c[:2] == ["timedatectl", "set-timezone"]]
    assert not tz_calls, "timezone unset → timedatectl не вызывается"
    assert "timezone not set in node.yaml" in caplog.text
    logger.info("[IMP:9][test] φ1: timezone unset → skip (INFO) ✓ (W7-3)")


# endregion FUNC_test_phase_system_bootstrap_timezone_unset_skips


# ═══════════════════════════════════════════════════════════════════════════
# DevPlan 162 W7-2: converge rc-пропагация (φ8.5/φ13)
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_phase_converge_services_rc_mapping
## @purpose  W7-2: rc converge.sh маппится в bool — rc=0 → True (clean), rc=1 → False
##           (warnings), rc=2 → False (drift). exit 0 ≠ «чисто» (раньше rc глотался).
# 🧪 TRAP[TEST] · phase_converge_services_rc_mapping · Behavioral · Regression: converge rc 1/2 глотался
# · Scenario: run_subprocess возвращает CompletedProcess с rc 0/1/2 → True/False/False
# · Last fail: N/A (новый тест 162 W7-2)
# · Remove if: rc-пропагация converge меняется
@ldd_trajectory
@pytest.mark.parametrize("rc,expected", [(0, True), (1, False), (2, False), (3, False)])
def test_phase_converge_services_rc_mapping(tmp_path, caplog, rc, expected) -> None:
    """φ8.5: converge rc → bool (0=clean True, 1=warnings/2=drift/прочее → False)."""
    caplog.set_level(logging.INFO)
    core_dir = _make_core_dir(tmp_path, scripts=("converge.sh",))

    result = sys_phases.phase_converge_services(
        str(core_dir), node_name="test-node", node_yaml="", runner=FakeRunner(rc=rc)
    )

    assert result is expected, f"rc={rc} → {expected}, got {result}"
    if rc == 0:
        assert "Converge clean" in caplog.text
    elif rc == 1:
        assert "warnings (rc=1)" in caplog.text
    elif rc == 2:
        assert "Converge FAILED (rc=2" in caplog.text
    logger.info("[IMP:9][test] φ8.5: converge rc=%s → %s ✓ (W7-2)", rc, expected)


# endregion FUNC_test_phase_converge_services_rc_mapping


# region FUNC_test_phase_converge_update_rc_mapping
## @purpose  W7-2: тот же rc-mapping в φ13 (converge_update, UPDATE mode).
# 🧪 TRAP[TEST] · phase_converge_update_rc_mapping · Behavioral · Regression: converge_update rc 1/2 глотался
# · Scenario: run_subprocess rc 0/1/2 → True/False/False (тот же mapping, что φ8.5)
# · Last fail: N/A (новый тест 162 W7-2)
# · Remove if: rc-пропагация converge_update меняется
@ldd_trajectory
@pytest.mark.parametrize("rc,expected", [(0, True), (1, False), (2, False)])
def test_phase_converge_update_rc_mapping(tmp_path, caplog, rc, expected) -> None:
    """φ13: converge_update rc → bool (0=clean True, 1=warnings/2=drift → False)."""
    caplog.set_level(logging.INFO)
    core_dir = _make_core_dir(tmp_path, scripts=("converge.sh",))

    result = sys_phases.phase_converge_update(
        str(core_dir), node_name="test-node", node_yaml="", runner=FakeRunner(rc=rc)
    )

    assert result is expected, f"rc={rc} → {expected}, got {result}"
    logger.info("[IMP:9][test] φ13: converge_update rc=%s → %s ✓ (W7-2)", rc, expected)


# endregion FUNC_test_phase_converge_update_rc_mapping
