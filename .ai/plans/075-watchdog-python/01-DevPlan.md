# DevPlan 075: platform-agent-watchdog.sh → Python Daemon

$ARTIFACT_CONTRACT
PURPOSE: Migrate platform-agent-watchdog.sh (549 LOC, 5 inline python3 calls, circuit breaker state machine) to a Python daemon. Shell is dangerous for a production watchdog.
DESCRIPTION: core/modules/hermes-agent/watchdog/platform-agent-watchdog.sh monitors the hermes-agent process, implements a circuit breaker pattern, manages JSON state files, and performs healthcheck + restart orchestration. Contains 5 inline python3 calls for JSON state management. The watchdog's core logic (state machine, circuit breaker, health evaluation) must be in Python for reliability.
RATIONALE: A watchdog implemented in shell is a single point of failure — if bash itself crashes or a subshell exits unexpectedly, the watchdog dies silently. Python daemon with proper signal handling and structured logging is the correct production choice. The circuit breaker state machine is non-trivial business logic that should not live in shell.
ACCEPTANCE_CRITERIA:
  - `core/modules/hermes-agent/watchdog/agent_watchdog.py` — Python daemon
  - `platform-agent-watchdog.sh` — reduced to <30 LOC launcher
  - Zero inline `python3 -c` calls in watchdog shell
  - Circuit breaker: 3 failures → cooldown → retry with backoff
  - Healthcheck: HTTP health endpoint poll with configurable interval
  - systemd service unit updated to call Python daemon
  - `tests/unit/test_agent_watchdog.py`
  - `make gate MODE=fast` — green
IMPLEMENTS: Wave 6B — Tier 1 shell → Python migration
IMPACTS:
  - core/modules/hermes-agent/watchdog/agent_watchdog.py (new)
  - core/modules/hermes-agent/watchdog/platform-agent-watchdog.sh (reduce)
  - core/modules/hermes-agent/watchdog/platform-agent-watchdog.service (update ExecStart)
  - tests/unit/test_agent_watchdog.py (new)
REQUIRES: None (can run parallel to 070-074)

## Tasks

### T1: Implement Python watchdog daemon
- `agent_watchdog.py`:
  - `WatchdogConfig` dataclass: health_url, interval, failure_threshold, cooldown_seconds, backoff_multiplier
  - `CircuitBreaker` class: track failures, transition OPEN→HALF_OPEN→CLOSED
  - `HealthChecker.poll(url, timeout)` → bool
  - `Watchdog.run()`: main loop with signal handlers (SIGTERM, SIGINT)
  - Structured logging with IMP levels matching current shell

### T2: Circuit breaker implementation
- States: CLOSED (normal), OPEN (failures ≥ threshold, stop restarting), HALF_OPEN (cooldown expired, probe)
- JSON state file for persistence across daemon restarts
- Configurable via environment variables or config file

### T3: Update systemd service
- platform-agent-watchdog.service: ExecStart → `/usr/bin/python3 /opt/platform/core/modules/hermes-agent/watchdog/agent_watchdog.py`
- Restart=always, RestartSec=10
- User=platform (not root)

### T4: Create thin shell launcher
- platform-agent-watchdog.sh → exec python3 agent_watchdog.py "$@"
- Keep <30 LOC
- Preserve backward compatibility for any direct shell invocation

### T5: Unit tests
- `tests/unit/test_agent_watchdog.py`:
  - `test_circuit_breaker_transitions` — CLOSED→OPEN→HALF_OPEN→CLOSED
  - `test_health_check_success` — mock HTTP 200
  - `test_health_check_failure` — mock connection refused
  - `test_cooldown_timer` — verify no restart during cooldown
  - `test_backoff_multiplier` — verify increasing intervals

### T6: Gate
- `make fix-gate && make gate MODE=fast` — green
