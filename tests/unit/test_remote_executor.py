# GREP_SUMMARY: test remote_executor execute-update execute-converge execute-reconcile CLI argparse FakeCommandRunner FakeFacts exit-codes VPS-self-SSH sync-core DRY_RUN LDD DI
# STRUCTURE: ▶ fixtures(resolve/extract/sync mock + FakeCommandRunner + FakeFacts) → ◇ CLI help → ◇ update: no-host=2 / VPS=2 / dry-run=0 / sync-fail=1 / ssh=0 / timeout=124 / resolve-fail=1 → ◇ converge: no-sync-core → ◇ reconcile: --reconcile flag → ⎋ LDD IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for remote_executor.py — CLI parsing, exit codes (0/1/2/124),
##           DRY_RUN, VPS self-SSH detect, sync-core/no-sync-core, error propagation, LDD IMP:9.
## @scope    11 tests per DevPlan 101 §8 $TEST_SPEC. FakeCommandRunner/FakeFacts (E1 DI) +
##           FakeDeliverer (167 D3 DI-объект) — zero real SSH/rsync, zero monkeypatch.
##           tmp_path fixtures — zero hardcoded paths. caplog-based LDD trajectory.
## @invariants — Все subprocess-зависимые тесты передают FakeCommandRunner (runner=)
##              — VPS self-SSH detect через FakeFacts.path_isfile (facts=), НЕ патч VPS_NODE_LIFECYCLE
##              — resolve/extract/sync через FakeDeliverer (deliverer=, DevPlan 167 D3) — DI-объект
##                вместо setattr-патча на уровне модуля (доменные зависимости, НЕ subprocess/facts)
##              — Каждый success-путь верифицирует IMP:9 лог (anti-illusion, testing.md §LDD)
## @rationale DevPlan 160 E1: monkeypatch subprocess.run (12) + VPS_NODE_LIFECYCLE (4) →
##            production-DI (runner/facts параметры RemoteExecutor); setattr 23 → 7 (−70%)
## @rationale DevPlan 167 D3: 7 остаточных setattr (resolve_node_yaml/extract_node_host/
##            sync_core_to_vps) → FakeDeliverer DI-объект (deliverer-параметр RemoteExecutor);
##            setattr 7 → 0
## @changes  2026-08-13 | E1 (160) — DI-конвертация (FakeCommandRunner/FakeFacts)
## @changes  2026-08-13 | E3 (160) — 7 остаточных setattr (resolve_node_yaml/extract_node_host/
##            sync_core_to_vps) ВНЕ СКОПА: overlay_deliverer/node_resolver доменные функции
##            (3-path search node.yaml, rsync core-канал) — мок на уровне модуля = documented
##            contract (см. @invariants); инъекция потребовала бы конструкторного рефакторинга
##            RemoteExecutor (глубокий, вне «минимальные keyword-only параметры» E3)
## @changes  2026-08-14 | 167 D3 — остаточные 7 setattr → FakeDeliverer (deliverer= DI-объект);
##            @rationale E3 устарел (шов добавлен в 167 D3)
## @usecases pytest tests/unit/test_remote_executor.py -v
# endregion MODULE_CONTRACT

import logging
import subprocess
from unittest import mock

import pytest
from _conftest.ldd import _print_ldd_trajectory

from core.internal.bootstrap import remote_executor
from core.internal.bootstrap.overlay_deliverer import NodeYamlNotFoundError, SyncCoreError

pytestmark = pytest.mark.static_audit


# ═══════════════════════════════════════════════════════════════════
# Fake-реализации DI-протоколов (W4b/W4d канон, DevPlan 160)
# ═══════════════════════════════════════════════════════════════════


class FakeCommandRunner:
    """Scripted CommandRunner (DI-канон W4b): результат из последовательности или дефолт.

    ## @purpose — Замена monkeypatch subprocess.run в тестах remote_executor: каждый вызов
    ##            записывается (calls/kwargs), возвращается scripted CompletedProcess.
    ## @io — ⇥ results: list[CompletedProcess] (FIFO), default → ⎋ CompletedProcess
    ## @complexity — O(1) — pop из списка / дефолт
    ## @invariants
    ##   - results исчерпаны → default (стабильное поведение для многошаговых сценариев)
    ##   - run() НЕ raise (канон subprocess_io check=False — graceful)
    """

    def __init__(self, results=None, default=None):
        self._results = list(results) if results else []
        self.default = default if default is not None else subprocess.CompletedProcess([], 0, "", "")
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    @property
    def last_cmd(self) -> list[str] | None:
        return self.calls[-1] if self.calls else None

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):
        self.calls.append(list(cmd))
        self.kwargs.append({"timeout": timeout, "check": check, "non_fatal": non_fatal, "fatal_rc": fatal_rc})
        if self._results:
            return self._results.pop(0)
        return self.default


class FakeFacts:
    """Fake EnvironmentFacts (DI-канон W4b): scripted is_root/which/path_isfile.

    ## @purpose — Замена monkeypatch os.path.isfile (VPS self-SSH detect) в тестах remote_executor.
    ## @io — ⇥ path_isfile: callable(path)->bool → ⎋ реализация протокола
    ## @complexity — O(1)
    """

    def __init__(self, is_root=True, which=lambda _: None, path_isfile=lambda _: False):
        self._is_root = is_root
        self._which = which
        self._path_isfile = path_isfile

    def is_root(self) -> bool:
        return self._is_root

    def which(self, binary: str) -> str | None:
        return self._which(binary)

    def path_isfile(self, path) -> bool:
        return self._path_isfile(path)


class FakeDeliverer:
    """Fake overlay_deliverer-функции (DI-объект, DevPlan 167 D3) — 0 setattr.

    ## @purpose — Замена setattr-патча remote_executor.resolve_node_yaml/
    ##            extract_node_host/sync_core_to_vps): методы- Mock'и, тест управляет
    ##            return_value/side_effect напрямую. Производственный шов — deliverer=
    ##            параметр RemoteExecutor (167 D3).
    ## @io — ⇥ node_yaml_path: str (резолв-результат), host: str (extract-результат)
    ##      → ⎋ объект с resolve_node_yaml/extract_node_host/sync_core_to_vps (Mock)
    ## @complexity — O(1)
    """

    def __init__(self, node_yaml_path: str, host: str = "10.0.0.1"):
        self.resolve_node_yaml = mock.Mock(return_value=node_yaml_path)
        self.extract_node_host = mock.Mock(return_value=host)
        self.sync_core_to_vps = mock.Mock(return_value=True)


def _proc(rc: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Build a CompletedProcess with given rc/stdout/stderr (fake-раннер результат)."""
    return subprocess.CompletedProcess([], returncode=rc, stdout=stdout, stderr=stderr)


class _TimeoutRunner:
    """Fake-раннер, бросающий TimeoutExpired (имитация таймаута ssh exec, exit 124)."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls.append(list(cmd))
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _capture_imp_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Capture INFO-level logs (IMP:7-10) — basicConfig in module sets WARNING."""
    caplog.set_level(logging.INFO)
    yield


@pytest.fixture
def executor(tmp_path):
    """RemoteExecutor with FakeDeliverer (DI-объект) + FakeCommandRunner + FakeFacts (0 monkeypatch)."""
    deliverer = FakeDeliverer(node_yaml_path=str(tmp_path / "node.yaml"))
    inst = remote_executor.RemoteExecutor(
        dry_run=False,
        runner=FakeCommandRunner(default=_proc(0)),
        facts=FakeFacts(path_isfile=lambda _: False),  # VPS marker отсутствует (no self-SSH)
        deliverer=deliverer,
    )
    inst.sync_mock = deliverer.sync_core_to_vps  # type: ignore[attr-defined]  # test convenience handle
    inst.runner = inst._runner  # type: ignore[attr-defined]  # test handle для ассертов команд
    return inst


REMOTE_CMD_UPDATE = (
    "set -euo pipefail && export PLATFORM_ROOT=/opt/platform && "
    "bash /opt/platform/core/internal/bootstrap/node-lifecycle.sh "
    "--mode update --node-name test-node --node-yaml /opt/node-configs/test-node/node.yaml"
)
REMOTE_CMD_CONVERGE = (
    "set -euo pipefail && export PLATFORM_ROOT=/opt/platform && "
    "bash /opt/platform/core/internal/bootstrap/converge.sh --node test-node"
)
REMOTE_CMD_CHECK_SECURITY = (
    "set -euo pipefail && export PLATFORM_ROOT=/opt/platform && export PYTHONPATH=/opt/platform && "
    "python3 /opt/platform/core/internal/bootstrap/security_posture.py --node test-node"
)


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_cli_execute_update_help
# 🧪 TRAP[TEST] · Regression · CLI subcommand usage
# · Scenario: `python3 -m core.internal.bootstrap.remote_executor execute-update --help`
# ·            должен печатать usage и завершаться с exit 0 (argparse SystemExit)
# · Last fail: N/A (new CLI — DevPlan 101 AC1)
# · Remove if: CLI subcommand structure changes
def test_cli_execute_update_help(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc_info:
        remote_executor.cli(["execute-update", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out
    assert "--remote-cmd" in out
    assert "--passthrough-args" in out


# endregion FUNC_test_cli_execute_update_help

# ═══════════════════════════════════════════════════════════════════
# execute_update — exit codes
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_execute_update_no_host_returns_2
# 🧪 TRAP[TEST] · Regression · No SSH host → local fallback
# · Scenario: extract_node_host возвращает "" → execute_update должен вернуть 2 (local fallback)
# · Last fail: N/A (new module — mirrors shell _resolve_and_extract return 2)
# · Remove if: local-fallback exit code semantics change
def test_execute_update_no_host_returns_2(executor, caplog) -> None:
    executor._deliverer.extract_node_host.return_value = ""  # type: ignore[union-attr]  # DI fake handle
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 2
    assert any("[IMP:9][execute_update][resolve] No SSH host" in r.message for r in caplog.records)


# endregion FUNC_test_execute_update_no_host_returns_2


# region FUNC_test_execute_update_vps_self_ssh_returns_2
# 🧪 TRAP[TEST] · Regression · VPS self-SSH loop (TRAP[BUG] P0)
# · Scenario: facts.path_isfile(VPS_NODE_LIFECYCLE) = True (мы на VPS)
# ·            → execute_update возвращает 2 (skip SSH proxy)
# · Last fail: 2026-07-23 — node-update зацикливался, подключаясь к самому себе
# · Remove if: VPS self-SSH detect механизм изменён
def test_execute_update_vps_self_ssh_returns_2(executor, caplog) -> None:
    executor._facts = FakeFacts(path_isfile=lambda _: True)  # type: ignore[attr-defined]
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 2
    assert any("[IMP:9][execute_update][vps-detect] Local VPS detected" in r.message for r in caplog.records)
    executor.sync_mock.assert_not_called()  # sync-core ДОЛЖЕН быть пропущен при self-SSH
    assert executor.runner.calls == []  # ssh НЕ вызывается


# endregion FUNC_test_execute_update_vps_self_ssh_returns_2


# region FUNC_test_execute_update_dry_run_exits_0
# 🧪 TRAP[TEST] · Regression · DRY_RUN не выполняет ssh/rsync
# · Scenario: dry_run=True → sync-core вызывается с dry_run=True, ssh НЕ вызывается, exit 0
# · Last fail: N/A (new module — mirrors shell early-exit 0)
# · Remove if: DRY_RUN семантика меняется
def test_execute_update_dry_run_exits_0(executor, caplog) -> None:
    dry_deliverer = FakeDeliverer(node_yaml_path="")  # DI: собственный fake на dry-путь (167 D3)
    dry = remote_executor.RemoteExecutor(
        dry_run=True,
        runner=FakeCommandRunner(default=_proc(0)),
        facts=FakeFacts(path_isfile=lambda _: False),
        deliverer=dry_deliverer,
    )
    rc = dry.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    dry_deliverer.sync_core_to_vps.assert_called_once()
    assert dry_deliverer.sync_core_to_vps.call_args.kwargs.get("dry_run") is True
    assert dry._runner.calls == [], f"DRY_RUN не должен выполнять ssh: {dry._runner.calls}"
    assert any("[IMP:8][execute_update][dry-run] DRY-RUN: ssh" in r.message for r in caplog.records)


# endregion FUNC_test_execute_update_dry_run_exits_0


# region FUNC_test_execute_update_sync_core_fails_returns_1
# 🧪 TRAP[TEST] · Regression · sync-core failure propagation
# · Scenario: sync_core_to_vps бросает SyncCoreError → execute_update возвращает 1, IMP:10 лог
# · Last fail: 2026-07-24 — node-update не доставлял core/ на VPS (TRAP[BUG] P0 overlay_deliverer)
# · Remove if: sync-core error handling изменён
def test_execute_update_sync_core_fails_returns_1(executor, caplog) -> None:
    msg = "rsync core/ failed for 10.0.0.1 (exit=1): test error"
    executor._deliverer.sync_core_to_vps.side_effect = SyncCoreError(msg)  # type: ignore[union-attr]
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 1
    assert any("[IMP:10][execute_update][sync-core] FATAL" in r.message for r in caplog.records)


# endregion FUNC_test_execute_update_sync_core_fails_returns_1


# region FUNC_test_execute_update_ssh_exec_success_returns_0
# 🧪 TRAP[TEST] · Regression · SSH exec success path
# · Scenario: FakeCommandRunner → rc=0 → execute_update возвращает 0; sync-core вызван;
# ·            ssh-команда: ["ssh", *SSH_OPTS, "root@10.0.0.1", remote_cmd]
# · Last fail: N/A (new module — mirrors shell ssh_exec return 0)
# · Remove if: ssh_exec success semantics change
def test_execute_update_ssh_exec_success_returns_0(executor, caplog) -> None:
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    executor.sync_mock.assert_called_once()  # update ОБЯЗАН синхронизировать core
    cmd = executor.runner.calls[-1]
    assert cmd[0] == "ssh"
    assert "root@10.0.0.1" in cmd
    assert cmd[-1] == REMOTE_CMD_UPDATE  # remote_cmd пробрасывается без изменений


# endregion FUNC_test_execute_update_ssh_exec_success_returns_0


# region FUNC_test_execute_update_ssh_exec_timeout_returns_124
# 🧪 TRAP[TEST] · Regression · SSH timeout detection (mirror lib/ssh.sh exit=124)
# · Scenario: _TimeoutRunner бросает TimeoutExpired → execute_update возвращает 124, IMP:10 лог
# · Last fail: N/A (new module — mirrors ssh_exec exit=124 → TIMEOUT)
# · Remove if: timeout handling изменён (SSH_EXEC_TIMEOUT / 124 convention)
def test_execute_update_ssh_exec_timeout_returns_124(executor, caplog) -> None:
    executor._runner = _TimeoutRunner()  # type: ignore[attr-defined]
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 124
    assert any("[IMP:10][ssh_exec][timeout] TIMEOUT" in r.message for r in caplog.records)


# endregion FUNC_test_execute_update_ssh_exec_timeout_returns_124

# ═══════════════════════════════════════════════════════════════════
# execute_converge / execute_reconcile
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_execute_converge_no_sync_core
# 🧪 TRAP[TEST] · Regression · Converge не синхронизирует core
# · Scenario: execute_converge выполняет ssh, но НЕ вызывает sync_core_to_vps (в отличие от update)
# · Last fail: N/A (new module — mirrors shell execute_remote_converge без sync-core)
# · Remove if: converge sync-core семантика меняется
def test_execute_converge_no_sync_core(executor, caplog) -> None:
    rc = executor.execute_converge("test-node", REMOTE_CMD_CONVERGE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    executor.sync_mock.assert_not_called()  # ключевое отличие от execute_update
    assert len(executor.runner.calls) == 1


# endregion FUNC_test_execute_converge_no_sync_core


# region FUNC_test_execute_reconcile_adds_reconcile_flag
# 🧪 TRAP[TEST] · Regression · Reconcile пробрасывает --reconcile в remote_cmd
# · Scenario: remote_cmd с --reconcile (добавлен shell build_converge_ssh_cmd) → ssh получает его
# ·            без изменений; sync-core НЕ вызывается (≡ converge)
# · Last fail: N/A (new module — mirrors shell execute_remote_reconcile)
# · Remove if: reconcile флаг-механизм меняется
def test_execute_reconcile_adds_reconcile_flag(executor, caplog) -> None:
    remote_cmd = REMOTE_CMD_CONVERGE + " --reconcile"
    rc = executor.execute_reconcile("test-node", remote_cmd)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    cmd = executor.runner.calls[-1]
    assert "--reconcile" in cmd[-1]
    executor.sync_mock.assert_not_called()
    assert any("[IMP:9][execute_reconcile][input] --reconcile flag present" in r.message for r in caplog.records)


# endregion FUNC_test_execute_reconcile_adds_reconcile_flag

# ═══════════════════════════════════════════════════════════════════
# execute-check-security (DevPlan 134 W2)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_execute_check_security_no_sync_core
# 🧪 TRAP[TEST] · Regression · check-security не синхронизирует core
# · Scenario: execute_check_security выполняет ssh (security_posture.py), но НЕ вызывает
# ·            sync_core_to_vps (read-only диагностика — remote core уже доставлен, DevPlan 134 D3)
# · Last fail: N/A (new module — зеркало execute_converge)
# · Remove if: check-security sync-core семантика меняется
def test_execute_check_security_no_sync_core(executor, caplog) -> None:
    rc = executor.execute_check_security("test-node", REMOTE_CMD_CHECK_SECURITY)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    executor.sync_mock.assert_not_called()  # ключевое отличие от execute_update
    assert len(executor.runner.calls) == 1


# endregion FUNC_test_execute_check_security_no_sync_core


# region FUNC_test_execute_check_security_vps_self_ssh_returns_2
# 🧪 TRAP[TEST] · Regression · VPS self-SSH detect (RC 121)
# · Scenario: facts.path_isfile(VPS_NODE_LIFECYCLE) = True (мы на VPS) → return 2, ssh НЕ вызывается
# · Last fail: N/A (зеркало execute_converge TRAP[BUG] 2026-08-03 RC 121)
# · Remove if: VPS self-detect семантика меняется
def test_execute_check_security_vps_self_ssh_returns_2(executor, caplog) -> None:
    executor._facts = FakeFacts(path_isfile=lambda _: True)  # type: ignore[attr-defined]
    rc = executor.execute_check_security("test-node", REMOTE_CMD_CHECK_SECURITY)
    assert _print_ldd_trajectory(caplog)
    assert rc == 2
    assert executor.runner.calls == []
    assert any("[vps-detect]" in r.message for r in caplog.records)


# endregion FUNC_test_execute_check_security_vps_self_ssh_returns_2


# region FUNC_test_execute_check_security_dry_run_exits_0
# 🧪 TRAP[TEST] · Regression · DRY_RUN печатает команду без ssh
# · Scenario: dry_run=True → IMP:8 dry-run лог, exit 0, runner.run НЕ вызывается
# · Last fail: N/A (зеркало execute_converge)
# · Remove if: dry-run семантика меняется
def test_execute_check_security_dry_run_exits_0(executor, caplog) -> None:
    executor.dry_run = True
    rc = executor.execute_check_security("test-node", REMOTE_CMD_CHECK_SECURITY)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    assert executor.runner.calls == []
    assert any("[dry-run]" in r.message for r in caplog.records)


# endregion FUNC_test_execute_check_security_dry_run_exits_0


# ═══════════════════════════════════════════════════════════════════
# LDD / error propagation
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_ldd_imp9_logs_on_success
# 🧪 TRAP[TEST] · Anti-illusion · IMP:9 trajectory на success-пути
# · Scenario: успешный SSH exec → в caplog минимум один IMP:9 лог (бизнес-логика достигнута)
# · Last fail: N/A (LDD methodology — testing.md §LDD anti-illusion rule)
# · Remove if: LDD лог-стандарт изменён
def test_ldd_imp9_logs_on_success(executor, caplog) -> None:
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert rc == 0
    found_log = _print_ldd_trajectory(caplog)
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_ldd_imp9_logs_on_success


# region FUNC_test_resolve_node_failure_returns_1
# 🧪 TRAP[TEST] · Regression · node.yaml resolution failure
# · Scenario: resolve_node_yaml бросает NodeYamlNotFoundError → execute_update возвращает 1, IMP:10
# · Last fail: N/A (new module — mirrors shell _resolve_and_extract return 1)
# · Remove if: resolve failure exit code semantics change
def test_resolve_node_failure_returns_1(executor, caplog) -> None:
    msg = "node.yaml not found for node=test-node"
    executor._deliverer.resolve_node_yaml.side_effect = NodeYamlNotFoundError(msg)  # type: ignore[union-attr]
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 1
    assert any("[IMP:10][execute_update][resolve] FATAL" in r.message for r in caplog.records)


# endregion FUNC_test_resolve_node_failure_returns_1


# ═══════════════════════════════════════════════════════════════════
# execute-deploy-context (DevPlan 153 T7, N3) — remote режим
# ═══════════════════════════════════════════════════════════════════


REMOTE_CMD_DEPLOY_CONTEXT = (
    "set -euo pipefail && export PLATFORM_ROOT=/opt/platform && export PYTHONPATH=/opt/platform && "
    "python3 /opt/platform/core/internal/bootstrap/deploy/context_deployer.py "
    "--node-yaml /opt/node-configs/test-node/node.yaml --context test-ctx"
)


# region FUNC_test_execute_deploy_context_ok
# 🧪 TRAP[TEST] · Regression · deploy-context remote: resolve host → ssh exec, без sync-core
# · Scenario: execute_deploy_context выполняет ssh (context_deployer.py), НЕ вызывает sync_core_to_vps
# · Last fail: RC-прогон 2026-08-12 — docker-операции выполнялись локально на macOS (N3)
# · Remove if: remote-канал deploy-context меняется
def test_execute_deploy_context_ok(executor, caplog) -> None:
    rc = executor.execute_deploy_context("test-node", REMOTE_CMD_DEPLOY_CONTEXT)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    executor.sync_mock.assert_not_called()  # ключевое отличие от execute_update
    assert len(executor.runner.calls) == 1


# endregion FUNC_test_execute_deploy_context_ok


# region FUNC_test_execute_deploy_context_no_host
# 🧪 TRAP[TEST] · Regression · deploy-context без host → exit 2 (local fallback)
# · Scenario: extract_node_host возвращает "" → exit 2, ssh НЕ выполняется
# · Last fail: N/A (new test, DevPlan 153 T7)
# · Remove if: remote-канал deploy-context меняется
def test_execute_deploy_context_no_host(executor, caplog) -> None:
    executor._deliverer.extract_node_host.return_value = ""  # type: ignore[union-attr]  # DI fake handle
    rc = executor.execute_deploy_context("test-node", REMOTE_CMD_DEPLOY_CONTEXT)
    assert _print_ldd_trajectory(caplog)
    assert rc == 2
    assert executor.runner.calls == []


# endregion FUNC_test_execute_deploy_context_no_host


# region FUNC_test_execute_deploy_context_vps_self
# 🧪 TRAP[TEST] · Regression · VPS self-SSH detect → exit 2 (local exec на ноде)
# · Scenario: facts.path_isfile(VPS_NODE_LIFECYCLE) = True (мы на ноде) → exit 2, ssh НЕ выполняется
# · Last fail: RC 121 — self-SSH loop (double reconcile) паттерн перенесён на deploy-context
# · Remove if: remote-канал deploy-context меняется
def test_execute_deploy_context_vps_self(executor, caplog) -> None:
    executor._facts = FakeFacts(path_isfile=lambda _: True)  # type: ignore[attr-defined]
    rc = executor.execute_deploy_context("test-node", REMOTE_CMD_DEPLOY_CONTEXT)
    assert _print_ldd_trajectory(caplog)
    assert rc == 2
    assert executor.runner.calls == []


# endregion FUNC_test_execute_deploy_context_vps_self
