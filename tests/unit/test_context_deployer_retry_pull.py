#!/usr/bin/env python3
# GREP_SUMMARY: test-context-deployer-retry-pull, retry-pull integration, ghcr-pull, build-fallback, audit-log, backoff-intervals
# STRUCTURE: ▶ tmp_path + ProjectInfo + mock shared functions → ◇ retry_pull success/fail → ◇ build fallback → ◇ audit verify → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for retry_pull integration in context_deployer._deploy_single_project.
##           Verifies the integration between _deploy_single_project and _shared_retry_pull
##           from core.internal.shared.docker_compose — NOT the retry_pull logic itself.
## @scope    Tests 5 scenarios:
##             1. retry_pull success → channel="ghcr"
##             2. retry_pull all fail → fallback to build → channel="build"
##             3. retry_pull called with correct backoff params
##             4. Successful retry_pull + audit logging
##             5. Fallback build + audit logging
## @invariants
##   - All shared functions mocked (no real docker compose)
##   - docker-compose.yml created in tmp_path (avoids bootstrap compose path)
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
## @rationale  DevPlan 079 DRIFT-B6: context_deployer uses shared retry_pull from
##             core.internal.shared.docker_compose. These tests verify correct integration
##             parameters (max_attempts, backoff_seconds) and fallback behaviour.
##             The retry_pull implementation is tested separately in test_shared_docker_compose.py.
## @changes  2026-07-26 | DevPlan 079 — Created retry_pull integration tests
# endregion MODULE_CONTRACT

import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ─────────────────────────────────────────────
# Use the same sys.path pattern as test_context_deployer.py for consistency.
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_MODULE_DIR))

import context_deployer as cd
from context_deployer import ProjectInfo


# ═══════════════════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_project() -> ProjectInfo:
    """Create a ProjectInfo instance for testing.

    ## @purpose — Provide a reusable ProjectInfo for all retry_pull integration tests.
    ## @io — ⇥ None → ⎋ ProjectInfo with name="test-app", context="test-ctx"
    """
    return ProjectInfo(
        name="test-app",
        repo="https://github.com/test/app",
        type="backend",
        domain="test-app.example.com",
        context="test-ctx",
    )


@pytest.fixture
def project_dir(tmp_path: Path) -> str:
    """Create a project directory with a minimal docker-compose.yml.

    ## @purpose — Provide a valid project directory with docker-compose.yml
    ##            so _deploy_single_project skips the bootstrap compose generation path.
    ## @io — ⇥ tmp_path → ⎋ str (path to project directory)
    """
    d = tmp_path / "projects" / "test-app"
    d.mkdir(parents=True, exist_ok=True)
    compose = d / "docker-compose.yml"
    compose.write_text(
        "version: '3.8'\n"
        "services:\n"
        "  app:\n"
        "    image: test:latest\n"
        "    restart: unless-stopped\n"
    )
    return str(d)


# endregion


# ═══════════════════════════════════════════════════════════════════════════════
# region Tests: retry_pull integration scenarios
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_retry_pull_success_first_attempt
## @purpose — Verify _deploy_single_project returns channel="ghcr" when
##            _shared_retry_pull succeeds on the first attempt.
## @io — ⇥ mocked project + shared functions → ⎋ ProjectDeployResult(channel="ghcr")
## @complexity — O(1)
## @invariants
##   - _is_project_healthy mocked → False (forces deploy path)
##   - _shared_retry_pull mocked → True
##   - _shared_docker_compose_up mocked → True
##   - _shared_healthcheck_poll mocked → "healthy"
##   - ghcr_fallback_build=True
@pytest.fixture
def _ensure_bootstrap_skip(monkeypatch):
    """Ensure _ensure_bootstrap_compose returns True so deploy proceeds.

    Avoids needing docker-compose.yml on disk; the fixture-based project_dir
    already provides one, but this guards against edge cases.
    """
    monkeypatch.setattr(cd, "_ensure_bootstrap_compose", lambda d, p: True)


@ldd_trajectory
def test_retry_pull_success_first_attempt(
    caplog: pytest.LogCaptureFixture,
    sample_project: ProjectInfo,
    project_dir: str,
) -> None:
    """retry_pull returns True → deploy succeeds via ghcr channel."""
    # 🧪 TRAP[TEST] · Regression · retry_pull success → channel="ghcr", status="deployed", health="healthy"
    # · Scenario: _is_project_healthy=False, retry_pull=True, up=True, healthcheck="healthy"
    # · Last fail: N/A (new test)
    # · Remove if: _deploy_single_project pull path changes

    with (
        patch.object(cd, "_is_project_healthy", return_value=False) as mock_healthy,
        patch.object(cd, "_shared_retry_pull", return_value=True) as mock_pull,
        patch.object(cd, "_shared_docker_compose_up", return_value=True) as mock_up,
        patch.object(cd, "_shared_healthcheck_poll", return_value="healthy") as mock_hc,
    ):
        result = cd._deploy_single_project(
            sample_project,
            projects_base=str(Path(project_dir).parent),
            ghcr_fallback_build=True,
        )

    # Verify mocks were exercised
    assert mock_healthy.called, "_is_project_healthy should be called"
    assert mock_pull.called, "_shared_retry_pull should be called"
    assert mock_up.called, "_shared_docker_compose_up should be called"
    assert mock_hc.called, "_shared_healthcheck_poll should be called"

    # Verify result
    assert result.status == "deployed", f"Expected status='deployed', got '{result.status}'"
    assert result.channel == "ghcr", f"Expected channel='ghcr', got '{result.channel}'"
    assert result.health == "healthy", f"Expected health='healthy', got '{result.health}'"
    assert result.error is None, f"Expected no error, got '{result.error}'"

    logger.critical("[IMP:9][test] retry_pull success — channel=ghcr, status=deployed, health=healthy")
# endregion


# region FUNC_test_retry_pull_all_failed_fallback_to_build
## @purpose — Verify _deploy_single_project falls back to on-node build when
##            all retry_pull attempts fail and ghcr_fallback_build=True.
## @io — ⇥ mocked project + shared functions → ⎋ ProjectDeployResult(channel="build")
## @complexity — O(1)
## @invariants
##   - _shared_retry_pull → False (all attempts fail)
##   - _shared_docker_compose_build → True (build succeeds)
##   - channel must be "build" after successful fallback
@ldd_trajectory
def test_retry_pull_all_failed_fallback_to_build(
    caplog: pytest.LogCaptureFixture,
    sample_project: ProjectInfo,
    project_dir: str,
) -> None:
    """retry_pull all fail → fallback to build → channel='build'."""
    # 🧪 TRAP[TEST] · Regression · retry_pull fails → fallback to build → channel="build"
    # · Scenario: _shared_retry_pull=False, _shared_docker_compose_build=True, up=True
    # · Last fail: N/A (new test)
    # · Remove if: build fallback logic in _deploy_single_project changes

    with (
        patch.object(cd, "_is_project_healthy", return_value=False) as mock_healthy,
        patch.object(cd, "_shared_retry_pull", return_value=False) as mock_pull,
        patch.object(cd, "_shared_docker_compose_build", return_value=True) as mock_build,
        patch.object(cd, "_shared_docker_compose_up", return_value=True) as mock_up,
        patch.object(cd, "_shared_healthcheck_poll", return_value="healthy") as mock_hc,
    ):
        result = cd._deploy_single_project(
            sample_project,
            projects_base=str(Path(project_dir).parent),
            ghcr_fallback_build=True,
        )

    # Verify mocks
    assert mock_healthy.called
    assert mock_pull.called
    assert mock_build.called, "_shared_docker_compose_build should be called on fallback"
    assert mock_up.called
    assert mock_hc.called

    # Verify result
    assert result.status == "deployed", f"Expected status='deployed', got '{result.status}'"
    assert result.channel == "build", f"Expected channel='build' (fallback), got '{result.channel}'"
    assert result.health == "healthy", f"Expected health='healthy', got '{result.health}'"
    assert result.error is None, f"Expected no error, got '{result.error}'"

    logger.critical("[IMP:9][test] retry_pull all fail → build fallback — channel=build, status=deployed")
# endregion


# region FUNC_test_retry_pull_backoff_intervals
## @purpose — Verify _shared_retry_pull is called with the correct parameters:
##            max_attempts=3, backoff_seconds=[5, 10, 20].
## @io — ⇥ mocked project + shared functions → ⎋ assertion on mock call args
## @complexity — O(1)
@ldd_trajectory
def test_retry_pull_backoff_intervals(
    caplog: pytest.LogCaptureFixture,
    sample_project: ProjectInfo,
    project_dir: str,
) -> None:
    """retry_pull called with max_attempts=3 and backoff_seconds=[5, 10, 20]."""
    # 🧪 TRAP[TEST] · Regression · retry_pull called with correct backoff params
    # · Scenario: capture _shared_retry_pull call args → verify max_attempts=3, backoff_seconds=[5,10,20]
    # · Last fail: N/A (new test)
    # · Remove if: retry_pull function signature or default backoff changes

    with (
        patch.object(cd, "_is_project_healthy", return_value=False),
        patch.object(cd, "_shared_retry_pull", return_value=True) as mock_pull,
        patch.object(cd, "_shared_docker_compose_up", return_value=True),
        patch.object(cd, "_shared_healthcheck_poll", return_value="healthy"),
    ):
        cd._deploy_single_project(
            sample_project,
            projects_base=str(Path(project_dir).parent),
            ghcr_fallback_build=True,
        )

    # Verify retry_pull was called exactly once with correct params
    mock_pull.assert_called_once()
    call_args, call_kwargs = mock_pull.call_args

    # Positional: compose_dir
    assert call_args[0] == project_dir, f"Expected compose_dir='{project_dir}', got '{call_args[0]}'"

    # Keyword args
    assert call_kwargs.get("max_attempts") == 3, (
        f"Expected max_attempts=3, got {call_kwargs.get('max_attempts')}"
    )
    assert call_kwargs.get("backoff_seconds") == [5, 10, 20], (
        f"Expected backoff_seconds=[5, 10, 20], got {call_kwargs.get('backoff_seconds')}"
    )

    logger.critical(
        "[IMP:9][test] retry_pull called with max_attempts=%d, backoff_seconds=%s — correct integration params",
        call_kwargs.get("max_attempts"),
        call_kwargs.get("backoff_seconds"),
    )
# endregion


# endregion


# ═══════════════════════════════════════════════════════════════════════════════
# region Tests: audit integration with retry_pull
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_retry_pull_audit_logged
## @purpose — Verify that successful retry_pull produces an audit entry via
##            write_audit_entry from core.internal.shared.audit_logger.
##            Status must contain "DEPLOYED", channel="ghcr".
## @io — ⇥ mocked deploy + audit → ⎋ assertion on write_audit_entry call args
## @complexity — O(1)
@ldd_trajectory
def test_retry_pull_audit_logged(
    caplog: pytest.LogCaptureFixture,
    sample_project: ProjectInfo,
    project_dir: str,
) -> None:
    """Successful retry_pull → audit entry with DEPLOYED and channel=ghcr."""
    # 🧪 TRAP[TEST] · Regression · retry_pull success → write_audit_entry called with DEPLOYED
    # · Scenario: retry_pull=True → deploy succeeds → _write_audit calls write_audit_entry(tag, DEPLOYED, channel=ghcr)
    # · Last fail: N/A (new test)
    # · Remove if: audit logging contract in _write_audit changes

    with (
        patch.object(cd, "_is_project_healthy", return_value=False),
        patch.object(cd, "_shared_retry_pull", return_value=True),
        patch.object(cd, "_shared_docker_compose_up", return_value=True),
        patch.object(cd, "_shared_healthcheck_poll", return_value="healthy"),
    ):
        result = cd._deploy_single_project(
            sample_project,
            projects_base=str(Path(project_dir).parent),
            ghcr_fallback_build=True,
        )

    # Verify deploy result before testing audit
    assert result.status == "deployed", f"Precondition failed: expected deployed, got '{result.status}'"
    assert result.channel == "ghcr", f"Precondition failed: expected ghcr, got '{result.channel}'"

    # Now verify _write_audit produces the correct audit entry
    # _write_audit does a local import of write_audit_entry from audit_logger
    with patch("core.internal.shared.audit_logger.write_audit_entry") as mock_audit:
        cd._write_audit(sample_project, result)

        assert mock_audit.called, "write_audit_entry should be called"
        call_args, call_kwargs = mock_audit.call_args

        # Verify status contains DEPLOYED
        status_arg = call_kwargs.get("status") or call_args[1]
        assert "DEPLOYED" in str(status_arg).upper(), (
            f"Expected status containing 'DEPLOYED', got '{status_arg}'"
        )

        # Verify tag contains project name
        tag_arg = call_kwargs.get("tag") or call_args[0]
        assert sample_project.name in str(tag_arg), (
            f"Expected tag containing '{sample_project.name}', got '{tag_arg}'"
        )

        # Verify message contains channel=ghcr
        msg_arg = call_kwargs.get("message") or call_args[2]
        assert "ghcr" in str(msg_arg).lower(), (
            f"Expected message containing 'ghcr', got '{msg_arg}'"
        )

        logger.critical(
            "[IMP:9][test] Audit entry verified — tag=%s status=%s msg=%s",
            tag_arg,
            status_arg,
            msg_arg,
        )
# endregion


# region FUNC_test_fallback_build_audit_logged
## @purpose — Verify that fallback-to-build produces an audit entry with
##            channel="build" in the message.
## @io — ⇥ mocked deploy (pull fail, build ok) + audit → ⎋ assertion on write_audit_entry call args
## @complexity — O(1)
@ldd_trajectory
def test_fallback_build_audit_logged(
    caplog: pytest.LogCaptureFixture,
    sample_project: ProjectInfo,
    project_dir: str,
) -> None:
    """Fallback build → audit entry with channel=build, status contains DEPLOYED."""
    # 🧪 TRAP[TEST] · Regression · fallback build → write_audit_entry called with channel=build
    # · Scenario: retry_pull=False, build=True → _write_audit(tag, DEPLOYED, channel=build)
    # · Last fail: N/A (new test)
    # · Remove if: build fallback audit logging changes

    with (
        patch.object(cd, "_is_project_healthy", return_value=False),
        patch.object(cd, "_shared_retry_pull", return_value=False),
        patch.object(cd, "_shared_docker_compose_build", return_value=True),
        patch.object(cd, "_shared_docker_compose_up", return_value=True),
        patch.object(cd, "_shared_healthcheck_poll", return_value="healthy"),
    ):
        result = cd._deploy_single_project(
            sample_project,
            projects_base=str(Path(project_dir).parent),
            ghcr_fallback_build=True,
        )

    # Verify deploy result before testing audit
    assert result.status == "deployed", f"Precondition failed: expected deployed, got '{result.status}'"
    assert result.channel == "build", f"Precondition failed: expected build, got '{result.channel}'"

    # Now verify _write_audit produces the correct audit entry
    with patch("core.internal.shared.audit_logger.write_audit_entry") as mock_audit:
        cd._write_audit(sample_project, result)

        assert mock_audit.called, "write_audit_entry should be called"
        call_args, call_kwargs = mock_audit.call_args

        # Verify status contains DEPLOYED
        status_arg = call_kwargs.get("status") or call_args[1]
        assert "DEPLOYED" in str(status_arg).upper(), (
            f"Expected status containing 'DEPLOYED', got '{status_arg}'"
        )

        # Verify message contains channel=build
        msg_arg = call_kwargs.get("message") or call_args[2]
        assert "build" in str(msg_arg).lower(), (
            f"Expected message containing 'build', got '{msg_arg}'"
        )
        assert "channel=build" in str(msg_arg).lower(), (
            f"Expected message containing 'channel=build', got '{msg_arg}'"
        )

        logger.critical(
            "[IMP:9][test] Fallback build audit verified — tag=%s status=%s msg=%s",
            call_kwargs.get("tag") or call_args[0],
            status_arg,
            msg_arg,
        )
# endregion


# endregion
