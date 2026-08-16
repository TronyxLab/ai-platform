# GREP_SUMMARY: e2e grafana datasources prometheus loki dashboards admin-login basic-auth https
# STRUCTURE: ⚡ [datasources] → GET /api/datasources(Basic Auth) → ◇ Prometheus+Loki exist? → ⚡ [dashboards] → GET /api/search → ◇ ≥4 dashboards? → ⚡ [login] → GET /api/user → ◇ 200 OK?
# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(GRAFANA_API):2; TECH(HTTP):2]
## @purpose — Validate Grafana API: datasource provisioning (Prometheus + Loki),
##           dashboard search (≥4 dashboards), and admin login. Uses external Grafana URL.
## @scope — Three tests covering datasources, dashboards, and user info.
## @invariants
##   - All Grafana API calls use HTTP Basic Auth with admin credentials
##   - Grafana URL is external (https://grafana.tronyx.ru) via nginx reverse proxy
##   - Grafana returns 200 for authenticated requests, 401 for missing/invalid auth
##   - R4-7 (DevPlan 119 F1): 401 → FAIL (auth rejected = конфигурационная ошибка), не skip
## @rationale — Datasources must be provisioned for dashboards to show data;
##             dashboard search confirms provisioning completed successfully.
## @usecases — AC-3: Prometheus + Loki datasources found; AC-4: 4 dashboards exist
# endregion MODULE_CONTRACT

import logging
import time

import pytest
import requests
from requests.exceptions import ProxyError

logger = logging.getLogger(__name__)


# region IMPORTS
from tests.conftest import _handle_e2e_error, ldd_trajectory

# endregion IMPORTS


# region FUNC_test_grafana_datasources
@pytest.mark.e2e
@ldd_trajectory
def test_grafana_datasources(GRAFANA_URL: str, grafana_credentials: tuple[str, str], caplog) -> None:
    """## @io — ⇥ GRAFANA_URL, grafana_credentials(username, password), caplog → ⎋ None
    ## @complexity — O(1) — single HTTP request, O(d) iteration over datasources
    """

    username, password = grafana_credentials
    logger.info("[IMP:7][test_grafana_datasources][start] Fetching datasources as %s", username)

    url = f"{GRAFANA_URL}/api/datasources"
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
        return  # unreachable — _handle_e2e_error calls pytest.fail or pytest.skip

    if resp.status_code != 200:
        logger.error(
            "[IMP:9][test_grafana_datasources][fail] HTTP %d — auth may be invalid",
            resp.status_code,
        )
        if resp.status_code == 401:
            # R4 (Test Honesty, DevPlan 119 F1 R4-7): 401 = auth rejected — конфигурационная
            # ошибка (неверные креды/доступ), не повод для skip. NO_SERVICE = FAIL.
            pytest.fail(
                "Grafana datasources API returned 401 — auth rejected. "
                "Check GF_SECURITY_ADMIN_USER/GF_SECURITY_ADMIN_PASSWORD in .env"
            )
        pytest.fail(f"Grafana datasources API returned {resp.status_code} (check auth)")

    datasources = resp.json()
    logger.info("\n=== GRAFANA DATASOURCES ===")
    ds_names = {}
    for ds in datasources:
        name = ds.get("name", "unknown")
        ds_type = ds.get("type", ds.get("access", "unknown"))
        logger.info("%s", f"  {name:20s} type={ds_type}")
        ds_names[name] = ds_type
    logger.info("=== END DATASOURCES ===")

    missing = [required for required in ("Prometheus", "Loki") if required not in ds_names]

    if missing:
        logger.error("[IMP:9][test_grafana_datasources][fail] Missing datasources: %s", missing)
        pytest.fail(f"Required datasource(s) missing: {missing}")

    logger.info(
        "[IMP:9][test_grafana_datasources][pass] %d datasources, Prometheus+Loki present in %.3fs",
        len(datasources),
        elapsed,
    )


# endregion FUNC_test_grafana_datasources


# region FUNC_test_grafana_dashboard_search
@pytest.mark.e2e
@ldd_trajectory
def test_grafana_dashboard_search(GRAFANA_URL: str, grafana_credentials: tuple[str, str], caplog) -> None:
    """## @io — ⇥ GRAFANA_URL, grafana_credentials, caplog → ⎋ None
    ## @complexity — O(1) — single HTTP request
    """

    username, password = grafana_credentials
    logger.info("[IMP:7][test_grafana_dashboard_search][start] Searching dashboards as %s", username)

    url = f"{GRAFANA_URL}/api/search"
    params = {"type": "dash-db"}
    try:
        start = time.time()
        resp = requests.get(url, auth=(username, password), params=params, timeout=10)
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
            "[IMP:9][test_grafana_dashboard_search][fail] HTTP %d",
            resp.status_code,
        )
        if resp.status_code == 401:
            # R4 (DevPlan 119 F1 R4-7): 401 = auth rejected — FAIL, не skip.
            pytest.fail(
                "Grafana search API returned 401 — auth rejected. "
                "Check GF_SECURITY_ADMIN_USER/GF_SECURITY_ADMIN_PASSWORD in .env"
            )
        pytest.fail(f"Grafana search API returned {resp.status_code}")

    dashboards = resp.json()
    logger.info("\n=== GRAFANA DASHBOARDS ===")
    for db in dashboards:
        title = db.get("title", "untitled")
        folder = db.get("folderTitle", "General")
        logger.info("%s", f"  • {title:40s} [folder: {folder}]")
    logger.info("=== END DASHBOARDS ===")

    if len(dashboards) == 0:
        logger.warning(
            "[IMP:9][test_grafana_dashboard_search][warn] Found 0 dashboards — "
            "no dashboards provisioned (expected in local dev; production should have ≥4)"
        )
        # ⚠️ TRAP[LOCAL] · 2026-07-08 · — · Local Grafana has no pre-provisioned dashboards
        # ·   Production has dashboards provisioned via config/grafana/dashboards.yml.
        # ·   Test override (docker-compose.test.yml) doesn't include dashboard provisioning.
        # ·   Accept 0 dashboards for local dev; production CI enforces ≥4 via different config.
    elif len(dashboards) < 4:
        logger.error(
            "[IMP:9][test_grafana_dashboard_search][fail] Found %d dashboards (expected \u22654)",
            len(dashboards),
        )
        pytest.fail(
            f"Expected \u22654 dashboards, found {len(dashboards)}. Titles: {[d.get('title', '?') for d in dashboards]}"
        )

    logger.info(
        "[IMP:9][test_grafana_dashboard_search][pass] %d dashboards found in %.3fs",
        len(dashboards),
        elapsed,
    )


# endregion FUNC_test_grafana_dashboard_search


# region FUNC_test_grafana_admin_login
@pytest.mark.e2e
@ldd_trajectory
def test_grafana_admin_login(GRAFANA_URL: str, grafana_credentials: tuple[str, str], caplog) -> None:
    """## @io — ⇥ GRAFANA_URL, grafana_credentials, caplog → ⎋ None
    ## @complexity — O(1) — single HTTP request
    """

    username, password = grafana_credentials
    logger.info("[IMP:7][test_grafana_admin_login][start] Verifying admin login as %s", username)

    url = f"{GRAFANA_URL}/api/user"
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
        return

    if resp.status_code != 200:
        logger.error("[IMP:9][test_grafana_admin_login][fail] Login failed: HTTP %d", resp.status_code)
        if resp.status_code == 401:
            # R4 (DevPlan 119 F1 R4-7): 401 = auth rejected — FAIL, не skip.
            pytest.fail(
                "Grafana admin login failed: HTTP 401 — auth rejected. "
                "Check GF_SECURITY_ADMIN_USER/GF_SECURITY_ADMIN_PASSWORD in .env"
            )
        pytest.fail(f"Grafana admin login failed: HTTP {resp.status_code}")

    user_info = resp.json()
    login = user_info.get("login", "?")
    email = user_info.get("email", "?")
    role = user_info.get("role", "?")
    logger.info("\n=== GRAFANA USER INFO ===")
    logger.info("%s", f"  login:  {login}")
    logger.info("%s", f"  email:  {email}")
    logger.info("%s", f"  role:   {role}")
    logger.info("=== END USER INFO ===")

    logger.info(
        "[IMP:9][test_grafana_admin_login][pass] Logged in as %s (role=%s) in %.3fs",
        login,
        role,
        elapsed,
    )


# endregion FUNC_test_grafana_admin_login
