#!/usr/bin/env python3
# GREP_SUMMARY: deploy-engine, atomic-deploy, rollback, healthcheck, remove, status, docker-compose, lifecycle, prune-images
# STRUCTURE: ▶ DataClasses(ServiceDeployResult|RemoveResult|StatusResult|ImageInfo) → [DeployEngine] →
#            ◇ deploy(project,ref,service,project_dir,node,max_wait,keep_images) → _save_previous_image →
#            _preflight_checks → _pull_image_with_retry → _atomic_up → _poll_health →
#            either success(DEPLOY_STATUS=success) or fail(first_deploy→exit|rollback→_perform_rollback) →
#            ◇ remove(project,project_dir,purge) → docker compose down(no -v by default) →
#            ◇ status(project,project_dir,stub_aware) → JSON → CLI: argparse(subcommands:deploy|remove|status) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Atomic deploy/rollback/remove/status engine for VPS-side forced-command deploy operations.
##           Migrated from the legacy deploy shell (1183→~600 LOC) via Strangler-Fig methodology (Wave 5e).
##           All Docker operations via subprocess.run (docker compose CLI), NOT docker-py SDK (D4).
## @scope    Called by the deploy shell facade for deploy/remove/status verbs.
##           Importable by other Python modules (context_deployer.py, etc.) via DeployEngine class.
## @invariants
##   1. All Docker operations go through shared docker_compose_* (sole path) — zero docker-py dependency
##   2. Previous image saved BEFORE docker compose pull — enables rollback (T1)
##   3. Healthcheck poll ≤ max_wait seconds — shell-wrapper poll_until_healthy used
##   4. DEPLOY_STATUS="success" set immediately after health-gate — BEFORE non-fatal housekeeping (B1/T3)
##   5. Rollback: re-tag previous image → docker compose up -d --force-recreate (T1)
##   6. First deploy with health fail → _handle_first_deploy → PlatformFatalError (exit 10, no rollback possible)
##   7. Remove: docker compose down --timeout <DOCKER_STOP_TIMEOUT> WITHOUT -v by default (O7/DD10, T11, C4 канон); purge=True adds -v
##   8. Status: JSON stdout with docker compose ps + DeployHistory last snapshot; stub-aware flag
##   9. All methods log at IMP:7-10 for LDD telemetry
##   10. No secrets or tokens in output — audit logs go to stderr
##   11. DevPlan 118 A7: единый snapshot-механизм — DeployHistory (create_snapshot/rollback).
##       DeployEngine._capture_deploy_snapshot УДАЛЁН (ps/images-файлы + .deploy-started никто не читал;
##       DeployHistory покрывает rollback/status; два namespace в .deploy-snapshots были источником
##       TRAP[BUG] deploy_history.py:280). Rollback остаётся на _perform_rollback (docker tag + up --force-recreate).
##   12. DevPlan 118 A8: deploy()/_deploy_inner схлопнуты — одна функция с contextlib.chdir, без дубля валидации.
## @rationale
##   ⚠️ TRAP[BUG] · 2026-07-18 · P1 · Deploy reports 'failed' despite success (B1)
##   · Symptom: Deploy SUCCESS in logs, deploy-result.json=status:"failed", exit 1
##   · Root: DEPLOY_STATUS="success" was assigned AFTER non-fatal steps under set -e
##   · Fix: DEPLOY_STATUS="success" immediately after health-gate; non-fatal steps guarded
##   · Prevention: Any code after health-gate in deploy() must not raise exceptions
##
##   🧐 TRAP[DECISION] · 2026-07-17 · — · Rollback on-node, not in CI/CD
##   · Rejected: CI/CD-driven rollback (re-deploy via GitHub Actions)
##   · Reason: instant rollback without CI pipeline wait, eliminates network roundtrip
##   · Rev: if deploy latency >5min from CI → reconsider
##
##   🧐 TRAP[DECISION] · 2026-07-17 · — · audit_log() replaces audit_write()
##   · Rejected: keeping audit_write() in the legacy deploy shell (duplicate)
##   · Reason: audit_log() was the canonical shell audit function (now replaced by AuditLogger Python).
##
##   ⚠️ TRAP[BUG] · 2026-07-20 · REF="<sha> production" — env suffix leaks into image tag
##   · Symptom: "invalid reference format" — docker pulls "image:sha production"
##   · Root: parse_ssh_command didn't strip optional third token (environment)
##   · Fix: REF="${REF%% *}" — strip everything after second space
##
##   💼 TRAP[BUSINESS] · 2026-07-17 · HI · remove = disconnect, данные не удаляются автоматически
##   · Source: owner (O7/DD10)
##   · Risk: авто-очистка = невосстановимая потеря БД проекта
##   · Safeguard: remove() uses docker compose down WITHOUT -v
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · FQDN uniqueness via validate.sh subprocess
##   · Rejected: Python socket/FQDN parsing (duplicates validate.sh logic)
##   · Reason: validate.sh is the canonical FQDN check
##   · Rev: if validate.sh is ever deprecated → inline Python socket.gethostbyname check
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · Port conflict via ss -tlnp
##   · Rejected: Docker network inspect (only shows mapped ports, not host conflicts)
##   · Reason: ss -tlnp shows ALL listening ports — detects conflicts before Docker starts
##   · Rev: if Docker adds host-port conflict detection → migrate to Docker-native check
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · STUB_AWARE_STATUS flag for stub-detection
##   · Rejected: always detect stubs (performance overhead on every status call)
##   · Reason: stub detection requires yaml_read — optional flag avoids unnecessary I/O
##   · Rev: if stub detection overhead <1ms → make it default, remove flag
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · Wave 5e: legacy deploy shell Strangler-Fig migrated to Python
##   · Rejected: keeping deploy logic in shell (risk: 1183 LOC monolith, 3 inline python3)
##   · Reason: языковая политика (AGENTS.md), тестируемость, дедупликация с ssh_command_parser
##   · Rev: если Python deploy_engine добавляет >2s latency vs shell → профилировать subprocess overhead
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · deploy_engine + payload_deliverer — TWO separate modules
##   · Rejected: единый deploy_orchestrator.py (God Class >800 LOC)
##   · Reason: разные домены (Docker orchestration vs file delivery), DDD boundary, переиспользование
## @changes 2026-07-26 · DevPlan 036E — Created (Wave 5e Strangler-Fig migration from the legacy deploy shell)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

# DevPlan 128 W1 (P2-5/D6): docker image inspect/tag примитивы — shared/docker_ops
# (единственный слой, гейт docker_sole_path).
from core.internal.shared import docker_ops

# B2: канонический дефолт PROJECTS_BASE — shared/deploy_paths (литерал /opt/projects удалён)
from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE
from core.internal.shared.docker_compose import (
    docker_compose_down as _shared_docker_compose_down,
)
from core.internal.shared.docker_compose import (
    docker_compose_images as _shared_docker_compose_images,
)
from core.internal.shared.docker_compose import (
    docker_compose_ps as _shared_docker_compose_ps,
)
from core.internal.shared.docker_compose import (
    docker_compose_up as _shared_docker_compose_up,
)
from core.internal.shared.docker_compose import (
    healthcheck_poll as _shared_healthcheck_poll,
)
from core.internal.shared.docker_compose import (
    retry_pull as _shared_retry_pull,
)
from core.internal.shared.exceptions import PlatformError
from core.internal.shared.project_registry import validate_project_name
from core.internal.shared.stub_detection import is_stub_ai_platform_yaml

# DevPlan 116 B5 T1: таймауты — единый реестр shared/timeouts.py (U-11, гейт timeout_literals)
# DevPlan 118 C4: DOCKER_STOP_TIMEOUT — канон для `docker compose down --timeout` (литерал 30 удалён).
from core.internal.shared.timeouts import (
    COMPOSE_UP_TIMEOUT,
    DOCKER_STOP_TIMEOUT,
    PULL_TIMEOUT,
)

logger = logging.getLogger(__name__)

# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class ServiceDeployResult:
    """Result of a deploy operation."""

    success: bool
    project: str
    ref: str
    service: str
    previous_image: str | None = None
    rollback_performed: bool = False
    first_deploy_failed: bool = False
    error_message: str | None = None


@dataclass
class RemoveResult:
    """Result of a remove operation."""

    success: bool
    project: str
    already_removed: bool = False
    error_message: str | None = None


@dataclass
class StatusResult:
    """Result of a status operation.

    ## @purpose — Status-контракт (DevPlan 116 B1 T3, U-36): StatusResult = ТОТ ЖЕ канон, что
    ##            ProjectStatus (orchestrator.py) — поля {project, status, containers, last_deploy}.
    ##            Поле `node` — расширение (заполняется на on-node статусах); в JSON-каноне
    ##            диспетчера (orchestrator_cli dispatch status) используется ProjectStatus.to_dict().
    ## @invariants
    ##   - status ∈ {"found", "not_found", "stub"} — тот же словарь, что у ProjectStatus
    ##   - containers: list[dict] — docker compose ps JSON-строки (та же структура)
    ##   - last_deploy: dict | None — последний DeployHistory snapshot (DevPlan 118 A6: единый
    ##     механизм с DeployOrchestrator.status(); legacy deploy-result.json больше не читается)
    ##   - Поля НЕ расходятся с ProjectStatus: тест set-сравнения ключей (T3 п.4)
    """

    project: str
    node: str
    status: str  # "found" | "not_found" | "stub"
    containers: list[dict] = field(default_factory=list)
    last_deploy: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON — канон ProjectStatus {project, status, containers, last_deploy} + node."""
        return {
            "project": self.project,
            "node": self.node,
            "status": self.status,
            "containers": self.containers,
            "last_deploy": self.last_deploy,
        }


@dataclass
class ImageInfo:
    """Info about a saved previous image."""

    id: str
    tag: str | None = None


# ── Custom exceptions (DevPlan 119 E4: единое определение — deploy/preflight.py) ──
# Локальные классы DeployError/ValidationError УДАЛЕНЫ (дубль); deploy_engine импортирует
# их из preflight.py, чтобы `except (ValidationError, DeployError)` ловил те же классы,
# что бросает run_preflight_checks. Backward-compat: имена доступны через deploy_engine.
from core.internal.deploy.preflight import DeployError, ValidationError

# ── DeployEngine ────────────────────────────────────────────────────────────

# region CLASS_DeployEngine


class DeployEngine:
    """Atomic deploy/rollback/remove/status engine.

    ## @rationale DevPlan 089 T7: DeployEngine is called from DeployOrchestrator,
    ##            not as standalone. API: deploy_compose() is the public interface.
    ##            CLI argparse is preserved for backward compatibility.
    """

    """Atomic deploy/rollback/remove/status engine.

    All Docker operations via shared docker_compose_* (sole path, DevPlan 116 B5).
    Healthcheck uses shell lib/healthcheck.sh via subprocess.
    """

    def __init__(self, projects_base: str = DEFAULT_PROJECTS_BASE):
        self.projects_base = projects_base
        self._validate_script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "internal",
            "validate",
            "validate.sh",
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
        project = os.path.basename(project_dir.rstrip("/"))
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
    ## @io       ⇥ project, ref, service, project_dir, node, max_wait, keep_images → ⎋ ServiceDeployResult
    ## @complexity — O(N) where N = pull retry attempts + healthcheck attempts
    ## @invariants
    ##   - Previous image saved BEFORE pull (enables rollback)
    ##   - On success: DEPLOY_STATUS="success" set immediately after health-gate
    ##   - On health fail + first deploy: sys.exit(1), no rollback
    ##   - On health fail + existing deploy: _perform_rollback called
    def deploy(
        self,
        project: str,
        ref: str,
        service: str,
        project_dir: str,
        node: str = "",
        max_wait: int = 60,
        keep_images: int = 3,
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
        #   PlatformFatalError от _handle_first_deploy); remove()/status() → subprocess cwd=project_dir (процесс
        #   вообще не меняет cwd). Rejected: subprocess-level cwd во всех 14 вызовах deploy() — большой
        #   рефакторинг с риском регрессии, contextlib.chdir семантически эквивалентен прежнему chdir.
        # · Rev: если deploy() начнёт вызываться из долгоживущих процессов чаще CLI-паттерна → вариант (a).
        with contextlib.chdir(project_dir):
            try:
                self._preflight_checks(project_dir, service)
            except (ValidationError, DeployError) as e:
                logger.error("[IMP:10][deploy][preflight] %s", str(e))
                return ServiceDeployResult(
                    success=False, project=project, ref=ref, service=service, error_message=str(e)
                )

            # ── Save previous image (BEFORE pull) ──
            previous_image = self._save_previous_image(project_dir, service)
            is_first_deploy = previous_image is None
            logger.log(
                logging.INFO if not is_first_deploy else logging.WARNING,
                "[IMP:9][deploy][save-prev] Previous image: %s (first_deploy=%s)",
                previous_image.id if previous_image else "NONE",
                is_first_deploy,
            )

            # ── Pull image with retry (T5.1: shared retry_pull — backoff [5,10,20], env IMAGE_TAG) ──
            # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — first-deploy пул = FATAL при 15s ретраев
            # · Symptom: холодный бутстрап — пул tronyx-site (nginx:alpine) упал 3× подряд за ~20s
            # ·   (транзиент: mirror/DNS на окне параллельных пулов 21 модуля) → «First deploy failed —
            # ·   no rollback possible» (exit 10) → deploy_services FAILED → весь bootstrap падает.
            # ·   Ручной пул через минуту — успех (образ в кеше). Ошибка транзиентная, окно ретраев мало.
            # · Fix: first-deploy пул — 5 попыток, backoff [5,10,20,40,60] (~2 мин окно транзиентов);
            # ·   повторный пул после частичного кеша = cache_hit (доказательство в 04-TimingsReport).
            # · Rev: если транзиентные фейлы пулов станут >2 мин — поднять max_attempts/backoff.
            if not _shared_retry_pull(
                project_dir,
                max_attempts=5,
                backoff_seconds=[5, 10, 20, 40, 60],
                timeout=PULL_TIMEOUT,
                service=service,
                env_override={"IMAGE_TAG": ref},
            ):
                self._handle_first_deploy(project, service, ref, "Pull failed after 5 attempts")
                # unreachable — _handle_first_deploy raises PlatformFatalError

            # ── Atomic up ──
            if not self._atomic_up(project_dir, service, ref):
                if is_first_deploy:
                    self._handle_first_deploy(project, service, ref, "docker compose up failed on first deploy")
                else:
                    logger.warning("[IMP:9][deploy][up-fail] atomic_up failed — attempting rollback")
                    rollback_ok = self._perform_rollback(project_dir, service, previous_image)
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

            # ── Poll health (T5.3: shared healthcheck_poll — inspect-критерий, service-фильтр) ──
            healthy = (
                _shared_healthcheck_poll(project_name=service, timeout=max_wait, interval=2, service=service)
                == "healthy"
            )
            if healthy:
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
                self._handle_first_deploy(project, service, ref, "Healthcheck failed on first deploy")
                # unreachable — _handle_first_deploy raises PlatformFatalError

            rollback_ok = self._perform_rollback(project_dir, service, previous_image)
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
    def remove(self, project: str, project_dir: str, purge: bool = False) -> RemoveResult:
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
        containers: list[dict[str, Any]] = []
        try:
            ps_result = _shared_docker_compose_ps(project_dir, format="json")
            ps_stdout = ps_result.stdout
            if isinstance(ps_stdout, bytes):
                ps_stdout = ps_stdout.decode("utf-8")
            if ps_result.returncode == 0 and ps_stdout.strip():
                for line in ps_stdout.strip().split("\n"):
                    try:
                        containers.append(json.loads(line))
                    except json.JSONDecodeError:  # noqa: PERF203
                        continue
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("[IMP:8][status][ps-error] docker compose ps error: %s", str(e))

        # ── Last deploy result — DeployHistory snapshot (канон, DevPlan 118 A6) ──
        # DevPlan 118 A6: DeployOrchestrator.status() делегирует сюда — last_deploy обязан читать
        # ТОТ ЖЕ механизм (DeployHistory), что пишет DeployOrchestrator.deploy() (create_snapshot).
        # deploy-result.json — legacy-артефакт shell-эпохи (больше НИЧЕМ не пишется, U-51) — удалён.
        last_deploy: dict[str, Any] | None = None
        try:
            from core.internal.deploy.deploy_history import DeployHistory

            snapshot = DeployHistory(projects_base=self.projects_base).latest_snapshot(project)
            if snapshot:
                last_deploy = snapshot
        except OSError as e:
            logger.warning("[IMP:8][status][last-deploy] Cannot read DeployHistory snapshot: %s", str(e))

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

    # region FUNC__save_previous_image
    ## @purpose  Save current image ID before pull (enables rollback).
    ## @io       ⇥ project_dir, service → ⎋ Optional[ImageInfo]
    ## @complexity — O(1) — one docker compose images call + optional docker inspect
    ## @invariants
    ##   - Called BEFORE pull (critical ordering for rollback)
    ##   - Returns None if no previous image (first deploy)
    ##   - If image tag is <none\>:<none\>, creates fallback tag `project:previous-rollback`
    def _save_previous_image(self, project_dir: str, service: str) -> ImageInfo | None:
        """Save current image ID and tag before deploy.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.

        Returns:
            ImageInfo with ID and tag, or None for first deploy.
        """
        logger.info("[IMP:8][save-prev] Saving previous image for service %s", service)

        # Shared docker_compose_images -q (sole path — T5)
        result = _shared_docker_compose_images(project_dir, service=service, flags=["-q"])
        image_id = result.stdout.strip()

        if not image_id:
            logger.info("[IMP:9][save-prev] FIRST DEPLOY: no previous image for %s", service)
            return None

        # Get tag (docker image inspect — локальная image-операция; W1: shared/docker_ops)
        tag = docker_ops.docker_image_inspect(image_id, "{{index .RepoTags 0}}")

        if not tag or tag == "<none>:<none>":
            tag = f"{service}:previous-rollback"
            docker_ops.docker_tag(image_id, tag)
            logger.info("[IMP:8][save-prev] Created fallback tag for dangling image: %s", tag)

        logger.info("[IMP:9][save-prev] Previous image saved: ID=%s TAG=%s", image_id, tag)
        return ImageInfo(id=image_id, tag=tag)

    # endregion FUNC__save_previous_image

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
    def _preflight_checks(self, project_dir: str, service: str) -> None:
        """Run pre-deploy validation checks (E4 — delegates to deploy/preflight.py).

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.

        Raises:
            ValidationError: If FQDN conflict detected.
            DeployError: If port conflict detected.
        """
        from core.internal.deploy.preflight import run_preflight_checks

        run_preflight_checks(project_dir, service, self._validate_script)

    # endregion FUNC__preflight_checks

    # region FUNC__atomic_up
    ## @purpose  Execute docker compose up -d for single service (тонкая обёртка над shared, T5.2).
    ## @io       ⇥ project_dir, service, ref → ⎋ bool
    ## @complexity — O(1) — делегирование в shared docker_compose_up
    ## @invariants — env_override={"IMAGE_TAG": ref}; shared = {**os.environ, **override} (D7)
    def _atomic_up(self, project_dir: str, service: str, ref: str) -> bool:
        """Start service via docker compose up -d (delegates to shared — sole path)."""
        logger.info("[IMP:9][up] Atomic up: %s (IMAGE_TAG=%s)", service, ref)
        return _shared_docker_compose_up(
            project_dir,
            timeout=COMPOSE_UP_TIMEOUT,
            service=service,
            env_override={"IMAGE_TAG": ref},
        )

    # endregion FUNC__atomic_up

    # region FUNC__perform_rollback
    ## @purpose  Rollback to previous image: re-tag + docker compose up --force-recreate.
    ## @io       ⇥ project_dir, service, previous_image → ⎋ bool
    ## @complexity — O(1) — tag + compose up calls
    ## @invariants
    ##   - Re-tags previous image before compose up (ensures correct image reference)
    ##   - Uses --force-recreate to ensure container replacement
    ##   - Returns False if rollback compose up fails
    def _perform_rollback(self, project_dir: str, service: str, previous_image: ImageInfo | None) -> bool:
        """Rollback to previous image.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            previous_image: ImageInfo with ID/tag of previous image.

        Returns:
            True if rollback succeeded, False otherwise.
        """
        if previous_image is None:
            logger.error("[IMP:10][rollback] No previous image — cannot rollback")
            return False

        logger.info("[IMP:10][rollback] ROLLING BACK %s to %s", service, previous_image.id)

        # Re-tag previous image (docker tag — локальная image-операция; W1: shared/docker_ops)
        if previous_image.tag:
            docker_ops.docker_tag(previous_image.id, previous_image.tag)
            logger.info("[IMP:9][rollback] Re-tagged %s → %s", previous_image.id, previous_image.tag)

        # docker compose up -d --force-recreate (T5.4: shared docker_compose_up — sole path)
        if not _shared_docker_compose_up(
            project_dir,
            timeout=COMPOSE_UP_TIMEOUT,
            service=service,
            flags=["--force-recreate"],
        ):
            logger.error("[IMP:10][rollback] Rollback compose up FAILED for %s", service)
            return False

        logger.info("[IMP:10][rollback] Rollback complete: %s restored to %s", service, previous_image.id)
        return True

    # endregion FUNC__perform_rollback

    # region FUNC__handle_first_deploy
    ## @purpose  Handle first deploy failure — no rollback possible, escalate.
    ##           DevPlan 119 E4: реализация вынесена в deploy/first_deploy.py (handle_first_deploy).
    ##           Тонкий фасад сохраняет имя для обратной совместимости.
    ## @io       ⇥ project, service, ref, reason → ⎋ None (raises PlatformFatalError, exit 10)
    def _handle_first_deploy(self, project: str, service: str, ref: str, reason: str) -> None:
        """Handle first deploy failure — no rollback possible (E4 — delegates to first_deploy.py).

        Args:
            project: Project name.
            service: Service name.
            ref: Image ref.
            reason: Failure reason for logging.

        Raises:
            PlatformFatalError: Always — no rollback possible, requires manual intervention
                (DevPlan 116 B4 T3.1: sys.exit(1) → raise PlatformFatalError, exit code 10).
        """
        from core.internal.deploy.first_deploy import handle_first_deploy

        handle_first_deploy(project, service, ref, reason)

    # endregion FUNC__handle_first_deploy


# endregion CLASS_DeployEngine


# ── CLI ──────────────────────────────────────────────────────────────────────


# region CLI
# region FUNC_main
## @purpose  CLI entrypoint with argparse subcommands: deploy, remove, status.
## @io       ⇥ sys.argv → ⎋ exit 0|1|10 (PlatformError → e.exit_code)
## @rationale D8 (DevPlan 036E): argparse subcommands for debuggability, testability, composability.
##            Shell facade performs verb classification before calling Python modules.
## @invariants
##   - First-deploy failure → PlatformFatalError → exit 10 (DevPlan 116 B4 T3.1/D4)
##   - Единый паттерн: except PlatformError as e → return e.exit_code (контракт T4)
def main(argv: list[str] | None = None) -> int:
    """CLI entry point — deploy/remove/status subcommands (contract: main() -> int)."""
    parser = argparse.ArgumentParser(description="Deploy Engine — atomic deploy/rollback/remove/status")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── Deploy subcommand ──
    deploy_parser = sub.add_parser("deploy", help="Atomic deploy with healthcheck-based rollback")
    deploy_parser.add_argument("--project", required=True)
    deploy_parser.add_argument("--ref", required=True)
    deploy_parser.add_argument("--service", required=True)
    deploy_parser.add_argument("--project-dir", required=True)
    deploy_parser.add_argument("--node", default="")
    deploy_parser.add_argument("--max-wait", type=int, default=60)
    deploy_parser.add_argument("--keep-images", type=int, default=3)

    # ── Remove subcommand ──
    remove_parser = sub.add_parser("remove", help="Idempotent remove (data preserved)")
    remove_parser.add_argument("--project", required=True)
    remove_parser.add_argument("--project-dir", required=True)

    # ── Status subcommand ──
    status_parser = sub.add_parser("status", help="JSON status output")
    status_parser.add_argument("--project", required=True)
    status_parser.add_argument("--project-dir", required=True)
    status_parser.add_argument("--stub-aware", action="store_true", default=False)

    args = parser.parse_args(argv)
    engine = DeployEngine()

    try:
        if args.command == "deploy":
            result = engine.deploy(
                project=args.project,
                ref=args.ref,
                service=args.service,
                project_dir=args.project_dir,
                node=args.node,
                max_wait=args.max_wait,
                keep_images=args.keep_images,
            )
            # JSON output for shell facade
            print(
                json.dumps(
                    {
                        "success": result.success,
                        "project": result.project,
                        "ref": result.ref,
                        "service": result.service,
                        "previous_image": result.previous_image,
                        "rollback_performed": result.rollback_performed,
                        "first_deploy_failed": result.first_deploy_failed,
                        "error_message": result.error_message,
                    }
                )
            )
            return 0 if result.success else 1

        if args.command == "remove":
            result = engine.remove(project=args.project, project_dir=args.project_dir)
            print(
                json.dumps(
                    {
                        "success": result.success,
                        "project": result.project,
                        "already_removed": result.already_removed,
                        "error_message": result.error_message,
                    }
                )
            )
            return 0 if result.success else 1

        # status
        result = engine.status(
            project=args.project,
            project_dir=args.project_dir,
            stub_aware=args.stub_aware,
        )
        print(
            json.dumps(
                {
                    "project": result.project,
                    "node": result.node,
                    "status": result.status,
                    "containers": result.containers,
                    "last_deploy": result.last_deploy,
                }
            )
        )
        return 0
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
# endregion CLI
