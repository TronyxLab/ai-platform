#!/usr/bin/env python3
# GREP_SUMMARY: orchestrator-cli, cli, receive, deploy, deploy-many, rollback, status, remove, entrypoint
"""
CLI entrypoint for DeployOrchestrator. Commands: receive, deploy, deploy-many, rollback, status, remove.
Usage:
    python3 -m core.internal.deploy.orchestrator_cli receive          # VPS-side: read tar from stdin
    python3 -m core.internal.deploy.orchestrator_cli deploy ...       # Deploy single project
    python3 -m core.internal.deploy.orchestrator_cli deploy-many ...  # Deploy multiple projects
    python3 -m core.internal.deploy.orchestrator_cli rollback ...     # Rollback project
    python3 -m core.internal.deploy.orchestrator_cli status ...       # Project status
    python3 -m core.internal.deploy.orchestrator_cli remove ...       # Remove project
"""
# STRUCTURE: ▶ main() → argparse dispatch → receive | deploy | deploy-many | rollback | status | remove → sys.exit(0|1)
# region MODULE_CONTRACT
## @purpose  CLI entrypoint for DeployOrchestrator. Replaces deploy-project.sh and provides
##           direct access to all orchestrator operations from command line.
##           Command `receive` reads Payload from stdin (tar), extracts, and calls DeployOrchestrator.deploy().
##           Command `deploy-many` accepts project_names + channel, delegates to DeployOrchestrator.deploy_many().
## @scope    Entrypoint for SSH forced-command (receive), shell scripts (deploy-many), and CLI testing.
## @invariants
##   1. receive command reads tar.gz from stdin — used as forced-command entrypoint
##   2. deploy-many accepts comma-separated project names
##   3. All commands return JSON to stdout and exit 0/1
##   4. Channel selection: --scp for SCPChannel, --forced-command for ForcedCommandChannel
## @rationale DevPlan 089 T6.6: Single CLI entrypoint replaces deploy-project.sh and
##            provides machine-parseable JSON output for shell consumers.
## @changes 2026-07-30 | DevPlan 089 T6.6 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from core.internal.deploy.channels import ForcedCommandChannel, SCPChannel
from core.internal.deploy.orchestrator import DeployOrchestrator

logger = logging.getLogger(__name__)


# region FUNC_build_parser
def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser.

    ## @purpose — Build argparse with subcommands for all deploy operations.
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        description="Deploy Orchestrator CLI — unified deploy entrypoint (DevPlan 089)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── receive — VPS-side tar reader ──
    recv = sub.add_parser("receive", help="Receive deploy payload via stdin (tar.gz)")

    # ── deploy — single project ──
    dep = sub.add_parser("deploy", help="Deploy a single project")
    dep.add_argument("--project", required=True, help="Project name")
    dep.add_argument("--version", default="latest", help="Version/tag to deploy")
    dep.add_argument("--service", default="", help="Docker Compose service name")
    dep.add_argument("--project-dir", default="", help="Project directory path")
    dep.add_argument("--scp", action="store_true", help="Use SCPChannel (default)")
    dep.add_argument("--forced-command", action="store_true", help="Use ForcedCommandChannel")
    dep.add_argument("--host", default="", help="Remote host for channel delivery")
    dep.add_argument("--user", default="", help="SSH user")
    dep.add_argument("--key-file", default="", help="SSH key file path")

    # ── deploy-many — multiple projects ──
    dm = sub.add_parser("deploy-many", help="Deploy multiple projects sequentially")
    dm.add_argument("--projects", required=True, help="Comma-separated project names")
    dm.add_argument("--version", default="latest", help="Version/tag")
    dm.add_argument("--scp", action="store_true", help="Use SCPChannel (default)")
    dm.add_argument("--forced-command", action="store_true", help="Use ForcedCommandChannel")
    dm.add_argument("--host", default="", help="Remote host")

    # ── rollback ──
    rb = sub.add_parser("rollback", help="Rollback a project")
    rb.add_argument("--project", required=True, help="Project name")
    rb.add_argument("--snapshot-id", default="", help="Specific snapshot ID (default: latest)")

    # ── status ──
    st = sub.add_parser("status", help="Get project status")
    st.add_argument("--project", required=True, help="Project name")

    # ── remove ──
    rm = sub.add_parser("remove", help="Remove project containers")
    rm.add_argument("--project", required=True, help="Project name")
    rm.add_argument("--purge", action="store_true", help="Remove compose volumes (down -v)")

    return parser


# endregion FUNC_build_parser


# region FUNC_build_channel
def build_channel(args: argparse.Namespace) -> SCPChannel | ForcedCommandChannel:
    """Build a delivery channel from CLI args.

    Args:
        args: Parsed CLI arguments.

    Returns:
        SCPChannel or ForcedCommandChannel.
    """
    use_forced = args.forced_command if hasattr(args, "forced_command") else False
    host = getattr(args, "host", "")
    user = getattr(args, "user", "")
    key_file = getattr(args, "key_file", "")
    host = host or os.environ.get("DEPLOY_HOST", "")

    if use_forced:
        channel = ForcedCommandChannel()
    else:
        channel = SCPChannel()

    if host:
        channel.metadata_defaults = {"host": host}
        if user:
            channel.metadata_defaults["user"] = user
        if key_file:
            channel.metadata_defaults["key_file"] = key_file

    return channel


# endregion FUNC_build_channel


# region FUNC_main
def main() -> int:
    """CLI entry point.

    ## @purpose — Dispatch to command handler based on argparse.
    ## @io — ⇥ sys.argv → ⎋ exit code 0/1
    ## @complexity — O(N) for deploy-many, O(1) for other commands
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = build_parser()
    args = parser.parse_args()

    orchestrator = DeployOrchestrator()

    # ── receive ──
    if args.command == "receive":
        exit_code = DeployOrchestrator.receive()
        return exit_code

    # ── deploy ──
    if args.command == "deploy":
        channel = build_channel(args)
        project_dir = args.project_dir or os.path.join(
            os.environ.get("PROJECTS_BASE", "/opt/projects"),
            args.project,
        )
        service = args.service or args.project

        result = orchestrator.deploy(
            project_name=args.project,
            channel=channel,
            version=args.version,
            service=service,
            project_dir=project_dir,
        )
        print(json.dumps(result.to_dict()))
        return 0 if result.is_success() else 1

    # ── deploy-many ──
    if args.command == "deploy-many":
        channel = build_channel(args)
        projects = [p.strip() for p in args.projects.split(",") if p.strip()]

        results = orchestrator.deploy_many(
            project_names=projects,
            channel=channel,
            version=args.version,
        )
        output = [r.to_dict() for r in results]
        print(json.dumps(output))
        failed = sum(1 for r in results if not r.is_success())
        return 1 if failed > 0 else 0

    # ── rollback ──
    if args.command == "rollback":
        result = orchestrator.rollback(
            project_name=args.project,
            snapshot_id=args.snapshot_id or None,
        )
        print(json.dumps(result.to_dict()))
        return 0 if result.is_success() else 1

    # ── status ──
    if args.command == "status":
        status = orchestrator.status(project_name=args.project)
        print(json.dumps(status.to_dict()))
        return 0

    # ── remove ──
    if args.command == "remove":
        result = orchestrator.remove(
            project_name=args.project,
            purge=getattr(args, "purge", False),
        )
        print(json.dumps(result.to_dict()))
        return 0 if result.is_success() else 1

    return 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
