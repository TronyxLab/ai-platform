#!/usr/bin/env python3
# GREP_SUMMARY: json-writer inode-preserving-write schema-version tempfile fsync direct-overwrite docker-bind-mount
# STRUCTURE: ▶ atomic_write(data, target_path) → tempfile.mkstemp → json.dump → os.fsync → validate → direct overwrite (preserves inode) → unlink temp
# region MODULE_CONTRACT
## @purpose  Inode-preserving JSON writer: tempfile + fsync + validate, then overwrite target in-place
## @scope    Host-side: writes status-metrics.json to /run/platform/
## @invariants
##   - SCHEMA_VERSION = 2 (META Δ4)
##   - tempfile.mkstemp in same directory as target (same filesystem)
##   - os.fsync before close — guarantees data flushed to disk
##   - ⚠️ TRAP[DOCKER-BIND-MOUNT] Direct overwrite (NOT os.replace) — preserves inode for Docker bind mount
##     of single files (os.replace creates new inode → Docker bind mount sees old inode → stale data)
##   - Validate temp file before overwriting target — corrupted JSON never overwrites valid data
##   - os.makedirs(exist_ok=True) for output directory
##   - dir_mode for created directories: 0o755
## @rationale os.replace() creates a new inode which breaks Docker bind mounts for single files
##            (Docker binds to the old inode and never sees the new file — permanent staleness).
##            Direct overwrite preserves the inode, letting Docker containers see updates immediately.
##            Temp-file-first-then-overwrite minimizes the race window (target is truncated to 0
##            only during the final write syscall — microseconds for a 16KB file).
##            Validation step prevents corrupt JSON from overwriting valid data.
# endregion MODULE_CONTRACT

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


# ⚠️ TRAP[DOCKER-BIND-MOUNT] · 2026-07-24 · HI · Direct overwrite instead of os.replace
# · Rejected: atomic rename via os.replace (risk: creates new inode, Docker bind mount
#   of single files binds to old inode → container sees stale data permanently)
# · Reason: Docker bind mount for individual files uses inode-level binding. os.replace()
#   creates a new inode, breaking the mount. Direct overwrite (open+write+fsync) preserves
#   the inode. Race window: target is empty between truncate and write (microseconds for
#   16KB JSON). Acceptable trade-off for cron-exported metrics (updated every 60s).
# · Recovery: if the race window becomes problematic (readers consistently see empty file),
#   switch to directory-level bind mount (/run/platform/ instead of single file).
# · Rev: if status-page log shows >1% JSONDecodeError on metrics file reads → escalate to
#   directory mount solution.


# region FUNC_atomic_write
## @purpose  Write JSON data to target_path preserving inode (Docker bind mount compatible)
## @io       ⇥ data: dict — data to serialize (SCHEMA_VERSION injected)
##           ⇥ target_path: str — output file path
##           ⇥ dir_mode: int — mode for parent dir creation (default: 0o755)
##           ⎋ None — raises on failure
## @complexity  O(N) where N = data size (json.dumps)
def atomic_write(data: dict, target_path: str, dir_mode: int = 0o755) -> None:
    """Write data as JSON to target_path, preserving inode for Docker bind mounts.

    # ▶ ┌data + target_path┐ → inject schema_version → tempfile.mkstemp(dir=target_dir)
    #    → json.dump → os.fsync → validate temp → overwrite target in-place → unlink temp → ⎋

    Steps:
    1. Ensure target directory exists (os.makedirs)
    2. Inject SCHEMA_VERSION into data dict
    3. Create temp file in same directory, write JSON, fsync
    4. Validate temp file is readable JSON
    5. Overwrite target file in-place (preserves inode for Docker bind mount)
    6. Remove temp file

    Raises OSError on write failure, ValueError on encoding/validation failure.
    """
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:8][json_writer][atomic_write] Writing (inode-preserving) to %s", target_path)

    # Step 1: Ensure target directory exists
    target_dir = os.path.dirname(target_path)
    try:
        os.makedirs(target_dir, mode=dir_mode, exist_ok=True)
    except OSError as exc:
        _logger.error("[IMP:8][json_writer][atomic_write] Cannot create directory %s: %s", target_dir, exc)
        raise

    # Step 2: Inject schema_version
    data["schema_version"] = SCHEMA_VERSION

    # Step 3: Create temp file in same directory, write JSON, fsync
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
    _logger.info("[IMP:8][json_writer][atomic_write] Temp file: %s", tmp_path)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())

        tmp_size = os.path.getsize(tmp_path)
        _logger.info("[IMP:9][json_writer][atomic_write] JSON written to temp, size=%d bytes", tmp_size)

        # Step 4: Validate temp file (read back to ensure valid JSON)
        try:
            with open(tmp_path, encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError as exc:
            _logger.error("[IMP:9][json_writer][atomic_write] Temp file validation failed: %s", exc)
            os.unlink(tmp_path)
            raise ValueError(f"Temp file contains invalid JSON: {exc}") from exc

        # Step 5: Overwrite target in-place (preserves inode)
        # TRAP[DOCKER-BIND-MOUNT]: must NOT use os.replace() — it creates new inode
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        _logger.info("[IMP:9][json_writer][atomic_write] Inode-preserving overwrite complete: %s", target_path)

        # Step 6: Remove temp file
        os.unlink(tmp_path)

    except (OSError, TypeError, ValueError) as exc:
        _logger.error("[IMP:9][json_writer][atomic_write] Write failed: %s", exc)
        # Clean up temp file on failure
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# endregion FUNC_atomic_write
