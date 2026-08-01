#!/usr/bin/env python3
# GREP_SUMMARY: test-monitoring-service-reload prometheus loki HTTP-POST reload created failed
# STRUCTURE: ┌3 test functions┐ → ◇ both reload succeed (1) → ◇ both fail (1) → ◇ partial (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/monitoring/service_reload.py — reload_monitoring_services()
#            (DevPlan 117 G T54 extraction).
## @scope    No network — urllib.request.urlopen mocked.
## @invariants
##   - All urllib calls mocked
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T54 §TEST_SPEC — service_reload direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T54 — created
# endregion MODULE_CONTRACT

import urllib.error
from unittest import mock

from monitoring.service_reload import reload_monitoring_services


class _MockResp:
    """Mock urllib response with context manager."""

    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# 🧪 TRAP[TEST] · Regression · Scenario: both services reload OK
# · Expect: 2 created results
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: reload_monitoring_services logic changes
def test_reload_both_created(caplog) -> None:
    """Prometheus + Loki reload → 2 created results."""
    caplog.set_level(0)
    with mock.patch(
        "monitoring.service_reload.urllib.request.urlopen",
        return_value=_MockResp(200),
    ):
        results = reload_monitoring_services()

    assert len(results) == 2
    assert all(r.status == "created" for r in results)


# 🧪 TRAP[TEST] · Regression · Scenario: both fail
# · Expect: 2 failed results
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: reload error handling changes
def test_reload_both_failed(caplog) -> None:
    """Both HTTP calls raise → 2 failed results."""
    caplog.set_level(0)
    with mock.patch(
        "monitoring.service_reload.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        results = reload_monitoring_services()

    assert len(results) == 2
    assert all(r.status == "failed" for r in results)


# 🧪 TRAP[TEST] · Regression · Scenario: mixed outcome
# · Expect: one created + one failed
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: reload partial-failure handling changes
def test_reload_mixed(caplog) -> None:
    """First succeeds, second fails → created + failed."""
    caplog.set_level(0)
    calls = [0]

    def _side_effect(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            return _MockResp(200)
        raise urllib.error.HTTPError("url", 500, "Internal", {}, None)

    with mock.patch("monitoring.service_reload.urllib.request.urlopen", side_effect=_side_effect):
        results = reload_monitoring_services()

    assert len(results) == 2
    assert results[0].status == "created"
    assert results[1].status == "failed"
