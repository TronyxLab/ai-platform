#!/usr/bin/env python3
# GREP_SUMMARY: vhost-cli, vhost-renderer, argparse, render-all, add, remove, normalize-mode, legacy-flags, subcommands
# STRUCTURE: ▶ build_parser ┌render-all|add|remove┐ → ⚡ _normalize_mode (legacy --add/--remove/--render-all → subcommand) → ○ main ┌dispatch┐ → ⊕ exit 0|1|2|4 → ⎋ int
# region MODULE_CONTRACT
## @purpose  CLI-блок vhost_renderer (T3.7 god-file trim): argparse-парсер, legacy-
##           нормализация флагов и main()-диспетч. Бизнес-логика осталась в vhost_renderer.py —
##           этот модуль только CLI-слой (прецедент: node_yaml/cli.py).
## @scope    `python3 -m core.internal.scaffold.vhost_cli` И `python3 -m core.internal.scaffold.vhost_renderer`
##           (последний — через lazy __main__ + PEP 562 __getattr__-фасад) — оба пути каноничны.
## @invariants
##   - main/build_parser/_normalize_mode доступны как атрибуты vhost_renderer (тесты пинят
##     vmod.main([...])) — через module-level __getattr__ (без цикла импортов)
##   - Exit-коды: 0 ok · 2 DuplicateDomainError · 4 ConfigValidationError · 1 прочее
##   - Legacy-флаги add-vhost.sh (--add/--remove/--render-all, implicit add) нормализуются
##     в subcommands (DevPlan 173 W2.3 контракт)
## @rationale God-file trim (план simplify-refactor-waves T3.7): 230 LOC CLI-слоя не смешиваются
##            с render-ядром; ядро остаётся единственным импортируемым API.
## @changes  2026-08-22 · T3.7 — извлечён из vhost_renderer.py (CLI-блок verbatim)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import ClassVar

# T3.7 (цикл): НИКАКИХ top-level импортов из vhost_renderer — ядро импортируется ЛЕНИВО
# внутри main() (канон import-outside-top-level, defer §4.4 ruff.toml). Единственное
# статическое ребро: vhost_renderer → vhost_cli (реэкспорт CLI-фасада).
logger = logging.getLogger(__name__)


# region CLI


class _VhostArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    Subcommands (render-all/add/remove) set command + their own args; common
    options (platform_domain/platform_root/dev_domain_suffix/output_dir) live
    on the top-level parser.
    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    command: ClassVar[str | None]
    platform_domain: ClassVar[str | None]
    platform_root: ClassVar[str | None]
    dev_domain_suffix: ClassVar[str | None]
    output_dir: ClassVar[str | None]
    node: ClassVar[str]
    node_configs_dir: ClassVar[str]
    project_dir: ClassVar[str]


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser with 3 subcommands.

    ▶ ┌None┐ → ⊕ argparse.ArgumentParser → ⎋ parser
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        description="nginx vhost config manager — render, add, remove",
    )
    parser.add_argument(
        "--platform-domain",
        default=None,
        help="Platform wildcard domain (PLATFORM_DOMAIN) — subdomains use wildcard cert",
    )
    parser.add_argument("--platform-root", default=None, help="Platform root directory (for audit log path)")
    parser.add_argument(
        "--dev-domain-suffix",
        default=None,
        help="Dev-mode FQDN suffix (DEV_DOMAIN_SUFFIX) — fqdn = <project>.<suffix>, e.g. ai-platform.local",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override final overlay dir (default: <node-configs>/<node>/overlays/nginx); VHOST_OUTPUT_DIR env",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    # ── render-all subcommand ─────────────────────────────────────
    render_parser = subparsers.add_parser("render-all", help="Batch-render all vhosts from node.yaml")
    render_parser.add_argument("--node", required=True, help="Node name")
    render_parser.add_argument("--node-configs-dir", required=True, help="Path to node-configs/ directory")

    # ── add subcommand ────────────────────────────────────────────
    add_parser = subparsers.add_parser("add", help="Generate vhost for a single project")
    add_parser.add_argument(
        "--project-dir", required=True, help="Path to project directory (contains ai-platform.yaml)"
    )
    add_parser.add_argument("--node-configs-dir", required=True, help="Path to node-configs/ directory")

    # ── remove subcommand ─────────────────────────────────────────
    remove_parser = subparsers.add_parser("remove", help="Remove vhost for a project")
    remove_parser.add_argument(
        "--project-dir", required=True, help="Path to project directory (contains ai-platform.yaml)"
    )
    remove_parser.add_argument("--node-configs-dir", required=True, help="Path to node-configs/ directory")

    return parser


# region FUNC_normalize_mode
# DevPlan 173 W2.3: legacy flag-style интерфейс add-vhost.sh (--add/--remove/--render-all,
# implicit add при --project-dir) → нормализация в subcommand (add/remove/render-all).
_LEGACY_MODE_FLAGS: dict[str, str] = {"--add": "add", "--remove": "remove", "--render-all": "render-all"}
_SUBCOMMANDS: frozenset[str] = frozenset({"add", "remove", "render-all"})


def _normalize_mode(args: list[str]) -> list[str]:
    """Нормализовать legacy-флаги режима → subcommand (add/remove/render-all).

    ## @purpose — Порт parse_args() из add-vhost.sh (DevPlan 173 W2.3): --add/--remove/
    ##            --render-all → subcommand; без флага и без subcommand → implicit "add".
    ## @io — ⇥ args: list[str] → ⎋ list[str] (с subcommand-первым элементом)
    ## @complexity — O(N)
    ## @invariants
    ##   - Явный subcommand (add/remove/render-all) → без изменений
    ##   - Legacy-флаг → заменяется на subcommand (первый элемент)
    ##   - Ни флага, ни subcommand → prepend "add" (implicit default, канон add-vhost.sh)
    """
    if not args:
        return ["add"]
    # Legacy-флаг (в любом месте, кроме уже-разобранных опций) → subcommand
    for i, arg in enumerate(args):
        if arg in _LEGACY_MODE_FLAGS:
            rest = args[:i] + args[i + 1 :]
            return [_LEGACY_MODE_FLAGS[arg], *rest]
    # Явный subcommand первым → как есть
    if args[0] in _SUBCOMMANDS:
        return args
    # Implicit add (первый аргумент — опция, напр. --project-dir)
    return ["add", *args]


# endregion FUNC_normalize_mode


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for vhost_renderer.

    ▶ ┌sys.argv┐ → ◇ normalize mode → ◇ parse args → ◇ dispatch (render-all|add|remove) → ⊕ exit code

    ## @purpose — CLI entry with 3 subcommands (+legacy flags, DevPlan 173 W2.3).
    ##            Returns exit code for shell facade.
    ## @io — ⇥ argv: list[str] | None → ⎋ int — exit code (0 = success)
    ## @complexity — O(R) where R = render pipeline complexity
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    # T3.7: ленивый импорт ядра (разрыв цикла vhost_renderer ⇄ vhost_cli)
    from core.internal.scaffold.vhost_renderer import (
        DuplicateDomainError,
        ProjectEntry,
        load_vhost_config,
        remove_vhost,
        render_all,
        render_vhost,
    )
    from core.internal.shared.exceptions import ConfigValidationError

    parser = build_parser()
    # DevPlan 173 W2.3: legacy add-vhost.sh flags (--add/--remove/--render-all) → subcommand
    args = parser.parse_args(_normalize_mode(list(sys.argv[1:] if argv is None else argv)), namespace=_VhostArgs())

    # Resolve platform_domain: CLI arg > env var > node.yaml#node.domain (F-10) > None
    platform_domain: str | None = args.platform_domain or os.environ.get("PLATFORM_DOMAIN")
    if not platform_domain and args.node:
        node_yaml_path = Path(args.node_configs_dir) / args.node / "node.yaml"
        from core.internal.shared.exceptions import (
            ConfigNotFoundError,
            ConfigParseError,
            ConfigValidationError,
        )
        from core.internal.shared.node_yaml import NodeYaml

        try:
            resolved_domain = NodeYaml(str(node_yaml_path)).get("domain")
            if isinstance(resolved_domain, str) and resolved_domain:
                platform_domain = resolved_domain
                logger.info("[IMP:8][main] platform_domain resolved from node.yaml: %s", resolved_domain)
        except (ConfigNotFoundError, ConfigParseError, ConfigValidationError):
            logger.info("[IMP:7][main] node.yaml domain unavailable — wildcard resolution skipped")
    platform_root: str | None = args.platform_root or os.environ.get("PLATFORM_ROOT")
    # Resolve dev-mode suffix: CLI arg > env var > None (prod renders are unaffected)
    dev_domain_suffix: str | None = args.dev_domain_suffix or os.environ.get("DEV_DOMAIN_SUFFIX") or None
    # Resolve output dir override: CLI arg > env var > None (default)
    output_dir: str | None = args.output_dir or os.environ.get("VHOST_OUTPUT_DIR") or None

    # ruff: ignore[PLW0717] — нужно >5 свободных локальных переменных — извлечение неразумно
    try:
        if args.command == "render-all":
            node_yaml_path = Path(args.node_configs_dir) / args.node / "node.yaml"
            render_all(
                node_yaml_path=str(node_yaml_path),
                node_configs_dir=args.node_configs_dir,
                node=args.node,
                platform_domain=platform_domain,
                dev_domain_suffix=dev_domain_suffix,
                output_dir=output_dir,
            )
            return 0

        if args.command == "add":
            # Read project YAML
            config = load_vhost_config(args.project_dir)
            if config is None:
                logger.info("[IMP:8][main] No vhost config found — skipping vhost generation")
                return 0

            # Render vhost
            render_vhost(
                entry=ProjectEntry(name=config.name, domain=config.domain),
                node=config.target_node,
                node_configs_dir=args.node_configs_dir,
                platform_domain=platform_domain,
                output_dir=output_dir,
                dev_domain_suffix=dev_domain_suffix,
            )

            print("")
            print("──────────────────────────────────────────────────────")
            print("  ✅ nginx vhost создан")
            print("──────────────────────────────────────────────────────")
            print("")
            logger.info("[IMP:9][main] DONE: vhost for %s generated successfully", config.domain)
            return 0

        if args.command == "remove":
            # Read project YAML
            config = load_vhost_config(args.project_dir)
            if config is None:
                logger.info("[IMP:8][main] No project config found — skipping vhost removal")
                return 0

            # Remove vhost
            overlays_dir = Path(args.node_configs_dir) / config.target_node / "overlays" / "nginx"
            remove_vhost(
                project_name=config.name,
                overlays_dir=str(overlays_dir),
                platform_root=platform_root,
            )

            print("")
            print("──────────────────────────────────────────────────────")
            print("  ✅ nginx vhost удалён")
            print("──────────────────────────────────────────────────────")
            print("")
            logger.info("[IMP:9][main] DONE: vhost for %s removed successfully", config.domain)
            return 0

        parser.print_help()

    except DuplicateDomainError as e:
        logger.error("[IMP:10][main] FQDN uniqueness violation: %s", e)
        return 2

    except ConfigValidationError as e:
        # H18 (security hardening): невалидный fqdn/project_name — exit 4 (ConfigValidation).
        logger.error("[IMP:10][main] %s", e)
        return e.exit_code

    except RuntimeError as e:
        logger.error("[IMP:10][main] %s", e)
        return 1

    # ruff: ignore[BLE001] — top-level CLI handler for unexpected errors
    except Exception as e:  # noqa: EXC — top-level CLI handler for unexpected errors
        logger.error("[IMP:10][main] Unexpected error: %s", e)
        return 1
    else:
        return 1


# endregion CLI
