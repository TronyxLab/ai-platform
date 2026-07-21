# GREP_SUMMARY: test-smoke-redis smoke requires_docker redis-cli ping config-get no-persistence allkeys-lfu no-host-ports shared-cache-net compose-up
# STRUCTURE: ⚡ [requires_docker + smoke] → ▶ [redis_compose fixture] → ┬─ test_redis_smoke_ping(◇ exec redis-cli ping → PONG) → ┬─ test_redis_config_appendonly(◇ CONFIG GET appendonly → no) → ┬─ test_redis_config_save(◇ CONFIG GET save → "") → ┬─ test_redis_config_maxmemory_policy(◇ CONFIG GET maxmemory-policy → allkeys-lfu) → ┬─ test_redis_no_host_ports(◇ docker inspect → no published 6379) → ⎋ teardown down -v
# region MODULE_CONTRACT
## @purpose  Smoke tests for redis module — validates cache-only configuration via live Docker compose.
##           Checks: connectivity (PONG), persistence off (appendonly=no, save=""),
##           eviction policy (allkeys-lfu), and network isolation (no host ports).
## @scope    Docker-dependent tests (pytest.mark.smoke + pytest.mark.requires_docker).
##           Requires Docker daemon. Module-scoped fixture manages compose lifecycle.
## @invariants
##   - Module-scoped fixture manages compose lifecycle: pre-cleanup → up → tests → down
##   - Stops any existing wave-redis project before starting wave-redis-smoke
##   - Creates shared-cache-net if absent (cleans up only if created)
##   - All tests use docker compose exec or docker inspect (no in-container tools needed)
##   - Container name: redis-test (from test.yml override)
##   - Compose project: wave-redis-smoke (isolated from production and live-verification)
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale Smoke tests validate the actual Docker container behavior — compose config
##            parsing alone cannot verify runtime config (CONFIG GET) and network isolation
##            (no host ports). Module-scoped fixture ensures isolation and cleanup.
## @usecases — Wave T5.3 (redis) acceptance: cache-only contract verified at runtime
# endregion MODULE_CONTRACT

import json
import logging
import subprocess
from pathlib import Path

import pytest
from _conftest.ldd import _print_ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REDIS_MODULE = _PROJECT_ROOT / "core" / "modules" / "redis"
_COMPOSE_BASE = _REDIS_MODULE / "docker-compose.base.yml"
_COMPOSE_TEST = _REDIS_MODULE / "docker-compose.test.yml"

# Compose project names
_WAVE_PROJECT = "wave-redis"  # existing production/live-verification project
_SMOKE_PROJECT = "wave-redis-smoke"  # isolated smoke test project

# Default test container name (from test.yml override)
_CONTAINER_NAME = "redis-test"

# Timeouts
_COMPOSE_UP_TIMEOUT = 90  # --wait-timeout 60 + buffer
_COMPOSE_DOWN_TIMEOUT = 20  # --timeout 5 + buffer
_EXEC_TIMEOUT = 15
_INSPECT_TIMEOUT = 15
_NETWORK_CREATE_TIMEOUT = 15


# region FIXTURES
## @purpose — Module-scoped compose lifecycle fixture for redis smoke tests.
##            Pre-cleans wave-redis project, starts wave-redis-smoke, yields,
##            tears down on completion.


@pytest.fixture(scope="module")
def redis_compose():
    """Module-scoped fixture: manage docker compose lifecycle for redis smoke tests.

    ## @purpose — Start redis container with cache-only config, yield for tests,
    ##            tear down and clean up after all tests in module.
    ## @io — ⇥ None → ⎋ dict (compose project name, container name for tests)
    ## @complexity — O(1) — startup/teardown, no loops
    ## @invariants
    ##   - Stops any running wave-redis project before starting smoke project
    ##   - Creates shared-cache-net Docker network if absent (removes if created)
    ##   - docker compose up -d --wait with 60s timeout
    ##   - Teardown: docker compose down -v --remove-orphans --timeout 5
    ##   - Only removes Docker network if fixture created it (flag created_by_us)
    """
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:7][redis_compose][setup] Starting redis smoke fixture")

    # ── Step 1: Stop any running wave-redis project ───────────────────────────
    _logger.info("[IMP:7][redis_compose][setup] Stopping existing wave-redis project")
    down_args = [
        "docker",
        "compose",
        "-p",
        _WAVE_PROJECT,
        "-f",
        str(_COMPOSE_BASE),
        "-f",
        str(_COMPOSE_TEST),
        "down",
        "-v",
        "--remove-orphans",
        "--timeout",
        "5",
    ]
    env_down = {**subprocess.os.environ, "COMPOSE_PROFILES": "redis"}
    try:
        result = subprocess.run(
            down_args,
            capture_output=True,
            text=True,
            timeout=_COMPOSE_DOWN_TIMEOUT,
            env=env_down,
        )
        _logger.info(
            "[IMP:8][redis_compose][setup] Stopped wave-redis: rc=%d stderr=%s",
            result.returncode,
            result.stderr.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        _logger.warning("[IMP:8][redis_compose][setup] wave-redis down timed out — continuing")

    # ── Step 2: Ensure test-shared-cache-net exists ────────────────────────────
    # ⚠️ TRAP[BUG] · 2026-07-21 · MED · Test compose (test.yml) overrides base network
    # to test-shared-cache-net — the test MUST create the test-prefixed network, not
    # the base shared-cache-net, or compose fails with "network not found".
    created_by_us = False
    _logger.info("[IMP:7][redis_compose][setup] Checking test-shared-cache-net")
    try:
        inspect_result = subprocess.run(
            ["docker", "network", "inspect", "test-shared-cache-net"],
            capture_output=True,
            text=True,
            timeout=_NETWORK_CREATE_TIMEOUT,
        )
        if inspect_result.returncode != 0:
            _logger.info("[IMP:8][redis_compose][setup] Creating test-shared-cache-net")
            subprocess.run(
                ["docker", "network", "create", "test-shared-cache-net"],
                capture_output=True,
                text=True,
                timeout=_NETWORK_CREATE_TIMEOUT,
                check=True,
            )
            created_by_us = True
            _logger.info("[IMP:9][redis_compose][setup] Created shared-cache-net")
        else:
            _logger.info("[IMP:8][redis_compose][setup] shared-cache-net already exists")
    except subprocess.TimeoutExpired:
        _logger.error("[IMP:9][redis_compose][setup] Timeout checking/creating shared-cache-net")
        pytest.fail("Failed to ensure shared-cache-net exists")

    # ── Step 3: Remove stale container from shared stack ──────────────────────
    _logger.info("[IMP:8][fixture][setup] Cleaning stale container: redis-test")
    subprocess.run(
        ["docker", "rm", "-f", "redis-test"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    # ── Step 4: Start redis compose ───────────────────────────────────────────
    _logger.info("[IMP:7][redis_compose][setup] Starting wave-redis-smoke compose")
    compose_up_args = [
        "docker",
        "compose",
        "-p",
        _SMOKE_PROJECT,
        "-f",
        str(_COMPOSE_BASE),
        "-f",
        str(_COMPOSE_TEST),
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "60",
    ]
    env_up = {**subprocess.os.environ, "COMPOSE_PROFILES": "redis"}

    try:
        up_result = subprocess.run(
            compose_up_args,
            capture_output=True,
            text=True,
            timeout=_COMPOSE_UP_TIMEOUT,
            env=env_up,
        )
        _logger.info(
            "[IMP:8][redis_compose][setup] compose up rc=%d",
            up_result.returncode,
        )
        if up_result.returncode != 0:
            # ── Diagnostic: collect logs ──────────────────────────
            log_args = [
                "docker",
                "compose",
                "-p",
                _SMOKE_PROJECT,
                "-f",
                str(_COMPOSE_BASE),
                "-f",
                str(_COMPOSE_TEST),
                "logs",
                "--tail",
                "50",
                "--no-color",
            ]
            logs_result = subprocess.run(
                log_args,
                capture_output=True,
                text=True,
                timeout=30,
                env=env_up,
            )
            _logger.error(
                "[IMP:9][redis_compose][setup] Compose up failed — rc=%d\nstderr: %s\nlogs: %s",
                up_result.returncode,
                up_result.stderr.strip()[-300:],
                (logs_result.stdout or logs_result.stderr).strip()[-300:],
            )
            pytest.fail(f"docker compose up failed with rc={up_result.returncode}")

        _logger.info("[IMP:9][redis_compose][setup] wave-redis-smoke started successfully")
    except subprocess.TimeoutExpired:
        _logger.error("[IMP:9][redis_compose][setup] compose up timed out after %ds", _COMPOSE_UP_TIMEOUT)
        pytest.fail(f"docker compose up timed out after {_COMPOSE_UP_TIMEOUT}s")

    # ── Yield test context ────────────────────────────────────────────────────
    yield {
        "project": _SMOKE_PROJECT,
        "container": _CONTAINER_NAME,
        "compose_base": str(_COMPOSE_BASE),
        "compose_test": str(_COMPOSE_TEST),
    }

    # ── Teardown: docker compose down ─────────────────────────────────────────
    _logger.info("[IMP:7][redis_compose][teardown] Tearing down wave-redis-smoke")
    down_smoke_args = [
        "docker",
        "compose",
        "-p",
        _SMOKE_PROJECT,
        "-f",
        str(_COMPOSE_BASE),
        "-f",
        str(_COMPOSE_TEST),
        "down",
        "-v",
        "--remove-orphans",
        "--timeout",
        "5",
    ]
    try:
        down_result = subprocess.run(
            down_smoke_args,
            capture_output=True,
            text=True,
            timeout=_COMPOSE_DOWN_TIMEOUT,
            env=env_up,
        )
        _logger.info(
            "[IMP:8][redis_compose][teardown] compose down rc=%d: %s",
            down_result.returncode,
            down_result.stderr.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        _logger.warning("[IMP:8][redis_compose][teardown] compose down timed out — continuing")

    # ── Remove test-shared-cache-net only if we created it ─────────────────────
    if created_by_us:
        _logger.info("[IMP:7][redis_compose][teardown] Removing test-shared-cache-net (created by fixture)")
        try:
            subprocess.run(
                ["docker", "network", "rm", "test-shared-cache-net"],
                capture_output=True,
                text=True,
                timeout=_NETWORK_CREATE_TIMEOUT,
            )
            _logger.info("[IMP:9][redis_compose][teardown] test-shared-cache-net removed")
        except subprocess.TimeoutExpired:
            _logger.warning("[IMP:8][redis_compose][teardown] Failed to remove test-shared-cache-net")

    _logger.info("[IMP:9][redis_compose][teardown] Fixture teardown complete")


# endregion FIXTURES


# region REDIS_SMOKE_TESTS
## @purpose — Cache-only contract verification at runtime via docker compose exec.
##            All tests use @pytest.mark.smoke + @pytest.mark.requires_docker.
## @scope    Live container tests — require Docker daemon and compose running.
## @invariants
##   - All tests depend on redis_compose fixture (module-scoped)
##   - Tests use docker compose exec for in-container redis-cli commands
##   - Port check uses docker container inspect (not compose)
##   - Each test asserts IMP:9 presence via ldd pattern in caplog

# ── Helper ────────────────────────────────────────────────────────────────────


def _compose_exec(redis_compose: dict, cmd: list[str], timeout: int = _EXEC_TIMEOUT) -> subprocess.CompletedProcess:
    """Run docker compose exec against the redis service.

    ## @purpose — Centralised subprocess runner for compose exec commands.
    ## @io — ⇥ redis_compose fixture dict, cmd list → ⎋ CompletedProcess
    ## @complexity — O(1)
    """
    exec_args = [
        "docker",
        "compose",
        "-p",
        redis_compose["project"],
        "-f",
        redis_compose["compose_base"],
        "-f",
        redis_compose["compose_test"],
        "exec",
        "-T",
        "redis",
        *cmd,
    ]
    env = {**subprocess.os.environ, "COMPOSE_PROFILES": "redis"}
    return subprocess.run(
        exec_args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


# ── Test 1: Ping ──────────────────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_redis_smoke_ping(redis_compose, caplog):
    """Redis responds to PING with PONG.

    ## @purpose — Verify basic redis connectivity via redis-cli PING.
    ## @io — ⇥ redis_compose fixture → ⎋ assert PONG in output
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_redis][smoke_ping] START")

        result = _compose_exec(redis_compose, ["redis-cli", "-h", "127.0.0.1", "ping"])
        stdout = result.stdout.strip()

        logger.info(
            "[IMP:8][test_redis][smoke_ping] rc=%d stdout=%s stderr=%s",
            result.returncode,
            stdout,
            result.stderr.strip()[:100],
        )

        is_pong = "PONG" in stdout
        logger.critical(
            "[IMP:9][test_redis][smoke_ping] ASSERT: PONG in output=%s",
            is_pong,
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found in smoke_ping"

        assert result.returncode == 0, f"redis-cli ping exited with {result.returncode}: {result.stderr.strip()}"
        assert is_pong, f"redis-cli ping did not return PONG, got: '{stdout}'"


# ── Test 2: CONFIG GET appendonly ────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_redis_config_appendonly(redis_compose, caplog):
    """CONFIG GET appendonly returns 'no' — persistence disabled.

    ## @purpose — Verify AOF persistence is off (cache-only contract).
    ## @io — ⇥ redis_compose fixture → ⎋ assert 'no' in output
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_redis][config_appendonly] START")

        result = _compose_exec(redis_compose, ["redis-cli", "-h", "127.0.0.1", "CONFIG", "GET", "appendonly"])
        stdout = result.stdout.strip()

        logger.info(
            "[IMP:8][test_redis][config_appendonly] rc=%d stdout=%s",
            result.returncode,
            stdout,
        )

        # CONFIG GET returns key\\nvalue — check value line
        lines = stdout.splitlines()
        has_value_no = any(line.strip() == "no" for line in lines)

        logger.critical(
            "[IMP:9][test_redis][config_appendonly] ASSERT: appendonly=no => %s",
            has_value_no,
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 log found in config_appendonly"

        assert result.returncode == 0, f"CONFIG GET appendonly failed: {result.stderr.strip()}"
        assert has_value_no, f"CONFIG GET appendonly must return 'no', got: {stdout!r}"


# ── Test 3: CONFIG GET save ──────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_redis_config_save(redis_compose, caplog):
    """CONFIG GET save returns empty — RDB persistence disabled.

    ## @purpose — Verify RDB persistence is off (save "" — cache-only contract).
    ## @io — ⇥ redis_compose fixture → ⎋ assert empty value
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_redis][config_save] START")

        result = _compose_exec(redis_compose, ["redis-cli", "-h", "127.0.0.1", "CONFIG", "GET", "save"])
        stdout = result.stdout.strip()

        logger.info(
            "[IMP:8][test_redis][config_save] rc=%d stdout=%r",
            result.returncode,
            stdout,
        )

        # CONFIG GET save returns key "save" on first line, value on second
        # When save is disabled (""), second line is empty or contains ""
        lines = stdout.splitlines()
        # Expect 2 lines: line0 = "save", line1 = "" or nothing after "save"
        value_is_empty = True
        if len(lines) >= 2:
            # Second line should be empty or contain just quotes
            value_line = lines[1].strip().strip('"')
            if value_line:
                value_is_empty = False
        # If only 1 line "save", the value is considered empty (Redis returns just key with no value)
        # If just no output or only save, it means save is off

        logger.critical(
            "[IMP:9][test_redis][config_save] ASSERT: save value is empty => %s",
            value_is_empty,
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 log found in config_save"

        assert result.returncode == 0, f"CONFIG GET save failed: {result.stderr.strip()}"
        assert value_is_empty, f"CONFIG GET save must return empty value (save disabled), got: {stdout!r}"


# ── Test 4: CONFIG GET maxmemory-policy ─────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_redis_config_maxmemory_policy(redis_compose, caplog):
    """CONFIG GET maxmemory-policy returns 'allkeys-lfu'.

    ## @purpose — Verify eviction policy is allkeys-lfu (cache-only contract).
    ## @io — ⇥ redis_compose fixture → ⎋ assert 'allkeys-lfu' in output
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_redis][config_maxmemory_policy] START")

        result = _compose_exec(redis_compose, ["redis-cli", "-h", "127.0.0.1", "CONFIG", "GET", "maxmemory-policy"])
        stdout = result.stdout.strip()

        logger.info(
            "[IMP:8][test_redis][config_maxmemory_policy] rc=%d stdout=%s",
            result.returncode,
            stdout,
        )

        has_allkeys_lfu = "allkeys-lfu" in stdout

        logger.critical(
            "[IMP:9][test_redis][config_maxmemory_policy] ASSERT: allkeys-lfu in output=%s",
            has_allkeys_lfu,
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 log found in config_maxmemory_policy"

        assert result.returncode == 0, f"CONFIG GET maxmemory-policy failed: {result.stderr.strip()}"
        assert has_allkeys_lfu, f"CONFIG GET maxmemory-policy must return 'allkeys-lfu', got: {stdout!r}"


# ── Test 5: No host ports ────────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_redis_no_host_ports(redis_compose, caplog):
    """Redis port 6379 is NOT published to host (internal network only).

    ## @purpose — Verify network isolation: redis should not expose ports to host.
    ## @io — ⇥ redis_compose fixture → ⎋ assert no HostPorts for 6379
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_redis][no_host_ports] START")

        container = redis_compose["container"]
        inspect_args = [
            "docker",
            "container",
            "inspect",
            container,
            "--format",
            "{{json .NetworkSettings.Ports}}",
        ]
        env = {**subprocess.os.environ, "COMPOSE_PROFILES": "redis"}

        result = subprocess.run(
            inspect_args,
            capture_output=True,
            text=True,
            timeout=_INSPECT_TIMEOUT,
            env=env,
        )

        logger.info(
            "[IMP:8][test_redis][no_host_ports] rc=%d stdout=%s",
            result.returncode,
            result.stdout.strip()[:300],
        )

        assert result.returncode == 0, f"docker inspect failed: {result.stderr.strip()}"

        ports_json = result.stdout.strip()
        try:
            ports_dict = json.loads(ports_json)
        except json.JSONDecodeError as exc:
            logger.critical(
                "[IMP:9][test_redis][no_host_ports] ASSERT failed: JSON parse error=%s",
                exc,
            )
            pytest.fail(f"Failed to parse docker inspect Ports JSON: {exc}")

        # Check that 6379/tcp has no host port mappings
        tcp_6379 = ports_dict.get("6379/tcp")
        port_published = tcp_6379 is not None and len(tcp_6379) > 0

        logger.critical(
            "[IMP:9][test_redis][no_host_ports] ASSERT: port 6379 published to host=%s (ports=%s)",
            port_published,
            ports_dict,
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 log found in no_host_ports"

        assert not port_published, (
            f"Redis port 6379 must NOT be published to host. "
            f"Found: {ports_dict}. "
            f"Cache-only redis must be internal network only."
        )


# endregion REDIS_SMOKE_TESTS
