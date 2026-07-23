#!/usr/bin/env python3
# GREP_SUMMARY: backup-collector last-run mtime postgres-log staleness status
# STRUCTURE: ▶ get_backup_status() → ┌log_paths┐ → ○ mtime check → ◇ age < 25h? → ⊕ status "ok"/"stale"/"unknown" → ⎋ dict
# region MODULE_CONTRACT
## @purpose  Backup status collector — checks mtime of backup log files to determine freshness
## @scope    Host-side cron export: reads log files from /var/log/platform/backup/
## @invariants
##   - Two backup types monitored: postgres (DB dumps), app-data (application data)
##   - Threshold: <25h = "ok", ≥25h = "stale", log missing = "unknown"
##   - All log paths configurable via env vars with sensible defaults
##   - Graceful degradation: returns all-null/unknown on file errors
## @rationale Backup-cron module writes logs but no collector reads them → silent failure risk.
##            Separate module (not in host_collector) maintains single responsibility.
# endregion MODULE_CONTRACT

import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Default backup log paths (overridable via environment)
_DEFAULT_POSTGRES_LOG = "/var/log/platform/backup/postgres.log"
_DEFAULT_APP_DATA_LOG = "/var/log/platform/backup/app-data.log"
_STALE_THRESHOLD_SECONDS = 25 * 3600  # 25 hours


# region FUNC_get_backup_status
## @purpose  Check backup freshness by reading log file mtime
## @io       ⇥ (none, uses env vars for paths) → ⎋ dict with last_postgres_at, last_app_data_at, status
## @complexity  O(1) — up to 2 os.path.getmtime calls
def get_backup_status() -> dict:
    """Check backup freshness by reading backup log file mtime.

    # ▶ ┌postgres.log path┐ → os.path.getmtime → ◇ age < 25h? → ⊕ status "ok"/"stale"/"unknown"
    #    → ┌app-data.log path┐ → same check → ⊕ result dict → ⎋ {last_postgres_at, last_app_data_at, status}

    Returns dict with:
      - last_postgres_at: ISO 8601 timestamp or None
      - last_app_data_at: ISO 8601 timestamp or None
      - status: "ok" (all present and fresh), "stale" (any present but >25h), "unknown" (none present)
    """
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:8][backup_collector][get_backup_status] Checking backup freshness")

    postgres_log = os.environ.get("BACKUP_POSTGRES_LOG", _DEFAULT_POSTGRES_LOG)
    app_data_log = os.environ.get("BACKUP_APP_DATA_LOG", _DEFAULT_APP_DATA_LOG)

    now = time.time()

    def _check_log(log_path: str) -> str | None:
        """Check a single log file: returns ISO timestamp string or None."""
        try:
            if os.path.isfile(log_path):
                mtime = os.path.getmtime(log_path)
                ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                _logger.info("[IMP:9][backup_collector][get_backup_status] Log %s mtime: %s (age: %.1fh)", log_path, ts, (now - mtime) / 3600)
                return ts
            else:
                _logger.info("[IMP:8][backup_collector][get_backup_status] Log not found: %s", log_path)
                return None
        except (OSError, PermissionError) as exc:
            _logger.warning("[IMP:8][backup_collector][get_backup_status] Error reading %s: %s", log_path, exc)
            return None

    last_postgres_at = _check_log(postgres_log)
    last_app_data_at = _check_log(app_data_log)

    # Determine overall status
    all_timestamps: list[str | None] = [last_postgres_at, last_app_data_at]
    present_timestamps = [ts for ts in all_timestamps if ts is not None]

    if not present_timestamps:
        status = "unknown"
    else:
        # Check if any present log is stale
        is_stale = False
        for ts in present_timestamps:
            try:
                ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                age = now - ts_dt.timestamp()
                if age > _STALE_THRESHOLD_SECONDS:
                    is_stale = True
                    break
            except (ValueError, TypeError):
                is_stale = True
                break

        status = "stale" if is_stale else "ok"

    _logger.info(
        "[IMP:9][backup_collector][get_backup_status] Backup status: %s (postgres=%s, app-data=%s)",
        status,
        last_postgres_at or "N/A",
        last_app_data_at or "N/A",
    )

    return {
        "last_postgres_at": last_postgres_at,
        "last_app_data_at": last_app_data_at,
        "status": status,
    }


# endregion FUNC_get_backup_status


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = get_backup_status()
    print(f"Backup status: {result}")
