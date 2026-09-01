"""
RollbackMixin — rollback-кластер DeployOrchestrator (snapshot/payload/compose restore).
T3.1: вынесен из deploy/orchestrator.py (механический перенос, 1:1) — тела методов,
LDD-логи и region-маркеры сохранены; DeployStatus/OrchestratorDeployResult — типы
деплой-статусов, владельцем которых стал rollback-модуль (re-export из orchestrator).
"""
# GREP_SUMMARY: deploy-rollback, rollback-mixin, snapshot, payload-restore, compose-rollback, deploy-status, deploy-result, previous-image-anchor, rollback-fact, T3.1
# STRUCTURE: ▶ RollbackMixin(_rollback_deploy|rollback|_restore_payload_files|_rollback_compose)
#            → ┌DeployHistory snapshot (latest|by-id)┐ → ┌_restore_payload_files (T9.8, до compose)┐
#            → ┌_rollback_compose (previous_image re-tag → deploy)┐ → ┌AuditLogger┐ → ⎋ OrchestratorDeployResult
# region MODULE_CONTRACT
## @purpose  Snapshot-based rollback of a deployed project: restore payload files (T9.8)
##           + compose from DeployHistory snapshot, plus the shared deploy status/result types.
##           RollbackMixin — mixin-кластер: DeployOrchestrator наследует его, публичный API
##           orchestrator (rollback/_rollback_deploy/_restore_payload_files/_rollback_compose)
##           сохраняется без изменений (старые имена живут через наследование + re-export).
## @scope    Пакет deploy/ (T3.1) — вынесен из deploy/orchestrator.py (1220 LOC → ~1030 + rollback.py).
##           Методы полагаются на state из DeployOrchestrator.__init__: projects_base,
##           deploy_history, audit_logger, _compose_rollback (DI-шов), _result (статический билдер).
## @invariants
##   1. rollback() — восстанавливает compose_state из snapshot (latest или по snapshot_id)
##   2. payload-файлы восстанавливаются ТОЛЬКО после успешного compose-rollback (REF-0004):
##      (а) deploy-failure — из payload_backup_dir (бэкап снят ДО overwrite в receive_flow),
##      (б) manual rollback — из snapshot payload_dir (T9.8, L-6)
##   3. _rollback_compose — previous_image re-tag → docker compose deploy c skip_pull
##      (doomed GHCR-pull устранён — REF-0004; W1: docker tag через shared/docker_ops);
##      F-11 (2026-08-27): re-tag на compose-RESOLVED ref (docker compose config --images
##      при IMAGE_TAG=previous-rollback) + skip_pull → --pull never в compose up (engine);
##      env-цепочка config/up — project_compose_env_args (secrets.env + .env.platform);
##      D8 (2026-09-01): отсутствие compose_state.previous_image → честный fail-fast ДО
##      engine.deploy; docker_tag-результат обязателен (False → FAIL до compose up);
##      PlatformError/OSError/SubprocessError → False
##   7. rollback() успешный → НОВЫЙ снапшот-факт (rollback=True + health_status=healthy +
##      compose_state.previous_image — якорь следующего отката); `status` CLI показывает
##      факт rollback (история не врёт); result_status остаётся DEPLOYED (rc=0 CLI-контракт)
##   4. _restore_payload_files — не-fatal: сбой restore НЕ блокирует compose-rollback
##   5. DeployStatus/OrchestratorDeployResult — единые типы статусов всех deploy-операций;
##      импортируются из rollback.py другими модулями напрямую или через re-export
##      orchestrator (backward-compat: from core.internal.deploy.orchestrator import DeployStatus)
##   6. Rollback успешен → ROLLED_BACK (deploy-failure, при rollback_verified) / DEPLOYED
##      (manual), иначе FAILED; OrchestratorDeployResult.rollback_verified (additive) несёт
##      факт единственного wait_health re-verify после отката
## @rationale DevPlan 089 — единый typed фасад (устраняет 6+ параллельных deploy-путей);
##            170 W4-B3 — декомпозиция orchestrator (audit/hooks вынесены ранее);
##            T3.1 — rollback-кластер (~230 LOC) извлекается как отделимый: самодостаточен
##            по state, единственный потребитель DeployStatus.ROLLED_BACK. Сплит НЕ меняет
##            поведение — тела методов перенесены 1:1, направление импорта orchestrator → rollback
##            (без цикла: rollback НЕ импортирует orchestrator в runtime).
## @changes 2026-08-22 | T3.1 — extracted from deploy/orchestrator.py (1:1, механический перенос)
## @changes 2026-08-24 | REF-0004 (DevPlan 11 В1) — skip_pull при previous-rollback; payload
##             restore только после успешного compose-rollback; поле rollback_verified (additive)
## @changes 2026-08-27 | F-11 (P1, rollback dance-site) — re-tag на compose-resolved ref
##             (docker compose config --images при IMAGE_TAG=previous-rollback) — bare-тег не
##             совпадал с ${REGISTRY}/${ORG}/${PROJECT}:${IMAGE_TAG} → compose up пуллил doomed
##             ref; skip_pull доведён до compose up (--pull never); env-цепочка
##             project_compose_env_args для config/up
## @changes 2026-09-01 | D8 (внешний rollback tronyx-site) — fail-fast без previous_image-якоря
##             (doomed «No such image: ...:previous-rollback» up + маскирующий внутренний
##             atomic_up-rollback устранены); docker_tag-результат обязателен (False → FAIL до
##             compose up); успешный rollback пишет снапшот-факт (rollback=True) — `status` CLI
##             показывает ROLLED_BACK-факт вместо врущей записи неудачного деплоя
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from core.internal.deploy.deploy_engine import DeployEngine
from core.internal.shared import docker_ops  # W1: docker tag примитив (гейт docker_sole_path)
from core.internal.shared.deploy_paths import project_compose_env_args
from core.internal.shared.docker_compose import docker_compose_config
from core.internal.shared.exceptions import PlatformError

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.internal.shared.audit import DeployAuditLogger

    from core.internal.deploy.channels import DeliveryChannel
    from core.internal.deploy.deploy_history import DeployHistory

logger = logging.getLogger(__name__)


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
    # Version pinning: sha из аргументов SSH-команды (receive \<project\> \<sha\>);
    # version = sha из CI (без чтения ai-platform.yaml).
    version: str = ""
    # REF-0004 (DevPlan 11 В1, additive — wire-freeze п.2 соблюдён): результат единственного
    # wait_health re-verify после отката. True ⇔ compose-rollback выполнен И health восстановлен.
    rollback_verified: bool = False

    def to_dict(self) -> dict[str, object]:
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
            # REF-0004: additive ключ — потребители с фикс. схемой игнорируют неизвестные поля
            "rollback_verified": self.rollback_verified,
        }

    def is_success(self) -> bool:
        """Returns True only for genuinely successful outcomes.

        REF-0003 (DevPlan 11 W0): PARTIAL исключён из success-предиката — внутренний
        статус, никогда не success (root cause «best-effort swallowing»: success-предикат
        был шире health-факта → зелёный CI при больном деплое). SKIPPED остаётся
        success (dry-run plan контракт, DevPlan 089 AC10).
        """
        return self.status in {DeployStatus.DEPLOYED, DeployStatus.SKIPPED}


# endregion ENUMS & DATACLASSES


# region CLASS_RollbackMixin


class RollbackMixin:
    """Snapshot-based rollback cluster extracted from DeployOrchestrator (T3.1).

    ## @purpose — Provide rollback()/_rollback_deploy()/_restore_payload_files()/_rollback_compose()
    ##            as mixin methods; DeployOrchestrator(RollbackMixin) наследует их, старый API жив.
    ## @io — ⇥ project_name/snapshot_id → ⎋ OrchestratorDeployResult
    ## @complexity — O(F + M) где F = payload-файлов, M = rollback lifecycle (compose re-tag + deploy)
    ## @invariants
    ##   - DeployHistory.rollback() — источник snapshot (latest или по id)
    ##   - Payload restore ДО compose-rollback (T9.8)
    ##   - Лог-префикс [DeployOrchestrator][rollback] сохранён (LDD-телеметрия не меняется)
    """

    # ── Mixin-контракт: атрибуты/статика хост-класса DeployOrchestrator (инициализация
    #    и def в orchestrator.py). Аннотации-декларации для basedpyright; значений
    #    миксин НЕ задаёт (прецедент W11: pyright: ignore[reportUninitializedInstanceVariable]).
    projects_base: str  # pyright: ignore[reportUninitializedInstanceVariable] — mixin-декларация (host __init__)
    audit_logger: DeployAuditLogger  # pyright: ignore[reportUninitializedInstanceVariable]
    deploy_history: DeployHistory  # pyright: ignore[reportUninitializedInstanceVariable]
    _compose_rollback: Callable[[str, str, dict[str, object]], bool] | None  # pyright: ignore[reportUninitializedInstanceVariable]
    # _result — @staticmethod ФАБРИКА на хост-классе (orchestrator.py:926), вызывается self._result(...)
    _result: Callable[..., OrchestratorDeployResult]  # pyright: ignore[reportUninitializedInstanceVariable]

    # region FUNC__rollback_deploy
    ## @purpose  E2 deploy step 4 (ROLLBACK): compose from snapshot + restore payload files (T9.8)
    ##           after failed apply.
    ## @io       ⇥ (project, channel, service, project_dir, snapshot, start, payload_backup_dir) → ⎋ OrchestratorDeployResult
    ## @complexity — O(F + 1) — F payload-файлов + rollback compose + audit
    ## @invariants
    ##   - REF-0004: compose-rollback СНАЧАЛА; payload-файлы восстанавливаются ТОЛЬКО после
    ##     успешного compose-rollback — иначе disk (старый payload) разошёлся бы с контейнерами
    ##     (новый образ), если compose-rollback упал
    ##   - Rollback успешен → ROLLED_BACK, иначе FAILED
    def _rollback_deploy(
        self,
        project_name: str,
        channel: DeliveryChannel,
        service: str,
        project_dir: str,
        snapshot: dict[str, object],
        start: float,
        payload_backup_dir: str | None = None,
    ) -> OrchestratorDeployResult:
        """Rollback payload + compose after failed deploy (E2 step ROLLBACK)."""
        rollback_fn = self._compose_rollback if self._compose_rollback is not None else self._rollback_compose
        rollback_ok = rollback_fn(project_dir, service, snapshot)
        # T9.8 (L-6) + REF-0004: payload-бэкап (предыдущие payload-файлы, снят ДО overwrite в
        # receive_flow) восстанавливается ТОЛЬКО после успешного compose-rollback — контейнеры
        # и disk остаются консистентными (старый образ ↔ старый payload).
        if rollback_ok and payload_backup_dir:
            restored = self._restore_payload_files(payload_backup_dir, project_dir)
            if restored:
                logger.info(
                    "[IMP:9][DeployOrchestrator][rollback] Payload files restored from backup %s after "
                    "successful compose-rollback (T9.8/REF-0004)",
                    payload_backup_dir,
                )
        elif payload_backup_dir and not rollback_ok:
            logger.warning(
                "[IMP:8][DeployOrchestrator][rollback] Compose rollback FAILED — payload files NOT restored "
                "(kept consistent with running containers; REF-0004)",
            )
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

    # region FUNC_rollback
    ## @purpose  Rollback a project to a previous snapshot.
    ## @io       ⇥ project_name: str, snapshot_id: str | None → ⎋ OrchestratorDeployResult
    ## @complexity — O(M) where M = rollback lifecycle
    ## @invariants
    ##   - If snapshot_id is None, uses latest snapshot
    ##   - Rollback restores compose_state from snapshot
    ##   - No rollback possible if no snapshots exist
    ##   - D8 (2026-09-01): успешный rollback пишет НОВЫЙ снапшот-факт (rollback=True +
    ##     health_status=healthy + compose_state.previous_image — якорь следующего отката);
    ##     `status` CLI (latest_snapshot) показывает факт rollback вместо врущей записи
    ##   - result_status остаётся DEPLOYED при успехе (rc=0 контракт CLI rollback);
    ##     ROLLED_BACK-факт живёт в снапшоте, не в is_success()-статусе
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

        rollback_fn = self._compose_rollback if self._compose_rollback is not None else self._rollback_compose
        rollback_ok = rollback_fn(project_dir, service, snapshot)

        # T9.8 (L-6) + REF-0004: payload-файлы из snapshot (payload_dir — пред-деплойный бэкап)
        # восстанавливаются ТОЛЬКО после успешного compose-rollback — disk не должен опережать
        # контейнеры (сломанный payload заменяется рабочим при живом старом образе).
        snapshot_payload_dir = cast(str | None, snapshot.get("payload_dir"))
        if rollback_ok and snapshot_payload_dir and os.path.isdir(snapshot_payload_dir):
            restored = self._restore_payload_files(snapshot_payload_dir, project_dir)
            if restored:
                logger.info(
                    "[IMP:9][DeployOrchestrator][rollback] Payload files restored from snapshot %s "
                    "(T9.8, post-compose)",
                    snapshot.get("snapshot_id"),
                )

        # ── История (D8, 2026-09-01): успешный rollback пишет снапшот-факт ──
        # Внешний/ручной rollback раньше НЕ трогал DeployHistory → `status` CLI показывал
        # last_deploy = снапшот неудачного деплоя (история врала: «last_deploy остался на новом
        # снапшоте»). Контракт inner-rollback (_finalize_engine_rollback): пишет снапшот с
        # compose_state.previous_image — якорь следующего отката. Здесь то же + rollback-маркер
        # (rollback=True, rollback_from_snapshot): история/status видят ФАКТ отката, а не врущую
        # запись. result_status остаётся DEPLOYED (rc=0 контракт CLI rollback); ROLLED_BACK-факт
        # несёт снапшот, НЕ is_success()-статус (ROLLED_BACK ∉ success — был бы rc=1 на успехе).
        rollback_snapshot_id: str | None = None
        if rollback_ok:
            compose_state = snapshot.get("compose_state")
            prev_image_id = compose_state.get("previous_image") if isinstance(compose_state, dict) else None
            rollback_snapshot_id = self.deploy_history.create_snapshot(
                project=project_name,
                version=cast(str, snapshot.get("version", "")),
                compose_state={"previous_image": prev_image_id} if prev_image_id else None,
                health_status="healthy",
                rollback=True,
                rollback_from_snapshot=cast(str, snapshot.get("snapshot_id", "")),
            )
            logger.info(
                "[IMP:9][DeployOrchestrator][rollback] History updated: rollback-fact snapshot %s for %s "
                "(status CLI now shows ROLLED_BACK fact)",
                rollback_snapshot_id,
                project_name,
            )

        # Audit — snapshot_id = снапшот-факт отката (при успехе) / источник (при FAILED)
        audit_snapshot_id = rollback_snapshot_id or cast(str | None, snapshot.get("snapshot_id", snapshot_id))
        result_status = DeployStatus.DEPLOYED if rollback_ok else DeployStatus.FAILED
        self.audit_logger.log(
            operation="rollback",
            project=project_name,
            result=result_status.value,
            duration_s=time.monotonic() - start,
            snapshot_id=audit_snapshot_id,
        )

        return self._result(
            result_status,
            project_name,
            error_info="" if rollback_ok else f"Rollback failed for {project_name}",
            duration_s=time.monotonic() - start,
            snapshot_id=audit_snapshot_id,
        )

    # endregion FUNC_rollback

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
    @staticmethod
    def _restore_payload_files(backup_dir: str, target_dir: str) -> bool:
        """Copy payload files from a backup dir into the project dir (T9.8)."""
        # ruff: ignore[PLW0717] — внутри try есть break/continue/await/yield — извлечение ломает управляющий поток
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
        except OSError as e:
            logger.error("[IMP:10][DeployOrchestrator][restore_payload] Payload restore failed: %s", e)
            return False
        else:
            return True

    # endregion FUNC__restore_payload_files

    # region FUNC__rollback_compose
    ## @purpose  Rollback compose to a previous snapshot state.
    ## @io       ⇥ project_dir: str, service: str, snapshot: dict[str, object] → ⎋ bool
    ## @complexity — O(1) — config-resolve (--images) + docker tag + docker compose deploy
    ## @invariants
    ##   - previous_image из compose_state re-tag → docker compose deploy (skip_pull — REF-0004:
    ##     образ уже локально перетегирован; registry-pull локального тега обречён ~135s ×5)
    ##   - F-11 (2026-08-27): target re-tag = compose-RESOLVED ref (docker compose config --images
    ##     при IMAGE_TAG=previous-rollback), НЕ bare `<service>:previous-rollback` — bare-тег не
    ##     совпадает с ${REGISTRY}/${ORG}/${PROJECT}:${IMAGE_TAG} → compose up пуллил doomed ref;
    ##     skip_pull доводится до compose up (--pull never, engine up_atomic pull_never)
    ##   - D8 (2026-09-01): отсутствие compose_state.previous_image → честный fail-fast ДО
    ##     engine.deploy (doomed «No such image: ...:previous-rollback» up + маскирующий
    ##     внутренний atomic_up-rollback устранены); docker_tag-результат ОБЯЗАТЕЛЕН — сбой
    ##     tag'а = FAIL до compose up (образ цели не локальный)
    ##   - compose config/env-chain: тот же env-набор (secrets.env + .env.platform) через
    ##     project_compose_env_args (F-11) — интерполяция config/up идентична продовому пути
    ##   - PlatformError/OSError/SubprocessError → False (audit пишет FAILED в _rollback_deploy)
    def _rollback_compose(self, project_dir: str, service: str, snapshot: dict[str, object]) -> bool:
        """Rollback compose to a previous snapshot state.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            snapshot: Snapshot data with compose_state.

        Returns:
            True if rollback succeeded.
        """
        # ruff: ignore[PLW0717] — try содержит каст snapshot + docker_tag + deploy (6 операторов);
        # извлечение engine/compose_state в хелпер разорвало бы консистентность except-обработки
        try:
            engine = DeployEngine(projects_base=self.projects_base)
            compose_state = snapshot.get("compose_state")
            # compose_state — dict-секция snapshot (object-граница, W11)
            prev_image_id: object = compose_state.get("previous_image") if isinstance(compose_state, dict) else None

            # ── D8 (2026-09-01): якорь previous_image ОБЯЗАТЕЛЕН — честный fail-fast вместо doomed up ──
            # Без якоря docker tag невозможен → compose up с ref=previous-rollback обречён
            # («No such image: ...:previous-rollback» — тега нет ни локально, ни в registry).
            # engine.deploy с ref=previous-rollback НЕ вызывается: раньше up-fail маскировался
            # внутренним atomic_up-rollback (тегирует ТЕКУЩИЙ образ → :latest → healthy →
            # «Rollback complete»), но вердикт был FAILED + история не тронута (D8).
            #
            # ⚠️ TRAP[BUG] · 2026-09-01 · P1 · D8 — внешний rollback без якоря → doomed up + маскирующий
            # · Symptom: orchestrator_cli rollback --project tronyx-site → «No such image:
            # ·   ghcr.io/tronyxlab/tronyx-site:previous-rollback» → up-fail → внутренний
            # ·   atomic_up-rollback (retag ТЕКУЩЕГО previous_image b08334da → :latest → up →
            # ·   healthy → «Rollback complete») восстановил контейнер, НО CLI вернул status=FAILED
            # ·   и snapshot-история не отразила откат (last_deploy остался на новом снапшоте).
            # · Root: (1) снапшот-цель без compose_state.previous_image → docker_tag пропущен →
            # ·   engine.deploy(ref="previous-rollback") с up --pull never падает «No such image»
            # ·   (тег не существует локально и не создаётся); (2) результат docker_tag в
            # ·   _rollback_compose игнорировался (non-fatal по контракту docker_ops) — даже при
            # ·   не-локальном образе цели up всё равно вызывался; (3) rollback() не писал историю.
            # · Fix: (1) отсутствие previous_image → честный fail-fast ДО engine.deploy (IMP:10);
            # ·   (2) docker_tag-результат обязателен — False → FAIL до compose up; (3) успешный
            # ·   rollback пишет снапшот-факт (rollback=True, ROLLED_BACK виден в `status`).
            # · Prevention: test_rollback_contour::test_rollback_compose_fails_fast_without_previous_image_anchor
            # ·   + test_rollback_compose_tags_previous_image_before_up_and_skips_pull +
            # ·   test_rollback_compose_fails_when_docker_tag_fails +
            # ·   test_rollback_writes_history_snapshot_with_rollback_fact
            if not prev_image_id:
                logger.error(
                    "[IMP:10][DeployOrchestrator][rollback_compose] Snapshot %s has no compose_state."
                    "previous_image — rollback target image unavailable; refusing doomed compose up "
                    "(No such image: %s:previous-rollback scenario eliminated)",
                    snapshot.get("snapshot_id", "<unknown>"),
                    service,
                )
                return False

            # Re-tag and restart (W1: docker tag — shared/docker_ops)
            # ⚠️ TRAP[BUG] · 2026-08-27 · P1 · F-11 — bare re-tag ≠ compose-resolved ref → doomed pull
            # · Symptom: ручной rollback dance-site падал «Up failed (exit=1): ... Image
            # ·   ghcr.io/tronyxlab/dance-site:previous-rollback Pulling» + REDIS_PASSWORD not set →
            # ·   rollback FAILED, хотя внутренний engine-rollback восстановил контейнер healthy.
            # · Root: (1) re-tag целился в bare `dance-site:previous-rollback`, а compose резолвит
            # ·   `${IMAGE_REGISTRY:-ghcr.io}/${ORG}/${PROJECT}:${IMAGE_TAG}` → `ghcr.io/...:previous-rollback` —
            # ·   тег НЕ существует локально → compose up ИМПЛИЦИТНО пуллил его из registry (тега там
            # ·   нет по определению) → rc=1; skip_pull пропускал только явный pull-шаг (engine),
            # ·   не up-пулл; (2) compose-интерполяция `${REDIS_PASSWORD}` шла без --env-file
            # ·   secrets.env (ручной CLI от root без sourced секретов).
            # · Fix: (1) target re-tag = compose-resolved ref (docker compose config --images при
            # ·   IMAGE_TAG=previous-rollback) + skip_pull → `docker compose up --pull never`
            # ·   (engine up_atomic pull_never) — registry НЕ трогается; (2) единая env-цепочка
            # ·   project_compose_env_args (secrets.env + .env.platform) для config/up/pull.
            # · Prevention: test_rollback_contour::test_rollback_compose_retags_to_compose_resolved_ref +
            # ·   test_rollback_contour::test_deploy_up_receives_env_file_args_and_pull_never
            target_ref = f"{service}:previous-rollback"
            cfg = docker_compose_config(
                project_dir,
                flags=["--images", service],
                compose_args=project_compose_env_args(project_dir),
                env_override={"IMAGE_TAG": "previous-rollback"},
            )
            cfg_stdout = cfg.stdout
            if isinstance(cfg_stdout, bytes):
                cfg_stdout = cfg_stdout.decode("utf-8", errors="replace")
            resolved = [ln.strip() for ln in (cfg_stdout or "").splitlines() if ln.strip()]
            if resolved:
                target_ref = resolved[0]
                logger.info(
                    "[IMP:8][DeployOrchestrator][rollback_compose] Compose-resolved ref for "
                    "IMAGE_TAG=previous-rollback: %s",
                    target_ref,
                )
            else:
                logger.warning(
                    "[IMP:7][DeployOrchestrator][rollback_compose] compose config --images resolved no "
                    "ref — fallback bare tag %s (compose up may pull)",
                    target_ref,
                )

            # ── D8 (2026-09-01): docker tag ПЕРЕД compose up — ПРЕДУСЛОВИЕ up ──
            # docker_tag non-fatal по контракту docker_ops (caller решает severity), НО здесь это
            # ЕДИНСТВЕННЫЙ создатель локального ref, который compose up резолвит при
            # IMAGE_TAG=previous-rollback: сбой tag'а = образ цели НЕ локальный (pruned/чужой
            # ноды) → честный FAIL до compose up (иначе up с --pull never падает
            # «No such image: ...:previous-rollback» — ровно D8-сценарий). Голое от pull
            # окружение не трогает registry: образ уже на ноде (якорь), tag лишь добавляет ref.
            #
            # 🧐 TRAP[DECISION] · 2026-09-01 · — · Внешний rollback = docker tag prev → compose-resolved
            # :previous-rollback + up (IMAGE_TAG=previous-rollback, --pull never) · Rejected: inner-механика
            # perform_rollback (retag prev → :latest + up без IMAGE_TAG-оверрайда) · Reason: точный
            # ref-pin (без :latest-двусмысленности — TRAP[DEBT] lifecycle.py:117), pull-free на голом
            # окружении (--pull never), F-11-проверен; inner-путь оставлен engine-внутренним (работает) ·
            # Rev: если perform_rollback начнёт принимать IMAGE_TAG-оверрайд — свести к одному контуру
            if not docker_ops.docker_tag(str(prev_image_id), target_ref):
                logger.error(
                    "[IMP:10][DeployOrchestrator][rollback_compose] docker tag %s → %s FAILED — rollback "
                    "target image not local; aborting before compose up",
                    prev_image_id,
                    target_ref,
                )
                return False
            logger.info(
                "[IMP:9][DeployOrchestrator][rollback_compose] Previous image %s tagged → %s (before compose up)",
                prev_image_id,
                target_ref,
            )

            # ⚠️ TRAP[BUG] · 2026-08-24 · P1 · REF-0004 · Doomed GHCR-pull при rollback (~135s ×5)
            # · Symptom: health-fail → rollback висел ~2.5 мин (retry_pull 5 попыток) и падал
            # ·   PlatformFatalError → FAILED, хотя контейнер уже откачен engine'ом (double rollback)
            # · Root: снапшоты создавались без compose_state.previous_image → docker_tag
            # ·   пропускался → engine.deploy(ref="previous-rollback") пытался ПУЛЛИТЬ локальный
            # ·   тег из registry (тег не существует там по определению).
            # · Fix: (1) якорь previous_image персистится в снапшот ДО compose-up;
            # ·   (2) skip_pull=True — локально перетегированный образ пуллить не нужно.
            # · F-11 rider (2026-08-27): skip_pull оказался НЕДОСТАТОЧНЫМ — compose up сам
            # ·   ИМПЛИЦИТНО пуллит недостающий локальный тег; закрыто TRAP[BUG] F-11 выше
            # ·   (re-tag на compose-resolved ref + --pull never). Prevention: см. F-11-тесты.
            result = engine.deploy(
                project=Path(project_dir).name,
                ref="previous-rollback",
                service=service,
                project_dir=project_dir,
                skip_pull=True,
            )
        except PlatformError as e:
            # T3.1 (DevPlan 116 B4): _handle_first_deploy → PlatformFatalError вместо SystemExit
            logger.error("[IMP:10][DeployOrchestrator][rollback_compose] Engine error (exit=%d): %s", e.exit_code, e)
            return False
        except (OSError, subprocess.SubprocessError) as e:
            logger.error("[IMP:10][DeployOrchestrator][rollback_compose] Failed: %s", e)
            return False
        else:
            return result.success

    # endregion FUNC__rollback_compose


# endregion CLASS_RollbackMixin
