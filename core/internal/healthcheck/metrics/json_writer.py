#!/usr/bin/env python3
# GREP_SUMMARY: json-writer atomic-write schema-version tempfile fsync os.replace
# STRUCTURE: ▶ atomic_write(data, target_path) → tempfile.mkstemp → json.dump → os.fsync → os.replace → atomic
# region MODULE_CONTRACT
## @purpose  Atomic JSON writer: tempfile + fsync + os.replace — reader never sees partial file
## @scope    Host-side: writes status-metrics.json atomically to /run/platform/
## @invariants
##   - SCHEMA_VERSION = 2 (META Δ4)
##   - tempfile.mkstemp in same directory as target (same filesystem → atomic rename)
##   - os.fsync before close — guarantees data flushed to disk
##   - os.replace — atomic rename (POSIX), readers see complete file or old version
##   - os.makedirs(exist_ok=True) for output directory
##   - dir_mode for created directories: 0o755
## @rationale Atomic write prevents race condition between cron export and status-page read (META Δ2).
##            Without this, status-page could read a half-written JSON file.
# endregion MODULE_CONTRACT

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


# region FUNC_atomic_write
## @purpose  Write JSON data atomically to target_path
## @io       ⇥ data: dict — data to serialize (SCHEMA_VERSION injected)
##           ⇥ target_path: str — output file path
##           ⇥ dir_mode: int — mode for parent dir creation (default: 0o755)
##           ⎋ None — raises on failure
## @complexity  O(N) where N = data size (json.dumps)
def atomic_write(data: dict, target_path: str, dir_mode: int = 0o755) -> None:
    """Write data as JSON to target_path atomically.

    # ▶ ┌data + target_path┐ → inject schema_version → tempfile.mkstemp(dir=target_dir)
    #    → json.dump → os.fsync → os.close → os.replace(tmp, target) → ⎋

    Steps:
    1. Ensure target directory exists (os.makedirs)
    2. Inject SCHEMA_VERSION into data dict
    3. Create temp file in same directory (guarantees same filesystem)
    4. json.dump to temp file
    5. os.fsync to flush to disk
    6. os.replace for atomic rename

    Raises OSError on write failure, RuntimeError on encoding failure.
    """
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:8][json_writer][atomic_write] Writing atomically to %s", target_path)

    # Step 1: Ensure target directory exists
    target_dir = os.path.dirname(target_path)
    try:
        os.makedirs(target_dir, mode=dir_mode, exist_ok=True)
    except OSError as exc:
        _logger.error("[IMP:8][json_writer][atomic_write] Cannot create directory %s: %s", target_dir, exc)
        raise

    # Step 2: Inject schema_version
    data["schema_version"] = SCHEMA_VERSION

    # Step 3: Create temp file in same directory
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
    _logger.info("[IMP:8][json_writer][atomic_write] Temp file: %s", tmp_path)

    try:
        # Step 4: Serialize to temp file
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2, ensure_ascii=False)
            # Step 5: fsync before close
            tmp.flush()
            os.fsync(tmp.fileno())

        _logger.info(
            "[IMP:9][json_writer][atomic_write] JSON written to temp, size=%d bytes", os.path.getsize(tmp_path)
        )

        # Step 6: Atomic rename
        os.replace(tmp_path, target_path)
        _logger.info("[IMP:9][json_writer][atomic_write] Atomic replace complete: %s", target_path)

    except (OSError, TypeError, ValueError) as exc:
        _logger.error("[IMP:9][json_writer][atomic_write] Write failed: %s", exc)
        # Clean up temp file on failure
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# endregion FUNC_atomic_write
