#!/usr/bin/env python3
# GREP_SUMMARY: backup-collector last-verified-stamp freshness postgres app-data-log stale status REF-0009
# STRUCTURE: ▶ get_backup_status() → ┌.last_verified stamp + app-data log┐ → ○ mtime check → ◇ age < 25h? → ⊕ status "ok"/"stale"/"unknown" → ⎋ dict
# region MODULE_CONTRACT
## @purpose  Backup status collector — postgres freshness from the verified-dump
##           stamp (.last_verified), app-data freshness from job log mtime
## @scope    Host-side cron export: reads spool stamp + log files
## @invariants
##   - Postgres freshness: mtime of {spool}/postgres/.last_verified — written by
##     backup_postgres.py ONLY after gzip -t OK + structure validation (REF-0009,
##     BUG-0803 ≡ FAIL-0903: прежний сигнал по mtime ЛОГА означал «cron запустился»,
##     не «бэкап удался» — упавшая ночью задача выглядела свежей)
##   - App-data freshness: mtime of the app-data job log (stub job has no stamp)
##   - Threshold: <25h = "ok", ≥25h = "stale", source missing = "unknown"
##   - All paths configurable via env vars with sensible defaults (stamp path is a
##     host bind-mount /var/lib/platform/backup-spool — readable host-side)
##   - Graceful degradation: returns all-null/unknown on file errors
## @rationale Backup-cron module writes logs but no collector reads them → silent failure risk.
##            Separate module (not in host_collector) maintains single responsibility.
##            BackupStatus wire-shape unchanged (status-metrics.json consumers).
# endregion MODULE_CONTRACT

import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypedDict

# Канонический резолвер spool-пути (SoT deploy_paths — gate run_paths_sole)
from core.internal.shared.deploy_paths import backup_spool_postgres_dir

logger = logging.getLogger(__name__)

# Default source paths (overridable via environment).
# Postgres: VERIFIED-DUMP STAMP in the spool bind-mount (REF-0009) — NOT the cron-
# refreshed log. App-data: job log (phase-02 stub writes only run markers).
_DEFAULT_POSTGRES_STAMP = str(backup_spool_postgres_dir() / ".last_verified")
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
    """Check backup freshness: postgres via .last_verified stamp, app-data via log mtime.

    # ▶ ┌.last_verified stamp path┐ → stat_fn → ◇ age < 25h? → ⊕ status "ok"/"stale"/"unknown"
    #    → ┌app-data.log path┐ → same check → ⊕ result dict → ⎋ {last_postgres_at, last_app_data_at, status}

    Returns dict with:
      - last_postgres_at: ISO 8601 timestamp of the last VERIFIED dump (stamp mtime) or None
      - last_app_data_at: ISO 8601 timestamp or None
      - status: "ok" (all present and fresh), "stale" (any present but >25h), "unknown" (none present)
    """
    logger_ = logging.getLogger(__name__)
    logger_.info("[IMP:8][backup_collector][get_backup_status] Checking backup freshness")

    postgres_stamp = os.environ.get("BACKUP_POSTGRES_STAMP", _DEFAULT_POSTGRES_STAMP)
    app_data_log = os.environ.get("BACKUP_APP_DATA_LOG", _DEFAULT_APP_DATA_LOG)

    now = time.time()

    def _read_mtime_ts(source_path: str) -> str | None:
        """mtime → ISO-строка через stat_fn (DI, 167 D6); None если файла нет."""
        getmtime = os.path.getmtime if stat_fn is None else stat_fn
        mtime = getmtime(source_path)
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        logger_.info(
            "[IMP:9][backup_collector][get_backup_status] Source %s mtime: %s (age: %.1fh)",
            source_path,
            ts,
            (now - mtime) / 3600,
        )
        return ts

    def _check_log(source_path: str) -> str | None:
        """Check a single freshness source (stamp/log): returns ISO timestamp string or None."""
        # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · mtime через stat_fn (167 D6)
        # · Rejected: прямой os.path.getmtime с monkeypatch-патчем в тестах (read-error)
        # · Reason: seam = тестируемость реального вызова — stat_fn инжектируется тестом
        # ·   (OSError-сбой чтения), default = os.path.getmtime (поведение неизменно)
        # · Rev: при переходе на pathlib.Path.stat() — заменить stat_fn на stat-объект
        try:
            if os.path.isfile(source_path):
                return _read_mtime_ts(source_path)
            logger_.info("[IMP:8][backup_collector][get_backup_status] Source not found: %s", source_path)
        except (OSError, PermissionError) as exc:
            logger_.warning("[IMP:8][backup_collector][get_backup_status] Error reading %s: %s", source_path, exc)
            return None
        else:
            return None

    last_postgres_at = _check_log(postgres_stamp)
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
