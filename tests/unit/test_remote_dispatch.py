# GREP_SUMMARY: test remote_dispatch converge node-update unified-verb rc2-discrimination local-fallback passthrough age-key rc3-semantics auto-detect FakeExecutor LDD
# STRUCTURE: ▶ FakeExecutor (DI-шов) + monkeypatch границ → ◇ parse_args (verb/passthrough/alias/usage) → ◇ converge (rc=2: host→2 / no-host→fallback; auto-detect; rc=0) → ◇ update (node-required; rc=3 age-key non-fatal; deliver fail/skip; rc=2 fallback) → ◇ fallback-команды (fake subprocess.call) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for remote_dispatch.py (DevPlan 170 W9-F2) — единый dispatch converge/
##           node-update: CLI-parse (--verb, passthrough, --node alias), rc=2 дискриминация
##           (R-unit errors vs no-SSH-host fallback), rc=3 age-key non-fatal семантика,
##           локальные fallback-команды, exit-коды 0/1/2.
## @scope    14 tests. SSH-канал НЕ выполняется: FakeExecutor (executor= DI-шов run_converge/
##           run_update). Пограничные каналы (resolve/age-key/deliver) — monkeypatch module-level
##           функций remote_dispatch (documented contract, как check_suite monkeypatch-контракт).
##           Локальные fallback-команды — fake subprocess.call (запись команд, 0 реальных bash).
##           tmp_path — не требуется (команды не исполняются). caplog LDD-траектория.
## @invariants — FakeExecutor НЕ наследует RemoteExecutor (протокол execute_converge/execute_update)
##              — Каждый success-путь верифицирует IMP:9 лог (anti-illusion, testing.md §LDD)
##              — build_*_ssh_cmd — РЕАЛЬНЫЕ функции (printf %q, 0 subprocess) — passthrough-проверки честны
## @rationale rc=2/rc=3 дискриминация — бизнес-логика, перенесённая из shell-двойников (research-A §9):
##            единственный способ гарантировать 1:1 семантику — unit-покрытие каждого ветвления.
## @changes  2026-08-15 | Created (DevPlan 170 W9-F2)
## @usecases pytest tests/unit/test_remote_dispatch.py -v
# endregion MODULE_CONTRACT

import logging
import os
from unittest import mock

import pytest
from _conftest.ldd import _print_ldd_trajectory

from core.internal.bootstrap import remote_dispatch
from core.internal.bootstrap.overlay_deliverer import DeliveryError, NodeYamlNotFoundError
from core.internal.shared.node_detect import NodeDetectionError
from core.internal.shared.ssh_cmd_builder import build_converge_ssh_cmd

pytestmark = pytest.mark.static_audit


# ═══════════════════════════════════════════════════════════════════
# Fake-реализация DI-протокола executor (run_converge/run_update шов)
# ═══════════════════════════════════════════════════════════════════


class FakeExecutor:
    """Scripted RemoteExecutor-протокол (DI-шов executor= remote_dispatch).

    ## @purpose — Замена RemoteExecutor в тестах: execute_converge/execute_update возвращают
    ##            scripted rc и записывают node/remote_cmd/passthrough для ассертов.
    ## @io — ⇥ rc: int (scripted результат) → ⎋ объект с execute_converge/execute_update
    ## @complexity — O(1)
    ## @invariants — ЗАПРЕЩАЕТ любой SSH/rsync/subprocess (чистый протокол-фейк)
    """

    def __init__(self, rc: int):
        self._rc = rc
        self.calls: list[dict] = []

    def _record(self, verb: str, node: str, remote_cmd: str, passthrough: str) -> int:
        self.calls.append({"verb": verb, "node": node, "remote_cmd": remote_cmd, "passthrough": passthrough})
        return self._rc

    def execute_converge(self, node: str, remote_cmd: str, passthrough: str) -> int:
        return self._record("converge", node, remote_cmd, passthrough)

    def execute_update(self, node: str, remote_cmd: str, passthrough: str) -> int:
        return self._record("update", node, remote_cmd, passthrough)


def _args(**overrides) -> remote_dispatch.Args:
    """Build Args с дефолтами (converge-контекст)."""
    defaults: dict = {"verb": "converge", "node": "test-node", "dry_run": False}
    defaults.update(overrides)
    return remote_dispatch.Args(**defaults)


@pytest.fixture(autouse=True)
def _capture_imp_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Capture INFO-level logs (IMP:7-10) — basicConfig in module sets WARNING."""
    caplog.set_level(logging.INFO)
    yield


# ═══════════════════════════════════════════════════════════════════
# parse_args
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_parse_args_verb_required
# 🧪 TRAP[TEST] · Regression: --verb обязателен (rc=2 usage-семантика argparse)
# · Scenario: dispatch без --verb не должен уходить в дефолтную операцию
# · Last fail: never (new)
# · Remove if: --verb станет опциональным с дефолтом
@pytest.mark.static_audit
def test_parse_args_verb_required() -> None:
    """--verb missing → SystemExit(2) (argparse usage)."""
    with pytest.raises(SystemExit) as exc_info:
        remote_dispatch.parse_args([])
    assert exc_info.value.code == 2


# endregion FUNC_test_parse_args_verb_required


# region FUNC_test_parse_args_converge_passthrough
# 🧪 TRAP[TEST] · Regression: passthrough-аккумуляция (--reconcile + unknown)
# · Scenario: --reconcile и неизвестные флаги должны форвардиться субагентам (семантика 1:1 shell)
# · Last fail: never (new)
# · Remove if: passthrough-паттерн отменён
@pytest.mark.static_audit
def test_parse_args_converge_passthrough() -> None:
    """--reconcile + --report-only → passthrough (shell PASSTHROUGH_ARGS эквивалент)."""
    args = remote_dispatch.parse_args([
        "--verb",
        "converge",
        "--node",
        "n1",
        "--reconcile",
        "--report-only",
        "--units",
        "R1",
    ])
    assert args.verb == "converge"
    assert args.node == "n1"
    assert args.passthrough == ["--reconcile", "--report-only", "--units", "R1"]


# endregion FUNC_test_parse_args_converge_passthrough


# region FUNC_test_parse_args_node_alias_and_age_key
# 🧪 TRAP[TEST] · Regression: --node-name alias + --age-secret-key-file
# · Scenario: оба флага существующих entrypoints не должны ломаться при миграции
# · Last fail: never (new)
# · Remove if: alias/флаг удалены из CLI
@pytest.mark.static_audit
def test_parse_args_node_alias_and_age_key() -> None:
    """--node-name alias + --age-secret-key-file + --dry-run парсятся."""
    args = remote_dispatch.parse_args([
        "--verb",
        "update",
        "--node-name",
        "n2",
        "--dry-run",
        "--age-secret-key-file",
        "/tmp/age.txt",
    ])
    assert args.verb == "update"
    assert args.node == "n2"
    assert args.dry_run is True
    assert args.age_secret_key_file == "/tmp/age.txt"


# endregion FUNC_test_parse_args_node_alias_and_age_key


# ═══════════════════════════════════════════════════════════════════
# run_converge
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_converge_remote_rc2_host_present_no_local_fallback
# 🧪 TRAP[TEST] · Regression: rc=2 дискриминация (142 B28b, TRAP[BUG] 2026-08-07)
# · Scenario: host из node.yaml есть → rc=2 = R-unit errors ноды → exit 2, БЕЗ локального прогона
# ·            (двойной ЛОКАЛЬНЫЙ прогон на dev-машине — артефакты macOS)
# · Last fail: 2026-08-07 (P1, в shell converge.sh)
# · Remove if: rc=2 дискриминация переехала на другой канал
@pytest.mark.static_audit
def test_converge_remote_rc2_host_present_no_local_fallback(caplog, monkeypatch) -> None:
    """rc=2 + host присутствует → exit 2, локальный fallback НЕ вызывается."""
    fake = FakeExecutor(rc=2)
    fallback_called: list[int] = []
    monkeypatch.setattr(remote_dispatch, "_resolve_ssh_host", lambda _: "10.0.0.1")
    monkeypatch.setattr(
        remote_dispatch, "_local_converge_fallback", lambda _node, _args: fallback_called.append(1) or 42
    )

    rc = remote_dispatch.run_converge(_args(), executor=fake)

    assert rc == 2, "rc=2 + host present должен форвардиться (R-unit errors), НЕ локально"
    assert fallback_called == [], "локальный fallback запрещён при наличии host"
    assert fake.calls[0]["verb"] == "converge" and fake.calls[0]["node"] == "test-node"
    assert _print_ldd_trajectory(caplog)
    assert any("[IMP:8][remote_dispatch][converge] Remote converge" in r.message for r in caplog.records)


# endregion FUNC_test_converge_remote_rc2_host_present_no_local_fallback


# region FUNC_test_converge_remote_rc2_no_host_local_fallback
# 🧪 TRAP[TEST] · Regression: rc=2 + host пуст → локальный fallback (backward-compatible)
# · Scenario: нет node.yaml/host → converge выполняется локально (dev-машина)
# · Last fail: never (new)
# · Remove if: локальный fallback отменён
@pytest.mark.static_audit
def test_converge_remote_rc2_no_host_local_fallback(caplog, monkeypatch) -> None:
    """rc=2 + host пуст → локальный fallback converge.sh (rc пробрасывается)."""
    fake = FakeExecutor(rc=2)
    fallback_called: list[tuple] = []
    monkeypatch.setattr(remote_dispatch, "_resolve_ssh_host", lambda _: "")
    monkeypatch.setattr(
        remote_dispatch, "_local_converge_fallback", lambda node, args: fallback_called.append((node, args)) or 42
    )

    rc = remote_dispatch.run_converge(_args(), executor=fake)

    assert rc == 42, "rc локального fallback должен пробрасываться"
    assert len(fallback_called) == 1 and fallback_called[0][0] == "test-node"
    assert _print_ldd_trajectory(caplog)
    assert any("[IMP:9][remote_dispatch][converge] No SSH host" in r.message for r in caplog.records)


# endregion FUNC_test_converge_remote_rc2_no_host_local_fallback


# region FUNC_test_converge_remote_rc0
# 🧪 TRAP[TEST] · Regression: rc=0 (success) пробрасывается; remote_cmd содержит passthrough
# · Scenario: успешный remote converge не должен триггерить fallback
# · Last fail: never (new)
# · Remove if: converge-цикл изменён
@pytest.mark.static_audit
def test_converge_remote_rc0(caplog, monkeypatch) -> None:
    """rc=0 → exit 0; build_converge_ssh_cmd реально получает passthrough (--reconcile)."""
    fake = FakeExecutor(rc=0)
    monkeypatch.setattr(remote_dispatch, "_resolve_ssh_host", lambda _: "10.0.0.1")

    rc = remote_dispatch.run_converge(_args(passthrough=["--reconcile"]), executor=fake)

    assert rc == 0
    assert fake.calls[0]["passthrough"] == "--reconcile"
    assert "--reconcile" in fake.calls[0]["remote_cmd"], "passthrough должен попасть в remote_cmd (D3 %q)"
    assert _print_ldd_trajectory(caplog)
    assert any("[IMP:9][remote_dispatch][converge] Starting converge" in r.message for r in caplog.records)


# endregion FUNC_test_converge_remote_rc0


# region FUNC_test_converge_auto_detect_node
# 🧪 TRAP[TEST] · Regression: --node отсутствует → auto-detect (node_detect канал)
# · Scenario: converge без --node должен детектить ноду (shell converge.sh:63-71)
# · Last fail: never (new)
# · Remove if: auto-detect отменён (--node станет обязательным)
@pytest.mark.static_audit
def test_converge_auto_detect_node(caplog, monkeypatch) -> None:
    """--node пуст → auto_detect_node_name() → нода подставляется в executor."""
    fake = FakeExecutor(rc=0)
    monkeypatch.setattr(remote_dispatch, "_resolve_ssh_host", lambda _: "")
    monkeypatch.setattr(remote_dispatch, "auto_detect_node_name", lambda: "auto-node")

    rc = remote_dispatch.run_converge(_args(node=""), executor=fake)

    assert rc == 0
    assert fake.calls[0]["node"] == "auto-node"
    assert _print_ldd_trajectory(caplog)
    assert any("[IMP:9][remote_dispatch][converge] Auto-detected NODE=auto-node" in r.message for r in caplog.records)


# endregion FUNC_test_converge_auto_detect_node


# region FUNC_test_converge_auto_detect_fails_exit1
# 🧪 TRAP[TEST] · Regression: auto-detect не нашёл ноду → FATAL exit 1 (shell converge.sh:65-69)
# · Scenario: ни --node, ни детект → понятная ошибка с usage
# · Last fail: never (new)
# · Remove if: converge-цикл изменён
@pytest.mark.static_audit
def test_converge_auto_detect_fails_exit1(caplog, monkeypatch) -> None:
    """auto_detect_node_name → NodeDetectionError → exit 1."""
    fake = FakeExecutor(rc=0)
    monkeypatch.setattr(remote_dispatch, "auto_detect_node_name", mock.Mock(side_effect=NodeDetectionError("none")))

    rc = remote_dispatch.run_converge(_args(node=""), executor=fake)

    assert rc == 1
    assert fake.calls == [], "executor не должен вызываться при fatal-ошибке входа"
    assert any("[IMP:10][remote_dispatch][converge] FATAL" in r.message for r in caplog.records)


# endregion FUNC_test_converge_auto_detect_fails_exit1


# ═══════════════════════════════════════════════════════════════════
# run_update
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_update_node_required
# 🧪 TRAP[TEST] · Regression: --node обязателен для update (shell node-update.sh:58-62)
# · Scenario: update без --node → FATAL exit 1 (в отличие от converge — БЕЗ auto-detect)
# · Last fail: never (new)
# · Remove if: update получит auto-detect
@pytest.mark.static_audit
def test_update_node_required(caplog) -> None:
    """--node пуст → exit 1 (update не делает auto-detect)."""
    fake = FakeExecutor(rc=0)
    rc = remote_dispatch.run_update(_args(verb="update", node=""), executor=fake)
    assert rc == 1
    assert fake.calls == []
    assert any("[IMP:10][remote_dispatch][update] FATAL: --node is required" in r.message for r in caplog.records)


# endregion FUNC_test_update_node_required


# region FUNC_test_update_age_key_file_exports_env
# 🧪 TRAP[TEST] · Regression: --age-secret-key-file → AGE_SECRET_KEY_FILE env (shell export)
# · Scenario: путь ключа должен попасть в env (detect_age_key читает AGE_SECRET_KEY_FILE)
# · Last fail: never (new)
# · Remove if: флаг удалён
@pytest.mark.static_audit
def test_update_age_key_file_exports_env(caplog, monkeypatch) -> None:
    """--age-secret-key-file экспортируется в os.environ (export-эквивалент shell)."""
    fake = FakeExecutor(rc=0)
    monkeypatch.setattr(remote_dispatch, "detect_age_key", lambda: None)
    monkeypatch.setattr(remote_dispatch, "deliver_vhost_overlays", lambda _: True)
    monkeypatch.setattr(remote_dispatch, "_local_update_fallback", lambda _node, _args: 0)

    rc = remote_dispatch.run_update(_args(verb="update", age_secret_key_file="/tmp/age.txt"), executor=fake)

    assert rc == 0
    assert os.environ.get("AGE_SECRET_KEY_FILE") == "/tmp/age.txt"
    assert any("[IMP:9][remote_dispatch][update] Starting node-update" in r.message for r in caplog.records)


# endregion FUNC_test_update_age_key_file_exports_env


# region FUNC_test_update_age_key_absent_rc3_non_fatal
# 🧪 TRAP[TEST] · Regression: rc=3 семантика (DevPlan 104 D3) — key absent = non-fatal
# · Scenario: detect_age_key → None (CLI-код 3 в shell) НЕ должен ронять update — age_key=""
# · Last fail: never (new)
# · Remove if: age-key станет обязательным
@pytest.mark.static_audit
def test_update_age_key_absent_rc3_non_fatal(caplog, monkeypatch) -> None:
    """detect_age_key → None → non-fatal (age_key="" в build_update_ssh_cmd), update продолжается."""
    fake = FakeExecutor(rc=0)
    monkeypatch.setattr(remote_dispatch, "detect_age_key", lambda: None)
    monkeypatch.setattr(remote_dispatch, "deliver_vhost_overlays", lambda _: True)
    monkeypatch.setattr(remote_dispatch, "_resolve_ssh_host", lambda _: "")

    rc = remote_dispatch.run_update(_args(verb="update"), executor=fake)

    assert rc == 0, "age-key absent (rc=3) — non-fatal, update должен продолжиться"
    assert fake.calls[0]["verb"] == "update"
    assert _print_ldd_trajectory(caplog)
    assert any("rc=3 non-fatal" in r.message for r in caplog.records)


# endregion FUNC_test_update_age_key_absent_rc3_non_fatal


# region FUNC_test_update_rc2_local_fallback
# 🧪 TRAP[TEST] · Regression: rc=2 (no SSH host) → локальный fallback node-lifecycle.sh
# · Scenario: update на dev-машине без host → локальный прогон lifecycle
# · Last fail: never (new)
# · Remove if: локальный fallback отменён
@pytest.mark.static_audit
def test_update_rc2_local_fallback(caplog, monkeypatch) -> None:
    """rc=2 → локальный fallback node-lifecycle.sh (rc пробрасывается)."""
    fake = FakeExecutor(rc=2)
    fallback_called: list[str] = []
    monkeypatch.setattr(remote_dispatch, "detect_age_key", lambda: "KEY")
    monkeypatch.setattr(remote_dispatch, "deliver_vhost_overlays", lambda _: True)
    monkeypatch.setattr(
        remote_dispatch, "_local_update_fallback", lambda node, _args: fallback_called.append(node) or 7
    )

    rc = remote_dispatch.run_update(_args(verb="update"), executor=fake)

    assert rc == 7
    assert fallback_called == ["test-node"]
    assert _print_ldd_trajectory(caplog)
    assert any("[IMP:9][remote_dispatch][update] No SSH host" in r.message for r in caplog.records)


# endregion FUNC_test_update_rc2_local_fallback


# region FUNC_test_update_deliver_fails_exit1
# 🧪 TRAP[TEST] · Regression: S2 vhost overlay delivery failure → FATAL exit 1
# · Scenario: deliver_vhost_overlays → DeliveryError → update НЕ запускается
# · Last fail: never (new)
# · Remove if: S2-доставка удалена
@pytest.mark.static_audit
def test_update_deliver_fails_exit1(caplog, monkeypatch) -> None:
    """DeliveryError от deliver_vhost_overlays → exit 1, executor НЕ вызывается."""
    fake = FakeExecutor(rc=0)
    monkeypatch.setattr(remote_dispatch, "detect_age_key", lambda: None)
    monkeypatch.setattr(remote_dispatch, "deliver_vhost_overlays", mock.Mock(side_effect=DeliveryError("rsync fail")))

    rc = remote_dispatch.run_update(_args(verb="update"), executor=fake)

    assert rc == 1
    assert fake.calls == []
    assert any("[IMP:10][remote_dispatch][update] FATAL: Vhost overlay delivery" in r.message for r in caplog.records)


# endregion FUNC_test_update_deliver_fails_exit1


# region FUNC_test_update_deliver_skipped_on_dry_run
# 🧪 TRAP[TEST] · Regression: dry-run НЕ доставляет overlays (shell node-update.sh:77)
# · Scenario: DRY_RUN → deliver_vhost_overlays пропускается
# · Last fail: never (new)
# · Remove if: dry-run семантика изменена
@pytest.mark.static_audit
def test_update_deliver_skipped_on_dry_run(caplog, monkeypatch) -> None:
    """dry_run=True → deliver_vhost_overlays не вызывается."""
    fake = FakeExecutor(rc=0)
    deliver_called: list[str] = []
    monkeypatch.setattr(remote_dispatch, "detect_age_key", lambda: None)
    monkeypatch.setattr(remote_dispatch, "deliver_vhost_overlays", lambda node: deliver_called.append(node) or True)

    rc = remote_dispatch.run_update(_args(verb="update", dry_run=True), executor=fake)

    assert rc == 0
    assert deliver_called == [], "dry-run не должен доставлять vhost overlays (S2, shell :77-83)"
    assert _print_ldd_trajectory(caplog)


# endregion FUNC_test_update_deliver_skipped_on_dry_run


# ═══════════════════════════════════════════════════════════════════
# Локальные fallback-команды (fake subprocess.call — 0 реальных bash)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_local_converge_fallback_command
# 🧪 TRAP[TEST] · Regression: локальная converge-команда (--node [--dry-run] passthrough)
# · Scenario: fallback должен звать bash converge.sh с теми же флагами, что shell (TRAP[BUG] 2026-07-23)
# · Last fail: 2026-07-23 (P0 — early exit на dry-run вместо делегирования)
# · Remove if: локальный fallback удалён
@pytest.mark.static_audit
def test_local_converge_fallback_command(caplog, monkeypatch) -> None:
    """_local_converge_fallback: команда = bash converge.sh --node n1 --dry-run --reconcile."""
    recorded: list[list[str]] = []
    monkeypatch.setattr(remote_dispatch.subprocess, "call", lambda cmd: recorded.append(list(cmd)) or 0)

    rc = remote_dispatch._local_converge_fallback("n1", _args(dry_run=True, passthrough=["--reconcile"]))

    assert rc == 0
    assert recorded[0] == [
        "bash",
        str(remote_dispatch._CONVERGE_INTERNAL),
        "--node",
        "n1",
        "--dry-run",
        "--reconcile",
    ], "--dry-run делегируется субагенту (НЕ ранний exit — TRAP[BUG] 2026-07-23 P0)"
    assert any("[IMP:8][remote_dispatch][converge] Delegating" in r.message for r in caplog.records)


# endregion FUNC_test_local_converge_fallback_command


# region FUNC_test_local_converge_fallback_missing_script
# 🧪 TRAP[TEST] · Regression: internal converge.sh отсутствует → FATAL exit 1
# · Scenario: missing субагент — понятная ошибка (shell converge.sh:102-105)
# · Last fail: never (new)
# · Remove if: субагент гарантированно существует
@pytest.mark.static_audit
def test_local_converge_fallback_missing_script(caplog, monkeypatch) -> None:
    """_CONVERGE_INTERNAL не существует → exit 1 (без subprocess.call)."""
    called: list = []
    monkeypatch.setattr(remote_dispatch.subprocess, "call", lambda cmd: called.append(cmd) or 0)
    monkeypatch.setattr(remote_dispatch, "_CONVERGE_INTERNAL", remote_dispatch._PROJECT_ROOT / "nope" / "converge.sh")

    rc = remote_dispatch._local_converge_fallback("n1", _args())

    assert rc == 1
    assert called == [], "missing субагент — exit 1 ДО subprocess.call"
    assert any(
        "[IMP:10][remote_dispatch][converge] FATAL: Internal script not found" in r.message for r in caplog.records
    )


# endregion FUNC_test_local_converge_fallback_missing_script


# region FUNC_test_local_update_fallback_command
# 🧪 TRAP[TEST] · Regression: локальная update-команда node-lifecycle.sh --mode update
# · Scenario: fallback зовёт bash node-lifecycle.sh --mode update --node-name --node-yaml (shell :105-114)
# · Last fail: never (new)
# · Remove if: локальный fallback удалён
@pytest.mark.static_audit
def test_local_update_fallback_command(caplog, monkeypatch) -> None:
    """_local_update_fallback: команда с --mode update + resolve node.yaml + passthrough."""
    recorded: list[list[str]] = []
    monkeypatch.setattr(remote_dispatch.subprocess, "call", lambda cmd: recorded.append(list(cmd)) or 0)
    monkeypatch.setattr(remote_dispatch, "resolve_node_yaml", lambda _: "/tmp/node.yaml")

    rc = remote_dispatch._local_update_fallback("n1", _args(verb="update", passthrough=["--reconcile"]))

    assert rc == 0
    assert recorded[0] == [
        "bash",
        str(remote_dispatch._NODE_LIFECYCLE_INTERNAL),
        "--mode",
        "update",
        "--node-name",
        "n1",
        "--node-yaml",
        "/tmp/node.yaml",
        "--reconcile",
    ]
    assert any("[IMP:8][remote_dispatch][update] Delegating" in r.message for r in caplog.records)


# endregion FUNC_test_local_update_fallback_command


# region FUNC_test_local_update_fallback_dry_run_exit0
# 🧪 TRAP[TEST] · Regression: dry-run локальный fallback → печать + exit 0 (shell :108-112)
# · Scenario: DRY_RUN без SSH host — команда печатается, НЕ исполняется
# · Last fail: never (new)
# · Remove if: dry-run семантика изменена
@pytest.mark.static_audit
def test_local_update_fallback_dry_run_exit0(caplog, monkeypatch) -> None:
    """dry_run → команда печатается (IMP:8/9), subprocess.call НЕ вызывается, exit 0."""
    called: list = []
    monkeypatch.setattr(remote_dispatch.subprocess, "call", lambda cmd: called.append(cmd) or 0)
    monkeypatch.setattr(remote_dispatch, "resolve_node_yaml", lambda _: "/tmp/node.yaml")

    rc = remote_dispatch._local_update_fallback("n1", _args(verb="update", dry_run=True))

    assert rc == 0
    assert called == [], "dry-run — команда только печатается"
    assert any("[IMP:9][remote_dispatch][update][dry-run] DRY-RUN complete" in r.message for r in caplog.records)


# endregion FUNC_test_local_update_fallback_dry_run_exit0


# region FUNC_test_local_update_fallback_resolve_fail
# 🧪 TRAP[TEST] · Regression: node.yaml не резолвится → FATAL exit 1 (shell node-update.sh:101-104)
# · Scenario: fallback без node.yaml — понятная ошибка
# · Last fail: never (new)
# · Remove if: резолв гарантирован
@pytest.mark.static_audit
def test_local_update_fallback_resolve_fail(caplog, monkeypatch) -> None:
    """resolve_node_yaml → NodeYamlNotFoundError → exit 1."""
    called: list = []
    monkeypatch.setattr(remote_dispatch.subprocess, "call", lambda cmd: called.append(cmd) or 0)
    monkeypatch.setattr(remote_dispatch, "resolve_node_yaml", mock.Mock(side_effect=NodeYamlNotFoundError("none")))

    rc = remote_dispatch._local_update_fallback("n1", _args(verb="update"))

    assert rc == 1
    assert called == []
    assert any("[IMP:10][remote_dispatch][update] FATAL: Cannot resolve node.yaml" in r.message for r in caplog.records)


# endregion FUNC_test_local_update_fallback_resolve_fail


# ═══════════════════════════════════════════════════════════════════
# main dispatch
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_main_dispatch_converge
# 🧪 TRAP[TEST] · Regression: main разводит --verb на run_converge/run_update
# · Scenario: CLI-маршрутизация — converge vs update
# · Last fail: never (new)
# · Remove if: CLI-контракт изменён
@pytest.mark.static_audit
def test_main_dispatch_converge(monkeypatch) -> None:
    """main(['--verb', 'converge', '--node', 'n1']) → run_converge (fake executor, rc=0)."""
    fake = FakeExecutor(rc=0)
    monkeypatch.setattr(remote_dispatch, "_resolve_ssh_host", lambda _: "10.0.0.1")

    rc = remote_dispatch.main(["--verb", "converge", "--node", "n1"], executor=fake)

    assert rc == 0
    assert fake.calls[0]["verb"] == "converge"


# endregion FUNC_test_main_dispatch_converge


# region FUNC_test_ssh_cmd_builder_passthrough_parity
# 🧪 TRAP[TEST] · Regression: D3-канал (build_converge_ssh_cmd) честно несёт passthrough
# · Scenario: build-функция реальна — passthrough-флаги %q-экранируются в remote_cmd
# · Last fail: never (new)
# · Remove if: build-канал заменён
@pytest.mark.static_audit
def test_ssh_cmd_builder_passthrough_parity() -> None:
    """build_converge_ssh_cmd реально включает passthrough (--reconcile) — семантика 1:1."""
    cmd = build_converge_ssh_cmd("test-node", ["--reconcile"])
    assert "--reconcile" in cmd
    assert "converge.sh" in cmd


# endregion FUNC_test_ssh_cmd_builder_passthrough_parity
