# GREP_SUMMARY: test-monitoring-langfuse-projects needs-llm secret-key 409-idempotent HTTP-error
# STRUCTURE: ┌5 test functions┐ → ◇ no LLM (1) → ◇ no secret (1) → ◇ created (1) → ◇ 409 skip (1) → ◇ network error (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/monitoring/langfuse_projects.py — create_langfuse_project()
#            (DevPlan 117 G T54 extraction).
## @scope    No network — urllib.request mocked.
## @invariants
##   - All subprocess/urllib calls mocked
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T54 §TEST_SPEC — langfuse_projects direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T54 — created
# endregion MODULE_CONTRACT

import urllib.error
from pathlib import Path
from unittest import mock

from monitoring.config_renderer import ProjectMonitoringConfig
from monitoring.langfuse_projects import create_langfuse_project


def _config(**overrides) -> ProjectMonitoringConfig:
    defaults = {
        "project_name": "myapp",
        "project_type": "backend",
        "project_dir": Path("/tmp"),
        "node_name": "tronyx-vps",
        "platform_root": Path("/opt/platform"),
        "needs_llm": True,
        "ai_retention_days": 30,
    }
    defaults.update(overrides)
    return ProjectMonitoringConfig(**defaults)


# 🧪 TRAP[TEST] · Regression · Scenario: no LLM needs
# · Expect: noop
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: create_langfuse_project logic changes
# GUARD-PRESERVE (168): единственное покрытие ветки needs_llm=False → noop (все остальные тесты файла — needs_llm=True)
def test_create_project_no_llm(caplog) -> None:
    """needs_llm=False → noop."""
    caplog.set_level(0)
    result = create_langfuse_project(_config(needs_llm=False))

    assert result.status == "noop"


# 🧪 TRAP[TEST] · Regression · Scenario: missing secret key
# · Expect: failed
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: secret-key guard logic changes
def test_create_project_no_secret(caplog, monkeypatch) -> None:
    """LANGFUSE_SECRET_KEY not set → failed."""
    caplog.set_level(0)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    result = create_langfuse_project(_config())

    assert result.status == "failed"


# 🧪 TRAP[TEST] · Regression · Scenario: HTTP 201 created
# · Expect: created
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: HTTP 200/201 handling changes
def test_create_project_created(caplog, monkeypatch) -> None:
    """HTTP 201 → created."""
    caplog.set_level(0)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    class MockResp:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch("core.internal.shared.http_client.request", return_value=MockResp()):
        result = create_langfuse_project(_config())

    assert result.status == "created"


# 🧪 TRAP[TEST] · Regression · Scenario: HTTP 409 already exists
# · Expect: skipped (idempotent)
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: 409 idempotency handling changes
def test_create_project_409_skip(caplog, monkeypatch) -> None:
    """HTTP 409 → skipped (idempotent)."""
    caplog.set_level(0)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    def _raise(*args, **kwargs):
        msg = "url"
        raise urllib.error.HTTPError(msg, 409, "Conflict", {}, None)

    with mock.patch("core.internal.shared.http_client.request", side_effect=_raise):
        result = create_langfuse_project(_config())

    assert result.status == "skipped"


# 🧪 TRAP[TEST] · Regression · Scenario: network error
# · Expect: failed
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: network-error handling changes
def test_create_project_network_error(caplog, monkeypatch) -> None:
    """URLError → failed."""
    caplog.set_level(0)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    with mock.patch(
        "core.internal.shared.http_client.request",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = create_langfuse_project(_config())

    assert result.status == "failed"
