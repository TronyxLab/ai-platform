# GREP_SUMMARY: test remote_executor execute-update execute-converge execute-reconcile CLI argparse mock-subprocess exit-codes VPS-self-SSH sync-core DRY_RUN LDD
# STRUCTURE: ▶ fixtures(mock resolve/extract/sync + no-VPS) → ◇ CLI help → ◇ update: no-host=2 / VPS=2 / dry-run=0 / sync-fail=1 / ssh=0 / timeout=124 / resolve-fail=1 → ◇ converge: no-sync-core → ◇ reconcile: --reconcile flag → ⎋ LDD IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for remote_executor.py — CLI parsing, exit codes (0/1/2/124),
##           DRY_RUN, VPS self-SSH detect, sync-core/no-sync-core, error propagation, LDD IMP:9.
## @scope    11 tests per DevPlan 101 §8 $TEST_SPEC. Mock subprocess.run — zero real SSH/rsync.
##           tmp_path fixtures — zero hardcoded paths. caplog-based LDD trajectory.
## @invariants — Все subprocess-dependent тесты мокают remote_executor.subprocess.run
##              — resolve/extract/sync мокаются на уровне модуля (monkeypatch) — нет файловой зависимости
##              — Каждый success-путь верифицирует IMP:9 лог (anti-illusion, testing.md §LDD)
## @rationale DevPlan 101 TASK-6: unit-покрытие Python-порта execute_remote_* оркестрации.
## @usecases pytest tests/unit/test_remote_executor.py -v
# endregion MODULE_CONTRACT

import logging
import subprocess
from unittest import mock

import pytest
from _conftest.ldd import _print_ldd_trajectory

from core.internal.bootstrap import remote_executor
from core.internal.bootstrap.overlay_deliverer import NodeYamlNotFoundError, SyncCoreError


@pytest.fixture(autouse=True)
def _capture_imp_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Capture INFO-level logs (IMP:7-10) — basicConfig in module sets WARNING."""
    caplog.set_level(logging.INFO)
    yield


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """RemoteExecutor with resolve/extract/sync mocked; VPS marker absent."""
    sync_mock = mock.Mock(return_value=True)
    monkeypatch.setattr(
        remote_executor, "resolve_node_yaml", lambda name, platform_root=None: str(tmp_path / "node.yaml")
    )
    monkeypatch.setattr(remote_executor, "extract_node_host", lambda yaml_path: "10.0.0.1")
    monkeypatch.setattr(remote_executor, "sync_core_to_vps", sync_mock)
    monkeypatch.setattr(remote_executor, "VPS_NODE_LIFECYCLE", str(tmp_path / "no-vps-marker"))
    inst = remote_executor.RemoteExecutor(dry_run=False)
    inst.sync_mock = sync_mock  # type: ignore[attr-defined]  # test convenience handle
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
def test_execute_update_no_host_returns_2(executor, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setattr(remote_executor, "extract_node_host", lambda yaml_path: "")
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 2
    assert any("[IMP:9][execute_update][resolve] No SSH host" in r.message for r in caplog.records)


# endregion FUNC_test_execute_update_no_host_returns_2


# region FUNC_test_execute_update_vps_self_ssh_returns_2
# 🧪 TRAP[TEST] · Regression · VPS self-SSH loop (TRAP[BUG] P0)
# · Scenario: /opt/platform/core/internal/bootstrap/node-lifecycle.sh существует локально
# ·            → мы на VPS → execute_update возвращает 2 (skip SSH proxy)
# · Last fail: 2026-07-23 — node-update зацикливался, подключаясь к самому себе
# · Remove if: VPS self-SSH detect механизм изменён
def test_execute_update_vps_self_ssh_returns_2(executor, monkeypatch: pytest.MonkeyPatch, tmp_path, caplog) -> None:
    vps_marker = tmp_path / "opt" / "platform" / "core" / "internal" / "bootstrap" / "node-lifecycle.sh"
    vps_marker.parent.mkdir(parents=True)
    vps_marker.write_text("#!/usr/bin/env bash\n")
    monkeypatch.setattr(remote_executor, "VPS_NODE_LIFECYCLE", str(vps_marker))
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 2
    assert any("[IMP:9][execute_update][vps-detect] Local VPS detected" in r.message for r in caplog.records)
    executor.sync_mock.assert_not_called()  # sync-core ДОЛЖЕН быть пропущен при self-SSH


# endregion FUNC_test_execute_update_vps_self_ssh_returns_2


# region FUNC_test_execute_update_dry_run_exits_0
# 🧪 TRAP[TEST] · Regression · DRY_RUN не выполняет ssh/rsync
# · Scenario: dry_run=True → sync-core вызывается с dry_run=True, ssh НЕ вызывается, exit 0
# · Last fail: N/A (new module — mirrors shell early-exit 0)
# · Remove if: DRY_RUN семантика меняется
def test_execute_update_dry_run_exits_0(executor, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    run_mock = mock.Mock(side_effect=AssertionError("DRY_RUN must not execute ssh/rsync"))
    monkeypatch.setattr(remote_executor.subprocess, "run", run_mock)
    dry = remote_executor.RemoteExecutor(dry_run=True)
    rc = dry.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    executor.sync_mock.assert_called_once()
    assert executor.sync_mock.call_args.kwargs.get("dry_run") is True
    run_mock.assert_not_called()
    assert any("[IMP:8][execute_update][dry-run] DRY-RUN: ssh" in r.message for r in caplog.records)


# endregion FUNC_test_execute_update_dry_run_exits_0


# region FUNC_test_execute_update_sync_core_fails_returns_1
# 🧪 TRAP[TEST] · Regression · sync-core failure propagation
# · Scenario: sync_core_to_vps бросает SyncCoreError → execute_update возвращает 1, IMP:10 лог
# · Last fail: 2026-07-24 — node-update не доставлял core/ на VPS (TRAP[BUG] P0 overlay_deliverer)
# · Remove if: sync-core error handling изменён
def test_execute_update_sync_core_fails_returns_1(executor, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    def _fail(*args, **kwargs):
        raise SyncCoreError("rsync core/ failed for 10.0.0.1 (exit=1): test error")

    monkeypatch.setattr(remote_executor, "sync_core_to_vps", _fail)
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 1
    assert any("[IMP:10][execute_update][sync-core] FATAL" in r.message for r in caplog.records)


# endregion FUNC_test_execute_update_sync_core_fails_returns_1


# region FUNC_test_execute_update_ssh_exec_success_returns_0
# 🧪 TRAP[TEST] · Regression · SSH exec success path
# · Scenario: subprocess.run(ssh) → rc=0 → execute_update возвращает 0; sync-core вызван
# · Last fail: N/A (new module — mirrors shell ssh_exec return 0)
# · Remove if: ssh_exec success semantics change
def test_execute_update_ssh_exec_success_returns_0(executor, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    run_mock = mock.Mock(return_value=mock.Mock(returncode=0))
    monkeypatch.setattr(remote_executor.subprocess, "run", run_mock)
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    executor.sync_mock.assert_called_once()  # update ОБЯЗАН синхронизировать core
    cmd = run_mock.call_args.args[0]
    assert cmd[0] == "ssh"
    assert "root@10.0.0.1" in cmd
    assert cmd[-1] == REMOTE_CMD_UPDATE  # remote_cmd пробрасывается без изменений


# endregion FUNC_test_execute_update_ssh_exec_success_returns_0


# region FUNC_test_execute_update_ssh_exec_timeout_returns_124
# 🧪 TRAP[TEST] · Regression · SSH timeout detection (mirror lib/ssh.sh exit=124)
# · Scenario: subprocess.TimeoutExpired → execute_update возвращает 124, IMP:10 timeout лог
# · Last fail: N/A (new module — mirrors ssh_exec exit=124 → TIMEOUT)
# · Remove if: timeout handling изменён (SSH_EXEC_TIMEOUT / 124 convention)
def test_execute_update_ssh_exec_timeout_returns_124(executor, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=600)

    monkeypatch.setattr(remote_executor.subprocess, "run", _timeout)
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
def test_execute_converge_no_sync_core(executor, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    run_mock = mock.Mock(return_value=mock.Mock(returncode=0))
    monkeypatch.setattr(remote_executor.subprocess, "run", run_mock)
    rc = executor.execute_converge("test-node", REMOTE_CMD_CONVERGE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    executor.sync_mock.assert_not_called()  # ключевое отличие от execute_update
    run_mock.assert_called_once()


# endregion FUNC_test_execute_converge_no_sync_core


# region FUNC_test_execute_reconcile_adds_reconcile_flag
# 🧪 TRAP[TEST] · Regression · Reconcile пробрасывает --reconcile в remote_cmd
# · Scenario: remote_cmd с --reconcile (добавлен shell build_converge_ssh_cmd) → ssh получает его
# ·            без изменений; sync-core НЕ вызывается (≡ converge)
# · Last fail: N/A (new module — mirrors shell execute_remote_reconcile)
# · Remove if: reconcile флаг-механизм меняется
def test_execute_reconcile_adds_reconcile_flag(executor, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    run_mock = mock.Mock(return_value=mock.Mock(returncode=0))
    monkeypatch.setattr(remote_executor.subprocess, "run", run_mock)
    remote_cmd = REMOTE_CMD_CONVERGE + " --reconcile"
    rc = executor.execute_reconcile("test-node", remote_cmd)
    assert _print_ldd_trajectory(caplog)
    assert rc == 0
    cmd = run_mock.call_args.args[0]
    assert "--reconcile" in cmd[-1]
    executor.sync_mock.assert_not_called()
    assert any("[IMP:9][execute_reconcile][input] --reconcile flag present" in r.message for r in caplog.records)


# endregion FUNC_test_execute_reconcile_adds_reconcile_flag

# ═══════════════════════════════════════════════════════════════════
# LDD / error propagation
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_ldd_imp9_logs_on_success
# 🧪 TRAP[TEST] · Anti-illusion · IMP:9 trajectory на success-пути
# · Scenario: успешный SSH exec → в caplog минимум один IMP:9 лог (бизнес-логика достигнута)
# · Last fail: N/A (LDD methodology — testing.md §LDD anti-illusion rule)
# · Remove if: LDD лог-стандарт изменён
def test_ldd_imp9_logs_on_success(executor, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setattr(remote_executor.subprocess, "run", mock.Mock(return_value=mock.Mock(returncode=0)))
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
def test_resolve_node_failure_returns_1(executor, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    def _fail(name: str, platform_root: str | None = None):
        raise NodeYamlNotFoundError("node.yaml not found for node=test-node")

    monkeypatch.setattr(remote_executor, "resolve_node_yaml", _fail)
    rc = executor.execute_update("test-node", REMOTE_CMD_UPDATE)
    assert _print_ldd_trajectory(caplog)
    assert rc == 1
    assert any("[IMP:10][execute_update][resolve] FATAL" in r.message for r in caplog.records)


# endregion FUNC_test_resolve_node_failure_returns_1
