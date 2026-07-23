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

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 30  # seconds for du -sb (large projects may take time)


# region FUNC_get_projects
## @purpose  Collect project code sizes with mtime-based caching
## @io       ⇥ node_yaml_path: str — path to node.yaml
##           ⇥ image_cache: dict[str, int] — {sha256: size_bytes} from docker_collector
##           ⇥ cache_mgr: CacheManager — TTL cache instance
##           ⎋ list[dict] — project data with code/image sizes
## @complexity  O(P) where P = projects, each du is O(1) subprocess or cache lookup
def get_projects(node_yaml_path: str, image_cache: dict[str, int] | None = None, cache_mgr=None) -> list[dict]:
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
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:8][project_collector][get_projects] Starting project collection")

    # Step 1: Load node.yaml
    try:
        import yaml

        with open(node_yaml_path) as f:
            node_data = yaml.safe_load(f) or {}
    except Exception as exc:
        _logger.warning("[IMP:8][project_collector][get_projects] Failed to load node.yaml %s: %s", node_yaml_path, exc)
        return []

    projects_config = node_data.get("projects", [])
    if not projects_config:
        _logger.info("[IMP:8][project_collector][get_projects] No projects in node.yaml")
        return []

    if image_cache is None:
        image_cache = {}
    projects: list[dict] = []
    code_paths_root = "/opt/projects"

    for p in projects_config:
        if not isinstance(p, dict):
            continue

        name = p.get("name", "")
        domain = p.get("domain", "")
        docker_image = p.get("docker_image", "")

        if not name:
            continue

        # Step 2: mtime check + du -sb
        code_path = os.path.join(code_paths_root, name)
        code_size_bytes = _get_code_size_cached(code_path, name, cache_mgr, _logger)

        # Step 3: Image size from cache
        docker_image_size_bytes = 0
        if docker_image and image_cache:
            docker_image_size_bytes = image_cache.get(docker_image, 0)

        project = {
            "name": name,
            "domain": domain,
            "code_size_bytes": code_size_bytes,
            "docker_image": docker_image,
            "docker_image_size_bytes": docker_image_size_bytes,
        }
        projects.append(project)

    _logger.info("[IMP:9][project_collector][get_projects] Collected %d project(s)", len(projects))
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
def _get_code_size_cached(code_path: str, name: str, cache_mgr, _logger: logging.Logger) -> int:
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
            _logger.info("[IMP:8][project_collector][_get_code_size] Cache HIT for %s (size=%d)", name, cached.get("size", 0))
            return cached.get("size", 0)

    # Cache miss — run du -sb
    _logger.info("[IMP:9][project_collector][_get_code_size] Cache MISS for %s — running du -sb", name)
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
