#!/usr/bin/env python3
# GREP_SUMMARY: wal-sync, wal-archive, s3-sync, head-object, safe-delete, retention, dry-run, rate-limit, pg-wal, RPO
# STRUCTURE: ▶ scan_local ┌listdir filter ^[0-9A-F]{24}$┐ → ○ sync ∋ HEAD→404→PUT (rate-limit) → ○ apply_local_retention (старше 7д И HEAD ok → rm) → ○ apply_s3_retention (wal/{node}/ старше 14д → delete) → ⎋ exit 0|1
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
##   4. D4: S3-side purge wal/{node}/ — старше WAL_S3_RETENTION_DAYS (14); retention.py НЕ трогается
##      (WAL-имена не парсятся retention.py — unparseable, никогда не удаляются)
##   5. Rate-limit: WAL_MAX_UPLOAD_PER_RUN (200) — максимум PUT-ов за прогон
##   6. S3-ошибка в sync → [IMP:10][wal_sync] S3 FAIL + exit 1 (WAL = RPO-гарантия, тихий отказ запрещён)
##   7. Retention-ошибки non-fatal: IMP:8 warning, прогон завершается exit 0
##   8. --dry-run: печатает план, 0 мутаций (нет PUT/delete/remove)
##   9. Финал: IMP:9 «WAL_SYNC OK: uploaded=N local_retained=M s3_retained=K»
## @rationale D2: state-файл uploaded-ключей теряется при пересоздании контейнера; HEAD дешевле
##           (≤1 req/файл) и идемпотентен. D3: слепое удаление по возрасту = потеря PITR-цели при
##           недоступности S3. D4: retention.py группирует по дате из имени (WAL-имена не парсятся).
## @changes  2026-08-04 | DevPlan 132 W2 — создан
## @modulemap
##   scan_local [W:1] — listdir + фильтр ^[0-9A-F]{24}$ (pg_wal сегменты; .history/.backup исключены)
##   sync [W:1] — HEAD→404→PUT (без локального удаления), rate-limit, идемпотентность D2
##   apply_local_retention [W:1] — safe-delete D3 (старше 7д И HEAD ok → os.remove, IMP:9)
##   apply_s3_retention [W:1] — list_objects_v2(prefix wal/{node}/) → delete старше 14д (D4)
##   main [W:1] — оркестрация + CLI (--dry-run) → exit 0|1
## @usecases
##   - cron: 10 * * * * root python3 /usr/local/bin/wal_sync.py
##   - dry-run: python3 wal_sync.py --dry-run
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from s3_client import S3Client

logger = logging.getLogger(__name__)

# ── Env-контракт (канон S3_* из upload-s3.sh; WAL_* — DevPlan 132 W2) ──
WAL_SEGMENT_RE = re.compile(r"^[0-9A-F]{24}$")

DEFAULT_S3_ENDPOINT_URL = "https://s3.timeweb.cloud"
DEFAULT_S3_REGION = "ru-1"
DEFAULT_S3_PREFIX = "platform/backups"
DEFAULT_WAL_ARCHIVE_DIR = "/var/lib/platform/wal-archive"
DEFAULT_LOCAL_RETENTION_DAYS = 7
DEFAULT_S3_RETENTION_DAYS = 14
DEFAULT_MAX_UPLOAD_PER_RUN = 200


class WalSyncError(Exception):
    """S3-ошибка в sync-фазе — сигнал exit 1 (WAL = RPO-гарантия)."""


# region FUNC__env_str
## @purpose  env-строка с default fallback.
## @io       ⇥ name: str, default: str → ⎋ str
## @complexity O(1)
def _env_str(name: str, default: str) -> str:
    """Read a string env var with default."""
    return os.environ.get(name, default)


# endregion FUNC__env_str


# region FUNC__env_int
## @purpose  env-int с валидным fallback (invalid → default, IMP:7 warning).
## @io       ⇥ name: str, default: int → ⎋ int
## @complexity O(1)
def _env_int(name: str, default: int) -> int:
    """Read a non-negative int env var with default fallback."""
    raw = os.environ.get(name, "")
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
## @purpose  Сканирование WAL-архива: listdir + фильтр ^[0-9A-F]{24}$ (сегменты pg_wal).
##           .history/.backup файлы (timeline history, backup labels) исключаются регуляркой.
## @io       ⇥ archive_dir: str → ⎋ list[dict] — [{name, mtime, path}], отсортировано по имени
## @complexity O(N) — N файлов в каталоге
## @invariants
##   - Фильтр строгий: только 24 hex-символа (pg_wal сегмент), UPPERCASE
##   - Отсутствующий каталог → IMP:7 warning + [] (не fatal)
##   - Ошибка stat отдельного файла → файл пропускается
def scan_local(archive_dir: str) -> list[dict]:
    """List WAL segment files in the archive dir (^[0-9A-F]{24}$)."""
    try:
        names = os.listdir(archive_dir)
    except FileNotFoundError:
        logger.warning("[IMP:7][wal_sync][scan] Archive dir %s not found — 0 files", archive_dir)
        return []
    except OSError as exc:
        logger.warning("[IMP:7][wal_sync][scan] Cannot list %s: %s", archive_dir, exc)
        return []

    entries: list[dict] = []
    for name in names:
        if not WAL_SEGMENT_RE.fullmatch(name):
            logger.info("[IMP:5][wal_sync][scan] Excluded (not a WAL segment): %s", name)
            continue
        path = os.path.join(archive_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        entries.append({"name": name, "mtime": mtime, "path": path})

    entries.sort(key=lambda e: e["name"])
    logger.info("[IMP:7][wal_sync][scan] %d WAL segment(s) in %s", len(entries), archive_dir)
    return entries


# endregion FUNC_scan_local


# region FUNC_build_s3_client
## @purpose  Построение S3-клиента (boto3 + S3Client обёртка модуля) из канонических S3_* env.
## @io       ⎋ S3Client (bucket привязан)
## @complexity O(1)
## @invariants
##   - Endpoint/region/prefix — канон upload-s3.sh (S3_ENDPOINT_URL/S3_REGION/S3_PREFIX)
##   - Креды из env (S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET)
##   - boto3 Config: connect/read timeout + 3 retries
def build_s3_client() -> S3Client:
    """Build the module S3 client from canonical S3_* env vars."""
    endpoint = _env_str("S3_ENDPOINT_URL", DEFAULT_S3_ENDPOINT_URL)
    region = _env_str("S3_REGION", DEFAULT_S3_REGION)
    access_key = _env_str("S3_ACCESS_KEY", "")
    secret_key = _env_str("S3_SECRET_KEY", "")
    bucket = _env_str("S3_BUCKET", "")

    session = boto3.session.Session(
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        region_name=region,
    )
    raw_client = session.client(
        "s3",
        endpoint_url=endpoint,
        config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3}),
    )
    logger.info("[IMP:7][wal_sync][s3] S3 client built: bucket=%s endpoint=%s region=%s", bucket, endpoint, region)
    return S3Client(raw_client, bucket)


# endregion FUNC_build_s3_client


# region FUNC__wal_key
## @purpose  S3-ключ WAL-объекта: {prefix}/wal/{node}/{segment}.
## @io       ⇥ prefix: str, node: str, name: str → ⎋ str
## @complexity O(1)
def _wal_key(prefix: str, node: str, name: str) -> str:
    """Compose the S3 key for a WAL segment (wal/{node}/ subprefix — D4)."""
    return f"{prefix.rstrip('/')}/wal/{node}/{name}"


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
        client._client.head_object(Bucket=client._bucket, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey"):
            return False
        raise


# endregion FUNC__head_exists


# region FUNC_sync
## @purpose  Upload локальных WAL-сегментов: HEAD → 404 → PUT (без локального удаления).
##           Идемпотентно (D2): HEAD ok → skip. Rate-limit WAL_MAX_UPLOAD_PER_RUN.
## @io       ⇥ local_files: list[dict], client: S3Client, prefix: str, node: str,
##              limit: int, dry_run: bool → ⎋ int (uploaded)
## @complexity O(min(N, limit)) — HEAD до PUT на файл
## @invariants
##   - Порядок: HEAD ДО PUT (избегаем повторных PUT-ов, rate-limit экономит)
##   - Обрабатываются ТОЛЬКО первые `limit` файлов (rate-limit)
##   - PUT не удаляет локальный файл (удаление — только safe-delete в retention)
##   - S3-ошибка → ClientError raise (exit 1)
def sync(
    local_files: list[dict],
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
            with open(entry["path"], "rb") as fh:
                client._client.put_object(Bucket=client._bucket, Key=key, Body=fh)
        except OSError as exc:
            logger.error("[IMP:10][wal_sync][sync] Cannot read local WAL %s: %s", entry["path"], exc)
            raise WalSyncError(f"cannot read local WAL {name}") from exc
        uploaded += 1
        logger.info("[IMP:9][wal_sync][sync] UPLOAD %s → %s", name, key)
    return uploaded


# endregion FUNC_sync


# region FUNC_apply_local_retention
## @purpose  Safe-delete локальных WAL (D3): файл старше retention_days И подтверждён HEAD в S3 →
##           os.remove. Неподтверждённый файл НИКОГДА не удаляется (RPO-гарантия).
## @io       ⇥ local_files: list[dict], client: S3Client, prefix: str, node: str,
##              retention_days: int, dry_run: bool → ⎋ int (удалено)
## @complexity O(N) — HEAD на каждый старый файл
## @invariants
##   - Удаление ТОЛЬКО если HEAD ok (файл безопасно в S3)
##   - IMP:9-лог каждого удаления
##   - Молодые файлы (< retention) — skip (IMP:7)
def apply_local_retention(
    local_files: list[dict],
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
            os.remove(entry["path"])
        except OSError as exc:
            logger.warning("[IMP:8][wal_sync][retention] Cannot remove %s (non-fatal): %s", entry["path"], exc)
            continue
        retained += 1
        logger.info("[IMP:9][wal_sync][retention] LOCAL DELETE %s (safe-delete: confirmed in S3)", entry["name"])
    return retained


# endregion FUNC_apply_local_retention


# region FUNC_apply_s3_retention
## @purpose  S3-side purge wal/{node}/ (D4): объекты старше retention_days → delete_objects.
##           retention.py НЕ трогается (WAL-имена для него unparseable — D4).
## @io       ⇥ client: S3Client, prefix: str, node: str, retention_days: int,
##              dry_run: bool → ⎋ int (объектов к удалению)
## @complexity O(K) — K объектов под wal/{node}/ (list + delete batch)
## @invariants
##   - list_objects(prefix=wal/{node}/) через S3Client (пагинация)
##   - LastModified старше cutoff → delete
##   - dry-run: только план (0 delete_objects)
def apply_s3_retention(
    client: S3Client,
    prefix: str,
    node: str,
    retention_days: int,
    dry_run: bool = False,
) -> int:
    """Purge S3 WAL objects older than retention_days (wal/{node}/ subprefix — D4)."""
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
            stale.append(obj["Key"])

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
## @io       ⇥ argv: list[str] | None (--dry-run) + env → ⎋ int (0 = ok, 1 = S3-ошибка в sync)
## @complexity O(N + K)
## @invariants
##   - S3-ошибка в sync → IMP:10 «S3 FAIL» + exit 1 (WAL = RPO-гарантия)
##   - Retention-ошибки → IMP:8 warning, non-fatal (exit 0)
##   - Финал: IMP:9 «WAL_SYNC OK: uploaded=N local_retained=M s3_retained=K»
def main(argv: list[str] | None = None) -> int:
    """Run one WAL sync pass. Returns process exit code (0 ok / 1 S3 failure in sync)."""
    parser = argparse.ArgumentParser(description="WAL → S3 sync + retention (DevPlan 132 W2)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without any mutation")
    args = parser.parse_args(argv)
    dry_run = args.dry_run

    archive_dir = _env_str("WAL_ARCHIVE_DIR", DEFAULT_WAL_ARCHIVE_DIR)
    prefix = _env_str("S3_PREFIX", DEFAULT_S3_PREFIX)
    node = _env_str("NODE_NAME", "unknown")
    local_retention = _env_int("WAL_LOCAL_RETENTION_DAYS", DEFAULT_LOCAL_RETENTION_DAYS)
    s3_retention = _env_int("WAL_S3_RETENTION_DAYS", DEFAULT_S3_RETENTION_DAYS)
    max_upload = _env_int("WAL_MAX_UPLOAD_PER_RUN", DEFAULT_MAX_UPLOAD_PER_RUN)

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

    client = build_s3_client()

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
    except Exception as exc:  # noqa: EXC — retention non-fatal по контракту D3/D4
        logger.warning("[IMP:8][wal_sync][retention] Local retention failed (non-fatal): %s", exc)
    try:
        s3_retained = apply_s3_retention(client, prefix, node, s3_retention, dry_run=dry_run)
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
