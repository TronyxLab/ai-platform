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
##              — SSH_OPTS mirror lib/ssh.sh SSH_OPTS_COMMON
## @rationale Strangler-Fig: remote-cmd.sh 672→~230 LOC shell facade. Business logic → unit-testable Python.
## @changes 2026-07-26 | TASK-036D — Initial implementation (Wave 5d Strangler-Fig)
# ⚠️ TRAP[BUG] · 2026-07-24 · P0 · node-update не доставлял core/ на VPS
# · Ported from remote-cmd.sh:294. Fix: rsync core/ + node.yaml before remote exec.
# · Prevention: always call sync_core_to_vps() before remote exec in node-update.
# 🧐 TRAP[DECISION] · 2026-07-26 · — · printf %q builders stay in shell (D3). Rejected: shlex.quote() ≠ printf '%q'.
# 📝 TRAP[DEBT] · 2026-07-26 · LO · node-resolver.sh:306-316 has inline python3 -c (Tier 1 Strangler trigger).
#   Migration of extract_node_host requires updating all 8+ callers. Deferred — separate DevPlan needed.
# endregion MODULE_CONTRACT

import argparse
import glob as glob_module
import logging
import os
import subprocess
import sys

from core.internal.shared.node_yaml import ConfigNotFoundError, ConfigParseError, ConfigValidationError, NodeYaml

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


# Mirror lib/ssh.sh SSH_OPTS_COMMON — BatchMode, accept-new, timeouts
SSH_OPTS: list[str] = [
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ConnectTimeout=30",
    "-o",
    "ServerAliveInterval=30",
    "-o",
    "ServerAliveCountMax=10",
]

RSYNC_EXCLUDES: list[str] = [
    "--exclude=.git",
    "--exclude=__pycache__",
    "--exclude=.pytest_cache",
    "--exclude=default-user.xml",
    "--exclude=.env",
]


# region FUNC__ssh_e
## @purpose  Build rsync -e argument from SSH_OPTS list.
## @io  input: SSH_OPTS (module-level list), output: ssh command string
## @complexity  O(k) where k = len(SSH_OPTS) — simple string join
def _ssh_e() -> str:
    """Build rsync -e argument from SSH_OPTS."""
    return f"ssh {' '.join(SSH_OPTS)}"


# endregion FUNC__ssh_e


# region FUNC_resolve_node_yaml
## @purpose  Search node.yaml across 3 candidate paths: platform-local → org repos → VPS fallback.
## @io  input: node_name (str), platform_root (str), projects_dir (Optional[str])
##      output: resolved absolute path as str
## @complexity  O(n) where n = number of candidate paths (≤4)
def resolve_node_yaml(
    node_name: str,
    platform_root: str = "/opt/platform",
    projects_dir: str | None = None,
) -> str:
    """Search node.yaml across 3 paths: platform-local → org repos → VPS fallback.

    @raises NodeYamlNotFoundError  If not found in any candidate path.
    """
    if not node_name:
        logger.info("[IMP:10][resolve_node_yaml][input] Missing node_name")
        raise NodeYamlNotFoundError("Missing required argument: node_name")
    if projects_dir is None:
        projects_dir = os.path.expanduser("~/projects")

    logger.info("[IMP:8][resolve_node_yaml][search] Resolving node.yaml for node=%s", node_name)
    candidates: list[str] = [
        os.path.join(platform_root, "node-configs", node_name, "node.yaml"),
    ]
    # Path 2: org repos glob — nullglob handled by Python glob (empty list if no match)
    # ⚠️ TRAP[BUG] · 2026-07-07 · P2 · Glob expansion nullguard (ported from node-resolver.sh)
    candidates.extend(sorted(glob_module.glob(os.path.join(projects_dir, "*", "node-configs", node_name, "node.yaml"))))
    candidates.append(f"/opt/node-configs/{node_name}/node.yaml")

    for p in candidates:
        if os.path.isfile(p):
            logger.info("[IMP:9][resolve_node_yaml][result] Resolved: %s", p)
            return p

    logger.info("[IMP:10][resolve_node_yaml][result] Not found for node=%s (searched: %s)", node_name, candidates)
    raise NodeYamlNotFoundError(f"node.yaml not found for node={node_name}")


# endregion FUNC_resolve_node_yaml


# region FUNC_extract_node_host
## @purpose  Extract node.host from node.yaml via NodeYaml. Returns "" if absent.
## @io  input: yaml_path (str), output: host string or "" (empty = no host)
## @complexity  O(1) — single NodeYaml lazy load + get
def extract_node_host(yaml_path: str) -> str:
    """Extract node.host from node.yaml via NodeYaml. Returns "" if absent."""
    if not yaml_path:
        raise NodeYamlNotFoundError("Missing required argument: yaml_path")
    if not os.path.isfile(yaml_path):
        raise NodeYamlNotFoundError(f"File not found: {yaml_path}")

    logger.info("[IMP:8][extract_node_host][parse] Extracting host from: %s", yaml_path)
    try:
        node = NodeYaml(yaml_path)
        host = node.get("node.host", default="")
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        raise NodeYamlNotFoundError(f"Failed to parse YAML: {yaml_path}") from exc

    logger.info("[IMP:9][extract_node_host][result] Host: %s", host if host else "(empty)")
    return host


# endregion FUNC_extract_node_host


# region FUNC_sync_core_to_vps
## @purpose  Rsync core/ + optional node.yaml to remote VPS. Dry-run mode prints commands, does not execute.
## @io  input: host, core_src, node_name, node_yaml, dry_run (bool); output: bool success
## @complexity  O(f + m) where f = files rsynced, m = metadata (node.yaml) rsync
def sync_core_to_vps(host: str, core_src: str, node_name: str = "", node_yaml: str = "", dry_run: bool = False) -> bool:
    """Rsync core/ + optional node.yaml to remote VPS. Dry-run: print, don't exec.

    @raises SyncCoreError  On rsync failure.
    """
    if not host:
        raise SyncCoreError("Missing required argument: host")
    if not core_src or not os.path.isdir(core_src):
        raise SyncCoreError(f"core_src not found: {core_src}")
    core_src = core_src if core_src.endswith("/") else core_src + "/"

    ssh_e = _ssh_e()
    cmd = ["rsync", "-avz", "--delete", *RSYNC_EXCLUDES, "-e", ssh_e, core_src, f"root@{host}:/opt/platform/core/"]

    if dry_run:
        logger.info("[IMP:8][sync_core_to_vps][dry-run] DRY-RUN: %s", " ".join(cmd))
        if node_yaml and os.path.isfile(node_yaml):
            logger.info(
                "[IMP:8][sync_core_to_vps][dry-run] DRY-RUN: rsync %s → root@%s:/opt/node-configs/%s/node.yaml",
                node_yaml,
                host,
                node_name,
            )
        return True

    logger.info("[IMP:9][sync_core_to_vps][exec] Rsyncing core/ → %s:/opt/platform/core/", host)
    logger.info("[IMP:7][sync_core_to_vps][exec] Running: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise SyncCoreError(f"rsync core/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}")
    logger.info("[IMP:9][sync_core_to_vps][exec] core/ rsync complete")

    if node_yaml and os.path.isfile(node_yaml):
        cmd2 = ["rsync", "-avz", "-e", ssh_e, node_yaml, f"root@{host}:/opt/node-configs/{node_name}/node.yaml"]
        logger.info("[IMP:9][sync_core_to_vps][exec] Rsyncing node.yaml → %s:/opt/node-configs/%s/", host, node_name)
        r = subprocess.run(cmd2, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise SyncCoreError(f"rsync node.yaml failed for {host} (exit={r.returncode}): {r.stderr.strip()}")
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
def deliver_vhost_overlays(node_name: str, platform_root: str = "/opt/platform", dry_run: bool = False) -> bool:
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

    overlay_dir = os.path.join(platform_root, "node-configs", node_name, "overlays", "nginx")
    if not os.path.isdir(overlay_dir):
        logger.info("[IMP:8][deliver_vhost_overlays][skip] No overlay dir: %s", overlay_dir)
        return True

    confs = sorted(glob_module.glob(os.path.join(overlay_dir, "*.conf")))
    if not confs:
        logger.info("[IMP:8][deliver_vhost_overlays][skip] No .conf files in %s", overlay_dir)
        return True

    logger.info("[IMP:9][deliver_vhost_overlays][deliver] Delivering %d overlay(s) to %s", len(confs), ssh_host)
    ssh_e = _ssh_e()

    if dry_run:
        logger.info("[IMP:8][deliver_vhost_overlays][dry-run] DRY-RUN: ssh root@%s mkdir -p ...", ssh_host)
        logger.info("[IMP:8][deliver_vhost_overlays][dry-run] DRY-RUN: rsync %s/ → root@%s:...", overlay_dir, ssh_host)
        return True

    mkdir_cmd = ["ssh", *SSH_OPTS, f"root@{ssh_host}", f"mkdir -p /opt/node-configs/{node_name}/overlays/nginx"]
    logger.info("[IMP:9][deliver_vhost_overlays][ssh] Creating remote overlay dir on %s", ssh_host)
    r = subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise DeliveryError(f"mkdir failed on {ssh_host} (exit={r.returncode}): {r.stderr.strip()}")

    rsync_cmd = [
        "rsync",
        "-avz",
        "--delete",
        "-e",
        ssh_e,
        f"{overlay_dir}/",
        f"root@{ssh_host}:/opt/node-configs/{node_name}/overlays/nginx/",
    ]
    logger.info("[IMP:9][deliver_vhost_overlays][rsync] Rsyncing overlays → %s", ssh_host)
    r = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise DeliveryError(f"rsync overlays failed for {ssh_host} (exit={r.returncode}): {r.stderr.strip()}")
    logger.info("[IMP:9][deliver_vhost_overlays][done] Overlay delivery complete")
    return True


# endregion FUNC_deliver_vhost_overlays


# region FUNC_cli
## @purpose  CLI entrypoint: resolve-node | extract-host | sync-core | deliver. Dispatches to sub-commands.
## @io  input: sys.argv (via argparse), output: stdout write or sys.exit(1) on error
## @complexity  O(1) — dispatch-only, delegates to sub-functions
def cli() -> None:
    """CLI entrypoint: resolve-node | extract-host | sync-core | deliver."""
    p = argparse.ArgumentParser(description="overlay_deliverer — vhost overlay delivery")
    sp = p.add_subparsers(dest="command", required=True)

    rp = sp.add_parser("resolve-node", help="Resolve node.yaml path")
    rp.add_argument("--node", required=True)
    rp.add_argument("--platform-root", default="/opt/platform")
    rp.add_argument("--projects-dir", default=None)

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
    dp.add_argument("--platform-root", default="/opt/platform")
    dp.add_argument("--dry-run", action="store_true")

    args = p.parse_args()
    try:
        if args.command == "resolve-node":
            sys.stdout.write(resolve_node_yaml(args.node, args.platform_root, args.projects_dir) + "\n")
        elif args.command == "extract-host":
            sys.stdout.write(extract_node_host(args.yaml_path) + "\n")
        elif args.command == "sync-core":
            sync_core_to_vps(args.host, args.core_src, args.node_name, args.node_yaml, args.dry_run)
        elif args.command == "deliver":
            deliver_vhost_overlays(args.node, args.platform_root, args.dry_run)
    except OverlayDelivererError as e:
        logger.info("[IMP:10][cli][error] %s", e)
        sys.exit(1)


# endregion FUNC_cli


if __name__ == "__main__":
    cli()
