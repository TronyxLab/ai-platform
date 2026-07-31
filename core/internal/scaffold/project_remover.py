#!/usr/bin/env python3
# GREP_SUMMARY: project_remover remove-project lifecycle unregister node-yaml compose-down vhost-safe report ssh
# STRUCTURE: ▶ parse_args → find_project_in_node_yaml → ◇ [not found → SKIP exit 0] → confirm → unregister → remove_vhost → ssh_compose_down → print_report → ⊕ exit 0
# region MODULE_CONTRACT
## @purpose  Python Strangler-Fig migration of remove-project.sh (423 LOC shell, 2 inline python3).
##           Remove a project from the platform lifecycle: unregister from node.yaml,
##           stop containers on target node (compose down WITHOUT -v), deactivate vhost.
##           Volumes, databases, images, and GitHub repo are NEVER touched (O7/DD10).
## @scope    Called from remove-project.sh facade. Reads/writes node.yaml via NodeYaml API.
## @invariants
##   - NEVER runs `docker compose down -v`, `docker volume rm`, `docker image rm`, `gh repo delete` (O7/DD10)
##   - Idempotent: second call with same project → SKIP with exit 0
##   - VPS unavailability → unregister + vhost removal execute, SSH step skipped + instruction printed
##   - Project not found in node.yaml → SKIP with exit 0
##   - Prints report of what was NOT deleted (volumes, DB images, GitHub repo, local dir)
## @rationale Completes the project lifecycle (CREATE→REGISTER→DEPLOY→REMOVE). Safe-only (O7): no automatic data deletion.
## @links    CALLED_BY: remove-project.sh (facade)
##           CALLS: NodeYaml.remove_project(), lib/ssh.sh::ssh_exec()
##           CONTRACTS: O7/DD10 — remove = disconnect, not destroy
##           DP-092 Wave 3
## @changes  2026-07-30 · Wave 3 — initial implementation
# endregion MODULE_CONTRACT

# 💼 TRAP[BUSINESS] · 2026-07-17 · HI · remove = disconnect, данные не удаляются автоматически
# · Source: owner
# · Risk: авто-очистка = невосстановимая потеря БД проекта

# ⚠️ TRAP[BUG] node_yaml.py:1186 (DP-088) — remove_project удаляет ВСЕ записи с matching name
# · Symptom: list comprehension filter удаляет все дубликаты, не только первую запись
# · Root: remove_project uses [p for p in projects if p.get('name') != name]
# · Fix: Документированное поведение — для project_remover это предпочтительно (cleanup corrupted data)
# · Prevention: негативный тест test_unregister_removes_all_duplicates

from __future__ import annotations

import argparse
import glob as glob_module
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Path defaults ────────────────────────────────────────────────────────
_DEFAULT_PROJECTS_ROOT = os.environ.get(
    "PROJECTS_ROOT",
    str(Path(__file__).resolve().parent.parent.parent.parent),
)


# region FUNC_find_project_in_node_yaml
## @purpose  Locate the node.yaml that contains the project.
##           If --node provided, search only that node's config.
##           Otherwise search all node-configs/*/node.yaml.
## @param name           Project name
## @param projects_root  Base directory
## @param node_filter    Optional node name
## @return   dict with keys: node_yaml(str), project_entry(dict), domain(str), host(str), org(str), node_configs_dir(str)
##           or empty dict if not found
## @complexity O(n·m) where n = node.yaml files, m = projects per file
def find_project_in_node_yaml(
    name: str,
    projects_root: Path,
    node_filter: str = "",
) -> dict[str, Any]:
    """Search all node.yaml files for a project by name.

    ## @purpose  Mirror of find_node_yaml() from remove-project.sh:110-166.
    ##           Replaces 2 inline python3 blocks (field extraction: domain, repo).
    ## @io        ⇥ name, projects_root, node_filter → ⎋ dict with project details
    ## @complexity O(n·m)
    ## @invariants
    ##   - Returns {} if project not found (caller handles idempotent exit)
    ##   - Uses NodeYaml API, not subprocess yq
    """
    logger.info("[IMP:7][remove][find] Searching for project '%s' in node.yaml files", name)

    # Find all node.yaml files
    search_pattern = f"*/*/node-configs/{node_filter}/node.yaml" if node_filter else "*/*/node-configs/*/node.yaml"
    glob_pattern = str(projects_root / search_pattern)
    yaml_files = sorted(Path(p) for p in glob_module.glob(glob_pattern) if Path(p).exists())

    # Fallback: broader search
    if not yaml_files:
        broader = "*/node-configs/*/node.yaml"
        yaml_files = sorted(Path(p) for p in glob_module.glob(str(projects_root / broader)) if Path(p).exists())

    logger.info("[IMP:7][remove][find] Found %d node.yaml file(s) to search", len(yaml_files))

    for ny in yaml_files:
        logger.info("[IMP:6][remove][find] Checking: %s", ny)

        try:
            from core.internal.shared.node_yaml import NodeYaml

            node = NodeYaml(str(ny))
            project = node.get_project(name)
            if project is None:
                continue

            # Found! Extract details — NO inline python3
            domain = project.get("domain", "")
            repo = project.get("repo", "")
            org = repo.split("/")[0] if "/" in repo else ""
            node_host = node._data.get("node", {}).get("host", "") if node._data else ""
            node_configs_dir = str(ny.parent.parent)  # .../node-configs/<node>/node.yaml → .../node-configs/

            logger.info("[IMP:7][remove][find] Found project '%s' in: %s", name, ny)
            logger.info(
                "[IMP:8][remove][find]   domain=%s host=%s org=%s",
                domain or "<none>",
                node_host or "<unknown>",
                org or "unknown",
            )

            return {
                "node_yaml": str(ny),
                "project_entry": project,
                "domain": domain,
                "host": node_host,
                "org": org,
                "node_configs_dir": node_configs_dir,
            }
        except (ImportError, ValueError, OSError) as exc:
            logger.info("[IMP:8][remove][find] Error reading %s: %s", ny, exc)
            continue

    logger.info("[IMP:8][remove][find] Project '%s' not found in any node.yaml", name)
    return {}


# endregion FUNC_find_project_in_node_yaml


# region FUNC_unregister_from_node_yaml
## @purpose  Remove the project entry from node.yaml using NodeYaml mutation API.
##           Preserves all other projects and YAML structure via ruamel.yaml.
## @param node_yaml_path  Path to node.yaml
## @param name            Project name to remove
## @return   True on success, False on failure
## @complexity O(p) where p = projects count
## @invariants
##   - TRAP node_yaml.py:1186 — removes ALL duplicates (documented, preferred for cleanup)
##   - Pure Python — no subprocess yq
def unregister_from_node_yaml(node_yaml_path: str, name: str) -> bool:
    """Remove project entry from node.yaml.

    ## @purpose  Mirror of unregister_from_node_yaml() from remove-project.sh:176-198.
    ## @io        ⇥ node_yaml_path, name → ⎋ bool — True if removed
    ## @complexity O(p)
    """
    logger.info("[IMP:7][remove][unregister] Unregistering '%s' from: %s", name, node_yaml_path)

    try:
        from core.internal.shared.node_yaml import NodeYaml

        node = NodeYaml(node_yaml_path)
        removed = node.remove_project(name)
        if removed:
            logger.info("[IMP:9][remove][unregister] NodeYaml: removed '%s' from %s", name, node_yaml_path)
            return True
        logger.info("[IMP:9][remove][unregister] Project '%s' not found — idempotent (nothing to remove)", name)
        return False
    except (ImportError, ValueError, OSError, TypeError) as exc:
        logger.info("[IMP:8][remove][unregister] NodeYaml remove_project failed: %s", exc)
        return False


# endregion FUNC_unregister_from_node_yaml


# region FUNC_remove_vhost
## @purpose  Remove nginx vhost file for the project, if a domain is configured.
##           The vhost file lives at <node-configs>/<node\>/overlays/nginx/<domain\>.conf
## @param domain            Domain name (empty = skip)
## @param node_configs_dir  Path to node-configs directory
## @return   True if removed or skipped, False on error
## @complexity O(1)
def remove_vhost(domain: str, node_configs_dir: str) -> bool:
    """Remove nginx vhost configuration file.

    ## @purpose  Mirror of remove_vhost() from remove-project.sh:207-228.
    ## @io        ⇥ domain, node_configs_dir → ⎋ bool
    ## @complexity O(1)
    """
    if not domain or not domain.strip():
        logger.info("[IMP:6][remove][vhost] No domain configured — skipping vhost removal")
        logger.info("[IMP:9][remove][vhost] Vhost removal skipped (no domain)")
        return True  # skip is success

    if not node_configs_dir:
        logger.info("[IMP:6][remove][vhost] No node-configs dir known — skipping vhost removal")
        logger.info("[IMP:9][remove][vhost] Vhost removal skipped (no node-configs dir)")
        return True

    # Derive node name from path: .../node-configs/<node> → node name
    _node_name = Path(node_configs_dir).name if Path(node_configs_dir).is_dir() else "unknown"
    vhost_file = Path(node_configs_dir) / "overlays" / "nginx" / f"{domain}.conf"

    if not vhost_file.exists():
        logger.info("[IMP:6][remove][vhost] Vhost file not found: %s — SKIP", vhost_file)
        return True

    logger.info("[IMP:7][remove][vhost] Removing nginx vhost: %s", vhost_file)
    vhost_file.unlink()
    logger.info("[IMP:9][remove][vhost] Vhost removed: %s", vhost_file)
    return True


# endregion FUNC_remove_vhost


# region FUNC_ssh_compose_down
## @purpose  SSH to target node and run docker compose down (WITHOUT -v) for the project.
##           Uses ci-deploy user if available, falls back to current user.
## @param host       SSH host (IP or domain)
## @param project    Project name
## @param ssh_runner Injectable callable for testing: (host, user, cmd, timeout) → (rc, output)
## @return   True on success, False on failure (VPS unreachable)
## @complexity O(w) where w = wait time for SSH connection
## @invariants
##   - NEVER includes `-v` flag — volumes preserved (O7/DD10)
##   - Tries ci-deploy user first, then current user
##   - Timeout enforced via ssh_exec
def ssh_compose_down(
    host: str,
    project: str,
    ssh_runner: Callable | None = None,
) -> bool:
    """Stop project containers on remote node via SSH (compose down, NO -v).

    ## @purpose  Mirror of ssh_compose_down() from remove-project.sh:240-302.
    ## @io        ⇥ host, project, ssh_runner → ⎋ bool — True on success
    ## @complexity O(w)
    ## @invariants
    ##   - NO `-v` flag per O7/DD10
    ##   - Command: `docker compose down --timeout 30` (NO -v)
    """
    if not host:
        logger.info("[IMP:8][remove][ssh] No SSH host available — skipping remote compose down")
        logger.info("[IMP:8][remove][ssh]   Manual step: ssh <host> 'docker compose -p %s down'", project)
        return False

    logger.info("[IMP:7][remove][ssh] Connecting to %s to stop project '%s' containers...", host, project)

    # Default SSH runner
    if ssh_runner is None:

        def _default_ssh(h: str, u: str, cmd: str, timeout: int = 120) -> tuple[int, str]:
            """Execute command on remote host via ssh_exec from lib/ssh.sh."""
            try:
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        f'source core/lib/ssh.sh && ssh_exec "{h}" "{u}" "{cmd}" {timeout}',
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout + 10,
                )
                return result.returncode, result.stdout
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return 1, "SSH timeout or unavailable"

        ssh_runner = _default_ssh

    import getpass

    current_user = os.environ.get("USER") or getpass.getuser()

    # Try ci-deploy user first, then current user
    for try_user in ("ci-deploy", current_user):
        effective_user = try_user if try_user else current_user
        logger.info("[IMP:6][remove][ssh]   Attempting SSH as: %s@%s", effective_user, host)

        # Test connection first
        rc, _ = ssh_runner(host, effective_user, "echo OK", timeout=10)
        if rc != 0:
            continue

        # Run compose down WITHOUT -v per O7/DD10
        compose_cmd = (
            f"cd /opt/projects/{project} 2>/dev/null && "
            f"docker compose down --timeout 30 2>&1 || "
            f"docker compose -p {project} down --timeout 30 2>&1"
        )

        rc, output = ssh_runner(host, effective_user, compose_cmd, timeout=120)
        if rc == 0:
            logger.info("[IMP:7][remove][ssh] docker compose down output:")
            for line in output.splitlines():
                line_stripped = line.strip()
                if line_stripped:
                    logger.info("[IMP:6][remove][ssh]   %s", line_stripped)

            logger.info("[IMP:9][remove][ssh] Containers stopped for '%s' on %s (compose down, NO -v)", project, host)
            return True

        logger.info("[IMP:8][remove][ssh] SSH command returned exit code %d", rc)
        logger.info("[IMP:8][remove][ssh]   Output: %s", output[:200])

    logger.info("[IMP:8][remove][ssh] SSH connection failed — VPS may be unavailable")
    logger.info("[IMP:8][remove][ssh]   Manual step: ssh <host> 'docker compose -p %s down'", project)
    return False


# endregion FUNC_ssh_compose_down


# region FUNC_print_report
## @purpose  Print a human-readable report of what was done and what was NOT deleted.
## @param name           Project name
## @param vhost_removed  Whether vhost was removed (bool or None)
## @param ssh_done       Whether SSH compose down succeeded (bool or None)
## @io        stdout: formatted report
## @rationale O7/DD10: user must be explicitly reminded that data persists.
def print_report(name: str, vhost_removed: bool, ssh_done: bool) -> None:
    """Print safe-remove report.

    ## @purpose  Mirror of print_report() from remove-project.sh:310-342.
    ## @io        ⇥ name, vhost_removed, ssh_done → ⎋ stdout
    """
    print()
    print("────────────────────────────────────────────────────────────")
    print(f"  ✅ remove-project: {name}")
    print("────────────────────────────────────────────────────────────")
    print()
    print("  Removed:")
    print("    ✔ Unregistered from node.yaml")
    if vhost_removed:
        print("    ✔ Nginx vhost deactivated")
    if ssh_done:
        print("    ✔ Containers stopped (compose down)")
    print()
    print("  ❗ NOT deleted (safe remove O7/DD10 — manual cleanup required):")
    print(f"    ❌ Docker volumes — run: docker volume ls | grep {name}")
    print("    ❌ Database — DROP DATABASE on postgres if needed")
    print(f"    ❌ Docker images — run: docker image ls | grep {name}")
    print(f"    ❌ GitHub repo — run: gh repo delete <org>/{name}")
    print("    ❌ Local project directory — run: rm -rf <project_dir>")
    if not ssh_done:
        print()
        print("  ⚠️  VPS was unreachable — SSH step SKIPPED.")
        print(f"     Manual: ssh <host> 'cd /opt/projects/{name} && docker compose down'")
    print()
    print("────────────────────────────────────────────────────────────")


# endregion FUNC_print_report


# region FUNC_main
## @purpose  Main entry point — orchestrate safe project removal
## @io        stdout: progress + report; stderr: LDD logs; exit 0 on success/idempotent, 1 on error
## @complexity O(n·m) for find + O(p) for unregister + O(w) for SSH
def main(argv: list[str] | None = None) -> None:
    """CLI dispatcher for project removal.

    ## @purpose  Parse args, find project, unregister, remove vhost, SSH compose down, print report.
    ## @io        ⇥ argv → ⎋ None (sys.exit)
    ## @complexity O(n·m + p + w)
    """
    parser = argparse.ArgumentParser(
        description="Remove a project from the platform lifecycle (SAFE — no data loss).",
    )
    parser.add_argument("--name", required=True, help="Project name to remove")
    parser.add_argument("--node", dest="node_name", default="", help="Target node name (searched in all if omitted)")
    parser.add_argument("--force", action="store_true", default=False, help="Skip confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Show plan without executing")
    parser.add_argument("--projects-root", default=_DEFAULT_PROJECTS_ROOT, help="Override PROJECTS_ROOT")

    args = parser.parse_args(argv)

    logger.info(
        "[IMP:7][remove][main] Args: name=%s node=%s force=%s dry-run=%s",
        args.name,
        args.node_name or "<auto>",
        args.force,
        args.dry_run,
    )

    # ── Find project in node.yaml ──
    project_info = find_project_in_node_yaml(
        name=args.name,
        projects_root=Path(args.projects_root),
        node_filter=args.node_name,
    )

    if not project_info:
        logger.info(
            "[IMP:9][remove][main] Project '%s' not found in any node.yaml — SKIP (idempotent, exit 0)", args.name
        )
        return  # exit 0

    # ── Dry-run: print plan and exit ──
    if args.dry_run:
        print(f"[DRY-RUN] Would remove project '{args.name}':")
        print(f"  node.yaml: {project_info['node_yaml']}")
        print(f"  domain: {project_info['domain'] or '<none>'}")
        print(f"  host: {project_info['host'] or '<unknown>'}")
        print(f"  org: {project_info['org'] or '<unknown>'}")
        print(f"  node-configs: {project_info['node_configs_dir']}")
        print()
        print("  Steps (not executed in dry-run):")
        print("    1. Unregister from node.yaml")
        print("    2. Remove nginx vhost")
        print("    3. SSH: docker compose down (NO -v)")
        print("    4. Print safe-remove report")
        logger.info("[IMP:9][remove][main] Dry-run complete")
        return

    # ── Confirmation ──
    if not args.force:
        print()
        print(f"  This will REMOVE '{args.name}' from the platform lifecycle.")
        print(
            f"  Node:  {project_info.get('host') or args.node_name or project_info.get('node_configs_dir', 'unknown')}"
        )
        print(f"  Org:   {project_info.get('org', 'unknown')}")
        print()
        print("  Volumes, databases, images, and GitHub repo will NOT be deleted (safe remove O7).")
        print()
        response = input("  Continue? [y/N] ").strip().lower()
        if response not in ("y", "yes"):
            logger.info("[IMP:7][remove][main] Cancelled by user")
            return

    # ── Step 1: Unregister from node.yaml ──
    logger.info("[IMP:7][remove][main] Step 1/4: Unregister from node.yaml")
    unregister_from_node_yaml(project_info["node_yaml"], args.name)

    # ── Step 2: Remove nginx vhost ──
    logger.info("[IMP:7][remove][main] Step 2/4: Remove nginx vhost")
    vhost_removed = False
    domain = project_info.get("domain", "")
    if domain and domain != "null":
        vhost_removed = remove_vhost(domain, project_info.get("node_configs_dir", ""))
    else:
        logger.info("[IMP:6][remove][main] No domain configured — skipping vhost removal")

    # ── Step 3: SSH compose down on target node ──
    logger.info("[IMP:7][remove][main] Step 3/4: Stop containers on target node")
    ssh_done = False
    host = project_info.get("host", "")
    if host:
        ssh_done = ssh_compose_down(host, args.name)
    else:
        logger.info("[IMP:8][remove][main] No host configured — skipping SSH compose down")

    # ── Step 4: Print report ──
    logger.info("[IMP:7][remove][main] Step 4/4: Print safe-remove report")
    print_report(args.name, vhost_removed, ssh_done)

    logger.info("[IMP:9][remove][main] remove-project DONE: %s", args.name)


# endregion FUNC_main

if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )
    main()
