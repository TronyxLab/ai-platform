# GREP_SUMMARY: hermes-workflow, hermes-agent, deploy-phase, handle-hermes-agent, compose-dir-resolve, single-build, L1-collapse
# STRUCTURE: ▶ handle_hermes_agent ┌_resolve_compose_dir + images/verify helpers┐ → ◇ resolve → None? → False │ → ◇ all found? → True │ → ⎋ build_images_from_source
# region MODULE_CONTRACT
## @purpose  Деплой-фаза hermes-agent (D1 handle_hermes_agent): resolve compose-каталога и оркестрация
##           image-check/build через images.py/verify.py. Impl-функция пакета hermes_workflow/ (T3.7) —
##           DI-аргументы ОБЯЗАТЕЛЬНЫ (типобезопасный DAG, 0 циклических импортов); fallback _shared_*
##           живёт в пакетном __init__ wrapper'е (patch-compat test_hermes_workflow).
## @scope    Внутренний API пакета — импортируется ТОЛЬКО пакетным __init__ (как _handle_hermes_agent_impl).
##           Публичный вызов — hermes_workflow.handle_hermes_agent (docker_orchestrator._phase_hermes).
## @invariants
##   1. compose-каталог: module_dir/MODULE_NAME ИЛИ каталог явного -f файла из compose_args
##      (TRAP[BUG] 1.0.0: module_dir от caller'а = PARENT core/modules)
##   2. resolve-compose-images fail (None) → False (фатально, без build)
##   3. все образы найдены → True (build не вызывается)
##   4. missing → единственный build из source (BUILD_TIMEOUT) → True/False
##   5. LDD-логи — байт-в-байт прежние (FUNC-слот [handle_hermes_agent] — единая workflow-траектория)
##   6. missing → pre-pull пинненных баз hermes Dockerfile (docker_prebuild_pull, F-03) ДО build;
##      best-effort: False ИЛИ exception НЕ абортят сборку — build остаётся арбитром (как _phase_rebuild)
## @rationale Q: Why impl с обязательными DI-аргументами? A: T3.7 — fallback _shared_* в подмодуле
##   потребовал бы self-import пакета (цикл, RED acyclic-internal-domains, 170 W10-B); обязательные
##   аргументы делают зависимость явной, wrapper __init__ — единственная точка fallback (TRAP[DI-SEAM]).
## @changes  2026-08-22 | T3.7 simplify — извлечено из hermes_workflow.py: impl + _resolve_compose_dir
## @changes  2026-08-27 | F-03 (017-launch-validation P0) — prebuild_pull_fn DI-шов + pre-pull баз
##           ДО build fallback (холодный bootstrap hermes упал на ноде первым — _phase_rebuild
##           pre-pull не покрывал hermes_workflow, сборка идёт через compose_build_fn DI)
# endregion MODULE_CONTRACT

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from .images import build_images_from_source, resolve_compose_images
from .verify import verify_images_present

logger = logging.getLogger(__name__)


# region FUNC__resolve_compose_dir
## @purpose  Resolve compose-каталог деплой-фазы: module_dir/MODULE_NAME (родительский core/modules)
##           либо каталог явного -f файла из compose_args (перекрывает дефолт).
## @io       ⇥ compose_args: list[str], module_dir: str, module_name: str → ⎋ Path — каталог compose
## @complexity O(k) — k = число compose_args (поиск первого -f)
## @invariants
##   - Default: Path(module_dir) / module_name — compose-файл лежит в подкаталоге модуля
##   - `-f <path>` → каталог родителя явного файла (приоритет)
## ⚠️ TRAP[BUG] · 1.0.0 · HI · module_dir от caller'а = PARENT (core/modules), compose-файл —
## · в module_dir/MODULE_NAME/; старый init (compose_dir = module_dir) ломал compose config
## · и build fallback (bootstrap 1.0.0: /opt/platform/core/modules/docker-compose.base.yml).
def _resolve_compose_dir(compose_args: list[str], module_dir: str, module_name: str) -> Path:
    compose_dir = Path(module_dir) / module_name
    for i, arg in enumerate(compose_args):
        if arg == "-f" and i + 1 < len(compose_args):
            compose_dir = Path(compose_args[i + 1]).parent
            break
    return compose_dir


# endregion FUNC__resolve_compose_dir


# region FUNC_handle_hermes_agent
## @purpose  Handle hermes-agent special case: check image existence, single build from source
##           if image not found. Pre-deploy step. Impl — DI-аргументы обязательные (см. MODULE_CONTRACT);
##           публичный wrapper с fallback — пакетный __init__.handle_hermes_agent.
## @io       ⇥ compose_args: list[str], module_dir: str, module_name: str
##           ⎋ bool: True if images are ready or built, False on fatal failure
## @complexity 2 — compose config --images + per-image check + conditional build
## @invariants
##   - Image resolution via compose config --images (single source of truth)
##   - Missing image → pre-pull баз (docker_prebuild_pull, F-03) ДО build; best-effort:
##     False/exception → build proceeds (build — арбитр)
##   - Failure to resolve images from compose config is fatal (return False)
##   - If ALL images exist in registry, returns True immediately (no build needed)
def handle_hermes_agent(
    compose_args: list[str],
    module_dir: str,
    module_name: str,
    *,
    compose_config_fn: Callable[..., subprocess.CompletedProcess[str]],
    check_image_exists_fn: Callable[..., bool],
    compose_build_fn: Callable[..., bool],
    prebuild_pull_fn: Callable[..., bool],
) -> bool:
    logger.info("[IMP:7][handle_hermes_agent][start] Handling hermes-agent pre-deploy checks")
    compose_dir = _resolve_compose_dir(compose_args, module_dir, module_name)
    images = resolve_compose_images(str(compose_dir), compose_args, compose_config_fn)
    if images is None:
        return False
    if verify_images_present(images, check_image_exists_fn):
        return True
    # ── F-03 (017-launch-validation P0): pre-pull пинненных баз hermes Dockerfile ДО первого
    #    compose build (fallback build-from-source). _phase_rebuild pre-pull НЕ покрывает hermes —
    #    его сборка идёт этим workflow (compose_build_fn DI); при холодном bootstrap hermes упал
    #    на ноде первым (BuildKit не ретраит pull). Best-effort, как в _phase_rebuild: False ИЛИ
    #    exception НЕ абортят сборку — build остаётся арбитром (база может быть в локальном кеше).
    hermes_module_dir = str(Path(module_dir) / module_name)
    try:
        pre_pull_ok = prebuild_pull_fn(hermes_module_dir)
    # ruff: ignore[BLE001] — pre-pull best-effort: exception не должен ронять деплой (build — арбитр)
    except Exception as exc:  # noqa: EXC — pre-pull best-effort: exception не должен ронять деплой
        logger.warning(
            "[IMP:7][handle_hermes_agent][prebuild_pull_exc] Pre-pull of base images raised for %s: %s — build proceeds (build is the arbiter)",
            module_name,
            exc,
        )
    else:
        if not pre_pull_ok:
            # docker_prebuild_pull сам логирует IMP:10 при исчерпании ретраев; здесь — мягкое
            # продолжение (build — арбитр, fail-fast был бы регрессией холодного bootstrap).
            logger.warning(
                "[IMP:7][handle_hermes_agent][prebuild_pull_fail] Pre-pull of base images failed for %s — build proceeds (may fail on pull)",
                module_name,
            )
        logger.info("[IMP:8][handle_hermes_agent][prebuild_pull] Base-image pre-pull finished for %s", module_name)
    return build_images_from_source(str(compose_dir), compose_args, compose_build_fn)


# endregion FUNC_handle_hermes_agent
