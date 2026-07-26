#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-docker-compose docker-compose pull build up healthcheck retry image-exists
# STRUCTURE: ┌mock subprocess.run┐ → ○ test scenarios: pull → build → up → healthcheck → retry → image-exists
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/docker_compose.py
##           Uses mock subprocess.run to verify all docker compose operations.
## @scope    Tests: pull, build, up, healthcheck_poll, retry_pull, check_image_exists.
## @invariants
##   - All tests use tmp_path (no hardcoded paths)
##   - No Docker dependency (mocked subprocess)
##   - LDD: at least one IMP:9 log in each successful scenario
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core.internal.shared.docker_compose import (
    check_image_exists,
    docker_compose_build,
    docker_compose_pull,
    docker_compose_up,
    healthcheck_poll,
    retry_pull,
)

# ── Fixture ─────────────────────────────────────────────────────────────────


@pytest.fixture
def compose_dir(tmp_path: Path) -> str:
    """Create a mock compose directory.

    ## @purpose — Provide a valid directory path for docker compose tests.
    ## @io — ⇥ tmp_path → ⎋ str (directory path)
    """
    d = tmp_path / "my_project"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


# ── Pull tests ──────────────────────────────────────────────────────────────


# region FUNC_test_pull_success
## @purpose — Verify docker_compose_pull returns True on success.
##            AC: mock subprocess.run returncode=0 → True.
## @complexity — O(1)
def test_pull_success(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """docker compose pull returns True on success."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: pull success
    # · Last fail: N/A (new test)
    # · Remove if: docker_compose_pull behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        result = docker_compose_pull(compose_dir)

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_pull_failure
## @purpose — Verify docker_compose_pull returns False on failure.
##            AC: mock subprocess.run returncode=1 → False.
## @complexity — O(1)
def test_pull_failure(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """docker compose pull returns False on failure."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: pull failure
    # · Last fail: N/A (new test)
    # · Remove if: docker_compose_pull behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "error"

        result = docker_compose_pull(compose_dir)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is False


# endregion


# ── Build tests ─────────────────────────────────────────────────────────────


# region FUNC_test_build_success
## @purpose — Verify docker_compose_build returns True on success.
##            AC: mock subprocess.run returncode=0 → True.
## @complexity — O(1)
def test_build_success(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """docker compose build returns True on success."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: build success
    # · Last fail: N/A (new test)
    # · Remove if: docker_compose_build behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        result = docker_compose_build(compose_dir)

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# ── Up tests ────────────────────────────────────────────────────────────────


# region FUNC_test_up_success
## @purpose — Verify docker_compose_up returns True on success.
##            AC: mock subprocess.run returncode=0 → True.
## @complexity — O(1)
def test_up_success(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """docker compose up -d returns True on success."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: compose up success
    # · Last fail: N/A (new test)
    # · Remove if: docker_compose_up behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        result = docker_compose_up(compose_dir)

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# ── Healthcheck tests ───────────────────────────────────────────────────────


# region FUNC_test_healthcheck_poll_healthy
## @purpose — Verify healthcheck_poll returns "healthy" when containers are healthy.
##            AC: mock docker ps → "healthy".
## @complexity — O(1)
def test_healthcheck_poll_healthy(caplog: pytest.LogCaptureFixture) -> None:
    """healthcheck_poll returns 'healthy' when containers are Up."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: healthy containers
    # · Last fail: N/A (new test)
    # · Remove if: healthcheck_poll behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        # First call may show no containers, second shows healthy
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=0, stdout="", stderr=""),  # no containers yet
            subprocess.CompletedProcess([], returncode=0, stdout="Up 2 hours\nhealthy", stderr=""),  # healthy
        ]

        result = healthcheck_poll("test_project", timeout=10, interval=1)

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result == "healthy"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_healthcheck_poll_timeout
## @purpose — Verify healthcheck_poll returns "unhealthy" after timeout.
##            AC: mock always unhealthy → returns "unhealthy" after N attempts.
## @complexity — O(1)
def test_healthcheck_poll_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """healthcheck_poll returns 'unhealthy' after timeout with unhealthy containers."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: timeout
    # · Last fail: N/A (new test)
    # · Remove if: healthcheck_poll behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "unhealthy"

        result = healthcheck_poll("test_project", timeout=1, interval=1)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result == "unhealthy"


# endregion


# ── Retry pull tests ────────────────────────────────────────────────────────


# region FUNC_test_retry_pull_success_second_attempt
## @purpose — Verify retry_pull succeeds on second attempt.
##            AC: 1st fail, 2nd success → True.
## @complexity — O(1)
def test_retry_pull_success_second_attempt(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """retry_pull returns True when second attempt succeeds."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: retry success on 2nd attempt
    # · Last fail: N/A (new test)
    # · Remove if: retry_pull behavior changes

    with patch("core.internal.shared.docker_compose.docker_compose_pull") as mock_pull:
        mock_pull.side_effect = [False, True]

        result = retry_pull(compose_dir, max_attempts=3, backoff_seconds=[1, 1, 1])

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert mock_pull.call_count == 2
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_retry_pull_all_fail
## @purpose — Verify retry_pull returns False after all attempts fail.
##            AC: all 3 attempts fail → False.
## @complexity — O(1)
def test_retry_pull_all_fail(compose_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    """retry_pull returns False when all attempts fail."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: all retries fail
    # · Last fail: N/A (new test)
    # · Remove if: retry_pull behavior changes

    with patch("core.internal.shared.docker_compose.docker_compose_pull") as mock_pull:
        mock_pull.return_value = False

        result = retry_pull(compose_dir, max_attempts=3, backoff_seconds=[1, 1, 1])

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is False
    assert mock_pull.call_count == 3


# endregion


# ── Check image exists tests ────────────────────────────────────────────────


# region FUNC_test_check_image_exists_found
## @purpose — Verify check_image_exists returns True when image is found.
##            AC: docker manifest inspect returncode=0 → True.
## @complexity — O(1)
def test_check_image_exists_found(caplog: pytest.LogCaptureFixture) -> None:
    """check_image_exists returns True when docker manifest inspect succeeds."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: image found in registry
    # · Last fail: N/A (new test)
    # · Remove if: check_image_exists behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        result = check_image_exists("ghcr.io/test/image:latest")

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is True
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_check_image_exists_not_found
## @purpose — Verify check_image_exists returns False when image not found.
##            AC: docker manifest inspect returncode=1 → False.
## @complexity — O(1)
def test_check_image_exists_not_found(caplog: pytest.LogCaptureFixture) -> None:
    """check_image_exists returns False when docker manifest inspect fails."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: image not found in registry
    # · Last fail: N/A (new test)
    # · Remove if: check_image_exists behavior changes

    with patch("core.internal.shared.docker_compose.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1

        result = check_image_exists("ghcr.io/test/image:latest")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert result is False


# endregion
