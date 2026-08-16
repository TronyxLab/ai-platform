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
##   - R4-6 (DevPlan 119 F1): если литellm-метрик нет — генерируется тестовый трафик,
##     при отсутствии метрик после генерации → FAIL (не skip) — scrape-таргет сломан
## @rationale — Direct LiteLLM access was removed when services were consolidated
##             behind nginx reverse proxy. LiteLLM remains internal-only.
## @usecases — AC-5: LiteLLM metrics visible in Prometheus (if traffic exists)
# endregion MODULE_CONTRACT

import logging
import os

import pytest
import requests

logger = logging.getLogger(__name__)


# region IMPORTS
from tests.conftest import _handle_e2e_error, ldd_trajectory

# endregion IMPORTS


# region FUNC__generate_litellm_traffic
def _generate_litellm_traffic(url: str, username: str, password: str, caplog, logger=None) -> None:
    """Generate a test request to LiteLLM so Prometheus scrape picks up a metric.

    ## @purpose — R4-6 (DevPlan 119 F1): при отсутствии litellm_requests_total — сгенерировать
    ##            тестовый запрос к LiteLLM API (best-effort). LiteLLM internal-only
    ##            (127.0.0.1:4000) — через nginx; в тестовом стеке порт 14000 (LITELLM_TEST_PORT).
    ##            Даже отклонённый auth-запрос создаёт litellm_requests_total-инкремент у
    ##            Litellm, поэтому POST достаточно. Не фатален при недоступности (логируется).
    ## @io — ⇥ url (Prometheus proxy URL), username, password, caplog → ⎋ None
    ## @complexity — O(1) — single HTTP POST
    ## @invariants
    ##   - Генерация best-effort: ошибки не фатальны (retry-query решает FAIL)
    ##   - Порт LiteLLM: LITELLM_TEST_PORT env (default 14000) — тестовый override
    ##   - POST /v1/chat/completions с минимальным телом (model + messages)
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    litellm_port = int(os.environ.get("LITELLM_TEST_PORT", "14000"))
    litellm_url = os.environ.get("E2E_LITELLM_URL", f"http://127.0.0.1:{litellm_port}")
    chat_url = f"{litellm_url}/v1/chat/completions"
    api_key = os.environ.get("LITELLM_MASTER_KEY", os.environ.get("LITELLM_KEY", "sk-test-master-key"))

    logger.info("[IMP:7][_generate_litellm_traffic] POST %s (test traffic)", chat_url)
    try:
        requests.post(
            chat_url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "ping"}]},
            timeout=10,
        )
        logger.info("[IMP:8][_generate_litellm_traffic] Test request sent (any HTTP status = counter increment)")
    except (requests.exceptions.RequestException, OSError) as exc:
        logger.warning("[IMP:7][_generate_litellm_traffic] Traffic generation failed (non-fatal): %s", exc)


# endregion FUNC__generate_litellm_traffic


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
        # R4-6 (DevPlan 119 F1) + W5 T5.1: прокси ОТВЕТИЛ (TCP service healthy) — non-200
        # означает конфигурационную ошибку scrape-прокси/datasource, а не отсутствие
        # сервиса в окружении. skip-as-bug-masking запрещён → FAIL безусловно.
        logger.error(
            "[IMP:9][test_litellm_metrics_available][fail] Prometheus proxy returned HTTP %d",
            resp.status_code,
        )
        pytest.fail(f"Prometheus proxy returned HTTP {resp.status_code} — scrape proxy broken")

    data = resp.json()
    results = data.get("data", {}).get("result", [])

    if not results:
        # R4 (Test Honesty, DevPlan 119 F1 R4-6): отсутствие метрик LiteLLM =
        # конфигурационная ошибка (scrape-таргет не работает / трафик не генерируется),
        # не повод для skip. Генерируем тестовый запрос к API — при недоступности FAIL.
        # В CI (REQUIRE_HONESTY_MODE=fail / E2E_MODE=ci) — FAIL; локально (marker) — skip.
        logger.warning(
            "[IMP:8][test_litellm_metrics_available][retry] No LiteLLM traffic yet — generating test request",
        )
        _generate_litellm_traffic(url, username, password, caplog, logger=logger)

        # После генерации трафика — повторный запрос (1 retry, R4-5 контракт)
        try:
            resp2 = requests.get(url, auth=(username, password), params=params, timeout=10)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.SSLError,
            requests.exceptions.ProxyError,
        ) as exc:
            _handle_e2e_error(exc, url, caplog, logger=logger)

        if resp2.status_code != 200:
            logger.error(
                "[IMP:9][test_litellm_metrics_available][fail] Prometheus proxy returned HTTP %d after traffic generation",
                resp2.status_code,
            )
            pytest.fail(
                f"Prometheus proxy returned HTTP {resp2.status_code} after traffic generation — "
                "LiteLLM scrape target not working"
            )

        results = resp2.json().get("data", {}).get("result", [])
        if not results:
            logger.error(
                "[IMP:9][test_litellm_metrics_available][fail] No litellm_requests_total after traffic generation",
            )
            pytest.fail(
                "No litellm_requests_total metric after test traffic generation — LiteLLM "
                "scrape target is not being collected by Prometheus"
            )
        data = resp2.json()

    logger.info("\n=== LITELLM METRICS (via Prometheus proxy) ===")
    for r in results:
        metric = r.get("metric", {})
        value = r.get("value", [])
        logger.info("%s", f"  • {metric} → {value}")
    logger.info("=== END METRICS ===")

    # R1 (B10 T1): the pass path must be falsifiable — assert the Prometheus
    # series shape. Malformed series (missing metric/value keys) = real failure.
    assert results, "Prometheus returned no litellm_requests_total time series"
    for series in results:
        assert isinstance(series, dict), f"Malformed Prometheus series (not a dict): {series!r}"
        assert "metric" in series and "value" in series, (
            f"Malformed Prometheus series (missing metric/value): {series!r}"
        )

    logger.info(
        "[IMP:9][test_litellm_metrics_available][pass] Found %d litellm_requests_total time series \u2014 LiteLLM is being scraped",
        len(results),
    )


# endregion FUNC_test_litellm_metrics_available
