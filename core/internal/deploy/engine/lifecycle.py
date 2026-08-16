#!/usr/bin/env python3
# GREP_SUMMARY: deploy-engine-lifecycle, save-previous-image, rollback, first-deploy, docker-tag, force-recreate, 170-W4-B2
# STRUCTURE: ▶ save_previous_image ┌project_dir, service┐ → images -q → ◇ none? → ⎋ None (first deploy) →
#            docker_ops inspect/tag → ⎋ ImageInfo │ ▶ perform_rollback → docker tag + up --force-recreate → ⎋ bool │
#            ▶ handle_first_deploy → first_deploy.handle_first_deploy → ⚡ PlatformFatalError (exit 10)
# region MODULE_CONTRACT
## @purpose  Жизненный цикл деплоя (170 W4-B2): сохранение предыдущего образа, rollback, first-deploy-эскалация.
## @scope    core/internal/deploy/engine/lifecycle.py — вызывается из DeployEngine (engine.py) и тестов
##           (test_deploy_engine: save_previous_image/perform_rollback/handle_first_deploy).
## @invariants
##   1. save_previous_image вызывается ДО pull (порядок критичен для rollback — T1)
##   2. perform_rollback: re-tag предыдущего образа → docker compose up --force-recreate (T1)
##   3. handle_first_deploy ВСЕГДА raise PlatformFatalError (exit 10, нет rollback — DevPlan 116 B4 T3.1)
## @rationale Единственный holder `shared_docker_compose_up` — engine/flow.py (используется и up_atomic,
##            и perform_rollback): lifecycle читает атрибут flow-модуля в рантайме, чтобы тест-патч
##            границы (test_deploy_engine deploy_boundary) покрывал ОБА потребителя одним target'ом.
## @changes 170 W4-B2 — extracted from deploy_engine.py; 170 private-imports: приватные имена
##           шагов переименованы в публичные (U-07)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

# 🧐 TRAP[DECISION] · 2026-08-15 · — · Единый patchable holder `shared_docker_compose_up` — engine/flow.py
# · Rejected: lifecycle держит собственный биндинг docker_compose_up + тест патчит оба модуля (8-й патч в фикстуре)
# · Reason: один holder → один тест-патч-таргет покрывает и up_atomic (flow), и rollback (lifecycle)
# · Rev: если тест-фикстура deploy_boundary перестанет патчить единый таргет — вернуть биндинг в lifecycle
from core.internal.deploy.engine import flow as _flow
from core.internal.deploy.engine.results import ImageInfo

# DevPlan 128 W1 (P2-5/D6): docker image inspect/tag примитивы — shared/docker_ops
# (единственный слой, гейт docker_sole_path).
from core.internal.shared import docker_ops
from core.internal.shared.docker_compose import (
    docker_compose_images as _shared_docker_compose_images,
)
from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC_save_previous_image
## @purpose  Save current image ID before pull (enables rollback).
## @io       ⇥ project_dir, service → ⎋ Optional[ImageInfo]
## @complexity — O(1) — one docker compose images call + optional docker inspect
## @invariants
##   - Called BEFORE pull (critical ordering for rollback)
##   - Returns None if no previous image (first deploy)
##   - If image tag is <none\>:<none\>, creates fallback tag `project:previous-rollback`
def save_previous_image(project_dir: str, service: str) -> ImageInfo | None:
    """Save current image ID and tag before deploy (extracted from DeployEngine — 170 W4-B2)."""
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


# endregion FUNC_save_previous_image


# region FUNC_perform_rollback
## @purpose  Rollback to previous image: re-tag + docker compose up --force-recreate.
## @io       ⇥ project_dir, service, previous_image → ⎋ bool
## @complexity — O(1) — tag + compose up calls
## @invariants
##   - Re-tags previous image before compose up (ensures correct image reference)
##   - Uses --force-recreate to ensure container replacement
##   - Returns False if rollback compose up fails
def perform_rollback(project_dir: str, service: str, previous_image: ImageInfo | None) -> bool:
    """Rollback to previous image (extracted from DeployEngine — 170 W4-B2)."""
    if previous_image is None:
        logger.error("[IMP:10][rollback] No previous image — cannot rollback")
        return False

    logger.info("[IMP:10][rollback] ROLLING BACK %s to %s", service, previous_image.id)

    # Re-tag previous image (docker tag — локальная image-операция; W1: shared/docker_ops)
    if previous_image.tag:
        docker_ops.docker_tag(previous_image.id, previous_image.tag)
        logger.info("[IMP:9][rollback] Re-tagged %s → %s", previous_image.id, previous_image.tag)

    # docker compose up -d --force-recreate (T5.4: shared docker_compose_up — sole path).
    # Единственный holder shared_docker_compose_up — engine/flow.py (см. @rationale модуля):
    # читаем атрибут flow-модуля в рантайме, чтобы патч границы (test_deploy_engine) покрыл
    # и up_atomic (flow), и rollback (lifecycle) одним target'ом.
    if not _flow.shared_docker_compose_up(
        project_dir,
        timeout=COMPOSE_UP_TIMEOUT,
        service=service,
        flags=["--force-recreate"],
    ):
        logger.error("[IMP:10][rollback] Rollback compose up FAILED for %s", service)
        return False

    logger.info("[IMP:10][rollback] Rollback complete: %s restored to %s", service, previous_image.id)
    return True


# endregion FUNC_perform_rollback


# region FUNC_handle_first_deploy
## @purpose  Handle first deploy failure — no rollback possible, escalate.
##           DevPlan 119 E4: реализация — deploy/first_deploy.py (handle_first_deploy).
## @io       ⇥ project, service, ref, reason → ⎋ None (raises PlatformFatalError, exit 10)
def handle_first_deploy(project: str, service: str, ref: str, reason: str) -> None:
    """Handle first deploy failure — no rollback possible (extracted from DeployEngine — 170 W4-B2).

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


# endregion FUNC_handle_first_deploy
