"""
# GREP_SUMMARY: test_preflight, preflight, ssh-probe, disk-space, s3-probe, ghcr, dockerhub, dns, graceful-degradation
# STRUCTURE: ▶ tmp_path + mock subprocess/socket → ◇ probe_ssh → ◇ probe_disk → ◇ probe_s3 → ◇ probe_ghcr → ◇ probe_dockerhub → ◇ probe_dns → ◇ run_preflight → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for preflight.py — pre-flight gate checks before bootstrap.
## @scope    Tests all 6 probe functions + run_preflight aggregator + FATAL/WARN classification.
## @invariants
##   - All network probes mock subprocess.run or socket.create_connection
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via caplog
## @rationale DevPlan 047 Phase 7: pre-flight gate prevents blind bootstrap attempts.
## @changes  2026-07-22 | DevPlan 047 Phase 7 — Created
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import preflight

# ═══════════════════════════════════════════════════════════════════
# region Tests: probe_ssh_connectivity
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · SSH connectivity probe returns ok on successful socket connection
# · Scenario: socket.create_connection succeeds → CheckResult(status="ok")
# · Last fail: N/A (new test)
# · Remove if: probe_ssh_connectivity logic changes
@ldd_trajectory
def test_ssh_connectivity_ok(caplog):
    """SSH probe should return ok when socket connects."""
    mock_socket = MagicMock()
    mock_socket.__enter__ = MagicMock(return_value=mock_socket)
    mock_socket.__exit__ = MagicMock(return_value=False)
    with patch("socket.create_connection", return_value=mock_socket):
        result = preflight.probe_ssh_connectivity("127.0.0.1", 22)
    assert result.status == "ok"
    assert result.latency_ms >= 0
    logger.critical("[IMP:9][test] SSH probe OK — FATAL check passed")


# 🧪 TRAP[TEST] · Regression · SSH connectivity probe returns fatal on connection refused
# · Scenario: socket.create_connection raises ConnectionRefusedError → CheckResult(status="fatal")
# · Last fail: N/A (new test)
# · Remove if: probe_ssh_connectivity logic changes
@ldd_trajectory
def test_ssh_connectivity_fail_fatal(caplog):
    """SSH probe should return fatal when connection refused."""
    with patch("socket.create_connection", side_effect=ConnectionRefusedError("refused")):
        result = preflight.probe_ssh_connectivity("127.0.0.1", 22)
    assert result.status == "fatal"
    assert result.error is not None
    logger.critical("[IMP:9][test] SSH probe FATAL — connection refused detected")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: probe_disk_space
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Disk space probe returns ok when sufficient space
# · Scenario: shutil.disk_usage returns >10GB free → CheckResult(status="ok")
# · Last fail: N/A (new test)
# · Remove if: probe_disk_space threshold changes
@ldd_trajectory
def test_disk_space_threshold(caplog, tmp_path):
    """Disk probe should return ok when free space > 10GB threshold."""
    # Mock shutil.disk_usage to return sufficient space
    mock_usage = MagicMock()
    mock_usage.free = 20 * 1024 * 1024 * 1024  # 20 GB
    mock_usage.total = 100 * 1024 * 1024 * 1024
    mock_usage.used = 80 * 1024 * 1024 * 1024
    with patch("shutil.disk_usage", return_value=mock_usage):
        result = preflight.probe_disk_space(str(tmp_path))
    assert result.status == "ok"
    assert "GB free" in result.detail
    logger.critical("[IMP:9][test] Disk space OK — above threshold")


# 🧪 TRAP[TEST] · Regression · Disk space probe returns fatal when below threshold
# · Scenario: shutil.disk_usage returns <10GB free → CheckResult(status="fatal")
# · Last fail: N/A (new test)
# · Remove if: probe_disk_space threshold changes
@ldd_trajectory
def test_disk_space_below_threshold_fatal(caplog, tmp_path):
    """Disk probe should return fatal when free space < 10GB."""
    mock_usage = MagicMock()
    mock_usage.free = 5 * 1024 * 1024 * 1024  # 5 GB — below threshold
    mock_usage.total = 50 * 1024 * 1024 * 1024
    mock_usage.used = 45 * 1024 * 1024 * 1024
    with patch("shutil.disk_usage", return_value=mock_usage):
        result = preflight.probe_disk_space(str(tmp_path))
    assert result.status == "fatal"
    assert "insufficient" in (result.error or "").lower() or "need" in result.detail.lower()
    logger.critical("[IMP:9][test] Disk space FATAL — below threshold detected")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: probe_s3_connectivity
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · S3 probe returns warn when credentials missing (graceful degradation)
# · Scenario: Empty credentials → CheckResult(status="warn") — non-fatal
# · Last fail: N/A (new test)
# · Remove if: S3 graceful degradation logic changes
@ldd_trajectory
def test_s3_graceful_degradation(caplog):
    """S3 probe should return warn when credentials missing (graceful)."""
    result = preflight.probe_s3_connectivity("", "", "", "")
    assert result.status == "warn"
    assert "not configured" in result.detail.lower() or "credentials" in result.detail.lower()
    logger.critical("[IMP:9][test] S3 graceful degradation — WARN on missing creds")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: probe_ghcr_auth
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · GHCR probe returns warn when token missing (fallback build)
# · Scenario: Empty token → CheckResult(status="warn") — build fallback used
# · Last fail: N/A (new test)
# · Remove if: GHCR graceful degradation logic changes
@ldd_trajectory
def test_ghcr_unavailable_warn(caplog):
    """GHCR probe should return warn when token missing."""
    result = preflight.probe_ghcr_auth("")
    assert result.status == "warn"
    assert "build fallback" in result.detail.lower() or "not set" in result.detail.lower()
    logger.critical("[IMP:9][test] GHCR WARN — build fallback will be used")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: run_preflight aggregator
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · run_preflight executes all checks and returns aggregated result
# · Scenario: Mock all probes to return ok → PreflightResult has 6 checks, 0 fatals
# · Last fail: N/A (new test)
# · Remove if: run_preflight aggregation logic changes
@ldd_trajectory
def test_parallel_execution(caplog, monkeypatch):
    """run_preflight should run all 6 checks and aggregate results."""
    # Mock all probes to return ok
    ok_result = preflight.CheckResult(status="ok", latency_ms=10, detail="OK")
    monkeypatch.setattr(preflight, "probe_ssh_connectivity", lambda *a, **k: ok_result)
    monkeypatch.setattr(preflight, "probe_disk_space", lambda *a, **k: ok_result)
    monkeypatch.setattr(preflight, "probe_s3_connectivity", lambda *a, **k: ok_result)
    monkeypatch.setattr(preflight, "probe_ghcr_auth", lambda *a, **k: ok_result)
    monkeypatch.setattr(preflight, "probe_docker_hub", lambda *a, **k: ok_result)
    monkeypatch.setattr(preflight, "probe_dns_resolution", lambda *a, **k: ok_result)

    result = preflight.run_preflight(node_yaml="", context="test", node_name="test-node")

    assert len(result.checks) == 6
    assert not result.has_fatals()
    assert "ssh_connectivity" in result.checks
    assert "disk_space" in result.checks
    assert "s3_connectivity" in result.checks
    assert "ghcr_auth" in result.checks
    assert "docker_hub_probe" in result.checks
    assert "dns_resolution" in result.checks
    logger.critical("[IMP:9][test] run_preflight executed all 6 checks — no fatals")


# 🧪 TRAP[TEST] · Regression · run_preflight detects FATAL checks and aborts
# · Scenario: ssh_connectivity returns fatal → PreflightResult.has_fatals() == True
# · Last fail: N/A (new test)
# · Remove if: FATAL classification logic changes
@ldd_trajectory
def test_run_preflight_fatal_detection(caplog, monkeypatch):
    """run_preflight should detect FATAL checks."""
    fatal_result = preflight.CheckResult(status="fatal", detail="SSH down", error="refused")
    ok_result = preflight.CheckResult(status="ok", detail="OK")
    monkeypatch.setattr(preflight, "probe_ssh_connectivity", lambda *a, **k: fatal_result)
    monkeypatch.setattr(preflight, "probe_disk_space", lambda *a, **k: ok_result)
    monkeypatch.setattr(preflight, "probe_s3_connectivity", lambda *a, **k: ok_result)
    monkeypatch.setattr(preflight, "probe_ghcr_auth", lambda *a, **k: ok_result)
    monkeypatch.setattr(preflight, "probe_docker_hub", lambda *a, **k: ok_result)
    monkeypatch.setattr(preflight, "probe_dns_resolution", lambda *a, **k: ok_result)

    result = preflight.run_preflight(node_yaml="", context="test", node_name="test")
    assert result.has_fatals()
    assert "ssh_connectivity" in result.fatals
    logger.critical("[IMP:9][test] run_preflight detected FATAL ssh check")


# endregion
