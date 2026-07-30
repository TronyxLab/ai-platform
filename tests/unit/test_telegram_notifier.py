#!/usr/bin/env python3
# GREP_SUMMARY: test-telegram-notifier send-telegram telegram-alert shared-telegram mocked-urllib
# STRUCTURE: ┌mock urlopen┐ → ○ test scenarios: success → missing_token → missing_chat → env_fallback → proxy → http_error → network_error
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/telegram_notifier.py
##           Verifies send_telegram() with mocked urllib.request.urlopen.
## @scope    Tests: successful send, missing credentials, env var fallback,
##           proxy configuration, HTTP error, network error.
## @invariants
##   - All tests use unittest.mock (no real HTTP calls)
##   - No Docker dependency (pure Python unit tests)
##   - LDD: at least one IMP:9 log in successful send
##   - IMP:7 warning logged on missing credentials or failures
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

from core.internal.shared.telegram_notifier import send_telegram

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
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

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

    # 🧪 TRAP[TEST] · Regression · Scenario: missing bot_token returns False
    # · Last fail: N/A (new test)
    # · Remove if: send_telegram changes credential resolution

    # Ensure env var is not set
    with patch.dict(os.environ, {}, clear=True):
        result = send_telegram(
            message="test",
            bot_token=None,
            chat_id="-123",
        )

    assert result is False, "Must return False when bot_token is missing"

    found_imp7 = False
    for record in caplog.records:
        if "[IMP:7]" in record.message and "TELEGRAM_BOT_TOKEN" in record.message:
            found_imp7 = True
            print(f"Captured warning: {record.message}")
    assert found_imp7, "IMP:7 warning must be logged for missing bot_token"


# endregion


# region FUNC_test_send_telegram_missing_chat_id


def test_send_telegram_missing_chat_id(caplog: pytest.LogCaptureFixture) -> None:
    """send_telegram returns False when chat_id is missing and not in env."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: missing chat_id returns False
    # · Last fail: N/A (new test)
    # · Remove if: send_telegram changes credential resolution

    with patch.dict(os.environ, {}, clear=True):
        result = send_telegram(
            message="test",
            bot_token="123:abc",
            chat_id=None,
        )

    assert result is False, "Must return False when chat_id is missing"

    found_imp7 = False
    for record in caplog.records:
        if "[IMP:7]" in record.message and "TELEGRAM_CHAT_ID" in record.message:
            found_imp7 = True
            print(f"Captured warning: {record.message}")
    assert found_imp7, "IMP:7 warning must be logged for missing chat_id"


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
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 9:
                found_imp9 = True
                print(f"IMP:9: {record.message}")

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
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7 and "proxy" in record.message.lower():
                found_imp7_proxy = True
                print(f"Proxy log: {record.message}")

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

    # 🧪 TRAP[TEST] · Regression · Scenario: HTTP error handled gracefully
    # · Last fail: N/A (new test)
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

    found_imp7 = False
    for record in caplog.records:
        if "[IMP:7]" in record.message and "failed" in record.message.lower():
            found_imp7 = True
            print(f"Captured: {record.message}")
    assert found_imp7, "IMP:7 warning must be logged for HTTP error"


# endregion


# region FUNC_test_send_telegram_network_error


def test_send_telegram_network_error(caplog: pytest.LogCaptureFixture) -> None:
    """send_telegram returns False on network-level errors (connection refused, DNS failure)."""
    caplog.set_level(logging.WARNING)

    # 🧪 TRAP[TEST] · Regression · Scenario: network error returns False
    # · Last fail: N/A (new test)
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

    found_imp7 = False
    for record in caplog.records:
        if "[IMP:7]" in record.message and "failed" in record.message.lower():
            found_imp7 = True
            print(f"Captured: {record.message}")
    assert found_imp7, "IMP:7 warning must be logged for network error"


# endregion
