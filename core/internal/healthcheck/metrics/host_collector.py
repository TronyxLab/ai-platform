#!/usr/bin/env python3
# GREP_SUMMARY: host-collector disk-usage shutil total-gb free-gb used-percent uptime load-average proc
# STRUCTURE: ▶ get_host_disk(path='/opt') → shutil.disk_usage → ⊕ {disk_total_gb, disk_free_gb, disk_used_percent} → ⎋ dict
# region MODULE_CONTRACT
## @purpose  Host disk usage collector via shutil.disk_usage (stdlib, one syscall)
## @scope    Host-side: always fresh (no cache — single syscall)
## @invariants
##   - Uses shutil.disk_usage (stdlib, Python stdlib) — no subprocess
##   - Always fresh — no TTL cache (single syscall, negligible cost)
##   - Returns GB values rounded to 1 decimal place
## @rationale Simplest collector — single stdlib call, no cache needed.
# endregion MODULE_CONTRACT

import logging
import shutil

logger = logging.getLogger(__name__)


# region FUNC_get_host_disk
## @purpose  Collect host disk usage stats via shutil.disk_usage
## @io       ⇥ path: str — mount path to check (default: /opt)
##           ⎋ dict — {disk_total_gb, disk_free_gb, disk_used_percent}
## @complexity  O(1) — single os.statvfs call behind shutil
def get_host_disk(path: str = "/opt") -> dict:
    """Get host disk usage for a mount point.

    # ▶ shutil.disk_usage(path) → ∑ total_gb, free_gb, used_pct → ⎋ {disk_total_gb, disk_free_gb, disk_used_percent}

    Returns dict with rounded GB values. Graceful: all zeros on failure.
    """
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:8][host_collector][get_host_disk] Checking disk usage for %s", path)

    try:
        usage = shutil.disk_usage(path)
        total_gb = round(usage.total / (1024**3), 1)
        free_gb = round(usage.free / (1024**3), 1)
        used_percent = round((1 - usage.free / usage.total) * 100, 1) if usage.total > 0 else 0.0

        result = {
            "disk_total_gb": total_gb,
            "disk_free_gb": free_gb,
            "disk_used_percent": used_percent,
        }

        _logger.info(
            "[IMP:9][host_collector][get_host_disk] Disk: total=%s GB, free=%s GB, used=%s%%",
            total_gb,
            free_gb,
            used_percent,
        )
        return result
    except OSError as exc:
        _logger.warning("[IMP:8][host_collector][get_host_disk] Failed to get disk usage for %s: %s", path, exc)
        return {"disk_total_gb": 0, "disk_free_gb": 0, "disk_used_percent": 0.0}


# endregion FUNC_get_host_disk


# region FUNC_get_host_uptime
## @purpose  Collect host system uptime (from /proc/uptime) and load average (from /proc/loadavg)
## @io       ⇥ (none, reads /proc filesystem) → ⎋ dict — {uptime_seconds, load_1m, load_5m, load_15m}
## @complexity  O(1) — two file reads, no subprocess
def get_host_uptime() -> dict:
    """Get host uptime (from /proc/uptime) and load average (from /proc/loadavg).

    # ▶ /proc/uptime → uptime_seconds → /proc/loadavg → load_1m/5m/15m → ⊕ dict → ⎋ result

    Uses only stdlib (/proc filesystem reads). Graceful degradation:
    returns all-null dict on missing files or parse errors.
    """
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:8][host_collector][get_host_uptime] Reading host uptime & load")

    result: dict = {
        "uptime_seconds": None,
        "load_1m": None,
        "load_5m": None,
        "load_15m": None,
    }

    # ── /proc/uptime: single line with two floats (uptime_seconds, idle_seconds) ──
    try:
        with open("/proc/uptime") as f:
            parts = f.read().strip().split()
            if parts:
                uptime_val = float(parts[0])
                result["uptime_seconds"] = uptime_val
                _logger.info("[IMP:9][host_collector][get_host_uptime] Uptime: %.2f seconds", uptime_val)
    except (FileNotFoundError, PermissionError, ValueError, OSError) as exc:
        _logger.warning("[IMP:8][host_collector][get_host_uptime] /proc/uptime unreadable: %s", exc)

    # ── /proc/loadavg: "0.50 0.30 0.10 1/234 56789" ──
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().strip().split()
            if len(parts) >= 3:
                result["load_1m"] = float(parts[0])
                result["load_5m"] = float(parts[1])
                result["load_15m"] = float(parts[2])
                _logger.info(
                    "[IMP:9][host_collector][get_host_uptime] Load average: %.2f / %.2f / %.2f",
                    result["load_1m"],
                    result["load_5m"],
                    result["load_15m"],
                )
    except (FileNotFoundError, PermissionError, ValueError, OSError) as exc:
        _logger.warning("[IMP:8][host_collector][get_host_uptime] /proc/loadavg unreadable: %s", exc)

    return result


# endregion FUNC_get_host_uptime
