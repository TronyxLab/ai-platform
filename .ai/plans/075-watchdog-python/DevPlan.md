$START_DEVPLAN
# DevPlan 075 (Expanded): platform-agent-watchdog.sh → Python Daemon

$ARTIFACT_CONTRACT
PURPOSE: Migrate platform-agent-watchdog.sh (549 LOC, 5 inline python3 calls, circuit breaker state machine) to a production-grade Python daemon with proper signal handling, structured logging, and typed state machine.
DESCRIPTION: core/modules/hermes-agent/watchdog/platform-agent-watchdog.sh is a systemd oneshot watchdog that runs every 30 seconds (via timer). It performs two independent phases per tick: (1) circuit breaker health checks for stateful services (postgres, redis, pgbouncer, loki, prometheus), and (2) self-update readiness polling for hermes-agent with automatic rollback. The migration replaces all inline python3 JSON state management with a Python-based `CircuitBreaker` class, extracts all business logic into Python, and leaves a <30 LOC shell launcher.
RATIONALE: A watchdog implemented in shell is a single point of failure — if bash itself crashes or a subshell exits unexpectedly, the watchdog dies silently. The circuit breaker state machine (5 inline python3 calls per check cycle across 5 services = 25 subshell launches per tick) is non-trivial business logic that should not live in shell. Python daemon with structured logging, type-safe state transitions, and testable components is the correct production choice.
ACCEPTANCE_CRITERIA:
  - `core/modules/hermes-agent/watchdog/agent_watchdog.py` — Python daemon with all business logic
  - `platform-agent-watchdog.sh` — reduced to <30 LOC launcher (zero inline python3)
  - Circuit breaker: 3-state FSM (CLOSED/OPEN/HALF_OPEN) with JSON persistence
  - Healthcheck: configurable per-service health check commands with failure window tracking
  - Self-update: poll /ready → success (mark+cleanup+exit 0) | fail (down→pull→up→re-poll → rollback_ok telegram+exit 0 | rollback_fail critical_telegram+exit 1)
  - Telegram notifications via direct HTTP (bypasses dead agent)
  - Docker image cleanup (keep KEEP_IMAGES newest)
  - systemd service unit updated to call Python daemon (Type=oneshot)
  - `tests/unit/test_agent_watchdog.py` — 10+ test cases
  - `make gate MODE=fast` — green
IMPLEMENTS: Wave 6B — Tier 1 shell → Python migration (Strangler-Fig Tier 1: ANY inline python3 change triggers extraction)
IMPACTS:
  - core/modules/hermes-agent/watchdog/agent_watchdog.py (NEW — ~500 LOC)
  - core/modules/hermes-agent/watchdog/platform-agent-watchdog.sh (REDUCE — 549→~25 LOC)
  - core/modules/hermes-agent/watchdog/platform-agent-watchdog.service (UPDATE — ExecStart line)
  - tests/unit/test_agent_watchdog.py (NEW — ~300 LOC)
  - core/modules/hermes-agent/watchdog/platform-agent-watchdog.timer (NO CHANGE)
REQUIRES: None (can run parallel to 070-074, 076)

## Source Analysis

### Source file: `core/modules/hermes-agent/watchdog/platform-agent-watchdog.sh`
- **549 LOC** bash script executed by systemd oneshot timer (every 30s)
- **Two independent phases per tick:**
  1. **Circuit Breaker** (lines 224-401): monitors 5 stateful services (postgres, pgbouncer, redis, loki, prometheus), tracks failures in JSON state files, opens circuit after N failures in time window, stops crash-looping containers, sends Telegram alerts
  2. **Self-Update Watchdog** (lines 403-549): checks PENDING_FILE marker, polls agent /ready endpoint, on success marks update complete + cleans old images, on failure performs docker compose down→pull→up rollback + re-polls, on rollback failure escalates with critical Telegram alert
- **5 inline python3 calls** in `increment_failure_counter()` (lines 298, 304, 317-327, 332, 336) — all for JSON state management of circuit breaker
- **TRAP annotations:**
  - Line 243: `TRAP[DECISION]` — eval replaced with array-based execution for check commands (shell injection prevention)
- **Configuration:** 8 env vars (WATCHDOG_TIMEOUT, AGENT_PORT, AGENT_READY_URL, PENDING_FILE, SECRETS_FILE, AUDIT_LOG, KEEP_IMAGES, MODULE_DIR) + circuit breaker config (CIRCUIT_BREAKER_STATE_DIR, CIRCUIT_BREAKER_SERVICES array)
- **Telegram:** Direct curl to api.telegram.org via Tor proxy (bypasses dead agent)

### Source file: `core/modules/hermes-agent/watchdog/platform-agent-watchdog.service`
- systemd oneshot unit, After=docker.service, RemainAfterExit=no, WantedBy=multi-user.target
- ExecStart: `/usr/local/bin/platform-agent-watchdog.sh`

### Source file: `core/modules/hermes-agent/watchdog/platform-agent-watchdog.timer`
- OnBootSec=60s, OnUnitActiveSec=30s, Unit=platform-agent-watchdog.service, WantedBy=timers.target

## Architecture Overview

### Design Decision: New Python module, NOT extension of existing code

**Decision:** Create new standalone `agent_watchdog.py` in `core/modules/hermes-agent/watchdog/`.

**@rationale:** The watchdog is an OS-level independent process that must NOT depend on any agent Python runtime (see source @rationale: "Watchdog is OS-level — must work even if Python runtime is unavailable"). However, the system Python (`/usr/bin/python3`) provided by the OS is always available. The watchdog uses ONLY stdlib + system utilities (curl, docker CLI) — zero agent dependencies. This is fundamentally different from the existing `reconciler.py` or other platform modules that import from `core.internal.*`.

**Contract:** The Python daemon `agent_watchdog.py` MUST:
- Use ONLY Python stdlib (`json`, `subprocess`, `signal`, `logging`, `dataclasses`, `time`, `os`, `pathlib`, `sys`, `argparse`, `urllib`)
- Run on Python 3.10+ (system Python on Ubuntu 22.04+)
- Be directly executable: `#!/usr/bin/env python3` + `chmod +x`
- NOT import from any `core.` package (OS-level independence)
- Be callable from systemd directly: `ExecStart=/usr/bin/python3 /opt/platform/core/modules/hermes-agent/watchdog/agent_watchdog.py`

### Draft Code Graph

```
┌─────────────────────────────────────────────────────────────────┐
│ agent_watchdog.py                                               │
│                                                                 │
│ @dataclass WatchdogConfig                                       │
│   health_url: str, interval: int, timeout: int                  │
│   pending_file: str, secrets_file: str, audit_log: str          │
│   keep_images: int, module_dir: str, compose_file: str          │
│   compose_project: str, telegram_proxy_url: str                 │
│   cb_state_dir: str, cb_services: list[CircuitBreakerService]   │
│   poll_interval: int, curl_max_time: int, curl_tg_max_time: int │
│   +from_env() classmethod                                       │
│                                                                 │
│ @dataclass CircuitBreakerService                                │
│   service_name: str                                             │
│   check_command: list[str]                                      │
│   max_failures: int                                             │
│   window_seconds: int                                           │
│   +from_config_entry(entry: str) classmethod                    │
│                                                                 │
│ @dataclass PendingUpdate                                        │
│   new_version: str, timestamp: str, state: str                  │
│   success_time: str = "", rollback_time: str = "",              │
│   failure_time: str = ""                                        │
│   +from_file(path: str) classmethod                             │
│   +write(path: str) method                                      │
│                                                                 │
│ class CircuitBreaker:                                           │
│   - _state_dir: Path                                            │
│   - _services: list[CircuitBreakerService]                      │
│   +check_all_services() → list[CircuitEvent]                    │
│   - _check_service(svc) → CircuitEvent | None                   │
│   - _read_state(name) → dict                                    │
│   - _write_state(name, state) → None                            │
│   - _run_health_check(cmd: list[str]) → bool                    │
│   - _increment_failures(name, max_f, window) → bool (open?)     │
│   - _circuit_break(name) → None                                 │
│                                                                 │
│ @dataclass CircuitEvent                                         │
│   service: str, event_type: "passed"|"failed"|"opened"|"reset"  │
│   failure_count: int, max_failures: int                         │
│                                                                 │
│ class HealthChecker:                                            │
│   - _url: str, _interval: int, _curl_timeout: int               │
│   +poll(timeout_sec: int, label: str) → bool                    │
│                                                                 │
│ class TelegramNotifier:                                         │
│   - _secrets_file: str, _proxy_url: str                         │
│   - _curl_timeout: int                                          │
│   +send(message: str) → bool                                    │
│   - _load_token() → tuple[str, str] | None                      │
│                                                                 │
│ class DockerManager:                                            │
│   - _compose_file: str, _project_name: str, _module_dir: str    │
│   +compose_down(service: str) → bool                            │
│   +compose_pull() → bool                                        │
│   +compose_up(service: str) → bool                              │
│   +cleanup_old_images(keep: int) → int (removed_count)          │
│   +container_status(name: str) → str                            │
│   +stop_container(name: str) → bool                             │
│                                                                 │
│ class AuditLogger:                                              │
│   - _audit_log: Path                                            │
│   +log(level: str, message: str) → None                         │
│                                                                 │
│ class Watchdog:                                                 │
│   - _config: WatchdogConfig                                     │
│   - _circuit_breaker: CircuitBreaker                            │
│   - _health_checker: HealthChecker                              │
│   - _telegram: TelegramNotifier                                 │
│   - _docker: DockerManager                                      │
│   - _logger: AuditLogger                                        │
│   +run() → int (exit code 0|1)                                  │
│   - _run_circuit_breaker_phase() → None                         │
│   - _run_self_update_phase() → int                              │
│   - _handle_update_success(version) → None                      │
│   - _handle_rollback(version) → int                             │
│   - _handle_rollback_failure(version) → int                     │
│   +setup_signal_handlers() → None                               │
│                                                                 │
│ CLI: main() → argparse → Watchdog(config).run()                 │
└─────────────────────────────────────────────────────────────────┘
```

## Step-by-Step Data Flow

### Phase 1: Circuit Breaker (runs EVERY tick)

```
▶ MAIN tick starts
  │
  ├─ 1. AuditLogger.log("[IMP:8] Watchdog tick started")
  │
  └─ 2. CircuitBreaker.check_all_services()
       │
       └─ for each CircuitBreakerService:
            │
            ├─ _check_service(svc):
            │    │
            │    ├─ _run_health_check(svc.check_command)  ← subprocess.run
            │    │    └─ return bool (exit 0 = healthy)
            │    │
            │    ├─ IF healthy:
            │    │    └─ log "[IMP:8] Health check PASSED" → return CircuitEvent("passed")
            │    │
            │    └─ IF unhealthy:
            │         │
            │         ├─ _increment_failures(name, max_failures, window_seconds):
            │         │    │
            │         │    ├─ _read_state(name) → {"failures":[], "circuit_open": false}
            │         │    │
            │         │    ├─ IF circuit_open AND window expired:
            │         │    │    └─ _write_state(name, {"failures":[], "circuit_open": false})
            │         │    │       → return False (circuit re-closed)
            │         │    │
            │         │    ├─ IF circuit_open:
            │         │    │    └─ return True (circuit stays open)
            │         │    │
            │         │    ├─ Filter failures within window, append now timestamp
            │         │    ├─ circuit_open = len(failures) >= max_failures
            │         │    ├─ _write_state(name, new_state)
            │         │    └─ return circuit_open
            │         │
            │         ├─ IF circuit opened:
            │         │    ├─ _circuit_break(name):
            │         │    │    ├─ DockerManager.stop_container(name)
            │         │    │    └─ TelegramNotifier.send("CIRCUIT BREAKER OPENED for {name}")
            │         │    └─ return CircuitEvent("opened")
            │         │
            │         └─ ELSE:
            │              └─ return CircuitEvent("failed")
```

### Phase 2: Self-Update (only if PENDING_FILE exists)

```
▶ Check PENDING_FILE existence
  │
  ├─ IF NOT exists:
  │    └─ log "[IMP:3] No pending update" → exit 0
  │
  └─ IF exists:
       │
       ├─ 3. PendingUpdate.from_file(path)
       │    └─ Read KEY=VALUE lines
       │    └─ IF state != "pending": log "already handled" → exit 0
       │
       ├─ 4. HealthChecker.poll(timeout=WATCHDOG_TIMEOUT, label="update")
       │    │
       │    └─ LOOP elapsed < timeout:
       │         ├─ urllib.request.urlopen(url, timeout=curl_max_time)
       │         ├─ IF 200: return True
       │         └─ sleep(poll_interval), elapsed += poll_interval
       │
       ├─ IF poll SUCCESS:
       │    │
       │    ├─ 5a. PendingUpdate.write(path, state="success", success_time=now)
       │    ├─ 5b. DockerManager.cleanup_old_images(keep=KEEP_IMAGES)
       │    │      └─ docker image ls → filter hermes-agent → sort by date
       │    │      └─ docker rmi for images beyond keep count
       │    ├─ 5c. log "[IMP:9] Self-update completed successfully"
       │    └─ exit 0
       │
       └─ IF poll FAILURE:
            │
            ├─ 6a. DockerManager.compose_down("hermes-agent")
            ├─ 6b. DockerManager.compose_pull()
            ├─ 6c. DockerManager.compose_up("hermes-agent")
            │
            ├─ 6d. HealthChecker.poll(timeout=WATCHDOG_TIMEOUT, label="rollback")
            │
            ├─ IF rollback poll SUCCESS:
            │    ├─ PendingUpdate.write(path, state="rolled_back", rollback_time=now)
            │    ├─ TelegramNotifier.send("Agent auto-rollback successful")
            │    └─ exit 0
            │
            └─ IF rollback poll FAILURE:
                 ├─ PendingUpdate.write(path, state="rollback_failed", failure_time=now)
                 ├─ DockerManager.container_status("hermes-agent") → log diagnostics
                 ├─ TelegramNotifier.send("CRITICAL: Agent rollback FAILED")
                 └─ exit 1
```

## Detailed Module Structure

### File: `core/modules/hermes-agent/watchdog/agent_watchdog.py`

```python
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
import urllib.request
import urllib.error
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
    check_command: list[str]       # Pre-split command for safe subprocess execution
    check_command_str: str         # Original string for logging
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
    module_dir: str = "/opt/platform/core/modules/hermes-agent"
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
        module_dir = os.environ.get("MODULE_DIR", "/opt/platform/core/modules/hermes-agent")
        
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
                CircuitBreakerService.from_config_entry(
                    "postgres:pg_isready -U postgres -h 127.0.0.1 -t 5:5:300"
                ),
                CircuitBreakerService.from_config_entry(
                    "pgbouncer:pg_isready -h 127.0.0.1 -p 6432 -U postgres -t 3:5:300"
                ),
                CircuitBreakerService.from_config_entry(
                    "redis:redis-cli -h 127.0.0.1 ping:5:300"
                ),
                CircuitBreakerService.from_config_entry(
                    "loki:/usr/bin/loki -version:5:300"
                ),
                CircuitBreakerService.from_config_entry(
                    "prometheus:wget -q -O- http://127.0.0.1:9090/-/healthy:5:300"
                ),
            ]
            cb_services = [s for s in cb_services if s is not None]
        
        return cls(
            health_url=os.environ.get(
                "AGENT_READY_URL", f"http://localhost:{agent_port}/ready"
            ),
            watchdog_timeout=int(os.environ.get("WATCHDOG_TIMEOUT", "90")),
            pending_file=os.environ.get(
                "PENDING_FILE", "/var/lib/platform/agent.update-pending"
            ),
            secrets_file=os.environ.get(
                "SECRETS_FILE", "/run/platform/secrets.env"
            ),
            audit_log=os.environ.get(
                "AUDIT_LOG", "/var/log/platform/watchdog-audit.log"
            ),
            keep_images=int(os.environ.get("KEEP_IMAGES", "3")),
            module_dir=module_dir,
            compose_file=os.environ.get(
                "COMPOSE_FILE", f"{module_dir}/docker-compose.base.yml"
            ),
            compose_project=os.environ.get("COMPOSE_PROJECT", "hermes-agent"),
            agent_port=agent_port,
            cb_state_dir=os.environ.get(
                "CIRCUIT_BREAKER_STATE_DIR", "/var/lib/platform/watchdog"
            ),
            cb_services=cb_services,
            poll_interval=int(os.environ.get("POLL_INTERVAL", "5")),
            curl_max_time=int(os.environ.get("CURL_MAX_TIME", "3")),
            curl_tg_max_time=int(os.environ.get("CURL_TG_MAX_TIME", "30")),
            telegram_proxy_url=os.environ.get(
                "TELEGRAM_PROXY_URL", "http://127.0.0.1:8118"
            ),
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
    def _increment_failures(
        self, service_name: str, max_failures: int, window_seconds: int
    ) -> bool:
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
        failures = [
            f for f in state.get("failures", [])
            if now - f < window_seconds
        ]
        failures.append(now)
        is_open = len(failures) >= max_failures
        
        new_state = {
            "failures": failures,
            "circuit_open": is_open,
        }
        self._write_state(service_name, new_state)
        
        logger.info(
            "[IMP:8][cb:%s] Failure count: %d/%d in %ds window",
            service_name, len(failures), max_failures, window_seconds,
        )
        
        if is_open:
            logger.info(
                "[IMP:9][cb:%s] CIRCUIT BREAKER OPENED — %d failures in %ds",
                service_name, len(failures), window_seconds,
            )
        
        return is_open
    # endregion
    
    # region FUNC__check_service
    def _check_service(
        self,
        svc: CircuitBreakerService,
        docker_manager: "DockerManager",
        telegram: "TelegramNotifier",
    ) -> Optional[CircuitEvent]:
        """Check one service and return circuit event or None."""
        logger.info(
            "[IMP:8][cb:%s] Checking health via: %s",
            svc.service_name, svc.check_command_str,
        )
        
        if self._run_health_check(svc.check_command):
            logger.info("[IMP:8][cb:%s] Health check PASSED", svc.service_name)
            return CircuitEvent(
                service=svc.service_name,
                event_type="passed",
                max_failures=svc.max_failures,
            )
        
        logger.info("[IMP:9][cb:%s] Health check FAILED", svc.service_name)
        
        circuit_opened = self._increment_failures(
            svc.service_name, svc.max_failures, svc.window_seconds
        )
        
        if circuit_opened:
            # Stop the crash-looping container
            logger.info(
                "[IMP:9][cb:%s] CIRCUIT BREAK: Stopping %s due to repeated health failures",
                svc.service_name, svc.service_name,
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
    def check_all_services(
        self, docker_manager: "DockerManager", telegram: "TelegramNotifier"
    ) -> list[CircuitEvent]:
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
            label, self._url, timeout_sec, self._interval,
        )
        elapsed = 0
        while elapsed < timeout_sec:
            try:
                req = urllib.request.Request(self._url, method="GET")
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    if resp.status == 200:
                        logger.info(
                            "[IMP:8][watchdog][%s] /ready returned 200 after %ds",
                            label, elapsed,
                        )
                        return True
            except (urllib.error.URLError, OSError, ValueError):
                pass  # Expected: agent not ready yet
            
            time.sleep(self._interval)
            elapsed += self._interval
        
        logger.info(
            "[IMP:9][watchdog][%s] /ready check timed out after %ds",
            label, timeout_sec,
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
    
    def _load_token(self) -> Optional[tuple[str, str]]:
        """Load TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from secrets file.
        
        Returns (token, chat_id) or None if not found.
        """
        secrets_path = Path(self._secrets_file)
        if not secrets_path.is_file():
            logger.info(
                "[IMP:9][watchdog][telegram] WARNING: Secrets file not found at %s — "
                "cannot send Telegram notification", self._secrets_file,
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
                "[IMP:9][watchdog][telegram] WARNING: TELEGRAM_BOT_TOKEN or "
                "TELEGRAM_CHAT_ID not set in %s", self._secrets_file,
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
                else:
                    logger.info(
                        "[IMP:9][watchdog][telegram] ERROR: Telegram API returned HTTP %d",
                        resp.status,
                    )
                    return False
        except Exception as e:
            logger.info(
                "[IMP:9][watchdog][telegram] ERROR: Telegram API request failed: %s", e,
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
                ["sudo", "docker"] + args,
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
        result = self._run_docker([
            "compose", "-f", self._compose_file,
            "--project-name", self._project,
            "down", service,
        ])
        if result.returncode != 0:
            logger.info(
                "[IMP:9][watchdog][rollback] WARNING: docker compose down returned non-zero — "
                "continuing rollback"
            )
        return result.returncode == 0
    
    def compose_pull(self) -> bool:
        """docker compose pull"""
        logger.info("[IMP:8][watchdog][rollback] Step 5b: pulling image via docker compose pull")
        result = self._run_docker([
            "compose", "-f", self._compose_file,
            "--project-name", self._project,
            "pull",
        ])
        if result.returncode != 0:
            logger.info(
                "[IMP:9][watchdog][rollback] CRITICAL: docker compose pull failed — "
                "image may not be available in registry"
            )
        return result.returncode == 0
    
    def compose_up(self, service: str) -> bool:
        """docker compose up -d <service>"""
        logger.info(
            "[IMP:8][watchdog][rollback] Step 5c: starting %s with previous version "
            "(docker compose up -d)", service,
        )
        result = self._run_docker([
            "compose", "-f", self._compose_file,
            "--project-name", self._project,
            "up", "-d", service,
        ])
        if result.returncode != 0:
            logger.info(
                "[IMP:9][watchdog][rollback] CRITICAL: docker compose up -d failed"
            )
        return result.returncode == 0
    
    def cleanup_old_images(self, keep: int) -> int:
        """Remove old hermes-agent images beyond keep count.
        
        Returns number of images removed.
        """
        logger.info("[IMP:7][watchdog][cleanup] Cleaning old hermes-agent images (keep=%d)", keep)
        
        # List hermes-agent images sorted by creation date (newest first)
        result = self._run_docker([
            "image", "ls",
            "--filter", "reference=hermes-agent",
            "--format", "{{.Repository}}:{{.Tag}} {{.CreatedAt}}",
        ], timeout=30)
        
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
            len(images), min(len(images), keep), removed,
        )
        return removed
    
    def stop_container(self, name: str) -> bool:
        """Stop a Docker container (docker stop, fallback to docker kill)."""
        # Check if container is running
        ps_result = self._run_docker([
            "ps", "--format", "{{.Names}}",
        ], timeout=10)
        
        running_containers = ps_result.stdout.strip().splitlines()
        if name not in running_containers:
            logger.info(
                "[IMP:8][watchdog][cb:%s] Container %s is not running", name, name,
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
                name, name,
            )
            return False
        return True
    
    def container_status(self, name: str) -> str:
        """Get container status for diagnostics."""
        result = self._run_docker([
            "ps", "-a",
            "--filter", f"name={name}",
            "--format", "{{.Names}} {{.Status}} {{.Image}}",
        ], timeout=10)
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
        self._health_checker = HealthChecker(
            config.health_url, config.poll_interval, config.curl_max_time
        )
        self._telegram = TelegramNotifier(
            config.secrets_file, config.telegram_proxy_url, config.curl_tg_max_time
        )
        self._docker = DockerManager(
            config.compose_file, config.compose_project, config.module_dir
        )
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
        self._log.log(
            f"[IMP:9][watchdog][success] New version {update.new_version} is ready and healthy"
        )
        
        # Mark success
        update.state = "success"
        update.success_time = str(int(time.time()))
        update.write(self._config.pending_file)
        
        # Cleanup old images
        self._docker.cleanup_old_images(self._config.keep_images)
        
        self._log.log(
            f"[IMP:9][watchdog][success] Self-update to {update.new_version} "
            f"completed successfully — agent healthy"
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
        self._log.log(
            "[IMP:8][watchdog][rollback] Rollback strategy: down → compose pull → up → re-wait → notify"
        )
        
        # 5a: docker compose down
        self._docker.compose_down("hermes-agent")
        
        # 5b: docker compose pull
        self._docker.compose_pull()
        
        # 5c: docker compose up -d
        self._docker.compose_up("hermes-agent")
        
        # 5d: Re-poll /ready
        self._log.log(
            "[IMP:8][watchdog][rollback] Step 5d: re-polling /ready for previous version"
        )
        rollback_ok = self._health_checker.poll(
            self._config.watchdog_timeout, "rollback"
        )
        
        if rollback_ok:
            self._log.log(
                "[IMP:9][watchdog][rollback_success] Rollback to previous version "
                "SUCCESSFUL — agent is healthy"
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
            
            self._log.log(
                "[IMP:9][watchdog][rollback_success] Rollback complete — node operational"
            )
            self._log.log(
                "[IMP:8][watchdog][main] ===== Watchdog tick completed (rolled_back) ====="
            )
            return 0
        
        return self._handle_rollback_failure(update)
    # endregion
    
    # region FUNC__handle_rollback_failure
    def _handle_rollback_failure(self, update: PendingUpdate) -> int:
        """Critical escalation when rollback also fails."""
        self._log.log(
            "[IMP:9][watchdog][rollback_critical] "
            "====================================================="
        )
        self._log.log(
            f"[IMP:9][watchdog][rollback_critical] CRITICAL: Agent rollback FAILED "
            f"after {self._config.watchdog_timeout}s"
        )
        self._log.log(
            "[IMP:9][watchdog][rollback_critical] Agent is NOT responsive — "
            "manual intervention required"
        )
        self._log.log(
            "[IMP:9][watchdog][rollback_critical] "
            "====================================================="
        )
        
        # Mark failure state
        update.state = "rollback_failed"
        update.failure_time = str(int(time.time()))
        update.write(self._config.pending_file)
        
        # Diagnostic: container status
        self._log.log(
            "[IMP:9][watchdog][rollback_critical] Current hermes-agent container status:"
        )
        status = self._docker.container_status("hermes-agent")
        for line in status.splitlines():
            if line.strip():
                self._log.log(f"[IMP:9][watchdog][rollback_critical]   {line}")
        
        # Critical Telegram notification
        self._telegram.send(
            "\U0001f6a8 CRITICAL: Agent rollback FAILED — manual intervention required"
        )
        
        self._log.log(
            "[IMP:9][watchdog][rollback_critical] Watchdog exiting with code 1 — "
            "node remains operational (agent is non-critical)"
        )
        self._log.log(
            "[IMP:8][watchdog][main] ===== Watchdog tick completed (CRITICAL FAILURE) ====="
        )
        return 1
    # endregion
    
    # region FUNC__run_self_update_phase
    def _run_self_update_phase(self) -> int:
        """Phase 2: Self-update check (only if PENDING_FILE exists).
        
        Returns exit code: 0 = success/skip, 1 = critical failure.
        """
        if not Path(self._config.pending_file).is_file():
            self._log.log(
                "[IMP:3][watchdog][main] No pending update file — "
                "circuit breaker cycle complete"
            )
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
        exit_code = self._run_self_update_phase()
        
        return exit_code
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
```

## Shell Wrapper (exact code)

### File: `core/modules/hermes-agent/watchdog/platform-agent-watchdog.sh`

```bash
#!/bin/bash
# GREP_SUMMARY: platform-agent-watchdog launcher python3 agent_watchdog.py systemd oneshot
# STRUCTURE: ▶ resolve script_dir → ▶ exec python3 agent_watchdog.py "$@" → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin shell launcher for agent_watchdog.py — preserves backward compatibility
##           for any direct shell invocation while delegating all logic to Python.
## @scope    <30 LOC — passes all arguments to Python daemon verbatim.
## @invariants
##   - Zero business logic — pure delegation
##   - Zero inline python3 calls
##   - exec replaces shell process (no subshell overhead)
##   - Same exit code as Python daemon
## @rationale Shell wrapper exists for backward compatibility with existing systemd unit
##            paths and any manual invocations from operator CLI.
# endregion MODULE_CONTRACT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/agent_watchdog.py" "$@"
```

## Systemd Unit Changes

### File: `core/modules/hermes-agent/watchdog/platform-agent-watchdog.service`

```ini
# GREP_SUMMARY: platform-agent-watchdog systemd oneshot unit hermes-agent self-update monitor readiness rollback external-controller
# STRUCTURE: ▶ timer triggers → ▶ ExecStart python3 agent_watchdog.py → ▶ poll /ready → ⊕ success | ◇ rollback → ⎋ exit
# region MODULE_CONTRACT [DOMAIN(DevOps): Agent self-update watchdog; CONCEPT(Watchdog): external controller for agent; TECH(Systemd): oneshot unit]
## @purpose  Systemd oneshot unit that executes agent_watchdog.py (Python daemon) to monitor agent self-update readiness.
## @scope    Phase 04: external watchdog for hermes-agent self-update; triggered by platform-agent-watchdog.timer.
## @invariants
##   - Type: oneshot — runs once per activation, exits after completion
##   - RemainAfterExit: no — unit is inactive between timer ticks
##   - After: docker.service — ensures Docker daemon is running before script executes
##   - WantedBy: multi-user.target — starts at normal system boot
##   - Watchdog is external to agent process — survives agent crash/restart
##   - WatchdogTimeoutSec=0 — managed internally by Python daemon timeout
## @rationale ExecStart updated from bash script to Python daemon per DevPlan 075.
##            Python daemon handles all business logic; shell is thin launcher only.
## @changes 2026-07-25 | ExecStart changed from /usr/local/bin/platform-agent-watchdog.sh to Python daemon
# endregion MODULE_CONTRACT

[Unit]
Description=Platform Agent Watchdog — hermes-agent self-update monitor
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/platform/core/modules/hermes-agent/watchdog/agent_watchdog.py
RemainAfterExit=no
WatchdogSec=0

[Install]
WantedBy=multi-user.target
```

### No changes to: `platform-agent-watchdog.timer`
Timer remains unchanged — it triggers the service unit, which now calls Python.

## Configuration DRY

| Config Value | Shell (old) | Python (new) | Shared env var |
|---|---|---|---|
| WATCHDOG_TIMEOUT | `$WATCHDOG_TIMEOUT:-90` | `os.environ.get("WATCHDOG_TIMEOUT", "90")` | WATCHDOG_TIMEOUT |
| AGENT_PORT | `$AGENT_PORT:-9119` | `os.environ.get("AGENT_PORT", "9119")` | AGENT_PORT |
| AGENT_READY_URL | `$AGENT_READY_URL:-http://...` | derived from AGENT_PORT | AGENT_READY_URL |
| PENDING_FILE | `$PENDING_FILE:-/var/lib/...` | `os.environ.get("PENDING_FILE", "/var/lib/...")` | PENDING_FILE |
| SECRETS_FILE | `$SECRETS_FILE:-/run/...` | `os.environ.get("SECRETS_FILE", "/run/...")` | SECRETS_FILE |
| AUDIT_LOG | `$AUDIT_LOG:-/var/log/...` | `os.environ.get("AUDIT_LOG", "/var/log/...")` | AUDIT_LOG |
| KEEP_IMAGES | `$KEEP_IMAGES:-3` | `os.environ.get("KEEP_IMAGES", "3")` | KEEP_IMAGES |
| MODULE_DIR | `$MODULE_DIR:-/opt/...` | `os.environ.get("MODULE_DIR", "/opt/...")` | MODULE_DIR |
| CIRCUIT_BREAKER_SERVICES | Bash array | Space-separated string | CIRCUIT_BREAKER_SERVICES |
| CIRCUIT_BREAKER_STATE_DIR | `$CIRCUIT_BREAKER_STATE_DIR:-/var/lib/...` | `os.environ.get("CIRCUIT_BREAKER_STATE_DIR", "/var/lib/...")` | CIRCUIT_BREAKER_STATE_DIR |

All config values are read from environment — single source of truth. The systemd unit can inject env vars via `Environment=` directives.

## TRAP Annotations Preserved

| Source Line | TRAP | Preservation |
|---|---|---|
| watchdog.sh:243 | `TRAP[DECISION]` — eval replaced with array-based execution | Python: `check_command` is already `list[str]`, subprocess.run uses list form (no shell injection) |

## Test Specification

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_agent_watchdog.py` | `test_config_from_env_defaults` | Verify WatchdogConfig.from_env() returns correct defaults | `agent_watchdog.WatchdogConfig` |
| `tests/unit/test_agent_watchdog.py` | `test_config_from_env_overrides` | Verify env var overrides are parsed correctly | `agent_watchdog.WatchdogConfig` |
| `tests/unit/test_agent_watchdog.py` | `test_cb_service_from_config_entry` | Parse "postgres:pg_isready -h 127.0.0.1:5:300" → CircuitBreakerService | `agent_watchdog.CircuitBreakerService` |
| `tests/unit/test_agent_watchdog.py` | `test_cb_service_from_config_entry_invalid` | Parse invalid entry → None | `agent_watchdog.CircuitBreakerService` |
| `tests/unit/test_agent_watchdog.py` | `test_circuit_breaker_read_write_state` | Write state → read back → verify JSON integrity | `agent_watchdog.CircuitBreaker` |
| `tests/unit/test_agent_watchdog.py` | `test_circuit_breaker_closed_to_open` | 5 failures in 300s window → circuit opens | `agent_watchdog.CircuitBreaker` |
| `tests/unit/test_agent_watchdog.py` | `test_circuit_breaker_window_expiry_reset` | Circuit open, window expired → auto-reset to closed | `agent_watchdog.CircuitBreaker` |
| `tests/unit/test_agent_watchdog.py` | `test_circuit_breaker_failures_filtered_by_window` | Old failures outside window → filtered out, count correct | `agent_watchdog.CircuitBreaker` |
| `tests/unit/test_agent_watchdog.py` | `test_health_checker_poll_success` | Mock HTTP 200 → poll returns True | `agent_watchdog.HealthChecker` |
| `tests/unit/test_agent_watchdog.py` | `test_health_checker_poll_timeout` | All requests fail → poll returns False after timeout | `agent_watchdog.HealthChecker` |
| `tests/unit/test_agent_watchdog.py` | `test_pending_update_read_write` | Write PendingUpdate → read back → fields match | `agent_watchdog.PendingUpdate` |
| `tests/unit/test_agent_watchdog.py` | `test_pending_update_missing_file` | from_file on nonexistent path → None | `agent_watchdog.PendingUpdate` |
| `tests/unit/test_agent_watchdog.py` | `test_telegram_notifier_no_secrets_file` | Secrets file missing → send returns False (no crash) | `agent_watchdog.TelegramNotifier` |

## $TASKS

### T1: Create agent_watchdog.py (all classes + CLI)
- **File:** `core/modules/hermes-agent/watchdog/agent_watchdog.py` (NEW)
- **Content:** All classes as specified in Draft Code Graph: WatchdogConfig, CircuitBreakerService, PendingUpdate, CircuitEvent, AuditLogger, CircuitBreaker, HealthChecker, TelegramNotifier, DockerManager, Watchdog, main()
- **Dependencies:** None
- **Complexity:** 9/10
- **Acceptance:** File exists, all classes defined, all function signatures match spec, `python3 -c "import agent_watchdog"` succeeds

### T2: Create shell launcher
- **File:** `core/modules/hermes-agent/watchdog/platform-agent-watchdog.sh` (OVERWRITE)
- **Content:** Exact shell wrapper from spec (<30 LOC, zero inline python3)
- **Dependencies:** T1 (agent_watchdog.py must exist)
- **Complexity:** 1/10
- **Acceptance:** `wc -l < 30`, `grep "python3 -c"` returns nothing, `grep "PYEOF"` returns nothing

### T3: Update systemd service unit
- **File:** `core/modules/hermes-agent/watchdog/platform-agent-watchdog.service` (UPDATE)
- **Content:** Update ExecStart to Python daemon path, add WatchdogSec=0
- **Dependencies:** T1
- **Complexity:** 1/10
- **Acceptance:** ExecStart points to Python daemon, Type=oneshot preserved

### T4: Make agent_watchdog.py executable
- **File:** `core/modules/hermes-agent/watchdog/agent_watchdog.py`
- **Command:** `chmod +x` + `make fix-executable-bit`
- **Dependencies:** T1
- **Complexity:** 1/10

### T5: Write unit tests
- **File:** `tests/unit/test_agent_watchdog.py` (NEW)
- **Content:** All test cases from $TEST_SPEC table (13 tests)
- **Dependencies:** T1
- **Complexity:** 6/10
- **Acceptance:** `python -m pytest tests/unit/test_agent_watchdog.py -v` — all green

### T6: Run gate
- **Command:** `make fix-gate && make gate MODE=fast`
- **Dependencies:** T1-T5
- **Complexity:** 2/10
- **Acceptance:** Gate green, zero regressions

## $PARALLEL_GROUPS

### Wave 1 (independent)
- Tasks: T1
- T1 must complete first (all other tasks depend on it)
- Blocking: T2, T3, T4, T5 all need T1

### Wave 2 (after T1, parallel)
- Tasks: T2, T3, T4, T5
- These can run in parallel (no file conflicts):
  - T2: shell wrapper (different file)
  - T3: systemd unit (different file)
  - T4: chmod (same file as T1 but just chmod)
  - T5: tests (different file)
- Command: `Read DevPlan.md, implement Wave 2: T2, T3, T4, T5`

### Wave 3 (after all)
- Tasks: T6
- Gate must run after all code changes

## Next Steps

### Wave 1
```
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/075-watchdog-python/02-DevPlan.md, implement Wave 1: T1 (create agent_watchdog.py)
```

### Wave 2
```
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/075-watchdog-python/02-DevPlan.md, implement Wave 2: T2, T3, T4, T5
```

$END_DEVPLAN
