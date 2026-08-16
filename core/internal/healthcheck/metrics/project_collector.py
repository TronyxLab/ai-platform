#!/usr/bin/env python3
# GREP_SUMMARY: project-collector code-size du-sb mtime-cache node.yaml projects docker-image-size
# STRUCTURE: ▶ get_projects(node_yaml, image_cache, cache_mgr) → projects[] → mtime check → du -sb (if cache miss)
#            → ⊕ projects[{name, domain, code_size_bytes, docker_image, docker_image_size_bytes}] → ⎋ list[dict]
# region MODULE_CONTRACT
## @purpose  Project code size collector via du -sb with mtime-based cache
## @scope    Host-side: reads node.yaml for project list, computes code sizes (bytes, machine-parseable)
## @invariants
##   - du -sb (bytes, machine-parseable), NOT du -sh (human-readable) — META Δ3
##   - mtime-based cache: project size recalculated ONLY when mtime changed OR >1 hour — Δ20
##   - Image size from image_cache (batch-loaded by docker_collector) by sha256, not by tag — Δ15
##   - Graceful: missing project dir → skip, not crash
## @rationale du -sb avoids human-parse of "1.4G" strings. mtime cache reduces disk I/O from 60x/hour to ~1x/hour.
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
from typing import TypedDict, cast

from core.internal.healthcheck.metrics.cache import CacheManager

# B2: канонический корень проектов — shared/deploy_paths (литерал /opt/projects удалён)
from core.internal.shared.deploy_paths import projects_base
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
from core.internal.shared.node_yaml import NodeYaml

# W1-A1 (план 170): _SUBPROCESS_TIMEOUT=30 (дубль SoT) → CONVERGE_DOCKER_TIMEOUT (30) —
# du -sb по крупным проектам использует каноническое 30s окно (C10).
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT as _SUBPROCESS_TIMEOUT

logger = logging.getLogger(__name__)


# region DATA_ProjectInfo
class ProjectInfo(TypedDict):
    """Запись проекта (граница status-metrics.json) — name/domain/code/image sizes.

    ## @purpose  Единица вывода get_projects: имя/домен проекта + code_size_bytes (du -sb)
    ##            + docker_image sha256 + размер образа из image_cache.
    """

    name: str
    domain: str
    code_size_bytes: int
    docker_image: str
    docker_image_size_bytes: int


# endregion DATA_ProjectInfo


# region FUNC_get_projects
## @purpose  Collect project code sizes with mtime-based caching
## @io       ⇥ node_yaml_path: str — path to node.yaml
##           ⇥ image_cache: dict[str, int] — {sha256: size_bytes} from docker_collector
##           ⇥ cache_mgr: CacheManager — TTL cache instance
##           ⎋ list[dict] — project data with code/image sizes
## @complexity  O(P) where P = projects, each du is O(1) subprocess or cache lookup
def get_projects(
    node_yaml_path: str, image_cache: dict[str, int] | None = None, cache_mgr: CacheManager | None = None
) -> list[ProjectInfo]:
    """Collect project sizes from node.yaml with mtime-based caching.

    # ▶ node.yaml → ┌projects[]┐
    #    → for each: ◇ mtime unchanged AND <1h → use cache
    #                └→ du -sb <code_path> (timeout=30s)
    #    → image size from image_cache[sha256]
    #    → ⊕ projects[{name, domain, code_size_bytes, ...}] → ⎋ list[dict]

    Each project dict contains:
    name, domain, code_size_bytes, docker_image, docker_image_size_bytes.

    Returns empty list on failure.
    """
    logger_ = logging.getLogger(__name__)
    logger_.info("[IMP:8][project_collector][get_projects] Starting project collection")

    # Step 1: Load node.yaml
    try:
        node = NodeYaml(node_yaml_path)
        projects_config = node.get_projects()
    except (ConfigNotFoundError, ConfigParseError, OSError) as exc:
        logger_.warning("[IMP:8][project_collector][get_projects] Failed to load node.yaml %s: %s", node_yaml_path, exc)
        return []
    if not projects_config:
        logger_.info("[IMP:8][project_collector][get_projects] No projects in node.yaml")
        return []

    if image_cache is None:
        image_cache = {}
    projects: list[ProjectInfo] = []
    # B2: канонический корень проектов — shared/deploy_paths (литерал /opt/projects удалён)
    code_paths_root = str(projects_base())

    for p in projects_config:
        if not isinstance(p, dict):
            continue

        name = str(p.get("name", ""))
        domain = str(p.get("domain", ""))
        docker_image = str(p.get("docker_image", ""))

        if not name:
            continue

        # Step 2: mtime check + du -sb
        code_path = os.path.join(code_paths_root, name)
        code_size_bytes = _get_code_size_cached(code_path, name, cache_mgr, logger_)

        # Step 3: Image size from cache
        docker_image_size_bytes = 0
        if docker_image and image_cache:
            docker_image_size_bytes = image_cache.get(docker_image, 0)

        project: ProjectInfo = {
            "name": name,
            "domain": domain,
            "code_size_bytes": code_size_bytes,
            "docker_image": docker_image,
            "docker_image_size_bytes": docker_image_size_bytes,
        }
        projects.append(project)

    logger_.info("[IMP:9][project_collector][get_projects] Collected %d project(s)", len(projects))
    return projects


# endregion FUNC_get_projects


# region FUNC__get_code_size_cached
## @purpose  Get code size with mtime-based cache logic (or fallback to direct du)
## @io       ⇥ code_path: str — absolute path to project code directory
##           ⇥ name: str — project name (for cache key)
##           ⇥ cache_mgr: CacheManager or None
##           ⇥ _logger: logging.Logger
##           ⎋ int — code size in bytes (0 if unavailable)
## @complexity  O(1) — cache hit or single subprocess
def _get_code_size_cached(code_path: str, name: str, cache_mgr: CacheManager | None, _logger: logging.Logger) -> int:
    """Get project code size, using mtime cache if available.

    # ▶ ┌code_path┐ → ◇ exists? → ◇ cache hit (mtime ok) → use cached | → du -sb → update cache → ⎋ bytes
    #                              └→ missing → ⎋ 0

    Cache key format: "project_size_{name}".
    """
    if not os.path.isdir(code_path):
        _logger.warning("[IMP:8][project_collector][_get_code_size] Project dir not found: %s", code_path)
        return 0

    # Get current mtime for invalidation check
    try:
        current_mtime = os.path.getmtime(code_path)
    except OSError:
        current_mtime = 0.0

    # Try cache
    if cache_mgr is not None:
        cache_key = f"project_size_{name}"
        cached = cache_mgr.get(cache_key, ttl_seconds=3600, source_mtime=current_mtime)
        if cached is not None:
            _logger.info(
                "[IMP:8][project_collector][_get_code_size] Cache HIT for %s (size=%d)", name, cached.get("size", 0)
            )
            return cast("int", cached.get("size", 0))  # W11: кэш-граница JSON (object → int)

    # Cache miss — run du -sb
    _logger.info("[IMP:9][project_collector][_get_code_size] Cache MISS for %s — running du -sb", name)
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        du_result = subprocess.run(
            ["du", "-sb", code_path],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if du_result.returncode == 0 and du_result.stdout.strip():
            size_str = du_result.stdout.strip().split("\t")[0]
            size_bytes = int(size_str)

            # Update cache
            if cache_mgr is not None:
                cache_key = f"project_size_{name}"
                cache_mgr.set(cache_key, {"size": size_bytes, "mtime": current_mtime})

            _logger.info("[IMP:9][project_collector][_get_code_size] du -sb for %s: %d bytes", name, size_bytes)
            return size_bytes
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        _logger.warning("[IMP:8][project_collector][_get_code_size] du -sb failed for %s: %s", name, exc)

    return 0


# endregion FUNC__get_code_size_cached
