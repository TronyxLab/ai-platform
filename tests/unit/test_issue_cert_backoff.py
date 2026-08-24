"""
# GREP_SUMMARY: test issue-cert backoff shared-retry exponential sleep-fn acme retry attempts rate-limit REF-0008
# STRUCTURE: ▶ scripted runner (fail×2 → success) + sleep-recorder → ◇ _acme_issue_with_retry: 3 attempts,
#            sleeps=[5,10] (DEFAULT_BACKOFF_SECONDS clamp) → ◇ exhaustion → last rc + FAIL ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Backoff unit-тесты ACME retry (REF-0008 подпункт 5): _acme_issue_with_retry делегирует
##           в shared/retry.retry — sleep/backoff между attempts (DEFAULT_BACKOFF_SECONDS [5,10,20],
##           clamp-last). Прежде retry жёг Let's Encrypt rate-limit (50 certs/domain/week) без пауз.
## @scope    issue_cert._acme_issue_with_retry через _issue_acme_generic (DI runner/sleep_fn).
## @invariants
##   - Пауза перед retry N: backoff_seconds[N-1], clamp на последний элемент (shared/retry канон)
##   - Успех на попытке K → K-1 sleep'ов; исчерпание → last rc ≠ 0, FAIL log_step
##   - Все subprocess через runner DI; sleep через sleep_fn DI (0 реального ожидания)
## @rationale REF-0008: ACME-retry без backoff = rate-limit burn → выпуск wildcard невозможен неделю
## @changes  2026-08-24 | REF-0008 (meta-refactoring В2) — Created
# endregion MODULE_CONTRACT
"""

import logging
import subprocess as _sp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"))

import issue_cert
import pytest

from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


class _ScriptedAcmeRunner:
    """Runner: первые fail_times вызовов acme.sh --issue → rc=1, далее rc=0. Прочее — rc=0."""

    def __init__(self, *, fail_times: int) -> None:
        self.fail_times = fail_times
        self.issue_calls = 0

    def run(self, cmd: list[str], **kwargs):  # ruff: ignore[ARG002] — DI fake канон
        if "acme.sh" in cmd[0] and "--issue" in cmd:
            self.issue_calls += 1
            rc = 1 if self.issue_calls <= self.fail_times else 0
            return _sp.CompletedProcess(cmd, rc, "", "")
        return _sp.CompletedProcess(cmd, 0, "", "")


def _make_ctx(
    tmp_path: Path, runner: _ScriptedAcmeRunner, sleeps: list[float], max_attempts: int
) -> issue_cert.IssueContext:
    """IssueContext с acme.sh-заглушкой и sleep-рекордером (backoff наблюдаем)."""
    acme_home = tmp_path / "acme"
    acme_home.mkdir(parents=True, exist_ok=True)
    acme_sh = acme_home / "acme.sh"
    acme_sh.write_text("#!/bin/sh\necho mock\n", encoding="utf-8")
    acme_sh.chmod(0o755)
    return issue_cert.IssueContext(
        runner=runner,
        facts=issue_cert.default_env_facts(),
        environ={},
        acme_home=str(acme_home),
        letsencrypt_dir=str(tmp_path / "le"),
        tmp_dir=str(tmp_path),
        max_attempts=max_attempts,
        sleep_fn=sleeps.append,
    )


@ldd_trajectory
def test_acme_backoff_between_attempts_then_success(caplog, tmp_path: Path) -> None:
    """fail×2 → success: 3 попытки, sleeps == [5.0, 10.0] (канон DEFAULT_BACKOFF_SECONDS)."""
    caplog.set_level(logging.INFO)
    runner = _ScriptedAcmeRunner(fail_times=2)
    sleeps: list[float] = []
    ctx = _make_ctx(tmp_path, runner, sleeps, max_attempts=3)

    ok = issue_cert._issue_acme_generic("app.example.com", "admin@example.com", "regru", wildcard=False, ctx=ctx)

    assert ok is True, "третья попытка должна быть успешной"
    assert runner.issue_calls == 3, f"ожидалось 3 попытки, got {runner.issue_calls}"
    assert sleeps == [5.0, 10.0], f"backoff между attempts обязателен (REF-0008): {sleeps}"
    logger.critical("[IMP:9][test] backoff: 2 паузы [5,10] между 3 attempts — rate-limit защищён")


@ldd_trajectory
def test_acme_exhaustion_returns_last_rc(caplog, tmp_path: Path) -> None:
    """Исчерпание attempts: все попытки fail → False, ровно max_attempts-1 sleep, FAIL log."""
    caplog.set_level(logging.INFO)
    runner = _ScriptedAcmeRunner(fail_times=99)
    sleeps: list[float] = []
    ctx = _make_ctx(tmp_path, runner, sleeps, max_attempts=2)

    ok = issue_cert._issue_acme_generic("app.example.com", "admin@example.com", "regru", wildcard=False, ctx=ctx)

    assert ok is False
    assert runner.issue_calls == 2, "ровно max_attempts попыток"
    assert sleeps == [5.0], f"одна пауза перед единственным retry: {sleeps}"
    assert any("FAIL" in r.message and "generic dns_regru" in r.message for r in caplog.records), (
        "FAIL log_step после исчерпания обязателен"
    )
    logger.critical("[IMP:9][test] exhaustion: last_rc≠0 → False, FAIL залогирован")


@ldd_trajectory
def test_acme_first_attempt_success_no_sleep(caplog, tmp_path: Path) -> None:
    """Успех с первой попытки: 0 sleep (backoff только между attempts)."""
    caplog.set_level(logging.INFO)
    runner = _ScriptedAcmeRunner(fail_times=0)
    sleeps: list[float] = []
    ctx = _make_ctx(tmp_path, runner, sleeps, max_attempts=3)

    ok = issue_cert._issue_acme_generic("app.example.com", "admin@example.com", "regru", wildcard=False, ctx=ctx)

    assert ok is True
    assert sleeps == [], "успешная первая попытка не должна спать"
    logger.critical("[IMP:9][test] happy path: 0 sleep")
