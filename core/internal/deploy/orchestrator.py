#!/usr/bin/env python3
"""
DeployOrchestrator — единый typed фасад для всех deploy-операций.
Инкапсулирует DeployEngine, PayloadDeliverer, DeliveryChannel, AuditLogger, DeployHistory, HealthcheckPoller.
170 W4-B3: DeployAuditLogger → deploy/audit/, post-deploy chain → deploy/hooks/ (фасад сохраняет API).
T3.1: rollback-кластер (DeployStatus/OrchestratorDeployResult/RollbackMixin) → deploy/rollback.py
(re-export сохраняет API: старые имена живут, DeployOrchestrator(RollbackMixin)).
"""
# GREP_SUMMARY: deploy-orchestrator, facade, deploy, rollback, status, remove, deploy-many, audit, delivery-channel, l1-pre-apply-gate
# STRUCTURE: ▶ DeployOrchestrator(deploy|deploy_many|rollback|status|remove) → ◇ L1 pre-apply gate (verify_contracts l1_only, REF-0006) → ┌DeliveryChannel deliver┐ → ┌DeployEngine deploy_compose┐ → ┌DeployHistory create_snapshot┐ → ┌AuditLogger log┐ → ┌hooks post_deploy_chain┐ → ⎋ OrchestratorDeployResult
# region MODULE_CONTRACT
## @purpose  Unified deploy orchestrator — single typed facade for all deploy operations.
##           Eliminates 6+ parallel deploy paths by providing deploy()/deploy_many()/rollback()/status()/remove().
##           Uses DeliveryChannel for transport, DeployEngine for Docker lifecycle,
##           PayloadDeliverer for tar assembly, AuditLogger for audit trail (deploy/audit/),
##           DeployHistory for snapshot-based rollback (deploy/audit/), HealthcheckPoller for health
##           verification, hooks/post_deploy_chain for best-effort post-deploy chain.
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
##   9. Аудит через AuditLogger (единый формат, shared/audit_logger)
##  10. L1 pre-apply gate (REF-0006, DevPlan 11 В2): verify_contracts(l1_only=True) на
##      project_dir ДО доставки/compose-up — violation → FAILED, контейнеры не запускаются
##      (TOCTOU-закрытие: гейт исполняется в том же процессе, что и compose)
## @rationale DevPlan 089 — устраняет дублирование бизнес-логики в 6+ путях деплоя.
##            Багфикс в одном пути применяется ко всем через единый DeployOrchestrator.
## @changes 2026-07-30 | DevPlan 089 T6 — Created
##           2026-08-05 | DevPlan 138 W3 — monitoring reconfig (run_monitoring_reconfig) в
##           _run_post_deploy_chain после generate-catalog, до deploy-hooks
##           2026-08-15 | 170 W4-B3 — DeployAuditLogger → deploy/audit/logger.py; deploy_history →
##           deploy/audit/history.py; _run_post_deploy_chain (4 подшага) → deploy/hooks/
##           post_deploy_chain.py (run_post_deploy_chain); lazy DeployEngine → module-level
##           2026-08-22 | T3.1 — rollback-кластер (DeployStatus, OrchestratorDeployResult,
##           RollbackMixin) → deploy/rollback.py; re-export + наследование сохраняют API
##           2026-08-24 | REF-0004 (DevPlan 11 В1) — rollback-контур: якорь previous_image
##           в снапшоте; skip double-rollback при engine rollback_performed; unhealthy →
##           ROLLED_BACK c одним re-verify (rollback_verified); require_healthy-цель отката
##           2026-08-25 | REF-0006 (DevPlan 11 В2) — L1 pre-apply gate: verify_project_contracts
##           (l1_only=True) внутри deploy() ПЕРЕД _apply_deploy; DI-шов pre_apply_gate
##           (None → реальный verify; fail-closed при ошибке гейта)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, cast

from core.internal.deploy.audit import DeployAuditLogger, DeployHistory
from core.internal.deploy.channels import DeliveryChannel, Payload
from core.internal.deploy.deploy_engine import DeployEngine
from core.internal.deploy.healthcheck_poller import HealthcheckPoller
from core.internal.deploy.hooks import run_post_deploy_chain

# T3.1: rollback-кластер вынесен в deploy/rollback.py — re-export старых имён (старые имена живут):
# DeployStatus/OrchestratorDeployResult остаются доступными из core.internal.deploy.orchestrator.
from core.internal.deploy.rollback import DeployStatus, OrchestratorDeployResult, RollbackMixin

# REF-0006 (DevPlan 11 В2): L1 pre-apply gate — тот же K3-канон verify_contracts, l1_only режим
# (ТОЛЬКО L1-статика, без docker-L2 латентности). НЕ дублирование receive-гейта (176 A.2):
# receive гейтит staging ДО копирования, этот — target_dir в момент compose (TOCTOU-закрытие).
from core.internal.deploy.verify_contracts import VerifyReport, verify_project_contracts

# W4a (DevPlan 160 T4.1): AppConfig — ленивый резолв PROJECTS_BASE (import-time env убран).
from core.internal.shared.app_config import AppConfig

# status/remove делегируют DeployEngine (StatusResult/RemoveResult) —
# прямых вызовов docker compose ps/down в DeployOrchestrator нет.
from core.internal.shared.exceptions import PlatformError

# DevPlan 136 W9 T9.1 (L-1/L-9/L-12): flock deploy lock per project. shared/ — deploy-слой
# НЕ импортирует bootstrap/ (инвариант core/AGENTS.md); канон — shared/file_lock.py
# (bootstrap/lifecycle/lock.py re-export удалён как мёртвый — потребители импортируют shared напрямую).
from core.internal.shared.file_lock import FileLock as _FileLock
from core.internal.shared.file_lock import FileLockError as _FileLockError
from core.internal.shared.file_lock import platform_lock_path as _platform_lock_path

# DevPlan 136 W9 T9.7 (L-10): validate_project_name в _prepare_deploy (до deliver).
from core.internal.shared.project_registry import validate_project_name as _validate_project_name

logger = logging.getLogger(__name__)


# Канонический дефолт PROJECTS_BASE — shared/deploy_paths (B2).
# W4a: дефолт PROJECTS_BASE резолвится лениво в __init__ (None → AppConfig.from_env().projects_base).

# 170 W4-B3: DeployAuditLogger вынесен в deploy/audit/logger.py (тонкий адаптер
# DeployOrchestrator → shared write_audit_entry). Импортируется module-level (строка 47).


# region FUNC__default_pre_apply_gate
## @purpose  Дефолтный L1 pre-apply gate (REF-0006, DevPlan 11 В2): verify_contracts l1_only
##           на каталоге проекта ДО доставки/compose-up. audit_project_name — реальное имя
##           проекта (block-события в аудит-трейле).
## @io       ⇥ project_dir: str, project_name: str → ⎋ VerifyReport
## @complexity O(S) где S = размер compose (чистая статика, 0 docker-subprocess)
def _default_pre_apply_gate(project_dir: str, project_name: str) -> VerifyReport:
    """L1-only pre-apply gate on the target project dir (REF-0006)."""
    return verify_project_contracts(Path(project_dir), l1_only=True, audit_project_name=project_name)


# endregion FUNC__default_pre_apply_gate


# 📝 TRAP[DEBT] · 2026-08-22 · LO · _try_json_loads — мёртвый код (0 вызовов/импортов в core/+tests/)
# · Observed: grep _try_json_loads по всему репо даёт только определение + внутренний cast
# · Suspected: хелпер пережил рефакторинги (PERF203-изоляция), потребитель удалён/не перенесён
# · Impact: мусорный API на модульном уровне; попадает в Doxygen-индекс как живой символ
# · When: T3.1 (сплит rollback-кластера) — волна 0 (dead-код sweep) этот символ пропустила
def _try_json_loads(s: str) -> dict[str, object] | None:
    """Parse a JSON string, returning None on failure.

    ## @purpose — Helper for PERF203: isolate try-except from loop.
    ## @io — ⇥ s: str → ⎋ dict | None
    ## @complexity — O(n) where n = len(s)
    """
    try:
        # json.loads → Any; объектная граница (W11)
        return cast(dict[str, object], json.loads(s))
    except json.JSONDecodeError:
        return None


# region DATACLASSES
# T3.1: DeployStatus/OrchestratorDeployResult вынесены в deploy/rollback.py (владелец —
# rollback-кластер); re-export наверху модуля сохраняет старые имена для потребителей.


@dataclass
class ProjectStatus:
    """Status of a project for status() operation.

    ## @purpose — Encapsulate project status information.
    ## @io — ⇥ constructor params → ⎋ ProjectStatus
    ## @complexity — O(1)
    """

    project: str
    status: str  # "found", "not_found", "stub"
    containers: list[dict[str, object]] = field(default_factory=list)
    last_deploy: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to JSON-compatible dict."""
        return {
            "project": self.project,
            "status": self.status,
            "containers": self.containers,
            "last_deploy": self.last_deploy,
        }


# endregion ENUMS & DATACLASSES


# region CLASS_DeployOrchestrator


class DeployOrchestrator(RollbackMixin):
    """Unified deploy orchestrator — single facade for all deploy operations.

    ## @purpose — Provide deploy()/deploy_many()/rollback()/status()/remove() as
    ##            typed methods. Internal components injected via constructor for testability.
    ##            T3.1: rollback-кластер унаследован из RollbackMixin (deploy/rollback.py) —
    ##            rollback()/_rollback_deploy()/_restore_payload_files()/_rollback_compose() живут.
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
        projects_base: str | None = None,
        audit_logger: DeployAuditLogger | None = None,
        deploy_history: DeployHistory | None = None,
        healthcheck_poller: HealthcheckPoller | None = None,
        *,
        compose_deployer: Callable[[str, str, str], bool] | None = None,
        compose_rollback: Callable[[str, str, dict[str, object]], bool] | None = None,
        previous_image_resolver: Callable[[str, str], str] | None = None,
        pre_apply_gate: Callable[[str, str], VerifyReport] | None = None,
    ):
        # W4a: ленивый env-фолбэк (None → AppConfig.from_env().projects_base) — тот же канон
        # PROJECTS_BASE → /opt/projects, но на момент конструирования, не импорта.
        self.projects_base = projects_base if projects_base is not None else AppConfig.from_env().projects_base
        self.audit_logger = audit_logger or DeployAuditLogger()
        self.deploy_history = deploy_history or DeployHistory(self.projects_base)
        self.healthcheck_poller = healthcheck_poller or HealthcheckPoller()
        # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · compose-швы в конструкторе (167 D3)
        # · Rejected: monkeypatch.setattr(DeployOrchestrator, "_deploy_compose"/"_rollback_compose") в тестах
        # · Reason: seam = тестируемость реального compose-вызова без docker (unit 76-152s → <1s);
        # ·   None → self._deploy_compose/_rollback_compose (прод-поведение без изменений)
        # · Rev: если compose-операции станут отдельным сервисом — параметры заменятся инстансом
        self._compose_deployer = compose_deployer
        self._compose_rollback = compose_rollback
        # REF-0004: DI-резолвер якоря previous_image (тесты); None → значение берётся из
        # результата реального engine.deploy (ServiceDeployResult.previous_image, stash ниже).
        self._previous_image_resolver = previous_image_resolver
        # REF-0006 (DevPlan 11 В2): DI-шов L1 pre-apply gate (прецедент TRAP[DI-SEAM] 167 D3);
        # None → _default_pre_apply_gate (реальный verify_contracts l1_only). Production-caller'ы
        # (ReceiveFlow/context_deployer/CLI) шов НЕ передают → гейт активен всегда.
        self._pre_apply_gate = pre_apply_gate
        # 🧐 TRAP[DECISION] · 2026-08-24 · — · Сигнал-канал от последнего engine.deploy (REF-0004)
        # · Rejected: менять сигнатуру compose-DI-шва на ServiceDeployResult (ломает существующие
        # ·   тесты-фейки Callable[..., bool]) / второй DI-параметр-engine
        # · Reason: _deploy_compose (реальный путь) пишут rollback_performed/rollback_verified/
        # ·   previous_image сюда; DI-швы (тесты) их не пишут → False/"" = «engine не участвовал»,
        # ·   двойной откат исключён. Инстанс живёт под per-project flock — гонок нет.
        # · Rev: если появится конкурентное использование одного инстанса без лока — заменить
        # ·   на явный result-object между шагами deploy().
        self._last_engine_rollback_performed = False
        self._last_engine_rollback_verified = False
        self._last_engine_previous_image = ""

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
        metadata: dict[str, object] | None = None,
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
            assert payload is not None  # контракт _prepare_deploy: failure=None ⇒ payload собран

            # ── Step 1.5 (REF-0006): L1 pre-apply gate — ДО доставки/compose-up ──
            gate_failure = self._run_l1_pre_apply_gate(project_name, channel, project_dir, start)
            if gate_failure is not None:
                return gate_failure

            # ── Step 2: _apply (deliver + compose up) ──
            apply_result = self._apply_deploy(project_name, channel, version, service, project_dir, payload, start)
            if apply_result is not None:
                return apply_result

            # ── Step 3: _verify (healthcheck + snapshot + audit + rollback contour) ──
            # T9.6 (L-11): исключение в verify (snapshot OSError и т.п.) — audit FAILED + результат,
            # не молчаливый проброс без audit-следа.
            try:
                return self._verify_deploy(
                    project_name,
                    channel,
                    version,
                    project_dir,
                    start,
                    payload_backup_dir=cast(str | None, metadata.get("payload_backup_dir")),
                    service=service,  # REF-0004 (additive): имя сервиса для rollback-контурa
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

    # region FUNC__run_l1_pre_apply_gate
    ## @purpose  REF-0006 (DevPlan 11 В2): L1 pre-apply gate перед _apply_deploy — единственная
    ##           точка между maintainer'ом проекта и compose от root в deploy-пути (receive-гейт
    ##           176 A.2 гейтит staging; здесь — target_dir в момент compose, TOCTOU-закрытие).
    ##           Violation → FAILED + audit; ошибка самого гейта → fail-CLOSED (блок, не пропуск:
    ##           security-гейт, падающий в open = дыра). dry_run до гейта не доходит (SKIPPED в prepare).
    ## @io       ⇥ project_name: str, channel: DeliveryChannel, project_dir: str, start: float
    ##           → ⎋ OrchestratorDeployResult | None (None = gate PASS → продолжаем)
    ## @complexity O(S) где S = размер compose (статика)
    ## @invariants
    ##   - Blocking violation → контейнеры НЕ запускаются, delivery НЕ выполняется
    ##   - Любое исключение гейта трактуется как блок (fail-closed) с честным error_info
    def _run_l1_pre_apply_gate(
        self,
        project_name: str,
        channel: DeliveryChannel,
        project_dir: str,
        start: float,
    ) -> OrchestratorDeployResult | None:
        """Run the L1 pre-apply gate (REF-0006); return FAILED result on block, None on pass."""
        gate_fn = self._pre_apply_gate if self._pre_apply_gate is not None else _default_pre_apply_gate
        try:
            report = gate_fn(project_dir, project_name)
        except Exception as e:  # ruff: ignore[BLE001] · ## noqa: EXC — except-тело best-effort (audit/log non-raising), вердикт гейта — fail-closed блок
            logger.error(
                "[IMP:10][DeployOrchestrator][l1-gate] Gate ERROR for %s (fail-closed): %s",
                project_name,
                e,
            )
            self.audit_logger.log(
                operation="deploy",
                project=project_name,
                channel=channel.__class__.__name__,
                result="FAILED",
                duration_s=time.monotonic() - start,
                error=f"L1 pre-apply gate error (fail-closed): {e}",
            )
            return self._result(
                DeployStatus.FAILED,
                project_name,
                channel.__class__.__name__,
                error_info=f"L1 pre-apply gate failed closed (REF-0006): {e}",
                duration_s=time.monotonic() - start,
            )
        if report.has_blocking_violation():
            n_block = sum(1 for f in report.findings if f.severity == "block")
            blocked_ids = sorted({f.contract_id for f in report.findings if f.severity == "block"})
            logger.error(
                "[IMP:10][DeployOrchestrator][l1-gate] BLOCKED %s (%d violations: %s) — "
                "delivery/compose NOT executed (REF-0006):\n%s",
                project_name,
                n_block,
                ",".join(blocked_ids),
                report.format_for_ssh(),
            )
            self.audit_logger.log(
                operation="deploy",
                project=project_name,
                channel=channel.__class__.__name__,
                result="FAILED",
                duration_s=time.monotonic() - start,
                error=f"L1 pre-apply gate blocked ({n_block} violations: {','.join(blocked_ids)})",
            )
            return self._result(
                DeployStatus.FAILED,
                project_name,
                channel.__class__.__name__,
                error_info=(
                    f"[PRACTICES:BLOCK] L1 pre-apply gate blocked '{project_name}' "
                    f"({n_block} violations: {','.join(blocked_ids)}) — containers NOT started "
                    f"(REF-0006/C1)"
                ),
                duration_s=time.monotonic() - start,
            )
        logger.info("[IMP:9][DeployOrchestrator][l1-gate] PASS project=%s (l1_only)", project_name)
        return None

    # endregion FUNC__run_l1_pre_apply_gate

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
        metadata: dict[str, object],
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
    ##   - REF-0004: rollback-target = latest ЗДОРОВЫЙ snapshot (require_healthy=True,
    ##     WARN-fallback); engine уже откатил (rollback_performed=True) → второй откат
    ##     ЗАПРЕЩЁН (double rollback) → финализация ROLLED_BACK/FAILED по verified
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

        # ── REF-0004: якорь предыдущего образа ДО compose-up (для снапшота) ──
        # DI-resolver (тесты) приоритетен; без него значение придёт из engine-результата
        # (ServiceDeployResult.previous_image — engine сохраняет образ ДО pull, инвариант T1).
        self._last_engine_rollback_performed = False
        self._last_engine_rollback_verified = False
        self._last_engine_previous_image = ""  # свежесть: перезаписывается resolver'ом/engine ниже
        if self._previous_image_resolver is not None:
            try:
                self._last_engine_previous_image = self._previous_image_resolver(project_dir, service) or ""
            except (OSError, subprocess.SubprocessError) as e:
                logger.warning("[IMP:8][DeployOrchestrator][snapshot] previous-image resolve failed (non-fatal): %s", e)
                self._last_engine_previous_image = ""

        # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · compose-deploy через DI-шов (167 D3)
        # · Rejected: прямой вызов self._deploy_compose (docker compose up в unit-тестах — 76-152s)
        # · Reason: seam = тестируемость реального compose-вызова (тест передаёт compose_deployer=
        # ·   в конструктор вместо monkeypatch.setattr(DeployOrchestrator, "_deploy_compose"))
        # · Rev: если compose-deploy станет отдельным сервисом — deployer станет его инстансом
        compose_fn = self._compose_deployer if self._compose_deployer is not None else self._deploy_compose
        compose_ok = compose_fn(project_dir, service, version)
        if not compose_ok:
            payload_backup_dir = cast(str | None, payload.metadata.get("payload_backup_dir"))

            # ── REF-0004: engine уже выполнил compose-rollback при health-fail (внутренний
            #    perform_rollback + единственный re-verify). Второй откат запрещён — не
            #    дёргаем контейнеры повторно и не зависаем на doomed-pull (~135s ×5).
            if self._last_engine_rollback_performed:
                logger.info(
                    "[IMP:9][DeployOrchestrator][deploy] Engine already rolled back %s (verified=%s) — "
                    "skipping second rollback (REF-0004)",
                    project_name,
                    self._last_engine_rollback_verified,
                )
                return self._finalize_engine_rollback(
                    project_name, channel, version, service, project_dir, start, payload_backup_dir
                )

            # Rollback if previous deployment exists (цель — последний ЗДОРОВЫЙ релиз)
            snapshot = self.deploy_history.latest_snapshot(project_name, require_healthy=True)
            if snapshot:
                logger.info(
                    "[IMP:9][DeployOrchestrator][deploy] Compose failed — attempting rollback for %s",
                    project_name,
                )
                # T9.8 (L-6): payload-бэкап (предыдущие payload-файлы, снят ДО overwrite в
                # receive_flow) передаётся в rollback — восстанавливаются НЕ только compose/image.
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

    # region FUNC__finalize_engine_rollback
    ## @purpose  REF-0004: финализация деплоя, когда engine УЖЕ сам откатил контейнер
    ##           (ServiceDeployResult.rollback_performed). Честная запись в историю +
    ##           audit + результат; НИКАКОГО второго отката и повторного poll'а
    ##           (единственный re-verify уже сделал engine — rollback_verified).
    ## @io       ⇥ (project_name, channel, version, service, project_dir, start,
    ##              payload_backup_dir) → ⎋ OrchestratorDeployResult
    ## @complexity — O(F) — snapshot write + optional payload restore
    ## @invariants
    ##   - Snapshot создаётся с health_status="unhealthy" (честная история неудачного деплоя)
    ##   - Payload restore ТОЛЬКО при успешном engine-откате (rollback_verified=True)
    ##   - ROLLED_BACK ⇔ verified; иначе FAILED (∉ success — CI красный в обоих случаях)
    def _finalize_engine_rollback(
        self,
        project_name: str,
        channel: DeliveryChannel,
        version: str,
        service: str,
        project_dir: str,
        start: float,
        payload_backup_dir: str | None,
    ) -> OrchestratorDeployResult:
        """Finalize a deploy that the engine already rolled back internally (REF-0004)."""
        del service  # симметрия сигнатуры с _verify_deploy; re-check делал engine (rollback_verified)
        verified = bool(self._last_engine_rollback_verified)
        prev_id = self._last_engine_previous_image
        snapshot_id = self.deploy_history.create_snapshot(
            project=project_name,
            version=version,
            compose_state={"previous_image": prev_id} if prev_id else None,
            health_status="unhealthy",
            payload_backup_dir=payload_backup_dir,
        )
        # REF-0004: payload восстанавливается ТОЛЬКО после успешного compose-rollback —
        # engine-откат подтверждён verified (compose up --force-recreate + health OK).
        if verified and payload_backup_dir:
            self._restore_payload_files(payload_backup_dir, project_dir)
        status = DeployStatus.ROLLED_BACK if verified else DeployStatus.FAILED
        self.audit_logger.log(
            operation="deploy",
            project=project_name,
            channel=channel.__class__.__name__,
            result=status.value,
            duration_s=time.monotonic() - start,
            snapshot_id=snapshot_id,
            rollback_verified=verified,
        )
        error_info = "Healthcheck failed after deploy: rollback performed by engine" + (
            " and re-verified healthy" if verified else " but health re-verification failed"
        )
        logger.info(
            "[IMP:9][DeployOrchestrator][deploy] Engine-rollback finalized: %s → %s", project_name, status.value
        )
        return self._result(
            status,
            project_name,
            channel.__class__.__name__,
            error_info=error_info,
            duration_s=time.monotonic() - start,
            healthcheck_status="unhealthy",
            snapshot_id=snapshot_id,
            rollback_verified=verified,
        )

    # endregion FUNC__finalize_engine_rollback

    # region FUNC__verify_deploy
    ## @purpose  E2 deploy step 3 (VERIFY): healthcheck → snapshot → audit → final result.
    ##           REF-0004: при unhealthy/timeout — rollback-контур: snapshot-rollback на
    ##           последний ЗДОРОВЫЙ релиз (require_healthy) → payload после успеха compose →
    ##           ОДИН re-verify → ROLLED_BACK | FAILED.
    ## @io       ⇥ (deploy args + start) → ⎋ OrchestratorDeployResult
    ## @complexity — O(1) — poll + snapshot + audit (+ rollback contour на unhealthy)
    ## @invariants
    ##   - Healthcheck status "healthy" → DEPLOYED; unhealthy/timeout → FAILED или ROLLED_BACK
    ##     (REF-0003, DevPlan 11 W0: PARTIAL больше не эмитится на healthcheck-ветке;
    ##     REF-0004, DevPlan 11 В1: unhealthy ветка откатывает и верифицирует откат)
    ##   - Snapshot создаётся после healthcheck (содержит post-deploy health + якорь
    ##     compose_state.previous_image ДО compose-up — REF-0004)
    ##   - payload_backup_dir (T9.8) персистится в snapshot (rollback восстанавливает payload)
    def _verify_deploy(
        self,
        project_name: str,
        channel: DeliveryChannel,
        version: str,
        project_dir: str,
        start: float,
        payload_backup_dir: str | None = None,
        service: str = "",
    ) -> OrchestratorDeployResult:
        """Healthcheck + snapshot + audit + rollback contour on unhealthy (E2 step VERIFY).

        ``service`` — additive kwarg (REF-0004): имя compose-сервиса для rollback-контурa;
        пустое значение → фолбэк на project_name (канон D5).
        """
        health = self.healthcheck_poller.poll_until_healthy(project_name, project_dir)
        healthcheck_status = health.status

        total_duration = time.monotonic() - start

        # REF-0004: якорь предыдущего образа в снапшоте — без него compose-rollback обречён
        # пуллить локальный тег "previous-rollback" из GHCR (~135s ретраев ×5) и падать.
        prev_image_id = self._last_engine_previous_image
        snapshot_id = self.deploy_history.create_snapshot(
            project=project_name,
            version=version,
            compose_state={"previous_image": prev_image_id} if prev_image_id else None,
            health_status=healthcheck_status,
            payload_backup_dir=payload_backup_dir,
        )

        # ⚠️ TRAP[BUG] · 2026-08-24 · P0 · Unhealthy/timeout healthcheck давал PARTIAL=success → зелёный CI без алерта
        # · Symptom: сломанный образ обслуживается при зелёном CI/Telegram «deployed»; post-deploy chain поверх больного деплоя
        # · Root: fail-open swallowing — success-предикат шире health-факта (_verify_deploy мапил unhealthy/timeout → PARTIAL;
        # ·   is_success() включал PARTIAL → receive exit 0)
        # · Fix: unhealthy/timeout → DeployStatus.FAILED (+error_info с фактом healthcheck); PARTIAL исключён из is_success()
        # ·   (rollback.py); deliver-rc по {DEPLOYED, SKIPPED}; critical-notify на unhealthy-ветке receive (REF-0003)
        # · Prevention: DI-тест poller=unhealthy → rc≠0 (test_healthcheck_failed_rc.py) + severity-mapping тест (TEST-04);
        # ⚠️ TRAP[BUG] · 2026-08-24 · P1 · REF-0004 (DevPlan 11 В1) · Rollback-контур структурно сломан — ROLLED_BACK unreachable
        # · Symptom: unhealthy → FAILED+alert БЕЗ отката; снапшоты без previous_image → doomed GHCR-pull (~135s ×5);
        # ·   engine сам откатывал контейнер, оркестратор запускал второй откат поверх (double rollback)
        # · Root: create_snapshot никогда не заполнял compose_state; ответственность rollback разорвана между engine/orchestrator
        # · Fix: якорь previous_image в снапшоте; skip повторного отката при rollback_performed;
        # ·   latest_snapshot(require_healthy)+WARN; один wait_health re-verify → rollback_verified; ROLLED_BACK достижим
        # · Prevention: tests/unit/test_rollback_contour.py (TEST-03, characterization до правки)
        result_status = DeployStatus.DEPLOYED if healthcheck_status == "healthy" else DeployStatus.FAILED
        rollback_verified = False
        if result_status == DeployStatus.FAILED:
            # ── REF-0004 rollback contour ──
            result_status, error_info, rollback_verified = self._attempt_post_failure_rollback(
                project_name=project_name,
                channel=channel,
                service=service or project_name,
                project_dir=project_dir,
                current_snapshot_id=snapshot_id,
                payload_backup_dir=payload_backup_dir,
                healthcheck_status=healthcheck_status,
            )
        else:
            error_info = ""
        self.audit_logger.log(
            operation="deploy",
            project=project_name,
            channel=channel.__class__.__name__,
            result=result_status.value,
            duration_s=total_duration,
            snapshot_id=snapshot_id,
            rollback_verified=rollback_verified,
        )

        logger.info(
            "[IMP:9][DeployOrchestrator][deploy] DONE: %s → %s (%.1fs)",
            project_name,
            result_status.value,
            total_duration,
        )
        if result_status in {DeployStatus.FAILED, DeployStatus.ROLLED_BACK}:
            logger.error(
                "[IMP:10][DeployOrchestrator][deploy] Healthcheck FAILED for %s: status=%s detail=%s",
                project_name,
                healthcheck_status,
                health.detail or "n/a",
            )

        return self._result(
            result_status,
            project_name,
            channel.__class__.__name__,
            error_info=error_info or None,
            duration_s=total_duration,
            healthcheck_status=healthcheck_status,
            snapshot_id=snapshot_id,
            rollback_verified=rollback_verified,
            version=version,
        )

    # endregion FUNC__verify_deploy

    # region FUNC__attempt_post_failure_rollback
    ## @purpose  REF-0004: rollback-контур на unhealthy-ветке verify. Цель — последний
    ##           ЗДОРОВЫЙ снапшот (require_healthy=True, WARN-fallback; свежий больной
    ##           снапшот текущего деплоя исключается). compose-rollback → payload только
    ##           после успеха → ОДИН re-verify через poller.
    ## @io       ⇥ (project_name, channel, service, project_dir, current_snapshot_id,
    ##              payload_backup_dir, healthcheck_status) → ⎋ tuple[DeployStatus, str, bool]
    ## @complexity — O(F) + rollback lifecycle + один poll
    ## @invariants
    ##   - Свежесозданный снапшот текущего деплоя не может быть целью отката
    ##   - Payload restore ТОЛЬКО после успешного compose-rollback
    ##   - РОВНО ОДИН re-verify (никаких повторных poll'ов по контуру)
    ##   - ROLLED_BACK ⇔ compose-rollback выполнен И re-verify healthy; иначе FAILED
    def _attempt_post_failure_rollback(
        self,
        project_name: str,
        channel: DeliveryChannel,
        service: str,
        project_dir: str,
        *,
        current_snapshot_id: str,
        payload_backup_dir: str | None,
        healthcheck_status: str,
    ) -> tuple[DeployStatus, str, bool]:
        """Attempt snapshot rollback after unhealthy healthcheck; returns (status, error_info, verified)."""
        del channel
        target = self.deploy_history.latest_snapshot(project_name, require_healthy=True)
        if not target or target.get("snapshot_id") == current_snapshot_id:
            logger.warning(
                "[IMP:8][DeployOrchestrator][rollback] No prior healthy snapshot for %s — rollback unavailable",
                project_name,
            )
            return (
                DeployStatus.FAILED,
                f"Healthcheck failed after deploy: status={healthcheck_status}; no healthy snapshot to roll back to",
                False,
            )

        logger.info(
            "[IMP:9][DeployOrchestrator][rollback] Healthcheck failed for %s — rolling back to snapshot %s (REF-0004)",
            project_name,
            target.get("snapshot_id"),
        )
        rollback_fn = self._compose_rollback if self._compose_rollback is not None else self._rollback_compose
        rollback_ok = rollback_fn(project_dir, service, target)
        if not rollback_ok:
            return (
                DeployStatus.FAILED,
                f"Healthcheck failed after deploy: status={healthcheck_status}; Rollback failed",
                False,
            )

        # Payload restore ТОЛЬКО после успешного compose-rollback (консистентность disk ↔ containers)
        if payload_backup_dir:
            restored = self._restore_payload_files(payload_backup_dir, project_dir)
            if restored:
                logger.info(
                    "[IMP:9][DeployOrchestrator][rollback] Payload files restored after successful rollback "
                    "(T9.8/REF-0004)"
                )

        # ── РОВНО ОДИН re-verify после отката ──
        recheck = self.healthcheck_poller.poll_until_healthy(project_name, project_dir)
        if recheck.status == "healthy":
            logger.info("[IMP:9][DeployOrchestrator][rollback] Re-verified HEALTHY after rollback for %s", project_name)
            return (
                DeployStatus.ROLLED_BACK,
                (
                    f"Healthcheck failed ({healthcheck_status}); rolled back to snapshot "
                    f"{target.get('snapshot_id')} and re-verified healthy"
                ),
                True,
            )
        logger.error(
            "[IMP:10][DeployOrchestrator][rollback] Rollback performed but healthcheck still failing for %s "
            "(re-check=%s)",
            project_name,
            recheck.status,
        )
        return (
            DeployStatus.FAILED,
            (
                f"Healthcheck failed ({healthcheck_status}); Rollback performed but re-verification "
                f"unhealthy ({recheck.status})"
            ),
            False,
        )

    # endregion FUNC__attempt_post_failure_rollback

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
        failed = sum(1 for r in results if r.status in {DeployStatus.FAILED, DeployStatus.ROLLED_BACK})
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
    ##           170 W10-B: фасад инжектит DeployOrchestrator-фабрику в ReceiveFlow (конструкторный
    ##           DI) — receive_flow больше не импортирует orchestrator (цикл разорван).
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
    ##   - ReceiveFlow получает orchestrator_factory (DI, 170 W10-B): явная фабрика или
    ##     дефолт `lambda base: DeployOrchestrator(projects_base=base)` — поведение без изменений
    def receive(
        self,
        project_name: str | None = None,
        version: str = "latest",
        *,
        stream: BinaryIO | None = None,  # stdin-канал (orchestrator_cli передаёт stdin_stream: BinaryIO | None)
        orchestrator_factory: Callable[[str], DeployOrchestrator] | None = None,
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
            stream: stdin-канал DI (W-H DevPlan 163) — io.BytesIO в тестах вместо патча sys.stdin;
                None = sys.stdin.buffer (поведение без изменений).
            orchestrator_factory: фабрика DeployOrchestrator (DI, W-H / 170 W10-B) — тесты
                инжектят субкласс-фабрику; None → дефолт DeployOrchestrator (инжектится фасадом,
                receive_flow сам DeployOrchestrator не импортирует).

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
        # /opt/projects) — receive() семантика: env на момент вызова, не import-константа.
        # W-H (DevPlan 163): self.projects_base (конструкторный DI) пробрасывается в ReceiveFlow,
        # когда задан — тесты передают projects_base= вместо monkeypatch.setenv(PROJECTS_BASE);
        # None → env-резолв (поведение по умолчанию неизменно).
        # 170 W10-B: дефолтная фабрика инжектится ЗДЕСЬ (receive_flow → orchestrator импорт удалён).
        flow = ReceiveFlow(
            projects_base=self.projects_base,
            orchestrator_factory=orchestrator_factory or (lambda base: DeployOrchestrator(projects_base=base)),
        )
        return flow.run(
            project_name=project_name,
            version=version,
            stream=stream,
        )

    # endregion FUNC_receive

    # region FUNC__run_post_deploy_chain
    ## @purpose  Best-effort post-deploy chain (DevPlan 116 B1 T2/D4, U-24): notify-hook (Telegram)
    ##           + generate-catalog (regen catalog.json) + monitoring reconfig (DevPlan 138 W3)
    ##           + module deploy-hooks (B8 wire).
    ##           Все неблокирующие: сбой → WARN, деплой НЕ фейлится (дизайн notify-hook always exit 0).
    ##           170 W4-B3: РЕАЛИЗАЦИЯ вынесена в deploy/hooks/post_deploy_chain.py
    ##           (run_post_deploy_chain, 4 handler-подшага). Тонкий делегат сохраняет
    ##           сигнатуру метода — тесты и ReceiveFlow (receive_flow.py:385) вызывают
    ##           _run_post_deploy_chain на (суб)классе DeployOrchestrator.
    ## @io       ⇥ project: str, version: str, status: str, project_dir: str, node_name: str → ⎋ None
    ## @complexity — O(1) — делегирование в run_post_deploy_chain (hooks/)
    ##
    ## @invariants
    ##   - Вызывается ТОЛЬКО после успешного деплоя (DEPLOYED/PARTIAL)
    ##   - notify-hook timeout 30s, generate-catalog timeout 60s, module deploy-hook COMPOSE_UP_TIMEOUT
    ##   - Сбой цепочки → logger.warning (IMP:8), не raise
    ##   - module deploy-hooks (module.yaml hooks.on_project_deploy) вызываются
    ##     через shared/module_interface.invoke (registry-driven)
    ##   - monitoring reconfig (run_monitoring_reconfig, lazy-import) — ПОСЛЕ
    ##     generate-catalog, ДО deploy-hooks; WARN non-fatal (R5)
    ##   - DI (W-H DevPlan 163): run_cmd=None → subprocess.run; platform_root_override=None →
    ##     platform_remote_base(); reconfig_fn=None → lazy run_monitoring_reconfig (канон)
    # ruff: ignore[PLR6301]  # метод-контракт: не использует self, но вызывается как инстанс-метод
    # (ReceiveFlow receive_flow.py:385 + тесты-субклассы переопределяют/вызывают super()) — консистентность API
    def _run_post_deploy_chain(
        self,
        project: str,
        version: str,
        status: str,
        project_dir: str | None = None,
        node_name: str = "",
        *,
        run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        platform_root_override: str | None = None,
        reconfig_fn: Callable[..., object] | None = None,
    ) -> None:
        """Run notify-hook + generate-catalog + monitoring reconfig + module deploy-hooks (best-effort, D4).

        ▶ ┌project/version/status┐ → run_post_deploy_chain (deploy/hooks/, 4 подшага) → ⎋ None
        """
        # 🧐 TRAP[DECISION] · 2026-08-15 · — · Делегат _run_post_deploy_chain сохранён (B3)
        # · Rejected: прямое удаление метода после выноса в deploy/hooks/post_deploy_chain.py
        # · Reason: deferred — ReceiveFlow (receive_flow.py:385) и тесты-субклассы (test_orchestrator_
        # ·   receive_version, test_receive_flow, test_orchestrator_cli_dispatch) вызывают приватный
        # ·   метод на (суб)классе DeployOrchestrator; перевод на публичный run_post_deploy_chain
        # ·   требует правки receive_flow.py — боевой CI-путь, последний в очереди декомпозиции (B3)
        # · Rev: после декомпозиции receive_flow.py — удалить делегат, обновить вызовы на hooks API
        run_post_deploy_chain(
            project,
            version,
            status,
            project_dir=project_dir,
            node_name=node_name,
            run_cmd=run_cmd,
            platform_root_override=platform_root_override,
            reconfig_fn=reconfig_fn,
        )

    # endregion FUNC__run_post_deploy_chain

    # ── Internal helpers ──────────────────────────────────────────────────

    def _assemble_payload(
        self,
        project_name: str,
        version: str,
        project_dir: str,
        metadata: dict[str, object],
    ) -> Payload:
        """Assemble a deploy payload from project files.

        Делегирование в PayloadDeliverer.assemble_payload (единственный путь сборки tar.gz).
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

        REF-0004: реальный путь пишет сигнал-канал (rollback_performed/rollback_verified/
        previous_image из ServiceDeployResult) — читается _apply_deploy/_verify_deploy.
        DI-шов (тесты) этот метод не вызывает — сигналы остаются False/"".

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            version: Image tag/version.

        Returns:
            True if compose up succeeded.
        """
        try:
            engine = DeployEngine(projects_base=self.projects_base)
            result = engine.deploy(
                project=Path(project_dir).name,
                ref=version,
                service=service,
                project_dir=project_dir,
            )
        except PlatformError as e:
            # T3.1 (DevPlan 116 B4): _handle_first_deploy → PlatformFatalError вместо SystemExit
            self._last_engine_rollback_performed = False
            self._last_engine_rollback_verified = False
            self._last_engine_previous_image = ""
            logger.error(
                "[IMP:10][DeployOrchestrator][deploy_compose] Deploy engine error (exit=%d): %s", e.exit_code, e
            )
            return False
        except (OSError, subprocess.SubprocessError) as e:
            self._last_engine_rollback_performed = False
            self._last_engine_rollback_verified = False
            self._last_engine_previous_image = ""
            logger.error("[IMP:10][DeployOrchestrator][deploy_compose] Failed: %s", e)
            return False
        else:
            self._last_engine_previous_image = result.previous_image or ""
            self._last_engine_rollback_performed = bool(getattr(result, "rollback_performed", False))
            self._last_engine_rollback_verified = bool(getattr(result, "rollback_verified", False))
            return result.success

    @staticmethod
    def _result(
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
        rollback_verified: bool = False,
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
            rollback_verified: REF-0004 — факт единственного re-verify после отката.

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
            rollback_verified=rollback_verified,
        )


# endregion CLASS_DeployOrchestrator
