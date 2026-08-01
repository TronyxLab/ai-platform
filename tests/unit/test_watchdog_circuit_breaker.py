#!/usr/bin/env python3
# GREP_SUMMARY: test-watchdog-circuit-breaker circuit-breaker-service circuit-event read-write-state closed-to-open window-reset filter check-all
# STRUCTURE: ┌9 test functions┐ → ◇ CircuitBreakerService parsing (2) → ◇ state roundtrip (1) → ◇ closed→open (1)
#            → ◇ window reset (1) → ◇ window filter (1) → ◇ check_all (3)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/hermes-agent/watchdog/circuit_breaker.py — extracted from
##           agent_watchdog.py (DevPlan 117 G T52). Characterization: reproduces pre-refactor behavior.
## @scope    No Docker — subprocess and docker_manager/telegram duck-typed collaborators mocked.
## @invariants
##   - All tests use tmp_path for state dir (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T52 §TEST_SPEC — circuit_breaker direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T52 — created
# endregion MODULE_CONTRACT

import sys
from pathlib import Path
from unittest import mock

# watchdog/ dir import path (same pattern as test_agent_watchdog.py).
_WATCHDOG_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "hermes-agent" / "watchdog"
if str(_WATCHDOG_DIR) not in sys.path:
    sys.path.insert(0, str(_WATCHDOG_DIR))

from circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerService,
)


class _FakeConfig:
    """Duck-typed WatchdogConfig — avoids importing agent_watchdog in the test module."""

    def __init__(self, state_dir: str, services: list | None = None):
        self.cb_state_dir = state_dir
        self.cb_services = services or []


# ══════════════════════════════════════════════════════════════════════
# TESTS: CircuitBreakerService parsing
# ══════════════════════════════════════════════════════════════════════


class TestCircuitBreakerService:
    """CircuitBreakerService.from_config_entry parsing."""

    # 🧪 TRAP[TEST] · Regression · Scenario: valid config entry
    # · Expect: service, command, max_failures, window parsed
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: parsing logic changes
    def test_cb_service_from_config_entry(self) -> None:
        """Valid entry → parsed service."""
        svc = CircuitBreakerService.from_config_entry("postgres:pg_isready -U postgres -h 127.0.0.1 -t 5:5:300")

        assert svc is not None
        assert svc.service_name == "postgres"
        assert svc.check_command == ["pg_isready", "-U", "postgres", "-h", "127.0.0.1", "-t", "5"]
        assert svc.max_failures == 5
        assert svc.window_seconds == 300

    # 🧪 TRAP[TEST] · Regression · Scenario: incomplete entry
    # · Expect: None
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: parsing logic changes
    def test_cb_service_incomplete_entry(self) -> None:
        """Incomplete entry → None."""
        assert CircuitBreakerService.from_config_entry("incomplete:entry") is None
        assert CircuitBreakerService.from_config_entry("") is None

    def test_cb_service_bad_ints_fallback(self) -> None:
        """Non-numeric max_failures/window → defaults 5/300."""
        svc = CircuitBreakerService.from_config_entry("svc:true:abc:xyz")
        assert svc is not None
        assert svc.max_failures == 5
        assert svc.window_seconds == 300


# ══════════════════════════════════════════════════════════════════════
# TESTS: CircuitBreaker state machine
# ══════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """CircuitBreaker state machine: read/write, open/close, window logic."""

    # 🧪 TRAP[TEST] · Regression · Scenario: state roundtrip
    # · Expect: write → read → identical
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: persistence format changes
    def test_cb_read_write_state(self, tmp_path: Path) -> None:
        """Write state → read back → identical."""
        config = _FakeConfig(str(tmp_path))
        cb = CircuitBreaker(config)

        expected = {"failures": [1000, 2000], "circuit_open": True}
        cb._write_state("test-svc", expected)

        assert cb._read_state("test-svc") == expected

    # 🧪 TRAP[TEST] · Regression · Scenario: missing state file
    # · Expect: default state
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: persistence format changes
    def test_cb_read_missing_state(self, tmp_path: Path) -> None:
        """Missing state file → default state."""
        cb = CircuitBreaker(_FakeConfig(str(tmp_path)))
        assert cb._read_state("nope") == {"failures": [], "circuit_open": False}

    # 🧪 TRAP[TEST] · Regression · Scenario: 5 failures → circuit opens
    # · Expect: 5th failure opens circuit
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: circuit state machine changes
    def test_cb_threshold_exceeded(self, tmp_path: Path) -> None:
        """5 failures in window → circuit opens on 5th."""
        cb = CircuitBreaker(_FakeConfig(str(tmp_path)))

        with mock.patch("time.time", return_value=1000000):
            for i in range(4):
                assert cb._increment_failures("svc", 5, 300) is False, f"failure {i + 1}"
            assert cb._increment_failures("svc", 5, 300) is True, "5th failure should open"

    # 🧪 TRAP[TEST] · Regression · Scenario: window expiry resets open circuit
    # · Expect: auto-reset to closed
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: window reset logic changes
    def test_cb_window_reset(self, tmp_path: Path) -> None:
        """Open circuit + expired window → reset to closed."""
        cb = CircuitBreaker(_FakeConfig(str(tmp_path)))

        with mock.patch("time.time") as mock_time:
            mock_time.return_value = 1000
            cb._write_state("svc", {"failures": [1000], "circuit_open": True})

            mock_time.return_value = 2000  # 1000 + 300 < 2000 → window expired
            assert cb._increment_failures("svc", 5, 300) is False
            state = cb._read_state("svc")
            assert state["circuit_open"] is False

    # 🧪 TRAP[TEST] · Regression · Scenario: open circuit within window stays open
    # · Expect: returns True
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: open-circuit short-circuit logic changes
    def test_cb_open_within_window_stays_open(self, tmp_path: Path) -> None:
        """Open circuit + still within window → True."""
        cb = CircuitBreaker(_FakeConfig(str(tmp_path)))

        with mock.patch("time.time") as mock_time:
            mock_time.return_value = 1000
            cb._write_state("svc", {"failures": [1000], "circuit_open": True})
            mock_time.return_value = 1100  # within 300s window
            assert cb._increment_failures("svc", 5, 300) is True

    # 🧪 TRAP[TEST] · Regression · Scenario: old failures filtered by window
    # · Expect: only in-window failures counted
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: filter logic changes
    def test_cb_failures_filtered_by_window(self, tmp_path: Path) -> None:
        """Old failures outside window → filtered."""
        cb = CircuitBreaker(_FakeConfig(str(tmp_path)))

        with mock.patch("time.time") as mock_time:
            cb._write_state("svc", {"failures": [100, 100, 400, 400, 450], "circuit_open": False})
            mock_time.return_value = 500
            assert cb._increment_failures("svc", 5, 300) is False
            state = cb._read_state("svc")
            assert len(state["failures"]) == 4  # 3 in-window + 1 new

    # 🧪 TRAP[TEST] · Regression · Scenario: health check command succeeds
    # · Expect: passed event, no failure increment
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: _check_service logic changes
    def test_cb_check_service_pass(self, tmp_path: Path, caplog) -> None:
        """Healthy service → passed event."""
        caplog.set_level(0)
        svc = CircuitBreakerService("svc", ["true"], "true", 5, 300)
        cb = CircuitBreaker(_FakeConfig(str(tmp_path), [svc]))
        docker = mock.MagicMock()
        telegram = mock.MagicMock()

        with mock.patch("circuit_breaker.subprocess.run", return_value=mock.MagicMock(returncode=0)):
            event = cb._check_service(svc, docker, telegram)

        assert event is not None
        assert event.event_type == "passed"
        docker.stop_container.assert_not_called()

    # 🧪 TRAP[TEST] · Regression · Scenario: threshold exceeded → circuit opens + stops container
    # · Expect: opened event, container stopped, telegram sent
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: _check_service circuit-open branch changes
    def test_cb_check_service_opens_stops_container(self, tmp_path: Path, caplog) -> None:
        """Repeated failures → circuit opens, container stopped, telegram sent."""
        caplog.set_level(0)
        svc = CircuitBreakerService("svc", ["false"], "false", 2, 300)
        cb = CircuitBreaker(_FakeConfig(str(tmp_path), [svc]))
        docker = mock.MagicMock()
        telegram = mock.MagicMock()

        with (
            mock.patch("time.time", return_value=1000),
            mock.patch("circuit_breaker.subprocess.run", return_value=mock.MagicMock(returncode=1)),
            mock.patch.dict("os.environ", {"CONTEXT": "tronyx161"}),
        ):
            first = cb._check_service(svc, docker, telegram)
            second = cb._check_service(svc, docker, telegram)

        assert first is not None
        assert first.event_type == "failed"
        assert second is not None
        assert second.event_type == "opened"
        docker.stop_container.assert_called_once_with("svc")
        telegram.send.assert_called_once()

    # 🧪 TRAP[TEST] · Regression · Scenario: check_all_services aggregates events
    # · Expect: all service events collected
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: check_all_services logic changes
    def test_cb_check_all_no_failures(self, tmp_path: Path, caplog) -> None:
        """check_all_services with healthy services → passed events."""
        caplog.set_level(0)
        svc = CircuitBreakerService("svc", ["true"], "true", 5, 300)
        cb = CircuitBreaker(_FakeConfig(str(tmp_path), [svc]))

        with mock.patch("circuit_breaker.subprocess.run", return_value=mock.MagicMock(returncode=0)):
            events = cb.check_all_services(mock.MagicMock(), mock.MagicMock())

        assert len(events) == 1
        assert events[0].event_type == "passed"
