#!/usr/bin/env python3
"""Unit tests for HealthcheckPoller."""
# GREP_SUMMARY: test-healthcheck-poller, healthcheck, poll, timeout, retry, unit-test
# STRUCTURE: ▶ test_init → test_poll_project_unknown → test_poll_until_healthy_timeout → test_healthcheck_result
# region MODULE_CONTRACT
## @purpose  Unit tests for HealthcheckPoller — health polling utility.
## @scope    Tests timeout, interval, max_retries configuration and edge cases.
## @invariants
##   - Default timeout: 30s, interval: 10s, max_retries: 6
##   - Returns "unhealthy" on any failure (never raises)
##   - poll_until_healthy returns "timeout" when retries exhausted
## @changes 2026-07-30 | DevPlan 089 T16 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from core.internal.deploy.healthcheck_poller import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_TIMEOUT,
    HealthcheckPoller,
    HealthcheckResult,
)


class TestHealthcheckPoller:
    """HealthcheckPoller unit tests."""

    # region FUNC_test_init_defaults
    def test_init_defaults(self) -> None:
        """Verify default parameters."""
        poller = HealthcheckPoller()
        assert poller.timeout == DEFAULT_POLL_TIMEOUT
        assert poller.interval == DEFAULT_POLL_INTERVAL
        assert poller.max_retries == DEFAULT_MAX_RETRIES

    # endregion

    # region FUNC_test_init_custom
    def test_init_custom(self) -> None:
        """Verify custom parameters."""
        poller = HealthcheckPoller(timeout=5, interval=2, max_retries=3)
        assert poller.timeout == 5
        assert poller.interval == 2
        assert poller.max_retries == 3

    # endregion

    # region FUNC_test_poll_project_no_method
    def test_poll_project_no_method(self) -> None:
        """Verify poll returns unhealthy when no method available."""
        poller = HealthcheckPoller(timeout=1, interval=1, max_retries=1)
        result = poller.poll_project("nonexistent-project")
        assert result.status == "unhealthy"
        assert result.project == "nonexistent-project"
        assert result.method == "unknown"

    # endregion

    # region FUNC_test_poll_until_healthy_timeout
    def test_poll_until_healthy_timeout(self) -> None:
        """Verify poll_until_healthy returns timeout after retries."""
        poller = HealthcheckPoller(timeout=1, interval=1, max_retries=2)
        result = poller.poll_until_healthy("nonexistent-project")
        assert result.status == "timeout"
        assert result.attempts == 2

    # endregion

    # region FUNC_test_healthcheck_result_creation
    def test_healthcheck_result_creation(self) -> None:
        """Verify HealthcheckResult dataclass."""
        result = HealthcheckResult(
            status="healthy",
            project="test",
            method="http",
            attempts=2,
            detail="OK",
        )
        assert result.status == "healthy"
        assert result.project == "test"
        assert result.method == "http"
        assert result.attempts == 2
        assert result.detail == "OK"

    # endregion

    # region FUNC_test_healthcheck_result_unhealthy
    def test_healthcheck_result_unhealthy(self) -> None:
        """Verify unhealthy HealthcheckResult."""
        result = HealthcheckResult(
            status="unhealthy",
            project="test",
            method="docker",
            attempts=3,
            detail="Connection refused",
        )
        assert result.status == "unhealthy"
        assert result.attempts == 3

    # endregion

    # 🧪 TRAP[TEST] · Regression · HealthcheckPoller is non-fatal on any failure
