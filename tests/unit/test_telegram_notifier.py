# GREP_SUMMARY: test-telegram-notifier send-telegram telegram-alert shared-telegram mocked-urllib
# STRUCTURE: ┌mock urlopen┐ → ○ test scenarios: success → missing_token → missing_chat → env_fallback → proxy → http_error → network_error
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/telegram_notifier.py
##           Verifies send_telegram() with mocked urllib.request.urlopen.
## @scope    Tests: successful send, missing credentials, env var fallback,
##           proxy configuration, HTTP error, network error, failure-маркеры (132 W4, 126 D-2).
## @invariants
##   - All tests use unittest.mock (no real HTTP calls)
##   - No Docker dependency (pure Python unit tests)
##   - LDD: at least one IMP:9 log in successful send
##   - Все failure-пути: IMP:9 DELIVERY FAILED маркер (126 D-2, DevPlan 132 W4)
##   - R5: негативные тесты с оригинальной формой (urlerror/http_non200/notify-fail)
##   - No hardcoded paths anywhere
# endregion MODULE_CONTRACT

import logging
import os
import urllib.error
import urllib.request
from http.client import HTTPMessage
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from core.internal.shared.telegram_notifier import (
    format_notify_message,
    notify,
    resolve_chat_id,
    send_telegram,
)

logger = logging.getLogger(__name__)

# region FUNC_test_send_telegram_mocked


def test_send_telegram_mocked(caplog: pytest.LogCaptureFixture) -> None:
    """send_telegram with mocked urlopen returns True on HTTP 200.

    Also verifies correct POST data (chat_id, text) was sent to the
    correct Telegram API endpoint.
    """
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: successful Telegram send via mocked urlopen
    # · Last fail: N/A (new test)
    # · Remove if: send_telegram changes HTTP transport

    bot_token = "123456:test-token"
    chat_id = "-987654321"
    message = "Hello from test"

    # ── Mock OpenerDirector.open ──
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = None
    mock_response.read.return_value = b'{"ok": true}'
    mock_response.headers = HTTPMessage()

    captured_request: list[urllib.request.Request] = []

    def fake_open(
        req: urllib.request.Request,
        timeout: int = 30,
    ) -> MagicMock:
        captured_request.append(req)
        return mock_response

    with patch.object(urllib.request.OpenerDirector, "open", side_effect=fake_open):
        result = send_telegram(
            message=message,
            bot_token=bot_token,
            chat_id=chat_id,
        )

    # ── LDD trajectory ──
    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")

    # ── Verify result ──
    assert result is True, "send_telegram must return True on HTTP 200"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    # ── Verify request construction ──
    assert len(captured_request) == 1, "Exactly one HTTP request must be made"
    req = captured_request[0]
    assert req.method == "POST", "Must use POST method"
    # Verify Content-Type header (Python 3.14 normalizes header names to Camel-Case)
    header_items = dict(req.header_items())
    assert header_items.get("Content-type") == "application/x-www-form-urlencoded"
    assert f"bot{bot_token}/sendMessage" in req.full_url, "URL must point to sendMessage endpoint"

    # Verify POST body contains chat_id and text
    body = req.data.decode("ascii") if req.data else ""
    assert "chat_id=-987654321" in body, "POST body must contain chat_id"
    assert "text=Hello%20from%20test" in body, "POST body must contain URL-encoded text (percent-encoded)"


# endregion


# region FUNC_test_send_telegram_missing_bot_token


def test_send_telegram_missing_bot_token(caplog: pytest.LogCaptureFixture) -> None:
    """send_telegram returns False when bot_token is missing and not in env."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: missing bot_token returns False + DELIVERY FAILED marker
    # · Last fail: N/A (new test — DevPlan 132 W4: missing creds → IMP:9 marker, 126 D-2)
    # · Remove if: send_telegram changes credential resolution

    # Ensure env var is not set
    with patch.dict(os.environ, {}, clear=True):
        result = send_telegram(
            message="test",
            bot_token=None,
            chat_id="-123",
        )

    assert result is False, "Must return False when bot_token is missing"

    found_marker = False
    for record in list(caplog.records):
        if (
            "[IMP:9]" in record.message
            and "DELIVERY FAILED" in record.message
            and "TELEGRAM_BOT_TOKEN" in record.message
        ):
            found_marker = True
            logger.info("%s", f"Captured marker: {record.message}")
    assert found_marker, "IMP:9 DELIVERY FAILED marker must be logged for missing bot_token"


# endregion


# region FUNC_test_send_telegram_missing_chat_id


def test_send_telegram_missing_chat_id(caplog: pytest.LogCaptureFixture) -> None:
    """send_telegram returns False when chat_id is missing and not in env."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: missing chat_id returns False + DELIVERY FAILED marker
    # · Last fail: N/A (new test — DevPlan 132 W4: missing creds → IMP:9 marker, 126 D-2)
    # · Remove if: send_telegram changes credential resolution

    with patch.dict(os.environ, {}, clear=True):
        result = send_telegram(
            message="test",
            bot_token="123:abc",
            chat_id=None,
        )

    assert result is False, "Must return False when chat_id is missing"

    found_marker = False
    for record in list(caplog.records):
        if "[IMP:9]" in record.message and "DELIVERY FAILED" in record.message and "TELEGRAM_CHAT_ID" in record.message:
            found_marker = True
            logger.info("%s", f"Captured marker: {record.message}")
    assert found_marker, "IMP:9 DELIVERY FAILED marker must be logged for missing chat_id"


# endregion


# region FUNC_test_send_telegram_env_fallback


def test_send_telegram_env_fallback(caplog: pytest.LogCaptureFixture) -> None:
    """send_telegram uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from env when
    parameters are not passed."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: env var fallback works
    # · Last fail: N/A (new test)
    # · Remove if: send_telegram changes credential resolution order

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = None
    mock_response.read.return_value = b'{"ok": true}'
    mock_response.headers = HTTPMessage()

    captured_request: list[urllib.request.Request] = []

    def fake_open(
        req: urllib.request.Request,
        timeout: int = 30,
    ) -> MagicMock:
        captured_request.append(req)
        return mock_response

    env_vars = {
        "TELEGRAM_BOT_TOKEN": "env-token-456",
        "TELEGRAM_CHAT_ID": "env-chat-789",
    }

    with (
        patch.dict(os.environ, env_vars, clear=True),
        patch.object(urllib.request.OpenerDirector, "open", side_effect=fake_open),
    ):
        result = send_telegram(
            message="env test",
            bot_token=None,
            chat_id=None,
        )

    found_imp9 = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 9:
                found_imp9 = True
                logger.info("%s", f"IMP:9: {record.message}")

    assert result is True, "Must return True when env vars are set"
    assert found_imp9, "IMP:9 log must be present on successful env-based send"
    assert len(captured_request) == 1
    body = captured_request[0].data.decode("ascii")
    assert "chat_id=env-chat-789" in body
    assert "text=env%20test" in body


# endregion


# region FUNC_test_send_telegram_with_proxy


def test_send_telegram_with_proxy(caplog: pytest.LogCaptureFixture) -> None:
    """send_telegram configures ProxyHandler when proxy_url is provided."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: proxy configuration in request
    # · Last fail: N/A (new test)
    # · Remove if: send_telegram changes proxy handling

    bot_token = "123:proxy-token"
    chat_id = "-42"
    proxy_url = "http://127.0.0.1:8118"

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = None
    mock_response.read.return_value = b'{"ok": true}'
    mock_response.headers = HTTPMessage()

    captured_handlers: list[urllib.request.BaseHandler] = []

    original_build_opener = urllib.request.build_opener

    def tracking_build_opener(*handlers: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
        captured_handlers.extend(handlers)
        opener = original_build_opener(*handlers)
        # Wrap open to return mock

        def tracking_open(
            req: urllib.request.Request,
            timeout: int = 30,
        ) -> MagicMock:
            return mock_response

        opener.open = tracking_open  # type: ignore[method-assign]
        return opener

    with patch("urllib.request.build_opener", side_effect=tracking_build_opener):
        result = send_telegram(
            message="proxy test",
            bot_token=bot_token,
            chat_id=chat_id,
            proxy_url=proxy_url,
        )

    found_imp7_proxy = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7 and "proxy" in record.message.lower():
                found_imp7_proxy = True
                logger.info("%s", f"Proxy log: {record.message}")

    assert result is True, "Must return True when proxy is configured"
    assert found_imp7_proxy, "IMP:7 log must mention proxy configuration"

    # Verify a ProxyHandler was created
    found_proxy_handler = any(isinstance(h, urllib.request.ProxyHandler) for h in captured_handlers)
    assert found_proxy_handler, "ProxyHandler must be configured when proxy_url is set"


# endregion


# region FUNC_test_send_telegram_http_error


def test_send_telegram_http_error(caplog: pytest.LogCaptureFixture) -> None:
    """send_telegram returns False when Telegram API returns non-200 HTTP status."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: HTTP error handled gracefully + DELIVERY FAILED marker
    # · Last fail: N/A (new test — DevPlan 132 W4: non-200 → IMP:9 marker, 126 D-2)
    # · Remove if: send_telegram changes error handling

    bot_token = "123:http-error"
    chat_id = "-1"

    # Simulate HTTP 401 by raising HTTPError
    http_error = urllib.error.HTTPError(
        url=f"https://api.telegram.org/bot{bot_token}/sendMessage",
        code=401,
        msg="Unauthorized",
        hdrs=HTTPMessage(),
        fp=BytesIO(b'{"ok":false,"error_code":401,"description":"Unauthorized"}'),
    )

    with patch.object(
        urllib.request.OpenerDirector,
        "open",
        side_effect=http_error,
    ):
        result = send_telegram(
            message="test",
            bot_token=bot_token,
            chat_id=chat_id,
        )

    assert result is False, "Must return False on HTTP error"

    found_marker = False
    for record in list(caplog.records):
        if "[IMP:9]" in record.message and "DELIVERY FAILED" in record.message and "401" in record.message:
            found_marker = True
            logger.info("%s", f"Captured marker: {record.message}")
    assert found_marker, "IMP:9 DELIVERY FAILED marker must be logged for non-200 HTTP"


# endregion


# region FUNC_test_send_telegram_network_error


def test_send_telegram_network_error(caplog: pytest.LogCaptureFixture) -> None:
    """send_telegram returns False on network-level errors (connection refused, DNS failure)."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: network error returns False + DELIVERY FAILED marker
    # · Last fail: N/A (new test — DevPlan 132 W4: URLError/OSError → IMP:9 marker, 126 D-2)
    # · Remove if: send_telegram changes error handling

    bot_token = "123:net-error"
    chat_id = "-1"

    with patch.object(
        urllib.request.OpenerDirector,
        "open",
        side_effect=OSError("Connection refused"),
    ):
        result = send_telegram(
            message="test",
            bot_token=bot_token,
            chat_id=chat_id,
        )

    assert result is False, "Must return False on network error"

    found_marker = False
    for record in list(caplog.records):
        if (
            "[IMP:9]" in record.message
            and "DELIVERY FAILED" in record.message
            and "Connection refused" in record.message
        ):
            found_marker = True
            logger.info("%s", f"Captured marker: {record.message}")
    assert found_marker, "IMP:9 DELIVERY FAILED marker must be logged for network error"


# endregion


# region E10_RESOLVE_CHAT_ID


@pytest.mark.parametrize(
    ("severity", "env", "expected"),
    [
        # critical → dedicated TELEGRAM_CHAT_ID_CRITICAL wins
        (
            "critical",
            {
                "TELEGRAM_CHAT_ID": "-100base",
                "TELEGRAM_CHAT_ID_CRITICAL": "-100crit",
                "TELEGRAM_CHAT_ID_WARNING": "-100warn",
            },
            "-100crit",
        ),
        # critical without dedicated var → TELEGRAM_CHAT_ID fallback
        ("critical", {"TELEGRAM_CHAT_ID": "-100base"}, "-100base"),
        # warning → TELEGRAM_CHAT_ID_WARNING
        ("warning", {"TELEGRAM_CHAT_ID": "-100base", "TELEGRAM_CHAT_ID_WARNING": "-100warn"}, "-100warn"),
        # info → base (no dedicated var)
        ("info", {"TELEGRAM_CHAT_ID": "-100base", "TELEGRAM_CHAT_ID_CRITICAL": "-100crit"}, "-100base"),
        # empty severity → base
        ("", {"TELEGRAM_CHAT_ID": "-100base", "TELEGRAM_CHAT_ID_CRITICAL": "-100crit"}, "-100base"),
        # no TELEGRAM_CHAT_ID at all → None
        ("info", {}, None),
        ("critical", {}, None),
    ],
)
def test_resolve_chat_id_variants(severity, env, expected) -> None:
    """Parametrized: chat-id resolution precedence/fallback (F5-reduction)."""
    assert resolve_chat_id(severity, env) == expected


# endregion E10_RESOLVE_CHAT_ID


# region E10_FORMAT_NOTIFY_MESSAGE


@pytest.mark.parametrize(
    ("emoji", "message", "context", "expected"),
    [
        ("🚀", "Deployed app", "platform", "[platform] 🚀 Deployed app"),
        ("✅", "", "platform", "✅"),  # empty message → bare emoji (notify-hook contract)
        ("✅", "msg", "", "[] ✅ msg"),  # empty context → caller supplies default
    ],
)
def test_format_notify_message_variants(emoji, message, context, expected) -> None:
    """Parametrized: notify message formatting contract (F5-reduction)."""
    assert format_notify_message(emoji, message, context) == expected


# endregion E10_FORMAT_NOTIFY_MESSAGE


# region E10_NOTIFY


def test_notify_non_blocking_missing_secrets(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """notify: missing secrets file → IMP:7 warning, returns True (never blocks deploy)."""
    caplog.set_level(logging.WARNING)
    missing = str(tmp_path / "no-secrets.env")

    with patch.dict(os.environ, {}, clear=True):
        ok = notify("🚀", "Deployed X", severity="info", secrets_file=missing)

    assert ok is True, "notify must always return True (non-blocking)"
    assert any("TELEGRAM_BOT_TOKEN" in r.message for r in caplog.records), "IMP:7 missing-token warning expected"


def test_notify_reads_secrets_and_sends(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """notify: reads secrets.env (KEY=VALUE), resolves chat by severity, sends via send_telegram."""
    caplog.set_level(logging.INFO)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=123:token\nTELEGRAM_CHAT_ID=-100base\nTELEGRAM_CHAT_ID_WARNING=-100warn\n")

    captured: list[tuple[str, str, str]] = []

    def fake_send(message, bot_token=None, chat_id=None, proxy_url=None, parse_mode=None) -> bool:
        captured.append((message, bot_token or "", chat_id or ""))
        return True

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("core.internal.shared.telegram_notifier.send_telegram", side_effect=fake_send),
    ):
        ok = notify("⚠️", "Disk almost full", severity="warning", secrets_file=str(secrets))

    assert ok is True
    assert len(captured) == 1
    message, token, chat = captured[0]
    assert token == "123:token"
    assert chat == "-100warn", "warning severity must resolve to TELEGRAM_CHAT_ID_WARNING"
    assert "[platform] ⚠️ Disk almost full" in message

    found_imp9 = any("[IMP:9]" in r.message and "notify" in r.message for r in caplog.records)
    assert found_imp9, "IMP:9 notify-sent log expected"


def test_notify_missing_chat_skips_send(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """notify: token set but no chat resolvable → skip send, return True."""
    caplog.set_level(logging.WARNING)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=123:token\n")

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("core.internal.shared.telegram_notifier.send_telegram", return_value=True) as mock_send,
    ):
        ok = notify("✅", "msg", severity="info", secrets_file=str(secrets))

    assert ok is True
    mock_send.assert_not_called(), "send_telegram must not be called when chat unresolvable"
    assert any("No TELEGRAM_CHAT_ID resolved" in r.message for r in caplog.records)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · send_telegram URLError → IMP:9 DELIVERY FAILED (126 D-2)
# · Scenario: оригинальная форма D-2 — send_telegram логировал провал только на IMP:7 без
# ·   маркера; реконструкция по логам была невозможна. Точный вход: urllib.error.URLError
# ·   (DNS/сеть). Маркер обязан содержать DELIVERY FAILED + причину + proxy-состояние.
# · Last fail: до 132 W4 — «[IMP:7] Telegram API request failed: <err>» (без маркера)
# · Remove if: send_telegram перестанет помечать провалы доставки
def test_send_telegram_delivery_failed_marker_urlerror(caplog: pytest.LogCaptureFixture) -> None:
    """R5: URLError (оригинальный вход D-2) → IMP:9 DELIVERY FAILED с причиной и proxy=none."""
    caplog.set_level(logging.WARNING)
    with patch.object(
        urllib.request.OpenerDirector,
        "open",
        side_effect=urllib.error.URLError("name resolution failed"),
    ):
        result = send_telegram("test", bot_token="123:tok", chat_id="-1")

    assert result is False
    found = [
        r.message
        for r in caplog.records
        if "[IMP:9]" in r.message
        and "DELIVERY FAILED" in r.message
        and "name resolution failed" in r.message
        and "(proxy=none)" in r.message
    ]
    assert found, f"R5 FAIL: URLError не дал IMP:9 DELIVERY FAILED marker. Logs: {[r.message for r in caplog.records]}"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · send_telegram non-200 → IMP:9 DELIVERY FAILED (126 D-2)
# · Scenario: оригинальная форма — non-200 HTTP логировался как «[IMP:7] HTTP %d» без маркера.
# ·   Точный вход: HTTPError 429 (rate limit) — маркер обязан быть IMP:9 DELIVERY FAILED.
# · Last fail: до 132 W4 — «[IMP:7] Telegram API returned HTTP 429» (без маркера)
# · Remove if: send_telegram перестанет помечать non-200 доставки
def test_send_telegram_delivery_failed_marker_http_non200(caplog: pytest.LogCaptureFixture) -> None:
    """R5: HTTPError 429 (оригинальный вход D-2) → IMP:9 DELIVERY FAILED с кодом."""
    caplog.set_level(logging.WARNING)
    http_error = urllib.error.HTTPError(
        url="https://api.telegram.org/bot123:tok/sendMessage",
        code=429,
        msg="Too Many Requests",
        hdrs=HTTPMessage(),
        fp=BytesIO(b"{}"),
    )
    with patch.object(urllib.request.OpenerDirector, "open", side_effect=http_error):
        result = send_telegram("test", bot_token="123:tok", chat_id="-1")

    assert result is False
    found = [
        r.message
        for r in caplog.records
        if "[IMP:9]" in r.message and "DELIVERY FAILED" in r.message and "429" in r.message
    ]
    assert found, (
        f"R5 FAIL: HTTPError 429 не дал IMP:9 DELIVERY FAILED marker. Logs: {[r.message for r in caplog.records]}"
    )


# 🧪 TRAP[TEST] · NEGATIVE (R5) · notify: send_telegram → False → IMP:9 DELIVERY FAILED (126 D-2)
# · Scenario: оригинальная форма D-2 — notify() писал «Notification sent» безусловно при
# ·   send_telegram → False (лживый лог). Точный вход: send_telegram вернул False.
# · Last fail: до 132 W4 — «[IMP:9] Notification sent» даже при провале (telegram_notifier.py:314-316)
# · Remove if: notify перестанет маркировать провалы доставки
def test_notify_delivery_failed_marker(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """R5: notify при send_telegram=False логирует IMP:9 DELIVERY FAILED (severity/context) и НЕ «Notification sent»."""
    caplog.set_level(logging.INFO)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=123:token\nTELEGRAM_CHAT_ID=-100base\n")

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("core.internal.shared.telegram_notifier.send_telegram", return_value=False),
    ):
        ok = notify("⚠️", "boom", severity="critical", context="watchdog", secrets_file=str(secrets))

    assert ok is True, "notify всегда True (неблокирующий контракт сохранён)"
    messages = [r.message for r in caplog.records]
    assert any(
        "[IMP:9]" in m and "DELIVERY FAILED" in m and "severity=critical" in m and "context=watchdog" in m
        for m in messages
    ), f"R5 FAIL: notify не залогировал IMP:9 DELIVERY FAILED. Logs: {messages}"
    assert not any("Notification sent" in m for m in messages), (
        "R5 FAIL: лживый «Notification sent» при send_telegram=False (исходный вход D-2)"
    )


# 🧪 TRAP[TEST] · Regression · Scenario: notify success без failure-маркера
# · Last fail: N/A (new test — DevPlan 132 W4)
# · Remove if: notify success-path логика меняется
def test_notify_success_no_failure_marker(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """notify при send_telegram=True: «Notification sent», НЕТ DELIVERY FAILED, IMP:9 присутствует."""
    caplog.set_level(logging.INFO)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=123:token\nTELEGRAM_CHAT_ID=-100base\n")

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("core.internal.shared.telegram_notifier.send_telegram", return_value=True),
    ):
        ok = notify("✅", "all good", severity="info", context="deploy", secrets_file=str(secrets))

    assert ok is True
    messages = [r.message for r in caplog.records]
    assert any("Notification sent" in m and "severity=info" in m for m in messages), "success log expected"
    assert not any("DELIVERY FAILED" in m for m in messages), "no failure marker on success"
    assert any("[IMP:9]" in m for m in messages), "IMP:9 on successful scenario (LDD)"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · notify: кавычки в secrets.env (ночная сессия 141)
# · Scenario: оригинальная форма — secrets.env от decrypt_secrets.py пишет значения в '...';
# ·   inline-парсер notify() с v.strip() оставлял кавычки → InvalidURL nonnumeric port "8118'"
# ·   (TELEGRAM_PROXY_URL) и 401-токен. Точный вход: single-quoted значения в secrets-файле.
# · Last fail: 2026-08-06 до фикса — ValueError: invalid literal for int() with base 10: "8118'"
# · Remove if: secrets.env перестанет писать значения в кавычках ИЛИ notify сменит источник env
def test_notify_quoted_secrets_env_original_form(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """R5: notify() с кавычками в secrets.env передаёт ОЧИЩЕННЫЕ token/chat/proxy в send_telegram."""
    caplog.set_level(logging.INFO)
    secrets = tmp_path / "secrets.env"
    # Точный формат write_secrets_env (decrypt_secrets.py): KEY='value' (single-quoted)
    secrets.write_text(
        "TELEGRAM_BOT_TOKEN='123:token'\nTELEGRAM_CHAT_ID='-100base'\nTELEGRAM_PROXY_URL='http://127.0.0.1:8118'\n"
    )
    captured: dict = {}

    def _fake_send(message, bot_token=None, chat_id=None, proxy_url=None, parse_mode=None):
        captured.update(token=bot_token, chat_id=chat_id, proxy=proxy_url, message=message, parse_mode=parse_mode)
        return True

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("core.internal.shared.telegram_notifier.send_telegram", side_effect=_fake_send),
    ):
        ok = notify("✅", "quoted env", severity="info", context="deploy", secrets_file=str(secrets))

    assert ok is True
    assert captured["token"] == "123:token", f"R5 FAIL: token с кавычками: {captured['token']!r}"
    assert captured["chat_id"] == "-100base", f"R5 FAIL: chat_id с кавычками: {captured['chat_id']!r}"
    assert captured["proxy"] == "http://127.0.0.1:8118", f"R5 FAIL: proxy с кавычками: {captured['proxy']!r}"
    messages = [r.message for r in caplog.records]
    assert any("[IMP:9]" in m and "Notification sent" in m for m in messages), (
        "R5 FAIL: успешная доставка не залогирована IMP:9 (LDD)"
    )


# 🧪 TRAP[TEST] · NEGATIVE (R5) · CLI send: kwarg token vs bot_token (ночная сессия 141)
# · Scenario: оригинальная форма — `send`/`get-me` вызывали send_telegram(token=...) →
# ·   TypeError: unexpected keyword argument 'token' (сигнатура bot_token). Точный вход: argv send.
# · Last fail: 2026-08-06 до фикса — TypeError в main() send/get-me ветках
# · Remove if: сигнатура send_telegram/get_me перестанет принимать bot_token
def test_main_send_cli_uses_bot_token_kwarg(tmp_path) -> None:
    """R5: CLI send вызывает send_telegram с bot_token= (НЕ token=)."""
    from core.internal.shared.telegram_notifier import main

    captured: dict = {}

    def _fake_send(message, bot_token=None, chat_id=None, proxy_url=None, parse_mode=None):
        captured.update(bot_token=bot_token, chat_id=chat_id, proxy=proxy_url)
        return True

    with (
        patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "123:token", "TELEGRAM_CHAT_ID": "-100base"},
            clear=True,
        ),
        patch(
            "sys.argv",
            ["telegram_notifier", "send", "hello"],
        ),
        patch("core.internal.shared.telegram_notifier.send_telegram", side_effect=_fake_send),
    ):
        rc = main()

    assert rc == 0
    assert captured["bot_token"] == "123:token", f"R5 FAIL: send_telegram не получил bot_token: {captured!r}"
    assert captured["chat_id"] == "-100base"


# endregion E10_NOTIFY
