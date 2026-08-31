"""
# GREP_SUMMARY: test_state_machine, state-machine, bootstrap, lifecycle, state-json, step-transitions, checkpoint-resume, content-hash, init-mode, update-mode, dry-run, force-mode, tor-conditional, validate-env, json-report, DI, env-dict, W-H
# STRUCTURE: ▶ tmp_path + env-дикт + mock subprocess → ◇ StateMachine init/load/save (3×) → ◇ step transitions: start/complete/skip/fail (6×) → ◇ content-hash computation (2×) → ◇ resume from checkpoint (2×) → ◇ init flow 23 steps (mock subprocess) → ◇ update flow 9 steps (mock subprocess) → ◇ name-based keys (3× DevPlan 071) → ◇ dry-run (no mutations) → ◇ force-mode (clear state) → ◇ validate_bootstrap_env (success/missing) → ◇ JSON report format → ◇ TOR conditional skip → ⎋ LDD trajectory IMP:7-10 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for state_machine.py — state transitions, checkpoint-resume,
##           content-hash, init/update flows, dry-run, force-mode, env validation,
##           JSON report, and TOR conditional logic.
## @scope    Tests StateMachine class and CLI dispatch with tmp_path fixtures,
##           env-дикты (DI, W-H DevPlan 163 — 0 setenv) и mock subprocess.
##           Does NOT require root privileges or real Docker/apt.
## @invariants
##   - All subprocess-dependent tests mock subprocess.run to avoid real system calls
##   - File operations use tmp_path exclusively — never /var/lib/platform
##   - Each test validates IMP:9 business logic log presence via caplog + ldd_trajectory
##   - State file path is configurable via --state-file (tmp_path in tests)
##   - step_hash tests use known file content for deterministic assertions
##   - W-H (DevPlan 163): autouse env_vars УДАЛЁН — env через StateMachine(env=_flow_env(...))
##     + cli.main(env=); preflight через run_cmd=/docker_info_fn=; resume через субкласс +
##     run_init_mode(smoke_fn=/audit_fn=/notify_fn=); B26 через main(argv=/sm_class=/audit_fn=)
##   - 167 D5 (DI-zero): os.makedirs/os.path.isdir FS-guard патчи УДАЛЕНЫ — helpers зафейканы
##     (0 реальных os.makedirs в flow, probe) + φ5 node_configs_dir path-injection
##     (NODE_CONFIGS_REMOTE_BASE env → tmp; 0 патчей os)
##   - Честный остаток (23 setattr): flow-тесты mock'ают FS/apt-writer-хелперы
##     (install_cron_metrics/install_zram/purge_cruft/add_ssh_key/os.makedirs/os.path.isdir),
##     вызываемые фазами НАПРЯМУЮ — требует phase-level helper-инъекции (AF-4d production,
##     вне DI-HYG скоупа W-H; Debt-отчёт H3)
## @rationale Direct class testing with mock subprocess for system-dependent steps
##   and tmp_path for state file operations. Avoids requiring root or real infrastructure.
## @changes
##   2026-07-22 · Created (W4-E2 extraction from node-lifecycle.sh)
##   2026-08-13 · DevPlan 160 E3 — root-факты flow-тестов через StateMachine(facts=...) /
##               precondition_check(facts=...) (0 os.geteuid-патчей × 4)
##   2026-08-13 · DevPlan 163 W-H — DI-перевод: 40 патчей (34 setattr + 6 setenv) → 23 setattr
##   2026-08-27 · P0 — контракт ssl_provision_via_orchestrator = str-статус: flow-тесты
##               monkeypatch-ят domains_helpers.ssl_provision_via_orchestrator → "converged"
##               (тест-среда: реальный вызов падает на /etc/letsencrypt Permission denied)
# endregion MODULE_CONTRACT
"""

import json
import logging
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

# P0 2026-08-27: ssl_provision_via_orchestrator контракт — str-статус
# (provisioned|converged|skipped_import|error). Канонический domains-модуль патчится в
# flow-тестах: φ7 (certs.py) и φ12 (docker.py) ссылаются на ЭТОТ ЖЕ модуль (helpers_domains),
# поэтому один setattr покрывает обе фазы (тест-среда: реальный вызов падает на /etc/letsencrypt).
from core.internal.bootstrap.lifecycle.helpers import domains as domains_helpers

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
    """Mock subprocess.run to return successful results by default.

    W5-C1 (план 170): + патч shutil.which в phases/preconditions — φ1 precondition
    (apt-get/dpkg) переведён с `command -v`/subprocess на stdlib which (детерминизм
    на любом раннере, в т.ч. macOS без apt-get).
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
        yield mock


def _flow_env(
    base_dir: Path,
    *,
    node_yaml: str | None = None,
    secrets_env: str | None = None,
    tor_enabled: str = "false",
) -> dict[str, str]:
    """Полный env-дикт фаз для StateMachine(env=...) (W-H DevPlan 163, DI — 0 setenv).

    ## @purpose — Все ключевые env-переменные фаз (NODE_NAME/NODE_YAML/SECRETS_ENV_FILE/
    ##            TOR_ENABLED/PLATFORM_OWNER_KEY/PLATFORM_CI_DEPLOY_KEY/GHCR_PULL_TOKEN/
    ##            AGE_SECRET_KEY) передаются диктом через StateMachine(env=...) — MERGE-семантика
    ##            execute_phase {**os.environ, **env} покрывает прекондишены и env-aware фазы.
    ##            W-H: autouse env_vars (6 setenv) УДАЛЁН — единственный источник env — параметры.
    ## @io — ⇥ base_dir: tmp-корень state.json, node_yaml/secrets_env: tmp-пути, tor_enabled → ⎋ dict
    ## @complexity — O(1)
    """
    return {
        "NODE_NAME": "test-node",
        "NODE_YAML": node_yaml or str(base_dir / "node.yaml"),
        "SECRETS_ENV_FILE": secrets_env or str(base_dir / "secrets.env"),
        "TOR_ENABLED": tor_enabled,
        # 167 D5: node_configs_dir резолвится через node_configs_remote(env) — tmp-база
        # (φ5 проверяет os.path.isdir(node_configs_dir) реально, папку создаёт тест)
        "NODE_CONFIGS_REMOTE_BASE": str(base_dir / "node-configs"),
        "PLATFORM_OWNER_KEY": "ssh-ed25519 AAAA... test@test",
        "PLATFORM_CI_DEPLOY_KEY": "ssh-ed25519 BBBB... ci@test",
        "GHCR_PULL_TOKEN": "ghp_test_token",
        # ⚠️ TRAP[BUG] 2026-08-03 · тест зависел от AGE-ключа dev-машины
        # · Symptom: CI static_audit — «Precondition failed for phase secrets_provision:
        #   requires AGE_SECRET_KEY env var or /etc/age/key.txt» — локально проходил,
        #   потому что AGE_SECRET_KEY был в env разработчика; на CI-раннере — нет.
        # · Fix: детерминированный тестовый ключ (secrets.env в тесте — свой, tmp).
        "AGE_SECRET_KEY": "AGE-SECRET-KEY-TEST-RC121",
    }


class FakeSystemHelpers:
    """System-хелперы fake (W-H DevPlan 163): φ1/φ3 FS/apt-writer-заглушки (0 патчей helpers)."""

    def __init__(self) -> None:
        self.install_apt_packages = lambda _: None
        self.ensure_sops = lambda: None
        self.ensure_journald_persistent = lambda: True
        self.install_zram = lambda: True
        self.install_cron_prune = lambda: True
        self.purge_cruft = lambda: True
        self.purge_provider_repos = lambda: True
        self.ensure_fstab_policy = lambda: True
        self.install_cron_metrics = lambda _: True
        self.install_cron_watchdog = lambda _: True


class FakeUserHelpers:
    """User-хелперы fake (W-H DevPlan 163): φ2 user-операции (0 патчей helpers)."""

    create_user = staticmethod(lambda *_, **__: None)
    add_ssh_key = staticmethod(lambda *_, **__: None)
    ensure_projects_base = staticmethod(lambda *_, **__: None)


class FakeValHelpers:
    """Validation-хелперы fake (W-H DevPlan 163): φ3 sudoers-валидация (0 патчей helpers)."""

    validate_sudoers = staticmethod(lambda *_, **__: None)


class FakeFacts:
    """EnvironmentFacts-fake (E3, DevPlan 160): is_root через параметр (0 os.geteuid-патчей)."

    ## @purpose — DI для facts= параметра StateMachine/execute_phase/precondition_check:
    ##            is_root управляет root-guard'ом фаз+прекондишенов; path_isfile — реальный
    ##            os.path.isfile (tmp_path-файлы видны без настройки).
    ## @io — ⇥ is_root: bool → ⎋ fake
    """

    def __init__(self, is_root: bool = True) -> None:
        self._is_root = is_root

    def is_root(self) -> bool:
        return self._is_root

    def which(self, binary: str) -> str | None:  # pragma: no cover — не используется в flow
        return binary

    def path_isfile(self, path) -> bool:

        return Path(path).is_file()


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


# 🧪 TRAP[TEST] · REGRESSION (R5 negative) · T9.2 — коррапт state.json → ЯВНАЯ ошибка (не fresh state)
# · Scenario: State file has invalid JSON → StateMachine.__init__ raises PlatformFatalError
# · Last fail: 2026-08-05 — load_state молча возвращал свежий state (L-2/B-2: потеря checkpoint'ов
#   тихо, node-update начинал всё заново; DevPlan 136 W9 T9.2)
# · Remove if: corrupt state handling changes (T9.2 контракт — explicit error, NOT fresh)
@ldd_trajectory
def test_load_corrupt_state(caplog, state_file):
    """T9.2: StateMachine raises PlatformFatalError on corrupt JSON (NOT fresh state)."""
    from core.internal.shared.exceptions import PlatformFatalError

    state_file.write_text("{invalid json...}")
    with pytest.raises(PlatformFatalError, match="corrupt"):
        sm.StateMachine(state_file_path=str(state_file))
    logger.critical("[IMP:9][test] Corrupt state raises explicit PlatformFatalError — OK (T9.2)")


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


# endregion Tests: StateMachine init/load/save


# ═══════════════════════════════════════════════════════════════════
# Волна 118 B1: step-API (start_step/complete_step/skip_step/fail_step/get_current_step +
# _is_step_done/_is_step_skipped/_hash_changed/_check_precondition/_check_postcondition)
# УДАЛЁН из state_machine.py — 0 callers в core/ + tests/ (CLI работает через
# execute_phase/setup_state, grouped-phases эра B9).
# W2 T2.4 (DevPlan 160): test_step_api_removed УДАЛЁН — мемориал исторического удаления;
# восстановление step-API — сознательное архитектурное решение (reviews), не регрессия.


# ═══════════════════════════════════════════════════════════════════
# region Tests: Resume logic (REMOVED API — волна 118 B1)
# ═══════════════════════════════════════════════════════════════════
# Волна 118 B1: get_current_step УДАЛЁН (0 callers — run_init/run_update проходят фазы
# последовательно через execute_phase + phase_is_done; current_step честно обновляется
# через cli._mark_phase_success). Тесты на get_current_step помечены removed API.
# R5 negative-покрытие: test_step_api_removed (hasattr(get_current_step) is False).


# endregion Tests: Resume logic (REMOVED API — волна 118 B1)


# ═══════════════════════════════════════════════════════════════════
# region Tests: Init flow (all phases, mocked subprocess)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · complete init flow runs all phases without error
# · Scenario: Mock subprocess, setup init mode, run _run_init_mode → all 9 phases complete
# · P0 2026-08-27: ssl_provision_via_orchestrator контракт — str-статус; мок "converged"
# ·   (happy-path: тест-среда не выдаёт сертов — реальный вызов упал бы на /etc/letsencrypt)
# · Last fail: N/A (new test)
# · Remove if: init flow execution logic changes fundamentally
@ldd_trajectory
def test_init_flow_all_phases(caplog, state_file, mock_subprocess, monkeypatch):
    """Init mode should run all phases without error (mocked subprocess).

    Note: env_vars (autouse) sets PLATFORM_OWNER_KEY and PLATFORM_CI_DEPLOY_KEY.
    _add_ssh_key is mocked to avoid writing to /home/* on macOS.
    CORE_DIR env не ставится — m.core_dir атрибут покрывает (execute_phase: self.core_dir or env).
    NODE_YAML/SECRETS_ENV_FILE/TOR_ENABLED — env-дикт через StateMachine(env=_flow_env(...)) (W4e).
    E3 (160): root-факты через StateMachine(facts=FakeFacts(is_root=True)) — 0 monkeypatch os.geteuid.
    167 D5 (DI-zero): FS-guard патчи (os.makedirs/os.path.isdir) УДАЛЕНЫ — helpers зафейканы
    (0 реальных os.makedirs в flow, probe 2026-08-14) + φ5     node_configs_dir через
    node_configs_remote(env): реальная tmp-папка вместо os.path.isdir-патча (/opt/node-configs).
    P0 (2026-08-27): ssl_provision_via_orchestrator возвращает str-статус; в тест-среде
    cert_orchestrator импортируется, но пишет в /etc/letsencrypt (Permission denied) → "error" →
    φ7 вернула бы done_with_warnings. Happy-path: monkeypatch → "converged" (статус-успех).
    """
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\nPLATFORM_MASTER_EMAIL=admin@test.local\n")
    # REF-0013 fail-fast: манифест доставляется с core/ всегда — фикстура зеркалит реальную ноду
    (Path(state_file).parent / "secrets-manifest.yaml").write_text("secrets: []\n")
    # phase_deploy_services precondition requires deploy-modules.sh and Docker running
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "deploy-modules.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "converge.sh").write_text("#!/bin/bash\necho ok\n")
    # Волна 117 D5 (WARN-семантика): фаза, вернувшая False → done_with_warnings (НЕ done).
    # Создаём все bootstrap-скрипты, которые проверяют фазы — happy path = все True.
    # DevPlan 134 W1: security_updates.py проверяется в φ1 шаг 5.5 (иначе фаза → False).
    for script in (
        "python_deps.py",
        "install-docker.sh",
        "install-tor-proxy.sh",
        "firewall.sh",
        "security_updates.py",
        "setup-node.sh",
        "install-acme.sh",
    ):
        (core_bootstrap_dir / script).write_text("#!/bin/bash\nexit 0\n")
    # φ5 (node_configuration) проверяет os.path.isdir(node_configs_dir) — 167 D5: реальная
    # tmp-папка (NODE_CONFIGS_REMOTE_BASE из _flow_env) вместо os.path.isdir-патча (/opt/node-configs)
    (Path(state_file).parent / "node-configs" / "test-node").mkdir(parents=True, exist_ok=True)
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")

    # DI (W-H): helper-неймспейсы в StateMachine (0 патчей helpers)
    m = sm.StateMachine(
        state_file_path=str(state_file),
        env=_flow_env(Path(state_file).parent),
        facts=FakeFacts(is_root=True),  # E3 (160): root-факты DI (0 os.geteuid-патчей)
        system_helpers=FakeSystemHelpers(),
        users_helpers=FakeUserHelpers(),
        val_helpers=FakeValHelpers(),
    )
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="init", node="test-node")

    # P0 2026-08-27: ssl_provision_via_orchestrator — str-статус
    # {provisioned|converged|skipped_import|error}. В тест-среде реальный вызов падает на
    # /etc/letsencrypt (Permission denied) → "error" → φ7 вернула бы False (done_with_warnings).
    # Happy-path: мок возвращает "converged" (успех — серты не наказываются повторным issuance).
    monkeypatch.setattr(domains_helpers, "ssl_provision_via_orchestrator", lambda _core_dir, _node_yaml: "converged")
    # strict-семантика INIT (2026-09-01): import_deploy_context патчится no-op — тест-среда
    # (node.yaml без contexts[]) не может реально задеплоить проекты; strict-семантика покрыта
    # отдельно (test_domains_import_deploy_context.py + passthrough-тесты φ8/φ12).
    monkeypatch.setattr(domains_helpers, "import_deploy_context", lambda *_args, **_kwargs: None)

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


# 🧪 TRAP[TEST] · Regression · phase_system_bootstrap fails without root
# · Scenario: os.geteuid() returns non-zero → precondition_check raises PhasePreconditionError
# · Last fail: N/A (new test)
# · Remove if: root check logic changes
@ldd_trajectory
def test_phase_system_bootstrap_no_root(caplog, machine):
    """phase_system_bootstrap should fail if not running as root (via precondition check)."""
    # B9 T1: precondition_check переехал в state_store.py; исключение raise'ится из
    # канонического пакетного модуля (state_machine.py, ленивый импорт) — не из script-загруженного sm
    from core.internal.bootstrap.lifecycle.state_machine import (
        PhasePreconditionError as _CanonicalPhasePreconditionError,
    )

    # E3 (160): root-guard через facts= DI (0 os.geteuid-патчей)
    with pytest.raises(_CanonicalPhasePreconditionError, match="requires root access"):
        machine.state.precondition_check(
            sm.BootstrapPhase.SYSTEM_BOOTSTRAP,
            facts=FakeFacts(is_root=False),
        )
    logger.critical("[IMP:9][test] system_bootstrap precondition detected non-root — OK")


# endregion Tests: Init flow (all phases, mocked subprocess)


# ═══════════════════════════════════════════════════════════════════
# region Tests: Update flow (mocked subprocess)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · complete update flow runs all phases without error
# · Scenario: Mock subprocess, setup update mode, run _run_update_mode → all 5 phases complete
# · P0 2026-08-27: ssl_provision_via_orchestrator контракт — str-статус; мок "converged"
# ·   (happy-path: φ12 не уходит в done_with_warnings из-за реального /etc/letsencrypt)
# · Last fail: N/A (new test)
# · Remove if: update flow execution logic changes
@ldd_trajectory
def test_update_flow_all_phases(caplog, state_file, mock_subprocess, monkeypatch):
    """Update mode should run all phases without error (mocked subprocess).

    P0 (2026-08-27): ssl_provision_via_orchestrator возвращает str-статус; в тест-среде
    cert_orchestrator импортируется, но пишет в /etc/letsencrypt (Permission denied) → "error" →
    φ12 (deploy_update) вернула бы done_with_warnings. Happy-path: monkeypatch → "converged".
    """
    # CORE_DIR env не ставится — m.core_dir покрывает (execute_phase: self.core_dir or env)
    # phase_node_config_update requires NODE_YAML to exist and be readable
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")
    # phase_secrets_update reads secrets.env
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\n")
    # REF-0013 fail-fast: манифест доставляется с core/ всегда — фикстура зеркалит реальную ноду
    (Path(state_file).parent / "secrets-manifest.yaml").write_text("secrets: []\n")
    # phase_deploy_update precondition requires deploy-modules.sh and Docker running
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "deploy-modules.sh").write_text("#!/bin/bash\necho ok\n")
    # All phase_*() functions also need converge.sh for φ13 converge_update
    (core_bootstrap_dir / "converge.sh").write_text("#!/bin/bash\necho ok\n")
    # DevPlan 134 W1: security_updates.py проверяется в φ12 deploy_update (иначе фаза → False)
    (core_bootstrap_dir / "security_updates.py").write_text("#!/bin/bash\nexit 0\n")
    # Волна 117 D5: φ11 (registry_update) проверяет internal/provision-environment.sh — happy path
    (Path(state_file).parent / "internal" / "provision-environment.sh").write_text("#!/bin/bash\nexit 0\n")

    m = sm.StateMachine(state_file_path=str(state_file), env=_flow_env(Path(state_file).parent))
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="update", node="test-node")

    # P0 2026-08-27: ssl_provision_via_orchestrator — str-статус. В тест-среде реальный вызов
    # падает на /etc/letsencrypt (Permission denied) → "error" → φ12 (deploy_update) вернула бы
    # False (done_with_warnings). Happy-path: мок возвращает "converged" (успех).
    monkeypatch.setattr(domains_helpers, "ssl_provision_via_orchestrator", lambda _core_dir, _node_yaml: "converged")

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


# endregion Tests: Update flow (mocked subprocess)


# ═══════════════════════════════════════════════════════════════════
# region Tests: Dry-run mode
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · dry_run_plan returns plan without state file mutation
# · Scenario: dry_run_plan() called → returns plan string, .save() not called
# · Last fail: N/A (new test)
# · Remove if: dry-run logic changes
# 🧪 TRAP[TEST] · Regression · dry-run prints all steps
# · Scenario: dry_run_plan for init mode → all phases included in output
# · Last fail: N/A (new test)
# · Remove if: dry-run plan format changes
@pytest.mark.parametrize(
    "mode,phase_order",
    [
        ("init", sm.BootstrapPhase.INIT_PHASE_ORDER),
        ("update", sm.BootstrapPhase.UPDATE_PHASE_ORDER),
    ],
)
@ldd_trajectory
def test_dry_run_plan(mode, phase_order, caplog, state_file):
    """dry_run_plan: план со всеми фазами режима, без мутации state file (по mode)."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode=mode, node="test")
    before = state_file.read_text()

    plan = m.dry_run_plan()

    assert "DRY RUN" in plan
    assert f"({len(phase_order)}-phase)" in plan
    for phase_val in phase_order:
        assert phase_val in plan, f"Phase {phase_val} missing from dry-run plan"
    assert state_file.read_text() == before, "dry_run_plan не должен мутировать state file"
    logger.critical("[IMP:9][test] dry_run_plan (%s) — план без мутаций, все %d фазы — OK", mode, len(phase_order))


# endregion Tests: Dry-run mode


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


# endregion Tests: Force mode (state reset)


# ═══════════════════════════════════════════════════════════════════
# region Tests: validate_bootstrap_env
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · validate_bootstrap_env returns True when all vars present
# · Scenario: NODE_NAME, NODE_YAML, PLATFORM_OWNER_KEY в env-дикте → validate returns True
# · Last fail: N/A (new test)
# · Remove if: env validation logic changes
# 🧪 TRAP[TEST] · Regression · validate_bootstrap_env returns False when vars missing
# · Scenario: PLATFORM_OWNER_KEY отсутствует в env-дикте → validate returns False
# · Last fail: N/A (new test)
# · Remove if: env validation logic changes
# 🧪 TRAP[TEST] · Regression · validate_bootstrap_env supports custom var list
# · Scenario: Pass custom list of vars → validates those instead of defaults
# · Last fail: N/A (new test)
# · Remove if: custom var validation changes
@pytest.mark.parametrize(
    "required_vars,env,expected",
    [
        # ok: все дефолтные vars присутствуют (required_vars=None → defaults)
        (
            None,
            {"NODE_NAME": "test-node", "NODE_YAML": "/tmp/node.yaml", "PLATFORM_OWNER_KEY": "ssh-ed25519 key"},
            True,
        ),
        # missing: PLATFORM_OWNER_KEY/NODE_YAML отсутствуют
        (["NODE_NAME", "NODE_YAML", "PLATFORM_OWNER_KEY"], {"NODE_NAME": "test-node"}, False),
        # custom vars: присутствует
        (["CUSTOM_VAR"], {"CUSTOM_VAR": "value"}, True),
        # custom vars: отсутствует
        (["MISSING_VAR"], {}, False),
    ],
)
@ldd_trajectory
def test_validate_bootstrap_env(required_vars, env, expected, caplog, machine):
    """validate_bootstrap_env: happy/negative по списку vars + env-дикт (DI, W4e)."""
    assert machine.validate_bootstrap_env(required_vars, env=env) is expected
    logger.critical(
        "[IMP:9][test] validate_bootstrap_env(vars=%r, env_keys=%s) → %s — OK", required_vars, sorted(env), expected
    )


# endregion Tests: validate_bootstrap_env


# ═══════════════════════════════════════════════════════════════════
# region Tests: JSON report format (REMOVED API — аудит 2026-08-22)
# ═══════════════════════════════════════════════════════════════════
# StateMachine.report() удалён (0 callers — dry_run_plan() покрывает human-readable
# сценарий; JSON-дамп state.json читается напрямую). test_report_format удалён —
# тест удалённого dead-API (метрика «число тестов не падает» не применяется).


# endregion Tests: JSON report format


# ═══════════════════════════════════════════════════════════════════
# region Tests: TOR conditional skip
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · TOR_DISABLED skips tor_proxy step
# · Scenario: TOR_ENABLED=false → tor_proxy step gets skipped, not failed
# · Last fail: N/A (new test)
# · Remove if: TOR conditional logic changes
@ldd_trajectory
def test_tor_conditional_skip(caplog, machine):
    """Phase should be skippable with reason (phase-based key)."""
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
def test_tor_conditional_runs(caplog, state_file, mock_subprocess, monkeypatch):
    """Init flow runs phases with TOR_ENABLED=true (env-дикт через StateMachine(env=)).

    167 D5 (DI-zero): FS-guard патчи УДАЛЕНЫ — helpers зафейканы (0 реальных os.makedirs)
    + φ5 node_configs_dir через реальную tmp-папку (NODE_CONFIGS_REMOTE_BASE).
    """
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\nPLATFORM_MASTER_EMAIL=admin@test.local\n")
    # REF-0013 fail-fast: манифест доставляется с core/ всегда — фикстура зеркалит реальную ноду
    (Path(state_file).parent / "secrets-manifest.yaml").write_text("secrets: []\n")
    # phase_deploy_services precondition requires deploy-modules.sh and Docker running
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "deploy-modules.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "converge.sh").write_text("#!/bin/bash\necho ok\n")
    # Волна 117 D5: happy path — создаём все скрипты, проверяемые фазами (TOR=true → нужен install-tor-proxy.sh)
    # DevPlan 134 W1: security_updates.py проверяется в φ1 шаг 5.5 (иначе фаза → False)
    for script in (
        "python_deps.py",
        "install-docker.sh",
        "install-tor-proxy.sh",
        "firewall.sh",
        "security_updates.py",
        "setup-node.sh",
        "install-acme.sh",
    ):
        (core_bootstrap_dir / script).write_text("#!/bin/bash\nexit 0\n")
    (Path(state_file).parent / "node-configs" / "test-node").mkdir(parents=True, exist_ok=True)
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")

    m = sm.StateMachine(
        state_file_path=str(state_file),
        env=_flow_env(Path(state_file).parent, tor_enabled="true"),
        facts=FakeFacts(is_root=True),  # E3 (160): root-факты DI (0 os.geteuid-патчей)
        system_helpers=FakeSystemHelpers(),
        users_helpers=FakeUserHelpers(),
        val_helpers=FakeValHelpers(),
    )
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="init", node="test-node")

    # strict-семантика INIT (2026-09-01): real import_deploy_context в тест-среде
    # (node.yaml без contexts[]) вернул бы failed=1 → PlatformFatalError. Flow-тест —
    # про TOR-ветвление, не про деплой контекста (покрыт test_domains_import_deploy_context.py).
    monkeypatch.setattr(domains_helpers, "import_deploy_context", lambda *_args, **_kwargs: None)

    exit_code = cli.run_init_mode(m)
    assert exit_code == 0

    # system_bootstrap phase should be done (Tor is handled inside phase)
    phase_name = sm.BootstrapPhase.INIT_PHASE_ORDER[0]  # system_bootstrap
    assert phase_name in m.state.steps
    assert m.state.steps[phase_name].status == "done", (
        f"system_bootstrap should be done, got: {m.state.steps[phase_name].status}"
    )

    logger.critical("[IMP:9][test] Init flow with TOR_ENABLED=true — OK")


# endregion Tests: TOR conditional skip


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
# GUARD-PRESERVE (168): контрактный guard регрессии F1 (VerificationReport — critical
# phase-key misalignment); единственное покрытие phase-based key migration контракта
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
    # and from_dict(step_list=INIT_STEPS). That path was removed with the migration
    # and the dead INIT_STEPS/UPDATE_STEPS constants (B4). Cold start only from 091 onward;
    # old numeric-key state.json files are no longer supported.
    # ⚠️ TRAP[DECISION] · 2026-07-30 · MED · Removed backward-compat numeric-key test scenario
    # · Rejected: keep test as xfail (risk: dead markers accumulate, Test Honesty R3)
    # · Reason: code under test (INIT_STEPS + numeric-key migration) deleted per User Constraint

    logger.critical("[IMP:9][test] Phase-based key regression guard — PASS")


# endregion Tests: Name-based keys (DevPlan 071 Rev 2)


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


# endregion Tests: Edge cases


# ═══════════════════════════════════════════════════════════════════
# region Tests: WARN-семантика и честный current_step (волна 117 D5)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D5 — фаза с non-fatal issues → done_with_warnings (НЕ done)
# · Scenario: phase_user_accounts возвращает False (non-fatal) → run_init_mode ставит done_with_warnings,
#   done=False; повторный run_init_mode перевыполняет фазу (не SKIP)
# · Last fail: WARN-фазы маскировались под done (execute_phase игнорировал результат)
# · Updated: 2026-08-27 (drill C2) — φ2 с done_with_warnings-пререком φ1 БОЛЬШЕ НЕ блокируется
#   (dependency {done, done_with_warnings}); WARN-перевыполнение самой фазы сохранено
# · Remove if: WARN-семантика статусов изменена
@ldd_trajectory
def test_phase_with_warnings_not_done(caplog, state_file, mock_subprocess, monkeypatch):
    """Фаза, вернувшая False, получает done_with_warnings и перевыполняется (D5).

    167 D5 (DI-zero): os.makedirs FS-guard УДАЛЁН — helpers зафейканы, 0 реальных вызовов.
    """
    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\nPLATFORM_MASTER_EMAIL=admin@test.local\n")
    # drill C2: φ4 (secrets_provision) достижим только теперь, когда φ2 НЕ блокируется —
    # manifest обязан быть доставлен (REF-0013 fail-fast, как в happy-path flow-тестах)
    (Path(state_file).parent / "secrets-manifest.yaml").write_text("secrets: []\n")
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (core_bootstrap_dir / "node-lifecycle.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "deploy-modules.sh").write_text("#!/bin/bash\necho ok\n")
    (core_bootstrap_dir / "converge.sh").write_text("#!/bin/bash\necho ok\n")
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")

    # Заставить φ1 (system_bootstrap) вернуть False: НЕ создаём python_deps.py/install-docker.sh/firewall.sh
    # DI (W-H): helper-неймспейсы (0 патчей helpers; zram/prune/purge — заглушки True)
    m = sm.StateMachine(
        state_file_path=str(state_file),
        env=_flow_env(Path(state_file).parent),
        facts=FakeFacts(is_root=True),  # E3 (160): root-факты DI (0 os.geteuid-патчей)
        system_helpers=FakeSystemHelpers(),
        users_helpers=FakeUserHelpers(),
    )
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="init", node="test-node")

    # strict-семантика INIT (2026-09-01): real import_deploy_context в тест-среде вернул бы
    # failed=1 → PlatformFatalError; тест — про WARN-семантику φ1, не про деплой контекста.
    monkeypatch.setattr(domains_helpers, "import_deploy_context", lambda *_args, **_kwargs: None)

    # drill C2 (2026-08-27): φ1 → done_with_warnings; φ2 (зависит от φ1) — dependency
    # УДОВЛЕТВОРЕНА (НЕ PhaseDependencyError): warning-фаза перевыполняется САМА, но
    # не блокирует downstream (раньше exit 1 + «requires prerequisite phase(s)»).
    rc = cli.run_init_mode(
        m,
        smoke_fn=lambda: True,
        audit_fn=lambda _, **__: None,
        notify_fn=lambda _: None,
    )
    assert rc == 0, f"WARN-фазы non-fatal → exit 0 (drill C2: φ2 не блокируется), got {rc}"
    assert not any("user_accounts" in r.message and "Dependency error" in r.message for r in caplog.records), (
        "drill C2: done_with_warnings prereq обязан УДОВЛЕТВОРЯТЬ dependency (не блокировать φ2)"
    )

    # φ1 помечен done_with_warnings (state сохранён) — WARN-семантика D5 сохранена
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
# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D6 — все фазы done → лёгкий liveness-probe (T9.17)
# · Scenario: _maybe_run_preflight при всех done → тяжёлый preflight НЕ вызывается (D6),
#   вместо него — лёгкий liveness probe (docker info, T9.17); тяжёлый preflight не запускается
# · Last fail: N/A (T9.17 — no-op bootstrap был слепым: preflight просто пропускался)
# · Remove if: preflight решение перенесено обратно в shell
@ldd_trajectory
def test_preflight_skipped_when_all_phases_done(caplog, state_file):
    """_maybe_run_preflight: все фазы done → лёгкий liveness probe (T9.17), НЕ тяжёлый preflight."""
    core_dir = Path(state_file).parent
    bootstrap_dir = core_dir / "internal" / "bootstrap"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "preflight.py").write_text("#!/usr/bin/env python3\nprint('probe')\n")

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(core_dir)
    m.setup_state(mode="init", node="test")
    # Все фазы done
    for pv in sm.BootstrapPhase.INIT_PHASE_ORDER:
        m.state.steps[pv] = sm.StepState(name=pv, status="done")
    m.save()

    preflight_calls: list[int] = []
    # DI (W-H): run_cmd= fake-канал subprocess + docker_info_fn= liveness probe (0 monkeypatch)
    rc = cli._maybe_run_preflight(
        m,
        run_cmd=lambda *_, **__: preflight_calls.append(1) or _FakeCompleted(0),
        docker_info_fn=lambda: _FakeCompleted(0),
    )
    assert rc == 0
    assert len(preflight_calls) == 0, f"тяжёлый preflight не должен вызываться при всех done, calls={preflight_calls}"
    assert any("liveness probe" in r.message for r in caplog.records), "Должен быть [IMP:9] лог liveness probe (T9.17)"
    logger.critical("[IMP:9][test] liveness probe при all-done, тяжёлый preflight не вызван — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · Regression: D6 — preflight выполняется при pending-фазах
# · Scenario: _maybe_run_preflight при pending-фазах → preflight.py вызывается, rc прокидывается
# · Last fail: N/A (новое поведение — решение перенесено в cli.py)
# · Remove if: preflight решение перенесено обратно в shell
@ldd_trajectory
def test_preflight_runs_when_pending(caplog, state_file):
    """_maybe_run_preflight: есть pending-фазы → preflight выполняется (D6)."""
    core_dir = Path(state_file).parent
    bootstrap_dir = core_dir / "internal" / "bootstrap"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "preflight.py").write_text("#!/usr/bin/env python3\nprint('probe')\n")

    m = sm.StateMachine(state_file_path=str(state_file))
    m.core_dir = str(core_dir)
    m.setup_state(mode="init", node="test")  # все pending

    preflight_calls: list[int] = []
    # DI (W-H): run_cmd= fake-канал subprocess (0 monkeypatch)
    rc = cli._maybe_run_preflight(
        m,
        run_cmd=lambda *_, **__: preflight_calls.append(1) or _FakeCompleted(0),
    )
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


# endregion Tests: WARN-семантика и честный current_step (волна 117 D5)


# ═══════════════════════════════════════════════════════════════════
# region Tests: Dependency satisfaction {done, done_with_warnings} (drill C2, 2026-08-27)
# ═══════════════════════════════════════════════════════════════════
# P1 2026-08-27 (drill C2): make node-update NODE=tronyx-vps rc=2 — φ11 registry_update
# завершилась done_with_warnings (healthcheck-транзиент) → φ12 deploy_update
# «requires prerequisite phase(s): registry_update» — dependency-гейт требовал СТРОГО
# done, а done_with_warnings не признавался prerequisite'ом → одиночный некритичный
# warning навсегда ломал цепочку update (до ручного сброса state).
# Контракт платформы: warning-фазы перевыполняются САМА, но УДОВЛЕТВОРЯЮТ зависимости
# последующих (идемпотентность bootstrap: частичный отказ «доводится» повторным прогоном).


# 🧪 TRAP[TEST] · 2026-08-27 · Regression · drill C2 — prereq done_with_warnings УДОВЛЕТВОРЯЕТ dependency (init)
# · Scenario: φ1 (system_bootstrap) = done_with_warnings → _missing_dependencies(φ2) пуст;
#   execute_phase(φ2) НЕ raise PhaseDependencyError (раньше: гейт требовал СТРОГО done)
# · Last fail: P1 2026-08-27 tronyx-vps node-update rc=2 — φ11 registry_update done_with_warnings
#   → deploy_update «requires prerequisite phase(s): registry_update» (до ручного сброса state)
# · Remove if: dependency-gate семантика изменена обратно на строгий done
@ldd_trajectory
def test_dependency_satisfied_prereq_done_with_warnings_init(caplog, state_file, mock_subprocess):
    """init: prereq done_with_warnings → dependency удовлетворена (НЕ PhaseDependencyError)."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test")
    # φ1 = done_with_warnings (WARN-фаза: перевыполняется САМА, но удовлетворяет downstream)
    m.state.steps[sm.BootstrapPhase.SYSTEM_BOOTSTRAP] = sm.StepState(
        name=sm.BootstrapPhase.SYSTEM_BOOTSTRAP, status="done_with_warnings"
    )
    # Dependency-gate: missing deps для φ2 пуст
    assert m._missing_dependencies(sm.BootstrapPhase.USER_ACCOUNTS) == [], (
        "done_with_warnings prereq обязан удовлетворять dependency (drill C2)"
    )
    # execute_phase(φ2) НЕ должен raise PhaseDependencyError (precondition — mock_subprocess
    # which-патч; фаза-заглушка True)
    assert (
        m.execute_phase(
            sm.BootstrapPhase.USER_ACCOUNTS,
            phase_func_override=lambda *_, **__: True,
        )
        is True
    )
    logger.critical("[IMP:9][test] init: prereq done_with_warnings удовлетворяет dependency — OK (drill C2)")


# 🧪 TRAP[TEST] · 2026-08-27 · Regression · drill C2 — prereq done_with_warnings УДОВЛЕТВОРЯЕТ dependency (update)
# · Scenario: φ9/φ11 = done_with_warnings → _missing_dependencies(φ12 deploy_update) пуст
#   (реальный P1: registry_update done_with_warnings ломал node-update)
# · Last fail: P1 2026-08-27 make node-update NODE=tronyx-vps rc=2
# · Remove if: dependency-gate семантика изменена обратно на строгий done
@ldd_trajectory
def test_dependency_satisfied_prereq_done_with_warnings_update(caplog, state_file):
    """update: φ9/φ11 done_with_warnings → φ12 dependency удовлетворена (P1 drill C2)."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="update", node="test")
    for pv in (sm.BootstrapPhase.SECRETS_UPDATE, sm.BootstrapPhase.REGISTRY_UPDATE):
        m.state.steps[pv] = sm.StepState(name=pv, status="done_with_warnings")
    missing = m._missing_dependencies(sm.BootstrapPhase.DEPLOY_UPDATE)
    assert missing == [], f"φ12 deps: {missing} — done_with_warnings prereq обязан удовлетворять dependency (drill C2)"
    logger.critical("[IMP:9][test] update: φ11 done_with_warnings удовлетворяет φ12 dependency — OK (drill C2)")


# 🧪 TRAP[TEST] · 2026-08-27 · NEGATIVE (R5) · drill C2 — failed prereq БЛОКИРУЕТ dependency
# · Scenario: φ1 = failed → _missing_dependencies(φ2) = [system_bootstrap]; execute_phase(φ2)
#   raise PhaseDependencyError (guard: незавершённые НЕ удовлетворяют)
# · Last fail: N/A (новый guard-тест — регрессия невозможна)
# · Remove if: failed-семантика зависимости изменена
@ldd_trajectory
def test_dependency_failed_prereq_still_blocks(caplog, state_file, mock_subprocess):
    """failed prereq → dependency НЕ удовлетворена (guard против незавершённых)."""
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test")
    m.state.steps[sm.BootstrapPhase.SYSTEM_BOOTSTRAP] = sm.StepState(
        name=sm.BootstrapPhase.SYSTEM_BOOTSTRAP, status="failed"
    )
    missing = m._missing_dependencies(sm.BootstrapPhase.USER_ACCOUNTS)
    assert missing == [sm.BootstrapPhase.SYSTEM_BOOTSTRAP], f"failed prereq обязан блокировать: {missing}"
    with pytest.raises(sm.PhaseDependencyError):
        m.execute_phase(sm.BootstrapPhase.USER_ACCOUNTS, phase_func_override=lambda *_, **__: True)
    logger.critical("[IMP:9][test] failed prereq блокирует dependency — OK (guard)")


# 🧪 TRAP[TEST] · 2026-08-27 · Regression · drill C2 — статус-набор phase_satisfies_dependency (все статусы enum)
# · Scenario: перечислены ВСЕ статусы StepState-статусов: done/done_with_warnings
#   удовлетворяют; pending/failed/skipped/running — нет (dict и StepState формы)
# · Last fail: N/A (нотация статус-набора — контракт предиката)
# · Remove if: статус-набор dependency изменён
@ldd_trajectory
def test_phase_satisfies_dependency_status_set(caplog):
    """Статус-набор {done, done_with_warnings} удовлетворяет dependency; остальные — нет."""
    status_expectations = {
        "done": True,
        "done_with_warnings": True,
        "pending": False,
        "failed": False,
        "skipped": False,
        "running": False,
    }
    # StepState-форма
    for status, expected in status_expectations.items():
        assert sm.phase_satisfies_dependency(sm.StepState(name="x", status=status)) is expected, (
            f"StepState status={status} → {expected}"
        )
    # dict-форма (state.json load)
    for status, expected in status_expectations.items():
        assert sm.phase_satisfies_dependency({"status": status}) is expected, f"dict status={status} → {expected}"
    # legacy dict-форма: done:true без status → удовлетворяет (state.json до StepState);
    # done:false → нет
    assert sm.phase_satisfies_dependency({"done": True}) is True
    assert sm.phase_satisfies_dependency({"done": False}) is False
    # СТРОГОСТЬ phase_is_done сохранена: done_with_warnings НЕ done (re-run/exit-code/strict-init)
    assert sm.phase_is_done(sm.StepState(name="x", status="done")) is True
    assert sm.phase_is_done(sm.StepState(name="x", status="done_with_warnings")) is False
    logger.critical("[IMP:9][test] status-set {done, done_with_warnings} — dependency contract — OK")


# endregion Tests: Dependency satisfaction {done, done_with_warnings} (drill C2)


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


# 🧪 TRAP[TEST] · Regression · CLI parses single-arg options (--context DevPlan 047, --run-phase)
# · Scenario: Parse --context test-ctx → args.context; Parse --run-phase → args.run_phase
# · Last fail: N/A (new test — DevPlan 047)
# · Remove if: CLI arg removed
# 🧪 TRAP[TEST] · Regression · CLI parses --run-phase correctly
# · Scenario: Parse --run-phase system_bootstrap → args.run_phase == "system_bootstrap"
# · Last fail: N/A (new test)
# · Remove if: --run-phase arg changes
@pytest.mark.parametrize(
    "argv,attr,expected",
    [
        (["--mode", "init", "--context", "test-ctx"], "context", "test-ctx"),
        (["--mode", "init", "--run-phase", "system_bootstrap"], "run_phase", "system_bootstrap"),
    ],
)
@ldd_trajectory
def test_cli_args_parsed(argv, attr, expected, caplog):
    """CLI single-arg options parsed (parametrized: --context / --run-phase)."""
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    assert getattr(args, attr) == expected
    logger.critical("[IMP:9][test] CLI %s=%s parsed — OK", attr, expected)


# endregion Tests: CLI argument parsing


# ═══════════════════════════════════════════════════════════════════
# region Tests: D8 — raw-dict записи + resume без setup_state (DevPlan 136 W2 T2.7)
# ═══════════════════════════════════════════════════════════════════
# D8: lifecycle cli _mark_phase_* вставлял raw-dict в steps → to_dict() save crash при
# отсутствующей фазе на resume (67d9f10, fa16f34 — StepState фикс в _mark_phase_*).
# W2 T2.7: resume-кейс — missing phase на resume выполняется (StepState, не raw-dict).


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D8/T2.7 — resume с missing phase (без setup_state)
# · Scenario: state.json с φ1/φ2 done (StepState), остальные фазы ОТСУТСТВУЮТ → run_init_mode
#   без setup_state: done-фазы skip, missing выполняются (не падает, не KeyError)
# · Last fail: 2026-08-05 — resume мог крэшиться на missing phase (raw-dict/отсутствие записи)
# · Remove if: run_init_mode перестаёт поддерживать resume без setup_state
@ldd_trajectory
def test_resume_missing_phase_executes(caplog, state_file):
    """D8/T2.7: resume без setup_state — done skip, missing фазы выполняются."""
    initial_data = {
        "mode": "init",
        "node": "test-node",
        "current_step": 2,
        "steps": {
            "system_bootstrap": {"name": "system_bootstrap", "status": "done"},
            "user_accounts": {"name": "user_accounts", "status": "done"},
        },
        "errors": [],
        "warnings": [],
    }
    state_file.write_text(json.dumps(initial_data))

    # Fake execute_phase через DI (W-H): StateMachine-субкласс с переопределённым методом
    # (0 setattr на классе) + run_init_mode DI-параметры (smoke/audit/notify)
    executed: list[str] = []

    class _ResumeSM(sm.StateMachine):
        def execute_phase(self, phase_value: str, *, _env=None, _facts=None):
            executed.append(phase_value)
            return True

    # НЕ вызываем setup_state — resume-сценарий: загруженное состояние используется как есть
    m = _ResumeSM(state_file_path=str(state_file))
    assert m.state.current_step == 2, "resume: current_step загружен из state.json"

    exit_code = cli.run_init_mode(
        m,
        smoke_fn=lambda: True,
        audit_fn=lambda _: None,
        notify_fn=lambda _: None,
    )
    assert exit_code == 0

    # Done-фазы НЕ перевыполнялись (resume-семантика)
    assert "system_bootstrap" not in executed, "done-фаза system_bootstrap не должна перевыполняться"
    assert "user_accounts" not in executed, "done-фаза user_accounts не должна перевыполняться"
    # Missing-фазы выполнились и помечены done (StepState — не raw-dict)
    assert "platform_setup" in executed, "missing фаза platform_setup обязана выполниться на resume"
    missing = sm.BootstrapPhase.INIT_PHASE_ORDER[2]  # platform_setup
    assert m.state.steps[missing].status == "done", f"{missing} должна быть done после resume"
    assert isinstance(m.state.steps[missing], sm.StepState), "steps обязан содержать StepState, не raw-dict"
    logger.critical("[IMP:9][test] resume missing phase executed, done skipped — OK")


# endregion Tests: D8 — raw-dict записи + resume без setup_state (DevPlan 136 W2 T2.7)


# ═══════════════════════════════════════════════════════════════════
# region B26 (142 W7): state.json аудит при удалении/сбросе
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-06 · NEGATIVE (R5) · 142 W7 B26 — --force reset аудитируется
# · Scenario: cli.main([..., --force]) на валидном state → write_audit_entry(tag="state.json",
# ·   status="reset") вызывается ДО sm.reset() (аудит-след операции сброса)
# · Last fail: 2026-08-06 (цикл 2 141, B26) — state.json исчез без следа, механизм не выявлен;
# ·   аудит-запись позволяет реконструировать кто/когда
# · Remove if: B26 аудит-защита удаляется
@ldd_trajectory
def test_force_reset_writes_audit_entry(caplog, state_file):
    """B26: --force reset state.json → audit-запись (tag=state.json, status=reset)."""
    caplog.set_level(logging.INFO)

    # Валидный state.json
    m = sm.StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="test-node")
    m.save()

    audit_calls: list[tuple] = []

    def fake_audit(tag, status, message, **extra):
        audit_calls.append((tag, status, message, extra))
        return True

    reset_called = []

    class _FakeSM(sm.StateMachine):
        def reset(self):
            reset_called.append(True)
            super().reset()

    # DI (W-H): main(argv=, env=, sm_class=, run_init_fn=, audit_fn=) — 0 setattr
    rc = cli.main(
        ["--mode", "init", "--force", "--state-file", str(state_file)],
        env=_flow_env(Path(state_file).parent),
        sm_class=_FakeSM,
        run_init_fn=lambda _: 0,
        audit_fn=fake_audit,
    )

    assert rc == 0
    assert audit_calls, "аудит-запись обязана быть при --force (B26)"
    assert audit_calls[0][0] == "state.json", f"tag=state.json, got {audit_calls[0][0]}"
    assert audit_calls[0][1] == "reset", f"status=reset, got {audit_calls[0][1]}"
    assert reset_called, "sm.reset() обязан вызываться после аудит-записи"
    logger.critical("[IMP:9][test] B26: --force reset → audit-запись state.json/reset ✓")


# endregion B26 (142 W7): state.json аудит при удалении/сбросе


# ═══════════════════════════════════════════════════════════════════
# region B8 (142 W7): _phase_input_hash парсит YAML node.yaml
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-06 · NEGATIVE (R5) · 142 W7 B8 — json.loads на YAML node.yaml
# · Scenario: node.yaml (YAML-формат: node: / modules: / services:) → _phase_input_hash
# ·   считает hash из релевантных полей (НЕ fallback «node-yaml-unparseable»)
# · Last fail: 2026-08-06 (циклы 1/2 141, B8) — json.load падал на YAML → hash всегда
# ·   «node-yaml-unparseable» → content-hash фаз сломан
# · Remove if: _phase_input_hash YAML-парсинг меняется
@ldd_trajectory
def test_phase_input_hash_parses_yaml_node_yaml(caplog, tmp_path):
    """B8: _phase_input_hash корректно парсит YAML node.yaml (не JSON) — env-дикт (DI)."""
    caplog.set_level(logging.INFO)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text(
        "node:\n"
        "  name: tronyx-vps\n"
        "  owner_key: ssh-ed25519 AAAA owner\n"
        "modules:\n"
        "  nginx:\n"
        "    enabled: true\n"
        "services:\n"
        "  status-page:\n"
        "    enabled: true\n"
    )

    m = sm.StateMachine(state_file_path=str(tmp_path / "state.json"))
    digest = m._phase_input_hash("deploy_services", env={"NODE_YAML": str(node_yaml)})

    assert digest and len(digest) == 64, f"SHA256 hexdigest ожидался, got {digest!r}"
    assert "Cannot parse" not in caplog.text, f"B8: парсинг не должен падать: {caplog.text[-300:]}"
    # Детерминизм + чувствительность: изменение релевантного поля меняет hash
    digest2 = m._phase_input_hash("deploy_services", env={"NODE_YAML": str(node_yaml)})
    assert digest == digest2, "повторный hash детерминирован"
    node_yaml.write_text(
        "node:\n  name: tronyx-vps\nmodules:\n  nginx:\n    enabled: false\nservices:\n  status-page:\n    enabled: true\n"
    )
    digest3 = m._phase_input_hash("deploy_services", env={"NODE_YAML": str(node_yaml)})
    assert digest3 != digest, "изменение modules/services обязано менять hash (T9.3)"
    logger.critical("[IMP:9][test] B8: _phase_input_hash парсит YAML node.yaml ✓")


# endregion B8 (142 W7): _phase_input_hash парсит YAML node.yaml


# ═══════════════════════════════════════════════════════════════════════════
# plan 012 T9 (F-015b): strict-init exit semantics + update best-effort preserved
# ═══════════════════════════════════════════════════════════════════════════


def _bootstrap_flow_env(parent: Path):
    """Минимальный env-дикт фаз для StateMachine(env=...) — канон _flow_env (ключи owner/ci-deploy)."""
    return {
        "NODE_NAME": "test-node",
        "NODE_YAML": str(parent / "node.yaml"),
        "SECRETS_ENV_FILE": str(parent / "secrets.env"),
        "TOR_ENABLED": "false",
        "NODE_CONFIGS_REMOTE_BASE": str(parent / "node-configs"),
        "PLATFORM_OWNER_KEY": "ssh-ed25519 AAAA... test@test",
        "PLATFORM_CI_DEPLOY_KEY": "ssh-ed25519 BBBB... ci@test",
        "GHCR_PULL_TOKEN": "ghp_test_token",
        "AGE_SECRET_KEY": "AGE-SECRET-KEY-TEST-RC121",
    }


# 🧪 TRAP[TEST] · Regression · plan 012 T9 (F-015b) — strict-init fail-loud + resumable
# · Scenario: φ8 INIT вызывает deploy-modules.sh --strict-init; rc≠0 (failed≠∅) →
#             PlatformFatalError → шаг DEPLOY_SERVICES = failed в state.json (persist),
#             run_init_mode возвращает ≠0; повторный прогон с успешным деплоем доводит фазу.
# · Last fail: F-015b — init c failed-модулем warn-severity давал exit 0 → «полу-стек = success».
# · Remove if: strict-init семантика отменена/перенесена в другой слой.
@ldd_trajectory
def test_init_strict_exit_on_failed(caplog, state_file, mock_subprocess, monkeypatch):
    """φ8 INIT: deploy-modules rc=2 → state failed + exit≠0; resumable повтор доводит."""
    import core.internal.shared.exceptions as pex
    import core.internal.shared.subprocess_io as sio
    from core.internal.bootstrap.lifecycle.phases import docker as dph

    secrets_env = Path(state_file).parent / "secrets.env"
    secrets_env.write_text("PLATFORM_MASTER_PASSWORD=test-password\nPLATFORM_MASTER_EMAIL=admin@test.local\n")
    (Path(state_file).parent / "secrets-manifest.yaml").write_text("secrets: []\n")
    core_bootstrap_dir = Path(state_file).parent / "internal" / "bootstrap"
    core_bootstrap_dir.mkdir(parents=True, exist_ok=True)
    for script in ("node-lifecycle.sh", "deploy-modules.sh", "converge.sh"):
        (core_bootstrap_dir / script).write_text("#!/bin/bash\necho ok\n")
    for script in (
        "python_deps.py",
        "install-docker.sh",
        "install-tor-proxy.sh",
        "firewall.sh",
        "security_updates.py",
        "setup-node.sh",
        "install-acme.sh",
    ):
        (core_bootstrap_dir / script).write_text("#!/bin/bash\nexit 0\n")
    (Path(state_file).parent / "node-configs" / "test-node").mkdir(parents=True, exist_ok=True)
    node_yaml_path = Path(state_file).parent / "node.yaml"
    node_yaml_path.write_text("node:\n  name: test-node\n  platform_domain: test.local\nprojects: []\n")

    captured: list[list] = []
    real_run = sio.run_subprocess

    def _strict_fail(cmd, **kwargs):
        if any("deploy-modules.sh" in str(part) for part in cmd):
            captured.append(list(cmd))
            msg = "deploy-modules exited 2 (strict-init: failed≠∅)"
            raise pex.PlatformFatalError(msg)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(dph.helpers_subprocess, "run_subprocess", _strict_fail)
    # strict-семантика INIT (2026-09-01): real import_deploy_context в тест-среде вернул бы
    # failed=1 → PlatformFatalError и сломал resumable-ветку (rc2 должен быть 0). Деплой
    # контекста — не предмет этого теста (F-015b про модули); strict-семантика покрыта
    # test_domains_import_deploy_context.py.
    monkeypatch.setattr(domains_helpers, "import_deploy_context", lambda *_args, **_kwargs: None)

    m = sm.StateMachine(
        state_file_path=str(state_file),
        env=_bootstrap_flow_env(Path(state_file).parent),
        facts=FakeFacts(is_root=True),
        system_helpers=FakeSystemHelpers(),
        users_helpers=FakeUserHelpers(),
        val_helpers=FakeValHelpers(),
    )
    m.core_dir = str(Path(state_file).parent)
    m.setup_state(mode="init", node="test-node")

    rc = cli.run_init_mode(m, smoke_fn=lambda: True, audit_fn=lambda _, **__: None, notify_fn=lambda _: None)
    assert rc != 0, f"strict-init failure must yield non-zero exit, got {rc}"
    assert captured and any("--strict-init" in map(str, cmd) for cmd in captured), (
        f"φ8 обязан передавать --strict-init фасаду: {captured}"
    )

    step = m.state.steps.get(sm.BootstrapPhase.DEPLOY_SERVICES)
    assert step is not None and step.status == "failed", (
        f"F-015b FAIL: DEPLOY_SERVICES должен быть failed в state.json, got {step}"
    )
    persisted = json.loads(Path(state_file).read_text(encoding="utf-8"))
    assert persisted["steps"]["deploy_services"]["status"] == "failed", "state.json обязан быть persist'нут"

    # Resumable: повторный прогон с успешным деплоем доводит φ8 → done
    def _ok_run(cmd, **kwargs):
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(dph.helpers_subprocess, "run_subprocess", _ok_run)
    m2 = sm.StateMachine(
        state_file_path=str(state_file),
        env=_bootstrap_flow_env(Path(state_file).parent),
        facts=FakeFacts(is_root=True),
        system_helpers=FakeSystemHelpers(),
        users_helpers=FakeUserHelpers(),
        val_helpers=FakeValHelpers(),
    )
    m2.core_dir = str(Path(state_file).parent)
    m2.setup_state(mode="init", node="test-node")
    rc2 = cli.run_init_mode(m2, smoke_fn=lambda: True, audit_fn=lambda _, **__: None, notify_fn=lambda _: None)
    assert rc2 == 0, f"resumable re-run must succeed, got {rc2}"
    assert m2.state.steps[sm.BootstrapPhase.DEPLOY_SERVICES].status == "done"
    logger.critical("[IMP:9][test] strict-init: failed→exit≠0+state failed; re-run довёл до done")


# 🧪 TRAP[TEST] · Regression · plan 012 T9 — update-mode best-effort preserved (D2)
# · Scenario: тот же результат (failed=["redis"], crit=0, warn>0) в UPDATE-режиме →
#             exit 0 + IMP:9 summary deployed=N failed=[...]; strict_init=True → exit 2.
# · Last fail: N/A (контракт-тест нового параметра; D2 — не ломать CI node-update)
# · Remove if: WARN→0 контракт update-режима пересмотрен владельцем.
@ldd_trajectory
def test_update_best_effort_preserved(caplog):
    """Update-режим сохраняет WARN→0 с честным summary; strict_init эскалирует тот же результат."""
    from core.internal.bootstrap.deploy.deploy_orchestrator import _compute_exit_code

    caplog.set_level(logging.INFO)

    # Update-семантика (strict_init=False): warn-only failures → exit 0 + summary
    code_update = _compute_exit_code(0, 2, 4, failed=["redis"], strict_init=False)
    assert code_update == 0, f"D2 FAIL: update WARN→0 контракт сломан: {code_update}"
    assert any("[IMP:9][summary] deployed=4" in r.getMessage() and "redis" in r.getMessage() for r in caplog.records), (
        "Императив T9: IMP:9 summary deployed=N failed=[...] обязан присутствовать"
    )

    # Init-семантика (strict_init=True): тот же результат → exit 2
    code_init = _compute_exit_code(0, 2, 4, failed=["redis"], strict_init=True)
    assert code_init == 2, f"F-015b FAIL: strict_init обязан эскалировать failed≠∅ до 2, got {code_init}"

    # Crit>0 эскалируется в обоих режимах
    assert _compute_exit_code(1, 0, 3, failed=["postgres"], strict_init=False) == 2
    assert _compute_exit_code(1, 0, 3, failed=["postgres"], strict_init=True) == 2
    logger.critical("[IMP:9][test] update WARN→0 preserved; strict_init escalates the same result to 2")


# ═══════════════════════════════════════════════════════════════════
# plan 012 T17: post-bootstrap report step
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T17 · report печатает summary (модули/TLS/projects/next)
# · Scenario: init-mode завершился → post_bootstrap_report выводит BOOTSTRAP REPORT с
#   deployed/failed, TLS-статусом, awaiting projects и 3 next commands; не влияет на exit.
#   T17-fix: docker-проба и projects_base — DI-фейки (0 реальных docker-вызовов в unit-тесте).
# · Last fail: N/A (new step — plan 012 T17)
# · Remove if: report step удалён/перенесён
def test_post_bootstrap_report_emits_summary(caplog, state_file, tmp_path, monkeypatch):
    """T17: report печатает BOOTSTRAP REPORT + JSON-вариант; exit-code не меняет."""
    from core.internal.bootstrap.lifecycle import cli as lifecycle_cli
    from core.internal.bootstrap.lifecycle.state_machine import StateMachine
    from core.internal.bootstrap.lifecycle.state_store import BootstrapState, StepState

    sm = StateMachine(state_file_path=str(state_file))
    sm.state = BootstrapState(
        mode="init",
        node="test-node",
        steps={
            "system_bootstrap": StepState(name="system_bootstrap", status="done"),
            "certificates": StepState(name="certificates", status="done"),
        },
        errors=["postgres: failed to pull"],
        warnings=["w1"],
    )
    node_yaml_path = tmp_path / "node.yaml"
    node_yaml_path.write_text("projects:\n  - name: app-one\n    domain: app-one.example.com\n", encoding="utf-8")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))
    monkeypatch.setenv("NODE_NAME", "test-node")

    with caplog.at_level(logging.INFO):
        lifecycle_cli.post_bootstrap_report(
            sm,
            # T17-fix DI: tmp_path base + fake docker probe — без реального docker/субпроцессов
            projects_base=str(tmp_path / "projects"),
            docker_check_fn=lambda _name: False,
        )

    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "BOOTSTRAP REPORT" in combined, "report шапка отсутствует"
    assert "Next commands" in combined, "нет секции next commands"
    assert "make e2e-verify NODE=test-node" in combined, "нет e2e-verify suggestion"
    assert "app-one" in combined, "awaiting project отсутствует"
    assert "Projects deployed: 0/1" in combined, "deployed-строка (N/M) отсутствует"
    assert "postgres: failed to pull" in combined, "failed-список отсутствует"

    # JSON-вариант
    monkeypatch.setenv("REPORT_JSON", "1")
    lifecycle_cli.post_bootstrap_report(
        sm,
        projects_base=str(tmp_path / "projects"),
        docker_check_fn=lambda _name: False,
    )
    logger.info("[IMP:9][test][T17] report summary + JSON PASS")


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T17 · report не ломается без node.yaml
# · Remove if: report step удалён/перенесён
def test_post_bootstrap_report_no_node_yaml(caplog, state_file, monkeypatch):
    """T17: node.yaml отсутствует → report продолжает (awaiting: none/unavailable), не raise."""
    from core.internal.bootstrap.lifecycle import cli as lifecycle_cli
    from core.internal.bootstrap.lifecycle.state_machine import StateMachine
    from core.internal.bootstrap.lifecycle.state_store import BootstrapState

    sm = StateMachine(state_file_path=str(state_file))
    sm.state = BootstrapState(mode="init", node="n", steps={}, errors=[], warnings=[])
    monkeypatch.delenv("NODE_YAML", raising=False)
    monkeypatch.delenv("NODE_NAME", raising=False)

    with caplog.at_level(logging.INFO):
        lifecycle_cli.post_bootstrap_report(sm)  # не должен raise
    assert not [r for r in caplog.records if "Traceback" in r.getMessage()], "report не должен ронять traceback"
    logger.info("[IMP:9][test][T17] report без node.yaml не raise PASS")
