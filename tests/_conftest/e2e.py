# GREP_SUMMARY: e2e fixtures GRAFANA_URL PROMETHEUS_PROXY_URL LOKI_PROXY_URL grafana-credentials datasource-uids dotenv proxy-disable conftest-e2e
# STRUCTURE: ⚡ early dotenv load → ◇ _load_test_env(autouse) → ◇ _e2e_disable_proxy(autouse) → ◇ grafana_credentials → ◇ GRAFANA_URL → ◇ datasource_uids → ◇ PROMETHEUS_PROXY_URL → ◇ LOKI_PROXY_URL
# region MODULE_CONTRACT
## @purpose — E2E credential and URL fixtures, relocated from tests/_e2e_fixtures.py to tests/conftest/e2e.py.
##            Imported via `from tests.conftest.e2e import *` in conftest.py or directly.
## @scope — Session-scoped fixtures shared across all E2E test files.
## @invariants
##   - GRAFANA_URL defaults to "http://127.0.0.1:3000" (E2E_GRAFANA_URL), production only in CI
##   - PROMETHEUS_PROXY_URL defaults to "https://grafana.tronyx.ru/api/datasources/proxy/1" (E2E_PROMETHEUS_PROXY_URL)
##   - LOKI_PROXY_URL defaults to "https://grafana.tronyx.ru/api/datasources/proxy/2" (E2E_LOKI_PROXY_URL)
##   - grafana_credentials reads from core/modules/hermes-agent/.env via python-dotenv (graceful fallback)
##   - T12.3 (T-5): _load_test_env / _e2e_disable_proxy — SCOPED маркером `e2e` (no-op для
##     статических сессий — env pollution устранена)
##   - T12.3 (T-6): NO_PROXY восстанавливается в teardown _e2e_disable_proxy
##   - T12.8 (T-12): grafana_credentials БЕЗ пароля → pytest.fail (R4, не skip); datasource_uids
##     при недоступности Grafana / non-200 → pytest.fail (не молчаливый {} / каскад пустых URL)
## @rationale — Centralising credentials in one module avoids repetition in every E2E test file.
##              Extracted from conftest.py to reduce coupling (from ~1671 to ~1420 lines).
## @changes — 2026-08-05 | DevPlan 136 W12: T12.3 (e2e-marker scope + NO_PROXY restore), T12.8 (R4-fail)
##            2026-07-09 · TASK-10 · Removed orphan fixtures: BASE_URL, langfuse_credentials, LANGFUSE_URL
##            2026-07-12 · Extracted from conftest.py to _e2e_fixtures.py (TASK-I1)
##            2026-07-12 · Moved to tests/conftest/e2e.py
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

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
    _early_dotenv_path = Path(__file__).parent / ".." / ".." / "core" / "modules" / "hermes-agent" / ".env"
    if pathlib.Path(_early_dotenv_path).is_file():
        load_dotenv(_early_dotenv_path)


@pytest.fixture(scope="session", autouse=True)
def _load_test_env(request: pytest.FixtureRequest) -> None:
    """
    ## @purpose — Load .env from core/modules/hermes-agent/ into os.environ for ALL tests.
    ## @rationale — grafana_credentials fixture also loads .env, but only when explicitly
    ##              requested by a test. Component tests (test_component_hermes.py) read
    ##              Grafana credentials directly from os.environ without requesting
    ##              grafana_credentials, so they would skip without this autouse fixture.
    ##              Loads .env BEFORE _e2e_disable_proxy clears proxy vars.
    ##              T12.3 (T-5): SCOPED by `e2e` marker — если в сессии нет e2e-тестов,
    ##              фикстура no-op (не загрязняет os.environ статическим сьюитам).
    ## @io — ⎋ None (side-effect: os.environ populated from .env)
    ## @complexity — O(1)
    """
    # T12.3 (T-5): env pollution — .env инжектится ТОЛЬКО для сессий с e2e-маркером.
    if not _session_has_e2e_marker(request):
        logger.info("[IMP:7][conftest][_load_test_env] No e2e marker in session — env load skipped (T12.3 T-5)")
        return
    dotenv_path = Path(__file__).parent / ".." / ".." / "core" / "modules" / "hermes-agent" / ".env"
    if _DOTENV_AVAILABLE and pathlib.Path(dotenv_path).is_file():
        load_dotenv(dotenv_path)
        logger.info("%s", f"[IMP:7][conftest][_load_test_env] Loaded .env: {dotenv_path}")
    elif not _DOTENV_AVAILABLE:
        logger.info("[IMP:4][conftest][_load_test_env] python-dotenv not installed — skip")
    elif not pathlib.Path(dotenv_path).is_file():
        # Fallback: абсолютный путь на VPS (/opt/platform/...)
        alt_path = "/opt/platform/core/modules/hermes-agent/.env"
        if pathlib.Path(alt_path).is_file():
            load_dotenv(alt_path)
            dotenv_path = alt_path
            logger.info("%s", f"[IMP:7][conftest][_load_test_env] Using fallback .env: {dotenv_path}")
        else:
            logger.info("%s", f"[IMP:4][conftest][_load_test_env] .env not found at {dotenv_path} — skip")


# region FUNC_session_has_e2e_marker
## @purpose  T12.3 (T-5): scoping env-фикстур маркером `e2e` — autouse session-фикстуры
##            проверяют наличие e2e-маркера среди СОБРАННЫХ тестов и no-op при его отсутствии
##            (статические/unit/gate сессии не получают .env-инъекцию и proxy-мутацию).
## @io       ⇥ request: pytest.FixtureRequest → ⎋ bool
## @complexity O(I) где I = собранные тесты
def _session_has_e2e_marker(request: pytest.FixtureRequest) -> bool:
    """True если хотя бы один собранный тест имеет маркер e2e."""
    return any(item.get_closest_marker("e2e") for item in request.session.items)


# endregion FUNC_session_has_e2e_marker


@pytest.fixture(scope="session", autouse=True)
def _e2e_disable_proxy(request: pytest.FixtureRequest) -> None:
    """
    ## @purpose — Disable HTTP_PROXY/HTTPS_PROXY for E2E tests.
    ## @rationale — Root .env sets HTTP_PROXY=http://172.23.0.1:8118 (Privoxy on Docker host for Telegram).
    ##              requests library picks this up
    ##              and routes ALL traffic through a proxy that doesn't exist locally.
    ##              All E2E targets (tronyx.ru) are directly reachable, no proxy needed.
    ##              T12.3 (T-5): scoped by `e2e` marker (no-op для статических сессий);
    ##              T12.3 (T-6): NO_PROXY восстанавливается в teardown (не только proxy-переменные).
    ## @io — ⎋ None (mutates os.environ)
    ## @complexity — O(1)
    """
    if not _session_has_e2e_marker(request):
        logger.info("[IMP:7][conftest][_e2e_disable_proxy] No e2e marker in session — proxy untouched (T12.3 T-5)")
        yield
        return
    saved = {}
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if var in os.environ:
            saved[var] = os.environ[var]
            del os.environ[var]
    # Ensure NO_PROXY covers tronyx.ru in case proxy vars come back
    # T12.3 (T-6): исходный NO_PROXY сохраняется и восстанавливается в teardown
    no_proxy_before = os.environ.get("NO_PROXY", "")
    no_proxy = no_proxy_before
    if "tronyx.ru" not in no_proxy:
        os.environ["NO_PROXY"] = f"{no_proxy},tronyx.ru,*.tronyx.ru".strip(",")
    # [IMP:7][conftest][_e2e_disable_proxy] Proxy env vars cleared for E2E tests
    if saved:
        logger.info("%s", f"[IMP:7][conftest][_e2e_disable_proxy] Cleared proxy vars: {list(saved.keys())}")
    yield
    # Restore proxy vars after test session
    for var, val in saved.items():
        os.environ[var] = val
    # T12.3 (T-6): restore NO_PROXY (если мы его мутировали)
    if os.environ.get("NO_PROXY") != no_proxy_before:
        if no_proxy_before:
            os.environ["NO_PROXY"] = no_proxy_before
        else:
            os.environ.pop("NO_PROXY", None)


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
    dotenv_path = Path(__file__).parent / ".." / ".." / "core" / "modules" / "hermes-agent" / ".env"
    if _DOTENV_AVAILABLE and pathlib.Path(dotenv_path).is_file():
        load_dotenv(dotenv_path)
        # [IMP:7][conftest][grafana_credentials] Loaded .env from {dotenv_path}
        logger.info("%s", f"[IMP:7][conftest][grafana_credentials] Loaded .env: {dotenv_path}")
    else:
        # [IMP:4][conftest][grafana_credentials] dotenv unavailable or .env not found — fallback to os.environ
        if not _DOTENV_AVAILABLE:
            logger.info("[IMP:4][conftest][grafana_credentials] python-dotenv not installed — fallback to os.environ")
        if not pathlib.Path(dotenv_path).is_file():
            logger.info(
                "%s", f"[IMP:4][conftest][grafana_credentials] .env not found at {dotenv_path} — fallback to os.environ"
            )

    username = os.environ.get(
        "GF_SECURITY_ADMIN_USER",
        os.environ.get("HERMES_DASHBOARD_USERNAME", "admin@ai-platform.local"),
    )
    password = os.environ.get("GF_SECURITY_ADMIN_PASSWORD")

    if not password:
        # T12.8 (T-12): R4-fail вместо skip — отсутствие пароля = конфигурационная ошибка
        # (environmental absence = FAIL, не skip; Test Honesty R4)
        pytest.fail(
            "Grafana password not set — set GF_SECURITY_ADMIN_PASSWORD (Rule R4: "
            "environmental absence is a configuration error, not skip)",
            pytrace=False,
        )

    # [IMP:9][conftest][grafana_credentials] Credentials resolved
    logger.info("%s", f"[IMP:9][conftest][grafana_credentials] Grafana user = {username}")
    return (username, password)


@pytest.fixture(scope="session")
def GRAFANA_URL() -> str:
    """
    ## @purpose — Return the Grafana external URL for E2E HTTP tests.
    ## @io — ⎋ str: Grafana URL
    ## @complexity — O(1)
    """
    url = os.environ.get("E2E_GRAFANA_URL", "http://127.0.0.1:3000")
    # ⚠️ TRAP[LOCAL] · 2026-07-21 · — · Production Grafana tests only run in CI
    # ·   Default URL is localhost; production domain requires explicit E2E_GRAFANA_URL or CI=true.
    # ·   This prevents accidental production traffic from local dev machines.
    if "tronyx.ru" in url and not os.environ.get("CI"):
        pytest.skip("Production Grafana tests only run in CI. Set CI=true or E2E_GRAFANA_URL for local override.")
    # [IMP:7][conftest][GRAFANA_URL] Grafana URL resolved
    logger.info("%s", f"[IMP:7][conftest][GRAFANA_URL] Grafana URL = {url}")
    return url


@pytest.fixture(scope="session")
def datasource_uids(GRAFANA_URL: str, grafana_credentials: tuple[str, str]) -> dict:
    """
    ## @purpose — Discover Prometheus and Loki datasource UIDs from Grafana API.
    ## @io — ⎋ dict[str, str] e.g. {"prometheus": "uid1", "loki": "uid2"}
    ## @complexity — O(1) — single HTTP GET request
    ## @rationale — Hardcoded proxy UIDs (1, 2) are unreliable; dynamic discovery from Grafana ensures correctness.
    ##              T12.8 (T-12): НЕ возвращает молча {} при недоступности — R4-fail:
    ##              недоступный datasource = конфигурационная ошибка, не graceful degradation.
    """
    try:
        import requests
    except ImportError:
        pytest.fail(
            "requests not installed — required for Grafana datasource discovery (Rule R4: "
            "environmental absence = FAIL, not skip)",
            pytrace=False,
        )

    username, password = grafana_credentials
    url = f"{GRAFANA_URL}/api/datasources"
    try:
        return _query_grafana_datasources(url, username, password)
    except (requests.exceptions.RequestException, ValueError) as exc:
        # T12.8 (T-12): fail при недоступности Grafana (не молчаливый {} → каскад пустых URL)
        pytest.fail(
            f"Failed to query Grafana datasources at {url}: {exc} (Rule R4: "
            f"unavailable datasource = configuration error, not skip)",
            pytrace=False,
        )


def _query_grafana_datasources(url: str, username: str, password: str) -> dict:
    """Fetch Grafana datasources and map prometheus/loki types to UIDs.

    ## @purpose — Вынесенная логика запроса (PLW0717): GET /api/datasources →
    ##             отображение type→uid для prometheus/loki.
    ## @io — ⇥ url, username, password → ⎋ dict[str, str] ({"prometheus": uid, "loki": uid})
    ## @complexity — O(N) где N = число datasources
    """
    import requests

    resp = requests.get(url, auth=(username, password), timeout=10)
    if resp.status_code != 200:
        # T12.8 (T-12): fail при недоступности (не пустой dict)
        pytest.fail(
            f"Grafana datasources API returned HTTP {resp.status_code} (Rule R4: "
            f"unavailable datasource = configuration error, not silent empty dict)",
            pytrace=False,
        )
    datasources = resp.json()
    result = {}
    for ds in datasources:
        ds_type = ds.get("type", "")
        uid = ds.get("uid", "")
        if ds_type == "prometheus":
            result["prometheus"] = uid
        elif ds_type == "loki":
            result["loki"] = uid
    logger.info("%s", f"[IMP:9][conftest][datasource_uids] Discovered UIDs: {result}")
    return result


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
    logger.info("%s", f"[IMP:7][conftest][PROMETHEUS_PROXY_URL] Prometheus proxy URL = '{url}'")
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
    logger.info("%s", f"[IMP:7][conftest][LOKI_PROXY_URL] Loki proxy URL = '{url}'")
    return url
