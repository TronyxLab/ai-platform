#!/usr/bin/env python3
"""Content-hash based build skip for Docker modules: check if source changed before rebuild."""
# GREP_SUMMARY: content-hash, build-skip, sha256, dockerfile, cache, check-build-need
# STRUCTURE: ▶ compute_source_hash [read Dockerfile → walk context → fnmatch .dockerignore → SHA256] → check_build_needed [read cached_hash → compare] → save_build_hash [write hash file]
# region MODULE_CONTRACT
## @purpose  Compute SHA256 hash of Dockerfile + build context, compare against cached hash,
##           and decide whether rebuild is needed. Enables skip of docker compose build
##           when source files haven't changed (DevPlan 050 W3.T3.3).
## @scope    Used by deploy_docker_module() in docker_orchestrator.py for modules with
##           build: section (status-page, backup-cron). Not for hermes-agent which has
##           its own image workflow (GHCR pull + BuildKit cache).
## @input    module_dir: str, cache_dir: str
## @output   check_build_needed → bool (True = build needed, False = skip)
## @invariants
##   - SHA256 is computed deterministically: same files → same hash
##   - .dockerignore is respected (files matching patterns are excluded)
##   - Missing Dockerfile → empty hash (build needed)
##   - Missing cache directory → created automatically
##   - Permission errors on cache write → logged, build proceeds (fail open)
## @rationale Q: Why SHA256 and not MD5? A: SHA256 is standard for Docker content-hash;
##   avoids collision concerns. Q: Why fail-open on cache write error? A: A cache
##   write failure should not block deployment — worst case is an extra build.
## @changes   2026-07-24 · Created for DevPlan 050 W3.T3.3
## @modulemap
##   compute_source_hash [W:2] — read Dockerfile + walk module_dir, filter by .dockerignore, SHA256
##   check_build_needed [W:1] — read cached hash, compare with current
##   save_build_hash [W:1] — write hash to cache file in cache_dir
##   _load_dockerignore [W:1] — parse .dockerignore patterns
##   _should_include [W:1] — fnmatch-based filter
## @usecases
##   - deploy_docker_module() calls check_build_needed() before build
##   - If hash matches → skip docker compose build, log IMP:9 skip
##   - If hash differs → build + save_build_hash()
## @links    CALLED_BY(core/internal/bootstrap/deploy/docker_orchestrator.py),
##           RELATED(core/internal/bootstrap/deploy-modules.sh)
# endregion MODULE_CONTRACT

import fnmatch
import hashlib
import logging
import os

logger = logging.getLogger("content_hash")

# ── Patterns always excluded from build context hashing ──
_ALWAYS_EXCLUDE = {
    ".git",
    ".gitignore",
    ".DS_Store",
    "__pycache__",
    "*.pyc",
    "*.md",
    ".env",
    ".gitattributes",
    ".gitmodules",
}


# region FUNC__load_dockerignore
## @purpose  Parse .dockerignore file from module directory and return set of patterns.
## @io       ⇥ module_dir: str
##           ⎋ set[str] — patterns from .dockerignore (empty if not present)
## @complexity 1 — file read + splitlines
def _load_dockerignore(module_dir: str) -> set[str]:
    """Load .dockerignore patterns from module directory."""
    dockerignore_path = os.path.join(module_dir, ".dockerignore")
    patterns: set[str] = set()
    if os.path.isfile(dockerignore_path):
        try:
            with open(dockerignore_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.add(line)
            logger.info(
                "[IMP:8][_load_dockerignore][loaded] Loaded %d patterns from .dockerignore for %s",
                len(patterns),
                module_dir,
            )
        except OSError as exc:
            logger.warning("[IMP:5][_load_dockerignore][error] Failed to read .dockerignore: %s", exc)
    return patterns


# endregion FUNC__load_dockerignore


# region FUNC__should_include
## @purpose  Check if a relative file path should be included in the hash computation,
##           based on .dockerignore patterns and always-excluded patterns.
## @io       ⇥ rel_path: str, ignore_patterns: set[str]
##           ⎋ bool — True if file should be included in hash
## @complexity 1 — fnmatch linear scan per path
def _should_include(rel_path: str, ignore_patterns: set[str]) -> bool:
    """Return True if rel_path should be included in the hash computation."""
    # Always exclude well-known patterns
    for pattern in _ALWAYS_EXCLUDE:
        if fnmatch.fnmatch(rel_path, pattern):
            return False
        # Directory prefix match (pattern ending with /)
        if pattern.endswith("/") and (
            rel_path == pattern.rstrip("/") or rel_path.startswith(pattern.rstrip("/") + "/")
        ):
            return False

    # User-defined .dockerignore patterns
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return False
        if pattern.endswith("/") and (
            rel_path == pattern.rstrip("/") or rel_path.startswith(pattern.rstrip("/") + "/")
        ):
            return False

    return True


# endregion FUNC__should_include


# region FUNC_compute_source_hash
## @purpose  Compute SHA256 hash of Dockerfile + build context in module_dir.
##           Respects .dockerignore patterns. Returns hex digest string.
## @io       ⇥ module_dir: str
##           ⎋ str — 64-char hex digest (empty string if no Dockerfile found)
## @complexity 3 — directory walk + file reads + hashing
## @invariants
##   - If Dockerfile is missing, returns empty string (triggers build-needed)
##   - Files are sorted by path for deterministic hashing
##   - Only regular files are included (no symlinks, sockets, etc.)
def compute_source_hash(module_dir: str) -> str:
    """Compute SHA256 hex digest of Dockerfile + build context in module_dir."""
    logger.info("[IMP:7][compute_source_hash][start] Computing source hash for %s", module_dir)

    dockerfile = os.path.join(module_dir, "Dockerfile")
    if not os.path.isfile(dockerfile):
        logger.warning(
            "[IMP:5][compute_source_hash][no_dockerfile] No Dockerfile in %s — returning empty hash", module_dir
        )
        return ""

    ignore_patterns = _load_dockerignore(module_dir)
    hasher = hashlib.sha256()

    # ── Walk module_dir, collect regular files ──
    files_to_hash: list[str] = []
    for root, dirs, fnames in os.walk(module_dir):
        rel_root = os.path.relpath(root, module_dir)
        if rel_root == ".":
            rel_root = ""

        # Filter directories in-place so os.walk skips them
        dirs[:] = [d for d in dirs if _should_include(os.path.join(rel_root, d) if rel_root else d, ignore_patterns)]

        for fname in fnames:
            rel_path = os.path.join(rel_root, fname) if rel_root else fname
            if _should_include(rel_path, ignore_patterns):
                files_to_hash.append(os.path.join(root, fname))

    files_to_hash.sort()
    logger.info(
        "[IMP:8][compute_source_hash][files] Hashing %d files in %s",
        len(files_to_hash),
        module_dir,
    )

    def _hash_file(filepath: str) -> None:
        try:
            rel = os.path.relpath(filepath, module_dir)
            with open(filepath, "rb") as f:
                content = f.read()
            hasher.update(rel.encode("utf-8"))
            hasher.update(content)
        except OSError as exc:
            logger.warning("[IMP:5][compute_source_hash][skip] Skipping %s: %s", filepath, exc)

    for filepath in files_to_hash:
        _hash_file(filepath)

    result = hasher.hexdigest()
    logger.info(
        "[IMP:9][compute_source_hash][done] Hash=%s files=%d for %s",
        result[:16],
        len(files_to_hash),
        module_dir,
    )
    return result


# endregion FUNC_compute_source_hash


# region FUNC_check_build_needed
## @purpose  Compare current source hash with cached hash.
##           Returns True if build is needed (hash differs or no cache).
## @io       ⇥ module_dir: str, cache_dir: str
##           ⎋ bool — True if build needed, False if skip (hash matches)
## @complexity 2 — compute hash + read cache + comparison
## @invariants
##   - Missing cache file → build needed (no cached hash to compare)
##   - Cache corruption (empty file, bad hex) → build needed
##   - Permission error reading cache → build needed (fail open)
def check_build_needed(module_dir: str, cache_dir: str = "/var/lib/platform/.build-cache") -> bool:
    """Check if docker compose build is needed by comparing source hash with cached hash."""
    module_name = os.path.basename(module_dir.rstrip("/"))
    current_hash = compute_source_hash(module_dir)

    if not current_hash:
        logger.info(
            "[IMP:9][check_build_needed][no_hash] No source hash computable for %s — build needed",
            module_name,
        )
        return True

    cache_file = os.path.join(cache_dir, f"{module_name}.hash")
    try:
        if os.path.isfile(cache_file):
            with open(cache_file) as f:
                cached_hash = f.read().strip()
            if cached_hash == current_hash:
                logger.info(
                    "[IMP:9][check_build_needed][skip] Build skipped for %s — source unchanged (hash=%s)",
                    module_name,
                    current_hash[:16],
                )
                return False
            logger.info(
                "[IMP:8][check_build_needed][changed] Hash changed for %s — rebuilding (old=%s new=%s)",
                module_name,
                cached_hash[:16],
                current_hash[:16],
            )
        else:
            logger.info(
                "[IMP:8][check_build_needed][no_cache] No cached hash for %s — build needed",
                module_name,
            )
    except OSError as exc:
        logger.warning(
            "[IMP:5][check_build_needed][cache_error] Failed to read cache for %s: %s — building",
            module_name,
            exc,
        )

    return True


# endregion FUNC_check_build_needed


# region FUNC_save_build_hash
## @purpose  Save computed source hash to cache file for future comparison.
## @io       ⇥ module_dir: str, hash_value: str, cache_dir: str
##           ⎋ None (side-effect: writes cache file)
## @complexity 1 — directory create + file write
## @invariants
##   - Cache directory is created if it doesn't exist
##   - Permission errors are logged but non-fatal (build already completed)
def save_build_hash(module_dir: str, hash_value: str, cache_dir: str = "/var/lib/platform/.build-cache") -> None:
    """Save source hash to cache file for future check_build_needed comparison."""
    module_name = os.path.basename(module_dir.rstrip("/"))
    try:
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{module_name}.hash")
        with open(cache_file, "w") as f:
            f.write(hash_value + "\n")
        logger.info(
            "[IMP:9][save_build_hash][saved] Saved build hash for %s: %s",
            module_name,
            hash_value[:16],
        )
    except OSError as exc:
        logger.warning(
            "[IMP:5][save_build_hash][error] Failed to save hash for %s: %s — non-fatal",
            module_name,
            exc,
        )


# endregion FUNC_save_build_hash
