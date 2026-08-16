# GREP_SUMMARY: cli, main, argparse, sync, watch, pack, check, project-dir, template, no-hermes, log-level, exit codes
# STRUCTURE: ┌argparse┐ → ○ configure logging → ○ dispatch sync|watch|pack|check → ◇ error ? ⊕ [IMP:10] FAIL → exit 1 : ⊕ summary → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Command-line interface for the ai-instructions convention compiler
## @scope    Subcommands sync/watch/pack/check, common args (--config, --canon-path,
##   --log-level), exit-code contract (0 clean, 1 error/drift)
## @invariants
##   - Exit codes are strictly 0 or 1
##   - sync logs [IMP:9][SYNC][DONE] on success and [IMP:10][SYNC][FAIL] on any error
##   - manage_config runs in consumer mode (CWD) and, via emit, in project mode
##   - Logging goes to stderr only, format "%(levelname)s %(message)s"
## @rationale A single entry point keeps script-name parity between the console script
##   and `python -m ai_instructions`; strict exit codes make CI gating trivial
# endregion MODULE_CONTRACT

import argparse
import logging
import sys
from pathlib import Path

import yaml

from ai_instructions import __version__
from ai_instructions.runtime.canon_source import CanonError, read_version, resolve_canon
from ai_instructions.runtime.config import Config, ConfigError, load_config
from ai_instructions.runtime.emitter import (
    EmitError,
    classify_source,
    cleanup_orphans,
    emit,
    has_stamp,
    manage_config,
    plan_outputs,
)
from ai_instructions.runtime.lock import LockError, check_drift, write_lock
from ai_instructions.runtime.packer import pack
from ai_instructions.runtime.resolver import ResolverError, resolve
from ai_instructions.runtime.walker import Entry, WalkError, collect, walk_tree
from ai_instructions.runtime.watcher import watch

logger = logging.getLogger(__name__)

DEFAULT_PINS_FILENAME = "ai-instructions-pins.yaml"

_CAUGHT = (ConfigError, CanonError, WalkError, ResolverError, EmitError, LockError, OSError, yaml.YAMLError)


def _configure_logging(level: str) -> None:
    """Attach a single stderr handler to the ai_instructions logger."""
    root_logger = logging.getLogger("ai_instructions")
    root_logger.setLevel(logging.DEBUG if level == "DEBUG" else logging.INFO)
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root_logger.addHandler(handler)


def _load_config(config_arg: str | None) -> Config:
    """Load the pins config: explicit --config, default pins file in CWD, or runtime defaults."""
    if config_arg:
        p = Path(config_arg)
        if not p.is_file():
            msg = f"config file not found: {p}"
            raise ConfigError(msg)
        return load_config(p)
    default_pins = Path.cwd() / DEFAULT_PINS_FILENAME
    if default_pins.is_file():
        return load_config(default_pins)
    logger.info("[IMP:4][CLI][CONFIG] no config file found, using defaults (canon.tag=v%s)", __version__)
    return Config(canon_tag=f"v{__version__}")


def _consumer_root(project_dir: str | None) -> Path:
    return Path(project_dir).resolve() if project_dir else Path.cwd()


def _walk_consumer(consumer_root: Path) -> dict[str, Entry]:
    ai = consumer_root / ".ai"
    if not ai.is_dir():
        return {}
    return collect(walk_tree(ai, is_canon=False))


def _lock_files(config: Config, plan: dict[Path, Entry], consumer_root: Path) -> list[tuple[str, str]]:
    managed = [p for p in plan if p.is_file() and has_stamp(p)]
    return [
        (str(p.relative_to(consumer_root)), classify_source(p, plan[p], config, consumer_root))
        for p in sorted(managed, key=str)
    ]


# region FUNC_sync_impl
## @purpose  The sync pipeline body (isolated from exception handling for clarity)
## @io       in: parsed args; out: exit code (0 ok)
## @complexity O(tree size)
def _sync_impl(args: argparse.Namespace) -> int:
    """▶ ┌args┐ → ○ resolve canon → ○ walk canon + .ai → ○ resolve → ○ emit → ○ cleanup → ○ lock → ⊕ [IMP:9][SYNC][DONE] → ⎋ 0"""
    config = _load_config(args.config)
    consumer_root = _consumer_root(args.project_dir)
    project_mode = args.project_dir is not None
    canon_dir = resolve_canon(config, args.canon_path, consumer_root)
    canon_version = read_version(canon_dir).strip()

    canon_entries = collect(walk_tree(canon_dir, is_canon=True))
    project_entries = _walk_consumer(consumer_root)
    effective, warnings = resolve(canon_entries, project_entries)
    for w in warnings:
        logger.warning("[IMP:5][SYNC][WARN] %s", w)

    hermes_enabled = None if not args.no_hermes else False
    written = emit(
        config,
        effective,
        consumer_root,
        canon_version,
        project_mode=project_mode,
        template=args.template,
        hermes_enabled=hermes_enabled,
    )
    deleted = cleanup_orphans(config, effective, consumer_root, project_mode=project_mode)
    plan = plan_outputs(
        config,
        effective,
        consumer_root,
        project_mode=project_mode,
        template=args.template,
        hermes_enabled=hermes_enabled,
    )
    skipped = len(plan) - len(written)

    if not project_mode:
        manage_config(consumer_root)

    files = _lock_files(config, plan, consumer_root)
    write_lock(consumer_root, canon_version, files)

    logger.info(
        "[IMP:9][SYNC][DONE] %d files emitted, %d orphans cleaned, %d skipped (manual)",
        len(written),
        len(deleted),
        skipped,
    )
    return 0
# endregion FUNC_sync_impl


# region FUNC_cmd_sync
## @purpose  Full sync pipeline: canon → walk → resolve → emit → cleanup → lock
## @io       in: parsed args; out: exit code (0 ok, 1 error)
## @complexity O(tree size)
def _cmd_sync(args: argparse.Namespace) -> int:
    """▶ _sync_impl → ◇ error ? ⊕ [IMP:10][SYNC][FAIL] → 1 : ⎋ 0"""
    try:
        return _sync_impl(args)
    except _CAUGHT as exc:
        logger.error("[IMP:10][SYNC][FAIL] %s: %s", type(exc).__name__, exc)
        return 1
# endregion FUNC_cmd_sync


# region FUNC_cmd_watch
## @purpose  Resolve canon and enter the polling watcher loop
## @io       in: parsed args; out: exit code
## @complexity O(1) setup, then loop
def _cmd_watch(args: argparse.Namespace) -> int:
    """▶ ┌args┐ → ○ load config → ○ resolve canon → ○ watch(canon, consumer) → ⎋ 0"""
    config = _load_config(args.config)
    consumer_root = Path.cwd()
    canon_dir = resolve_canon(config, args.canon_path, consumer_root)
    watch(config, consumer_root, canon_dir)
    return 0
# endregion FUNC_cmd_watch


# region FUNC_cmd_pack
## @purpose  Walk + resolve + pack into a single markdown
## @io       in: parsed args (--out required); out: exit code
## @complexity O(tree size)
def _cmd_pack(args: argparse.Namespace) -> int:
    """▶ ┌args┐ → ○ resolve canon → ○ walk canon + .ai → ○ resolve → ○ pack → ⎋ 0"""
    config = _load_config(args.config)
    consumer_root = Path.cwd()
    canon_dir = resolve_canon(config, args.canon_path, consumer_root)
    canon_version = read_version(canon_dir).strip()
    canon_entries = collect(walk_tree(canon_dir, is_canon=True))
    project_entries = _walk_consumer(consumer_root)
    effective, warnings = resolve(canon_entries, project_entries)
    for w in warnings:
        logger.warning("[IMP:5][PACK][WARN] %s", w)
    pack(config, effective, canon_version, Path(args.out))
    return 0
# endregion FUNC_cmd_pack


# region FUNC_cmd_check
## @purpose  Report lock drift; exit 1 when any output drifted
## @io       in: parsed args; out: exit code (0 clean, 1 drift)
## @complexity O(lock files × size)
def _cmd_check(args: argparse.Namespace) -> int:
    """▶ ┌args┐ → ○ consumer root → ○ check_drift → ○ log messages to stderr → ⎋ 0|1"""
    consumer_root = _consumer_root(args.project_dir)
    ok, messages = check_drift(consumer_root)
    for message in messages:
        logger.error("[IMP:7][CHECK][DRIFT] %s", message)
    return 0 if ok else 1
# endregion FUNC_cmd_check


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse program with subcommands sync/watch/pack/check.

    Common args (--config/--canon-path/--log-level) live on a shared parent parser
    attached to every SUBPARSER, so the canonical invocation is
    `ai-instructions <subcommand> --canon-path <dir>` — argparse subparsers parse
    with a fresh namespace, so common args on the main parser would be clobbered by
    subparser defaults when given before the subcommand.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=None,
        help=f"pins YAML path (default: {DEFAULT_PINS_FILENAME} in CWD if present)",
    )
    common.add_argument("--canon-path", default=None, help="local canon directory (bypasses cache/clone)")
    common.add_argument("--log-level", choices=["INFO", "DEBUG"], default="INFO")

    parser = argparse.ArgumentParser(
        prog="ai-instructions",
        description="AI Instructions convention compiler (walk → resolve → emit → lock)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", parents=[common], help="compile canon + consumer .ai into .kilo/ outputs")
    p_sync.add_argument("--project-dir", default=None, help="operate on a project directory (project mode)")
    p_sync.add_argument("--template", choices=["all", "backend", "frontend"], default=None)
    p_sync.add_argument("--no-hermes", action="store_true", help="disable hermes profile emission")

    sub.add_parser("watch", parents=[common], help="watch canon + consumer trees and rebuild on change")

    p_pack = sub.add_parser("pack", parents=[common], help="pack canon + consumer into a single markdown")
    p_pack.add_argument("--out", required=True, help="output markdown path")

    p_check = sub.add_parser("check", parents=[common], help="check drift against ai-instructions.lock")
    p_check.add_argument("--project-dir", default=None, help="check a project directory instead of CWD")
    return parser


# region FUNC_main
## @purpose  Program entry point: parse, configure logging, dispatch, map errors to exit 1
## @io       in: argv (optional); out: exit code
## @complexity O(1)
def main(argv: list[str] | None = None) -> int:
    """▶ ┌argv┐ → ○ parse → ○ configure logging → ○ dispatch → ◇ error ? ⊕ FAIL → 1 : ○ return → ⎋ exit code"""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # usage errors exit 2 by default — collapse to the strict 0/1 exit-code contract
        return 0 if exc.code in {0, None} else 1
    _configure_logging(args.log_level)
    try:
        if args.command == "sync":
            return _cmd_sync(args)
        if args.command == "watch":
            return _cmd_watch(args)
        if args.command == "pack":
            return _cmd_pack(args)
        if args.command == "check":
            return _cmd_check(args)
    except KeyboardInterrupt:
        return 0
    except _CAUGHT as exc:
        logger.error("[IMP:10][CLI][FAIL] %s: %s", type(exc).__name__, exc)
        return 1
    return 0
# endregion FUNC_main
