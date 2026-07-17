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

logger = logging.getLogger(__name__)

LANGFUSE_HOST = "127.0.0.1"
LANGFUSE_PORT = int(os.environ.get("LANGFUSE_PORT", "3001"))
LANGFUSE_BASE = f"http://{LANGFUSE_HOST}:{LANGFUSE_PORT}"
LANGFUSE_HEALTH_URL = f"{LANGFUSE_BASE}/api/public/health"
LANGFUSE_INGESTION_URL = f"{LANGFUSE_BASE}/api/public/ingestion"
LANGFUSE_CSRF_URL = f"{LANGFUSE_BASE}/api/auth/csrf"
LANGFUSE_LOGIN_URL = f"{LANGFUSE_BASE}/api/auth/callback/credentials"

# Use root .env values (headless-init generated keys), ignoring hermes-agent test keys
LANGFUSE_PUBLIC_KEY = "pk-lf_68db9366171efc2a510743dea2cc1259"
LANGFUSE_SECRET_KEY = "sk-lf_3d233e93f5b8a0e55cde74d048c1eb5a"
LANGFUSE_EMAIL = os.environ.get("LANGFUSE_INIT_USER_EMAIL", "admin@ai-platform.local")
LANGFUSE_PASS = os.environ.get("LANGFUSE_INIT_USER_PASSWORD", "testpass")


def _port_reachable(host=LANGFUSE_HOST, port=LANGFUSE_PORT, timeout=3.0):
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


@pytest.mark.smoke
def test_langfuse_health(caplog):
    caplog.set_level(logging.INFO)
    if not _port_reachable():
        pytest.skip(f"Port {LANGFUSE_PORT} not reachable")
    resp = requests.get(LANGFUSE_HEALTH_URL, timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "OK"
    logger.info("[IMP:9][test_langfuse_health][pass] Langfuse healthy")


@pytest.mark.smoke
def test_langfuse_ingestion(caplog):
    caplog.set_level(logging.INFO)
    if not _port_reachable():
        pytest.skip(f"Port {LANGFUSE_PORT} not reachable")
    now = time.time()
    _basic = base64.b64encode(f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()
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
def test_langfuse_login(caplog):
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
