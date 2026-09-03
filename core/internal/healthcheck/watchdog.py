#!/usr/bin/env python3
# GREP_SUMMARY: watchdog, unhealthy-restart, docker-restart, cooldown, dry-run, stdlib-only, host-cron, is-n-loop, state-json, crashloop-notify, stamp-after-success, wipe-guard, state-loss, F8, boot-id, named-volumes
# STRUCTURE: ▶ scan_containers ┌docker ps -q + inspect┐ → ◇ filters (health?+restart!=no+RestartCount<=5) → ○ unhealthy_since state → ◇ unhealthy>=10min ∧ cooldown 30min? → ⚡ docker restart → ⊕ stamp last_restart ПОСЛЕ успеха + re-save per-action → ⚡ TG notify │ ◇ RestartCount>5? → ⚡ TG «crash-loop detected, не рестарчу» │ ◇ F8 wipe-guard (boot_id+volumes+marker vs baseline → TG watchdog.wipe) → ⎋ exit 0|1
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
## @changes 2026-09-03 | F8 prevention (DevPlan 031 T3) — +wipe-guard (check_wipe_signature/
##           notify_wipe/_wipe_*): state-loss детектор (boot_id + named volumes + /opt/platform/core
##           маркер vs persistent baseline → TG watchdog.wipe) — tronyx-vps терял ВСЁ docker-
##           состояние за ночь (provider-level reset 2026-09-03, journald = 1 boot)
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
import datetime
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
# plan 012 T13 (F-026): скользящее окно рестартов. RestartCount — LIFETIME счётчик: легитимные
# пересоздания (деплой: docker compose up → новый контейнер; rollback; converge) накапливают
# RestartCount без crash-loop. «restart-loop» поднимается ТОЛЬКО при рестартах В окне:
# контейнер, стартовавший ≤ RESTART_WINDOW_SEC назад с RestartCount > порога — активный loop;
# Up 46 мин healthy после 14 деплой-рестартов (E-сценарий F-026) — НЕ loop.
RESTART_WINDOW_SEC = 15 * 60  # WATCHDOG_RESTART_WINDOW_MIN (env, мин) — скользящее окно
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
    started_at: float | None  # plan 012 T13 (F-026): unix-ts последнего старта (State.StartedAt)


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
    started_at = _parse_started_at(state.get("StartedAt"))
    return ContainerRecord(
        id=cid,
        name=raw_name,
        health=health,
        restart_count=restart_count,
        restart_policy=restart_policy,
        started_at=started_at,
    )


# endregion FUNC__parse_inspect


# region FUNC__parse_started_at
## @purpose  Парсинг State.StartedAt (ISO-8601 от docker) → unix-ts. Некорректная/пустая
##           строка → None (окно-детекция T13: неизвестный старт = консервативно НЕ loop).
## @io       ⇥ raw: object | None → ⎋ float | None
## @complexity O(1)
def _parse_started_at(raw: object | None) -> float | None:
    """Parse docker State.StartedAt (ISO-8601, e.g. 2026-08-26T09:00:00.123456789Z) → unix ts."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        normalized = raw.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


# endregion FUNC__parse_started_at


# region FUNC__is_eligible
## @purpose  Кандидат на рестарт: health существует И != healthy/none; restart != "no";
##           НЕ crash-looping в скользящем окне (plan 012 T13 / F-026).
## @io       ⇥ c: dict, now: float | None (ts окна; None = time.time()) → ⎋ bool
## @complexity O(1)
## @invariants
##   - restart_policy "no" исключает one-shot (prometheus-config-init, minio-createbuckets)
##   - Логика crash-loop инвертирована: eligible = unhealthy + restartable + NOT window-loop
##     (lifetime RestartCount от легитимных пересозданий НЕ блокирует рестарт — F-026)
def _is_eligible(c: ContainerRecord, now: float | None = None) -> bool:
    """Return True if the container is a restart candidate (unhealthy + restartable + not window-crash-looping)."""
    health = c.get("health")
    if health is None or health in {"healthy", "none"}:
        return False
    if c.get("restart_policy") == "no":
        return False
    return not _is_crash_looping(c, now=now)


# endregion FUNC__is_eligible


# region FUNC__is_crash_looping
## @purpose  Crash-loop детекция skip-path (REF-0014 + plan 012 T13 / F-026): нездоровый
##           контейнер с рестарт-политикой и RestartCount > порога В СКОЛЬЗЯЩЕМ ОКНЕ —
##           docker restart не лечит (T2.6 канон), только TG-нотификация оператору
##           («crash-loop detected, не рестарчу»). Lifetime RestartCount при УЖЕ ДОЛГОМ
##           аптайме (старт > окна назад) = легитимные пересоздания, НЕ loop (E-сценарий
##           F-026: Up 46 мин healthy после 14 деплой-рестартов → PASS).
## @io       ⇥ c: ContainerRecord, now: float | None (ts окна; None = time.time()) → ⎋ bool
## @complexity O(1)
## @invariants
##   - restarting=true ИЛИ RestartCount > T И старт В окне (≤ RESTART_WINDOW_SEC назад) → loop
##   - started_at неизвестен (None) → консервативно НЕ loop (легитимные пересоздания не
##     должны ложно триггерить при нечитаемом StartedAt)
def _is_crash_looping(c: ContainerRecord, now: float | None = None) -> bool:
    """Return True for an unhealthy restartable container over threshold within the sliding window."""
    health = c.get("health")
    if health is None or health in {"healthy", "none"}:
        return False
    if c.get("restart_policy") == "no":
        return False
    if int(c.get("restart_count", 0)) <= RESTART_LOOP_THRESHOLD:
        return False
    started_at = c.get("started_at")
    if started_at is None:
        return False
    window_ts = time.time() if now is None else now
    return window_ts - started_at <= RESTART_WINDOW_SEC


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
    eligible = {c["name"]: c for c in containers if _is_eligible(c, now=now)}
    current_names = {c["name"] for c in containers}
    crashloop_now = {c["name"] for c in containers if _is_crash_looping(c, now=now)}

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
def restart_container(
    name: str, cid: str, since: float, *, dry_run: bool = False, run_cmd: RunCmd | None = None
) -> bool:
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
def notify_telegram(name: str, since: float, *, dry_run: bool = False, run_cmd: RunCmd | None = None) -> bool:
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


# ═══════════════════════════════════════════════════════════════════════
# Wipe-guard (F8 prevention, DevPlan 031 T3) — state-loss detector
# ═══════════════════════════════════════════════════════════════════════
# 🔴 TRAP[INCIDENT] · 2026-09-03 · P1 · tronyx-vps: повторный docker-state wipe за ночь
# · Symptom: нода потеряла named volumes (12) + 4 проектных payload + /opt/<ctx>/platform +
# ·   /root/.ssh/known_hosts; НЕ затронуто... journald сброшен — root-fs reset, не docker prune.
# · Root: форензика 031 T3 (read-only): journalctl --list-boots = ОДИН boot (15:18 MSK), каталог
# ·   /var/log/journal создан на этом boot; volumes пересозданы оператором 12:42-12:47 UTC;
# ·   /etc/cron.d/platform-prune (docker system prune -af --filter until=720h, день 1 04:00,
# ·   БЕЗ --volumes) НЕ объясняет потерю known_hosts/journald//opt. Класс: provider-level node
# ·   reset (reinstall/reboot/migration/snapshot) ИЛИ операторский re-provision. Точная причина —
# ·   в provider console (биллинг/события), owner-only — ЭСКАЛАЦИЯ владельцу (DevPlan 031 T3).
# · Fix: канон восстановления (cold bootstrap + deploy-project ×N) применён; prevention —
# ·   on-node wipe-guard ниже (TG watchdog.wipe на схлопывание volumes/смену boot) + TRAP[DEBT]
# ·   на offsite heartbeat (on-node монитор слеп к собственному root-fs сбросу).
# · Prevention: повторный wipe → НЕ молча пере-бутстрапить — сначала provider console +
# ·   watchdog.wipe-алерт; полный класс — offsite heartbeat (Rev в TRAP[DEBT] ниже).
# 📝 TRAP[DEBT] · 2026-09-03 · MED · Полный root-fs reset ноды недетектируем on-node
# · Observed: journald = 1 boot после wipe tronyx-vps (2026-09-03) — watchdog/cron в
# ·   /opt/platform/core унесены сбросом; on-node wipe-guard после re-bootstrap видит только
# ·   свежий baseline (первый прогон), алерта нет.
# · Suspected: единственный надёжный сигнал полного сброса — offsite (S3-marker heartbeat или
# ·   provider API/console); on-node мониторинг физически слеп к собственному исчезновению.
# · Impact: класс «нода пересоздана провайдером» остаётся на ручном контроле оператора
# · When: DevPlan 031 T3 (F8 форензика) · Rev: первый повторный wipe после внедрения guard —
# ·   завести offsite heartbeat (S3 upload раз в N минут из backup-cron/метрик) + алерт на отсутствие


# region DATA_WipeState
class WipeState(TypedDict):
    """Персистентный baseline wipe-guard (state-файл, граница JSON).

    ## @purpose  {boot_id, volumes} — здоровый baseline ноды между прогонами watchdog;
    ##            alerted_at — suppress-штамп последнего TG watchdog.wipe.
    """

    boot_id: str
    volumes: int
    alerted_at: float


# endregion DATA_WipeState

WIPE_GUARD_STATE_DEFAULT: str = "/var/lib/platform/run/wipe-guard-state.json"
# Порог здорового baseline: нода с ≥WIPE_MIN_BASELINE_VOLUMES named volumes считается
# «имеющей docker-состояние» — его схлопывание к 0 = wipe-сигнатура.
WIPE_MIN_BASELINE_VOLUMES: int = 3
# Повторный алерт watchdog.wipe не чаще раза в 6 часов (после алерта baseline = текущее
# пустое состояние; следующий wipe после восстановления даст новый baseline → новый алерт).
WIPE_ALERT_COOLDOWN_SEC: float = 6 * 3600.0


# region FUNC_wipe_helpers
## @purpose  Чтение boot_id (Linux /proc/sys/kernel/random/boot_id — инвариант на время boot;
##           перезагрузка = смена id) и подсчёт named volumes (docker volume ls -q).
##           None на ошибку — guard graceful-skip (не ложный алерт).
def _wipe_read_boot_id() -> str | None:
    """Return current boot id from /proc, or None on non-Linux/unreadable (skip guard)."""
    try:
        raw = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw or None


def _wipe_count_volumes(run_cmd: RunCmd | None) -> int | None:
    """Count named docker volumes (docker volume ls -q); None on daemon/CLI failure (skip)."""
    run_impl = run_cmd if run_cmd is not None else _run_cmd
    try:
        result = run_impl(["docker", "volume", "ls", "-q"], timeout=DOCKER_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _wipe_state_path() -> str:
    """Wipe-guard state file: env WIPE_GUARD_STATE_FILE > default /var/lib/platform/run/..."""
    return os.environ.get("WIPE_GUARD_STATE_FILE", WIPE_GUARD_STATE_DEFAULT)


def _wipe_load_state(path: str) -> WipeState | None:
    """Load wipe-guard baseline; None on missing/corrupt state (first run → baseline)."""
    try:
        data: object = cast("object", json.loads(pathlib.Path(path).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None
    data_dict = cast("dict[str, object]", data) if isinstance(data, dict) else None
    if data_dict is None:
        return None
    boot_id = data_dict.get("boot_id")
    volumes = data_dict.get("volumes")
    alerted_raw = data_dict.get("alerted_at", 0.0)
    # W11-строгая типизация: json.loads → Any; isinstance-сужение до конкретных скаляров
    if not isinstance(boot_id, str) or not isinstance(volumes, int):
        return None
    alerted_at: float = float(alerted_raw) if isinstance(alerted_raw, (int, float)) else 0.0
    return WipeState(boot_id=boot_id, volumes=volumes, alerted_at=alerted_at)


def _wipe_save_state(path: str, state: WipeState) -> None:
    """Atomically persist wipe-guard baseline (tmp + os.replace канон atomic_writer-паттерна)."""
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with pathlib.Path(tmp).open("w", encoding="utf-8") as fh:
        json.dump(dict(state), fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    pathlib.Path(tmp).replace(path)


# endregion FUNC_wipe_helpers


# region FUNC_notify_wipe
## @purpose  TG-алерт watchdog.wipe (F8 prevention, DevPlan 031 T3): нода потеряла docker-
##           состояние (named volumes схлопнулись к 0 при здоровом baseline) — сигнал оператору
##           проверить provider-консоль (reinstall/reboot/migration), а не молча пере-бутстрапить.
##           Единый notifier-контракт (как notify_crashloop); event watchdog.wipe — каталог.
## @io       ⇥ baseline: WipeState, current_volumes: int, dry_run: bool, run_cmd → ⎋ bool
## @complexity O(1) — один subprocess
## @invariants
##   - PYTHONPATH={core_dir} в env дочернего процесса (cron без PYTHONPATH)
##   - Failure → IMP:7 warning, False — best-effort, НЕ блокирует проход watchdog
def notify_wipe(
    baseline: WipeState,
    current_volumes: int,
    *,
    dry_run: bool = False,
    run_cmd: RunCmd | None = None,
) -> bool:
    """Send a non-blocking Telegram WIPE alert (best-effort)."""
    if dry_run:
        logger.info(
            "[IMP:9][watchdog][wipe][dry-run] WOULD notify Telegram: docker-state WIPE "
            "(baseline volumes=%d boot=%s → current volumes=%d)",
            baseline["volumes"],
            baseline["boot_id"][:12],
            current_volumes,
        )
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
        "watchdog.wipe",
        "--corr-id",
        f"watchdog-wipe-{baseline['boot_id'][:12]}",
        "🧹",
        (
            f"DOCKER-STATE WIPE: нода потеряла docker-состояние (baseline volumes="
            f"{baseline['volumes']} boot={baseline['boot_id'][:12]} → now volumes="
            f"{current_volumes}). Проверь provider console: reinstall/reboot/migration — "
            f"не пере-бутстрапи молча (DevPlan 031 F8)"
        ),
    ]
    run_impl = run_cmd if run_cmd is not None else _run_cmd
    try:
        result = run_impl(cmd, timeout=DOCKER_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[IMP:7][watchdog][wipe] wipe notify failed (non-fatal): %s", exc)
        return False
    if result.returncode != 0:
        logger.warning(
            "[IMP:7][watchdog][wipe] wipe notify rc=%d (non-fatal): %s",
            result.returncode,
            (result.stderr or result.stdout).strip()[:200],
        )
        return False
    logger.info("[IMP:9][watchdog][wipe] WIPE NOTIFY sent (baseline volumes=%d)", baseline["volumes"])
    return True


# endregion FUNC_notify_wipe


# region FUNC_check_wipe_signature
## @purpose  Wipe-guard проверка (F8 prevention, DevPlan 031 T3): сравнение текущего состояния
##           ноды (boot_id + число named volumes) с персистентным здоровым baseline.
##           Wipe-сигнатура: здоровый baseline (≥3 named volumes) → ТЕКУЩЕЕ volumes=0
##           (docker-data reset / «ребут с потерей состояния»). Первый прогон = baseline
##           (без алерта); перезагрузка с восстановленным состоянием = re-baseline (без алерта);
##           dry-run = план без мутаций. Ограничение (TRAP[DEBT] выше): полный root-fs reset
##           уносит сам watchdog — этот класс детектируется только offsite/provider console.
## @io       ⇥ dry_run/state_file/now/run_cmd/boot_id_fn (DI) → ⎋ int (0 = ok/skip)
## @complexity O(1) — чтение boot_id + один docker volume ls
## @invariants
##   - boot_id недоступен (non-Linux) ИЛИ docker volume ls rc≠0 → skip (0), НЕ ложный алерт
##   - Без baseline (первый прогон) → сохранить baseline, без алерта
##   - Wipe-алерт — только из здорового baseline (volumes ≥ WIPE_MIN_BASELINE_VOLUMES) в volumes=0;
##     подавлен WIPE_ALERT_COOLDOWN_SEC (после алерта baseline = текущее)
##   - Перезагрузка (boot_id сменился) с восстановленным состоянием → re-baseline БЕЗ алерта
##   - dry-run: 0 мутаций (state не пишется, TG не вызывается)
def check_wipe_signature(
    *,
    dry_run: bool = False,
    state_file: str | None = None,
    now: float | None = None,
    run_cmd: RunCmd | None = None,
    boot_id_fn: Callable[[], str | None] | None = None,
) -> int:
    """Run the wipe-guard state-loss check; returns 0 (ok/skip) — watchdog проход не блокирует."""
    ts = now if now is not None else time.time()
    boot_id = (boot_id_fn if boot_id_fn is not None else _wipe_read_boot_id)()
    if boot_id is None:
        logger.info("[IMP:7][watchdog][wipe] boot_id unavailable (non-Linux?) — wipe-guard skipped")
        return 0
    volumes = _wipe_count_volumes(run_cmd)
    if volumes is None:
        logger.info("[IMP:7][watchdog][wipe] docker volume ls failed/unavailable — wipe-guard skipped")
        return 0
    path = state_file or _wipe_state_path()

    baseline = _wipe_load_state(path)
    if baseline is None:
        logger.info(
            "[IMP:8][watchdog][wipe] First run — baseline saved (boot=%s volumes=%d)",
            boot_id[:12],
            volumes,
        )
        if not dry_run:
            _wipe_save_state(path, WipeState(boot_id=boot_id, volumes=volumes, alerted_at=0.0))
        return 0

    healthy_baseline = baseline["volumes"] >= WIPE_MIN_BASELINE_VOLUMES
    rebooted = baseline["boot_id"] != boot_id
    wipe_signature = healthy_baseline and volumes == 0

    if wipe_signature:
        logger.error(
            "[IMP:10][watchdog][wipe] WIPE SIGNATURE: baseline volumes=%d boot=%s → now volumes=0 "
            "(docker-state reset?)",
            baseline["volumes"],
            baseline["boot_id"][:12],
        )
        if not dry_run:
            notified = False
            if ts - baseline["alerted_at"] >= WIPE_ALERT_COOLDOWN_SEC:
                notified = notify_wipe(baseline, volumes, dry_run=False, run_cmd=run_cmd)
            # baseline = текущее (пустое) состояние — без перезаписи alerted_at алерт бы
            # повторялся каждые 5 минут; следующий здоровый прогон даст свежий baseline.
            _wipe_save_state(
                path,
                WipeState(
                    boot_id=boot_id,
                    volumes=volumes,
                    alerted_at=ts if notified else baseline["alerted_at"],
                ),
            )
        return 0
    if rebooted:
        logger.info(
            "[IMP:8][watchdog][wipe] Boot changed %s → %s (volumes=%d) — re-baseline",
            baseline["boot_id"][:12],
            boot_id[:12],
            volumes,
        )
        if not dry_run:
            _wipe_save_state(path, WipeState(boot_id=boot_id, volumes=volumes, alerted_at=0.0))
        return 0
    if healthy_baseline and volumes < baseline["volumes"]:
        logger.warning(
            "[IMP:8][watchdog][wipe] Named volume count dropped %d → %d (no reboot) — watch",
            baseline["volumes"],
            volumes,
        )
    if not dry_run and volumes != baseline["volumes"]:
        _wipe_save_state(path, WipeState(boot_id=boot_id, volumes=volumes, alerted_at=baseline["alerted_at"]))
    return 0


# endregion FUNC_check_wipe_signature


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
                # QA R4/T2.D: сбой re-save НЕ блокирует остаток батча — остальные crash-loop
                # уведомления обрабатываются; штамп в памяти не персистирован → re-notify
                # следующим проходом (retry-семантика). Exit 1 сохраняется за вызывающим.
                logger.error(
                    "[IMP:10][watchdog][state] Crashloop state re-save failed: %s — "
                    "continuing with remaining crashloops (re-notify next pass)",
                    exc,
                )
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
            # QA R4/T2.D: сбой re-save НЕ прерывает остаток батча — остальные restart'ы
            # лечатся; штамп в памяти не персистирован → retry следующего прохода.
            logger.error(
                "[IMP:10][watchdog][state] State re-save failed (%s) — action counted failed, "
                "remaining actions continue; stamp retries next pass",
                exc,
            )
            restart_failures += 1
            continue
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
    state_file: str | None = None,
    now: float | None = None,
    *,
    # QA-гигиена (T2.D-волна): булевы параметры — kw-only (FBT001/FBT002)
    dry_run: bool = False,
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

    # ── REF-0014 + plan 012 T13 (F-026): crash-loop skip-path (window) — TG «не рестарчу» ──
    crashloop_names = sorted({c["name"] for c in containers if _is_crash_looping(c, now=ts)})

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

    # ── F8 wipe-guard (DevPlan 031 T3): state-loss детектор ПЕРЕД контейнерным проходом ──
    # Отдельная проверка: при wipe контейнеров 0 → run_watchdog вышел бы на раннем return 0,
    # а baseline wipe-guard обязан фиксироваться и в пустом состоянии. Ошибка guard (≠0) НЕ
    # блокирует контейнерный watchdog (detector best-effort, алерт — TG).
    _ = check_wipe_signature(dry_run=args.dry_run, run_cmd=None)

    return run_watchdog(dry_run=args.dry_run, state_file=args.state_file, facts=facts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
# endregion FUNC_main
