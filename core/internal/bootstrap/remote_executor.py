#!/usr/bin/env python3
# GREP_SUMMARY: remote-executor execute-update execute-converge execute-reconcile VPS-self-SSH sync-core ssh-exec DRY_RUN exit-codes remote-cmd
# STRUCTURE: ▶ CLI(3 subcmds) → RemoteExecutor → resolve+extract → ◇ host?→2 | ◇ VPS self-SSH→2 → prepare_ssh_opts(update) → ◇ update?→sync-core → ◇ dry-run?→print→0 → ⚡ ssh subprocess.run(timeout=DEPLOY_TIMEOUT) → ⎋ exit 0|1|2|124
# region MODULE_CONTRACT
## @purpose  Execute remote node commands over SSH: full orchestration cycle (resolve node.yaml →
##           extract host → VPS self-SSH detect → prepare ssh opts → sync-core → ssh exec) for
##           node-update / converge / reconcile. Python-порт execute_remote_* из remote-cmd.sh (DevPlan 101).
## @scope    RemoteExecutor class + CLI (execute-update | execute-converge | execute-reconcile).
##           Импортирует overlay_deliverer (resolve_node_yaml, extract_node_host, sync_core_to_vps) —
##           композиция, не дублирование. SSH_OPTS mirror из overlay_deliverer.
## @invariants — exit codes: 0=success, 1=fatal (resolve/sync-core failure), 2=local fallback
##              (no SSH host / VPS self-SSH detected), 124=ssh timeout (mirror lib/ssh.sh)
##              — DRY_RUN: печатает команды (IMP:8), НЕ вызывает ssh/rsync, exit 0
##              — sync-core выполняется ТОЛЬКО в execute_update (converge/reconcile — нет)
##              — VPS self-SSH detect — только в execute_update (локальная проверка файла, как в shell)
##              — ssh через subprocess.run(["ssh", *SSH_OPTS, f"root@{host}", remote_cmd], timeout=DEPLOY_TIMEOUT)
## @rationale SRP (DevPlan 101 D2): overlay_deliverer (421 LOC) = доставка, remote_executor = исполнение.
##            D4: Python не вызывает shell-функцию ssh_exec — subprocess.run + timeout зеркалит поведение
##            (exit=124 → timeout). Логика без set -e — P4-контекст bare ssh_exec не применим.
## @changes 2026-07-31 | DevPlan 101 TASK-2 — Initial implementation (Wave 5d Strangler-Fig завершение)
## @changes 2026-08-13 | DevPlan 160 E1 — +runner: CommandRunner / facts: EnvironmentFacts (DI);
##            subprocess.run + os.path.isfile (VPS detect) → DI-канал, поведение без изменений
## @changes 2026-08-24 | REF-0007 (11-DevPlan В1) — _ssh_exec/execute_update: +stdin_payload
##            (secret-prelude → `bash -s`), AGE-ключ вне argv node-update
# ⚠️ TRAP[BUG] · 2026-07-23 · P0 · VPS self-SSH loop: detect /opt/platform/ → local exec
# · Symptom: `make node-update` на самом VPS зацикливался — execute_remote_update видел SSH host
# ·   из node.yaml, подключался сам к себе и гонял remote-фазы повторно.
# · Fix: перед SSH proxy проверить наличие /opt/platform/core/internal/bootstrap/node-lifecycle.sh
# ·   локально → если есть, мы уже на VPS → return 2 (local fallback).
# · Prevention: любая remote-оркестрация обязана проверять VPS self-SSH ДО sync-core/ssh.
# · Migrated from remote-cmd.sh:164-167 (DevPlan 101 TASK-2).
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Protocol, cast

from core.internal.bootstrap.overlay_deliverer import (
    NodeYamlNotFoundError,
    SyncCoreError,
    extract_node_host,
    resolve_node_yaml,
    sync_core_to_vps,
)

# DevPlan 116 B5 T2 (D1): SSH_OPTS — единый SoT shared/ssh_opts.py (импорт из overlay_deliverer заменён)
# B3: канонический platform base — shared/deploy_paths (литерал /opt/platform удалён)
# E1 (DevPlan 160): runner/facts DI-параметры (W4b/W4d канон) — тесты без monkeypatch subprocess/os
from core.internal.shared.deploy_paths import DEFAULT_PLATFORM_BASE as DEFAULT_REMOTE_PLATFORM
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.ssh_opts import SSH_OPTS
from core.internal.shared.subprocess_io import CommandRunner

# DevPlan 116 B5 T1: таймауты — единый реестр shared/timeouts.py (U-11)
from core.internal.shared.timeouts import DEPLOY_TIMEOUT, SSH_CONNECT_TIMEOUT

logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

# Аналог lib/ssh.sh ssh_exec deploy-mode default (900s, cold-node TRAP timeouts.py:147) — SoT: timeouts.DEPLOY_TIMEOUT (U-11)
SSH_EXEC_TIMEOUT = DEPLOY_TIMEOUT

# VPS self-SSH marker — та же проверка, что remote-cmd.sh:165 (локальный filesystem probe)
# ⚠️ TRAP[BUG] · 2026-08-03 · P1 · VPS-self-detect брал ЛОКАЛЬНЫЙ PLATFORM_ROOT (RC 121 e2e)
# · Symptom: make node-update на dev-машине → «Local VPS detected» → deploy-modules «must run as root».
# · Root: platform_remote_base() наследовал PLATFORM_ROOT (make передаёт локальный корень) —
#   node-lifecycle.sh существует локально → ложный self-detect.
# · Fix: remote-база = PLATFORM_REMOTE_BASE → /opt/platform (deploy_paths канон, PLATFORM_ROOT исключён).
VPS_NODE_LIFECYCLE = str(platform_remote_base() / "core" / "internal" / "bootstrap" / "node-lifecycle.sh")


# region PROTOCOL_Deliverer
class Deliverer(Protocol):
    """DI-контракт deliverer-namespace (DevPlan 167 D3): функции overlay_deliverer.

    ## @purpose — Тип DI-параметра deliverer вместо Any (W11-G3): структурный контракт
    ##            resolve_node_yaml/extract_node_host/sync_core_to_vps — реализуется
    ##            overlay_deliverer-модулем и FakeDeliverer тестов.
    ## @complexity — O(1) — декларация протокола
    """

    def resolve_node_yaml(
        self,
        node_name: str,
        platform_root: str = ...,
        projects_dir: str | None = ...,
    ) -> str: ...

    def extract_node_host(self, yaml_path: str) -> str: ...

    def sync_core_to_vps(
        self,
        host: str,
        core_src: str,
        node_name: str = ...,
        node_yaml: str = ...,
        dry_run: bool = ...,
    ) -> bool: ...


# endregion PROTOCOL_Deliverer


# region FUNC__core_src
## @purpose  Resolve core/ source dir for sync-core: env CORE_DIR override, else module-relative.
## @io  input: env CORE_DIR (optional), output: absolute core/ path
## @complexity  O(1) — env check + path join
def _core_src() -> str:
    """Resolve core/ source dir: ${CORE_DIR} override, else two levels up from this module."""
    return os.environ.get("CORE_DIR") or str((pathlib.Path(__file__).parent / ".." / "..").resolve())


# endregion FUNC__core_src


# region FUNC__prepare_ssh_opts
## @purpose  Mirror prepare_ssh_opts() from scp-deliver.sh: ssh-keygen -R только в init mode.
## @io  input: host (str), mode (str, default "update"), runner (CommandRunner | None, DI)
##      output: None (side-effect: known_hosts)
## @complexity  O(1) — branch + optional subprocess
## @invariants  update mode (execute_* контекст) НЕ запускает ssh-keygen -R (honest TOFU,
##              TRAP[DECISION] 2026-07-18 M7/G4: known_hosts init-only)
## @changes 2026-08-13 | E1 (160): +runner DI-параметр — runner=None → subprocess.run (default),
##            runner задан → runner.run (fake в тестах; поведение/exit-коды не изменены)
def _prepare_ssh_opts(host: str, mode: str = "update", *, runner: CommandRunner | None = None) -> None:
    """Prepare SSH options: clean host key only in init mode (mirror scp-deliver.sh)."""
    if mode == "init":
        logger.info("[IMP:8][prepare_ssh_opts][ssh] Cleaning SSH host key for %s (mode=init)", host)
        # W4d-канон (core_deliverer._run_cmd): условный DI-шов — дефолт — subprocess.run
        # subprocess.run (вывод в stdio), runner задан (тесты) → runner.run (fake scripted).
        if runner is None:
            subprocess.run(["ssh-keygen", "-R", host], capture_output=True, check=False, timeout=SSH_CONNECT_TIMEOUT)
        else:
            runner.run(["ssh-keygen", "-R", host], timeout=SSH_CONNECT_TIMEOUT, check=False)
    else:
        logger.info("[IMP:8][prepare_ssh_opts][ssh] Preserving known_hosts for %s (mode=%s)", host, mode)


# endregion FUNC__prepare_ssh_opts


# region CLS_RemoteExecutor
class RemoteExecutor:
    """Execute remote node commands over SSH — full orchestration cycle (DevPlan 101)."""

    # region FUNC___init__
    ## @purpose  Конструктор с DI: runner/facts/deliverer параметры (W4b/W4d канон, E1;
    ##            DevPlan 167 D3 — deliverer) — ленивые defaults (default_command_runner()/
    ##            default_env_facts()/module-functions) сохраняют prod-поведение.
    ## @io  input: dry_run: bool, runner: CommandRunner | None, facts: EnvironmentFacts | None,
    ##      deliverer: object | None (namespace с resolve_node_yaml/extract_node_host/
    ##      sync_core_to_vps — DI-объект, тесты без monkeypatch.setattr)
    ##      output: None
    ## @complexity  O(1)
    ## @changes 2026-08-13 | E1 (160): +runner/facts — тесты передают FakeCommandRunner/FakeFacts
    ##            вместо monkeypatch subprocess.run/os.path.isfile (VPS self-SSH detect)
    ## @changes 2026-08-14 | 167 D3: +deliverer — тесты передают FakeDeliverer вместо
    ##            monkeypatch.setattr(remote_executor, resolve_node_yaml/extract_node_host/
    ##            sync_core_to_vps) на уровне модуля
    def __init__(
        self,
        dry_run: bool = False,
        *,
        runner: CommandRunner | None = None,
        facts: EnvironmentFacts | None = None,
        deliverer: Deliverer | None = None,
    ) -> None:
        """Create executor with dry-run flag (печатает команды, не выполняет)."""
        self.dry_run = dry_run
        self._runner = runner
        self._facts = facts
        self._deliverer = deliverer

    # endregion FUNC___init__

    # region FUNC__resolve_host
    ## @purpose  Resolve node.yaml path + extract SSH host (делегирует overlay_deliverer).
    ## @io  input: node_name (str), output: (yaml_path, host); host может быть "" (нет хоста)
    ## @raises NodeYamlNotFoundError  node.yaml не найден или не парсится
    ## @complexity  O(n) — делегирует NodeYaml.resolve() (≤4 кандидата)
    ## ⚠️ TRAP[BUG] · 2026-08-03 · P1 · локальный поиск через PLATFORM_ROOT (RC 121 e2e)
    ## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · resolve/extract через deliverer-namespace (167 D3)
    ## · Rejected: прямой module-level вызов resolve_node_yaml/extract_node_host
    ## · Reason: seam = тестируемость реального resolve-вызова (тест передаёт FakeDeliverer
    ## ·   вместо monkeypatch.setattr на уровне модуля; прод — module fallback без изменений)
    ## · Rev: если overlay_deliverer станет объектом/классом — deliverer станет его инстансом
    def _resolve_host(self, node_name: str) -> tuple[str, str]:
        """Resolve node.yaml and extract SSH host. Returns (yaml_path, host_or_empty)."""
        if self._deliverer is not None:
            resolve_fn = self._deliverer.resolve_node_yaml
            extract_fn = self._deliverer.extract_node_host
        else:
            resolve_fn = resolve_node_yaml
            extract_fn = extract_node_host
        # Локальный поиск node.yaml: PLATFORM_ROOT env (make передаёт) → /opt/platform.
        # deploy_paths.platform_remote_base() — REMOTE-база (PLATFORM_ROOT исключён из цепочки).
        yaml_path = resolve_fn(
            node_name,
            platform_root=os.environ.get("PLATFORM_ROOT") or str(DEFAULT_REMOTE_PLATFORM),
        )
        host = extract_fn(yaml_path)
        return yaml_path, host

    # endregion FUNC__resolve_host

    # region FUNC__ssh_exec
    ## @purpose  SSH exec mirror lib/ssh.sh ssh_exec: subprocess.run + timeout, exit 124 на таймаут.
    ##           REF-0007: stdin_payload непуст → remote-команда = `bash -s`, скрипт (secret-prelude
    ##           + тело) уходит в stdin — секреты не попадают в argv локального ssh и remote shell.
    ## @io  input: host (str), remote_cmd (str), stdin_payload (str, "" = legacy argv-режим),
    ##      output: exit code (0/124/rc)
    ## @complexity  O(1) — один SSH-вызов с timeout wrapper
    ## @invariants  stream-вывод (наследует stdio как shell-версия — оператор видит remote-логи);
    ##              runner задан (тесты) → runner.run (fake scripted; stream-семантика не нужна);
    ##              stdin_payload НЕ логируется (содержит значения ключей)
    ## @changes 2026-08-13 | E1 (160): +DI-канал self._runner — W4d-канон условного шва:
    ##            runner=None → subprocess.run (default, stdio-наследование сохранено);
    ##            runner задан → runner.run(cmd, timeout=...)
    ## @changes 2026-08-24 | REF-0007: +stdin_payload — транспорт ключей вне argv (`bash -s`)
    def _ssh_exec(self, host: str, remote_cmd: str, stdin_payload: str = "") -> int:
        """Run ssh root@host with DEPLOY_TIMEOUT (900s). Returns 0/124/propagated rc."""
        if stdin_payload:
            # REF-0007: скрипт (prelude+тело) в stdin; в argv только `bash -s`
            cmd = ["ssh", *SSH_OPTS, f"root@{host}", "bash -s"]
            script = f"{stdin_payload}\n{remote_cmd}\n"
        else:
            cmd = ["ssh", *SSH_OPTS, f"root@{host}", remote_cmd]
            script = ""
        logger.info("[IMP:7][ssh_exec][exec] Starting: timeout %ss ssh root@%s (mode=deploy)", SSH_EXEC_TIMEOUT, host)

        # DI-шов: замыкание читает cmd/script/self — извлечение наружу ломает инкапсуляцию
        def _invoke() -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
            """Single ssh invocation (default subprocess vs DI-runner; ±stdin payload)."""
            if self._runner is None:
                if script:
                    return subprocess.run(cmd, input=script, text=True, timeout=SSH_EXEC_TIMEOUT, check=False)
                return subprocess.run(cmd, timeout=SSH_EXEC_TIMEOUT, check=False)
            if script:
                # DI-канон W4d: фейки принимают superset kwargs (tests/helpers/fakes.py)
                runner_run = cast("Callable[..., object]", self._runner.run)
                result = runner_run(cmd, timeout=SSH_EXEC_TIMEOUT, input=script)
                return cast("subprocess.CompletedProcess[str]", result)
            # Протокол CommandRunner аннотирован bare CompletedProcess (bytes-default) —
            # фактический контент стримится в stdio, типизация канала не меняется.
            result = self._runner.run(cmd, timeout=SSH_EXEC_TIMEOUT)
            return cast("subprocess.CompletedProcess[str]", result)

        try:
            r = _invoke()
        except subprocess.TimeoutExpired:
            logger.info("[IMP:10][ssh_exec][timeout] TIMEOUT: root@%s — %ss exceeded", host, SSH_EXEC_TIMEOUT)
            return 124
        if r.returncode == 0:
            logger.info("[IMP:9][ssh_exec][exec] OK: root@%s — command completed", host)
        else:
            logger.info("[IMP:7][ssh_exec][exec] FAIL: root@%s — exit=%s", host, r.returncode)
        return r.returncode

    # endregion FUNC__ssh_exec

    # region FUNC_execute_update
    ## @purpose  Полный цикл node-update: resolve → VPS detect → prepare opts → sync-core → ssh exec.
    ## @io  input: node_name, remote_cmd (build_update_ssh_cmd output), passthrough_args (info),
    ##      secret_prelude (REF-0007: export-скрипт для ssh-stdin; "" = нет секретов);
    ##      output: exit code 0=success, 1=fatal, 2=local fallback, 124=timeout
    ## @complexity  O(f + m) — f=файлы rsync (sync-core), m=metadata; ssh exec O(1)
    ## @invariants  sync-core обязателен ДО ssh exec (TRAP[BUG] P0 ported 2026-07-24)
    ##              DRY_RUN: sync-core --dry-run + печать ssh-команды, exit 0
    ##              REF-0007: secret_prelude НЕ логируется и НЕ попадает в argv (`bash -s`)
    def execute_update(
        self, node_name: str, remote_cmd: str, passthrough_args: str = "", *, secret_prelude: str = ""
    ) -> int:
        """Execute remote node update (resolve → VPS detect → sync-core → ssh)."""
        try:
            yaml_path, host = self._resolve_host(node_name)
        except NodeYamlNotFoundError as exc:
            logger.info(
                "[IMP:10][execute_update][resolve] FATAL: Cannot resolve node.yaml for node=%s: %s", node_name, exc
            )
            return 1
        if passthrough_args:
            logger.info("[IMP:8][execute_update][input] passthrough args: %s", passthrough_args)
        if not host:
            logger.info("[IMP:9][execute_update][resolve] No SSH host — local fallback")
            return 2
        # ⚠️ TRAP[BUG] P0 — VPS self-SSH loop (см. MODULE_CONTRACT): мы уже на VPS → local exec
        # E1 (160): facts.path_isfile — DI (тесты: FakeFacts вместо monkeypatch os.path.isfile)
        if (self._facts or default_env_facts()).path_isfile(VPS_NODE_LIFECYCLE):
            logger.info("[IMP:9][execute_update][vps-detect] Local VPS detected — skipping SSH proxy")
            return 2
        logger.info("[IMP:9][execute_update][resolve] SSH host: %s — REMOTE update", host)
        _prepare_ssh_opts(host, "update", runner=self._runner)

        core_src = _core_src()
        # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · sync-core через deliverer-namespace (167 D3)
        # · Rejected: прямой module-level вызов sync_core_to_vps
        # · Reason: seam = тестируемость реального sync-core вызова (FakeDeliverer.sync_core_to_vps —
        # ·   Mock, тест ассертит вызовы; прод — module fallback без изменений)
        # · Rev: если overlay_deliverer станет объектом/классом — deliverer станет его инстансом
        sync_fn = self._deliverer.sync_core_to_vps if self._deliverer is not None else sync_core_to_vps
        try:
            if self.dry_run:
                sync_fn(host, core_src, node_name, yaml_path, dry_run=True)
            else:
                sync_fn(host, core_src, node_name, yaml_path)
        except SyncCoreError as exc:
            logger.info("[IMP:10][execute_update][sync-core] FATAL: sync-core failed: %s", exc)
            return 1

        if self.dry_run:
            logger.info("[IMP:8][execute_update][dry-run] DRY-RUN: ssh ... root@%s", host)
            return 0
        logger.info("[IMP:9][execute_update][ssh] Executing node-lifecycle.sh --mode update on root@%s", host)
        return self._ssh_exec(host, remote_cmd, secret_prelude)

    # endregion FUNC_execute_update

    # region FUNC_execute_converge
    ## @purpose  Удалённый converge: resolve → prepare opts → ssh exec. БЕЗ sync-core (по плану 3.2).
    ## @io  input: node_name, remote_cmd (build_converge_ssh_cmd output), passthrough_args (info);
    ##      output: exit code 0/1/2/124
    ## @complexity  O(1) — resolve + ssh exec
    ## @invariants  НЕ вызывает sync_core_to_vps (в отличие от execute_update) — converge не доставляет core
    ## ⚠️ TRAP[BUG] · 2026-08-03 · P1 · VPS self-SSH detect добавлен (RC 121 e2e)
    ## · Symptom: make converge на dev → ДВОЙНОЙ прогон reconcile (ssh на себя: локальный external →
    ##   ssh → remote external → self-ssh → remote external → local fallback) — R2 мутировал дважды.
    ## · Root: execute_converge не имел VPS-self-detect (в отличие от execute_update) — на VPS
    ##   remote external converge.sh снова ssh'ил на себя.
    ## · Fix: тот же VPS_NODE_LIFECYCLE check → return 2 (local fallback на VPS).
    def execute_converge(self, node_name: str, remote_cmd: str, passthrough_args: str = "") -> int:
        """Execute remote converge (resolve → prepare opts → ssh exec, no sync-core)."""
        try:
            _, host = self._resolve_host(node_name)
        except NodeYamlNotFoundError as exc:
            logger.info(
                "[IMP:10][execute_converge][resolve] FATAL: Cannot resolve node.yaml for node=%s: %s", node_name, exc
            )
            return 1
        if passthrough_args:
            logger.info("[IMP:8][execute_converge][input] passthrough args: %s", passthrough_args)
        if not host:
            logger.info("[IMP:9][execute_converge][resolve] No SSH host — local fallback")
            return 2
        # ⚠️ TRAP[BUG] 2026-08-03 (RC 121): self-SSH loop — мы уже на VPS → local exec
        # E1 (160): facts.path_isfile — DI (тесты: FakeFacts вместо monkeypatch os.path.isfile)
        if (self._facts or default_env_facts()).path_isfile(VPS_NODE_LIFECYCLE):
            logger.info("[IMP:9][execute_converge][vps-detect] Local VPS detected — skipping SSH proxy")
            return 2
        logger.info("[IMP:9][execute_converge][resolve] SSH host: %s — REMOTE converge", host)
        _prepare_ssh_opts(host, "update", runner=self._runner)

        if self.dry_run:
            logger.info("[IMP:8][execute_converge][dry-run] DRY-RUN: ssh ... root@%s", host)
            return 0
        logger.info("[IMP:9][execute_converge][ssh] Executing converge.sh on root@%s", host)
        return self._ssh_exec(host, remote_cmd)

    # endregion FUNC_execute_converge

    # region FUNC_execute_deploy_context
    ## @purpose  Удалённый deploy-context: resolve → VPS self-detect → ssh exec. БЕЗ sync-core
    ##           (как execute_converge): deploy-context не доставляет core, только деплоит
    ##           проекты контекста и обновляет vhost'ы на ноде (DevPlan 153 T7, N3).
    ## @io  input: node_name, remote_cmd (build_converge_ssh_cmd-style output), passthrough_args (info);
    ##      output: exit code 0/1/2/124
    ## @complexity  O(1) — resolve + ssh exec
    ## @invariants  НЕ вызывает sync_core_to_vps (в отличие от execute_update)
    ##              VPS self-SSH detect → return 2 (local fallback на VPS, паттерн execute_converge)
    def execute_deploy_context(self, node_name: str, remote_cmd: str, passthrough_args: str = "") -> int:
        """Execute remote deploy-context (resolve → prepare opts → ssh exec, no sync-core)."""
        try:
            _, host = self._resolve_host(node_name)
        except NodeYamlNotFoundError as exc:
            logger.info(
                "[IMP:10][execute_deploy_context][resolve] FATAL: Cannot resolve node.yaml for node=%s: %s",
                node_name,
                exc,
            )
            return 1
        if passthrough_args:
            logger.info("[IMP:8][execute_deploy_context][input] passthrough args: %s", passthrough_args)
        if not host:
            logger.info("[IMP:9][execute_deploy_context][resolve] No SSH host — local fallback")
            return 2
        # ⚠️ TRAP[BUG] 2026-08-03 (RC 121): self-SSH loop — мы уже на VPS → local exec
        # E1 (160): facts.path_isfile — DI (тесты: FakeFacts вместо monkeypatch os.path.isfile)
        if (self._facts or default_env_facts()).path_isfile(VPS_NODE_LIFECYCLE):
            logger.info("[IMP:9][execute_deploy_context][vps-detect] Local VPS detected — skipping SSH proxy")
            return 2
        logger.info("[IMP:9][execute_deploy_context][resolve] SSH host: %s — REMOTE deploy-context", host)
        _prepare_ssh_opts(host, "update", runner=self._runner)

        if self.dry_run:
            logger.info("[IMP:8][execute_deploy_context][dry-run] DRY-RUN: ssh ... root@%s", host)
            return 0
        logger.info("[IMP:9][execute_deploy_context][ssh] Executing context_deployer on root@%s", host)
        return self._ssh_exec(host, remote_cmd)

    # endregion FUNC_execute_deploy_context

    # region FUNC_execute_reconcile
    ## @purpose  Reconcile ≡ converge + --reconcile в remote_cmd (флаг добавляет shell
    ##           build_converge_ssh_cmd "node" "--reconcile" ...). Делегирует execute_converge.
    ## @io  input: node_name, remote_cmd (уже содержит --reconcile), passthrough_args (info);
    ##      output: exit code 0/1/2/124
    ## @complexity  O(1) — делегирование
    ## @invariants  remote_cmd строится ТОЛЬКО в shell (D3 printf %q) — Python не модифицирует команду
    def execute_reconcile(self, node_name: str, remote_cmd: str, passthrough_args: str = "") -> int:
        """Execute remote reconcile (≡ converge, --reconcile уже внутри remote_cmd)."""
        if "--reconcile" in remote_cmd:
            logger.info("[IMP:9][execute_reconcile][input] --reconcile flag present in remote_cmd")
        else:
            logger.info("[IMP:8][execute_reconcile][input] WARN: --reconcile missing from remote_cmd")
        return self.execute_converge(node_name, remote_cmd, passthrough_args)

    # endregion FUNC_execute_reconcile

    # region FUNC_execute_check_security
    ## @purpose  Удалённая security-проверка: resolve → VPS self-detect → ssh exec security_posture.py.
    ##           БЕЗ sync-core (read-only диагностика — remote core уже доставлен, DevPlan 134 D3).
    ## @io  input: node_name, remote_cmd (build_check_security_ssh_cmd output), passthrough_args (info);
    ##      output: exit code 0/1/2/124 (0=healthy 1=warnings 2=errors)
    ## @complexity  O(1) — resolve + ssh exec
    ## @invariants  НЕ вызывает sync_core_to_vps (зеркало execute_converge)
    ##              VPS self-SSH detect — тот же VPS_NODE_LIFECYCLE probe (TRAP[BUG] RC 121)
    def execute_check_security(self, node_name: str, remote_cmd: str, passthrough_args: str = "") -> int:
        """Execute remote security posture check (resolve → VPS detect → ssh exec, no sync-core)."""
        try:
            _, host = self._resolve_host(node_name)
        except NodeYamlNotFoundError as exc:
            logger.info(
                "[IMP:10][execute_check_security][resolve] FATAL: Cannot resolve node.yaml for node=%s: %s",
                node_name,
                exc,
            )
            return 1
        if passthrough_args:
            logger.info("[IMP:8][execute_check_security][input] passthrough args: %s", passthrough_args)
        if not host:
            logger.info("[IMP:9][execute_check_security][resolve] No SSH host — local fallback")
            return 2
        # ⚠️ TRAP[BUG] RC 121: self-SSH loop — мы уже на VPS → local exec
        # E1 (160): facts.path_isfile — DI (тесты: FakeFacts вместо monkeypatch os.path.isfile)
        if (self._facts or default_env_facts()).path_isfile(VPS_NODE_LIFECYCLE):
            logger.info("[IMP:9][execute_check_security][vps-detect] Local VPS detected — skipping SSH proxy")
            return 2
        logger.info("[IMP:9][execute_check_security][resolve] SSH host: %s — REMOTE check", host)
        _prepare_ssh_opts(host, "update", runner=self._runner)

        if self.dry_run:
            logger.info("[IMP:8][execute_check_security][dry-run] DRY-RUN: ssh ... root@%s", host)
            return 0
        logger.info("[IMP:9][execute_check_security][ssh] Executing security_posture.py on root@%s", host)
        return self._ssh_exec(host, remote_cmd)

    # endregion FUNC_execute_check_security


# endregion CLS_RemoteExecutor


# region FUNC_cli
## @purpose  CLI entrypoint: execute-update | execute-converge | execute-reconcile | execute-check-security.
##           Argparse: --node, --remote-cmd, --dry-run, --passthrough-args.
## @io  input: argv (Optional[list[str]], default sys.argv[1:]), output: exit code int
## @complexity  O(1) — dispatch-only
def cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns exit code (0/1/2/124) — sys.exit handled by __main__."""
    p = argparse.ArgumentParser(description="remote_executor — execute remote node commands over SSH")
    sp = p.add_subparsers(dest="command", required=True)

    for name in (
        "execute-update",
        "execute-converge",
        "execute-reconcile",
        "execute-check-security",
        "execute-deploy-context",
    ):
        c = sp.add_parser(name, help=f"Execute remote {name.removeprefix('execute-')} command")
        c.add_argument("--node", required=True, dest="node_name")
        c.add_argument("--remote-cmd", required=True, dest="remote_cmd")
        c.add_argument("--dry-run", action="store_true")
        c.add_argument("--passthrough-args", default="", dest="passthrough_args")

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3)."""

        def __init__(self) -> None:
            super().__init__()
            self.command: str
            self.node_name: str
            self.remote_cmd: str
            self.dry_run: bool
            self.passthrough_args: str

    args = p.parse_args(argv, namespace=_CliArgs())
    executor = RemoteExecutor(dry_run=args.dry_run)
    if args.command == "execute-update":
        return executor.execute_update(args.node_name, args.remote_cmd, args.passthrough_args)
    if args.command == "execute-converge":
        return executor.execute_converge(args.node_name, args.remote_cmd, args.passthrough_args)
    if args.command == "execute-check-security":
        return executor.execute_check_security(args.node_name, args.remote_cmd, args.passthrough_args)
    if args.command == "execute-deploy-context":
        return executor.execute_deploy_context(args.node_name, args.remote_cmd, args.passthrough_args)
    return executor.execute_reconcile(args.node_name, args.remote_cmd, args.passthrough_args)


# endregion FUNC_cli


if __name__ == "__main__":
    sys.exit(cli())
