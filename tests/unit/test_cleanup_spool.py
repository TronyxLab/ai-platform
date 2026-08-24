# GREP_SUMMARY: test-cleanup-spool sentinel-gated uploaded last_verified pending-retry spool-retry REF-0009 BUG-0802
# STRUCTURE: ▶ plan_cleanup (sentinel delete / orphan sweep / legacy marker / keep-pending / fresh skip) → ▶ run_cleanup I/O → ▶ spool_retry find_pending+fail-closed → ⎋ LDD IMP:9
# region MODULE_CONTRACT
"""
Unit tests for backup-cron cleanup_spool.py + spool_retry.py (REF-0009, BUG-0802 ≡ DATA-502).

@purpose  Sentinel-gated cleanup contract: cleanup deletes ONLY confirmed-uploaded
          spool files; unuploaded files are kept for the daily rescan retry.
          .last_verified freshness stamp is never deleted.
@scope    Pure planner (plan_cleanup, stat_fn DI для синтетических путей) +
          I/O wrapper (run_cleanup на tmp_path) + retry scanner (find_pending /
          run_retry fail-closed encryption path).
@invariants
  - Data file WITHOUT .uploaded sentinel is NEVER classified for deletion (R5:
    original bug — cleanup destroyed unuploaded dumps at day 7)
  - Data file WITH sentinel → both file and marker deleted
  - Orphan sentinels (data gone — crash window) swept
  - .last_verified NEVER deleted regardless of age
  - Legacy .backup_ran_* markers deletable by age (not backups)
  - spool_retry skips sentinel-confirmed files; plain dumps without AGE_RECIPIENT
    are counted as failures without any upload call (fail-closed)
  - tmp_path fixtures only (Zero Hardcode); время через time.time() + stat_fn DI
  - LDD IMP:9 trajectories asserted via @ldd_trajectory
@changes  2026-08-25 | Created (REF-0009, meta-refactoring W2)
"""
# endregion MODULE_CONTRACT

import logging
import os
import sys
import time
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "backup-cron" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import cleanup_spool  # pyright: ignore[reportImplicitRelativeImport]
import pytest
import spool_retry  # pyright: ignore[reportImplicitRelativeImport]

pytestmark = pytest.mark.static_audit

_DAY = 86400


# region HELPERS
def _aged(path: Path, days: float = 8) -> Path:
    """Create file with mtime N days back from REAL now."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"dump")
    m = time.time() - days * _DAY
    os.utime(path, (m, m))
    return path


def _fresh(path: Path) -> Path:
    """Create current-mtime file."""
    return _aged(path, days=0)


def _old_stat(_path: str) -> float:
    """stat_fn DI: все пути «старше» retention (для синтетических путей без диска)."""
    return time.time() - 8 * _DAY


def _fresh_stat(_path: str) -> float:
    """stat_fn DI: все пути свежие."""
    return time.time()


# endregion HELPERS


# ═══════════════════════════════════════════════════════════════════
# plan_cleanup — чистый планировщик (stat_fn DI)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_cleanup_keeps_unsentinel_file
## @purpose  R5 NEGATIVE (BUG-0802): файл старше retention БЕЗ .uploaded sentinel —
##           НЕ удаляется, попадает в pending_keep (daily rescan retry его подхватит).
# 🧪 TRAP[TEST] · unsentinel kept · NEGATIVE (R5) · Regression: cleanup уничтожал незагруженное на 7-й день
# · Last fail: до REF-0009 `find -mtime +7` удалял дамп при S3-outage — off-site копии не было вовсе
# · Remove if: cleanup перестанет быть sentinel-gated (недопустимо без нового DR-контракта)
@ldd_trajectory
def test_cleanup_keeps_aged_file_without_sentinel(caplog) -> None:
    """Aged file without .uploaded sentinel → pending_keep, not deletion."""
    target = "/spool/postgres/pgdumpall_old.sql.gz.age"
    confirmed, orphans, markers, pending = cleanup_spool.plan_cleanup(
        [target],
        time.time(),
        7,
        stat_fn=_old_stat,
    )
    assert confirmed == [] and orphans == [] and markers == []
    assert pending == [target], "unuploaded dump must be KEPT for rescan retry (BUG-0802)"
    logger.info("[IMP:9][test] plan_cleanup: файл без sentinel сохранён (pending) ✓")


# endregion FUNC_test_cleanup_keeps_unsentinel_file


@ldd_trajectory
def test_cleanup_deletes_only_confirmed_pair(caplog) -> None:
    """Aged file WITH sibling .uploaded sentinel → both deleted."""
    data = "/spool/postgres/pgdumpall_ok.sql.gz.age"
    confirmed, orphans, markers, pending = cleanup_spool.plan_cleanup(
        [data, data + ".uploaded"],
        time.time(),
        7,
        stat_fn=_old_stat,
    )
    assert sorted(confirmed) == [data, data + ".uploaded"], "confirmed pair deleted together"
    assert orphans == [] and markers == [] and pending == []
    logger.critical("[IMP:9][test] plan_cleanup: подтверждённая пара удаляется целиком ✓")


@ldd_trajectory
def test_cleanup_sweeps_orphan_sentinel_and_legacy_markers(caplog) -> None:
    """Orphan sentinel (data gone) swept; legacy .backup_ran_* markers age-deleted."""
    orphan = "/spool/postgres/pgdumpall_gone.sql.gz.age.uploaded"
    marker = "/spool/app-data/.backup_ran_20260101T000000Z"
    confirmed, orphans, markers, pending = cleanup_spool.plan_cleanup(
        [orphan, marker],
        time.time(),
        7,
        stat_fn=_old_stat,
    )
    assert orphans == [orphan]
    assert markers == [marker]
    assert confirmed == [] and pending == []
    logger.critical("[IMP:9][test] plan_cleanup: orphan-sentinel + легаси-маркер под sweep ✓")


@ldd_trajectory
def test_cleanup_never_deletes_last_verified_stamp(caplog) -> None:
    """.last_verified любой древности остаётся (freshness-метрика collector'а)."""
    stamp = "/spool/postgres/.last_verified"
    confirmed, orphans, markers, pending = cleanup_spool.plan_cleanup(
        [stamp],
        time.time(),
        7,
        stat_fn=_old_stat,
    )
    assert stamp in pending, "stamp must survive cleanup (REF-0009 collector contract)"
    assert confirmed == [] and orphans == [] and markers == []
    logger.critical("[IMP:9][test] plan_cleanup: .last_verified переживает cleanup ✓")


@ldd_trajectory
def test_cleanup_skips_fresh_files(caplog) -> None:
    """Свежие (<retention) файлы вообще не классифицируются."""
    fresh = "/spool/postgres/pgdumpall_new.sql.gz.age"
    confirmed, _orphans, _markers, pending = cleanup_spool.plan_cleanup(
        [fresh],
        time.time(),
        7,
        stat_fn=_fresh_stat,
    )
    assert confirmed == [] and pending == [], "fresh files outside retention scope"
    logger.critical("[IMP:9][test] plan_cleanup: свежие файлы вне ретеншена ✓")


# ═══════════════════════════════════════════════════════════════════
# run_cleanup — I/O-обёртка (tmp_path)
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_run_cleanup_end_to_end(tmp_path, caplog) -> None:
    """E2E на реальном spool: подтверждённое удалено, неподтверждённое и stamp живы."""
    pg = tmp_path / "postgres"
    old_confirmed = _aged(pg / "pgdumpall_c.sql.gz.age")
    old_sentinel = pg / (old_confirmed.name + ".uploaded")
    old_sentinel.write_bytes(b"")
    old_unsentinel = _aged(pg / "pgdumpall_u.sql.gz.age")
    _fresh(pg / ".last_verified")

    rc = cleanup_spool.run_cleanup(str(tmp_path), 7)

    assert rc == 0
    assert not old_confirmed.exists(), "confirmed-uploaded файл удаляется"
    assert not old_sentinel.exists(), "sentinel уходит вместе с данными"
    assert old_unsentinel.exists(), "неподтверждённый дамп НЕ удаляется (BUG-0802)"
    assert (pg / ".last_verified").exists(), "freshness stamp выживает"
    logger.info("[IMP:9][test] run_cleanup: sentinel-gated удаление, pending сохранён ✓")


# ═══════════════════════════════════════════════════════════════════
# spool_retry — ежедневный rescan
# ═══════════════════════════════════════════════════════════════════


class _RetryRunner:
    """Fake runner: фиксирует вызовы upload-s3.sh, эмулирует age-создание dst."""

    DEVNULL = object()

    def __init__(self, upload_rcs: dict[str, int] | None = None):
        self.upload_rcs = upload_rcs or {}
        self.upload_calls: list[list] = []

    def run(self, cmd: list, **_kwargs: object):  # fake DI — контракт runner'а упрощён до run()
        if cmd[0] == "age":
            Path(str(cmd[4])).write_bytes(b"enc")  # args: age -r <rcpt> -o <dst> <src>

            class _AgeResult:
                returncode = 0

            return _AgeResult()
        if str(cmd[0]).endswith("upload-s3.sh"):
            self.upload_calls.append(cmd)
            target = str(cmd[1])
            rc = self.upload_rcs.get(target, 0)
            if rc == 0:
                # upload-s3.sh контракт: успех удаляет spool-файл
                Path(target).unlink(missing_ok=True)

            class _UploadResult:
                returncode = rc

            return _UploadResult()

        class _OtherResult:
            returncode = 0

        return _OtherResult()


@ldd_trajectory
def test_find_pending_skips_sentinel_and_dotfiles(tmp_path, monkeypatch, caplog) -> None:
    """Кандидаты = только файлы без sentinel; dotfiles/легаси-маркеры исключены."""
    spool = tmp_path
    confirmed = _aged(spool / "postgres" / "done.sql.gz.age", days=2)
    (spool / "postgres" / (confirmed.name + ".uploaded")).write_bytes(b"")
    pending_plain = _aged(spool / "postgres" / "pending.sql.gz", days=2)
    pending_enc = _aged(spool / "postgres" / "pending2.sql.gz.age", days=3)
    _fresh(spool / "postgres" / ".last_verified")
    _aged(spool / "app-data" / ".backup_ran_x", days=9)
    monkeypatch.setenv("BACKUP_SPOOL_DIR", str(spool))

    result = spool_retry.find_pending(str(spool))
    found = {p for p, _ in result}
    keys = sorted(k for _, k in result)

    assert keys == ["postgres/pending.sql.gz", "postgres/pending2.sql.gz.age"]
    assert str(pending_plain) in found and str(pending_enc) in found
    logger.info("[IMP:9][test] find_pending: только unsentinel-кандидаты ✓")


@ldd_trajectory
def test_run_retry_fail_closed_without_recipient(tmp_path, monkeypatch, caplog) -> None:
    """Нет AGE_RECIPIENT → plain-дампы считаются failure, upload НЕ вызывается, exit 1."""
    spool = tmp_path
    stuck = _aged(spool / "postgres" / "stuck.sql.gz", days=2)
    monkeypatch.delenv("AGE_RECIPIENT", raising=False)
    runner = _RetryRunner()

    rc = spool_retry.run_retry(str(spool), runner=runner)

    assert rc == 1, "неуспешный retry должен быть виден в exit code / Loki-алерте"
    assert runner.upload_calls == [], "plaintext никогда не загружается (SEC-0018 fail-closed)"
    assert stuck.exists(), "дамп остаётся в spool"
    warns = [r.message for r in caplog.records if "AGE_RECIPIENT" in r.message]
    assert warns, "IMP:9 alert обязателен"
    logger.info("[IMP:9][test] run_retry: fail-closed без recipient ✓")


@ldd_trajectory
def test_run_retry_encrypts_then_uploads(tmp_path, monkeypatch, caplog) -> None:
    """Plain-дамп шифруется (age) и уходит в S3 как .age; подтверждение удаляет артефакт."""
    spool = tmp_path
    plain = _aged(spool / "postgres" / "retry.sql.gz", days=1)
    enc_pending = _aged(spool / "postgres" / "already.sql.gz.age", days=1)
    monkeypatch.setenv("AGE_RECIPIENT", "age1qqtestrecipientkey")
    runner = _RetryRunner(upload_rcs={str(enc_pending): 0})

    rc = spool_retry.run_retry(str(spool), runner=runner)

    assert rc == 0
    uploaded_keys = [str(cmd[2]) for cmd in runner.upload_calls]
    assert "postgres/retry.sql.gz.age" in uploaded_keys, "plain шифруется перед загрузкой"
    assert "postgres/already.sql.gz.age" in uploaded_keys
    assert not plain.exists(), "plaintext удалён после успешного шифрования"
    assert not enc_pending.exists(), "upload-s3.sh контракт: успех удаляет spool-файл"
    logger.info("[IMP:9][test] run_retry: encrypt→upload→confirm ✓")
