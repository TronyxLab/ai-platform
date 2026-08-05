#!/usr/bin/env python3
# GREP_SUMMARY: telegram-notifier, send-telegram, telegram-alert, shared-telegram, urllib
# STRUCTURE: ▶ send_telegram(message, [token], [chat_id], [proxy]) → ◇ env fallback → ◇ POST to api.telegram.org → ⊕ HTTP 200? → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Unified Telegram notification client — replaces 6 independent implementations
##           (3 shell + 3 Python: steps.py _send_telegram_notification,
##           notify-hook.sh shell curl variants). Single shared module that all platform
##           components import for sending Telegram alerts.
## @scope    Shared library consumed by bootstrap steps, deploy hooks,
##           and any other platform component needing Telegram notification. Uses urllib
##           only (stdlib) — zero external dependencies. No requests library.
## @invariants
##   1. bot_token and chat_id sourced from parameters first, then os.environ fallback
##   2. If both are None/empty AND not in env → log IMP:9 DELIVERY FAILED marker, return False
##   3. POST to https://api.telegram.org/bot{token}/sendMessage
##   4. Content-Type: application/x-www-form-urlencoded (data in POST body, not query string)
##   5. If proxy_url set → configure ProxyHandler for both http and https
##   6. Timeout: 30s default (urllib.request.urlopen timeout parameter)
##   7. All exceptions caught → IMP:9 DELIVERY FAILED marker (reason + proxy state), return False
##   8. Non-fatal by design — caller must never depend on notification delivery
##   9. notify(): ok = send_telegram(...); НЕ пишет «Notification sent» при неудаче (фикс 132 W4)
## @rationale DevPlan 081B7: Six independent Telegram notification implementations exist
##            across the codebase with different mechanisms (curl, urllib, requests).
##            A single shared module eliminates duplication, ensures consistent error
##            handling, and removes the `requests` external dependency. Stdlib-only
##            (urllib) keeps the module deployable without pip install on bare-metal nodes.
##            DevPlan 132 W4 (126 D-2): failure-маркеры IMP:9 (DELIVERY FAILED + reason +
##            proxy state) — реконструируемость провалов по логам; фикс лживого
##            «Notification sent» (писался безусловно при send_telegram → False).
## @changes  2026-07-30 | DevPlan 081B7 — Created unified telegram_notifier module
##           2026-08-02 | DevPlan 118 E10 — +resolve_chat_id/format_notify_message/notify()
##                      (severity-mapping merged from notify-hook.sh); CLI +notify subcommand
##           2026-08-04 | DevPlan 132 W4 — failure-маркеры IMP:9 (D-2), notify-fix
# endregion MODULE_CONTRACT

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

from core.internal.shared import secrets_env_parser

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
    ##   - Never raises: all exceptions caught, logged with IMP:9 DELIVERY FAILED marker, return False
    ##   - Token/chat_id resolution: param > env > fail
    ##   - POST body is application/x-www-form-urlencoded (not query string in URL)
    ##   - ProxyHandler configured for both http and https schemes when proxy_url set
    ##   - 30s timeout prevents hanging on unreachable Telegram API
    ##   - Каждый failure-путь логирует [IMP:9] DELIVERY FAILED: <reason> (proxy=<set|none>) — 126 D-2
    """
    # ── Resolve credentials: parameter > environment variable ──
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    proxy_state = "set" if proxy_url else "none"

    if not token or not chat:
        logger.warning(
            "[IMP:9][telegram_notifier][send_telegram] DELIVERY FAILED: TELEGRAM_BOT_TOKEN or "
            "TELEGRAM_CHAT_ID not set (proxy=%s)",
            proxy_state,
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
                "[IMP:9][telegram_notifier][send_telegram] DELIVERY FAILED: Telegram API returned HTTP %d (proxy=%s)",
                resp.status,
                proxy_state,
            )
            return False
    except (OSError, urllib.error.URLError) as e:
        logger.warning(
            "[IMP:9][telegram_notifier][send_telegram] DELIVERY FAILED: %s (proxy=%s)",
            e,
            proxy_state,
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
        logger.warning("[IMP:7][telegram_notifier][get_me] TELEGRAM_BOT_TOKEN not set")
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
                logger.warning("[IMP:7][telegram_notifier][get_me] getMe returned ok:false")
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


# region FUNC_resolve_chat_id
def resolve_chat_id(severity: str, env: Mapping[str, str]) -> str | None:
    """Resolve Telegram chat_id by notification severity (critical/warning/info).

    ## @purpose  Resolve TELEGRAM_CHAT_ID by severity (DevPlan 118 E10 — merged from notify-hook.sh):
    ##           critical → TELEGRAM_CHAT_ID_CRITICAL (fallback TELEGRAM_CHAT_ID),
    ##           warning  → TELEGRAM_CHAT_ID_WARNING (fallback TELEGRAM_CHAT_ID),
    ##           info/other → TELEGRAM_CHAT_ID.
    ## @io       ⇥ severity: str, env: Mapping[str, str] → ⎋ str | None — chat_id (None if unresolvable)
    ## @complexity O(1) — 2 env lookups
    ## @invariants
    ##   - critical/warning use their dedicated vars with TELEGRAM_CHAT_ID as fallback
    ##   - info and unknown severities resolve to TELEGRAM_CHAT_ID only
    """
    base = env.get("TELEGRAM_CHAT_ID", "")
    if severity == "critical":
        return env.get("TELEGRAM_CHAT_ID_CRITICAL") or base or None
    if severity == "warning":
        return env.get("TELEGRAM_CHAT_ID_WARNING") or base or None
    return base or None


# endregion FUNC_resolve_chat_id


# region FUNC_format_notify_message
def format_notify_message(emoji: str, message: str, context: str) -> str:
    """Build the full notification text: [context] emoji message (message optional).

    ## @purpose  Format notification message: "[context] emoji message" — or bare emoji if message empty
    ##           (DevPlan 118 E10 — merged from notify-hook.sh).
    ## @io       ⇥ emoji: str, message: str, context: str → ⎋ str
    ## @complexity O(1) — string concat
    """
    if not message:
        return emoji
    return f"[{context}] {emoji} {message}"


# endregion FUNC_format_notify_message


# region FUNC_notify
def notify(
    emoji: str,
    message: str,
    severity: str = "",
    context: str = "platform",
    secrets_file: str = "/run/platform/secrets.env",
) -> bool:
    """Send a non-blocking notification (notify-hook.sh contract, E10). Always returns True.

    ## @purpose  Полный non-blocking notify: load secrets env → resolve token/chat by severity → format →
    ##           send_telegram (HTML). Всегда возвращает True (exit 0) — неблокирующий по дизайну.
    ##           (DevPlan 118 E10 — Python-порт notify-hook.sh логики.)
    ## @io       ⇥ emoji: str, message: str, severity: str, context: str, secrets_file: str → ⎋ bool (всегда True)
    ## @complexity O(N) — чтение secrets.env (N строк) + 1 HTTP POST
    ## @invariants
    ##   - Отсутствие secrets-файла / токена / chat → IMP:7 log, return True (неблокирующий)
    ##   - send_telegram с parse_mode="HTML" — HTML-разметка в сообщениях деплоя
    ##   - ok = send_telegram(...); при not ok → IMP:9 DELIVERY FAILED (severity, context) — 126 D-2;
    ##     «Notification sent» пишется ТОЛЬКО при ok (фикс лживого лога, DevPlan 132 W4)
    ##   - always return True (неблокирующий дизайн сохранён)
    """
    env = dict(os.environ)
    if os.path.isfile(secrets_file):
        try:
            # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — кавычки в secrets.env ломали доставку
            # · Symptom: notify() падал с ValueError: invalid literal for int() with base 10: "8118'"
            # ·   (TELEGRAM_PROXY_URL='http://127.0.0.1:8118' — одинарные кавычки из write_secrets_env
            # ·   оставались в значении) / Telegram 401 при кавычках в токене.
            # · Root: inline-парсер с v.strip() без снятия кавычек — 8-й дубль канона secrets_env_parser.
            # · Fix: канонический secrets_env_parser.parse() (снимает '...' и "...").
            # · Prevention: secrets.env читается ТОЛЬКО через secrets_env_parser (SoT, инвариант 11).
            env.update(secrets_env_parser.parse(secrets_file))
        except OSError as exc:
            logger.warning("[IMP:7][telegram_notifier][notify] Cannot read secrets %s: %s", secrets_file, exc)

    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("[IMP:7][telegram_notifier][notify] TELEGRAM_BOT_TOKEN not set — notification skipped")
        return True
    chat_id = resolve_chat_id(severity, env)
    if not chat_id:
        logger.warning(
            "[IMP:7][telegram_notifier][notify] No TELEGRAM_CHAT_ID resolved (severity=%s)", severity or "none"
        )
        return True

    full_message = format_notify_message(emoji, message, context)
    proxy = env.get("TELEGRAM_PROXY_URL") or env.get("PROXY_URL")
    ok = send_telegram(full_message, bot_token=token, chat_id=chat_id, proxy_url=proxy, parse_mode="HTML")
    if not ok:
        # ⚠️ TRAP[BUG] · 2026-08-04 · P1 · Лживый лог «Notification sent» при неудаче (126 D-2)
        # · Symptom: notify() писал «[IMP:9] Notification sent» БЕЗУСЛОВНО (telegram_notifier.py:314-316),
        # ·   даже когда send_telegram вернул False — оператор видел успех при реальном провале.
        # · Root: результат send_telegram не захватывался; лог писался всегда.
        # · Fix: ok = send_telegram(...); при not ok — IMP:9 DELIVERY FAILED (severity/context);
        # ·   «Notification sent» — только при ok. Контракт «always exit 0 / always True» сохранён.
        # · Prevention: маркер DELIVERY FAILED делает провалы реконструируемыми по логам (D-2).
        logger.warning(
            "[IMP:9][telegram_notifier][notify] DELIVERY FAILED (severity=%s, context=%s)",
            severity or "none",
            context,
        )
    else:
        logger.info(
            "[IMP:9][telegram_notifier][notify] Notification sent (severity=%s, context=%s)",
            severity or "none",
            context,
        )
    return True


# endregion FUNC_notify


# region FUNC_main
def main() -> int:
    """CLI entry: `python3 -m core.internal.shared.telegram_notifier send <text>|get-me`.

    ▶ ┌argv + env┐ → ◇ parse → ◇ send/get-me dispatch → ⎋ exit 0|1

    ## @purpose — CLI для shell-фасадов: send / get-me (Strangler 2026-07-31 — заменяет 3 inline
    ##            python3 -c с sys.path.insert: notify-hook.sh, tor-proxy-healthcheck.sh и
    ##            disk-monitor.sh; последний удалён как мёртвый код волной 117 D10 — остались 2 фасада).
    ## @io — ⇥ argv + env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_PROXY_URL|PROXY_URL)
    ##       → ⎋ exit 0|1
    ## @invariants — токен/чат читаются из env (никаких секретов в argv)
    """
    import argparse

    parser = argparse.ArgumentParser(description="Telegram notifier CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    send_parser = subparsers.add_parser("send", help="Send message to chat")
    send_parser.add_argument("text", help="Message text")
    subparsers.add_parser("get-me", help="Verify Telegram API reachability")
    notify_parser = subparsers.add_parser(
        "notify",
        help="Non-blocking notify (notify-hook.sh contract, DevPlan 118 E10)",
    )
    notify_parser.add_argument("--severity", default="", help="critical|warning|info (chat_id resolution)")
    notify_parser.add_argument("--context", default="platform", help="Context prefix in message")
    notify_parser.add_argument("--secrets-file", default="/run/platform/secrets.env", help="secrets.env path")
    notify_parser.add_argument("emoji", default="✅", nargs="?", help="Emoji prefix")
    notify_parser.add_argument("message", nargs="?", default="", help="Message text")
    args = parser.parse_args()

    if args.command == "notify":
        # always exit 0 (non-blocking by design — notification failure must not block deploy)
        notify(
            emoji=args.emoji,
            message=args.message,
            severity=args.severity,
            context=args.context,
            secrets_file=args.secrets_file,
        )
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.error("[IMP:10][telegram][cli] TELEGRAM_BOT_TOKEN not set")
        return 1

    proxy = os.environ.get("TELEGRAM_PROXY_URL") or os.environ.get("PROXY_URL")
    if args.command == "send":
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not chat_id:
            logger.error("[IMP:10][telegram][cli] TELEGRAM_CHAT_ID not set")
            return 1
        ok = send_telegram(args.text, chat_id=chat_id, bot_token=token, proxy_url=proxy, parse_mode="HTML")
        return 0 if ok else 1
    ok = get_me(bot_token=token, proxy_url=proxy)
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
# endregion FUNC_main
