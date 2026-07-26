#!/usr/bin/env python3
# GREP_SUMMARY: vhost_yaml_reader.py, read-projects, node.yaml, vhost
# STRUCTURE: ▶ read_projects() → ⎋ CLI (argparse: read-projects)
"""
Read project names and domains from node.yaml for vhost generation.

Usage:
    python3 vhost_yaml_reader.py read-projects --yaml-path <path>

Outputs JSON lines:
    {"name": "...", "domain": "..."}
One line per project with a non-empty 'domain' field.
Exits with 0 (no output) if no projects with domain found.
"""

import argparse
import json
import sys

import yaml


def read_projects(yaml_path: str) -> list[dict[str, str]]:
    """Parse node.yaml and extract project entries with domain.

    Args:
        yaml_path: Path to node.yaml file

    Returns:
        List of dicts with 'name' and 'domain' keys for projects
        that have both fields set. Empty list if no projects or yaml missing.

    Raises:
        SystemExit(1) on YAML parse errors
    """
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"[IMP:9][read_node_yaml] ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    projects = data.get("projects", []) if data else []
    if not isinstance(projects, list):
        return []

    result: list[dict[str, str]] = []
    for p in projects:
        if not isinstance(p, dict):
            continue
        name = p.get("name", "")
        domain = p.get("domain", "")
        if name and domain:
            result.append({"name": name, "domain": domain})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Read projects from node.yaml for vhost generation")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    read_parser = subparsers.add_parser("read-projects", help="Read projects with domain from node.yaml")
    read_parser.add_argument("--yaml-path", required=True, help="Path to node.yaml file")

    args = parser.parse_args()

    if args.command == "read-projects":
        projects = read_projects(args.yaml_path)
        for p in projects:
            print(json.dumps(p))


if __name__ == "__main__":
    main()
