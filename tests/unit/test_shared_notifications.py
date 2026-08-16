# GREP_SUMMARY: test-shared-notifications notify-event envelope escape resolve-chat-id throttle dedup audit-fallback non-blocking notify-ci
# STRUCTURE: ┌Notification/envelope/escape/resolve SoT┐ → ◇ notify_event (throttle → resolve → envelope → send) → ◇ fallback/audit → ◇ CLI notify-ci env-контракт → ⎋ asserts (LDD IMP:9)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/notifications.py (DevPlan 003 B1/B2):
##           конверт, escape, resolve SoT, throttle, fallback, non-blocking (всегда True),
##           CLI notify-ci env-контракт. 0 реальных HTTP (send_fn DI).
## @scope    tests/unit — native imports; транспорт заменяется send_fn (TRAP 160 W4b:
##           инъекция в потребителя, не в транспорт); audit — audit_fn DI.
## @invariants
##   - notify_event ВСЕГДА возвращает True (non-blocking, AC-7 DevPlan 003)
##   - throttle: (event, fingerprint) → suppressed (send_fn НЕ вызывается)
##   - Провал доставки → audit-fallback вызывается + True (D-2)
##   - direct_https=True → proxy=None принудительно (TRAP[BUG] 141)
##   - safe default: env TELEGRAM_PROXY_URL → proxy (нода: Tor/Privoxy)
##   - LDD: IMP:9 логи отправки/провала (Anti-Illusion Rule)
## @rationale  DevPlan 003 §TEST_SPEC: конверт, escape, resolve SoT, throttle, fallback,
##             non-blocking — покрыты; R5-негативы для TRAP[BUG] 141 (direct на ноде).
## @changes  2026-08-16 | DevPlan 003 — created
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.shared.notifications import (
    Notification,
    escape_html,
    format_envelope,
    format_notify_message,
    main,
    notify_event,
    resolve_chat_id,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region FUNC_escape_envelope


# 🧪 TRAP[TEST] · Regression · Scenario: единый экранизатор escape_html (& < >; кавычки — нет)
# · Last fail: N/A (new — DevPlan 003 B1 инвариант 4)
# · Remove if: escape_html меняет контракт (html.escape quote=False)
def test_escape_html(caplog) -> None:
    """escape_html: & < > экранируются; кавычки НЕ (Telegram HTML-safe)."""
    caplog.set_level(logging.INFO)
    assert escape_html("a & b < c > d") == "a &amp; b &lt; c &gt; d"
    assert escape_html('"quoted"') == '"quoted"'
    logger.info("[IMP:9][test_notifications] escape_html contract PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: канонический HTML-конверт (badge+severity+context+details+footer)
# · Last fail: N/A (new — DevPlan 003 AC-4 единый конверт у всех отправителей)
# · Remove if: формат конверта сознательно меняется (snapshot)
def test_format_envelope_structure(caplog) -> None:
    """format_envelope: badge + [SEVERITY] + [context] + message + details + footer (ts/corr/link/action)."""
    caplog.set_level(logging.INFO)
    n = Notification(
        severity="critical",
        context="deploy",
        event="deploy.failed",
        message="Deploy <proj> FAILED",
        details=["container unhealthy (exit 137)"],
        corr_id="corr-abc",
        links=["https://example.com/run/1"],
        action="fix-forward: новый коммит",
        ts="2026-08-16T18:20:00Z",
    )
    envelope = format_envelope(n)
    assert "🚨" in envelope and "<b>[CRITICAL]</b>" in envelope
    assert "[deploy]" in envelope
    assert "Deploy &lt;proj&gt; FAILED" in envelope, "message должен быть escape_html"
    assert "• container unhealthy (exit 137)" in envelope
    assert "⏱ 2026-08-16T18:20:00Z" in envelope and "🪪 corr-abc" in envelope
    assert '<a href="https://example.com/run/1">' in envelope
    assert "💡 fix-forward: новый коммит" in envelope
    logger.info("[IMP:9][test_notifications] envelope structure PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: пустые секции конверта опускаются (info-минимум)
# · Last fail: N/A (new — DevPlan 003)
# · Remove if: формат конверта меняется
def test_format_envelope_minimal(caplog) -> None:
    """Конверт без details/links/action — без пустых футер-секций."""
    caplog.set_level(logging.INFO)
    n = Notification(severity="info", context="bootstrap", message="done", ts="")
    envelope = format_envelope(n)
    assert envelope == "✅ <b>[INFO]</b> [bootstrap] done"
    assert "🔗" not in envelope and "💡" not in envelope
    logger.info("[IMP:9][test_notifications] minimal envelope PASS")


# endregion FUNC_escape_envelope


# region FUNC_resolve_sot


# 🧪 TRAP[TEST] · Regression · Scenario: SoT severity→chat (critical/warning/info + fallback base)
# · Last fail: N/A (new — DevPlan 003 B2: единый SoT, parity-гейт против Grafana)
# · Remove if: resolve_chat_id SoT переезжает из notifications.py
def test_resolve_chat_id_sot(caplog) -> None:
    """resolve_chat_id: critical→_CRITICAL, warning→_WARNING, info→base, fallback→base, unknown→base."""
    caplog.set_level(logging.INFO)
    env = {
        "TELEGRAM_CHAT_ID": "-100base",
        "TELEGRAM_CHAT_ID_CRITICAL": "-100crit",
        "TELEGRAM_CHAT_ID_WARNING": "-100warn",
    }
    assert resolve_chat_id("critical", env) == "-100crit"
    assert resolve_chat_id("warning", env) == "-100warn"
    assert resolve_chat_id("info", env) == "-100base"
    assert resolve_chat_id("unknown", env) == "-100base"  # unknown → base (канон E10)
    # fallback: critical без dedicated → base
    assert resolve_chat_id("critical", {"TELEGRAM_CHAT_ID": "-100base"}) == "-100base"
    # нерезолвится → None
    assert resolve_chat_id("critical", {}) is None
    logger.info("[IMP:9][test_notifications] resolve_chat_id SoT PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: backward-compat форматтер format_notify_message
# · Last fail: N/A (new — DevPlan 003 B3a shim)
# · Remove if: format_notify_message удаляется (все потребители на format_envelope)
def test_format_notify_message_compat(caplog) -> None:
    """format_notify_message: "[ctx] emoji msg" / голый emoji (backward-compat)."""
    caplog.set_level(logging.INFO)
    assert format_notify_message("✅", "hello", "platform") == "[platform] ✅ hello"
    assert format_notify_message("✅", "", "platform") == "✅"
    logger.info("[IMP:9][test_notifications] format_notify_message compat PASS")


# endregion FUNC_resolve_sot


# region FUNC_notify_event


# 🧪 TRAP[TEST] · Regression · Scenario: notify_event success — send_fn с конвертом/token/chat/proxy
# · Last fail: N/A (new — DevPlan 003 AC-3: единая точка отправки)
# · Remove if: notify_event меняет транспортный контракт
def test_notify_event_success(caplog) -> None:
    """notify_event: send_fn получает envelope (HTML) + creds; IMP:9 sent; registry обновлён."""
    caplog.set_level(logging.INFO)
    captured: dict = {}

    def _fake_send(envelope, bot_token=None, chat_id=None, proxy_url=None, parse_mode=None):
        captured.update(
            envelope=envelope, bot_token=bot_token, chat_id=chat_id, proxy_url=proxy_url, parse_mode=parse_mode
        )
        return True

    registry: dict = {}
    n = Notification(
        severity="critical",
        context="deploy",
        event="deploy.failed",
        message="boom",
        corr_id="c1",
    )
    result = notify_event(
        n,
        env={
            "TELEGRAM_BOT_TOKEN": "123:t",
            "TELEGRAM_CHAT_ID_CRITICAL": "-100c",
            "TELEGRAM_PROXY_URL": "http://127.0.0.1:8118",
        },
        send_fn=_fake_send,
        throttle_registry=registry,
        now=1_000_000.0,
    )

    assert result is True, "notify_event всегда True (AC-7)"
    assert captured["bot_token"] == "123:t"
    assert captured["chat_id"] == "-100c"
    assert captured["proxy_url"] == "http://127.0.0.1:8118", "safe default: env TELEGRAM_PROXY_URL (Tor)"
    assert captured["parse_mode"] == "HTML"
    assert "<b>[CRITICAL]</b>" in captured["envelope"]
    assert registry.get(("deploy.failed", "boom")) == 1_000_000.0, "throttle-реестр обновляется при успехе"
    assert any("[IMP:9]" in r.message and "Notification sent" in r.message for r in caplog.records), "LDD: IMP:9 sent"
    logger.info("[IMP:9][test_notifications] notify_event success PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: non-blocking без кредов — True, send_fn НЕ вызывается
# · Last fail: N/A (new — DevPlan 003 AC-7)
# · Remove if: non-blocking контракт меняется
def test_notify_event_missing_creds_non_blocking(caplog) -> None:
    """Нет токена → True (skip), send_fn не вызывается (уведомление не блокирует операцию)."""
    caplog.set_level(logging.INFO)
    called: list[bool] = []
    result = notify_event(
        Notification(severity="critical", event="deploy.failed", message="x"),
        env={},  # без TELEGRAM_BOT_TOKEN
        send_fn=lambda *_a, **_k: called.append(True) or True,
    )
    assert result is True
    assert called == [], "send_fn не должен вызываться без токена"
    logger.info("[IMP:9][test_notifications] missing-creds non-blocking PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: throttle — повтор (event, fingerprint) в окне suppressed
# · Last fail: N/A (new — DevPlan 003 B1: throttle/dedup)
# · Remove if: throttle-контракт меняется
def test_notify_event_throttle_suppression(caplog) -> None:
    """Повтор (event, fingerprint) в окне → IMP:8 suppressed, send_fn НЕ вызывается."""
    caplog.set_level(logging.INFO)
    registry: dict = {("deploy.failed", "boom"): 1_000_000.0}
    called: list[bool] = []
    result = notify_event(
        Notification(severity="critical", event="deploy.failed", message="boom"),
        env={"TELEGRAM_BOT_TOKEN": "123:t", "TELEGRAM_CHAT_ID_CRITICAL": "-100c"},
        send_fn=lambda *_a, **_k: called.append(True) or True,
        throttle_registry=registry,
        now=1_000_100.0,  # +100s < 3600s окно
    )
    assert result is True
    assert called == [], "throttle: повтор suppressed"
    assert any("SUPPRESSED" in r.message for r in caplog.records), "LDD: IMP:8 suppressed"
    logger.info("[IMP:9][test_notifications] throttle suppression PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: throttle-окно из каталога (throttle_min) применяется
# · Last fail: N/A (new — DevPlan 003 B4: catalog-driven throttle)
# · Remove if: catalog-интеграция убирается
def test_notify_event_catalog_throttle_window(caplog) -> None:
    """Каталог (throttle_min=30) → окно 1800s; за окном — повторная отправка."""
    caplog.set_level(logging.INFO)
    registry: dict = {("ci.failure", "same"): 1_000_000.0}
    calls: list[int] = []
    # +3600s (> 1800s окно из каталога) → отправка разрешена
    notify_event(
        Notification(severity="critical", event="ci.failure", message="same"),
        env={"TELEGRAM_BOT_TOKEN": "123:t", "TELEGRAM_CHAT_ID_CRITICAL": "-100c"},
        send_fn=lambda *_a, **_k: calls.append(1) or True,
        throttle_registry=registry,
        now=1_003_600.0,
    )
    assert len(calls) == 1, "за каталог-окном (throttle_min=30 → 1800s) повтор разрешён"
    logger.info("[IMP:9][test_notifications] catalog throttle window PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: провал доставки → audit-fallback + True (D-2)
# · Last fail: N/A (new — DevPlan 003 B1: audit-fallback при провале)
# · Remove if: fallback-контракт меняется
def test_notify_event_delivery_failure_audit_fallback(caplog) -> None:
    """send_fn=False → audit-fallback вызывается, результат True, реестр НЕ обновляется."""
    caplog.set_level(logging.INFO)
    audited: list[tuple] = []

    def _audit(n, reason):
        audited.append((n.event, reason))

    registry: dict = {}
    result = notify_event(
        Notification(severity="critical", event="deploy.rollback", message="rb"),
        env={"TELEGRAM_BOT_TOKEN": "123:t", "TELEGRAM_CHAT_ID_CRITICAL": "-100c"},
        send_fn=lambda *_a, **_k: False,
        audit_fn=_audit,
        throttle_registry=registry,
        now=5.0,
    )
    assert result is True, "провал доставки не блокирует операцию"
    assert len(audited) == 1 and audited[0][0] == "deploy.rollback", "audit-fallback записан"
    assert registry == {}, "реестр НЕ обновляется при провале (retry возможен)"
    assert any("DELIVERY FAILED" in r.message for r in caplog.records), "LDD: IMP:9 DELIVERY FAILED"
    logger.info("[IMP:9][test_notifications] audit-fallback PASS")


# 🧪 TRAP[TEST] · Regression · R5-негатив TRAP[BUG] 141: direct_https=True → proxy=None принудительно
# · Scenario: env содержит TELEGRAM_PROXY_URL, но direct_https=True (CI) → send_fn получает proxy=None
# · Last fail: N/A (new — DevPlan 003: прямой HTTPS только из CI, утечка IP ноды запрещена)
# · Remove if: direct_https-контракт меняется
def test_notify_event_direct_https_ci_only(caplog) -> None:
    """direct_https=True → proxy=None, даже если env задаёт TELEGRAM_PROXY_URL (TRAP[BUG] 141)."""
    caplog.set_level(logging.INFO)
    captured: dict = {}

    def _fake_send(envelope, bot_token=None, chat_id=None, proxy_url=None, parse_mode=None):
        captured["proxy_url"] = proxy_url
        return True

    notify_event(
        Notification(severity="critical", event="ci.failure", message="gate red"),
        env={
            "TELEGRAM_BOT_TOKEN": "123:t",
            "TELEGRAM_CHAT_ID_CRITICAL": "-100c",
            "TELEGRAM_PROXY_URL": "http://127.0.0.1:8118",  # ловушка: CI не должен взять прокси
        },
        direct_https=True,
        send_fn=_fake_send,
    )
    assert captured["proxy_url"] is None, "direct_https=True: прокси принудительно None (CI)"
    logger.info("[IMP:9][test_notifications] direct-https CI-only PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: severity нормализуется (unknown → info)
# · Last fail: N/A (new — DevPlan 003 инвариант severity)
# · Remove if: нормализация severity меняется
def test_notify_event_severity_normalization(caplog) -> None:
    """Unknown severity → info (IMP:7 warning) — chat резолвится из base."""
    caplog.set_level(logging.INFO)
    captured: dict = {}
    notify_event(
        Notification(severity="bogus", event="deploy.success", message="m"),
        env={"TELEGRAM_BOT_TOKEN": "123:t", "TELEGRAM_CHAT_ID": "-100base"},
        send_fn=lambda _envelope, **_k: captured.update(chat=_k.get("chat_id")) or True,
    )
    assert captured.get("chat") == "-100base", "unknown severity → info → base chat"
    assert any("normalized to info" in r.message for r in caplog.records)
    logger.info("[IMP:9][test_notifications] severity normalization PASS")


# endregion FUNC_notify_event


# region FUNC_cli


# 🧪 TRAP[TEST] · Regression · Scenario: CLI notify-ci env-контракт (NOTIFY_* + TELEGRAM_*)
# · Last fail: N/A (new — DevPlan 003 A1: composite action вызывает notify-ci)
# · Remove if: notify-ci CLI контракт меняется
def test_cli_notify_ci_env_contract(caplog, monkeypatch) -> None:
    """notify-ci: NOTIFY_* env → Notification (event/severity/context); exit 0 всегда."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("NOTIFY_SEVERITY", "critical")
    monkeypatch.setenv("NOTIFY_CONTEXT", "core-deploy")
    monkeypatch.setenv("NOTIFY_EVENT", "ci.failure")
    monkeypatch.setenv("NOTIFY_MESSAGE", "deploy failed")
    monkeypatch.setenv("NOTIFY_CORR_ID", "run-42")

    captured: list[Notification] = []
    rc = main(["notify-ci"], notify_fn=lambda n: captured.append(n) or True)

    assert rc == 0, "notify-ci exit 0 (non-blocking)"
    assert len(captured) == 1
    n = captured[0]
    assert n.event == "ci.failure" and n.severity == "critical"
    assert n.context == "core-deploy" and n.message == "deploy failed" and n.corr_id == "run-42"
    logger.info("[IMP:9][test_notifications] notify-ci env contract PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: notify-ci с argv (без NOTIFY_* env) — аргументы
# · Last fail: N/A (new — DevPlan 003)
# · Remove if: notify-ci CLI контракт меняется
def test_cli_notify_ci_argv_args(caplog, monkeypatch) -> None:
    """notify-ci без NOTIFY_* env: значения из argv (event/severity/message)."""
    caplog.set_level(logging.INFO)
    for var in ("NOTIFY_SEVERITY", "NOTIFY_EVENT", "NOTIFY_MESSAGE", "NOTIFY_CONTEXT", "NOTIFY_CORR_ID"):
        monkeypatch.delenv(var, raising=False)
    captured: list[Notification] = []
    rc = main(
        [
            "notify-ci",
            "--severity",
            "warning",
            "--context",
            "hermes-nightly",
            "--event",
            "ci.build_failed",
            "build red",
        ],
        notify_fn=lambda n: captured.append(n) or True,
    )
    assert rc == 0
    n = captured[0]
    assert n.event == "ci.build_failed" and n.severity == "warning" and n.message == "build red"
    logger.info("[IMP:9][test_notifications] notify-ci argv PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: notify-ci details split из NOTIFY_DETAILS (newline)
# · Last fail: N/A (new — DevPlan 003 A1: action передаёт details)
# · Remove if: NOTIFY_DETAILS контракт меняется
def test_cli_notify_ci_details_env(caplog, monkeypatch) -> None:
    """NOTIFY_DETAILS (newline-separated) → details список."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("NOTIFY_DETAILS", "line one\nline two")
    captured: list[Notification] = []
    main(["notify-ci"], notify_fn=lambda n: captured.append(n) or True)
    assert captured[0].details == ["line one", "line two"]
    logger.info("[IMP:9][test_notifications] notify-ci details PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: CLI notify — missing secrets → exit 0 (не блокирует)
# · Last fail: N/A (new — DevPlan 003)
# · Remove if: notify CLI non-blocking контракт меняется
def test_cli_notify_non_blocking_missing_secrets(caplog, monkeypatch, tmp_path) -> None:
    """notify без токена → exit 0 (non-blocking), IMP:7 skip."""
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    secrets = tmp_path / "secrets.env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
    rc = main(["notify", "--secrets-file", str(secrets), "--event", "bootstrap.report", "msg"])
    assert rc == 0, "notify exit 0 даже без токена (non-blocking)"
    logger.info("[IMP:9][test_notifications] notify non-blocking PASS")


# endregion FUNC_cli


# region FUNC_legacy_shim


# 🧪 TRAP[TEST] · Regression · Scenario: telegram_notifier shim — resolve_chat_id делегирует SoT
# · Last fail: N/A (new — DevPlan 003 B3a: shim над notifications)
# · Remove if: telegram_notifier shim удаляется (все потребители на notifications)
def test_telegram_notifier_shim_delegation(caplog) -> None:
    """telegram_notifier.resolve_chat_id/escape_html — shim: поведение идентично SoT."""
    caplog.set_level(logging.INFO)
    from core.internal.shared.telegram_notifier import escape_html as tn_escape
    from core.internal.shared.telegram_notifier import resolve_chat_id as tn_resolve

    assert tn_resolve("critical", {"TELEGRAM_CHAT_ID_CRITICAL": "-1", "TELEGRAM_CHAT_ID": "-2"}) == "-1"
    assert tn_resolve("info", {"TELEGRAM_CHAT_ID": "-2"}) == "-2"
    assert tn_escape("<b>") == "&lt;b&gt;"
    logger.info("[IMP:9][test_notifications] telegram_notifier shim PASS")


# endregion FUNC_legacy_shim
