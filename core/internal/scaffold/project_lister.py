#!/usr/bin/env python3
# GREP_SUMMARY: project_lister list projects offline table json ssh-status node-yaml find-project-node ambiguity
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
##   - find_project_node: проект в >1 node.yaml на РАЗНЫХ узлах → ProjectAmbiguityError
##     (fail-fast, NODE=\<node\>); все совпадения на одном узле → первый (прежнее поведение)
## @rationale Completes Strangler-Fig for project-list: removes 7 inline python3 blocks.
##            NodeYaml.get_projects() covers 90% of logic. Simplest wave — warm-up.
## @links    CALLED_BY: project-list.sh (facade)
##           CALLS: NodeYaml.get_projects(), lib/ssh.sh::ssh_read()
##           DP-092 Wave 1
## @changes  2026-07-30 · Wave 1 — initial implementation
##           2026-08-02 · DevPlan 118 C11 — timeout=10 → SSH_READ_TIMEOUT (единый канон)
##           2026-08-27 · DevPlan 015 F-11 — scan-root → NODE_CONFIGS_DIR (env) → repo/node-configs;
##                      find_node_yaml_files: `*/node.yaml` ∪ backward-compat `*/node-configs/*/node.yaml`
##           2026-09-01 · FIX — дизамбигуация find_project_node: silent first-match → fail-fast
##                      ProjectAmbiguityError при разных узлах (make project-status NAME=x без NODE);
##                      общий helper ensure_same_node_or_raise (проект_remover делит его)
# endregion MODULE_CONTRACT

# ⚠️ TRAP[BUG] · 2026-09-01 · MED · Silent wrong-node resolution: проект в >1 node.yaml → молча
# · первый host (test-node/localhost) вместо цели → SSH fail · Root: find_project_node возвращал
# · первый match, узлы не сравнивались · Fix: ensure_same_node_or_raise — разные узлы →
# · ProjectAmbiguityError (перечень кандидатов + NODE=<node>) · Prevention: тест 2 node.yaml разных узлов

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, cast

# FIX (дизамбигуация): PlatformError-база для ProjectAmbiguityError (проект в нескольких node.yaml).
from core.internal.shared.exceptions import PlatformError

# DevPlan 118 C11: SSH-таймаут — единый канон shared/timeouts.SSH_READ_TIMEOUT (литерал 10 удалён).
from core.internal.shared.timeouts import SSH_READ_TIMEOUT

# DevPlan 139 W3 T4: SSH-раннер дедуплицирован — канон shared/vps_readiness
# (verbatim-копия _ssh_read удалена; сигнатура (host, user, cmd, timeout, ssh_lib_path)
# идентична, timeout-семантика = bash timeout + 5s сохранена).
from core.internal.shared.vps_readiness import default_ssh_runner

logger = logging.getLogger(__name__)

# ── Path defaults ────────────────────────────────────────────────────────
_DEFAULT_PROJECTS_ROOT = os.environ.get(
    "PROJECTS_BASE",
    str(Path(__file__).resolve().parent.parent.parent.parent),
)
_DEFAULT_SSH_HOST = os.environ.get("DEFAULT_SSH_HOST", "")

# ⚠️ TRAP[DECISION] · 2026-07-30 · — · ssh_read via subprocess (not direct import of lib/ssh.sh)
# · Rejected: direct Python SSH library (paramiko) — adds dependency, out of scope
# · Reason: lib/ssh.sh is the single source of truth for all SSH operations.
#   Calling via subprocess preserves the facade contract and timeout handling.
#   In tests, the ssh runner is injected as a callable (DI over Mocks).
# · Rev: if subprocess overhead becomes problematic → extract Python SSH runner from lib/ssh.sh


# region CLS_ProjectAmbiguityError
## @purpose  Проект найден в НЕСКОЛЬКИХ node.yaml на РАЗНЫХ узлах — молчаливый выбор первого
##           файла запрещён (silent wrong-node resolution → SSH/операция к неверному host).
##           Пользователь обязан указать NODE=\<node\>. exit_code=1 (generic error,
##           контракт exit-кодов core/AGENTS.md: 1 = PlatformError base).
## @io       carries project + candidates [(node_name, host, node_yaml_path)] →
##           человекочитаемое многострочное сообщение (перечень + подсказка NODE)
## @complexity O(C) — форматирование перечня кандидатов
## @invariants
##   - exit_code=1 (generic error, НЕ idempotent-skip)
##   - candidates — в порядке обнаружения (порядок обхода node.yaml)
class ProjectAmbiguityError(PlatformError):
    """Project matched in multiple node.yaml files on DIFFERENT nodes — NODE required."""

    exit_code: int = 1  # generic error (контракт exit-кодов core/AGENTS.md)

    def __init__(self, project: str, candidates: list[tuple[str, str, str]]) -> None:
        """Build readable multi-line message: candidate list (file+host) + NODE hint."""
        self.project = project
        self.candidates = candidates
        lines = [f"Project '{project}' found in multiple node.yaml files on DIFFERENT nodes:"]
        for node_name, host, ny_path in candidates:
            lines.append(f"  - node '{node_name}' (host={host or '<unknown>'}) → {ny_path}")
        lines.append("Refusing to pick one silently. Specify the target node: NODE=<node>")
        super().__init__("\n".join(lines))


# endregion CLS_ProjectAmbiguityError


# region FUNC_ensure_same_node_or_raise
## @purpose  Единое правило дизамбигуации find-паттерна (ОБЩИЙ для project_lister и
##           project_remover — оба молча брали первый match): ВСЕ совпадения проекта обязаны
##           лежать на ОДНОМ узле (идентичная пара node_name+host). Разные узлы →
##           ProjectAmbiguityError (fail-fast). Совпадения на одном узле (dual-path/context-
##           дубли) → no-op — caller берёт matches[0] (прежнее поведение сохранено).
## @param project  Project name (для сообщения об ошибке)
## @param matches  [(node_name, host, node_yaml_path), ...] в порядке обнаружения
## @io       ⇥ project, matches → ⎋ None (raise при разных узлах)
## @complexity O(m) — сравнение identity-множества пар (node, host)
## @invariants
##   - len(matches) ≤ 1 → no-op (нет дизамбигуации)
##   - единственная identity-пара → no-op (один узел — использовать его)
def ensure_same_node_or_raise(project: str, matches: list[tuple[str, str, str]]) -> None:
    """Raise ProjectAmbiguityError when project matches span different (node, host) pairs."""
    identities = {(node_name, host) for node_name, host, _ in matches}
    if len(identities) <= 1:
        return  # 0 совпадений (no-op) или единственная identity-пара (один узел — caller берёт matches[0])
    raise ProjectAmbiguityError(project, matches)


# endregion FUNC_ensure_same_node_or_raise


# region FUNC__shared_ssh_read
## @purpose  Default SSH-раннер через канон shared/vps_readiness.default_ssh_runner
##           (DevPlan 139 W3 T4 — дедупликация локальной verbatim-копии _ssh_read).
##           Адаптирует (rc, stdout) → stdout|None: rc==0 → stdout, иначе None.
## @param h        SSH host
## @param u        SSH user
## @param cmd      Remote command (verb `status <project>`)
## @param timeout  Bash-level ssh_read timeout (default SSH_READ_TIMEOUT, C11 канон)
## @io        ⎋ str | None — stdout при rc==0, None при ошибке/таймауте
## @complexity O(1) — single subprocess call в каноне
## @invariants
##   - Timeout-семантика канона: Python-level = bash timeout + 5s (macOS без GNU timeout)
##   - None на любом сбое (таймаут/FileNotFound/rc!=0) — прежнее поведение сохранено
def _shared_ssh_read(h: str, u: str, cmd: str, timeout: int = SSH_READ_TIMEOUT) -> str | None:
    """SSH-раннер через канон shared/vps_readiness — адаптер (rc, stdout) → stdout|None."""
    rc, stdout = default_ssh_runner(h, u, cmd, timeout)
    return stdout if rc == 0 else None


# endregion FUNC__shared_ssh_read


# region FUNC__resolve_scan_root
## @purpose  F-11 (DevPlan 015): резолв scan-root для find_node_yaml_files. Канонический
##           dev-layout — `node-configs/(node)/node.yaml` прямо в корне репо (NODE_CONFIGS_DIR
##           из .env); прежний glob `*/node-configs/*/node.yaml` кодировал НЕ-каноничный
##           layout `(context)/node-configs/` и давал «Found 0 node.yaml file(s)» на dev.
##           Цепочка: NODE_CONFIGS_DIR (env) → repo/node-configs (существует) → repo-root
##           (PROJECTS_BASE-режим, прежнее поведение).
## @io       ⇥ base_root: Path (--projects-root / _DEFAULT_PROJECTS_ROOT) → ⎋ Path (scan-root)
## @complexity — O(1) — env-чтение + 1 filesystem check
## @invariants
##   - NODE_CONFIGS_DIR задан → используется как есть (remote-нода: /opt/node-configs)
##   - `base_root`/node-configs существует → этот каталог (dev-корень репо)
##   - Иначе → base_root (PROJECTS_BASE-режим, backward-compat)
def _resolve_scan_root(base_root: Path) -> Path:
    """Resolve the node.yaml scan root: NODE_CONFIGS_DIR env → `repo`/node-configs → base_root (F-11)."""
    env_dir = os.environ.get("NODE_CONFIGS_DIR")
    if env_dir:
        logger.info("[IMP:8][list][scan-root] NODE_CONFIGS_DIR=%s", env_dir)
        return Path(env_dir)
    node_configs = base_root / "node-configs"
    if node_configs.is_dir():
        logger.info("[IMP:8][list][scan-root] <base>/node-configs=%s", node_configs)
        return node_configs
    logger.info("[IMP:8][list][scan-root] fallback base_root=%s (PROJECTS_BASE-режим)", base_root)
    return base_root


# endregion FUNC__resolve_scan_root


# region FUNC_find_node_yaml_files
## @purpose  Find all node.yaml files под scan-root (F-11): паттерн `*/node.yaml` (dev/bare-NODE:
##           node-configs/(node)/node.yaml) ∪ backward-compat `*/node-configs/*/node.yaml`
##           (multi-context: (context)/node-configs/(node)/node.yaml).
## @param projects_root  Base directory (scan-root — резолв в main/_resolve_scan_root)
## @param node_filter    Optional node name to filter
## @return  List of Path objects to node.yaml files
## @complexity O(f) where f = number of files under scan-root
def find_node_yaml_files(projects_root: Path, node_filter: str = "") -> list[Path]:
    """Find node.yaml files matching optional node filter.

    ## @purpose  Mirror of find_node_yaml_files() from project-list.sh:109-121.
    ##           Prefers Path.glob over find for cross-platform compatibility.
    ##           F-11 (DevPlan 015): dual-pattern — `*/node.yaml` (канонический dev/bare-NODE
    ##           layout node-configs/(node)/) + backward-compat `*/node-configs/*/node.yaml`.
    ## @io        ⇥ projects_root: Path, node_filter: str → ⎋ list[Path]
    ## @complexity O(f) where f = files matched
    ## @invariants
    ##   - Node-filter применяется к обоим паттернам
    ##   - Дубли (один файл, оба паттерна) дедуплицируются сортированным set
    """
    if not projects_root.exists():
        logger.info("[IMP:7][list][find] Projects root not found: %s", projects_root)
        return []

    if node_filter:
        patterns = (f"{node_filter}/node.yaml", f"*/node-configs/{node_filter}/node.yaml")
    else:
        patterns = ("*/node.yaml", "*/node-configs/*/node.yaml")

    yaml_files = sorted({p for pattern in patterns for p in projects_root.glob(pattern)})
    logger.info(
        "[IMP:7][list][find] Found %d node.yaml file(s) under %s (filter=%r)",
        len(yaml_files),
        projects_root,
        node_filter or "*",
    )
    return yaml_files


# endregion FUNC_find_node_yaml_files


# region FUNC_list_projects_offline
## @purpose  Read all node.yaml files and print a table/JSON of registered projects.
##           Works entirely offline — no network required.
## @param projects_root  Base directory (PROJECTS_BASE)
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
) -> list[dict[str, object]]:
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

    all_projects: list[dict[str, object]] = []

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
    elif not all_projects:
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
##           FIX (дизамбигуация): при совпадениях в НЕСКОЛЬКИХ node.yaml на РАЗНЫХ узлах —
##           НЕ брать первый молча: ensure_same_node_or_raise → ProjectAmbiguityError
##           (fail-fast, перечень кандидатов + NODE=\<node\>). Все совпадения на ОДНОМ узле —
##           использовать его (прежнее поведение).
## @param name           Project name to search for
## @param projects_root  Base directory
## @param node_filter    Optional node filter
## @return  (node_yaml_path: Path | None, ssh_host: str) — None if not found
## @raises  ProjectAmbiguityError — проект найден в >1 node.yaml на разных узлах
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
    ##   - >1 совпадения на РАЗНЫХ узлах → ProjectAmbiguityError (fail-fast, NODE обязателен)
    ##   - Все совпадения на ОДНОМ узле → первый (прежнее поведение)
    """
    logger.info("[IMP:7][list][find_node] Searching for project '%s' in node.yaml files", name)

    # Check python3 availability once (not per file)
    import shutil

    if not shutil.which("python3"):
        logger.info("[IMP:8][list][find_node] python3 not available for NodeYaml CLI")
        return None, ""

    yaml_files = find_node_yaml_files(projects_root, node_filter)

    # FIX (дизамбигуация): собираем ВСЕ совпадения, а не возвращаем первое.
    # matches: (node_name, host, node_yaml_path) — в порядке обхода yaml_files.
    matches: list[tuple[str, str, str]] = []

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
            matches.append((ny.parent.name, ssh_host, str(ny)))
            logger.info("[IMP:7][list][find_node] Project '%s' found in: %s host=%s", name, ny, ssh_host or "<unknown>")

    if not matches:
        logger.info("[IMP:8][list][find_node] Project '%s' not found in any node.yaml", name)
        return None, ""

    # Единое правило дизамбигуации (общее с project_remover): разные узлы → fail-fast.
    ensure_same_node_or_raise(name, matches)
    first_node, first_host, first_path = matches[0]
    logger.info(
        "[IMP:9][list][find_node] Resolved project '%s' → node '%s' host=%s (single node)",
        name,
        first_node,
        first_host or "<unknown>",
    )
    return Path(first_path), first_host


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
    ssh_runner: Callable[..., str | None] | None = None,  # default_ssh_runner: rc==0 → stdout, иначе None
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

    # Default ssh runner: канон shared/vps_readiness (DevPlan 139 W3 T4) —
    # адаптер (rc, stdout) → stdout|None (прежняя семантика локальной _ssh_read).
    if ssh_runner is None:
        ssh_runner = _shared_ssh_read

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
                # W11: json.loads returns Any → cast to status-JSON boundary
                status_data: dict[str, object] | None = cast(dict[str, object] | None, json.loads(output.strip()))
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
                # Фолбэк: не-JSON вывод (нода без dispatch) — как есть
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
def _render_status_json(status_data: dict[str, object]) -> None:
    """Render ProjectStatus JSON to a human-readable table (Name/Status/Ports)."""
    status = status_data.get("status", "unknown")
    # W11: dict[str, object].get → object → cast to list boundary (runtime: `or []` unchanged)
    containers = cast(list[object], status_data.get("containers", []) or [])

    print(f"  Project:   {status_data.get('project', '')}")
    print(f"  Status:    {status}")
    if status_data.get("last_deploy"):
        print(f"  Last deploy: {status_data['last_deploy']}")

    if containers:
        print()
        print(f"{'NAME':<40} {'STATUS':<30} PORTS")
        print(f"{'─':─<40} {'─':─<30} {'─':─<10}")
        for c in containers:
            if not isinstance(c, dict):
                continue
            c_typed = cast(dict[str, object], c)
            name = c_typed.get("Name", c_typed.get("name", ""))
            cstatus = c_typed.get("Status", c_typed.get("status", ""))
            ports = c_typed.get("Ports", c_typed.get("ports", ""))
            print(f"{name:<40} {cstatus:<30} {ports}")
    else:
        print()
        print("  No running containers")


# endregion FUNC__render_status_json


# region FUNC_main
class _ListerArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    mode: ClassVar[str | None]
    node_name: ClassVar[str]
    project_name: ClassVar[str]
    output_format: ClassVar[str]
    projects_root: ClassVar[str]


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
    parser.add_argument("--projects-root", default=_DEFAULT_PROJECTS_ROOT, help="Override PROJECTS_BASE")

    args = parser.parse_args(argv, namespace=_ListerArgs())

    # Default mode: list
    mode = args.mode if args.mode else "list"
    # F-11 (DevPlan 015): scan-root = NODE_CONFIGS_DIR → repo/node-configs → --projects-root
    base_root = Path(args.projects_root)
    scan_root = _resolve_scan_root(base_root)

    logger.info(
        "[IMP:7][list][main] Args: mode=%s node=%s name=%s format=%s root=%s scan_root=%s",
        mode,
        args.node_name or "<auto>",
        args.project_name or "<all>",
        args.output_format,
        base_root,
        scan_root,
    )

    if mode == "list":
        logger.info("[IMP:7][list][main] Mode: list — offline project listing")
        list_projects_offline(
            projects_root=scan_root,
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

        try:
            node_yaml_path, ssh_host = find_project_node(
                name=args.project_name,
                projects_root=scan_root,
                node_filter=args.node_name,
            )
        except ProjectAmbiguityError as exc:
            logger.info(
                "[IMP:10][list][main] FAIL-FAST: project '%s' matches multiple nodes — NODE required",
                args.project_name,
            )
            print(str(exc))
            return exc.exit_code
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
        # W11: getattr(logging, str) → Any; level must be int for basicConfig
        level=cast(int, getattr(logging, os.environ.get("LOG_LEVEL", "INFO"))),
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )
    main()
