#!/usr/bin/env python3
# GREP_SUMMARY: compose-args, build-compose-args, resolve-compose-file, docker-compose-args, leaf, 170, cycle-break, bootstrap-deploy
# STRUCTURE: ▶ resolve_compose_file ┌module_dir┐ → canonical scan (shared compose_files) → ⎋ Path | None │ ▶ build_compose_args ┌compose_file + env + overlay + profile┐ → root-compose-first -f + --env-file + overlay + --profile → ⎋ list[str]
# region MODULE_CONTRACT
## @purpose  Leaf-модуль compose-args (DevPlan 170 W10-B): публичные build_compose_args /
##           resolve_compose_file — вынесены из docker_orchestrator.py (были приватные
##           _build_compose_args/_resolve_compose_file) для разрыва import-цикла
##           parallel_runner ↔ docker_orchestrator (wave-briefs W10-design п.4).
## @scope    Только построение docker compose CLI-аргументов и резолв compose-файла —
##           НИКАКОЙ оркестрации (деплой/healthcheck/rollback). Потребители:
##           docker_orchestrator (приватные алиасы U-07), parallel_runner (module-level).
## @invariants
##   - Чистый leaf: импортирует ТОЛЬКО core.internal.shared (compose_files, deploy_paths) —
##     нет зависимостей на bootstrap/deploy-сиблингов (без циклов)
##   - root compose ПЕРВЫМ и ЕДИНСТВЕННЫМ -f (TRAP[BUG] RC 121, U-49): при наличии
##     root docker-compose.yml модуль деплоится ТОЛЬКО через root (+ --profile module);
##     модульный файл отдельно — только когда root отсутствует (fallback)
##   - --env-file добавляется только если файл существует (secrets.env / platform .env)
##   - -f overlay compose.override.yaml добавляется только при наличии overlay_dir
##   - --profile module_name — всегда (требуется для standalone base.yml deploy)
## @rationale DevPlan 170 W10-B п.4: параллельный цикл parallel_runner↔docker_orchestrator
##            держался на lazy-импортах compose-хелперов; вынос в leaf позволяет
##            parallel_runner импортировать их module-level, а docker_orchestrator —
##            приватными алиасами (гейт no_private_cross_module + тесты dorch._* не меняются).
## @changes  2026-08-15 · DevPlan 170 W10-B — вынесено из docker_orchestrator.py
##           (_resolve_compose_file:230-241, _build_compose_args:258-307; пост-squash 169)
## @modulemap
##   resolve_compose_file [W:1] — canonical scan через shared.compose_files (118 A2)
##   build_compose_args [W:2] — root-compose-first + env/overlay/profile аргументы
## @usecases
##   - parallel_runner.pull_module_images / deploy_docker_group (module-level импорт)
##   - docker_orchestrator.deploy_docker_module (приватные алиасы _resolve_compose_file/_build_compose_args)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

from core.internal.shared import deploy_paths
from core.internal.shared.compose_files import COMPOSE_FILENAMES as _CANON_COMPOSE_FILENAMES
from core.internal.shared.compose_files import resolve_compose_file as _resolve_compose_file_shared
from core.internal.shared.deploy_paths import platform_remote_base

logger = logging.getLogger(__name__)


# region FUNC_resolve_compose_file
## @purpose  Find the first existing compose file in a module directory.
##           Resolution order: canonical COMPOSE_FILENAMES from shared/compose_files.py
##           (compose.yaml → docker-compose.yaml → docker-compose.yml → docker-compose.base.yml,
##           DevPlan 118 A2) — thin delegating wrapper (публичный API сохранён).
## @io       ⇥ module_dir: str (path to module directory)
##           ⎋ Path | None — resolved compose file path, or None if none found
## @complexity 1 — linear scan of canonical tuple (delegates to shared)
## @invariants — Делегирует shared.compose_files.resolve_compose_file (единственный SoT,
##               гейт compose_files_sole_path); None при отсутствии файлов
def resolve_compose_file(module_dir: str) -> Path | None:
    """Find the first existing compose file in a module directory."""
    logger.info("[IMP:7][resolve_compose_file][scan] Resolving compose file in %s", module_dir)
    resolved = _resolve_compose_file_shared(module_dir)
    if resolved is not None:
        logger.info("[IMP:8][resolve_compose_file][found] Using compose file: %s", resolved)
    else:
        logger.warning(
            "[IMP:5][resolve_compose_file][missing] No compose file found in %s (tried %s)",
            module_dir,
            _CANON_COMPOSE_FILENAMES,
        )
    return resolved


# endregion FUNC_resolve_compose_file


# region FUNC_build_compose_args
## @purpose  Build docker compose argument list from compose file, env files, overlay, and profile.
## @io       ⇥ compose_file: Path, secrets_env_file: str | None, platform_root: str | None,
##           overlay_dir: str | None, module_name: str
##           ⎋ list[str] — docker compose arguments
## @complexity 1 — linear arg building
## @invariants
##   - --env-file for secrets.env is added only if the file exists
##   - --env-file for platform .env is added only if the file exists
##   - -f for overlay compose.override.yaml is added only if it exists
##   - --profile is always passed with module_name
def build_compose_args(
    compose_file: Path,
    secrets_env_file: str | None,
    platform_root: str | None,
    overlay_dir: str | None,
    module_name: str,
) -> list[str]:
    """Build docker compose argument list (root-compose-first, U-49)."""
    logger.info("[IMP:7][build_compose_args][build] Building compose args for %s", module_name)
    args: list[str] = []

    # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · root compose ПЕРВЫМ и ЕДИНСТВЕННЫМ -f (RC 121, U-49 regression)
    # · Symptom 1: изолированный модульный -f: "refers to undefined volume backup-spool"
    # · Symptom 2: root + модульный -f вместе: "security_opt items at 0 and 1 are equal" —
    #   root compose УЖЕ include'ит модульные base.yml; двойное включение конкатенирует списки.
    # · Fix: при наличии root compose (U-49 доставка) модуль деплоится ТОЛЬКО через root
    #   (+ --profile module); модульный файл отдельно — только когда root отсутствует (fallback).
    root_compose = Path(platform_root or str(platform_remote_base()), "docker-compose.yml")
    if Path(root_compose).is_file():
        args.extend(["-f", str(root_compose)])
        logger.info("[IMP:8][build_compose_args][root-compose] Adding root compose ONLY: %s", root_compose)
    else:
        args.extend(["-f", str(compose_file)])
        logger.info(
            "[IMP:8][build_compose_args][module-compose] Root compose absent — module file only: %s", compose_file
        )

    # Secrets env file
    env_file = secrets_env_file or str(deploy_paths.secrets_env_file())
    if Path(env_file).is_file():
        args.extend(["--env-file", env_file])
        logger.info("[IMP:8][build_compose_args][env] Adding secrets env-file: %s", env_file)

    # Platform root .env
    platform_env = str(Path(platform_root or str(platform_remote_base())) / ".env")
    if Path(platform_env).is_file():
        args.extend(["--env-file", platform_env])
        logger.info("[IMP:8][build_compose_args][env] Adding platform env-file: %s", platform_env)

    # Overlay compose override
    if overlay_dir:
        override = Path(overlay_dir) / "compose.override.yaml"
        if override.is_file():
            args.extend(["-f", str(override)])
            logger.info("[IMP:8][build_compose_args][overlay] Adding overlay compose: %s", override)

    # Profile — required for standalone base.yml deploy
    args.extend(["--profile", module_name])
    logger.info("[IMP:8][build_compose_args][profile] Adding profile: %s", module_name)

    return args


# endregion FUNC_build_compose_args
