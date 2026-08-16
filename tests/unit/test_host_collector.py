# GREP_SUMMARY: test-host-collector host-collector memory swap uname /proc/meminfo mock unit-test
# STRUCTURE: ▶ test_get_host_memory_parses_meminfo → mock /proc/meminfo → verify 6 float fields
#            ▶ test_get_host_memory_file_not_found → no /proc/meminfo → graceful zeros
#            ▶ test_get_host_uname_returns_os_fields → mock os.uname → verify dict
#            ▶ test_get_host_uname_os_error → os.uname raises OSError → graceful 'unknown'
# region MODULE_CONTRACT
## @purpose  Unit tests for host_collector.py — memory/swap parser and OS/kernel collector
## @scope    No Docker required. Uses mock.patch for /proc filesystem and os.uname.
## @invariants
##   - tmp_path for temporary files (Zero Hardcode Rule)
##   - No subprocess.run — pure unit tests
##   - Graceful degradation tested: FileNotFoundError, OSError
## @rationale  /proc/meminfo parser is Linux-specific; mocking ensures testability on macOS.
## @changes
##   2026-07-24 | 047 W1 | Created — 4 tests for get_host_memory + get_host_uname
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path
from unittest import mock

import pytest

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

# ── Import target module ──
# Need to add the module path to sys.path
HOST_COLLECTOR_DIR = Path(__file__).parent.parent.parent / "core" / "internal" / "healthcheck" / "metrics"
sys.path.insert(0, str(HOST_COLLECTOR_DIR))


# ═══════════════════════════════════════════════════════════════════
# TESTS: get_host_memory()
# ═══════════════════════════════════════════════════════════════════


class TestGetHostMemory:
    """Tests for host_collector.get_host_memory()."""

    MEMINFO_CONTENT = """\
MemTotal:       16234500 kB
MemFree:         8234560 kB
MemAvailable:    8234560 kB
Buffers:          524288 kB
Cached:          4194304 kB
SwapCached:            0 kB
SwapTotal:       4194300 kB
SwapFree:        3900123 kB
"""

    def test_get_host_memory_parses_meminfo(self, caplog):
        """/proc/meminfo with complete data → all 6 float fields parsed correctly."""
        caplog.set_level(0)

        with mock.patch("builtins.open", mock.mock_open(read_data=self.MEMINFO_CONTENT)):
            # Re-import after path setup
            import importlib

            host_collector = importlib.import_module("host_collector")
            importlib.reload(host_collector)

            result = host_collector.get_host_memory()

        # ── LDD TRAJECTORY ──
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        found_imp9 = False
        for record in list(caplog.records):
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        logger.info("%s", msg)
                    if imp_level >= 9:
                        found_imp9 = True
        logger.info("--- END LDD TRAJECTORY ---")

        # MemTotal: 16234500 kB → 16234500 / 1024^2 = ~15.5 GB
        assert result["memory_total_gb"] == 15.5, f"Expected 15.5, got {result['memory_total_gb']}"
        # MemAvailable: 8234560 kB → 8234560 / 1024^2 = ~7.9 GB
        assert result["memory_available_gb"] == 7.9, f"Expected 7.9, got {result['memory_available_gb']}"
        # Used: (1 - 8234560/16234500) * 100 ≈ 49.3
        assert result["memory_used_percent"] == 49.3, f"Expected 49.3, got {result['memory_used_percent']}"
        # SwapTotal: 4194300 kB → 4194300 / 1024^2 = 4.0 GB
        assert result["swap_total_gb"] == 4.0, f"Expected 4.0, got {result['swap_total_gb']}"
        # SwapFree: 3900123 kB → 3900123 / 1024^2 = 3.7 GB
        assert result["swap_free_gb"] == 3.7, f"Expected 3.7, got {result['swap_free_gb']}"
        # Swap used: (1 - 3900123/4194300) * 100 ≈ 7.0
        assert result["swap_used_percent"] == 7.0, f"Expected 7.0, got {result['swap_used_percent']}"
        assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    def test_get_host_memory_file_not_found(self, caplog):
        """/proc/meminfo missing → all fields = 0, no exception."""
        caplog.set_level(0)

        with mock.patch("builtins.open", mock.mock_open()) as mock_file:
            mock_file.side_effect = FileNotFoundError("No such file")
            import importlib

            host_collector = importlib.import_module("host_collector")
            importlib.reload(host_collector)

            result = host_collector.get_host_memory()

        # ── LDD TRAJECTORY ──
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in list(caplog.records):
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        logger.info("%s", msg)
        logger.info("--- END LDD TRAJECTORY ---")

        # All fields should be 0
        assert result["memory_total_gb"] == 0.0
        assert result["memory_available_gb"] == 0.0
        assert result["memory_used_percent"] == 0.0
        assert result["swap_total_gb"] == 0.0
        assert result["swap_free_gb"] == 0.0
        assert result["swap_used_percent"] == 0.0


# ═══════════════════════════════════════════════════════════════════
# TESTS: get_host_uname()
# ═══════════════════════════════════════════════════════════════════


class TestGetHostUname:
    """Tests for host_collector.get_host_uname()."""

    def test_get_host_uname_returns_os_fields(self, caplog):
        """os.uname() returns sysname, release, machine → dict contains os_name, kernel_version, arch."""
        caplog.set_level(0)

        uname_result = mock.Mock()
        uname_result.sysname = "Linux"
        uname_result.release = "6.1.0"
        uname_result.machine = "x86_64"

        with mock.patch("os.uname", return_value=uname_result):
            import importlib

            host_collector = importlib.import_module("host_collector")
            importlib.reload(host_collector)

            result = host_collector.get_host_uname()

        # ── LDD TRAJECTORY ──
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        found_imp9 = False
        for record in list(caplog.records):
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        logger.info("%s", msg)
                    if imp_level >= 9:
                        found_imp9 = True
        logger.info("--- END LDD TRAJECTORY ---")

        assert result["os_name"] == "Linux"
        assert result["kernel_version"] == "6.1.0"
        assert result["arch"] == "x86_64"
        assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    def test_get_host_uname_os_error(self, caplog):
        """os.uname() raises OSError → all fields = 'unknown', no exception."""
        caplog.set_level(0)

        with mock.patch("os.uname", side_effect=OSError("Function not implemented")):
            import importlib

            host_collector = importlib.import_module("host_collector")
            importlib.reload(host_collector)

            result = host_collector.get_host_uname()

        # ── LDD TRAJECTORY ──
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in list(caplog.records):
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        logger.info("%s", msg)
        logger.info("--- END LDD TRAJECTORY ---")

        assert result["os_name"] == "unknown"
        assert result["kernel_version"] == "unknown"
        assert result["arch"] == "unknown"
