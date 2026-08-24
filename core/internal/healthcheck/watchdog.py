#!/usr/bin/env python3
# GREP_SUMMARY: watchdog, unhealthy-restart, docker-restart, cooldown, dry-run, stdlib-only, host-cron, is-n-loop, state-json, crashloop-notify, stamp-after-success
# STRUCTURE: ▶ scan_containers ┌docker ps -q + inspect┐ → ◇ filters (health?+restart!=no+RestartCount<=5) → ○ unhealthy_since state → ◇ unhealthy>=10min ∧ cooldown 30min? → ⚡ docker restart → ⊕ stamp last_restart ПОСЛЕ успеха + re-save per-action → ⚡ TG notify │ ◇ RestartCount>5? → ⚡ TG «crash-loop detected, не рестарчу» → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Host-side watchdog (DevPlan 132 W1): auto-restart of unhealthy containers that
##           survive their restart policy — «живой, но unhealthy» висит вечно без рестарта.
##           Runs from /etc/cron.d/platform-watchdog every 5 minutes (flock -n + timeout 50).
## @scope    core/internal/healthcheck/watchdog.py — stdlib-only (subprocess/json/time/os/argparse):
##           cron запускает скрипт БЕЗ PYTHONPATH, поэтому НЕ импортируется core.internal.
##           Telegram-notify — subprocess `python3 -m core.internal.shared.telegram_notifier notify`
##           с PYTHONPATH={core_dir} в env дочернего процесса (ядро само не импортирует core).
## @invariants
##   1. Stdlib-only — 0 импортов core.internal (cron без PYTHONPATH); путь state-файла —
##      литерал + env-override, НИКОГДА импорт core.internal
##   2. Фильтры кандидатов: health-статус существует И != healthy/none; RestartPolicy.Name != "no"
##      (one-shot: prometheus-config-init, minio-createbuckets); RestartCount <= 5
##      (канон RESTART_LOOP_THRESHOLD — watchdog.py, T2.6: CrashLoopBackOff рестартом не лечится)
##   3. Действие: unhealthy >= WATCHDOG_UNHEALTHY_MIN (default 10 мин) И cooldown 30 мин с
##      last_restart → docker restart + Telegram notify (severity=critical, context=watchdog);
##      штамп last_restart наносится ТОЛЬКО ПОСЛЕ успешного restart + re-save per-action
##      (REF-0014 stamp-after-success: failure → NO stamp + skip-notify — cooldown не сгорает)
##   4. State /var/lib/platform/run/watchdog-state.json (persistent — НЕ tmpfs, 142 W2; atomic write
##      tempfile+os.replace): unhealthy_since {container: ts}, last_restart {container: ts},
##      crashloop_notified {container: ts} (REF-0014 suppress TG crash-loop); мусорные записи чистятся
##   5. docker CLI недоступен → IMP:7 + exit 0 (non-fatal); docker-команда упала → IMP:10 + exit 1
##   6. --dry-run: печатает план действий, без restart/notify/state-mutation
##   7. exit 0 при отсутствии действий; exit 1 при внутренней ошибке (docker fail / рестарт не удался)
##   8. Skip-path crash-loop (RestartCount > RESTART_LOOP_THRESHOLD) НЕ молчит: TG
##      watchdog.crashloop «crash-loop detected, не рестарчу» (REF-0014; suppress 60 мин/container)
## @rationale D1: host-cron по канону install_cron_metrics (/etc/cron.d, flock -n + timeout) —
##           точечное расширение без новой архитектуры; stdlib-only исключает PYTHONPATH-зависимость.
## @changes 2026-08-04 | DevPlan 132 W1 — создан
## @changes 2026-08-24 | REF-0014 (DevPlan meta-refactoring В1) — stamp-after-success + re-save
##           per-action (state-commit транзакционен с действием); failed restart не блокирует
##           остальные действия (exit 1 в конце прохода); crash-loop skip-path → TG-нотификация
## @modulemap
##   scan_containers [W:1] — docker ps -q → docker inspect (Name/Health/RestartCount/RestartPolicy)
##   decide_actions [W:1] — фильтры + unhealthy_since/last_restart + решение restart/wait (без штампов)
##   load_state/save_state [W:1] — atomic JSON state (persistent /var/lib/platform/run)
##   restart_container [W:1] — docker restart + IMP:9 RESTART
##   notify_telegram [W:1] — subprocess telegram_notifier notify (best-effort)
##   notify_crashloop [W:1] — subprocess notifications notify watchdog.crashloop (skip-path, REF-0014)
##   run_watchdog [W:1] — оркестрация → exit 0|1
## @usecases
##   - cron: */5 * * * * root flock -n /run/lock/platform-watchdog.lock timeout 50 watchdog.py
##   - ручной dry-run: python3 watchdog.py --dry-run
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:  # pragma: no cover — никогда не исполняется в runtime (cron-safe, 162 W1-1)
    from core.internal.shared.env_facts import EnvironmentFacts

logger = logging.getLogger(__name__)

# ── Константы (env-оверрайды для тестов/оператора) ──
# 142 W2 (B21): state-файл переехал в persistent /var/lib/platform/run —
# reboot-устойчивость (watchdog-state.json не должен теряться при перезагрузке ноды).
# ⚠️ TRAP[BUG] · 2026-08-13 · P1 · Регрессия 142 W2: импорт core.internal в stdlib-only скрипт ломал cron
# · Symptom: ModuleNotFoundError каждые 5 минут (cron без PYTHONPATH), watchdog мёртв — единственный auto-healer не работал
# · Root: импорт core.internal.shared.deploy_paths на module-level нарушал @invariant 1 («Stdlib-only — 0 импортов core.internal»)
# · Fix: литерал пути + env-override; НИКОГДА импорт core.internal на module-level. Merge-нота (160 W4b): DI-параметр
# ·   facts (EnvironmentFacts) сохранён — но импорт env_facts только ЛОКАЛЬНЫЙ с stdlib-fallback (shutil.which):
# ·   cron без PYTHONPATH получает None-facts и работает на чистом stdlib (см. _default_facts/_docker_binary).
# · Prevention: CI-gate test_gate_watchdog_clean_env.py гоняет watchdog в чистом env (env -i, python3 -S)
# 🧐 TRAP[DECISION] · 2026-08-14 · — · Литерал state-файла сохранён (не deploy_paths.watchdog_state_file)
# · Rejected: импорт core.internal.shared.deploy_paths (резолвер существует — 142 W2/170 W1-A2)
# · Reason: stdlib-only канон модуля (@invariant 1: «0 импортов core.internal») — cron без PYTHONPATH;
# ·   импорт = регрессия 142 W2 (ModuleNotFoundError каждые 5 минут, P1 — TRAP[BUG] выше).
# ·   allowlist гейта test_gate_run_paths_sole.py.
# · Rev: cron-строка начнёт задавать PYTHONPATH (или env-инъекцию WATCHDOG_STATE_FILE) — заменить
DEFAULT_STATE_FILE: str = os.environ.get(
    "WATCHDOG_STATE_FILE",
    "/var/lib/platform/run/watchdog-state.json",  # литерал (142 W2 reboot-устойчивость; /var/lib/platform/run — persistent, НЕ tmpfs)
)
DEFAULT_UNHEALTHY_MIN = 10  # WATCHDOG_UNHEALTHY_MIN — сколько минут unhealthy до рестарта
DEFAULT_COOLDOWN_MIN = 30  # WATCHDOG_COOLDOWN_MIN — пауза между рестартами одного контейнера
# T2.6: ЕДИНАЯ константа restart-loop (одно имя, одно определение).
# Канон-место — watchdog.py: watchdog — enforcement-site (RestartCount <= X решает рестарт),
# И stdlib-only (@invariant 1: 0 импортов core.internal, cron без PYTHONPATH — TRAP[BUG] 142 W2),
# т.е. watchdog физически НЕ может импортировать modules_healthcheck/shared. Обратный импорт
# безопасен (module-level watchdog = чистый stdlib). modules_healthcheck.RESTART_LOOP_THRESHOLD
# импортируется отсюда (DevPlan T2.6). Значение 5 НЕ меняется: RestartCount > 5 = CrashLoopBackOff.
RESTART_LOOP_THRESHOLD = 5
DOCKER_TIMEOUT = 30  # таймаут docker-команд (файл вне domain-скоупа timeout-literals гейта)
# REF-0014: suppress-окно TG «crash-loop detected, не рестарчу» per-container. CLI-throttle
# notifications.py процесс-локален (cron = новый процесс каждые 5 мин — реестр пуст), поэтому
# окно держит сам watchdog в persistent state (ключ crashloop_notified, 142 W2).
CRASHLOOP_NOTIFY_COOLDOWN_MIN = 60


class DockerError(Exception):
    """Внутренняя ошибка docker-команды — сигнал exit 1 (не отсутствие docker CLI)."""


# region DATA_ContainerRecord
class ContainerRecord(TypedDict):
    """Запись контейнера из docker inspect (граница JSON) — вход фильтров/решения.

    ## @purpose  Типизированная запись скана: id/name контейнера, health-статус
    ##            (None при отсутствии Health-блока), restart_count/policy.
    """

    id: str
    name: str
    health: str | None
    restart_count: int
    restart_policy: str


# endregion DATA_ContainerRecord


# region DATA_WatchdogState
class WatchdogState(TypedDict):
    """Состояние watchdog (state-файл, граница JSON): unhealthy_since + last_restart.

    ## @purpose  Персистентное состояние между cron-прогонами: {container: unix-ts}.
    ##            crashloop_notified — ts последней TG «не рестарчу» (REF-0014 suppress-окно).
    """

    unhealthy_since: dict[str, float]
    last_restart: dict[str, float]
    crashloop_notified: dict[str, float]


# endregion DATA_WatchdogState


# region DATA_Action
class Action(TypedDict):
    """Одно действие рестарта (решение decide_actions → исполнение в run_watchdog)."""

    name: str
    id: str
    since: float
    now: float


# endregion DATA_Action


# region DATA_CliArgs
class CliArgs(argparse.Namespace):
    """Типизированный namespace CLI (W11): ТОЛЬКО аннотации без значений.

    ## @purpose  Значения НЕ задаются class-атрибутами — hasattr(namespace, dest)
    ##            перебивает parser-дефолты; поля заполняет parse_args(namespace=CliArgs()).
    """

    dry_run: bool  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)
    state_file: str | None  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)


# endregion DATA_CliArgs


# W11: единый тип DI-канала docker/notify-команд (167 D0) — CompletedProcess-контракт
RunCmd = Callable[..., subprocess.CompletedProcess[str]]


# region FUNC__run_cmd
## @purpose  Единая точка subprocess.run (stdlib) — monkeypatch-мишень unit-тестов.
## @io       ⇥ cmd: list[str], timeout: int, env: dict | None → ⎋ subprocess.CompletedProcess
## @complexity O(1) — один subprocess-вызов
def _run_cmd(
    cmd: list[str], timeout: int = DOCKER_TIMEOUT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command capturing output. FileNotFoundError propagates to caller."""
    logger.info("[IMP:7][watchdog][run] cmd=%s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)


# endregion FUNC__run_cmd


# region FUNC__docker_binary
## @purpose  Локация docker CLI (None = недоступен → non-fatal exit 0).
## @io       ⇥ facts: EnvironmentFacts | None → ⎋ str | None
## @complexity O(1)
def _default_facts() -> EnvironmentFacts | None:
    """Ленивый импорт env_facts (160 W4b DI) со stdlib-fallback — 162 W1-1 merge-нота.

    Cron запускает watchdog БЕЗ PYTHONPATH → core.internal недоступен → ImportError →
    возвращаем None, и _docker_binary падает на stdlib shutil.which. Модуль-level импорт
    env_facts ЗАПРЕЩЁН (нарушил бы @invariant 1 «Stdlib-only — 0 импортов core.internal»).
    """
    try:
        from core.internal.shared.env_facts import default_env_facts

        return default_env_facts()
    except ImportError:
        return None


def _docker_binary(facts: EnvironmentFacts | None = None) -> str | None:
    """Return docker binary path or None if unavailable."""
    f = facts if facts is not None else _default_facts()
    if f is not None:
        return f.which("docker")
    return shutil.which("docker")


# endregion FUNC__docker_binary


# region FUNC__get_state_file
## @purpose  Путь state-файла: env WATCHDOG_STATE_FILE > default /var/lib/platform/run/watchdog-state.json.
## @io       ⎋ str
## @complexity O(1)
def _get_state_file() -> str:
    """State file path (env override for tests/dev)."""
    return os.environ.get("WATCHDOG_STATE_FILE", DEFAULT_STATE_FILE)


# endregion FUNC__get_state_file


# region FUNC__get_int_env
## @purpose  Парсинг int env с валидным fallback (invalid → default, IMP:7 warning).
## @io       ⇥ name: str, default: int → ⎋ int
## @complexity O(1)
def _get_int_env(name: str, default: int) -> int:
    """Read a non-negative int env var with default fallback (invalid → default)."""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[IMP:7][watchdog][env] %s=%r invalid int — using default %d", name, raw, default)
        return default
    return value if value >= 0 else default


# endregion FUNC__get_int_env


# region FUNC__core_dir
## @purpose  Вывод core_dir из расположения скрипта (3 уровня вверх): {core}/internal/healthcheck/.
## @io       ⎋ str
## @complexity O(1)
def _core_dir() -> str:
    """Derive the platform core dir from this file's location (watchdog.py → core_dir)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# endregion FUNC__core_dir


# region FUNC_scan_containers
## @purpose  docker ps -q → ПАКЕТНЫЙ docker inspect c1 c2 ... (JSON-массив) для всех контейнеров;
##           извлекает Name, State.Health.Status, State.RestartCount, HostConfig.RestartPolicy.Name.
##           T2.7 (perf): O(C) subprocess-вызовов → O(1) subprocess + O(C) парсинг (host-cron 5 мин).
## @io       ⇥ facts: EnvironmentFacts | None, run_cmd: Callable | None (DI-канал, 167 D0)
##           → ⎋ list[dict] — записи {id, name, health, restart_count, restart_policy};
##           docker CLI недоступен → [] (non-fatal); docker-ошибка → ⚡ DockerError (exit 1)
## @complexity O(1) subprocess (batch inspect) + O(C) parse — C контейнеров
## @invariants
##   - docker ps returncode != 0 → IMP:10 + DockerError (внутренняя ошибка, exit 1)
##   - docker inspect (batch) returncode != 0 → IMP:10 + DockerError
##   - docker inspect возвращает JSON-массив в порядке аргументов (c1 c2 ...) — zip по позиции;
##     расхождение длины → WARN, парсится min(len) записей
##   - Контейнер без Health-блока → health=None (отсеивается фильтром)
## @changes 2026-08-13 | DevPlan 160 W4b — +facts: EnvironmentFacts | None (which docker DI)
## @changes 2026-08-14 | DevPlan 167 D0 — +run_cmd: Callable | None (docker-канал DI)
## @changes 2026-08-22 | T2.7 — batch docker inspect (N+1 → O(1)); вердикты идентичны (per-контейнер
##           DockerError при любом rc!=0 сохраняется — docker inspect падает целиком при отсутствии id)
def scan_containers(facts: EnvironmentFacts | None = None, run_cmd: RunCmd | None = None) -> list[ContainerRecord]:
    """Scan all running containers and extract health/restart metadata."""
    run_impl = run_cmd if run_cmd is not None else _run_cmd
    if _docker_binary(facts) is None:
        logger.warning("[IMP:7][watchdog][scan] docker CLI unavailable — skip (non-fatal, exit 0)")
        return []

    ps = run_impl(["docker", "ps", "-q"])
    if ps.returncode != 0:
        logger.error(
            "[IMP:10][watchdog][scan] docker ps failed rc=%d: %s",
            ps.returncode,
            (ps.stderr or ps.stdout).strip(),
        )
        msg = "docker ps failed"
        raise DockerError(msg)

    container_ids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not container_ids:
        logger.info("[IMP:7][watchdog][scan] scanned 0 container(s)")
        return []

    # T2.7: один docker inspect c1 c2 ... вместо C per-контейнер вызовов (N+1).
    # docker inspect возвращает JSON-массив в порядке переданных аргументов.
    inspect = run_impl(["docker", "inspect", *container_ids])
    if inspect.returncode != 0:
        logger.error(
            "[IMP:10][watchdog][scan] docker inspect failed for %d container(s) rc=%d: %s",
            len(container_ids),
            inspect.returncode,
            (inspect.stderr or inspect.stdout).strip(),
        )
        msg = f"docker inspect failed for {len(container_ids)} container(s)"
        raise DockerError(msg)
    try:
        data: list[object] = cast("list[object]", json.loads(inspect.stdout))  # W11: json → Any → list[object]
    except json.JSONDecodeError as exc:
        logger.error("[IMP:10][watchdog][scan] docker inspect invalid JSON: %s", exc)
        msg = "docker inspect invalid JSON"
        raise DockerError(msg) from exc
    if len(data) != len(container_ids):
        logger.warning(
            "[IMP:7][watchdog][scan] inspect returned %d record(s) for %d id(s) — parsing by position",
            len(data),
            len(container_ids),
        )
    # strict=False: при расхождении длин (WARN выше) парсим min(len) записей по позиции
    containers = [_parse_inspect(cid, [item]) for cid, item in zip(container_ids, data, strict=False)]

    logger.info("[IMP:7][watchdog][scan] scanned %d container(s)", len(containers))
    return containers


# endregion FUNC_scan_containers


# region FUNC__parse_inspect
## @purpose  Извлечение записи контейнера из сырого docker inspect JSON-массива.
## @io       ⇥ cid: str, data: list → ⎋ dict {id, name, health, restart_count, restart_policy}
## @complexity O(1)
def _parse_inspect(cid: str, data: list[object]) -> ContainerRecord:
    """Extract container record from docker inspect output (array of one object)."""
    # W11: object-граница JSON — local vars для isinstance-сужения (двойной .get в тернарнике → Unknown)
    info = cast("dict[str, object]", data[0]) if data and isinstance(data[0], dict) else cast("dict[str, object]", {})
    state_raw = info.get("State")
    state = cast("dict[str, object]", state_raw) if isinstance(state_raw, dict) else cast("dict[str, object]", {})
    health_raw = state.get("Health")
    health = (
        cast("str | None", cast("dict[str, object]", health_raw).get("Status"))
        if isinstance(health_raw, dict)
        else None
    )
    host_raw = info.get("HostConfig")
    host_config = cast("dict[str, object]", host_raw) if isinstance(host_raw, dict) else cast("dict[str, object]", {})
    restart_raw = host_config.get("RestartPolicy")
    restart_policy = (
        cast("str", cast("dict[str, object]", restart_raw).get("Name", "")) if isinstance(restart_raw, dict) else ""
    )
    raw_name = str(info.get("Name", cid)).lstrip("/")
    try:
        restart_count = int(cast("int | str", state.get("RestartCount", 0)))
    except (TypeError, ValueError):
        restart_count = 0
    return ContainerRecord(
        id=cid,
        name=raw_name,
        health=health,
        restart_count=restart_count,
        restart_policy=restart_policy,
    )


# endregion FUNC__parse_inspect


# region FUNC__is_eligible
## @purpose  Кандидат на рестарт: health существует И != healthy/none; restart != "no"; RestartCount <= 5.
## @io       ⇥ c: dict → ⎋ bool
## @complexity O(1)
## @invariants
##   - restart_policy "no" исключает one-shot (prometheus-config-init, minio-createbuckets)
##   - RestartCount > 5 = CrashLoopBackOff (RESTART_LOOP_THRESHOLD канон, T2.6) — рестартом не лечится
def _is_eligible(c: ContainerRecord) -> bool:
    """Return True if the container is a restart candidate (unhealthy + restartable + not crash-looping)."""
    health = c.get("health")
    if health is None or health in {"healthy", "none"}:
        return False
    if c.get("restart_policy") == "no":
        return False
    return int(c.get("restart_count", 0)) <= RESTART_LOOP_THRESHOLD


# endregion FUNC__is_eligible


# region FUNC__is_crash_looping
## @purpose  Crash-loop детекция skip-path (REF-0014): нездоровый контейнер с рестарт-политикой
##           и RestartCount > RESTART_LOOP_THRESHOLD — docker restart не лечит (T2.6 канон),
##            только TG-нотификация оператору («crash-loop detected, не рестарчу»).
## @io       ⇥ c: ContainerRecord → ⎋ bool
## @complexity O(1)
## @invariants — зеркальна _is_eligible по порогу: eligible = ...<= T, crash-loop = ...> T
def _is_crash_looping(c: ContainerRecord) -> bool:
    """Return True for an unhealthy restartable container over RESTART_LOOP_THRESHOLD (T2.6)."""
    health = c.get("health")
    if health is None or health in {"healthy", "none"}:
        return False
    if c.get("restart_policy") == "no":
        return False
    return int(c.get("restart_count", 0)) > RESTART_LOOP_THRESHOLD


# endregion FUNC__is_crash_looping


# region FUNC_load_state
## @purpose  Чтение state-файла; отсутствующий/битый файл → пустой state (IMP:7 warning).
## @io       ⇥ path: str → ⎋ dict {"unhealthy_since": {}, "last_restart": {}}
## @complexity O(1)
def load_state(path: str) -> WatchdogState:
    """Load watchdog state JSON (missing/corrupt → empty state)."""
    try:
        with pathlib.Path(path).open(encoding="utf-8") as f:
            data: object = cast("object", json.load(f))  # W11: json → Any → object
    except FileNotFoundError:
        return {"unhealthy_since": {}, "last_restart": {}, "crashloop_notified": {}}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[IMP:7][watchdog][state] Cannot read %s (%s) — starting empty", path, exc)
        return {"unhealthy_since": {}, "last_restart": {}, "crashloop_notified": {}}
    data_dict = cast("dict[str, object]", data) if isinstance(data, dict) else cast("dict[str, object]", {})
    unhealthy_raw = data_dict.get("unhealthy_since")
    last_restart_raw = data_dict.get("last_restart")
    crashloop_raw = data_dict.get("crashloop_notified")  # старые state-файлы без ключа → {}
    unhealthy_since = cast("dict[str, float]", unhealthy_raw) if isinstance(unhealthy_raw, dict) else {}
    last_restart = cast("dict[str, float]", last_restart_raw) if isinstance(last_restart_raw, dict) else {}
    crashloop_notified = cast("dict[str, float]", crashloop_raw) if isinstance(crashloop_raw, dict) else {}
    return {
        "unhealthy_since": unhealthy_since,
        "last_restart": last_restart,
        "crashloop_notified": crashloop_notified,
    }


# endregion FUNC_load_state


# region FUNC_save_state
## @purpose  Атомарная запись state: tempfile в той же директории + os.replace (tmpfs-friendly).
## @io       ⇥ path: str, state: dict → ⎋ None ⚡ OSError (→ exit 1)
## @complexity O(1)
## @invariants
##   - temp создаётся В ТОЙ ЖЕ директории (same-filesystem rename на tmpfs)
##   - os.replace атомарен — читатель видит старый или новый файл, не частичный
##   - Ошибка записи → OSError пробрасывается (внутренняя ошибка → exit 1)
# region FUNC__plw_body_save_state
## @purpose  Тело try-блока (PLW0717 extraction из save_state) — семантика except не меняется.
## @io       ⇥ fd, path, payload, tmp_path → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body_save_state(fd: int, path: str, payload: str, tmp_path: str) -> None:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    pathlib.Path(tmp_path).replace(path)
    logger.info("[IMP:9][watchdog][state] State saved: %s", path)


# endregion FUNC__plw_body_save_state


def save_state(path: str, state: WatchdogState) -> None:
    """Atomically write watchdog state (tempfile + os.replace)."""
    payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    target_dir = pathlib.Path(path).parent or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".watchdog-state.", suffix=".tmp")
    try:
        _plw_body_save_state(fd, path, payload, tmp_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# endregion FUNC_save_state


# region FUNC_decide_actions
## @purpose  Ядро решения: для каждого eligible-контейнера вести unhealthy_since; рестарт, когда
##           unhealthy >= unhealthy_min_sec И cooldown с last_restart истёк. Чистка мусорных записей.
## @io       ⇥ containers: list[dict], state: dict, now: float, unhealthy_min_sec: float,
##              cooldown_sec: float → ⎋ tuple[list[dict], dict] — (actions, new_state)
## @complexity O(C) — C контейнеров
## @invariants
##   - Первый unhealthy-run только фиксирует unhealthy_since (wait, без рестарта)
##   - Рестарт: now - since >= unhealthy_min_sec AND (нет last_restart OR now - last_restart >= cooldown_sec)
##   - РЕШЕНИЕ БЕЗ ШТАМПОВ (REF-0014 stamp-after-success): last_restart/unhealthy_since reset
##     наносит run_watchdog ТОЛЬКО после успешного docker restart (+ re-save) — failed restart
##     не вооружает cooldown и не сбрасывает окно наблюдения (retry на следующем проходе)
##   - Garbage: unhealthy_since для healthy/исчезнувших — удаляется; last_restart для
##     исчезнувших контейнеров — удаляется; crashloop_notified для не-crash-loop'ящихся — удаляется
def decide_actions(
    containers: list[ContainerRecord],
    state: WatchdogState,
    now: float,
    unhealthy_min_sec: float,
    cooldown_sec: float,
) -> tuple[list[Action], WatchdogState]:
    """Decide restart actions based on state and current health. Returns (actions, new_state)."""
    eligible = {c["name"]: c for c in containers if _is_eligible(c)}
    current_names = {c["name"] for c in containers}
    crashloop_now = {c["name"] for c in containers if _is_crash_looping(c)}

    new_state: WatchdogState = {
        "unhealthy_since": dict(state.get("unhealthy_since", {})),
        "last_restart": dict(state.get("last_restart", {})),
        "crashloop_notified": dict(state.get("crashloop_notified", {})),
    }
    actions: list[Action] = []

    for name, c in sorted(eligible.items()):
        since = new_state["unhealthy_since"].get(name)
        if since is None:
            new_state["unhealthy_since"][name] = now
            logger.info(
                "[IMP:7][watchdog][decide] %s unhealthy (health=%s) — recording since=%.0f (wait)",
                name,
                c.get("health"),
                now,
            )
            continue
        last_restart = new_state["last_restart"].get(name)
        if now - since >= unhealthy_min_sec and (last_restart is None or now - last_restart >= cooldown_sec):
            logger.info(
                "[IMP:9][watchdog][decide] %s unhealthy since %.0f — restart decision (cooldown ok)",
                name,
                since,
            )
            # REF-0014: решение фиксируется action'ом; штампы — ТОЛЬКО после успешного restart
            # в run_watchdog (state-commit транзакционен с действием).
            actions.append({"name": name, "id": c["id"], "since": since, "now": now})
        else:
            logger.info(
                "[IMP:7][watchdog][decide] %s unhealthy since %.0f — wait (age=%.0fs/%s cooldown=%.0fs/%s)",
                name,
                since,
                now - since,
                unhealthy_min_sec,
                (now - last_restart) if last_restart else 0,
                cooldown_sec,
            )

    # ── Garbage cleanup ──
    for name in list(new_state["unhealthy_since"]):
        if name not in eligible:
            del new_state["unhealthy_since"][name]
    for name in list(new_state["last_restart"]):
        if name not in current_names:
            del new_state["last_restart"][name]
    for name in list(new_state["crashloop_notified"]):
        if name not in crashloop_now:
            del new_state["crashloop_notified"][name]

    return actions, new_state


# endregion FUNC_decide_actions


# region FUNC_restart_container
## @purpose  docker restart контейнера; dry-run печатает план без вызова.
## @io       ⇥ name: str, cid: str, dry_run: bool, run_cmd: Callable | None (DI, 167 D0)
##           → ⎋ bool (True = ok/dry-run, False = docker fail)
## @complexity O(1) — один subprocess
## @invariants
##   - Успех → IMP:9 «RESTART {name} (unhealthy since {ts})»
##   - docker restart rc != 0 → IMP:10 + False (вызывающий → exit 1)
def restart_container(name: str, cid: str, since: float, dry_run: bool = False, run_cmd: RunCmd | None = None) -> bool:
    """Restart a container (or print plan in dry-run mode). Returns True on success."""
    if dry_run:
        logger.info("[IMP:8][watchdog][dry-run] WOULD restart %s (unhealthy since %.0f)", name, since)
        return True
    run_impl = run_cmd if run_cmd is not None else _run_cmd
    result = run_impl(["docker", "restart", cid])
    if result.returncode != 0:
        logger.error(
            "[IMP:10][watchdog] docker restart %s failed rc=%d: %s",
            name,
            result.returncode,
            (result.stderr or result.stdout).strip(),
        )
        return False
    logger.info("[IMP:9][watchdog] RESTART %s (unhealthy since %.0f)", name, since)
    return True


# endregion FUNC_restart_container


# region FUNC_notify_telegram
## @purpose  Telegram-уведомление о рестарте через subprocess python3 -m core.internal.shared.notifications
##           notify --severity critical --event watchdog.restart --corr-id ... (DevPlan 003 B3:
##           единый контракт — event id + corr_id; best-effort, non-blocking).
## @io       ⇥ name: str, since: float, dry_run: bool → ⎋ bool
## @complexity O(1) — один subprocess
## @invariants
##   - PYTHONPATH={core_dir} передаётся в env дочернего процесса (cron сам без PYTHONPATH)
##   - Failure (rc != 0 / exception) → IMP:7 warning, False — НЕ блокирует (уведомление best-effort)
##   - dry-run: печатает план, без вызова
def notify_telegram(name: str, since: float, dry_run: bool = False, run_cmd: RunCmd | None = None) -> bool:
    """Send a non-blocking Telegram notification about the restart (best-effort)."""
    if dry_run:
        logger.info("[IMP:8][watchdog][dry-run] WOULD notify Telegram: restarted %s", name)
        return True
    env = os.environ.copy()
    core_dir = _core_dir()
    env["PYTHONPATH"] = core_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [
        "python3",
        "-m",
        "core.internal.shared.notifications",
        "notify",
        "--severity",
        "critical",
        "--context",
        "watchdog",
        "--event",
        "watchdog.restart",
        "--corr-id",
        f"watchdog-{name}-{int(since)}",
        "🔄",
        f"watchdog restarted {name} (unhealthy since {int(since)})",
    ]
    run_impl = run_cmd if run_cmd is not None else _run_cmd
    try:
        result = run_impl(cmd, timeout=DOCKER_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[IMP:7][watchdog][notify] telegram notify failed (non-fatal): %s", exc)
        return False
    if result.returncode != 0:
        logger.warning(
            "[IMP:7][watchdog][notify] telegram notify rc=%d (non-fatal): %s",
            result.returncode,
            (result.stderr or result.stdout).strip()[:200],
        )
        return False
    return True


# endregion FUNC_notify_telegram


# region FUNC_notify_crashloop
## @purpose  TG «crash-loop detected, не рестарчу» в skip-path (REF-0014): контейнер с
##           RestartCount > RESTART_LOOP_THRESHOLD пропускается молча → оператор без сигнала.
##           Единый notifier-контракт: python3 -m core.internal.shared.notifications notify
##           --event watchdog.crashloop (зарегистрирован в core/notification-catalog.yaml —
##           parity-гейт B4); best-effort, non-blocking; suppress-окно держит run_watchdog
##           через state.crashloop_notified (CLI-throttle процесс-локален — cron = новый процесс).
## @io       ⇥ name: str, dry_run: bool (keyword-only — FBT-чистый контракт), run_cmd: Callable | None
##              (DI, 167 D0) → ⎋ bool
## @complexity O(1) — один subprocess
## @invariants
##   - PYTHONPATH={core_dir} передаётся в env дочернего процесса (cron сам без PYTHONPATH)
##   - Failure (rc != 0 / exception) → IMP:7 warning, False — НЕ блокирует проход
##   - dry-run: печатает план, без вызова
def notify_crashloop(name: str, *, dry_run: bool = False, run_cmd: RunCmd | None = None) -> bool:
    """Send a non-blocking Telegram notification about a crash-looped container (skip-path)."""
    if dry_run:
        logger.info("[IMP:8][watchdog][dry-run] WOULD notify Telegram: crash-loop %s (не рестарчу)", name)
        return True
    env = os.environ.copy()
    core_dir = _core_dir()
    env["PYTHONPATH"] = core_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [
        "python3",
        "-m",
        "core.internal.shared.notifications",
        "notify",
        "--severity",
        "critical",
        "--context",
        "watchdog",
        "--event",
        "watchdog.crashloop",
        "--corr-id",
        f"watchdog-crashloop-{name}",
        "⛔",
        f"crash-loop detected, не рестарчу: {name} (RestartCount > {RESTART_LOOP_THRESHOLD})",
    ]
    run_impl = run_cmd if run_cmd is not None else _run_cmd
    try:
        result = run_impl(cmd, timeout=DOCKER_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[IMP:7][watchdog][notify] crash-loop notify failed (non-fatal): %s", exc)
        return False
    if result.returncode != 0:
        logger.warning(
            "[IMP:7][watchdog][notify] crash-loop notify rc=%d (non-fatal): %s",
            result.returncode,
            (result.stderr or result.stdout).strip()[:200],
        )
        return False
    logger.info("[IMP:9][watchdog][crashloop] CRASHLOOP NOTIFY %s (не рестарчу)", name)
    return True


# endregion FUNC_notify_crashloop


# region FUNC__notify_crashloops_with_suppress
## @purpose  Crash-loop TG-нотификации с per-container suppress через state (REF-0014):
##           окно CRASHLOOP_NOTIFY_COOLDOWN_MIN; штамп ТОЛЬКО после успешной отправки
##           (провал доставки → ретрай следующим проходом) + немедленный re-save.
## @io       ⇥ crashloop_names: list[str], new_state: WatchdogState, ts: float, path: str,
##              run_cmd: RunCmd | None → ⎋ int (0 = ok, 1 = state write failure)
## @complexity O(K) — K crash-looped контейнеров
def _notify_crashloops_with_suppress(
    crashloop_names: list[str],
    new_state: WatchdogState,
    ts: float,
    path: str,
    run_cmd: RunCmd | None = None,
) -> int:
    """Send suppressed crash-loop notifications; stamp + re-save on success. Returns exit code."""
    for cl_name in crashloop_names:
        last_sent = new_state["crashloop_notified"].get(cl_name)
        if last_sent is not None and ts - last_sent < CRASHLOOP_NOTIFY_COOLDOWN_MIN * 60.0:
            logger.info(
                "[IMP:7][watchdog][crashloop] %s notified %.0fs ago (< %d min) — suppressed",
                cl_name,
                ts - last_sent,
                CRASHLOOP_NOTIFY_COOLDOWN_MIN,
            )
            continue
        if notify_crashloop(cl_name, dry_run=False, run_cmd=run_cmd):
            new_state["crashloop_notified"][cl_name] = ts
            try:
                save_state(path, new_state)
            except OSError as exc:
                logger.error("[IMP:10][watchdog][state] State re-save failed: %s", exc)
                return 1
    return 0


# endregion FUNC__notify_crashloops_with_suppress


# region FUNC__execute_restarts_stamp_after_success
## @purpose  Исполнение restart-действий с транзакционным state-commit (REF-0014 stamp-after-success).
## @io       ⇥ actions: list[Action], new_state: WatchdogState, path: str, run_cmd: RunCmd | None
##           → ⎋ int — число failed restart'ов (0 = все ok); exit-решение за вызывающим
## @complexity O(R) — R рестартов
## ⚠️ TRAP[BUG] · 2026-08-24 · P1 · REF-0014: last_restart штампился ДО restart (stamp-before-success)
# · Symptom: state сохранялся с уже проставленными last_restart всех действий; первый failed
# ·   restart возвращал exit 1 — cooldown остальных действий «сгорел» впустую (латентность
# ·   самолечения 10→40+ мин), host-cron timeout 50s убивал проход посреди цикла.
# · Root: state-commit не транзакционен с действием — decide_actions писал штампы в new_state,
# ·   run_watchdog сохранял state единым блоком ДО выполнения restart'ов.
# · Fix: решение без штампов (decide_actions); здесь — restart ok → штамп last_restart +
# ·   unhealthy_since reset + НЕМЕДЛЕННЫЙ re-save per-action; fail → NO stamp + skip-notify,
# ·   остальные действия продолжаются (exit 1 в конце прохода).
# · Prevention: test_watchdog.py::test_stamp_written_only_after_successful_restart /
# ·   ::test_failed_restart_no_stamp_no_notify (sequence-тесты ordering).
def _execute_restarts_stamp_after_success(
    actions: list[Action],
    new_state: WatchdogState,
    path: str,
    run_cmd: RunCmd | None = None,
) -> int:
    """Execute restart actions; stamp state only after each successful restart. Returns failure count."""
    restart_failures = 0
    for action in actions:
        ok = restart_container(action["name"], action["id"], action["since"], dry_run=False, run_cmd=run_cmd)
        if not ok:
            logger.error(
                "[IMP:9][watchdog] Restart FAILED %s — NO cooldown stamp, NO notify (retry next pass)",
                action["name"],
            )
            restart_failures += 1
            continue
        new_state["last_restart"][action["name"]] = action["now"]
        new_state["unhealthy_since"][action["name"]] = action["now"]
        try:
            save_state(path, new_state)
        except OSError as exc:
            logger.error("[IMP:10][watchdog][state] State re-save failed: %s", exc)
            # Штамп уже в памяти new_state, но не персистирован — сигнал внутренней ошибкой
            return restart_failures + 1
        notify_telegram(action["name"], action["since"], dry_run=False, run_cmd=run_cmd)
    return restart_failures


# endregion FUNC__execute_restarts_stamp_after_success


# region FUNC_run_watchdog
## @purpose  Оркестрация одного прогона: scan → decide → (dry-run: план | save state + crash-loop
##           notify + restart + stamp-after-success re-save + notify).
## @io       ⇥ dry_run: bool, state_file: str | None, now: float | None,
##              facts: EnvironmentFacts | None (W4b DI: which docker),
##              run_cmd: Callable | None (167 D0 DI: docker/notify-канал) → ⎋ int (0 = ok, 1 = internal error)
## @complexity O(C + R) — C контейнеров, R рестартов
## @invariants
##   - docker CLI недоступен → scan возвращает [] → exit 0 (non-fatal)
##   - DockerError при скане → IMP:10 уже залогирован → exit 1
##   - dry-run: 0 мутаций (state не сохраняется, рестарты/notify не вызываются)
##   - REF-0014 stamp-after-success: last_restart/unhealthy_since штампуются ТОЛЬКО после
##     успешного docker restart + немедленный re-save (state-commit транзакционен с действием);
##     failed restart → NO stamp + skip-notify, ОСТАЛЬНЫЕ действия продолжаются, exit 1 в конце
##   - Crash-loop skip-path: TG watchdog.crashloop с suppress-окном CRASHLOOP_NOTIFY_COOLDOWN_MIN
##     (штамп только после успешной отправки — провал доставки ретраится следующим проходом)
## @changes 2026-08-13 | DevPlan 160 W4b — +facts: EnvironmentFacts | None (DI)
## @changes 2026-08-14 | DevPlan 167 D0 — +run_cmd: Callable | None (DI-канал, thread в 3 функции)
## @changes 2026-08-24 | REF-0014 — stamp-after-success + re-save per-action; continue-on-failure;
##           crash-loop TG-нотификация в skip-path (watchdog.crashloop)
def run_watchdog(
    dry_run: bool = False,
    state_file: str | None = None,
    now: float | None = None,
    *,
    facts: EnvironmentFacts | None = None,
    run_cmd: RunCmd | None = None,
) -> int:
    """Run one watchdog pass. Returns process exit code (0 ok / 1 internal error)."""
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · run_cmd-параметр: docker/notify-канал через DI вместо
    # ·   monkeypatch watchdog._run_cmd (DevPlan 167 D0)
    # · Rejected: прямой вызов _run_cmd (тест не мог бы наблюдать реальный вызов без subprocess)
    # · Reason: run_cmd=None → модульный _run_cmd (поведение без изменений); тесты передают
    # ·   fake (FakeDocker) — docker ps/inspect/restart/telegram перехватываются одним каналом
    # · Rev: если watchdog переедет на CommandRunner-протокол (shared/subprocess_io) — заменить
    ts = now if now is not None else time.time()
    path = state_file or _get_state_file()
    unhealthy_min = _get_int_env("WATCHDOG_UNHEALTHY_MIN", DEFAULT_UNHEALTHY_MIN)
    cooldown_min = _get_int_env("WATCHDOG_COOLDOWN_MIN", DEFAULT_COOLDOWN_MIN)

    try:
        containers = scan_containers(facts, run_cmd=run_cmd)
    except DockerError:
        return 1

    if not containers:
        logger.info("[IMP:7][watchdog] 0 containers scanned — no action (exit 0)")
        return 0

    state = load_state(path)
    actions, new_state = decide_actions(
        containers,
        state,
        ts,
        unhealthy_min * 60.0,
        cooldown_min * 60.0,
    )

    # ── REF-0014: crash-loop skip-path (>RESTART_LOOP_THRESHOLD) — TG «не рестарчу» ──
    crashloop_names = sorted({c["name"] for c in containers if _is_crash_looping(c)})

    if dry_run:
        for action in actions:
            logger.info(
                "[IMP:8][watchdog][dry-run] ACTION: restart %s (unhealthy since %.0f)",
                action["name"],
                action["since"],
            )
        for cl_name in crashloop_names:
            logger.info("[IMP:8][watchdog][dry-run] WOULD notify Telegram: crash-loop %s (не рестарчу)", cl_name)
        logger.info("[IMP:7][watchdog][dry-run] %d action(s) planned — no mutation", len(actions))
        return 0

    # ── Persist observational state (unhealthy_since records/wait + garbage cleanup) ──
    try:
        save_state(path, new_state)
    except OSError as exc:
        logger.error("[IMP:10][watchdog][state] State write failed: %s", exc)
        return 1

    # ── Crash-loop TG-notify (suppress per-container через state; штамп после успешной отправки) ──
    if _notify_crashloops_with_suppress(crashloop_names, new_state, ts, path, run_cmd) != 0:
        return 1

    # ── Execute restarts + notifications (REF-0014: штамп ТОЛЬКО после успеха) ──
    restart_failures = _execute_restarts_stamp_after_success(actions, new_state, path, run_cmd)

    logger.info(
        "[IMP:9][watchdog] Pass complete: %d/%d restart(s) ok, state=%s",
        len(actions) - restart_failures,
        len(actions),
        path,
    )
    return 1 if restart_failures else 0


# endregion FUNC_run_watchdog


# region FUNC_main
## @purpose  CLI: python3 watchdog.py [--dry-run] [--state-file PATH]. Exit 0/1 по контракту.
## @io       ⇥ argv + env (WATCHDOG_STATE_FILE/WATCHDOG_UNHEALTHY_MIN/WATCHDOG_COOLDOWN_MIN),
##              facts: EnvironmentFacts | None (W4b DI) → ⎋ int
## @complexity O(C) — один прогон
def main(facts: EnvironmentFacts | None = None) -> int:
    """CLI entrypoint: run one watchdog pass (cron: */5 * * * *)."""
    parser = argparse.ArgumentParser(
        description="Watchdog: auto-restart unhealthy docker containers (DevPlan 132 W1)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without restart/notify")
    parser.add_argument("--state-file", default=None, help=f"State file path (default: {DEFAULT_STATE_FILE})")
    args = parser.parse_args(namespace=CliArgs())  # W11: типизированный namespace (без class-value дефолтов)
    return run_watchdog(dry_run=args.dry_run, state_file=args.state_file, facts=facts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
# endregion FUNC_main
