#!/usr/bin/env python3
# GREP_SUMMARY: hermes-workflow, hermes-agent, single-build, compose-config-images, build-from-source, docker-orchestrator-decomposition, L1-collapse
# STRUCTURE: ▶ _handle_hermes_agent ┌compose_args + module_dir┐ → ◇ compose config --images → ◇ all found? → ⎋ True │ → ◇ compose build (BUILD_TIMEOUT) → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Hermes-agent pre-deploy workflow — экстракция из docker_orchestrator.py
##           (DevPlan 118 D1, _handle_hermes_agent, ~123 LOC): проверка pre-built образов
##           в registry, единый build из source при отсутствии (L1→L2 коллапс DevPlan 002:
##           L1 pull/bare-tag/build удалены — единственный образ hermes-agent-context).
## @scope    bootstrap/deploy — вызывается docker_orchestrator.deploy_docker_module
##           (module_name == "hermes-agent"). Зависимости только shared-слой + platform_config.
## @invariants
##   1. Image resolution via `docker compose config --images` (single source of truth)
##   2. If ALL images exist in registry → returns True immediately (no build needed)
##   3. Missing image → единственный docker compose build из source (BUILD_TIMEOUT)
##   4. Failure to resolve images from compose config is fatal (return False)
##   5. Все docker compose вызовы — через shared/docker_compose.py (гейт docker_sole_path)
##   6. НЕТ L1 pull / GHCR org / bare-tag / docker_tag — L1 схлопнут в L2 (DevPlan 002)
## @rationale Q: Why automatic build instead of FAIL? A: deploy cycle time — manual
##   make hermes-build-context adds ~2 min to deploy. Automatic fallback on 404 reduces
##   deploy cycle from 3 steps to 1, matching the FAIL semantics but avoiding manual work.
##   DevPlan 118 D1: выделение спец-workflow hermes-agent из оркестратора (AC-D1).
##   DevPlan 002 W2 T2.2: L1-механика удалена — единый образ собирается из source
##   (единый Dockerfile: base-стадия + final-стадия).
## @changes  2026-08-02 | DevPlan 118 D1 — экстракция из docker_orchestrator.py (чистая, без смены контрактов)
##           2026-08-14 | DevPlan 167 D2 — DI-швы handle_hermes_agent: docker-объект, compose-fn ×3,
##           ghcr_org (0 monkeypatch в тестах; _shared_* fallback сохраняет unittest-patch совместимость)
##           2026-08-16 | DevPlan 002 W2 T2.2 — L1_BASE_IMAGE/GHCR_ORG/docker_ops/docker/ghcr_org удалены;
##           flow = config --images → all found? True : compose build (один build call)
# endregion MODULE_CONTRACT

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from core.internal.shared.docker_compose import (
    check_image_exists as _shared_check_image_exists,
)
from core.internal.shared.docker_compose import (
    docker_compose_build as _shared_docker_compose_build,
)
from core.internal.shared.docker_compose import (
    docker_compose_config as _shared_docker_compose_config,
)
from core.internal.shared.timeouts import BUILD_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC_handle_hermes_agent
## @purpose  Handle hermes-agent special case: check image existence, single build from source
##           if image not found. This is a pre-deploy step.
## @io       ⇥ compose_args: list[str], module_dir: str, module_name: str
##           ⎋ bool: True if images are ready or built, False on fatal failure
## @complexity 2 — compose config --images + per-image check + conditional build
## @invariants
##   - Image resolution via compose config --images (single source of truth)
##   - Missing image → единственный docker compose build из source
##   - Failure to resolve images from compose config is fatal (return False)
##   - If ALL images exist in registry, returns True immediately (no build needed)
## @rationale Q: Why automatic build instead of FAIL? A: deploy cycle time — manual
##   make hermes-build-context adds ~2 min to deploy. Automatic fallback on 404 reduces
##   deploy cycle from 3 steps to 1, matching the FAIL semantics but avoiding manual work.
## ⚠️ TRAP[BUG] · 2026-07-17 · P1 · Hardcoded hermes images drifted from compose
## · Symptom: hermes-agent deployed with stale image (tronyx161/hermes-agent-tronyx-lab:latest
## ·   vs tronyxlab/hermes-agent-context:v2026.7.1), no tty/command → restart loop 101 times
## · Root: hardcoded image names duplicated knowledge — compose and deploy-modules.sh diverged
## · Fix: derive images from `docker compose config --images` (single source of truth)
## · Prevention: deploy-modules.sh must NOT hardcode any image names — always resolve from compose
def handle_hermes_agent(
    compose_args: list[str],
    module_dir: str,
    module_name: str,
    *,
    compose_config_fn: Callable[..., subprocess.CompletedProcess[str]]
    | None = None,  # DI: docker_compose_config (None → _shared_*)
    check_image_exists_fn: Callable[..., bool] | None = None,  # DI: check_image_exists (None → _shared_*)
    compose_build_fn: Callable[..., bool] | None = None,  # DI: docker_compose_build (None → _shared_*)
) -> bool:
    logger.info("[IMP:7][handle_hermes_agent][start] Handling hermes-agent pre-deploy checks")
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · DI-швы handle_hermes_agent: compose-fn ×3
    # · Rejected: прямой вызов _shared_*/GHCR_ORG (тест патчил модуль-глобалы monkeypatch.setattr)
    # · Reason: seam = тестируемость реального вызова; docker-объект удалён DevPlan 002 (L1 коллапс);
    # ·   _shared_* fallback читает модуль-глобал на вызове — unittest-patch (test_hermes_workflow) жив
    # · Rev: при консолидации compose-примитивов в единый объект-канал — слить compose_config_fn/
    # ·   check_image_exists_fn/compose_build_fn в один DI-объект
    # ── Resolve actual images from compose config (T4 fix — single source of truth, shared) ──
    # ⚠️ TRAP[BUG] · 1.0.0 · HI · module_dir от caller'а = PARENT (core/modules), compose-файл —
    # · в module_dir/<module_name>/; старый init (compose_dir = module_dir) ломал compose config
    # · и build fallback (bootstrap 1.0.0: /opt/platform/core/modules/docker-compose.base.yml).
    compose_dir = Path(module_dir) / module_name
    for i, arg in enumerate(compose_args):
        if arg == "-f" and i + 1 < len(compose_args):
            compose_dir = Path(compose_args[i + 1]).parent
            break
    compose_config = compose_config_fn if compose_config_fn is not None else _shared_docker_compose_config
    img_result = compose_config(
        str(compose_dir),
        compose_args=compose_args,
        flags=["--images"],
    )
    if img_result.returncode != 0:
        logger.error("[IMP:10][handle_hermes_agent][config_fail] Failed to resolve images from compose config")
        return False
    img_stdout = img_result.stdout
    if isinstance(img_stdout, bytes):
        img_stdout = img_stdout.decode("utf-8")
    hermes_images = [line.strip() for line in img_stdout.splitlines() if line.strip()]

    if not hermes_images:
        logger.error("[IMP:10][handle_hermes_agent][no_images] No images resolved from compose config")
        return False

    # ── Check each image ──
    check_image_exists = check_image_exists_fn if check_image_exists_fn is not None else _shared_check_image_exists
    all_found = True
    for img in hermes_images:
        if not check_image_exists(img):
            all_found = False
            logger.warning(
                "[IMP:5][handle_hermes_agent][missing] Pre-built image not found: %s — will build locally", img
            )

    if all_found:
        logger.info("[IMP:9][handle_hermes_agent][all_found] All hermes-agent images found in registry")
        return True

    # ── Single build from source (L1→L2 коллапс DevPlan 002: L1 pull/bare-tag удалены) ──
    logger.info("[IMP:7][handle_hermes_agent][build] Building hermes-agent locally (fallback)")
    compose_build = compose_build_fn if compose_build_fn is not None else _shared_docker_compose_build
    if not compose_build(
        str(compose_dir),
        timeout=BUILD_TIMEOUT,
        compose_args=compose_args,
    ):
        logger.error("[IMP:10][handle_hermes_agent][build_fail] Local build failed")
        return False
    logger.info("[IMP:9][handle_hermes_agent][built] Hermes-agent built locally")
    return True


# endregion FUNC_handle_hermes_agent
