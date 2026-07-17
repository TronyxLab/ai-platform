# GREP_SUMMARY: e2e health grafana langfuse parametrized
# STRUCTURE: ⚡ [2 accessible services] → ○ HTTP GET with Basic Auth(none) → ◇ status 200? → ⊕ PASS|FAIL → ∑ summary_report
# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(HEALTH_CHECK):2; TECH(HTTP):2]
## @purpose — E2E health checks for 2 externally accessible observability stack services:
##           Grafana (https), Langfuse (https).
## @scope — Parametrized test across 2 accessible services; summary test for all.
## @invariants
##   - Grafana request uses Basic Auth (grafana_credentials)
##   - Langfuse /api/public/health is unauthenticated
##   - Requests timeout after 10 seconds
##   - SSLError is caught and reported — cert may be self-signed
## @rationale — Only Grafana (nginx reverse proxy) and Langfuse are directly exposed.
##             Prometheus and Loki are tested separately in test_e2e_prometheus.py and test_e2e_loki.py.
##             Internal-only services are skipped rather than failed.
## @usecases — AC-1: All 2 accessible health endpoints return 200 OK
# endregion MODULE_CONTRACT

import logging
import os
import time

import pytest
import requests
from requests.exceptions import ProxyError

logger = logging.getLogger(__name__)


# region IMPORTS
from conftest import _handle_e2e_error, ldd_trajectory

# endregion IMPORTS

# region FUNC_service_urls


@pytest.fixture(scope="module")
def service_urls(GRAFANA_URL: str) -> dict:
    """## @purpose — Map service names to their base URLs for parametrized health checks.
    ## @io — ⇥ GRAFANA_URL, E2E_LANGFUSE_URL env → ⎋ dict[str, str]
    ## @complexity — O(1)
    """
    langfuse_url = os.environ.get("E2E_LANGFUSE_URL", "https://langfuse.tronyx.ru")
    return {
        "grafana": GRAFANA_URL,
        "langfuse": langfuse_url,
    }


# endregion FUNC_service_urls


# region FUNC_test_service_health


@ldd_trajectory
@pytest.mark.e2e
@pytest.mark.parametrize(
    "service,path,needs_auth",
    [
        ("grafana", "/api/health", True),
        ("langfuse", "/api/public/health", False),
    ],
)
def test_service_health(
    service_urls: dict,
    service: str,
    path: str,
    needs_auth: bool,
    grafana_credentials: tuple[str, str],
    caplog,
) -> None:
    """## @purpose — Probe each accessible service health endpoint; assert HTTP 200.
    ## @io — ⇥ service_urls, service, path, needs_auth, grafana_credentials → ⎋ None
    ##        ⚡ Side-effect: sends HTTPS GET to resolved URL
    ## @complexity — O(1) — single HTTP request per invocation
    """

    base_url = service_urls[service]
    full_url = base_url.rstrip("/") + "/" + path.lstrip("/")

    logger.info("[IMP:7][test_service_health][start] Checking %s at %s", service, full_url)

    kwargs = {"timeout": 10}
    if needs_auth:
        username, password = grafana_credentials
        kwargs["auth"] = (username, password)
        logger.info("[IMP:7][test_service_health][auth] Using Basic Auth for %s", service)

    try:
        start = time.time()
        resp = requests.get(full_url, **kwargs)
        elapsed = round(time.time() - start, 3)

        if resp.status_code != 200:
            body_preview = resp.text[:200]
            logger.error(
                "[IMP:9][test_service_health][fail] %s responded %d (expected 200) — body: %s",
                service,
                resp.status_code,
                body_preview,
            )
            pytest.fail(
                f"[{service}] Expected status 200, got {resp.status_code}. URL: {full_url}. Response: {body_preview}"
            )

        logger.info(
            "[IMP:7][test_service_health][pass] %s \u2192 %d in %.3fs",
            service,
            resp.status_code,
            elapsed,
        )
    except (
        requests.exceptions.SSLError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        ProxyError,
    ) as exc:
        _handle_e2e_error(exc, full_url, caplog, logger=logger)


# endregion FUNC_test_service_health
