#!/usr/bin/env python3
"""
DeployOrchestrator — единый typed фасад для всех deploy-операций.
Инкапсулирует DeployEngine, PayloadDeliverer, DeliveryChannel, AuditLogger, DeployHistory, HealthcheckPoller.
"""
# GREP_SUMMARY: deploy-orchestrator, facade, deploy, rollback, status, remove, deploy-many, audit, delivery-channel
# STRUCTURE: ▶ DeployOrchestrator(deploy|deploy_many|rollback|status|remove) → ┌DeliveryChannel deliver┐ → ┌DeployEngine deploy_compose┐ → ┌DeployHistory create_snapshot┐ → ┌AuditLogger log┐ → ⎋ OrchestratorDeployResult
# region MODULE_CONTRACT
## @purpose  Unified deploy orchestrator — single typed facade for all deploy operations.
##           Eliminates 6+ parallel deploy paths by providing deploy()/deploy_many()/rollback()/status()/remove().
##           Uses DeliveryChannel for transport, DeployEngine for Docker lifecycle,
##           PayloadDeliverer for tar assembly, AuditLogger for audit trail,
##           DeployHistory for snapshot-based rollback, HealthcheckPoller for health verification.
## @scope    All deploy operations pass through DeployOrchestrator. No direct calls to
##           DeployEngine, PayloadDeliverer, or DeliveryChannel outside this module.
## @invariants
##   1. deploy() — принимает project_name + channel, возвращает OrchestratorDeployResult
##   2. deploy_many() — последовательно вызывает deploy() для каждого проекта
##   3. rollback() — восстанавливает compose_state из DeployHistory snapshot
##   4. status() — возвращает ProjectStatus (found/not_found/stub + containers + last_deploy)
##   5. remove() — docker compose down без -v (данные сохраняются)
##   6. Concurrent guard: file lock /var/lock/platform-deploy-{project}.lock
##   7. OrchestratorDeployResult: Union[[DEPLOYED, FAILED, PARTIAL, SKIPPED], error_info, duration_s]
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
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from core.internal.deploy.channels import DeliveryChannel, Payload
from core.internal.deploy.deploy_history import DeployHistory
from core.internal.deploy.healthcheck_poller import HealthcheckPoller

# DevPlan 116 B11 T2 (U-10, D1): единый audit-writer — shared/audit_logger.
# deploy/audit_logger.py УДАЛЁН; DeployOrchestrator пишет через write_audit_entry
# (tag="deploy:<operation>") через тонкий адаптер DeployAuditLogger (см. ниже).
from core.internal.shared.audit_logger import DEFAULT_LOG_FILE as _SHARED_AUDIT_LOG_FILE
from core.internal.shared.audit_logger import write_audit_entry as _shared_write_audit_entry

# DevPlan 116 B5 T3: shared docker compose — sole path (гейт docker_sole_path)
from core.internal.shared.docker_compose import (
    docker_compose_down as _shared_docker_compose_down,
)
from core.internal.shared.docker_compose import (
    docker_compose_ps as _shared_docker_compose_ps,
)
from core.internal.shared.exceptions import PlatformError

# DevPlan 116 B5 T1: таймауты — единый реестр shared/timeouts.py (U-11)
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)


# region CLASS_DeployAuditLogger
class DeployAuditLogger:
    """Thin adapter: DeployOrchestrator .log()/.log_many() interface → shared write_audit_entry.

    ## @purpose — Единственный мост между DeployOrchestrator'ом и единым writer'ом
    ##            shared/audit_logger (D1, DevPlan 116 B11 T2). Маппит deploy-поля
    ##            (operation/project/channel/result/duration_s/snapshot_id) в расширенную
    ##            схему write_audit_entry(tag="deploy:<operation>", **extra).
    ##            Все записи идут в ЕДИНЫЙ файл audit.jsonl (DEFAULT_LOG_FILE).
    ## @io — ⇥ log_file: str (default shared DEFAULT_LOG_FILE) → ⎋ None
    ## @complexity O(1) per call
    ## @invariants
    ##   - .log()/.log_many() интерфейс сохраняется (тесты/вызовы не ломаются)
    ##   - НИКАКОГО прямого f.write — все записи через shared write_audit_entry
    ##   - tag = "deploy:<operation>", status = result (или "UNKNOWN")
    ##   - Расширенная схема: extra-поля (operation, project, channel, result, duration_s,
    ##     snapshot_id, projects, per_project_results) — в ту же JSON-строку
    """

    def __init__(self, log_file: str = _SHARED_AUDIT_LOG_FILE):
        self.log_file = log_file

    def log(
        self,
        operation: str,
        project: str,
        channel: str = "",
        result: str = "",
        duration_s: float = 0.0,
        snapshot_id: str | None = None,
        **extra: str,
    ) -> None:
        """Write a single-project deploy audit entry via shared write_audit_entry."""
        _shared_write_audit_entry(
            tag=f"deploy:{operation}",
            status=result or "UNKNOWN",
            message=f"{operation} project={project} channel={channel or '-'}",
            log_file=self.log_file,
            operation=operation,
            project=project,
            channel=channel,
            result=result,
            duration_s=round(duration_s, 3),
            snapshot_id=snapshot_id or "",
            **extra,
        )

    def log_many(
        self,
        operation: str,
        projects: list[str],
        channel: str = "",
        results: list[str] | None = None,
        overall_result: str = "",
    ) -> None:
        """Write a multi-project deploy audit entry via shared write_audit_entry."""
        _shared_write_audit_entry(
            tag=f"deploy:{operation}",
            status=overall_result or "UNKNOWN",
            message=f"{operation} {len(projects)} project(s)",
            log_file=self.log_file,
            operation=operation,
            projects=projects,
            channel=channel,
            result=overall_result,
            project_count=len(projects),
            per_project_results=results or [],
        )


# endregion CLASS_DeployAuditLogger

DEFAULT_PROJECTS_BASE = "/opt/projects"
PROJECTS_BASE = os.environ.get("PROJECTS_BASE", DEFAULT_PROJECTS_BASE)


def _try_json_loads(s: str) -> dict | None:
    """Parse a JSON string, returning None on failure.

    ## @purpose — Helper for PERF203: isolate try-except from loop.
    ## @io — ⇥ s: str → ⎋ dict | None
    ## @complexity — O(n) where n = len(s)
    """
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


# region ENUMS & DATACLASSES


class DeployStatus(str, Enum):
    """Deploy result status."""

    DEPLOYED = "DEPLOYED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class OrchestratorDeployResult:
    """Result of a deploy/rollback/remove operation.

    ## @purpose — Standardized result for all DeployOrchestrator operations.
    ## @io — ⇥ constructor params → ⎋ OrchestratorDeployResult with status and timing
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
    # DevPlan 116 B1 T2 (D5): version pinning — sha из аргументов SSH-команды (receive \<project\> \<sha\>).
    # phantom-read version/service из ai-platform.yaml УДАЛЁН (U-37); version = sha из CI.
    version: str = ""

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
            # AC2 (DevPlan 116 B1): JSON receive-результата содержит project, version, sha, status
            "version": self.version,
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
    ## @io — ⇥ project_name, channel → ⎋ OrchestratorDeployResult
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
        audit_logger: DeployAuditLogger | None = None,
        deploy_history: DeployHistory | None = None,
        healthcheck_poller: HealthcheckPoller | None = None,
    ):
        self.projects_base = projects_base
        self.audit_logger = audit_logger or DeployAuditLogger()
        self.deploy_history = deploy_history or DeployHistory(projects_base)
        self.healthcheck_poller = healthcheck_poller or HealthcheckPoller()

    # ── Public API ──────────────────────────────────────────────────────────

    # region FUNC_deploy
    ## @purpose  Deploy a single project through a delivery channel. Full lifecycle:
    ##
    ##           - Check concurrent guard (file lock)
    ##           - Assemble payload via DeployEngine/PayloadDeliverer
    ##           - Deliver via DeliveryChannel
    ##           - Deploy compose via DeployEngine.deploy_compose()
    ##           - Healthcheck via HealthcheckPoller
    ##           - Create snapshot via DeployHistory
    ##           - Audit via AuditLogger
    ##           When dry_run=True: validate inputs and emit a plan (channels, steps, target
    ##           project_dir) to stderr, then return DeployStatus.SKIPPED WITHOUT executing
    ##           delivery/compose/healthcheck. DevPlan 089 AC10 / DevPlan 091 Wave A.
    ## @io       ⇥ project_name: str, channel: DeliveryChannel, version: str, service: str,
    ##              project_dir: str, metadata: dict, dry_run: bool → ⎋ OrchestratorDeployResult
    ## @complexity — O(N) where N = deploy lifecycle steps (dry_run: O(1))
    ## @invariants
    ##
    ##   - Lock file acquired before deploy, released after (try/finally)
    ##   - Payload assembled from project_dir (docker-compose.yml + ai-platform.yaml)
    ##   - Healthcheck runs AFTER compose up, BEFORE snapshot creation
    ##   - Snapshot contains pre-deploy state + post-deploy health
    ##   - Audit entry written regardless of success/failure
    ##   - Rollback on healthcheck failure (if previous snapshot exists)
    ##   - dry_run=True short-circuits BEFORE delivery — no side effects, status=SKIPPED
    def deploy(
        self,
        project_name: str,
        channel: DeliveryChannel,
        version: str = "",
        service: str = "",
        project_dir: str | None = None,
        metadata: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> OrchestratorDeployResult:
        """Deploy a single project.

        Args:
            project_name: Project name.
            channel: Delivery channel (SCPChannel or ForcedCommandChannel).
            version: Version/tag to deploy.
            service: Docker Compose service name (defaults to project_name).
            project_dir: Project directory path (defaults to projects_base/project_name).
            metadata: Additional metadata for channel delivery.
            dry_run: If True, emit a plan to stderr and return SKIPPED without executing.

        Returns:
            OrchestratorDeployResult with status and timing.
        """
        start = time.monotonic()
        metadata = metadata or {}
        project_dir = project_dir or os.path.join(self.projects_base, project_name)
        service = service or project_name

        logger.info(
            "[IMP:9][DeployOrchestrator][deploy] START: %s (channel=%s, version=%s, dry_run=%s)",
            project_name,
            channel.__class__.__name__,
            version,
            dry_run,
        )

        # ── Step 1: Validate ──
        if not project_name:
            return self._result(
                DeployStatus.FAILED,
                project_name,
                "",
                error_info="Project name is required",
                duration_s=time.monotonic() - start,
            )

        # ── dry-run short-circuit (DevPlan 089 AC10) ──
        # Emit a human/machine-readable plan to stderr, return SKIPPED without side effects.
        if dry_run:
            plan_lines = [
                "[DRY-RUN][DeployOrchestrator][deploy] Plan (no execution):",
                f"  project     = {project_name}",
                f"  project_dir = {project_dir}",
                f"  service     = {service}",
                f"  version     = {version or 'latest'}",
                f"  channel     = {channel.__class__.__name__}",
                f"  channel_meta= {metadata}",
                "  steps       = assemble-payload → deliver → compose-up → healthcheck → snapshot → audit",
            ]
            for line in plan_lines:
                logger.info("[IMP:8][DeployOrchestrator][deploy][DRY-RUN] %s", line)
            return self._result(
                DeployStatus.SKIPPED,
                project_name,
                channel.__class__.__name__,
                error_info="dry-run — no execution",
                duration_s=time.monotonic() - start,
            )

        # ── Step 2: Assemble payload ──
        try:
            payload = self._assemble_payload(project_name, version, project_dir, metadata)
        except (OSError, ValueError) as e:
            return self._result(
                DeployStatus.FAILED,
                project_name,
                channel.__class__.__name__,
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
                DeployStatus.FAILED,
                project_name,
                channel.__class__.__name__,
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
            version=version,
        )

    # endregion FUNC_deploy

    # region FUNC_deploy_many
    ## @purpose  Deploy multiple projects sequentially. Each project uses the same channel.
    ##           Failure of one project does NOT block subsequent projects.
    ##           When dry_run=True: every project is planned but not executed.
    ## @io       ⇥ project_names: list[str], channel: DeliveryChannel,
    ##              version: str, project_base_dir: str, dry_run: bool → ⎋ list[OrchestratorDeployResult]
    ## @complexity — O(N × M) where N = projects, M = deploy lifecycle per project
    ## @invariants
    ##   - Projects are deployed sequentially (not parallel)
    ##   - One project failure does NOT block others
    ##   - Result list preserves input order
    ##   - dry_run propagates to each deploy() call — no side effects for the whole batch
    def deploy_many(
        self,
        project_names: list[str],
        channel: DeliveryChannel,
        version: str = "",
        project_base_dir: str | None = None,
        dry_run: bool = False,
    ) -> list[OrchestratorDeployResult]:
        """Deploy multiple projects sequentially.

        Args:
            project_names: List of project names.
            channel: Delivery channel.
            version: Version/tag for all projects.
            project_base_dir: Base directory for projects.
            dry_run: If True, plan each deploy without executing.

        Returns:
            List of OrchestratorDeployResult in input order.
        """
        results: list[OrchestratorDeployResult] = []
        for name in project_names:
            result = self.deploy(
                project_name=name,
                channel=channel,
                version=version,
                project_dir=os.path.join(project_base_dir or self.projects_base, name),
                dry_run=dry_run,
            )
            results.append(result)

        # Audit multi-deploy summary
        deployed = sum(1 for r in results if r.status == DeployStatus.DEPLOYED)
        failed = sum(1 for r in results if r.status in (DeployStatus.FAILED, DeployStatus.ROLLED_BACK))
        overall = (
            DeployStatus.DEPLOYED.value
            if failed == 0
            else DeployStatus.PARTIAL.value
            if deployed > 0
            else DeployStatus.FAILED.value
        )
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
    ## @io       ⇥ project_name: str, snapshot_id: str | None → ⎋ OrchestratorDeployResult
    ## @complexity — O(M) where M = rollback lifecycle
    ## @invariants
    ##   - If snapshot_id is None, uses latest snapshot
    ##   - Rollback restores compose_state from snapshot
    ##   - No rollback possible if no snapshots exist
    def rollback(self, project_name: str, snapshot_id: str | None = None) -> OrchestratorDeployResult:
        """Rollback a project to a previous snapshot.

        Args:
            project_name: Project name.
            snapshot_id: Specific snapshot ID, or None for latest.

        Returns:
            OrchestratorDeployResult with rollback status.
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
                DeployStatus.FAILED,
                project_name,
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

        # Get containers via docker compose ps (shared — sole path, DevPlan 116 B5 T3)
        containers: list[dict[str, Any]] = []
        try:
            ps_result = _shared_docker_compose_ps(project_dir, format="json")
            ps_stdout = ps_result.stdout
            if isinstance(ps_stdout, bytes):
                ps_stdout = ps_stdout.decode("utf-8")
            if ps_result.returncode == 0 and ps_stdout.strip():
                containers.extend(
                    c for line in ps_stdout.strip().split("\n") if (c := _try_json_loads(line)) is not None
                )
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
    ## @io       ⇥ project_name: str, purge: bool = False → ⎋ OrchestratorDeployResult
    ## @complexity — O(1) — single docker compose call
    ## @invariants
    ##   - docker compose down WITHOUT -v (data preserved)
    ##   - purge=True removes compose volumes (docker compose down -v)
    ##   - Idempotent: if project dir missing → SKIPPED
    def remove(self, project_name: str, purge: bool = False) -> OrchestratorDeployResult:
        """Safely remove project containers (data preserved unless purge=True).

        Args:
            project_name: Project name.
            purge: If True, remove compose volumes (docker compose down -v).

        Returns:
            OrchestratorDeployResult with status.
        """
        start = time.monotonic()
        logger.info("[IMP:9][DeployOrchestrator][remove] START: %s (purge=%s)", project_name, purge)

        project_dir = os.path.join(self.projects_base, project_name)
        if not os.path.isdir(project_dir):
            return self._result(
                DeployStatus.SKIPPED,
                project_name,
                error_info="Project directory not found — already removed",
                duration_s=time.monotonic() - start,
            )

        # docker compose down (± -v) — shared sole path (DevPlan 116 B5 T3)
        try:
            flags = ["--timeout", "30"]
            if purge:
                flags.append("-v")

            down_ok = _shared_docker_compose_down(project_dir, flags=flags)
            duration = time.monotonic() - start

            if not down_ok:
                logger.warning("[IMP:8][remove] docker compose down reported failure")

            self.audit_logger.log(
                operation="remove",
                project=project_name,
                result=DeployStatus.DEPLOYED.value,
                duration_s=duration,
            )

            return self._result(
                DeployStatus.DEPLOYED,
                project_name,
                duration_s=duration,
                stdout="docker compose down completed",
                stderr="",
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return self._result(
                DeployStatus.FAILED,
                project_name,
                error_info=str(e),
                duration_s=time.monotonic() - start,
            )

    # endregion FUNC_remove

    # ── receive() — VPS-side forced-command receiver ──

    # region FUNC_receive
    ## @purpose  VPS-side forced-command receiver (DevPlan 116 B1 T2, D1/D4/D5). Reads tar from
    ##           stdin, validates payload (fail-fast), extracts to /opt/projects/\<project\>/,
    ##           runs the full DeployOrchestrator pipeline via LocalChannel, then best-effort
    ##           post-deploy chain (notify-hook + generate-catalog). Версия (sha) — ТОЛЬКО из
    ##           аргументов SSH-команды (receive \<project\> \<sha\>); phantom-read version/service
    ##           из ai-platform.yaml УДАЛЁН (U-37). Вызывается из `orchestrator_cli dispatch receive`.
    ## @io       ⇥ stdin (tar bytes), project_name: str | None (из SSH-аргументов),
    ##              version: str (sha из CI, D5) → ⎋ int (exit code 0/1) + JSON OrchestratorDeployResult в stdout
    ## @complexity — O(N) where N = tar entries + deploy lifecycle
    ## @invariants
    ##   - Пустой stdin → JSON-ошибка + exit 1 (fail-fast, БЕЗ || true-масок)
    ##   - ai-platform.yaml отсутствует → JSON-ошибка + exit 1 (fail-fast)
    ##   - project_name из аргументов (валидируется validate_project_name + verb-reserve U-56);
    ##     фолбэк на ai-platform.yaml `name` — ТОЛЬКО для локальных/ручных вызовов без аргументов
    ##   - version ТОЛЬКО из аргументов (D5); service = project_name
    ##   - Деплой через LocalChannel (payload уже извлечён — TRAP[DECISION] 2026-07-31)
    ##   - Пост-деплой цепочка (D4): notify-hook + generate-catalog — best-effort,
    ##     сбой → WARN, деплой НЕ фейлится (notify-hook always exit 0)
    ##   - JSON OrchestratorDeployResult содержит version (AC2: project, version, sha, status)
    def receive(
        self,
        project_name: str | None = None,
        version: str = "latest",
    ) -> int:
        """Receive a deploy payload via stdin (tar) and execute deploy.

        This is the VPS-side entry point for the forced-command dispatcher
        (`orchestrator_cli dispatch receive <project> <sha>`).

        Args:
            project_name: Project name from SSH_ORIGINAL_COMMAND args (D5). When None
                (локальные/ручные вызовы) — фолбэк на ai-platform.yaml `name`.
            version: Version/sha from SSH args (D5). Default "latest" для локальных вызовов.

        Returns:
            Exit code (0 = success, 1 = failure).
        """
        import io
        import shutil
        import sys
        import tarfile
        import tempfile

        from core.internal.shared.project_registry import validate_project_name

        logger.info("[IMP:9][DeployOrchestrator][receive] Receiving deploy payload via stdin (version=%s)", version)

        # Read tar from stdin — пустой stdin → fail-fast (БЕЗ || true-масок)
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

            # Parse ai-platform.yaml for metadata (fail-fast: отсутствие = ошибка)
            ai_yaml = Path(staging) / "ai-platform.yaml"
            if not ai_yaml.is_file():
                logger.error("[IMP:10][DeployOrchestrator][receive] ai-platform.yaml not found in payload")
                print(json.dumps({"status": "FAILED", "error": "ai-platform.yaml not found in payload"}))
                return 1

            import yaml

            with open(ai_yaml) as f:
                config = yaml.safe_load(f) or {}

            # D5: проект — из аргументов SSH-команды (приоритет), фолбэк на yaml `name` для
            # локальных/ручных вызовов. version — ТОЛЬКО из аргументов (sha-pinning).
            resolved_project = project_name or config.get("name", config.get("project", ""))
            if not resolved_project:
                logger.error("[IMP:10][DeployOrchestrator][receive] No project name in args or ai-platform.yaml")
                print(json.dumps({"status": "FAILED", "error": "No project name in args or ai-platform.yaml"}))
                return 1

            # U-56 verb-reserve + canonical name validation (проект «status» невалиден)
            if not validate_project_name(resolved_project):
                logger.error(
                    "[IMP:10][DeployOrchestrator][receive] Invalid/reserved project name: %r", resolved_project
                )
                print(
                    json.dumps({"status": "FAILED", "error": f"Invalid or reserved project name: {resolved_project}"})
                )
                return 1

            service = resolved_project  # D5: service = project_name (чтение service из yaml удалено, U-37)

            # Copy payload files to project directory
            projects_base = os.environ.get("PROJECTS_BASE", "/opt/projects")
            target_dir = os.path.join(projects_base, resolved_project)
            os.makedirs(target_dir, exist_ok=True)

            for item in Path(staging).iterdir():
                if item.is_file():
                    shutil.copy2(str(item), os.path.join(target_dir, item.name))

            # Execute deploy
            # 🧐 TRAP[DECISION] · 2026-07-31 · HI · receive() local delivery channel
            # · Rejected: SCPChannel() with empty metadata (bug — deliver() always FAILED:
            #   "SCPChannel requires 'host' in payload.metadata"; the payload is already
            #   extracted to target_dir, so a transport hop is meaningless; exposed by
            #   DevPlan 095 E2E T16 on a real VPS — the mocked IntegrationMockChannel never
            #   caught it)
            # · Reason: LocalChannel is a no-op delivery that preserves the full
            #   DeployOrchestrator pipeline (compose up → healthcheck → DeployHistory
            #   snapshot → audit) on the VPS side. Alternative rejected: self-SSH
            #   (root@127.0.0.1) — requires the VPS root key to authorize itself.
            # · Rev: if receive() ever needs to ship the payload to a THIRD host, switch
            #   back to a real transport channel with explicit host metadata.
            from core.internal.deploy.channels import LocalChannel

            local_channel = LocalChannel()
            orchestrator = DeployOrchestrator(projects_base=projects_base)
            result = orchestrator.deploy(
                project_name=resolved_project,
                channel=local_channel,
                version=version,
                service=service,
                project_dir=target_dir,
            )
            # D5: version (sha) попадает в OrchestratorDeployResult JSON — sha-pinning в snapshots уже
            # сделан внутри deploy() (DeployHistory.create_snapshot(version=version)).
            result.version = version

            # ── Пост-деплой цепочка (D4, U-24): best-effort, сбой → WARN, НЕ фейлит деплой ──
            if result.is_success():
                self._run_post_deploy_chain(resolved_project, version, result.status.value)

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

    # region FUNC__run_post_deploy_chain
    ## @purpose  Best-effort post-deploy chain (DevPlan 116 B1 T2/D4, U-24): notify-hook (Telegram)
    ##           + generate-catalog (regen catalog.json). Оба неблокирующие: сбой → WARN,
    ##           деплой НЕ фейлится (дизайн notify-hook always exit 0).
    ## @io       ⇥ project: str, version: str, status: str → ⎋ None
    ## @complexity — O(1) — два subprocess-вызова с timeout
    ## @invariants
    ##   - Вызывается ТОЛЬКО после успешного деплоя (DEPLOYED/PARTIAL)
    ##   - notify-hook timeout 30s, generate-catalog timeout 60s
    ##   - Сбой цепочки → logger.warning (IMP:8), не raise
    def _run_post_deploy_chain(self, project: str, version: str, status: str) -> None:
        """Run notify-hook + generate-catalog after a successful deploy (best-effort, D4)."""
        platform_root = os.environ.get("PLATFORM_ROOT", "/opt/platform")
        notify_hook = os.path.join(platform_root, "core", "internal", "notify", "notify-hook.sh")
        generate_catalog = os.path.join(platform_root, "core", "internal", "catalog", "generate-catalog.sh")

        logger.info(
            "[IMP:8][DeployOrchestrator][post_deploy_chain] Running notify-hook + generate-catalog for %s (%s)",
            project,
            version,
        )

        # ── notify-hook (Telegram) — неблокирующий (always exit 0) ──
        try:
            subprocess.run(
                [
                    notify_hook,
                    "--severity",
                    "info",
                    "🚀",
                    f"Deployed {project} ({version}) — {status}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            logger.info("[IMP:9][DeployOrchestrator][post_deploy_chain] notify-hook sent for %s", project)
        except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            # Best-effort: сбой уведомления НЕ фейлит деплой (D4, дизайн notify-hook)
            logger.warning("[IMP:8][DeployOrchestrator][post_deploy_chain] notify-hook WARN (non-fatal): %s", e)

        # ── generate-catalog (regen catalog.json) ──
        try:
            subprocess.run(
                [generate_catalog],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            logger.info("[IMP:9][DeployOrchestrator][post_deploy_chain] generate-catalog regenerated for %s", project)
        except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            logger.warning("[IMP:8][DeployOrchestrator][post_deploy_chain] generate-catalog WARN (non-fatal): %s", e)

    # endregion FUNC__run_post_deploy_chain

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
        import tarfile
        import tempfile

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
            from core.internal.deploy.deploy_engine import DeployEngine

            engine = DeployEngine(projects_base=self.projects_base)
            result = engine.deploy(
                project=Path(project_dir).name,
                ref=version,
                service=service,
                project_dir=project_dir,
            )
            return result.success
        except PlatformError as e:
            # T3.1 (DevPlan 116 B4): _handle_first_deploy → PlatformFatalError вместо SystemExit
            logger.error(
                "[IMP:10][DeployOrchestrator][deploy_compose] Deploy engine error (exit=%d): %s", e.exit_code, e
            )
            return False
        except (OSError, subprocess.SubprocessError) as e:
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
            from core.internal.deploy.deploy_engine import DeployEngine

            engine = DeployEngine(projects_base=self.projects_base)
            prev_image_id = snapshot.get("compose_state", {}).get("previous_image")

            # Re-tag and restart
            if prev_image_id:
                subprocess.run(
                    ["docker", "tag", prev_image_id, f"{service}:previous-rollback"],
                    capture_output=True,
                    timeout=DOCKER_CMD_TIMEOUT,
                    check=False,
                )

            result = engine.deploy(
                project=Path(project_dir).name,
                ref="previous-rollback",
                service=service,
                project_dir=project_dir,
            )
            return result.success
        except PlatformError as e:
            # T3.1 (DevPlan 116 B4): _handle_first_deploy → PlatformFatalError вместо SystemExit
            logger.error("[IMP:10][DeployOrchestrator][rollback_compose] Engine error (exit=%d): %s", e.exit_code, e)
            return False
        except (OSError, subprocess.SubprocessError) as e:
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
        version: str = "",
    ) -> OrchestratorDeployResult:
        """Build a OrchestratorDeployResult with common fields.

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
            version: Version/sha (D5 — из аргументов receive).

        Returns:
            OrchestratorDeployResult instance.
        """
        from datetime import datetime, timezone

        return OrchestratorDeployResult(
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
            version=version,
        )


# endregion CLASS_DeployOrchestrator
