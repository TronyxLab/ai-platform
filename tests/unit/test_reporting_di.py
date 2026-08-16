# GREP_SUMMARY: test-reporting-di, send-telegram, notifier-param, telegram-notifier, DI, factory-injection, W4b, fake-notifier
# STRUCTURE: ▶ fake StateMachine (node/warnings/errors) + TELEGRAM_* env → ◇ reporting.send_telegram(sm, notifier=fake)
#           → ⊕ captured (message, token, chat, proxy) → ◇ missing token → skip (notifier НЕ вызван) → ⎋ asserts
# region MODULE_CONTRACT
## @purpose  Unit-тесты DI reporting.send_telegram (DevPlan 160 W4b T4.2): notifier параметром
##           с ленивым default — тест передаёт fake-notifier с ассертами, 0 патчей telegram_notifier.
## @scope    tests/unit — native imports; StateMachine заменяется SimpleNamespace-дублем (duck-типизация
##           reporting.send_telegram читает только sm.state.node/warnings/errors).
## @invariants
##   - TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не заданы → skip (notifier НЕ вызывается)
##   - notifier=fake получает (message, token, chat, proxy) — контракт _shared_send_telegram
##   - LDD: IMP:9 лог отправки/скипа (Anti-Illusion Rule)
## @rationale W4b (AF-2): reporting.send_telegram — потребитель фабрики send_telegram; параметр
##            notifier=None (ленивый default = shared) делает его тестируемым без monkeypatch.
## @changes  2026-08-13 | DevPlan 160 W4b — created (T4.2 send_telegram factory injection)
# endregion MODULE_CONTRACT

import logging
from types import SimpleNamespace

import pytest

from core.internal.bootstrap.lifecycle.helpers.reporting import send_telegram

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _fake_sm(node: str = "test-node", warnings: list[str] | None = None, errors: list[str] | None = None):
    """StateMachine-дубль: send_telegram читает только sm.state (duck-типизация)."""
    return SimpleNamespace(state=SimpleNamespace(node=node, warnings=warnings or [], errors=errors or []))


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · W4b — notifier=fake получает сообщение с контекстом
# · Scenario: TELEGRAM_* заданы → fake notifier вызывается ровно один раз с (msg, token, chat, proxy)
# · Last fail: N/A (new — DevPlan 160 W4b T4.2 send_telegram factory injection)
# · Remove if: reporting.send_telegram перестанет принимать notifier
def test_send_telegram_notifier_injected(caplog) -> None:
    """notifier=fake: вызывается с (message, token, chat, proxy); IMP:9 «Notification sent»."""
    caplog.set_level(logging.INFO)
    captured: dict = {}

    def _fake_notifier(message, bot_token=None, chat_id=None, proxy_url=None):
        captured.update(message=message, bot_token=bot_token, chat_id=chat_id, proxy_url=proxy_url)
        return True

    # DI (W-H): bot_token/chat_id параметрами (0 setenv); proxy_url из env — канон
    send_telegram(
        _fake_sm(node="node-a", warnings=["w1"]),
        notifier=_fake_notifier,
        bot_token="123:token",
        chat_id="-100base",
    )

    assert captured["bot_token"] == "123:token"
    assert captured["chat_id"] == "-100base"
    assert captured["proxy_url"] == "http://127.0.0.1:8118"
    assert "node-a" in captured["message"] and "w1" in captured["message"]
    assert any("[IMP:9]" in r.message and "Notification sent" in r.message for r in caplog.records), (
        "LDD: нет IMP:9 лога отправки"
    )
    logger.critical("[IMP:9][test] notifier-injected send — контракт (msg, token, chat, proxy) OK")


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · W4b — неблокирующий skip при отсутствии кредов
# · Scenario: TELEGRAM_BOT_TOKEN пуст → notifier НЕ вызывается, IMP:9 «notifications disabled»
# · Last fail: N/A (new — DevPlan 160 W4b)
# · Remove if: skip-семантика при отсутствии кредов меняется
def test_send_telegram_skip_without_creds(caplog, monkeypatch) -> None:
    """Креды не заданы → notifier не вызывается (non-fatal skip, IMP:9)."""
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    called: list[bool] = []

    def _fake_notifier(*_a, **_k):
        called.append(True)
        return True

    send_telegram(_fake_sm(), notifier=_fake_notifier)

    assert called == [], "notifier НЕ должен вызываться при отсутствии кредов"
    assert any("[IMP:9]" in r.message and "notifications disabled" in r.message for r in caplog.records), (
        "LDD: нет IMP:9 skip-лога"
    )
    logger.critical("[IMP:9][test] skip без кредов — notifier не вызван OK")


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · W4b — провал доставки не роняет lifecycle
# · Scenario: notifier вернул False → функция возвращается тихо (non-fatal, без raise)
# · Last fail: N/A (new — DevPlan 160 W4b)
# · Remove if: non-fatal контракт reporting-хелперов меняется
def test_send_telegram_notifier_failure_non_fatal(caplog) -> None:
    """notifier=False → no raise, no «Notification sent» (неблокирующий контракт)."""
    caplog.set_level(logging.INFO)

    send_telegram(_fake_sm(), notifier=lambda *_a, **_k: False, bot_token="123:token", chat_id="-100base")

    messages = [r.message for r in caplog.records]
    assert not any("Notification sent" in m for m in messages), "провал не должен логировать успех"
    logger.critical("[IMP:9][test] notifier-failure non-fatal — lifecycle не заблокирован OK")
