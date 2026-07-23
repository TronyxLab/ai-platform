#!/usr/bin/env python3
# GREP_SUMMARY: host-collector disk-usage shutil total-gb free-gb used-percent
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
