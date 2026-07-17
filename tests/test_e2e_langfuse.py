# GREP_SUMMARY: e2e langfuse health public-api status-ok https
# STRUCTURE: ⚡ [health] → GET /api/public/health → ◇ status 200 + JSON.ok? → ⊕ PASS|FAIL
# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(LANGFUSE):2; TECH(HTTP):2]
## @purpose — Validate Langfuse health endpoint returns {"status":"ok"}. Uses external URL.
## @scope — Single test: Langfuse public health API.
## @invariants
##   - /api/public/health is unauthenticated
##   - Response is JSON with "status" field
##   - Langfuse URL is external (https://langfuse.tronyx.ru) via nginx reverse proxy
## @rationale — Langfuse health must pass before any trace/observation tests.
## @usecases — AC-1: Langfuse health returns ok
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


# region FUNC_test_langfuse_health
@pytest.mark.e2e
@ldd_trajectory
def test_langfuse_health(caplog) -> None:
    """## @io — ⇥ E2E_LANGFUSE_URL env, caplog → ⎋ None
    ## @complexity — O(1) — single HTTP request
    """

    langfuse_url = os.environ.get("E2E_LANGFUSE_URL", "https://langfuse.tronyx.ru")

    logger.info("[IMP:7][test_langfuse_health][start] Checking Langfuse health")

    url = f"{langfuse_url}/api/public/health"

    try:
        start = time.time()
        resp = requests.get(url, timeout=10)
        elapsed = round(time.time() - start, 3)
    except (
        requests.exceptions.SSLError,
        ProxyError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    ) as exc:
        _handle_e2e_error(exc, url, caplog)
        return

    if resp.status_code != 200:
        logger.error(
            "[IMP:9][test_langfuse_health][fail] HTTP %d (expected 200)",
            resp.status_code,
        )
        pytest.fail(f"Langfuse health endpoint returned HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError as exc:
        logger.error(
            "[IMP:9][test_langfuse_health][fail] Response is not valid JSON: %s",
            resp.text[:200],
        )
        pytest.fail(f"Langfuse health response is not JSON: {exc}")

    status_value = body.get("status")
    if not status_value or status_value.lower() != "ok":
        logger.error(
            "[IMP:9][test_langfuse_health][fail] Unexpected status: %s (expected 'ok')",
            status_value,
        )
        pytest.fail(f"Langfuse health status is '{status_value}', expected 'ok'. Full body: {body}")

    print("\n=== LANGFUSE HEALTH ===")
    print(f"  status: {body.get('status')}")
    print(f"  body:   {body}")
    print(f"  time:   {elapsed}s")
    print("=== END HEALTH ===")

    logger.info(
        "[IMP:7][test_langfuse_health][pass] Langfuse healthy (status=ok) in %.3fs",
        elapsed,
    )


# endregion FUNC_test_langfuse_health
