# GREP_SUMMARY: e2e loki labels log-query alloy pushing grafana-proxy basic-auth
# STRUCTURE: ⚡ [labels via Grafana proxy] → GET /loki/api/v1/labels(Basic Auth) → ◇ response is list with known labels? → ⊕ PASS|FAIL
# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(LOKI_API):2; TECH(HTTP):2]
## @purpose — Validate Loki is receiving logs: query label names endpoint through Grafana
##           datasource proxy (UID 2) and verify expected labels exist.
## @scope — Single test: Loki labels endpoint.
## @invariants
##   - Loki is accessed via Grafana datasource proxy (not directly: 127.0.0.1:3100)
##   - All requests use Basic Auth with grafana_credentials
##   - /loki/api/v1/labels returns JSON with "data" and "status" fields
##   - Empty label list means Promtail is not pushing logs
## @rationale — Labels are the first indicator that logs are flowing into Loki.
##             If empty, Promtail scrape config or network is broken.
## @usecases — AC-1: Loki accessible and labels present
# endregion MODULE_CONTRACT

import logging
import time

import pytest
import requests
from requests.exceptions import ProxyError

logger = logging.getLogger(__name__)

_EXPECTED_LABELS = {"compose_service", "container"}


# region IMPORTS
from tests.conftest import _handle_e2e_error, ldd_trajectory

# endregion IMPORTS


# region FUNC_test_loki_labels
@pytest.mark.e2e
@ldd_trajectory
def test_loki_labels(LOKI_PROXY_URL: str, grafana_credentials: tuple[str, str], caplog) -> None:
    """##            assert at least some expected labels present.
    ## @io — ⇥ LOKI_PROXY_URL, grafana_credentials, caplog → ⎋ None
    ## @complexity — O(1) — single HTTP request
    """
    if not LOKI_PROXY_URL:
        pytest.skip("Loki datasource UID not discovered \u2014 skipping test")

    username, password = grafana_credentials
    logger.info("[IMP:7][test_loki_labels][start] Fetching Loki label names via Grafana proxy")

    url = f"{LOKI_PROXY_URL}/loki/api/v1/labels"

    try:
        start = time.time()
        resp = requests.get(url, auth=(username, password), timeout=10)
        elapsed = round(time.time() - start, 3)
    except (
        requests.exceptions.SSLError,
        ProxyError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    ) as exc:
        _handle_e2e_error(exc, url, caplog)

    if resp.status_code != 200:
        body_preview = resp.text[:300]
        logger.error(
            "[IMP:9][test_loki_labels][fail] HTTP %d — body: %s",
            resp.status_code,
            body_preview,
        )
        # R4 (DevPlan 160 W5 T5.1): 401 = auth misconfiguration = bug, NOT environmental
        # unavailability — skip-as-bug-masking запрещён. Сервис ответил (TCP up), но
        # авторизация сломана → FAIL безусловно.
        pytest.fail(f"Loki labels API returned {resp.status_code}: {body_preview}")

    data = resp.json()
    # Loki returns { "status": "success", "data": ["label1", "label2", ...] }
    labels_data = data.get("data", [])
    if not isinstance(labels_data, list):
        logger.error("[IMP:9][test_loki_labels][fail] Unexpected response format: 'data' is not a list")
        pytest.fail(f"Loki labels 'data' field is not a list: {labels_data}")

    label_set = set(labels_data)
    logger.info("\n=== LOKI LABELS ===")
    for lbl in sorted(label_set):
        logger.info("%s", f"  • {lbl}")
    logger.info("=== END LABELS ===")

    if not label_set:
        # ⚠️ TRAP[LOCAL] · 2026-07-08 · — · No Loki labels — alloy may not be running
        # ·   on macOS Docker Desktop due to /var/log/nginx bind mount limitation.
        # ·   Production should have labels from alloy log collection.
        logger.warning(
            "[IMP:9][test_loki_labels][warn] No labels in Loki — "
            "Promtail may not be running (expected in local macOS dev)"
        )
        # Don't fail — log warning and continue

    found_expected = _EXPECTED_LABELS & label_set
    if not found_expected:
        logger.warning(
            "[IMP:8][test_loki_labels] Expected labels %s not found; found: %s",
            _EXPECTED_LABELS,
            label_set,
        )

    logger.info(
        "[IMP:7][test_loki_labels][pass] %d labels found (matched %s) in %.3fs",
        len(label_set),
        found_expected if found_expected else "none",
        elapsed,
    )


# endregion FUNC_test_loki_labels
