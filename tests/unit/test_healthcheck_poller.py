"""Unit tests for HealthcheckPoller."""
# GREP_SUMMARY: test-healthcheck-poller, healthcheck, poll, timeout, retry, unit-test
# STRUCTURE: ▶ test_init → test_poll_project_unknown → test_poll_until_healthy_timeout → test_healthcheck_result
# region MODULE_CONTRACT
## @purpose  Unit tests for HealthcheckPoller — health polling utility.
## @scope    Tests timeout, interval, max_retries configuration and edge cases.
## @invariants
##   - Default timeout: 30s, interval: 10s, max_retries: HEALTHCHECK_POLL_MAX_RETRIES (20, из timeouts.py D34)
##   - Returns "unhealthy" on any failure (never raises)
##   - poll_until_healthy returns "timeout" when retries exhausted
## @changes 2026-07-30 | DevPlan 089 T16 — Created
## @changes 2026-08-01 | DevPlan 117 D34 — max_retries из timeouts.HEALTHCHECK_POLL_MAX_RETRIES (6→20)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

import pytest

from core.internal.deploy.healthcheck_poller import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_TIMEOUT,
    HealthcheckPoller,
    HealthcheckResult,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


class TestHealthcheckPoller:
    """HealthcheckPoller unit tests."""

    # region FUNC_test_init_defaults
    def test_init_defaults(self) -> None:
        """Verify default parameters."""
        poller = HealthcheckPoller()
        assert poller.timeout == DEFAULT_POLL_TIMEOUT
        assert poller.interval == DEFAULT_POLL_INTERVAL
        assert poller.max_retries == DEFAULT_MAX_RETRIES

    # endregion FUNC_test_init_defaults

    # region FUNC_test_defaults_aligned_with_canon (DevPlan 118 C11)
    # 🧪 TRAP[TEST] · Regression · C11 — poller дефолты выровнены с каноном shared/timeouts
    # · Scenario: DEFAULT_POLL_TIMEOUT == HEALTHCHECK_POLL_TIMEOUT (60); INTERVAL == HEALTHCHECK_POLL_INTERVAL (3);
    # ·   окно поллинга = max_retries × interval == 60s (прежние 30/10 → 200s)
    # · Last fail: poller 30/10 vs канон 60/3 — расхождение окна поллинга (DevPlan 118 C11)
    # · Remove if: poller дефолты отвязаны от timeouts канона
    def test_defaults_aligned_with_canon(self) -> None:
        """C11: poller дефолты == HEALTHCHECK_POLL_TIMEOUT/HEALTHCHECK_POLL_INTERVAL (канон timeouts)."""
        from core.internal.shared.timeouts import HEALTHCHECK_POLL_INTERVAL, HEALTHCHECK_POLL_TIMEOUT

        assert DEFAULT_POLL_TIMEOUT == HEALTHCHECK_POLL_TIMEOUT, (
            f"C11 FAIL: DEFAULT_POLL_TIMEOUT={DEFAULT_POLL_TIMEOUT} != канон {HEALTHCHECK_POLL_TIMEOUT}"
        )
        assert DEFAULT_POLL_INTERVAL == HEALTHCHECK_POLL_INTERVAL, (
            f"C11 FAIL: DEFAULT_POLL_INTERVAL={DEFAULT_POLL_INTERVAL} != канон {HEALTHCHECK_POLL_INTERVAL}"
        )
        assert DEFAULT_POLL_TIMEOUT == DEFAULT_MAX_RETRIES * DEFAULT_POLL_INTERVAL, (
            f"C11 FAIL: окно поллинга {DEFAULT_MAX_RETRIES}×{DEFAULT_POLL_INTERVAL} != timeout {DEFAULT_POLL_TIMEOUT}"
        )
        logger.critical(
            "[IMP:9][test] C11 poller канон: timeout=%ds interval=%ds окно=%ds",
            DEFAULT_POLL_TIMEOUT,
            DEFAULT_POLL_INTERVAL,
            DEFAULT_MAX_RETRIES * DEFAULT_POLL_INTERVAL,
        )

    # endregion FUNC_test_defaults_aligned_with_canon (DevPlan 118 C11)

    # region FUNC_test_init_custom
    def test_init_custom(self) -> None:
        """Verify custom parameters."""
        poller = HealthcheckPoller(timeout=5, interval=2, max_retries=3)
        assert poller.timeout == 5
        assert poller.interval == 2
        assert poller.max_retries == 3

    # endregion FUNC_test_init_custom

    # region FUNC_test_poll_project_no_method
    def test_poll_project_no_method(self) -> None:
        """Verify poll returns unhealthy when no method available."""
        poller = HealthcheckPoller(timeout=1, interval=1, max_retries=1)
        result = poller.poll_project("nonexistent-project")
        assert result.status == "unhealthy"
        assert result.project == "nonexistent-project"
        assert result.method == "unknown"

    # endregion FUNC_test_poll_project_no_method

    # region FUNC_test_poll_until_healthy_timeout
    def test_poll_until_healthy_timeout(self) -> None:
        """Verify poll_until_healthy returns timeout after retries."""
        poller = HealthcheckPoller(timeout=1, interval=1, max_retries=2)
        result = poller.poll_until_healthy("nonexistent-project")
        assert result.status == "timeout"
        assert result.attempts == 2

    # endregion FUNC_test_poll_until_healthy_timeout

    # region FUNC_test_healthcheck_result_creation
    def test_healthcheck_result_creation(self) -> None:
        """Verify HealthcheckResult dataclass."""
        result = HealthcheckResult(
            status="healthy",
            project="test",
            method="http",
            attempts=2,
            detail="OK",
        )
        assert result.status == "healthy"
        assert result.project == "test"
        assert result.method == "http"
        assert result.attempts == 2
        assert result.detail == "OK"

    # endregion FUNC_test_healthcheck_result_creation

    # region FUNC_test_healthcheck_result_unhealthy
    def test_healthcheck_result_unhealthy(self) -> None:
        """Verify unhealthy HealthcheckResult."""
        result = HealthcheckResult(
            status="unhealthy",
            project="test",
            method="docker",
            attempts=3,
            detail="Connection refused",
        )
        assert result.status == "unhealthy"
        assert result.attempts == 3


# endregion FUNC_test_healthcheck_result_unhealthy

# 🧪 TRAP[TEST] · Regression · HealthcheckPoller is non-fatal on any failure


# region TEST_REF0103_wall_time_budget
# REF-0103: единый monotonic deadline (start + max_retries×interval) — wall-time бюджет.
# До фикса вложенные бюджеты (per-attempt полный timeout) давали 20×(60+3)s ≈ 21 мин
# при документированных 60s. Тест детерминирован DI-швами clock_fn/sleep_fn (0 monkeypatch
# на time-модуль, 0 реальных sleep).


class _FakeClock:
    """Детерминированные часы: продвигаются только вызовом advance()."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _install_hanging_http(monkeypatch: pytest.MonkeyPatch, clock: _FakeClock, timeouts_seen: list[int]) -> None:
    """HTTP-клиент, «зависающий» ровно на свой timeout (продвигает fake-clock), всегда fail.

    ## @purpose — Симуляция worst-case REF-0103: каждая попытка съедает ВЕСЬ свой per-check
    ##            бюджет на сетевом I/O. Старый код при таком поведении уходил в ~21 мин.
    """

    class _HangingCtx:
        """Контекст http_client.request с «зависающим» коннектом (advance + TimeoutError)."""

        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            timeouts_seen.append(self.timeout)
            clock.advance(self.timeout)
            msg = f"hanging connect ({self.timeout}s)"
            raise TimeoutError(msg)

        def __exit__(self, *args: object) -> bool:
            return False

        status = 503

    def _fake_request(url: str, *, method: str = "GET", timeout: int = 5, **_kw: object):
        del url, method
        return _HangingCtx(timeout)

    monkeypatch.setattr("core.internal.deploy.healthcheck_poller.http_client.request", _fake_request)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · REF-0103 wall-time budget — исходный вход BUG-класса
# «60-секундное окно реально ~21 мин» (P1-before-launch REF-0103 Problem)
# · Scenario: HTTP-коннект зависает на весь per-check бюджет; max_retries=20 × interval=3s
# ·   → старый код делал бы 20 попыток × 63s ≈ 1260s. Новый код обязан остановиться по
# ·   deadline=60s: РОВНО одна attempt (бюджет исчерпан первой же попыткой).
# · Remove if: поллер перестаёт обещать документированный wall-time бюджет
def test_poll_until_healthy_wall_time_budget_red_ref0103(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wall-time ≤ задокументированного бюджета; hanging-коннект не размножает попытки."""
    from core.internal.deploy.healthcheck_poller import DEFAULT_MAX_RETRIES, DEFAULT_POLL_INTERVAL

    budget = DEFAULT_MAX_RETRIES * DEFAULT_POLL_INTERVAL  # документированный бюджет 60s
    clock = _FakeClock()
    sleeps: list[float] = []
    timeouts_seen: list[int] = []
    captured: dict[str, object] = {}

    _install_hanging_http(monkeypatch, clock, timeouts_seen)

    poller = HealthcheckPoller(clock_fn=clock, sleep_fn=sleeps.append)
    result = poller.poll_until_healthy("hanging-project")

    captured["attempts"] = result.attempts
    captured["elapsed"] = clock.now
    logger.critical(
        "[IMP:9][test][REF-0103] wall-time budget: elapsed=%.0fs/%ds budget, attempts=%s, per-url timeouts=%s",
        clock.now,
        budget,
        result.attempts,
        sorted(set(timeouts_seen)),
    )

    assert result.status == "timeout", "зависающий проект → честный timeout-статус"
    # Главная red→green ассерта REF-0103: первая же attempt съедает весь 60s бюджет →
    # попыток НЕ 20 (старый код), а минимум; суммарное время ≤ бюджета + последний интервал.
    assert captured["elapsed"] <= budget + DEFAULT_POLL_INTERVAL, (
        f"REF-0103 FAIL: wall-time {captured['elapsed']}s > бюджет {budget}s (+1 interval)"
    )
    assert captured["attempts"] < DEFAULT_MAX_RETRIES // 2, (
        f"REF-0103 FAIL: {captured['attempts']} полных бюджетных попыток — deadline не работает"
    )
    # Бюджет прокидывается вниз: ни один per-URL timeout не превысил общий бюджет поллера.
    assert all(t <= budget for t in timeouts_seen), f"per-URL timeout выше бюджета: {timeouts_seen}"
    # Поллер не спал после исчерпания дедлайна (нет хвостовых sleep'ов).
    assert sum(sleeps) <= budget + DEFAULT_POLL_INTERVAL


def test_poll_until_healthy_healthy_first_attempt_no_wasted_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthy на первой попытке → мгновенный выход, без retry-sleep (deadline не тратится)."""
    clock = _FakeClock()

    class _OkCtx:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        status = 200

    monkeypatch.setattr(
        "core.internal.deploy.healthcheck_poller.http_client.request",
        lambda *_a, **_k: _OkCtx(),
    )
    sleeps: list[float] = []
    poller = HealthcheckPoller(clock_fn=clock, sleep_fn=sleeps.append)
    result = poller.poll_until_healthy("ok-project")

    assert result.status == "healthy"
    assert result.method == "http"
    assert sleeps == [], "healthy на первой попытке не должен спать"
    assert clock.now == 0.0


# 🧪 TRAP[TEST] · Regression · REF-0103 · budget прокидывается в docker-poll window
# · Scenario: poll_project(budget=25, project_dir=…) → shared healthcheck_poll получает
# ·   timeout=min(60, 25)=25 (не полный 60) — docker-ветка уважает остаток дедлайна.
# · Remove if: docker-ветка перестаёт принимать budget
def test_try_docker_receives_remaining_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """budget ≤ remaining прокидывается в shared healthcheck_poll как poll window."""
    import core.internal.shared.docker_compose as dc_module

    seen: dict[str, object] = {}

    def _fake_shared_poll(name: str, *, timeout: int = 60, interval: int = 3) -> str:
        seen["timeout"] = timeout
        seen["interval"] = interval
        return "unhealthy"

    monkeypatch.setattr(dc_module, "healthcheck_poll", _fake_shared_poll)

    poller = HealthcheckPoller()
    result = poller.poll_project("proj", project_dir="/tmp/x", budget=25)

    assert seen["timeout"] == 25, f"docker-poll window должен быть 25 (остаток), got {seen['timeout']}"
    assert result.status == "timeout"


# endregion TEST_REF0103_wall_time_budget
