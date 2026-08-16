# GREP_SUMMARY: loadtest capacity unit ramp steps safety-stop error p99 max-rps deterministic simulation
# STRUCTURE: ▶ plan_steps (start×2^i, max_steps, parametrized profile) → ◇ run_capacity (fake step_runner: насыщение
#           по error / по p99 / без насыщения / runner error / первый шаг fail) → ⎋ 7 tests
# region MODULE_CONTRACT
## @purpose  Unit-тесты capacity (DevPlan 146 W4, tests/unit/test_loadtest_capacity.py):
##           детерминированная симуляция ступеней через DI step_runner (fake-runner,
##           БЕЗ locust/subprocess): насыщение по error_rate, по p99, отсутствие насыщения
##           в лимите шагов, runner error, первый шаг fail → max_rps=0.
## @scope    Чистые функции core/internal/loadtest/capacity.py (plan_steps, run_capacity).
## @invariants
##   - plan_steps: [start, start×2, ...] длиной max_steps; start<=0 → ValueError
##   - safety-stop: error > max_error ИЛИ p99 > max_p99 → шаг fail, цикл стоп
##   - max_rps = последний успешный шаг; 0 если первый шаг не успешен (verdict FAIL)
##   - runner error ("error" в dict) → fail с reason, цикл стоп
##   - LDD: IMP:9 в успешных сценариях (Anti-Illusion Rule)
## @rationale Capacity — AC3 (поиск max RPS с автостопом): логика ступеней и критерии
##            останова тестируются детерминированно (W4-критерий: 3 сценария симуляции).
## @changes  2026-08-11 | DevPlan 146 W4 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

import pytest

from core.internal.loadtest.capacity import CapacityResult, plan_steps, run_capacity

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region HELPER_fake_runner
def _fake_runner(fail_at: int | None = None, p99_at: int | None = None, error_at: int | None = None):
    """Фабрика fake step_runner: метрики шага детерминированы (rps = step, p95 = step/20).

    ## @purpose — DI-симуляция одного headless-прогона (DevPlan 146 §3.3): метрики
    ##            вычислимы из step без I/O — детерминированные тесты останова.
    ## @io — ⇥ fail_at: шаг, на котором вернуть {"error"}; p99_at/error_at: шаги,
    ##         на которых p99/error_rate превышают порог → ⎋ Callable[[int], dict]
    """

    def _step(rps: int) -> dict:
        if fail_at is not None and rps >= fail_at:
            return {"error": "locust rc=1: connection refused"}
        if p99_at is not None and rps >= p99_at:
            return {"rps": float(rps), "p95": rps / 20, "p99": 4.0, "error_rate": 0.0}
        if error_at is not None and rps >= error_at:
            return {"rps": float(rps), "p95": rps / 20, "p99": 1.0, "error_rate": 0.1}
        return {"rps": float(rps), "p95": rps / 20, "p99": 1.0, "error_rate": 0.0}

    return _step


# endregion HELPER_fake_runner


# ═══════════════════════════════════════════════════════════════════════════════
# plan_steps
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_plan_steps
# 🧪 TRAP[TEST] · Scenario: профиль шагов start×2^i (DevPlan 146 §3.3)
# · Regression: start=2, max_steps=8 → [2,4,8,16,32,64,128,256]; start<=0 → ValueError
# · Last fail: N/A (new)
# · Remove if: политика профиля capacity изменена
class TestPlanSteps:
    @pytest.mark.parametrize(
        "start_rps,max_steps,expected",
        [
            (2, 8, [2, 4, 8, 16, 32, 64, 128, 256]),  # start=2, max_steps=8 → 2..256 (×2 каждый шаг)
            (5, 3, [5, 10, 20]),  # max_steps=3 → 3 шага
        ],
    )
    def test_plan_steps_profile(self, start_rps: int, max_steps: int, expected: list[int]):
        """plan_steps: профиль start×2^i длиной max_steps (DevPlan 146 §3.3)."""
        assert plan_steps(start_rps, max_steps) == expected

    def test_non_positive_start_rejected(self):
        """start_rps <= 0 → ConfigValidationError (exit 4 по контракту, без bare ValueError)."""
        from core.internal.shared.exceptions import ConfigValidationError

        with pytest.raises(ConfigValidationError):
            plan_steps(0, 8)


# endregion TEST_plan_steps


# ═══════════════════════════════════════════════════════════════════════════════
# run_capacity — детерминированная симуляция
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_run_capacity
# 🧪 TRAP[TEST] · Scenario: run_capacity safety-stop (error / p99 / лимит / runner error / первый шаг)
# · Regression: автостоп на error>5% | p99>3s; max_rps = последний успешный шаг (AC3)
# · Last fail: N/A (new)
# · Remove if: критерии останова capacity изменены
class TestRunCapacity:
    def test_saturation_by_error(self, caplog):
        """Насыщение по error_rate (10% > 5%) на шаге 8 → max_rps = 4, saturated=True."""
        caplog.set_level(logging.INFO)
        result = run_capacity(_fake_runner(error_at=8), start_rps=2, max_steps=8)
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in list(caplog.records):
            if "[IMP:" in record.message:
                logger.info("%s", record.message)
                if "[IMP:9]" in record.message:
                    found = True
        logger.info("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (capacity run)"
        assert isinstance(result, CapacityResult)
        assert result.max_rps == 4
        assert result.saturated is True
        assert len(result.profile) == 3  # 2, 4, 8(fail)
        assert result.profile[-1].success is False
        assert "safety-stop" in (result.profile[-1].reason or "")

    def test_saturation_by_p99(self):
        """Насыщение по p99 (4s > 3s) на шаге 16 → max_rps = 8, saturated=True."""
        result = run_capacity(_fake_runner(p99_at=16), start_rps=2, max_steps=8)
        assert result.max_rps == 8
        assert result.saturated is True
        assert result.profile[-1].reason and "p99" in result.profile[-1].reason

    def test_no_saturation_within_steps(self):
        """Все шаги успешны → max_rps = последний шаг (256), saturated=False, 8 шагов."""
        result = run_capacity(_fake_runner(), start_rps=2, max_steps=8)
        assert result.max_rps == 256
        assert result.saturated is False
        assert len(result.profile) == 8
        assert all(step.success for step in result.profile)

    def test_runner_error_stops(self):
        """Runner error (locust rc!=0) на шаге 4 → max_rps = 2, reason='runner error'."""
        result = run_capacity(_fake_runner(fail_at=4), start_rps=2, max_steps=8)
        assert result.max_rps == 2
        assert result.profile[-1].success is False
        assert "runner error" in (result.profile[-1].reason or "")

    def test_first_step_fails_max_rps_zero(self):
        """Первый шаг уже fail → max_rps = 0 (вердикт FAIL, exit 1 контракт)."""
        result = run_capacity(_fake_runner(error_at=2), start_rps=2, max_steps=8)
        assert result.max_rps == 0
        assert len(result.profile) == 1
        assert result.profile[0].success is False


# endregion TEST_run_capacity
