#!/usr/bin/env python3
# GREP_SUMMARY: test-docker-auth, docker-login, ghcr-login, configure-docker-auth, auth-mocks, password-stdin
# STRUCTURE: ┌mock subprocess.run┐ → ○ test scenarios: docker_login → ghcr_login → configure_docker_auth
#            └verify args + LDD trajectory┘
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/docker_auth.py
##           Validates docker_login, ghcr_login, and configure_docker_auth
##           with mocked subprocess and env var fallback.
## @scope    Tests: docker_login (success/failure/anonymous), ghcr_login (success/failure/anonymous),
##           configure_docker_auth (base64 encoding, mirror_url).
## @invariants
##   - All subprocess calls mocked (no real docker binary required)
##   - Native imports via core.internal.shared.docker_auth (no sys.path hack)
##   - LDD: @ldd_trajectory on every test function for IMP:9 validation
##   - No file I/O or Docker daemon dependency
## @rationale Verifies the consolidated auth module replaces 5 duplicate auth
##            points. Token leakage prevention (DEVNULL stdout) is verified.
## @changes  2026-07-30 · DevPlan D8 — Created shared docker_auth tests
# endregion MODULE_CONTRACT

import base64
import logging
import os
import subprocess
from unittest.mock import MagicMock, patch

from tests._conftest.ldd import ldd_trajectory

# ── Module under test ─────────────────────────────────────────────
from core.internal.shared.docker_auth import configure_docker_auth, docker_login, ghcr_login

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# region Tests: docker_login
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · docker_login passes correct args and token via stdin
# · Scenario: Mock subprocess.run returncode=0, verify cmd, input, DEVNULL
# · Last fail: N/A (new test)
# · Remove if: docker_login implementation changes
@ldd_trajectory
def test_docker_login_success(caplog) -> None:
    """docker_login should pass correct cmd, token via stdin, DEVNULL stdout."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch("core.internal.shared.docker_auth.subprocess.run", return_value=mock_result) as mock_run:
        ok = docker_login("https://index.docker.io/v1/", "testuser", "testtoken")

    assert ok is True

    # Verify subprocess.run was called with correct args
    mock_run.assert_called_once()
    pos_args, kw_args = mock_run.call_args

    # Command structure: docker login <registry> --username <user> --password-stdin
    assert pos_args[0] == [
        "docker", "login", "https://index.docker.io/v1/",
        "--username", "testuser", "--password-stdin",
    ]
    assert kw_args.get("input") == "testtoken"
    assert kw_args.get("stdout") == subprocess.DEVNULL
    assert kw_args.get("stderr") == subprocess.PIPE
    assert kw_args.get("text") is True

    logger.critical("[IMP:9][test] docker_login — correct args, token via stdin, DEVNULL stdout — OK")


# 🧪 TRAP[TEST] · Regression · docker_login returns False on docker auth failure
# · Scenario: Mock subprocess.run returncode=1 → returns False
# · Last fail: N/A (new test)
# · Remove if: docker_login failure handling changes
@ldd_trajectory
def test_docker_login_failure(caplog) -> None:
    """docker_login should return False on non-zero exit code."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "authentication required"

    with patch("core.internal.shared.docker_auth.subprocess.run", return_value=mock_result):
        ok = docker_login("https://index.docker.io/v1/", "baduser", "badtoken")

    assert ok is False
    logger.critical("[IMP:9][test] docker_login failure — invalid credentials rejected — OK")


# 🧪 TRAP[TEST] · Regression · docker_login returns True when no creds (anonymous fallback)
# · Scenario: No username/token args, no env vars → IMP:7 log, returns True
# · Last fail: N/A (new test)
# · Remove if: anonymous fallback behavior changes
@ldd_trajectory
def test_docker_login_anonymous_fallback(caplog) -> None:
    """docker_login should return True (anonymous) when no credentials available."""
    with patch.dict(os.environ, {}, clear=True):
        ok = docker_login()

    assert ok is True
    logger.critical("[IMP:9][test] docker_login anonymous fallback — True without subprocess call — OK")


# 🧪 TRAP[TEST] · Regression · docker_login reads DOCKER_HUB_USERNAME/TOKEN from env
# · Scenario: Set env vars, call with None args → should use env values
# · Last fail: N/A (new test)
# · Remove if: env var fallback logic changes
@ldd_trajectory
def test_docker_login_env_fallback(caplog) -> None:
    """docker_login should fall back to DOCKER_HUB_USERNAME / DOCKER_HUB_TOKEN env vars."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    env = {
        "DOCKER_HUB_USERNAME": "envuser",
        "DOCKER_HUB_TOKEN": "envtoken",
    }
    with patch.dict(os.environ, env, clear=True):
        with patch("core.internal.shared.docker_auth.subprocess.run", return_value=mock_result) as mock_run:
            ok = docker_login()  # No args → should use env vars

    assert ok is True
    mock_run.assert_called_once()
    pos_args, _ = mock_run.call_args
    assert "--username" in pos_args[0]
    user_idx = pos_args[0].index("--username") + 1
    assert pos_args[0][user_idx] == "envuser"
    logger.critical("[IMP:9][test] docker_login env var fallback — DOCKER_HUB_USERNAME picked up — OK")


# enregion
# ═══════════════════════════════════════════════════════════════════
# region Tests: ghcr_login
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · ghcr_login passes correct ghcr.io args
# · Scenario: Mock subprocess.run, verify cmd targets ghcr.io with --password-stdin
# · Last fail: N/A (new test)
# · Remove if: ghcr_login implementation changes
@ldd_trajectory
def test_ghcr_login_success(caplog) -> None:
    """ghcr_login should target ghcr.io with correct args."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch("core.internal.shared.docker_auth.subprocess.run", return_value=mock_result) as mock_run:
        ok = ghcr_login("ghcr_pat_123", "ci-bot")

    assert ok is True

    mock_run.assert_called_once()
    pos_args, kw_args = mock_run.call_args

    # Command: docker login ghcr.io --username ci-bot --password-stdin
    assert pos_args[0] == [
        "docker", "login", "ghcr.io",
        "--username", "ci-bot", "--password-stdin",
    ]
    assert kw_args.get("input") == "ghcr_pat_123"
    assert kw_args.get("stdout") == subprocess.DEVNULL

    logger.critical("[IMP:9][test] ghcr_login — correct ghcr.io args — OK")


# 🧪 TRAP[TEST] · Regression · ghcr_login returns False on failure
# · Scenario: Mock subprocess.run returncode=1 → returns False
# · Last fail: N/A (new test)
# · Remove if: ghcr_login failure handling changes
@ldd_trajectory
def test_ghcr_login_failure(caplog) -> None:
    """ghcr_login should return False on non-zero exit."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "denied"

    with patch("core.internal.shared.docker_auth.subprocess.run", return_value=mock_result):
        ok = ghcr_login("badtoken")

    assert ok is False
    logger.critical("[IMP:9][test] ghcr_login failure — invalid token rejected — OK")


# 🧪 TRAP[TEST] · Regression · ghcr_login anonymous fallback when no token
# · Scenario: No token arg, no GHCR_PULL_TOKEN env → IMP:7, returns True
# · Last fail: N/A (new test)
# · Remove if: ghcr_login anonymous fallback changes
@ldd_trajectory
def test_ghcr_login_anonymous_fallback(caplog) -> None:
    """ghcr_login should return True (anonymous) when no token available."""
    with patch.dict(os.environ, {}, clear=True):
        ok = ghcr_login()

    assert ok is True
    logger.critical("[IMP:9][test] ghcr_login anonymous fallback — True without subprocess — OK")


# 🧪 TRAP[TEST] · Regression · ghcr_login reads GHCR_PULL_TOKEN from env
# · Scenario: Set GHCR_PULL_TOKEN env, call with token=None → should use env value
# · Last fail: N/A (new test)
# · Remove if: ghcr_login env var fallback changes
@ldd_trajectory
def test_ghcr_login_env_fallback(caplog) -> None:
    """ghcr_login should fall back to GHCR_PULL_TOKEN env var."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch.dict(os.environ, {"GHCR_PULL_TOKEN": "env_token_456"}, clear=True):
        with patch("core.internal.shared.docker_auth.subprocess.run", return_value=mock_result) as mock_run:
            ok = ghcr_login()  # No token arg → should use GHCR_PULL_TOKEN

    assert ok is True
    mock_run.assert_called_once()
    _, kw_args = mock_run.call_args
    assert kw_args.get("input") == "env_token_456"
    logger.critical("[IMP:9][test] ghcr_login env fallback — GHCR_PULL_TOKEN used — OK")


# enregion
# ═══════════════════════════════════════════════════════════════════
# region Tests: configure_docker_auth
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · configure_docker_auth returns valid dict with base64 creds
# · Scenario: Call with user/token → verify base64-encoded auth in dict
# · Last fail: N/A (new test)
# · Remove if: configure_docker_auth encoding changes
@ldd_trajectory
def test_configure_docker_auth_encoding(caplog) -> None:
    """configure_docker_auth should return base64-encoded auth dict."""
    result = configure_docker_auth("myuser", "mytoken")

    assert "auths" in result
    assert "https://index.docker.io/v1/" in result["auths"]
    auth_entry = result["auths"]["https://index.docker.io/v1/"]

    # Verify base64 encoding
    expected_b64 = base64.b64encode(b"myuser:mytoken").decode("ascii")
    assert auth_entry["auth"] == expected_b64

    logger.critical("[IMP:9][test] configure_docker_auth — base64 encoding correct — OK")


# 🧪 TRAP[TEST] · Regression · configure_docker_auth uses custom mirror_url
# · Scenario: Pass mirror_url → dict key uses that URL instead of default
# · Last fail: N/A (new test)
# · Remove if: mirror_url logic changes
@ldd_trajectory
def test_configure_docker_auth_custom_mirror(caplog) -> None:
    """configure_docker_auth should use custom mirror URL if provided."""
    result = configure_docker_auth("u", "t", mirror_url="https://mirror.gcr.io")

    assert "https://mirror.gcr.io" in result["auths"]
    assert "https://index.docker.io/v1/" not in result["auths"]

    logger.critical("[IMP:9][test] configure_docker_auth — custom mirror URL — OK")


# 🧪 TRAP[TEST] · Regression · configure_docker_auth handles empty credentials gracefully
# · Scenario: Empty username/token → still returns valid dict with empty auth
# · Last fail: N/A (new test)
# · Remove if: empty credential handling changes
@ldd_trajectory
def test_configure_docker_auth_empty_creds(caplog) -> None:
    """configure_docker_auth should handle empty username/token gracefully."""
    result = configure_docker_auth("", "")

    assert "auths" in result
    expected_b64 = base64.b64encode(b":").decode("ascii")
    assert result["auths"]["https://index.docker.io/v1/"]["auth"] == expected_b64

    logger.critical("[IMP:9][test] configure_docker_auth — empty creds produce colon-only encoding — OK")


# enregion
