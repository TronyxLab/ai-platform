#!/usr/bin/env python3
# GREP_SUMMARY: hermes-workflow, hermes-agent, L1, L2, pull-or-build, ghcr, compose-config-images, D1, docker-orchestrator-decomposition
# STRUCTURE: ▶ _handle_hermes_agent ┌compose_args + module_dir┐ → ◇ compose config --images → ⊕ hermes_images → ◇ all found? → ⎋ True │ → ◇ L1 inspect → pull GHCR / build L1 → ◇ L1→L2 build → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Hermes-agent pre-deploy workflow — экстракция из docker_orchestrator.py
##           (DevPlan 118 D1, _handle_hermes_agent, ~123 LOC): проверка pre-built образов
##           в registry, L1 pull из GHCR, L1→L2 build fallback при отсутствии.
## @scope    bootstrap/deploy — вызывается docker_orchestrator.deploy_docker_module
##           (module_name == "hermes-agent"). Зависимости только shared-слой + platform_config.
## @invariants
##   1. L1 base image pulled from GHCR first, then built from source if pull fails
##   2. L1→L2 build runs docker compose build with --profile
##   3. Failure to resolve images from compose config is fatal (return False)
##   4. If ALL images exist in registry, returns True immediately (no build needed)
##   5. Все docker compose вызовы — через shared/docker_compose.py (гейт docker_sole_path)
## @rationale Q: Why automatic build instead of FAIL? A: deploy cycle time — manual
##   make hermes-build-context adds ~2 min to deploy. Automatic fallback on 404 reduces
##   deploy cycle from 3 steps to 1, matching the FAIL semantics but avoiding manual work.
##   DevPlan 118 D1: выделение спец-workflow hermes-agent из оркестратора (AC-D1).
## @changes  2026-08-02 | DevPlan 118 D1 — экстракция из docker_orchestrator.py (чистая, без смены контрактов)
##           2026-08-14 | DevPlan 167 D2 — DI-швы handle_hermes_agent: docker-объект, compose-fn ×3,
##           ghcr_org (0 monkeypatch в тестах; _shared_* fallback сохраняет unittest-patch совместимость)
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from core.internal.config import platform_config
from core.internal.shared import docker_ops  # W1: docker image inspect/pull примитивы (гейт docker_sole_path)
from core.internal.shared.docker_compose import (
    check_image_exists as _shared_check_image_exists,
)
from core.internal.shared.docker_compose import (
    docker_compose_build as _shared_docker_compose_build,
)
from core.internal.shared.docker_compose import (
    docker_compose_config as _shared_docker_compose_config,
)
from core.internal.shared.timeouts import (
    BUILD_TIMEOUT,
    IMAGE_CHECK_TIMEOUT,
    PULL_TIMEOUT,
)

logger = logging.getLogger(__name__)

# ── Константы hermes-agent (спец-workflow) ──
L1_BASE_IMAGE = "hermes-agent-base"
GHCR_ORG = os.environ.get("GHCR_ORG", "ghcr.io/tronyx161")


# region FUNC_handle_hermes_agent
## @purpose  Handle hermes-agent special case: check image existence, L1 pull from GHCR,
##           L1→L2 build fallback if image not found. This is a pre-deploy step.
## @io       ⇥ compose_args: list[str], module_dir: str, module_name: str
##           ⎋ bool: True if images are ready or built, False on fatal failure
## @complexity 3 — compose config --images + per-image check + conditional pull/build
## @invariants
##   - L1 base image is pulled from GHCR first, then built from source if pull fails
##   - L1→L2 build runs docker compose build with --profile
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
class _DockerOpsProto(Protocol):
    """Structural DI-контракт docker-объекта (167 D2): shared docker_ops или fake тестов.

    ## @purpose  Типизация DI-параметра docker (вместо Any): 3 примитива hermes-workflow.
    ## @complexity O(1) — декларация протокола
    """

    def docker_image_inspect_exists(self, image_ref: str, timeout: int = ...) -> bool: ...

    def docker_pull(self, image_ref: str, timeout: int = ..., runner: Callable[..., object] | None = ...) -> bool: ...

    def docker_tag(
        self, image_id: str, tag: str, timeout: int = ..., runner: Callable[..., object] | None = ...
    ) -> bool: ...


def handle_hermes_agent(
    compose_args: list[str],
    module_dir: str,
    module_name: str,
    *,
    docker: _DockerOpsProto | None = None,  # DI-объект docker_ops (docker_image_inspect_exists/pull/tag, 167 D2)
    compose_config_fn: Callable[..., subprocess.CompletedProcess[str]]
    | None = None,  # DI: docker_compose_config (None → _shared_*)
    check_image_exists_fn: Callable[..., bool] | None = None,  # DI: check_image_exists (None → _shared_*)
    compose_build_fn: Callable[..., bool] | None = None,  # DI: docker_compose_build (None → _shared_*)
    ghcr_org: str | None = None,  # env-dict DI для GHCR_ORG (None → константа модуля)
) -> bool:
    logger.info("[IMP:7][handle_hermes_agent][start] Handling hermes-agent pre-deploy checks")
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · DI-швы handle_hermes_agent: docker-объект + compose-fn + ghcr_org
    # · Rejected: прямой вызов docker_ops/_shared_*/GHCR_ORG (тест патчил модуль-глобалы monkeypatch.setattr)
    # · Reason: seam = тестируемость реального вызова; docker-объект — один DI-шов для 3 примитивов;
    # ·   _shared_* fallback читает модуль-глобал на вызове — unittest-patch (test_hermes_workflow) жив
    # · Rev: при консолидации compose-примитивов в единый объект-канал — слить compose_config_fn/
    # ·   check_image_exists_fn/compose_build_fn в один DI-объект
    dops = docker if docker is not None else docker_ops
    ghcr = ghcr_org or GHCR_ORG
    # ── Resolve actual images from compose config (T4 fix — single source of truth, shared) ──
    # ⚠️ TRAP[BUG] · 1.0.0 · HI · module_dir от caller'а = PARENT (core/modules), compose-файл —
    # · в module_dir/<module_name>/; старый init (compose_dir = module_dir) ломал compose config
    # · и L1 build fallback (bootstrap 1.0.0: /opt/platform/core/modules/docker-compose.base.yml).
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

    # ── Ensure L1 base image exists locally (W1: docker image inspect — shared/docker_ops, non-fatal) ──
    l1_exists = dops.docker_image_inspect_exists(f"{L1_BASE_IMAGE}:latest", timeout=IMAGE_CHECK_TIMEOUT)

    if not l1_exists:
        logger.info(
            "[IMP:7][handle_hermes_agent][l1_missing] L1 base image not found locally — attempting pull from GHCR"
        )
        # W1: docker pull — shared/docker_ops (non-fatal; сбой → build from source)
        if not dops.docker_pull(f"{ghcr}/{L1_BASE_IMAGE}:latest", timeout=PULL_TIMEOUT):
            logger.warning("[IMP:5][handle_hermes_agent][l1_pull_fail] L1 pull failed — building L1 from source")
            # Build L1 from source (shared docker_compose_build — sole path).
            # ⚠️ TRAP[BUG] · 1.0.0 · HI · base_compose брался как module_dir/docker-compose.base.yml
            # · (не существует — module_dir = PARENT); compose-файл лежит в module_dir/<module_name>/.
            # · Fix: переиспользовать compose-файл из -f в compose_args (резолвен выше в compose_dir).
            base_compose = str(Path(compose_dir) / "docker-compose.base.yml")
            compose_build = compose_build_fn if compose_build_fn is not None else _shared_docker_compose_build
            l1_ok = compose_build(
                str(Path(base_compose).parent),
                timeout=BUILD_TIMEOUT,
                compose_args=["-f", base_compose, "--profile", module_name],
                flags=[
                    "--build-arg",
                    f"CONTEXT={os.environ.get('CONTEXT', platform_config.default_context())}",
                ],
            )
            if not l1_ok:
                logger.error("[IMP:10][handle_hermes_agent][l1_build_fail] L1 build failed")
                return False
            logger.info("[IMP:9][handle_hermes_agent][l1_built] L1 built from source")
        else:
            logger.info("[IMP:9][handle_hermes_agent][l1_pulled] L1 pulled from GHCR")
            # L2 Dockerfile: FROM hermes-agent-base:latest (bare tag) — pulled образ
            # носит полное имя ghcr.io/...; без локального bare-тега L2 build падает
            # (Docker Hub pull attempt). Tag — идемпотентный no-op при существующем.
            if not dops.docker_tag(f"{ghcr}/{L1_BASE_IMAGE}:latest", f"{L1_BASE_IMAGE}:latest"):
                logger.warning("[IMP:7][handle_hermes_agent][l1_tag_fail] Cannot tag L1 as %s:latest", L1_BASE_IMAGE)

    # ── Build L1→L2 locally ──
    logger.info("[IMP:7][handle_hermes_agent][build] Building hermes-agent L1→L2 locally (fallback)")
    compose_build = compose_build_fn if compose_build_fn is not None else _shared_docker_compose_build
    if not compose_build(
        str(compose_dir),
        timeout=BUILD_TIMEOUT,
        compose_args=compose_args,
    ):
        logger.error("[IMP:10][handle_hermes_agent][build_fail] Local L1→L2 build failed")
        return False
    logger.info("[IMP:9][handle_hermes_agent][built] Hermes-agent built locally")
    return True


# endregion FUNC_handle_hermes_agent
