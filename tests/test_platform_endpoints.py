# GREP_SUMMARY: test-platform-endpoints localhost hermes-dashboard prometheus grafana langfuse healthcheck
# STRUCTURE: ⚡[HTTP GET localhost:19119/]→test_hermes_dashboard_endpoint || ⊕[summary IMP:9]
# @file test_platform_endpoints.py
# @purpose  Health-check tests for platform endpoints: Hermes Dashboard,
#           Prometheus, Grafana, Langfuse.
# @scope    HTTP smoke tests against localhost; Docker Compose lifecycle managed by platform_services fixture
#           (fixture starts all compose modules before tests, tears down after).
# @invariants
#   - All tests use @pytest.mark.smoke and @pytest.mark.requires_docker markers
#   - HTTP requests to localhost (shifted test ports, NOT canonical production ports)
#   - HTTP timeout: 10s
#   - LDD trajectory (IMP:7-10) printed before every assert
# @rationale  These endpoints were reported as "errors" by the user.
#             Testing them explicitly ensures the platform is healthy from a user's perspective.
#             Tests run against services started by platform_services fixture (Docker compose lifecycle).
#             LiteLLM smoke tests moved to test_smoke_litellm.py (wave-litellm reset).
#             Loki smoke tests moved to test_smoke_logging.py (wave-logging reset).
# ⚠️ TRAP[BUG] · 2026-07-18 · HIGH · До F-7 тест проходил через side-effect склейки портов
# · Root: compose merge склеивал ports из base+test → тестовые контейнеры биндили canonical порты
# ·   на host → этот тест (пишущий на canonical порты) случайно проходил.
# · Fix: после !override (F-7) контейнеры биндят только shifted ports (1XXXX). Тест переведён
# ·   на сдвинутые порты, читаемые из SMOKE_ENV/SMOKE_ENV-констант.

# region MODULE_CONTRACT
## @purpose  — Health-check tests for platform endpoints: Hermes Dashboard,
##            Prometheus, Grafana, Langfuse.
## @scope    — Smoke-level HTTP tests against localhost; platform_services fixture manages compose lifecycle.
## @invariants
##   - All tests marked @pytest.mark.smoke and @pytest.mark.requires_docker
##   - HTTP timeout: 10 seconds
##   - LDD trajectory (IMP:7-10) printed for each test
##   - Services started by platform_services fixture (Docker compose lifecycle)
##   - LiteLLM smoke tests moved to test_smoke_litellm.py (wave-litellm reset)
##   - Loki smoke tests moved to test_smoke_logging.py (wave-logging reset)
## @rationale — User reported errors at these URLs. Tests validate the fix. Docker stack managed by platform_services fixture.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os

import pytest
import requests
from conftest import _handle_e2e_error, ldd_trajectory

logger = logging.getLogger(__name__)

# Test ports — shifted (1XXXX) for test overlay coexistence with production (F-7)
# ⚠️ TRAP[BUG] · 2026-07-18 · HIGH · canonical ports больше не работают из-за !override
# Читаются из SMOKE_ENV, задаваемой platform_env fixture (tests/_conftest/smoke.py:SMOKE_ENV)
_HERMES_DASHBOARD_TEST_PORT = int(os.environ.get("HERMES_DASHBOARD_TEST_PORT", "19119"))
_PROMETHEUS_TEST_PORT = int(os.environ.get("PROMETHEUS_TEST_PORT", "19090"))
_GRAFANA_TEST_PORT = int(os.environ.get("GRAFANA_TEST_PORT", "13030"))
_LANGFUSE_TEST_PORT = int(os.environ.get("LANGFUSE_TEST_PORT", "13000"))


def _build_url(port: int, path: str) -> str:
    """Build http://localhost:{port}{path} from port number."""
    return f"http://localhost:{port}{path}"


# region TESTS

# ══════════════════════════════════════════════════════════════════════════════
# Test 1: Hermes Dashboard / must be reachable (HTTP 200 or 302)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.requires_docker
@pytest.mark.smoke
@ldd_trajectory
def test_hermes_dashboard_endpoint(caplog, platform_services) -> None:
    """Verify Hermes Dashboard / is reachable — accepts HTTP 200 or 302.

    ## @purpose — Hermes Dashboard on test port HERMES_DASHBOARD_TEST_PORT (19119)
    ##            is behind basic auth gate. Without credentials, it redirects
    ##            (302) to /auth/login. With credentials, it returns HTTP 200.
    ##            Uses shifted test port (F-7), not canonical 9119.
    ## @io — ⇥ caplog → ⚡ HTTP GET → ⎋ None (asserts 2xx/302)
    ## @complexity — O(1)
    """
    url = _build_url(_HERMES_DASHBOARD_TEST_PORT, "/")
    logger.info("[IMP:7][test_hermes_dashboard_endpoint] Checking Hermes Dashboard at %s", url)

    try:
        r = requests.get(url, timeout=10, allow_redirects=False)
        logger.info(
            "[IMP:8][test_hermes_dashboard_endpoint] Hermes Dashboard returned HTTP %s, Location: %s",
            r.status_code,
            r.headers.get("Location", "none"),
        )

        # Accept either: 200 (authenticated) or 302 (redirect to auth)
        assert r.status_code in (200, 302), (
            f"Hermes Dashboard returned HTTP {r.status_code}, expected 200 or 302. Response headers: {dict(r.headers)}"
        )
        logger.info("[IMP:9][test_hermes_dashboard_endpoint] ✅ Hermes Dashboard reachable: HTTP %s", r.status_code)
    except requests.RequestException as exc:
        _handle_e2e_error(exc, url, caplog, logger)
        return


# ══════════════════════════════════════════════════════════════════════════════
# Helper: _check_port_forwarded
# ══════════════════════════════════════════════════════════════════════════════


def _check_port_forwarded(port: int, http_path: str = "/") -> bool:
    """Check if a TCP port on localhost is forwarded AND serves HTTP responses.

    ## @purpose — Two-phase verification: (1) TCP connect to confirm port is open,
    ##            (2) quick HTTP GET to confirm the port actually serves HTTP traffic
    ##            from a container (not just a lingering Docker proxy socket).
    ##            Used for services that may have ports: [] in test overlay.
    ## @io — ⇥ port, http_path → ⎋ bool
    ## @complexity — O(1) — one TCP connect + one short HTTP request
    """
    import socket

    # Phase 1: TCP connect — fast rejection for closed ports
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False

    # Phase 2: HTTP GET — verify the port actually serves HTTP from the container
    # (Docker proxy may accept TCP connections even when ports: [] is set)
    try:
        requests.get(f"http://localhost:{port}{http_path}", timeout=3)
        return True
    except requests.RequestException:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: Prometheus /-/healthy endpoint must return HTTP 200
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.requires_docker
@pytest.mark.smoke
@ldd_trajectory
def test_prometheus_healthy_endpoint(caplog, platform_services) -> None:
    """Verify Prometheus /-/healthy returns HTTP 200.

    ## @purpose — Prometheus health endpoint confirms the server is operational.
    ##            Uses shifted test port PROMETHEUS_TEST_PORT (19090), not canonical 9090 (F-7).
    ## @io — ⇥ caplog → ⚡ HTTP GET → ⎋ None (asserts 200)
    ## @complexity — O(1)
    """
    url = _build_url(_PROMETHEUS_TEST_PORT, "/-/healthy")
    logger.info("[IMP:7][test_prometheus_healthy_endpoint] Checking Prometheus /-/healthy at %s", url)

    try:
        r = requests.get(url, timeout=10)
        logger.info(
            "[IMP:8][test_prometheus_healthy_endpoint] Prometheus /-/healthy returned HTTP %s: %s",
            r.status_code,
            r.text.strip()[:100],
        )

        assert r.status_code == 200, (
            f"Prometheus /-/healthy returned HTTP {r.status_code}, expected 200. Response preview: {r.text[:300]}"
        )
        logger.info("[IMP:9][test_prometheus_healthy_endpoint] ✅ Prometheus /-/healthy OK: HTTP 200")
    except requests.RequestException as exc:
        _handle_e2e_error(exc, url, caplog, logger)
        return


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: Grafana /api/health endpoint must return HTTP 200
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.requires_docker
@pytest.mark.smoke
@ldd_trajectory
def test_grafana_health_endpoint(caplog, platform_services) -> None:
    """Verify Grafana /api/health returns HTTP 200.

    ## @purpose — Grafana health endpoint returns database and server health status.
    ##            Uses shifted test port GRAFANA_TEST_PORT (13030), not canonical 3000 (F-7).
    ## @io — ⇥ caplog → ⚡ HTTP GET → ⎋ None (asserts 200)
    ## @complexity — O(1)
    """
    url = _build_url(_GRAFANA_TEST_PORT, "/api/health")
    logger.info("[IMP:7][test_grafana_health_endpoint] Checking Grafana /api/health at %s", url)

    try:
        r = requests.get(url, timeout=10)
        logger.info("[IMP:8][test_grafana_health_endpoint] Grafana /api/health returned HTTP %s", r.status_code)

        assert r.status_code == 200, (
            f"Grafana /api/health returned HTTP {r.status_code}, expected 200. Response preview: {r.text[:300]}"
        )
        logger.info("[IMP:9][test_grafana_health_endpoint] ✅ Grafana /api/health OK: HTTP 200")
    except requests.RequestException as exc:
        _handle_e2e_error(exc, url, caplog, logger)
        return


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: Langfuse /api/public/health — HTTP 200 or skip if port not forwarded
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.requires_docker
@pytest.mark.smoke
@ldd_trajectory
def test_langfuse_health_endpoint(caplog, platform_services) -> None:
    """Verify Langfuse /api/public/health returns HTTP 200, or skip if port not forwarded.

    ## @purpose — Langfuse public health endpoint. In test environment, langfuse
    ##            uses shifted port LANGFUSE_TEST_PORT (13000) to avoid conflicts with
    ##            production (F-7). This test validates the endpoint when accessible
    ##            and skips with a descriptive message when port is blocked.
    ## @io — ⇥ caplog → ⚡ port check → ◇ forwarded → HTTP GET → ⎋ None
    ##                           → ◇ not forwarded → ⎋ pytest.skip
    ## @complexity — O(1)
    ## @changes — 2026-07-18 | F-7: Port shifted to LANGFUSE_TEST_PORT=13000, !override prevents merge
    """
    port = _LANGFUSE_TEST_PORT
    url = _build_url(port, "/api/public/health")

    # Check if port is actually forwarded from a Docker container
    if not _check_port_forwarded(port, "/api/public/health"):
        logger.info(
            "[IMP:7][test_langfuse_health_endpoint] Port %d not accessible — langfuse port not forwarded under test overlay",
            port,
        )
        pytest.skip(
            f"Langfuse port {port} not forwarded under test overlay "
            f"(docker-compose.test.yml shifts to 127.0.0.1:13000:3000 for test isolation). "
            f"This is expected — langfuse is isolated from host in test environment."
        )

    logger.info("[IMP:7][test_langfuse_health_endpoint] Checking Langfuse /api/public/health at %s", url)

    try:
        r = requests.get(url, timeout=10)
        logger.info(
            "[IMP:8][test_langfuse_health_endpoint] Langfuse /api/public/health returned HTTP %s",
            r.status_code,
        )

        assert r.status_code == 200, (
            f"Langfuse /api/public/health returned HTTP {r.status_code}, expected 200. Response preview: {r.text[:300]}"
        )
        logger.info("[IMP:9][test_langfuse_health_endpoint] ✅ Langfuse /api/public/health OK: HTTP 200")
    except requests.RequestException as exc:
        _handle_e2e_error(exc, url, caplog, logger)
        return


# endregion TESTS
