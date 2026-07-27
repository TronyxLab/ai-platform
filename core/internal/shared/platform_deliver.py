#!/usr/bin/env python3
# GREP_SUMMARY: platform-deliver, build-deliver-command, parse-deliver-args, forced-command
# STRUCTURE: ▶ build_deliver_command(org, project) → ◇ parse_deliver_args(args) → ⊕ CLI → ⎋
# region MODULE_CONTRACT
## @purpose  Unified platform-deliver verb builder — replaces duplicate string construction
##           in deploy-project.sh and reconcile-projects.sh. Provides canonical build/parse
##           operations for the SSH forced-command verb "platform-deliver".
## @scope    Used by reconciler_projects.py, deploy scripts, and any consumer that needs
##           to construct or deconstruct platform-deliver command strings.
##           CLI mode via `python3 -m` for interactive debugging and testing.
## @invariants
##   1. org is optional (None or "" → single-token format)
##   2. Always returns lowercase "platform-deliver" prefix
##   3. CLI via `python3 -m core.internal.shared.platform_deliver`
##   4. parse_deliver_args raises ValueError on empty input
##   5. build_deliver_command raises ValueError if project is empty
## @rationale DRIFT-elimination (Brief 077): deploy-project.sh and reconciler_projects.py
##            both constructed platform-deliver strings manually, leading to subtle
##            formatting drift. Single canonical source prevents divergence.
## @changes  2026-07-26 | DevPlan 081 Phase A — Created platform-deliver verb builder
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import sys

logger = logging.getLogger(__name__)


# region FUNC_build_deliver_command
## @purpose  Build a "platform-deliver" command string from org and project components.
##           With org: "platform-deliver {org} {project}".
##           Without org: "platform-deliver {project}".
## @io       ⇥ org (str, optional), project (str) → ⎋ str
## @complexity  O(1)
def build_deliver_command(org: str = "", project: str = "") -> str:
    """Build platform-deliver command string.

    Args:
        org: Optional organization name. Empty string or None → single-token format.
        project: Project name. Must be non-empty.

    Returns:
        Formatted "platform-deliver ..." command string.

    Raises:
        ValueError: If project is empty.
    """
    if not project:
        raise ValueError("project must be non-empty")

    cmd = f"platform-deliver {org} {project}" if org else f"platform-deliver {project}"

    logger.info("[IMP:9][build_deliver_command] Built deliver verb: %s", cmd)
    return cmd


# endregion FUNC_build_deliver_command


# region FUNC_parse_deliver_args
## @purpose  Parse the arguments portion of a "platform-deliver" command string
##           (everything after "platform-deliver " prefix).
##           Two tokens → (org, project) — new format with org.
##           One token → ("", project) — legacy format without org.
## @io       ⇥ args (str) → ⎋ tuple[str, str] as (org, project)
## @complexity  O(n) where n = number of space-separated tokens (max 2)
def parse_deliver_args(args: str) -> tuple[str, str]:
    """Parse the arguments after "platform-deliver " prefix.

    Args:
        args: The string AFTER "platform-deliver " prefix (the remaining tokens).

    Returns:
        Tuple of (org, project). org is empty string for legacy single-token format.

    Raises:
        ValueError: If args is empty or whitespace-only.
    """
    stripped = args.strip()
    if not stripped:
        raise ValueError("args must be non-empty after stripping whitespace")

    tokens = stripped.split()
    if len(tokens) == 1:
        org, project = "", tokens[0]
    elif len(tokens) == 2:
        org, project = tokens[0], tokens[1]
    else:
        raise ValueError(f"Expected 1 or 2 tokens, got {len(tokens)}: {stripped!r}")

    logger.info("[IMP:9][parse_deliver_args] Parsed args: org=%r project=%r", org, project)
    return org, project


# endregion FUNC_parse_deliver_args


# region FUNC_CLI
## @purpose  CLI entry point for `python3 -m core.internal.shared.platform_deliver`.
##           Supports two subcommands:
##             build   — builds a platform-deliver verb string
##             parse   — parses a platform-deliver argument string to JSON
## @io       ⇥ sys.argv → ⎋ exit 0 on success, exit 1 on error
## @complexity  O(1) dispatch
def _cli() -> None:
    """CLI entry point for platform_deliver module."""
    parser = argparse.ArgumentParser(description="platform-deliver verb builder and parser")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── build subcommand ──────────────────────────────────────────────────
    build_parser = subparsers.add_parser("build", help="Build platform-deliver verb")
    build_parser.add_argument("--org", default="", help="Organization name (optional)")
    build_parser.add_argument("--project", required=True, help="Project name")

    # ── parse subcommand ──────────────────────────────────────────────────
    parse_parser = subparsers.add_parser("parse", help="Parse platform-deliver args")
    parse_parser.add_argument(
        "--format",
        choices=["json", "lines"],
        default="json",
        dest="output_format",
        help="Output format: json (default) or lines (project\\norg)",
    )
    parse_parser.add_argument("args", help="Arguments string after 'platform-deliver '")

    args = parser.parse_args()

    if args.command == "build":
        try:
            result = build_deliver_command(org=args.org, project=args.project)
            print(result)
        except ValueError as exc:
            logger.error("[IMP:3][CLI] build failed: %s", exc)
            sys.exit(1)

    elif args.command == "parse":
        try:
            org, project = parse_deliver_args(args.args)
            if args.output_format == "lines":
                print(project)
                print(org)
            else:
                output = {"org": org, "project": project}
                print(json.dumps(output))
        except ValueError as exc:
            logger.error("[IMP:3][CLI] parse failed: %s", exc)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )
    _cli()
# endregion FUNC_CLI
