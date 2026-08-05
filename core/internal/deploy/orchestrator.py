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
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from core.internal.deploy.channels import DeliveryChannel, Payload
from core.internal.deploy.deploy_history import DeployHistory
from core.internal.deploy.healthcheck_poller import HealthcheckPoller
from core.internal.shared import docker_ops  # W1: docker tag примитив (гейт docker_sole_path)

# DevPlan 116 B11 T2 (U-10, D1): единый audit-writer — shared/audit_logger.
# deploy/audit_logger.py УДАЛЁН; DeployOrchestrator пишет через write_audit_entry
# (tag="deploy:<operation>") через тонкий адаптер DeployAuditLogger (см. ниже).
from core.internal.shared.audit_logger import DEFAULT_LOG_FILE as _SHARED_AUDIT_LOG_FILE
from core.internal.shared.audit_logger import write_audit_entry as _shared_write_audit_entry

# DevPlan 116 B5 T3: shared docker compose — sole path (гейт docker_sole_path).
# DevPlan 118 A6: status/remove делегируют DeployEngine (StatusResult/RemoveResult) —
# прямые вызовы docker compose ps/down из DeployOrchestrator удалены (импорты ниже не нужны).
from core.internal.shared.deploy_paths import platform_remote_base, projects_base
from core.internal.shared.exceptions import PlatformError

# DevPlan 136 W9 T9.1 (L-1/L-9/L-12): flock deploy lock per project. shared/ — deploy-слой
# НЕ импортирует bootstrap/ (инвариант core/AGENTS.md); lifecycle/lock.py — bootstrap-фасад.
from core.internal.shared.file_lock import FileLock as _FileLock
from core.internal.shared.file_lock import FileLockError as _FileLockError
from core.internal.shared.file_lock import platform_lock_path as _platform_lock_path

# DevPlan 136 W9 T9.7 (L-10): validate_project_name в _prepare_deploy (до deliver).
from core.internal.shared.project_registry import validate_project_name as _validate_project_name

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

# B2: канонический дефолт PROJECTS_BASE — shared/deploy_paths (литерал /opt/projects удалён)
PROJECTS_BASE = str(projects_base())


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

        DevPlan 119 E2: тело разбито на шаги _prepare / _apply / _verify / _rollback
        (было 186 LOC CC=13 → CC ≤ 8 на шаг). Поведение не меняется (R5: parity-тест).

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

        # ── Step 0: concurrent guard (T9.1, L-1/L-9/L-12) — flock per project, non-blocking ──
        # Retry double-deploy / параллельный deploy того же проекта → явный FAILED «locked by PID X»,
        # а не параллельное исполнение compose-операций. Stale-блокировки невозможны (kernel-managed flock).
        lock = _FileLock(_platform_lock_path(project_name), timeout=0.0)
        try:
            lock.acquire()
        except _FileLockError as e:
            logger.error("[IMP:10][DeployOrchestrator][deploy] Concurrent deploy blocked for %s: %s", project_name, e)
            self.audit_logger.log(
                operation="deploy",
                project=project_name,
                channel=channel.__class__.__name__,
                result="FAILED",
                duration_s=time.monotonic() - start,
                error=str(e),
            )
            return self._result(
                DeployStatus.FAILED,
                project_name,
                channel.__class__.__name__,
                error_info=f"Concurrent deploy blocked: {e}",
                duration_s=time.monotonic() - start,
            )

        try:
            # ── Step 1: _prepare (validate + dry-run + payload assembly) ──
            payload, failure = self._prepare_deploy(
                project_name, channel, version, service, project_dir, metadata, dry_run, start
            )
            if failure is not None:
                return failure

            # ── Step 2: _apply (deliver + compose up) ──
            apply_result = self._apply_deploy(project_name, channel, version, service, project_dir, payload, start)
            if apply_result is not None:
                return apply_result

            # ── Step 3: _verify (healthcheck + snapshot + audit) ──
            # T9.6 (L-11): исключение в verify (snapshot OSError и т.п.) — audit FAILED + результат,
            # не молчаливый проброс без audit-следа.
            try:
                return self._verify_deploy(
                    project_name,
                    channel,
                    version,
                    project_dir,
                    start,
                    payload_backup_dir=metadata.get("payload_backup_dir"),
                )
            except (OSError, subprocess.SubprocessError) as e:
                logger.error(
                    "[IMP:10][DeployOrchestrator][deploy] Verify failed for %s: %s (auditing FAILED)", project_name, e
                )
                self.audit_logger.log(
                    operation="deploy",
                    project=project_name,
                    channel=channel.__class__.__name__,
                    result="FAILED",
                    duration_s=time.monotonic() - start,
                    error=str(e),
                )
                return self._result(
                    DeployStatus.FAILED,
                    project_name,
                    channel.__class__.__name__,
                    error_info=f"Deploy verify failed: {e}",
                    duration_s=time.monotonic() - start,
                )
        finally:
            lock.release()

    # endregion FUNC_deploy

    # region FUNC__prepare_deploy
    ## @purpose  E2 deploy step 1 (PREPARE): validate project_name → dry-run short-circuit →
    ##           assemble payload. Returns (payload, failure_result): failure_result != None
    ##           → abort (deploy вернёт его как есть).
    ## @io       ⇥ (deploy args + start) → ⎋ tuple[Payload | None, OrchestratorDeployResult | None]
    ## @complexity — O(1) — validation + dry-run; O(PAYLOAD) on assemble
    ## @invariants
    ##   - Пустой project_name → FAILED (validation)
    ##   - dry_run=True → SKIPPED plan (no side effects, DevPlan 089 AC10)
    ##   - Assembly failure (OSError/ValueError) → FAILED
    def _prepare_deploy(
        self,
        project_name: str,
        channel: DeliveryChannel,
        version: str,
        service: str,
        project_dir: str,
        metadata: dict[str, Any],
        dry_run: bool,
        start: float,
    ) -> tuple[Payload | None, OrchestratorDeployResult | None]:
        """Validate inputs, handle dry-run, assemble payload (E2 step PREPARE)."""
        # ── Validate ──
        if not project_name:
            return None, self._result(
                DeployStatus.FAILED,
                project_name,
                "",
                error_info="Project name is required",
                duration_s=time.monotonic() - start,
            )

        # ── T9.7 (L-10): validate_project_name ДО deliver — инъекция `;`/`../` в project_name
        # отсекается ДО маршрутизации/доставки (канон — shared/project_registry, verb-reserve U-56) ──
        if not _validate_project_name(project_name):
            logger.error("[IMP:10][DeployOrchestrator][prepare] Invalid/reserved project name: %r (T9.7)", project_name)
            return None, self._result(
                DeployStatus.FAILED,
                project_name,
                channel.__class__.__name__,
                error_info=f"Invalid or reserved project name: {project_name}",
                duration_s=time.monotonic() - start,
            )

        # ── dry-run short-circuit (DevPlan 089 AC10) ──
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
            return None, self._result(
                DeployStatus.SKIPPED,
                project_name,
                channel.__class__.__name__,
                error_info="dry-run — no execution",
                duration_s=time.monotonic() - start,
            )

        # ── Assemble payload ──
        try:
            payload = self._assemble_payload(project_name, version, project_dir, metadata)
        except (OSError, ValueError) as e:
            return None, self._result(
                DeployStatus.FAILED,
                project_name,
                channel.__class__.__name__,
                error_info=f"Payload assembly failed: {e}",
                duration_s=time.monotonic() - start,
            )
        return payload, None

    # endregion FUNC__prepare_deploy

    # region FUNC__apply_deploy
    ## @purpose  E2 deploy step 2 (APPLY): deliver payload through channel → compose up.
    ##           Returns failure/rolled-back result or None (→ proceed to verify).
    ## @io       ⇥ (deploy args + payload + start) → ⎋ OrchestratorDeployResult | None
    ## @complexity — O(1) — deliver + compose call
    ## @invariants
    ##   - Delivery failure → FAILED (with delivery stdout/stderr)
    ##   - Compose failure → rollback if snapshot exists (else FAILED)
    def _apply_deploy(
        self,
        project_name: str,
        channel: DeliveryChannel,
        version: str,
        service: str,
        project_dir: str,
        payload: Payload,
        start: float,
    ) -> OrchestratorDeployResult | None:
        """Deliver payload + compose up (E2 step APPLY). Returns result on failure, None on success."""
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

        compose_ok = self._deploy_compose(project_dir, service, version)
        if not compose_ok:
            # Rollback if previous deployment exists
            snapshot = self.deploy_history.latest_snapshot(project_name)
            if snapshot:
                logger.info(
                    "[IMP:9][DeployOrchestrator][deploy] Compose failed — attempting rollback for %s",
                    project_name,
                )
                # T9.8 (L-6): payload-бэкап (предыдущие payload-файлы, снят ДО overwrite в
                # receive_flow) передаётся в rollback — восстанавливаются НЕ только compose/image.
                payload_backup_dir = payload.metadata.get("payload_backup_dir")
                return self._rollback_deploy(
                    project_name, channel, service, project_dir, snapshot, start, payload_backup_dir=payload_backup_dir
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
        return None

    # endregion FUNC__apply_deploy

    # region FUNC__verify_deploy
    ## @purpose  E2 deploy step 3 (VERIFY): healthcheck → snapshot → audit → final result.
    ## @io       ⇥ (deploy args + start) → ⎋ OrchestratorDeployResult
    ## @complexity — O(1) — poll + snapshot + audit
    ## @invariants
    ##   - Healthcheck status "healthy" → DEPLOYED, иначе PARTIAL
    ##   - Snapshot создаётся после healthcheck (содержит post-deploy health)
    ##   - payload_backup_dir (T9.8) персистится в snapshot (rollback восстанавливает payload)
    def _verify_deploy(
        self,
        project_name: str,
        channel: DeliveryChannel,
        version: str,
        project_dir: str,
        start: float,
        payload_backup_dir: str | None = None,
    ) -> OrchestratorDeployResult:
        """Healthcheck + snapshot + audit (E2 step VERIFY)."""
        health = self.healthcheck_poller.poll_until_healthy(project_name, project_dir)
        healthcheck_status = health.status

        total_duration = time.monotonic() - start

        snapshot_id = self.deploy_history.create_snapshot(
            project=project_name,
            version=version,
            health_status=healthcheck_status,
            payload_backup_dir=payload_backup_dir,
        )

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

    # endregion FUNC__verify_deploy

    # region FUNC__rollback_deploy
    ## @purpose  E2 deploy step 4 (ROLLBACK): restore payload files (T9.8) + compose from
    ##           snapshot after failed apply.
    ## @io       ⇥ (project, channel, service, project_dir, snapshot, start, payload_backup_dir) → ⎋ OrchestratorDeployResult
    ## @complexity — O(F + 1) — F payload-файлов + rollback compose + audit
    ## @invariants
    ##   - payload_backup_dir (предыдущие payload-файлы, снят до overwrite) → restore ДО compose
    ##   - Rollback успешен → ROLLED_BACK, иначе FAILED
    def _rollback_deploy(
        self,
        project_name: str,
        channel: DeliveryChannel,
        service: str,
        project_dir: str,
        snapshot: dict[str, Any],
        start: float,
        payload_backup_dir: str | None = None,
    ) -> OrchestratorDeployResult:
        """Rollback payload + compose after failed deploy (E2 step ROLLBACK)."""
        # T9.8 (L-6): rollback восстанавливает payload-файлы из бэкапа (не только compose).
        # Бэкап снят ДО overwrite в receive_flow — содержит предыдущие (рабочие) файлы.
        if payload_backup_dir:
            restored = self._restore_payload_files(payload_backup_dir, project_dir)
            if restored:
                logger.info(
                    "[IMP:9][DeployOrchestrator][rollback] Payload files restored from backup %s (T9.8)",
                    payload_backup_dir,
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

    # endregion FUNC__rollback_deploy

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

        # T9.8 (L-6): payload-файлы из snapshot (payload_dir — пред-деплойный бэкап) восстанавливаются
        # ДО compose-rollback: сломанный payload (compose/ai-platform.yaml) заменяется рабочим.
        snapshot_payload_dir = snapshot.get("payload_dir")
        if snapshot_payload_dir and os.path.isdir(snapshot_payload_dir):
            restored = self._restore_payload_files(snapshot_payload_dir, project_dir)
            if restored:
                logger.info(
                    "[IMP:9][DeployOrchestrator][rollback] Payload files restored from snapshot %s (T9.8)",
                    snapshot.get("snapshot_id"),
                )

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
    ##           DevPlan 118 A6: канон = DeployEngine.status() (StatusResult); DeployOrchestrator
    ##           делегирует и преобразует тип StatusResult → ProjectStatus (JSON-канон диспетчера).
    ##           Единая stub-детекция — shared/stub_detection.is_stub_ai_platform_yaml (внутри engine).
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

        from core.internal.deploy.deploy_engine import DeployEngine

        engine = DeployEngine(projects_base=self.projects_base)
        sr = engine.status(project=project_name, project_dir=project_dir, stub_aware=True)

        # StatusResult → ProjectStatus: drop node (не входит в JSON-канон диспетчера, D6)
        return ProjectStatus(
            project=sr.project,
            status=sr.status,
            containers=sr.containers,
            last_deploy=sr.last_deploy,
        )

    # endregion FUNC_status

    # region FUNC_remove
    ## @purpose  Remove a project's containers (idempotent). Data preserved — no -v flag.
    ##           DevPlan 118 A6: канон = DeployEngine.remove() (RemoveResult); DeployOrchestrator
    ##           делегирует и преобразует тип RemoveResult → OrchestratorDeployResult.
    ## @io       ⇥ project_name: str, purge: bool = False → ⎋ OrchestratorDeployResult
    ## @complexity — O(1) — single docker compose call
    ## @invariants
    ##   - docker compose down WITHOUT -v (data preserved) — О7
    ##   - purge=True removes compose volumes (docker compose down -v) — явный CLI-флаг
    ##   - Idempotent: if project dir missing → SKIPPED
    ##   - Audit пишется через DeployAuditLogger (единый writer, D1)
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

        from core.internal.deploy.deploy_engine import DeployEngine

        engine = DeployEngine(projects_base=self.projects_base)
        rr = engine.remove(project=project_name, project_dir=project_dir, purge=purge)

        duration = time.monotonic() - start

        # RemoveResult → OrchestratorDeployResult: already_removed → SKIPPED, success → DEPLOYED, fail → FAILED
        if not rr.success:
            return self._result(
                DeployStatus.FAILED,
                project_name,
                error_info=rr.error_message or "Remove failed",
                duration_s=duration,
            )

        if rr.already_removed:
            self.audit_logger.log(
                operation="remove",
                project=project_name,
                result=DeployStatus.SKIPPED.value,
                duration_s=duration,
            )
            return self._result(
                DeployStatus.SKIPPED,
                project_name,
                error_info="Project directory not found — already removed",
                duration_s=duration,
            )

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

    # endregion FUNC_remove

    # ── receive() — VPS-side forced-command receiver ──

    # region FUNC_receive
    ## @purpose  VPS-side forced-command receiver (DevPlan 116 B1 T2, D1/D4/D5). DevPlan 119 E2:
    ##           реализация вынесена в deploy/receive_flow.py (ReceiveFlow: unpack → validate →
    ##           deploy). Тонкий фасад сохраняет публичный API receive(project_name, version) -> int
    ##           и JSON OrchestratorDeployResult в stdout (контракт orchestrator_cli dispatch).
    ## @io       ⇥ stdin (tar bytes), project_name: str | None (из SSH-аргументов),
    ##              version: str (sha из CI, D5) → ⎋ int (exit code 0/1) + JSON в stdout
    ## @complexity — O(1) — delegation to ReceiveFlow (вся логика в receive_flow.py, E2)
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

        E2 (DevPlan 119): делегирование в ReceiveFlow (deploy/receive_flow.py) — unpack,
        validate, deploy изолированы (CC 15 → ≤8 на метод). Контракт не меняется.

        Args:
            project_name: Project name from SSH_ORIGINAL_COMMAND args (D5). When None
                (локальные/ручные вызовы) — фолбэк на ai-platform.yaml `name`.
            version: Version/sha from SSH args (D5). Default "latest" для локальных вызовов.

        Returns:
            Exit code (0 = success, 1 = failure).
        """
        from core.internal.deploy.receive_flow import ReceiveFlow

        logger.info(
            "[IMP:9][DeployOrchestrator][receive] Delegating to ReceiveFlow (E2) project=%s version=%s",
            project_name or "auto",
            version,
        )
        # E2: projects_base резолвится ВНУТРИ ReceiveFlow.run() из env-цепочки (PROJECTS_BASE →
        # /opt/projects) — legacy receive() семантика: env на момент вызова, не import-константа.
        # Передача None (не self.projects_base — модульная константа import-времени).
        flow = ReceiveFlow(projects_base=None)
        return flow.run(project_name=project_name, version=version)

    # endregion FUNC_receive

    # region FUNC__run_post_deploy_chain
    ## @purpose  Best-effort post-deploy chain (DevPlan 116 B1 T2/D4, U-24): notify-hook (Telegram)
    ##           + generate-catalog (regen catalog.json) + module deploy-hooks (B8 wire).
    ##           Все неблокирующие: сбой → WARN, деплой НЕ фейлится (дизайн notify-hook always exit 0).
    ## @io       ⇥ project: str, version: str, status: str, project_dir: str, node_name: str → ⎋ None
    ## @complexity — O(1) — subprocess-вызовы с timeout
    ## @invariants
    ##   - Вызывается ТОЛЬКО после успешного деплоя (DEPLOYED/PARTIAL)
    ##   - notify-hook timeout 30s, generate-catalog timeout 60s, module deploy-hook COMPOSE_UP_TIMEOUT
    ##   - Сбой цепочки → logger.warning (IMP:8), не raise
    ##   - B8 (волна 118): module deploy-hooks (module.yaml hooks.on_project_deploy) вызываются
    ##     через shared/module_interface.invoke — восстановленный триггер (ранее удалён в 117 sweep)
    def _run_post_deploy_chain(
        self,
        project: str,
        version: str,
        status: str,
        project_dir: str | None = None,
        node_name: str = "",
    ) -> None:
        """Run notify-hook + generate-catalog + module deploy-hooks (best-effort, D4)."""
        platform_root = str(platform_remote_base())
        notify_hook = os.path.join(platform_root, "core", "internal", "notify", "notify-hook.sh")
        generate_catalog = os.path.join(platform_root, "core", "internal", "catalog", "generate-catalog.sh")

        logger.info(
            "[IMP:8][DeployOrchestrator][post_deploy_chain] Running notify-hook + generate-catalog + deploy-hooks for %s (%s)",
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

        # ── Module deploy-hooks (B8, волна 118): deploy-hook для зарегистрированных модулей ──
        # Регистрация: module.yaml hooks.on_project_deploy (+ entrypoint-manifest module_hooks).
        # После B8 зарегистрирован только nginx (reload-guard); monitoring/postgres удалены
        # (Python-эквиваленты: monitoring_config_renderer.py / on_project_deploy.py).
        if project_dir:
            self._invoke_registered_deploy_hooks(project_dir, project, node_name)

    # endregion FUNC__run_post_deploy_chain

    # region FUNC__invoke_registered_deploy_hooks
    ## @purpose  Invoke deploy-hook for every module declaring hooks.on_project_deploy (B8 wire).
    ##            Registry-driven: читает core/modules/*/module.yaml (registry = файловая система),
    ##            НЕ хардкодит имена модулей. Best-effort: сбой → WARN, деплой не фейлится.
    ## @io       ⇥ project_dir: str, project: str, node_name: str → ⎋ None
    ## @complexity — O(M * K) где M = модули с hooks, K = hook-скрипты на модуль
    ## @invariants
    ##   - Каждый module.yaml с hooks.on_project_deploy → module_interface.invoke(module, "deploy-hook", ...)
    ##   - hook args: PROJECT_DIR PROJECT NODE_NAME (сигнатура nginx_reload_hook.sh)
    ##   - Сбой invoke → WARN (IMP:8), не raise (Best-effort контракт post-deploy chain)
    def _invoke_registered_deploy_hooks(self, project_dir: str, project: str, node_name: str) -> None:
        """Invoke registered module deploy-hooks via shared module_interface (B8)."""
        from core.internal.shared.module_interface import invoke as invoke_module_hook

        platform_root = str(platform_remote_base())
        modules_dir = os.path.join(platform_root, "core", "modules")
        if not os.path.isdir(modules_dir):
            logger.info("[IMP:7][DeployOrchestrator][deploy_hooks] modules dir not found: %s", modules_dir)
            return

        import glob

        for module_yaml in sorted(glob.glob(os.path.join(modules_dir, "*/module.yaml"))):
            module_name = os.path.basename(os.path.dirname(module_yaml))
            try:
                import yaml

                with open(module_yaml) as f:
                    data = yaml.safe_load(f) or {}
                hooks = data.get("hooks") or {}
                if not hooks.get("on_project_deploy"):
                    continue
            except (OSError, yaml.YAMLError) as e:
                logger.warning("[IMP:8][DeployOrchestrator][deploy_hooks] read error %s: %s", module_yaml, e)
                continue

            logger.info(
                "[IMP:8][DeployOrchestrator][deploy_hooks] Invoking deploy-hook for module %s (project=%s)",
                module_name,
                project,
            )
            ok, output = invoke_module_hook(module_name, "deploy-hook", project_dir, project, node_name)
            if not ok:
                logger.warning(
                    "[IMP:8][DeployOrchestrator][deploy_hooks] %s deploy-hook WARN (non-fatal): %s",
                    module_name,
                    (output or "").strip()[-300:],
                )
            else:
                logger.info("[IMP:9][DeployOrchestrator][deploy_hooks] %s deploy-hook done", module_name)

    # endregion FUNC__invoke_registered_deploy_hooks

    # ── Internal helpers ──────────────────────────────────────────────────

    def _assemble_payload(
        self,
        project_name: str,
        version: str,
        project_dir: str,
        metadata: dict[str, Any],
    ) -> Payload:
        """Assemble a deploy payload from project files.

        DevPlan 118 A4: локальная tar-реализация УДАЛЕНА — делегирование в
        PayloadDeliverer.assemble_payload (единственный путь сборки tar.gz).
        Контракт аргументов идентичен (project_name/version/project_dir/metadata);
        file-set совпадает (_PAYLOAD_FILE_NAMES = compose-канон + ai-platform.yaml + .env.platform).
        Сборка через тот же код, что receive/deliver — исключает дрейф формата payload (K8).

        Args:
            project_name: Project name.
            version: Version/tag.
            project_dir: Project directory path.
            metadata: Additional metadata.

        Returns:
            Payload with tar_path pointing to assembled tar.gz.

        Raises:
            OSError: If project files cannot be read.
        """
        from core.internal.deploy.payload_deliverer import PayloadDeliverer

        deliverer = PayloadDeliverer(projects_base=self.projects_base)
        return deliverer.assemble_payload(
            project_name=project_name,
            version=version,
            project_dir=project_dir,
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

    # region FUNC__restore_payload_files
    ## @purpose  Restore payload files from a backup dir into the project dir (T9.8, L-6).
    ##           Используется при rollback: (а) deploy-failure — из metadata payload_backup_dir
    ##           (бэкап снят ДО overwrite в receive_flow), (б) manual rollback — из snapshot payload_dir.
    ## @io       ⇥ backup_dir: str (директория с сохранёнными payload-файлами), target_dir: str → ⎋ bool
    ## @complexity O(F) где F = файлов в backup
    ## @invariants
    ##   - Копирует ВСЕ файлы backup (compose/ai-platform.yaml/.env.platform) поверх target
    ##   - Файл не читается (OSError) → WARN, restore считается неуспешным (False)
    ##   - Не-fatal для общего rollback-флоу: сбой restore НЕ блокирует compose-rollback
    def _restore_payload_files(self, backup_dir: str, target_dir: str) -> bool:
        """Copy payload files from a backup dir into the project dir (T9.8)."""
        try:
            os.makedirs(target_dir, exist_ok=True)
            restored = 0
            for item in os.listdir(backup_dir):
                src = os.path.join(backup_dir, item)
                if not os.path.isfile(src):
                    continue
                dest = os.path.join(target_dir, item)
                if os.path.lexists(dest):
                    try:
                        os.remove(dest)
                    except OSError as e:
                        logger.warning(
                            "[IMP:7][DeployOrchestrator][restore_payload] Cannot remove %s (non-fatal): %s", dest, e
                        )
                shutil.copy2(src, dest)
                restored += 1
            logger.info(
                "[IMP:9][DeployOrchestrator][restore_payload] Restored %d payload file(s) → %s", restored, target_dir
            )
            return True
        except OSError as e:
            logger.error("[IMP:10][DeployOrchestrator][restore_payload] Payload restore failed: %s", e)
            return False

    # endregion FUNC__restore_payload_files

    # region FUNC__rollback_compose
    ## @purpose  Rollback compose to a previous snapshot state.
    ## @io       ⇥ project_dir: str, service: str, snapshot: dict[str, Any] → ⎋ bool
    ## @complexity — O(1) — single docker compose deploy of previous image
    ## @invariants
    ##   - previous_image из compose_state re-tag → docker compose deploy
    ##   - PlatformError/OSError/SubprocessError → False (audit пишет FAILED в _rollback_deploy)
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

            # Re-tag and restart (W1: docker tag — shared/docker_ops, non-fatal)
            if prev_image_id:
                docker_ops.docker_tag(prev_image_id, f"{service}:previous-rollback")

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

    # endregion FUNC__rollback_compose

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
