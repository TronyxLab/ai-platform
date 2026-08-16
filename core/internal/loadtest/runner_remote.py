#!/usr/bin/env python3
# GREP_SUMMARY: loadtest remote runner rsync docker-run locust container node LOAD_RUNNER image cpus ssh network
# STRUCTURE: ▶ ship (rsync core/loadtest/ → /tmp/loadtest-<ts>/) → ◇ docker run --rm --network <network>
#           --cpus ${LOAD_CPUS:-2} -v remote:/lt -w /lt ${LOAD_IMAGE:-locustio/locust:2.32.10} → ◇ fetch
#           (rsync results обратно) → ⎋ локальная сборка отчёта (PromQL pull с локальной машины)
# region MODULE_CONTRACT
## @purpose  Remote-режим генератора (DevPlan 146 W5, LOAD_RUNNER=node): locust-прогон
##           выполняется в docker-контейнере НА ноде (не через docker compose сервисов —
##           инвариант 3), когда канал dev-машины до ноды слабый. rsync сценария+SoT на
##           ноду (канон shared.ssh_opts — НЕ tests/_conftest, runtime не импортирует
##           тестовую инфраструктуру), docker run с --network host и --cpus LOAD_CPUS,
##           rsync CSV обратно; PromQL-pull и сборка отчёта — локально.
## @scope    Потребитель: runner_cli.py (LOAD_RUNNER=node ветка). Командостроители —
##           чистые функции (unit-тесты), exec-обёртки — subprocess через ssh_opts.
## @invariants
##   1. docker run ОТДЕЛЬНО от стека: НЕ compose-сервис, НЕ observability-net (инвариант 3)
##   2. Образ параметризуется LOAD_IMAGE (default locustio/locust:2.32.10 — полный semver:
##      minor-only тега 2.32 в Docker Hub НЕ существует, BUG-4 146-m4) — Docker Hub
##      rate-limit митигируется ghcr.io-зеркалом/кэшем (риск R8, DevPlan 146 §7)
##   3. boto3 в образе отсутствует — s3-сценарий через HTTP API minio (не boto3)
##   4. CPU-limit LOAD_CPUS (default 2) — генератор не съедает хост под capacity
##   5. env в контейнер — только через -e (значения shlex.quote — никакой shell-инъекции)
##   6. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
##   7. Сеть контейнера — параметр network (DevPlan 148 TASK-4): default "host" — web/s3
##      (эндпоинты сервисов ноды на host-сети); db — "shared-db-net" (PostgreSQL публикуется
##      ТОЛЬКО в docker-сеть, NO ports: directive — `docker run --network shared-db-net`
##      даёт доступ по DNS-алиасу postgres:5432). Значение из scenarios.yaml#network
##      (config.py), override LOAD_NETWORK.
## @rationale Генератор на ноде (рядом с сервисами) — минимальная сетевая зависимость
##            от слабого канала dev-машины; docker run вместо compose — изоляция от стека
##            (инвариант 3) и точный контроль ресурсов (--cpus). network — расширение
##            существующего builder'а (не новая инфраструктура): postgres/pgbouncer
##            публикуются только в shared-db-net, и контейнер генератора должен войти
##            в эту сеть, чтобы достать их по DNS-алиасу без хардкода IP.
## @changes  2026-08-11 | DevPlan 146 W5 — Created
## @changes  2026-08-12 | DevPlan 148 TASK-4 — network-параметр (--network, default host)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from collections.abc import Callable

from core.internal.shared.ssh_opts import SSH_OPTS, build_rsync_ssh_opts
from core.internal.shared.subprocess_io import run_subprocess

logger = logging.getLogger(__name__)

# Пин образа должен совпадать с pyproject.toml pin (locust>=2.32,<2.33 → установлена
# 2.32.10) и фактическими тегами Docker Hub: minor-only тега 2.32 НЕ существует,
# docker pull locustio/locust:2.32 → not found (BUG-4, 146-m4). Полный semver 2.32.10.
DEFAULT_IMAGE = "locustio/locust:2.32.10"
DEFAULT_CPUS = "2"
# Сеть контейнера генератора (DevPlan 148 TASK-4): web/s3 — host-сеть (эндпоинты
# сервисов ноды), db — shared-db-net (PostgreSQL только в docker-сети, NO ports: directive).
DEFAULT_NETWORK = "host"


# region DATA_RemoteError
class RemoteError(Exception):
    """Ошибка remote-режима (rsync/ssh/docker run) — exit 1 (контракт guard-таблицы)."""


# endregion DATA_RemoteError


# region FUNC_build_rsync_push_cmd
def build_rsync_push_cmd(src_dir: str, user: str, host: str, remote_dir: str) -> list[str]:
    """Команда rsync сценариев на ноду (ssh-e из канона ssh_opts).

    ▶ ┌src, user, host, remote_dir┐ → ○ ssh-e = build_rsync_ssh_opts → ⎋ ["rsync", "-az", "-e", ...]

    ## @purpose  Ship-команда (инвариант: ssh через shared.ssh_opts канон — единый
    ##            источник SSH-флагов платформы, DevPlan 116 B5 D1).
    ## @io — ⇥ src_dir: str — С TRAILING SLASH (контракт, BUG-5 146-m5): rsync без '/'
    ##         копирует src вложенной папкой в remote_dir; нормализует ship() перед
    ##         вызовом; user/host: str, remote_dir: str
    ##       → ⎋ list[str] — argv для subprocess
    ## @complexity — O(1)
    """
    return ["rsync", "-az", "-e", build_rsync_ssh_opts(), src_dir, f"{user}@{host}:{remote_dir}"]


# endregion FUNC_build_rsync_push_cmd


# region FUNC_build_rsync_fetch_cmd
def build_rsync_fetch_cmd(remote_dir: str, user: str, host: str, local_dir: str) -> list[str]:
    """Команда rsync результатов обратно на dev-машину.

    ▶ ┌remote_dir, user, host, local_dir┐ → ⎋ ["rsync", "-az", "-e", ...]

    ## @purpose  Fetch-команда: CSV/отчёт ноды → локальный load-results/.
    ## @io — ⇥ remote_dir: str (results-директория на ноде), user/host, local_dir: str
    ##       → ⎋ list[str] — argv для subprocess
    ## @complexity — O(1)
    """
    return ["rsync", "-az", "-e", build_rsync_ssh_opts(), f"{user}@{host}:{remote_dir}/", f"{local_dir}/"]


# endregion FUNC_build_rsync_fetch_cmd


# region FUNC_build_ssh_docker_run_cmd
def build_ssh_docker_run_cmd(
    image: str,
    cpus: str,
    remote_workdir: str,
    env: dict[str, str],
    locust_args: list[str],
    network: str = DEFAULT_NETWORK,
) -> str:
    """Сборка remote-команды: ssh "docker run --rm --network <net> --cpus ... -e ... image locust-args".

    ▶ ┌image, cpus, workdir, env, locust_args, network┐ → ○ docker run argv (env → -e, shlex.quote)
      → ⎋ str — одна shell-команда для ssh (значения экранированы)

    ## @purpose  Единственный builder docker-запуска на ноде (инварианты 1-5, 7): образ
    ##            LOAD_IMAGE, --network <network> (default host — web/s3; shared-db-net —
    ##            db, 148 TASK-4), --cpus LOAD_CPUS, -v workdir:/lt -w /lt, env через -e
    ##            с shlex.quote (без shell-инъекции).
    ## @io — ⇥ image: str, cpus: str, remote_workdir: str (например /tmp/loadtest-<ts>),
    ##         env: dict[str, str] (LT_*), locust_args: list[str] (-f ... --headless ...),
    ##         network: str (docker-сеть контейнера; DEFAULT_NETWORK "host")
    ##       → ⎋ str — команда для `ssh user@host <cmd>`
    ## @complexity — O(E) — E = число env-переменных
    ## @invariants
    ##   - ENTRYPOINT образа = locust (locustio/locust) — locust_args передаются как есть
    ##   - Значения env экранируются shlex.quote (кавычки/пробелы/метасимволы)
    ##   - Никаких локальных путей в remote-команде (контракт core/AGENTS.md T9)
    ##   - --network всегда явный (host — тоже явный флаг, не дефолт docker daemon)
    """
    docker = ["docker", "run", "--rm", "--network", network, "--cpus", cpus]
    docker += ["-v", f"{remote_workdir}:/lt", "-w", "/lt"]
    for key in sorted(env):
        docker += ["-e", f"{key}={env[key]}"]
    docker.append(image)
    docker += locust_args
    cmd = " ".join(shlex.quote(part) for part in docker)
    logger.info("[IMP:8][remote][build_docker_run] network=%s %s", network, cmd[:200])
    return cmd


# endregion FUNC_build_ssh_docker_run_cmd


# region FUNC_ship
def ship(
    host: str,
    user: str,
    src_dir: str,
    remote_dir: str,
    timeout: int = 300,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> None:
    """rsync core/loadtest/ → /tmp/loadtest-<ts>/ на ноде (шаг 1 remote-режима).

    ▶ ┌host, user, src, remote_dir┐ → ○ normalize src trailing slash → ○ rsync push (ssh_opts)
      → ○ ssh chmod -R a+rwX (write-права для контейнера) → ◇ rc != 0 → RemoteError → ⎋ None

    ## @purpose  Доставка сценариев+SoT на ноду. SSH через канон ssh_opts (инвариант:
    ##            runtime НЕ импортирует tests/_conftest — NodeSSHClient только для e2e).
    ##            TRAILING SLASH ОБЯЗАТЕЛЕН (BUG-5, 146-m5): rsync БЕЗ '/' копирует src
    ##            ДИРЕКТОРИЕЙ в remote_dir (→ remote_dir/loadtest/...), а контейнер
    ##            монтирует workdir в /lt и ждёт /lt/scenarios/<name>.py — вложенность
    ##            ломает путь (Could not find '/lt/scenarios/s3.py'). Нормализация здесь —
    ##            single-point fix, защищает всех вызывающих.
    ##            chmod a+rwX (BUG-6, 146-m6): rsync-ship создаёт remote_dir от root
    ##            (owner root, mode 755), docker run locust-образа — от non-root → контейнер
    ##            не может создать /lt/results (csv_prefix) → PermissionError [Errno 13].
    ##            chmod (не chown к uid 1000 — хрупок при смене версии образа) даёт write-
    ##            права пользователю контейнера без привязки к конкретному uid.
    ## @io — ⇥ host: str, user: str, src_dir: str (trailing slash нормализуется
    ##         rstrip("/") + "/" — содержимое копируется, не вложенная папка),
    ##         remote_dir: str, timeout: int,
    ##         runner: Callable | None (167 D0 DI: rsync/ssh-канал; None → run_subprocess)
    ##       → ⎋ None
    ## @complexity — O(F + N) — F = объём передаваемых файлов, N = число файлов (chmod)
    ## @raises — RemoteError: rsync ИЛИ chmod вернул ненулевой rc
    """
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · runner-параметр: rsync/ssh-канал через DI вместо
    # ·   monkeypatch runner_remote.run_subprocess (DevPlan 167 D0)
    # · Rejected: прямой вызов run_subprocess (тест не мог бы перехватить argv-последовательность)
    # · Reason: runner=None → модульный run_subprocess (поведение без изменений); тесты передают
    # ·   call-recording fake — BUG-5/6 argv-контракт (trailing slash, chmod) наблюдается честно
    # · Rev: при переходе на CommandRunner-протокол (shared/subprocess_io) — синхронизировать
    run_impl = runner if runner is not None else run_subprocess
    src_dir = str(src_dir).rstrip("/") + "/"
    cmd = build_rsync_push_cmd(src_dir, user, host, remote_dir)
    result = run_impl(cmd, timeout=timeout, check=False, non_fatal=True)
    if result.returncode != 0:
        msg = f"rsync ship failed (rc={result.returncode}): {result.stderr.strip()[:300]}"
        raise RemoteError(msg)
    chmod_cmd = f"chmod -R a+rwX {remote_dir}"
    chmod_result = run_impl(
        ["ssh", *SSH_OPTS, f"{user}@{host}", chmod_cmd],
        timeout=timeout,
        check=False,
        non_fatal=True,
    )
    if chmod_result.returncode != 0:
        msg = f"remote chmod failed (rc={chmod_result.returncode}): {chmod_result.stderr.strip()[:300]}"
        raise RemoteError(msg)
    logger.info("[IMP:8][remote][ship] src normalized to trailing slash: %s", src_dir)
    logger.info("[IMP:9][remote][ship] %s → %s@%s:%s (chmod a+rwX)", src_dir, user, host, remote_dir)


# endregion FUNC_ship


# region FUNC_fetch
def fetch(host: str, user: str, remote_dir: str, local_dir: str, timeout: int = 300) -> None:
    """rsync результатов с ноды → локальный load-results/ (шаг 4 remote-режима).

    ▶ ┌host, user, remote_dir, local_dir┐ → ○ rsync fetch → ◇ rc != 0 → RemoteError → ⎋ None

    ## @purpose  Обратная доставка CSV (stats/history) — отчёт собирается локально,
    ##            PromQL-pull — с локальной машины к Prometheus ноды (DevPlan 146 §3.6).
    ## @io — ⇥ host/user, remote_dir: str, local_dir: str, timeout: int → ⎋ None
    ## @complexity — O(F) — F = объём CSV
    ## @raises — RemoteError: rsync вернул ненулевой rc
    """
    cmd = build_rsync_fetch_cmd(remote_dir, user, host, local_dir)
    result = run_subprocess(cmd, timeout=timeout, check=False, non_fatal=True)
    if result.returncode != 0:
        msg = f"rsync fetch failed (rc={result.returncode}): {result.stderr.strip()[:300]}"
        raise RemoteError(msg)
    logger.info("[IMP:9][remote][fetch] %s@%s:%s → %s", user, host, remote_dir, local_dir)


# endregion FUNC_fetch


# region FUNC_run_remote_locust
def run_remote_locust(
    host: str,
    user: str,
    image: str,
    cpus: str,
    remote_workdir: str,
    env: dict[str, str],
    locust_args: list[str],
    timeout: int = 600,
    network: str = DEFAULT_NETWORK,
) -> None:
    """ssh docker run locust-контейнера на ноде (шаг 2-3 remote-режима).

    ▶ ┌host, user, image, cpus, workdir, env, locust_args, timeout, network┐ → ○ build_ssh_docker_run_cmd
      → ○ ssh (SSH_OPTS) → ◇ rc != 0 → RemoteError → ⎋ None

    ## @purpose  Исполнение прогона на ноде: docker run изолирован от стека (инвариант 3),
    ##            --network <network> (инвариант 7: host — web/s3; shared-db-net — db,
    ##            148 TASK-4), --cpus (инвариант 4).
    ## @io — ⇥ host: str, user: str, image: str, cpus: str, remote_workdir: str,
    ##         env: dict[str, str] (LT_*), locust_args: list[str], timeout: int,
    ##         network: str (docker-сеть; default DEFAULT_NETWORK "host") → ⎋ None
    ## @complexity — O(RT) — RT = run_time прогона
    ## @raises — RemoteError: ssh/docker вернул ненулевой rc (вывод docker в сообщении)
    """
    cmd = build_ssh_docker_run_cmd(image, cpus, remote_workdir, env, locust_args, network=network)
    result = run_subprocess(
        ["ssh", *SSH_OPTS, f"{user}@{host}", cmd],
        timeout=timeout,
        check=False,
        non_fatal=True,
    )
    if result.returncode != 0:
        tail = result.stdout.strip()[-2000:] if result.stdout.strip() else result.stderr.strip()[-2000:]
        msg = f"remote locust run failed (rc={result.returncode}): {tail}"
        raise RemoteError(msg)
    logger.info("[IMP:9][remote][run] locust run completed on %s@%s (network=%s)", user, host, network)


# endregion FUNC_run_remote_locust


# region FUNC_make_remote_dir
def make_remote_workdir() -> str:
    """Уникальная remote-директория: /tmp/loadtest-<unix-ts>.

    ▶ ┌—┐ → ○ time.time → ⎋ "/tmp/loadtest-<ts>"

    ## @purpose  Изоляция прогонов на ноде (конкурентные прогоны не затирают друг друга).
    ## @io — ⇥ None → ⎋ str
    ## @complexity — O(1)
    """
    return f"/tmp/loadtest-{int(time.time())}"  # nosec B108 — remote node workdir per DevPlan 146 §3.6 (fixed /tmp contract, rsync-isolated)


# endregion FUNC_make_remote_dir
