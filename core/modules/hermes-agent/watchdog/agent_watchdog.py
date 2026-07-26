#!/usr/bin/env python3
# GREP_SUMMARY: agent_watchdog, circuit-breaker, self-update, hermes-agent, healthcheck, rollback, telegram, watchdog-daemon
# STRUCTURE: ▶ argparse config → ▶ CircuitBreaker.check_all → ▶ check PENDING_FILE → ◇ poll_ready → ⊕ success(cleanup+exit0) | ◇ rollback(down→pull→up→re-poll) → ⊕ rollback_ok(telegram+exit0) | ⊕ rollback_fail(critical_telegram+exit1)
# region MODULE_CONTRACT
## @purpose  Production watchdog daemon for hermes-agent self-update monitoring and stateful service circuit breaking.
## @scope    OS-level independent process — uses ONLY Python stdlib, no agent dependencies.
##           Two independent phases per tick: (1) circuit breaker for 5 stateful services,
##           (2) self-update readiness check with automatic rollback.
## @invariants
##   - Zero imports from core.* — OS-level independence (system Python only)
##   - Python 3.10+ stdlib only: json, subprocess, signal, logging, dataclasses, time, os, pathlib, sys, argparse, urllib
##   - Two phases are independent — circuit breaker failure does NOT affect self-update phase
##   - Exit codes: 0 = success or skip, 1 = critical failure (self-update rollback failed)
##   - All logs to both stdout (systemd journal) and AUDIT_LOG file
##   - Telegram via direct HTTP (urllib) — bypasses dead agent
##   - Secrets file absence handled gracefully (log warning, skip notification)
##   - Docker commands via subprocess.run — NEVER shell=True for command strings
## @rationale Migrated from shell (platform-agent-watchdog.sh) per Strangler-Fig Tier 1 trigger:
##           5 inline python3 calls for JSON state management → extracted into CircuitBreaker class.
##           Shell watchdog = single point of failure; Python daemon with signal handling = production-grade.
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("watchdog")

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


# region DATACLASS__WatchdogConfig
@dataclass
class WatchdogConfig:
    """Central configuration — populated from environment variables."""

    # Self-update
    health_url: str = "http://localhost:9119/ready"
    watchdog_timeout: int = 90
    pending_file: str = "/var/lib/platform/agent.update-pending"
    secrets_file: str = "/run/platform/secrets.env"
    audit_log: str = "/var/log/platform/watchdog-audit.log"
    keep_images: int = 3
    # module_dir default is set dynamically in from_env() using PLATFORM_ROOT.
    # Empty string signals "not set" — from_env() provides the real default.
    module_dir: str = ""
    compose_file: str = ""
    compose_project: str = "hermes-agent"
    agent_port: int = 9119

    # Circuit breaker
    cb_state_dir: str = "/var/lib/platform/watchdog"
    cb_services: list[CircuitBreakerService] = field(default_factory=list)

    # Polling
    poll_interval: int = 5
    curl_max_time: int = 3
    curl_tg_max_time: int = 30

    # Telegram
    telegram_proxy_url: str = "http://127.0.0.1:8118"

    @classmethod
    def from_env(cls) -> "WatchdogConfig":
        """Construct config from environment variables with defaults."""
        agent_port = int(os.environ.get("AGENT_PORT", "9119"))
        # Use PLATFORM_ROOT as base for deployment paths — canonical platform pattern.
        # This matches the gate test allowlist (os.environ.get("PLATFORM_ROOT", "/opt/platform")).
        platform_root = os.environ.get("PLATFORM_ROOT", "/opt/platform")
        module_dir = os.environ.get("MODULE_DIR", f"{platform_root}/core/modules/hermes-agent")

        # Circuit breaker services: parse from env or use defaults
        cb_services_raw = os.environ.get("CIRCUIT_BREAKER_SERVICES", "")
        if cb_services_raw:
            cb_services = []
            for entry in cb_services_raw.split():
                svc = CircuitBreakerService.from_config_entry(entry)
                if svc:
                    cb_services.append(svc)
        else:
            cb_services = [
                CircuitBreakerService.from_config_entry("postgres:pg_isready -U postgres -h 127.0.0.1 -t 5:5:300"),
                CircuitBreakerService.from_config_entry(
                    "pgbouncer:pg_isready -h 127.0.0.1 -p 6432 -U postgres -t 3:5:300"
                ),
                CircuitBreakerService.from_config_entry("redis:redis-cli -h 127.0.0.1 ping:5:300"),
                CircuitBreakerService.from_config_entry("loki:/usr/bin/loki -version:5:300"),
                CircuitBreakerService.from_config_entry("prometheus:wget -q -O- http://127.0.0.1:9090/-/healthy:5:300"),
            ]
            cb_services = [s for s in cb_services if s is not None]

        return cls(
            health_url=os.environ.get("AGENT_READY_URL", f"http://localhost:{agent_port}/ready"),
            watchdog_timeout=int(os.environ.get("WATCHDOG_TIMEOUT", "90")),
            pending_file=os.environ.get("PENDING_FILE", "/var/lib/platform/agent.update-pending"),
            secrets_file=os.environ.get("SECRETS_FILE", "/run/platform/secrets.env"),
            audit_log=os.environ.get("AUDIT_LOG", "/var/log/platform/watchdog-audit.log"),
            keep_images=int(os.environ.get("KEEP_IMAGES", "3")),
            module_dir=module_dir,
            compose_file=os.environ.get("COMPOSE_FILE", f"{module_dir}/docker-compose.base.yml"),
            compose_project=os.environ.get("COMPOSE_PROJECT", "hermes-agent"),
            agent_port=agent_port,
            cb_state_dir=os.environ.get("CIRCUIT_BREAKER_STATE_DIR", "/var/lib/platform/watchdog"),
            cb_services=cb_services,
            poll_interval=int(os.environ.get("POLL_INTERVAL", "5")),
            curl_max_time=int(os.environ.get("CURL_MAX_TIME", "3")),
            curl_tg_max_time=int(os.environ.get("CURL_TG_MAX_TIME", "30")),
            telegram_proxy_url=os.environ.get("TELEGRAM_PROXY_URL", "http://127.0.0.1:8118"),
        )


# endregion


# region DATACLASS__PendingUpdate
@dataclass
class PendingUpdate:
    """Represents the agent.update-pending state file."""

    new_version: str = ""
    timestamp: str = ""
    state: str = "pending"
    success_time: str = ""
    rollback_time: str = ""
    failure_time: str = ""

    @classmethod
    def from_file(cls, path: str) -> Optional["PendingUpdate"]:
        """Read KEY=VALUE lines from pending file. Returns None if missing."""
        p = Path(path)
        if not p.is_file():
            return None
        data = {}
        for line in p.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                data[key.strip()] = val.strip().strip('"').strip("'")
        return cls(
            new_version=data.get("new_version", ""),
            timestamp=data.get("timestamp", ""),
            state=data.get("state", "pending"),
            success_time=data.get("success_time", ""),
            rollback_time=data.get("rollback_time", ""),
            failure_time=data.get("failure_time", ""),
        )

    def write(self, path: str) -> None:
        """Write state back to pending file."""
        lines = [
            f"new_version={self.new_version}",
            f"timestamp={self.timestamp}",
            f"state={self.state}",
        ]
        if self.success_time:
            lines.append(f"success_time={self.success_time}")
        if self.rollback_time:
            lines.append(f"rollback_time={self.rollback_time}")
        if self.failure_time:
            lines.append(f"failure_time={self.failure_time}")
        Path(path).write_text("\n".join(lines) + "\n")


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
# AuditLogger
# ═══════════════════════════════════════════════════════════════════


# region CLASS__AuditLogger
class AuditLogger:
    """Log to both stdout (systemd journal) and audit log file.

    Mirrors shell timestamp() + log() functions.
    """

    def __init__(self, audit_log_path: str):
        self._audit_log = Path(audit_log_path)
        self._audit_log.parent.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def log(self, message: str) -> None:
        """Write timestamped message to stdout and append to audit log."""
        ts = self._timestamp()
        line = f"{ts} {message}"
        print(line, flush=True)
        try:
            with open(self._audit_log, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass  # Non-fatal: audit log write failure must not crash watchdog


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

    def __init__(self, config: WatchdogConfig):
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
                timeout=10,  # Hard timeout for health checks
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
        docker_manager: "DockerManager",
        telegram: "TelegramNotifier",
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
            context = os.environ.get("CONTEXT", "unknown")
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
    def check_all_services(self, docker_manager: "DockerManager", telegram: "TelegramNotifier") -> list[CircuitEvent]:
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

# ═══════════════════════════════════════════════════════════════════
# HealthChecker
# ═══════════════════════════════════════════════════════════════════


# region CLASS__HealthChecker
class HealthChecker:
    """Poll an HTTP health endpoint until success or timeout."""

    def __init__(self, url: str, poll_interval: int = 5, curl_timeout: int = 3):
        self._url = url
        self._interval = poll_interval
        self._timeout = curl_timeout

    def poll(self, timeout_sec: int, label: str = "update") -> bool:
        """Poll health URL until 200 response or timeout.

        Returns True if healthy (200 response received), False if timeout.
        """
        logger.info(
            "[IMP:8][watchdog][%s] Polling %s (timeout=%ds, interval=%ds)",
            label,
            self._url,
            timeout_sec,
            self._interval,
        )
        elapsed = 0
        while elapsed < timeout_sec:
            try:
                req = urllib.request.Request(self._url, method="GET")
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    if resp.status == 200:
                        logger.info(
                            "[IMP:8][watchdog][%s] /ready returned 200 after %ds",
                            label,
                            elapsed,
                        )
                        return True
            except (urllib.error.URLError, OSError, ValueError):
                pass  # Expected: agent not ready yet

            time.sleep(self._interval)
            elapsed += self._interval

        logger.info(
            "[IMP:9][watchdog][%s] /ready check timed out after %ds",
            label,
            timeout_sec,
        )
        return False


# endregion

# ═══════════════════════════════════════════════════════════════════
# TelegramNotifier
# ═══════════════════════════════════════════════════════════════════


# region CLASS__TelegramNotifier
class TelegramNotifier:
    """Send Telegram notifications via direct HTTP — bypasses dead agent."""

    def __init__(self, secrets_file: str, proxy_url: str, curl_timeout: int = 30):
        self._secrets_file = secrets_file
        self._proxy_url = proxy_url
        self._timeout = curl_timeout

    def _load_token(self) -> tuple[str, str] | None:
        """Load TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from secrets file.

        Returns (token, chat_id) or None if not found.
        """
        secrets_path = Path(self._secrets_file)
        if not secrets_path.is_file():
            logger.info(
                "[IMP:9][watchdog][telegram] WARNING: Secrets file not found at %s — cannot send Telegram notification",
                self._secrets_file,
            )
            return None

        token = None
        chat_id = None
        for line in secrets_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("TELEGRAM_CHAT_ID="):
                chat_id = line.split("=", 1)[1].strip().strip('"').strip("'")

        if not token or not chat_id:
            logger.info(
                "[IMP:9][watchdog][telegram] WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in %s",
                self._secrets_file,
            )
            return None

        return token, chat_id

    def send(self, message: str) -> bool:
        """Send a message to Telegram.

        Returns True if sent successfully, False otherwise.
        Non-fatal to watchdog flow — failures are logged, not escalated.
        """
        creds = self._load_token()
        if not creds:
            return False

        token, chat_id = creds

        # Build URL-encoded request
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = f"chat_id={chat_id}&text={urllib.parse.quote(message, safe='')}"
        data_bytes = data.encode("ascii")

        try:
            # Set up proxy if configured
            if self._proxy_url:
                proxy_handler = urllib.request.ProxyHandler({"https": self._proxy_url})
                opener = urllib.request.build_opener(proxy_handler)
            else:
                opener = urllib.request.build_opener()

            req = urllib.request.Request(
                url,
                data=data_bytes,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with opener.open(req, timeout=self._timeout) as resp:
                if resp.status == 200:
                    logger.info("[IMP:8][watchdog][telegram] Notification sent successfully")
                    return True
                logger.info(
                    "[IMP:9][watchdog][telegram] ERROR: Telegram API returned HTTP %d",
                    resp.status,
                )
                return False
        except Exception as e:
            logger.info(
                "[IMP:9][watchdog][telegram] ERROR: Telegram API request failed: %s",
                e,
            )
            return False


# endregion

# ═══════════════════════════════════════════════════════════════════
# DockerManager
# ═══════════════════════════════════════════════════════════════════


# region CLASS__DockerManager
class DockerManager:
    """Manage Docker operations: compose, images, containers."""

    def __init__(self, compose_file: str, project_name: str, module_dir: str):
        self._compose_file = compose_file
        self._project = project_name
        self._module_dir = module_dir

    def _run_docker(self, args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
        """Run a docker/docker compose command with consistent error handling."""
        try:
            return subprocess.run(
                ["sudo", "docker", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.info("[IMP:9][watchdog][docker] Timeout: docker %s", " ".join(args))
            return subprocess.CompletedProcess(args=args, returncode=124, stdout="", stderr="timeout")
        except FileNotFoundError:
            logger.info("[IMP:9][watchdog][docker] docker not found")
            return subprocess.CompletedProcess(args=args, returncode=127, stdout="", stderr="docker: not found")

    def compose_down(self, service: str) -> bool:
        """docker compose down <service>"""
        logger.info("[IMP:8][watchdog][rollback] Step 5a: stopping %s via docker compose down", service)
        result = self._run_docker(
            [
                "compose",
                "-f",
                self._compose_file,
                "--project-name",
                self._project,
                "down",
                service,
            ]
        )
        if result.returncode != 0:
            logger.info(
                "[IMP:9][watchdog][rollback] WARNING: docker compose down returned non-zero — continuing rollback"
            )
        return result.returncode == 0

    def compose_pull(self) -> bool:
        """docker compose pull"""
        logger.info("[IMP:8][watchdog][rollback] Step 5b: pulling image via docker compose pull")
        result = self._run_docker(
            [
                "compose",
                "-f",
                self._compose_file,
                "--project-name",
                self._project,
                "pull",
            ]
        )
        if result.returncode != 0:
            logger.info(
                "[IMP:9][watchdog][rollback] CRITICAL: docker compose pull failed — "
                "image may not be available in registry"
            )
        return result.returncode == 0

    def compose_up(self, service: str) -> bool:
        """docker compose up -d <service>"""
        logger.info(
            "[IMP:8][watchdog][rollback] Step 5c: starting %s with previous version (docker compose up -d)",
            service,
        )
        result = self._run_docker(
            [
                "compose",
                "-f",
                self._compose_file,
                "--project-name",
                self._project,
                "up",
                "-d",
                service,
            ]
        )
        if result.returncode != 0:
            logger.info("[IMP:9][watchdog][rollback] CRITICAL: docker compose up -d failed")
        return result.returncode == 0

    def cleanup_old_images(self, keep: int) -> int:
        """Remove old hermes-agent images beyond keep count.

        Returns number of images removed.
        """
        logger.info("[IMP:7][watchdog][cleanup] Cleaning old hermes-agent images (keep=%d)", keep)

        # List hermes-agent images sorted by creation date (newest first)
        result = self._run_docker(
            [
                "image",
                "ls",
                "--filter",
                "reference=hermes-agent",
                "--format",
                "{{.Repository}}:{{.Tag}} {{.CreatedAt}}",
            ],
            timeout=30,
        )

        if result.returncode != 0 or not result.stdout.strip():
            logger.info("[IMP:7][watchdog][cleanup] No hermes-agent images found — skipping cleanup")
            return 0

        # Parse and sort by date (newest first)
        lines = result.stdout.strip().splitlines()
        # Each line: "hermes-agent:tag 2024-01-01 12:00:00 +0000 UTC"
        images = []
        for line in lines:
            parts = line.split(" ", 1)
            if len(parts) >= 2:
                images.append((parts[0], parts[1]))

        # Sort by date descending (newest first)
        images.sort(key=lambda x: x[1], reverse=True)

        removed = 0
        for i, (img_ref, _) in enumerate(images):
            if i < keep:
                continue
            logger.info("[IMP:7][watchdog][cleanup] Removing old image: %s", img_ref)
            r = self._run_docker(["rmi", img_ref], timeout=30)
            if r.returncode == 0:
                removed += 1
            else:
                logger.info(
                    "[IMP:7][watchdog][cleanup] WARNING: Could not remove image %s (may be in use)",
                    img_ref,
                )

        logger.info(
            "[IMP:7][watchdog][cleanup] Image cleanup complete (found=%d, kept=%d, removed=%d)",
            len(images),
            min(len(images), keep),
            removed,
        )
        return removed

    def stop_container(self, name: str) -> bool:
        """Stop a Docker container (docker stop, fallback to docker kill)."""
        # Check if container is running
        ps_result = self._run_docker(
            [
                "ps",
                "--format",
                "{{.Names}}",
            ],
            timeout=10,
        )

        running_containers = ps_result.stdout.strip().splitlines()
        if name not in running_containers:
            logger.info(
                "[IMP:8][watchdog][cb:%s] Container %s is not running",
                name,
                name,
            )
            return True  # Already stopped = success

        logger.info("[IMP:9][watchdog][cb:%s] Stopping container %s", name, name)
        stop_result = self._run_docker(["stop", name], timeout=30)
        if stop_result.returncode == 0:
            logger.info("[IMP:8][watchdog][cb:%s] Container %s stopped", name, name)
            return True

        # Fallback to kill
        logger.info("[IMP:9][watchdog][cb:%s] stop failed — trying kill", name)
        kill_result = self._run_docker(["kill", name], timeout=10)
        if kill_result.returncode != 0:
            logger.info(
                "[IMP:9][watchdog][cb:%s] WARNING: Could not stop container %s",
                name,
                name,
            )
            return False
        return True

    def container_status(self, name: str) -> str:
        """Get container status for diagnostics."""
        result = self._run_docker(
            [
                "ps",
                "-a",
                "--filter",
                f"name={name}",
                "--format",
                "{{.Names}} {{.Status}} {{.Image}}",
            ],
            timeout=10,
        )
        return result.stdout.strip()


# endregion

# ═══════════════════════════════════════════════════════════════════
# Watchdog — main orchestrator
# ═══════════════════════════════════════════════════════════════════


# region CLASS__Watchdog
class Watchdog:
    """Main watchdog daemon — orchestrates circuit breaker and self-update phases."""

    def __init__(self, config: WatchdogConfig):
        self._config = config
        self._log = AuditLogger(config.audit_log)
        self._circuit_breaker = CircuitBreaker(config)
        self._health_checker = HealthChecker(config.health_url, config.poll_interval, config.curl_max_time)
        self._telegram = TelegramNotifier(config.secrets_file, config.telegram_proxy_url, config.curl_tg_max_time)
        self._docker = DockerManager(config.compose_file, config.compose_project, config.module_dir)
        self._shutdown_requested = False

    # region FUNC_setup_signal_handlers
    def setup_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown."""

        def _handler(signum: int, frame) -> None:
            sig_name = signal.Signals(signum).name
            self._log.log(f"[IMP:9][watchdog][signal] Received {sig_name} — shutting down")
            self._shutdown_requested = True

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    # endregion

    # region FUNC__run_circuit_breaker_phase
    def _run_circuit_breaker_phase(self) -> None:
        """Phase 1: Check all circuit breaker services."""
        self._circuit_breaker.check_all_services(self._docker, self._telegram)

    # endregion

    # region FUNC__handle_update_success
    def _handle_update_success(self, update: PendingUpdate) -> None:
        """Handle successful self-update: mark state, cleanup images."""
        self._log.log(f"[IMP:9][watchdog][success] New version {update.new_version} is ready and healthy")

        # Mark success
        update.state = "success"
        update.success_time = str(int(time.time()))
        update.write(self._config.pending_file)

        # Cleanup old images
        self._docker.cleanup_old_images(self._config.keep_images)

        self._log.log(
            f"[IMP:9][watchdog][success] Self-update to {update.new_version} completed successfully — agent healthy"
        )
        self._log.log("[IMP:8][watchdog][main] ===== Watchdog tick completed (success) =====")

    # endregion

    # region FUNC__handle_rollback
    def _handle_rollback(self, update: PendingUpdate) -> int:
        """Perform rollback: down → pull → up → re-poll.

        Returns 0 on success, 1 on critical failure.
        """
        self._log.log(
            f"[IMP:9][watchdog][rollback] New version {update.new_version} FAILED /ready check "
            f"({self._config.watchdog_timeout}s timeout) — initiating automatic rollback"
        )
        self._log.log("[IMP:8][watchdog][rollback] Rollback strategy: down → compose pull → up → re-wait → notify")

        # 5a: docker compose down
        self._docker.compose_down("hermes-agent")

        # 5b: docker compose pull
        self._docker.compose_pull()

        # 5c: docker compose up -d
        self._docker.compose_up("hermes-agent")

        # 5d: Re-poll /ready
        self._log.log("[IMP:8][watchdog][rollback] Step 5d: re-polling /ready for previous version")
        rollback_ok = self._health_checker.poll(self._config.watchdog_timeout, "rollback")

        if rollback_ok:
            self._log.log(
                "[IMP:9][watchdog][rollback_success] Rollback to previous version SUCCESSFUL — agent is healthy"
            )

            # Mark rollback state
            update.state = "rolled_back"
            update.rollback_time = str(int(time.time()))
            update.write(self._config.pending_file)

            # Telegram notification
            context = os.environ.get("CONTEXT", "unknown")
            self._telegram.send(
                f"\U0001f504 [{context}] Agent auto-rollback%0A"
                f"New version failed /ready check ({self._config.watchdog_timeout}s timeout)%0A"
                f"Reverted to previous image"
            )

            self._log.log("[IMP:9][watchdog][rollback_success] Rollback complete — node operational")
            self._log.log("[IMP:8][watchdog][main] ===== Watchdog tick completed (rolled_back) =====")
            return 0

        return self._handle_rollback_failure(update)

    # endregion

    # region FUNC__handle_rollback_failure
    def _handle_rollback_failure(self, update: PendingUpdate) -> int:
        """Critical escalation when rollback also fails."""
        self._log.log("[IMP:9][watchdog][rollback_critical] =====================================================")
        self._log.log(
            f"[IMP:9][watchdog][rollback_critical] CRITICAL: Agent rollback FAILED "
            f"after {self._config.watchdog_timeout}s"
        )
        self._log.log("[IMP:9][watchdog][rollback_critical] Agent is NOT responsive — manual intervention required")
        self._log.log("[IMP:9][watchdog][rollback_critical] =====================================================")

        # Mark failure state
        update.state = "rollback_failed"
        update.failure_time = str(int(time.time()))
        update.write(self._config.pending_file)

        # Diagnostic: container status
        self._log.log("[IMP:9][watchdog][rollback_critical] Current hermes-agent container status:")
        status = self._docker.container_status("hermes-agent")
        for line in status.splitlines():
            if line.strip():
                self._log.log(f"[IMP:9][watchdog][rollback_critical]   {line}")

        # Critical Telegram notification
        self._telegram.send("\U0001f6a8 CRITICAL: Agent rollback FAILED — manual intervention required")

        self._log.log(
            "[IMP:9][watchdog][rollback_critical] Watchdog exiting with code 1 — "
            "node remains operational (agent is non-critical)"
        )
        self._log.log("[IMP:8][watchdog][main] ===== Watchdog tick completed (CRITICAL FAILURE) =====")
        return 1

    # endregion

    # region FUNC__run_self_update_phase
    def _run_self_update_phase(self) -> int:
        """Phase 2: Self-update check (only if PENDING_FILE exists).

        Returns exit code: 0 = success/skip, 1 = critical failure.
        """
        if not Path(self._config.pending_file).is_file():
            self._log.log("[IMP:3][watchdog][main] No pending update file — circuit breaker cycle complete")
            return 0

        # Read pending update
        update = PendingUpdate.from_file(self._config.pending_file)
        if update is None:
            return 0

        if update.state != "pending":
            self._log.log(
                f"[IMP:3][watchdog][main] PENDING_FILE state is '{update.state}' "
                f"(not pending) — already handled, exiting"
            )
            return 0

        self._log.log(
            f"[IMP:8][watchdog][main] Pending update detected: "
            f"version={update.new_version} timestamp={update.timestamp}"
        )

        # Poll /ready
        ready = self._health_checker.poll(self._config.watchdog_timeout, "update")

        if ready:
            self._handle_update_success(update)
            return 0

        return self._handle_rollback(update)

    # endregion

    # region FUNC_run
    def run(self) -> int:
        """Main watchdog entry point. Returns exit code."""
        self._log.log("[IMP:8][watchdog][main] ===== Watchdog tick started =====")

        # Phase 1: Circuit breaker (every tick, independent)
        self._run_circuit_breaker_phase()

        # Phase 2: Self-update (only if PENDING_FILE exists)
        return self._run_self_update_phase()


    # endregion


# endregion

# ═══════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════


# region FUNC_main
def main() -> None:
    """CLI entry point — parse args, run watchdog, exit with code."""
    parser = argparse.ArgumentParser(
        description="Platform Agent Watchdog — hermes-agent self-update monitor and circuit breaker",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Run one tick and exit (oneshot mode, default)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        default=False,
        help="Run continuously as daemon (future: timer loop mode)",
    )
    args = parser.parse_args()

    config = WatchdogConfig.from_env()
    watchdog = Watchdog(config)
    watchdog.setup_signal_handlers()

    if args.daemon:
        # Future: continuous daemon mode with internal timer loop
        # For now, systemd timer handles scheduling — daemon mode is unused
        logger.info("[IMP:9][watchdog] Daemon mode not yet implemented — use systemd timer")
        sys.exit(0)

    exit_code = watchdog.run()
    sys.exit(exit_code)


# endregion

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    main()
