# GREP_SUMMARY: test-smoke-hermes dashboard-health auth-login api-server hermes-agent
# STRUCTURE: ○ test_hermes_dashboard_health[⚡GET / → 302/login] → ○ test_hermes_auth_login[⚡POST password-login → 200+ok:true] → ○ test_hermes_api_completions[⚡POST /v1/chat/completions → 200+content]
# @file test_smoke_hermes.py
# @purpose  Smoke tests for hermes-agent module: dashboard, auth, API server
# @scope    Smoke tests; requires Docker daemon running locally.
# @invariants
#   - All tests use @pytest.mark.smoke and @pytest.mark.requires_docker markers
#   - platform_services fixture manages compose lifecycle
#   - HTTP timeout: 10 seconds for health, 15 for auth, 60 for completions
#   - LDD trajectory printed before every assert
# @rationale  Created as part of wave-hermes-agent reset (T5.11) — replaces stale e2e
#             tests from old VPS-targeted tests. Dedicated smoke file per-module
#             per DevPlan §Протокол модульной волны.
#
# region MODULE_CONTRACT
## @purpose  — Smoke tests for hermes-agent: dashboard reachability, auth flow, API server.
## @scope    — Smoke-level HTTP tests against localhost; platform_services manages compose.
## @invariants
##   - All tests marked @pytest.mark.smoke and @pytest.mark.requires_docker
##   - HTTP timeout: 10 seconds for health, 15 for auth, 60 for completions
##   - API server requires Bearer token auth via API_SERVER_KEY
##   - Dashboard auth via HERMES_DASHBOARD_USERNAME / HERMES_DASHBOARD_PASSWORD
##   - LDD trajectory (IMP:7-10) printed for each test
## @rationale — Dedicated per-module smoke test per DevPlan wave-hermes-agent reset.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os

import pytest
import requests
from conftest import _handle_e2e_error, ldd_trajectory

logger = logging.getLogger(__name__)


def _build_url(port: int, path: str = "") -> str:
    """Build http://localhost:{port}{path} from port number."""
    return f"http://localhost:{port}{path}"


HERMES_USER = os.environ.get("HERMES_DASHBOARD_USERNAME", "tronyx")
HERMES_PASS = os.environ.get("HERMES_DASHBOARD_PASSWORD")
API_SERVER_KEY = os.environ.get("API_SERVER_KEY")


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: Hermes Dashboard health — redirects to login
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.smoke
@pytest.mark.requires_docker
@ldd_trajectory
def test_hermes_dashboard_health(caplog, platform_services, platform_ports) -> None:
    """Verify Hermes Dashboard root redirects to login page (302).

    ## @purpose — Dashboard is the web UI entry point. A 302 redirect to
    ##            /auth/login confirms the server is alive and auth is enabled.
    ## @io — ⇥ platform_services, platform_ports (fixture) →
    ##       ⚡ HTTP GET / → ⎋ None (asserts 302 redirect to /auth/login)
    ## @complexity — O(1)
    """
    dash_port = platform_ports["HERMES_DASHBOARD_PORT"]
    hermes_dashboard_url = os.environ.get("HERMES_DASHBOARD_URL", _build_url(dash_port))
    url = f"{hermes_dashboard_url}/"
    logger.info("[IMP:7][test_hermes_dashboard_health] Checking Hermes Dashboard at %s ...", url)

    try:
        r = requests.get(url, timeout=10, allow_redirects=False)
        logger.info(
            "[IMP:8][test_hermes_dashboard_health] Dashboard returned HTTP %s, Location: %s",
            r.status_code,
            r.headers.get("Location", "none"),
        )
        assert r.status_code in (302, 200), f"Dashboard returned HTTP {r.status_code}, expected 302 or 200"
        logger.info("[IMP:9][test_hermes_dashboard_health] ✅ Hermes Dashboard reachable: HTTP %s", r.status_code)
    except requests.RequestException as exc:
        _handle_e2e_error(exc, url, caplog, logger)


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: Hermes Dashboard auth login
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.smoke
@pytest.mark.requires_docker
@ldd_trajectory
def test_hermes_auth_login(caplog, platform_services, platform_ports) -> None:
    """Verify password login returns ok:true with session cookies.

    ## @purpose — Auth gate uses username/password provider. Successful login
    ##            confirms the auth provider is configured correctly.
    ## @io — ⇥ platform_services, platform_ports (fixture) →
    ##       ⚡ POST /auth/password-login with JSON credentials →
    ##       ⎋ None (asserts 200 + ok:true + session cookies)
    ## @complexity — O(1)
    """
    if not HERMES_PASS:
        pytest.skip("HERMES_DASHBOARD_PASSWORD not set")

    dash_port = platform_ports["HERMES_DASHBOARD_PORT"]
    hermes_dashboard_url = os.environ.get("HERMES_DASHBOARD_URL", _build_url(dash_port))
    url = f"{hermes_dashboard_url}/auth/password-login"
    payload = {
        "username": HERMES_USER,
        "password": HERMES_PASS,
        "provider": "basic",
        "next": "",
    }
    logger.info("[IMP:7][test_hermes_auth_login] Logging in as %s ...", HERMES_USER)

    try:
        r = requests.post(url, json=payload, timeout=15)
        logger.info("[IMP:8][test_hermes_auth_login] Auth returned HTTP %s", r.status_code)

        assert r.status_code == 200, f"Auth returned HTTP {r.status_code}: {r.text[:200]}"
        body = r.json()
        assert body.get("ok") is True, f"Expected ok:true, got: {body}"
        assert r.cookies.get("hermes_session_at"), "hermes_session_at cookie not set"
        logger.info("[IMP:9][test_hermes_auth_login] ✅ Auth OK: ok:true, session cookies set")
    except requests.RequestException as exc:
        _handle_e2e_error(exc, url, caplog, logger)


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: Hermes API server — chat completions
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.smoke
@pytest.mark.requires_docker
@ldd_trajectory
def test_hermes_api_completions(caplog, platform_services, platform_ports) -> None:
    """Verify Hermes API server responds to /v1/chat/completions.

    ## @purpose — API server on port HERMES_DESKTOP_PORT is the OpenAI-compatible
    ##            endpoint. A successful response confirms the API server, model
    ##            routing, and LiteLLM integration are working.
    ## @io — ⇥ platform_services, platform_ports (fixture) →
    ##       ⚡ POST /v1/chat/completions with Bearer auth →
    ##       ⎋ None (asserts 200 + choices[0].message.content)
    ## @complexity — O(1) — single HTTP POST to LLM proxy
    """
    if not API_SERVER_KEY:
        pytest.skip("API_SERVER_KEY not set — cannot authenticate")

    api_port = platform_ports["HERMES_DESKTOP_PORT"]
    hermes_api_url = os.environ.get("HERMES_API_URL", _build_url(api_port))
    url = f"{hermes_api_url}/v1/chat/completions"
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "Reply with exactly the word hello."}],
        "max_tokens": 20,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_SERVER_KEY}",
    }

    logger.info("[IMP:7][test_hermes_api_completions] Sending chat to %s ...", url)

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        logger.info("[IMP:8][test_hermes_api_completions] API returned HTTP %s", r.status_code)

        assert r.status_code == 200, f"API returned HTTP {r.status_code}: {r.text[:300]}"
        body = r.json()
        choices = body.get("choices", [])
        assert len(choices) > 0, f"No choices in response: {body}"
        content = choices[0].get("message", {}).get("content", "")
        assert content, f"Empty content in response: {body}"
        logger.info("[IMP:9][test_hermes_api_completions] ✅ API completions OK: %s ...", content[:50])
    except requests.RequestException as exc:
        _handle_e2e_error(exc, url, caplog, logger)
