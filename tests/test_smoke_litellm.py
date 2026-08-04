# GREP_SUMMARY: test-smoke-litellm readiness models-api healthcheck litellm-proxy
# STRUCTURE: ○ test_litellm_readiness[⚡HTTP GET /health/readiness → 200] → ○ test_litellm_models_api[⚡HTTP GET /v1/models → 200+BearerAuth+model_list]
# @file test_smoke_litellm.py
# @purpose  Smoke tests for litellm module: readiness + models API via compose
# @scope    Smoke tests; requires Docker daemon running locally.
# @invariants
#   - All tests use @pytest.mark.smoke and @pytest.mark.requires_docker markers
#   - platform_services fixture manages compose lifecycle
#   - HTTP timeout: 10 seconds
#   - LDD trajectory printed before every assert
# @rationale  Created as part of wave-litellm reset (T5.5) — replaces stale litellm
#             tests from old observability module and platform_endpoints file.
#             Dedicated smoke file per-module per DevPlan §Протокол модульной волны.
#
# region MODULE_CONTRACT
## @purpose  — Smoke tests for litellm LLM Gateway: readiness + models API availability.
## @scope    — Smoke-level HTTP tests against localhost; platform_services manages compose.
## @invariants
##   - All tests marked @pytest.mark.smoke and @pytest.mark.requires_docker
##   - HTTP timeout: 10 seconds
##   - LDD trajectory (IMP:7-10) printed for each test
## @rationale — Dedicated per-module smoke test per DevPlan wave-litellm reset.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import time

import pytest
import requests
from _conftest.honesty import require_env_or_fail
from conftest import _handle_e2e_error, _module_container_running, ldd_trajectory

logger = logging.getLogger(__name__)


def _build_litellm_url(port: int, path: str) -> str:
    """Build http://localhost:{port}{path} for LiteLLM endpoints."""
    return f"http://localhost:{port}{path}"


_LITELLM_TEST_PORT = int(os.environ.get("LITELLM_TEST_PORT", "14000"))


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: LiteLLM /health/readiness must return HTTP 200
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.smoke
@pytest.mark.requires_docker
@ldd_trajectory
def test_litellm_readiness(caplog, platform_services) -> None:
    """Verify LiteLLM /health/readiness returns HTTP 200 (proxy fully initialized).

    ## @purpose — LiteLLM is the LLM gateway / proxy. /health/readiness confirms the
    ##            proxy is running and connected to its DB — does not require
    ##            model list initialization or API key auth.
    ## @io — ⇥ platform_services (fixture) →
    ##       ⚡ HTTP GET http://localhost:14000/health/readiness →
    ##       ⎋ None (asserts 200)
    ## @complexity — O(1)
    """
    # region FUNC_test_litellm_readiness

    # ⚠️ TRAP[BUG] · 2026-07-18 · R4 Fail-fast: live container check
    # · failed лист липкий (первый --wait timeout), restart: unless-stopped мог восстановить.
    # · Используем docker inspect для верификации реального состояния.
    # 2026-08-04 (DevPlan 129 W1 T2): first-start crash mitigated retry+backoff ниже
    # (1s/2s/4s, TRAP[BUG] 2026-07-23 P0 с prevention-условием).
    # Root-cause (Dep 017 DNS-alias pgbouncer) — вне скоупа (домен 126/прод).
    if not _module_container_running(platform_services, "litellm", "litellm-test", logger):
        pytest.fail("litellm-test did not start — smoke tests require running containers")

    url = _build_litellm_url(_LITELLM_TEST_PORT, "/health/readiness")
    logger.info("[IMP:7][test_litellm_readiness] Checking LiteLLM %s ...", url)

    # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · LiteLLM crash-restart cycle on CI
    # · Symptom: ConnectionResetError(104) on first request — container restarts after crash
    # · Root: LiteLLM can crash on Application startup (httpx.ConnectError to model),
    # ·   then restart: unless-stopped restores it. First request hits the restart window.
    # · Fix: retry with backoff (1s/2s/4s) — gives container time to stabilize after restart.
    # · Prevention: if retry count >1 in >50% CI runs → investigate root cause (Dep 017 DNS-alias).
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=10)
            logger.info(
                "[IMP:8][test_litellm_readiness] LiteLLM returned HTTP %s (attempt %d)",
                r.status_code,
                attempt + 1,
            )
            assert r.status_code == 200, (
                f"LiteLLM readiness endpoint returned HTTP {r.status_code}, expected 200. Response: {r.text[:300]}"
            )
            logger.info("[IMP:9][test_litellm_readiness] ✅ LiteLLM readiness OK: HTTP %s", r.status_code)
            break
        except requests.RequestException as exc:
            if attempt < 2:
                wait_s = 2**attempt  # 1s, 2s backoff
                logger.warning(
                    "[IMP:7][test_litellm_readiness] Attempt %d failed (%s), retrying in %ds...",
                    attempt + 1,
                    exc,
                    wait_s,
                )
                time.sleep(wait_s)
            else:
                _handle_e2e_error(exc, url, caplog, logger)
                return
    # endregion FUNC_test_litellm_readiness


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: LiteLLM /v1/models must return HTTP 200 with model_list
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.smoke
@pytest.mark.requires_docker
@ldd_trajectory
def test_litellm_models_api(caplog, platform_services) -> None:
    """Verify LiteLLM /v1/models returns HTTP 200 with model list via Bearer auth.

    ## @purpose — Models API confirms LiteLLM has loaded model_list from config and
    ##            is ready to proxy requests. Requires valid LITELLM_MASTER_KEY.
    ## @io — ⇥ platform_services (fixture) →
    ##       ⚡ HTTP GET http://localhost:14000/v1/models (Authorization: Bearer $LITELLM_MASTER_KEY) →
    ##       ⎋ None (asserts 200 + non-empty data)
    ## @complexity — O(1)
    """
    # region FUNC_test_litellm_models_api

    master_key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not master_key:
        require_env_or_fail("LITELLM_MASTER_KEY", reason="LiteLLM /v1/models requires master key")

    url = _build_litellm_url(_LITELLM_TEST_PORT, "/v1/models")
    headers = {"Authorization": f"Bearer {master_key}"}
    logger.info("[IMP:7][test_litellm_models_api] Checking LiteLLM %s ...", url)

    try:
        r = requests.get(url, headers=headers, timeout=10)
        logger.info("[IMP:8][test_litellm_models_api] LiteLLM /v1/models returned HTTP %s", r.status_code)
        assert r.status_code == 200, (
            f"LiteLLM /v1/models returned HTTP {r.status_code}, expected 200. Response: {r.text[:300]}"
        )
        data = r.json()
        assert "data" in data, f"LiteLLM /v1/models response missing 'data' key: {r.text[:300]}"
        assert len(data["data"]) > 0, "LiteLLM /v1/models returned empty model list"
        model_ids = [m["id"] for m in data["data"]]
        logger.info(
            "[IMP:9][test_litellm_models_api] ✅ LiteLLM models API OK: %d models — %s",
            len(model_ids),
            ", ".join(model_ids),
        )
    except (requests.RequestException, ValueError) as exc:
        _handle_e2e_error(exc, url, caplog, logger)
        return
    # endregion FUNC_test_litellm_models_api
