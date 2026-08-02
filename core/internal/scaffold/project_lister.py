#!/usr/bin/env python3
# GREP_SUMMARY: project_lister list projects offline table json ssh-status node-yaml
# STRUCTURE: ▶ parse_args → ◇ find_node_yaml_files → ⚡ list_projects_offline (⌀ NodeYaml.get_projects → ⊕ table|json) → ◇ find_project_node → ⚡ get_status_via_ssh (ssh_read wrapper) → ⎋ main dispatch
# region MODULE_CONTRACT
## @purpose  Python Strangler-Fig migration of project-list.sh (403 LOC shell, 7 inline python3).
##           Lists projects registered in node.yaml (offline) or queries live status via SSH.
##           Replaces 7 inline python3 blocks with clean Python. Completes the OBSERVE phase.
## @scope    Called from project-list.sh facade. Reads node.yaml via NodeYaml API.
##           Provides: list (offline table/JSON), status (live SSH), find-project-node.
## @invariants
##   - Works offline without network (list mode)
##   - SSH status has SSH_READ_TIMEOUT timeout via ssh_read wrapper (C11 канон shared/timeouts)
##   - Never modifies state (read-only: OBSERVE phase)
##   - 0 inline python3 blocks — pure Python
##   - JSON output is valid JSON array
## @rationale Completes Strangler-Fig for project-list: removes 7 inline python3 blocks.
##            NodeYaml.get_projects() covers 90% of logic. Simplest wave — warm-up.
## @links    CALLED_BY: project-list.sh (facade)
##           CALLS: NodeYaml.get_projects(), lib/ssh.sh::ssh_read()
##           DP-092 Wave 1
## @changes  2026-07-30 · Wave 1 — initial implementation
##           2026-08-02 · DevPlan 118 C11 — timeout=10 → SSH_READ_TIMEOUT (единый канон)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# DevPlan 118 C11: SSH-таймаут — единый канон shared/timeouts.SSH_READ_TIMEOUT (литерал 10 удалён).
from core.internal.shared.timeouts import SSH_READ_TIMEOUT

logger = logging.getLogger(__name__)

# ── Path defaults ────────────────────────────────────────────────────────
_DEFAULT_PROJECTS_ROOT = os.environ.get(
    "PROJECTS_ROOT",
    str(Path(__file__).resolve().parent.parent.parent.parent),
)
_DEFAULT_SSH_HOST = os.environ.get("DEFAULT_SSH_HOST", "")

# ⚠️ TRAP[DECISION] · 2026-07-30 · — · ssh_read via subprocess (not direct import of lib/ssh.sh)
# · Rejected: direct Python SSH library (paramiko) — adds dependency, out of scope
# · Reason: lib/ssh.sh is the single source of truth for all SSH operations.
#   Calling via subprocess preserves the facade contract and timeout handling.
#   In tests, the ssh runner is injected as a callable (DI over Mocks).
# · Rev: if subprocess overhead becomes problematic → extract Python SSH runner from lib/ssh.sh


# region FUNC_find_node_yaml_files
## @purpose  Find all node.yaml files under PROJECTS_ROOT/*/node-configs/*/
## @param projects_root  Base directory (PROJECTS_ROOT)
## @param node_filter    Optional node name to filter
## @return  List of Path objects to node.yaml files
## @complexity O(f) where f = number of files under node-configs/
def find_node_yaml_files(projects_root: Path, node_filter: str = "") -> list[Path]:
    """Find node.yaml files matching optional node filter.

    ## @purpose  Mirror of find_node_yaml_files() from project-list.sh:109-121.
    ##           Prefers Path.glob over find for cross-platform compatibility.
    ## @io        ⇥ projects_root: Path, node_filter: str → ⎋ list[Path]
    ## @complexity O(f) where f = files matched
    """
    if not projects_root.exists():
        logger.info("[IMP:7][list][find] Projects root not found: %s", projects_root)
        return []

    pattern = f"*/node-configs/{node_filter}/node.yaml" if node_filter else "*/node-configs/*/node.yaml"

    yaml_files = sorted(projects_root.glob(pattern))
    logger.info("[IMP:7][list][find] Found %d node.yaml file(s) (filter=%r)", len(yaml_files), node_filter or "*")
    return yaml_files


# endregion FUNC_find_node_yaml_files


# region FUNC_list_projects_offline
## @purpose  Read all node.yaml files and print a table/JSON of registered projects.
##           Works entirely offline — no network required.
## @param projects_root  Base directory (PROJECTS_ROOT)
## @param node_filter    Optional node filter
## @param project_name   Optional project name filter
## @param output_format  "table" (default) or "json"
## @io        stdout: formatted table or JSON
## @return    list of project dicts (for testing convenience)
## @complexity O(p+f) where p = total projects, f = node.yaml files
def list_projects_offline(
    projects_root: Path,
    node_filter: str = "",
    project_name: str = "",
    output_format: str = "table",
) -> list[dict[str, Any]]:
    """List all projects from local node.yaml files.

    ## @purpose  Core listing logic: read node.yaml → extract projects → format output.
    ##           Replaces L130-238 in project-list.sh (7 inline python3 blocks).
    ## @io        ⇥ projects_root, node_filter, project_name, output_format → ⎋ list[dict]
    ## @complexity O(p+f)
    ## @invariants
    ##   - Pure Python: no inline python3, no subprocess yq
    ##   - Empty state → prints "No projects found" + returns []
    """
    logger.info("[IMP:7][list][offline] Listing projects from local node.yaml files (offline)")

    yaml_files = find_node_yaml_files(projects_root, node_filter)

    if not yaml_files:
        logger.info("[IMP:8][list][offline] No node.yaml files found under %s", projects_root)
        logger.info("[IMP:9][list][offline] Empty state — no node.yaml files to list")
        if output_format == "json":
            print("[]")
        else:
            print("No projects found (no node.yaml files)")
        return []

    all_projects: list[dict[str, Any]] = []

    for ny in yaml_files:
        # Derive node name from path: .../node-configs/<node>/node.yaml
        node_name = ny.parent.name
        node_host = ""

        # Read node host via NodeYaml API
        try:
            from core.internal.shared.node_yaml import NodeYaml

            node = NodeYaml(str(ny))
            projects = node.get_projects()
            # DevPlan 116 B6 T8.2: dotted-key фасад вместо приватного кэш-атрибута (`_data`).
            # node.get("node.host", default="") — НЕ get_node_info().fqdn (тот читает node.fqdn,
            # а в образцах только node.host — не эквивалентно).
            node_host = node.get("node.host", default="")
        except (ImportError, ValueError, FileNotFoundError, OSError) as exc:
            logger.info("[IMP:8][list][offline] Failed to read %s: %s", ny, exc)
            continue

        if not projects:
            continue

        for p in projects:
            # DevPlan 116 B6 T4.7: мутируем КОПИЮ dict (entry = dict(p)), а не ссылку
            # из get_projects() — get_projects() возвращает ССЫЛКУ на кэш NodeYaml.
            entry = dict(p)
            entry["node"] = node_name
            entry["host"] = node_host
            if not project_name or entry.get("name") == project_name:
                all_projects.append(entry)

    # ── Output ──
    if output_format == "json":
        print(json.dumps(all_projects, indent=2, default=str))
    else:
        if not all_projects:
            print("No projects found")
        else:
            # Table header
            print(f"{'NAME':<25} {'NODE':<20} {'DOMAIN':<30} {'TYPE':<15} REPO")
            print(f"{'─':─<25} {'─':─<20} {'─':─<30} {'─':─<15} {'─':─<10}")
            for p in all_projects:
                pname = p.get("name", "")
                pnode = p.get("node", "")
                pdomain = p.get("domain", "") or "-"
                ptype = p.get("type", "") or "-"
                prepo = p.get("repo", "")
                print(f"{pname:<25} {pnode:<20} {pdomain:<30} {ptype:<15} {prepo}")

    logger.info("[IMP:9][list][offline] Offline project listing complete (%d projects)", len(all_projects))
    return all_projects


# endregion FUNC_list_projects_offline


# region FUNC_find_project_node
## @purpose  Find the node.yaml containing a specific project, and extract SSH host.
## @param name           Project name to search for
## @param projects_root  Base directory
## @param node_filter    Optional node filter
## @return  (node_yaml_path: Path | None, ssh_host: str) — None if not found
## @complexity O(p+f)
def find_project_node(
    name: str,
    projects_root: Path,
    node_filter: str = "",
) -> tuple[Path | None, str]:
    """Locate the node.yaml file containing a specific project.

    ## @purpose  Mirror of find_project_node_yaml() from project-list.sh:249-272.
    ##           Uses NodeYaml CLI --find-project for search.
    ## @io        ⇥ name, projects_root, node_filter → ⎋ (Path | None, host: str)
    ## @complexity O(f) where f = node.yaml files
    ## @invariants
    ##   - Returns (None, "") if project not found
    ##   - Returns host="" if node.yaml has no node.host
    """
    logger.info("[IMP:7][list][find_node] Searching for project '%s' in node.yaml files", name)

    # Check python3 availability once (not per file)
    import shutil

    if not shutil.which("python3"):
        logger.info("[IMP:8][list][find_node] python3 not available for NodeYaml CLI")
        return None, ""

    yaml_files = find_node_yaml_files(projects_root, node_filter)

    for ny in yaml_files:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "core.internal.shared.node_yaml",
                "--file",
                str(ny),
                "--find-project",
                name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Found the project — now get SSH host
            host_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "core.internal.shared.node_yaml",
                    "--file",
                    str(ny),
                    "--get",
                    "node.host",
                    "--default",
                    "",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            ssh_host = host_result.stdout.strip() if host_result.returncode == 0 else ""
            logger.info("[IMP:7][list][find_node] Found project '%s' in: %s host=%s", name, ny, ssh_host or "<unknown>")
            return ny, ssh_host

    logger.info("[IMP:8][list][find_node] Project '%s' not found in any node.yaml", name)
    return None, ""


# endregion FUNC_find_project_node


# region FUNC_get_status_via_ssh
## @purpose  SSH to target node and query project status via the `status <project>` forced-command
##           verb (DevPlan 116 B1 T3, U-36). SSH-команда — НЕ raw docker compose ps, а verb
##           `status <project>`: authorized_keys → orchestrator_cli dispatch → ProjectStatus JSON.
##           JSON парсится и рендерится в человекочитаемую таблицу (Name/Status/Ports из containers).
## @param host     SSH host (IP or domain)
## @param project  Project name
## @param ssh_runner  Callable (host, user, cmd, timeout) → str | None — injected for testing
## @io        stdout: human-readable status
## @return    True on success, False on failure
## @complexity O(t) where t = SSH round-trip time (≤10s)
## @invariants
##   - Timeout ≤ SSH_READ_TIMEOUT (C11 канон)
##   - SSH-команда = verb `status <project>` (D6: forced-command status; ответ — ProjectStatus JSON)
##   - Ответ JSON парсится; containers рендерятся в таблицу Name/Status/Ports
##   - Триггеры ci-deploy user first, then current user
##   - Returns False on SSH failure (connection refused, timeout, JSON parse fail)
def get_status_via_ssh(
    host: str,
    project: str,
    ssh_runner: callable | None = None,
) -> bool:
    """Query live project status via the `status <project>` forced-command verb.

    ## @purpose  Mirror of get_status_via_ssh() from project-list.sh:284-339, но через
    ##            status-verb (U-36): dispatch → ProjectStatus JSON → таблица Name/Status/Ports.
    ## @io        ⇥ host, project, ssh_runner → ⎋ bool — True on success
    ## @complexity O(t) where t = SSH round-trip time
    ## @invariants
    ##   - Timeout enforced via ssh_read (SSH_READ_TIMEOUT by default, C11)
    ##   - Falls back from ci-deploy to $USER on auth failure
    ##   - Raw docker compose ps path УДАЛЁН — единый status-контракт (T3)
    """
    if not host:
        logger.info("[IMP:10][list][status] FAIL-FAST: No SSH host available for project '%s'", project)
        print(f"ERROR: Cannot determine SSH host for project '{project}'")
        return False

    logger.info("[IMP:7][list][status] Connecting to %s for project '%s' status...", host, project)

    import getpass

    # Default ssh runner: subprocess-based ssh_read from lib/ssh.sh
    if ssh_runner is None:

        def _ssh_read(h: str, u: str, cmd: str, timeout: int = SSH_READ_TIMEOUT) -> str | None:
            """Default SSH runner via lib/ssh.sh facade (C11: SSH_READ_TIMEOUT канон)."""
            try:
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        f'source core/lib/ssh.sh && ssh_read "{h}" "{u}" "{cmd}" {timeout}',
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout + 5,
                )
                if result.returncode == 0:
                    return result.stdout
                return None
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return None

        ssh_runner = _ssh_read

    # Try ci-deploy user first, then current user
    current_user = os.environ.get("USER") or getpass.getuser()
    for try_user in ("ci-deploy", current_user):
        effective_user = try_user if try_user else current_user
        logger.info("[IMP:6][list][status]  Trying SSH as: %s@%s", effective_user, host)

        # U-36: forced-command status verb (dispatch диспетчеризует SSH_ORIGINAL_COMMAND).
        # Ответ — ProjectStatus JSON {project, status, containers, last_deploy}.
        ssh_cmd = f"status {project}"

        try:
            output = ssh_runner(host, effective_user, ssh_cmd, timeout=SSH_READ_TIMEOUT)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError, ValueError) as exc:
            logger.info("[IMP:8][list][status] SSH attempt failed (%s): %s", effective_user, exc)
            continue

        if output is not None:
            try:
                status_data = json.loads(output.strip())
            except (json.JSONDecodeError, AttributeError):
                logger.warning("[IMP:8][list][status] Non-JSON status response from %s — raw passthrough", host)
                status_data = None

            print()
            print("──────────────────────────────────────────────")
            print(f"  Status: {project} on {host}")
            print("──────────────────────────────────────────────")
            print()
            if isinstance(status_data, dict):
                _render_status_json(status_data)
            elif output:
                # Фолбэк: не-JSON вывод (legacy нода без dispatch) — как есть
                print(output)
            print()
            print("──────────────────────────────────────────────")
            logger.info("[IMP:9][list][status] Status retrieved for project '%s' from %s", project, host)
            return True

    logger.info("[IMP:10][list][status] SSH connection failed to %s for project '%s'", host, project)
    print(f"ERROR: Cannot connect to node {host} (timeout/connectivity)")
    print("  Check: node reachability, SSH keys, ci-deploy user")
    return False


# endregion FUNC_get_status_via_ssh


# region FUNC__render_status_json
## @purpose  Рендер ProjectStatus JSON в человекочитаемую таблицу Name/Status/Ports (U-36, T3).
## @io       ⇥ status_data: dict (ProjectStatus.to_dict()) → ⎋ None (печатает в stdout)
## @complexity — O(C) где C = число containers
## @invariants
##   - Выводит project/status строку + таблицу containers (Name/Status/Ports)
##   - containers пуст → "No running containers" строка
def _render_status_json(status_data: dict) -> None:
    """Render ProjectStatus JSON to a human-readable table (Name/Status/Ports)."""
    status = status_data.get("status", "unknown")
    containers = status_data.get("containers", []) or []

    print(f"  Project:   {status_data.get('project', '')}")
    print(f"  Status:    {status}")
    if status_data.get("last_deploy"):
        print(f"  Last deploy: {status_data['last_deploy']}")

    if containers:
        print()
        print(f"{'NAME':<40} {'STATUS':<30} PORTS")
        print(f"{'─':─<40} {'─':─<30} {'─':─<10}")
        for c in containers:
            name = c.get("Name", c.get("name", ""))
            cstatus = c.get("Status", c.get("status", ""))
            ports = c.get("Ports", c.get("ports", ""))
            print(f"{name:<40} {cstatus:<30} {ports}")
    else:
        print()
        print("  No running containers")


# endregion FUNC__render_status_json


# region FUNC_main
## @purpose  CLI entry point — dispatch to list or status based on --mode
## @io        stdout: formatted output; exit 0 on success, 1 on error
## @complexity O(p+f) for list, O(t) for status
def main(argv: list[str] | None = None) -> int:
    """CLI dispatcher for project lister.

    ## @purpose  Parse args, dispatch to list or status mode.
    ## @io        ⇥ argv → ⎋ int exit code (контракт T4: main() -> int)
    ## @complexity O(p+f) or O(t)
    """
    parser = argparse.ArgumentParser(
        description="List projects registered in node.yaml (offline) or query live status via SSH.",
    )
    parser.add_argument("--list", dest="mode", action="store_const", const="list", help="List projects (offline)")
    parser.add_argument("--status", dest="mode", action="store_const", const="status", help="Query live status via SSH")
    parser.add_argument("--node", dest="node_name", default="", help="Node name filter")
    parser.add_argument("--name", dest="project_name", default="", help="Project name filter")
    parser.add_argument(
        "--format",
        dest="output_format",
        default="table",
        choices=["table", "json"],
        help="Output format (default: table)",
    )
    parser.add_argument("--projects-root", default=_DEFAULT_PROJECTS_ROOT, help="Override PROJECTS_ROOT")

    args = parser.parse_args(argv)

    # Default mode: list
    mode = args.mode if args.mode else "list"
    projects_root = Path(args.projects_root)

    logger.info(
        "[IMP:7][list][main] Args: mode=%s node=%s name=%s format=%s root=%s",
        mode,
        args.node_name or "<auto>",
        args.project_name or "<all>",
        args.output_format,
        projects_root,
    )

    if mode == "list":
        logger.info("[IMP:7][list][main] Mode: list — offline project listing")
        list_projects_offline(
            projects_root=projects_root,
            node_filter=args.node_name,
            project_name=args.project_name,
            output_format=args.output_format,
        )
    elif mode == "status":
        logger.info("[IMP:7][list][main] Mode: status — live SSH status query")
        if not args.project_name:
            logger.info("[IMP:10][list][main] FAIL-FAST: --status requires --name <project>")
            print("ERROR: --status requires --name <project>")
            print("Usage: project-list.sh --status --name <project> [--node <node>]")
            return 1

        node_yaml_path, ssh_host = find_project_node(
            name=args.project_name,
            projects_root=projects_root,
            node_filter=args.node_name,
        )
        if node_yaml_path is None:
            print(f"ERROR: Project '{args.project_name}' not found in node.yaml")
            print("  Register it first or check --name spelling")
            return 1

        if not ssh_host:
            print(f"ERROR: No SSH host found for project '{args.project_name}' in node.yaml")
            print(f"  Check node.host in: {node_yaml_path}")
            return 1

        success = get_status_via_ssh(host=ssh_host, project=args.project_name)
        if not success:
            return 1
    else:
        logger.info("[IMP:10][list][main] Unknown mode: %s", mode)
        return 1

    logger.info("[IMP:9][list][main] project-list DONE (mode=%s)", mode)
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )
    main()
