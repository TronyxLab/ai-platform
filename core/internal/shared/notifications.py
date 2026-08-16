#!/usr/bin/env python3
# GREP_SUMMARY: notifications, notify-event, severity-routing, chat-id-sot, envelope, escape-html, throttle, dedup, audit-fallback, non-blocking, notify-ci, direct-https
# STRUCTURE: ▶ notify_event(n) → ◇ throttle?(event,fingerprint) → ◇ resolve_chat_id(severity) → ◇ format_envelope(n) → ◇ send_telegram(proxy) → ◇ fail? audit-fallback → ⎋ True (non-blocking) · CLI: notify (нода, Tor) | notify-ci (CI, прямой HTTPS)
# region MODULE_CONTRACT
## @purpose  Единый notifier-контракт платформы (DevPlan 003): канонический payload
##           (Notification dataclass), HTML-конверт (format_envelope), ЕДИНСТВЕННЫЙ
##           экранизатор (escape_html), SoT severity→chat (resolve_chat_id),
##           единая точка отправки notify_event (non-blocking, всегда True,
##           throttle/dedup по (event, fingerprint), audit-fallback при провале
##           доставки). CLI notify (нода: Tor/Privoxy) и notify-ci (CI: прямой HTTPS).
##           Все Python-отправители платформы шлют ТОЛЬКО через этот модуль.
## @scope    shared/notifications.py — потребляется 6+ отправителями (reporting,
##           cert_expiry_check, reboot_policy, security_updates, watchdog,
##           post_deploy_chain, notify-hook, CI-workflows). stdlib-only на module-level
##           (boto3/audit_logger/telegram_notifier/yaml — ленивые импорты внутри функций):
##           systemd-cron пути (cert_expiry/reboot_policy subprocess) без PYTHONPATH-риска.
## @invariants
##   1. notify_event ВСЕГДА возвращает True (non-blocking — уведомление не блокирует операцию)
##   2. resolve_chat_id — ЕДИНСТВЕННЫЙ SoT severity→chat: critical → TELEGRAM_CHAT_ID_CRITICAL
##      (fallback TELEGRAM_CHAT_ID), warning → TELEGRAM_CHAT_ID_WARNING (fallback base),
##      info/unknown → TELEGRAM_CHAT_ID. Дубль в telegram_notifier — shim-делегация.
##   3. format_envelope — единый HTML-конверт: badge + [severity] + [context] + message +
##      details + footer (⏱ ts · 🪪 corr_id · 🔗 links · 💡 action). Все data-поля — escape_html.
##   4. escape_html — ЕДИНСТВЕННЫЙ экранизатор платформы (html.escape, quote=False)
##   5. Транспорт из ноды — ТОЛЬКО через Tor/Privoxy (TELEGRAM_PROXY_URL из env/secrets);
##      прямой HTTPS на ноде ЗАПРЕЩЁН (TRAP[BUG] 141 — утечка IP). notify-ci (CI/GitHub
##      Actions) — прямой HTTPS (proxy_url=None), НИКОГДА не берёт прокси из env.
##   6. Throttle/dedup: реестр {(event, fingerprint): ts}; окно — из notification-catalog.yaml
##      (throttle_min) или DEFAULT_THROTTLE_SECONDS (3600). Подавление → IMP:8, return True.
##   7. Провал доставки → IMP:9 DELIVERY FAILED + audit-fallback
##      write_audit_entry(tag="notify:failed", status="ERROR") — реконструируемость (D-2).
##   8. Токен/чаты читаются из env: параметры процесса > secrets.env (только notify) > os.environ.
##   9. Каталог событий (core/notification-catalog.yaml) — декларативный реестр; runtime
##      использует его для throttle-окна; parity-гейт проверяет «код шлёт только
##      зарегистрированные события».
## @rationale Два несвязанных механизма доставки (Grafana vs Python) и 4 из 6 Python-
##            отправителей без severity → «тихие отказы» (DevPlan 003 аудит). Единый
##            конверт+экранизатор+SoT на обе среды (нода и CI) исключает рассинхрон
##            конвертов; TRAP[DECISION] 160 W4b: DI notifier живёт в потребителе
##            (send_fn параметром notify_event), не http_opener в send_telegram.
## @changes  2026-08-16 | DevPlan 003 — создан (Волна B1)
## @modulemap
##   Notification [W:1] — канонический payload всех отправителей
##   escape_html [W:1] — единый экранизатор (html.escape)
##   format_envelope [W:1] — HTML-конверт (badge+context+message+details+footer)
##   resolve_chat_id [W:1] — ЕДИНСТВЕННЫЙ SoT severity→chat (перенесён из telegram_notifier)
##   format_notify_message [W:1] — backward-compat формат "[ctx] emoji msg" (CLI notify-hook)
##   notify_event [W:1] — единая точка отправки (throttle + severity-routing + audit-fallback)
##   _load_catalog [W:1] — ленивый читатель notification-catalog.yaml (throttle-окна)
##   main [W:1] — CLI notify (нода/Tor) / notify-ci (CI/прямой HTTPS)
## @usecases
##   - notify_event(Notification(severity="critical", event="deploy.failed", ...))
##   - python3 -m core.internal.shared.notifications notify --severity critical --event cert.expiry ...
##   - python3 -m core.internal.shared.notifications notify-ci --event ci.failure ... (GH Actions)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import html
import logging
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from http.client import HTTPResponse
from typing import cast

from core.internal.shared import secrets_env_parser
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
HTTP_OK: int = 200  # успешный ответ Telegram Bot API

# ── SoT severity→chat (B2: parity-гейт сверяет этот словарь с Grafana contact-points.yml) ──
SEVERITY_CHAT_ENV: Mapping[str, str] = {
    "critical": "TELEGRAM_CHAT_ID_CRITICAL",
    "warning": "TELEGRAM_CHAT_ID_WARNING",
    "info": "TELEGRAM_CHAT_ID",
}
VALID_SEVERITIES: tuple[str, ...] = ("critical", "warning", "info")

# Дефолтное throttle-окно dedup (сек): (event, fingerprint) — не чаще раза в час,
# если каталог не задаёт throttle_min для события
DEFAULT_THROTTLE_SECONDS: float = 3600.0

# Badge-эмодзи конверта по severity
_SEVERITY_BADGE: Mapping[str, str] = {
    "critical": "🚨",
    "warning": "⚠️",
    "info": "✅",
}

# Модульный throttle-реестр (процесс одноразовый для CLI; реестр важен для in-process
# потребителей и тестов; DI-параметр notify_event переопределяет для изоляции)
_THROTTLE_REGISTRY: dict[tuple[str, str], float] = {}


# region DATACLASS_Notification
@dataclass
class Notification:
    """Каноническая обёртка сообщения — единый payload всех отправителей платформы.

    ## @purpose  Единый контракт события: severity/context/event/corr_id/message/details/
    ##            links/action/ts. Все Python-отправители формируют именно этот объект
    ##            (DevPlan 003 §3 Notification_py_CLASS).
    ## @complexity O(1) — декларация
    ## @invariants
    ##   - severity ∈ {critical, warning, info} (нормализуется в notify_event)
    ##   - ts — ISO8601 UTC, ставится автоматически при создании (переопределяемо)
    ##   - fingerprint — ключ dedup (по умолчанию = message)
    """

    severity: str = "info"
    context: str = "platform"
    event: str = ""
    message: str = ""
    details: list[str] = field(default_factory=list)
    corr_id: str = ""
    links: list[str] = field(default_factory=list)
    action: str = ""
    fingerprint: str = ""
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# endregion DATACLASS_Notification


# region FUNC_escape_html
def escape_html(text: str) -> str:
    """Единый экранизатор HTML-конверта (html.escape, quote=False).

    ▶ ┌text┐ → html.escape → ⎋ escaped

    ## @purpose  ЕДИНСТВЕННЫЙ экранизатор платформы для Telegram-HTML конвертов.
    ##            Потребители НЕ вызывают html.escape напрямую — только эту функцию
    ##            (иначе рассинхрон экранирования между отправителями).
    ## @io       ⇥ text: str — сырой текст (message/details/context/corr_id/action)
    ##           → ⎋ str — HTML-экранированный (& < >)
    ## @complexity O(N) — N = длина текста
    ## @invariants  quote=False: кавычки НЕ экранируются (безопасно в Telegram HTML-тексте)
    """
    return html.escape(text, quote=False)


# endregion FUNC_escape_html


# region FUNC_resolve_chat_id
def resolve_chat_id(severity: str, env: Mapping[str, str]) -> str | None:
    """Resolve Telegram chat_id by notification severity (critical/warning/info).

    ▶ ┌severity + env┐ → ◇ critical/warning? → dedicated var (fallback base) → ◇ else base → ⎋ chat_id | None

    ## @purpose  ЕДИНСТВЕННЫЙ SoT severity→chat (перенесён из telegram_notifier, DevPlan 003 B2):
    ##           critical → TELEGRAM_CHAT_ID_CRITICAL (fallback TELEGRAM_CHAT_ID),
    ##           warning → TELEGRAM_CHAT_ID_WARNING (fallback TELEGRAM_CHAT_ID),
    ##           info/unknown → TELEGRAM_CHAT_ID.
    ## @io       ⇥ severity: str, env: Mapping[str, str] → ⎋ str | None — chat_id (None если unresolvable)
    ## @complexity O(1) — 2 env lookups
    ## @invariants
    ##   - Словарь маршрутизации SEVERITY_CHAT_ENV — единый (parity-гейт против Grafana)
    ##   - critical/warning — dedicated vars с TELEGRAM_CHAT_ID fallback
    ##   - info и неизвестные severity → TELEGRAM_CHAT_ID
    ## 🧐 TRAP[DECISION] · 2026-08-15 · — · TELEGRAM_CHAT_ID — FALLBACK, не SoT · Rejected: удалить
    ##   TELEGRAM_CHAT_ID полностью (severity-роутинг в shell/Grafana) · Reason: backward-compat для
    ##   CLI-путей send_telegram (bootstrap/update отчёты reporting.py без severity) — единый базовый
    ##   chat для non-severity потребителей; SoT для severity — CRITICAL/WARNING (паритет 170 W10-C) ·
    ##   Rev: если все потребители перейдут на severity-схему — удалить fallback (DevPlan 003 B3)
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
    """Build the legacy notification text: [context] emoji message (backward-compat).

    ## @purpose  Backward-compat формат CLI notify-hook: "[context] emoji message" — или
    ##            голый emoji при пустом message (DevPlan 118 E10, перенесён в SoT 003 B1).
    ##            НОВЫЕ отправители используют format_envelope; эта функция — только
    ##            для сохранения контракта notify()/notify-hook.sh.
    ## @io       ⇥ emoji: str, message: str, context: str → ⎋ str
    ## @complexity O(1) — string concat
    """
    if not message:
        return emoji
    return f"[{context}] {emoji} {message}"


# endregion FUNC_format_notify_message


# region FUNC_format_envelope
def format_envelope(n: Notification) -> str:
    """Канонический HTML-конверт: badge + [severity] + [context] + message + details + footer.

    ▶ ┌n┐ → ◇ badge/severity → ⊕ header → ⊕ details "• …" → ⊕ footer (⏱ ts · 🪪 corr · 🔗 links · 💡 action) → ⎋ HTML-строка

    ## @purpose  Единый конверт всех отправителей (DevPlan 003 §4 пример): структура
    ##            badge+context+message+details+footer фиксирована; все data-поля
    ##            проходят escape_html (инвариант 4). Снэпшот-тест гарантирует форму.
    ## @io       ⇥ n: Notification → ⎋ str — HTML-сообщение для sendMessage (parse_mode=HTML)
    ## @complexity O(D + L) — D details, L links
    ## @invariants
    ##   - header: "{badge} <b>[SEVERITY]</b> [{context}] {message}" (message — escape_html)
    ##   - details: строки "• {detail}" (escape_html), пустые пропускаются
    ##   - footer: "⏱ {ts} · 🪪 {corr_id}" + "🔗 <a href={url}>{label}</a>" + "💡 {action}"
    ##     (пустые секции опускаются; разделитель " · ")
    ##   - links: url — html.escape(quote=True) (атрибут href), label — escape_html
    """
    badge = _SEVERITY_BADGE.get(n.severity, "🔔")
    header = f"{badge} <b>[{n.severity.upper()}]</b> [{escape_html(n.context)}] {escape_html(n.message)}"

    parts: list[str] = [header]
    parts.extend(f"• {escape_html(detail)}" for detail in n.details if detail.strip())

    footer: list[str] = []
    if n.ts:
        footer.append(f"⏱ {n.ts}")
    if n.corr_id:
        footer.append(f"🪪 {escape_html(n.corr_id)}")
    for link in n.links:
        if link.strip():
            href = html.escape(link, quote=True)
            footer.append(f'🔗 <a href="{href}">{escape_html(link)}</a>')
    if n.action:
        footer.append(f"💡 {escape_html(n.action)}")
    if footer:
        parts.append(" · ".join(footer))

    return "\n".join(parts)


# endregion FUNC_format_envelope


# region FUNC__load_catalog
@lru_cache(maxsize=1)
def _load_catalog(catalog_path: str | None = None) -> dict[str, dict[str, object]]:
    """Ленивый читатель core/notification-catalog.yaml → {event_id: entry}.

    ▶ ┌catalog_path?┐ → ◇ cache hit? → ◇ lazy yaml import → ⊕ parse events → ⎋ {id: entry}

    ## @purpose  Реестр событий (лёгкий S5, DevPlan 003 B4): runtime использует каталог
    ##            для throttle-окон; parity-гейт — для статической сверки call-sites.
    ##            yaml — ЛЕНИВЫЙ импорт (stdlib-only канон module-level, инвариант 8).
    ##            lru_cache(maxsize=1) — кэш без global-стейта (PLW0603-free).
    ## @io       ⇥ catalog_path: str | None (None → core/notification-catalog.yaml от __file__)
    ##           → ⎋ dict[str, dict] — {event_id: {severity, context, throttle_min, action}};
    ##           отсутствующий/битый каталог → {} (runtime деградирует на дефолт-throttle)
    ## @complexity O(E) — E событий (с кэшем — O(1) повторные)
    """
    if catalog_path is None:
        catalog_path = str(pathlib.Path(__file__).resolve().parents[3] / "notification-catalog.yaml")
    catalog_file = pathlib.Path(catalog_path)
    if not catalog_file.is_file():
        logger.warning("[IMP:7][notifications][catalog] %s not found — runtime throttle defaults", catalog_path)
        return {}
    try:
        import yaml  # лениво: stdlib-only module-level канон (systemd-cron пути без PYTHONPATH)

        with catalog_file.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    # ruff: ignore[BLE001] — best-effort каталог: runtime НЕ падает из-за YAML (003 B4)
    except Exception as e:  # noqa: EXC — каталог best-effort: runtime НЕ падает из-за YAML
        logger.warning(
            "[IMP:7][notifications][catalog] Cannot load %s (%s) — runtime throttle defaults", catalog_path, e
        )
        return {}
    events_raw = data.get("events", []) if isinstance(data, dict) else []
    events: dict[str, dict[str, object]] = {}
    if isinstance(events_raw, list):
        for entry in events_raw:
            if isinstance(entry, dict) and entry.get("id"):
                events[str(entry["id"])] = cast("dict[str, object]", entry)
    return events


# endregion FUNC__load_catalog


# region FUNC__resolve_env
def _resolve_env(
    env: Mapping[str, str] | None,
    secrets_file: str | None,
) -> dict[str, str]:
    """Собрать env: os.environ + secrets.env (если файл существует и задан).

    ▶ ┌env?, secrets_file?┐ → ⊕ os.environ → ◇ secrets-файл? → ⊕ parse → ⎋ dict

    ## @purpose  Источник токена/чатов/прокси: параметр env > secrets.env > os.environ
    ##            (параметры процесса имеют приоритет — инвариант 8).
    ## @io       ⇥ env: Mapping | None, secrets_file: str | None → ⎋ dict[str, str]
    ## @complexity O(N) — N = строк secrets-файла
    """
    merged = dict(os.environ if env is None else env)
    if secrets_file and pathlib.Path(secrets_file).is_file():
        try:
            merged.update(secrets_env_parser.parse(secrets_file))
        except OSError as exc:
            logger.warning("[IMP:7][notifications][env] Cannot read %s: %s", secrets_file, exc)
    return merged


# endregion FUNC__resolve_env


# region FUNC__audit_fallback
def _audit_fallback(
    n: Notification,
    reason: str,
    audit_fn: Callable[..., None] | None,
) -> None:
    """Audit-fallback при провале доставки (D-2 реконструируемость).

    ▶ ┌n + reason┐ → ◇ audit_fn? → ○ write_audit_entry(tag="notify:failed", ERROR) → ⎋ None

    ## @purpose  Провал доставки НЕ молчит: запись в единый audit-лог (audit_logger,
    ##            ленивый импорт — stdlib-only канон). Best-effort: сбой аудита → WARN.
    ## @io       ⇥ n: Notification, reason: str, audit_fn: Callable | None (DI) → ⎋ None
    ## @complexity O(1) — одна JSON-lines запись
    ## @invariants  Никогда не raise (fallback не маскирует основной путь)
    """
    try:
        if audit_fn is not None:
            audit_fn(n, reason)
            return
        from core.internal.shared.audit_logger import write_audit_entry

        write_audit_entry(
            tag="notify:failed",
            status="ERROR",
            message=f"severity={n.severity} event={n.event or '-'} context={n.context} — {reason}",
            operation="notify",
        )
    # ruff: ignore[BLE001] — audit-fallback best-effort: не маскирует провал доставки (003 A/B)
    except Exception as e:  # noqa: EXC — best-effort audit-fallback (DevPlan 003)
        logger.warning("[IMP:7][notifications][audit] audit-fallback failed (non-fatal): %s", e)


# endregion FUNC__audit_fallback


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

    ## @purpose  Send a text message to Telegram using the Bot API. Транспорт единого
    ##            notifier-контракта (DevPlan 003 B1 — перенесён из telegram_notifier;
    ##            telegram_notifier — shim-реэкспорт). Non-fatal: never raises.
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
            "[IMP:9][notifications][send_telegram] DELIVERY FAILED: TELEGRAM_BOT_TOKEN or "
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
            "[IMP:7][notifications][send_telegram] Using proxy: %s",
            proxy_url,
        )
        proxy_handler = urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        })
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()

    # ── Execute POST ──
    try:
        logger.info(
            "[IMP:7][notifications][send_telegram] Sending notification to chat %s",
            chat,
        )
        # opener.open → Any (typeshed urllib); HTTPResponse-граница (W11) — .status типизирован
        with cast(HTTPResponse, opener.open(req, timeout=SSH_CONNECT_TIMEOUT)) as resp:
            if resp.status == HTTP_OK:
                logger.info(
                    "[IMP:9][notifications][send_telegram] Notification sent successfully to chat %s",
                    chat,
                )
                return True
            logger.warning(
                "[IMP:9][notifications][send_telegram] DELIVERY FAILED: Telegram API returned HTTP %d (proxy=%s)",
                resp.status,
                proxy_state,
            )
            return False
    except (OSError, urllib.error.URLError) as e:
        logger.warning(
            "[IMP:9][notifications][send_telegram] DELIVERY FAILED: %s (proxy=%s)",
            e,
            proxy_state,
        )
        return False


# endregion FUNC_send_telegram


# region FUNC_notify_event
def notify_event(
    n: Notification,
    *,
    env: Mapping[str, str] | None = None,
    secrets_file: str | None = None,
    proxy_url: str | None = None,
    direct_https: bool = False,
    send_fn: Callable[..., bool] | None = None,
    audit_fn: Callable[..., None] | None = None,
    throttle_registry: dict[tuple[str, str], float] | None = None,
    now: float | None = None,
) -> bool:
    """Единая точка отправки: throttle → resolve_chat_id → format_envelope → send_telegram → audit-fallback.

    ▶ ┌n┐ → ◇ severity normalize → ◇ throttle?(event,fingerprint) → IMP:8 suppressed → ◇ resolve chat → ◇ format_envelope → ○ send (proxy) → ⊕ ok? → ⎋ True (всегда)

    ## @purpose  ЕДИНАЯ точка отправки всех Python-отправителей (DevPlan 003 §4 data flow):
    ##           throttle/dedup по (event, fingerprint), SoT severity→chat, единый конверт,
    ##           транспорт нода=Tor/CI=прямой HTTPS, audit-fallback при провале.
    ##           ВСЕГДА возвращает True (инвариант 1 — уведомление не блокирует операцию).
    ## @io       ⇥ n: Notification — payload события
    ##           ⇥ env: Mapping | None — DI окружение (None → os.environ)
    ##           ⇥ secrets_file: str | None — secrets.env (нода; None → пропуск)
    ##           ⇥ proxy_url: str | None — прокси доставки; None → auto-resolve из env
    ##             TELEGRAM_PROXY_URL (нода: Tor/Privoxy — safe default, TRAP[BUG] 141)
    ##           ⇥ direct_https: bool — True → proxy принудительно None (ТОЛЬКО CI/GitHub Actions,
    ##             вне ноды); перекрывает proxy_url и env-резолв
    ##           ⇥ send_fn: Callable[..., bool] | None — DI транспорта (None → telegram_notifier.send_telegram,
    ##             ленивый импорт; TRAP[DECISION] 160 W4b: инъекция в потребителя, не в транспорт)
    ##           ⇥ audit_fn: Callable[..., None] | None — DI audit-fallback
    ##           ⇥ throttle_registry: dict | None — DI dedup-реестр (None → модульный)
    ##           ⇥ now: float | None — DI время (тесты)
    ##           → ⎋ bool — ВСЕГДА True (non-blocking контракт; False не возвращается)
    ## @complexity O(1) + 1 HTTP POST (30s timeout)
    ## @invariants
    ##   - severity нормализуется: не из VALID_SEVERITIES → "info" (IMP:7 warning)
    ##   - Throttle: окно из каталога (throttle_min) или DEFAULT_THROTTLE_SECONDS;
    ##     подавление → IMP:8 suppressed, True; реестр обновляется ТОЛЬКО при успехе
    ##   - Токен/чат отсутствуют → IMP:7, True (неблокирующий; missing-secrets ≠ ошибка операции)
    ##   - Провал доставки → IMP:9 DELIVERY FAILED + audit-fallback, True
    ##   - Прямой HTTPS из ноды НЕ возникает: proxy default = env TELEGRAM_PROXY_URL (Tor/Privoxy);
    ##     direct_https=True — ТОЛЬКО для CI (утечка IP ноды, TRAP[BUG] 141)
    ## @raises   Никогда (все исключения ловятся; fallback-пути не маскируют основную логику)
    """
    severity = n.severity if n.severity in VALID_SEVERITIES else "info"
    if n.severity not in VALID_SEVERITIES:
        logger.warning("[IMP:7][notifications][notify_event] Unknown severity %r — normalized to info", n.severity)
        n = Notification(
            severity=severity,
            context=n.context,
            event=n.event,
            message=n.message,
            details=list(n.details),
            corr_id=n.corr_id,
            links=list(n.links),
            action=n.action,
            fingerprint=n.fingerprint,
            ts=n.ts,
        )

    fingerprint = n.fingerprint or n.message
    ts = now if now is not None else datetime.now(timezone.utc).timestamp()
    registry = _THROTTLE_REGISTRY if throttle_registry is None else throttle_registry

    # ── Throttle/dedup по (event, fingerprint) ──
    dedup_key = (n.event or "-", fingerprint)
    catalog = _load_catalog()
    entry = catalog.get(n.event, {}) if n.event else {}
    window = DEFAULT_THROTTLE_SECONDS
    throttle_min = entry.get("throttle_min") if isinstance(entry, dict) else None
    if isinstance(throttle_min, (int, float)) and float(throttle_min) > 0:
        window = float(throttle_min) * 60.0
    last_sent = registry.get(dedup_key)
    if last_sent is not None and ts - last_sent < window:
        logger.info(
            "[IMP:8][notifications][notify_event] SUPPRESSED (throttle): event=%s fingerprint=%r window=%.0fs",
            n.event or "-",
            fingerprint[:80],
            window,
        )
        return True

    resolved_env = _resolve_env(env, secrets_file)
    token = resolved_env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("[IMP:7][notifications][notify_event] TELEGRAM_BOT_TOKEN not set — notification skipped")
        return True
    chat_id = resolve_chat_id(severity, resolved_env)
    if not chat_id:
        logger.warning(
            "[IMP:7][notifications][notify_event] No TELEGRAM_CHAT_ID resolved (severity=%s, event=%s)",
            severity,
            n.event or "-",
        )
        return True

    # ── Прокси: direct_https (CI) > proxy_url > env TELEGRAM_PROXY_URL (нода: Tor/Privoxy) ──
    # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Telegram из контейнера/ноды ТОЛЬКО через Tor/Privoxy
    # · Symptom: api.telegram.org недоступен напрямую из ноды (IP-block); прямой HTTPS с ноды = утечка IP
    # · Root: direct-HTTP fallback добавлялся как «простое решение» (141)
    # · Fix: safe default — env TELEGRAM_PROXY_URL; direct_https=True ТОЛЬКО из CI (notify-ci/heartbeat-check)
    # · Prevention: direct_https — явный opt-in флаг; нода без прокси → skip (не direct)
    if direct_https:
        proxy = None
    elif proxy_url is not None:
        proxy = proxy_url
    else:
        proxy = resolved_env.get("TELEGRAM_PROXY_URL") or None

    envelope = format_envelope(n)
    proxy_state = "set" if proxy else "none"
    try:
        if send_fn is not None:
            ok = bool(send_fn(envelope, bot_token=token, chat_id=chat_id, proxy_url=proxy, parse_mode="HTML"))
        else:
            # Транспорт живёт в этом модуле (DevPlan 003 B1 — разрыв цикла
            # telegram_notifier ↔ notifications; telegram_notifier — shim-реэкспорт)
            ok = send_telegram(envelope, bot_token=token, chat_id=chat_id, proxy_url=proxy, parse_mode="HTML")
    # ruff: ignore[BLE001] — best-effort контракт: ЛЮБОЙ сбой транспорта → fallback, True
    except Exception as e:  # noqa: EXC — best-effort non-blocking контракт notify_event (DevPlan 003)
        logger.warning(
            "[IMP:9][notifications][notify_event] DELIVERY FAILED (severity=%s, event=%s, proxy=%s): %s",
            severity,
            n.event or "-",
            proxy_state,
            e,
        )
        _audit_fallback(n, f"transport exception: {e}", audit_fn)
        return True

    if ok:
        registry[dedup_key] = ts
        logger.info(
            "[IMP:9][notifications][notify_event] Notification sent (severity=%s, event=%s, chat=%s)",
            severity,
            n.event or "-",
            chat_id,
        )
    else:
        logger.warning(
            "[IMP:9][notifications][notify_event] DELIVERY FAILED (severity=%s, event=%s, proxy=%s)",
            severity,
            n.event or "-",
            proxy_state,
        )
        _audit_fallback(n, f"send_telegram returned False (proxy={proxy_state})", audit_fn)
    return True


# endregion FUNC_notify_event


# region DATACLASS_CliArgs
class CliArgs(argparse.Namespace):
    """Типизированный namespace CLI (W11): поля заполняет parse_args."""

    command: str  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills
    severity: str  # pyright: ignore[reportUninitializedInstanceVariable]
    context: str  # pyright: ignore[reportUninitializedInstanceVariable]
    event: str  # pyright: ignore[reportUninitializedInstanceVariable]
    corr_id: str  # pyright: ignore[reportUninitializedInstanceVariable]
    link: list[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    action: str  # pyright: ignore[reportUninitializedInstanceVariable]
    detail: list[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    secrets_file: str  # pyright: ignore[reportUninitializedInstanceVariable]
    message: str  # pyright: ignore[reportUninitializedInstanceVariable]


# endregion DATACLASS_CliArgs


# region FUNC__default_secrets_file
def _default_secrets_file() -> str:
    """Канонический secrets.env путь (142 W2: persistent /var/lib/platform/run)."""
    # Ленивый импорт (stdlib-only module-level канон); резолвер SoT — deploy_paths
    # (гейт test_gate_run_paths_sole: raw-литералы /var/lib/platform вне deploy_paths — RED)
    from core.internal.shared.deploy_paths import secrets_env_file

    return str(secrets_env_file())


# endregion FUNC__default_secrets_file


# region FUNC_main
def main(
    argv: list[str] | None = None,
    *,
    notify_fn: Callable[..., bool] | None = None,
) -> int:
    """CLI: notify (нода: Tor/Privoxy) | notify-ci (CI: прямой HTTPS). Always exit 0.

    ▶ ┌argv + env┐ → ◇ parse → ◇ notify/notify-ci dispatch → ○ notify_event → ⎋ 0

    ## @purpose  Shell-фасады и CI-workflows: python3 -m core.internal.shared.notifications
    ##            notify|notify-ci (DevPlan 003 A1 — composite action вызывает notify-ci).
    ##            Оба сабкоманда non-blocking: exit 0 ВСЕГДА (уведомление не роняет
    ##            деплой/gate). Секреты — через env (GH Secrets / secrets.env), НЕ argv.
    ## @io       ⇥ argv → ⎋ int — всегда 0
    ##           ⇥ notify_fn: Callable | None — DI-шов (тесты: перехват Notification вместо
    ##             реальной отправки; None → notify_event). Паттерн W-H DevPlan 163.
    ## @complexity O(1) + 1 HTTP POST
    ## @invariants
    ##   - notify: env = os.environ + secrets.env (--secrets-file); прокси — TELEGRAM_PROXY_URL
    ##     из env (нода: Tor/Privoxy); прямой HTTPS из ноды запрещён (TRAP[BUG] 141)
    ##   - notify-ci: env = os.environ (GH Secrets как env); direct_https=True ВСЕГДА (CI:
    ##     прямой HTTPS); TELEGRAM_PROXY_URL из окружения ИГНОРИРУЕТСЯ (CI-раннер вне ноды)
    ##   - Все значения сообщения — параметрами/env; секреты — только env (никаких токенов в argv)
    ##   - exit 0 даже при провале (неблокирующий контракт; лог IMP:9 DELIVERY FAILED)
    """
    parser = argparse.ArgumentParser(description="Unified platform notifications (DevPlan 003)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("notify", "Node path: Tor/Privoxy proxy from secrets.env (TELEGRAM_PROXY_URL)"),
        ("notify-ci", "CI path: direct HTTPS from GitHub Actions env (no proxy)"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--severity", default="info", choices=VALID_SEVERITIES, help="critical|warning|info")
        sub.add_argument("--context", default="platform", help="Context prefix in message")
        sub.add_argument("--event", default="", help="Notification-catalog event id (parity-gated)")
        sub.add_argument("--corr-id", default="", help="Correlation id (CI run id / deploy sha)")
        sub.add_argument("--link", action="append", default=[], help="Link URL (repeatable)")
        sub.add_argument("--action", default="", help="Suggested action (footer 💡)")
        sub.add_argument("--detail", action="append", default=[], help="Detail line (repeatable)")
        sub.add_argument("message", nargs="?", default="", help="Message text")
        if name == "notify":
            sub.add_argument(
                "--secrets-file",
                default="",
                help=f"secrets.env path (default: {_default_secrets_file()})",
            )

    args = parser.parse_args(argv, namespace=CliArgs())
    if args.command == "notify":
        # Нода: env = os.environ + secrets.env; прокси — TELEGRAM_PROXY_URL (Tor/Privoxy)
        n = Notification(
            severity=args.severity,
            context=args.context,
            event=args.event,
            message=args.message,
            details=list(args.detail),
            corr_id=args.corr_id,
            links=list(args.link),
            action=args.action,
        )
        secrets_file = args.secrets_file or _default_secrets_file()
        proxy = os.environ.get("TELEGRAM_PROXY_URL")
        if not proxy:
            resolved = _resolve_env(None, secrets_file)
            proxy = resolved.get("TELEGRAM_PROXY_URL")
        if notify_fn is not None:
            notify_fn(n)
        else:
            notify_event(n, secrets_file=secrets_file, proxy_url=proxy)
    else:
        # notify-ci: env-контракт composite action (.github/actions/notify-telegram):
        # NOTIFY_* + TELEGRAM_* — GH Secrets как env; env значения перекрывают argv.
        # Прямой HTTPS (CI-раннер вне ноды): direct_https=True — прокси принудительно None,
        # TELEGRAM_PROXY_URL из окружения ИГНОРИРУЕТСЯ (TRAP[BUG] 141: direct только вне ноды)
        ci_env = os.environ
        n = Notification(
            severity=ci_env.get("NOTIFY_SEVERITY", args.severity),
            context=ci_env.get("NOTIFY_CONTEXT", args.context),
            event=ci_env.get("NOTIFY_EVENT", args.event),
            message=ci_env.get("NOTIFY_MESSAGE", args.message),
            details=[d for d in ci_env.get("NOTIFY_DETAILS", "").splitlines() if d.strip()] or list(args.detail),
            corr_id=ci_env.get("NOTIFY_CORR_ID", args.corr_id),
            links=[link for link in ci_env.get("NOTIFY_LINK", "").splitlines() if link.strip()] or list(args.link),
            action=ci_env.get("NOTIFY_ACTION", args.action),
        )
        if notify_fn is not None:
            notify_fn(n)
        else:
            notify_event(n, direct_https=True)
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    import sys

    sys.exit(main())
