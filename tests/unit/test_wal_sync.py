#!/usr/bin/env python3
# GREP_SUMMARY: test-wal-sync wal-archive s3-sync head-object safe-delete retention dry-run rate-limit container-contract RPO
# STRUCTURE: ┌FakeBoto (head/put/list/delete)┐ → ○ scan filter (excl .history/.backup) → ○ HEAD 404→PUT / ok→skip → ○ safe-delete (old+in-S3→rm; old+NOT-in-S3→keep R5) → ○ rate-limit → ○ dry-run 0 mutations → ○ S3-fail→exit 1
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/backup-cron/scripts/wal_sync.py (DevPlan 132 W2).
## @scope    Pure Python — boto3 заменяется FakeBoto (0 реальных S3-вызовов). scripts/ добавляется
##           в sys.path по канону module-specific paths (tests/AGENTS.md).
## @invariants
##   - Контейнерный контракт: wal_sync.py не импортирует core.internal (статическая проверка)
##   - tmp_path для WAL-архива (Zero Hardcode Rule)
##   - R5 negative: safe-delete «старый+НЕ-в-S3 → НЕ rm» с оригинальной формой (слепое удаление по возрасту)
##   - R1: реальные assertions; LDD: IMP:9 на удалениях/upload
## @rationale  DevPlan 132 W2 §TEST_SPEC: scan-фильтр, HEAD 404→PUT, HEAD ok→skip (идемпотентность),
##             safe-delete (R5 negative), rate-limit, dry-run 0 мутаций, S3-fail → exit 1.
## @changes  2026-08-04 | DevPlan 132 W2 — created
# endregion MODULE_CONTRACT

import contextlib
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from botocore.exceptions import ClientError

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "core" / "modules" / "backup-cron" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import wal_sync
from s3_client import S3Client

logger = logging.getLogger(__name__)


# region FAKE_BOTO


class FakeBoto:
    """In-memory boto3 S3 client: head/put/list/delete with real ClientError semantics."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}  # key -> {"content": bytes, "lm": datetime}
        self.head_calls: list[str] = []
        self.put_calls: list[str] = []
        self.deleted: list[str] = []
        self.fail_head_with: Exception | None = None

    def head_object(self, Bucket: str | None = None, Key: str | None = None) -> dict:
        self.head_calls.append(Key)
        if self.fail_head_with is not None:
            raise self.fail_head_with
        if Key in self.objects:
            return {"ETag": "etag"}
        raise ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}, "ResponseMetadata": {}},
            "HeadObject",
        )

    def put_object(self, Bucket: str | None = None, Key: str | None = None, Body=None) -> dict:
        self.put_calls.append(Key)
        self.objects[Key] = {"content": b"x", "lm": datetime.now(timezone.utc)}
        return {"ETag": "etag"}

    def list_objects_v2(self, Bucket=None, Prefix=None, MaxKeys=None, ContinuationToken=None) -> dict:
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {
            "Contents": [{"Key": k, "LastModified": self.objects[k]["lm"]} for k in keys],
            "IsTruncated": False,
        }

    def delete_objects(self, Bucket=None, Delete=None) -> dict:
        keys = [o["Key"] for o in Delete.get("Objects", [])]
        self.deleted.extend(keys)
        for k in keys:
            self.objects.pop(k, None)
        return {"Deleted": [{"Key": k} for k in keys]}


def _make_client(fake: FakeBoto, bucket: str = "test-bucket") -> S3Client:
    """Wrap FakeBoto in the module S3Client wrapper."""
    return S3Client(fake, bucket)


# endregion FAKE_BOTO


# region CONTAINER_CONTRACT


# 🧪 TRAP[TEST] · Regression · Scenario: wal_sync.py не импортирует core.internal (контейнерный контракт)
# · Last fail: N/A (new test — DevPlan 132 W2; паттерн test_backup_cron_dockerfile.py AC-C1.2)
# · Remove if: backup-cron образ начнёт включать core/internal (не планируется)
def test_wal_sync_no_core_internal_import(caplog) -> None:
    """Контейнерный контракт: 0 импортов core.internal в wal_sync.py."""
    caplog.set_level(logging.INFO)
    source = (_SCRIPTS_DIR / "wal_sync.py").read_text(encoding="utf-8")
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("from core.internal", "import core.internal"))
    ]
    assert not import_lines, f"wal_sync.py импортирует core.internal: {import_lines}"
    assert "wal_sync" in sys.modules, "wal_sync должен импортироваться нативно (контейнерный runtime)"
    logger.info("[IMP:9][test_wal_sync] 0 core.internal imports (container contract PASS)")


# 🧪 TRAP[TEST] · Regression · Scenario: wal_sync нативно импортируется из scripts/ (как в контейнере)
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: wal_sync переезжает в другой механизм доставки
def test_wal_sync_import_clean(caplog) -> None:
    """wal_sync + s3_client импортируются чисто (цепочка как в образе)."""
    caplog.set_level(logging.INFO)
    assert "wal_sync" in sys.modules and "s3_client" in sys.modules
    assert callable(wal_sync.scan_local) and callable(wal_sync.sync)
    logger.info("[IMP:9][test_wal_sync] native import chain OK")


# endregion CONTAINER_CONTRACT


# region SCAN


# 🧪 TRAP[TEST] · Regression · Scenario: scan-фильтр ^[0-9A-F]{24}$ включая .history/.backup исключения
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: WAL segment pattern changes
def test_scan_local_filters_non_wal_files(tmp_path: Path, caplog) -> None:
    """Только 24-hex сегменты; .history/.backup/прочие исключены."""
    caplog.set_level(logging.INFO)
    wal = "0" * 24  # валидный 24-hex сегмент
    (tmp_path / wal).write_bytes(b"wal-data")
    (tmp_path / "00000002.history").write_bytes(b"history")
    (tmp_path / "00000002.backup").write_bytes(b"backup")
    (tmp_path / "00000002.detail").write_bytes(b"detail")
    (tmp_path / "README").write_text("readme")

    entries = wal_sync.scan_local(str(tmp_path))

    names = [e["name"] for e in entries]
    assert names == [wal], f"scan должен вернуть только 24-hex сегменты, got {names}"
    logger.info("[IMP:9][test_wal_sync] scan filter (incl .history/.backup exclusion) PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: отсутствующий каталог → [] (не fatal)
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: scan_local error semantics change
def test_scan_local_missing_dir_returns_empty(tmp_path: Path, caplog) -> None:
    """Отсутствующий WAL-каталог → пустой список + warning."""
    caplog.set_level(logging.INFO)
    assert wal_sync.scan_local(str(tmp_path / "missing")) == []
    logger.info("[IMP:9][test_wal_sync] missing dir → [] PASS")


# endregion SCAN


# region SYNC


# 🧪 TRAP[TEST] · Regression · Scenario: HEAD 404 → PUT
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: sync HEAD→PUT logic changes
def test_sync_uploads_when_head_404(tmp_path: Path, caplog) -> None:
    """Файл отсутствует в S3 (HEAD 404) → PUT."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    client = _make_client(fake)
    wal = "A" * 24
    (tmp_path / wal).write_bytes(b"data")
    entries = wal_sync.scan_local(str(tmp_path))

    uploaded = wal_sync.sync(entries, client, "platform/backups", "node1", limit=10)

    assert uploaded == 1
    assert fake.put_calls == ["platform/backups/wal/node1/" + wal], "HEAD 404 → PUT expected"
    logger.info("[IMP:9][test_wal_sync] HEAD 404 → PUT PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: HEAD ok → skip (идемпотентность D2)
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: D2 idempotency logic changes
def test_sync_skips_when_head_ok(tmp_path: Path, caplog) -> None:
    """Файл уже в S3 (HEAD ok) → skip — повторный прогон = no-op (D2)."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    client = _make_client(fake)
    wal = "B" * 24
    fake.objects["platform/backups/wal/node1/" + wal] = {"content": b"x", "lm": datetime.now(timezone.utc)}
    (tmp_path / wal).write_bytes(b"data")
    entries = wal_sync.scan_local(str(tmp_path))

    uploaded = wal_sync.sync(entries, client, "platform/backups", "node1", limit=10)

    assert uploaded == 0
    assert fake.put_calls == [], "HEAD ok → skip (идемпотентность D2)"
    logger.info("[IMP:9][test_wal_sync] HEAD ok → skip (idempotent) PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: rate-limit WAL_MAX_UPLOAD_PER_RUN
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: rate-limit logic changes
def test_sync_respects_rate_limit(tmp_path: Path, caplog) -> None:
    """WAL_MAX_UPLOAD_PER_RUN=2 → только 2 файла upload за прогон."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    client = _make_client(fake)
    for i in range(4):
        wal = f"{i:024X}"  # 24 hex chars
        (tmp_path / wal).write_bytes(b"data")
    entries = wal_sync.scan_local(str(tmp_path))
    assert len(entries) == 4

    uploaded = wal_sync.sync(entries, client, "platform/backups", "node1", limit=2)

    assert uploaded == 2, "rate-limit: только 2 upload"
    assert len(fake.put_calls) == 2
    logger.info("[IMP:9][test_wal_sync] rate-limit PASS")


# endregion SYNC


# region LOCAL_RETENTION


def _old_file(tmp_path: Path, name: str, days_old: float = 10.0) -> Path:
    """Create a WAL file older than `days_old` days (mtime in the past).

    Uses contextlib.suppress (не bare pass — R1/CONSTITUTION-4) и assert на mtime
    (реальное утверждение: retention-тест зависит от возраста файла).
    """
    p = tmp_path / name
    p.write_bytes(b"data")
    old_ts = time.time() - days_old * 86400.0
    with contextlib.suppress(OSError):
        os.utime(p, (old_ts, old_ts))
    assert p.stat().st_mtime < time.time() - (days_old - 1) * 86400.0, "mtime-based retention test needs mtime control"
    return p


# 🧪 TRAP[TEST] · Regression · Scenario: safe-delete старый+в-S3 → rm
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: safe-delete logic changes
def test_local_retention_removes_old_confirmed(tmp_path: Path, caplog) -> None:
    """Старый файл (10д) + HEAD ok → удаляется локально (safe-delete D3)."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    client = _make_client(fake)
    wal = "C" * 24
    p = _old_file(tmp_path, wal)
    fake.objects["platform/backups/wal/node1/" + wal] = {"content": b"x", "lm": datetime.now(timezone.utc)}
    entries = wal_sync.scan_local(str(tmp_path))

    retained = wal_sync.apply_local_retention(entries, client, "platform/backups", "node1", 7)

    assert retained == 1
    assert not p.exists(), "старый + подтверждённый в S3 → локальный файл удалён"
    assert any("[IMP:9][wal_sync][retention] LOCAL DELETE" in r.message for r in caplog.records), "IMP:9 per delete"
    logger.info("[IMP:9][test_wal_sync] safe-delete old+in-S3 PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · safe-delete «старый + НЕ-в-S3 → НЕ rm»
# · Scenario: оригинальная форма риска D3 — слепое удаление по возрасту удалило бы PITR-цель при
# ·   недоступности S3. Точный вход: файл старше 7д, HEAD вернул 404 (объект НЕ в S3).
# · Last fail: до 132 W2 — wal-archive рос вечно без retention (rsync-строка закомментирована,
# ·   backup_postgres.py:55); слепой age-based delete = потеря RPO=24ч молча
# · Remove if: wal_sync перестанет гарантировать safe-delete
def test_local_retention_keeps_old_not_in_s3_negative(tmp_path: Path, caplog) -> None:
    """R5 negative: старый файл НЕ в S3 (HEAD 404) → НЕ удаляется (RPO-гарантия D3)."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    client = _make_client(fake)
    wal = "D" * 24
    p = _old_file(tmp_path, wal)  # НЕ добавляем в fake.objects → HEAD 404
    entries = wal_sync.scan_local(str(tmp_path))

    retained = wal_sync.apply_local_retention(entries, client, "platform/backups", "node1", 7)

    assert retained == 0
    assert p.exists(), "R5 FAIL: файл старше 7д НЕ в S3 — удалять запрещено (потеря PITR-цели)"
    assert any("NOT in S3 — KEEP" in r.message for r in caplog.records), "KEEP warning expected"
    logger.info("[IMP:9][test_wal_sync][R5] старый+НЕ-в-S3 → KEEP PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: молодой файл (< retention) — keep
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: retention age check changes
def test_local_retention_keeps_young(tmp_path: Path, caplog) -> None:
    """Молодой файл (< 7д) → локально сохраняется даже если в S3."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    client = _make_client(fake)
    wal = "E" * 24
    p = tmp_path / wal
    p.write_bytes(b"data")  # mtime = now (молодой)
    fake.objects["platform/backups/wal/node1/" + wal] = {"content": b"x", "lm": datetime.now(timezone.utc)}
    entries = wal_sync.scan_local(str(tmp_path))

    retained = wal_sync.apply_local_retention(entries, client, "platform/backups", "node1", 7)

    assert retained == 0
    assert p.exists(), "молодой файл не удаляется"
    logger.info("[IMP:9][test_wal_sync] young keep PASS")


# endregion LOCAL_RETENTION


# region S3_RETENTION


# 🧪 TRAP[TEST] · Regression · Scenario: S3-side purge wal/<node>/ старше 14д (D4)
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: apply_s3_retention logic changes
def test_s3_retention_purges_old_objects(caplog) -> None:
    """Объекты wal/node1/ старше 14д удаляются; свежие остаются; чужие node — не тронуты."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    client = _make_client(fake)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=20)
    fresh = now - timedelta(days=2)
    prefix = "platform/backups/wal/node1/"
    fake.objects[prefix + "F" * 24] = {"content": b"x", "lm": old}
    fake.objects[prefix + "G" * 24] = {"content": b"x", "lm": fresh}
    fake.objects["platform/backups/wal/node2/" + "H" * 24] = {"content": b"x", "lm": old}

    purged = wal_sync.apply_s3_retention(client, "platform/backups", "node1", 14)

    assert purged == 1, "только старый объект node1"
    assert fake.deleted == [prefix + "F" * 24]
    assert prefix + "G" * 24 in fake.objects, "свежий объект остаётся"
    assert "platform/backups/wal/node2/" + "H" * 24 in fake.objects, "чужой node не тронут"
    logger.info("[IMP:9][test_wal_sync] S3 retention purge PASS")


# endregion S3_RETENTION


# region DRY_RUN


# 🧪 TRAP[TEST] · Regression · Scenario: dry-run 0 мутаций
# · Last fail: N/A (new test — DevPlan 132 W2)
# · Remove if: dry-run semantics change
def test_dry_run_zero_mutations(tmp_path: Path, caplog) -> None:
    """dry-run: нет PUT, нет локальных удалений, нет delete_objects."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    client = _make_client(fake)
    wal_new = "A" * 24
    (tmp_path / wal_new).write_bytes(b"data")  # молодой — upload кандидат
    wal_old = "B" * 24
    p_old = _old_file(tmp_path, wal_old)  # старый — retention кандидат
    fake.objects["platform/backups/wal/node1/" + wal_old] = {"content": b"x", "lm": datetime.now(timezone.utc)}
    entries = wal_sync.scan_local(str(tmp_path))

    uploaded = wal_sync.sync(entries, client, "platform/backups", "node1", limit=10, dry_run=True)
    retained = wal_sync.apply_local_retention(entries, client, "platform/backups", "node1", 7, dry_run=True)
    s3_retained = wal_sync.apply_s3_retention(client, "platform/backups", "node1", 14, dry_run=True)

    assert uploaded == 1 and fake.put_calls == [], "dry-run: план upload без PUT"
    assert retained == 1 and p_old.exists(), "dry-run: план удаления без os.remove"
    assert s3_retained == 0, "dry-run: план S3 purge без delete"
    assert fake.deleted == []
    logger.info("[IMP:9][test_wal_sync] dry-run 0 mutations PASS")


# endregion DRY_RUN


# region S3_FAILURE


# 🧪 TRAP[TEST] · NEGATIVE (R5) · S3-ошибка в sync → IMP:10 + exit 1
# · Scenario: оригинальная форма — S3 недоступен во время upload; тихий отказ запрещён
# ·   (WAL = RPO-гарантия). Точный вход: head_object бросает ClientError 500 (не 404).
# · Last fail: до 132 W2 — WAL-архив писался вечно без выгрузки (rsync закомментирован,
# ·   backup_postgres.py:55), отсутствие S3 не детектировалось никак
# · Remove if: S3-fail перестанет быть громким (exit 1)
def test_s3_failure_in_sync_exits_1(monkeypatch, tmp_path: Path, caplog) -> None:
    """S3-ошибка в sync → main возвращает 1 + IMP:10 «S3 FAIL»."""
    caplog.set_level(logging.INFO)
    wal = "C" * 24
    (tmp_path / wal).write_bytes(b"data")

    fake = FakeBoto()
    fake.fail_head_with = ClientError(
        {"Error": {"Code": "500", "Message": "InternalError"}, "ResponseMetadata": {}},
        "HeadObject",
    )
    failing_client = _make_client(fake)
    original_scan_local = wal_sync.scan_local
    monkeypatch.setattr(wal_sync, "build_s3_client", lambda: failing_client)
    monkeypatch.setattr(wal_sync, "scan_local", lambda archive_dir: original_scan_local(str(tmp_path)))
    monkeypatch.setattr(wal_sync, "_env_str", lambda name, default: default)
    monkeypatch.setattr(wal_sync, "_env_int", lambda name, default: default)

    exit_code = wal_sync.main([])

    assert exit_code == 1, "S3 failure in sync → exit 1 (WAL = RPO-гарантия)"
    assert any("[IMP:10]" in r.message and "S3 FAIL" in r.message for r in caplog.records), "IMP:10 S3 FAIL log"
    logger.info("[IMP:9][test_wal_sync][R5] S3-fail → exit 1 PASS")


# endregion S3_FAILURE
