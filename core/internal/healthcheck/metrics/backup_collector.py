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
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypedDict

logger = logging.getLogger(__name__)

# Default backup log paths (overridable via environment)
_DEFAULT_POSTGRES_LOG = "/var/log/platform/backup/postgres.log"
_DEFAULT_APP_DATA_LOG = "/var/log/platform/backup/app-data.log"
_STALE_THRESHOLD_SECONDS = 25 * 3600  # 25 hours

# Тип stat-функции (mtime): получает путь → float epoch (DI, 167 D6).
StatFn = Callable[[str], float]


# region DATA_BackupStatus
class BackupStatus(TypedDict):
    """Свежесть бэкапов (граница status-metrics.json).

    ## @purpose  last_postgres_at/last_app_data_at — ISO-время последнего лога (или None),
    ##            status — ok/stale/unknown по порогу 25h.
    """

    last_postgres_at: str | None
    last_app_data_at: str | None
    status: str


# endregion DATA_BackupStatus


# region FUNC_get_backup_status
## @purpose  Check backup freshness by reading log file mtime
## @io       ⇥ stat_fn: StatFn | None (DI, 167 D6 — os.path.getmtime-семантика; None = канон) → ⎋ dict
## @complexity  O(1) — up to 2 stat_fn calls
def get_backup_status(stat_fn: StatFn | None = None) -> BackupStatus:
    """Check backup freshness by reading backup log file mtime.

    # ▶ ┌postgres.log path┐ → stat_fn → ◇ age < 25h? → ⊕ status "ok"/"stale"/"unknown"
    #    → ┌app-data.log path┐ → same check → ⊕ result dict → ⎋ {last_postgres_at, last_app_data_at, status}

    Returns dict with:
      - last_postgres_at: ISO 8601 timestamp or None
      - last_app_data_at: ISO 8601 timestamp or None
      - status: "ok" (all present and fresh), "stale" (any present but >25h), "unknown" (none present)
    """
    logger_ = logging.getLogger(__name__)
    logger_.info("[IMP:8][backup_collector][get_backup_status] Checking backup freshness")

    postgres_log = os.environ.get("BACKUP_POSTGRES_LOG", _DEFAULT_POSTGRES_LOG)
    app_data_log = os.environ.get("BACKUP_APP_DATA_LOG", _DEFAULT_APP_DATA_LOG)

    now = time.time()

    def _read_mtime_ts(log_path: str) -> str | None:
        """mtime → ISO-строка через stat_fn (DI, 167 D6); None если файла нет."""
        getmtime = os.path.getmtime if stat_fn is None else stat_fn
        mtime = getmtime(log_path)
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        logger_.info(
            "[IMP:9][backup_collector][get_backup_status] Log %s mtime: %s (age: %.1fh)",
            log_path,
            ts,
            (now - mtime) / 3600,
        )
        return ts

    def _check_log(log_path: str) -> str | None:
        """Check a single log file: returns ISO timestamp string or None."""
        # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · mtime через stat_fn (167 D6)
        # · Rejected: прямой os.path.getmtime с monkeypatch-патчем в тестах (read-error)
        # · Reason: seam = тестируемость реального вызова — stat_fn инжектируется тестом
        # ·   (OSError-сбой чтения), default = os.path.getmtime (поведение неизменно)
        # · Rev: при переходе на pathlib.Path.stat() — заменить stat_fn на stat-объект
        try:
            if os.path.isfile(log_path):
                return _read_mtime_ts(log_path)
            logger_.info("[IMP:8][backup_collector][get_backup_status] Log not found: %s", log_path)
        except (OSError, PermissionError) as exc:
            logger_.warning("[IMP:8][backup_collector][get_backup_status] Error reading %s: %s", log_path, exc)
            return None
        else:
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

    logger_.info(
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
