# GREP_SUMMARY: test-retry, shared-retry, backoff-sequence, exponential-backoff, retryable, predicate, exception-mode, result-mode, sleep-di, exhaustion-raise, clamp, DevPlan-177
# STRUCTURE: ▶ exception-mode ┌flaky fn┐ → retry → success/raise │ ▶ result-mode ┌rc/bool┐ → retry → last │ ▶ backoff-sequence ┌sleep recorder┐ → assert intervals │ ▶ exponential_backoff schedule │ ▶ validation
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/shared/retry.py (DevPlan 177 W3.1) — ЕДИНСТВЕННЫЙ
##           retry-helper платформы (консолидация 4 дублей). Покрытие: backoff-последовательность
##           (проверка интервалов через DI sleep-recorder), retry до успеха, финальный raise
##           после исчерпания попыток (exception-mode), non-retryable ошибка без повторов,
##           result-mode (success/последний результат), clamp backoff, exponential_backoff,
##           fail-fast валидация входов.
## @scope    unit-тесты: fake sleep_fn (0 реального time.sleep — паттерн DI, W-H);
##           tmp_path не требуется (нет I/O).
## @invariants
##   - Native imports; LDD IMP:9 в успешных сценариях (@ldd_trajectory)
##   - НИКАКОГО реального time.sleep — sleep_fn-рекордер инжектится через DI-параметр
##   - Backoff-интервалы проверяются ФАЛЬСИФИЦИРУЕМО: recorder.assert == ожидаемый список
##   - attempts-семантика: attempts = общее число попыток (включая первую)
## @rationale  $TEST_SPEC DevPlan 177 W3.1: единый retry-helper требует собственного
##            unit-покрытия (инвариант shared/: каждый модуль — unit-тесты);
##            consumer-поведение покрыто существующими тестами потребителей
##            (test_helpers_system_w9, test_idempotency_hash, test_channels, test_shared_docker_compose).
## @changes  2026-08-16 · Created (DevPlan 177 W3.1)
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.retry import (
    DEFAULT_ATTEMPTS,
    DEFAULT_BACKOFF_SECONDS,
    exponential_backoff,
    retry,
)
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# exception-mode: retry на исключении, re-raise на исчерпании
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — retry до успеха (exception-mode)
# · Scenario: func raise OSError на 1-й попытке → retryable → 2-я попытка успех → value
# · Last fail: N/A (новый тест 177 W3.1)
# · Remove if: retry-контракт (attempts/retryable/exception_mode) меняется
@ldd_trajectory
def test_retry_succeeds_on_second_attempt(caplog: pytest.LogCaptureFixture) -> None:
    """exception-mode: транзиентный OSError → retry → успех на 2-й попытке (fake sleep)."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def _flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            msg = "transient network blip"
            raise OSError(msg)
        return "ok"

    result = retry(
        _flaky,
        attempts=3,
        backoff_seconds=[5, 10, 20],
        retryable=lambda exc: isinstance(exc, OSError),
        sleep_fn=sleeps.append,
        exception_mode=True,
    )
    assert result == "ok"
    assert calls["n"] == 2, f"fail+success = 2 попытки, got {calls['n']}"
    assert sleeps == [5.0], f"одна пауза 5s перед retry, got {sleeps}"
    logger.critical("[IMP:9][test] retry succeeds on 2nd attempt with backoff [5.0] — OK (177 W3.1)")


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — backoff-последовательность интервалов
# · Scenario: ВСЕГДА raise → sleep-рекордер фиксирует интервалы [5, 10] (не реальный sleep)
# · Last fail: N/A (новый тест 177 W3.1)
# · Remove if: backoff-семантика (clamp / последовательность) меняется
@ldd_trajectory
def test_retry_backoff_sequence_exponential(caplog: pytest.LogCaptureFixture) -> None:
    """backoff-интервалы: [5.0, 10.0] между 3 попытками (fake sleep-рекордер)."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def _always_fail() -> None:
        calls["n"] += 1
        msg = "boom"
        raise OSError(msg)

    with pytest.raises(OSError, match="boom"):
        retry(
            _always_fail,
            attempts=3,
            backoff_seconds=[5, 10, 20],
            retryable=lambda exc: isinstance(exc, OSError),
            sleep_fn=sleeps.append,
            exception_mode=True,
        )
    assert calls["n"] == 3, f"3 попытки, got {calls['n']}"
    assert sleeps == [5.0, 10.0], f"backoff [5, 10], got {sleeps}"
    logger.critical("[IMP:9][test] backoff sequence [5.0, 10.0] recorded — OK (177 W3.1)")


# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · 177 W3.1 — финальный raise после исчерпания
# · Scenario: ВСЕГДА raise → после attempts=2 последний exception re-raise (fail-fast, T9.11)
# · Last fail: N/A (новый negative-тест 177 W3.1)
# · Remove if: exception-mode exhaustion-семантика меняется
@ldd_trajectory
def test_retry_exhaustion_raises(caplog: pytest.LogCaptureFixture) -> None:
    """exception-mode: исчерпание попыток → последний exception re-raise (не маскировка)."""
    calls = {"n": 0}

    def _always_raise() -> None:
        calls["n"] += 1
        msg = "persistent failure"
        raise OSError(msg)

    with pytest.raises(OSError, match="persistent failure"):
        retry(
            _always_raise,
            attempts=2,
            backoff_seconds=[2],
            retryable=lambda exc: isinstance(exc, OSError),
            sleep_fn=lambda _: None,
            exception_mode=True,
        )
    assert calls["n"] == 2, f"attempts=2: ровно 2 попытки, got {calls['n']}"
    logger.critical("[IMP:9][test] exhaustion re-raises last exception after 2 attempts — OK (177 W3.1)")


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — non-retryable без повторов
# · Scenario: KeyError (вне retryable-предиката) → 1 вызов, мгновенный raise, 0 sleep
# · Last fail: N/A (новый тест 177 W3.1)
# · Remove if: retryable-predicate контракт меняется
@ldd_trajectory
def test_retry_non_retryable_no_retries(caplog: pytest.LogCaptureFixture) -> None:
    """exception-mode: non-retryable ошибка → без повторов (1 вызов, 0 sleep)."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def _permanent() -> None:
        calls["n"] += 1
        msg = "permanent config error"
        raise KeyError(msg)

    with pytest.raises(KeyError, match="permanent config error"):
        retry(
            _permanent,
            attempts=3,
            backoff_seconds=[5, 10, 20],
            retryable=lambda exc: isinstance(exc, OSError),
            sleep_fn=sleeps.append,
            exception_mode=True,
        )
    assert calls["n"] == 1, f"non-retryable → 1 вызов, got {calls['n']}"
    assert sleeps == [], f"non-retryable → 0 sleep, got {sleeps}"
    logger.critical("[IMP:9][test] non-retryable error propagates without retries — OK (177 W3.1)")


# ═══════════════════════════════════════════════════════════════════════════
# result-mode: retry на возвращённом значении, последний результат на исчерпании
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — result-mode успех на 2-й
# · Scenario: rc 1 → 0 (graceful run_subprocess-канон) → 2 вызова, успех
# · Last fail: N/A (новый тест 177 W3.1)
# · Remove if: result-mode контракт меняется
@ldd_trajectory
def test_retry_result_mode_success(caplog: pytest.LogCaptureFixture) -> None:
    """result-mode: retry на ненулевом rc → успех на 2-й попытке (системный канон W7-4)."""
    calls = {"n": 0}

    def _flaky_rc() -> int:
        calls["n"] += 1
        return 0 if calls["n"] >= 2 else 1

    result = retry(
        _flaky_rc,
        attempts=3,
        backoff_seconds=[5, 10, 20],
        retryable=lambda rc: rc != 0,
        sleep_fn=lambda _: None,
    )
    assert result == 0
    assert calls["n"] == 2, f"fail+success = 2 вызова, got {calls['n']}"
    logger.critical("[IMP:9][test] result-mode succeeds on 2nd attempt — OK (177 W3.1)")


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — result-mode последний результат
# · Scenario: ВСЕГДА False (retry_pull-канон) → последний результат False после attempts
# · Last fail: N/A (новый тест 177 W3.1)
# · Remove if: result-mode exhaustion-семантика меняется
@ldd_trajectory
def test_retry_result_mode_exhaustion_returns_last(caplog: pytest.LogCaptureFixture) -> None:
    """result-mode: исчерпание → последний результат (bool-канон retry_pull, не raise)."""
    sleeps: list[float] = []

    result = retry(
        lambda: False,
        attempts=3,
        backoff_seconds=[5, 10, 20],
        retryable=lambda ok: not ok,
        sleep_fn=sleeps.append,
    )
    assert result is False
    assert sleeps == [5.0, 10.0], f"2 паузы [5, 10], got {sleeps}"
    logger.critical("[IMP:9][test] result-mode exhaustion returns last value — OK (177 W3.1)")


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — clamp backoff на последний элемент
# · Scenario: backoff=[1] короче 3 ретраев → последний элемент повторяется (канон docker_compose)
# · Last fail: N/A (новый тест 177 W3.1)
# · Remove if: clamp-семантика меняется
@ldd_trajectory
def test_retry_backoff_clamps_to_last_element(caplog: pytest.LogCaptureFixture) -> None:
    """backoff-список короче attempts → последний элемент повторяется (clamp)."""
    sleeps: list[float] = []

    def _always_fail() -> None:
        msg = "boom"
        raise OSError(msg)

    with pytest.raises(OSError):
        retry(
            _always_fail,
            attempts=4,
            backoff_seconds=[1],
            retryable=lambda exc: isinstance(exc, OSError),
            sleep_fn=sleeps.append,
            exception_mode=True,
        )
    assert sleeps == [1.0, 1.0, 1.0], f"clamp: [1, 1, 1], got {sleeps}"
    logger.critical("[IMP:9][test] backoff clamps to last element — OK (177 W3.1)")


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — успех с первой попытки без backoff
# · Scenario: func успешен сразу → 0 sleep, 1 вызов (никаких лишних попыток)
# · Last fail: N/A (новый тест 177 W3.1)
# · Remove if: early-return контракт меняется
@ldd_trajectory
def test_retry_success_first_attempt_no_backoff(caplog: pytest.LogCaptureFixture) -> None:
    """успех с первой попытки → 0 sleep, мгновенный возврат (нет лишних попыток)."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def _ok() -> str:
        calls["n"] += 1
        return "ok"

    result = retry(
        _ok,
        attempts=3,
        backoff_seconds=[5, 10, 20],
        retryable=lambda _: False,
        sleep_fn=sleeps.append,
    )
    assert result == "ok"
    assert calls["n"] == 1
    assert sleeps == [], f"успех на 1-й попытке → 0 sleep, got {sleeps}"
    logger.critical("[IMP:9][test] success on first attempt without backoff — OK (177 W3.1)")


# ═══════════════════════════════════════════════════════════════════════════
# exponential_backoff schedule + fail-fast валидация
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — exponential_backoff расписание
# · Scenario: base=2 (канон RETRY_BACKOFF_EXPONENTIAL_BASE) → [2, 4, 8]; max_seconds кап → [2, 4, 5]
# · Last fail: N/A (новый тест 177 W3.1)
# · Remove if: exponential_backoff контракт меняется
@ldd_trajectory
def test_exponential_backoff_schedule(caplog: pytest.LogCaptureFixture) -> None:
    """exponential_backoff: [base**1..base**retries], с потолком max_seconds."""
    assert exponential_backoff(3) == [2.0, 4.0, 8.0]
    assert exponential_backoff(3, max_seconds=5) == [2.0, 4.0, 5.0]
    assert exponential_backoff(0) == []
    logger.critical("[IMP:9][test] exponential_backoff schedule [2,4,8] / capped [2,4,5] — OK (177 W3.1)")


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — fail-fast валидация входов
# · Scenario: attempts=0 / пустой backoff → ConfigValidationError (fail-fast, конституция L0.3)
# · Last fail: N/A (новый тест 177 W3.1)
# · Remove if: валидация входов меняется
@ldd_trajectory
def test_retry_invalid_inputs_raise(caplog: pytest.LogCaptureFixture) -> None:
    """retry: attempts<1 / пустой backoff → ConfigValidationError (typed hierarchy, U-12)."""
    with pytest.raises(ConfigValidationError, match="attempts must be >= 1"):
        retry(lambda: None, attempts=0, backoff_seconds=[1], retryable=lambda _: False)
    with pytest.raises(ConfigValidationError, match="backoff_seconds must be non-empty"):
        retry(lambda: None, attempts=1, backoff_seconds=[], retryable=lambda _: False)
    logger.critical("[IMP:9][test] invalid inputs raise ConfigValidationError — OK (177 W3.1)")


# 🧪 TRAP[TEST] · 2026-08-16 · REGRESSION · 177 W3.1 — дефолты политик из timeouts
# · Scenario: DEFAULT_ATTEMPTS/DEFAULT_BACKOFF_SECONDS производны от RETRY_COUNT/RETRY_BACKOFF_SECONDS
# · Last fail: N/A (новый тест 177 W3.1 — политики из единого реестра, не литералы)
# · Remove if: политики осознанно переопределяются (обновить оба места)
@ldd_trajectory
def test_retry_defaults_from_timeouts(caplog: pytest.LogCaptureFixture) -> None:
    """Дефолты retry.py производны от timeouts-канона (RETRY_COUNT + RETRY_BACKOFF_SECONDS)."""
    from core.internal.shared import timeouts

    assert DEFAULT_ATTEMPTS == timeouts.RETRY_COUNT + 1
    assert list(DEFAULT_BACKOFF_SECONDS) == list(timeouts.RETRY_BACKOFF_SECONDS)
    logger.critical("[IMP:9][test] retry defaults derived from timeouts registry — OK (177 W3.1)")
