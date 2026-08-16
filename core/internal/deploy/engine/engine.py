#!/usr/bin/env python3
# GREP_SUMMARY: deploy-engine, DeployEngine, atomic-deploy, rollback, remove, status, deploy-compose, 170-W4-B2
# STRUCTURE: ▶ DeployEngine ┌projects_base┐ → deploy(project,ref,service,project_dir,node,max_wait,keep_images):
#            contextlib.chdir → _preflight_checks → lifecycle.save_previous_image → flow.pull_images →
#            flow.up_atomic → flow.wait_health → success(DEPLOY_STATUS=success) | first_deploy→exit |
#            rollback→lifecycle.perform_rollback → remove(status) → ⎋ ServiceDeployResult/RemoveResult/StatusResult
# region MODULE_CONTRACT
## @purpose  Atomic deploy/rollback/remove/status engine для VPS-side forced-command deploy-операций
##           (170 W4-B2: класс DeployEngine, извлечён из монолита deploy_engine.py 821 LOC).
##           Все Docker-операции — через shared docker_compose_* (subprocess.run, НЕ docker-py SDK, D4).
## @scope    Фасад пакета engine/ для deploy-оркестраторов: DeployOrchestrator (orchestrator.py),
##           context_deployer.py, CLI (engine/cli.py). Шаги deploy — engine/flow.py, жизненный цикл —
##           engine/lifecycle.py, result-контракты — engine/results.py.
## @invariants
##   1. Все Docker-операции через shared docker_compose_* (sole path) — zero docker-py dependency
##   2. Previous image saved BEFORE pull — enables rollback (T1)
##   3. Healthcheck poll ≤ max_wait — shared healthcheck_poll (inspect-критерий)
##   4. DEPLOY_STATUS="success" установка сразу после health-gate — ДО non-fatal housekeeping (B1/T3)
##   5. Rollback: re-tag previous image → docker compose up -d --force-recreate (T1)
##   6. First deploy с health fail → handle_first_deploy → PlatformFatalError (exit 10, no rollback)
##   7. Remove: docker compose down --timeout <DOCKER_STOP_TIMEOUT> БЕЗ -v по умолчанию (O7/DD10, C4); purge=True → -v
##   8. Status: JSON stdout — docker compose ps + DeployHistory last snapshot; stub-aware flag
##   9. Все методы логируют IMP:7-10 (LDD telemetry)
##   10. No secrets/tokens в выводе — audit-логи в stderr
##   11. Единый snapshot-механизм — DeployHistory; rollback — через perform_rollback (docker tag + up --force-recreate)
##   12. deploy() — contextlib.chdir (восстановление cwd при ЛЮБОМ exit path), без дубля валидации
## @rationale DevPlan 089 T7: DeployEngine вызывается из DeployOrchestrator, не standalone.
##           API deploy_compose() — публичный интерфейс; CLI argparse сохранён (backward compat).
## @changes 170 W4-B2 — extracted from deploy_engine.py
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import json
import logging
import os
import pathlib
import subprocess
from collections.abc import Callable
from typing import cast

# Шаги deploy() и жизненного цикла — извлечены в подмодули пакета (170 W4-B2).
from core.internal.deploy.engine.flow import (
    pull_images,
    up_atomic,
    wait_health,
)
from core.internal.deploy.engine.lifecycle import (
    handle_first_deploy,
    perform_rollback,
    save_previous_image,
)
from core.internal.deploy.engine.results import (
    ImageInfo,
    RemoveResult,
    ServiceDeployResult,
    StatusResult,
)

# Custom exceptions (единое определение — deploy/preflight.py): `except (ValidationError, DeployError)`
# ловит те же классы, что бросает run_preflight_checks. Backward-compat: имена доступны через фасад.
from core.internal.deploy.preflight import DeployError, ValidationError

# Канонический дефолт PROJECTS_BASE — shared/deploy_paths
from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE
from core.internal.shared.docker_compose import (
    docker_compose_down as _shared_docker_compose_down,
)
from core.internal.shared.docker_compose import (
    docker_compose_ps as _shared_docker_compose_ps,
)
from core.internal.shared.project_registry import validate_project_name
from core.internal.shared.stub_detection import is_stub_ai_platform_yaml

# Таймауты — единый реестр shared/timeouts.py (гейт timeout_literals)
from core.internal.shared.timeouts import DOCKER_STOP_TIMEOUT

logger = logging.getLogger(__name__)


# region CLASS_DeployEngine
class DeployEngine:
    """Atomic deploy/rollback/remove/status engine.

    All Docker operations via shared docker_compose_* (sole path, DevPlan 116 B5).
    Healthcheck uses shared healthcheck_poll (inspect-критерий, DevPlan 128 W1 T5.3).
    """

    def __init__(self, projects_base: str = DEFAULT_PROJECTS_BASE):
        self.projects_base = projects_base
        # 170 W4-B2: путь к validate.sh пересчитан под новую глубину пакета (engine/engine.py):
        # parents[3] = core/ (в монолите deploy_engine.py было 3×dirname; здесь +1 уровень).
        self._validate_script = str(
            pathlib.Path(__file__).resolve().parents[3] / "internal" / "validate" / "validate.sh"
        )

    # ── Public API ──────────────────────────────────────────────────────────

    # region FUNC_deploy_compose
    ## @purpose  Simplified deploy interface for DeployOrchestrator. Thin wrapper around deploy()
    ##           with project_dir-based project name extraction.
    ## @io       ⇥ project_dir: str, service: str, version: str → ⎋ ServiceDeployResult
    ## @complexity — O(N) where N = deploy steps
    ## @invariants
    ##   - Extracts project name from project_dir basename
    ##   - Uses default max_wait=60 and keep_images=3
    ##   - Returns ServiceDeployResult compatible with DeployOrchestrator
    def deploy_compose(self, project_dir: str, service: str, version: str) -> ServiceDeployResult:
        """Deploy a single compose service. Called by DeployOrchestrator.

        Args:
            project_dir: Absolute path to project directory.
            service: Docker Compose service name.
            version: Image version/tag to deploy.

        Returns:
            ServiceDeployResult with success/failure status.
        """
        project = pathlib.Path(project_dir.rstrip("/")).name
        logger.info(
            "[IMP:9][deploy_compose][start] Deploy compose: %s/%s → %s",
            project,
            service,
            version,
        )
        return self.deploy(
            project=project,
            ref=version,
            service=service,
            project_dir=project_dir,
            node="",
            max_wait=60,
            keep_images=3,
        )

    # endregion FUNC_deploy_compose

    # region FUNC_deploy
    ## @purpose  Perform atomic deploy with healthcheck-based rollback.
    ##           DevPlan 118 A8: deploy()/_deploy_inner схлопнуты в одну функцию —
    ##           дублированная validate_project_name убрана; contextlib.chdir гарантирует
    ##           восстановление cwd при ЛЮБОМ exit path (return/PlatformFatalError).
    ##           170 W4-B2: приватные шаги извлечены в engine/flow.py (pull_images/up_atomic/wait_health)
    ##           и engine/lifecycle.py (save_previous_image/perform_rollback/handle_first_deploy) —
    ##           семантика 1:1, поведение НЕ изменено.
    ## @io       ⇥ project, ref, service, project_dir, node, max_wait, keep_images → ⎋ ServiceDeployResult
    ## @complexity — O(N) where N = pull retry attempts + healthcheck attempts
    ## @invariants
    ##   - Previous image saved BEFORE pull (enables rollback)
    ##   - On success: DEPLOY_STATUS="success" set immediately after health-gate
    ##   - On health fail + first deploy: sys.exit(1), no rollback
    ##   - On health fail + existing deploy: perform_rollback called
    def deploy(
        self,
        project: str,
        ref: str,
        service: str,
        project_dir: str,
        node: str = "",  # ruff: ignore[ARG002]
        max_wait: int = 60,
        keep_images: int = 3,  # ruff: ignore[ARG002]
    ) -> ServiceDeployResult:
        """Execute atomic deploy with rollback capability.

        Args:
            project: Project name.
            ref: Image tag/ref to deploy.
            service: Docker Compose service name.
            project_dir: Path to project directory.
            node: Node name (hostname).
            max_wait: Max seconds to wait for healthcheck.
            keep_images: Number of old images to keep during prune.

        Returns:
            ServiceDeployResult with status and rollback metadata.
        """
        # ── Validate ──
        logger.info("[IMP:9][deploy][start] Deploy START: %s/%s → %s", project, service, ref)

        if not validate_project_name(project):
            msg = f"Invalid project name: {project}"
            logger.error("[IMP:10][deploy][validation] %s", msg)
            return ServiceDeployResult(success=False, project=project, ref=ref, service=service, error_message=msg)

        # 🧐 TRAP[DECISION] · 2026-07-31 · HI · os.chdir без восстановления — ядовитый cwd для pytest-процесса
        # · Symptom: tests/integration/test_deploy_e2e.py → DeployEngine → os.chdir(project_dir) из tempdir +
        #   teardown rmtree = удалённый cwd → FileNotFoundError os.getcwd() во ВСЕХ последующих тестах
        #   (каскад 71 failure в static_audit, gate MODE=fast RED). Root-cause: строки 277/380/449.
        # · Fix (2026-07-31): deploy() → contextlib.chdir (восстановление при любом exit path, включая
        #   PlatformFatalError от handle_first_deploy); remove()/status() → subprocess cwd=project_dir (процесс
        #   вообще не меняет cwd). Rejected: subprocess-level cwd во всех 14 вызовах deploy() — большой
        #   рефакторинг с риском регрессии, contextlib.chdir семантически эквивалентен прежнему chdir.
        # · Rev: если deploy() начнёт вызываться из долгоживущих процессов чаще CLI-паттерна → вариант (a).
        # contextlib.chdir: Python ≥3.11; pyrightconfig pythonVersion=3.10 (typeshed не знает chdir),
        # runtime платформы = 3.14 (DevPlan φ1 python_deps) → доступен.
        with contextlib.chdir(project_dir):  # pyright: ignore[reportAttributeAccessIssue]
            try:
                self._preflight_checks(project_dir, service)
            except (ValidationError, DeployError) as e:
                logger.error("[IMP:10][deploy][preflight] %s", e)
                return ServiceDeployResult(
                    success=False, project=project, ref=ref, service=service, error_message=str(e)
                )

            # ── Save previous image (BEFORE pull) ──
            previous_image = save_previous_image(project_dir, service)
            is_first_deploy = previous_image is None
            logger.log(
                logging.INFO if not is_first_deploy else logging.WARNING,
                "[IMP:9][deploy][save-prev] Previous image: %s (first_deploy=%s)",
                previous_image.id if previous_image else "NONE",
                is_first_deploy,
            )

            # ── Pull image with retry (flow.pull_images: shared retry_pull — backoff [5,10,20,40,60]) ──
            # (TRAP[BUG] ночной сессии 141 — в engine/flow.py у pull_images)
            if not pull_images(project_dir, service, ref):
                handle_first_deploy(project, service, ref, "Pull failed after 5 attempts")
                # unreachable — handle_first_deploy raises PlatformFatalError

            # ── Atomic up ──
            if not up_atomic(project_dir, service, ref):
                if is_first_deploy:
                    handle_first_deploy(project, service, ref, "docker compose up failed on first deploy")
                else:
                    logger.warning("[IMP:9][deploy][up-fail] atomic_up failed — attempting rollback")
                    rollback_ok = perform_rollback(project_dir, service, previous_image)
                    return ServiceDeployResult(
                        success=False,
                        project=project,
                        ref=ref,
                        service=service,
                        previous_image=previous_image.id if previous_image else None,
                        rollback_performed=rollback_ok,
                        error_message="docker compose up failed, rollback "
                        + ("performed" if rollback_ok else "failed"),
                    )

            # ── Poll health (flow.wait_health: shared healthcheck_poll — inspect-критерий) ──
            if wait_health(service, max_wait):
                # B1: DEPLOY_STATUS immediately after health-gate
                logger.info("[IMP:9][deploy][health] Healthcheck PASSED for %s/%s", project, service)
                return ServiceDeployResult(
                    success=True,
                    project=project,
                    ref=ref,
                    service=service,
                    previous_image=previous_image.id if previous_image else None,
                )
            logger.error("[IMP:10][deploy][health] Healthcheck FAILED for %s/%s", project, service)
            if is_first_deploy:
                handle_first_deploy(project, service, ref, "Healthcheck failed on first deploy")
                # unreachable — handle_first_deploy raises PlatformFatalError

            rollback_ok = perform_rollback(project_dir, service, previous_image)
            return ServiceDeployResult(
                success=False,
                project=project,
                ref=ref,
                service=service,
                previous_image=previous_image.id if previous_image else None,
                rollback_performed=rollback_ok,
                error_message="Healthcheck failed, rollback " + ("performed" if rollback_ok else "failed"),
            )

    # endregion FUNC_deploy

    # region FUNC_remove
    ## @purpose  Idempotent remove: stop containers WITHOUT destroying data (O7/DD10).
    ## @io       ⇥ project, project_dir, purge → ⎋ RemoveResult
    ## @complexity — O(1) — single docker compose call
    ## @invariants
    ##   - docker compose down WITHOUT -v (data preserved) — О7 (purge=False default)
    ##   - purge=True — docker compose down -v (удаление volumes; ТОЛЬКО по явному CLI-флагу)
    ##   - Idempotent: if project dir missing or already stopped → already_removed=True
    @staticmethod
    def remove(project: str, project_dir: str, purge: bool = False) -> RemoveResult:
        """Safely remove (disconnect) a project: stop containers, keep data.

        Args:
            project: Project name.
            project_dir: Path to project directory.
            purge: If True, remove compose volumes too (docker compose down -v).
                Default False — data preserved (O7/DD10). DevPlan 118 A6: purge-поддержка
                добавлена для делегирования DeployOrchestrator.remove(purge=...) (CLI --purge).

        Returns:
            RemoveResult with status.
        """
        logger.info("[IMP:9][remove][start] Remove START: %s (purge=%s)", project, purge)

        if not validate_project_name(project):
            msg = f"Invalid project name: {project}"
            logger.error("[IMP:10][remove][validation] %s", msg)
            return RemoveResult(success=False, project=project, error_message=msg)

        if not os.path.isdir(project_dir):
            logger.info("[IMP:9][remove][not-found] Project dir not found: %s — already removed", project_dir)
            return RemoveResult(success=True, project=project, already_removed=True)

        # docker compose down WITHOUT -v (O7 — data preserved) — shared sole path (T5);
        # purge=True добавляет -v (явное CLI-решение, DevPlan 118 A6)
        # C4: --timeout из канона DOCKER_STOP_TIMEOUT (shared/timeouts, 0 литералов)
        flags = ["--timeout", str(DOCKER_STOP_TIMEOUT)]
        if purge:
            flags.append("-v")
        logger.info(
            "[IMP:9][remove][down] Stopping containers for %s (purge=%s, data preserved unless purge)", project, purge
        )
        if not _shared_docker_compose_down(project_dir, flags=flags):
            logger.warning("[IMP:8][remove][down-warn] docker compose down reported failure")

        logger.info("[IMP:9][remove][done] Remove DONE: %s (data preserved)", project)
        return RemoveResult(success=True, project=project, already_removed=False)

    # endregion FUNC_remove

    # region FUNC_status
    ## @purpose  Print JSON status for a project: docker compose ps + DeployHistory last snapshot.
    ## @io       ⇥ project, project_dir, stub_aware → ⎋ StatusResult
    ## @complexity — O(1) — reads files + docker ps call
    ## @invariants
    ##   - If project dir missing → status="not_found"
    ##   - If ai-platform.yaml starts with GENERATED-STUB → status="stub" (when stub_aware=True)
    ##   - Always returns valid JSON structure
    def status(self, project: str, project_dir: str, stub_aware: bool = False) -> StatusResult:
        """Get JSON status for a project.

        Args:
            project: Project name.
            project_dir: Path to project directory.
            stub_aware: Enable stub detection via ai-platform.yaml header check.

        Returns:
            StatusResult with project state.
        """
        logger.info("[IMP:9][status][start] Status check: %s", project)

        if not os.path.isdir(project_dir):
            logger.info("[IMP:9][status][not-found] Project dir not found: %s", project_dir)
            return StatusResult(project=project, node="", status="not_found")

        # ── Stub-aware detection ──
        # 🧐 TRAP[DECISION] · 2026-07-26 · — · STUB_AWARE_STATUS flag
        # DevPlan 118 A6: единая is_stub-детекция через shared/stub_detection (U-28) —
        # инлайн-копия (первая строка ai-platform.yaml) заменена каноническим детектором.
        ai_yaml = os.path.join(project_dir, "ai-platform.yaml")
        if stub_aware and is_stub_ai_platform_yaml(ai_yaml):
            logger.info("[IMP:9][status][stub] Project %s is a GENERATED-STUB", project)
            return StatusResult(
                project=project,
                node="",
                status="stub",
                last_deploy={"message": "Project directory exists but ai-platform.yaml is a GENERATED-STUB"},
            )

        # ── Docker compose ps (shared sole path — T5) ──
        containers: list[dict[str, object]] = []
        # ruff: ignore[PLW0717] — внутри try есть break/continue/await/yield — извлечение ломает управляющий поток
        try:
            ps_result = _shared_docker_compose_ps(project_dir, format="json")
            ps_stdout = ps_result.stdout
            if isinstance(ps_stdout, bytes):
                ps_stdout = ps_stdout.decode("utf-8")
            if ps_result.returncode == 0 and ps_stdout.strip():
                for line in ps_stdout.strip().split("\n"):
                    try:
                        containers.append(cast(dict[str, object], json.loads(line)))
                    except json.JSONDecodeError as exc:  # ruff: ignore[PERF203]
                        # 170 W2-A2 (B3): тихий continue → debug с контекстом (не-валидная строка
                        # docker compose ps json — skip, остальные строки обрабатываются)
                        logger.debug(
                            "[IMP:5][status][ps-line] Skipping non-JSON docker compose ps line: %r (%s)",
                            line,
                            exc,
                        )
                        continue
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("[IMP:8][status][ps-error] docker compose ps error: %s", e)

        # ── Last deploy result — DeployHistory snapshot (канон) ──
        # DeployOrchestrator.status() делегирует сюда — last_deploy обязан читать
        # ТОТ ЖЕ механизм (DeployHistory), что пишет DeployOrchestrator.deploy() (create_snapshot).
        last_deploy: dict[str, object] | None = None
        try:
            # 170 W4-B3: DeployHistory переехал в deploy/audit/ (фасад deploy_history.py сохранён)
            from core.internal.deploy.audit import DeployHistory

            snapshot = DeployHistory(projects_base=self.projects_base).latest_snapshot(project)
            if snapshot:
                last_deploy = snapshot
        except OSError as e:
            logger.warning("[IMP:8][status][last-deploy] Cannot read DeployHistory snapshot: %s", e)

        logger.info("[IMP:9][status][found] Project %s: %d containers", project, len(containers))
        return StatusResult(
            project=project,
            node="",
            status="found",
            containers=containers,
            last_deploy=last_deploy,
        )

    # endregion FUNC_status

    # ── Internal helpers ────────────────────────────────────────────────────

    # region FUNC__preflight_checks
    ## @purpose  Validate pre-deploy conditions: FQDN uniqueness and port conflicts.
    ##           DevPlan 119 E4: реализация вынесена в deploy/preflight.py (run_preflight_checks).
    ##           Тонкий фасад сохраняет имя для обратной совместимости.
    ## @io       ⇥ project_dir, service → ⎋ None (raises on fail)
    ## @complexity — O(1) — delegation to preflight module
    ## @invariants
    ##   - FQDN check via validate.sh subprocess (canonical, not duplicated in Python)
    ##   - Port conflict via ss -tlnp (shows ALL listening ports)
    ##   - Both checks are non-blocking for first deploy (warnings logged)
    def _preflight_checks(
        self, project_dir: str, service: str, run_preflight: Callable[..., None] | None = None
    ) -> None:
        """Run pre-deploy validation checks (E4 — delegates to deploy/preflight.py).

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            run_preflight: Optional injected preflight-runner (167 D3 DI-seam). None → канон.

        Raises:
            ValidationError: If FQDN conflict detected.
            DeployError: If port conflict detected.
        """
        if run_preflight is None:
            from core.internal.deploy.preflight import run_preflight_checks

            run_preflight = run_preflight_checks

        run_preflight(project_dir, service, self._validate_script)

    # endregion FUNC__preflight_checks

    # region FUNC__save_previous_image
    ## @purpose  Save current image ID before pull (enables rollback). API-compat шim:
    ##           реализация — engine/lifecycle.save_previous_image (170 W4-B2).
    ## @io       ⇥ project_dir, service → ⎋ Optional[ImageInfo]
    ## @complexity — O(1) — one docker compose images call + optional docker inspect
    @staticmethod
    def _save_previous_image(project_dir: str, service: str) -> ImageInfo | None:
        """Save current image ID and tag before deploy (delegates to engine.lifecycle)."""
        return save_previous_image(project_dir, service)

    # endregion FUNC__save_previous_image

    # region FUNC__atomic_up
    ## @purpose  Execute docker compose up -d for single service. API-compat shim:
    ##           реализация — engine/flow.up_atomic (170 W4-B2).
    ## @io       ⇥ project_dir, service, ref → ⎋ bool
    ## @complexity — O(1) — делегирование в shared docker_compose_up
    @staticmethod
    def _atomic_up(project_dir: str, service: str, ref: str) -> bool:
        """Start service via docker compose up -d (delegates to engine.flow.up_atomic)."""
        return up_atomic(project_dir, service, ref)

    # endregion FUNC__atomic_up

    # region FUNC__perform_rollback
    ## @purpose  Rollback to previous image: re-tag + docker compose up --force-recreate.
    ##           API-compat shim: реализация — engine/lifecycle.perform_rollback (170 W4-B2).
    ## @io       ⇥ project_dir, service, previous_image → ⎋ bool
    ## @complexity — O(1) — tag + compose up calls
    @staticmethod
    def _perform_rollback(project_dir: str, service: str, previous_image: ImageInfo | None) -> bool:
        """Rollback to previous image (delegates to engine.lifecycle.perform_rollback)."""
        return perform_rollback(project_dir, service, previous_image)

    # endregion FUNC__perform_rollback

    # region FUNC__handle_first_deploy
    ## @purpose  Handle first deploy failure — no rollback possible, escalate.
    ##           API-compat shim: реализация — engine/lifecycle.handle_first_deploy (170 W4-B2),
    ##           которая делегирует в deploy/first_deploy.py (DevPlan 119 E4).
    ## @io       ⇥ project, service, ref, reason → ⎋ None (raises PlatformFatalError, exit 10)
    @staticmethod
    def _handle_first_deploy(project: str, service: str, ref: str, reason: str) -> None:
        """Handle first deploy failure — no rollback possible (delegates to engine.lifecycle)."""
        handle_first_deploy(project, service, ref, reason)

    # endregion FUNC__handle_first_deploy


# endregion CLASS_DeployEngine
