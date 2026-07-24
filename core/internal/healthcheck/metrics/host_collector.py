#!/usr/bin/env python3
# GREP_SUMMARY: host-collector disk-usage shutil total-gb free-gb used-percent uptime load-average proc memory swap uname os kernel
# STRUCTURE: ▶ get_host_disk: shutil.disk_usage → ⊕ disk → ⎋ dict | ▶ get_host_memory: /proc/meminfo → ⊕ memory + swap → ⎋ dict | ▶ get_host_uptime: /proc/uptime + /proc/loadavg → ⎋ dict | ▶ get_host_uname: os.uname() → ⎋ {os_name, kernel_version, arch}
# region MODULE_CONTRACT
## @purpose  Host metrics collector — disk, memory/swap, uptime/load, OS/kernel via stdlib
## @scope    Host-side: always fresh (no cache — single syscall each)
## @invariants
##   - Uses shutil.disk_usage, os.uname (stdlib) — no subprocess
##   - /proc/meminfo parser for memory/swap (Linux only, graceful zeros on macOS)
##   - Always fresh — no TTL cache (single syscall, negligible cost)
##   - Returns GB values rounded to 1 decimal place
## @rationale Simplest collector — single stdlib call, no cache needed.
## @changes
##   2026-07-24 | 047 W1 | Added get_host_memory(), get_host_uname()
# endregion MODULE_CONTRACT

import logging
import os
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


# region FUNC_get_host_memory
## @purpose  Collect host RAM + Swap from /proc/meminfo
## @io       ⇥ (none, reads /proc/meminfo) → ⎋ dict — {memory_total_gb, memory_available_gb, memory_used_percent, swap_total_gb, swap_free_gb, swap_used_percent}
## @complexity  O(1) — single file read
def get_host_memory() -> dict:
    """Get host memory and swap usage from /proc/meminfo.

    # ▶ /proc/meminfo → parse MemTotal, MemAvailable, SwapTotal, SwapFree
    #    → ⊕ memory_total_gb, memory_available_gb, memory_used_percent
    #    → ⊕ swap_total_gb, swap_free_gb, swap_used_percent → ⎋ dict

    Graceful degradation: zeros on FileNotFoundError or parse error.
    Uses kB values from meminfo (kernel reports in kB).
    """
    _logger = logging.getLogger(__name__)
    result = {
        "memory_total_gb": 0.0,
        "memory_available_gb": 0.0,
        "memory_used_percent": 0.0,
        "swap_total_gb": 0.0,
        "swap_free_gb": 0.0,
        "swap_used_percent": 0.0,
    }

    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val_str = parts[1].strip().split()[0]  # "16234500 kB" → "16234500"
                    try:
                        meminfo[key] = int(val_str)
                    except ValueError:
                        pass

        mem_total = meminfo.get("MemTotal", 0)
        mem_available = meminfo.get("MemAvailable", 0)
        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", 0)

        if mem_total > 0:
            result["memory_total_gb"] = round(mem_total / (1024**2), 1)  # kB → GB
            result["memory_available_gb"] = round(mem_available / (1024**2), 1)
            result["memory_used_percent"] = round((1 - mem_available / mem_total) * 100, 1)

        if swap_total > 0:
            result["swap_total_gb"] = round(swap_total / (1024**2), 1)
            result["swap_free_gb"] = round(swap_free / (1024**2), 1)
            result["swap_used_percent"] = round((1 - swap_free / swap_total) * 100, 1)

        _logger.info(
            "[IMP:9][host_collector][get_host_memory] RAM: %.1f/%.1f GB (%.1f%%), Swap: %.1f/%.1f GB (%.1f%%)",
            result["memory_available_gb"],
            result["memory_total_gb"],
            result["memory_used_percent"],
            result["swap_free_gb"],
            result["swap_total_gb"],
            result["swap_used_percent"],
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        _logger.warning("[IMP:8][host_collector][get_host_memory] /proc/meminfo unreadable: %s", exc)

    return result


# endregion FUNC_get_host_memory


# region FUNC_get_host_uname
## @purpose  Collect OS name, kernel version, architecture via os.uname()
## @io       ⇥ (none) → ⎋ dict — {os_name, kernel_version, arch}
## @complexity  O(1) — single stdlib call
def get_host_uname() -> dict:
    """Get OS/kernel/arch via os.uname() — zero-cost stdlib call.

    # ▶ os.uname() → ⊕ {os_name, kernel_version, arch} → ⎋ dict
    """
    _logger = logging.getLogger(__name__)
    try:
        un = os.uname()
        result = {
            "os_name": un.sysname,
            "kernel_version": un.release,
            "arch": un.machine,
        }
        _logger.info(
            "[IMP:9][host_collector][get_host_uname] OS: %s %s %s",
            result["os_name"],
            result["kernel_version"],
            result["arch"],
        )
        return result
    except (OSError, AttributeError) as exc:
        _logger.warning("[IMP:8][host_collector][get_host_uname] os.uname failed: %s", exc)
        return {"os_name": "unknown", "kernel_version": "unknown", "arch": "unknown"}


# endregion FUNC_get_host_uname
