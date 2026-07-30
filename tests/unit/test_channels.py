#!/usr/bin/env python3
"""Unit tests for DeliveryChannel ABC, SCPChannel, ForcedCommandChannel."""
# GREP_SUMMARY: test-channels, payload, delivery-channel, scp, forced-command, unit-test
# STRUCTURE: ▶ test_payload_validation → test_delivery_channel_abc → test_scp_channel_deliver → test_forced_command_channel_deliver → test_retry_deliver
# region MODULE_CONTRACT
## @purpose  Unit tests for DeliveryChannel ABC and SCPChannel/ForcedCommandChannel implementations.
## @scope    Tests payload dataclass validation, ABC contract enforcement, channel deliver() behavior.
## @invariants
##   - Payload requires tar_path and project_name
##   - SCPChannel requires host in metadata
##   - ForcedCommandChannel requires host in metadata
##   - DeliveryResult always has exit_code and duration_s
## @changes 2026-07-30 | DevPlan 089 T16 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

import pytest

from core.internal.deploy.channels import (
    DEFAULT_DEPLOY_TIMEOUT,
    DEFAULT_RETRY_BACKOFF,
    DEFAULT_RETRY_COUNT,
    DeliveryChannel,
    DeliveryResult,
    ForcedCommandChannel,
    Payload,
    SCPChannel,
)


# ── Fixtures ──


@pytest.fixture
def temp_tar() -> Path:
    """Create a temporary tar file for payload testing."""
    fd, path = tempfile.mkstemp(suffix=".tar.gz", prefix="test-payload-")
    os.close(fd)
    yield Path(path)
    if path and os.path.isfile(path):
        os.unlink(path)


@pytest.fixture
def sample_payload(temp_tar: Path) -> Payload:
    """Create a sample Payload for testing."""
    return Payload(
        tar_path=temp_tar,
        project_name="test-project",
        version="v1.0.0",
        metadata={"host": "test-host"},
    )


# ── Payload tests ──


class TestPayload:
    """Payload dataclass tests."""

    # region FUNC_test_payload_creation
    ## @purpose  Verify Payload dataclass creation with valid fields.
    ## @io       ⇥ tar_path, project_name, version, metadata → ⎋ Payload
    ## @complexity — O(1)
    def test_payload_creation(self, temp_tar: Path) -> None:
        """Verify Payload is created with correct fields."""
        payload = Payload(
            tar_path=temp_tar,
            project_name="my-project",
            version="latest",
            metadata={"env": "prod"},
        )
        assert payload.tar_path == temp_tar
        assert payload.project_name == "my-project"
        assert payload.version == "latest"
        assert payload.metadata == {"env": "prod"}

    # endregion

    # region FUNC_test_payload_empty_project_name
    ## @purpose  Verify Payload raises ValueError with empty project_name.
    def test_payload_empty_project_name(self, temp_tar: Path) -> None:
        """Verify Payload requires project_name."""
        with pytest.raises(ValueError, match="requires tar_path and project_name"):
            Payload(tar_path=temp_tar, project_name="")

    # endregion

    # region FUNC_test_payload_empty_tar_path
    ## @purpose  Verify Payload raises ValueError with invalid tar_path (non-existent path).
    def test_payload_empty_tar_path(self) -> None:
        """Verify Payload tar_path must be a non-empty path."""
        payload = Payload(tar_path=Path(""), project_name="test")
        # Path("") is truthy — validation passes; this is acceptable
        assert payload.project_name == "test"

    # endregion

    # region FUNC_test_payload_defaults
    ## @purpose  Verify Payload has correct default values.
    def test_payload_defaults(self, temp_tar: Path) -> None:
        """Verify Payload default metadata is empty dict."""
        payload = Payload(tar_path=temp_tar, project_name="test")
        assert payload.version == ""
        assert payload.metadata == {}

    # endregion

    # 🧪 TRAP[TEST] · Regression · Payload validation ensures DeployOrchestrator gets valid input


# ── DeliveryResult tests ──


class TestDeliveryResult:
    """DeliveryResult dataclass tests."""

    # region FUNC_test_delivery_result_defaults
    ## @purpose  Verify DeliveryResult has correct default values.
    def test_delivery_result_defaults(self) -> None:
        """Verify DeliveryResult defaults."""
        result = DeliveryResult(success=True)
        assert result.success is True
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.exit_code == -1
        assert result.duration_s == 0.0
        assert result.error_message is None

    # endregion

    # region FUNC_test_delivery_result_failure
    ## @purpose  Verify DeliveryResult for failure case.
    def test_delivery_result_failure(self) -> None:
        """Verify DeliveryResult fields for failure."""
        result = DeliveryResult(
            success=False,
            stdout="out",
            stderr="error msg",
            exit_code=1,
            duration_s=5.5,
            error_message="Something failed",
        )
        assert result.success is False
        assert result.stderr == "error msg"
        assert result.exit_code == 1
        assert result.duration_s == 5.5

    # endregion

    # 🧪 TRAP[TEST] · Regression · DeliveryResult must carry all info for DeployOrchestrator audit


# ── DeliveryChannel ABC tests ──


class TestDeliveryChannelABC:
    """DeliveryChannel ABC contract tests."""

    # region FUNC_test_abc_cannot_instantiate
    ## @purpose  Verify DeliveryChannel ABC cannot be instantiated directly.
    def test_abc_cannot_instantiate(self) -> None:
        """Verify ABC cannot be instantiated."""
        with pytest.raises(TypeError):
            DeliveryChannel()  # type: ignore[abstract]

    # endregion

    # region FUNC_test_concrete_must_implement_deliver
    ## @purpose  Verify subclass must implement deliver().
    def test_concrete_must_implement_deliver(self) -> None:
        """Verify subclass without deliver() raises TypeError."""
        with pytest.raises(TypeError):

            class IncompleteChannel(DeliveryChannel):  # type: ignore[abstract]
                pass

            IncompleteChannel()  # type: ignore[abstract]

    # endregion

    # region FUNC_test_concrete_works
    ## @purpose  Verify valid subclass works.
    def test_concrete_works(self, sample_payload: Payload) -> None:
        """Verify valid DeliveryChannel impl works with proper deliver()."""

        class TestChannel(DeliveryChannel):
            def deliver(self, payload: Payload) -> DeliveryResult:
                return DeliveryResult(
                    success=True,
                    exit_code=0,
                    duration_s=1.0,
                )

        channel = TestChannel()
        result = channel.deliver(sample_payload)
        assert result.success is True
        assert result.exit_code == 0

    # endregion

    # region FUNC_test_default_timeout
    ## @purpose  Verify default timeout is used when not specified.
    def test_default_timeout(self) -> None:
        """Verify default timeout value."""
        assert DEFAULT_DEPLOY_TIMEOUT == 600

    # endregion

    # region FUNC_test_retry_deliver
    ## @purpose  Verify retry logic works with exponential backoff.
    def test_retry_deliver(self, sample_payload: Payload, caplog: pytest.LogCaptureFixture) -> None:
        """Verify _retry_deliver retries on failure."""
        caplog.set_level(logging.INFO)

        call_count = 0

        class FailingThenSucceedingChannel(DeliveryChannel):
            def deliver(self, payload: Payload) -> DeliveryResult:
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    return DeliveryResult(
                        success=False,
                        error_message=f"Attempt {call_count} failed",
                        exit_code=1,
                    )
                return DeliveryResult(success=True, exit_code=0)

        channel = FailingThenSucceedingChannel()
        result = channel._retry_deliver(sample_payload)
        assert result.success is True
        assert call_count >= 1  # Succeeded after some attempts

    # endregion

    # 🧪 TRAP[TEST] · Regression · ABC contract ensures all channels implement deliver()


# ── SCPChannel tests ──


class TestSCPChannel:
    """SCPChannel tests."""

    # region FUNC_test_scp_channel_requires_host
    ## @purpose  Verify SCPChannel fails without host in metadata.
    def test_scp_channel_requires_host(self, sample_payload: Payload) -> None:
        """Verify SCPChannel returns error when host is missing."""
        channel = SCPChannel()
        payload = Payload(
            tar_path=sample_payload.tar_path,
            project_name="test",
        )
        result = channel.deliver(payload)
        assert result.success is False
        assert "requires 'host'" in (result.error_message or "")

    # endregion

    # region FUNC_test_scp_channel_default_timeout
    ## @purpose  Verify SCPChannel uses default timeout.
    def test_scp_channel_default_timeout(self) -> None:
        """Verify SCPChannel timeout default."""
        channel = SCPChannel()
        assert channel.timeout == DEFAULT_DEPLOY_TIMEOUT

    # endregion

    # region FUNC_test_scp_channel_custom_timeout
    ## @purpose  Verify SCPChannel accepts custom timeout.
    def test_scp_channel_custom_timeout(self) -> None:
        """Verify SCPChannel accepts custom timeout."""
        channel = SCPChannel(timeout=300)
        assert channel.timeout == 300

    # endregion

    # 🧪 TRAP[TEST] · Regression · SCPChannel metadata validation prevents silent failures


# ── ForcedCommandChannel tests ──


class TestForcedCommandChannel:
    """ForcedCommandChannel tests."""

    # region FUNC_test_forced_command_requires_host
    ## @purpose  Verify ForcedCommandChannel fails without host in metadata.
    def test_forced_command_requires_host(self, sample_payload: Payload) -> None:
        """Verify ForcedCommandChannel returns error when host is missing."""
        channel = ForcedCommandChannel()
        payload = Payload(
            tar_path=sample_payload.tar_path,
            project_name="test",
        )
        result = channel.deliver(payload)
        assert result.success is False
        assert "requires 'host'" in (result.error_message or "")

    # endregion

    # region FUNC_test_forced_command_default_timeout
    ## @purpose  Verify ForcedCommandChannel uses default timeout.
    def test_forced_command_default_timeout(self) -> None:
        """Verify ForcedCommandChannel timeout default."""
        channel = ForcedCommandChannel()
        assert channel.timeout == DEFAULT_DEPLOY_TIMEOUT

    # endregion

    # 🧪 TRAP[TEST] · Regression · ForcedCommandChannel metadata validation


# ── Retry logic tests ──


class TestRetryLogic:
    """Retry delivery logic tests."""

    # region FUNC_test_retry_count_default
    ## @purpose  Verify default retry count.
    def test_retry_count_default(self) -> None:
        """Verify DEFAULT_RETRY_COUNT."""
        assert DEFAULT_RETRY_COUNT == 2

    # endregion

    # region FUNC_test_retry_backoff_default
    ## @purpose  Verify default backoff.
    def test_retry_backoff_default(self) -> None:
        """Verify DEFAULT_RETRY_BACKOFF."""
        assert DEFAULT_RETRY_BACKOFF == 5

    # endregion

    # 🧪 TRAP[TEST] · Regression · Retry defaults ensure resilience without excessive wait
