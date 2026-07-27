#!/usr/bin/env python3
# GREP_SUMMARY: project_registry, node-yaml, register-project, deregister-project, scaffold, idempotent
# STRUCTURE: ▶ register_project(name, repo, type, domain, database, node_yaml) → ◇ idempotency check → ◇ append entry → ⎋ yaml.dump
#            └ deregister_project(name, node_yaml) → ◇ filter projects list → ⎋ yaml.dump
#            └ list_projects(node_yaml) → ○ for each: stdout "name repo type domain" → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Project registry — single-source-of-truth for project registration/deregistration/listing
##           in node.yaml. Extracted from 3 independent Python heredoc blocks in scaffold scripts.
## @scope    Shell-accessible via CLI subcommands (register, deregister, list). Python-importable
##           for direct function calls. Used by add-project.sh, adopt-project.sh, remove-project.sh.
## @invariants
##   1. Library functions return (bool, str) tuple — True=success, False=error, message describes outcome
##   2. CLI __main__ still calls sys.exit(0/1) for shell compatibility — shell wrappers check exit code
##   3. Idempotent: register skips if name/repo already exists; deregister skips if not found
##   4. YAML written with default_flow_style=False, sort_keys=False (preserves ordering)
##   5. Logs to stderr at IMP:9 on success/skip, IMP:7-8 for warnings
## @rationale DRIFT-B5 elimination (Brief 077): 3 Python heredoc blocks → 1 canonical source.
##            sys.exit replaced with return (bool, str) in library functions for testability.
##            CLI layer handles exit code generation for shell compatibility.
## @changes  2026-07-25 · DevPlan 070 — Created shared module (DRIFT-B5)
##           2026-07-26 · DevPlan 038b — sys.exit removed from library functions, return (bool, str) instead
# endregion MODULE_CONTRACT

import logging
import re
import sys

logger = logging.getLogger(__name__)

# ── Project name validation ─────────────────────────────────────────────────
## @purpose  Canonical project name validation used by deploy_engine, payload_deliverer, reconciler.
##           Rejects empty names, path traversal sequences, and invalid characters.
## @invariants
##   - Regex: ^[a-zA-Z0-9_-]+$ — alphanumeric, underscore, hyphen only
##   - Returns bool (never raises, never sys.exit)
##   - DRY: single implementation shared by 3+ consumers
## @rationale D7 (DevPlan 036E): _validate_project_name() дублировалась в deploy-project.sh:207,
##            reconciler.py:701, и новой payload_deliverer.py. Единая реализация в project_registry.py
##            устраняет дублирование. Regex ^[a-zA-Z0-9_-]+$ строже shell-версии (reject '/..'/special chars).
## @changes 2026-07-26 · DevPlan 036E — Added validate_project_name() for Wave 5e Strangler-Fig


def validate_project_name(name: str) -> bool:
    """Validate project name: alphanumeric, underscore, hyphen only.

    Args:
        name: Project name string to validate.

    Returns:
        True if valid, False otherwise.
    """
    if not name or not isinstance(name, str):
        return False
    # Regex: alphanumeric, underscore, hyphen — no spaces, slashes, or path traversal
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", name))


# region FUNC_register_project
## @purpose — Register a project in node.yaml. Idempotent: skips if name/repo already exist.
##            Supports optional domain and database fields. Appends entry to projects list.
## @io — ⇥ name: str, repo: str, project_type: str = "", node_yaml_path: str = "",
##        domain: str = "", database: str = "", log_prefix: str = "add-project"
##        → ⎋ tuple[bool, str]: (True, message) on success, (False, message) on error
## @complexity — O(N) where N = len(projects)
## @invariants
##   - Idempotent: if project name or repo already exists → returns (True, "Idempotent SKIP...")
##   - Creates 'projects' key if missing
##   - Writes YAML with default_flow_style=False, sort_keys=False (preserves existing ordering)
##   - Logs to stderr at IMP:9 on success/skip
##   - Does NOT call sys.exit() — caller (CLI or test) handles exit code
## @rationale Extracted from add-project.sh:719 heredoc and adopt-project.sh:674 heredoc
##            (DRIFT-B5 elimination, Brief 077). Idempotency check prevents duplicate entries.
##            DevPlan 038b: sys.exit replaced with return tuple for testability.
def register_project(
    name: str,
    repo: str,
    project_type: str = "",
    node_yaml_path: str = "",
    domain: str = "",
    database: str = "",
    log_prefix: str = "add-project",
) -> tuple[bool, str]:
    """Register a project in node.yaml. Idempotent. Returns (success, message)."""
    try:
        import yaml
    except ImportError:
        msg = f"[IMP:10][{log_prefix}][register] PyYAML not available — cannot register"
        print(msg, file=sys.stderr)
        return (False, msg)

    if not name or not repo or not node_yaml_path:
        msg = (
            f"[IMP:7][{log_prefix}][register] Missing required params (name={name}, repo={repo}, yaml={node_yaml_path})"
        )
        print(msg, file=sys.stderr)
        return (False, msg)

    with open(node_yaml_path) as f:
        data = yaml.safe_load(f)

    if "projects" in data:
        for p in data["projects"]:
            if p.get("name") == name or p.get("repo") == repo:
                msg = f"[IMP:9][{log_prefix}][register] Idempotent SKIP — {name} already in node.yaml"
                print(msg, file=sys.stderr)
                return (True, msg)

    entry: dict[str, str] = {"name": name, "repo": repo}
    if project_type:
        entry["type"] = project_type
    if domain:
        entry["domain"] = domain
    if database:
        entry["database"] = database

    if "projects" not in data:
        data["projects"] = []
    data["projects"].append(entry)

    with open(node_yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    msg = f"[IMP:9][{log_prefix}][register] Registered {name} → {node_yaml_path}"
    print(msg, file=sys.stderr)
    return (True, msg)


# endregion FUNC_register_project


# region FUNC_deregister_project
## @purpose — Remove a project from node.yaml by name. Idempotent.
## @io — ⇥ name: str = "", node_yaml_path: str = "", log_prefix: str = "remove-project"
##        → ⎋ tuple[bool, str]: (True, message) on success, (False, message) on error
## @complexity — O(N) where N = len(projects)
## @invariants
##   - Idempotent: if project not found → returns (True, ...) (no error)
##   - Filters projects list, preserving all other entries
##   - Writes YAML with default_flow_style=False, sort_keys=False
##   - Reports removed count at IMP:9
##   - Does NOT call sys.exit() — caller handles exit code
## @rationale Extracted from remove-project.sh:212 heredoc (DRIFT-B5 elimination, Brief 077).
##            DevPlan 038b: sys.exit replaced with return tuple for testability.
def deregister_project(
    name: str = "",
    node_yaml_path: str = "",
    log_prefix: str = "remove-project",
) -> tuple[bool, str]:
    """Remove a project from node.yaml by name. Idempotent. Returns (success, message)."""
    try:
        import yaml
    except ImportError:
        msg = f"[IMP:10][{log_prefix}][unregister] PyYAML not available — cannot deregister"
        print(msg, file=sys.stderr)
        return (False, msg)

    if not name or not node_yaml_path:
        msg = f"[IMP:7][{log_prefix}][unregister] Missing required params (name={name}, yaml={node_yaml_path})"
        print(msg, file=sys.stderr)
        return (False, msg)

    with open(node_yaml_path) as f:
        data = yaml.safe_load(f)

    if "projects" not in data:
        msg = f"[IMP:8][{log_prefix}][unregister] No projects section — nothing to remove"
        print(msg, file=sys.stderr)
        return (True, msg)

    orig_count = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p.get("name") != name]
    removed = orig_count - len(data["projects"])

    with open(node_yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    msg = f"[IMP:9][{log_prefix}][unregister] Removed '{name}' from {node_yaml_path} ({removed} entries removed)"
    print(msg, file=sys.stderr)
    return (True, msg)


# endregion FUNC_deregister_project


# region FUNC_list_projects
## @purpose — List all projects registered in node.yaml. Outputs one line per project to stdout,
##            space-separated: name repo type domain. Empty fields output as "-".
## @io — ⇥ node_yaml_path: str = "", log_prefix: str = "list-projects"
##        → ⎋ tuple[bool, str]: (True, message) on success, (False, message) on error
## @complexity — O(N) where N = len(projects)
## @invariants
##   - Outputs to stdout (designed for shell `grep` / `while read` consumers)
##   - Empty projects list → returns (True, ...), no stdout output
##   - Missing projects key → returns (True, ...), no stdout output
##   - Errors (missing file, invalid YAML) → returns (False, ...) with message to stderr
##   - Does NOT call sys.exit() — caller handles exit code
## @rationale Extracted from duplicate project-existence checks in adopt-project.sh:687 and
##            add-project.sh:725 heredocs (DRIFT-B5 elimination, Brief 077).
##            Forward-looking: DevPlans 079/080 need project listing for drift detection.
##            DevPlan 038b: sys.exit replaced with return tuple for testability.
def list_projects(
    node_yaml_path: str = "",
    log_prefix: str = "list-projects",
) -> tuple[bool, str]:
    """List all projects. Outputs 'name repo type domain' per line to stdout. Returns (success, message)."""
    try:
        import yaml
    except ImportError:
        msg = f"[IMP:10][{log_prefix}][list] PyYAML not available"
        print(msg, file=sys.stderr)
        return (False, msg)

    if not node_yaml_path:
        msg = f"[IMP:7][{log_prefix}][list] Missing node_yaml_path"
        print(msg, file=sys.stderr)
        return (False, msg)

    try:
        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
    except (FileNotFoundError, yaml.YAMLError) as e:
        msg = f"[IMP:8][{log_prefix}][list] Failed to read {node_yaml_path}: {e}"
        print(msg, file=sys.stderr)
        return (False, msg)

    projects = data.get("projects", []) if isinstance(data, dict) else []
    for p in projects:
        name = p.get("name", "-") or "-"
        repo = p.get("repo", "-") or "-"
        ptype = p.get("type", "-") or "-"
        domain = p.get("domain", "-") or "-"
        print(f"{name} {repo} {ptype} {domain}")

    msg = f"[IMP:9][{log_prefix}][list] Listed {len(projects)} project(s) from {node_yaml_path}"
    print(msg, file=sys.stderr)
    return (True, msg)


# endregion FUNC_list_projects


# region FUNC_CLI
## @purpose — CLI entrypoint. Usage:
##   python3 project_registry.py register --name X --repo Y --type Z --node-yaml N [--domain D] [--database DB] [--log-prefix P]
##   python3 project_registry.py deregister --name X --node-yaml N [--log-prefix P]
##   python3 project_registry.py list --node-yaml N [--log-prefix P]
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Project Registry — register/deregister/list projects in node.yaml")
    sub = parser.add_subparsers(dest="action", required=True)

    reg = sub.add_parser("register", help="Register a project")
    reg.add_argument("--name", required=True)
    reg.add_argument("--repo", required=True)
    reg.add_argument("--type", default="")
    reg.add_argument("--node-yaml", required=True)
    reg.add_argument("--domain", default="")
    reg.add_argument("--database", default="")
    reg.add_argument("--log-prefix", default="add-project")

    dereg = sub.add_parser("deregister", help="Deregister a project")
    dereg.add_argument("--name", required=True)
    dereg.add_argument("--node-yaml", required=True)
    dereg.add_argument("--log-prefix", default="remove-project")

    lst = sub.add_parser("list", help="List all projects")
    lst.add_argument("--node-yaml", required=True)
    lst.add_argument("--log-prefix", default="list-projects")

    args = parser.parse_args()

    if args.action == "register":
        success, msg = register_project(
            name=args.name,
            repo=args.repo,
            project_type=getattr(args, "type", ""),
            node_yaml_path=getattr(args, "node_yaml", ""),
            domain=getattr(args, "domain", ""),
            database=getattr(args, "database", ""),
            log_prefix=args.log_prefix,
        )
        print(msg, file=sys.stderr)
        sys.exit(0 if success else 1)
    elif args.action == "deregister":
        success, msg = deregister_project(
            name=args.name,
            node_yaml_path=getattr(args, "node_yaml", ""),
            log_prefix=args.log_prefix,
        )
        print(msg, file=sys.stderr)
        sys.exit(0 if success else 1)
    elif args.action == "list":
        success, msg = list_projects(
            node_yaml_path=getattr(args, "node_yaml", ""),
            log_prefix=args.log_prefix,
        )
        print(msg, file=sys.stderr)
        sys.exit(0 if success else 1)


# endregion FUNC_CLI
