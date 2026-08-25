#!/usr/bin/env python3
# GREP_SUMMARY: test issue-cert backoff rate-limit fail-fast no-retry LE 429 acme retry sleep DI
# STRUCTURE: ▶ fake-runner(rc≠0 + rate-limit text) → ◇ _acme_issue_with_retry → ⊕ ровно 1 attempt, 0 sleeps → ⎋ last_rc≠0 │ ▶ обычный сбой → 2 attempts (regression guard)
# region MODULE_CONTRACT
## @purpose  QA R11/T2.F (DevPlan 14): rate-limit ответ Let's Encrypt (HTTP 429 / «rate limit»)
##           → fail-fast БЕЗ второй попытки и backoff — повтор жжёт тот же лимит и усиливает блок.
## @scope    core/internal/bootstrap/issue_cert.py::_acme_issue_with_retry (shared/retry канон).
## @invariants
##   - rate-limit ветка: attempts==1, sleep_fn не вызван, last_rc != 0
##   - обычный transient-сбой: attempts==max_attempts (регрессия против over-blocking)
##   - DI: fake runner + sleep_fn recorder — 0 реальных acme.sh/sleep вызовов
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import types

import pytest
from _conftest.ldd import ldd_trajectory

from core.internal.bootstrap import issue_cert as ic

logger = logging.getLogger(__name__)


class _FakeRunner:
    """Fake CommandRunner: возвращает заданный stdout/stderr/rc, считает вызовы."""

    def __init__(self, *, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.calls = 0
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    def run(self, cmd: list[str], timeout: int = 30, check: bool = False):  # ruff: ignore[ARG002]
        self.calls += 1
        return types.SimpleNamespace(returncode=self._returncode, stdout=self._stdout, stderr=self._stderr)


_RATE_LIMIT_STDERR = (
    "[Wed Aug 26 01:00:00 UTC 2026] Register account Error: "
    "urllib.error.HTTPError: HTTP Error 429: Too Many Requests — Rate limit exceeded"
)


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R11/T2.F — rate-limit → fail-fast без повтора
# · Scenario: первая попытка получает 429/rate-limit → прежний retryable=rc!=0 запускал вторую
#   попытку через backoff — повтор жёг тот же лимит и усиливал блок аккаунта/домена
# · Last fail: 2026-08-25 (REGRESSIONS.md R11) — retryable не знал о классе ошибки
# · Remove if: retry-политика переедет в shared/retry с классификацией исключений
@ldd_trajectory
def test_rate_limit_no_retry(caplog: pytest.LogCaptureFixture) -> None:
    """Rate-limit ответ → ровно 1 attempt, 0 backoff-sleep, rc != 0."""
    caplog.set_level(logging.INFO)
    runner = _FakeRunner(returncode=1, stderr=_RATE_LIMIT_STDERR)
    sleeps: list[float] = []
    ctx = ic.IssueContext(
        runner=runner,
        acme_home="/tmp/fake-acme-home",
        max_attempts=ic.ISSUE_MAX_ATTEMPTS,
        sleep_fn=sleeps.append,
    )

    last_rc = ic._acme_issue_with_retry(
        ctx=ctx,
        email="ops@example.com",
        domains=["d.example.com"],
        extra_args=["--standalone"],
        log_step="T2F",
        warn_fn=lambda rc, att: f"warn {rc} attempt {att}",
        fail_fn=lambda rc: f"fail {rc}",
    )

    assert runner.calls == 1, f"R11 FAIL: rate-limit обязан дать fail-fast, было попыток: {runner.calls}"
    assert sleeps == [], f"R11 FAIL: backoff при rate-limit запрещён (повтор жжёт лимит): {sleeps}"
    assert last_rc != 0, "rate-limit должен завершиться неудачей (без self-signed маскировки здесь)"
    assert any("rate limit" in r.message.lower() for r in caplog.records), (
        "ожидается IMP FAIL-лог о детекции rate limit"
    )
    logger.info("[IMP:9][test][backoff] rate-limited attempt=%d sleeps=%d rc=%s", runner.calls, len(sleeps), last_rc)


# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · обычный transient-сбой по-прежнему ретраится
# · Regression: защита от over-blocking — ужесточение retryable не должно убить легитимный retry
# · Last fail: N/A (preventive)
# · Remove if: вместе с rate-limit детектором
@ldd_trajectory
def test_transient_failure_still_retries(caplog: pytest.LogCaptureFixture) -> None:
    """Обычный сбой (не rate-limit) → max_attempts попыток + backoff-sleep между ними."""
    caplog.set_level(logging.INFO)
    runner = _FakeRunner(returncode=1, stderr="[err] webroot check failed, port busy")
    sleeps: list[float] = []
    ctx = ic.IssueContext(
        runner=runner,
        acme_home="/tmp/fake-acme-home",
        max_attempts=2,
        sleep_fn=sleeps.append,
    )

    last_rc = ic._acme_issue_with_retry(
        ctx=ctx,
        email="ops@example.com",
        domains=["d.example.com"],
        extra_args=["--standalone"],
        log_step="T2F",
        warn_fn=lambda rc, att: f"warn {rc} attempt {att}",
        fail_fn=lambda rc: f"fail {rc}",
    )

    assert runner.calls == 2, f"transient-сбой обязан ретраиться: attempts={runner.calls}"
    assert len(sleeps) == 1, f"между попытками ожидается один backoff-sleep: {sleeps}"
    assert last_rc != 0
    logger.info("[IMP:9][test][backoff] transient failure retried: attempts=%d", runner.calls)
