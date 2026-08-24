"""
# GREP_SUMMARY: test-notify-severity-mapping, TEST-04, severity, critical, info, notify-hook, telegram, REF-0003, post-deploy-chain
# STRUCTURE: ▶ _notify_hook(status) → ◇ status∈{FAILED,ROLLBACK}? → ⎋ (--severity critical + 💥)
#            │ иначе → ⎋ (--severity info + 🚀) │ ▶ notify_deploy_failure → всегда critical
# region MODULE_CONTRACT
## @purpose  TEST-04 (карточка REF-0003): severity-mapping уведомлений deploy-канала.
##           FAILED/ROLLBACK → --severity critical (💥 «healthcheck-rollback»); DEPLOYED →
##           info (🚀); PARTIAL → legacy-info (внутренний статус, в prod-notify больше не
##           попадает после REF-0003). notify_deploy_failure — единственная точка critical-пуша
##           unhealthy/timeout-ветки receive (без catalog/reconfig/hooks поверх больного деплоя).
## @scope    core/internal/deploy/hooks/post_deploy_chain.py: _notify_hook + notify_deploy_failure.
## @invariants
##   - Native fake-runner DI (run_cmd) — 0 subprocess, 0 setattr
##   - notify-hook best-effort контракт не нарушается: runner-исключение → WARN, не raise
##   - LDD: IMP:9 на успешной отправке (Semantic Trace Verification)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import logging

import pytest

from core.internal.deploy.hooks.post_deploy_chain import _notify_hook, notify_deploy_failure

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


class _RecordingRunner:
    """Fake subprocess-канал (W-H): записывает argv вызовов, возвращает CompletedProcess-подобный no-op."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kwargs: object) -> None:
        self.calls.append(list(cmd))


# 🧪 TRAP[TEST] · 2026-08-24 · unit · TEST-04 — severity-mapping статуса деплоя
# · Regression: карточка REF-0003 «notify severity=critical на unhealthy-ветку»;
# ·   до DevPlan 003 A4 существовал только success/info-путь
# · Scenario: таблица маппинга исходов на severity канала:
# ·   DEPLOYED→info/🚀, PARTIAL→info (legacy; внутренний, в prod-канал не попадает),
# ·   FAILED→critical/💥, ROLLBACK→critical/💥 («Deploy X — healthcheck-rollback»)
# · Last fail: N/A для самой таблицы (mapping был); red — отсутствие critical-вызова из receive
# · Remove if: severity-словарь канала Telegram расширяется (warning-канал) — пересмотреть таблицу
@pytest.mark.parametrize(
    ("status", "expected_severity", "expected_emoji"),
    [
        ("DEPLOYED", "info", "🚀"),
        ("PARTIAL", "info", "🚀"),
        ("FAILED", "critical", "💥"),
        ("ROLLBACK", "critical", "💥"),
    ],
)
def test_notify_hook_severity_mapping(
    status: str,
    expected_severity: str,
    expected_emoji: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Маппинг исхода деплоя на severity канала уведомлений (TEST-04)."""
    caplog.set_level(logging.INFO)
    runner = _RecordingRunner()

    _notify_hook(runner, "notify-hook.sh", "proj", "v1", status)

    assert len(runner.calls) == 1, "notify-hook вызывается ровно один раз"
    cmd = runner.calls[0]
    assert "--severity" in cmd, f"argv обязан нести --severity: {cmd}"
    assert cmd[cmd.index("--severity") + 1] == expected_severity
    assert expected_emoji in cmd, f"emoji {expected_emoji} ожидался в argv: {cmd}"
    assert "[IMP:9]" in caplog.text, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · 2026-08-24 · unit · REF-0003 — notify_deploy_failure = критичный пуш failed-healthcheck
# · Regression: карточка REF-0003 «Telegram severity=critical» на unhealthy-ветке receive
# · Scenario: notify_deploy_failure(project, version, "FAILED") → argv с --severity critical,
# ·   💥 и сообщением о healthcheck-rollback; сбой runner'а → WARN non-fatal (best-effort D4)
# · Last fail: функции не существовало (red: ImportError)
# · Remove if: critical-пуш переезжает в выделенный notifier-сервис
def test_notify_deploy_failure_critical(caplog: pytest.LogCaptureFixture) -> None:
    """notify_deploy_failure шлёт critical c 💥 и best-effort WARN при сбое канала."""
    caplog.set_level(logging.INFO)
    runner = _RecordingRunner()

    notify_deploy_failure("proj", "abc123", "FAILED", run_cmd=runner, platform_root_override="/nonexistent-root")

    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[cmd.index("--severity") + 1] == "critical"
    assert "💥" in cmd
    assert any("FAILED" in arg for arg in cmd), f"статус FAILED в сообщении: {cmd}"

    # Best-effort контракт: сбой runner → WARN, НЕ raise (D4)
    channel_down = OSError("notify channel down")

    def _boom(_cmd: list[str], **_kwargs: object) -> None:
        raise channel_down

    notify_deploy_failure("proj", "abc123", "FAILED", run_cmd=_boom, platform_root_override="/nonexistent-root")
    assert "WARN" in caplog.text or "non-fatal" in caplog.text.lower(), "сбой notify ожидает WARN (IMP:8)"
    logger.info("--- LDD TRAJECTORY: severity mapping + failure notify OK ---")
