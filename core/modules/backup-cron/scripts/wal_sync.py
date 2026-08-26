#!/usr/bin/env python3
# GREP_SUMMARY: wal-sync, wal-archive, s3-sync, head-object, safe-delete, retention, dry-run, rate-limit, pg-wal, RPO
# STRUCTURE: ▶ scan_local ┌listdir filter ^([0-9A-F]{24}|[0-9A-F]{8}\.history)$┐ → ○ sync ∋ HEAD→404→PUT (rate-limit, key wal/{node}/{timeline}/) → ○ apply_local_retention (старше 7д И HEAD ok → rm) → ○ apply_s3_retention (wal/{node}/ старше 14д → delete) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  WAL → S3 sync + retention (DevPlan 132 W2, приоритеты C+D): снижение RPO — WAL-архив
##           pg_wal выгружается в S3 каждый час (cron 10 * * * *), локальные файлы удаляются ТОЛЬКО
##           после HEAD-подтверждения в S3 (safe-delete, D3), S3-side retention прунит wal/{node}/.
## @scope    Контейнерный модуль backup-cron: /usr/local/bin/wal_sync.py. НЕ импортирует core.internal
##           (контейнерный контракт — образ не содержит core/). S3-клиент — s3_client.py модуля.
## @invariants
##   1. Контейнерный контракт: 0 импортов core.internal (образ backup-cron без core/)
##   2. D2: HEAD-object — source of truth (идемпотентно: повторный прогон = no-op); state-файла нет
##   3. D3: локальное удаление ТОЛЬКО safe-delete — старше WAL_LOCAL_RETENTION_DAYS (7) И HEAD ok в S3;
##      неподтверждённый файл НИКОГДА не удаляется (RPO-гарантия)
##   4. D4: S3-side purge wal/{node}/ — старше WAL_S3_RETENTION_DAYS (14); префикс wal/{node}/ матчит
##      и вложенные timeline-ключи wal/{node}/{timeline}/ (prefix-scan, 162 W1-4); retention.py НЕ
##      трогается (WAL-имена не парсятся retention.py — unparseable, никогда не удаляются)
##   5. Rate-limit: WAL_MAX_UPLOAD_PER_RUN (200) — максимум PUT-ов за прогон
##   6. S3-ошибка в sync → [IMP:10][wal_sync] S3 FAIL + exit 1 (WAL = RPO-гарантия, тихий отказ запрещён)
##   7. Retention-ошибки non-fatal: IMP:8 warning, прогон завершается exit 0
##   8. --dry-run: печатает план, 0 мутаций (нет PUT/delete/remove)
##   9. Финал: IMP:9 «WAL_SYNC OK: uploaded=N local_retained=M s3_retained=K»
##   10. Timeline namespace (DevPlan 162 W1-4, Solution A): S3-ключ = wal/{node}/{timeline}/{name},
##       timeline = первые 8 hex имени WAL-сегмента (PG timeline ID, lowercase) — исключает коллизию
##       после реинсталла: новый инстанс нумерует WAL с #1, старые #1-#42 уже в S3 → без namespace
##       sync skip по HEAD и PITR нового инстанса сломан. .history файлы (timeline-переходы) синкаются.
## @rationale D2: state-файл uploaded-ключей теряется при пересоздании контейнера; HEAD дешевле
##           (≤1 req/файл) и идемпотентен. D3: слепое удаление по возрасту = потеря PITR-цели при
##           недоступности S3. D4: retention.py группирует по дате из имени (WAL-имена не парсятся).
## @changes  2026-08-04 | DevPlan 132 W2 — создан
## @changes  2026-08-13 | DevPlan 162 W1-4 — PG timeline namespace (ключ wal/{node}/{timeline}/) +
##             .history файлы включены в sync (PITR); миграция старых плоских ключей — server-side purge
## @modulemap
##   scan_local [W:1] — listdir + фильтр ^([0-9A-F]{24}|[0-9A-F]{8}\.history)$ (pg_wal сегменты + timeline history)
##   sync [W:1] — HEAD→404→PUT (без локального удаления), rate-limit, идемпотентность D2
##   apply_local_retention [W:1] — safe-delete D3 (старше 7д И HEAD ok → os.remove, IMP:9)
##   apply_s3_retention [W:1] — list_objects_v2(prefix wal/{node}/, матчит вложенные timeline-ключи) → delete старше 14д (D4)
##   main [W:1] — оркестрация + CLI (--dry-run) → exit 0|1
## @usecases
##   - cron: 10 * * * * root python3 /usr/local/bin/wal_sync.py
##   - dry-run: python3 wal_sync.py --dry-run
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import pathlib
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, TypedDict, cast

from botocore.exceptions import ClientError  # pyright: ignore[reportImplicitRelativeImport]
from s3_client import Boto3S3, S3Client, build_boto3_s3_client  # pyright: ignore[reportImplicitRelativeImport]

logger = logging.getLogger(__name__)

# ── Env-контракт (канон S3_* из upload-s3.sh; WAL_* — DevPlan 132 W2) ──
# WAL-имя = <timeline:8hex><logseg:16hex> (24 hex всего, напр. 000000010000000000000001: timeline 00000001,
# logseg 0000000000000001). .history файлы (напр. 00000002.history = <8hex-timeline>.history) содержат
# timeline-переходы при recovery-переключениях и НУЖНЫ для PITR (DevPlan 162 W1-4: до этого исключались).
# ⚠️ TRAP[DECISION] · 2026-08-13 · — · История-файлы: 8-hex+\.history, НЕ 24-hex+\.history
# · Rejected: буквальный паттерн плана ^[0-9A-F]{24}(\.history)?$ — матчил бы только 24-hex+.history;
# ·   реальные PG history-файлы именуются <8-hex-timeline>.history (00000002.history), поэтому с
# ·   буквальным паттерном .history НЕ включались бы → PITR-цель W1-4 не достигнута
# · Reason: приёмка W1-4 (тест a: «00000002.history» должен матчиться) однозначно фиксирует поведение;
# ·   паттерн ^([0-9A-F]{24}|[0-9A-F]{8}\.history)$ покрывает и сегменты, и реальные history-имена
# · Rev: если PostgreSQL изменит схему именования history-файлов
WAL_SEGMENT_RE = re.compile(r"^([0-9A-F]{24}|[0-9A-F]{8}\.history)$")

DEFAULT_S3_ENDPOINT_URL = "https://s3.timeweb.cloud"
DEFAULT_S3_REGION = "ru-1"
DEFAULT_S3_PREFIX = "platform/backups"
DEFAULT_WAL_ARCHIVE_DIR = "/var/lib/platform/wal-archive"
DEFAULT_LOCAL_RETENTION_DAYS = 7
DEFAULT_S3_RETENTION_DAYS = 14
DEFAULT_MAX_UPLOAD_PER_RUN = 200


# 🧐 TRAP[DECISION] · 2026-08-26 · — · Жёсткий S3-бюджет wal_sync (10/30/×3) сохранён осознанно
# · Rejected: унификация с дефолтом строителя 30/60 (upload/retention)
# · Reason: WAL-sync обслуживает RPO-критичный канал репликации — зависание на широком
#   бюджете откладывало бы обнаружение недоступности S3 и сдвигало бы RPO; жёсткие таймауты
#   дают быстрый fail → retry-цикл cron'а. Значения НЕ изменились относительно прежних
#   inline Config(connect=10, read=30, retries=3) — только именованы (DevPlan 17 T2.4).
# · Rev: если появится требование long-tail объектов (медленный S3-канал) — пересмотреть read.
class _WalSyncS3Timeouts(NamedTuple):
    """Именованный S3-бюджет wal_sync (RPO-критичный канал)."""

    connect: int
    read: int
    max_attempts: int


WAL_SYNC_S3_TIMEOUTS = _WalSyncS3Timeouts(connect=10, read=30, max_attempts=3)


class WalSyncError(Exception):
    """S3-ошибка в sync-фазе — сигнал exit 1 (WAL = RPO-гарантия)."""


# region DATA_WalEntry
class WalEntry(TypedDict):
    """Локальный WAL-файл из архива (граница scan_local): имя/mtime/путь."""

    name: str
    mtime: float
    path: str


# endregion DATA_WalEntry


# region DATA_CliArgs
@dataclass
class CliArgs:
    """Типизированные аргументы CLI (W11: argparse.Namespace → dataclass-namespace)."""

    dry_run: bool = False


# endregion DATA_CliArgs


# region FUNC__env_str
## @purpose  env-строка с default fallback. DI (DevPlan 167 D4): env: dict | None = None —
##           тесты инжектят словарь вместо monkeypatch-патча os.environ (None = os.environ).
## @io       ⇥ name: str, default: str, env: dict[str, str] | None → ⎋ str
## @complexity O(1)
def _env_str(name: str, default: str, env: dict[str, str] | None = None) -> str:
    """Read a string env var with default (DI: env dict injected by tests)."""
    source = os.environ if env is None else env
    return source.get(name, default)


# endregion FUNC__env_str


# region FUNC__env_int
## @purpose  env-int с валидным fallback (invalid → default, IMP:7 warning). DI (167 D4):
##           env: dict | None = None — тесты инжектят словарь вместо патча os.environ.
## @io       ⇥ name: str, default: int, env: dict[str, str] | None → ⎋ int
## @complexity O(1)
def _env_int(name: str, default: int, env: dict[str, str] | None = None) -> int:
    """Read a non-negative int env var with default fallback (DI: env dict injected)."""
    source = os.environ if env is None else env
    raw = source.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[IMP:7][wal_sync][env] %s=%r invalid int — using default %d", name, raw, default)
        return default
    return value if value >= 0 else default


# endregion FUNC__env_int


# region FUNC_scan_local
## @purpose  Сканирование WAL-архива: listdir + фильтр ^([0-9A-F]{24}|[0-9A-F]{8}\.history)$ — сегменты
##           pg_wal И .history файлы (timeline-переходы, PITR, DevPlan 162 W1-4). .backup/labels/
##           прочие не-WAL файлы — исключаются регуляркой.
## @io       ⇥ archive_dir: str → ⎋ list[dict] — [{name, mtime, path}], отсортировано по имени
## @complexity O(N) — N файлов в каталоге
## @invariants
##   - Фильтр строгий: 24 hex UPPERCASE (pg_wal сегмент) ИЛИ 8-hex-timeline + .history
##   - Отсутствующий каталог → IMP:7 warning + [] (не fatal)
##   - Ошибка stat отдельного файла → файл пропускается
def scan_local(archive_dir: str) -> list[WalEntry]:
    r"""List WAL segment + timeline-history files in the archive dir (^([0-9A-F]{24}|[0-9A-F]{8}\.history)$)."""
    try:
        names = os.listdir(archive_dir)
    except FileNotFoundError:
        logger.warning("[IMP:7][wal_sync][scan] Archive dir %s not found — 0 files", archive_dir)
        return []
    except OSError as exc:
        logger.warning("[IMP:7][wal_sync][scan] Cannot list %s: %s", archive_dir, exc)
        return []

    entries: list[WalEntry] = []
    for name in names:
        if not WAL_SEGMENT_RE.fullmatch(name):
            logger.info("[IMP:5][wal_sync][scan] Excluded (not a WAL segment): %s", name)
            continue
        if name.endswith(".history"):
            logger.info("[IMP:8][wal_sync][scan] Timeline history file included (PITR, 162 W1-4): %s", name)
        path = os.path.join(archive_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError as exc:
            # 170 W2-A2 (B3): тихий continue → warning, консистентно с соседними хендлерами scan_local
            logger.warning("[IMP:7][wal_sync][scan] Cannot stat %s — skipping: %s", name, exc)
            continue
        entries.append({"name": name, "mtime": mtime, "path": path})

    entries.sort(key=lambda e: e["name"])
    logger.info("[IMP:7][wal_sync][scan] %d WAL segment(s) in %s", len(entries), archive_dir)
    return entries


# endregion FUNC_scan_local


# region FUNC_build_s3_client
## @purpose  Построение S3-клиента (boto3 + S3Client обёртка модуля) из канонических S3_* env.
##           DI (DevPlan 167 D4): env: dict | None = None — тесты инжектят словарь вместо
##           monkeypatch-патча _env_str (None = os.environ).
## @io       ⇥ env: dict[str, str] | None → ⎋ S3Client (bucket привязан)
## @complexity O(1)
## @invariants
##   - Endpoint/region/prefix — канон upload-s3.sh (S3_ENDPOINT_URL/S3_REGION/S3_PREFIX)
##   - Креды из env (S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET)
##   - boto3 Config: connect/read timeout + 3 retries
def build_s3_client(env: dict[str, str] | None = None) -> S3Client:
    """Build the module S3 client from canonical S3_* env vars (DI: env dict injected)."""
    endpoint = _env_str("S3_ENDPOINT_URL", DEFAULT_S3_ENDPOINT_URL, env)
    region = _env_str("S3_REGION", DEFAULT_S3_REGION, env)
    access_key = _env_str("S3_ACCESS_KEY", "", env)
    secret_key = _env_str("S3_SECRET_KEY", "", env)
    bucket = _env_str("S3_BUCKET", "", env)

    # AI-0073 (DevPlan 17 T2.4): единый строитель s3_client.build_boto3_s3_client;
    # жёсткий RPO-бюджет wal_sync — именованная константа WAL_SYNC_S3_TIMEOUTS
    raw_client = cast(
        "Boto3S3",
        build_boto3_s3_client(
            endpoint_url=endpoint,
            access_key=access_key or None,
            secret_key=secret_key or None,
            region=region,
            connect_timeout=WAL_SYNC_S3_TIMEOUTS.connect,
            read_timeout=WAL_SYNC_S3_TIMEOUTS.read,
            max_attempts=WAL_SYNC_S3_TIMEOUTS.max_attempts,
        ),
    )
    logger.info("[IMP:7][wal_sync][s3] S3 client built: bucket=%s endpoint=%s region=%s", bucket, endpoint, region)
    return S3Client(raw_client, bucket)  # W11: boto3 → Any → Boto3S3-протокол


# endregion FUNC_build_s3_client


# region FUNC__wal_key
## @purpose  S3-ключ WAL-объекта: {prefix}/wal/{node}/{timeline}/{segment}. Timeline-namespace —
##           канонический PG timeline ID (первые 8 hex имени WAL-сегмента) — исключает коллизию
##           после реинсталла (DevPlan 162 W1-4, Solution A).
## @io       ⇥ prefix: str, node: str, name: str → ⎋ str
## @complexity O(1)
## @invariants
##   - timeline = name[:8].lower() — PG timeline ID из имени (000000010000000000000001 → 00000001)
##   - .history имена парсятся по первым 8 hex (00000002.history → timeline 00000002)
def _wal_key(prefix: str, node: str, name: str) -> str:
    """Compose the S3 key for a WAL segment (wal/{node}/{timeline}/ — D4 + timeline namespace)."""
    # Парсим PG timeline ID (первые 8 hex имени WAL-сегмента, напр. 00000001 из 000000010000000000000001).
    # Namespace по timeline исключает коллизию после реинсталла: новый инстанс нумерует WAL с #1,
    # а старые #1-#42 уже в S3 → без namespace wal_sync skip по HEAD и PITR нового инстанса сломан.
    #
    # ⚠️ Migration (DevPlan 162 W1-4, решение оператора): старые плоские ключи
    #    {prefix}/wal/{node}/<24hex> подлежат purge на ноде при первом запуске после деплоя — чужой
    #    timeline невосстановим для нового кластера в любом случае. Purge server-side; репозиторий
    #    только фиксирует решение (плоские ключи больше не создаются этим кодом).
    timeline = name[:8].lower()
    return f"{prefix.rstrip('/')}/wal/{node}/{timeline}/{name}"


# endregion FUNC__wal_key


# region FUNC__head_exists
## @purpose  HEAD-object: True = объект существует, False = 404/NoSuchKey (нет → надо PUT).
## @io       ⇥ client: S3Client, key: str → ⎋ bool ⚡ ClientError (не-404) — S3-ошибка
## @complexity O(1) — один HEAD-запрос
## @invariants
##   - 404 / NoSuchKey → False (объект отсутствует)
##   - Любая другая ClientError → raise (S3-ошибка, exit 1 в sync)
def _head_exists(client: S3Client, key: str) -> bool:
    """Return True if the object exists in S3 (HEAD). 404 → False."""
    try:
        client._client.head_object(Bucket=client._bucket, Key=key)  # pyright: ignore[reportPrivateUsage] — W11: S3Client-обёртка не имеет head-метода; _client — boto3-контракт модуля
    except ClientError as exc:
        response = cast("dict[str, object]", cast(object, exc.response))  # W11: botocore TypedDict-ответ → object-мост
        error_raw = response.get("Error")
        code = cast("str", cast("dict[str, object]", error_raw).get("Code", "")) if isinstance(error_raw, dict) else ""
        if code in {"404", "NoSuchKey"}:
            return False
        raise
    else:
        return True


# endregion FUNC__head_exists


# region FUNC_sync
## @purpose  Upload локальных WAL-сегментов: HEAD → 404 → PUT (без локального удаления).
##           Идемпотентно (D2): HEAD ok → skip. Rate-limit WAL_MAX_UPLOAD_PER_RUN.
## @io       ⇥ local_files: list[WalEntry], client: S3Client, prefix: str, node: str,
##              limit: int, dry_run: bool → ⎋ int (uploaded)
## @complexity O(min(N, limit)) — HEAD до PUT на файл
## @invariants
##   - Порядок: HEAD ДО PUT (избегаем повторных PUT-ов, rate-limit экономит)
##   - Обрабатываются ТОЛЬКО первые `limit` файлов (rate-limit)
##   - PUT не удаляет локальный файл (удаление — только safe-delete в retention)
##   - S3-ошибка → ClientError raise (exit 1)
## @changes  2026-08-15 | DevPlan 170 W11 — list[WalEntry] (TypedDict)
def sync(
    local_files: list[WalEntry],
    client: S3Client,
    prefix: str,
    node: str,
    limit: int,
    dry_run: bool = False,
) -> int:
    """Upload WAL segments not yet present in S3 (HEAD-first, idempotent)."""
    uploaded = 0
    for entry in local_files[:limit]:
        name = entry["name"]
        key = _wal_key(prefix, node, name)
        exists = _head_exists(client, key)
        if exists:
            logger.info("[IMP:7][wal_sync][sync] %s already in S3 — skip (idempotent, D2)", name)
            continue
        if dry_run:
            logger.info("[IMP:8][wal_sync][dry-run] WOULD upload %s → %s", name, key)
            uploaded += 1
            continue
        try:
            with pathlib.Path(entry["path"]).open("rb") as fh:
                client._client.put_object(Bucket=client._bucket, Key=key, Body=fh)  # pyright: ignore[reportPrivateUsage] — W11: см. _head_exists
        except OSError as exc:
            logger.error("[IMP:10][wal_sync][sync] Cannot read local WAL %s: %s", entry["path"], exc)
            msg = f"cannot read local WAL {name}"
            raise WalSyncError(msg) from exc
        uploaded += 1
        logger.info("[IMP:9][wal_sync][sync] UPLOAD %s → %s", name, key)
    return uploaded


# endregion FUNC_sync


# region FUNC_apply_local_retention
## @purpose  Safe-delete локальных WAL (D3): файл старше retention_days И подтверждён HEAD в S3 →
##           os.remove. Неподтверждённый файл НИКОГДА не удаляется (RPO-гарантия).
## @io       ⇥ local_files: list[WalEntry], client: S3Client, prefix: str, node: str,
##              retention_days: int, dry_run: bool → ⎋ int (удалено)
## @complexity O(N) — HEAD на каждый старый файл
## @invariants
##   - Удаление ТОЛЬКО если HEAD ok (файл безопасно в S3)
##   - IMP:9-лог каждого удаления
##   - Молодые файлы (< retention) — skip (IMP:7)
## @changes  2026-08-15 | DevPlan 170 W11 — list[WalEntry] (TypedDict)
def apply_local_retention(
    local_files: list[WalEntry],
    client: S3Client,
    prefix: str,
    node: str,
    retention_days: int,
    dry_run: bool = False,
) -> int:
    """Delete local WAL segments older than retention_days IF confirmed in S3 (safe-delete D3)."""
    now = time.time()
    cutoff = now - retention_days * 86400.0
    retained = 0
    for entry in local_files:
        if entry["mtime"] > cutoff:
            logger.info("[IMP:7][wal_sync][retention] %s younger than %dd — keep local", entry["name"], retention_days)
            continue
        key = _wal_key(prefix, node, entry["name"])
        if dry_run:
            logger.info("[IMP:8][wal_sync][dry-run] WOULD remove local %s (old + HEAD check)", entry["name"])
            retained += 1
            continue
        try:
            in_s3 = _head_exists(client, key)
        except ClientError as exc:
            logger.warning("[IMP:8][wal_sync][retention] HEAD failed for %s (non-fatal): %s", key, exc)
            continue
        if not in_s3:
            logger.warning(
                "[IMP:8][wal_sync][retention] %s old but NOT in S3 — KEEP (safe-delete D3, RPO guard)",
                entry["name"],
            )
            continue
        try:
            os.unlink(entry["path"])
        except OSError as exc:
            logger.warning("[IMP:8][wal_sync][retention] Cannot remove %s (non-fatal): %s", entry["path"], exc)
            continue
        retained += 1
        logger.info("[IMP:9][wal_sync][retention] LOCAL DELETE %s (safe-delete: confirmed in S3)", entry["name"])
    return retained


# endregion FUNC_apply_local_retention


# region FUNC_apply_s3_retention
## @purpose  S3-side purge wal/{node}/ (D4): объекты старше retention_days → delete_objects.
##           Префикс wal/{node}/ матчит и вложенные timeline-ключи wal/{node}/{timeline}/ (prefix-scan,
##           DevPlan 162 W1-4 — единый retention для всех timelines). retention.py НЕ трогается
##           (WAL-имена для него unparseable — D4).
## @io       ⇥ client: S3Client, prefix: str, node: str, retention_days: int,
##              dry_run: bool → ⎋ int (объектов к удалению)
## @complexity O(K) — K объектов под wal/{node}/ (list + delete batch)
## @invariants
##   - list_objects(prefix=wal/{node}/) через S3Client (пагинация); вложенные timeline-ключи матчатся
##   - LastModified старше cutoff → delete
##   - dry-run: только план (0 delete_objects)
def apply_s3_retention(
    client: S3Client,
    prefix: str,
    node: str,
    retention_days: int,
    dry_run: bool = False,
) -> int:
    """Purge S3 WAL objects older than retention_days (wal/{node}/ subprefix — D4, nested timeline keys)."""
    wal_prefix = f"{prefix.rstrip('/')}/wal/{node}/"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).timestamp()
    objects = client.list_objects(prefix=wal_prefix)

    stale: list[str] = []
    for obj in objects:
        last_modified = obj.get("LastModified")
        if last_modified is None:
            continue
        try:
            ts = last_modified.timestamp()
        except (AttributeError, OSError):
            continue
        if ts < cutoff:
            stale.append(cast("str", obj.get("Key")))  # W11: S3Object total=False — Key гарантирован у stale

    if dry_run:
        for key in stale:
            logger.info("[IMP:8][wal_sync][dry-run] WOULD delete S3 %s", key)
        logger.info("[IMP:7][wal_sync][retention] %d stale S3 object(s) planned (dry-run)", len(stale))
        return len(stale)

    if stale:
        # S3Client.delete_objects логирует IMP:9 (IRREVERSIBLE) per batch
        client.delete_objects(stale)
    logger.info("[IMP:9][wal_sync][retention] S3 retention: %d object(s) deleted (wal/%s/)", len(stale), node)
    return len(stale)


# endregion FUNC_apply_s3_retention


# region FUNC_main
## @purpose  Оркестрация: scan → sync → local retention → s3 retention → финальный IMP:9.
## @io       ⇥ argv: list[str] | None (--dry-run), env: dict[str, str] | None (DI — тесты
##              инжектят WAL_ARCHIVE_DIR и др. вместо monkeypatch-патчей _env_str/_env_int),
##              client_factory: Callable[[], S3Client] | None (DI — тесты инжектят FakeBoto-
##              клиент вместо monkeypatch build_s3_client) → ⎋ int (0 = ok, 1 = S3-ошибка в sync)
## @complexity O(N + K)
## @invariants
##   - S3-ошибка в sync → IMP:10 «S3 FAIL» + exit 1 (WAL = RPO-гарантия)
##   - Retention-ошибки → IMP:8 warning, non-fatal (exit 0)
##   - Финал: IMP:9 «WAL_SYNC OK: uploaded=N local_retained=M s3_retained=K»
##   - DI (DevPlan 167 D4): env=None → os.environ; client_factory=None → build_s3_client(env) —
##     публичная сигнатура обратно-совместима (дефолты = прежнее поведение)
## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · S3-ошибка в sync тестируется через реальный main()
## · Rejected: прямой вызов build_s3_client без параметра (тест патчил build_s3_client/
## ·   scan_local/_env_str/_env_int лямбдами — 4 monkeypatch.setattr на один сценарий)
## · Reason: seam = тестируемость реального вызова main() с инжектированными env-dict
## ·   (WAL_ARCHIVE_DIR → tmp_path) и client_factory (FakeBoto) — 0 патчей, тот же assert
## · Rev: при появлении второго потребителя конфигурации — вынести env-dict в AppConfig-объект
def main(
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    client_factory: Callable[[], S3Client] | None = None,
) -> int:
    """Run one WAL sync pass. Returns process exit code (0 ok / 1 S3 failure in sync)."""
    parser = argparse.ArgumentParser(description="WAL → S3 sync + retention (DevPlan 132 W2)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without any mutation")
    args = parser.parse_args(argv, namespace=CliArgs())  # W11: dataclass-namespace
    dry_run = args.dry_run

    archive_dir = _env_str("WAL_ARCHIVE_DIR", DEFAULT_WAL_ARCHIVE_DIR, env)
    prefix = _env_str("S3_PREFIX", DEFAULT_S3_PREFIX, env)
    node = _env_str("NODE_NAME", "unknown", env)
    local_retention = _env_int("WAL_LOCAL_RETENTION_DAYS", DEFAULT_LOCAL_RETENTION_DAYS, env)
    s3_retention = _env_int("WAL_S3_RETENTION_DAYS", DEFAULT_S3_RETENTION_DAYS, env)
    max_upload = _env_int("WAL_MAX_UPLOAD_PER_RUN", DEFAULT_MAX_UPLOAD_PER_RUN, env)

    local_files = scan_local(archive_dir)
    logger.info(
        "[IMP:7][wal_sync][main] node=%s prefix=%s archive=%s files=%d limit=%d dry_run=%s",
        node,
        prefix,
        archive_dir,
        len(local_files),
        max_upload,
        dry_run,
    )

    client = client_factory() if client_factory is not None else build_s3_client(env)

    # ── Sync (S3-fail → exit 1: WAL = RPO-гарантия, тихий отказ запрещён) ──
    try:
        uploaded = sync(local_files, client, prefix, node, max_upload, dry_run=dry_run)
    except (ClientError, WalSyncError) as exc:
        logger.error("[IMP:10][wal_sync] S3 FAIL during sync: %s", exc)
        return 1

    # ── Retention (non-fatal: IMP:8 warning, exit 0) ──
    local_retained = 0
    s3_retained = 0
    try:
        local_retained = apply_local_retention(local_files, client, prefix, node, local_retention, dry_run=dry_run)
    # ruff: ignore[BLE001] — retention non-fatal по контракту D3/D4 (boto3 ops)
    except Exception as exc:  # noqa: EXC — retention non-fatal по контракту D3/D4
        logger.warning("[IMP:8][wal_sync][retention] Local retention failed (non-fatal): %s", exc)
    try:
        s3_retained = apply_s3_retention(client, prefix, node, s3_retention, dry_run=dry_run)
    # ruff: ignore[BLE001] — retention non-fatal по контракту D3/D4 (boto3 ops)
    except Exception as exc:  # noqa: EXC — retention non-fatal по контракту D3/D4
        logger.warning("[IMP:8][wal_sync][retention] S3 retention failed (non-fatal): %s", exc)

    logger.info(
        "[IMP:9][wal_sync] WAL_SYNC OK: uploaded=%d local_retained=%d s3_retained=%d",
        uploaded,
        local_retained,
        s3_retained,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
# endregion FUNC_main
