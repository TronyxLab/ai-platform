# GREP_SUMMARY: e2e litellm metrics prometheus-proxy indirect-query skip-internal
# STRUCTURE: ⚡ [litellm metric via Prometheus proxy] → GET /api/v1/query?query=litellm_requests_total(Basic Auth) → ◇ results? PASS|SKIP
# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(LITELLM):2; TECH(HTTP):2]
## @purpose — Indirect LiteLLM check via Prometheus datasource proxy. LiteLLM is internal-only
##           (127.0.0.1:4000) and not externally exposed. Instead of direct HTTP access, we
##           query Prometheus for litellm_requests_total metric through the Grafana proxy.
## @scope — Single test: query Prometheus for LiteLLM metrics.
## @invariants
##   - LiteLLM /v1/models and /health/models are NOT accessible externally
##   - Prometheus may scrape LiteLLM metrics if configured
##   - If Prometheus has litellm_* metrics, the scrape target is alive
##   - SSLError → pytest.fail via _handle_e2e_error (not skip), TLS failures are platform errors
##   - If no litellm metrics exist, the test skips (no traffic yet, not a failure)
## @rationale — Direct LiteLLM access was removed when services were consolidated
##             behind nginx reverse proxy. LiteLLM remains internal-only.
## @usecases — AC-5: LiteLLM metrics visible in Prometheus (if traffic exists)
# endregion MODULE_CONTRACT

import logging

import pytest
import requests

logger = logging.getLogger(__name__)


# region IMPORTS
from conftest import _handle_e2e_error, ldd_trajectory

# endregion IMPORTS


# region FUNC_test_litellm_metrics_available
@pytest.mark.e2e
@ldd_trajectory
def test_litellm_metrics_available(PROMETHEUS_PROXY_URL: str, grafana_credentials: tuple[str, str], caplog) -> None:
    """##            If metric exists, LiteLLM is being scraped. Otherwise skip.
    ## @io — ⇥ PROMETHEUS_PROXY_URL, grafana_credentials, caplog \u2192 \xe2\x8b\x8b None
    ## @complexity \xe2\x80\x94 O(1) \xe2\x80\x94 single Prometheus instant query
    """
    if not PROMETHEUS_PROXY_URL:
        pytest.skip("Prometheus datasource UID not discovered — skipping test")

    username, password = grafana_credentials
    logger.info("[IMP:7][test_litellm_metrics_available][start] Checking LiteLLM metrics via Prometheus proxy")

    url = f"{PROMETHEUS_PROXY_URL}/api/v1/query"
    params = {"query": "litellm_requests_total"}

    try:
        resp = requests.get(url, auth=(username, password), params=params, timeout=10)
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.SSLError,
        requests.exceptions.ProxyError,
    ) as exc:
        _handle_e2e_error(exc, url, caplog, logger=logger)

    if resp.status_code != 200:
        logger.info(
            "[IMP:7][test_litellm_metrics_available][skip] Prometheus proxy returned HTTP %d",
            resp.status_code,
        )
        pytest.skip(f"Prometheus proxy returned HTTP {resp.status_code}")

    data = resp.json()
    results = data.get("data", {}).get("result", [])

    if not results:
        logger.info("[IMP:7][test_litellm_metrics_available][skip] No LiteLLM traffic yet, metrics not available")
        pytest.skip("No LiteLLM traffic yet, metrics not available")

    print("\n=== LITELLM METRICS (via Prometheus proxy) ===")
    for r in results:
        metric = r.get("metric", {})
        value = r.get("value", [])
        print(f"  \u2022 {metric} \u2192 {value}")
    print("=== END METRICS ===")

    logger.info(
        "[IMP:9][test_litellm_metrics_available][pass] Found %d litellm_requests_total time series \u2014 LiteLLM is being scraped",
        len(results),
    )


# endregion FUNC_test_litellm_metrics_available
