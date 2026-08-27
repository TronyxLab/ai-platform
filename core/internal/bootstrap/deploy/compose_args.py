#!/usr/bin/env python3
# GREP_SUMMARY: compose-args, build-compose-args, resolve-compose-file, compose-defined-containers, compose-defined-services, docker-compose-args, leaf, 170, cycle-break, bootstrap-deploy
# STRUCTURE: ▶ resolve_compose_file ┌module_dir┐ → canonical scan (shared compose_files) → ⎋ Path | None │ ▶ build_compose_args ┌compose_file + env + overlay + profile┐ → root-compose-first -f + --env-file + overlay + --profile → ⎋ list[str] │ ▶ compose_defined_{containers,services} ┌module_dir + cache┐ → build_compose_args → docker compose config --format json → ⊕ {service: container_name} → ⎋ list[str]
# region MODULE_CONTRACT
## @purpose  Leaf-модуль compose-args (DevPlan 170 W10-B): публичные build_compose_args /
##           resolve_compose_file — вынесены из docker_orchestrator.py (были приватные
##           _build_compose_args/_resolve_compose_file) для разрыва import-цикла
##           parallel_runner ↔ docker_orchestrator (wave-briefs W10-design п.4).
##           Phase E 017 (F-017): +compose_defined_containers/compose_defined_services —
##           канонические имена контейнеров/сервисов модуля из docker compose config
##           (fallback резолва контейнеров R9 при label-miss; сервисный путь down disabled-flow).
## @scope    Построение docker compose CLI-аргументов, резолв compose-файла и compose-config
##           интроспекция (имена контейнеров/сервисов) — НИКАКОЙ оркестрации
##           (деплой/healthcheck/rollback). Потребители: docker_orchestrator (приватные
##           алиасы U-07), parallel_runner (module-level), converge/runtime (R9, Phase E 017).
## @invariants
##   - Чистый leaf: импортирует ТОЛЬКО core.internal.shared (compose_files, deploy_paths,
##     docker_compose, timeouts) — нет зависимостей на bootstrap/deploy-сиблингов (без циклов)
##   - root compose ПЕРВЫМ и ЕДИНСТВЕННЫМ -f (TRAP[BUG] RC 121, U-49): при наличии
##     root docker-compose.yml модуль деплоится ТОЛЬКО через root (+ --profile module);
##     модульный файл отдельно — только когда root отсутствует (fallback)
##   - --env-file добавляется только если файл существует (secrets.env / platform .env)
##   - -f overlay compose.override.yaml добавляется только при наличии overlay_dir
##   - --profile module_name — всегда (требуется для standalone base.yml deploy)
##   - compose_defined_* возвращают [] при config-сбое (graceful, НЕ unverified-канал) —
##     rc≠0 label-запроса docker ps остаётся единственным UNVERIFIED-триггером R9
##   - Кэш compose_defined_*: dict прогона (ключ = str(module_dir)) — один docker compose
##     config на модуль за прогон; без caller-кэша — module-level кэш
## @rationale DevPlan 170 W10-B п.4: параллельный цикл parallel_runner↔docker_orchestrator
##            держался на lazy-импортах compose-хелперов; вынос в leaf позволяет
##            parallel_runner импортировать их module-level, а docker_orchestrator —
##            приватными алиасами (гейт no_private_cross_module + тесты dorch._* не меняются).
##            Phase E 017: у орphan_reconciler._get_compose_services был приватный инлайн-аналог
##            (root-first + env-file + config JSON); converge/R9 не мог его использовать — публичный
##            helper здесь (leaf, shared-only) закрывает потребность converge без расшаривания
##            приватного API orphan-модуля (TRAP[DEBT] на дубль — см. orphan_reconciler).
## @changes  2026-08-15 · DevPlan 170 W10-B — вынесено из docker_orchestrator.py
##           (_resolve_compose_file:230-241, _build_compose_args:258-307; пост-squash 169)
##           2026-08-27 · Phase E 017 (F-017) — +compose_defined_containers/services
##           (fallback резолва контейнеров R9 + сервисный путь down disabled-flow)
## @modulemap
##   resolve_compose_file [W:1] — canonical scan через shared.compose_files (118 A2)
##   build_compose_args [W:2] — root-compose-first + env/overlay/profile аргументы
##   compose_defined_containers [W:3] — container-имена модуля из compose config (R9 fallback)
##   compose_defined_services [W:4] — service-имена модуля из compose config (R9 down-путь)
## @usecases
##   - parallel_runner.pull_module_images / deploy_docker_group (module-level импорт)
##   - docker_orchestrator.deploy_docker_module (приватные алиасы _resolve_compose_file/_build_compose_args)
##   - converge/runtime.reconcile_runtime_state (resolve_container_name fallback + disabled down)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from core.internal.shared import deploy_paths
from core.internal.shared.compose_files import COMPOSE_FILENAMES as _CANON_COMPOSE_FILENAMES
from core.internal.shared.compose_files import resolve_compose_file as _resolve_compose_file_shared
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.docker_compose import docker_compose_config as _shared_docker_compose_config
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT

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


# ═══════════════════════════════════════════════════════════════════════
# Phase E 017 (F-017) — compose-config интроспекция (имена контейнеров/сервисов)
# ═══════════════════════════════════════════════════════════════════════

# Module-level кэш {module_dir: {service: container_name}} — converge — CLI-процесс
# (свежий per-прогон); тесты передают собственный cache-дикт (изоляция).
_COMPOSE_SERVICES_CACHE: dict[str, dict[str, str]] = {}


# region FUNC__compose_services_map
## @purpose  Shared core compose-интроспекции: ОДИН `docker compose config --format json`
##           на модуль → {service: container_name}. Кэшируется по str(module_dir) —
##           compose_defined_containers/compose_defined_services делят ОДИН config-вызов
##           за прогон. U-49 канон argv через build_compose_args (root-compose-first).
## @io       ⇥ module_dir: str | Path, cache: dict[str, dict[str, str]] | None
##           ⎋ dict[str, str] {service: container_name} ({} при отсутствии compose/config-сбое)
## @complexity O(C) — config-вызов + JSON-парсинг (1 на модуль за прогон благодаря кэшу)
## @invariants
##   - ВСЕГДА dict (не None): config rc≠0/JSON-ошибка → {} (graceful) — НЕ маскирует
##     UNVERIFIED-канал R9 (тот держится на rc≠0 label-запроса docker ps)
##   - container_name сервиса = явный container_name или service name (compose-канон,
##     реальные модули платформы все имеют явный container_name — проверено ФС)
##   - --profile <module_dir.name> — compose резолвит ТОЛЬКО сервисы модуля (профиль-гейт)
##   - bytes stdout нормализуется (моки без text=True, W11)
def _compose_services_map(
    module_dir: str | Path,
    *,
    cache: dict[str, dict[str, str]] | None = None,
) -> dict[str, str]:
    """Load {service: container_name} mapping from docker compose config (cached per module_dir)."""
    key = str(module_dir)
    store = cache if cache is not None else _COMPOSE_SERVICES_CACHE
    if key in store:
        return store[key]

    compose_file = _resolve_compose_file_shared(module_dir)
    if compose_file is None:
        logger.warning("[IMP:8][compose_defined][no-compose] No compose file in %s — empty services map", module_dir)
        store[key] = {}
        return {}

    # U-49 канон argv (root-compose-first + --profile module): паритет с deploy/R7/orphan_reconciler.
    # module_name для --profile = имя директории модуля (== имя из node.yaml).
    compose_args = build_compose_args(
        compose_file=compose_file,
        secrets_env_file=None,  # канон deploy_paths.secrets_env_file() внутри (env-override SECRETS_ENV_FILE)
        platform_root=None,  # канон platform_remote_base() внутри (env-override PLATFORM_REMOTE_BASE)
        overlay_dir=None,
        module_name=Path(module_dir).name,
    )
    cfg_r = _shared_docker_compose_config(
        str(compose_file.parent),
        timeout=CONVERGE_DOCKER_TIMEOUT,
        compose_args=compose_args,
        flags=["--format", "json"],
    )
    if cfg_r.returncode != 0:
        logger.warning(
            "[IMP:8][compose_defined][config-fail] docker compose config failed for %s (rc=%d): %s",
            module_dir,
            cfg_r.returncode,
            cfg_r.stderr.strip() if cfg_r.stderr else "no stderr",
        )
        store[key] = {}
        return {}

    cfg_stdout = cfg_r.stdout
    if isinstance(cfg_stdout, bytes):  # defensive: моки без text=True (канон orphan_reconciler)
        cfg_stdout = cfg_stdout.decode("utf-8", errors="replace")
    try:
        # W11: json.loads → Any — каст к compose-config dict (services)
        cfg = cast(dict[str, object], json.loads(cfg_stdout))
    except json.JSONDecodeError as exc:
        logger.warning(
            "[IMP:8][compose_defined][json-fail] Invalid JSON from docker compose config for %s: %s", module_dir, exc
        )
        store[key] = {}
        return {}

    mapping: dict[str, str] = {}
    services = cfg.get("services", {})
    if isinstance(services, dict):
        for svc_name, svc in cast(dict[str, dict[str, object]], services).items():
            # W11: элемент services[] — каст строкового container_name/name
            cname = cast(str, svc.get("container_name", "") or svc.get("name", ""))
            if cname:
                mapping[svc_name] = cname

    store[key] = mapping
    logger.info(
        "[IMP:8][compose_defined][resolved] %d container name(s) for %s: %s",
        len(mapping),
        module_dir,
        list(mapping.values()),
    )
    return mapping


# endregion FUNC__compose_services_map


# region FUNC_compose_defined_containers
## @purpose  Канонические имена КОНТЕЙНЕРОВ модуля из docker compose config (U-49 root-first).
##           Phase E 017 (F-017): fallback резолва контейнеров R9 при label-miss —
##           label=com.docker.compose.project=`<module>` даёт 0 рядов на U-49-нодах
##           (ВСЕ контейнеры project=platform). Возвращает container_name (или service name)
##           каждого сервиса — то, что compose реально создаёт.
## @io       ⇥ module_dir: str | Path, cache: dict[str, dict[str, str]] | None
##           ⎋ list[str] — канонические container-имена ([] при отсутствии compose/config-сбое)
## @complexity O(C) — один docker compose config на модуль (кэш: 1 вызов на модуль за прогон)
## @invariants
##   - Делегирует build_compose_args (root-compose-first + --profile module) — паритет с deploy/R7
##   - rc≠0/JSON-ошибка → [] (graceful, зеркально orphan_reconciler._get_compose_services)
##   - Кэш: caller-дикт (ключ = str(module_dir)); без него — module-level кэш
## @changes  2026-08-27 | Phase E 017 (F-017) — created (fallback резолва контейнеров R9)
def compose_defined_containers(
    module_dir: str | Path,
    *,
    cache: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Resolve canonical container names for a module via docker compose config (U-49 root-first)."""
    mapping = _compose_services_map(module_dir, cache=cache)
    return list(mapping.values())


# endregion FUNC_compose_defined_containers


# region FUNC_compose_defined_services
## @purpose  Канонические имена СЕРВИСОВ модуля из docker compose config. Сервисный путь
##           `docker compose down <service>` для disabled-flow R9 (Phase E 017): down с
##           именами сервисов останавливает ТОЛЬКО контейнеры отключаемого модуля —
##           на U-49-ноде down без сервисов снёс бы ВЕСЬ project=platform.
## @io       ⇥ module_dir: str | Path, cache → ⎋ list[str] (имена сервисов; [] при сбое)
## @complexity O(C) — делит кэш-запись с compose_defined_containers (0 доп. config-вызовов)
## @invariants — rc≠0/JSON-ошибка → [] (graceful); ключи services-дикта = канонические service-имена
def compose_defined_services(
    module_dir: str | Path,
    *,
    cache: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Resolve canonical SERVICE names for a module via docker compose config (down-service path)."""
    mapping = _compose_services_map(module_dir, cache=cache)
    return list(mapping.keys())


# endregion FUNC_compose_defined_services
