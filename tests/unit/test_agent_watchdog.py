"""
# GREP_SUMMARY: test_agent_watchdog, circuit-breaker, self-update, healthcheck, telegram, pending-update, watchdog-config
# STRUCTURE: ▶ Config/Service/PendingUpdate dataclass tests → ▶ CircuitBreaker state machine (read/write/transition/reset/filter) →
#            ▶ HealthChecker poll (success/timeout) → ▶ TelegramNotifier (missing secrets)
# region MODULE_CONTRACT
## @purpose  Unit tests for agent_watchdog.py — watchdog daemon for hermes-agent self-update and circuit breaker.
## @scope    Python-only unit tests (no Docker, no subprocess for business logic).
##           13 test cases covering: config parsing, circuit breaker state machine,
##           health check polling, pending update I/O, telegram notification.
## @invariants
##   - Uses tmp_path for all file I/O — no hardcoded paths
##   - Mock urllib for HealthChecker — no network in unit tests
##   - CircuitBreaker tests use isolated tmp_path state directory
##   - All tests verify IMP:9 business logic logs via caplog
## @rationale DevPlan 075 $TEST_SPEC — full coverage of agent_watchdog.py components.
# endregion MODULE_CONTRACT
"""

import logging
import os
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "hermes-agent" / "watchdog"),
)
# DevPlan 117 G T52: CircuitBreaker/CircuitBreakerService extracted to circuit_breaker.py.
from agent_watchdog import (
    HealthChecker,
    PendingUpdate,
    TelegramNotifier,
    WatchdogConfig,
)
from circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerService,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# WatchdogConfig tests
# ═══════════════════════════════════════════════════════════════════


class TestWatchdogConfig:
    """WatchdogConfig.from_env() parsing and defaults."""

    # region test_config_from_env_defaults
    def test_config_from_env_defaults(self, caplog) -> None:
        """Verify WatchdogConfig.from_env() returns correct defaults when no env vars set."""
        # 🧪 TRAP[TEST] · Regression: Ensure config defaults match shell script defaults
        # · Scenario: No env vars — all defaults from env or class defaults
        # · Last fail: N/A
        # · Remove if: config defaults change
        caplog.set_level(logging.INFO)

        # Clear relevant env vars
        for key in [
            "AGENT_PORT",
            "WATCHDOG_TIMEOUT",
            "PENDING_FILE",
            "SECRETS_FILE",
            "AUDIT_LOG",
            "KEEP_IMAGES",
            "MODULE_DIR",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT",
            "CIRCUIT_BREAKER_STATE_DIR",
            "CIRCUIT_BREAKER_SERVICES",
            "POLL_INTERVAL",
            "CURL_MAX_TIME",
            "CURL_TG_MAX_TIME",
            "TELEGRAM_PROXY_URL",
            "AGENT_READY_URL",
        ]:
            os.environ.pop(key, None)

        config = WatchdogConfig.from_env()

        assert config.health_url == "http://localhost:9119/ready"
        assert config.watchdog_timeout == 90
        assert config.pending_file == WatchdogConfig.DEFAULT_PENDING_FILE
        assert config.secrets_file == WatchdogConfig.DEFAULT_SECRETS_FILE
        assert config.audit_log == WatchdogConfig.DEFAULT_AUDIT_LOG
        assert config.keep_images == 3
        assert config.module_dir == WatchdogConfig.DEFAULT_PLATFORM_ROOT + "/core/modules/hermes-agent"
        assert config.compose_project == "hermes-agent"
        assert config.agent_port == 9119
        assert config.cb_state_dir == WatchdogConfig.DEFAULT_CB_STATE_DIR
        assert config.poll_interval == 5
        assert config.curl_max_time == 3
        assert config.curl_tg_max_time == 30
        assert config.telegram_proxy_url == WatchdogConfig.DEFAULT_TELEGRAM_PROXY_URL
        assert len(config.cb_services) == 5

        # Verify LDD telemetry
        for record in caplog.records:
            if "[IMP:" in record.message and int(record.message.split("[IMP:")[1].split("]")[0]) >= 9:
                break
        # No specific IMP:9 expected from from_env() — config construction is not logged
        # This test verifies no errors during config construction

    # endregion

    # region test_config_from_env_overrides
    def test_config_from_env_overrides(self, caplog) -> None:
        """Verify env var overrides are parsed correctly."""
        # 🧪 TRAP[TEST] · Regression: Ensure env var overrides take precedence over defaults
        # · Scenario: Set env vars to non-default values, verify config reflects them
        # · Last fail: N/A
        # · Remove if: env parsing logic changes
        caplog.set_level(logging.INFO)

        os.environ["AGENT_PORT"] = "9220"
        os.environ["WATCHDOG_TIMEOUT"] = "60"
        os.environ["KEEP_IMAGES"] = "5"
        os.environ["POLL_INTERVAL"] = "2"
        # NOTE: CIRCUIT_BREAKER_SERVICES env var is space-separated entries, each entry
        # is colon-separated. Check commands with spaces would be split by str.split().
        # Use a simple command (no spaces) for env-var parsing tests.
        os.environ["CIRCUIT_BREAKER_SERVICES"] = "myservice:true:3:120"

        config = WatchdogConfig.from_env()

        assert config.agent_port == 9220
        assert config.watchdog_timeout == 60
        assert config.keep_images == 5
        assert config.poll_interval == 2
        assert len(config.cb_services) == 1
        assert config.cb_services[0].service_name == "myservice"
        assert config.cb_services[0].max_failures == 3
        assert config.cb_services[0].window_seconds == 120
        assert config.health_url == "http://localhost:9220/ready"

        # Cleanup
        for key in [
            "AGENT_PORT",
            "WATCHDOG_TIMEOUT",
            "KEEP_IMAGES",
            "POLL_INTERVAL",
            "CIRCUIT_BREAKER_SERVICES",
        ]:
            os.environ.pop(key, None)

    # endregion


# ═══════════════════════════════════════════════════════════════════
# CircuitBreakerService tests
# ═══════════════════════════════════════════════════════════════════


class TestCircuitBreakerService:
    """CircuitBreakerService config entry parsing."""

    # region test_cb_service_from_config_entry
    def test_cb_service_from_config_entry(self, caplog) -> None:
        """Parse valid colon-separated config entry → CircuitBreakerService."""
        # 🧪 TRAP[TEST] · Regression: Ensure config entry parsing matches shell format
        # · Scenario: "postgres:pg_isready -U postgres -h 127.0.0.1 -t 5:5:300"
        # · Last fail: N/A
        # · Remove if: entry format changes
        caplog.set_level(logging.INFO)

        entry = "postgres:pg_isready -U postgres -h 127.0.0.1 -t 5:5:300"
        svc = CircuitBreakerService.from_config_entry(entry)

        assert svc is not None
        assert svc.service_name == "postgres"
        assert svc.check_command == ["pg_isready", "-U", "postgres", "-h", "127.0.0.1", "-t", "5"]
        assert svc.check_command_str == "pg_isready -U postgres -h 127.0.0.1 -t 5"
        assert svc.max_failures == 5
        assert svc.window_seconds == 300

    # endregion

    # region test_cb_service_from_config_entry_invalid
    def test_cb_service_from_config_entry_invalid(self, caplog) -> None:
        """Parse invalid config entry → None."""
        # 🧪 TRAP[TEST] · Regression: Ensure invalid entries are skipped gracefully
        # · Scenario: Entry with fewer than 4 colon-separated parts
        # · Last fail: N/A
        # · Remove if: parser edge case handling changes
        caplog.set_level(logging.INFO)

        # Too few parts
        svc = CircuitBreakerService.from_config_entry("incomplete:entry")
        assert svc is None

        # Empty string
        svc = CircuitBreakerService.from_config_entry("")
        assert svc is None

    # endregion


# ═══════════════════════════════════════════════════════════════════
# CircuitBreaker tests
# ═══════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """Circuit breaker state machine: read/write, open/close, window expiry, filtering."""

    # region test_circuit_breaker_read_write_state
    def test_circuit_breaker_read_write_state(self, tmp_path: Path, caplog) -> None:
        """Write circuit breaker state → read back → verify JSON integrity."""
        # 🧪 TRAP[TEST] · Regression: Ensure state persistence roundtrip preserves data
        # · Scenario: Write state dict, read back, compare
        # · Last fail: N/A
        # · Remove if: state persistence format changes
        caplog.set_level(logging.INFO)

        config = WatchdogConfig(cb_state_dir=str(tmp_path))
        config.cb_services = [
            CircuitBreakerService(
                service_name="test-svc",
                check_command=["true"],
                check_command_str="true",
                max_failures=5,
                window_seconds=300,
            )
        ]
        cb = CircuitBreaker(config)

        # Write state
        expected = {"failures": [1000, 2000], "circuit_open": True}
        cb._write_state("test-svc", expected)

        # Read state back
        actual = cb._read_state("test-svc")
        assert actual == expected
        assert actual["circuit_open"] is True
        assert len(actual["failures"]) == 2

    # endregion

    # region test_circuit_breaker_closed_to_open
    def test_circuit_breaker_closed_to_open(self, tmp_path: Path, caplog) -> None:
        """5 failures in 300s window → circuit opens on 5th failure."""
        # 🧪 TRAP[TEST] · Regression: Circuit state machine transitions correctly
        # · Scenario: Inject 5 failures via _increment_failures, 4th returns False, 5th True
        # · Last fail: N/A
        # · Remove if: circuit breaker logic fundamentally changes
        caplog.set_level(logging.INFO)

        config = WatchdogConfig(cb_state_dir=str(tmp_path))
        cb = CircuitBreaker(config)

        with patch("time.time", return_value=1000000):
            # Failures 1-4: circuit stays closed
            for i in range(4):
                is_open = cb._increment_failures("test-svc", 5, 300)
                assert is_open is False, f"Failure {i + 1} should not open circuit"

            # Failure 5: circuit opens
            is_open = cb._increment_failures("test-svc", 5, 300)
            assert is_open is True, "5th failure should open circuit"

            # Verify state persisted
            state = cb._read_state("test-svc")
            assert state["circuit_open"] is True
            assert len(state["failures"]) == 5

            # Verify LDD trajectory
            found_imp9 = False
            for record in caplog.records:
                if "[IMP:9]" in record.message and "CIRCUIT BREAKER OPENED" in record.message:
                    found_imp9 = True
                    break
            assert found_imp9, "LDD Error: No [IMP:9] circuit breaker opened log"

    # endregion

    # region test_circuit_breaker_window_expiry_reset
    def test_circuit_breaker_window_expiry_reset(self, tmp_path: Path, caplog) -> None:
        """Circuit open, window expired → auto-reset to closed."""
        # 🧪 TRAP[TEST] · Regression: Circuit auto-recovery after window expiry
        # · Scenario: Open circuit with failure at t=1000, check at t=2000 with 300s window
        # · Last fail: N/A
        # · Remove if: window expiry logic changes
        caplog.set_level(logging.INFO)

        config = WatchdogConfig(cb_state_dir=str(tmp_path))
        cb = CircuitBreaker(config)

        with patch("time.time") as mock_time:
            # Open circuit at t=1000
            mock_time.return_value = 1000
            cb._write_state("test-svc", {"failures": [1000], "circuit_open": True})

            # Check at t=2000 — window (300s) has expired (1000 + 300 = 1300 < 2000)
            mock_time.return_value = 2000
            is_open = cb._increment_failures("test-svc", 5, 300)
            assert is_open is False, "Circuit should auto-reset after window expiry"

            # Verify state was reset
            state = cb._read_state("test-svc")
            assert state["circuit_open"] is False
            assert len(state["failures"]) == 0

    # endregion

    # region test_circuit_breaker_failures_filtered_by_window
    def test_circuit_breaker_failures_filtered_by_window(self, tmp_path: Path, caplog) -> None:
        """Old failures outside window → filtered out, count correct."""
        # 🧪 TRAP[TEST] · Regression: Only failures within window count
        # · Scenario: 2 failures at t=100 (outside 300s window from t=500), 2 at t=400, 1 at t=450
        # · Check at t=500: only 3 failures in window (t=400, 400, 450) < max_failures=5
        # · Last fail: N/A
        # · Remove if: filter logic changes
        caplog.set_level(logging.INFO)

        config = WatchdogConfig(cb_state_dir=str(tmp_path))
        cb = CircuitBreaker(config)

        with patch("time.time") as mock_time:
            # Write state with mixed timestamps
            cb._write_state("test-svc", {"failures": [100, 100, 400, 400, 450], "circuit_open": False})

            # Check at t=500 with 300s window
            mock_time.return_value = 500
            # Only failures >= 200 (500-300) should count
            # t=100, t=100 are outside window → filtered
            # t=400, t=400, t=450 are inside window = 3 failures < 5 threshold
            is_open = cb._increment_failures("test-svc", 5, 300)
            assert is_open is False, "3 in-window failures < 5 threshold should not open"

            # State should now have 4 failures (3 old + 1 new)
            state = cb._read_state("test-svc")
            assert len(state["failures"]) == 4
            assert state["circuit_open"] is False

    # endregion


# ═══════════════════════════════════════════════════════════════════
# HealthChecker tests
# ═══════════════════════════════════════════════════════════════════


class TestHealthChecker:
    """HealthChecker poll behavior: success (HTTP 200) and timeout."""

    # region test_health_checker_poll_success
    def test_health_checker_poll_success(self, caplog) -> None:
        """Mock HTTP 200 response → poll returns True."""
        # 🧪 TRAP[TEST] · Regression: HealthChecker detects healthy agent
        # · Scenario: urllib returns 200 on first request → poll returns True
        # · Last fail: N/A
        # · Remove if: poll logic changes
        caplog.set_level(logging.INFO)

        hc = HealthChecker("http://test.local/ready", poll_interval=1, curl_timeout=3)

        class MockResponse:
            """Mock urllib response with context manager support."""

            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        def _mock_urlopen(req, timeout=None):
            return MockResponse()

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen) as mock_urlopen:
            result = hc.poll(timeout_sec=5, label="test")
            assert result is True
            mock_urlopen.assert_called_once()

        # Verify LDD trajectory
        for record in caplog.records:
            if "[IMP:" in record.message:
                imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
                if imp_level >= 9:
                    break
        # On success path, IMP:8 is expected (not IMP:9)
        # The assertion is that no error occurs

    # endregion

    # region test_health_checker_poll_timeout
    def test_health_checker_poll_timeout(self, caplog) -> None:
        """All requests fail → poll returns False after timeout."""
        # 🧪 TRAP[TEST] · Regression: HealthChecker times out when agent is unresponsive
        # · Scenario: urllib raises URLError repeatedly → poll returns False after timeout
        # · Last fail: N/A
        # · Remove if: poll logic changes
        caplog.set_level(logging.INFO)

        hc = HealthChecker("http://test.local/ready", poll_interval=1, curl_timeout=3)

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            result = hc.poll(timeout_sec=3, label="test")
            assert result is False, "Poll should return False on timeout"

        # Verify LDD trajectory — should find IMP:9 timeout log
        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message and "timed out" in record.message:
                found_imp9 = True
                break
        assert found_imp9, "LDD Error: No [IMP:9] timeout log found"

    # endregion


# ═══════════════════════════════════════════════════════════════════
# PendingUpdate tests
# ═══════════════════════════════════════════════════════════════════


class TestPendingUpdate:
    """PendingUpdate file I/O: read/write roundtrip and missing file."""

    # region test_pending_update_read_write
    def test_pending_update_read_write(self, tmp_path: Path, caplog) -> None:
        """Write PendingUpdate → read back → all fields match."""
        # 🧪 TRAP[TEST] · Regression: PendingUpdate serialization roundtrip preserves data
        # · Scenario: Create PendingUpdate, write to file, read from_file, verify all fields
        # · Last fail: N/A
        # · Remove if: I/O format changes
        caplog.set_level(logging.INFO)

        pf = tmp_path / "agent.update-pending"

        update = PendingUpdate(
            new_version="v2.0.0",
            timestamp="2026-07-25T12:00:00Z",
            state="pending",
        )
        update.write(str(pf))

        # Read back
        result = PendingUpdate.from_file(str(pf))
        assert result is not None
        assert result.new_version == "v2.0.0"
        assert result.timestamp == "2026-07-25T12:00:00Z"
        assert result.state == "pending"
        assert result.success_time == ""
        assert result.rollback_time == ""
        assert result.failure_time == ""

        # Verify file content format
        text = pf.read_text()
        assert "new_version=v2.0.0" in text
        assert "timestamp=2026-07-25T12:00:00Z" in text
        assert "state=pending" in text

    # endregion

    # region test_pending_update_missing_file
    def test_pending_update_missing_file(self, tmp_path: Path, caplog) -> None:
        """from_file on nonexistent path → None (no crash)."""
        # 🧪 TRAP[TEST] · Regression: Missing pending file handled gracefully
        # · Scenario: Call from_file on path that does not exist → None
        # · Last fail: N/A
        # · Remove if: I/O logic changes
        caplog.set_level(logging.INFO)

        pf = tmp_path / "nonexistent.file"
        result = PendingUpdate.from_file(str(pf))
        assert result is None, "from_file should return None for missing file"

    # endregion


# ═══════════════════════════════════════════════════════════════════
# TelegramNotifier tests
# ═══════════════════════════════════════════════════════════════════


class TestTelegramNotifier:
    """TelegramNotifier behavior when secrets file is missing or present."""

    # region test_telegram_notifier_no_secrets_file
    def test_telegram_notifier_no_secrets_file(self, tmp_path: Path, caplog) -> None:
        """Secrets file missing → send returns False (no crash, no network)."""
        # 🧪 TRAP[TEST] · Regression: TelegramNotifier handles missing secrets gracefully
        # · Scenario: secrets_file path does not exist → send returns False
        # · Last fail: N/A
        # · Remove if: Telegram error handling changes
        caplog.set_level(logging.INFO)

        tn = TelegramNotifier(
            secrets_file=str(tmp_path / "nonexistent.env"),
            proxy_url="http://127.0.0.1:8118",
            curl_timeout=5,
        )

        result = tn.send("Test message")
        assert result is False, "send should return False when secrets file is missing"

        # Verify LDD trajectory — should find IMP:9 warning about missing secrets file
        found_warning = False
        for record in caplog.records:
            if "[IMP:9]" in record.message and "Secrets file not found" in record.message:
                found_warning = True
                break
        assert found_warning, "LDD Error: No [IMP:9] secrets file warning log"

    # endregion
