#!/usr/bin/env python3
# GREP_SUMMARY: retry, backoff, exponential-backoff, retryable, predicate, sleep-fn, exception-mode, attempts, retry-loop, shared, DevPlan-177
# STRUCTURE: ▶ ┌func + policy┐ → ○ attempt 1..N: ⊕ func() → ◇ retryable(predicate) → ⚡ sleep(backoff, clamp-last) → ⎋ last value / re-raise
# region MODULE_CONTRACT
## @purpose  Единый retry-helper платформы (DevPlan 177 W3.1): консолидация 4 дублей
##           retry-циклов (state_machine._call_with_retry, system._run_with_retry,
##           channels._retry_deliver, docker_compose.retry_pull) в один параметризуемый
##           retry() с экспоненциальным backoff. Политики — из shared/timeouts.py (U-11/D34):
##           RETRY_COUNT, RETRY_BACKOFF_SECONDS, RETRY_BACKOFF_EXPONENTIAL_BASE.
## @scope    Все Python-модули core/internal, выполняющие retry-операции с backoff.
##           Модуль НЕ знает о доменах потребителей — только loop/backoff/sleep/logging;
##           доменные адаптеры (predicate, число попыток, перевод исключений) — у потребителей.
## @invariants
##   1. Числовые политики импортируются из shared/timeouts.py — литералов 2/3/5/10/20 здесь НЕТ.
##   2. retry() — ЕДИНСТВЕННАЯ реализация retry-цикла; потребители делегируют (прямое замещение).
##   3. retryable-предикат параметризует проверку ошибки: exception_mode=True — предикат
##      получает исключение; exception_mode=False — предикат получает возвращённое func значение.
##   4. Backoff-список короче числа ретраев → последний элемент повторяется (clamp,
##      канон docker_compose.retry_pull / system._run_with_retry).
##   5. exception_mode: не-retryable исключение ИЛИ исчерпание попыток → последнее исключение
##      re-raise (fail-fast, без маскировки). result_mode: exhaustion → последний результат
##      возвращается (caller решает severity — bool/result/raise).
##   6. sleep_fn DI-шов (None = time.sleep) — тесты инжектят мгновенный fake (0 реального sleep).
##   7. Logging LDD: IMP:8 попытка, IMP:7 backoff, IMP:9 успех, IMP:10 исчерпание.
## @rationale DevPlan 177 W3.1: 4 независимые копии retry-цикла (state_machine/system/channels/
##            docker_compose) с разными backoff-политиками и логами — дрейф-вектор (одна волна
##            правит backoff в одном месте, забывая остальные). Единый helper (критерий shared/:
##            дедупликация ≥2 реализаций, RC3 C1) централизует loop/backoff/sleep/logging;
##            доменные различия (raise vs result, attempts-семантика) выражаются параметрами.
## @changes 2026-08-16 | DevPlan 177 W3.1 — Created (консолидация 4 retry-дублей)
# 🧐 TRAP[DECISION] · 2026-08-16 · — · Инвентарная запись shared/AGENTS.md (таблица модулей) НЕ добавлена
# · Rejected: правка shared/AGENTS.md в рамках 177 W3.1 (файл вне разрешённого списка волны)
# · Reason: deferred, out of scope — инвариант 3(c) shared/AGENTS.md требует запись «retry.py» в таблицу
# · Rev: при следующей волне, трогающей shared/AGENTS.md — добавить строку (API: retry/exponential_backoff,
# ·   потребители: state_machine/system/channels/docker_compose)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Literal, TypeVar, overload

# Единый реестр политик — shared/timeouts.py (U-11/D34): RETRY_COUNT=2 ретрая,
# RETRY_BACKOFF_SECONDS=[5,10,20] (список), RETRY_BACKOFF_EXPONENTIAL_BASE=2 (база).
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.timeouts import (
    RETRY_BACKOFF_EXPONENTIAL_BASE,
    RETRY_BACKOFF_SECONDS,
    RETRY_COUNT,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Канонические дефолты политик — ПРОИЗВОДНЫЕ от timeouts, не литералы:
# RETRY_COUNT=2 ретрая → DEFAULT_ATTEMPTS=3 (первая попытка + 2 ретрая)
DEFAULT_ATTEMPTS = RETRY_COUNT + 1
# Backoff-список по умолчанию — канон [5, 10, 20]
DEFAULT_BACKOFF_SECONDS: Sequence[float] = RETRY_BACKOFF_SECONDS


# region FUNC_exponential_backoff
## @purpose  Генерация экспоненциального backoff-расписания [base**1, ..., base**retries]
##           с необязательным потолком max_seconds (base — RETRY_BACKOFF_EXPONENTIAL_BASE=2
##           из shared/timeouts; потолок — канонический максимум RETRY_BACKOFF_SECONDS[-1]=20).
##           Заменяет инлайн `base ** attempt`-генерацию state_machine (W5-E6 C2).
## @io       ⇥ retries: int (число ретраев = attempts-1), base: float = RETRY_BACKOFF_EXPONENTIAL_BASE,
##              max_seconds: float | None = None → ⎋ list[float] (расписание sleep-пауз)
## @complexity O(retries)
## @invariants
##   - Расписание [base**1, base**2, ...] — индексы 1-based (первая пауза после attempt 1)
##   - max_seconds=None → без потолка (state_machine-семантика: backoff не капается)
##   - max_seconds задан → каждый элемент min(d, max_seconds) (потолок канона)
def exponential_backoff(
    retries: int,
    *,
    base: float = RETRY_BACKOFF_EXPONENTIAL_BASE,
    max_seconds: float | None = None,
) -> list[float]:
    """Generate exponential backoff schedule [base**1, ..., base**retries] capped at max_seconds."""
    schedule: list[float] = [float(base**i) for i in range(1, retries + 1)]
    if max_seconds is not None:
        schedule = [min(d, max_seconds) for d in schedule]
    logger.info("[IMP:8][exponential_backoff] Schedule for %d retries: %s", retries, schedule)
    return schedule


# endregion FUNC_exponential_backoff


# region FUNC__backoff_delay
## @purpose  Извлечение sleep-паузы для попытки attempt: backoff_seconds[attempt-1] с clamp
##           на последний элемент (канон docker_compose/system: короткий список → последний
##           элемент повторяется на оставшихся попытках).
## @io       ⇥ backoff_seconds: Sequence[float] (непустая), attempt: int (1-based) → ⎋ float
## @complexity O(1)
def _backoff_delay(backoff_seconds: Sequence[float], attempt: int) -> float:
    """Return sleep delay for attempt (1-based), clamping to the last backoff element."""
    return float(backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)])


# endregion FUNC__backoff_delay


# region FUNC_retry
## @purpose  ЕДИНСТВЕННЫЙ retry-цикл платформы: исполняет func до attempts раз; retryable-
##           предикат решает, retry'ить ли результат/исключение; backoff-пауза между
##           попытками; LDD-logging IMP:7-10. Заменяет 4 дубля (DevPlan 177 W3.1).
##           Два overload'а дают type-safe предикат: exception_mode=True → Callable[[Exception], bool],
##           иначе → Callable[[T], bool] (T = возвращаемый тип func).
## @io       ⇥ func: Callable[[], T] (thunk — потребитель связывает доменные аргументы),
##              attempts: int = DEFAULT_ATTEMPTS (общее число попыток, включая первую),
##              backoff_seconds: Sequence[float] = DEFAULT_BACKOFF_SECONDS (паузы; clamp-last),
##              retryable: Callable[..., bool] (predicate: True = retry; тип — по overload'у),
##              sleep_fn: Callable[[float], None] | None = None (DI; None = time.sleep),
##              exception_mode: bool = False (True: predicate ← exception, exhaustion → re-raise;
##                                False: predicate ← возвращённое значение, exhaustion → last value)
##           → ⎋ T
##           ⚡ exception_mode=True: re-raise последнего исключения при исчерпании/не-retryable
##           ⚡ result_mode: исключения func НЕ перехватываются (пропагируются — контракт caller'а)
## @complexity O(attempts * cost(func))
## @invariants
##   - attempts >= 1 и backoff_seconds непустой — валидируется на входе (fail-fast)
##   - Пауза перед retry N: backoff_seconds[N-1] с clamp на последний элемент
##   - exception_mode: retryable(exc) False ИЛИ attempts исчерпаны → последний exc re-raise
##     (никакой маскировки — fail-fast); BaseException (KeyboardInterrupt/SystemExit) НЕ ловятся
##   - result_mode: retryable(value) False → мгновенный возврат value (без лишних попыток)
@overload
def retry(
    func: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: Sequence[float] = DEFAULT_BACKOFF_SECONDS,
    retryable: Callable[[T], bool],
    sleep_fn: Callable[[float], None] | None = None,
    exception_mode: Literal[False] = False,
) -> T: ...


@overload
def retry(
    func: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: Sequence[float] = DEFAULT_BACKOFF_SECONDS,
    retryable: Callable[[Exception], bool],
    sleep_fn: Callable[[float], None] | None = None,
    exception_mode: Literal[True],
) -> T: ...


def retry(
    func: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: Sequence[float] = DEFAULT_BACKOFF_SECONDS,
    retryable: Callable[..., bool],
    sleep_fn: Callable[[float], None] | None = None,
    exception_mode: bool = False,
) -> T:
    """Run func with retry+backoff; see MODULE_CONTRACT invariants."""
    if attempts < 1:
        msg = "attempts must be >= 1"
        raise ConfigValidationError(msg)
    if not backoff_seconds:
        msg = "backoff_seconds must be non-empty"
        raise ConfigValidationError(msg)
    sleeper = time.sleep if sleep_fn is None else sleep_fn

    for attempt in range(1, attempts + 1):
        logger.info("[IMP:8][retry][attempt] Attempt %d/%d", attempt, attempts)
        if exception_mode:
            try:
                value = func()
            except Exception as exc:  # noqa: EXC — retry policy: перехват ТОЛЬКО для передачи в retryable-предикат; не-retryable или исчерпание → re-raise (fail-fast, L0.4)
                # retry-loop: перехват ТОЛЬКО для передачи в retryable-предикат; не-retryable
                # или исчерпание → re-raise (fail-fast, никакой маскировки — конституция L0.4)
                if not retryable(exc) or attempt >= attempts:
                    logger.error("[IMP:10][retry][exhausted] All %d attempts failed: %s", attempts, exc)
                    raise
                delay = _backoff_delay(backoff_seconds, attempt)
                logger.info(
                    "[IMP:7][retry][backoff] Retrying in %gs (attempt %d/%d) after %s",
                    delay,
                    attempt + 1,
                    attempts,
                    exc,
                )
                sleeper(delay)
                continue
            logger.info("[IMP:9][retry][success] Succeeded on attempt %d/%d", attempt, attempts)
            return value
        # result_mode: исключения func НЕ перехватываются — контракт caller'а (graceful канон
        # run_subprocess check=False / docker_compose_pull bool-семантика не роняют retry-цикл)
        value = func()
        if not retryable(value):
            logger.info("[IMP:9][retry][success] Succeeded on attempt %d/%d", attempt, attempts)
            return value
        if attempt >= attempts:
            logger.warning("[IMP:10][retry][exhausted] All %d attempts failed", attempts)
            return value
        delay = _backoff_delay(backoff_seconds, attempt)
        logger.info("[IMP:7][retry][backoff] Retrying in %gs (attempt %d/%d)", delay, attempt + 1, attempts)
        sleeper(delay)
    # Недостижимо: attempts >= 1 гарантирует return/raise в цикле (pyright-strict требует return)
    msg = "unreachable: attempts >= 1 гарантирует return/raise в цикле"
    raise AssertionError(msg)  # pragma: no cover


# endregion FUNC_retry
