# GREP_SUMMARY: hermes-images, resolve-images, compose-config-images, build-from-source, single-build, docker-compose-build, BUILD_TIMEOUT, L1-collapse
# STRUCTURE: ▶ resolve_compose_images ┌compose config --images┐ → ◇ rc≠0? → None │ → ∑ lines → ⎋ list │ ▶ build_images_from_source ┌compose build (BUILD_TIMEOUT)┐ → ◇ ok? → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Образы hermes-agent: resolve из compose config (single source of truth) и единый
##           build из source при отсутствии pre-built (L1→L2 коллапс DevPlan 002: L1 pull/bare-tag
##           удалены — единственный образ hermes-agent-context). Подмодуль пакета hermes_workflow/ (T3.7).
## @scope    Внутренний API пакета — вызывается deploy.handle_hermes_agent (impl). Публичный фасад —
##           пакетный __init__.handle_hermes_agent (DI-fallback wrapper). Зависимости: shared/docker_compose,
##           shared/timeouts. Ноль прямых docker-вызовов (гейт docker_sole_path).
## @invariants
##   1. resolve_compose_images: returncode≠0 ИЛИ пустой список → None (фатально, caller → False)
##   2. stdout может быть bytes (mock) — decode utf-8 у caller'а (нормализация строк)
##   3. build_images_from_source: ровно ОДИН docker compose build call (timeout=BUILD_TIMEOUT,
##      compose_args, БЕЗ flags — L1-механика удалена)
##   4. LDD-логи — байт-в-байт прежние: FUNC-слот [handle_hermes_agent] сохраняет workflow-фазу
##      (T3.7 TRAP[DECISION] в __init__ — единый формат траектории независимо от подмодуля)
## @rationale Q: Why images.py отдельно? A: T3.7 декомпозиция по связности — resolve/build образов
##   (pull/build/pin) — одна ответственность; TRAP[BUG] о hardcoded image drift живёт рядом с
##   resolution-логикой (knowledge locality).
## @changes  2026-08-22 | T3.7 simplify — извлечено из hermes_workflow.py (байт-в-байт LDD, DI через
##           аргументы; fallback _shared_* — в пакетном __init__)
# endregion MODULE_CONTRACT

import logging
import subprocess
from collections.abc import Callable

from core.internal.shared.timeouts import BUILD_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC_resolve_compose_images
## @purpose  Resolve актуальных образов hermes через `docker compose config --images`
##           (T4 fix — single source of truth, shared). None на фатальном сбое (config fail
##           или пустой результат) — caller возвращает False без build.
## @io       ⇥ compose_dir: str, compose_args: list[str],
##           compose_config_fn: Callable[..., CompletedProcess[str]]
##           ⎋ list[str] | None — строки образов (strip, без пустых) | None на фатальном сбое
## @complexity 1 — один compose config --images + splitlines
## @invariants
##   - returncode≠0 → IMP:10 config_fail → None
##   - stdout bytes → decode utf-8 (mock-совместимость)
##   - пустой результат → IMP:10 no_images → None
## ⚠️ TRAP[BUG] · 2026-07-17 · P1 · Hardcoded hermes images drifted from compose
## · Symptom: hermes-agent deployed with stale image (tronyx161/hermes-agent-tronyx-lab:latest
## ·   vs tronyxlab/hermes-agent-context:v2026.7.1), no tty/command → restart loop 101 times
## · Root: hardcoded image names duplicated knowledge — compose and deploy-modules.sh diverged
## · Fix: derive images from `docker compose config --images` (single source of truth)
## · Prevention: deploy-modules.sh must NOT hardcode any image names — always resolve from compose
def resolve_compose_images(
    compose_dir: str,
    compose_args: list[str],
    compose_config_fn: Callable[..., subprocess.CompletedProcess[str]],
) -> list[str] | None:
    img_result = compose_config_fn(
        compose_dir,
        compose_args=compose_args,
        flags=["--images"],
    )
    if img_result.returncode != 0:
        logger.error("[IMP:10][handle_hermes_agent][config_fail] Failed to resolve images from compose config")
        return None
    img_stdout = img_result.stdout
    if isinstance(img_stdout, bytes):
        img_stdout = img_stdout.decode("utf-8")
    hermes_images = [line.strip() for line in img_stdout.splitlines() if line.strip()]

    if not hermes_images:
        logger.error("[IMP:10][handle_hermes_agent][no_images] No images resolved from compose config")
        return None
    return hermes_images


# endregion FUNC_resolve_compose_images


# region FUNC_build_images_from_source
## @purpose  Единый build hermes-образа из source (L1→L2 коллапс DevPlan 002: L1 pull/bare-tag
##           удалены) — fallback при отсутствии pre-built в registry.
## @io       ⇥ compose_dir: str, compose_args: list[str],
##           compose_build_fn: Callable[..., bool] → ⎋ bool: True = built, False = build fail
## @complexity 1 — один docker compose build call (timeout=BUILD_TIMEOUT)
## @invariants
##   - Ровно ОДИН build call (single-build контракт; L1-chain не существует)
##   - timeout=BUILD_TIMEOUT, compose_args — БЕЗ flags (L1-механика удалена)
##   - False → IMP:10 build_fail → caller возвращает False (деплой abort, hermes-critical)
def build_images_from_source(
    compose_dir: str,
    compose_args: list[str],
    compose_build_fn: Callable[..., bool],
) -> bool:
    logger.info("[IMP:7][handle_hermes_agent][build] Building hermes-agent locally (fallback)")
    if not compose_build_fn(
        compose_dir,
        timeout=BUILD_TIMEOUT,
        compose_args=compose_args,
    ):
        logger.error("[IMP:10][handle_hermes_agent][build_fail] Local build failed")
        return False
    logger.info("[IMP:9][handle_hermes_agent][built] Hermes-agent built locally")
    return True


# endregion FUNC_build_images_from_source
