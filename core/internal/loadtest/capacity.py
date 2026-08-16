#!/usr/bin/env python3
# GREP_SUMMARY: loadtest capacity ramp steps rps doubling stabilization safety-stop error-rate p99 constant-throughput profile
# STRUCTURE: ▶ plan_steps (start×2^i, max_steps) → ◇ run_capacity ∋ per-step: step_runner(rps) →
#           ◇ error>max_error | p99>max_p99 → safety-stop → ⊕ profile + max_rps (last success) → ⎋ CapacityResult
# region MODULE_CONTRACT
## @purpose  Capacity-режим нагрузочного тестирования (DevPlan 146 W4): итеративный
##           профиль шагов (start_rps ×2, max_steps=8), стабилизация 60s/шаг, точный RPS
##           шага через LT_TARGET_RPS=<step> (constant_throughput per-user через
##           _locust_env, 146-m1 BUG-1; users = step×2 пул), критерий
##           останова (error > 5% | p99 > max_p99), max_rps = последний успешный шаг.
##           Формат: ПОСЛЕДОВАТЕЛЬНЫЕ headless-прогоны по шагу (детерминированнее
##           locust --steps — нет встроенной семантики стабилизации+проверки между шагами).
## @scope    Потребитель: runner_cli.py (capacity-ветка). Логика ступеней — чистая
##           (DI step_runner) — детерминированные unit-тесты без реального locust
##           (tests/unit/test_loadtest_capacity.py: насыщение по error, по p99, без насыщения).
## @invariants
##   1. plan_steps: [start, start×2, start×4, ...] — max_steps элементов (default 8)
##   2. Каждый шаг — отдельный headless-прогон duration=capacity_step_duration (60s),
##      LT_TARGET_RPS=<step> (constant_throughput per-user), users = step×2,
##      spawn-rate = step (DevPlan 146 §3.3; 146-m1 BUG-1)
##   3. Safety-stop шага: error_rate > max_error ИЛИ p99 > max_p99 → шаг НЕ успешен → стоп
##   4. max_rps = RPS последнего успешного шага; 0 если первый шаг уже не успешен
##      (вердикт FAIL, exit 1 — capacity-вердикт по контракту)
##   5. Суммарный timeout = max_steps × (step_duration + 30s) + 120s (runner_cli guard)
##   6. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
## @rationale Последовательные прогоны (а не --steps) дают детерминированную проверку
##            критерия останова МЕЖДУ шагами (стабилизация + валидация метрик до следующего
##            шага) — встроенный locust --steps такой семантики не имеет (DevPlan 146 §3.3).
## @changes  2026-08-11 | DevPlan 146 W4 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from core.internal.loadtest.report import StepStats
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 8


# region DATA_StepResult
@dataclass(frozen=True)
class StepResult:
    """Результат одного шага capacity (профиль шагов в отчёте).

    ## @purpose  Строка capacity_profile: целевой step RPS, фактические метрики шага,
    ##            успешность и причина останова (safety-stop / runner error).
    ## @invariants
    ##   - success=True → reason=None; success=False → reason задан (причина останова)
    ##   - rps/p95/p99/error_rate — фактические метрики шага (None при runner error)
    """

    step: int
    rps: float | None = None
    p95: float | None = None
    p99: float | None = None
    error_rate: float | None = None
    success: bool = False
    reason: str | None = None


# endregion DATA_StepResult


# region DATA_CapacityResult
@dataclass(frozen=True)
class CapacityResult:
    """Итог capacity-прогона: профиль шагов, max_rps, насыщенность.

    ## @purpose  Вход для отчёта (capacity_profile + max_rps + verdict_capacity).
    ## @invariants
    ##   - max_rps = 0 → ни один шаг не успешен (verdict FAIL)
    ##   - saturated=True → останов по safety-stop (error/p99), не по лимиту шагов
    """

    profile: list[StepResult] = field(default_factory=list)
    max_rps: int = 0
    saturated: bool = False


# endregion DATA_CapacityResult


# region FUNC_plan_steps
def plan_steps(start_rps: int, max_steps: int = DEFAULT_MAX_STEPS) -> list[int]:
    """План шагов: start × 2^i, i = 0..max_steps-1 (детерминированный профиль).

    ▶ ┌start_rps, max_steps┐ → ○ [start × 2^i] → ⎋ list[int]

    ## @purpose  Профиль удвоения RPS (DevPlan 146 §3.3: start_rps=2 → 2,4,8,...,256).
    ## @io — ⇥ start_rps: int (> 0), max_steps: int → ⎋ list[int] длины max_steps
    ## @complexity — O(max_steps)
    ## @invariants
    ##   - start_rps <= 0 → ValueError (fail-fast: конфиг валидирует раньше, exit 4)
    ##   - max_steps <= 0 → пустой список (цикл не выполняется)
    """
    if start_rps <= 0:
        msg = f"start_rps должен быть > 0, получено {start_rps}"
        raise ConfigValidationError(msg)
    steps = [start_rps * (2**i) for i in range(max_steps)]
    logger.info("[IMP:8][capacity][plan_steps] start=%d max_steps=%d → %s", start_rps, max_steps, steps)
    return steps


# endregion FUNC_plan_steps


# region FUNC_run_capacity
def run_capacity(
    step_runner: Callable[[int], StepStats],
    start_rps: int,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_error: float = 0.05,
    max_p99: float = 3.0,
) -> CapacityResult:
    """Прогон capacity-профиля: цикл шагов со safety-stop (DI step_runner).

    ▶ ┌step_runner, start_rps, max_steps, max_error, max_p99┐ → ○ for step in plan_steps:
      → raw = step_runner(step) → ◇ "error" в raw → шаг fail → break → ◇ error>max_error |
      p99>max_p99 → шаг fail → break → ⊕ profile → ⎋ CapacityResult

    ## @purpose  Оркестрация шагов (инварианты 2-4): step_runner выполняет один
    ##            headless-прогон (локально или на ноде) и возвращает метрики —
    ##            DI делает функцию детерминированно тестируемой (fake-runner).
    ## @io — ⇥ step_runner: Callable[[int], dict] — rps → {"rps", "p95", "p99",
    ##         "error_rate"} | {"error": str}; start_rps: int; max_steps: int;
    ##         max_error: float (0.05 = 5%); max_p99: float (s)
    ##       → ⎋ CapacityResult {profile, max_rps, saturated}
    ## @complexity — O(S) — S = выполненных шагов (≤ max_steps)
    ## @invariants
    ##   - step_runner вернул "error" (прогон упал) → шаг fail с reason, цикл стоп
    ##   - error_rate > max_error ИЛИ p99 > max_p99 → safety-stop (reason документирован)
    ##   - max_rps = последний успешный шаг; 0 если первый шаг не успешен
    ##   - runner error / safety-stop не прерывает CapacityResult — профиль сохраняется
    """
    profile: list[StepResult] = []
    max_rps = 0
    saturated = False
    for step in plan_steps(start_rps, max_steps):
        raw = step_runner(step)
        if "error" in raw:
            reason = f"runner error: {raw['error']}"
            logger.error("[IMP:10][capacity][step] step=%d %s", step, reason)
            profile.append(StepResult(step=step, success=False, reason=reason))
            break
        error_rate = float(raw.get("error_rate") or 0.0)
        p99 = raw.get("p99")
        rps = raw.get("rps")
        p95 = raw.get("p95")
        if error_rate > max_error:
            reason = f"safety-stop: error_rate {error_rate:.3f} > {max_error}"
            logger.info("[IMP:9][capacity][step] step=%d %s", step, reason)
            profile.append(
                StepResult(step=step, rps=rps, p95=p95, p99=p99, error_rate=error_rate, success=False, reason=reason)
            )
            saturated = True
            break
        if p99 is not None and p99 > max_p99:
            reason = f"safety-stop: p99 {p99:.3f}s > {max_p99}s"
            logger.info("[IMP:9][capacity][step] step=%d %s", step, reason)
            profile.append(
                StepResult(step=step, rps=rps, p95=p95, p99=p99, error_rate=error_rate, success=False, reason=reason)
            )
            saturated = True
            break
        logger.info(
            "[IMP:9][capacity][step] step=%d OK (rps=%s p95=%s p99=%s error=%s)",
            step,
            rps,
            p95,
            p99,
            error_rate,
        )
        profile.append(
            StepResult(step=step, rps=rps, p95=p95, p99=p99, error_rate=error_rate, success=True, reason=None)
        )
        max_rps = step

    logger.info(
        "[IMP:9][capacity][run] profile=%d steps, max_rps=%d, saturated=%s",
        len(profile),
        max_rps,
        saturated,
    )
    return CapacityResult(profile=profile, max_rps=max_rps, saturated=saturated)


# endregion FUNC_run_capacity
