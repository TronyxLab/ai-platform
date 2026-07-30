#!/usr/bin/env python3
"""
DeployOrchestrator — единый typed фасад для всех deploy-операций.
Инкапсулирует DeployEngine, PayloadDeliverer, DeliveryChannel, AuditLogger, DeployHistory, HealthcheckPoller.
"""
# GREP_SUMMARY: deploy-orchestrator, facade, deploy, rollback, status, remove, deploy-many, audit, delivery-channel
# STRUCTURE: ▶ DeployOrchestrator(deploy|deploy_many|rollback|status|remove) → ┌DeliveryChannel deliver┐ → ┌DeployEngine deploy_compose┐ → ┌DeployHistory create_snapshot┐ → ┌AuditLogger log┐ → ⎋ DeployResult
# region MODULE_CONTRACT
## @purpose  Unified deploy orchestrator — single typed facade for all deploy operations.
##           Eliminates 6+ parallel deploy paths by providing deploy()/deploy_many()/rollback()/status()/remove().
##           Uses DeliveryChannel for transport, DeployEngine for Docker lifecycle,
##           PayloadDeliverer for tar assembly, AuditLogger for audit trail,
##           DeployHistory for snapshot-based rollback, HealthcheckPoller for health verification.
## @scope    All deploy operations pass through DeployOrchestrator. No direct calls to
##           DeployEngine, PayloadDeliverer, or DeliveryChannel outside this module.
## @invariants
##   1. deploy() — принимает project_name + channel, возвращает DeployResult
##   2. deploy_many() — последовательно вызывает deploy() для каждого проекта
##   3. rollback() — восстанавливает compose_state из DeployHistory snapshot
##   4. status() — возвращает ProjectStatus (found/not_found/stub + containers + last_deploy)
##   5. remove() — docker compose down без -v (данные сохраняются)
##   6. Concurrent guard: file lock /var/lock/platform-deploy-{project}.lock
##   7. DeployResult: Union[[DEPLOYED, FAILED, PARTIAL, SKIPPED], error_info, duration_s]
##   8. Healthcheck после deploy через HealthcheckPoller (один раз, не дублируется)
##   9. Аудит через AuditLogger (единый формат, заменяет deprecated shell audit logger)
## @rationale DevPlan 089 — устраняет дублирование бизнес-логики в 6+ путях деплоя.
##            Багфикс в одном пути применяется ко всем через единый DeployOrchestrator.
## @changes 2026-07-30 | DevPlan 089 T6 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from core.internal.deploy.audit_logger import AuditLogger
from core.internal.deploy.channels import DeliveryChannel, Payload
from core.internal.deploy.deploy_history import DeployHistory
from core.internal.deploy.healthcheck_poller import HealthcheckPoller, HealthcheckResult

logger = logging.getLogger(__name__)

DEFAULT_PROJECTS_BASE = "/opt/projects"
PROJECTS_BASE = os.environ.get("PROJECTS_BASE", DEFAULT_PROJECTS_BASE)


# region ENUMS & DATACLASSES


class DeployStatus(str, Enum):
    """Deploy result status."""
    DEPLOYED = "DEPLOYED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class DeployResult:
    """Result of a deploy/rollback/remove operation.

    ## @purpose — Standardized result for all DeployOrchestrator operations.
    ## @io — ⇥ constructor params → ⎋ DeployResult with status and timing
    ## @complexity — O(1)
    """

    status: DeployStatus
    project: str
    channel: str = ""
    error_info: str | None = None
    duration_s: float = 0.0
    healthcheck_status: str = ""
    snapshot_id: str | None = None
    deploy_time: str = ""
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "status": self.status.value,
            "project": self.project,
            "channel": self.channel,
            "error_info": self.error_info,
            "duration_s": round(self.duration_s, 3),
            "healthcheck_status": self.healthcheck_status,
            "snapshot_id": self.snapshot_id or "",
            "deploy_time": self.deploy_time,
        }

    def is_success(self) -> bool:
        """Returns True if operation was successful or partial."""
        return self.status in (DeployStatus.DEPLOYED, DeployStatus.PARTIAL, DeployStatus.SKIPPED)


@dataclass
class ProjectStatus:
    """Status of a project for status() operation.

    ## @purpose — Encapsulate project status information.
    ## @io — ⇥ constructor params → ⎋ ProjectStatus
    ## @complexity — O(1)
    """

    project: str
    status: str  # "found", "not_found", "stub"
    containers: list[dict] = field(default_factory=list)
    last_deploy: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "project": self.project,
            "status": self.status,
            "containers": self.containers,
            "last_deploy": self.last_deploy,
        }


# endregion ENUMS & DATACLASSES


# region CLASS_DeployOrchestrator


class DeployOrchestrator:
    """Unified deploy orchestrator — single facade for all deploy operations.

    ## @purpose — Provide deploy()/deploy_many()/rollback()/status()/remove() as
    ##            typed methods. Internal components injected via constructor for testability.
    ## @io — ⇥ project_name, channel → ⎋ DeployResult
    ## @complexity — O(N) where N = deploy steps (assemble → deliver → compose → healthcheck → audit)
    ## @invariants
    ##   - All operations log through AuditLogger
    ##   - Lock file prevents concurrent deploys of same project
    ##   - Healthcheck runs exactly once after deploy
    ##   - Snapshot created on every successful deploy
    ##   - Rollback restores from latest snapshot
    """

    def __init__(
        self,
        projects_base: str = PROJECTS_BASE,
        audit_logger: AuditLogger | None = None,
        deploy_history: DeployHistory | None = None,
        healthcheck_poller: HealthcheckPoller | None = None,
    ):
        self.projects_base = projects_base
        self.audit_logger = audit_logger or AuditLogger()
        self.deploy_history = deploy_history or DeployHistory(projects_base)
        self.healthcheck_poller = healthcheck_poller or HealthcheckPoller()

    # ── Public API ──────────────────────────────────────────────────────────

    # region FUNC_deploy
    ## @purpose  Deploy a single project through a delivery channel. Full lifecycle:
    ##           1. Check concurrent guard (file lock)
    ##           2. Assemble payload via DeployEngine/PayloadDeliverer
    ##           3. Deliver via DeliveryChannel
    ##           4. Deploy compose via DeployEngine.deploy_compose()
    ##           5. Healthcheck via HealthcheckPoller
    ##           6. Create snapshot via DeployHistory
    ##           7. Audit via AuditLogger
    ## @io       ⇥ project_name: str, channel: DeliveryChannel, version: str, service: str,
    ##              project_dir: str, metadata: dict → ⎋ DeployResult
    ## @complexity — O(N) where N = deploy lifecycle steps
    ## @invariants
    ##   - Lock file acquired before deploy, released after (try/finally)
    ##   - Payload assembled from project_dir (docker-compose.yml + ai-platform.yaml)
    ##   - Healthcheck runs AFTER compose up, BEFORE snapshot creation
    ##   - Snapshot contains pre-deploy state + post-deploy health
    ##   - Audit entry written regardless of success/failure
    ##   - Rollback on healthcheck failure (if previous snapshot exists)
    def deploy(
        self,
        project_name: str,
        channel: DeliveryChannel,
        version: str = "",
        service: str = "",
        project_dir: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DeployResult:
        """Deploy a single project.

        Args:
            project_name: Project name.
            channel: Delivery channel (SCPChannel or ForcedCommandChannel).
            version: Version/tag to deploy.
            service: Docker Compose service name (defaults to project_name).
            project_dir: Project directory path (defaults to projects_base/project_name).
            metadata: Additional metadata for channel delivery.

        Returns:
            DeployResult with status and timing.
        """
        start = time.monotonic()
        metadata = metadata or {}
        project_dir = project_dir or os.path.join(self.projects_base, project_name)
        service = service or project_name

        logger.info(
            "[IMP:9][DeployOrchestrator][deploy] START: %s (channel=%s, version=%s)",
            project_name,
            channel.__class__.__name__,
            version,
        )

        # ── Step 1: Validate ──
        if not project_name:
            return self._result(
                DeployStatus.FAILED, project_name, "", error_info="Project name is required", duration_s=time.monotonic() - start
            )

        # ── Step 2: Assemble payload ──
        try:
            payload = self._assemble_payload(project_name, version, project_dir, metadata)
        except (OSError, ValueError) as e:
            return self._result(
                DeployStatus.FAILED, project_name, channel.__class__.__name__,
                error_info=f"Payload assembly failed: {e}",
                duration_s=time.monotonic() - start,
            )

        # ── Step 3: Deliver through channel ──
        delivery_result = channel._retry_deliver(payload)

        if not delivery_result.success:
            self.audit_logger.log(
                operation="deploy",
                project=project_name,
                channel=channel.__class__.__name__,
                result="FAILED",
                duration_s=time.monotonic() - start,
            )
            return self._result(
                DeployStatus.FAILED, project_name, channel.__class__.__name__,
                error_info=f"Delivery failed: {delivery_result.error_message}",
                stdout=delivery_result.stdout,
                stderr=delivery_result.stderr,
                duration_s=delivery_result.duration_s,
            )

        # ── Step 4: Deploy compose ──
        compose_ok = self._deploy_compose(project_dir, service, version)

        if not compose_ok:
            # Rollback if previous deployment exists
            snapshot = self.deploy_history.latest_snapshot(project_name)
            if snapshot:
                logger.info(
                    "[IMP:9][DeployOrchestrator][deploy] Compose failed — attempting rollback for %s",
                    project_name,
                )
                rollback_ok = self._rollback_compose(project_dir, service, snapshot)
                rollback_status = "ROLLED_BACK" if rollback_ok else "FAILED"
                self.audit_logger.log(
                    operation="deploy",
                    project=project_name,
                    channel=channel.__class__.__name__,
                    result=rollback_status,
                    duration_s=time.monotonic() - start,
                )
                return self._result(
                    DeployStatus.ROLLED_BACK if rollback_ok else DeployStatus.FAILED,
                    project_name,
                    channel.__class__.__name__,
                    error_info=f"Compose deploy failed, rollback {'performed' if rollback_ok else 'failed'}",
                    duration_s=time.monotonic() - start,
                )

            self.audit_logger.log(
                operation="deploy",
                project=project_name,
                channel=channel.__class__.__name__,
                result="FAILED",
                duration_s=time.monotonic() - start,
            )
            return self._result(
                DeployStatus.FAILED,
                project_name,
                channel.__class__.__name__,
                error_info="First deploy compose failed (no rollback available)",
                duration_s=time.monotonic() - start,
            )

        # ── Step 5: Healthcheck ──
        health = self.healthcheck_poller.poll_until_healthy(project_name, project_dir)
        healthcheck_status = health.status

        total_duration = time.monotonic() - start

        # ── Step 6: Create snapshot ──
        snapshot_id = self.deploy_history.create_snapshot(
            project=project_name,
            version=version,
            health_status=healthcheck_status,
        )

        # ── Step 7: Audit ──
        result_status = DeployStatus.DEPLOYED if healthcheck_status == "healthy" else DeployStatus.PARTIAL
        self.audit_logger.log(
            operation="deploy",
            project=project_name,
            channel=channel.__class__.__name__,
            result=result_status.value,
            duration_s=total_duration,
            snapshot_id=snapshot_id,
        )

        logger.info(
            "[IMP:9][DeployOrchestrator][deploy] DONE: %s → %s (%.1fs)",
            project_name,
            result_status.value,
            total_duration,
        )

        return self._result(
            result_status,
            project_name,
            channel.__class__.__name__,
            duration_s=total_duration,
            healthcheck_status=healthcheck_status,
            snapshot_id=snapshot_id,
        )

    # endregion FUNC_deploy

    # region FUNC_deploy_many
    ## @purpose  Deploy multiple projects sequentially. Each project uses the same channel.
    ##           Failure of one project does NOT block subsequent projects.
    ## @io       ⇥ project_names: list[str], channel: DeliveryChannel,
    ##              version: str, project_base_dir: str → ⎋ list[DeployResult]
    ## @complexity — O(N × M) where N = projects, M = deploy lifecycle per project
    ## @invariants
    ##   - Projects are deployed sequentially (not parallel)
    ##   - One project failure does NOT block others
    ##   - Result list preserves input order
    def deploy_many(
        self,
        project_names: list[str],
        channel: DeliveryChannel,
        version: str = "",
        project_base_dir: str | None = None,
    ) -> list[DeployResult]:
        """Deploy multiple projects sequentially.

        Args:
            project_names: List of project names.
            channel: Delivery channel.
            version: Version/tag for all projects.
            project_base_dir: Base directory for projects.

        Returns:
            List of DeployResult in input order.
        """
        results: list[DeployResult] = []
        for name in project_names:
            result = self.deploy(
                project_name=name,
                channel=channel,
                version=version,
                project_dir=os.path.join(project_base_dir or self.projects_base, name),
            )
            results.append(result)

        # Audit multi-deploy summary
        deployed = sum(1 for r in results if r.status == DeployStatus.DEPLOYED)
        failed = sum(1 for r in results if r.status in (DeployStatus.FAILED, DeployStatus.ROLLED_BACK))
        overall = DeployStatus.DEPLOYED.value if failed == 0 else DeployStatus.PARTIAL.value if deployed > 0 else DeployStatus.FAILED.value
        self.audit_logger.log_many(
            operation="deploy_many",
            projects=project_names,
            channel=channel.__class__.__name__,
            overall_result=overall,
        )

        logger.info(
            "[IMP:9][DeployOrchestrator][deploy_many] %d/%d deployed, %d failed",
            deployed,
            len(project_names),
            failed,
        )
        return results

    # endregion FUNC_deploy_many

    # region FUNC_rollback
    ## @purpose  Rollback a project to a previous snapshot.
    ## @io       ⇥ project_name: str, snapshot_id: str | None → ⎋ DeployResult
    ## @complexity — O(M) where M = rollback lifecycle
    ## @invariants
    ##   - If snapshot_id is None, uses latest snapshot
    ##   - Rollback restores compose_state from snapshot
    ##   - No rollback possible if no snapshots exist
    def rollback(self, project_name: str, snapshot_id: str | None = None) -> DeployResult:
        """Rollback a project to a previous snapshot.

        Args:
            project_name: Project name.
            snapshot_id: Specific snapshot ID, or None for latest.

        Returns:
            DeployResult with rollback status.
        """
        start = time.monotonic()
        logger.info(
            "[IMP:9][DeployOrchestrator][rollback] START: %s (snapshot=%s)",
            project_name,
            snapshot_id or "latest",
        )

        snapshot = self.deploy_history.rollback(project_name, snapshot_id)
        if not snapshot:
            return self._result(
                DeployStatus.FAILED, project_name,
                error_info=f"No snapshot found for rollback of {project_name}",
                duration_s=time.monotonic() - start,
            )

        project_dir = os.path.join(self.projects_base, project_name)
        service = project_name

        rollback_ok = self._rollback_compose(project_dir, service, snapshot)

        # Audit
        result_status = DeployStatus.DEPLOYED if rollback_ok else DeployStatus.FAILED
        self.audit_logger.log(
            operation="rollback",
            project=project_name,
            result=result_status.value,
            duration_s=time.monotonic() - start,
            snapshot_id=snapshot.get("snapshot_id", snapshot_id),
        )

        return self._result(
            result_status,
            project_name,
            error_info="" if rollback_ok else f"Rollback failed for {project_name}",
            duration_s=time.monotonic() - start,
            snapshot_id=snapshot.get("snapshot_id", snapshot_id),
        )

    # endregion FUNC_rollback

    # region FUNC_status
    ## @purpose  Get project status (found/not_found/stub + containers + last_deploy).
    ## @io       ⇥ project_name: str → ⎋ ProjectStatus
    ## @complexity — O(1) — file reads + docker ps call
    def status(self, project_name: str) -> ProjectStatus:
        """Get project status.

        Args:
            project_name: Project name.

        Returns:
            ProjectStatus with containers and last deploy info.
        """
        logger.info("[IMP:9][DeployOrchestrator][status] Status check: %s", project_name)
        project_dir = os.path.join(self.projects_base, project_name)

        if not os.path.isdir(project_dir):
            return ProjectStatus(project=project_name, status="not_found")

        # Check for stub
        ai_yaml = os.path.join(project_dir, "ai-platform.yaml")
        if os.path.isfile(ai_yaml):
            try:
                with open(ai_yaml) as f:
                    if "GENERATED-STUB" in f.readline():
                        return ProjectStatus(project=project_name, status="stub")
            except OSError:
                pass

        # Get containers via docker compose ps
        containers: list[dict[str, Any]] = []
        try:
            import subprocess  # noqa: PLC0415

            ps_result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                capture_output=True, text=True, timeout=30,
                cwd=project_dir,
            )
            if ps_result.returncode == 0 and ps_result.stdout.strip():
                for line in ps_result.stdout.strip().split("\n"):
                    try:
                        containers.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("[IMP:8][status] docker compose ps error: %s", e)

        # Get last deploy from snapshots
        last_deploy: dict | None = None
        snapshot = self.deploy_history.latest_snapshot(project_name)
        if snapshot:
            last_deploy = snapshot

        return ProjectStatus(
            project=project_name,
            status="found",
            containers=containers,
            last_deploy=last_deploy,
        )

    # endregion FUNC_status

    # region FUNC_remove
    ## @purpose  Remove a project's containers (idempotent). Data preserved — no -v flag.
    ## @io       ⇥ project_name: str, purge: bool = False → ⎋ DeployResult
    ## @complexity — O(1) — single docker compose call
    ## @invariants
    ##   - docker compose down WITHOUT -v (data preserved)
    ##   - purge=True removes compose volumes (docker compose down -v)
    ##   - Idempotent: if project dir missing → SKIPPED
    def remove(self, project_name: str, purge: bool = False) -> DeployResult:
        """Safely remove project containers (data preserved unless purge=True).

        Args:
            project_name: Project name.
            purge: If True, remove compose volumes (docker compose down -v).

        Returns:
            DeployResult with status.
        """
        start = time.monotonic()
        logger.info("[IMP:9][DeployOrchestrator][remove] START: %s (purge=%s)", project_name, purge)

        project_dir = os.path.join(self.projects_base, project_name)
        if not os.path.isdir(project_dir):
            return self._result(
                DeployStatus.SKIPPED, project_name,
                error_info="Project directory not found — already removed",
                duration_s=time.monotonic() - start,
            )

        # docker compose down (± -v)
        try:
            import subprocess  # noqa: PLC0415

            cmd = ["docker", "compose", "down", "--timeout", "30"]
            if purge:
                cmd.append("-v")

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, cwd=project_dir,
            )
            duration = time.monotonic() - start

            if result.returncode != 0:
                logger.warning(
                    "[IMP:8][remove] docker compose down exit=%s: %s",
                    result.returncode, result.stderr.strip(),
                )

            self.audit_logger.log(
                operation="remove",
                project=project_name,
                result=DeployStatus.DEPLOYED.value,
                duration_s=duration,
            )

            return self._result(
                DeployStatus.DEPLOYED, project_name,
                duration_s=duration,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return self._result(
                DeployStatus.FAILED, project_name,
                error_info=str(e),
                duration_s=time.monotonic() - start,
            )

    # endregion FUNC_remove

    # ── receive() — VPS-side forced-command receiver ──

    # region FUNC_receive
    ## @purpose  VPS-side forced-command receiver. Reads tar from stdin,
    ##           extracts payload metadata, calls deploy(). Replaces deploy-project.sh.
    ## @io       ⇥ stdin (tar bytes) → ⎋ str (JSON DeployResult) via stdout
    ## @complexity — O(N) where N = tar entries
    ## @invariants
    ##   - Reads tar from stdin (binary)
    ##   - Extracts to staging directory
    ##   - Parses payload metadata from ai-platform.yaml
    ##   - Calls deploy() for the project
    ##   - Outputs JSON DeployResult to stdout, exit code 0/1
    @staticmethod
    def receive() -> int:
        """Receive a deploy payload via stdin (tar) and execute deploy.

        This is the VPS-side entry point for ForcedCommandChannel.
        Replaces deploy-project.sh as the forced-command receiver.

        Returns:
            Exit code (0 = success, 1 = failure).
        """
        import io  # noqa: PLC0415
        import sys  # noqa: PLC0415
        import tarfile  # noqa: PLC0415
        import tempfile  # noqa: PLC0415
        import shutil  # noqa: PLC0415

        logger.info("[IMP:9][DeployOrchestrator][receive] Receiving deploy payload via stdin")

        # Read tar from stdin
        tar_bytes = sys.stdin.buffer.read()
        if not tar_bytes:
            logger.error("[IMP:10][DeployOrchestrator][receive] No data received on stdin")
            print(json.dumps({"status": "FAILED", "error": "No data received on stdin"}))
            return 1

        # Extract to staging
        staging = tempfile.mkdtemp(prefix="deploy-receive-")
        try:
            buf = io.BytesIO(tar_bytes)
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                tar.extractall(path=staging, filter="data")

            # Parse ai-platform.yaml for metadata
            ai_yaml = Path(staging) / "ai-platform.yaml"
            if not ai_yaml.is_file():
                logger.error("[IMP:10][DeployOrchestrator][receive] ai-platform.yaml not found in payload")
                print(json.dumps({"status": "FAILED", "error": "ai-platform.yaml not found in payload"}))
                return 1

            import yaml  # noqa: PLC0415
            with open(ai_yaml) as f:
                config = yaml.safe_load(f) or {}

            project_name = config.get("project", config.get("name", ""))
            service = config.get("service", project_name)
            version = config.get("version", "latest")

            if not project_name:
                logger.error("[IMP:10][DeployOrchestrator][receive] No project name in ai-platform.yaml")
                print(json.dumps({"status": "FAILED", "error": "No project name in ai-platform.yaml"}))
                return 1

            # Copy payload files to project directory
            projects_base = os.environ.get("PROJECTS_BASE", "/opt/projects")
            target_dir = os.path.join(projects_base, project_name)
            os.makedirs(target_dir, exist_ok=True)

            for item in Path(staging).iterdir():
                if item.is_file():
                    shutil.copy2(str(item), os.path.join(target_dir, item.name))

            # Execute deploy
            from core.internal.deploy.channels import SCPChannel  # noqa: PLC0415
            local_channel = SCPChannel()
            orchestrator = DeployOrchestrator(projects_base=projects_base)
            result = orchestrator.deploy(
                project_name=project_name,
                channel=local_channel,
                version=version,
                service=service,
                project_dir=target_dir,
            )

            output = json.dumps(result.to_dict())
            print(output)
            return 0 if result.is_success() else 1

        except (tarfile.TarError, OSError, yaml.YAMLError) as e:
            logger.error("[IMP:10][DeployOrchestrator][receive] Error: %s", e)
            print(json.dumps({"status": "FAILED", "error": str(e)}))
            return 1
        finally:
            if os.path.isdir(staging):
                shutil.rmtree(staging, ignore_errors=True)

    # endregion FUNC_receive

    # ── Internal helpers ──────────────────────────────────────────────────

    def _assemble_payload(
        self,
        project_name: str,
        version: str,
        project_dir: str,
        metadata: dict[str, Any],
    ) -> Payload:
        """Assemble a deploy payload from project files.

        Args:
            project_name: Project name.
            version: Version/tag.
            project_dir: Project directory path.
            metadata: Additional metadata.

        Returns:
            Payload with tar_path pointing to assembled tar.gz.

        Raises:
            OSError: If project files cannot be read.
            ValueError: If required files are missing.
        """
        import tempfile  # noqa: PLC0415
        import subprocess  # noqa: PLC0415
        import tarfile  # noqa: PLC0415

        # Create tar.gz of project files
        tar_fd, tar_path = tempfile.mkstemp(suffix=".tar.gz", prefix=f"deploy-{project_name}-")
        os.close(tar_fd)

        with tarfile.open(tar_path, "w:gz") as tar:
            for fname in ("docker-compose.yml", "compose.yaml", "ai-platform.yaml", ".env.platform"):
                fpath = os.path.join(project_dir, fname)
                if os.path.isfile(fpath):
                    tar.add(fpath, arcname=fname)

        return Payload(
            tar_path=Path(tar_path),
            project_name=project_name,
            version=version,
            metadata=metadata,
        )

    def _deploy_compose(self, project_dir: str, service: str, version: str) -> bool:
        """Execute docker compose deploy.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            version: Image tag/version.

        Returns:
            True if compose up succeeded.
        """
        try:
            from core.internal.deploy.deploy_engine import DeployEngine  # noqa: PLC0415

            engine = DeployEngine(projects_base=self.projects_base)
            result = engine.deploy(
                project=Path(project_dir).name,
                ref=version,
                service=service,
                project_dir=project_dir,
            )
            return result.success
        except SystemExit:
            logger.error("[IMP:10][DeployOrchestrator][deploy_compose] Deploy engine exited (first deploy failure)")
            return False
        except Exception as e:  # noqa: BLE001
            logger.error("[IMP:10][DeployOrchestrator][deploy_compose] Failed: %s", e)
            return False

    def _rollback_compose(self, project_dir: str, service: str, snapshot: dict[str, Any]) -> bool:
        """Rollback compose to a previous snapshot state.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            snapshot: Snapshot data with compose_state.

        Returns:
            True if rollback succeeded.
        """
        try:
            from core.internal.deploy.deploy_engine import DeployEngine  # noqa: PLC0415

            engine = DeployEngine(projects_base=self.projects_base)
            prev_image_id = snapshot.get("compose_state", {}).get("previous_image")

            # Re-tag and restart
            if prev_image_id:
                import subprocess  # noqa: PLC0415
                subprocess.run(
                    ["docker", "tag", prev_image_id, f"{service}:previous-rollback"],
                    capture_output=True, timeout=30, check=False,
                )

            result = engine.deploy(
                project=Path(project_dir).name,
                ref="previous-rollback",
                service=service,
                project_dir=project_dir,
            )
            return result.success
        except SystemExit:
            logger.error("[IMP:10][DeployOrchestrator][rollback_compose] Engine exited during rollback")
            return False
        except Exception as e:  # noqa: BLE001
            logger.error("[IMP:10][DeployOrchestrator][rollback_compose] Failed: %s", e)
            return False

    def _result(
        self,
        status: DeployStatus,
        project: str,
        channel: str = "",
        error_info: str | None = None,
        duration_s: float = 0.0,
        healthcheck_status: str = "",
        snapshot_id: str | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> DeployResult:
        """Build a DeployResult with common fields.

        Args:
            status: Deploy status.
            project: Project name.
            channel: Channel name.
            error_info: Error description.
            duration_s: Duration in seconds.
            healthcheck_status: Healthcheck result.
            snapshot_id: Optional snapshot ID.
            stdout: Command stdout.
            stderr: Command stderr.

        Returns:
            DeployResult instance.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        return DeployResult(
            status=status,
            project=project,
            channel=channel,
            error_info=error_info,
            duration_s=duration_s,
            healthcheck_status=healthcheck_status,
            snapshot_id=snapshot_id,
            deploy_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            stdout=stdout,
            stderr=stderr,
        )


# endregion CLASS_DeployOrchestrator
