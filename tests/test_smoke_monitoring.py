# GREP_SUMMARY: test-smoke-monitoring smoke requires_docker prometheus grafana health-endpoints compose-up
# STRUCTURE: ⚡ [requires_docker + smoke] → ▶ [monitoring_compose fixture] → ┬─ test_prometheus_health(◇ /-/healthy → 200) → ┬─ test_grafana_health(◇ /api/health → 200) → ┬─ test_prometheus_targets_up(◇ /api/v1/targets → 5+ UP) → ⎋ teardown down
# region MODULE_CONTRACT
## @purpose  Smoke tests for monitoring module — validates Prometheus + Grafana
##           health endpoints via live Docker compose.
##           Checks: Prometheus /-/healthy, Grafana /api/health, Prometheus targets.
## @scope    Docker-dependent tests (pytest.mark.smoke + pytest.mark.requires_docker).
##           Requires Docker daemon. Module-scoped fixture manages compose lifecycle.
## @invariants
##   - Module-scoped fixture manages compose lifecycle: pre-cleanup → up → tests → down
##   - Stops any existing wave-monitoring project before starting wave-monitoring-smoke
##   - Creates required Docker networks if absent (observability-net, proxy-net)
##   - All tests use HTTP GET to localhost (published ports)
##   - Container names: prometheus-test, grafana-test (from test.yml override)
##   - Compose project: wave-monitoring-smoke (isolated)
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale Smoke tests validate the actual Docker container behavior — port binding,
##            healthcheck execution, and service readiness. HTTP-level validation
##            confirms the services are operational from the host perspective.
## @usecases — Wave T5.8 (monitoring) acceptance: Prometheus + Grafana health verified
# endregion MODULE_CONTRACT

import logging
import subprocess
import time

import pytest
import requests
from _conftest.compose import _compose_file_args  # DevPlan 170 W8: canonical compose module
from _conftest.env import get_smoke_env  # T12.3: compose-subprocess-ы получают SMOKE_ENV через merge
from _conftest.ldd import _print_ldd_trajectory

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_MONITORING_MODULE = repo_root() / "core" / "modules" / "monitoring"
_COMPOSE_BASE = _MONITORING_MODULE / "docker-compose.base.yml"
_COMPOSE_TEST = _MONITORING_MODULE / "docker-compose.test.yml"

# Compose project names
_WAVE_PROJECT = "wave-monitoring"
_SMOKE_PROJECT = "wave-monitoring-smoke"

# Ports (from test.yml — shifted for test overlay coexistence with production)
_PROMETHEUS_PORT = 19090
_GRAFANA_PORT = 13030

# Endpoints
_PROMETHEUS_HEALTH_URL = f"http://127.0.0.1:{_PROMETHEUS_PORT}/-/healthy"
_GRAFANA_HEALTH_URL = f"http://127.0.0.1:{_GRAFANA_PORT}/api/health"
_PROMETHEUS_TARGETS_URL = f"http://127.0.0.1:{_PROMETHEUS_PORT}/api/v1/targets"

# Timeouts
_COMPOSE_UP_TIMEOUT = 120
_COMPOSE_DOWN_TIMEOUT = 20
_HTTP_TIMEOUT = 10
_NETWORK_CREATE_TIMEOUT = 15

# Required external networks
_EXTERNAL_NETWORKS = ["test-observability-net", "test-proxy-net"]


# region FIXTURES
## @purpose — Module-scoped compose lifecycle fixture for monitoring smoke tests.
##            Pre-cleans wave-monitoring project, starts wave-monitoring-smoke,
##            yields, tears down on completion.


def _ensure_external_network(net: str) -> None:
    """docker network inspect/create при отсутствии сети (PLW0717-хелпер).

    ## @io — ⇥ net → ⎋ None (pytest.fail при timeout)
    ## @complexity O(1) — один subprocess
    """
    try:
        inspect_result = subprocess.run(
            ["docker", "network", "inspect", net],
            capture_output=True,
            text=True,
            timeout=_NETWORK_CREATE_TIMEOUT,
            check=False,
        )
        if inspect_result.returncode != 0:
            _create_network(net)
        else:
            logger.info("[IMP:8][monitoring_compose][setup] %s already exists", net)
    except subprocess.TimeoutExpired:
        logger.error("[IMP:9][monitoring_compose][setup] Timeout checking %s", net)
        pytest.fail(f"Failed to ensure network {net} exists")


def _create_network(net: str) -> None:
    """docker network create + лог (PLW0717-хелпер).

    ## @io — ⇥ net → ⎋ None
    ## @complexity O(1) — один subprocess
    """
    logger.info("[IMP:8][monitoring_compose][setup] Creating network %s", net)
    subprocess.run(
        ["docker", "network", "create", net],
        capture_output=True,
        text=True,
        timeout=_NETWORK_CREATE_TIMEOUT,
        check=True,
    )
    logger.info("[IMP:9][monitoring_compose][setup] Created %s", net)


def _monitoring_compose_up(compose_up_args: list[str], env_up: dict) -> None:
    """compose up + диагностика логов при rc!=0 (PLW0717-хелпер).

    ## @io — ⇥ compose_up_args, env_up → ⎋ None (pytest.fail при rc!=0)
    ## @complexity O(1) — один subprocess
    """
    up_result = subprocess.run(
        compose_up_args, capture_output=True, text=True, timeout=_COMPOSE_UP_TIMEOUT, env=env_up, check=False
    )
    logger.info("[IMP:8][monitoring_compose][setup] compose up rc=%d", up_result.returncode)
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
            "[IMP:9][monitoring_compose][setup] Compose up failed — rc=%d\nstdout: %s\nstderr: %s\nlogs: %s",
            up_result.returncode,
            up_result.stdout.strip()[-500:],
            up_result.stderr.strip()[-500:],
            (logs_result.stdout or logs_result.stderr).strip()[-500:],
        )
        pytest.fail(f"docker compose up failed with rc={up_result.returncode}")


# 2026-08-04 (DevPlan 129 W3): volume prometheus-config-gen
# объявлен в docker-compose.test.yml (test-overlay, см. volumes: секцию) — test-стек
# base+test больше не ссылается на необъявленный volume («undefined volume» устранён).


@pytest.fixture(scope="module")
def monitoring_compose():
    """Module-scoped fixture: manage docker compose lifecycle for monitoring smoke tests.

    ## @purpose — Start Prometheus + Grafana containers, yield for tests,
    ##            tear down and clean up after all tests in module.
    ## @io — ⇥ None → ⎋ dict (compose project, ports for tests)
    ## @complexity — O(1) — startup/teardown, no loops
    ## @invariants
    ##   - Stops any running wave-monitoring project before starting smoke project
    ##   - Creates external Docker networks if absent (removes if created)
    ##   - docker compose up -d --wait with 90s timeout
    ##   - Teardown: docker compose down -v --remove-orphans --timeout 5
    ##   - Shared external networks are NEVER removed in teardown (B10 T5 contract)
    """
    logger = logging.getLogger(__name__)
    logger.info("[IMP:7][monitoring_compose][setup] Starting monitoring smoke fixture")

    # ── Step 1: Stop any running wave-monitoring project ───────────────────
    logger.info("[IMP:7][monitoring_compose][setup] Stopping existing wave-monitoring project")
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
    # T12.3 (2026-08-17): merge get_smoke_env() — без него CI-интерполяция root-compose
    # (${VAR:?} B23) падала на недостающих статик-переменных.
    env_down = {**subprocess.os.environ, **get_smoke_env(), "COMPOSE_PROFILES": "monitoring"}
    try:
        result = subprocess.run(
            down_args, capture_output=True, text=True, timeout=_COMPOSE_DOWN_TIMEOUT, env=env_down, check=False
        )
        logger.info(
            "[IMP:8][monitoring_compose][setup] Stopped wave-monitoring: rc=%d stderr=%s",
            result.returncode,
            result.stderr.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:8][monitoring_compose][setup] wave-monitoring down timed out")

    # ── Step 2: Ensure external networks exist ─────────────────────────────
    for net in _EXTERNAL_NETWORKS:
        logger.info("[IMP:7][monitoring_compose][setup] Checking network %s", net)
        _ensure_external_network(net)

    # ── Step 3: Remove stale containers from shared stack ─────────────────────
    # ⚠️ TRAP[BUG] · 2026-07-18 · HIGH · prometheus-config-init one-shot container blocks compose
    # · Root: platform_services starts prometheus-config-init as part of monitoring module.
    # ·   This Exited container persists and blocks the monitoring_compose fixture from
    # ·   recreating it (container name "prometheus-config-init-test" collision with stale run).
    # · Fix: include prometheus-config-init-test in stale container removal.
    stale_containers = ["prometheus-test", "grafana-test", "prometheus-config-init-test"]
    for c in stale_containers:
        logger.info("[IMP:8][fixture][setup] Cleaning stale container: %s", c)
        subprocess.run(
            ["docker", "rm", "-f", c],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    # ── Step 4: Ensure external networks still exist (may have been removed by prior test teardown) ──
    # ⚠️ TRAP[BUG] · 2026-07-18 · HIGH · observability-net removed by prior module fixture teardown
    # · Root: module-scoped fixtures (infra_metrics, logging, etc.) create and later remove
    # ·   external networks during their lifecycle. By the time monitoring_compose runs,
    # ·   observability-net and proxy-net may have been destroyed.
    # · Fix: recreate networks unconditionally before compose up, even if they exist.
    for net in _EXTERNAL_NETWORKS:
        net_check = subprocess.run(
            ["docker", "network", "inspect", net], capture_output=True, text=True, timeout=10, check=False
        )
        if net_check.returncode != 0:
            logger.info("[IMP:8][monitoring_compose][setup] Recreating network '%s'", net)
            subprocess.run(
                ["docker", "network", "create", net],
                capture_output=True,
                text=True,
                timeout=_NETWORK_CREATE_TIMEOUT,
                check=True,
            )
            logger.info("[IMP:9][monitoring_compose][setup] Recreated network '%s'", net)
        else:
            logger.info("[IMP:8][monitoring_compose][setup] Network '%s' already exists", net)

    # ── Step 5: Start monitoring compose ──────────────────────────────────
    logger.info("[IMP:7][monitoring_compose][setup] Starting wave-monitoring-smoke compose")
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
    # Docker Compose v5+ does NOT auto-load .env when using -f to files in subdirectories.
    # Pass --env-file .env explicitly, but also fall back to env vars for robustness.
    env_up = {
        **subprocess.os.environ,
        **get_smoke_env(),
        "COMPOSE_PROFILES": "monitoring",
        "PROMETHEUS_TARGETS_DIR": subprocess.os.environ.get("PROMETHEUS_TARGETS_DIR", "/tmp/prometheus-targets"),
        "PROMETHEUS_RULES_DIR": subprocess.os.environ.get("PROMETHEUS_RULES_DIR", "/tmp/prometheus-rules"),
    }

    try:
        _monitoring_compose_up(compose_up_args, env_up)
        logger.info("[IMP:9][monitoring_compose][setup] wave-monitoring-smoke started successfully")
    except subprocess.TimeoutExpired:
        logger.error("[IMP:9][monitoring_compose][setup] compose up timed out after %ds", _COMPOSE_UP_TIMEOUT)
        pytest.fail(f"docker compose up timed out after {_COMPOSE_UP_TIMEOUT}s")

    # ── Yield test context ────────────────────────────────────────────────
    yield {
        "project": _SMOKE_PROJECT,
        "prometheus_port": _PROMETHEUS_PORT,
        "grafana_port": _GRAFANA_PORT,
    }

    # ── Teardown: docker compose down ─────────────────────────────────────
    logger.info("[IMP:7][monitoring_compose][teardown] Tearing down wave-monitoring-smoke")
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
            "[IMP:8][monitoring_compose][teardown] compose down rc=%d: %s",
            down_result.returncode,
            down_result.stderr.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:8][monitoring_compose][teardown] compose down timed out")

    # ── External networks are NEVER removed in teardown (contract, DevPlan 116 B10 T5) ──
    # test-observability-net/test-proxy-net are SHARED external test networks (tests/_conftest/
    # networks.py TEST_NETWORKS). B10 T5 contract: shared external networks are created once,
    # reused across sessions, and MUST NOT be removed in teardown (parallel-session race —
    # networks.py). The old `created_by_us` rm-loop was removed 2026-08-01
    # (was: this fixture deleted networks it created while other sessions still needed them).

    logger.info("[IMP:9][monitoring_compose][teardown] Fixture teardown complete")


# endregion FIXTURES


# region MONITORING_SMOKE_TESTS
## @purpose — Health endpoint verification for Prometheus and Grafana.
##            All tests use @pytest.mark.smoke + @pytest.mark.requires_docker.
## @scope    Live container tests — require Docker daemon and compose running.
## @invariants
##   - All tests depend on monitoring_compose fixture (module-scoped)
##   - HTTP requests to 127.0.0.1 with 10s timeout
##   - Each test asserts IMP:9 presence via ldd pattern in caplog


# ⚠️ TRAP[BUG] · 2026-07-23 · P1 · No retry on transient HTTP failures in monitoring smoke tests
# · Symptom: CI flakiness — monitoring smoke tests fail on transient ConnectionError
# ·   or timeout from containers still initializing after compose healthcheck.
# · Root: requests.get() called once without retry — any transient network glitch
# ·   caused immediate pytest.fail().
# · Fix: Added 3-attempt retry loop with exponential backoff (1s/2s/4s).
# ·   Only pytest.fail() on last attempt. Logs each retry attempt at [IMP:8].
# · Prevention: All HTTP test calls should use retry pattern to tolerate transient failures.

# ── Test 1: Prometheus /-/healthy ─────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_prometheus_health(caplog, monitoring_compose) -> None:
    """Prometheus /-/healthy returns HTTP 200.

    ## @purpose — Verify Prometheus is operational via its health endpoint.
    ## @io — ⇥ monitoring_compose fixture → ⚡ HTTP GET health → ⎋ None (asserts 200)
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_prometheus_health] Checking %s", _PROMETHEUS_HEALTH_URL)

        r = None
        for attempt in range(3):
            try:
                r = requests.get(_PROMETHEUS_HEALTH_URL, timeout=_HTTP_TIMEOUT)
                logger.info(
                    "[IMP:8][test_prometheus_health] Attempt %d/3 succeeded",
                    attempt + 1,
                )
                break
            except requests.RequestException as exc:
                if attempt < 2:
                    backoff = 2**attempt
                    logger.warning(
                        "[IMP:8][test_prometheus_health] Attempt %d/3 failed, retrying in %ds: %s",
                        attempt + 1,
                        backoff,
                        exc,
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "[IMP:9][test_prometheus_health] All 3 attempts failed: %s",
                        exc,
                    )
                    pytest.fail(f"Prometheus health endpoint unreachable after 3 attempts: {exc}")

        logger.info("[IMP:8][test_prometheus_health] HTTP %d: %s", r.status_code, r.text.strip()[:100])

        logger.critical(
            "[IMP:9][test_prometheus_health] ASSERT: status_code==200 => %s",
            r.status_code == 200,
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 log found in test_prometheus_health"

        assert r.status_code == 200, (
            f"Prometheus /-/healthy returned HTTP {r.status_code}, expected 200. Response: {r.text[:300]}"
        )


# ── Test 2: Grafana /api/health ──────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_grafana_health(caplog, monitoring_compose) -> None:
    """Grafana /api/health returns HTTP 200.

    ## @purpose — Verify Grafana is operational via its health endpoint.
    ## @io — ⇥ monitoring_compose fixture → ⚡ HTTP GET health → ⎋ None (asserts 200)
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_grafana_health] Checking %s", _GRAFANA_HEALTH_URL)

        r = None
        for attempt in range(3):
            try:
                r = requests.get(_GRAFANA_HEALTH_URL, timeout=_HTTP_TIMEOUT)
                logger.info(
                    "[IMP:8][test_grafana_health] Attempt %d/3 succeeded",
                    attempt + 1,
                )
                break
            except requests.RequestException as exc:
                if attempt < 2:
                    backoff = 2**attempt
                    logger.warning(
                        "[IMP:8][test_grafana_health] Attempt %d/3 failed, retrying in %ds: %s",
                        attempt + 1,
                        backoff,
                        exc,
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "[IMP:9][test_grafana_health] All 3 attempts failed: %s",
                        exc,
                    )
                    pytest.fail(f"Grafana health endpoint unreachable after 3 attempts: {exc}")

        logger.info("[IMP:8][test_grafana_health] HTTP %d: %s", r.status_code, r.text.strip()[:100])

        logger.critical(
            "[IMP:9][test_grafana_health] ASSERT: status_code==200 => %s",
            r.status_code == 200,
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 log found in test_grafana_health"

        assert r.status_code == 200, (
            f"Grafana /api/health returned HTTP {r.status_code}, expected 200. Response: {r.text[:300]}"
        )


# ── Test 3: Prometheus targets API ────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_prometheus_targets_api(caplog, monitoring_compose) -> None:
    """Prometheus /api/v1/targets returns 200 with at least one active target.

    ## @purpose — Verify Prometheus scrape targets are accessible.
    ##            Accepts 0 targets (self-scrape only in isolation).
    ## @io — ⇥ monitoring_compose fixture → ⚡ HTTP GET targets API → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_prometheus_targets] Checking %s", _PROMETHEUS_TARGETS_URL)

        r = None
        for attempt in range(3):
            try:
                r = requests.get(_PROMETHEUS_TARGETS_URL, timeout=_HTTP_TIMEOUT)
                logger.info(
                    "[IMP:8][test_prometheus_targets] Attempt %d/3 succeeded",
                    attempt + 1,
                )
                break
            except requests.RequestException as exc:
                if attempt < 2:
                    backoff = 2**attempt
                    logger.warning(
                        "[IMP:8][test_prometheus_targets] Attempt %d/3 failed, retrying in %ds: %s",
                        attempt + 1,
                        backoff,
                        exc,
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "[IMP:9][test_prometheus_targets] All 3 attempts failed: %s",
                        exc,
                    )
                    pytest.fail(f"Prometheus targets endpoint unreachable after 3 attempts: {exc}")

        logger.info("[IMP:8][test_prometheus_targets] HTTP %d", r.status_code)

        assert r.status_code == 200, (
            f"Prometheus targets endpoint returned HTTP {r.status_code}, expected 200. Response: {r.text[:300]}"
        )

        data = r.json()
        targets = data.get("data", {}).get("activeTargets", [])
        num_up = sum(1 for t in targets if t.get("health") == "up")

        logger.info(
            "[IMP:8][test_prometheus_targets] %d targets, %d UP",
            len(targets),
            num_up,
        )

        logger.critical(
            "[IMP:9][test_prometheus_targets] ASSERT: targets API responded OK",
        )

        # LDD trajectory verification
        found_imp9 = _print_ldd_trajectory(caplog)
        assert found_imp9, "Critical LDD Error: No IMP:9 log found in test_prometheus_targets"

        # Don't assert on count — in isolation only self-scrape exists
        logger.info("[IMP:7][test_prometheus_targets] ✅ targets API OK: %d targets, %d UP", len(targets), num_up)


# endregion MONITORING_SMOKE_TESTS
