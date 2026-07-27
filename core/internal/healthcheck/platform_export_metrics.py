#!/usr/bin/env python3
# GREP_SUMMARY: platform-export-metrics coordinator metrics-collector atomic-write cron status-metrics.json host-uptime backup docker-images-size memory swap uname os
# STRUCTURE: ▶ main() → load node.yaml → docker_collector → cert_collector → project_collector → host_collector
#            → host_uptime → docker_images_size_gb → host_memory → host_uname → backup_collector
#            → merge + errors[] + backup + platform_services
#            → json_writer.atomic_write(/run/platform/status-metrics.json) → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Metrics export coordinator — collects data from all collectors, applies TTL cache,
##           merges, writes atomically to status-metrics.json
## @scope    Host-side cron export: runs every minute via platform-export-metrics.sh wrapper
## @invariants
##   - Runtime data (containers, disk) always fresh — no cache
##   - Inventory data (certs, image sizes, project sizes) use TTL cache (1h default)
##   - All collectors called even if some fail — graceful degradation with errors[] array
##   - Atomic write via json_writer.atomic_write — status-page never sees partial file
##   - schema_version: 2 injected by json_writer
##   - node.yaml path from env var NODE_YAML_PATH or /opt/node-configs/<NODE_NAME>/node.yaml
##   - Output: /run/platform/status-metrics.json
##   - Total execution expected <15s (AC10-M)
## @rationale  Coordinator pattern (META Δ5) separates collection concerns into testable modules.
##             Graceful degradation (Δ13) ensures partial data + errors on any collector failure.
# endregion MODULE_CONTRACT

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# Configuration — lazy env lookup to support test-time env override
# (module-level constants would freeze at first import across tests)
# 🧐 TRAP[DECISION] · 2026-07-23 · — · Lazy env lookup in coordinator
# · Rejected: module-level constants (freeze at first import, break tests)
# · Reason: pytest does not reload modules between test functions; lazy
#   function calls (`_get_*()`) allow each test to set custom env vars.
# · Rev: if performance becomes an issue (unlikely — called once per minute),
#   use env caching with TTL or pytest fixture-based module reload.


def _get_node_yaml_path() -> str:
    """Get node.yaml path from env, with fallback."""
    return os.environ.get(
        "NODE_YAML_PATH",
        os.path.join(
            os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs"),
            os.environ.get("NODE_NAME", "unknown"),
            "node.yaml",
        ),
    )


def _get_status_metrics_json() -> str:
    """Get status-metrics.json output path from env."""
    return os.environ.get("STATUS_METRICS_JSON", "/run/platform/status-metrics.json")


def _get_node_name() -> str:
    """Get node name from env."""
    return os.environ.get("NODE_NAME", "unknown")


def _get_cache_dir() -> str:
    """Get cache directory from env."""
    return os.environ.get("METRICS_CACHE_DIR", "/var/cache/platform/metrics")


# region FUNC_main
## @purpose  Main entry point — coordinate all collectors and write status-metrics.json
## @io       ⇥ sys.argv (unused, config via env vars)
##           ⎋ int — exit code (0 = success)
## @complexity  O(C + P + D + S) where C=certs, P=projects, D=docker count, S=disk stat
def main() -> int:
    """Run the full metrics export pipeline.

    # ▶ load node.yaml
    #    → docker_collector.get_containers() (runtime, always fresh)
    #    → image_ids from containers
    #    → _get_image_sizes_cached() (TTL cache)
    #    → cert_collector.get_certs() (TTL cache)
    #    → project_collector.get_projects() (mtime cache)
    #    → host_collector.get_host_disk() (always fresh)
    #    → merge + errors[] → json_writer.atomic_write()
    #    → ⎋ exit 0

    Returns 0 on success (even partial data), 1 on total failure.
    """
    _logger = logging.getLogger(__name__)
    node_name = _get_node_name()
    status_metrics_json = _get_status_metrics_json()
    node_yaml_path = _get_node_yaml_path()
    cache_dir = _get_cache_dir()

    start = time.monotonic()
    _logger.info("[IMP:9][coordinator][main] Starting metrics export for node=%s", node_name)

    errors: list[str] = []
    containers: list[dict] = []
    certs: list[dict] = []
    projects: list[dict] = []
    host: dict = {}
    image_sizes: dict[str, int] = {}

    # ── 1. Node.yaml (loaded, used by downstream collectors) ──
    _load_node_yaml(node_yaml_path, _logger)

    # ── 2. Docker containers (always fresh) ──
    try:
        from core.internal.healthcheck.metrics.docker_collector import get_containers

        containers = get_containers()
        _logger.info("[IMP:9][coordinator][main] Docker containers collected: %d", len(containers))
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Docker container collection failed: %s", exc)
        errors.append(f"docker_containers: {exc}")

    # Extract unique image IDs for image size lookup
    image_ids: set[str] = set()
    for c in containers:
        img_id = c.get("image_id", "")
        if img_id:
            image_ids.add(img_id)

    # ── 3. Image sizes (TTL cache) ──
    try:
        cache_mgr = _get_cache_manager(_logger, cache_dir)
        image_sizes = _get_image_sizes_cached(image_ids, cache_mgr, _logger)
        _logger.info("[IMP:9][coordinator][main] Image sizes collected: %d", len(image_sizes))
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Image size collection failed: %s", exc)
        errors.append(f"image_sizes: {exc}")

    # ── 4. Certificates (TTL cache) ──
    try:
        from core.internal.healthcheck.metrics.cert_collector import get_certs

        certs = get_certs(node_yaml_path)
        _logger.info("[IMP:9][coordinator][main] Certificates collected: %d", len(certs))
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Cert collection failed: %s", exc)
        errors.append(f"certs: {exc}")

    # ── 5. Projects (mtime cache) ──
    try:
        from core.internal.healthcheck.metrics.project_collector import get_projects

        projects = get_projects(node_yaml_path, image_cache=image_sizes, cache_mgr=cache_mgr)
        _logger.info("[IMP:9][coordinator][main] Projects collected: %d", len(projects))
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Project collection failed: %s", exc)
        errors.append(f"projects: {exc}")

    # ── 6. Host disk (always fresh) ──
    try:
        from core.internal.healthcheck.metrics.host_collector import get_host_disk

        host = get_host_disk()
        _logger.info("[IMP:9][coordinator][main] Host disk collected")
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Host disk collection failed: %s", exc)
        errors.append(f"host: {exc}")

    # ── 6b. Host uptime & load average (always fresh) ──
    try:
        from core.internal.healthcheck.metrics.host_collector import get_host_uptime

        host_uptime = get_host_uptime()
        host.update(host_uptime)
        _logger.info("[IMP:9][coordinator][main] Host uptime & load collected")
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Host uptime collection failed: %s", exc)
        errors.append(f"host_uptime: {exc}")

    # ── 6c. Docker images total size (from image_sizes dict, zero-cost) ──
    try:
        if image_sizes:
            total_bytes = sum(image_sizes.values())
            host["docker_images_size_gb"] = round(total_bytes / (1024**3), 2)
            _logger.info("[IMP:9][coordinator][main] Docker images total size: %.2f GB", host["docker_images_size_gb"])
        else:
            host["docker_images_size_gb"] = 0.0
    except (OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Docker images size calc failed: %s", exc)
        errors.append(f"docker_images_size: {exc}")

    # ── 6d. Host memory & swap (always fresh) ──
    try:
        from core.internal.healthcheck.metrics.host_collector import get_host_memory

        host.update(get_host_memory())
        _logger.info("[IMP:9][coordinator][main] Host memory & swap collected")
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Host memory collection failed: %s", exc)
        errors.append(f"host_memory: {exc}")

    # ── 6e. Host OS/kernel (always fresh) ──
    try:
        from core.internal.healthcheck.metrics.host_collector import get_host_uname

        host.update(get_host_uname())
        _logger.info("[IMP:9][coordinator][main] Host OS/kernel collected")
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Host OS collection failed: %s", exc)
        errors.append(f"host_os: {exc}")

    # ── 7. Backup status ──
    backup: dict = {}
    try:
        from core.internal.healthcheck.metrics.backup_collector import get_backup_status

        backup = get_backup_status()
        _logger.info("[IMP:9][coordinator][main] Backup status collected: %s", backup.get("status", "unknown"))
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][main] Backup collection failed: %s", exc)
        errors.append(f"backup: {exc}")

    # ── 8. Build final data ──
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "schema_version": 2,  # will be overwritten by json_writer.atomic_write
        "generated_at": generated_at,
        "node": node_name,
        "containers": containers,
        "certs": certs,
        "projects": projects,
        "host": host,
        "backup": backup,
        "platform_services": [],  # placeholder — filled in W3 by app.py live checks
        "errors": errors,
    }

    # ── 8. Atomic write ──
    try:
        from core.internal.healthcheck.metrics.json_writer import atomic_write

        atomic_write(data, status_metrics_json)
        duration_s = round(time.monotonic() - start, 2)
        _logger.info(
            "[IMP:9][coordinator][main] Export complete: %d containers, %d certs, %d projects, %d errors in %.2fs",
            len(containers),
            len(certs),
            len(projects),
            len(errors),
            duration_s,
        )
    except (OSError, json.JSONDecodeError) as exc:
        _logger.error("[IMP:9][coordinator][main] Atomic write failed: %s", exc)
        errors.append(f"write: {exc}")
        # If even writing fails, return 1
        return 1

    return 0


# endregion FUNC_main


# region FUNC_load_node_yaml
def _load_node_yaml(path: str, _logger: logging.Logger) -> dict:
    """Load node.yaml, return dict (empty on failure).

    ## @io  ⇥ path: str → ⎋ dict
    ## @complexity  O(1) — single file read + yaml parse
    """
    try:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, yaml.YAMLError, OSError) as exc:
        _logger.warning("[IMP:8][coordinator][_load_node_yaml] Failed to load %s: %s", path, exc)
        return {}


# endregion FUNC_load_node_yaml


# region FUNC_get_cache_manager
def _get_cache_manager(_logger: logging.Logger, cache_dir: str | None = None):
    """Get or create CacheManager instance.

    ## @io  ⇥ cache_dir: str | None — overrides env or default
    ##      ⎋ CacheManager or None
    """
    if cache_dir is None:
        cache_dir = _get_cache_dir()
    try:
        from core.internal.healthcheck.metrics.cache import CacheManager

        return CacheManager(cache_dir)
    except (ImportError, OSError, FileNotFoundError) as exc:
        _logger.warning("[IMP:8][coordinator][_get_cache_manager] Cache init failed: %s", exc)
        return None


# endregion FUNC_get_cache_manager


# region FUNC_get_image_sizes_cached
def _get_image_sizes_cached(image_ids: set[str], cache_mgr, _logger: logging.Logger) -> dict[str, int]:
    """Get image sizes with TTL cache (1 hour).

    ## @io  ⇥ image_ids: set[str], cache_mgr → ⎋ dict[str, int]
    ## @complexity  O(1) subprocess or cache hit
    """
    if not image_ids:
        return {}

    # Try cache
    if cache_mgr is not None:
        cache_key = "image_sizes"
        cached = cache_mgr.get(cache_key, ttl_seconds=3600)
        if cached is not None:
            _logger.info("[IMP:8][coordinator][image_sizes] Cache HIT: %d image(s)", len(cached))
            return cached

    # Cache miss — fetch fresh
    _logger.info("[IMP:9][coordinator][image_sizes] Cache MISS — fetching fresh")
    from core.internal.healthcheck.metrics.docker_collector import get_image_sizes as _docker_image_sizes

    sizes = _docker_image_sizes(image_ids)

    # Save to cache
    if cache_mgr is not None and sizes:
        cache_mgr.set("image_sizes", sizes)

    return sizes


# endregion FUNC_get_image_sizes_cached


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[IMP:%(levelno)s][%(name)s][%(funcName)s] %(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
