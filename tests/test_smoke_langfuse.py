# GREP_SUMMARY: smoke langfuse health ingestion login 3001 minio minioadmin
# STRUCTURE: ▶ test_langfuse_health[smoke] → ▶ test_langfuse_ingestion[smoke] → ▶ test_langfuse_login[smoke] → ┐
# region MODULE_CONTRACT
## @purpose  Smoke tests for langfuse module — health, ingestion, and login
## @scope    Local langfuse instance (127.0.0.1:3001); requires compose stack running
## @invariants
##   - All tests use @pytest.mark.smoke and @pytest.mark.requires_docker markers
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
from conftest import _handle_e2e_error, _module_container_running

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
    ## ⚠️ TRAP[BUG] · 2026-07-23 · P1 · Transient langfuse startup: ConnectionError without retry
    ## · Symptom: first http request to langfuse may hit the container startup window
    ## ·            (Docker Desktop accepts TCP before Node.js binds the port)
    ## · Root: langfuse Node.js process may not have bound the HTTP port yet even though
    ## ·        Docker Desktop reports the container as healthy (TCP port forwarding race)
    ## · Fix: retry with exponential backoff (1s/2s/4s) — gives the server a chance to bind
    ## · Prevention: if retry count >1 in >50% CI runs → investigate langfuse startup time
    """
    for attempt in range(3):
        try:
            requests.get(f"http://{host}:{port}/api/public/health", timeout=timeout)
            return True
        except (requests.ConnectionError, requests.Timeout, ValueError):
            if attempt < 2:
                time.sleep(2**attempt)
            else:
                return False
    return False  # unreachable — satisfies RET503 linter contract


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_langfuse_health(caplog, platform_services):
    # ⚠️ TRAP[BUG] · 2026-07-18 · R4 Fail-fast: live container check (не sticky failed list)
    if not _module_container_running(platform_services, "langfuse", "langfuse-test", logger):
        pytest.fail("langfuse-test did not start — smoke tests require running containers")
    caplog.set_level(logging.INFO)
    if not _port_reachable():
        pytest.skip(f"Port {LANGFUSE_PORT} not reachable")

    # ⚠️ TRAP[BUG] · 2026-07-23 · P1 · Transient langfuse startup: ConnectionError without retry
    # · Symptom: requests.get to langfuse health endpoint may raise ConnectionError
    # ·            if the container is still restarting (crash-restart loop on first start)
    # · Root: langfuse can crash on Application startup (httpx.ConnectError to model),
    # ·        then restart: unless-stopped restores it. The first test request hits
    # ·        the restart window. Same root cause as LiteLLM P0-5.
    # · Fix: retry with exponential backoff (1s/2s/4s) — gives container time to stabilize
    # ·        after restart. On last attempt → _handle_e2e_error for proper routing.
    for attempt in range(3):
        try:
            resp = requests.get(LANGFUSE_HEALTH_URL, timeout=10)
            logger.info(
                "[IMP:8][test_langfuse_health] Langfuse returned HTTP %s (attempt %d)",
                resp.status_code,
                attempt + 1,
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body.get("status") == "OK"
            logger.info("[IMP:9][test_langfuse_health][pass] Langfuse healthy")
            break
        except requests.RequestException as exc:
            if attempt < 2:
                wait_s = 2**attempt
                logger.warning(
                    "[IMP:7][test_langfuse_health] Attempt %d failed (%s), retrying in %ds...",
                    attempt + 1,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
            else:
                _handle_e2e_error(exc, LANGFUSE_HEALTH_URL, caplog, logger)
                return


@pytest.mark.smoke
@pytest.mark.requires_docker
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

    # ⚠️ TRAP[BUG] · 2026-07-23 · P1 · Transient langfuse startup: ConnectionError without retry
    # · Symptom: requests.post to ingestion endpoint may raise ConnectionError
    # ·            during langfuse startup window (same root cause as health test)
    # · Fix: retry with exponential backoff (1s/2s/4s)
    for attempt in range(3):
        try:
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
            logger.info(
                "[IMP:8][test_langfuse_ingestion] Ingestion returned HTTP %s (attempt %d)",
                resp.status_code,
                attempt + 1,
            )
            assert resp.status_code in (200, 207), f"Ingestion returned HTTP {resp.status_code}: {resp.text[:200]}"
            logger.info("[IMP:9][test_langfuse_ingestion][pass] Ingestion accepted (HTTP %d)", resp.status_code)
            break
        except requests.RequestException as exc:
            if attempt < 2:
                wait_s = 2**attempt
                logger.warning(
                    "[IMP:7][test_langfuse_ingestion] Attempt %d failed (%s), retrying in %ds...",
                    attempt + 1,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
            else:
                _handle_e2e_error(exc, LANGFUSE_INGESTION_URL, caplog, logger)
                return


@pytest.mark.smoke
@pytest.mark.requires_docker
def test_langfuse_login(caplog, platform_services):
    # ⚠️ TRAP[BUG] · 2026-07-18 · R4 Fail-fast: live container check (не sticky failed list)
    if not _module_container_running(platform_services, "langfuse", "langfuse-test", logger):
        pytest.fail("langfuse-test did not start — smoke tests require running containers")
    caplog.set_level(logging.INFO)
    if not _port_reachable():
        pytest.skip(f"Port {LANGFUSE_PORT} not reachable")
    # ⚠️ TRAP[BUG] · 2026-07-23 · P1 · Transient langfuse startup: ConnectionError without retry
    # · Symptom: requests.get to CSRF endpoint may raise ConnectionError
    # ·            during langfuse startup window (same root cause as health test)
    # · Fix: retry with exponential backoff (1s/2s/4s)
    for attempt in range(3):
        try:
            csrf = requests.get(LANGFUSE_CSRF_URL, timeout=10).json()
            logger.info(
                "[IMP:8][test_langfuse_login] CSRF token obtained (attempt %d)",
                attempt + 1,
            )
            assert csrf.get("csrfToken"), f"No CSRF token: {csrf}"
            break
        except requests.RequestException as exc:
            if attempt < 2:
                wait_s = 2**attempt
                logger.warning(
                    "[IMP:7][test_langfuse_login] CSRF attempt %d failed (%s), retrying in %ds...",
                    attempt + 1,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
            else:
                _handle_e2e_error(exc, LANGFUSE_CSRF_URL, caplog, logger)
                return

    # ⚠️ TRAP[BUG] · 2026-07-23 · P1 · Transient langfuse startup: ConnectionError without retry
    # · Symptom: requests.post to login endpoint may raise ConnectionError
    # ·            during langfuse startup window (same root cause as health test)
    # · Fix: retry with exponential backoff (1s/2s/4s)
    for attempt in range(3):
        try:
            login = requests.post(
                LANGFUSE_LOGIN_URL,
                data={"csrfToken": csrf["csrfToken"], "email": LANGFUSE_EMAIL, "password": LANGFUSE_PASS},
                allow_redirects=False,
                timeout=10,
            )
            logger.info(
                "[IMP:8][test_langfuse_login] Login returned HTTP %s (attempt %d)",
                login.status_code,
                attempt + 1,
            )
            assert login.status_code in (200, 302), f"Login failed: HTTP {login.status_code}"
            logger.info("[IMP:9][test_langfuse_login][pass] Langfuse login OK")
            break
        except requests.RequestException as exc:
            if attempt < 2:
                wait_s = 2**attempt
                logger.warning(
                    "[IMP:7][test_langfuse_login] Login attempt %d failed (%s), retrying in %ds...",
                    attempt + 1,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
            else:
                _handle_e2e_error(exc, LANGFUSE_LOGIN_URL, caplog, logger)
                return
