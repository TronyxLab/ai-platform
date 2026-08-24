"""
# GREP_SUMMARY: test backup_postgres pg_dumpall gzip-t last_verified stamp age-encrypt sentinel REF-0009
# STRUCTURE: ▶ success pipeline 1× → ▶ fail-проверки (dump rc, gzip -t, structure) + отсутствие stamp → ▶ fail-closed без AGE_RECIPIENT → ▶ missing env 2× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/backup-cron/scripts/backup_postgres.py (DevPlan 117 H D64,
##           REF-0009). subprocess fully mocked — zero real pg_dumpall/gzip/age/upload calls.
## @scope    All integrity checks (pg_dumpall returncode, gzip -t, structure), freshness stamp
##           (.last_verified ТОЛЬКО после gzip -t OK — REF-0009), age-encrypt перед upload
##           (fail-closed без AGE_RECIPIENT), gzip-pipe failure, missing env validation,
##           full success pipeline.
## @invariants
##   - subprocess.Popen/run заменяются fake-subprocess-объектом через runner DI-параметр
##     (DevPlan 167 D2 CommandRunner-seam — 0 патчей)
##   - spool_dir/timestamp/env passed explicitly (Zero Hardcode Rule — no env reliance)
##   - Partial dump removed on failure (trap cleanup parity)
##   - Upload exit code НЕ проваливает бэкап (DevPlan 119 C1); отсутствие AGE_RECIPIENT —
##     fail-closed: plaintext НЕ уходит с ноды, дамп остаётся в spool (REF-0009 SEC-0018)
##   - @ldd_trajectory asserts IMP:9 log presence
## @rationale DevPlan 09 §D64: unit coverage for the Python backup port — production
##            backup is critical, so every failure branch must be covered.
##            REF-0009: stamp/encrypt ветки — критичный DR-контракт (BUG-0802/0803).
## @changes 2026-08-02 | Created (Brief H D64)
##          2026-08-02 | DevPlan 119 C1 — test_upload_exit_code_propagated → non-blocking
##          2026-08-14 | DevPlan 167 D2 — subprocess monkeypatch → runner DI-param (8 → 0 setattr)
##          2026-08-25 | REF-0009 — stamp после gzip -t OK; age-encrypt перед upload
# endregion MODULE_CONTRACT
"""

import io
import logging
import sys
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test (module-specific path) ──
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "backup-cron" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))
from dataclasses import dataclass

import backup_postgres
import pytest

pytestmark = pytest.mark.static_audit

_VALID_ENV = {
    "POSTGRES_HOST": "postgres",
    "POSTGRES_PASSWORD": "test-password",
    "POSTGRES_USER": "postgres",
    "BACKUP_SPOOL_DIR": "/var/lib/platform/backup-spool",
    # REF-0009: публичный ключ реципиента (env безопасен); без него upload fail-closed
    "AGE_RECIPIENT": "age1qqtestrecipientkey",
}


@dataclass
class _FakeRunResult:
    """Fake subprocess.CompletedProcess."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class _FakePopenProc:
    """Fake Popen process object with stdout pipe + wait() returncode."""

    def __init__(self, returncode: int = 0, stdout_text: str | None = None):
        self.stdout = io.StringIO(stdout_text or "")
        self._rc = returncode

    def wait(self) -> int:
        return self._rc


# Реалистичный text-формат дампа pg_dumpall (header + SQL-стейтменты) — для zcat-валидации
_FAKE_DUMP_TEXT = (
    "--\n"
    "-- PostgreSQL database cluster dump\n"
    "--\n"
    "CREATE TABLE public.t (id serial);\n"
    "COPY public.t (id) FROM stdin;\n"
    "\\.\n"
)


class _FakeSubprocess:
    """Drop-in fake subprocess module: command-name-indexed returncodes.

    Передаётся в run_backup(runner=...) как DI-объект (DevPlan 167 D2) —
    Popen/run/PIPE/DEVNULL интерфейс идентичен реальному subprocess-модулю.
    REF-0009: команда ``age`` эмулирует создание зашифрованного артефакта
    (args: age -r <recipient> -o <dst> <src>) — verify_encrypted видит файл.
    """

    DEVNULL = object()
    PIPE = object()

    def __init__(self, run_rcs: dict[str, int] | None = None, dump_rc: int = 0, gzip_rc: int = 0):
        self.run_rcs = run_rcs or {}
        self.dump_rc = dump_rc
        self.gzip_rc = gzip_rc
        self.run_calls: list[list] = []
        self.popen_calls: list[list] = []

    def Popen(self, cmd: list, **kwargs):
        self.popen_calls.append([cmd, kwargs])
        name = cmd[0]
        if name == "pg_dumpall":
            return _FakePopenProc(self.dump_rc)
        if name == "gzip":
            return _FakePopenProc(self.gzip_rc)
        if name == "zcat":
            return _FakePopenProc(0, stdout_text=_FAKE_DUMP_TEXT)
        msg = f"unexpected Popen command: {cmd}"
        raise AssertionError(msg)

    def run(self, cmd: list, **kwargs):
        self.run_calls.append([cmd, kwargs])
        name = cmd[0]
        if name == "du":
            return _FakeRunResult(stdout=f"1.2M\t{cmd[1]}\n")
        if name == "age":
            if self.run_rcs.get("age", 0) == 0:
                Path(cmd[4]).write_bytes(b"fake-age-armored-payload")  # -o dst = args[4]
            return _FakeRunResult(returncode=self.run_rcs.get("age", 0))
        return _FakeRunResult(returncode=self.run_rcs.get(name, 0))


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_success_full_pipeline(caplog, tmp_path):
    """All steps succeed → returns 0 (upload OK), plaintext removed, .age + stamp kept."""
    fake = _FakeSubprocess(
        run_rcs={"gzip": 0, "/usr/local/bin/backup-cleanup.sh": 0, "age": 0, "/usr/local/bin/upload-s3.sh": 0}
    )

    rc = backup_postgres.run_backup(
        spool_dir=str(tmp_path),
        timestamp="20260802T000000Z",
        env=_VALID_ENV,
        runner=fake,
    )

    assert rc == 0
    # All steps executed in order: gzip -t → cleanup → du → age-encrypt → upload
    run_names = [call[0][0] for call in fake.run_calls]
    assert run_names == [
        "gzip",
        "/usr/local/bin/backup-cleanup.sh",
        "du",
        "age",
        "/usr/local/bin/upload-s3.sh",
    ]
    # zcat-валидация выполнена (Popen zcat присутствует)
    zcat_calls = [call[0] for call in fake.popen_calls if call[0][0] == "zcat"]
    assert len(zcat_calls) == 1, "text-format structure validation must run zcat"
    # Plaintext dump removed after encryption; encrypted artifact retained in spool
    plain = tmp_path / "pgdumpall_20260802T000000Z.sql.gz"
    encrypted = tmp_path / "pgdumpall_20260802T000000Z.sql.gz.age"
    assert not plain.exists(), "plaintext dump must be removed after successful encryption"
    assert encrypted.exists(), "encrypted artifact must remain for cleanup/retry contract"
    # Upload receives the ENCRYPTED file and .age S3 key (SEC-0018)
    upload_call = next(call for call in fake.run_calls if call[0][0] == "/usr/local/bin/upload-s3.sh")
    assert upload_call[0][1] == str(encrypted), "upload must receive the encrypted artifact"
    assert upload_call[0][2] == "postgres/pgdumpall_20260802T000000Z.sql.gz.age"
    # R5/TRAP[BUG] 2026-08-03 (126-chaos W1): pg_dumpall должен идти с явным портом —
    #   regression-вход: pgbouncer:5432 «Connection refused» (порт не передавался)
    dump_cmd = fake.popen_calls[0][0]
    assert dump_cmd[0] == "pg_dumpall"
    assert "-p" in dump_cmd and dump_cmd[dump_cmd.index("-p") + 1] == "5432", (
        f"pg_dumpall must pass explicit POSTGRES_PORT: {dump_cmd}"
    )
    logger.info("[IMP:9][test] full pipeline: encrypt→upload OK, stamp=%s", (tmp_path / ".last_verified").exists())


@ldd_trajectory
def test_last_verified_stamp_written_only_after_verification(caplog, tmp_path):
    """REF-0009: .last_verified пишется ТОЛЬКО после gzip -t OK + structure validation."""
    # Success path → stamp exists
    ok_fake = _FakeSubprocess(run_rcs={"age": 0})
    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t-ok", env=_VALID_ENV, runner=ok_fake)
    assert rc == 0
    stamp = tmp_path / ".last_verified"
    assert stamp.exists(), "freshness stamp must be written after verified backup"

    # NEGATIVE (R5): gzip -t failure → NO stamp (corrupted dump never fakes freshness)
    bad_gzip_dir = tmp_path / "bad-gzip"
    bad_gzip_dir.mkdir()
    fail_fake = _FakeSubprocess(run_rcs={"gzip": 1})
    rc_fail = backup_postgres.run_backup(
        spool_dir=str(bad_gzip_dir), timestamp="t-bad", env=_VALID_ENV, runner=fail_fake
    )
    assert rc_fail == 1
    assert not list(bad_gzip_dir.iterdir()), "failed pipeline leaves nothing (incl. no stamp)"

    # NEGATIVE (R5): structure validation failure (no header marker) → NO stamp
    class _NoHeaderSubprocess(_FakeSubprocess):
        def Popen(self, cmd: list, **kwargs):
            if cmd[0] == "zcat":
                return _FakePopenProc(0, stdout_text="-- no header here\nCREATE TABLE x ();\n")
            return super().Popen(cmd, **kwargs)

    no_header_dir = tmp_path / "no-header"
    no_header_dir.mkdir()
    rc_header = backup_postgres.run_backup(
        spool_dir=str(no_header_dir), timestamp="t-hdr", env=_VALID_ENV, runner=_NoHeaderSubprocess(run_rcs={"gzip": 0})
    )
    assert rc_header == 1
    assert not list(no_header_dir.iterdir()), "structure-invalid dump: no artifacts, no stamp"
    logger.info("[IMP:9][test] stamp honesty: only verified backups write .last_verified ✓")


@ldd_trajectory
def test_pg_dumpall_failure_removes_partial_dump(caplog, tmp_path):
    """pg_dumpall returncode != 0 → CRITICAL, return 1, partial dump removed."""
    fake = _FakeSubprocess(dump_rc=1)

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV, runner=fake)

    assert rc == 1
    assert not list(tmp_path.iterdir()), "partial dump must be removed on failure"


@ldd_trajectory
def test_gzip_pipe_failure(caplog, tmp_path):
    """gzip (pipe) returncode != 0 → CRITICAL, return 1, partial dump removed."""
    fake = _FakeSubprocess(dump_rc=0, gzip_rc=1)

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV, runner=fake)

    assert rc == 1
    assert not list(tmp_path.iterdir()), "partial dump must be removed on failure"


@ldd_trajectory
def test_gzip_t_integrity_failure(caplog, tmp_path):
    """gzip -t fails → FAIL, return 1, corrupted dump removed."""
    fake = _FakeSubprocess(run_rcs={"gzip": 1})

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV, runner=fake)

    assert rc == 1
    assert not list(tmp_path.iterdir()), "corrupted dump must be removed"


@ldd_trajectory
def test_dump_structure_validation_failure(caplog, tmp_path):
    """zcat-валидация text-дампа не находит header-маркер → FAIL, return 1, dump removed.

    R5-negative: прежний детектор (pg_restore --list) пропускал текст-дампы pg_dumpall
    («input file appears to be a text format dump») — regression-вход: zcat без
    «PostgreSQL database cluster dump» маркера обязан быть отвергнут.
    """

    class _NoHeaderSubprocess(_FakeSubprocess):
        def Popen(self, cmd: list, **kwargs):
            if cmd[0] == "zcat":
                return _FakePopenProc(0, stdout_text="-- no header here\nCREATE TABLE x ();\n")
            return super().Popen(cmd, **kwargs)

    no_header_fake = _NoHeaderSubprocess(run_rcs={"gzip": 0})

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV, runner=no_header_fake)

    assert rc == 1
    assert not list(tmp_path.iterdir()), "structurally-invalid dump must be removed"


@ldd_trajectory
def test_upload_failure_non_blocking(caplog, tmp_path):
    """Upload failure does NOT fail the backup (DevPlan 119 C1 «не блокировать при
    ошибке upload») — rc 0, encrypted dump retained in spool, IMP:9 warning logged."""
    fake = _FakeSubprocess(
        run_rcs={"gzip": 0, "/usr/local/bin/backup-cleanup.sh": 0, "age": 0, "/usr/local/bin/upload-s3.sh": 1}
    )

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV, runner=fake)

    assert rc == 0, "upload failure must NOT fail the local backup (C1 non-blocking)"
    # Encrypted artifact retained in spool for daily rescan retry (plaintext removed)
    assert (tmp_path / "pgdumpall_t.sql.gz.age").exists(), "encrypted dump retained for retry"
    assert not (tmp_path / "pgdumpall_t.sql.gz").exists(), "plaintext must not linger after encryption"
    # Upload still invoked after the dump (off-site chain active)
    run_names = [call[0][0] for call in fake.run_calls]
    assert "/usr/local/bin/upload-s3.sh" in run_names, "upload-s3.sh must be invoked after dump"


# ═══════════════════════════════════════════════════════════════════
# REF-0009: age-encrypt перед upload (SEC-0018) — fail-closed контракт
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_missing_age_recipient_fail_closed(caplog, tmp_path):
    """Нет AGE_RECIPIENT → upload SKIPPED (fail-closed): plaintext НЕ уходит с ноды,
    дамп остаётся в spool для daily rescan retry, rc 0 + IMP:9 alert."""
    env = dict(_VALID_ENV)
    del env["AGE_RECIPIENT"]
    fake = _FakeSubprocess(run_rcs={"gzip": 0})

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=env, runner=fake)

    assert rc == 0, "missing recipient is an alertable config error, не фейл локального бэкапа"
    run_names = [call[0][0] for call in fake.run_calls]
    assert "age" not in run_names and "/usr/local/bin/upload-s3.sh" not in run_names, (
        "fail-closed: ни шифрования (некуда), ни загрузки plaintext'а"
    )
    assert (tmp_path / "pgdumpall_t.sql.gz").exists(), "verified dump retained for retry"
    warns = [r.message for r in caplog.records if "AGE_RECIPIENT" in r.message]
    assert warns, "IMP:9 alert про отсутствие AGE_RECIPIENT обязателен"


@ldd_trajectory
def test_age_encryption_failure_retains_plaintext_no_upload(caplog, tmp_path):
    """age rc!=0 → plaintext НЕ удаляется, upload НЕ вызывается, rc 0 (C1 semantics)."""
    fake = _FakeSubprocess(run_rcs={"gzip": 0, "age": 1})

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV, runner=fake)

    assert rc == 0
    assert (tmp_path / "pgdumpall_t.sql.gz").exists(), "encryption failed → plaintext retained"
    run_names = [call[0][0] for call in fake.run_calls]
    assert "/usr/local/bin/upload-s3.sh" not in run_names, "no upload without successful encryption"
    logger.info("[IMP:9][test] age-fail: plaintext retained, no plaintext off-node ✓")


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_missing_postgres_host_fails(caplog, tmp_path):
    """POSTGRES_HOST missing → FAIL (return 1) before any subprocess call."""
    fake = _FakeSubprocess()

    env = dict(_VALID_ENV)
    del env["POSTGRES_HOST"]
    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=env, runner=fake)

    assert rc == 1
    assert fake.popen_calls == [], "no dump may start without POSTGRES_HOST"


@ldd_trajectory
def test_missing_postgres_password_fails(caplog, tmp_path):
    """POSTGRES_PASSWORD missing → FAIL (return 1)."""
    fake = _FakeSubprocess()

    env = dict(_VALID_ENV)
    del env["POSTGRES_PASSWORD"]
    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=env, runner=fake)

    assert rc == 1
    assert fake.popen_calls == [], "no dump may start without POSTGRES_PASSWORD"
