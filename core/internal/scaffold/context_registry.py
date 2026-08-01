#!/usr/bin/env python3
# GREP_SUMMARY: context_registry.py, register-context, yaml, platform-node-yaml, NodeYaml-facade
# STRUCTURE: ▶ register_context → NodeYaml.add_context → ⎋ "OK" | "EXISTS" | raise PlatformError | CLI (argparse: register, main()->int)
# region MODULE_CONTRACT
## @purpose  Register a context entry in platform node.yaml contexts[] list.
## @scope    CLI tool: register-context with yaml-path, name, desc, repos.
## @invariants
##   - Exits with 0 if context already exists ("EXISTS" response)
##   - Raises ConfigValidationError (validation) / PlatformFatalError (IO) — sys.exit только в main()/__main__
##     (DevPlan 116 B4 T3.4: business sys.exit → raise, контракт T4)
##   - Uses NodeYaml facade add_context() (DevPlan 116 B6 T6.4, D2) — последний raw-путь
##     мутации node.yaml закрыт (сырой дамп + yaml.dump удалены из этого модуля)
## @rationale Needed for scaffold automation — programmatic context registration
##            without manual node.yaml editing.
## @changes 2026-08-01 · DevPlan 116 B6 T6.4 (D2) — raw() + yaml.dump → NodeYaml.add_context()
##           2026-08-01 · DevPlan 116 B4 T3.4 — sys.exit → raise; main() -> int + sys.exit(main())
# endregion MODULE_CONTRACT

import argparse
import logging
import sys

from core.internal.shared.exceptions import PlatformError

logger = logging.getLogger(__name__)


def register_context(
    yaml_path: str,
    name: str,
    desc: str = "",
    node_cfg_repo: str = "",
    hermes_agent_repo: str = "",
) -> str:
    """Register a new context entry in the platform node.yaml contexts[] list.

    DevPlan 116 B6 T6.4 (D2): raw-мутация (сырой дамп NodeYaml + yaml.dump) заменена на
    фасадный NodeYaml.add_context(). Дубликат (ConfigValidationError) → "EXISTS".

    Args:
        yaml_path: Path to the platform node.yaml file
        name: Context name to register
        desc: Optional description
        node_cfg_repo: Optional node configs repo URL
        hermes_agent_repo: Optional hermes agent repo URL

    Returns:
        "OK" on success, "EXISTS" if context already registered

    Raises:
        ConfigValidationError: validation-ошибка (кроме дубликата)
        PlatformFatalError: YAML read/write errors (IO, требует вмешательства)
    """
    try:
        from core.internal.shared.exceptions import (
            ConfigNotFoundError,
            ConfigParseError,
            ConfigValidationError,
            PlatformFatalError,
        )
        from core.internal.shared.node_yaml import NodeYaml

        NodeYaml(yaml_path).add_context(
            name=name,
            description=desc,
            node_configs_repo=node_cfg_repo,
            hermes_agent_repo=hermes_agent_repo,
        )
    except ConfigValidationError as e:
        # Duplicate context name → "EXISTS" (контракт сохранён);
        # прочие validation-ошибки (contexts не list) → raise (T3.4: sys.exit(1) → raise).
        if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
            return "EXISTS"
        raise
    except (ConfigNotFoundError, ConfigParseError) as e:
        # IO-ошибки чтения/записи node.yaml — невосстановимо без ручного действия → PlatformFatalError (T3.4)
        raise PlatformFatalError(f"Failed to read/write {yaml_path}: {e}") from e

    return "OK"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register a context in platform node.yaml")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    register_parser = subparsers.add_parser("register", help="Register a new context entry")
    register_parser.add_argument("--yaml-path", required=True, help="Path to platform node.yaml")
    register_parser.add_argument("--name", required=True, help="Context name")
    register_parser.add_argument("--desc", default="", help="Context description")
    register_parser.add_argument("--node-cfg-repo", default="", help="Node configs repo URL")
    register_parser.add_argument("--hermes-agent-repo", default="", help="Hermes agent repo URL")

    args = parser.parse_args(argv)

    try:
        if args.command == "register":
            result = register_context(
                yaml_path=args.yaml_path,
                name=args.name,
                desc=args.desc,
                node_cfg_repo=args.node_cfg_repo,
                hermes_agent_repo=args.hermes_agent_repo,
            )
            print(result)
        return 0
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())
