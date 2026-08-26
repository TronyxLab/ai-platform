# GREP_SUMMARY: orphan-reconciler, docker, container, reconcile, batch, orphan, compose, inspect
# STRUCTURE: ▶ ┌module_entries + modules_dir┐ → ⚡ find compose files → ⚡ docker ps -a (single call) → ○ for each service: docker compose config → ◇ container in existing set? → docker inspect project label → ◇ project != module_name? → ⊕ orphans[] → ⎋ return orphans
# region MODULE_CONTRACT
## @purpose  S8: Batch orphan container reconciliation — replaces inline python3
##           in deploy-modules.sh batch_orphan_reconciliation(). Collects all compose
##           files, does one docker ps -a, compares container names with compose project
##           labels, and returns list of orphan (foreign) containers.
## @scope    Called during module deployment to detect and reconcile containers that belong
##           to a different compose project than expected (e.g., after module rename or
##           migration). The shell facade reads the output and stops+removes each orphan.
## @invariants
##   - One docker ps -a call for all modules (not per-module)
##   - Compose file discovery order: compose.yaml → docker-compose.yaml → docker-compose.base.yml
##   - Subprocess errors are WARN-logged, never raised — always graceful degradation
##   - Returned orphans always have "container_name" and "project" keys
##   - Project is empty string if inspect fails or label is missing
##   - Empty module_entries returns empty list immediately
## @rationale  Per-module orphan detection created 13 subprocess spawns per update cycle.
##             Batch approach: 1 python3 call for all modules, reducing total spawn time
##             and consolidating container state into a single snapshot.
## @changes
##   2026-07-22 · Created (W4-E1 extraction from deploy-modules.sh §1190-1261)
##   2026-07-22 · Added W5-E5 self-heal functions (_self_heal_orphan_containers, _self_heal_aged_images)
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

# DevPlan 128 W1 (P2-5/D6): docker ps/inspect/rm примитивы — shared/docker_ops
# (единственный слой, гейт docker_sole_path).
# DevPlan 118 A2: единый канон списков compose-файлов — shared/compose_files.py.
# Локальный COMPOSE_FILE_CANDIDATES УДАЛЁН (6 копий → 1 SoT; канон расширен docker-compose.yml).
# B3: канонический platform root — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared import deploy_paths, docker_ops
from core.internal.shared.compose_files import COMPOSE_FILENAMES as COMPOSE_FILE_CANDIDATES
from core.internal.shared.deploy_paths import platform_remote_base

# DevPlan 116 B5 T3: shared docker compose config — sole path (гейт docker_sole_path)
from core.internal.shared.docker_compose import docker_compose_config as _shared_docker_compose_config

# DevPlan 116 B5 T1: таймауты — единый реестр shared/timeouts.py (U-11)
from core.internal.shared.timeouts import IMAGE_CHECK_TIMEOUT

"""## @invariant Compose file search priority — canonical COMPOSE_FILENAMES (DevPlan 118 A2), first match wins."""

logger = logging.getLogger(__name__)

# ── Constants ──
DEFAULT_IMAGE_RETENTION_DAYS: int = 30
DOCKER_RM_TIMEOUT: int = 30

DOCKER_PS_TIMEOUT: int = 15
"""## @invariant docker ps -a timeout (seconds). Matches deploy-modules.sh value."""

DOCKER_INSPECT_TIMEOUT: int = 15
"""## @invariant docker inspect timeout (seconds). Single container lookup."""


# region FUNC__find_compose_files
## @purpose  Discover compose files for each module entry in priority order
## @io       ⇥ module_entries: list[str], modules_dir: str → ⎋ list[tuple[str, str]] (module_name, compose_path)
## @complexity O(N * C) where N = len(entries), C = len(COMPOSE_FILE_CANDIDATES) = 3
## @invariants
##   - Each entry is "module_name:overlay_dir" or just "module_name"
##   - Only the module_name segment (before ':') is used for compose file discovery
##   - First matching compose file in priority order wins
##   - Entries without any compose file are silently skipped
##   - Entry format: "module_name[:overlay_dir]" — overlay subdirectory is ignored (only relevant for sudoers/shell)
def _find_compose_files(module_entries: list[str], modules_dir: str) -> list[tuple[str, str]]:
    """Discover compose files for each module entry.

    Scans each module directory for compose files in priority order:
    compose.yaml → docker-compose.yaml → docker-compose.base.yml.
    Returns a list of (module_name, compose_file_path) tuples for
    entries that have a compose file.
    """
    logger.info("[IMP:7][_find_compose_files] Scanning %d entries in %s", len(module_entries), modules_dir)

    result: list[tuple[str, str]] = []
    for entry in module_entries:
        mod_name = entry.split(":")[0]
        mod_dir = Path(modules_dir) / mod_name

        if not mod_dir.is_dir():
            logger.warning("[IMP:8][_find_compose_files] Module directory not found: %s", mod_dir)
            continue

        found = False
        for cf in COMPOSE_FILE_CANDIDATES:
            cf_path = mod_dir / cf
            if cf_path.is_file():
                result.append((mod_name, str(cf_path)))
                logger.info("[IMP:7][_find_compose_files] Found compose file for %s: %s", mod_name, cf_path)
                found = True
                break

        if not found:
            logger.warning(
                "[IMP:8][_find_compose_files] No compose file found for module %s (searched: %s)",
                mod_name,
                COMPOSE_FILE_CANDIDATES,
            )

    logger.info(
        "[IMP:7][_find_compose_files] Resolved %d compose files from %d entries", len(result), len(module_entries)
    )
    return result


# endregion FUNC__find_compose_files


# region FUNC__get_existing_containers
## @purpose  Run a single docker ps -a to get all container names known to the daemon
## @io       ⇥ None → ⎋ set[str] (container names)
## @complexity 1 — single subprocess call with timeout
## @invariants
##   - On subprocess error: log WARN, return empty set (graceful degradation)
##   - Empty lines are filtered out
##   - Timeout is DOCKER_PS_TIMEOUT seconds
def _get_existing_containers() -> set[str]:
    """Run docker ps -a once and return set of container names.

    Returns empty set on any subprocess error — never raises.
    This allows the caller to continue gracefully if docker is unavailable.
    """
    logger.info("[IMP:7][_get_existing_containers] Querying docker ps -a for all containers")
    # W1 (DevPlan 128): docker ps — shared/docker_ops (non-fatal, [] на сбое/таймауте)
    names_list = docker_ops.ps_container_names(all=True, timeout=DOCKER_PS_TIMEOUT)
    names = set(names_list)
    logger.info("[IMP:7][_get_existing_containers] Found %d containers in docker ps -a", len(names))
    return names


# endregion FUNC__get_existing_containers


# region FUNC__get_compose_services
## @purpose  Parse container names + project name from docker compose config --format json output.
##           Возвращает (container_names, project_name): project_name = config "name" — ФАКТИЧЕСКИЙ
##           compose project деплоя (basename dirname первого -f файла: root → "platform", модуль → имя модуля).
## @io       ⇥ compose_path: str, module_name: str → ⎋ tuple[list[str], str] (container names, project name)
## @complexity 2 — subprocess call + JSON parse + iteration over services
## @invariants
##   - On subprocess error: log WARN, return ([], "")
##   - JSON parse error: log WARN, return ([], "")
##   - Service container_name resolves to explicit container_name or service name
##   - Services without container_name AND without name field are skipped
##   - --profile <module_name\> is passed to get the correct config resolution
##   - --env-file /var/lib/platform/run/secrets.env добавляется если существует (R7-fix 141 B18:
##     без env-file ${VAR:?} в base.yml падает — config слеп, orphan-детекция = 0 сервисов)
##   - project_name используется для orphan-сравнения (expected project), НЕ module_name
def _get_compose_services(compose_path: str, module_name: str) -> tuple[list[str], str]:
    """Run docker compose config --format json and extract container names + project name.

    Uses --profile <module_name\\> so compose resolves the correct service set
    for the given module. Returns (container names, project name) — project name
    is the ACTUAL compose project of the deploy (config JSON "name" field).
    """
    logger.info("[IMP:7][_get_compose_services] Resolving services for %s from %s", module_name, compose_path)
    # Shared docker_compose_config — sole path (DevPlan 116 B5 T3, гейт docker_sole_path).
    # Shared возвращает CompletedProcess (никогда не raise) — try/except TimeoutExpired/OSError удалены.
    # ⚠️ TRAP[BUG] 2026-08-03 (RC 121, U-49): root compose ЕДИНСТВЕННЫЙ -f (он include'ит модульные
    # base.yml; двойное включение конкатенирует списки — security_opt dup)
    root_compose = os.path.join(str(platform_remote_base()), "docker-compose.yml")
    compose_args = ["-f", compose_path, "--profile", module_name]
    if os.path.isfile(root_compose):
        compose_args = ["-f", root_compose, "--profile", module_name]
    # ⚠️ TRAP[BUG] · 2026-08-06 · HI · R7: config без secrets env-file слеп (141 B18)
    # · Symptom: docker compose config падал "required variable POSTGRES_PASSWORD is missing"
    # ·   (26 вхождений/прогон) → orphan-детекция 0 сервисов; R7 volumes — detect-only мимо.
    # · Fix: --env-file /var/lib/platform/run/secrets.env если существует (канон _build_compose_args).
    secrets_env = str(deploy_paths.secrets_env_file())
    if os.path.isfile(secrets_env):
        compose_args = ["--env-file", secrets_env, *compose_args]
    cfg_r = _shared_docker_compose_config(
        str(Path(compose_path).parent),
        compose_args=compose_args,
        flags=["--format", "json"],
    )
    if cfg_r.returncode != 0:
        logger.warning(
            "[IMP:8][_get_compose_services] docker compose config failed for %s (returncode=%d): %s",
            module_name,
            cfg_r.returncode,
            cfg_r.stderr.strip() if cfg_r.stderr else "no stderr",
        )
        return [], ""

    cfg_stdout = cfg_r.stdout
    if isinstance(cfg_stdout, bytes):
        cfg_stdout = cfg_stdout.decode("utf-8")
    try:
        # W11: json.loads → Any — каст к compose-config dict (name/services)
        cfg = cast(dict[str, object], json.loads(cfg_stdout))
    except json.JSONDecodeError as e:
        logger.warning(
            "[IMP:8][_get_compose_services] Invalid JSON from docker compose config for %s: %s",
            module_name,
            e,
        )
        return [], ""

    # Проект деплоя: docker compose config "name" (basename dirname первого -f файла).
    # root compose → "platform" (на ноде); модульный -f → имя модуля.
    project_name = str(cfg.get("name", "") or "")
    services = cfg.get("services", {})
    if not isinstance(services, dict):
        services = {}
    container_names: list[str] = []

    for svc_name, svc in cast(dict[str, dict[str, object]], services).items():
        # W11: элемент services[] — каст строкового container_name/name
        cname = cast(str, svc.get("container_name", "") or svc.get("name", ""))
        if cname:
            container_names.append(cname)
            logger.info("[IMP:7][_get_compose_services] %s → container_name: %s", svc_name, cname)

    logger.info(
        "[IMP:7][_get_compose_services] Resolved %d container names (project=%s) from %s",
        len(container_names),
        project_name,
        compose_path,
    )
    return container_names, project_name


# endregion FUNC__get_compose_services


# region FUNC__inspect_project_label
## @purpose  Get the com.docker.compose.project label from a running/stopped container
## @io       ⇥ container_name: str → ⎋ str (project label, or empty string on error)
## @complexity 1 — single subprocess call per container
## @invariants
##   - On subprocess error: log WARN, return empty string (treated as orphan)
##   - Empty label is returned as empty string (not "None")
##   - Whitespace is stripped from the output
def _inspect_project_label(container_name: str) -> str:
    """Retrieve the com.docker.compose.project label from a container.

    Returns an empty string if:
    - docker is unavailable
    - the container does not exist
    - the label is not set on the container
    An empty string project label is treated as orphan by the caller.
    """
    logger.info("[IMP:7][_inspect_project_label] Inspecting project label for container %s", container_name)
    try:
        # W1 (DevPlan 128): docker inspect — shared/docker_ops (non-fatal)
        ins_r = docker_ops.docker_inspect(
            container_name,
            format='{{index .Config.Labels "com.docker.compose.project"}}',
            timeout=DOCKER_INSPECT_TIMEOUT,
        )
        proj = ins_r.stdout.strip()
        logger.info("[IMP:7][_inspect_project_label] Container %s → project label: '%s'", container_name, proj)
    except FileNotFoundError:
        logger.warning(
            "[IMP:8][_inspect_project_label] docker binary not found — returning empty project for %s", container_name
        )
        return ""
    except subprocess.TimeoutExpired:
        logger.warning(
            "[IMP:8][_inspect_project_label] docker inspect timed out for %s after %ds — returning empty project",
            container_name,
            DOCKER_INSPECT_TIMEOUT,
        )
        return ""
    except (OSError, subprocess.CalledProcessError) as e:
        logger.warning(
            "[IMP:8][_inspect_project_label] Unexpected docker inspect error for %s: %s — returning empty",
            container_name,
            e,
        )
        return ""
    else:
        return proj


# endregion FUNC__inspect_project_label


# region FUNC_batch_orphan_reconciliation
## @purpose  Main entry point: detect orphan containers across all modules in one pass
## @io       ⇥ module_entries: list[str], modules_dir: str → ⎋ list[dict[str, str]]
## @complexity 3 — multi-step pipeline: compose discovery → docker ps → per-service inspect
## @invariants
##   - Returns empty list on any fatal error (docker unavailable, no compose files, etc.)
##   - Each orphan dict has "container_name" and "project" keys
##   - Project is empty string if label not found
##   - Container is orphan if project label != deploy project (compose config "name"),
##     fallback expected = module_name (config без "name")
##   - Container not in docker ps -a output is silently skipped (not an orphan)
##   - All subprocess errors are caught and logged at WARN level — never raised
## @rationale  One-pass algorithm avoids per-module docker ps -a (O(N) → O(1) docker calls).
##             The single docker ps -a call provides a consistent snapshot of container state,
##             avoiding TOCTOU race conditions that per-module calls would create.
## ⚠️ TRAP[BUG] · 2026-08-06 · HI · expected project != module_name (141 B18)
## · Symptom: контейнеры модулей (project=platform от root compose, RC-121) удалялись как
## ·   orphans (expected=module_name) → ВСЕ контейнеры платформы исчезали после деплоя.
## · Fix: expected = compose config "name" (фактический project деплоя) — root "platform",
## ·   модульный деплой = имя модуля; fallback на module_name при отсутствии "name".
def batch_orphan_reconciliation(module_entries: list[str], modules_dir: str) -> list[dict[str, str]]:
    """Detect orphan containers across all modules.

    Algorithm:
    1. Find compose files for each module entry
    2. Run docker ps -a once for all modules
    3. For each module's compose services: resolve container names + deploy project (config name)
    4. For each resolved container that exists: check project label
    5. If project label != deploy project → it's a foreign/orphan container

    Returns a list of orphan dicts: [{"container_name": "...", "project": "..."}]
    The shell facade reads this output and stops+removes each orphan.
    """
    logger.info(
        "[IMP:9][batch_orphan_reconciliation] Starting batch orphan reconciliation for %d entries", len(module_entries)
    )

    if not module_entries:
        logger.info("[IMP:9][batch_orphan_reconciliation] Empty module entries — no orphans to reconcile")
        return []

    # Step 1: Find compose files
    compose_files = _find_compose_files(module_entries, modules_dir)
    if not compose_files:
        logger.info("[IMP:9][batch_orphan_reconciliation] No compose files found — no orphans to reconcile")
        return []

    # Step 2: Single docker ps -a
    existing = _get_existing_containers()
    if not existing:
        logger.info(
            "[IMP:9][batch_orphan_reconciliation] No existing containers from docker ps -a — no orphans to reconcile"
        )
        return []

    logger.info(
        "[IMP:7][batch_orphan_reconciliation] %d containers in docker ps -a, %d compose files to check",
        len(existing),
        len(compose_files),
    )

    # Steps 3-5: For each compose file, resolve services and check project labels.
    # Дедуп по container_name: при полном COMPOSE_PROFILES config каждого модуля видит
    # сервисы ВСЕХ модулей — один контейнер может встретиться N раз (N = число модулей).
    orphans: dict[str, dict[str, str]] = {}

    for mod_name, cf_path in compose_files:
        logger.info("[IMP:7][batch_orphan_reconciliation] Checking module: %s (compose: %s)", mod_name, cf_path)

        container_names, project_name = _get_compose_services(cf_path, mod_name)
        logger.info(
            "[IMP:7][batch_orphan_reconciliation] Module %s has %d container names (deploy project=%s) from compose config",
            mod_name,
            len(container_names),
            project_name,
        )

        # Expected project: фактический project деплоя (root compose → "platform", модульный → имя модуля).
        # Fallback на module_name при config без "name" (безопасность).
        expected_project = project_name or mod_name

        for cname in container_names:
            if cname not in existing:
                logger.info("[IMP:7][batch_orphan_reconciliation] Container %s not in docker ps -a — skipping", cname)
                continue

            # Check the project label
            proj = _inspect_project_label(cname)

            # Container is orphan if: no project label OR project label != deploy project
            if not proj or proj != expected_project:
                logger.info(
                    "[IMP:9][batch_orphan_reconciliation] ORPHAN DETECTED: container=%s, expected_project=%s, actual_project='%s'",
                    cname,
                    expected_project,
                    proj,
                )
                orphans[cname] = {"container_name": cname, "project": proj}
            else:
                logger.info(
                    "[IMP:7][batch_orphan_reconciliation] Container %s OK — project label matches deploy project %s",
                    cname,
                    expected_project,
                )

    orphan_list = list(orphans.values())
    logger.info(
        "[IMP:9][batch_orphan_reconciliation] Batch orphan reconciliation complete — %d orphans found across %d modules",
        len(orphan_list),
        len(compose_files),
    )
    return orphan_list


# endregion FUNC_batch_orphan_reconciliation


# region FUNC__list_module_dirs
## @purpose  Список всех директорий-модулей в modules_dir (для disabled-детекции T14/F-027).
## @io       ⇥ modules_dir: str → ⎋ list[str] (имена модулей; [] при отсутствии dir)
## @complexity O(N) — один listdir
## @invariants
##   - Только директории (файлы игнорируются)
##   - Скрытые/служебные каталоги (.git, __pycache__) отфильтрованы
def _list_module_dirs(modules_dir: str) -> list[str]:
    """List all module directory names under modules_dir (plan 012 T14)."""
    base = Path(modules_dir)
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith((".", "_")))


# endregion FUNC__list_module_dirs


# region FUNC_detect_disabled_module_containers
## @purpose  plan 012 T14 (F-027): детекция контейнеров ВЫКЛЮЧЕННЫХ модулей — живой контейнер
##           модуля, отсутствующего в желаемом наборе (COMPOSE_PROFILES/node.yaml enabled),
##           снимается. Containers ONLY — volumes НЕ затрагиваются (docker rm без -v;
##           docker_ops.docker_rm не имеет volume-флагов). Dry-run = детекция без remove.
## @io       ⇥ enabled_names: list[str], modules_dir: str → ⎋ list[dict[str, str]] (orphans)
## @complexity 2 — listdir + docker ps -a + per-disabled compose config
## @invariants
##   - disabled = все module-dir'ы с compose-файлом МИНУС enabled_names
##   - Контейнер учитывается как orphan только если реально существует в docker ps -a
##   - Volumes НЕ удаляются (удаление только контейнера — remove_orphans → docker rm -f)
##   - Subprocess-сбои WARN-logged, никогда не raise (graceful, зеркально batch_*)
## @rationale Существующий batch_orphan_reconciliation детектит ЧУЖИЕ контейнеры (project-label
##            mismatch) среди ENABLED модулей; контейнеры отключённого модуля (правильный label,
##            но модуль убран из node.yaml) он не видит — F-027. Отдельная детекция по
##            дополнению желаемого набора.
def detect_disabled_module_containers(enabled_names: list[str], modules_dir: str) -> list[dict[str, str]]:
    """Detect live containers of modules NOT in the desired set (plan 012 T14 / F-027)."""
    enabled_set = set(enabled_names)
    all_dirs = _list_module_dirs(modules_dir)
    disabled_entries = [name for name in all_dirs if name not in enabled_set]
    if not disabled_entries:
        logger.info("[IMP:8][detect_disabled] All %d module dirs are enabled — no disabled modules", len(all_dirs))
        return []

    compose_files = _find_compose_files(disabled_entries, modules_dir)
    if not compose_files:
        logger.info("[IMP:8][detect_disabled] No disabled modules with compose files")
        return []

    existing = _get_existing_containers()
    if not existing:
        logger.info("[IMP:8][detect_disabled] docker ps -a empty — nothing to reconcile")
        return []

    orphans: dict[str, dict[str, str]] = {}
    for mod_name, cf_path in compose_files:
        container_names, _project = _get_compose_services(cf_path, mod_name)
        for cname in container_names:
            if cname in existing:
                logger.info(
                    "[IMP:9][detect_disabled] DISABLED-MODULE CONTAINER: %s (module=%s, disabled) — will be removed",
                    cname,
                    mod_name,
                )
                orphans[cname] = {"container_name": cname, "project": mod_name}
            else:
                logger.info(
                    "[IMP:7][detect_disabled] %s not running (module=%s disabled) — nothing to remove", cname, mod_name
                )

    orphan_list = list(orphans.values())
    logger.info(
        "[IMP:9][detect_disabled] Disabled-module reconciliation complete — %d container(s) of disabled modules",
        len(orphan_list),
    )
    return orphan_list


# endregion FUNC_detect_disabled_module_containers


# region FUNC_remove_orphans
## @purpose  Remove orphan containers detected by batch_orphan_reconciliation.
##           Публичный wrapper над приватной _self_heal_orphan_containers (DevPlan 117 D18) —
##           используется docker_orchestrator.deploy_docker_module (межмодульный доступ
##           к приватным запрещён, гейт T6.1).
## @io       ⇥ orphans: list[dict[str, str]] → ⎋ int (removed_count)
## @complexity — O(n) где n = len(orphans)
## @invariants
##   - Delegates to _self_heal_orphan_containers (docker rm -f per orphan)
##   - Returns removed count (0 если orphans пуст)
def remove_orphans(orphans: list[dict[str, str]]) -> int:
    """Remove orphan containers using docker rm -f (public API, DevPlan 117 D18)."""
    if not orphans:
        logger.info("[IMP:8][remove_orphans] No orphans to remove")
        return 0
    return _self_heal_orphan_containers(orphans)


# endregion FUNC_remove_orphans


# region FUNC__self_heal_orphan_containers
## @purpose  Remove orphan containers detected by batch_orphan_reconciliation.
##           Only active when --self-heal flag is set (W5-E5).
def _self_heal_orphan_containers(orphans: list[dict[str, str]]) -> int:
    """Remove orphan containers using docker rm -f.

    ## @io — ⇥ orphans: list of {container_name, project} dicts (from batch_orphan_reconciliation)
    ##           → ⎋ removed_count: int
    ## @complexity — O(n) where n = len(orphans)
    """
    removed = 0
    for orphan in orphans:
        cname = orphan.get("container_name", "") or orphan.get("container", "")
        if not cname:
            continue
        # W1 (DevPlan 128): docker rm -f — shared/docker_ops (non-fatal)
        if docker_ops.docker_rm(cname, force=True, timeout=DOCKER_RM_TIMEOUT):
            logger.info(
                "[IMP:9][self_heal][orphan] Removed orphan container: %s (module: %s)",
                cname,
                orphan.get("project", "unknown"),
            )
            removed += 1
        else:
            logger.warning("[IMP:8][self_heal][orphan] Failed to remove orphan %s", cname)
    return removed


# endregion FUNC__self_heal_orphan_containers


# region FUNC__self_heal_aged_images
## @purpose  Prune aged Docker images to free disk space (W5-E5).
##           Only active when --self-heal flag is set.
def _self_heal_aged_images(retention_days: int = DEFAULT_IMAGE_RETENTION_DAYS) -> int:
    """Prune Docker images older than retention_days.

    ## @io — ⇥ retention_days: int (default 30) → ⎋ pruned_count: int
    ## @complexity — O(1)
    ## @invariants
    ##   - Filters by label=com.docker.compose.project (NOT dangling-only)
    ##   - Default retention = 30d (overridable via node.yaml image_retention_days)
    """
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        result = subprocess.run(
            [
                "docker",
                "image",
                "prune",
                "-f",
                "--filter",
                f"until={retention_days * 24}h",
                "--filter",
                "label=com.docker.compose.project",
            ],
            capture_output=True,
            timeout=IMAGE_CHECK_TIMEOUT,
            check=False,
        )
        # Parse prune output for count (docker image prune reports "Total reclaimed space")
        # Совместимость со str-фейками тестов И bytes-каналом (без text=True) — единый нормализатор
        stdout = result.stdout
        if isinstance(stdout, bytes):  # pyright: ignore[reportUnnecessaryIsInstance] — defensive: str-фейки тестов (без text=True канон → bytes)
            stdout = stdout.decode("utf-8", errors="replace")
        # Count lines that look like deleted images (they contain the image tag/id)
        lines = [line for line in stdout.splitlines() if line.strip() and not line.startswith("Total")]
        pruned = len(lines)

        if pruned > 0:
            logger.info(
                "[IMP:9][self_heal][prune] Pruned %d aged images (retention: %d days)",
                pruned,
                retention_days,
            )
        else:
            logger.info(
                "[IMP:7][self_heal][prune] No aged images to prune (retention: %d days)",
                retention_days,
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:5][self_heal][prune] Image prune failed: %s", exc)
        return 0
    else:
        return pruned


# endregion FUNC__self_heal_aged_images


# region FUNC_main
class _CliArgs(Protocol):
    """Typed argparse-namespace контракт (W11: reportAny=error — Namespace-атрибуты Any).

    ## @purpose  Типизация CLI-аргументов через Protocol (БЕЗ runtime-атрибутов — обход
    ##            hasattr-гейта argparse: class-атрибут со значением заставляет parse_args
    ##            пропускать parser-default для dest; Protocol-cast runtime не создаёт).
    ## @invariants  Поля = имена dest'ов argparse (всегда устанавливаются parser'ом).
    """

    module_entries: str
    modules_dir: str
    self_heal: bool
    dry_run: bool


## @purpose  CLI entrypoint: parse args, run reconciliation, optionally self-heal (--self-heal).
##           Outputs orphans as pipe-delimited lines. With --self-heal, removes orphan containers
##           and prunes aged Docker images (W5-E5).
## @io       ⇥ sys.argv → ⎋ exit 0 (always); stdout: "container_name|project_name" per orphan
## @complexity 2 — reconciliation + optional self-heal dispatch
## @invariants
##   --module-entries accepts comma-separated "name:overlay" or just "name"
##   --modules-dir defaults to "/opt/platform/modules" (standard deploy path)
##   --self-heal enables docker rm -f + docker image prune after detection
##   - Output is on stdout, one orphan per line in "container_name|project_name" format
##   - Empty project field outputs as empty string: "container_name|"
##   - Exit code is always 0 — shell reads stdout and handles stop/rm
def main() -> int:
    """CLI entrypoint for orphan_reconciler.py.

    Usage:
        python3 orphan_reconciler.py --module-entries "module1:overlay1,module2" --modules-dir /opt/platform/modules
        python3 orphan_reconciler.py --module-entries "module1:overlay1" --self-heal

    Output format (one orphan per line):
        container_name|project_name

    With --self-heal:
        Removes orphan containers (docker rm -f) and prunes aged images (docker image prune).
    """
    parser = argparse.ArgumentParser(
        description="Batch orphan container reconciliation — detect containers "  # pyright: ignore[reportImplicitStringConcatenation] — W11: смежные строки (эквивалент ISC)
        "whose compose project label does not match their module.",
    )
    _ = parser.add_argument(
        "--module-entries",
        required=True,
        type=str,
        help="Comma-separated list of module entries: 'name:overlay' or just 'name'",
    )
    _ = parser.add_argument(
        "--modules-dir",
        default=os.path.join(str(platform_remote_base()), "modules"),
        type=str,
        help="Path to modules directory (default: PLATFORM_ROOT/modules)",
    )
    _ = parser.add_argument(
        "--self-heal",
        action="store_true",
        default=False,
        help="Enable self-heal mode: remove orphan containers and prune aged images (default: detect-only)",
    )
    _ = parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="plan 012 T14: печатает план (disabled-module containers) без мутаций (implied detect-only)",
    )

    # W11: parse_args → Namespace (атрибуты Any) — Protocol-cast через object (см. _CliArgs)
    args = cast(_CliArgs, cast(object, parser.parse_args()))

    # Parse comma-separated entries
    entries = [e.strip() for e in args.module_entries.split(",") if e.strip()]
    logger.info(
        "[IMP:7][main] CLI args: module_entries=%s, modules_dir=%s, self_heal=%s, dry_run=%s",
        entries,
        args.modules_dir,
        args.self_heal,
        args.dry_run,
    )

    # Run reconciliation
    orphans = batch_orphan_reconciliation(entries, args.modules_dir)
    # plan 012 T14 (F-027): контейнеры выключенных модулей (docker rm БЕЗ -v → volumes целы)
    disabled_orphans = detect_disabled_module_containers(entries, args.modules_dir)
    all_orphans = orphans + [o for o in disabled_orphans if o not in orphans]

    # ── Self-heal mode (W5-E5 + plan 012 T14) ──
    if args.self_heal and not args.dry_run:
        if all_orphans:
            removed = _self_heal_orphan_containers(all_orphans)
            logger.info("[IMP:9][main][self_heal] Removed %d orphan container(s)", removed)
        else:
            logger.info("[IMP:7][main][self_heal] No orphan containers to remove")
        pruned = _self_heal_aged_images()
        logger.info("[IMP:9][main][self_heal] Pruned %d aged image(s)", pruned)
    elif args.dry_run:
        for orphan in all_orphans:
            logger.info(
                "[IMP:8][main][dry-run] WOULD remove orphan %s (project=%s)",
                orphan["container_name"],
                orphan["project"],
            )

    # Output each orphan on its own line as "container_name|project_name"
    for orphan in all_orphans:
        print(f"{orphan['container_name']}|{orphan['project']}")

    logger.info("[IMP:7][main] CLI complete — %d orphans output to stdout", len(all_orphans))
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
