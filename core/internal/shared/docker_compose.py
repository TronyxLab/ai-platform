#!/usr/bin/env python3
# GREP_SUMMARY: docker-compose, shared, pull, build, up, healthcheck, retry, image-exists, config, ps, images, down, sole-path
# STRUCTURE: ▶ ┌compose_dir┐ → ◇ docker compose pull|build|up -d|down → ⊕ subprocess.run → ⎋ bool
#            ▶ healthcheck_poll ┌project_name┐ → ○ loop [interval] → ◇ docker ps/compose ps → ◇ inspect State.Status|Health.Status → все running+healthy|""|none? → ⎋ "healthy"|"unhealthy"
#            ▶ retry_pull ┌compose_dir┐ → ○ max_attempts: ◇ pull? → success / backoff → ⎋ bool
#            ▶ config/ps/images ┌compose_dir┐ → ◇ docker compose <sub> → ⎋ CompletedProcess (не raise)
#            ▶ check_image_exists ┌image_ref┐ → ◇ docker manifest inspect → returncode? → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Shared Docker Compose operations library — ЕДИНСТВЕННЫЙ путь docker compose
##           операций платформы (DevPlan 116 B5 T3, U-13/U-14). Заменяет 4 локальные копии
##           (docker_orchestrator, DeployEngine, reconciler, healthcheck_poller) + консолидирует
##           healthcheck-критерий (5 реализаций → 1).
## @scope    Low-level infrastructure layer (by DDD): up/pull/build/down/config/ps/images,
##           healthcheck_poll, retry_pull, check_image_exists. NOT business logic — не знает
##           о проектах/контекстах/модулях. Business orchestration stays in callers.
## @invariants
##   1. ВСЕ docker compose subprocess-вызовы платформы живут ТОЛЬКО здесь (гейт docker_sole_path).
##   2. Все функции log via standard logging.getLogger(__name__); контракт non-fatal —
##      up/pull/build/down/retry возвращают bool, config/ps/images возвращают CompletedProcess
##      (никогда не raise; caller решает severity).
##   3. Единый критерий «здоров» (D5): контейнер running AND (Health.Status ∈ {healthy, "", "none"})
##      = здоров; "unhealthy" → ждать (стартовые гонки); timeout → "unhealthy".
##   4. Timeouts — из core/internal/shared/timeouts.py (единственный реестр, U-11).
##   5. env_override = {**os.environ, **(env_override or {})} — копия + override, НЕ замена
##      (не ломает COMPOSE_PROFILES-экспорты).
##   6. Directory existence validated before operations (возвращает False/failed).
## @rationale  DevPlan 116 B5: shared-модуль мёртв (docker_compose_up — 0 production-потребителей),
##             каждая волна добавляла 4-ю копию. Расширение API (flags/service/env_override + D7)
##             делает переход структурно обязательным; гейт docker_sole_path запрещает возврат копий.
## @changes    2026-07-25 | DevPlan 079 DRIFT-B6 — Created as shared module
##             2026-08-01 | DevPlan 116 B5 T3 — API-расширение (compose_args/service/env_override/flags
##                        для up/pull/build), healthcheck_poll переработан на inspect-критерий,
##                        константы → timeouts.py; +config/ps/images/down (sole-path extension)
##             2026-08-14 | DevPlan 167 D2 — healthcheck_poll: DI-швы docker-объект + sleep_fn
##                        (0 monkeypatch в тестах; unittest.patch-совместимость сохранена)
##             2026-08-16 | DevPlan 177 W3.1 — retry_pull: retry-цикл → shared/retry.py
##                        (1:1 семантика; max_attempts default — RETRY_COUNT+1 из timeouts)
##             2026-08-27 | F-03 (017-launch-validation P0) — +docker_prebuild_pull:
##                        pre-pull пинненных баз build-модулей до первого build
##                        (deterministic cold-cache bootstrap; buildkit не ретраит pull)
# endregion MODULE_CONTRACT

import functools
import logging
import os
import pathlib
import subprocess
import time
from collections.abc import Callable
from typing import Protocol

# DevPlan 128 W1: примитивы docker ps/inspect/exec — shared/docker_ops (единственный слой,
# гейт docker_sole_path). Compose-домен остаётся здесь (свой compose-гейт).
from core.internal.shared import docker_ops
from core.internal.shared.dockerfile_bases import module_base_images as _module_base_images
from core.internal.shared.retry import retry as _shared_retry
from core.internal.shared.timeouts import (
    BUILD_TIMEOUT,
    COMPOSE_UP_TIMEOUT,
    DOCKER_CMD_TIMEOUT,
    HEALTHCHECK_POLL_TIMEOUT,
    IMAGE_CHECK_TIMEOUT,
    PREBUILD_PULL_ATTEMPTS,
    PREBUILD_PULL_BACKOFF_SECONDS,
    PULL_TIMEOUT,
    RETRY_BACKOFF_SECONDS,
    RETRY_COUNT,
)

logger = logging.getLogger(__name__)


# region PROTOCOL_DockerOps
class _DockerOpsProtocol(Protocol):
    """Минимальный DI-контракт docker-объекта (W11, DevPlan 170): методы, используемые
    healthcheck_poll (docker_ps + inspect_state_health). docker_ops-модуль структурно
    удовлетворяет протоколу (без Any-аннотации DI-параметра)."""

    # ruff: ignore[A002]  # `all`/`format` = канонические docker CLI flag names (зеркало docker_ops API)
    def docker_ps(
        self,
        *,
        all: bool = False,
        quiet: bool = False,
        filters: list[str] | None = None,
        format: str | None = None,
        timeout: int = ...,
        runner: object | None = ...,
    ) -> subprocess.CompletedProcess[str]: ...

    def inspect_state_health(
        self,
        identifier: str,
        timeout: int = ...,
        runner: object | None = ...,
    ) -> tuple[str, str]: ...


# endregion PROTOCOL_DockerOps


# region FUNC__failed_process
def _failed_process(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Build a synthetic failed CompletedProcess (для info-functions при сбое запуска).

    ▶ ┌cmd┐ → ⎋ CompletedProcess(returncode=1, stdout="", stderr="command failed")

    ## @purpose — Non-fatal контракт: если subprocess не запустился (нет docker/директории),
    ##            info-функции возвращают failed CompletedProcess вместо raise.
    ## @io — ⇥ cmd: list[str] → ⎋ subprocess.CompletedProcess[str]
    ## @complexity — O(1)
    """
    return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="command failed to start")


# endregion FUNC__failed_process


# region FUNC__run_compose
def _run_compose(
    cmd: list[str],
    compose_dir: str,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run docker compose subcommand with unified error handling.

    ▶ ┌cmd, compose_dir┐ → ◇ isdir? → subprocess.run(cmd, cwd=compose_dir, env=env) → ⎋ CompletedProcess|None

    ## @purpose — Единая обёртка subprocess для всех docker compose операций: валидация
    ##            директории, capture_output+text, timeout, логирование TimeoutExpired/FileNotFound.
    ## @io — ⇥ cmd: list[str], compose_dir: str, timeout: int, env: dict|None → ⎋ CompletedProcess[str] | None
    ## @complexity — O(1) + subprocess I/O
    ## @invariants
    ##   - None = не запустился (нет директории / docker / timeout) — caller маппит в False/failed.
    ##   - env=None → наследуется os.environ (subprocess default); env задан → полный dict.
    """
    if not os.path.isdir(compose_dir):
        logger.warning("[IMP:7][docker_compose] Directory not found: %s", compose_dir)
        return None
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=compose_dir, env=env, check=False
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][docker_compose] Command timed out (%ds): %s", timeout, " ".join(cmd))
        return None
    except FileNotFoundError:
        logger.error("[IMP:10][docker_compose] docker command not found")
        return None


# endregion FUNC__run_compose


# region FUNC_docker_compose_pull


def docker_compose_pull(
    compose_dir: str,
    timeout: int = PULL_TIMEOUT,
    compose_args: list[str] | None = None,
    service: str | None = None,
    env_override: dict[str, str] | None = None,
) -> bool:
    """Run docker compose pull in a directory.

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose pull [service] → ⊕ returncode==0 → ⎋ bool

    ## @purpose — Pull container images for a docker compose project.
    ## @io — ⇥ compose_dir: str, timeout: int, compose_args: list[str] | None,
    ##       service: str | None, env_override: dict[str, str] | None → ⎋ bool (True = success)
    ## @complexity — O(1) + network I/O
    ## @invariants
    ##   - Non-fatal: returns False on failure/exception
    ##   - compose_args (если заданы) вставляются ПЕРЕД "pull" (e.g. ["-f", ..., "--profile", ...])
    ##   - service ограничивает pull одним сервисом; env_override = {**os.environ, **override}
    ## @changes 2026-07-26 · DevPlan 079 TASK-9 — Added compose_args parameter
    ##           2026-08-01 · DevPlan 116 B5 T3 — Added service/env_override (D7)
    """
    if not os.path.isdir(compose_dir):
        logger.warning("[IMP:7][docker_compose_pull] Directory not found: %s", compose_dir)
        return False

    logger.info("[IMP:7][docker_compose_pull] Pulling images in %s", compose_dir)
    cmd = ["docker", "compose"]
    if compose_args:
        cmd.extend(compose_args)
    cmd.append("pull")
    if service:
        cmd.append(service)

    # env=None → subprocess наследует os.environ (функционально идентично); env передаётся
    # ТОЛЬКО при env_override (IMAGE_TAG) — чистота call_args для тестов/отладки
    env = {**os.environ, **(env_override or {})} if env_override else None
    result = _run_compose(cmd, compose_dir, timeout, env=env)
    if result is None:
        return False
    if result.returncode == 0:
        logger.info("[IMP:9][docker_compose_pull] Pull succeeded in %s", compose_dir)
        return True
    logger.warning(
        "[IMP:7][docker_compose_pull] Pull failed (exit=%d): %s",
        result.returncode,
        result.stderr.strip()[:200],
    )
    return False


# endregion FUNC_docker_compose_pull


# region FUNC_docker_compose_build


def docker_compose_build(
    compose_dir: str,
    timeout: int = BUILD_TIMEOUT,
    compose_args: list[str] | None = None,
    flags: list[str] | None = None,
    env_override: dict[str, str] | None = None,
) -> bool:
    """Run docker compose build in a directory.

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose build [flags] → ⊕ returncode==0 → ⎋ bool

    ## @purpose — Build container images locally for a docker compose project.
    ## @io — ⇥ compose_dir: str, timeout: int, compose_args: list[str] | None,
    ##       flags: list[str] | None (e.g. --build-arg), env_override → ⎋ bool (True = success)
    ## @complexity — O(1) + build I/O
    ## @invariants
    ##   - Non-fatal: returns False on failure; longer timeout (300s) for slow builds
    ##   - flags вставляются ПОСЛЕ "build" (e.g. ["--build-arg", "CONTEXT=..."])
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — Added compose_args/flags/env_override (D7)
    """
    if not os.path.isdir(compose_dir):
        logger.warning("[IMP:7][docker_compose_build] Directory not found: %s", compose_dir)
        return False

    logger.info("[IMP:7][docker_compose_build] Building images in %s", compose_dir)
    cmd = ["docker", "compose"]
    if compose_args:
        cmd.extend(compose_args)
    cmd.append("build")
    if flags:
        cmd.extend(flags)

    # env=None → subprocess наследует os.environ (функционально идентично); env передаётся
    # ТОЛЬКО при env_override (IMAGE_TAG) — чистота call_args для тестов/отладки
    env = {**os.environ, **(env_override or {})} if env_override else None
    result = _run_compose(cmd, compose_dir, timeout, env=env)
    if result is None:
        return False
    if result.returncode == 0:
        logger.info("[IMP:9][docker_compose_build] Build succeeded in %s", compose_dir)
        return True
    logger.warning(
        "[IMP:7][docker_compose_build] Build failed (exit=%d): %s",
        result.returncode,
        result.stderr.strip()[:200],
    )
    return False


# endregion FUNC_docker_compose_build


# region FUNC_docker_compose_up


def docker_compose_up(
    compose_dir: str,
    timeout: int = COMPOSE_UP_TIMEOUT,
    compose_args: list[str] | None = None,
    service: str | None = None,
    env_override: dict[str, str] | None = None,
    flags: list[str] | None = None,
) -> bool:
    """Run docker compose up -d in a directory.

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose up -d [flags] [service] → ⊕ returncode==0 → ⎋ bool

    ## @purpose — Start containers for a docker compose project in detached mode.
    ## @io — ⇥ compose_dir: str, timeout: int, compose_args: list[str] | None,
    ##       service: str | None, env_override: dict[str, str] | None,
    ##       flags: list[str] | None (политические флаги --remove-orphans/--force-recreate) → ⎋ bool
    ## @complexity — O(1) + startup I/O
    ## @invariants
    ##   - Non-fatal: returns False on failure; uses -d (detached) mode
    ##   - flags вставляются ПОСЛЕ "up -d"; service (если задан) — последним аргументом
    ##   - env_override = {**os.environ, **override} — не ломает COMPOSE_PROFILES-экспорты (D7)
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — Added compose_args/service/env_override/flags (D7)
    """
    if not pathlib.Path(compose_dir).is_dir():
        logger.warning("[IMP:7][docker_compose_up] Directory not found: %s", compose_dir)
        return False

    logger.info("[IMP:7][docker_compose_up] Starting containers in %s", compose_dir)
    cmd = ["docker", "compose"]
    if compose_args:
        cmd.extend(compose_args)
    cmd.extend(["up", "-d"])
    if flags:
        cmd.extend(flags)
    if service:
        cmd.append(service)

    # env=None → subprocess наследует os.environ (функционально идентично); env передаётся
    # ТОЛЬКО при env_override (IMAGE_TAG) — чистота call_args для тестов/отладки
    env = {**os.environ, **(env_override or {})} if env_override else None
    result = _run_compose(cmd, compose_dir, timeout, env=env)
    if result is None:
        return False
    if result.returncode == 0:
        logger.info("[IMP:9][docker_compose_up] Containers started in %s", compose_dir)
        return True
    logger.warning(
        "[IMP:7][docker_compose_up] Up failed (exit=%d): %s",
        result.returncode,
        result.stderr.strip()[:200],
    )
    return False


# endregion FUNC_docker_compose_up


# region FUNC_docker_compose_down


def docker_compose_down(
    compose_dir: str,
    timeout: int = COMPOSE_UP_TIMEOUT,
    compose_args: list[str] | None = None,
    flags: list[str] | None = None,
    env_override: dict[str, str] | None = None,
    service: str | None = None,
) -> bool:
    """Run docker compose down in a directory.

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose down [flags] [service] → ⊕ returncode==0 → ⎋ bool

    ## @purpose — Stop/remove containers for a docker compose project (данные сохраняются —
    ##            флаг -v НЕ передаётся, если caller его не добавил явно).
    ## @io — ⇥ compose_dir: str, timeout: int, compose_args: list[str] | None,
    ##       flags: list[str] | None (e.g. --timeout <DOCKER_STOP_TIMEOUT>, C4 канон), env_override,
    ##       service: str | None (ограничить down одним сервисом) → ⎋ bool (True = success)
    ## @complexity — O(1) + shutdown I/O
    ## @invariants
    ##   - Non-fatal: returns False on failure
    ##   - Никогда не добавляет -v по умолчанию (O7: данные проекта не удаляются)
    ##   - service (если задан) — последним аргументом (docker compose down <service>)
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — sole-path extension (rollback/remove)
    ##           2026-08-01 · DevPlan 117 D19 — добавлен service параметр
    """
    if not pathlib.Path(compose_dir).is_dir():
        logger.warning("[IMP:7][docker_compose_down] Directory not found: %s", compose_dir)
        return False

    logger.info("[IMP:7][docker_compose_down] Stopping containers in %s", compose_dir)
    cmd = ["docker", "compose"]
    if compose_args:
        cmd.extend(compose_args)
    cmd.append("down")
    if flags:
        cmd.extend(flags)
    if service:
        cmd.append(service)

    # env=None → subprocess наследует os.environ (функционально идентично); env передаётся
    # ТОЛЬКО при env_override (IMAGE_TAG) — чистота call_args для тестов/отладки
    env = {**os.environ, **(env_override or {})} if env_override else None
    result = _run_compose(cmd, compose_dir, timeout, env=env)
    if result is None:
        return False
    if result.returncode == 0:
        logger.info("[IMP:9][docker_compose_down] Containers stopped in %s", compose_dir)
        return True
    logger.warning(
        "[IMP:7][docker_compose_down] Down failed (exit=%d): %s",
        result.returncode,
        result.stderr.strip()[:200],
    )
    return False


# endregion FUNC_docker_compose_down


# region FUNC_docker_compose_config


def docker_compose_config(
    compose_dir: str,
    timeout: int = DOCKER_CMD_TIMEOUT,
    compose_args: list[str] | None = None,
    flags: list[str] | None = None,
    env_override: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run docker compose config (info-gathering).

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose config [flags] → ⎋ CompletedProcess

    ## @purpose — Единая точка `docker compose config` (--format json / --images / --services).
    ##            Используется orphan-реконсиляцией, hermes image-resolution, R7 volumes.
    ## @io — ⇥ compose_dir: str, timeout: int, compose_args: list[str] | None,
    ##       flags: list[str] | None (e.g. ["--format", "json"], ["--images"]) → ⎋ CompletedProcess[str]
    ## @complexity — O(1) + compose resolve I/O
    ## @invariants
    ##   - Non-fatal: возвращает CompletedProcess (никогда не raise); при сбое запуска —
    ##     failed CompletedProcess (returncode=1)
    ##   - Caller инспектирует .returncode/.stdout (могут быть bytes от mock — normalize у caller)
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — sole-path extension
    """
    cmd = ["docker", "compose"]
    if compose_args:
        cmd.extend(compose_args)
    cmd.append("config")
    if flags:
        cmd.extend(flags)

    # env=None → subprocess наследует os.environ (функционально идентично); env передаётся
    # ТОЛЬКО при env_override (IMAGE_TAG) — чистота call_args для тестов/отладки
    env = {**os.environ, **(env_override or {})} if env_override else None
    result = _run_compose(cmd, compose_dir, timeout, env=env)
    if result is None:
        return _failed_process(cmd)
    return result


# endregion FUNC_docker_compose_config


# region FUNC_docker_compose_ps


# ruff: ignore[A002]  # `format` = canonical docker CLI flag name (--format), public keyword API
def docker_compose_ps(
    compose_dir: str,
    timeout: int = DOCKER_CMD_TIMEOUT,
    compose_args: list[str] | None = None,
    format: str | None = None,
    service: str | None = None,
    env_override: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run docker compose ps (info-gathering).

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose ps [--format] [service] → ⎋ CompletedProcess

    ## @purpose — Единая точка `docker compose ps` (--format json для status/snapshot).
    ## @io — ⇥ compose_dir: str, timeout: int, compose_args: list[str] | None, format: str | None,
    ##       service: str | None → ⎋ CompletedProcess[str]
    ## @complexity — O(1) + docker ps I/O
    ## @invariants
    ##   - Non-fatal: возвращает CompletedProcess (никогда не raise)
    ##   - format вставляется как "--format <value>"; service — последним аргументом
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — sole-path extension
    """
    cmd = ["docker", "compose"]
    if compose_args:
        cmd.extend(compose_args)
    cmd.append("ps")
    if format:
        cmd.extend(["--format", format])
    if service:
        cmd.append(service)

    # env=None → subprocess наследует os.environ (функционально идентично); env передаётся
    # ТОЛЬКО при env_override (IMAGE_TAG) — чистота call_args для тестов/отладки
    env = {**os.environ, **(env_override or {})} if env_override else None
    result = _run_compose(cmd, compose_dir, timeout, env=env)
    if result is None:
        return _failed_process(cmd)
    return result


# endregion FUNC_docker_compose_ps


# region FUNC_docker_compose_images


def docker_compose_images(
    compose_dir: str,
    timeout: int = DOCKER_CMD_TIMEOUT,
    compose_args: list[str] | None = None,
    service: str | None = None,
    flags: list[str] | None = None,
    env_override: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run docker compose images (info-gathering).

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose images [-q] [--format json] [service] → ⎋ CompletedProcess

    ## @purpose — Единая точка `docker compose images` (-q для save_previous_image,
    ##            --format json для snapshot).
    ## @io — ⇥ compose_dir: str, timeout: int, compose_args: list[str] | None, service: str | None,
    ##       flags: list[str] | None (e.g. ["-q"], ["--format", "json"]) → ⎋ CompletedProcess[str]
    ## @complexity — O(1) + docker images I/O
    ## @invariants — Non-fatal: возвращает CompletedProcess (никогда не raise)
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — sole-path extension
    """
    cmd = ["docker", "compose"]
    if compose_args:
        cmd.extend(compose_args)
    cmd.append("images")
    if flags:
        cmd.extend(flags)
    if service:
        cmd.append(service)

    # env=None → subprocess наследует os.environ (функционально идентично); env передаётся
    # ТОЛЬКО при env_override (IMAGE_TAG) — чистота call_args для тестов/отладки
    env = {**os.environ, **(env_override or {})} if env_override else None
    result = _run_compose(cmd, compose_dir, timeout, env=env)
    if result is None:
        return _failed_process(cmd)
    return result


# endregion FUNC_docker_compose_images


# region FUNC_is_container_healthy
def is_container_healthy(state: str | None, health: str | None) -> bool:
    """Единый критерий «здоров» контейнера (D5, AI-0065): running AND Health.Status ∈ {healthy,"",none}.

    ## @purpose  Канонический leaf-предикат здоровья — единственная реализация критерия;
    ##            переиспользуется healthcheck_poll и метрическим docker_collector (паритет
    ##            collector↔canon: running-без-healthcheck = здоров).
    ## @io       ⇥ state: State.Status ("running"/"exited"/...), health: Health.Status
    ##           ("healthy"/"unhealthy"/"starting"/""/None) → ⎋ bool
    ## @complexity O(1)
    ## @invariants
    ##   - health=None/""/"none" (контейнер без HEALTHCHECK) при running → healthy=True
    ##   - "starting"/"unhealthy" или не-running → False
    """
    return state == "running" and health in {"healthy", "", "none", None}


# endregion FUNC_is_container_healthy


# region FUNC_healthcheck_poll


def healthcheck_poll(
    project_name: str,
    timeout: int = HEALTHCHECK_POLL_TIMEOUT,
    interval: int = 3,
    service: str | None = None,
    *,
    docker: _DockerOpsProtocol | None = None,  # DI-объект docker_ops (docker_ps/inspect_state_health, 167 D2)
    sleep_fn: Callable[[float], None] | None = None,  # DI: time.sleep fake (None → time.sleep)
    attempts: int | None = None,  # REF-0103: max число проверок (None → deadline-driven; 1 = single-shot)
) -> str:
    """Poll container health for a project until healthy or timeout (inspect-критерий, D5).

    ▶ ┌project_name + timeout┐ → ○ loop [interval; ≤attempts]: ◇ docker ps/compose ps → cids →
      ○ for cid: inspect State.Status|Health.Status → ◇ все (running AND healthy|""|none)? → "healthy"
    │                                                                                                     → timeout? → "unhealthy"

    ## @purpose — Wait for a docker compose project to become healthy. ЕДИНЫЙ критерий «здоров»:
    ##            контейнер running AND Health.Status ∈ {healthy, "", "none"} (running-без-healthcheck
    ##            = здоров). "unhealthy" НЕ фейлит сразу — стартовые гонки (ждём).
    ## @io — ⇥ project_name: str, timeout: int, interval: int, service: str | None,
    ##       docker: DI-объект (None → docker_ops), sleep_fn: DI-fn (None → time.sleep),
    ##       attempts: int | None (REF-0103 single-shot gate) → ⎋ str ("healthy"|"unhealthy")
    ## @complexity — O(min(T/I, attempts)) где T = timeout, I = interval
    ## @invariants
    ##   - service задан → `docker compose ps -q {service}` (deploy_engine; cwd = project_dir через chdir);
    ##     иначе `docker ps --filter name={project_name}` (глобальный фильтр)
    ##   - inspect: --format '{{.State.Status}}|{{.State.Health.Status}}'
    ##   - ВСЕ контейнеры должны быть здоровы (любой не-здоров → ждать)
    ##   - timeout → "unhealthy"; non-fatal: docker-ошибки → ждать (не raise)
    ##   - attempts (REF-0103): None → прежнее deadline-driven поведение байт-в-байт;
    ##     N ≥ 1 → не более N проверок (single-shot skip-gate); N < 1 трактуется как 1
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — критерий переработан на inspect (5 реализаций → 1, D5)
    ##           2026-08-14 · DevPlan 167 D2 — DI-швы docker-объект + sleep_fn (0 monkeypatch в тестах)
    ##           2026-08-16 · DevPlan 177 W3.1 — retry_pull: retry-цикл → shared/retry.py
    ##           2026-08-25 · REF-0103 — +attempts kwarg (аддитивный single-shot режим для
    ##                        cold-skip gate context_deployer: отсутствующий проект не должен
    ##                        сжигать полное окно поллинга в idempotent-skip проверке)
    ## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · DI-швы healthcheck_poll: docker-объект + sleep_fn
    ## · Rejected: прямой вызов docker_ops/time.sleep (тест патчил их monkeypatch.setattr)
    ## · Reason: seam = тестируемость реального вызова; docker-объект — один DI-шов для
    ## ·   docker_ps/inspect_state_health; docker_ops-fallback читает модуль-глобал на вызове —
    ## ·   unittest.patch (test_shared_docker_compose) жив
    ## · Rev: при консолидации docker-примитивов в единый объект-канал — слить docker-шов
    """
    dops = docker if docker is not None else docker_ops
    sleep = sleep_fn if sleep_fn is not None else time.sleep
    deadline = time.monotonic() + timeout

    iterations = 0
    max_iterations = max(int(attempts), 1) if attempts is not None else None
    while time.monotonic() < deadline:
        if max_iterations is not None and iterations >= max_iterations:
            break
        iterations += 1
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            if service:
                result = subprocess.run(
                    ["docker", "compose", "ps", "-q", service],
                    capture_output=True,
                    text=True,
                    timeout=DOCKER_CMD_TIMEOUT,
                    check=False,
                )
                cids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            else:
                # docker ps --filter name=... (D5, W1: примитив — shared/docker_ops)
                result = dops.docker_ps(
                    filters=[f"name={project_name}"],
                    format="{{.ID}}",
                    timeout=DOCKER_CMD_TIMEOUT,
                )
                cids = [line.strip() for line in result.stdout.splitlines() if line.strip()]

            if result.returncode != 0 or not cids:
                logger.debug(
                    "[IMP:6][healthcheck_poll] No containers for %s yet (rc=%d)", project_name, result.returncode
                )
                sleep(interval)
                continue

            # Единый критерий «здоров» (D5): ВСЕ контейнеры running AND (healthy | "" | none)
            all_healthy = True
            for cid in cids:
                # docker inspect State.Status|State.Health.Status (W1: примитив — shared/docker_ops)
                state, health = dops.inspect_state_health(cid, timeout=DOCKER_CMD_TIMEOUT)
                if not is_container_healthy(state, health):
                    # "unhealthy"/"starting"/exited — ждём (стартовые гонки), не fail сразу
                    logger.debug(
                        "[IMP:6][healthcheck_poll] %s container %s not healthy yet (state=%s health=%s)",
                        project_name,
                        cid,
                        state,
                        health or "<none>",
                    )
                    all_healthy = False
                    break

            if all_healthy:
                logger.info("[IMP:9][healthcheck_poll] %s — healthy", project_name)
                return "healthy"

        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug("[IMP:6][healthcheck_poll] Docker check failed for %s: %s", project_name, e)
            sleep(interval)
            continue

        sleep(interval)

    logger.warning("[IMP:7][healthcheck_poll] %s — timeout (%ds) — unhealthy", project_name, timeout)
    return "unhealthy"


# endregion FUNC_healthcheck_poll


# region FUNC_retry_pull


def retry_pull(
    compose_dir: str,
    max_attempts: int = RETRY_COUNT + 1,
    backoff_seconds: list[int] | None = None,
    timeout: int = PULL_TIMEOUT,
    compose_args: list[str] | None = None,
    service: str | None = None,
    env_override: dict[str, str] | None = None,
) -> bool:
    """Pull images with retries and backoff.

    ▶ ┌compose_dir┐ → ○ attempt 1..max: pull → success? → ⎋ True
    │                                            → fail → sleep backoff → retry

    ## @purpose — Resilient pull with configurable retries and backoff (ЕДИНСТВЕННАЯ реализация;
    ##            docker_orchestrator + deploy_engine подключаются, T4.5/T5.1). Retry-цикл —
    ##            shared/retry.py (DevPlan 177 W3.1, result-mode: predicate на bool pull-результат).
    ## @io — ⇥ compose_dir: str, max_attempts: int (default RETRY_COUNT+1 — канон timeouts),
    ##       backoff_seconds: list[int] | None, timeout: int, compose_args, service,
    ##       env_override → ⎋ bool
    ## @complexity — O(max_attempts * pull_time)
    ## @invariants
    ##   - Default backoff: timeouts.RETRY_BACKOFF_SECONDS ([5, 10, 20])
    ##   - If backoff list shorter than attempts, all remaining attempts use last value
    ##     (clamp — канон shared.retry)
    ##   - All attempts must fail for final False; пробрасывает service/env_override в pull (D7)
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — service/env_override/compose_args проброс (D7)
    ## @changes 2026-08-16 · DevPlan 177 W3.1 — retry-цикл → shared/retry.py (1:1 семантика)
    """
    if backoff_seconds is None:
        backoff_seconds = RETRY_BACKOFF_SECONDS

    def _pull() -> bool:
        return docker_compose_pull(
            compose_dir,
            timeout=timeout,
            compose_args=compose_args,
            service=service,
            env_override=env_override,
        )

    return _shared_retry(
        _pull,
        attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        retryable=lambda ok: not ok,
    )


# endregion FUNC_retry_pull


# region FUNC__docker_pull_ref
def _docker_pull_ref(ref: str, timeout: int = PULL_TIMEOUT) -> bool:
    """Pull a single base image ref via `docker pull` (non-fatal, bool-контракт).

    ▶ ┌ref┐ → ◇ subprocess docker pull ref → ⊕ returncode==0 → ⎋ True
    │                                                       → timeout/not-found/rc≠0 → ⎋ False

    ## @purpose  Единая точка `docker pull <ref>` в compose-домене (для docker_prebuild_pull):
    ##            capture_output+text, timeout=PULL_TIMEOUT (канон timeouts), non-fatal
    ##            (TimeoutExpired/FileNotFoundError → False — контракт caller'а).
    ## @io       ⇥ ref: str (name[:tag][@sha256:...]), timeout: int = PULL_TIMEOUT → ⎋ bool
    ## @complexity O(1) + network I/O
    ## @invariants
    ##   - Никогда не raise (graceful канон run_subprocess check=False, 127/124 — caller severity)
    ##   - Команда строго ["docker", "pull", ref] — без shell; гейт docker_sole_path: "pull" —
    ##     не compose и не ps/inspect/exec токен → legal в shared/docker_compose.py
    """
    logger.info("[IMP:8][docker_pull_ref] Pulling base image: %s", ref)
    try:
        result = subprocess.run(
            ["docker", "pull", ref],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("[IMP:7][docker_pull_ref] Pull failed for %s: %s", ref, exc)
        return False
    if result.returncode == 0:
        logger.info("[IMP:9][docker_pull_ref] Pulled base image: %s", ref)
        return True
    logger.warning(
        "[IMP:7][docker_pull_ref] Pull failed (exit=%d) for %s: %s",
        result.returncode,
        ref,
        result.stderr.strip()[:200],
    )
    return False


# endregion FUNC__docker_pull_ref


# region FUNC_docker_prebuild_pull
def docker_prebuild_pull(
    module_dir: str,
    *,
    sleep_fn: Callable[[float], None] | None = None,
) -> bool:
    """Pre-pull пинненных базовых образов Dockerfile модуля ДО первого docker compose build.

    ▶ ┌module_dir┐ → ◇ module_base_images → [] → ⎋ True (no-op)
    │                                  → refs → ○ for ref: retry(docker pull, 4 попытки, backoff 5/15/45)
    │                                        → ◇ все ок? → ⎋ True · частично → ⚠️ IMP:7 → ⎋ True
    │                                        → ◇ все исчерпаны → 🔴 IMP:10 → ⎋ False

    ## @purpose  Детерминизация холодного bootstrap (F-03, 017-launch-validation P0): BuildKit НЕ
    ##            ретраит docker-pull внутри сборки — первый массовый пул базовых образов с docker.io
    ##            на голой ноде транзиентно падает (троттлинг хостера). Pre-pull с retry
    ##            (PREBUILD_PULL_ATTEMPTS=4, backoff 5/15/45s — окно 65s) прогревает кеш ДО build.
    ## @io       ⇥ module_dir: str (директория модуля, напр. <modules_dir>/status-page),
    ##            sleep_fn: Callable[[float], None] | None (DI-шов ретрая — тесты; None = time.sleep)
    ##           → ⎋ bool (True = все базы спулены ИЛИ нечего пулить; False = ВСЕ ретраи исчерпаны)
    ## @complexity O(R * A) — R баз × A попыток с backoff
    ## @invariants
    ##   - Non-fatal контракт: docker pull сам не raise (bool); retry-цикл — shared/retry.py
    ##     (result_mode: предикат retryable=lambda ok: not ok, последнее значение при исчерпании)
    ##   - Частичный success (часть баз спулена) → True + warning [IMP:7] (мягкий проход —
    ##     build остаётся арбитром; образ может быть уже в локальном кеше)
    ##   - ВСЕ базы исчерпали попытки → False + error [IMP:10] (caller решает: build вероятно упадёт)
    ##   - Нет Dockerfile/нет баз → True (no-op, не ошибка)
    ##   - Логи [IMP:8][docker_prebuild_pull] на старт/этап, [IMP:9] успех, [IMP:10] исчерпание
    ## @changes 2026-08-27 | F-03 (017-launch-validation P0) — Created
    """
    refs = _module_base_images(module_dir)
    if not refs:
        logger.info(
            "[IMP:8][docker_prebuild_pull][no_bases] No pinned base images for %s — nothing to pre-pull",
            module_dir,
        )
        return True

    logger.info(
        "[IMP:8][docker_prebuild_pull][start] Pre-pulling %d base image(s) for %s: %s",
        len(refs),
        module_dir,
        refs,
    )
    pulled: list[str] = []
    failed: list[str] = []
    for ref in refs:
        ok = _shared_retry(
            functools.partial(_docker_pull_ref, ref),
            attempts=PREBUILD_PULL_ATTEMPTS,
            backoff_seconds=PREBUILD_PULL_BACKOFF_SECONDS,
            retryable=lambda ok: not ok,
            sleep_fn=sleep_fn,
        )
        if ok:
            pulled.append(ref)
            logger.info("[IMP:9][docker_prebuild_pull][pulled] %s", ref)
        else:
            failed.append(ref)
            logger.error(
                "[IMP:10][docker_prebuild_pull][exhausted] All %d attempts failed for base image %s",
                PREBUILD_PULL_ATTEMPTS,
                ref,
            )

    if failed:
        if pulled:
            logger.warning(
                "[IMP:7][docker_prebuild_pull][partial] %d/%d base images pre-pulled; failed: %s",
                len(pulled),
                len(refs),
                failed,
            )
            return True
        logger.error(
            "[IMP:10][docker_prebuild_pull][fail] Pre-pull failed for ALL %d base image(s): %s",
            len(refs),
            failed,
        )
        return False

    logger.info("[IMP:9][docker_prebuild_pull][done] All %d base image(s) pre-pulled", len(refs))
    return True


# endregion FUNC_docker_prebuild_pull


# region FUNC_check_image_exists


def check_image_exists(image_ref: str, timeout: int = IMAGE_CHECK_TIMEOUT) -> bool:
    """Check if a Docker image exists in registry via docker manifest inspect.

    ▶ ┌image_ref┐ → ◇ docker manifest inspect → returncode==0? → ⎋ True
    │                                                       → else → ⎋ False

    ## @purpose — Verify image existence without pulling (uses manifest inspect).
    ## @io — ⇥ image_ref: str, timeout: int → ⎋ bool (True = image exists)
    ## @complexity — O(1) + network
    ## @invariants
    ##   - Uses `docker manifest inspect` which does not pull the image
    ##   - stderr is suppressed (expected on non-existent image)
    ##   - Non-fatal: returns False on errors/timeout
    """
    logger.info("[IMP:7][check_image_exists] Checking image: %s", image_ref)
    # docker manifest inspect (W1: примитив — shared/docker_ops)
    return docker_ops.docker_manifest_inspect(image_ref, timeout=timeout)


# endregion FUNC_check_image_exists


# region FUNC_nginx_reload
## @purpose  Reload nginx container via docker exec (DevPlan 118 D6). Единый фасад nginx-reload
##           в shared-слое — устраняет дубль docker CLI вызова.
## @io       ⇥ container: str = "nginx", timeout: int = DOCKER_CMD_TIMEOUT → ⎋ None (side-effect: reload)
## @complexity — O(1) — single subprocess call
## @invariants
##   - docker exec nginx nginx -s reload (non-fatal контракт: caller решает severity)
##   - timeout — из shared/timeouts (DOCKER_CMD_TIMEOUT)
##   - Raises subprocess.TimeoutExpired/OSError/FileNotFoundError — caller (шаг D6) ловит
def nginx_reload(container: str = "nginx", timeout: int = DOCKER_CMD_TIMEOUT) -> None:
    """Reload nginx container via docker exec (shared facade, DevPlan 118 D6)."""
    logger.info("[IMP:7][nginx_reload] Reloading nginx container: %s", container)
    # docker exec (W1: примитив — shared/docker_ops; non-fatal контракт сохраняется)
    docker_ops.docker_exec(container, ["nginx", "-s", "reload"], timeout=timeout)
    logger.info("[IMP:9][nginx_reload] nginx reload executed: %s", container)


# endregion FUNC_nginx_reload
