#!/usr/bin/env python3
# GREP_SUMMARY: overlay deliverer vhost rsync node-yaml resolve extract host core delivery remote-cmd strangler
# STRUCTURE: ▶ ┌node_name┐ → ○ resolve_node_yaml(3-path) → ○ extract_node_host(pyyaml) → ◇ synccore(rsync) → ◇ deliver(mkdir+rsync) → ⎋ CLI(4 subcmds)
# region MODULE_CONTRACT
## @purpose  Resolve node.yaml paths, extract SSH hosts, deliver vhost overlays, sync core/ to VPS.
##           Python-порт deliver_vhost_overlays() + resolve/extract из remote-cmd.sh (Wave 5d).
## @scope    resolve_node_yaml() 3-path search; extract_node_host() pyyaml; sync_core_to_vps() rsync;
##           deliver_vhost_overlays() full pipeline; CLI (resolve-node|extract-host|sync-core|deliver)
## @invariants — 3-path search: platform-local → org repos → VPS fallback
##              — extract_node_host returns "" if host absent (not error)
##              — Dry-run: prints commands to stderr, does NOT execute
##              — SSH_OPTS — единый SoT из shared/ssh_opts.py (DevPlan 116 B5 T2, D1)
## @rationale Strangler-Fig: remote-cmd.sh 672→~230 LOC shell facade. Business logic → unit-testable Python.
## @changes 2026-07-26 | TASK-036D — Initial implementation (Wave 5d Strangler-Fig)
##           2026-07-31 | DevPlan 108 — sync_core_to_vps делегирует core/ rsync в
##                       core_deliverer.deliver_core(); dead exclude-const удалён
##           2026-08-01 | DevPlan 116 B5 T2 — SSH_OPTS/_ssh_e → shared/ssh_opts.py (D1);
##                      rsync/ssh timeouts → shared/timeouts.py (U-11)
# ⚠️ TRAP[BUG] · 2026-07-24 · P0 · node-update не доставлял core/ на VPS
# · Ported from remote-cmd.sh:294. Fix: rsync core/ + node.yaml before remote exec.
# · Prevention: always call sync_core_to_vps() before remote exec in node-update.
# 🧐 TRAP[DECISION] · 2026-07-26 · — · printf %q builders stay in shell (D3). Rejected: shlex.quote() ≠ printf '%q'.
# endregion MODULE_CONTRACT

import argparse
import logging
import pathlib
import subprocess
import sys

# DevPlan 108 F3: sync_core_to_vps делегирует core/ rsync в core_deliverer.deliver_core()
# (DRY-унификация двойного core/ rsync, P2/D3). Направление импорта overlay → core — без цикла.
from core.internal.bootstrap.core_deliverer import CoreDeliveryError, deliver_core

# DevPlan 118 C7: /opt/node-configs — единый резолвер shared/deploy_paths.node_configs_remote().
# DevPlan 119 B3: /opt/platform — единый резолвер shared/deploy_paths.platform_remote_base().
from core.internal.shared.deploy_paths import node_configs_remote, platform_remote_base

# REF-0107: Config*Error — ТОЛЬКО канонический импорт-путь shared.exceptions (детектор
# exception-import-path; re-export node_yaml создаёт второй путь к тем же классам).
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml

# DevPlan 116 B5 T2 (D1): SSH_OPTS — единый SoT shared/ssh_opts.py (дублирующие копии устранены)
from core.internal.shared.ssh_opts import SSH_OPTS, build_rsync_ssh_opts

# DevPlan 116 B5 T1: rsync/ssh таймауты — единый реестр shared/timeouts.py (U-11)
from core.internal.shared.timeouts import RSYNC_TIMEOUT, SSH_CONNECT_TIMEOUT

logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)


# region EXC_OverlayDelivererError
class OverlayDelivererError(Exception):
    """Base exception for overlay_deliverer errors."""


# endregion EXC_OverlayDelivererError


# region EXC_NodeYamlNotFoundError
class NodeYamlNotFoundError(OverlayDelivererError):
    """Raised when node.yaml cannot be found in any search path."""


# endregion EXC_NodeYamlNotFoundError


# region EXC_SyncCoreError
class SyncCoreError(OverlayDelivererError):
    """Raised when rsync core/ delivery to VPS fails."""


# endregion EXC_SyncCoreError


# region EXC_DeliveryError
class DeliveryError(OverlayDelivererError):
    """Raised when overlay delivery (mkdir/rsync/ssh) fails."""


# endregion EXC_DeliveryError


# region FUNC_resolve_node_yaml
## @purpose  Search node.yaml via NodeYaml.resolve() (unified 3-path resolver).
## @io  input: node_name (str), platform_root (str)
##      output: resolved absolute path as str
## @complexity  O(n) where n = number of candidate paths (≤4) — delegates to NodeYaml.resolve()
## @invariants  Delegates to NodeYaml.resolve() — single source of truth per AC4.
##              Raises NodeYamlNotFoundError wrapping ConfigNotFoundError.
## @rationale  DRIFT-088-1 fix: own 3-path implementation was not migrated. Now delegates to
##             NodeYaml.resolve() which is the single canonical 3-path resolver (AC4).
# 🧐 TRAP[DECISION] · 2026-08-26 · — · projects_dir снят с цепочки (AI-0035, DevPlan 17 T7.5)
# · Rejected: оставить параметр с VESTIGIAL-аннотацией (byte-compat keep)
# · Reason: параметр нигде не читался (ложное заявление «sets PLATFORM_ROOT» удалено);
#   callers в репо не передавали его
# · Rev: если появится реальный consumer projects_dir — вернуть с реализацией
def resolve_node_yaml(
    node_name: str,
    platform_root: str = str(platform_remote_base()),
) -> str:
    """Search node.yaml via NodeYaml.resolve() (unified 3-path resolver).

    Delegates to the canonical NodeYaml.resolve() — single source of truth.

    @raises NodeYamlNotFoundError  If not found in any candidate path.
    """
    if not node_name:
        logger.info("[IMP:10][resolve_node_yaml][input] Missing node_name")
        msg = "Missing required argument: node_name"
        raise NodeYamlNotFoundError(msg)

    logger.info("[IMP:8][resolve_node_yaml][search] Resolving node.yaml for node=%s", node_name)
    try:
        resolved_config_dir: str = platform_root
        ny = NodeYaml.resolve(node_name=node_name, config_dir=resolved_config_dir)
        resolved = ny._path
        if resolved:
            logger.info("[IMP:9][resolve_node_yaml][result] Resolved: %s", resolved)
            return resolved
    except ConfigNotFoundError as exc:
        logger.info("[IMP:10][resolve_node_yaml][result] Not found for node=%s: %s", node_name, exc)
        msg = f"node.yaml not found for node={node_name}"
        raise NodeYamlNotFoundError(msg) from exc

    msg = f"node.yaml not found for node={node_name}"
    raise NodeYamlNotFoundError(msg)


# endregion FUNC_resolve_node_yaml


# region FUNC_extract_node_host
## @purpose  Extract node.host from node.yaml via NodeYaml. Returns "" if absent.
## @io  input: yaml_path (str), output: host string or "" (empty = no host)
## @complexity  O(1) — single NodeYaml lazy load + get
def extract_node_host(yaml_path: str) -> str:
    """Extract node.host from node.yaml via NodeYaml. Returns "" if absent."""
    if not yaml_path:
        msg = "Missing required argument: yaml_path"
        raise NodeYamlNotFoundError(msg)
    if not pathlib.Path(yaml_path).is_file():
        msg = f"File not found: {yaml_path}"
        raise NodeYamlNotFoundError(msg)

    logger.info("[IMP:8][extract_node_host][parse] Extracting host from: %s", yaml_path)
    try:
        node = NodeYaml(yaml_path)
        host = node.get("node.host", default="")
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        msg = f"Failed to parse YAML: {yaml_path}"
        raise NodeYamlNotFoundError(msg) from exc

    logger.info("[IMP:9][extract_node_host][result] Host: %s", host if host else "(empty)")
    return host


# endregion FUNC_extract_node_host


# region FUNC_sync_core_to_vps
## @purpose  Rsync core/ + optional node.yaml to remote VPS. Dry-run mode prints commands, does not execute.
##           core/ rsync ДЕЛЕГИРУЕТСЯ в core_deliverer.deliver_core() (DevPlan 108 F3) — единый источник.
## @io  input: host, core_src, node_name, node_yaml, dry_run (bool); output: bool success
## @complexity  O(f + m) where f = files rsynced, m = metadata (node.yaml) rsync
## @rationale  D3 DevPlan 108: ДВА независимых core/ rsync (scp-deliver Phase 1 + этот inline) породили
##             P1-дрейф remote base (TRAP[BUG] ниже). Делегирование сохраняет сигнатуру (host, core_src,
##             node_name, node_yaml, dry_run) → bool и исключение SyncCoreError → тесты test_sync_core_*
##             проходят без модификации. node.yaml rsync остаётся overlay-специфичной доставкой.
def sync_core_to_vps(host: str, core_src: str, node_name: str = "", node_yaml: str = "", dry_run: bool = False) -> bool:
    """Rsync core/ + optional node.yaml to remote VPS. Dry-run: print, don't exec.

    @raises SyncCoreError  On rsync failure.
    """
    if not host:
        msg = "Missing required argument: host"
        raise SyncCoreError(msg)
    if not core_src or not pathlib.Path(core_src).is_dir():
        msg = f"core_src not found: {core_src}"
        raise SyncCoreError(msg)

    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · ДВА независимых core/ rsync → ЕДИНЫЙ источник (DevPlan 108 F3)
    # · Symptom: `make node-update NODE=<host>` доставлял core в /opt/platform/core, а bootstrap —
    # ·   в ${PLATFORM_REMOTE_BASE:-${PLATFORM_ROOT:-/opt/platform}}/core → на VPS ДВЕ копии core;
    # ·   update-фазы выполнялись из чужого дерева (state.json от init не находил скриптов).
    # · Fix (DevPlan 108): ЕДИНАЯ точка резолюции remote base — core_deliverer.resolve_remote_base()
    # ·   (цепочка PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform, та же, что scp-deliver.sh:129).
    # ·   sync_core_to_vps ДЕЛЕГИРУЕТ core/ rsync в core_deliverer.deliver_core() — дублирование устранено.
    # · Prevention: любой код, доставляющий core на VPS, использует core_deliverer.deliver_core().
    # · Note (2026-08-03, DevPlan 123 T8): актуальная remote-цепочка — PLATFORM_REMOTE_BASE → /opt/platform
    # ·   (PLATFORM_ROOT УБРАН из remote-резолюции — TRAP[BUG] 2026-08-03 в deploy_paths.platform_remote_base);
    # ·   единый resolve_remote_base() сохраняется; история выше описывает состояние ДО фикса DevPlan 108.
    try:
        deliver_core(host=host, core_dir=core_src, remote_user="root", dry_run=dry_run)
    except CoreDeliveryError as exc:
        raise SyncCoreError(str(exc)) from exc

    ssh_e = build_rsync_ssh_opts()
    if dry_run:
        if node_yaml and pathlib.Path(node_yaml).is_file():
            logger.info(
                "[IMP:8][sync_core_to_vps][dry-run] DRY-RUN: rsync %s → root@%s:%s/%s/node.yaml",
                node_yaml,
                host,
                node_configs_remote(),
                node_name,
            )
        return True

    if node_yaml and pathlib.Path(node_yaml).is_file():
        cmd2 = ["rsync", "-avz", "-e", ssh_e, node_yaml, f"root@{host}:{node_configs_remote()}/{node_name}/node.yaml"]
        logger.info(
            "[IMP:9][sync_core_to_vps][exec] Rsyncing node.yaml → %s:%s/%s/", host, node_configs_remote(), node_name
        )
        r = subprocess.run(cmd2, capture_output=True, text=True, timeout=RSYNC_TIMEOUT, check=False)
        if r.returncode != 0:
            msg = f"rsync node.yaml failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
            raise SyncCoreError(msg)
        logger.info("[IMP:9][sync_core_to_vps][exec] node.yaml rsync complete")
    else:
        logger.info("[IMP:8][sync_core_to_vps][exec] SKIP node.yaml — not found")

    return True


# endregion FUNC_sync_core_to_vps


# region FUNC_deliver_vhost_overlays
## @purpose  Full overlay delivery pipeline: resolve → extract → check → dry-run/mkdir/rsync.
##           Graceful skip if no overlays, no host, or no .conf files.
## @io  input: node_name, platform_root, dry_run; output: bool success
## @complexity  O(n + f) where n = candidate paths, f = .conf files to rsync
def deliver_vhost_overlays(
    node_name: str, platform_root: str = str(platform_remote_base()), dry_run: bool = False
) -> bool:
    """Full overlay delivery: resolve → extract → check → dry-run/mkdir/rsync.

    Graceful skip if no overlays, no host, or no .conf files.
    @raises DeliveryError  On mkdir or rsync failure.
    """
    logger.info("[IMP:8][deliver_vhost_overlays][start] Starting delivery for node=%s", node_name)

    try:
        node_yaml = resolve_node_yaml(node_name, platform_root)
        ssh_host = extract_node_host(node_yaml)
    except NodeYamlNotFoundError:
        logger.info("[IMP:8][deliver_vhost_overlays][skip] Cannot resolve/extract — skipping")
        return True

    if not ssh_host:
        logger.info("[IMP:8][deliver_vhost_overlays][skip] No SSH host — local mode")
        return True

    overlay_dir = pathlib.Path(platform_root) / "node-configs" / node_name / "overlays" / "nginx"
    if not pathlib.Path(overlay_dir).is_dir():
        logger.info("[IMP:8][deliver_vhost_overlays][skip] No overlay dir: %s", overlay_dir)
        return True

    confs = sorted(overlay_dir.glob("*.conf"))
    if not confs:
        logger.info("[IMP:8][deliver_vhost_overlays][skip] No .conf files in %s", overlay_dir)
        return True

    logger.info("[IMP:9][deliver_vhost_overlays][deliver] Delivering %d overlay(s) to %s", len(confs), ssh_host)
    ssh_e = build_rsync_ssh_opts()

    if dry_run:
        logger.info("[IMP:8][deliver_vhost_overlays][dry-run] DRY-RUN: ssh root@%s mkdir -p ...", ssh_host)
        logger.info("[IMP:8][deliver_vhost_overlays][dry-run] DRY-RUN: rsync %s/ → root@%s:...", overlay_dir, ssh_host)
        return True

    mkdir_cmd = ["ssh", *SSH_OPTS, f"root@{ssh_host}", f"mkdir -p {node_configs_remote()}/{node_name}/overlays/nginx"]
    logger.info("[IMP:9][deliver_vhost_overlays][ssh] Creating remote overlay dir on %s", ssh_host)
    r = subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=SSH_CONNECT_TIMEOUT, check=False)
    if r.returncode != 0:
        msg = f"mkdir failed on {ssh_host} (exit={r.returncode}): {r.stderr.strip()}"
        raise DeliveryError(msg)

    rsync_cmd = [
        "rsync",
        "-avz",
        "--delete",
        "-e",
        ssh_e,
        f"{overlay_dir}/",
        f"root@{ssh_host}:{node_configs_remote()}/{node_name}/overlays/nginx/",
    ]
    logger.info("[IMP:9][deliver_vhost_overlays][rsync] Rsyncing overlays → %s", ssh_host)
    r = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT, check=False)
    if r.returncode != 0:
        msg = f"rsync overlays failed for {ssh_host} (exit={r.returncode}): {r.stderr.strip()}"
        raise DeliveryError(msg)
    logger.info("[IMP:9][deliver_vhost_overlays][done] Overlay delivery complete")
    return True


# endregion FUNC_deliver_vhost_overlays


# region FUNC_cli
## @purpose  CLI entrypoint: resolve-node | extract-host | sync-core | deliver. Dispatches to sub-commands.
## @io  input: sys.argv (via argparse), output: stdout write or sys.exit(1) on error
## @complexity  O(1) — dispatch-only, delegates to sub-functions
# region CLASS_CliArgs
class _CliArgs(argparse.Namespace):
    """Типизированный argparse-Namespace (W11-G3): parse_args(namespace=...)."""

    def __init__(self) -> None:
        super().__init__()
        self.command: str
        self.node: str
        self.platform_root: str
        self.yaml_path: str
        self.host: str
        self.core_src: str
        self.node_name: str
        self.node_yaml: str
        self.dry_run: bool


# endregion CLASS_CliArgs


# region FUNC__plw_body_cli
## @purpose  Тело try-блока (PLW0717 extraction из cli) — семантика except не меняется.
## @io       ⇥ args: _CliArgs → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body_cli(args: _CliArgs) -> None:
    if args.command == "resolve-node":
        sys.stdout.write(resolve_node_yaml(args.node, args.platform_root) + "\n")
    elif args.command == "extract-host":
        sys.stdout.write(extract_node_host(args.yaml_path) + "\n")
    elif args.command == "sync-core":
        sync_core_to_vps(args.host, args.core_src, args.node_name, args.node_yaml, args.dry_run)
    elif args.command == "deliver":
        deliver_vhost_overlays(args.node, args.platform_root, args.dry_run)


# endregion FUNC__plw_body_cli


def cli() -> int:
    """CLI entrypoint: resolve-node | extract-host | sync-core | deliver."""
    p = argparse.ArgumentParser(description="overlay_deliverer — vhost overlay delivery")
    sp = p.add_subparsers(dest="command", required=True)

    rp = sp.add_parser("resolve-node", help="Resolve node.yaml path")
    rp.add_argument("--node", required=True)
    rp.add_argument("--platform-root", default=str(platform_remote_base()))

    ep = sp.add_parser("extract-host", help="Extract SSH host from node.yaml")
    ep.add_argument("--yaml", required=True, dest="yaml_path")

    sp2 = sp.add_parser("sync-core", help="Rsync core/ to VPS")
    sp2.add_argument("--host", required=True)
    sp2.add_argument("--core-src", required=True)
    sp2.add_argument("--node", default="", dest="node_name")
    sp2.add_argument("--node-yaml", default="")
    sp2.add_argument("--dry-run", action="store_true")

    dp = sp.add_parser("deliver", help="Deliver vhost overlays")
    dp.add_argument("--node", required=True)
    dp.add_argument("--platform-root", default=str(platform_remote_base()))
    dp.add_argument("--dry-run", action="store_true")

    args = p.parse_args(namespace=_CliArgs())
    try:
        _plw_body_cli(args)
    except OverlayDelivererError as e:
        logger.info("[IMP:10][cli][error] %s", e)
        return 1
    return 0


# endregion FUNC_cli


if __name__ == "__main__":
    sys.exit(cli())
