#!/usr/bin/env python3
# GREP_SUMMARY: circuit-breaker circuit-breaker-service circuit-event state-machine failures window open reset check-all
# STRUCTURE: ▶ CircuitBreakerService.from_config_entry → ▶ CircuitEvent → ▶ CircuitBreaker: _read_state/_write_state → _run_health_check → _increment_failures → _check_service → check_all_services
# region MODULE_CONTRACT
## @purpose  Circuit breaker framework for stateful services — extracted from agent_watchdog.py
##           (DevPlan 117 G T52). Tracks repeated health-check failures per service and stops
##           crash-looping containers once the failure threshold is crossed within a time window.
## @scope    Consumed by core/modules/hermes-agent/watchdog/agent_watchdog.py (lazy import).
##           Self-contained: imports only stdlib + core.internal.config (default_context) +
##           duck-typed DockerManager/TelegramNotifier collaborators.
## @invariants
##   - State persistence: JSON files at {state_dir}/{service_name}.json
##   - State machine: CLOSED → OPEN (failures >= max in window) → HALF_OPEN (window expiry → reset)
##   - Health checks via subprocess with 10s hard timeout; failures never raise
##   - Does NOT import agent_watchdog (no circular dep) — WatchdogConfig passed duck-typed
## @rationale  DevPlan 117 G T52 — extracted verbatim from agent_watchdog.py (CircuitBreakerService
##            + CircuitEvent + CircuitBreaker, ~266 LOC) with all LDD logs, TRAP comments and
##            docstrings preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T52 — extracted from agent_watchdog.py
## @changes  2026-08-02 · DevPlan 119 A2 — timeout=10 литерал → WATCHDOG_CB_CHECK_TIMEOUT из shared/timeouts
# endregion MODULE_CONTRACT

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from core.internal.config import (
    platform_config,
)  # LINT-EXEMPT: контейнерный модуль; internal.config — by design (D1, allowlist 116 B11 T1)
from core.internal.shared.timeouts import (
    WATCHDOG_CB_CHECK_TIMEOUT,
)  # LINT-EXEMPT: контейнерный модуль; shared.timeouts — watchdog-таймауты из единого реестра (DevPlan 119 A2)

if TYPE_CHECKING:
    from agent_watchdog import WatchdogConfig  # type: ignore[import-not-found]  # duck-typed, never imported at runtime

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Configuration dataclasses
# ═══════════════════════════════════════════════════════════════════


# region DATACLASS__CircuitBreakerService
@dataclass
class CircuitBreakerService:
    """Configuration for one circuit breaker service.

    Parsed from the shell format: "service_name:check_cmd:max_failures:window_seconds"
    """

    service_name: str
    check_command: list[str]  # Pre-split command for safe subprocess execution
    check_command_str: str  # Original string for logging
    max_failures: int = 5
    window_seconds: int = 300

    @classmethod
    def from_config_entry(cls, entry: str) -> Optional["CircuitBreakerService"]:
        """Parse colon-separated config entry.

        Format: "postgres:pg_isready -U postgres -h 127.0.0.1 -t 5:5:300"

        TRAP: The shell version used IFS=' ' read -ra to split the check command.
        In Python, we split by colon first (4 parts), then split the command by spaces.
        """
        parts = entry.split(":", 3)
        if len(parts) < 4:
            return None
        service_name, cmd_str, max_fail_str, window_str = parts
        cmd_parts = cmd_str.split()
        try:
            max_failures = int(max_fail_str)
            window_seconds = int(window_str)
        except ValueError:
            max_failures = 5
            window_seconds = 300
        return cls(
            service_name=service_name.strip(),
            check_command=cmd_parts,
            check_command_str=cmd_str.strip(),
            max_failures=max_failures,
            window_seconds=window_seconds,
        )


# endregion


# region DATACLASS__CircuitEvent
@dataclass
class CircuitEvent:
    """Result of a single circuit breaker check."""

    service: str
    event_type: str  # "passed", "failed", "opened", "reset"
    failure_count: int = 0
    max_failures: int = 0


# endregion

# ═══════════════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════════════


# region CLASS__CircuitBreaker
class CircuitBreaker:
    """Circuit breaker framework for stateful services.

    State machine:
        CLOSED (circuit_open=False) — normal operation, tracking failures
        OPEN (circuit_open=True) — failures >= threshold in window, service stopped
        HALF_OPEN — window expired since last failure, auto-reset to CLOSED

    State persistence: JSON files at {state_dir}/{service_name}.json
    Format: {"failures": [unix_timestamp, ...], "circuit_open": bool}
    """

    def __init__(self, config: "WatchdogConfig"):
        self._state_dir = Path(config.cb_state_dir)
        self._services = config.cb_services
        self._state_dir.mkdir(parents=True, exist_ok=True)

    # region FUNC__read_state
    def _read_state(self, service_name: str) -> dict:
        """Read circuit breaker state from JSON file.

        Returns default state {"failures": [], "circuit_open": false} if file missing.
        """
        state_file = self._state_dir / f"{service_name}.json"
        if state_file.is_file():
            try:
                return json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"failures": [], "circuit_open": False}

    # endregion

    # region FUNC__write_state
    def _write_state(self, service_name: str, state: dict) -> None:
        """Write circuit breaker state to JSON file atomically."""
        state_file = self._state_dir / f"{service_name}.json"
        tmp_file = self._state_dir / f".{service_name}.json.tmp"
        try:
            tmp_file.write_text(json.dumps(state))
            tmp_file.replace(state_file)
        except OSError:
            # Non-fatal: state file write failure — log and continue
            logger.warning("[IMP:8][cb] Failed to write state for %s", service_name)

    # endregion

    # region FUNC__run_health_check
    def _run_health_check(self, command: list[str]) -> bool:
        """Execute a health check command. Returns True if healthy (exit 0)."""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=WATCHDOG_CB_CHECK_TIMEOUT,  # Hard timeout for health checks (канон shared/timeouts, 119 A2)
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    # endregion

    # region FUNC__increment_failures
    def _increment_failures(self, service_name: str, max_failures: int, window_seconds: int) -> bool:
        """Record a failure and check if circuit should open.

        Returns True if circuit is OPEN (stop restarting),
        False if circuit is CLOSED (continue tracking).
        """
        now = int(time.time())
        state = self._read_state(service_name)
        circuit_open = state.get("circuit_open", False)

        # If circuit already open, check for auto-recovery
        if circuit_open:
            failures = state.get("failures", [])
            last_failure = failures[-1] if failures else 0
            if now - last_failure > window_seconds:
                # Window expired — auto-recover
                logger.info(
                    "[IMP:8][cb:%s] Circuit window expired — resetting failures",
                    service_name,
                )
                self._write_state(service_name, {"failures": [], "circuit_open": False})
                return False
            logger.info("[IMP:9][cb:%s] Circuit is OPEN — service is stopped", service_name)
            return True

        # Filter failures within window, append current
        failures = [f for f in state.get("failures", []) if now - f < window_seconds]
        failures.append(now)
        is_open = len(failures) >= max_failures

        new_state = {
            "failures": failures,
            "circuit_open": is_open,
        }
        self._write_state(service_name, new_state)

        logger.info(
            "[IMP:8][cb:%s] Failure count: %d/%d in %ds window",
            service_name,
            len(failures),
            max_failures,
            window_seconds,
        )

        if is_open:
            logger.info(
                "[IMP:9][cb:%s] CIRCUIT BREAKER OPENED — %d failures in %ds",
                service_name,
                len(failures),
                window_seconds,
            )

        return is_open

    # endregion

    # region FUNC__check_service
    def _check_service(
        self,
        svc: CircuitBreakerService,
        docker_manager,  # Duck-typed DockerManager — lazy import avoids circular dep
        telegram,  # Duck-typed TelegramNotifier — lazy import avoids circular dep
    ) -> CircuitEvent | None:
        """Check one service and return circuit event or None."""
        logger.info(
            "[IMP:8][cb:%s] Checking health via: %s",
            svc.service_name,
            svc.check_command_str,
        )

        if self._run_health_check(svc.check_command):
            logger.info("[IMP:8][cb:%s] Health check PASSED", svc.service_name)
            return CircuitEvent(
                service=svc.service_name,
                event_type="passed",
                max_failures=svc.max_failures,
            )

        logger.info("[IMP:9][cb:%s] Health check FAILED", svc.service_name)

        circuit_opened = self._increment_failures(svc.service_name, svc.max_failures, svc.window_seconds)

        if circuit_opened:
            # Stop the crash-looping container
            logger.info(
                "[IMP:9][cb:%s] CIRCUIT BREAK: Stopping %s due to repeated health failures",
                svc.service_name,
                svc.service_name,
            )
            docker_manager.stop_container(svc.service_name)
            # 🧐 TRAP[DECISION] · 2026-08-01 · — · default_context() без "test" fallback (DevPlan 116 B6 D4)
            # · Rejected: literal fallback "test" (хардкод-копия SoT env_defaults.CONTEXT)
            # · Reason: platform-env.yaml отсутствует в образе hermes-agent (верифицировано 2026-08-01 —
            #   нет COPY в L2 Dockerfile, нет volume-маунта); watchdog всегда получает CONTEXT из
            #   docker-compose env (`${CONTEXT:-test}`, base.yml:154) → поведение не меняется; если env
            #   отсутствует — fallback деградирует до "" (fail-visible) вместо тихой лжи "test".
            # · Rev: если образ начнёт доставлять platform-env.yaml — удалить заметку
            context = os.environ.get("CONTEXT", platform_config.default_context())
            telegram.send(
                f"\U0001f6a8 [{context}] Circuit breaker opened for {svc.service_name}%0A"
                f"{svc.max_failures} failures in {svc.window_seconds}s%0A"
                f"Auto-stopped to prevent crash-loop data corruption"
            )

            return CircuitEvent(
                service=svc.service_name,
                event_type="opened",
                failure_count=svc.max_failures,
                max_failures=svc.max_failures,
            )

        return CircuitEvent(
            service=svc.service_name,
            event_type="failed",
            max_failures=svc.max_failures,
        )

    # endregion

    # region FUNC_check_all_services
    def check_all_services(self, docker_manager, telegram) -> list[CircuitEvent]:
        """Run one circuit breaker check cycle for all configured services.

        Returns list of CircuitEvent results.
        """
        logger.info("[IMP:7][cb] Running circuit breaker check cycle")
        events: list[CircuitEvent] = []

        for svc in self._services:
            if not svc.service_name:
                logger.info("[IMP:8][cb] Invalid circuit breaker entry — skipping")
                continue
            event = self._check_service(svc, docker_manager, telegram)
            if event:
                events.append(event)

        logger.info("[IMP:7][cb] Circuit breaker cycle complete")
        return events

    # endregion


# endregion
