#!/usr/bin/env python3
# GREP_SUMMARY: agent_watchdog, circuit-breaker, self-update, hermes-agent, healthcheck, rollback, telegram, watchdog-daemon, secrets_env_parser, telegram_notifier
# STRUCTURE: ▶ argparse config → ▶ CircuitBreaker.check_all → ▶ check PENDING_FILE → ◇ poll_ready → ⊕ success(cleanup+exit0) | ◇ rollback(down→pull→up→re-poll) → ⊕ rollback_ok(telegram+exit0) | ⊕ rollback_fail(critical_telegram+exit1)
# 📝 TRAP[DEBT] · 2026-08-02 · HI · Watchdog subsystem not delivered (DevPlan 119 C2)
# · Observed: 0 references in Dockerfile/compose/systemd/CI — подсистема не доставляется
# · Suspected: feature-flag awaiting activation or abandoned prototype
# · Impact: dead code in repo; тесты покрывают недоставленную функциональность
# · When: 119 wave 2 audit — deferred, решение пользователя на волну 120 (D-1, Brief 119)
# · Rev: 2026-08-XX (120) — владелец решает: доставить (feature flag) или полный sweep
#   (код + тесты + module.yaml env_requires)
# region MODULE_CONTRACT
## @purpose  Production watchdog daemon for hermes-agent self-update monitoring and stateful service circuit breaking.
## @scope    OS-level independent process — uses ONLY Python stdlib, no agent dependencies.
##           Two independent phases per tick: (1) circuit breaker for 5 stateful services,
##           (2) self-update readiness check with automatic rollback.
## @invariants
##   - Imports from core.internal.shared for shared utilities (secrets_env_parser, telegram_notifier)
##   - Core dependencies: Python 3.10+ stdlib + core/internal/shared modules (both stdlib-only)
##   - Two phases are independent — circuit breaker failure does NOT affect self-update phase
##   - Exit codes: 0 = success or skip, 1 = critical failure (self-update rollback failed)
##   - All logs via standard logging to stderr (systemd journal); самописный AuditLogger
##     удалён (DevPlan 117 D19) — файл /var/log/platform/watchdog-audit.log никем не читался
##   - Telegram via shared telegram_notifier module (urllib-based) — bypasses dead agent
##   - Secrets file absence handled gracefully (log warning, skip notification)
##   - Docker compose-операции (down/pull/up) через shared/docker_compose.py (DevPlan 117 D19);
##     raw docker-команды (image ls, stop, ps) — через _run_docker
##   - Docker commands via subprocess.run — NEVER shell=True for command strings
## @rationale Migrated from shell (platform-agent-watchdog.sh) per Strangler-Fig Tier 1 trigger:
##           5 inline python3 calls for JSON state management → extracted into CircuitBreaker class.
##           Shell watchdog = single point of failure; Python daemon with signal handling = production-grade.
##           T8: Inline Telegram parser and sender replaced with shared modules from
##           core/internal/shared/ to eliminate code duplication across the platform.
## @changes    2026-07-30 | DevPlan T8 — Replaced inline _load_token() with secrets_env_parser shared module;
##            replaced send() urllib with telegram_notifier shared module; updated invariants
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from core.internal.config import (
    platform_config,
)  # LINT-EXEMPT: контейнерный модуль; internal.config — by design (D1, allowlist 116 B11 T1)
from core.internal.shared.secrets_env_parser import (
    parse as parse_secrets_env,
)  # LINT-EXEMPT: контейнерный модуль; shared — by design (D1, allowlist 116 B11 T1)
from core.internal.shared.telegram_notifier import (
    send_telegram as send_tg,
)  # LINT-EXEMPT: контейнерный модуль; shared — by design (D1, allowlist 116 B11 T1)
from core.internal.shared.timeouts import (
    WATCHDOG_CURL_MAX_TIME,
    WATCHDOG_CURL_TG_MAX_TIME,
    WATCHDOG_POLL_INTERVAL,
    WATCHDOG_TIMEOUT,
)  # LINT-EXEMPT: контейнерный модуль; shared — by design (D1, DevPlan 117 D29)

# ⚠️ NOTE (DevPlan 117 G T52): CircuitBreakerService/CircuitEvent/CircuitBreaker moved to
# circuit_breaker.py; DockerManager moved to docker_ops.py. Lazy imports inside methods
# (from circuit_breaker import ... / from docker_ops import ...) — start-up time unchanged (AC-G5).
# TYPE_CHECKING-only annotations for the extracted classes — never imported at runtime.
if TYPE_CHECKING:
    from circuit_breaker import (  # type: ignore[import-not-found]
        CircuitBreakerService,
    )

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Configuration dataclasses
# ═══════════════════════════════════════════════════════════════════


# region DATACLASS__WatchdogConfig
@dataclass
class WatchdogConfig:
    """Central configuration — populated from environment variables."""

    # Class-level default constants — single source of truth for deployment paths
    DEFAULT_PLATFORM_ROOT: ClassVar[str] = "/opt/platform"
    DEFAULT_PENDING_FILE: ClassVar[str] = "/var/lib/platform/agent.update-pending"
    DEFAULT_SECRETS_FILE: ClassVar[str] = "/run/platform/secrets.env"
    DEFAULT_AUDIT_LOG: ClassVar[str] = "/var/log/platform/watchdog-audit.log"
    DEFAULT_CB_STATE_DIR: ClassVar[str] = "/var/lib/platform/watchdog"
    DEFAULT_TELEGRAM_PROXY_URL: ClassVar[str] = "http://127.0.0.1:8118"

    # Self-update
    health_url: str = "http://localhost:9119/ready"
    watchdog_timeout: int = 90
    pending_file: str = ""
    secrets_file: str = ""
    audit_log: str = ""
    keep_images: int = 3
    # module_dir default is set dynamically in from_env() using PLATFORM_ROOT.
    # Empty string signals "not set" — from_env() provides the real default.
    module_dir: str = ""
    compose_file: str = ""
    compose_project: str = "hermes-agent"
    agent_port: int = 9119

    # Circuit breaker
    # Circuit breaker — services list typed via TYPE_CHECKING import (circuit_breaker.py
    # is lazy-imported, never at module level).
    cb_state_dir: str = ""
    cb_services: list[CircuitBreakerService] = field(default_factory=list)

    # Polling
    poll_interval: int = 5
    curl_max_time: int = 3
    curl_tg_max_time: int = 30

    # Telegram
    telegram_proxy_url: str = ""

    @classmethod
    def from_env(cls) -> WatchdogConfig:
        """Construct config from environment variables with defaults."""
        # Lazy import — CircuitBreakerService lives in circuit_breaker.py (DevPlan 117 G T52).
        from circuit_breaker import CircuitBreakerService

        # AGENT_PORT — runtime env var; соответствует HERMES_DASHBOARD_PORT (9119) из
        # platform-infra.yaml env_defaults (SoT портов). Двойное имя сохранено для
        # backward-compat: AGENT_PORT используется watchdog-контейнером, HERMES_DASHBOARD_PORT —
        # платформенным env (DevPlan 117 D31). Дефолт 9119 — единый порт дашборда.
        agent_port = int(os.environ.get("AGENT_PORT", "9119"))
        # Use PLATFORM_ROOT as base for deployment paths — canonical platform pattern.
        # This matches the gate test allowlist (os.environ.get("PLATFORM_ROOT", cls.DEFAULT_PLATFORM_ROOT)).
        platform_root = os.environ.get("PLATFORM_ROOT", cls.DEFAULT_PLATFORM_ROOT)
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
            # Таймаут-дефолты — из timeouts.py (DevPlan 117 D29); env-переменные сохраняют приоритет.
            watchdog_timeout=int(os.environ.get("WATCHDOG_TIMEOUT", str(WATCHDOG_TIMEOUT))),
            pending_file=os.environ.get("PENDING_FILE", cls.DEFAULT_PENDING_FILE),
            secrets_file=os.environ.get("SECRETS_FILE", cls.DEFAULT_SECRETS_FILE),
            audit_log=os.environ.get("AUDIT_LOG", cls.DEFAULT_AUDIT_LOG),
            keep_images=int(os.environ.get("KEEP_IMAGES", "3")),
            module_dir=module_dir,
            compose_file=os.environ.get("COMPOSE_FILE", f"{module_dir}/docker-compose.base.yml"),
            compose_project=os.environ.get("COMPOSE_PROJECT", "hermes-agent"),
            agent_port=agent_port,
            cb_state_dir=os.environ.get("CIRCUIT_BREAKER_STATE_DIR", cls.DEFAULT_CB_STATE_DIR),
            cb_services=cb_services,
            poll_interval=int(os.environ.get("POLL_INTERVAL", str(WATCHDOG_POLL_INTERVAL))),
            curl_max_time=int(os.environ.get("CURL_MAX_TIME", str(WATCHDOG_CURL_MAX_TIME))),
            curl_tg_max_time=int(os.environ.get("CURL_TG_MAX_TIME", str(WATCHDOG_CURL_TG_MAX_TIME))),
            telegram_proxy_url=os.environ.get("TELEGRAM_PROXY_URL", cls.DEFAULT_TELEGRAM_PROXY_URL),
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
    def from_file(cls, path: str) -> PendingUpdate | None:
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


# ═══════════════════════════════════════════════════════════════════
# Circuit Breaker — moved to circuit_breaker.py (DevPlan 117 G T52).
# agent_watchdog.py instantiates it lazily inside Watchdog.__init__.
# ═══════════════════════════════════════════════════════════════════


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
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310 — local Hermes agent health endpoint
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

        Delegates to shared secrets_env_parser module (core/internal/shared/secrets_env_parser.py).

        Returns (token, chat_id) or None if not found.
        """
        if not os.path.isfile(self._secrets_file):
            logger.info(
                "[IMP:9][watchdog][telegram] WARNING: Secrets file not found at %s — cannot send Telegram notification",
                self._secrets_file,
            )
            return None

        try:
            secrets = parse_secrets_env(self._secrets_file)
        except (FileNotFoundError, OSError) as e:
            logger.info(
                "[IMP:9][watchdog][telegram] ERROR: Could not read secrets file %s: %s",
                self._secrets_file,
                e,
            )
            return None

        token = secrets.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = secrets.get("TELEGRAM_CHAT_ID", "")

        if not token or not chat_id:
            logger.info(
                "[IMP:9][watchdog][telegram] WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in %s",
                self._secrets_file,
            )
            return None

        return token, chat_id

    def send(self, message: str) -> bool:
        """Send a message to Telegram.

        Delegates to shared telegram_notifier module (core/internal/shared/telegram_notifier.py).

        Returns True if sent successfully, False otherwise.
        Non-fatal to watchdog flow — failures are logged, not escalated.
        """
        creds = self._load_token()
        if not creds:
            return False

        token, chat_id = creds
        return send_tg(message, token, chat_id, self._proxy_url)


# endregion

# ═══════════════════════════════════════════════════════════════════
# DockerManager — moved to docker_ops.py (DevPlan 117 G T52).
# agent_watchdog.py instantiates it lazily inside Watchdog.__init__.
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# Watchdog — main orchestrator
# ═══════════════════════════════════════════════════════════════════


# region CLASS__Watchdog
class Watchdog:
    """Main watchdog daemon — orchestrates circuit breaker and self-update phases."""

    def __init__(self, config: WatchdogConfig):
        self._config = config
        # DevPlan 117 D19: AuditLogger (самописный ts+print+file) → стандартный logging.
        # Файл /var/log/platform/watchdog-audit.log никем не читается (grep 2026-08-01) —
        # замена безопасна; логи идут в stderr (systemd journal), форматтер в main().
        self._log = logger
        # Lazy imports — CircuitBreaker/DockerManager live in sibling modules (DevPlan 117 G T52).
        from circuit_breaker import CircuitBreaker
        from docker_ops import DockerManager

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
            self._log.info(f"[IMP:9][watchdog][signal] Received {sig_name} — shutting down")
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
        self._log.info(f"[IMP:9][watchdog][success] New version {update.new_version} is ready and healthy")

        # Mark success
        update.state = "success"
        update.success_time = str(int(time.time()))
        update.write(self._config.pending_file)

        # Cleanup old images
        self._docker.cleanup_old_images(self._config.keep_images)

        self._log.info(
            f"[IMP:9][watchdog][success] Self-update to {update.new_version} completed successfully — agent healthy"
        )
        self._log.info("[IMP:8][watchdog][main] ===== Watchdog tick completed (success) =====")

    # endregion

    # region FUNC__handle_rollback
    def _handle_rollback(self, update: PendingUpdate) -> int:
        """Perform rollback: down → pull → up → re-poll.

        Returns 0 on success, 1 on critical failure.
        """
        self._log.info(
            f"[IMP:9][watchdog][rollback] New version {update.new_version} FAILED /ready check "
            f"({self._config.watchdog_timeout}s timeout) — initiating automatic rollback"
        )
        self._log.info("[IMP:8][watchdog][rollback] Rollback strategy: down → compose pull → up → re-wait → notify")

        # 5a: docker compose down
        self._docker.compose_down("hermes-agent")

        # 5b: docker compose pull
        self._docker.compose_pull()

        # 5c: docker compose up -d
        self._docker.compose_up("hermes-agent")

        # 5d: Re-poll /ready
        self._log.info("[IMP:8][watchdog][rollback] Step 5d: re-polling /ready for previous version")
        rollback_ok = self._health_checker.poll(self._config.watchdog_timeout, "rollback")

        if rollback_ok:
            self._log.info(
                "[IMP:9][watchdog][rollback_success] Rollback to previous version SUCCESSFUL — agent is healthy"
            )

            # Mark rollback state
            update.state = "rolled_back"
            update.rollback_time = str(int(time.time()))
            update.write(self._config.pending_file)

            # Telegram notification
            # 🧐 TRAP[DECISION] · 2026-08-01 · — · default_context() без "test" fallback (DevPlan 116 B6 D4)
            # · Rejected: literal fallback "test" (хардкод-копия SoT env_defaults.CONTEXT)
            # · Reason: watchdog получает CONTEXT из docker-compose env (`${CONTEXT:-test}`); платформенный
            #   platform-env.yaml в образе отсутствует → fallback деградирует до "" (fail-visible).
            # · Rev: если образ начнёт доставлять platform-env.yaml — удалить заметку
            context = os.environ.get("CONTEXT", platform_config.default_context())
            self._telegram.send(
                f"\U0001f504 [{context}] Agent auto-rollback%0A"
                f"New version failed /ready check ({self._config.watchdog_timeout}s timeout)%0A"
                f"Reverted to previous image"
            )

            self._log.info("[IMP:9][watchdog][rollback_success] Rollback complete — node operational")
            self._log.info("[IMP:8][watchdog][main] ===== Watchdog tick completed (rolled_back) =====")
            return 0

        return self._handle_rollback_failure(update)

    # endregion

    # region FUNC__handle_rollback_failure
    def _handle_rollback_failure(self, update: PendingUpdate) -> int:
        """Critical escalation when rollback also fails."""
        self._log.info("[IMP:9][watchdog][rollback_critical] =====================================================")
        self._log.info(
            f"[IMP:9][watchdog][rollback_critical] CRITICAL: Agent rollback FAILED "
            f"after {self._config.watchdog_timeout}s"
        )
        self._log.info("[IMP:9][watchdog][rollback_critical] Agent is NOT responsive — manual intervention required")
        self._log.info("[IMP:9][watchdog][rollback_critical] =====================================================")

        # Mark failure state
        update.state = "rollback_failed"
        update.failure_time = str(int(time.time()))
        update.write(self._config.pending_file)

        # Diagnostic: container status
        self._log.info("[IMP:9][watchdog][rollback_critical] Current hermes-agent container status:")
        status = self._docker.container_status("hermes-agent")
        for line in status.splitlines():
            if line.strip():
                self._log.info(f"[IMP:9][watchdog][rollback_critical]   {line}")

        # Critical Telegram notification
        self._telegram.send("\U0001f6a8 CRITICAL: Agent rollback FAILED — manual intervention required")

        self._log.info(
            "[IMP:9][watchdog][rollback_critical] Watchdog exiting with code 1 — "
            "node remains operational (agent is non-critical)"
        )
        self._log.info("[IMP:8][watchdog][main] ===== Watchdog tick completed (CRITICAL FAILURE) =====")
        return 1

    # endregion

    # region FUNC__run_self_update_phase
    def _run_self_update_phase(self) -> int:
        """Phase 2: Self-update check (only if PENDING_FILE exists).

        Returns exit code: 0 = success/skip, 1 = critical failure.
        """
        if not Path(self._config.pending_file).is_file():
            self._log.info("[IMP:3][watchdog][main] No pending update file — circuit breaker cycle complete")
            return 0

        # Read pending update
        update = PendingUpdate.from_file(self._config.pending_file)
        if update is None:
            return 0

        if update.state != "pending":
            self._log.info(
                f"[IMP:3][watchdog][main] PENDING_FILE state is '{update.state}' "
                f"(not pending) — already handled, exiting"
            )
            return 0

        self._log.info(
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
        self._log.info("[IMP:8][watchdog][main] ===== Watchdog tick started =====")

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
