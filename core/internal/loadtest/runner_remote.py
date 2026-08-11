#!/usr/bin/env python3
# GREP_SUMMARY: loadtest remote runner rsync docker-run locust container node LOAD_RUNNER image cpus ssh
# STRUCTURE: ▶ ship (rsync core/loadtest/ → /tmp/loadtest-<ts>/) → ◇ docker run --rm --network host
#           --cpus ${LOAD_CPUS:-2} -v remote:/lt -w /lt ${LOAD_IMAGE:-locustio/locust:2.32} → ◇ fetch
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
##   2. Образ параметризуется LOAD_IMAGE (default locustio/locust:2.32) — Docker Hub
##      rate-limit митигируется ghcr.io-зеркалом/кэшем (риск R8, DevPlan 146 §7)
##   3. boto3 в образе отсутствует — s3-сценарий через HTTP API minio (не boto3)
##   4. CPU-limit LOAD_CPUS (default 2) — генератор не съедает хост под capacity
##   5. env в контейнер — только через -e (значения shlex.quote — никакой shell-инъекции)
##   6. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
## @rationale Генератор на ноде (рядом с сервисами) — минимальная сетевая зависимость
##            от слабого канала dev-машины; docker run вместо compose — изоляция от стека
##            (инвариант 3) и точный контроль ресурсов (--cpus).
## @changes  2026-08-11 | DevPlan 146 W5 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import shlex
import time

from core.internal.shared.ssh_opts import SSH_OPTS, build_rsync_ssh_opts
from core.internal.shared.subprocess_io import run_subprocess

logger = logging.getLogger(__name__)

# Пин образа должен совпадать с pyproject.toml pin (locust>=2.32,<2.33) —
# иначе CLI-дрейф между docker-образом и dev-окружением (146-m1 TASK-6).
DEFAULT_IMAGE = "locustio/locust:2.32"
DEFAULT_CPUS = "2"


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
    ## @io — ⇥ src_dir: str (core/loadtest/), user/host: str, remote_dir: str
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
) -> str:
    """Сборка remote-команды: ssh "docker run --rm --network host --cpus ... -e ... image locust-args".

    ▶ ┌image, cpus, workdir, env, locust_args┐ → ○ docker run argv (env → -e, shlex.quote)
      → ⎋ str — одна shell-команда для ssh (значения экранированы)

    ## @purpose  Единственный builder docker-запуска на ноде (инварианты 1-5): образ
    ##            LOAD_IMAGE, --network host (доступ к сервисам ноды), --cpus LOAD_CPUS,
    ##            -v workdir:/lt -w /lt, env через -e с shlex.quote (без shell-инъекции).
    ## @io — ⇥ image: str, cpus: str, remote_workdir: str (например /tmp/loadtest-<ts>),
    ##         env: dict[str, str] (LT_*), locust_args: list[str] (-f ... --headless ...)
    ##       → ⎋ str — команда для `ssh user@host <cmd>`
    ## @complexity — O(E) — E = число env-переменных
    ## @invariants
    ##   - ENTRYPOINT образа = locust (locustio/locust) — locust_args передаются как есть
    ##   - Значения env экранируются shlex.quote (кавычки/пробелы/метасимволы)
    ##   - Никаких локальных путей в remote-команде (контракт core/AGENTS.md T9)
    """
    docker = ["docker", "run", "--rm", "--network", "host", "--cpus", cpus]
    docker += ["-v", f"{remote_workdir}:/lt", "-w", "/lt"]
    for key in sorted(env):
        docker += ["-e", f"{key}={env[key]}"]
    docker.append(image)
    docker += locust_args
    cmd = " ".join(shlex.quote(part) for part in docker)
    logger.info("[IMP:8][remote][build_docker_run] %s", cmd[:200])
    return cmd


# endregion FUNC_build_ssh_docker_run_cmd


# region FUNC_ship
def ship(host: str, user: str, src_dir: str, remote_dir: str, timeout: int = 300) -> None:
    """rsync core/loadtest/ → /tmp/loadtest-<ts>/ на ноде (шаг 1 remote-режима).

    ▶ ┌host, user, src, remote_dir┐ → ○ rsync push (ssh_opts) → ◇ rc != 0 → RemoteError → ⎋ None

    ## @purpose  Доставка сценариев+SoT на ноду. SSH через канон ssh_opts (инвариант:
    ##            runtime НЕ импортирует tests/_conftest — NodeSSHClient только для e2e).
    ## @io — ⇥ host: str, user: str, src_dir: str, remote_dir: str, timeout: int → ⎋ None
    ## @complexity — O(F) — F = объём передаваемых файлов
    ## @raises — RemoteError: rsync вернул ненулевой rc
    """
    cmd = build_rsync_push_cmd(src_dir, user, host, remote_dir)
    result = run_subprocess(cmd, timeout=timeout, check=False, non_fatal=True)
    if result.returncode != 0:
        raise RemoteError(f"rsync ship failed (rc={result.returncode}): {result.stderr.strip()[:300]}")
    logger.info("[IMP:9][remote][ship] %s → %s@%s:%s", src_dir, user, host, remote_dir)


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
        raise RemoteError(f"rsync fetch failed (rc={result.returncode}): {result.stderr.strip()[:300]}")
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
) -> None:
    """ssh docker run locust-контейнера на ноде (шаг 2-3 remote-режима).

    ▶ ┌host, user, image, cpus, workdir, env, locust_args, timeout┐ → ○ build_ssh_docker_run_cmd
      → ○ ssh (SSH_OPTS) → ◇ rc != 0 → RemoteError → ⎋ None

    ## @purpose  Исполнение прогона на ноде: docker run изолирован от стека (инвариант 3),
    ##            --network host (эндпоинты сервисов ноды), --cpus (инвариант 4).
    ## @io — ⇥ host: str, user: str, image: str, cpus: str, remote_workdir: str,
    ##         env: dict[str, str] (LT_*), locust_args: list[str], timeout: int → ⎋ None
    ## @complexity — O(RT) — RT = run_time прогона
    ## @raises — RemoteError: ssh/docker вернул ненулевой rc (вывод docker в сообщении)
    """
    cmd = build_ssh_docker_run_cmd(image, cpus, remote_workdir, env, locust_args)
    result = run_subprocess(
        ["ssh", *SSH_OPTS, f"{user}@{host}", cmd],
        timeout=timeout,
        check=False,
        non_fatal=True,
    )
    if result.returncode != 0:
        tail = result.stdout.strip()[-2000:] if result.stdout.strip() else result.stderr.strip()[-2000:]
        raise RemoteError(f"remote locust run failed (rc={result.returncode}): {tail}")
    logger.info("[IMP:9][remote][run] locust run completed on %s@%s", user, host)


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
