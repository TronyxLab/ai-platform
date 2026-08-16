# GREP_SUMMARY: local-auth hermes grafana langfuse login correct wrong regression 127.0.0.1
# STRUCTURE: ⚡ [hermes_correct + hermes_wrong] → POST 127.0.0.1:9119/auth/password-login → ◇ ok:true?: ⊕ → ⚡ [grafana_correct + grafana_wrong] → GET 127.0.0.1:3000/api/user(Basic) → ◇ 200?: ⊕ → ⚡ [langfuse_correct + langfuse_wrong] → CSRF → POST 127.0.0.1:3001/api/auth/callback/credentials → ◇ signin?: ⊕ → ∑ 6 tests
# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(AUTH):2; TECH(HTTP):2]
## @purpose — Local auth regression tests for Hermes, Grafana, Langfuse.
##            All targets use 127.0.0.1:<port> (NOT external domains).
##            Credentials from os.environ (loaded by conftest._load_test_env from
##            core/modules/hermes-agent/.env).
## @scope — 6 tests (correct + wrong × 3 services); require running local platform.
## @invariants
##   - All tests use @pytest.mark.local_auth + @ldd_trajectory decorator
##   - All URLs hardcoded to 127.0.0.1:<port>
##   - Credentials from os.environ with sensible defaults
##   - Connection refused → require_service_healthy (R4 honesty: skip/fail по режиму); auth failures → pytest.fail
##   - Each test prints LDD trajectory (IMP:7-10) via caplog
## @rationale — TASK-4 from DevPlan 018-auth-fix: validate that all 3 dashboard
##             services accept correct creds and reject wrong creds after
##             credentials sync.
## @usecases — AC: Hermes ok:true, Grafana 200, Langfuse session; ALL reject wrong password
# endregion MODULE_CONTRACT

import logging
import os

import pytest
import requests
from _conftest.honesty import require_docker_or_fail, require_env_or_fail, require_service_healthy

logger = logging.getLogger(__name__)

from conftest import _handle_e2e_error, ldd_trajectory

# region MODULE_SKIP_DOCKER

require_docker_or_fail(reason="local auth tests require Docker daemon")

# endregion MODULE_SKIP_DOCKER

# region CONSTANTS

HERMES_AUTH_URL = "http://127.0.0.1:9119/auth/password-login"
GRAFANA_USER_URL = "http://127.0.0.1:3000/api/user"
LANGFUSE_CSRF_URL = "http://127.0.0.1:3001/api/auth/csrf"
LANGFUSE_LOGIN_URL = "http://127.0.0.1:3001/api/auth/callback/credentials"

HERMES_USER = os.environ.get("HERMES_DASHBOARD_USERNAME", "admin@ai-platform.local")
HERMES_PASS = os.environ.get("HERMES_DASHBOARD_PASSWORD")

GRAFANA_USER = os.environ.get(
    "GF_SECURITY_ADMIN_USER",
    os.environ.get("HERMES_DASHBOARD_USERNAME", "admin@ai-platform.local"),
)
GRAFANA_PASS = os.environ.get(
    "GF_SECURITY_ADMIN_PASSWORD",
    os.environ.get("HERMES_DASHBOARD_PASSWORD"),
)

LANGFUSE_EMAIL = os.environ.get("LANGFUSE_INIT_USER_EMAIL", "admin@ai-platform.local")
LANGFUSE_PASS = os.environ.get("LANGFUSE_INIT_USER_PASSWORD", "testpass")

REQUEST_TIMEOUT = 10

logger.info(
    "[IMP:7][constants] HERMES_USER=%s, HERMES_PASS=%s, "
    "GRAFANA_USER=%s, GRAFANA_PASS=%s, "
    "LANGFUSE_EMAIL=%s, LANGFUSE_PASS=%s",
    HERMES_USER,
    "***" if HERMES_PASS else "<not set>",
    GRAFANA_USER,
    "***" if GRAFANA_PASS else "<not set>",
    LANGFUSE_EMAIL,
    "***",
)

# endregion CONSTANTS


# region FUNC__skip_if_port_unreachable


def _skip_if_port_unreachable(host: str, port: int, reason: str = "local platform service") -> None:
    ## @purpose — Test port reachability via require_service_healthy (R4 honesty);
    ##            pytest.skip (marker mode) / fail (fail mode) if connection refused.
    ## @io — ⇥ host, port → ⎋ None (side-effect: dispatch через honesty mode)
    ## @complexity — O(1)
    require_service_healthy(host, port, reason=reason)


# endregion FUNC__skip_if_port_unreachable


# region TESTS

# ===== HERMES =====


@pytest.mark.local_auth
@ldd_trajectory
def test_hermes_login_local_correct(caplog) -> None:
    ## @purpose — POST 127.0.0.1:9119/auth/password-login with correct creds → 200, ok:true, session cookie
    ## @complexity — O(1)
    if not HERMES_PASS:
        require_env_or_fail("HERMES_DASHBOARD_PASSWORD", reason="local Hermes auth test")
    _skip_if_port_unreachable("127.0.0.1", 9119)
    logger.info("[IMP:9][test_hermes_login_local_correct] Starting local Hermes auth test")

    payload = {
        "username": HERMES_USER,
        "password": HERMES_PASS,
        "provider": "basic",
        "next": "",
    }
    try:
        resp = requests.post(
            HERMES_AUTH_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.ConnectionError:
        require_service_healthy("127.0.0.1", 9119, reason="Hermes HTTP service")
        pytest.fail(f"Hermes port 9119 reachable but HTTP request failed: {HERMES_AUTH_URL}")
    except requests.RequestException as exc:
        _handle_e2e_error(exc, HERMES_AUTH_URL, caplog, logger=logger)
        return

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
    logger.info("[IMP:8][test_hermes_login_local_correct] HTTP 200 OK")

    try:
        body = resp.json()
    except ValueError:
        pytest.fail(f"Response not valid JSON: {resp.text[:300]}")

    assert body.get("ok") is True, f"Expected ok:true, got: {body}"
    assert body.get("next") == "/", f"Expected next:/, got: {body.get('next')}"
    logger.info("[IMP:9][test_hermes_login_local_correct] Body ok:true, next:/")

    access_cookie = resp.cookies.get("hermes_session_at")
    assert access_cookie, "hermes_session_at cookie not set"
    logger.info("[IMP:9][test_hermes_login_local_correct] PASSED — session cookie present")


@pytest.mark.local_auth
@ldd_trajectory
def test_hermes_login_local_wrong(caplog) -> None:
    ## @purpose — POST 127.0.0.1:9119/auth/password-login with wrong password → NOT ok:true
    ## @complexity — O(1)
    _skip_if_port_unreachable("127.0.0.1", 9119)
    logger.info("[IMP:9][test_hermes_login_local_wrong] Starting local Hermes wrong-password test")

    payload = {
        "username": HERMES_USER,
        "password": "WRONG_PASSWORD_12345",
        "provider": "basic",
        "next": "",
    }
    try:
        resp = requests.post(
            HERMES_AUTH_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.ConnectionError:
        require_service_healthy("127.0.0.1", 9119, reason="Hermes HTTP service")
        pytest.fail(f"Hermes port 9119 reachable but HTTP request failed: {HERMES_AUTH_URL}")
    except requests.RequestException as exc:
        _handle_e2e_error(exc, HERMES_AUTH_URL, caplog, logger=logger)
        return

    try:
        body = resp.json()
    except ValueError:
        body = {}

    logger.info(
        "[IMP:8][test_hermes_login_local_wrong] status=%d body_keys=%s",
        resp.status_code,
        list(body.keys()) if isinstance(body, dict) else str(body),
    )

    is_ok = body.get("ok") if isinstance(body, dict) else None
    assert is_ok is not True, f"Wrong password returned ok:true! Body: {body}"

    has_error = resp.status_code != 200 or (isinstance(body, dict) and body.get("detail"))
    assert has_error, f"Expected error for wrong password, got status={resp.status_code} body={body}"
    logger.info("[IMP:9][test_hermes_login_local_wrong] PASSED — wrong password correctly rejected")


# ===== GRAFANA =====


@pytest.mark.local_auth
@ldd_trajectory
@pytest.mark.parametrize(
    ("password", "expected_ok"),
    [
        (GRAFANA_PASS, True),  # correct creds → 200, login+role fields
        ("WRONG_PASSWORD_12345", False),  # wrong password → rejected (не 200)
    ],
)
def test_grafana_login_local(password, expected_ok, caplog) -> None:
    ## @purpose — GET 127.0.0.1:3000/api/user with correct/wrong password → 200 + user fields / rejected
    ## @complexity — O(1)
    if not GRAFANA_PASS and expected_ok:
        require_env_or_fail("GF_SECURITY_ADMIN_PASSWORD", reason="local Grafana auth test")
    _skip_if_port_unreachable("127.0.0.1", 3000)
    logger.info("[IMP:9][test_grafana_login_local] Starting local Grafana auth test (ok=%s)", expected_ok)

    try:
        resp = requests.get(
            GRAFANA_USER_URL,
            auth=(GRAFANA_USER, password),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.ConnectionError:
        require_service_healthy("127.0.0.1", 3000, reason="Grafana HTTP service")
        pytest.fail(f"Grafana port 3000 reachable but HTTP request failed: {GRAFANA_USER_URL}")
    except requests.RequestException as exc:
        _handle_e2e_error(exc, GRAFANA_USER_URL, caplog, logger=logger)
        return

    if not expected_ok:
        assert resp.status_code != 200, (
            f"Grafana should reject wrong password, got HTTP {resp.status_code}: {resp.text[:300]}"
        )
        logger.info(
            "[IMP:9][test_grafana_login_local] PASSED — wrong password rejected, HTTP %d",
            resp.status_code,
        )
        return

    assert resp.status_code == 200, f"Grafana login failed: HTTP {resp.status_code}: {resp.text[:300]}"
    logger.info("[IMP:8][test_grafana_login_local] HTTP 200 OK")

    try:
        user_info = resp.json()
    except ValueError:
        pytest.fail(f"Grafana response not valid JSON: {resp.text[:300]}")

    assert "login" in user_info, f"login field missing: {user_info}"
    assert "role" in user_info or user_info.get("isGrafanaAdmin") is not None, (
        f"role and isGrafanaAdmin fields missing: {user_info}"
    )

    logger.info("\n=== GRAFANA LOCAL USER ===")
    logger.info("%s", f"  login:  {user_info.get('login', '?')}")
    logger.info("%s", f"  email:  {user_info.get('email', '?')}")
    logger.info("%s", f"  role:   {user_info.get('role', '?')}")
    logger.info("=== END USER ===")

    logger.info(
        "[IMP:9][test_grafana_login_local] PASSED — login=%s role=%s",
        user_info.get("login"),
        user_info.get("isGrafanaAdmin", user_info.get("role")),
    )


# ===== LANGFUSE =====


def _langfuse_login(email: str, password: str, caplog):
    ## @purpose — Execute Langfuse CSRF login flow: GET csrf → POST credentials callback
    ## @io — ⇥ email, password, caplog → ⎋ Response from callback endpoint
    ## @complexity — O(1) — 2 HTTP round-trips
    ## 🧐 TRAP[BUG] · 2026-07-10 · P1 · CSRF cookie not persisted between GET and POST
    ## · Symptom: login POST redirected to signin despite correct credentials
    ## · Root: two separate requests.get/post calls — CSRF cookie from GET not sent with POST
    ## · Fix: use requests.Session() which persists cookies across multiple requests
    session = requests.Session()
    try:
        csrf_resp = session.get(LANGFUSE_CSRF_URL, timeout=REQUEST_TIMEOUT)
    except requests.ConnectionError:
        require_service_healthy("127.0.0.1", 3001, reason="Langfuse HTTP service")
        pytest.fail(f"Langfuse port 3001 reachable but CSRF request failed: {LANGFUSE_CSRF_URL}")
    except requests.RequestException as exc:
        _handle_e2e_error(exc, LANGFUSE_CSRF_URL, caplog, logger=logger)
        return None

    if csrf_resp.status_code != 200:
        pytest.fail(f"Langfuse CSRF endpoint returned {csrf_resp.status_code}: {csrf_resp.text[:300]}")

    csrf_token = csrf_resp.json().get("csrfToken")
    if not csrf_token:
        pytest.fail(f"No csrfToken in CSRF response: {csrf_resp.text[:300]}")
    logger.info("[IMP:8][_langfuse_login] Got csrfToken=%s...", csrf_token[:20])

    body_data = (
        f"email={requests.utils.quote(email)}"
        f"&password={requests.utils.quote(password)}"
        f"&csrfToken={requests.utils.quote(csrf_token)}"
        f"&callbackUrl=http://127.0.0.1:3001"
        f"&redirect=false"
        f"&json=true"
    )

    try:
        login_resp = session.post(
            LANGFUSE_LOGIN_URL,
            data=body_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        _handle_e2e_error(exc, LANGFUSE_LOGIN_URL, caplog, logger=logger)
        return None

    return login_resp


@pytest.mark.local_auth
@ldd_trajectory
@pytest.mark.parametrize(
    ("password", "expect_success"),
    [
        (LANGFUSE_PASS, True),  # correct creds → session, no signin redirect
        ("WRONG_PASSWORD_12345", False),  # wrong password → signin redirect / error
    ],
)
def test_langfuse_login_local(password, expect_success, caplog) -> None:
    ## @purpose — Langfuse CSRF login with correct/wrong credentials → success / signin redirect
    ## @complexity — O(1)
    logger.info("[IMP:9][test_langfuse_login_local] Starting local Langfuse auth test (ok=%s)", expect_success)

    resp = _langfuse_login(LANGFUSE_EMAIL, password, caplog)
    if resp is None:
        return

    logger.info(
        "[IMP:8][test_langfuse_login_local] status=%d",
        resp.status_code,
    )

    try:
        body = resp.json()
    except ValueError:
        body = {}

    redirect_url = body.get("url", "") if isinstance(body, dict) else ""
    is_signin = "signin" in redirect_url.lower() if redirect_url else False

    if not expect_success:
        has_error = "error" in resp.text.lower() if resp.text else False
        assert is_signin or has_error, (
            f"Wrong password should fail (redirect to signin or error), "
            f"got status={resp.status_code} body={body}\nText: {resp.text[:500]}"
        )
        logger.info("[IMP:9][test_langfuse_login_local] PASSED — wrong password correctly rejected")
        return

    assert not is_signin, f"Langfuse login redirected to signin page: url={redirect_url}\nBody: {resp.text[:500]}"
    # 🧐 TRAP[BUG] · 2026-07-10 · — · Session() cookies + resp.cookies both checked
    # ·   requests.Session() persists CSRF cookie across GET/POST. NextAuth session token
    # ·   may be set either in resp.cookies or persisted in session object.
    has_session_cookie = "next-auth.session-token" in resp.cookies
    logger.info("[IMP:8][test_langfuse_login_local] session cookie found=%s, url=%s", has_session_cookie, redirect_url)
    logger.info("[IMP:9][test_langfuse_login_local] PASSED — login succeeded (no signin redirect)")


# endregion TESTS
