#!/usr/bin/env python3
# GREP_SUMMARY: remote-executor execute-update execute-converge execute-reconcile VPS-self-SSH sync-core ssh-exec DRY_RUN exit-codes remote-cmd
# STRUCTURE: ▶ CLI(3 subcmds) → RemoteExecutor → resolve+extract → ◇ host?→2 | ◇ VPS self-SSH→2 → prepare_ssh_opts(update) → ◇ update?→sync-core → ◇ dry-run?→print→0 → ⚡ ssh subprocess.run(timeout=600) → ⎋ exit 0|1|2|124
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
##              — ssh через subprocess.run(["ssh", *SSH_OPTS, f"root@{host}", remote_cmd], timeout=600)
## @rationale SRP (DevPlan 101 D2): overlay_deliverer (421 LOC) = доставка, remote_executor = исполнение.
##            D4: Python не вызывает shell-функцию ssh_exec — subprocess.run + timeout зеркалит поведение
##            (exit=124 → timeout). Логика без set -e → TRAP[BUG] P4 (bare ssh_exec under set -e) не релевантен.
## @changes 2026-07-31 | DevPlan 101 TASK-2 — Initial implementation (Wave 5d Strangler-Fig завершение)
# ⚠️ TRAP[BUG] · 2026-07-23 · P0 · VPS self-SSH loop: detect /opt/platform/ → local exec
# · Symptom: `make node-update` на самом VPS зацикливался — execute_remote_update видел SSH host
# ·   из node.yaml, подключался сам к себе и гонял remote-фазы повторно.
# · Fix: перед SSH proxy проверить наличие /opt/platform/core/internal/bootstrap/node-lifecycle.sh
# ·   локально → если есть, мы уже на VPS → return 2 (local fallback).
# · Prevention: любая remote-оркестрация обязана проверять VPS self-SSH ДО sync-core/ssh.
# · Migrated from remote-cmd.sh:164-167 (DevPlan 101 TASK-2).
# ⚠️ TRAP[BUG] · 2026-07-24 · P4 · bare ssh_exec may silently fail under set -e
# · Symptom: shell ssh_exec вызывался без || catch → при set -e ошибка ssh молча роняла скрипт.
# · Fix: Python subprocess.run(check=False) + явная обработка returncode (0/124/non-zero).
# ·   Нет set -e в Python — P4-контекст не применим (DevPlan 101 D4).
# · Migrated from remote-cmd.sh:188 (DevPlan 101 TASK-2).
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import subprocess
import sys

from core.internal.bootstrap.overlay_deliverer import (
    SSH_OPTS,
    NodeYamlNotFoundError,
    SyncCoreError,
    extract_node_host,
    resolve_node_yaml,
    sync_core_to_vps,
)

logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

# Mirror lib/ssh.sh ssh_exec deploy-mode default (600s) + TRAP[DECISION] 2026-07-21
SSH_EXEC_TIMEOUT = 600

# VPS self-SSH marker — та же проверка, что remote-cmd.sh:165 (локальный filesystem probe)
VPS_NODE_LIFECYCLE = "/opt/platform/core/internal/bootstrap/node-lifecycle.sh"


# region FUNC__core_src
## @purpose  Resolve core/ source dir for sync-core: env CORE_DIR override, else module-relative.
## @io  input: env CORE_DIR (optional), output: absolute core/ path
## @complexity  O(1) — env check + path join
def _core_src() -> str:
    """Resolve core/ source dir: ${CORE_DIR} override, else two levels up from this module."""
    return os.environ.get("CORE_DIR") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# endregion FUNC__core_src


# region FUNC__prepare_ssh_opts
## @purpose  Mirror prepare_ssh_opts() from scp-deliver.sh: ssh-keygen -R только в init mode.
## @io  input: host (str), mode (str, default "update"), output: None (side-effect: known_hosts)
## @complexity  O(1) — branch + optional subprocess
## @invariants  update mode (execute_* контекст) НЕ запускает ssh-keygen -R (honest TOFU,
##              TRAP[DECISION] 2026-07-18 M7/G4: known_hosts init-only)
def _prepare_ssh_opts(host: str, mode: str = "update") -> None:
    """Prepare SSH options: clean host key only in init mode (mirror scp-deliver.sh)."""
    if mode == "init":
        logger.info("[IMP:8][prepare_ssh_opts][ssh] Cleaning SSH host key for %s (mode=init)", host)
        subprocess.run(["ssh-keygen", "-R", host], capture_output=True, check=False, timeout=30)
    else:
        logger.info("[IMP:8][prepare_ssh_opts][ssh] Preserving known_hosts for %s (mode=%s)", host, mode)


# endregion FUNC__prepare_ssh_opts


# region CLS_RemoteExecutor
class RemoteExecutor:
    """Execute remote node commands over SSH — full orchestration cycle (DevPlan 101)."""

    # region FUNC___init__
    def __init__(self, dry_run: bool = False) -> None:
        """Create executor with dry-run flag (печатает команды, не выполняет)."""
        self.dry_run = dry_run

    # endregion FUNC___init__

    # region FUNC__resolve_host
    ## @purpose  Resolve node.yaml path + extract SSH host (делегирует overlay_deliverer).
    ## @io  input: node_name (str), output: (yaml_path, host); host может быть "" (нет хоста)
    ## @raises NodeYamlNotFoundError  node.yaml не найден или не парсится
    ## @complexity  O(n) — делегирует NodeYaml.resolve() (≤4 кандидата)
    def _resolve_host(self, node_name: str) -> tuple[str, str]:
        """Resolve node.yaml and extract SSH host. Returns (yaml_path, host_or_empty)."""
        yaml_path = resolve_node_yaml(node_name)
        host = extract_node_host(yaml_path)
        return yaml_path, host

    # endregion FUNC__resolve_host

    # region FUNC__ssh_exec
    ## @purpose  SSH exec mirror lib/ssh.sh ssh_exec: subprocess.run + timeout, exit 124 на таймаут.
    ## @io  input: host (str), remote_cmd (str), output: exit code (0/124/rc)
    ## @complexity  O(1) — один SSH-вызов с timeout wrapper
    ## @invariants  stream-вывод (наследует stdio как shell-версия — оператор видит remote-логи)
    def _ssh_exec(self, host: str, remote_cmd: str) -> int:
        """Run ssh root@host remote_cmd with 600s timeout. Returns 0/124/propagated rc."""
        cmd = ["ssh", *SSH_OPTS, f"root@{host}", remote_cmd]
        logger.info("[IMP:7][ssh_exec][exec] Starting: timeout %ss ssh root@%s (mode=deploy)", SSH_EXEC_TIMEOUT, host)
        try:
            r = subprocess.run(cmd, timeout=SSH_EXEC_TIMEOUT, check=False)
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
    ## @io  input: node_name, remote_cmd (build_update_ssh_cmd output), passthrough_args (info);
    ##      output: exit code 0=success, 1=fatal, 2=local fallback, 124=timeout
    ## @complexity  O(f + m) — f=файлы rsync (sync-core), m=metadata; ssh exec O(1)
    ## @invariants  sync-core обязателен ДО ssh exec (TRAP[BUG] P0 ported 2026-07-24)
    ##              DRY_RUN: sync-core --dry-run + печать ssh-команды, exit 0
    def execute_update(self, node_name: str, remote_cmd: str, passthrough_args: str = "") -> int:
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
        if os.path.isfile(VPS_NODE_LIFECYCLE):
            logger.info("[IMP:9][execute_update][vps-detect] Local VPS detected — skipping SSH proxy")
            return 2
        logger.info("[IMP:9][execute_update][resolve] SSH host: %s — REMOTE update", host)
        _prepare_ssh_opts(host, "update")

        core_src = _core_src()
        try:
            if self.dry_run:
                sync_core_to_vps(host, core_src, node_name, yaml_path, dry_run=True)
            else:
                sync_core_to_vps(host, core_src, node_name, yaml_path)
        except SyncCoreError as exc:
            logger.info("[IMP:10][execute_update][sync-core] FATAL: sync-core failed: %s", exc)
            return 1

        if self.dry_run:
            logger.info("[IMP:8][execute_update][dry-run] DRY-RUN: ssh ... root@%s", host)
            return 0
        logger.info("[IMP:9][execute_update][ssh] Executing node-lifecycle.sh --mode update on root@%s", host)
        return self._ssh_exec(host, remote_cmd)

    # endregion FUNC_execute_update

    # region FUNC_execute_converge
    ## @purpose  Удалённый converge: resolve → prepare opts → ssh exec. БЕЗ sync-core (по плану 3.2).
    ## @io  input: node_name, remote_cmd (build_converge_ssh_cmd output), passthrough_args (info);
    ##      output: exit code 0/1/2/124
    ## @complexity  O(1) — resolve + ssh exec
    ## @invariants  НЕ вызывает sync_core_to_vps (в отличие от execute_update) — converge не доставляет core
    ##              VPS self-SSH detect отсутствует (как в shell execute_remote_converge)
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
        logger.info("[IMP:9][execute_converge][resolve] SSH host: %s — REMOTE converge", host)
        _prepare_ssh_opts(host, "update")

        if self.dry_run:
            logger.info("[IMP:8][execute_converge][dry-run] DRY-RUN: ssh ... root@%s", host)
            return 0
        logger.info("[IMP:9][execute_converge][ssh] Executing converge.sh on root@%s", host)
        return self._ssh_exec(host, remote_cmd)

    # endregion FUNC_execute_converge

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


# endregion CLS_RemoteExecutor


# region FUNC_cli
## @purpose  CLI entrypoint: execute-update | execute-converge | execute-reconcile.
##           Argparse: --node, --remote-cmd, --dry-run, --passthrough-args.
## @io  input: argv (Optional[list[str]], default sys.argv[1:]), output: exit code int
## @complexity  O(1) — dispatch-only
def cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns exit code (0/1/2/124) — sys.exit handled by __main__."""
    p = argparse.ArgumentParser(description="remote_executor — execute remote node commands over SSH")
    sp = p.add_subparsers(dest="command", required=True)

    for name in ("execute-update", "execute-converge", "execute-reconcile"):
        c = sp.add_parser(name, help=f"Execute remote {name.removeprefix('execute-')} command")
        c.add_argument("--node", required=True, dest="node_name")
        c.add_argument("--remote-cmd", required=True, dest="remote_cmd")
        c.add_argument("--dry-run", action="store_true")
        c.add_argument("--passthrough-args", default="", dest="passthrough_args")

    args = p.parse_args(argv)
    executor = RemoteExecutor(dry_run=args.dry_run)
    if args.command == "execute-update":
        return executor.execute_update(args.node_name, args.remote_cmd, args.passthrough_args)
    if args.command == "execute-converge":
        return executor.execute_converge(args.node_name, args.remote_cmd, args.passthrough_args)
    return executor.execute_reconcile(args.node_name, args.remote_cmd, args.passthrough_args)


# endregion FUNC_cli


if __name__ == "__main__":
    sys.exit(cli())
