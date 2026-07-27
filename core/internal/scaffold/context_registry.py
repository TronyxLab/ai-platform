#!/usr/bin/env python3
# GREP_SUMMARY: context_registry.py, register-context, yaml, platform-node-yaml
# STRUCTURE: ▶ register_context → ⎋ CLI (argparse: register)
# region MODULE_CONTRACT
## @purpose  Register a context entry in platform node.yaml contexts[] list.
## @scope    CLI tool: register-context with yaml-path, name, desc, repos.
## @invariants
##   - Exits with 0 if context already exists ("EXISTS" response)
##   - Exits with 1 on YAML read/write errors
##   - Uses PyYAML safe_load — no arbitrary code execution
## @rationale Needed for scaffold automation — programmatic context registration
##            without manual node.yaml editing.
# endregion MODULE_CONTRACT

import argparse
import sys

import yaml


def register_context(
    yaml_path: str,
    name: str,
    desc: str = "",
    node_cfg_repo: str = "",
    hermes_agent_repo: str = "",
) -> str:
    """Register a new context entry in the platform node.yaml contexts[] list.

    Args:
        yaml_path: Path to the platform node.yaml file
        name: Context name to register
        desc: Optional description
        node_cfg_repo: Optional node configs repo URL
        hermes_agent_repo: Optional hermes agent repo URL

    Returns:
        "OK" on success, "EXISTS" if context already registered

    Raises:
        SystemExit(1) on YAML read/write errors
    """
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError, FileNotFoundError) as e:
        print(f"ERROR: Failed to read {yaml_path}: {e}")
        sys.exit(1)

    if "contexts" not in data or data["contexts"] is None:
        data["contexts"] = []

    for ctx in data["contexts"]:
        if isinstance(ctx, dict) and ctx.get("name") == name:
            return "EXISTS"

    new_entry = {"name": name}
    if desc:
        new_entry["description"] = desc
    if node_cfg_repo:
        new_entry["node_configs_repo"] = node_cfg_repo
    if hermes_agent_repo:
        new_entry["hermes_agent_repo"] = hermes_agent_repo

    data["contexts"].append(new_entry)

    try:
        with open(yaml_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except (yaml.YAMLError, OSError) as e:
        print(f"ERROR: Failed to write {yaml_path}: {e}")
        sys.exit(1)

    return "OK"


def main() -> None:
    parser = argparse.ArgumentParser(description="Register a context in platform node.yaml")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    register_parser = subparsers.add_parser("register", help="Register a new context entry")
    register_parser.add_argument("--yaml-path", required=True, help="Path to platform node.yaml")
    register_parser.add_argument("--name", required=True, help="Context name")
    register_parser.add_argument("--desc", default="", help="Context description")
    register_parser.add_argument("--node-cfg-repo", default="", help="Node configs repo URL")
    register_parser.add_argument("--hermes-agent-repo", default="", help="Hermes agent repo URL")

    args = parser.parse_args()

    if args.command == "register":
        result = register_context(
            yaml_path=args.yaml_path,
            name=args.name,
            desc=args.desc,
            node_cfg_repo=args.node_cfg_repo,
            hermes_agent_repo=args.hermes_agent_repo,
        )
        print(result)


if __name__ == "__main__":
    main()
