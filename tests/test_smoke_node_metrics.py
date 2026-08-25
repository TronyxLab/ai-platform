# GREP_SUMMARY: test-smoke-node-metrics smoke requires_docker cadvisor node-exporter compose-up healthcheck
# STRUCTURE: ⚡ [requires_docker + smoke] → ▶ [node_metrics_compose fixture] → ┬─ test_cadvisor_healthz(◇ HTTP GET /healthz → 200) → ┬─ test_node_exporter_metrics(◇ HTTP GET /metrics → 200) → ┬─ test_infra_metrics_healthcheck(◇ bash healthcheck.sh deep → exit 0) → ⎋ teardown down
# region MODULE_CONTRACT
## @purpose  Smoke tests for node-metrics module — validates cAdvisor, Node Exporter HTTP endpoints.
##           Преемник smoke infra-metrics (DevPlan 008 T5.10); перенаправлен при T3.2 split (DevPlan 010).
## @scope    Docker-dependent tests (pytest.mark.smoke + pytest.mark.requires_docker).
##           Requires Docker daemon. Module-scoped fixture manages compose lifecycle.
## @invariants
##   - Module-scoped fixture manages compose lifecycle: pre-cleanup → up → tests → down
##   - Stops any existing ai-platform-test project before starting smoke project
##   - Ensures observability-net and shared-cache-net exist (external networks)
##   - Uses test.yml overlay for isolated container names (-test suffix)
##   - Compose project: wave-node-metrics-smoke (isolated from other tests)
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale Smoke tests validate the actual Docker container behavior — HTTP endpoint
##            connectivity for cAdvisor and Node Exporter.
## @usecases — Wave T5.10 (node-metrics) acceptance: HTTP endpoints verified at runtime
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import time

import pytest
import requests
from _conftest.env import get_smoke_env  # T12.3: compose-subprocess-ы получают SMOKE_ENV через merge
from _conftest.networks import get_network_manager

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_NODE_METRICS_MODULE = repo_root() / "core" / "modules" / "node-metrics"
_COMPOSE_BASE = _NODE_METRICS_MODULE / "docker-compose.base.yml"
_COMPOSE_TEST = _NODE_METRICS_MODULE / "docker-compose.test.yml"
_HEALTHCHECK_SH = _NODE_METRICS_MODULE / "healthcheck.sh"

# Compose project names
_EXISTING_PROJECT = "ai-platform-existing"  # existing production/live-verification project — NOT "ai-platform-test" to avoid destroying the platform_services session stack
_SMOKE_PROJECT = "wave-node-metrics-smoke"  # isolated smoke test project

# Default test container names (from test.yml override)
_CADVISOR_CONTAINER = "cadvisor-test"
_NODE_EXPORTER_CONTAINER = "node-exporter-test"

# External Docker networks (must match all networks in docker-compose.test.yml)
_EXTERNAL_NETWORKS = {"test-observability-net"}  # node-metrics: только observability-net (T3.2)

# Timeouts
_COMPOSE_UP_TIMEOUT = 90
_COMPOSE_DOWN_TIMEOUT = 20
_NETWORK_CREATE_TIMEOUT = 15
_CURL_TIMEOUT = 10

# Test ports (from docker-compose.test.yml overlay: 1XXXX:YYYY)
# ⚠️ TRAP[BUG] · 2026-07-16 · HI · _CADVISOR_PORT 18080 collided with nginx-test
# · Symptom: smoke ERRORS — node_metrics_compose failed, port 18080 already allocated
# · Root: nginx-test uses 18080:80, cadvisor-test used 18080:8080 → same host port
# · Fix: changed to 18081 (verified free — grep found zero collisions)
# · Prevention: see node-metrics docker-compose.test.yml TRAP[BUG]
_CADVISOR_PORT = 18081
_NODE_EXPORTER_PORT = 19100


# region FIXTURES
## @purpose — Module-scoped compose lifecycle fixture for node-metrics smoke tests.


def _run_docker(
    args: list[str],
    env_override: dict[str, str] | None = None,
    timeout: int = 30,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a docker subprocess with optional env overrides.

    ## @purpose — Centralised docker subprocess runner for smoke tests.
    ## @io — ⇥ args, env_override, timeout, check → ⎋ CompletedProcess
    ## @complexity — O(1)
    """
    cmd_env = None
    if env_override:
        # T12.3 (2026-08-17): merge SMOKE_ENV — CI os.environ не содержит статик-smoke-env
        cmd_env = {**__import__("os").environ, **get_smoke_env(), **env_override}
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=cmd_env, check=False)
        if check and result.returncode != 0:
            logger.warning("[IMP:8][docker] %s failed: %s", args[0], result.stderr.strip()[-200:])
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:8][docker] %s timed out after %ds", args[0], timeout)
        raise
    else:
        return result


@pytest.fixture(scope="module")
def node_metrics_compose():
    """Module-scoped fixture: manage docker compose lifecycle for node-metrics smoke tests.

    ## @purpose — Start node-metrics containers (cadvisor, node-exporter, nginx-exporter, redis-exporter),
    ##            yield config info for tests, tear down after all tests in module.
    ## @io — ⇥ None → ⎋ dict (compose project, container names, ports)
    ## @complexity — O(1) — startup/teardown with network creation
    ## @invariants
    ##   - Stops any running ai-platform-test project before starting smoke project
    ##   - Creates observability-net and shared-cache-net if absent (cleans up if created)
    ##   - docker compose up -d --wait with timeout
    ##   - Teardown: docker compose down --remove-orphans --timeout 5
    ##   - Only removes Docker networks if fixture created them (flag created_nets)
    """
    logger = logging.getLogger(__name__)
    logger.info("[IMP:7][node_metrics_compose][setup] Starting node-metrics smoke fixture")

    # ── Step 1: Stop any running existing project ─────────────────────────────
    logger.info("[IMP:7][node_metrics_compose][setup] Stopping existing %s project", _EXISTING_PROJECT)
    down_args = [
        "docker",
        "compose",
        "-f",
        str(_COMPOSE_BASE),
        "-f",
        str(_COMPOSE_TEST),
        "--profile",
        "node-metrics",
        "-p",
        _EXISTING_PROJECT,
        "down",
        "--timeout",
        "5",
        "--remove-orphans",
    ]
    _run_docker(down_args, timeout=20, check=False)

    # ── Step 2: Pre-clean any previous smoke project ──────────────────────────
    logger.info("[IMP:7][node_metrics_compose][setup] Cleaning previous %s project", _SMOKE_PROJECT)
    clean_args = [
        "docker",
        "compose",
        "-f",
        str(_COMPOSE_BASE),
        "-f",
        str(_COMPOSE_TEST),
        "--profile",
        "node-metrics",
        "-p",
        _SMOKE_PROJECT,
        "down",
        "--timeout",
        "5",
        "--remove-orphans",
    ]
    _run_docker(clean_args, timeout=20, check=False)

    # ── Step 3: Create external networks via NetworkLeaseManager ──────────────
    # Acquire networks through the canonical lease manager to coordinate with
    # platform_services and other fixtures. Replaces direct docker network create
    # which silently fails when networks are held by another fixture.
    nm = get_network_manager()
    for net_name in sorted(_EXTERNAL_NETWORKS):
        nm.acquire(net_name)
    logger.info(
        "[IMP:9][node_metrics_compose][setup] External networks acquired via NetworkLeaseManager: %s",
        sorted(_EXTERNAL_NETWORKS),
    )

    # ── Step 4: Remove stale containers from shared stack ─────────────────────
    # (nginx-prometheus-exporter-test убран — DR-H2 fix: exporter в модуле nginx)
    stale_containers = [
        "cadvisor-test",
        "node-exporter-test",
        "redis-exporter-test",
        "postgres-exporter-test",
    ]
    for c in stale_containers:
        logger.info("[IMP:8][fixture][setup] Cleaning stale container: %s", c)
        subprocess.run(
            ["docker", "rm", "-f", c],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    # ── Step 5: Start node-metrics compose ───────────────────────────────────
    up_args = [
        "docker",
        "compose",
        "-f",
        str(_COMPOSE_BASE),
        "-f",
        str(_COMPOSE_TEST),
        "--profile",
        "node-metrics",
        "-p",
        _SMOKE_PROJECT,
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "60",
    ]

    logger.info("[IMP:7][node_metrics_compose][setup] Starting node-metrics stack")
    # Set env vars for postgres-exporter (needs POSTGRES_PASSWORD for DATA_SOURCE_NAME)
    up_result = _run_docker(
        up_args,
        timeout=_COMPOSE_UP_TIMEOUT,
        env_override={
            "POSTGRES_USER": os.environ.get("POSTGRES_USER", "postgres"),
            "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", "test_password_2024"),
            "POSTGRES_DB": os.environ.get("POSTGRES_DB", "platform"),
        },
    )

    if up_result.returncode != 0:
        logger.error(
            "[IMP:9][node_metrics_compose][setup] docker compose up failed: %s",
            up_result.stderr.strip()[-500:],
        )
        # Attempt cleanup
        _run_docker(clean_args, timeout=20, check=False)
        pytest.fail(f"docker compose up failed: {up_result.stderr.strip()[-300:]}")

    logger.info("[IMP:9][node_metrics_compose][setup] node-metrics stack started")

    # ── Yield config ──────────────────────────────────────────────────────────
    yield {
        "project": _SMOKE_PROJECT,
        "cadvisor": {
            "container": _CADVISOR_CONTAINER,
            "port": _CADVISOR_PORT,
        },
        "node_exporter": {
            "container": _NODE_EXPORTER_CONTAINER,
            "port": _NODE_EXPORTER_PORT,
        },
    }

    # ── Teardown ──────────────────────────────────────────────────────────────
    logger.info("[IMP:7][node_metrics_compose][teardown] Stopping node-metrics stack")
    _run_docker(clean_args, timeout=_COMPOSE_DOWN_TIMEOUT, check=False)

    # Release networks via canonical NetworkLeaseManager
    for net_name in sorted(_EXTERNAL_NETWORKS, reverse=True):
        nm.release(net_name)

    logger.info("[IMP:9][node_metrics_compose][teardown] node-metrics stack stopped")


# endregion FIXTURES

# region TESTS


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: cAdvisor /healthz endpoint must return HTTP 200
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.requires_docker
@pytest.mark.smoke
def test_cadvisor_healthz(caplog, node_metrics_compose) -> None:
    """Verify cAdvisor /healthz returns HTTP 200 with 'ok' body.

    ## @purpose — cAdvisor health endpoint. Primary liveness check.
    ## @io — ⇥ caplog → ⚡ HTTP GET http://127.0.0.1:18081/healthz → ⎋ None (asserts 200)
    ## @complexity — O(1)
    """
    port = node_metrics_compose["cadvisor"]["port"]
    url = f"http://127.0.0.1:{port}/healthz"
    logger.info("[IMP:7][smoke][cadvisor] Checking %s", url)

    # ⚠️ TRAP[BUG] · 2026-07-23 · P1 · requests.get without retry in test_cadvisor_healthz
    # · Symptom: transient network error (ConnectionError, Timeout) on container startup → test crash
    # · Root: cAdvisor container may still be starting when HTTP request arrives
    # · Fix: retry with backoff (1s/2s/4s) — gives container time to stabilize after restart
    # · Prevention: UF10 gate (test_gate_http_retry_policy) will catch similar cases in future
    for attempt in range(3):
        try:
            _assert_cadvisor_healthz(url, attempt)
            break
        except requests.RequestException as exc:
            if attempt < 2:
                wait_s = 2**attempt  # 1s, 2s backoff
                logger.warning(
                    "[IMP:7][smoke][cadvisor] Attempt %d failed (%s), retrying in %ds...",
                    attempt + 1,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
            else:
                logger.error(
                    "[IMP:9][smoke][cadvisor] All 3 attempts failed for %s: %s",
                    url,
                    exc,
                )
                raise


def _assert_cadvisor_healthz(url: str, attempt: int) -> None:
    """GET /healthz: HTTP 200 + body 'ok' (TRAP[BUG] 2026-07-23 retry, PLW0717-хелпер).

    ## @io — ⇥ url, attempt → ⎋ None (asserts; бросает RequestException в retry-цикл)
    ## @complexity O(1) — один HTTP запрос
    """
    # ⚠️ TRAP[BUG] · 2026-08-14 · P1 · Retry restoration — loop removed by batch-coder
    # refactor (TRY300/PLW0717); gate test_gate_http_retry_policy requires retry here.
    for retry in range(3):
        try:  # ruff: ignore[PLW0717] — retry-loop body: извлечение ломает retry-семантику (все операторы — запрос+проверки одной попытки)
            r = requests.get(url, timeout=_CURL_TIMEOUT)
            logger.info(
                "[IMP:8][smoke][cadvisor] /healthz returned HTTP %s (attempt %d): %s",
                r.status_code,
                attempt + 1,
                r.text.strip(),
            )

            assert r.status_code == 200, (
                f"cAdvisor /healthz returned HTTP {r.status_code}, expected 200. Response: {r.text[:300]}"
            )
            assert r.text.strip() == "ok", f"cAdvisor /healthz body '{r.text.strip()}', expected 'ok'"
            logger.info("[IMP:9][smoke][cadvisor] ✅ cAdvisor /healthz OK: HTTP 200")
            break
        except requests.RequestException as exc:
            if retry < 2:
                wait_s = 2**retry
                logger.warning(
                    "[IMP:7][smoke][cadvisor] Attempt %d failed (%s), retrying in %ds...",
                    retry + 1,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
            else:
                logger.error(
                    "[IMP:9][smoke][cadvisor] All 3 attempts failed for %s: %s",
                    url,
                    exc,
                )
                raise


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: Node Exporter /metrics endpoint must return HTTP 200
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.requires_docker
@pytest.mark.smoke
def test_node_exporter_metrics(caplog, node_metrics_compose) -> None:
    """Verify Node Exporter /metrics returns HTTP 200 with Prometheus metrics.

    ## @purpose — Node Exporter metrics endpoint. Returns Prometheus-format metrics.
    ## @io — ⇥ caplog → ⚡ HTTP GET http://127.0.0.1:19100/metrics → ⎋ None (asserts 200 + metrics)
    ## @complexity — O(1)
    """
    port = node_metrics_compose["node_exporter"]["port"]
    url = f"http://127.0.0.1:{port}/metrics"
    logger.info("[IMP:7][smoke][node-exporter] Checking %s", url)

    # ⚠️ TRAP[BUG] · 2026-07-23 · P1 · requests.get without retry in test_node_exporter_metrics
    # · Symptom: transient network error (ConnectionError, Timeout) on container startup → test crash
    # · Root: Node Exporter container may still be starting when HTTP request arrives
    # · Fix: retry with backoff (1s/2s/4s) — gives container time to stabilize after restart
    # · Prevention: UF10 gate (test_gate_http_retry_policy) will catch similar cases in future
    for attempt in range(3):
        try:
            _assert_node_exporter_metrics(url, attempt)
            break
        except requests.RequestException as exc:
            if attempt < 2:
                wait_s = 2**attempt  # 1s, 2s backoff
                logger.warning(
                    "[IMP:7][smoke][node-exporter] Attempt %d failed (%s), retrying in %ds...",
                    attempt + 1,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
            else:
                logger.error(
                    "[IMP:9][smoke][node-exporter] All 3 attempts failed for %s: %s",
                    url,
                    exc,
                )
                raise


def _assert_node_exporter_metrics(url: str, attempt: int) -> None:
    """GET /metrics: HTTP 200 + Prometheus-формат (# HELP, node_) (TRAP[BUG] retry, PLW0717-хелпер).

    ## @io — ⇥ url, attempt → ⎋ None (asserts; бросает RequestException в retry-цикл)
    ## @complexity O(1) — один HTTP запрос
    """
    # ⚠️ TRAP[BUG] · 2026-08-14 · P1 · Retry restoration — loop removed by batch-coder
    # refactor (TRY300/PLW0717); gate test_gate_http_retry_policy requires retry here.
    for retry in range(3):
        try:  # ruff: ignore[PLW0717] — retry-loop body: извлечение ломает retry-семантику (все операторы — запрос+проверки одной попытки)
            r = requests.get(url, timeout=_CURL_TIMEOUT)
            logger.info(
                "[IMP:8][smoke][node-exporter] /metrics returned HTTP %s (%d bytes, attempt %d)",
                r.status_code,
                len(r.content),
                attempt + 1,
            )

            assert r.status_code == 200, (
                f"Node Exporter /metrics returned HTTP {r.status_code}, expected 200. Response: {r.text[:300]}"
            )
            # Verify the response contains Prometheus metrics (starts with # HELP)
            assert "# HELP" in r.text, (
                f"Node Exporter /metrics response does not contain Prometheus format: {r.text[:200]}"
            )
            assert "node_" in r.text, f"Node Exporter /metrics missing 'node_' prefixed metrics: {r.text[:200]}"
            logger.info(
                "[IMP:9][smoke][node-exporter] ✅ Node Exporter /metrics OK: HTTP 200, %d bytes, attempt %d",
                len(r.content),
                attempt + 1,
            )
            break
        except requests.RequestException as exc:
            if retry < 2:
                wait_s = 2**retry
                logger.warning(
                    "[IMP:7][smoke][node-exporter] Attempt %d failed (%s), retrying in %ds...",
                    retry + 1,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
            else:
                logger.error(
                    "[IMP:9][smoke][node-exporter] All 3 attempts failed for %s: %s",
                    url,
                    exc,
                )
                raise


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: healthcheck.sh deep mode passes
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.requires_docker
@pytest.mark.smoke
def test_node_metrics_healthcheck(caplog, node_metrics_compose) -> None:
    """Verify healthcheck.sh deep mode passes (exit 0).

    ## @purpose — The module healthcheck.sh script validates all containers
    ##            and HTTP endpoints. Deep mode checks /healthz and /metrics.
    ##            Passes shifted test ports (CADVISOR_PORT=18081,
    ##            NODE_EXPORTER_PORT=19100) — скрипт использует их через env.
    ## @io — ⇥ caplog → ⚡ bash healthcheck.sh deep → ⎋ None (asserts exit 0)
    ## @complexity — O(N)
    ## ⚠️ TRAP[BUG] · 2026-07-18 · HIGH · тест проходил через F-7 side-effect
    ## · Root: до !override тестовые контейнеры биндили canonical порты 8080/9100
    ## ·   (склейка base+test), и healthcheck.sh с hardcoded портами проходил.
    ## · Fix: порты параметризованы через env; тест передаёт shifted порты.
    """
    logger.info("[IMP:7][smoke][healthcheck] Running healthcheck.sh deep")

    # ⚠️ Передаём shifted test порты (F-7): CADVISOR_PORT=18081, NODE_EXPORTER_PORT=19100
    # ⚠️ Передаём test container names (2026-07-27): канонические имена заменены на -test суффиксы
    healthcheck_env = {
        **os.environ,
        "CADVISOR_PORT": str(_CADVISOR_PORT),
        "NODE_EXPORTER_PORT": str(_NODE_EXPORTER_PORT),
        "CADVISOR_CONTAINER_NAME": _CADVISOR_CONTAINER,
        "NODE_EXPORTER_CONTAINER_NAME": _NODE_EXPORTER_CONTAINER,
        # nginx-exporter убран (DR-H2 fix): контейнер принадлежит модулю nginx
        "REDIS_EXPORTER_CONTAINER_NAME": "redis-exporter-test",
    }
    result = subprocess.run(
        ["bash", str(_HEALTHCHECK_SH), "deep"],
        capture_output=True,
        text=True,
        timeout=30,
        env=healthcheck_env,
        check=False,
    )

    logger.info(
        "[IMP:8][smoke][healthcheck] healthcheck.sh exit: %d, stdout: %s",
        result.returncode,
        result.stdout.strip()[-300:],
    )

    if result.returncode != 0:
        logger.error(
            "[IMP:9][smoke][healthcheck] healthcheck.sh FAILED: %s",
            result.stderr.strip()[-500:],
        )

    assert result.returncode == 0, (
        f"healthcheck.sh deep failed with exit code {result.returncode}. Stderr: {result.stderr.strip()[-500:]}"
    )

    # healthcheck.sh logs go to stderr (via log_imp in lib/healthcheck.sh)
    assert "All node-metrics deep checks passed" in result.stderr, (
        "healthcheck.sh deep did not report all deep checks passed. "
        f"Stdout: {result.stdout.strip()[:300]}, Stderr: {result.stderr.strip()[:300]}"
    )
    logger.info("[IMP:9][smoke][healthcheck] ✅ healthcheck.sh deep: exit 0, all healthy")


# endregion TESTS
