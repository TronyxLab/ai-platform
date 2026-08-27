# GREP_SUMMARY: hermes-workflow, hermes-agent, single-build, compose-config-images, build-from-source, docker-orchestrator-decomposition, L1-collapse, re-export, DI-seam, unittest-patch
# STRUCTURE: ┌shared compose-примитивы + impl handle_hermes_agent┐ → ◇ DI-fallback wrapper (_shared_* модуль-глобал на вызове) → ⚡ __all__ handle_hermes_agent → ⎋ полный прежний API
# region MODULE_CONTRACT
## @purpose  Hermes-agent pre-deploy workflow — пакет (T3.7 simplify: hermes_workflow.py → hermes_workflow/).
##           Подмодули по связности: images.py (resolve/build образов), verify.py (верификация наличия
##           образов в registry), deploy.py (деплой-фаза handle_hermes_agent). __init__ реэкспортирует
##           полный прежний API модуля и держит DI-fallback _shared_* (patch-compat для test_hermes_workflow).
## @scope    bootstrap/deploy — вызывается docker_orchestrator.deploy_docker_module
##           (module_name == "hermes-agent"). Зависимости только shared-слой + platform_config.
##           Переименование bootstrap/deploy → bootstrap/deployment выполняется отдельным процессом —
##           путь пакета сохраняется как есть (path-preserving).
## @invariants
##   1. Полный прежний API: handle_hermes_agent + _shared_docker_compose_config/_shared_check_image_exists/
##      _shared_docker_compose_build/_shared_docker_prebuild_pull + BUILD_TIMEOUT + logger
##      (имена-атрибуты пакета — unittest.patch цели)
##   2. DI-fallback БЕЗ циклических импортов: _shared_* читаются из модуль-глобала __init__ НА ВЫЗОВЕ
##      (wrapper handle_hermes_agent); подмодули — чистый DAG (import-linter acyclic-internal-domains)
##   3. Image resolution via `docker compose config --images` (single source of truth)
##   4. If ALL images exist in registry → returns True immediately (no build needed)
##   5. Missing image → pre-pull пинненных баз (F-03, best-effort) + единственный docker compose
##      build из source (BUILD_TIMEOUT)
##   6. Failure to resolve images from compose config is fatal (return False)
##   7. Все docker compose вызовы — через shared/docker_compose.py (гейт docker_sole_path)
##   8. НЕТ L1 pull / GHCR org / bare-tag / docker_tag — L1 схлопнут в L2 (DevPlan 002)
## @rationale Q: Why package split? A: T3.7 simplify — декомпозиция по связности (≤400-500 LOC/файл,
##   ноль дублей); единая точка DI-fallback в __init__ сохраняет unittest.patch-совместимость
##   (TRAP[DI-SEAM]) без submodule→package self-import (запрещён acyclic-internal-domains, 170 W10-B).
##   Q: Why automatic build instead of FAIL? A: deploy cycle time — manual make hermes-build-context
##   adds ~2 min to deploy. Automatic fallback on 404 reduces deploy cycle from 3 steps to 1.
##   DevPlan 118 D1: выделение спец-workflow hermes-agent из оркестратора (AC-D1).
##   DevPlan 002 W2 T2.2: L1-механика удалена — единый образ собирается из source.
## @changes  2026-08-02 | DevPlan 118 D1 — экстракция из docker_orchestrator.py (чистая, без смены контрактов)
##           2026-08-14 | DevPlan 167 D2 — DI-швы handle_hermes_agent: docker-объект, compose-fn ×3,
##           ghcr_org (0 monkeypatch в тестах; _shared_* fallback сохраняет unittest-patch совместимость)
##           2026-08-16 | DevPlan 002 W2 T2.2 — L1_BASE_IMAGE/GHCR_ORG/docker_ops/docker/ghcr_org удалены;
##           flow = config --images → all found? True : compose build (один build call)
##           2026-08-22 | T3.7 simplify — hermes_workflow.py → пакет hermes_workflow/ (images.py, verify.py,
##           deploy.py, __main__.py); DI-fallback перенесён в wrapper __init__ (patch-compat, 0 циклов)
##           2026-08-27 | F-03 (017-launch-validation P0) — prebuild_pull_fn DI-шов (fallback
##           _shared_docker_prebuild_pull): pre-pull баз hermes Dockerfile ДО build fallback
# 🧐 TRAP[DECISION] · 2026-08-22 · — · LDD-логи при split: FUNC-слот [handle_hermes_agent] сохранён
# · в извлечённых helpers (images/verify) · Rejected: per-function слоты ([resolve_compose_images],
# · [verify_images_present], [build_images_from_source]) · Reason: байт-в-байт траектория workflow-фазы +
# · test_handle_hermes_build_fail ассертит "[IMP:10][handle_hermes_agent][build_fail]" в caplog.text;
# · единый слот — контракт «одна бизнес-фаза = один FUNC-тег» · Rev: при консолидации LDD-формата
# · (единый конфигуратор логов) — пересмотреть FUNC-теги извлечённых подмодулей
# endregion MODULE_CONTRACT

import logging
import subprocess
from collections.abc import Callable

from core.internal.shared.docker_compose import check_image_exists as _shared_check_image_exists
from core.internal.shared.docker_compose import docker_compose_build as _shared_docker_compose_build
from core.internal.shared.docker_compose import docker_compose_config as _shared_docker_compose_config
from core.internal.shared.docker_compose import docker_prebuild_pull as _shared_docker_prebuild_pull
from core.internal.shared.timeouts import BUILD_TIMEOUT

from .deploy import handle_hermes_agent as _handle_hermes_agent_impl

logger = logging.getLogger(__name__)


# region FUNC_handle_hermes_agent
## @purpose  Handle hermes-agent special case: check image existence, single build from source
##           if image not found. This is a pre-deploy step. Публичный фасад пакета (полный прежний
##           API) — DI-fallback wrapper: подставляет _shared_* модуль-глобалы пакета, если DI-аргумент
##           не передан (unittest.patch test_hermes_workflow читает именно эти атрибуты пакета).
## @io       ⇥ compose_args: list[str], module_dir: str, module_name: str
##           ⎋ bool: True if images are ready or built, False on fatal failure
## @complexity 2 — compose config --images + per-image check + conditional build
## @invariants
##   - Image resolution via compose config --images (single source of truth)
##   - Missing image → pre-pull баз (F-03, best-effort) + единственный docker compose build из source
##   - Failure to resolve images from compose config is fatal (return False)
##   - If ALL images exist in registry, returns True immediately (no build needed)
## @rationale Q: Why wrapper instead of in-place fallback? A: T3.7 split — fallback обязан читать
##   модуль-глобал НА ВЫЗОВЕ для unittest.patch-совместимости (TRAP[DI-SEAM]); в подмодуле это
##   потребовало бы self-import пакета (цикл — RED acyclic-internal-domains). Wrapper — единственная
##   точка fallback, подмодули получают конкретные функции (типобезопасный DAG).
# 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · DI-швы handle_hermes_agent: compose-fn ×3
# · Rejected: прямой вызов _shared_*/GHCR_ORG (тест патчил модуль-глобалы monkeypatch.setattr)
# · Reason: seam = тестируемость реального вызова; docker-объект удалён DevPlan 002 (L1 коллапс);
# ·   _shared_* fallback читает модуль-глобал пакета на вызове (wrapper __init__) — unittest-patch
# ·   (test_hermes_workflow) жив; T3.7: fallback вынесен в wrapper — patch-цели не изменились;
# ·   F-03 (2026-08-27): +prebuild_pull_fn — четвёртый DI-шов (fallback _shared_docker_prebuild_pull)
# · Rev: при консолидации compose-примитивов в единый объект-канал — слить compose_config_fn/
# ·   check_image_exists_fn/compose_build_fn/prebuild_pull_fn в один DI-объект
def handle_hermes_agent(
    compose_args: list[str],
    module_dir: str,
    module_name: str,
    *,
    compose_config_fn: Callable[..., subprocess.CompletedProcess[str]]
    | None = None,  # DI: docker_compose_config (None → пакетный _shared_*)
    check_image_exists_fn: Callable[..., bool] | None = None,  # DI: check_image_exists (None → пакетный _shared_*)
    compose_build_fn: Callable[..., bool] | None = None,  # DI: docker_compose_build (None → пакетный _shared_*)
    prebuild_pull_fn: Callable[..., bool] | None = None,  # DI: docker_prebuild_pull (None → пакетный _shared_*)
) -> bool:
    return _handle_hermes_agent_impl(
        compose_args,
        module_dir,
        module_name,
        compose_config_fn=compose_config_fn if compose_config_fn is not None else _shared_docker_compose_config,
        check_image_exists_fn=check_image_exists_fn
        if check_image_exists_fn is not None
        else _shared_check_image_exists,
        compose_build_fn=compose_build_fn if compose_build_fn is not None else _shared_docker_compose_build,
        prebuild_pull_fn=prebuild_pull_fn if prebuild_pull_fn is not None else _shared_docker_prebuild_pull,
    )


# endregion FUNC_handle_hermes_agent

# Публичный контракт пакета (RUF022: case-sensitive ASCII сортировка):
# единственное публичное имя — handle_hermes_agent; _shared_* — patch-цели unittest (НЕ public API)
__all__ = ["handle_hermes_agent"]
