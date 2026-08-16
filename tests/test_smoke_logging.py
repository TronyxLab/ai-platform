# GREP_SUMMARY: test-smoke-logging smoke requires_docker loki buildinfo ready compose-up
# STRUCTURE: ⚡ [requires_docker + smoke] → ▶ [logging_compose fixture] → ┬─ test_loki_ready(◇ /ready → 200) → ┬─ test_loki_buildinfo(◇ /loki/api/v1/status/buildinfo → 200 + "version") → ⎋ teardown down
# region MODULE_CONTRACT
## @purpose  Smoke tests for logging module — validates Loki health and buildinfo
##           endpoints via live Docker compose.
##           Checks: Loki /ready, Loki /loki/api/v1/status/buildinfo.
## @scope    Docker-dependent tests (pytest.mark.smoke + pytest.mark.requires_docker).
##           Requires Docker daemon. Module-scoped fixture manages compose lifecycle.
## @invariants
##   - Module-scoped fixture manages compose lifecycle: pre-cleanup → up → tests → down
##   - Stops any existing wave-logging project before starting wave-logging-smoke
##   - Creates observability-net Docker network if absent
##   - All tests use HTTP GET to localhost (published port 13100)
##   - Container names: loki-test, alloy-test (from test.yml override)
##   - Compose project: wave-logging-smoke (isolated)
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale Smoke tests validate the actual Docker container behavior — port binding,
##            healthcheck execution, and service readiness. HTTP-level validation
##            confirms the services are operational from the host perspective.
## @usecases — Wave T5.8 (logging) acceptance: Loki health + buildinfo verified
# endregion MODULE_CONTRACT

import logging
import subprocess
import time

import pytest
import requests
from _conftest.compose import _compose_file_args  # DevPlan 170 W8: canonical compose module
from _conftest.ldd import _print_ldd_trajectory

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_LOGGING_MODULE = repo_root() / "core" / "modules" / "logging"
_COMPOSE_BASE = _LOGGING_MODULE / "docker-compose.base.yml"
_COMPOSE_TEST = _LOGGING_MODULE / "docker-compose.test.yml"

# Compose project names
_WAVE_PROJECT = "wave-logging"
_SMOKE_PROJECT = "wave-logging-smoke"

# Ports (test.yml overrides Loki to 127.0.0.1:13100:3100)
_LOKI_PORT = 13100

# Endpoints
_LOKI_READY_URL = f"http://127.0.0.1:{_LOKI_PORT}/ready"
_LOKI_BUILDINFO_URL = f"http://127.0.0.1:{_LOKI_PORT}/loki/api/v1/status/buildinfo"

# Timeouts
_COMPOSE_UP_TIMEOUT = 120
_COMPOSE_DOWN_TIMEOUT = 20
_HTTP_TIMEOUT = 10
_NETWORK_CREATE_TIMEOUT = 15

# Required external networks
_EXTERNAL_NETWORKS = ["test-observability-net"]


# region FIXTURES
## @purpose — Module-scoped compose lifecycle fixture for logging smoke tests.
##            Pre-cleans wave-logging project, starts wave-logging-smoke,
##            yields, tears down on completion.


def _ensure_external_network(net: str, created_by_us: set) -> None:
    """docker network inspect/create при отсутствии сети (PLW0717-хелпер).

    ## @io — ⇥ net, created_by_us (мутируется) → ⎋ None
    ## @complexity O(1) — один subprocess
    """
    inspect_result = subprocess.run(
        ["docker", "network", "inspect", net],
        capture_output=True,
        text=True,
        timeout=_NETWORK_CREATE_TIMEOUT,
        check=False,
    )
    if inspect_result.returncode != 0:
        logger.info("[IMP:8][logging_compose][setup] Creating network %s", net)
        subprocess.run(
            ["docker", "network", "create", net],
            capture_output=True,
            text=True,
            timeout=_NETWORK_CREATE_TIMEOUT,
            check=True,
        )
        created_by_us.add(net)
        logger.info("[IMP:9][logging_compose][setup] Created %s", net)
    else:
        logger.info("[IMP:8][logging_compose][setup] %s already exists", net)


def _compose_up_with_logs(compose_up_args: list[str], env_up: dict) -> None:
    """compose up + диагностика логов при rc!=0 (PLW0717-хелпер).

    ## @io — ⇥ compose_up_args, env_up → ⎋ None (pytest.fail при rc!=0)
    ## @complexity O(1) — один subprocess
    """
    up_result = subprocess.run(
        compose_up_args, capture_output=True, text=True, timeout=_COMPOSE_UP_TIMEOUT, env=env_up, check=False
    )
    logger.info("[IMP:8][logging_compose][setup] compose up rc=%d", up_result.returncode)
    if up_result.returncode != 0:
        log_args = [
            "docker",
            "compose",
            "-p",
            _SMOKE_PROJECT,
            *_compose_file_args(_COMPOSE_BASE, _COMPOSE_TEST),
            "logs",
            "--tail",
            "50",
            "--no-color",
        ]
        logs_result = subprocess.run(log_args, capture_output=True, text=True, timeout=30, env=env_up, check=False)
        logger.error(
            "[IMP:9][logging_compose][setup] Compose up failed — rc=%d\nstderr: %s\nlogs: %s",
            up_result.returncode,
            up_result.stderr.strip()[-300:],
            (logs_result.stdout or logs_result.stderr).strip()[-300:],
        )
        pytest.fail(f"docker compose up failed with rc={up_result.returncode}")


@pytest.fixture(scope="module")
def logging_compose():
    """Module-scoped fixture: manage docker compose lifecycle for logging smoke tests.

    ## @purpose — Start Loki + Promtail containers, yield for tests,
    ##            tear down and clean up after all tests in module.
    ## @io — ⇥ None → ⎋ dict (compose project, ports for tests)
    ## @complexity — O(1) — startup/teardown, no loops
    ## @invariants
    ##   - Stops any running wave-logging project before starting smoke project
    ##   - Creates observability-net Docker network if absent (removes if created)
    ##   - docker compose up -d --wait with 90s timeout
    ##   - Teardown: docker compose down -v --remove-orphans --timeout 5
    ##   - Only removes Docker networks if fixture created them (created_by_us set)
    """
    logger = logging.getLogger(__name__)
    logger.info("[IMP:7][logging_compose][setup] Starting logging smoke fixture")

    # ── Step 1: Stop any running wave-logging project ──────────────────────
    logger.info("[IMP:7][logging_compose][setup] Stopping existing wave-logging project")
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
    env_down = {**subprocess.os.environ, "COMPOSE_PROFILES": "logging"}
    try:
        result = subprocess.run(
            down_args, capture_output=True, text=True, timeout=_COMPOSE_DOWN_TIMEOUT, env=env_down, check=False
        )
        logger.info(
            "[IMP:8][logging_compose][setup] Stopped wave-logging: rc=%d stderr=%s",
            result.returncode,
            result.stderr.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:8][logging_compose][setup] wave-logging down timed out")

    # ── Step 2: Ensure external networks exist ─────────────────────────────
    created_by_us = set()
    for net in _EXTERNAL_NETWORKS:
        logger.info("[IMP:7][logging_compose][setup] Checking network %s", net)
        _ensure_external_network(net, created_by_us)

    # ── Step 3: Remove stale containers from shared stack ─────────────────────
    stale_containers = ["loki-test", "alloy-test"]
    for c in stale_containers:
        logger.info("[IMP:8][fixture][setup] Cleaning stale container: %s", c)
        subprocess.run(
            ["docker", "rm", "-f", c],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    # ── Step 4: Start logging compose ──────────────────────────────────────
    logger.info("[IMP:7][logging_compose][setup] Starting wave-logging-smoke compose")
    compose_up_args = [
        "docker",
        "compose",
        "-p",
        _SMOKE_PROJECT,
        *_compose_file_args(_COMPOSE_BASE, _COMPOSE_TEST),
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "90",
    ]
    env_up = {**subprocess.os.environ, "COMPOSE_PROFILES": "logging"}

    try:
        _compose_up_with_logs(compose_up_args, env_up)
        logger.info("[IMP:9][logging_compose][setup] wave-logging-smoke started successfully")
    except subprocess.TimeoutExpired:
        logger.error("[IMP:9][logging_compose][setup] compose up timed out after %ds", _COMPOSE_UP_TIMEOUT)
        pytest.fail(f"docker compose up timed out after {_COMPOSE_UP_TIMEOUT}s")

    # ── Yield test context ────────────────────────────────────────────────
    yield {
        "project": _SMOKE_PROJECT,
        "loki_port": _LOKI_PORT,
    }

    # ── Teardown: docker compose down ─────────────────────────────────────
    logger.info("[IMP:7][logging_compose][teardown] Tearing down wave-logging-smoke")
    down_smoke_args = [
        "docker",
        "compose",
        "-p",
        _SMOKE_PROJECT,
        *_compose_file_args(_COMPOSE_BASE, _COMPOSE_TEST),
        "down",
        "-v",
        "--remove-orphans",
        "--timeout",
        "5",
    ]
    try:
        down_result = subprocess.run(
            down_smoke_args, capture_output=True, text=True, timeout=_COMPOSE_DOWN_TIMEOUT, env=env_up, check=False
        )
        logger.info(
            "[IMP:8][logging_compose][teardown] compose down rc=%d: %s",
            down_result.returncode,
            down_result.stderr.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:8][logging_compose][teardown] compose down timed out")

    # ── Remove networks only if we created them ───────────────────────────
    for net in created_by_us:
        logger.info("[IMP:7][logging_compose][teardown] Removing network %s (created by fixture)", net)
        try:
            subprocess.run(
                ["docker", "network", "rm", net],
                capture_output=True,
                text=True,
                timeout=_NETWORK_CREATE_TIMEOUT,
                check=False,
            )
            logger.info("[IMP:9][logging_compose][teardown] %s removed", net)
        except subprocess.TimeoutExpired:
            logger.warning("[IMP:8][logging_compose][teardown] Failed to remove %s", net)

    logger.info("[IMP:9][logging_compose][teardown] Fixture teardown complete")


# endregion FIXTURES


# region LOGGING_SMOKE_TESTS
## @purpose — Health and buildinfo endpoint verification for Loki.
##            All tests use @pytest.mark.smoke + @pytest.mark.requires_docker.
## @scope    Live container tests — require Docker daemon and compose running.
## @invariants
##   - All tests depend on logging_compose fixture (module-scoped)
##   - HTTP requests to 127.0.0.1 with 10s timeout
##   - Each test asserts IMP:9 presence via ldd pattern in caplog


# ── Test 1: Loki /ready ──────────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_loki_ready(caplog, logging_compose) -> None:
    """Loki /ready returns HTTP 200.

    ## @purpose — Verify Loki is operational via its readiness endpoint.
    ## @io — ⇥ logging_compose fixture → ⚡ HTTP GET /ready → ⎋ None (asserts 200)
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_loki_ready] Checking %s", _LOKI_READY_URL)

        # ⚠️ TRAP[BUG] · 2026-07-23 · P1 · Unprotected requests.get() — retry with backoff
        # · Symptom: transient ConnectionError when Loki container is still starting up
        # · Root: no retry logic — single attempt fails on any transient failure
        # · Fix: exponential backoff retry (3 attempts, 1s/2s/4s)
        for attempt in range(3):
            try:
                r = requests.get(_LOKI_READY_URL, timeout=_HTTP_TIMEOUT)
                break
            except requests.RequestException as exc:
                if attempt < 2:
                    wait_s = 2**attempt
                    logger.warning(
                        "[IMP:7][test_loki_ready] Attempt %d failed (%s), retrying in %ds...",
                        attempt + 1,
                        exc,
                        wait_s,
                    )
                    time.sleep(wait_s)
                else:
                    logger.error("[IMP:9][test_loki_ready] All 3 attempts failed: %s", exc)
                    pytest.fail(f"Loki ready endpoint unreachable after 3 retries: {exc}")

        logger.info("[IMP:8][test_loki_ready] HTTP %d: %s", r.status_code, r.text.strip()[:100])

        logger.critical(
            "[IMP:9][test_loki_ready] ASSERT: status_code==200 => %s",
            r.status_code == 200,
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 log found in test_loki_ready"

        assert r.status_code == 200, (
            f"Loki /ready returned HTTP {r.status_code}, expected 200. Response: {r.text[:300]}"
        )


# ── Test 2: Loki buildinfo ───────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_loki_buildinfo(caplog, logging_compose) -> None:
    """Loki /loki/api/v1/status/buildinfo returns HTTP 200 with version field.

    ## @purpose — Verify Loki buildinfo API is accessible and returns version metadata.
    ## @io — ⇥ logging_compose fixture → ⚡ HTTP GET buildinfo → ⎋ None (asserts 200 + "version" in JSON)
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_loki_buildinfo] Checking %s", _LOKI_BUILDINFO_URL)

        # ⚠️ TRAP[BUG] · 2026-07-23 · P1 · Unprotected requests.get() — retry with backoff
        # · Symptom: transient ConnectionError when Loki container is still starting up
        # · Root: no retry logic — single attempt fails on any transient failure
        # · Fix: exponential backoff retry (3 attempts, 1s/2s/4s)
        for attempt in range(3):
            try:
                r = requests.get(_LOKI_BUILDINFO_URL, timeout=_HTTP_TIMEOUT)
                break
            except requests.RequestException as exc:
                if attempt < 2:
                    wait_s = 2**attempt
                    logger.warning(
                        "[IMP:7][test_loki_buildinfo] Attempt %d failed (%s), retrying in %ds...",
                        attempt + 1,
                        exc,
                        wait_s,
                    )
                    time.sleep(wait_s)
                else:
                    logger.error("[IMP:9][test_loki_buildinfo] All 3 attempts failed: %s", exc)
                    pytest.fail(f"Loki buildinfo endpoint unreachable after 3 retries: {exc}")

        logger.info("[IMP:8][test_loki_buildinfo] HTTP %d", r.status_code)

        assert r.status_code == 200, (
            f"Loki buildinfo returned HTTP {r.status_code}, expected 200. Response: {r.text[:300]}"
        )

        data = r.json()
        has_version = "version" in data
        logger.info(
            "[IMP:8][test_loki_buildinfo] buildinfo keys: %s",
            list(data.keys()),
        )

        logger.critical(
            "[IMP:9][test_loki_buildinfo] ASSERT: status_code==200 AND 'version' in response => %s",
            has_version,
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 log found in test_loki_buildinfo"

        assert has_version, f"Loki buildinfo response missing 'version' field. Keys: {list(data.keys())}"


# endregion LOGGING_SMOKE_TESTS
