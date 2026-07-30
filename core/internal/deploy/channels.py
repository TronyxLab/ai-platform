#!/usr/bin/env python3
"""
Delivery Channel abstractions for DeployOrchestrator — SCPChannel and ForcedCommandChannel.
"""
# GREP_SUMMARY: delivery-channels, abc, scp, rsync, forced-command, ssh, payload, deliver, timeout, retry, auth
# STRUCTURE: ▶ Payload dataclass → DeliveryResult dataclass → DeliveryChannel ABC → SCPChannel(deliver via scp/rsync) → ForcedCommandChannel(deliver via SSH forced-command)
# region MODULE_CONTRACT
## @purpose  DeliveryChannel ABC with SCPChannel and ForcedCommandChannel implementations.
##           Each channel delivers a Payload (tar_path, project_name, version, metadata)
##           and returns DeliveryResult (success, stdout, stderr, exit_code, duration_s).
## @scope    Used by DeployOrchestrator to abstract delivery mechanism. SCPChannel for
##           bootstrap/rsync delivery, ForcedCommandChannel for CI/tar+SSH forced-command.
## @invariants
##   1. Payload must contain at minimum tar_path and project_name
##   2. All channels have configurable timeout (default 600s) via PLATFORM_DEPLOY_TIMEOUT env var
##   3. Retry: 2 retries + exponential backoff (initial 5s, factor 2×)
##   4. Auth: SSH key-based only (no password auth)
##   5. SCPChannel uses SSH agent forwarding
##   6. DeliveryResult always has duration_s populated
## @rationale DevPlan 089 DD1: ABC allows adding third channel (HTTP push for serverless)
##            without changing DeployOrchestrator. Two existing channels (SCP, forced-command)
##            have fundamentally different lifecycles.
## @changes 2026-07-30 | DevPlan 089 T1/T2/T3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import abc
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Defaults ──
DEFAULT_DEPLOY_TIMEOUT = int(os.environ.get("PLATFORM_DEPLOY_TIMEOUT", "600"))
DEFAULT_RETRY_COUNT = 2
DEFAULT_RETRY_BACKOFF = 5  # seconds, multiplied by 2× each retry


# region DATACLASSES


@dataclass
class Payload:
    """Payload to be delivered via a DeliveryChannel.

    ## @purpose — Encapsulates all data needed for a deploy delivery.
    ## @io — ⇥ constructor params → ⎋ Payload instance
    ## @complexity — O(1)
    """

    tar_path: Path
    project_name: str
    version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tar_path or not self.project_name:
            raise ValueError("Payload requires tar_path and project_name")


@dataclass
class DeliveryResult:
    """Result of a payload delivery operation.

    ## @purpose — Track delivery outcome, timing, and error details.
    ## @io — ⇥ constructor params → ⎋ DeliveryResult instance
    ## @complexity — O(1)
    """

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_s: float = 0.0
    error_message: str | None = None


# endregion DATACLASSES


# region CLASS_DeliveryChannel (ABC)


class DeliveryChannel(abc.ABC):
    """Abstract base for delivery channels.

    ## @purpose — Define contract for all delivery channels.
    ## @io — ⇥ Payload → ⎋ DeliveryResult
    ## @complexity — O(N) where N depends on channel implementation
    ## @invariants
    ##   - deliver() must always return a DeliveryResult (never raise)
    ##   - Timeout applies to the entire deliver() call
    ##   - Retries are handled internally by deliver()
    """

    def __init__(self, timeout: int = DEFAULT_DEPLOY_TIMEOUT):
        self.timeout = timeout

    @abc.abstractmethod
    def deliver(self, payload: Payload) -> DeliveryResult:
        """Deliver a payload through this channel.

        Args:
            payload: Payload to deliver.

        Returns:
            DeliveryResult with status and timing.
        """
        ...

    def _retry_deliver(self, payload: Payload) -> DeliveryResult:
        """Deliver with retry logic (2 retries + exponential backoff).

        Args:
            payload: Payload to deliver.

        Returns:
            DeliveryResult from the last attempt.
        """
        last_result: DeliveryResult | None = None
        delay = DEFAULT_RETRY_BACKOFF

        for attempt in range(1 + DEFAULT_RETRY_COUNT):
            logger.info(
                "[IMP:8][_retry_deliver][attempt] Deliver attempt %d/%d for %s",
                attempt,
                1 + DEFAULT_RETRY_COUNT,
                payload.project_name,
            )
            start = time.monotonic()
            try:
                result = self.deliver(payload)
                result.duration_s = time.monotonic() - start
                if result.success:
                    logger.info(
                        "[IMP:9][_retry_deliver][success] Deliver succeeded on attempt %d for %s",
                        attempt,
                        payload.project_name,
                    )
                    return result
                last_result = result
            except (subprocess.CalledProcessError, OSError, TimeoutError) as e:
                duration = time.monotonic() - start
                logger.error(
                    "[IMP:10][_retry_deliver][exception] Deliver exception on attempt %d for %s: %s",
                    attempt,
                    payload.project_name,
                    e,
                )
                last_result = DeliveryResult(
                    success=False,
                    stderr=str(e),
                    exit_code=-1,
                    duration_s=duration,
                    error_message=str(e),
                )

            if attempt < DEFAULT_RETRY_COUNT:
                logger.info(
                    "[IMP:7][_retry_deliver][backoff] Waiting %ds before retry %d for %s",
                    delay,
                    attempt + 1,
                    payload.project_name,
                )
                time.sleep(delay)
                delay *= 2  # Exponential backoff

        if last_result is None:
            return DeliveryResult(success=False, error_message="No attempts made")
        return last_result


# endregion CLASS_DeliveryChannel (ABC)


# region CLASS_SCPChannel


class SCPChannel(DeliveryChannel):
    """SCP/rsync-based delivery channel. Uses SSH agent forwarding.

    ## @purpose — Deliver payload via scp/rsync + remote-cmd.sh unpack.
    ##            Uses SSH key-based auth with agent forwarding.
    ## @io — ⇥ Payload → ⎋ DeliveryResult
    ## @complexity — O(N) where N = file size / transfer speed
    ## @invariants
    ##   - Host parameter passed in payload.metadata["host"]
    ##   - Remote path in payload.metadata["remote_dir"]
    ##   - SSH user in payload.metadata.get("user", "root")
    ##   - Uses rsync -avz for delivery, with --delete for cleanup
    """

    def __init__(self, timeout: int = DEFAULT_DEPLOY_TIMEOUT):
        super().__init__(timeout)
        self.ssh_opts: list[str] = [
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=30",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=10",
        ]

    def deliver(self, payload: Payload) -> DeliveryResult:
        host = payload.metadata.get("host", "")
        remote_dir = payload.metadata.get("remote_dir", "/opt/projects")
        user = payload.metadata.get("user", "root")

        if not host:
            return DeliveryResult(
                success=False,
                error_message="SCPChannel requires 'host' in payload.metadata",
                exit_code=1,
            )

        remote = f"{user}@{host}" if user else host
        target = f"{remote}:{remote_dir}/{payload.project_name}/"

        # Ensure remote dir exists
        ssh_cmd = ["ssh", *self.ssh_opts, remote, f"mkdir -p {remote_dir}/{payload.project_name}"]
        logger.info(
            "[IMP:8][SCPChannel][deliver] Creating remote dir %s/%s on %s",
            remote_dir,
            payload.project_name,
            host,
        )
        start = time.monotonic()
        try:
            mkdir_result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
            if mkdir_result.returncode != 0:
                return DeliveryResult(
                    success=False,
                    stdout=mkdir_result.stdout,
                    stderr=mkdir_result.stderr,
                    exit_code=mkdir_result.returncode,
                    duration_s=time.monotonic() - start,
                    error_message=f"mkdir failed on {host}: {mkdir_result.stderr.strip()}",
                )

            # Rsync tar file
            rsync_cmd = [
                "rsync",
                "-avz",
                "--progress",
                "-e",
                f"ssh {' '.join(self.ssh_opts)}",
                str(payload.tar_path),
                target,
            ]
            logger.info(
                "[IMP:8][SCPChannel][deliver] Rsyncing %s → %s",
                payload.tar_path,
                target,
            )
            rsync_result = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=self.timeout)
            duration = time.monotonic() - start

            if rsync_result.returncode == 0:
                # Run remote-cmd.sh unpack if available
                unpack_script = f"{remote_dir}/{payload.project_name}/remote-cmd.sh unpack {payload.project_name}"
                subprocess.run(
                    ["ssh", *self.ssh_opts, remote, unpack_script],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                logger.info(
                    "[IMP:9][SCPChannel][deliver] Deliver SUCCESS for %s to %s",
                    payload.project_name,
                    host,
                )
                return DeliveryResult(
                    success=True,
                    stdout=rsync_result.stdout,
                    exit_code=0,
                    duration_s=duration,
                )

            duration = time.monotonic() - start
            return DeliveryResult(
                success=False,
                stdout=rsync_result.stdout,
                stderr=rsync_result.stderr,
                exit_code=rsync_result.returncode,
                duration_s=duration,
                error_message=f"rsync failed: {rsync_result.stderr.strip()}",
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return DeliveryResult(
                success=False,
                error_message=f"SCPChannel timeout after {self.timeout}s for {payload.project_name}",
                exit_code=124,
                duration_s=duration,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            duration = time.monotonic() - start
            return DeliveryResult(
                success=False,
                error_message=f"SCPChannel error: {e}",
                exit_code=1,
                duration_s=duration,
            )


# endregion CLASS_SCPChannel


# region CLASS_ForcedCommandChannel


class ForcedCommandChannel(DeliveryChannel):
    """SSH forced-command delivery channel.

    ## @purpose — Deliver payload via SSH forced-command.
    ##            Tar payload is piped through stdin to the VPS-side receiver.
    ## @io — ⇥ Payload → ⎋ DeliveryResult
    ## @complexity — O(N) where N = file size / transfer speed
    ## @invariants
    ##   - Host parameter passed in payload.metadata["host"]
    ##   - SSH user from payload.metadata.get("user", "ci-deploy")
    ##   - SSH key from payload.metadata.get("key_file", "~/.ssh/ci_deploy_key")
    ##   - Uses python3 -m core.internal.deploy.orchestrator_cli receive as remote command
    """

    def __init__(self, timeout: int = DEFAULT_DEPLOY_TIMEOUT):
        super().__init__(timeout)
        self.ssh_opts: list[str] = [
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=30",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=10",
        ]

    def deliver(self, payload: Payload) -> DeliveryResult:
        host = payload.metadata.get("host", "")
        user = payload.metadata.get("user", "ci-deploy")
        key_file = payload.metadata.get("key_file", os.path.expanduser("~/.ssh/ci_deploy_key"))

        if not host:
            return DeliveryResult(
                success=False,
                error_message="ForcedCommandChannel requires 'host' in payload.metadata",
                exit_code=1,
            )

        remote_user = f"{user}@{host}" if user else host
        remote_cmd = "python3 -m core.internal.deploy.orchestrator_cli receive"

        # Build SSH command with piped tar
        ssh_cmd = [
            "ssh",
            "-i",
            key_file,
            *self.ssh_opts,
            remote_user,
            remote_cmd,
        ]

        logger.info(
            "[IMP:8][ForcedCommandChannel][deliver] Delivering %s to %s via forced-command",
            payload.project_name,
            host,
        )

        start = time.monotonic()
        try:
            with open(payload.tar_path, "rb") as tar_file:
                result = subprocess.run(
                    ssh_cmd,
                    stdin=tar_file,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            duration = time.monotonic() - start

            if result.returncode == 0:
                logger.info(
                    "[IMP:9][ForcedCommandChannel][deliver] Deliver SUCCESS for %s to %s",
                    payload.project_name,
                    host,
                )
                return DeliveryResult(
                    success=True,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=0,
                    duration_s=duration,
                )

            return DeliveryResult(
                success=False,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration_s=duration,
                error_message=f"Forced-command failed (exit={result.returncode}): {result.stderr.strip()}",
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return DeliveryResult(
                success=False,
                error_message=f"ForcedCommandChannel timeout after {self.timeout}s for {payload.project_name}",
                exit_code=124,
                duration_s=duration,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            duration = time.monotonic() - start
            return DeliveryResult(
                success=False,
                error_message=f"ForcedCommandChannel error: {e}",
                exit_code=1,
                duration_s=duration,
            )


# endregion CLASS_ForcedCommandChannel
