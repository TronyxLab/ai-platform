#!/usr/bin/env python3
# GREP_SUMMARY: docker-collector container status resource-usage image-size batch-inspect stats
# STRUCTURE: ▶ get_containers → docker ps -aq → batch docker inspect (all) → docker stats --no-stream → merge containers[] → ⎋ list[dict]
#            ▶ get_image_sizes → docker image inspect (sha256 IDs) → map {sha256: Size} → ⎋ dict
# region MODULE_CONTRACT
## @purpose  Docker collector — container status, resource usage (CPU%, memory), image sizes
## @scope    Host-side cron export: docker CLI via subprocess, NO docker SDK
## @invariants
##   - Batch-first: docker inspect all containers in one subprocess call
##   - docker stats --no-stream for runtime metrics (CPU%, memory usage/limit)
##   - Image sizes by sha256 digest, NOT by tag name
##   - All subprocess calls have timeout (default 15s)
##   - Graceful degradation: returns partial data with errors[] on subprocess failure
## @rationale docker stats --no-stream replaces docker inspect for runtime metrics (META Δ1).
##            Batch inspect reduces subprocess calls from O(N) to O(1).
# endregion MODULE_CONTRACT

import json
import logging

from core.internal.shared import docker_ops  # W1: docker ps/inspect/stats/image примитивы (гейт docker_sole_path)

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 15  # seconds, per subprocess call


# region FUNC_get_containers
## @purpose  Collect container status + resource usage via batch docker inspect + docker stats
## @strategy  docker ps -aq → batch docker inspect (one call) → docker stats --no-stream (one call) → merge by container name
## @io       ⇥ (none, uses docker CLI) → ⎋ list[dict] containers
## @complexity  O(1) subprocess calls (always 2), O(N) merge
def get_containers() -> list[dict]:
    """Collect all container statuses with resource usage.

    # ▶ docker ps -aq → ∑ all_ids → docker inspect batch → docker stats batch → ⊕ merge containers[] → ⎋ list[dict]

    Returns a list of container dicts with keys:
    name, running, healthy, exit_code, status_line, image, image_id,
    memory_usage_bytes, memory_limit_bytes, cpu_percent, restart_policy.
    Returns empty list on total failure.
    """
    import logging

    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:8][docker_collector][get_containers] Starting container collection")

    # Step 1: docker ps -aq → all container IDs (W1: shared/docker_ops, non-fatal)
    ps_result = docker_ops.docker_ps(all=True, quiet=True, timeout=_SUBPROCESS_TIMEOUT)
    if ps_result.returncode != 0:
        _logger.warning("[IMP:8][docker_collector][get_containers] docker ps -aq failed: %s", ps_result.stderr.strip())
        return []
    all_ids = [cid.strip() for cid in ps_result.stdout.strip().splitlines() if cid.strip()]
    _logger.info("[IMP:8][docker_collector][get_containers] Found %d container(s) via docker ps -aq", len(all_ids))

    if not all_ids:
        _logger.info("[IMP:8][docker_collector][get_containers] No containers found")
        return []

    # Step 2: Batch docker inspect (ALL containers, ONE subprocess call; W1: shared/docker_ops)
    inspect_result = docker_ops.docker_inspect_many(all_ids, timeout=_SUBPROCESS_TIMEOUT)
    if inspect_result.returncode != 0:
        _logger.warning(
            "[IMP:8][docker_collector][get_containers] docker inspect batch failed: %s",
            inspect_result.stderr.strip(),
        )
        return []
    try:
        inspect_data = json.loads(inspect_result.stdout)
    except json.JSONDecodeError as exc:
        _logger.warning("[IMP:8][docker_collector][get_containers] docker inspect parse error: %s", exc)
        return []
    _logger.info(
        "[IMP:9][docker_collector][get_containers] Batch docker inspect completed for %d container(s)", len(all_ids)
    )

    # Step 3: docker stats --no-stream (runtime CPU% + memory; W1: shared/docker_ops, non-fatal)
    stats_map: dict[str, dict] = {}
    stats_result = docker_ops.docker_stats("{{json .}}", timeout=_SUBPROCESS_TIMEOUT)
    if stats_result.returncode == 0 and stats_result.stdout.strip():
        for line in stats_result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                stat = json.loads(line)
                name = stat.get("Name", "")
                stats_map[name] = {
                    "cpu_percent": _parse_percent(stat.get("CPUPerc", "0%")),
                    "memory_usage_bytes": _parse_bytes(stat.get("MemUsage", "0B / 0B").split(" / ")[0]),
                    "memory_limit_bytes": _parse_bytes(stat.get("MemLimit", "0B") or "0B"),
                }
            except (json.JSONDecodeError, KeyError):
                continue
        _logger.info(
            "[IMP:9][docker_collector][get_containers] docker stats completed for %d container(s)", len(stats_map)
        )

    # Step 4: Merge inspect_data + stats_map into container dicts
    containers: list[dict] = []
    for c in inspect_data:
        if not isinstance(c, dict):
            continue
        state = c.get("State", {}) or {}
        config = c.get("Config", {}) or {}
        host_config = c.get("HostConfig", {}) or {}
        name = c.get("Name", "").lstrip("/")
        image = config.get("Image", "")
        image_id = config.get("Image", "")
        # Resolve image ID from RepoTags or RepoDigests
        if c.get("RepoDigests"):
            image_id = c["RepoDigests"][0].split("@")[1] if "@" in c["RepoDigests"][0] else image_id

        # Get runtime stats from stats_map
        stats = stats_map.get(name, {})

        started_at_raw = state.get("StartedAt", "")
        # StartedAt format from docker API: "2026-07-24T00:00:00.000000000Z"
        # Pass through directly; consumer (app.py _enrich_containers) converts to human-readable
        started_at = started_at_raw if started_at_raw else None

        container = {
            "name": name,
            "running": state.get("Running", False),
            "healthy": _get_health_status(state),
            "exit_code": state.get("ExitCode"),
            "status_line": state.get("Status", ""),
            "started_at": started_at,
            "image": image,
            "image_id": c.get("Image", ""),
            "memory_usage_bytes": stats.get("memory_usage_bytes", 0),
            "memory_limit_bytes": stats.get("memory_limit_bytes", 0),
            "cpu_percent": stats.get("cpu_percent", 0.0),
            "restart_policy": host_config.get("RestartPolicy", {}).get("Name", ""),
        }
        containers.append(container)

    _logger.info("[IMP:9][docker_collector][get_containers] Collected %d container(s) total", len(containers))
    return containers


# endregion FUNC_get_containers


# region FUNC_get_image_sizes
## @purpose  Get image sizes by sha256 digest — batch docker image inspect
## @io       ⇥ image_ids: set[str] — set of sha256 image references
##           ⎋ dict[str, int] — {sha256: size_bytes}
## @complexity  O(1) subprocess call, O(N) parse
def get_image_sizes(image_ids: set[str]) -> dict[str, int]:
    """Get docker image sizes by sha256 digest.

    # ▶ ┌image_ids┐ → docker image inspect (batch) --format '{{json .}}' → ⊕ map[sha256] → ⎋ {sha256: Size}

    Returns {sha256: size_bytes} for each requested image ID.
    Returns empty dict on failure.
    """
    _logger = logging.getLogger(__name__)
    if not image_ids:
        _logger.info("[IMP:8][docker_collector][get_image_sizes] No image IDs to inspect")
        return {}

    ids_list = list(image_ids)
    _logger.info("[IMP:8][docker_collector][get_image_sizes] Inspecting %d image(s)", len(ids_list))

    # W1: docker image inspect batch — shared/docker_ops (non-fatal)
    result = docker_ops.docker_image_inspect_many(ids_list, "{{json .}}", timeout=_SUBPROCESS_TIMEOUT)
    if result.returncode != 0:
        _logger.warning(
            "[IMP:8][docker_collector][get_image_sizes] docker image inspect failed: %s",
            result.stderr.strip()[:200],
        )
        return {}

    sizes: dict[str, int] = {}
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            img = json.loads(line)
            # Extract sha256 ID from Id field
            img_id = img.get("Id", "")
            img_size = img.get("Size", 0)
            if img_id:
                sizes[img_id] = img_size
        except json.JSONDecodeError:
            continue

    _logger.info("[IMP:9][docker_collector][get_image_sizes] Got sizes for %d image(s)", len(sizes))
    return sizes


# endregion FUNC_get_image_sizes


# region HELPER__get_health_status
def _get_health_status(state: dict) -> bool:
    """Extract health status from docker container State dict.

    ## @purpose  Normalize Health.Status field: 'healthy' → True, all else → False
    ## @io       ⇥ state: dict from docker inspect → ⎋ bool
    ## @complexity  O(1)
    """
    health = state.get("Health", {}) or {}
    return health.get("Status") == "healthy"


# endregion HELPER__get_health_status


# region HELPER__parse_percent
def _parse_percent(value: str) -> float:
    """Parse a percentage string like '2.45%' → 2.45.

    ## @io       ⇥ value: str (e.g. '2.45%', '0.00%') → ⎋ float
    ## @complexity  O(1)
    """
    try:
        return float(value.strip().rstrip("%"))
    except (ValueError, AttributeError):
        return 0.0


# endregion HELPER__parse_percent


# region HELPER__parse_bytes
def _parse_bytes(value: str) -> int:
    """Parse a memory string like '1.234GiB' or '500MiB' to bytes.

    ## @io       ⇥ value: str (e.g. '1.234GiB', '500MiB', '1.5kB') → ⎋ int (bytes)
    ## @complexity  O(1)
    """
    import re

    value = value.strip()
    if not value:
        return 0

    units = {
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "TiB": 1024**4,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "B": 1,
    }

    match = re.match(r"([\d.]+)\s*([KMGTP]?i?B)", value, re.IGNORECASE)
    if not match:
        # Try plain number (bytes)
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 0

    num = float(match.group(1))
    unit = match.group(2)
    multiplier = units.get(unit, 1)
    return int(num * multiplier)


# endregion HELPER__parse_bytes


if __name__ == "__main__":
    # Quick manual test
    logging.basicConfig(level=logging.INFO)
    containers = get_containers()
    print(f"Containers: {len(containers)}")
    for c in containers[:3]:
        print(f"  {c['name']}: running={c['running']} healthy={c['healthy']} cpu={c['cpu_percent']}%")
    image_ids = {c["image_id"] for c in containers if c.get("image_id")}
    sizes = get_image_sizes(image_ids)
    print(f"Image sizes: {len(sizes)}")
