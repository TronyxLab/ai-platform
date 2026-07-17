# GREP_SUMMARY: e2e fixtures GRAFANA_URL PROMETHEUS_PROXY_URL LOKI_PROXY_URL grafana-credentials datasource-uids dotenv proxy-disable conftest-e2e
# STRUCTURE: ⚡ early dotenv load → ◇ _load_test_env(autouse) → ◇ _e2e_disable_proxy(autouse) → ◇ grafana_credentials → ◇ GRAFANA_URL → ◇ datasource_uids → ◇ PROMETHEUS_PROXY_URL → ◇ LOKI_PROXY_URL
# region MODULE_CONTRACT
## @purpose — E2E credential and URL fixtures, relocated from tests/_e2e_fixtures.py to tests/conftest/e2e.py.
##            Imported via `from tests.conftest.e2e import *` in conftest.py or directly.
## @scope — Session-scoped fixtures shared across all E2E test files.
## @invariants
##   - GRAFANA_URL defaults to "https://grafana.tronyx.ru" (E2E_GRAFANA_URL)
##   - PROMETHEUS_PROXY_URL defaults to "https://grafana.tronyx.ru/api/datasources/proxy/1" (E2E_PROMETHEUS_PROXY_URL)
##   - LOKI_PROXY_URL defaults to "https://grafana.tronyx.ru/api/datasources/proxy/2" (E2E_LOKI_PROXY_URL)
##   - grafana_credentials reads from core/modules/hermes-agent/.env via python-dotenv (graceful fallback)
## @rationale — Centralising credentials in one module avoids repetition in every E2E test file.
##              Extracted from conftest.py to reduce coupling (from ~1671 to ~1420 lines).
## @changes — 2026-07-09 · TASK-10 · Removed orphan fixtures: BASE_URL, langfuse_credentials, LANGFUSE_URL
##            2026-07-12 · Extracted from conftest.py to _e2e_fixtures.py (TASK-I1)
##            2026-07-12 · Moved to tests/conftest/e2e.py
# endregion MODULE_CONTRACT

import os
import sys

import pytest

# Attempt to load python-dotenv with graceful fallback
try:
    from dotenv import load_dotenv

    _DOTENV_AVAILABLE = True
except ImportError:
    load_dotenv = None  # type: ignore[assignment]
    _DOTENV_AVAILABLE = False

# Early load: загружаем .env на уровне модуля, чтобы os.environ был доступен
# при импорте тестовых файлов (HERMES_PASS и др. модульные константы).
# Это ДО выполнения session fixtures.
if _DOTENV_AVAILABLE:
    _early_dotenv_path = os.path.join(os.path.dirname(__file__), "..", "..", "core", "modules", "hermes-agent", ".env")
    if os.path.isfile(_early_dotenv_path):
        load_dotenv(_early_dotenv_path)


@pytest.fixture(scope="session", autouse=True)
def _load_test_env() -> None:
    """
    ## @purpose — Load .env from core/modules/hermes-agent/ into os.environ for ALL tests.
    ## @rationale — grafana_credentials fixture also loads .env, but only when explicitly
    ##              requested by a test. Component tests (test_component_hermes.py) read
    ##              Grafana credentials directly from os.environ without requesting
    ##              grafana_credentials, so they would skip without this autouse fixture.
    ##              Loads .env BEFORE _e2e_disable_proxy clears proxy vars.
    ## @io — ⎋ None (side-effect: os.environ populated from .env)
    ## @complexity — O(1)
    """
    dotenv_path = os.path.join(os.path.dirname(__file__), "..", "..", "core", "modules", "hermes-agent", ".env")
    if _DOTENV_AVAILABLE and os.path.isfile(dotenv_path):
        load_dotenv(dotenv_path)
        print(f"[IMP:7][conftest][_load_test_env] Loaded .env: {dotenv_path}", file=sys.stderr)
    elif not _DOTENV_AVAILABLE:
        print("[IMP:4][conftest][_load_test_env] python-dotenv not installed — skip", file=sys.stderr)
    elif not os.path.isfile(dotenv_path):
        # Fallback: абсолютный путь на VPS (/opt/platform/...)
        alt_path = "/opt/platform/core/modules/hermes-agent/.env"
        if os.path.isfile(alt_path):
            load_dotenv(alt_path)
            dotenv_path = alt_path
            print(f"[IMP:7][conftest][_load_test_env] Using fallback .env: {dotenv_path}", file=sys.stderr)
        else:
            print(f"[IMP:4][conftest][_load_test_env] .env not found at {dotenv_path} — skip", file=sys.stderr)


@pytest.fixture(scope="session", autouse=True)
def _e2e_disable_proxy() -> None:
    """
    ## @purpose — Disable HTTP_PROXY/HTTPS_PROXY for E2E tests.
    ## @rationale — Root .env sets HTTP_PROXY=http://172.23.0.1:8118 (Privoxy on Docker host for Telegram).
    ##              requests library picks this up
    ##              and routes ALL traffic through a proxy that doesn't exist locally.
    ##              All E2E targets (tronyx.ru) are directly reachable, no proxy needed.
    ## @io — ⎋ None (mutates os.environ)
    ## @complexity — O(1)
    """
    saved = {}
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if var in os.environ:
            saved[var] = os.environ[var]
            del os.environ[var]
    # Ensure NO_PROXY covers tronyx.ru in case proxy vars come back
    no_proxy = os.environ.get("NO_PROXY", "")
    if "tronyx.ru" not in no_proxy:
        os.environ["NO_PROXY"] = f"{no_proxy},tronyx.ru,*.tronyx.ru".strip(",")
    # [IMP:7][conftest][_e2e_disable_proxy] Proxy env vars cleared for E2E tests
    if saved:
        print(f"[IMP:7][conftest][_e2e_disable_proxy] Cleared proxy vars: {list(saved.keys())}", file=sys.stderr)
    yield
    # Restore proxy vars after test session
    for var, val in saved.items():
        os.environ[var] = val


# 🧐 TRAP[DECISION] · 2026-07-09 · — · Removed BASE_URL, langfuse_credentials, LANGFUSE_URL orphan fixtures
# · Rejected: keeping them as "convenience" fixtures for E2E tests
# · Reason: BASE_URL was deprecated (no test used it), langfuse_credentials was
#   orphan (no test requested it), LANGFUSE_URL: test_e2e_health.py and
#   test_e2e_langfuse.py used it but they define their own local fallback
#   via E2E_LANGFUSE_URL. Removing from conftest.py prevents stale fixture
#   proliferation. E2E tests that need these values should define them locally.
# · Rev: if a new E2E test needs LANGFUSE_URL, define it in that test's local
#   conftest or use os.environ.get("E2E_LANGFUSE_URL") directly.


@pytest.fixture(scope="session")
def grafana_credentials() -> tuple[str, str]:
    """
    ## @purpose — Read Grafana admin credentials from core/modules/hermes-agent/.env.
    ## @io — ⎋ (str username, str password)
    ## @complexity — O(1)
    ## @rationale — .env is the single source of truth for dashboard credentials.
    ##              No fallback passwords — missing env → pytest.skip().
    """
    dotenv_path = os.path.join(os.path.dirname(__file__), "..", "..", "core", "modules", "hermes-agent", ".env")
    if _DOTENV_AVAILABLE and os.path.isfile(dotenv_path):
        load_dotenv(dotenv_path)
        # [IMP:7][conftest][grafana_credentials] Loaded .env from {dotenv_path}
        print(f"[IMP:7][conftest][grafana_credentials] Loaded .env: {dotenv_path}", file=sys.stderr)
    else:
        # [IMP:4][conftest][grafana_credentials] dotenv unavailable or .env not found — fallback to os.environ
        if not _DOTENV_AVAILABLE:
            print(
                "[IMP:4][conftest][grafana_credentials] python-dotenv not installed — fallback to os.environ",
                file=sys.stderr,
            )
        if not os.path.isfile(dotenv_path):
            print(
                f"[IMP:4][conftest][grafana_credentials] .env not found at {dotenv_path} — fallback to os.environ",
                file=sys.stderr,
            )

    username = os.environ.get(
        "GF_SECURITY_ADMIN_USER",
        os.environ.get("HERMES_DASHBOARD_USERNAME", "admin"),
    )
    password = os.environ.get("GF_SECURITY_ADMIN_PASSWORD")

    if not password:
        pytest.skip("Grafana password not set — set GF_SECURITY_ADMIN_PASSWORD")

    # [IMP:9][conftest][grafana_credentials] Credentials resolved
    print(f"[IMP:9][conftest][grafana_credentials] Grafana user = {username}", file=sys.stderr)
    return (username, password)


@pytest.fixture(scope="session")
def GRAFANA_URL() -> str:
    """
    ## @purpose — Return the Grafana external URL for E2E HTTP tests.
    ## @io — ⎋ str: Grafana URL
    ## @complexity — O(1)
    """
    url = os.environ.get("E2E_GRAFANA_URL", "https://grafana.tronyx.ru")
    # [IMP:7][conftest][GRAFANA_URL] Grafana URL resolved
    print(f"[IMP:7][conftest][GRAFANA_URL] Grafana URL = {url}", file=sys.stderr)
    return url


@pytest.fixture(scope="session")
def datasource_uids(GRAFANA_URL: str, grafana_credentials: tuple[str, str]) -> dict:
    """
    ## @purpose — Discover Prometheus and Loki datasource UIDs from Grafana API.
    ## @io — ⎋ dict[str, str] e.g. {"prometheus": "uid1", "loki": "uid2"}
    ## @complexity — O(1) — single HTTP GET request
    ## @rationale — Hardcoded proxy UIDs (1, 2) are unreliable; dynamic discovery from Grafana ensures correctness.
    """
    try:
        import requests
    except ImportError:
        print("[IMP:9][conftest][datasource_uids] requests not installed — returning empty dict", file=sys.stderr)
        return {}

    username, password = grafana_credentials
    url = f"{GRAFANA_URL}/api/datasources"
    try:
        resp = requests.get(url, auth=(username, password), timeout=10)
        if resp.status_code != 200:
            print(
                f"[IMP:9][conftest][datasource_uids] Grafana returned HTTP {resp.status_code} — returning empty dict",
                file=sys.stderr,
            )
            return {}
        datasources = resp.json()
        result = {}
        for ds in datasources:
            ds_type = ds.get("type", "")
            uid = ds.get("uid", "")
            if ds_type == "prometheus":
                result["prometheus"] = uid
            elif ds_type == "loki":
                result["loki"] = uid
        print(f"[IMP:9][conftest][datasource_uids] Discovered UIDs: {result}", file=sys.stderr)
        return result
    except (requests.exceptions.RequestException, ValueError) as exc:
        print(
            f"[IMP:9][conftest][datasource_uids] Failed to query Grafana: {exc} — returning empty dict", file=sys.stderr
        )
        return {}


@pytest.fixture(scope="session")
def PROMETHEUS_PROXY_URL(GRAFANA_URL: str, datasource_uids: dict) -> str:
    """
    ## @purpose — Return the Prometheus Grafana datasource proxy URL for E2E HTTP tests.
    ## @io — ⎋ str: Prometheus proxy URL (empty string if datasource not found)
    ## @complexity — O(1)
    ## @rationale — Prometheus is not externally exposed (127.0.0.1:9090);
    ##              accessed through Grafana datasource proxy via dynamically discovered UID (/api/datasources/proxy/uid/{uid}).
    """
    env_url = os.environ.get("E2E_PROMETHEUS_PROXY_URL")
    if env_url:
        url = env_url
    elif datasource_uids.get("prometheus"):
        url = f"{GRAFANA_URL}/api/datasources/proxy/uid/{datasource_uids['prometheus']}"
    else:
        url = ""
    # [IMP:7][conftest][PROMETHEUS_PROXY_URL] Prometheus proxy URL resolved
    print(f"[IMP:7][conftest][PROMETHEUS_PROXY_URL] Prometheus proxy URL = '{url}'", file=sys.stderr)
    return url


@pytest.fixture(scope="session")
def LOKI_PROXY_URL(GRAFANA_URL: str, datasource_uids: dict) -> str:
    """
    ## @purpose — Return the Loki Grafana datasource proxy URL for E2E HTTP tests.
    ## @io — ⎋ str: Loki proxy URL (empty string if datasource not found)
    ## @complexity — O(1)
    ## @rationale — Loki is not externally exposed (127.0.0.1:3100);
    ##              accessed through Grafana datasource proxy via dynamically discovered UID (/api/datasources/proxy/uid/{uid}).
    """
    env_url = os.environ.get("E2E_LOKI_PROXY_URL")
    if env_url:
        url = env_url
    elif datasource_uids.get("loki"):
        url = f"{GRAFANA_URL}/api/datasources/proxy/uid/{datasource_uids['loki']}"
    else:
        url = ""
    # [IMP:7][conftest][LOKI_PROXY_URL] Loki proxy URL resolved
    print(f"[IMP:7][conftest][LOKI_PROXY_URL] Loki proxy URL = '{url}'", file=sys.stderr)
    return url
