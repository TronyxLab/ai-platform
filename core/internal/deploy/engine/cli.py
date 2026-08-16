#!/usr/bin/env python3
# GREP_SUMMARY: deploy-engine-cli, argparse, deploy, remove, status, subcommands, exit-0-1-10, 170-W4-B2
# STRUCTURE: ▶ argparse (deploy|remove|status) → DeployEngine().deploy/remove/status → ⊕ JSON stdout → ◇ PlatformError → ⎋ exit 0|1|10
# region MODULE_CONTRACT
## @purpose  CLI entrypoint DeployEngine (170 W4-B2): argparse subcommands deploy/remove/status.
##           Перенесён из монолита deploy_engine.py БЕЗ изменения тела (main() 728-816 оригинала).
## @scope    core/internal/deploy/engine/cli.py — вызывается как `python3 -m core.internal.deploy.deploy_engine`
##           (фасад deploy_engine.py ре-экспортирует main) и напрямую `python3 engine/cli.py`.
## @invariants
##   - First-deploy failure → PlatformFatalError → exit 10 (DevPlan 116 B4 T3.1/D4)
##   - Единый паттерн: except PlatformError as e → return e.exit_code (контракт T4)
##   - Shell-фасад выполняет verb-классификацию до вызова Python-модулей (D8)
## @changes 170 W4-B2 — extracted from deploy_engine.py
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from typing import cast

from core.internal.deploy.engine.engine import DeployEngine
from core.internal.shared.exceptions import PlatformError

logger = logging.getLogger(__name__)


# region DATACLASS_CliArgs
@dataclass
class _CliArgs:
    """Типизированная граница argparse.Namespace CLI (W11, DevPlan 170)."""

    command: str
    project: str
    ref: str
    service: str
    project_dir: str
    node: str
    max_wait: int
    keep_images: int
    stub_aware: bool


# endregion DATACLASS_CliArgs


# region FUNC_main
## @purpose  CLI entrypoint with argparse subcommands: deploy, remove, status.
## @io       ⇥ sys.argv → ⎋ exit 0|1|10 (PlatformError → e.exit_code)
## @rationale D8 (DevPlan 036E): argparse subcommands for debuggability, testability, composability.
##            Shell facade performs verb classification before calling Python modules.
## @invariants
##   - First-deploy failure → PlatformFatalError → exit 10 (DevPlan 116 B4 T3.1/D4)
##   - Единый паттерн: except PlatformError as e → return e.exit_code (контракт T4)
def main(argv: list[str] | None = None) -> int:
    """CLI entry point — deploy/remove/status subcommands (contract: main() -> int)."""
    parser = argparse.ArgumentParser(description="Deploy Engine — atomic deploy/rollback/remove/status")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── Deploy subcommand ──
    deploy_parser = sub.add_parser("deploy", help="Atomic deploy with healthcheck-based rollback")
    deploy_parser.add_argument("--project", required=True)
    deploy_parser.add_argument("--ref", required=True)
    deploy_parser.add_argument("--service", required=True)
    deploy_parser.add_argument("--project-dir", required=True)
    deploy_parser.add_argument("--node", default="")
    deploy_parser.add_argument("--max-wait", type=int, default=60)
    deploy_parser.add_argument("--keep-images", type=int, default=3)

    # ── Remove subcommand ──
    remove_parser = sub.add_parser("remove", help="Idempotent remove (data preserved)")
    remove_parser.add_argument("--project", required=True)
    remove_parser.add_argument("--project-dir", required=True)

    # ── Status subcommand ──
    status_parser = sub.add_parser("status", help="JSON status output")
    status_parser.add_argument("--project", required=True)
    status_parser.add_argument("--project-dir", required=True)
    status_parser.add_argument("--stub-aware", action="store_true", default=False)

    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))
    engine = DeployEngine()

    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        if args.command == "deploy":
            result = engine.deploy(
                project=args.project,
                ref=args.ref,
                service=args.service,
                project_dir=args.project_dir,
                node=args.node,
                max_wait=args.max_wait,
                keep_images=args.keep_images,
            )
            # JSON output for shell facade
            print(
                json.dumps({
                    "success": result.success,
                    "project": result.project,
                    "ref": result.ref,
                    "service": result.service,
                    "previous_image": result.previous_image,
                    "rollback_performed": result.rollback_performed,
                    "first_deploy_failed": result.first_deploy_failed,
                    "error_message": result.error_message,
                })
            )
            return 0 if result.success else 1
        if args.command == "remove":
            result = engine.remove(project=args.project, project_dir=args.project_dir)
            print(
                json.dumps({
                    "success": result.success,
                    "project": result.project,
                    "already_removed": result.already_removed,
                    "error_message": result.error_message,
                })
            )
            return 0 if result.success else 1
        result = engine.status(
            project=args.project,
            project_dir=args.project_dir,
            stub_aware=args.stub_aware,
        )
        print(
            json.dumps({
                "project": result.project,
                "node": result.node,
                "status": result.status,
                "containers": result.containers,
                "last_deploy": result.last_deploy,
            })
        )
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code
    else:
        return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
