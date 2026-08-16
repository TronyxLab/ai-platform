#!/usr/bin/env python3
# GREP_SUMMARY: heartbeat-check, s3-list, staleness, out-of-band, dead-man-switch-reader, notify-critical, ci-cron, 003-A2
# STRUCTURE: ▶ build_s3_client (read-only S3_READONLY_*) → ○ list_objects_v2({prefix}/heartbeat/) → ○ auto-detect nodes (heartbeat/{node}/heartbeat.json) → ◇ stale >2ч? → ⚡ notify_event(critical, heartbeat.stale) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Reader dead-man's switch heartbeat (DevPlan 003 A2, out-of-band живучесть S4):
##           GitHub Actions cron (*/30) читает S3 {prefix}/heartbeat/ → авто-обнаружение нод →
##           stale >2ч → Telegram critical (прямой HTTPS). Узел умер целиком → heartbeat
##           перестаёт обновляться → алерт извне (heartbeat.py writer — backup-cron на ноде).
## @scope    CI-крон: .github/workflows/heartbeat-check.yml вызывает напрямую (boto3 в CI).
##           НЕ systemd-путь: boto3 разрешён (DevPlan 003 REQUIRES: «boto3 — только CI-крон»).
##           DI (DevPlan 167 D4): env/client_factory/notify_fn параметрами — тесты без патчей.
## @invariants
##   1. S3-ключи: {prefix}/heartbeat/{node}/heartbeat.json (паритет heartbeat.py writer, 162 W6-1)
##   2. Read-only креды: S3_READONLY_ACCESS_KEY/S3_READONLY_SECRET_KEY (отдельный read-only IAM
##      ключ; мастер-ключи НЕ переиспользуются — DevPlan 003 B5). Отсутствие → IMP:10 + exit 1
##      (конфиг-ошибка честна, R4: NO_SERVICE = FAIL)
##   3. Stale-порог: --stale-hours (default 2) — LastModified объекта старше порога → stale
##   4. Авто-обнаружение нод: каждый объект heartbeat/{node}/heartbeat.json = нода {node}
##   5. Stale-ноды → notify_event(severity=critical, event="heartbeat.stale") c proxy_url=None
##      (прямой HTTPS из CI; TRAP[BUG] 141 — direct HTTPS только вне ноды)
##   6. S3-ошибка → IMP:10 + exit 1 (heartbeat-reader молчать не может — тихий отказ запрещён)
##   7. --dry-run: план (стали/свежие), 0 уведомлений
##   8. exit 0 при отсутствии stale-нод; exit 1 только при S3-ошибке/конфиг-ошибке
## @rationale  Внешний чекер по DevPlan 162 W6-1 validation: «External checker: свежесть
##             LastModified >2ч → алерт». S3 — off-node сигнал жизни; проверка из CI
##             переживает смерть ноды (in-band алертер умирает с пациентом).
## @changes  2026-08-16 | DevPlan 003 A2 — created
## @modulemap
##   build_s3_client [W:1] — read-only boto3-клиент (S3_READONLY_* env)
##   list_heartbeats [W:1] — list_objects_v2 + парсинг {node: LastModified}
##   find_stale [W:1] — stale-порог → [node, ...]
##   main [W:1] — оркестрация + CLI (--stale-hours/--dry-run) → exit 0|1
## @usecases
##   - CI cron: python3 core/internal/scripts/heartbeat_check.py (env S3_* + TELEGRAM_*)
##   - dry-run: python3 heartbeat_check.py --dry-run
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import sys
import typing
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

logger = logging.getLogger(__name__)

DEFAULT_S3_ENDPOINT_URL = "https://s3.timeweb.cloud"
DEFAULT_S3_REGION = "ru-1"
DEFAULT_S3_PREFIX = "platform/backups"  # канон: prefix общий для бэкапов и heartbeat (162 W6-1)
DEFAULT_STALE_HOURS = 2  # DevPlan 162 W6-1 / 003 A2: stale >2ч → критический алерт

HEARTBEAT_OBJECT_SUFFIX = "/heartbeat.json"


# region FUNC__env_str
## @purpose  env-строка с default fallback (DI: env dict инжектится тестами — 167 D4).
## @io       ⇥ name: str, default: str, env: dict | None → ⎋ str
## @complexity O(1)
def _env_str(name: str, default: str, env: dict[str, str] | None = None) -> str:
    """Read a string env var with default (DI: env dict injected by tests)."""
    source = os.environ if env is None else env
    return source.get(name, default)


# endregion FUNC__env_str


# region DATA_CliArgs
@dataclass
class CliArgs:
    """Типизированные аргументы CLI (W11: argparse.Namespace → dataclass-namespace)."""

    stale_hours: float = DEFAULT_STALE_HOURS
    dry_run: bool = False


# endregion DATA_CliArgs


# region FUNC_build_s3_client
## @purpose  Построение read-only boto3 S3-клиента из S3_READONLY_* env (endpoint/region/
##           keys/bucket). Read-only IAM — отдельный ключ (DevPlan 003 B5), НЕ мастер-ключи.
## @io       ⇥ env: dict | None → ⎋ tuple[boto3 client, str bucket] ⚡ RuntimeError — креды отсутствуют
## @complexity O(1)
## @invariants
##   - S3_READONLY_ACCESS_KEY/S3_READONLY_SECRET_KEY ОБЯЗАТЕЛЬНЫ (иначе конфиг-ошибка exit 1)
##   - Endpoint/region/prefix — канон upload-s3.sh (S3_ENDPOINT_URL/S3_REGION/S3_PREFIX)
##   - boto3 Config: connect/read timeout + 3 retries (паритет heartbeat.py writer)
def build_s3_client(env: dict[str, str] | None = None) -> tuple[object, str]:
    """Build read-only boto3 S3 client from S3_READONLY_* env vars. Raises on missing creds."""
    import boto3  # лениво: boto3 только в CI-кроне (DevPlan 003 REQUIRES)
    from botocore.config import Config

    endpoint = _env_str("S3_ENDPOINT_URL", DEFAULT_S3_ENDPOINT_URL, env)
    region = _env_str("S3_REGION", DEFAULT_S3_REGION, env)
    access_key = _env_str("S3_READONLY_ACCESS_KEY", "", env)
    secret_key = _env_str("S3_READONLY_SECRET_KEY", "", env)
    bucket = _env_str("S3_BUCKET", "", env)
    if not access_key or not secret_key:
        missing_creds_msg = "S3_READONLY credentials missing"
        logger.error(
            "[IMP:10][heartbeat_check][s3] S3_READONLY_ACCESS_KEY/S3_READONLY_SECRET_KEY not set — "
            "heartbeat-checker требует отдельный read-only IAM ключ (DevPlan 003 B5)"
        )
        # Канон исключений платформы (bare-raise детектор: RuntimeError вне typed-иерархии — RED)
        from core.internal.shared.exceptions import PlatformError

        raise PlatformError(missing_creds_msg)

    session = boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    client = session.client(
        "s3",
        endpoint_url=endpoint,
        config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3}),
    )
    logger.info(
        "[IMP:7][heartbeat_check][s3] S3 read-only client built: bucket=%s endpoint=%s region=%s",
        bucket,
        endpoint,
        region,
    )
    return client, bucket


# endregion FUNC_build_s3_client


# region FUNC_list_heartbeats
## @purpose  list_objects_v2 по префиксу {prefix}/heartbeat/ → {node: LastModified (datetime UTC)}.
##           Авто-обнаружение нод: каждый объект heartbeat/{node}/heartbeat.json.
## @io       ⇥ client, bucket: str, prefix: str → ⎋ dict[str, datetime]
##           ⚡ Exception — S3-ошибка (exit 1 в main; тихий отказ запрещён)
## @complexity O(N) — N объектов (pagination)
## @invariants
##   - Ключ объекта парсится: имя до финального "/" = node (после {prefix}/heartbeat/)
##   - Объекты без суффикса heartbeat.json игнорируются (совместимость с другими файлами prefix)
##   - Pagination: ContinuationToken цикл (IsTruncated)
def list_heartbeats(client: object, bucket: str, prefix: str) -> dict[str, datetime]:
    """List heartbeat objects → {node: LastModified}. Raises on S3 error."""
    hb_prefix = f"{prefix.rstrip('/')}/heartbeat/"
    nodes: dict[str, datetime] = {}
    continuation: str | None = None
    while True:
        params: dict[str, object] = {"Bucket": bucket, "Prefix": hb_prefix}
        if continuation:
            params["ContinuationToken"] = continuation
        # W11: boto3 list_objects_v2 → Any; JSON-граница через dict-акцесс
        # (pyright ignore — boto3 external untyped-оверлоады)
        resp = cast(
            "dict[str, object]",
            client.list_objects_v2(**params),  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue]
        )
        contents = resp.get("Contents")
        if isinstance(contents, list):
            for obj in contents:
                if not isinstance(obj, dict):
                    continue
                key = str(obj.get("Key", ""))
                if not key.endswith(HEARTBEAT_OBJECT_SUFFIX):
                    continue
                # {prefix}/heartbeat/{node}/heartbeat.json → node — часть между heartbeat/ и /heartbeat.json
                rel = key[len(hb_prefix) : -len(HEARTBEAT_OBJECT_SUFFIX)]
                if not rel or "/" in rel:
                    continue
                lm_raw = obj.get("LastModified")
                last = lm_raw if isinstance(lm_raw, datetime) else datetime.now(timezone.utc)
                nodes[rel] = last
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
        continuation = str(token) if token else None
        if not continuation:
            break
    logger.info("[IMP:8][heartbeat_check][list] %d heartbeat object(s) under %s", len(nodes), hb_prefix)
    return nodes


# endregion FUNC_list_heartbeats


# region FUNC__fetch_one_payload
## @purpose  GET + parse одного heartbeat-объекта (003 A3). Ошибка → None + IMP:7 WARN
##           (best-effort per-node: битая нода не роняет всего reader'а).
## @io       ⇥ client, bucket: str, key: str → ⎋ dict[str, object] | None
## @complexity O(1) — один get_object
@typing.runtime_checkable
class _ReadableBody(typing.Protocol):
    """StreamingBody-подобный протокол: read() → bytes (boto3 get_object Body)."""

    def read(self) -> bytes: ...


def _fetch_one_payload(client: object, bucket: str, key: str) -> dict[str, object] | None:
    """Fetch and parse one heartbeat JSON payload; None on any read/parse error."""
    import json as _json

    resp = cast(
        "dict[str, object]",
        client.get_object(Bucket=bucket, Key=key),  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue]
    )
    body_raw: object = resp.get("Body")
    data_raw = cast(
        object,
        (
            _json.loads(body_raw.read().decode("utf-8"))
            if isinstance(body_raw, _ReadableBody)
            else _json.loads(str(body_raw or "{}"))
        ),
    )
    return cast("dict[str, object]", data_raw) if isinstance(data_raw, dict) else None


# endregion FUNC__fetch_one_payload


# region FUNC__fetch_payloads
## @purpose  GET тела heartbeat-объектов (003 A3): {node: dict payload}. Tor-canary
##           (tor_chain_down) читается out-of-band ТОЛЬКО отсюда. Ошибка GET/JSON —
##           WARN + пропуск ноды (payload None) — читатель не падает на одной битой ноде.
## @io       ⇥ client, bucket: str, prefix: str, nodes: dict[str, datetime] → ⎋ dict[str, dict]
## @complexity O(N) — N get_object
def _fetch_payloads(
    client: object, bucket: str, prefix: str, nodes: dict[str, datetime]
) -> dict[str, dict[str, object]]:
    """Fetch parsed JSON payloads for each node's heartbeat object. Skips unreadable nodes."""
    hb_prefix = f"{prefix.rstrip('/')}/heartbeat/"
    payloads: dict[str, dict[str, object]] = {}
    for node in sorted(nodes):
        key = f"{hb_prefix}{node}{HEARTBEAT_OBJECT_SUFFIX}"
        try:
            data = _fetch_one_payload(client, bucket, key)
        # ruff: ignore[BLE001] — best-effort per-node read: битая нода не роняет всего reader'а
        except Exception as exc:  # noqa: EXC — per-node payload read (003 A3 best-effort)
            logger.info("[IMP:7][heartbeat_check][payload] %s: payload unreadable (%s) — skipped", node, exc)
            continue
        if data is not None:
            payloads[node] = data
        else:
            logger.info("[IMP:7][heartbeat_check][payload] %s: non-dict body — skipped", node)
    logger.info("[IMP:8][heartbeat_check][payload] %d payload(s) fetched", len(payloads))
    return payloads


# endregion FUNC__fetch_payloads


# region FUNC_find_stale
## @purpose  Ноды со stale-сердцебиением: LastModified старше порога → [(node, age_hours)].
## @io       ⇥ nodes: dict[str, datetime], stale_hours: float, now: datetime | None → ⎋ list[tuple[str, float]]
## @complexity O(N)
def find_stale(
    nodes: dict[str, datetime],
    stale_hours: float = DEFAULT_STALE_HOURS,
    now: datetime | None = None,
) -> list[tuple[str, float]]:
    """Return [(node, age_hours)] whose heartbeat is older than stale_hours."""
    ref = now if now is not None else datetime.now(timezone.utc)
    stale: list[tuple[str, float]] = []
    for node, last in sorted(nodes.items()):
        age = (ref - last).total_seconds() / 3600.0
        if age > stale_hours:
            stale.append((node, age))
    if stale:
        logger.info("[IMP:9][heartbeat_check][stale] %d stale node(s): %s", len(stale), ", ".join(n for n, _ in stale))
    else:
        logger.info("[IMP:9][heartbeat_check][stale] 0 stale nodes (threshold=%.1fh)", stale_hours)
    return stale


# endregion FUNC_find_stale


# region FUNC_main
## @purpose  Оркестрация: build client → list → payloads → find_stale → stale? notify critical
##           → tor_chain_down? notify tor.chain_down (CI, direct HTTPS) → exit 0|1.
##           S3-ошибка/конфиг-ошибка → IMP:10 + exit 1 (тихий отказ запрещён).
## @io       ⇥ argv: list | None (--stale-hours/--dry-run), env: dict | None (DI),
##              client_factory: Callable | None (DI — FakeS3 вместо boto3),
##              notify_fn: Callable | None (DI — перехват heartbeat.stale),
##              tor_notify_fn: Callable | None (DI — перехват tor.chain_down, 003 A3)
##           → ⎋ int (0 = ok, 1 = S3/конфиг-ошибка)
## @complexity O(N) — N объектов
## @invariants
##   - stale-ноды → notify_event(Notification(critical, event="heartbeat.stale"), proxy_url=None)
##   - tor_chain_down в payload → notify_event(critical, event="tor.chain_down") ДАЖЕ при
##     свежем heartbeat (003 A3: нода жива, Tor мёртв — out-of-band канарейка)
##   - dry-run: план без уведомлений; exit 0
##   - S3-ошибка → IMP:10 + exit 1 (heartbeat-reader не может молчать)
##   - DI (167 D4): env=None → os.environ; client_factory=None → build_s3_client(env)
def main(
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    client_factory: Callable[[], tuple[object, str]] | None = None,
    notify_fn: Callable[..., bool] | None = None,
    tor_notify_fn: Callable[..., bool] | None = None,
) -> int:
    """Run one heartbeat-check pass. Returns exit code (0 ok / 1 S3/config error)."""
    parser = argparse.ArgumentParser(description="Heartbeat reader: S3 list + staleness → Telegram (DevPlan 003 A2)")
    parser.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS, help="Stale threshold in hours")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without any notification")
    args = parser.parse_args(argv, namespace=CliArgs())

    prefix = _env_str("S3_PREFIX", DEFAULT_S3_PREFIX, env)
    logger.info(
        "[IMP:7][heartbeat_check][main] prefix=%s stale_hours=%.1f dry_run=%s",
        prefix,
        args.stale_hours,
        args.dry_run,
    )

    try:
        if client_factory is not None:
            client, bucket = client_factory()
        else:
            client, bucket = build_s3_client(env)
    # ruff: ignore[BLE001] — top-level CLI handler: S3-ошибка/конфиг → честный exit 1 (тихий отказ запрещён)
    except Exception as exc:  # noqa: EXC — top-level CLI handler (heartbeat_check main)
        logger.error("[IMP:10][heartbeat_check] S3 FAIL during client build: %s", exc)
        return 1

    try:
        nodes = list_heartbeats(client, bucket, prefix)
    # ruff: ignore[BLE001] — top-level CLI handler: S3-ошибка/конфиг → честный exit 1 (тихий отказ запрещён)
    except Exception as exc:  # noqa: EXC — top-level CLI handler (heartbeat_check main)
        logger.error("[IMP:10][heartbeat_check] S3 FAIL during heartbeat check: %s", exc)
        return 1
    # best-effort: per-node ошибки уже WARN-обработаны внутри _fetch_payloads
    payloads = _fetch_payloads(client, bucket, prefix, nodes)

    stale = find_stale(nodes, args.stale_hours)
    tor_down = [n for n in sorted(payloads) if payloads[n].get("tor_chain_down") is True]
    if not stale and not tor_down:
        logger.info("[IMP:9][heartbeat_check] All nodes fresh, no tor-chain alerts")
        return 0
    if args.dry_run:
        for node, age in stale:
            logger.info("[IMP:8][heartbeat_check][dry-run] WOULD notify: node=%s stale=%.1fh", node, age)
        for node in tor_down:
            logger.info("[IMP:8][heartbeat_check][dry-run] WOULD notify tor.chain_down: node=%s", node)
        logger.info(
            "[IMP:7][heartbeat_check][dry-run] %d stale + %d tor-down node(s) — no mutation", len(stale), len(tor_down)
        )
        return 0

    details = [f"{node}: stale {age:.1f}h (last heartbeat >{args.stale_hours:.0f}h ago)" for node, age in stale]
    if stale and notify_fn is not None:
        notify_fn(details)
    elif stale:
        from core.internal.shared.notifications import Notification, notify_event  # лениво (stdlib канон)

        notify_event(
            Notification(
                severity="critical",
                context="heartbeat",
                event="heartbeat.stale",
                message=f"Heartbeat STALE >{args.stale_hours:.0f}h — {len(stale)} node(s) may be DOWN",
                details=details,
                corr_id=f"heartbeat-check-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}",
                action="Node may be down — check VPS console / SSH",
            ),
            direct_https=True,  # CI: прямой HTTPS (TRAP[BUG] 141 — direct HTTPS только вне ноды)
        )
    tor_details = [f"{n}: tor-chain-state red (canary, 003 A3)" for n in tor_down]
    if tor_down and tor_notify_fn is not None:
        tor_notify_fn(tor_details)
    elif tor_down:
        from core.internal.shared.notifications import Notification, notify_event  # лениво (stdlib канон)

        notify_event(
            Notification(
                severity="critical",
                context="heartbeat",
                event="tor.chain_down",
                message=(
                    f"Tor→Privoxy→Telegram chain DOWN on {len(tor_down)} node(s): "
                    + ", ".join(tor_down)
                    + " — нода жива (heartbeat fresh), но out-of-band Telegram-доставка мертва"
                ),
                details=tor_details,
                corr_id=f"tor-chain-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}",
                action="Check tor proxy on the node: make check-security / tor_proxy_check.py",
            ),
            direct_https=True,  # CI: прямой HTTPS — нода не участвует (Tor мёртв)
        )
    logger.info(
        "[IMP:9][heartbeat_check] Notified critical: %d stale node(s), %d tor-down node(s)",
        len(stale),
        len(tor_down),
    )
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
