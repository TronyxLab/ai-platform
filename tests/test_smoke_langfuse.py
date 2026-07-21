# GREP_SUMMARY: smoke langfuse health ingestion login 3001 minio minioadmin
# STRUCTURE: ▶ test_langfuse_health[smoke] → ▶ test_langfuse_ingestion[smoke] → ▶ test_langfuse_login[smoke] → ┐
# region MODULE_CONTRACT
## @purpose  Smoke tests for langfuse module — health, ingestion, and login
## @scope    Local langfuse instance (127.0.0.1:3001); requires compose stack running
## @invariants
##   - All tests use @pytest.mark.smoke marker
##   - Health endpoint is unauthenticated
##   - Ingestion test authenticates with Basic auth (public_key:secret_key)
##   - Login test uses CSRF + credentials flow
## @rationale Wave T5.6 langfuse: smoke tests verify langfuse works with MinIO S3 backend
# endregion MODULE_CONTRACT

import base64
import datetime
import logging
import os
import time

import pytest
import requests
from conftest import _module_container_running

logger = logging.getLogger(__name__)

LANGFUSE_HOST = "127.0.0.1"
LANGFUSE_PORT = int(os.environ.get("LANGFUSE_TEST_PORT", "13000"))
LANGFUSE_BASE = f"http://{LANGFUSE_HOST}:{LANGFUSE_PORT}"
LANGFUSE_HEALTH_URL = f"{LANGFUSE_BASE}/api/public/health"
LANGFUSE_INGESTION_URL = f"{LANGFUSE_BASE}/api/public/ingestion"
LANGFUSE_CSRF_URL = f"{LANGFUSE_BASE}/api/auth/csrf"
LANGFUSE_LOGIN_URL = f"{LANGFUSE_BASE}/api/auth/callback/credentials"

LANGFUSE_EMAIL = os.environ.get("LANGFUSE_INIT_USER_EMAIL", "admin@ai-platform.local")
LANGFUSE_PASS = os.environ.get("LANGFUSE_INIT_USER_PASSWORD", "testpass")


def _langfuse_credentials() -> tuple[str, str]:
    """Read Langfuse API credentials from environment at call time.

    ## @purpose — Module-level constants would capture os.environ BEFORE
    ##            platform_env fixture sets SMOKE_ENV overrides. Reading
    ##            at call time ensures consistency with container env vars.
    ## @io — ⎋ (public_key: str, secret_key: str)
    """
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-test-langfuse-public")
    sk = os.environ.get("LANGFUSE_SECRET_KEY", "sk-test-langfuse-secret")
    return pk, sk


def _port_reachable(host=LANGFUSE_HOST, port=LANGFUSE_PORT, timeout=3.0):
    """Check if langfuse HTTP endpoint responds (not just TCP port open).

    ## @purpose — Docker Desktop port forwarding accepts TCP connections before the
    ##            app inside the container has bound the port. A TCP-only check (socket
    ##            connect) returns True even when the Node.js server hasn't started yet,
    ##            causing RemoteDisconnected or ReadTimeout in subsequent requests.
    ##            This version does an actual HTTP HEAD to verify the app is serving.
    ## @io — ⎋ bool: True if HTTP endpoint responds (any status), False otherwise
    """
    try:
        requests.get(f"http://{host}:{port}/api/public/health", timeout=timeout)
        return True
    except (requests.ConnectionError, requests.Timeout, ValueError):
        return False


@pytest.mark.smoke
def test_langfuse_health(caplog, platform_services):
    # ⚠️ TRAP[BUG] · 2026-07-18 · R4 Fail-fast: live container check (не sticky failed list)
    if not _module_container_running(platform_services, "langfuse", "langfuse-test", logger):
        pytest.fail("langfuse-test did not start — smoke tests require running containers")
    caplog.set_level(logging.INFO)
    if not _port_reachable():
        pytest.skip(f"Port {LANGFUSE_PORT} not reachable")
    resp = requests.get(LANGFUSE_HEALTH_URL, timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "OK"
    logger.info("[IMP:9][test_langfuse_health][pass] Langfuse healthy")


@pytest.mark.smoke
def test_langfuse_ingestion(caplog, platform_services):
    # ⚠️ TRAP[BUG] · 2026-07-18 · R4 Fail-fast: live container check (не sticky failed list)
    if not _module_container_running(platform_services, "langfuse", "langfuse-test", logger):
        pytest.fail("langfuse-test did not start — smoke tests require running containers")
    caplog.set_level(logging.INFO)
    if not _port_reachable():
        pytest.skip(f"Port {LANGFUSE_PORT} not reachable")
    langfuse_pk, langfuse_sk = _langfuse_credentials()
    now = time.time()
    _basic = base64.b64encode(f"{langfuse_pk}:{langfuse_sk}".encode()).decode()
    resp = requests.post(
        LANGFUSE_INGESTION_URL,
        json={
            "batch": [
                {
                    "id": f"test-{int(now * 1000)}",
                    "timestamp": datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc).isoformat(),
                    "type": "trace-create",
                    "body": {"name": "smoke-test-trace"},
                }
            ]
        },
        headers={"Authorization": f"Basic {_basic}"},
        timeout=15,
    )
    assert resp.status_code in (200, 207), f"Ingestion returned HTTP {resp.status_code}: {resp.text[:200]}"
    logger.info("[IMP:9][test_langfuse_ingestion][pass] Ingestion accepted (HTTP %d)", resp.status_code)


@pytest.mark.smoke
def test_langfuse_login(caplog, platform_services):
    # ⚠️ TRAP[BUG] · 2026-07-18 · R4 Fail-fast: live container check (не sticky failed list)
    if not _module_container_running(platform_services, "langfuse", "langfuse-test", logger):
        pytest.fail("langfuse-test did not start — smoke tests require running containers")
    caplog.set_level(logging.INFO)
    if not _port_reachable():
        pytest.skip(f"Port {LANGFUSE_PORT} not reachable")
    csrf = requests.get(LANGFUSE_CSRF_URL, timeout=10).json()
    assert csrf.get("csrfToken"), f"No CSRF token: {csrf}"
    login = requests.post(
        LANGFUSE_LOGIN_URL,
        data={"csrfToken": csrf["csrfToken"], "email": LANGFUSE_EMAIL, "password": LANGFUSE_PASS},
        allow_redirects=False,
        timeout=10,
    )
    assert login.status_code in (200, 302), f"Login failed: HTTP {login.status_code}"
    logger.info("[IMP:9][test_langfuse_login][pass] Langfuse login OK")
