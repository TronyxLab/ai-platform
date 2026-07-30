#!/usr/bin/env python3
# GREP_SUMMARY: telegram-notifier, send-telegram, telegram-alert, shared-telegram, urllib
# STRUCTURE: ▶ send_telegram(message, [token], [chat_id], [proxy]) → ◇ env fallback → ◇ POST to api.telegram.org → ⊕ HTTP 200? → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Unified Telegram notification client — replaces 6 independent implementations
##           (3 shell + 3 Python: agent_watchdog.py TelegramNotifier, steps.py _send_telegram_notification,
##           notify-hook.sh shell curl variants). Single shared module that all platform
##           components import for sending Telegram alerts.
## @scope    Shared library consumed by bootstrap steps, watchdog agent, deploy hooks,
##           and any other platform component needing Telegram notification. Uses urllib
##           only (stdlib) — zero external dependencies. No requests library.
## @invariants
##   1. bot_token and chat_id sourced from parameters first, then os.environ fallback
##   2. If both are None/empty AND not in env → log WARNING at IMP:7, return False
##   3. POST to https://api.telegram.org/bot{token}/sendMessage
##   4. Content-Type: application/x-www-form-urlencoded (data in POST body, not query string)
##   5. If proxy_url set → configure ProxyHandler for both http and https
##   6. Timeout: 30s default (urllib.request.urlopen timeout parameter)
##   7. All exceptions caught → log at IMP:7, return False (never raises)
##   8. Non-fatal by design — caller must never depend on notification delivery
## @rationale DevPlan 081B7: Six independent Telegram notification implementations exist
##            across the codebase with different mechanisms (curl, urllib, requests).
##            A single shared module eliminates duplication, ensures consistent error
##            handling, and removes the `requests` external dependency. Stdlib-only
##            (urllib) keeps the module deployable without pip install on bare-metal nodes.
## @changes  2026-07-30 | DevPlan 081B7 — Created unified telegram_notifier module
# endregion MODULE_CONTRACT

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_GETME_URL = "https://api.telegram.org/bot{token}/getMe"
DEFAULT_TIMEOUT = 30


# region FUNC_send_telegram


def send_telegram(
    message: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
    proxy_url: str | None = None,
    parse_mode: str | None = None,
) -> bool:
    """Send a message to a Telegram chat via the Bot API.

    ▶ ┌message, [token], [chat_id], [proxy], [parse_mode]┐ →
    ◇ resolve token/chat_id (param → env) →
    ◇ missing? → IMP:7 warning → return False → ◇ urlencode POST data →
    ◇ ProxyHandler if proxy_url → ◇ POST /sendMessage → ⊕ HTTP 200? → ⎋ bool

    ## @purpose  Send a text message to Telegram using the Bot API. Single entrypoint
    ##            for all platform Telegram notifications. Non-fatal: never raises.
    ## @io — ⇥ message: str — text to send (URL-encoded automatically via urlencode)
    ##       ⇥ bot_token: str | None — Telegram bot token (falls back to TELEGRAM_BOT_TOKEN env)
    ##       ⇥ chat_id: str | None — Telegram chat ID (falls back to TELEGRAM_CHAT_ID env)
    ##       ⇥ proxy_url: str | None — optional SOCKS/HTTP proxy URL (e.g. "http://127.0.0.1:8118")
    ##       ⇥ parse_mode: str | None — optional Telegram parse_mode (HTML, MarkdownV2, etc.)
    ##       → ⎋ bool — True if HTTP 200 received, False on any failure
    ## @complexity — O(1) + 1 HTTP POST request with 30s timeout
    ## @invariants
    ##   - Never raises: all exceptions caught, logged at IMP:7, return False
    ##   - Token/chat_id resolution: param > env > fail
    ##   - POST body is application/x-www-form-urlencoded (not query string in URL)
    ##   - ProxyHandler configured for both http and https schemes when proxy_url set
    ##   - 30s timeout prevents hanging on unreachable Telegram API
    """
    # ── Resolve credentials: parameter > environment variable ──
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat:
        logger.warning(
            "[IMP:7][telegram_notifier][send_telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — notification suppressed"
        )
        return False

    # ── Build POST data (application/x-www-form-urlencoded) ──
    post_params: dict[str, str] = {"chat_id": chat, "text": message}
    if parse_mode:
        post_params["parse_mode"] = parse_mode
    post_data = urllib.parse.urlencode(
        post_params,
        quote_via=urllib.parse.quote,
    ).encode("ascii")

    url = TELEGRAM_API_BASE.format(token=token)
    req = urllib.request.Request(
        url,
        data=post_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    # ── Configure proxy handler if requested ──
    opener: urllib.request.OpenerDirector
    if proxy_url:
        logger.info(
            "[IMP:7][telegram_notifier][send_telegram] Using proxy: %s",
            proxy_url,
        )
        proxy_handler = urllib.request.ProxyHandler(
            {
                "http": proxy_url,
                "https": proxy_url,
            }
        )
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()

    # ── Execute POST ──
    try:
        logger.info(
            "[IMP:7][telegram_notifier][send_telegram] Sending notification to chat %s",
            chat,
        )
        with opener.open(req, timeout=DEFAULT_TIMEOUT) as resp:
            if resp.status == 200:
                logger.info(
                    "[IMP:9][telegram_notifier][send_telegram] Notification sent successfully to chat %s",
                    chat,
                )
                return True
            logger.warning(
                "[IMP:7][telegram_notifier][send_telegram] Telegram API returned HTTP %d",
                resp.status,
            )
            return False
    except (OSError, urllib.error.URLError) as e:
        logger.warning(
            "[IMP:7][telegram_notifier][send_telegram] Telegram API request failed: %s",
            e,
        )
        return False


# endregion FUNC_send_telegram

# region FUNC_get_me


def get_me(
    bot_token: str | None = None,
    proxy_url: str | None = None,
) -> bool:
    """Verify Telegram bot token by calling the getMe API endpoint.

    ▶ ┌[token], [proxy]┐ → ◇ resolve token (param → env) →
    ◇ missing? → IMP:7 warning → return False →
    ◇ ProxyHandler if proxy_url → ◇ GET /getMe → ⊕ ok:true? → ⎋ bool

    ## @purpose  Verify a Telegram bot token is valid without sending a message.
    ##            Used by healthcheck scripts to confirm the proxy→Telegram chain works.
    ## @io — ⇥ bot_token: str | None — Telegram bot token (falls back to TELEGRAM_BOT_TOKEN env)
    ##       ⇥ proxy_url: str | None — optional proxy URL (e.g. "http://127.0.0.1:8118")
    ##       → ⎋ bool — True if getMe returns ok:true, False on any failure
    ## @complexity — O(1) + 1 HTTP GET request with 30s timeout
    """
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning(
            "[IMP:7][telegram_notifier][get_me] TELEGRAM_BOT_TOKEN not set"
        )
        return False

    url = TELEGRAM_GETME_URL.format(token=token)

    # ── Configure proxy handler if requested ──
    opener: urllib.request.OpenerDirector
    if proxy_url:
        logger.info(
            "[IMP:7][telegram_notifier][get_me] Using proxy: %s",
            proxy_url,
        )
        proxy_handler = urllib.request.ProxyHandler(
            {
                "http": proxy_url,
                "https": proxy_url,
            }
        )
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()

    req = urllib.request.Request(url, method="GET")

    try:
        with opener.open(req, timeout=DEFAULT_TIMEOUT) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("ok"):
                    bot_username = data.get("result", {}).get("username", "?")
                    logger.info(
                        "[IMP:9][telegram_notifier][get_me] Bot token valid: @%s",
                        bot_username,
                    )
                    return True
                logger.warning(
                    "[IMP:7][telegram_notifier][get_me] getMe returned ok:false"
                )
                return False
            logger.warning(
                "[IMP:7][telegram_notifier][get_me] HTTP %d from getMe",
                resp.status,
            )
            return False
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        logger.warning(
            "[IMP:7][telegram_notifier][get_me] getMe request failed: %s",
            e,
        )
        return False


# endregion FUNC_get_me
