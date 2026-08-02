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
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import time

from core.internal.shared.timeouts import (
    BUILD_TIMEOUT,
    COMPOSE_UP_TIMEOUT,
    DOCKER_CMD_TIMEOUT,
    HEALTHCHECK_POLL_TIMEOUT,
    IMAGE_CHECK_TIMEOUT,
    PULL_TIMEOUT,
    RETRY_BACKOFF_SECONDS,
)

logger = logging.getLogger(__name__)


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
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=compose_dir,
            env=env,
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
    if not os.path.isdir(compose_dir):
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
    ##       service: str | None (ограничить down одним сервисом — watchdog rollback) → ⎋ bool (True = success)
    ## @complexity — O(1) + shutdown I/O
    ## @invariants
    ##   - Non-fatal: returns False on failure
    ##   - Никогда не добавляет -v по умолчанию (O7: данные проекта не удаляются)
    ##   - service (если задан) — последним аргументом (docker compose down <service>)
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — sole-path extension (rollback/remove)
    ##           2026-08-01 · DevPlan 117 D19 — добавлен service параметр (watchdog DockerManager)
    """
    if not os.path.isdir(compose_dir):
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


# region FUNC_healthcheck_poll


def healthcheck_poll(
    project_name: str,
    timeout: int = HEALTHCHECK_POLL_TIMEOUT,
    interval: int = 3,
    service: str | None = None,
) -> str:
    """Poll container health for a project until healthy or timeout (inspect-критерий, D5).

    ▶ ┌project_name + timeout┐ → ○ loop [interval]: ◇ docker ps/compose ps → cids → ○ for cid: inspect State.Status|Health.Status → ◇ все (running AND healthy|""|none)? → "healthy"
    │                                                                                                     → timeout? → "unhealthy"

    ## @purpose — Wait for a docker compose project to become healthy. ЕДИНЫЙ критерий «здоров»:
    ##            контейнер running AND Health.Status ∈ {healthy, "", "none"} (running-без-healthcheck
    ##            = здоров). "unhealthy" НЕ фейлит сразу — стартовые гонки (ждём).
    ## @io — ⇥ project_name: str, timeout: int, interval: int, service: str | None → ⎋ str ("healthy"|"unhealthy")
    ## @complexity — O(T/I) где T = timeout, I = interval
    ## @invariants
    ##   - service задан → `docker compose ps -q {service}` (deploy_engine; cwd = project_dir через chdir);
    ##     иначе `docker ps --filter name={project_name}` (глобальный фильтр)
    ##   - inspect: --format '{{.State.Status}}|{{.State.Health.Status}}'
    ##   - ВСЕ контейнеры должны быть здоровы (любой не-здоров → ждать)
    ##   - timeout → "unhealthy"; non-fatal: docker-ошибки → ждать (не raise)
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — критерий переработан на inspect (5 реализаций → 1, D5)
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            if service:
                result = subprocess.run(
                    ["docker", "compose", "ps", "-q", service],
                    capture_output=True,
                    text=True,
                    timeout=DOCKER_CMD_TIMEOUT,
                )
                cids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            else:
                result = subprocess.run(
                    ["docker", "ps", "--filter", f"name={project_name}", "--format", "{{.ID}}"],
                    capture_output=True,
                    text=True,
                    timeout=DOCKER_CMD_TIMEOUT,
                )
                cids = [line.strip() for line in result.stdout.splitlines() if line.strip()]

            if result.returncode != 0 or not cids:
                logger.debug(
                    "[IMP:6][healthcheck_poll] No containers for %s yet (rc=%d)", project_name, result.returncode
                )
                time.sleep(interval)
                continue

            # Единый критерий «здоров» (D5): ВСЕ контейнеры running AND (healthy | "" | none)
            all_healthy = True
            for cid in cids:
                insp = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Status}}|{{.State.Health.Status}}", cid],
                    capture_output=True,
                    text=True,
                    timeout=DOCKER_CMD_TIMEOUT,
                )
                status_line = insp.stdout.strip()
                state, _, health = status_line.partition("|")
                if not (state == "running" and health in ("healthy", "", "none")):
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
            time.sleep(interval)
            continue

        time.sleep(interval)

    logger.warning("[IMP:7][healthcheck_poll] %s — timeout (%ds) — unhealthy", project_name, timeout)
    return "unhealthy"


# endregion FUNC_healthcheck_poll


# region FUNC_retry_pull


def retry_pull(
    compose_dir: str,
    max_attempts: int = 3,
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
    ##            docker_orchestrator + deploy_engine подключаются, T4.5/T5.1).
    ## @io — ⇥ compose_dir: str, max_attempts: int, backoff_seconds: list[int] | None,
    ##       timeout: int, compose_args, service, env_override → ⎋ bool
    ## @complexity — O(max_attempts * pull_time)
    ## @invariants
    ##   - Default backoff: timeouts.RETRY_BACKOFF_SECONDS ([5, 10, 20])
    ##   - If backoff list shorter than attempts, all remaining attempts use last value
    ##   - All attempts must fail for final False; пробрасывает service/env_override в pull (D7)
    ## @changes 2026-08-01 · DevPlan 116 B5 T3 — service/env_override/compose_args проброс (D7)
    """
    if backoff_seconds is None:
        backoff_seconds = RETRY_BACKOFF_SECONDS

    for attempt in range(1, max_attempts + 1):
        logger.info(
            "[IMP:7][retry_pull] Attempt %d/%d for %s",
            attempt,
            max_attempts,
            compose_dir,
        )
        if docker_compose_pull(
            compose_dir,
            timeout=timeout,
            compose_args=compose_args,
            service=service,
            env_override=env_override,
        ):
            logger.info(
                "[IMP:9][retry_pull] Pull succeeded on attempt %d/%d for %s", attempt, max_attempts, compose_dir
            )
            return True

        if attempt < max_attempts:
            backoff_idx = min(attempt - 1, len(backoff_seconds) - 1)
            sleep_sec = backoff_seconds[backoff_idx]
            logger.warning(
                "[IMP:7][retry_pull] Pull attempt %d failed — retrying in %ds",
                attempt,
                sleep_sec,
            )
            time.sleep(sleep_sec)

    logger.warning("[IMP:7][retry_pull] All %d attempts failed for %s", max_attempts, compose_dir)
    return False


# endregion FUNC_retry_pull


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
    try:
        result = subprocess.run(
            ["docker", "manifest", "inspect", image_ref],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][check_image_exists] Image exists: %s", image_ref)
            return True
        logger.warning("[IMP:5][check_image_exists] Image NOT found: %s", image_ref)
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:5][check_image_exists] docker manifest inspect timed out for %s", image_ref)
        return False
    except FileNotFoundError:
        logger.error("[IMP:10][check_image_exists] docker command not found")
        return False


# endregion FUNC_check_image_exists


# region FUNC_nginx_reload
## @purpose  Reload nginx container via docker exec (DevPlan 118 D6). Единый фасад nginx-reload
##           в shared-слое — ранее docker exec вызывался инлайн в context_deployer.deploy_context
##           (god-function, теперь _step_nginx_reload). Устраняет дубль docker CLI вызова.
## @io       ⇥ container: str = "nginx", timeout: int = DOCKER_CMD_TIMEOUT → ⎋ None (side-effect: reload)
## @complexity — O(1) — single subprocess call
## @invariants
##   - docker exec nginx nginx -s reload (non-fatal контракт: caller решает severity)
##   - timeout — из shared/timeouts (DOCKER_CMD_TIMEOUT)
##   - Raises subprocess.TimeoutExpired/OSError/FileNotFoundError — caller (шаг D6) ловит
def nginx_reload(container: str = "nginx", timeout: int = DOCKER_CMD_TIMEOUT) -> None:
    """Reload nginx container via docker exec (shared facade, DevPlan 118 D6)."""
    logger.info("[IMP:7][nginx_reload] Reloading nginx container: %s", container)
    subprocess.run(
        ["docker", "exec", container, "nginx", "-s", "reload"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    logger.info("[IMP:9][nginx_reload] nginx reload executed: %s", container)


# endregion FUNC_nginx_reload
