#!/usr/bin/env python3
# GREP_SUMMARY: heartbeat, dead-man-switch, s3-object, out-of-band-monitoring, node-alive, 162-W6-1
# STRUCTURE: ▶ build_s3_client (canon S3_* env) → ○ heartbeat_run ∋ put_object({prefix}/heartbeat/{node}/heartbeat.json, {ts,ok,node}) → ◇ dry-run? план → ⊕ IMP:9 OK | IMP:10 FAIL → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Dead-man's switch heartbeat (DevPlan 162 W6-1, out-of-band monitoring): каждый
##           прогон (cron */15) кладёт в S3 объект {prefix}/heartbeat/{node}/heartbeat.json
##           с ISO8601-timestamp. Внешний checker (uptime-checker / GitHub Actions cron) проверяет
##           свежесть объекта → алерт, если heartbeat stale >2ч. Решает проблему 100% in-band
##           мониторинга: при смерти хоста in-band алертер умирает вместе с пациентом —
##           S3-объект (off-node) остаётся единственным сигналом жизни.
## @scope    Контейнерный модуль backup-cron: /usr/local/bin/heartbeat.py. НЕ импортирует
##           core.internal (контейнерный контракт — образ не содержит core/). S3-клиент —
##           boto3 raw client (S3Client обёртка не имеет put_object — как wal_sync).
## @invariants
##   1. Контейнерный контракт: 0 импортов core.internal (образ backup-cron без core/)
##   2. S3-ключ: {prefix}/heartbeat/{node}/heartbeat.json (prefix = S3_PREFIX, default
##      "platform/backups" — канон upload-s3.sh / wal_sync)
##   3. Body: {"ts": "<ISO8601 UTC now>", "ok": true, "node": "<NODE_NAME>"} (NODE_NAME env,
##      default "unknown") — JSON-сериализация, перезапись (идемпотентно, overwrite)
##   4. --dry-run: печатает план, 0 мутаций (нет put_object)
##   5. S3-ошибка → [IMP:10][heartbeat] S3 FAIL + exit 1 (heartbeat = off-node сигнал,
##      тихий отказ запрещён — stale heartbeat должен быть алертом, а не тишиной)
##   6. Успех → [IMP:9][heartbeat] HEARTBEAT OK: {key} ts={ts}
## @rationale  Альтернатива внешнему uptime-checker (UptimeRobot/StatusCake): self-hosted,
##           дешевле, не требует third-party. S3 уже на платформе (backup-cron) — 0 новой
##           инфраструктуры. Объект перезаписывается (state-free) — нет состояния для потери.
##           External checker: свежесть LastModified >2ч → алерт (DevPlan 162 W6-1 validation).
## @changes  2026-08-13 | DevPlan 162 W6-1 — создан
## @modulemap
##   build_s3_client [W:1] — boto3 raw client из канонических S3_* env (endpoint/region/keys/bucket)
##   heartbeat_run [W:1] — PUT {prefix}/heartbeat/{node}/heartbeat.json {ts, ok, node} (dry-run aware)
##   main [W:1] — оркестрация + CLI (--dry-run) → exit 0|1
## @usecases
##   - cron: */15 * * * * root python3 /usr/local/bin/heartbeat.py
##   - dry-run: python3 heartbeat.py --dry-run
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from s3_client import Boto3S3  # pyright: ignore[reportImplicitRelativeImport]

logger = logging.getLogger(__name__)

# ── Env-контракт (канон S3_* из upload-s3.sh / wal_sync.py — единый S3-контракт модуля) ──
DEFAULT_S3_ENDPOINT_URL = "https://s3.timeweb.cloud"
DEFAULT_S3_REGION = "ru-1"
DEFAULT_S3_PREFIX = "platform/backups"  # канон: prefix общий для бэкапов и heartbeat (162 W6-1)


# region FUNC__env_str
## @purpose  env-строка с default fallback (паритет wal_sync._env_str). DI (DevPlan 167 D4):
##           env: dict | None = None — тесты инжектят словарь вместо monkeypatch-патча.
## @io       ⇥ name: str, default: str, env: dict[str, str] | None → ⎋ str
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

    dry_run: bool = False


# endregion DATA_CliArgs


# region FUNC_build_s3_client
## @purpose  Построение raw boto3 S3-клиента из канонических S3_* env (endpoint/region/keys/
##           bucket). S3Client обёртка модуля не имеет put_object (только list/delete) —
##           heartbeat использует raw client (паттерн wal_sync.sync → client._client.put_object).
##           DI (DevPlan 167 D4): env: dict | None = None — тесты инжектят словарь.
## @io       ⇥ env: dict[str, str] | None → ⎋ tuple[boto3 client, str bucket]
## @complexity O(1)
## @invariants
##   - Endpoint/region/prefix — канон upload-s3.sh (S3_ENDPOINT_URL/S3_REGION/S3_PREFIX)
##   - Креды из env (S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET)
##   - boto3 Config: connect/read timeout + 3 retries (паритет wal_sync.build_s3_client)
def build_s3_client(env: dict[str, str] | None = None) -> tuple[Boto3S3, str]:
    """Build raw boto3 S3 client + bucket from canonical S3_* env vars (DI: env dict injected)."""
    endpoint = _env_str("S3_ENDPOINT_URL", DEFAULT_S3_ENDPOINT_URL, env)
    region = _env_str("S3_REGION", DEFAULT_S3_REGION, env)
    access_key = _env_str("S3_ACCESS_KEY", "", env)
    secret_key = _env_str("S3_SECRET_KEY", "", env)
    bucket = _env_str("S3_BUCKET", "", env)

    session = boto3.Session(  # публичный API (НЕ boto3.session — v1.0.1 CI-fix: boto3 1.39+ py.typed без stubs → reportAttributeAccessIssue)
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        region_name=region,
    )
    client = cast(
        "Boto3S3",
        session.client(  # pyright: ignore[reportUnknownMemberType] — W11 external boto3 Session.client untyped-оверлоады
            "s3",
            endpoint_url=endpoint,
            config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3}),
        ),
    )  # W11: boto3 → Any → Boto3S3-протокол
    logger.info("[IMP:7][heartbeat][s3] S3 client built: bucket=%s endpoint=%s region=%s", bucket, endpoint, region)
    return client, bucket


# endregion FUNC_build_s3_client


# region FUNC__heartbeat_key
## @purpose  S3-ключ heartbeat-объекта: {prefix}/heartbeat/{node}/heartbeat.json. Node-namespace —
##           несколько нод пишут независимые объекты (внешний checker проверяет свежесть по ноде).
## @io       ⇥ prefix: str, node: str → ⎋ str
## @complexity O(1)
def _heartbeat_key(prefix: str, node: str) -> str:
    """Compose the S3 key for the heartbeat object (heartbeat/{node}/heartbeat.json)."""
    return f"{prefix.rstrip('/')}/heartbeat/{node}/heartbeat.json"


# endregion FUNC__heartbeat_key


# region FUNC_heartbeat_run
## @purpose  Запись heartbeat-объекта: put_object({prefix}/heartbeat/{node}/heartbeat.json,
##           body {ts: ISO8601 UTC now, ok: true, node}). Идемпотентно (overwrite) — state-free.
##           --dry-run: план без put_object.
## @io       ⇥ client: boto3, bucket: str, prefix: str, node: str, dry_run: bool → ⎋ str (key)
##           ⚡ ClientError — S3-ошибка (exit 1 в main)
## @complexity O(1) — один PUT-запрос
## @invariants
##   - body JSON: {"ts": "<ISO8601 UTC>", "ok": true, "node": "<node>"}
##   - dry-run: IMP:8 план, 0 мутаций
##   - S3-ошибка → ClientError raise (exit 1 — heartbeat = off-node сигнал)
def heartbeat_run(client: Boto3S3, bucket: str, prefix: str, node: str, dry_run: bool = False) -> str:
    """Put the heartbeat object to S3 (overwrite, idempotent). Returns the S3 key."""
    key = _heartbeat_key(prefix, node)
    now = datetime.now(timezone.utc)
    body = {
        "ts": now.isoformat(),
        "ok": True,
        "node": node,
    }
    if dry_run:
        logger.info("[IMP:8][heartbeat][dry-run] WOULD put %s → %s", key, json.dumps(body))
        return key
    try:
        client.put_object(Bucket=bucket, Key=key, Body=json.dumps(body).encode("utf-8"))
    except ClientError as exc:
        logger.error("[IMP:10][heartbeat][put] S3 put_object failed for %s: %s", key, exc)
        raise
    logger.info("[IMP:9][heartbeat][put] HEARTBEAT OK: %s ts=%s", key, now.isoformat())
    return key


# endregion FUNC_heartbeat_run


# region FUNC_main
## @purpose  Оркестрация: build client → heartbeat_run → exit 0|1. S3-ошибка → IMP:10 + exit 1.
## @io       ⇥ argv: list[str] | None (--dry-run), env: dict[str, str] | None (DI — тесты
##              инжектят S3_PREFIX/NODE_NAME вместо monkeypatch-патча _env_str),
##              client_factory: Callable[[], tuple[Any, str]] | None (DI — тесты инжектят
##              FakeBoto-клиент вместо monkeypatch build_s3_client) → ⎋ int (0 = ok, 1 = S3-ошибка)
## @complexity O(1)
## @invariants
##   - S3-ошибка → [IMP:10][heartbeat] S3 FAIL + exit 1 (тихий отказ запрещён)
##   - Финал: [IMP:9][heartbeat] HEARTBEAT OK: {key} ts={ts}
##   - DI (DevPlan 167 D4): env=None → os.environ; client_factory=None → build_s3_client(env) —
##     публичная сигнатура обратно-совместима (дефолты = прежнее поведение)
## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · main-сценарии (exit 0 / S3-fail exit 1) через реальный main()
## · Rejected: прямой вызов build_s3_client без параметра (тест патчил build_s3_client/
## ·   _env_str лямбдами — 4 monkeypatch.setattr на 2 сценария)
## · Reason: seam = тестируемость реального вызова main() с client_factory (FakeBoto) — 0 патчей
## · Rev: при появлении второго потребителя конфигурации — вынести env-dict в AppConfig-объект
def main(
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    client_factory: Callable[[], tuple[Boto3S3, str]] | None = None,
) -> int:
    """Run one heartbeat pass. Returns process exit code (0 ok / 1 S3 failure)."""
    parser = argparse.ArgumentParser(description="Dead-man's switch heartbeat → S3 (DevPlan 162 W6-1)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without any mutation")
    args = parser.parse_args(argv, namespace=CliArgs())  # W11: dataclass-namespace
    dry_run = args.dry_run

    prefix = _env_str("S3_PREFIX", DEFAULT_S3_PREFIX, env)
    node = _env_str("NODE_NAME", "unknown", env)
    logger.info(
        "[IMP:7][heartbeat][main] node=%s prefix=%s dry_run=%s",
        node,
        prefix,
        dry_run,
    )

    try:
        if client_factory is not None:
            client, bucket = client_factory()
        else:
            client, bucket = build_s3_client(env)
        key = heartbeat_run(client, bucket, prefix, node, dry_run=dry_run)
    except ClientError as exc:
        logger.error("[IMP:10][heartbeat] S3 FAIL during heartbeat: %s", exc)
        return 1

    logger.info("[IMP:9][heartbeat] HEARTBEAT OK: %s", key)
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
