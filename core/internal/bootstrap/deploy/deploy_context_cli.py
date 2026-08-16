#!/usr/bin/env python3
# GREP_SUMMARY: deploy-context-cli entrypoint thin-facade context-deployer remote-executor node-yaml resolve local-fallback
# STRUCTURE: ▶ parse (--node/--context/--node-yaml/--local) → ◇ remote (--node + !local + !VPS) → ⊕ remote_executor.execute_deploy_context → ⎋ rc → ◇ local: resolve node.yaml → subprocess context_deployer.py → ⎋ rc
# region MODULE_CONTRACT
## @purpose  CLI-слой `make deploy-context` (DevPlan 173 W2.1) — Python-порт deploy-context.sh
##           (79 LOC shell): arg-парсинг, remote/local детекция, node.yaml резолв. Фасад
##           core/entrypoints/deploy-context.sh → `exec python3 -m ...deploy_context_cli`.
## @scope    Вызывается из core/entrypoints/deploy-context.sh (make deploy-context NODE=<n>).
## @invariants
##   - remote-режим: --node + !--local + маркер node-lifecycle.sh отсутствует (DevPlan 153 T7);
##     remote_cmd строится ssh_cmd_builder.build_deploy_context_ssh_cmd, исполняется
##     remote_executor.execute_deploy_context (rc 0/1/2/124, БЕЗ локального fallback — контракт shell)
##   - local-режим: node.yaml резолв (node_resolver → /opt/node-configs/NODE/node.yaml fallback)
##     → subprocess context_deployer.py (rc passthrough)
##   - exit-коды НЕ меняются относительно deploy-context.sh (guardrail 173)
## @rationale Языковая политика: бизнес-логика (парсинг/детекция/резолв) → Python; entrypoint = exec.
## @changes  2026-08-16 | DevPlan 173 W2.1 — Created (порт deploy-context.sh)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

# self-bootstrap корня репо (канон context_deployer.py:60) — `from core.internal...` в любом cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.internal.bootstrap.remote_executor import VPS_NODE_LIFECYCLE, RemoteExecutor
from core.internal.shared import deploy_paths
from core.internal.shared.exceptions import ConfigNotFoundError
from core.internal.shared.node_resolver import resolve_node_yaml
from core.internal.shared.ssh_cmd_builder import build_deploy_context_ssh_cmd

logger = logging.getLogger(__name__)

# context_deployer.py — тот же каталог (bootstrap/deploy/)
_DEPLOYER = Path(__file__).resolve().parent / "context_deployer.py"


# region DATA_CliArgs
@dataclass
class CliArgs:
    """Typed CLI-аргументы deploy-context (W11: dataclass-граница argparse)."""

    node: str
    context: str
    node_yaml: str
    local: bool


# endregion DATA_CliArgs


# region FUNC_resolve_local_node_yaml
## @purpose  Резолв node.yaml для local-режима: node_resolver → /opt/node-configs/NODE/node.yaml.
## @io       ⇥ node_name: str → ⎋ str (резолвленный путь; "" = не найден)
## @complexity O(P) — 3-path search (NodeYaml.resolve)
def _resolve_local_node_yaml(node_name: str) -> str:
    """Resolve node.yaml via node_resolver, empty string on failure (shell fallback)."""
    try:
        return resolve_node_yaml(node_name)
    except (ConfigNotFoundError, OSError, ValueError):
        logger.info("[IMP:7][deploy-context][resolve] node_resolver failed — fallback to /opt/node-configs")
        return ""


# endregion FUNC_resolve_local_node_yaml


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `make deploy-context` (remote or local context deploy).

    ▶ ┌argv┐ → ◇ remote (--node+!local+!VPS) → ⊕ execute_deploy_context → ⎋ rc ·
      ◇ local → ⊕ resolve node.yaml → subprocess context_deployer.py → ⎋ rc
    """
    parser = argparse.ArgumentParser(description="Deploy context projects (DevPlan 173 W2.1)")
    parser.add_argument("--node", default="", help="Node name")
    parser.add_argument("--context", default="", help="Deployment context (auto-extracted if empty)")
    parser.add_argument("--node-yaml", default="", help="Path to node.yaml (bypasses resolution)")
    parser.add_argument("--local", action="store_true", help="Force local mode (skip remote SSH)")
    args = cast(CliArgs, cast(object, parser.parse_args(argv)))

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    # ── Remote mode (DevPlan 153 T7, N3): НЕ на VPS и не --local ──
    # Порт deploy-context.sh: маркер node-lifecycle.sh отсутствует → remote; иначе local.
    if args.node and not args.local and not Path(VPS_NODE_LIFECYCLE).is_file():
        logger.info("[IMP:9][deploy-context] Remote mode: executing deploy-context on node %s", args.node)
        passthrough = ["--context", args.context] if args.context else []
        remote_cmd = build_deploy_context_ssh_cmd(args.node, passthrough)
        dry_run = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
        return RemoteExecutor(dry_run=dry_run).execute_deploy_context(args.node, remote_cmd, " ".join(passthrough))

    # ── Local mode: resolve node.yaml ──
    node_yaml = args.node_yaml
    if not node_yaml:
        if args.node:
            node_yaml = _resolve_local_node_yaml(args.node)
        if not node_yaml:
            node_yaml = f"{deploy_paths.node_configs_remote()}/{args.node}/node.yaml"

    if not Path(node_yaml).is_file():
        logger.error("[IMP:10][deploy-context] ERROR: node.yaml not found: %s", node_yaml)
        return 1

    logger.info(
        "[IMP:9][deploy-context] Deploying context projects (NODE=%s, CONTEXT=%s)", args.node, args.context or "<auto>"
    )

    # ── Delegate to context_deployer.py ──
    cmd = [sys.executable, str(_DEPLOYER), "--node-yaml", node_yaml]
    if args.context:
        cmd += ["--context", args.context]
    result = subprocess.run(cmd, check=False)
    return result.returncode


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
