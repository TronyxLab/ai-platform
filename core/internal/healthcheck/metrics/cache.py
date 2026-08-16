#!/usr/bin/env python3
# GREP_SUMMARY: ttl-cache cache-manager json on-disk mtime-invalidation metrics
# STRUCTURE: ▶ CacheManager(cache_dir) → get(key, ttl, source_mtime) → ◇ fresh? → return cached | ⎋ None
#            → set(key, data) → save with timestamp → ⎋
# region MODULE_CONTRACT
## @purpose  Generic TTL cache for inventory data (certs, image sizes, project sizes)
## @scope    Host-side: /var/cache/platform/metrics/ — JSON files on disk
## @invariants
##   - Cache stored as JSON files on disk (/var/cache/platform/metrics/)
##   - os.makedirs(exist_ok=True) on init
##   - get() returns None on cache miss (caller recomputes)
##   - get() with source_mtime: if source file mtime > cache timestamp → cache miss
##   - set() writes JSON with current timestamp (time.time())
##   - Graceful: corrupt cache file → None (recompute), not crash
## @rationale TTL cache reduces expensive operations (du -sb, x509 parsing) to once per hour.
##            mtime-based invalidation handles file changes between TTL cycles.
# endregion MODULE_CONTRACT

import json
import logging
import os
import pathlib
import time
from collections.abc import Mapping
from typing import cast

# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
from core.internal.shared.atomic_writer import atomic_write_json as _atomic_write_json

logger = logging.getLogger(__name__)


# region CLASS_CacheManager
class CacheManager:
    """TTL cache manager backed by JSON files on disk.

    # ▶ ┌cache_dir┐ → os.makedirs(exist_ok=True)
    #    → get(key) → ◇ cache file exists AND fresh? → return data | ⎋ None
    #    → set(key, data) → json dump with timestamp → ⎋

    Cache files are stored as {cache_dir}/{key}.json.
    Each file contains: {"timestamp": float, "data": {...}}
    """

    # region FUNC___init__
    ## @purpose  Initialize cache manager — ensure cache directory exists
    ## @io       ⇥ cache_dir: str — path to cache directory (created if not exists)
    ## @complexity  O(1)
    def __init__(self, cache_dir: str = "/var/cache/platform/metrics"):
        """Initialize CacheManager with a cache directory.

        Creates the directory if it doesn't exist (exist_ok=True).
        """
        self.cache_dir = cache_dir
        logger_ = logging.getLogger(__name__)
        try:
            os.makedirs(cache_dir, mode=0o755, exist_ok=True)
            logger_.info("[IMP:8][cache][init] Cache directory ensured: %s", cache_dir)
        except OSError as exc:
            logger_.warning("[IMP:8][cache][init] Cannot create cache dir %s: %s", cache_dir, exc)

    # endregion FUNC___init__

    # region FUNC__cache_path
    def _cache_path(self, key: str) -> str:
        """Get the filesystem path for a cache key.

        ## @io  ⇥ key: str → ⎋ str (absolute path to cache file)
        ## @complexity  O(1)
        """
        # Sanitize key: replace non-alphanumeric chars with underscore
        safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        return os.path.join(self.cache_dir, f"{safe_key}.json")

    # endregion FUNC__cache_path

    # region FUNC_get
    ## @purpose  Retrieve cached data if fresh, return None on miss
    ## @io       ⇥ key: str — cache key
    ##           ⇥ ttl_seconds: int — TTL in seconds (default: 3600)
    ##           ⇥ source_mtime: float | None — source file mtime for invalidation
    ##           ⎋ dict | None — cached data or None
    ## @complexity  O(1) — single file read
    def get(self, key: str, ttl_seconds: int = 3600, source_mtime: float | None = None) -> dict[str, object] | None:
        """Get cached data for key. Returns None on cache miss or expiry.

        # ▶ ┌key┐ → ◇ cache file exists? → ◇ within TTL? → ◇ source_mtime check? → return data | ⎋ None
        #                                                     └→ expired → ⎋ None
        #                                └→ not found → ⎋ None

        If source_mtime is provided and is newer than cache timestamp → miss.
        If cache timestamp + ttl < now → miss.
        """
        logger_ = logging.getLogger(__name__)
        cache_path = self._cache_path(key)

        if not os.path.isfile(cache_path):
            logger_.info("[IMP:8][cache][get] Cache MISS (no file): %s", key)
            return None

        try:
            with pathlib.Path(cache_path).open(encoding="utf-8") as f:
                cached = cast("dict[str, object]", json.load(f))  # W11: json → Any → dict[str, object]
        except (OSError, json.JSONDecodeError) as exc:
            logger_.warning("[IMP:8][cache][get] Cache MISS (corrupt): %s: %s", key, exc)
            return None

        cache_timestamp = cast("float", cached.get("timestamp", 0))
        now = time.time()

        # TTL check
        if cache_timestamp + ttl_seconds < now:
            logger_.info("[IMP:8][cache][get] Cache MISS (TTL expired): %s (age=%.0fs)", key, now - cache_timestamp)
            return None

        # Source mtime invalidation check
        if source_mtime is not None and source_mtime > cache_timestamp:
            logger_.info(
                "[IMP:8][cache][get] Cache MISS (source mtime newer): %s (mtime=%.0f > cache=%.0f)",
                key,
                source_mtime,
                cache_timestamp,
            )
            return None

        logger_.info("[IMP:9][cache][get] Cache HIT: %s (age=%.0fs)", key, now - cache_timestamp)
        return cast("dict[str, object] | None", cached.get("data"))

    # endregion FUNC_get

    # region FUNC_set
    ## @purpose  Store data in cache with current timestamp
    ## @io       ⇥ key: str — cache key
    ##           ⇥ data: dict — data to cache (JSON-serializable)
    ##           ⎋ None
    ## @complexity  O(1) — single file write
    def set(self, key: str, data: Mapping[str, object]) -> None:
        """Save data to cache with current timestamp.

        # ▶ ┌key + data┐ → {timestamp: now, data: data} → json dump → ⎋
        """
        logger_ = logging.getLogger(__name__)
        cache_path = self._cache_path(key)

        cache_entry: dict[str, object] = {
            "timestamp": time.time(),
            "data": data,
        }

        try:
            # Atomic write via shared atomic_writer canon (E5 — tempfile + fsync + os.replace)
            _atomic_write_json(cache_path, cache_entry)
            logger_.info("[IMP:9][cache][set] Cache updated: %s", key)
        except (OSError, TypeError) as exc:
            logger_.warning("[IMP:8][cache][set] Cache write failed for %s: %s", key, exc)

    # endregion FUNC_set

    # region FUNC_clear
    ## @purpose  Remove a cache entry
    ## @io       ⇥ key: str — cache key to clear
    ##           ⎋ None
    ## @complexity  O(1)
    def clear(self, key: str) -> None:
        """Remove a cache entry from disk.

        # ▶ ┌key┐ → os.unlink(cache_path) → ⎋
        """
        logger_ = logging.getLogger(__name__)
        cache_path = self._cache_path(key)
        try:
            os.unlink(cache_path)
            logger_.info("[IMP:8][cache][clear] Cache cleared: %s", key)
        except OSError as exc:
            logger_.warning("[IMP:8][cache][clear] Cache clear failed for %s: %s", key, exc)

    # endregion FUNC_clear


# endregion CLASS_CacheManager
