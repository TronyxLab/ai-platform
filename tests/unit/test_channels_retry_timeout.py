# GREP_SUMMARY: test-channels-retry-timeout, REF-0011, retryable, exit-code-124, timeout, no-retry, forced-command, FAIL-0700
# STRUCTURE: ▶ timeout(124) → ровно 1 attempt, 0 backoff │ ▶ failure(1→success) → ≥2 attempts (retry жив) │ ▶ OSError-исключение → retryable
# region MODULE_CONTRACT
## @purpose  Тесты политики ретрая каналов доставки (REF-0011 карточка): retryable =
##           not success AND exit_code != 124 — таймаут (ForcedCommandChannel TimeoutExpired
##           → 124) НЕ ретраится: deliver — POST-like операция, receive на VPS мог уже
##           применить payload; повтор = двойные compose-циклы/снапшоты либо ложный CI-red.
## @scope    unit; sleep_fn DI — 0 реального backoff.
## @invariants
##   - R1/R2: содержательные assert'ы на число попыток/вызовов backoff
## @rationale  $TEST_SPEC REF-0011: at-least-once retry поверх POST-like receive множил
##            полу-applied деплои при сетевых таймаутах (FAIL-0700).
## @changes  2026-08-24 · Created (REF-0011, meta-refactoring В1)
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.deploy.channels.base import DeliveryChannel, DeliveryResult, Payload
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


class _ScriptedChannel(DeliveryChannel):
    """Канал со сценарным deliver(): возвращает результаты по очереди, счётчик попыток."""

    def __init__(self, results: list[DeliveryResult]):
        super().__init__(timeout=5, sleep_fn=lambda _: None)
        self._results = list(results)
        self._last = DeliveryResult(success=False, error_message="empty-script", exit_code=1)
        self.attempts = 0

    def deliver(self, _payload: Payload) -> DeliveryResult:
        self.attempts += 1
        if self._results:
            self._last = self._results.pop(0)
        return self._last


def _payload(tmp_path) -> Payload:
    tar_path = tmp_path / "p.tar.gz"
    tar_path.write_bytes(b"tar")
    return Payload(tar_path=tar_path, project_name="proj")


@ldd_trajectory
def test_timeout_result_is_not_retried(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """REF-0011/FAIL-0700: exit_code=124 (timeout) → ровно ОДНА попытка, backoff не тикает."""
    caplog.set_level(logging.INFO)
    channel = _ScriptedChannel([
        DeliveryResult(success=False, error_message="timeout", exit_code=124),
    ])
    sleeps: list[float] = []
    channel._sleep_fn = sleeps.append  # type-ignore-free DI-шов (тот же атрибут, что __init__)

    result = channel._retry_deliver(_payload(tmp_path))

    assert result.success is False and result.exit_code == 124
    assert channel.attempts == 1, f"таймаут НЕ ретраится: попыток {channel.attempts}"
    assert sleeps == [], "backoff-sleep при таймауте не выполняется"
    logger.critical("[IMP:9][test] timeout (124) not retried: single attempt, zero backoff")


@ldd_trajectory
def test_regular_failure_still_retries_until_success(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Обычный сбой (exit_code=1) сохраняет ретрай-политику: 2-я попытка успешна."""
    caplog.set_level(logging.INFO)
    channel = _ScriptedChannel([
        DeliveryResult(success=False, error_message="ssh flake", exit_code=1),
        DeliveryResult(success=True, exit_code=0),
    ])
    sleeps: list[float] = []
    channel._sleep_fn = sleeps.append

    result = channel._retry_deliver(_payload(tmp_path))

    assert result.success is True
    assert channel.attempts == 2, f"ретрай жив для обычных сбоев: попыток {channel.attempts}"
    assert len(sleeps) >= 1, "между попытками был backoff"
    logger.critical("[IMP:9][test] regular failure retried to success (policy preserved)")


@ldd_trajectory
def test_exhausted_non_timeout_failures_stop_after_all_attempts(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Не-таймаутные сбои исчерпывают все попытки (1 + DEFAULT_RETRY_COUNT)."""
    from core.internal.deploy.channels.base import DEFAULT_RETRY_COUNT

    caplog.set_level(logging.INFO)
    failures = [DeliveryResult(success=False, error_message=f"fail#{i}", exit_code=1) for i in range(10)]
    channel = _ScriptedChannel(failures)

    result = channel._retry_deliver(_payload(tmp_path))

    assert result.success is False
    assert channel.attempts == 1 + DEFAULT_RETRY_COUNT, (
        f"ожидается 1 + {DEFAULT_RETRY_COUNT} попыток, было {channel.attempts}"
    )
    logger.critical("[IMP:9][test] non-timeout exhaustion uses full attempt budget")
