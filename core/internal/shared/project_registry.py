#!/usr/bin/env python3
# GREP_SUMMARY: project_registry, node-yaml, register-project, deregister-project, scaffold, idempotent, NodeYaml-bridge
# STRUCTURE: ▶ register_project → ◇ NodeYaml.add_project → ◇ soft-idempotency bridge (ConfigValidationError→skip) → ⎋ (bool, str)
#            └ deregister_project → ◇ NodeYaml.remove_project → ⎋ (bool, str)
#            └ list_projects → ◇ NodeYaml.get_projects → ⊕ stdout: "name repo type domain" → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Project registry — thin wrapper over NodeYaml for project registration/deregistration/listing.
##           DevPlan 091 Wave C (AC2): migrated from yaml.safe_load/dump to NodeYaml.add_project/remove_project/get_projects.
##           Soft-idempotency preserved via ConfigValidationError bridge for register (hard-error → soft skip).
## @scope    Shell-accessible via CLI subcommands. Python-importable for direct function calls.
## @invariants
##   1. Library functions return (bool, str) tuple
##   2. CLI __main__ calls sys.exit(0/1) for shell compatibility
##   3. Idempotent: register skips via ConfigValidationError catch; deregister is nodeyaml-idempotent
##   4. No direct yaml.safe_load/dump — all YAML ops through NodeYaml
##   5. Logs to stderr at IMP:9 on success/skip, IMP:7-8 for warnings
## @rationale DRIFT-088-7: 3 yaml.safe_load calls were bypassing NodeYaml facade, creating
##            a parallel node.yaml mutation path that didn't benefit from NodeYaml validation,
##            error handling, and mutation safety. Now a thin bridge — consumers unchanged,
##            but node.yaml access is unified through a single facade.
## @changes  2026-07-25 · DevPlan 070 — Created
##           2026-07-26 · DevPlan 038b — sys.exit replaced with return tuple
##           2026-07-30 · DevPlan 091 Wave C — yaml.safe_load → NodeYaml bridge (AC2)
# endregion MODULE_CONTRACT

import logging
import os
import re
import sys

# Standalone CLI bootstrap: when run directly (subprocess), add project root to sys.path
# so that `from core.internal.shared.*` imports resolve. This is the same pattern used
# in context_deployer.py and other CLI-accessible shared modules.
if __name__ == "__main__" or not __package__:
    _project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

logger = logging.getLogger(__name__)

# DevPlan 091 Wave C (AC2): NodeYaml replaces yaml.safe_load/dump.
# Imports are module-level — the sys.path bootstrap above ensures they resolve
# in standalone CLI (subprocess) mode. For pytest, rootdir = project root.
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml, ProjectEntry

# ── Project name validation ─────────────────────────────────────────────────
## @purpose  Canonical project name validation used by deploy_engine, payload_deliverer, reconciler,
##           context_initializer, project_scaffolder. Rejects empty names, path traversal sequences,
##           invalid characters, and leading '-'/'_' (strict regex, DevPlan 116 B6 T3).
## @invariants
##   - Regex: ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ — must start with alphanumeric; hyphen/underscore allowed
##     only after the first char. STRICT: rejects leading '-'/'_' (эквивалентен бывшему
##     контекстному валидатору context_initializer).
##   - Returns bool (never raises, never sys.exit)
##   - DRY: single implementation shared by 5+ consumers
## @rationale D7 (DevPlan 036E): приватный валидатор имён дублировался в deploy-project.sh:207,
##            reconciler.py:701, и новой payload_deliverer.py. Единая реализация в project_registry.py
##            устраняет дублирование. Regex ^[a-zA-Z0-9_-]+$ строже shell-версии (reject '/..'/special chars).
##            DevPlan 116 B6 T3 (U-06): regex ужесточён до ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ — reject leading
##            '-'/'_' (эквивалент контекстного валидатора); все 3 локальных валидатора
##            (reconciler, context_initializer, project_scaffolder strip-check)
##            мигрированы на этот канон.
## @changes 2026-07-26 · DevPlan 036E — Added validate_project_name() for Wave 5e Strangler-Fig
## @changes 2026-08-01 · DevPlan 116 B6 T3 — regex ужесточён: leading '-'/'_' rejected


def validate_project_name(name: str) -> bool:
    """Validate project name: alphanumeric first char, then alphanumeric/underscore/hyphen.

    Args:
        name: Project name string to validate.

    Returns:
        True if valid, False otherwise.
    """
    if not name or not isinstance(name, str):
        return False
    # Strict regex: must start [a-zA-Z0-9]; then [a-zA-Z0-9_-]* — no spaces, slashes,
    # path traversal ('.' not in class), or leading '-'/'_' (DevPlan 116 B6 T3).
    return bool(re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", name))


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
    """Register a project in node.yaml via NodeYaml. Idempotent. Returns (success, message).

    DevPlan 091 Wave C (AC2/DRIFT-088-7): replaced yaml.safe_load/dump with NodeYaml.add_project().
    Soft-idempotency preserved: NodeYaml.add_project() raises ConfigValidationError on duplicate →
    caught and translated to (True, "Idempotent SKIP") to maintain the existing consumer contract.
    Signal signature unchanged — consumers (project_adopter.py, CLI) are not affected.
    """
    if not name or not repo or not node_yaml_path:
        msg = (
            f"[IMP:7][{log_prefix}][register] Missing required params (name={name}, repo={repo}, yaml={node_yaml_path})"
        )
        print(msg, file=sys.stderr)
        return (False, msg)

    try:
        ny = NodeYaml(node_yaml_path)

        # Pre-check: repo-based idempotency (backward-compat with old yaml.safe_load path).
        # NodeYaml.add_project() only guards on name; the old code also checked repo.
        for p in ny.get_projects():
            if p.get("repo") == repo and p.get("name") != name:
                msg = f"[IMP:9][{log_prefix}][register] Idempotent SKIP — {name} already in node.yaml (repo duplicate: {repo})"
                print(msg, file=sys.stderr)
                return (True, msg)

        project = ProjectEntry(
            name=name,
            repo=repo,
            type=project_type,
            domain=domain,
            database=database,
        )
        ny.add_project(project)
        msg = f"[IMP:9][{log_prefix}][register] Registered {name} → {node_yaml_path}"
        print(msg, file=sys.stderr)
        return (True, msg)
    except ConfigValidationError as e:
        # Bridge: NodeYaml hard-error on duplicate → soft-idempotent skip
        if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
            msg = f"[IMP:9][{log_prefix}][register] Idempotent SKIP — {name} already in node.yaml"
            print(msg, file=sys.stderr)
            return (True, msg)
        msg = f"[IMP:10][{log_prefix}][register] Validation error: {e}"
        print(msg, file=sys.stderr)
        return (False, msg)
    except (OSError, ValueError) as e:
        msg = f"[IMP:10][{log_prefix}][register] Failed to register {name}: {e}"
        print(msg, file=sys.stderr)
        return (False, msg)


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
    """Remove a project from node.yaml by name via NodeYaml. Idempotent. Returns (success, message).

    DevPlan 091 Wave C (AC2): replaced yaml.safe_load/dump with NodeYaml.remove_project().
    NodeYaml.remove_project() returns bool (True=removed, False=not found) → wrapped in tuple.
    """
    if not name or not node_yaml_path:
        msg = f"[IMP:7][{log_prefix}][unregister] Missing required params (name={name}, yaml={node_yaml_path})"
        print(msg, file=sys.stderr)
        return (False, msg)

    try:
        ny = NodeYaml(node_yaml_path)

        # Pre-check: count existing projects for backward-compat message format.
        existing_projects = ny.get_projects()
        if not existing_projects:  # type: ignore[truthy-function]
            msg = f"[IMP:8][{log_prefix}][unregister] No projects section — nothing to remove"
            print(msg, file=sys.stderr)
            return (True, msg)

        orig_count = len(existing_projects)  # type: ignore[arg-type]
        removed = ny.remove_project(name)

        if removed:
            removed_count = orig_count - len(ny.get_projects())  # type: ignore[arg-type]
            msg = f"[IMP:9][{log_prefix}][unregister] Removed '{name}' from {node_yaml_path} ({removed_count} entries removed)"
        else:
            msg = f"[IMP:8][{log_prefix}][unregister] Removed '{name}' from {node_yaml_path} (0 entries removed)"
        print(msg, file=sys.stderr)
        return (True, msg)
    except (OSError, ValueError) as e:
        msg = f"[IMP:10][{log_prefix}][unregister] Failed to deregister {name}: {e}"
        print(msg, file=sys.stderr)
        return (False, msg)


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
    """List all projects via NodeYaml.get_projects(). Outputs 'name repo type domain' per line.

    DevPlan 091 Wave C (AC2): replaced yaml.safe_load with NodeYaml.get_projects().
    """
    if not node_yaml_path:
        msg = f"[IMP:7][{log_prefix}][list] Missing node_yaml_path"
        print(msg, file=sys.stderr)
        return (False, msg)

    try:
        from core.internal.shared.exceptions import ConfigNotFoundError

        ny = NodeYaml(node_yaml_path)
        projects = ny.get_projects()
    except ConfigNotFoundError:
        msg = f"[IMP:8][{log_prefix}][list] Failed to read {node_yaml_path}: FileNotFoundError"
        print(msg, file=sys.stderr)
        return (False, msg)
    except (OSError, ValueError, FileNotFoundError) as e:
        msg = f"[IMP:8][{log_prefix}][list] Failed to read {node_yaml_path}: {e}"
        print(msg, file=sys.stderr)
        return (False, msg)

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
