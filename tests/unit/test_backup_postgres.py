"""
# GREP_SUMMARY: test backup_postgres pg_dumpall gzip-t pg_restore --list returncode retention upload
# STRUCTURE: ▶ success pipeline 1× → ▶ 3 проверки fail (dump rc, gzip -t, pg_restore) → ▶ gzip pipe fail → ▶ missing env 2× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/backup-cron/scripts/backup_postgres.py (DevPlan 117 H D64).
##           subprocess fully mocked — zero real pg_dumpall/gzip/pg_restore calls.
## @scope    Tests all 3 integrity checks (pg_dumpall returncode, gzip -t, pg_restore --list),
##           gzip-pipe failure, missing env validation, and the full success pipeline.
## @invariants
##   - subprocess.Popen/run mocked via monkeypatch (module-level subprocess replacement)
##   - spool_dir/timestamp/env passed explicitly (Zero Hardcode Rule — no env reliance)
##   - Partial dump removed on failure (trap cleanup parity)
##   - Upload exit code НЕ проваливает бэкап (DevPlan 119 C1: «не блокировать при
##     ошибке upload») — upload failure → rc 0, дамп остаётся в spool
##   - @ldd_trajectory asserts IMP:9 log presence
## @rationale DevPlan 09 §D64: unit coverage for the Python backup port — production
##            backup is critical, so every failure branch must be covered.
## @changes 2026-08-02 | Created (Brief H D64)
##           2026-08-02 | DevPlan 119 C1 — test_upload_exit_code_propagated → non-blocking
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
import backup_postgres

_VALID_ENV = {
    "POSTGRES_HOST": "postgres",
    "POSTGRES_PASSWORD": "test-password",
    "POSTGRES_USER": "postgres",
    "BACKUP_SPOOL_DIR": "/var/lib/platform/backup-spool",
}


class _FakeRunResult:
    """Fake subprocess.CompletedProcess."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


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
    """Drop-in fake subprocess module: command-name-indexed returncodes."""

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
        raise AssertionError(f"unexpected Popen command: {cmd}")

    def run(self, cmd: list, **kwargs):
        self.run_calls.append([cmd, kwargs])
        name = cmd[0]
        if name == "du":
            return _FakeRunResult(stdout=f"1.2M\t{cmd[1]}\n")
        return _FakeRunResult(returncode=self.run_rcs.get(name, 0))


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_success_full_pipeline(caplog, tmp_path, monkeypatch):
    """All steps succeed → returns 0 (upload OK) and dump file is kept."""
    fake = _FakeSubprocess(
        run_rcs={"gzip": 0, "pg_restore": 0, "/usr/local/bin/backup-cleanup.sh": 0, "/usr/local/bin/upload-s3.sh": 0}
    )
    monkeypatch.setattr(backup_postgres, "subprocess", fake)

    rc = backup_postgres.run_backup(
        spool_dir=str(tmp_path),
        timestamp="20260802T000000Z",
        env=_VALID_ENV,
    )

    assert rc == 0
    # All checks executed: gzip -t + text-валидация (zcat Popen), cleanup+du+upload
    run_names = [call[0][0] for call in fake.run_calls]
    assert run_names == [
        "gzip",
        "/usr/local/bin/backup-cleanup.sh",
        "du",
        "/usr/local/bin/upload-s3.sh",
    ]
    # zcat-валидация выполнена (Popen zcat присутствует)
    zcat_calls = [call[0] for call in fake.popen_calls if call[0][0] == "zcat"]
    assert len(zcat_calls) == 1, "text-format structure validation must run zcat"
    # Dump file kept on success (no trap cleanup)
    assert (tmp_path / "pgdumpall_20260802T000000Z.sql.gz").exists()
    # R5/TRAP[BUG] 2026-08-03 (126-chaos W1): pg_dumpall должен идти с явным портом —
    #   regression-вход: pgbouncer:5432 «Connection refused» (порт не передавался)
    dump_cmd = fake.popen_calls[0][0]
    assert dump_cmd[0] == "pg_dumpall"
    assert "-p" in dump_cmd and dump_cmd[dump_cmd.index("-p") + 1] == "5432", (
        f"pg_dumpall must pass explicit POSTGRES_PORT: {dump_cmd}"
    )


@ldd_trajectory
def test_pg_dumpall_failure_removes_partial_dump(caplog, tmp_path, monkeypatch):
    """pg_dumpall returncode != 0 → CRITICAL, return 1, partial dump removed."""
    fake = _FakeSubprocess(dump_rc=1)
    monkeypatch.setattr(backup_postgres, "subprocess", fake)

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV)

    assert rc == 1
    assert not list(tmp_path.iterdir()), "partial dump must be removed on failure"


@ldd_trajectory
def test_gzip_pipe_failure(caplog, tmp_path, monkeypatch):
    """gzip (pipe) returncode != 0 → CRITICAL, return 1, partial dump removed."""
    fake = _FakeSubprocess(dump_rc=0, gzip_rc=1)
    monkeypatch.setattr(backup_postgres, "subprocess", fake)

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV)

    assert rc == 1
    assert not list(tmp_path.iterdir()), "partial dump must be removed on failure"


@ldd_trajectory
def test_gzip_t_integrity_failure(caplog, tmp_path, monkeypatch):
    """gzip -t fails → FAIL, return 1, corrupted dump removed."""
    fake = _FakeSubprocess(run_rcs={"gzip": 1})
    monkeypatch.setattr(backup_postgres, "subprocess", fake)

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV)

    assert rc == 1
    assert not list(tmp_path.iterdir()), "corrupted dump must be removed"


@ldd_trajectory
def test_dump_structure_validation_failure(caplog, tmp_path, monkeypatch):
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
    monkeypatch.setattr(backup_postgres, "subprocess", no_header_fake)

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV)

    assert rc == 1
    assert not list(tmp_path.iterdir()), "structurally-invalid dump must be removed"


@ldd_trajectory
def test_upload_failure_non_blocking(caplog, tmp_path, monkeypatch):
    """Upload failure does NOT fail the backup (DevPlan 119 C1 «не блокировать при
    ошибке upload») — rc 0, dump retained in spool, IMP:9 upload warning logged."""
    fake = _FakeSubprocess(
        run_rcs={"gzip": 0, "pg_restore": 0, "/usr/local/bin/backup-cleanup.sh": 0, "/usr/local/bin/upload-s3.sh": 1}
    )
    monkeypatch.setattr(backup_postgres, "subprocess", fake)

    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=_VALID_ENV)

    assert rc == 0, "upload failure must NOT fail the local backup (C1 non-blocking)"
    # Dump retained in spool for manual/retry upload
    assert (tmp_path / "pgdumpall_t.sql.gz").exists()
    # Upload still invoked after the dump (off-site chain active)
    run_names = [call[0][0] for call in fake.run_calls]
    assert "/usr/local/bin/upload-s3.sh" in run_names, "upload-s3.sh must be invoked after dump"


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_missing_postgres_host_fails(caplog, tmp_path, monkeypatch):
    """POSTGRES_HOST missing → FAIL (return 1) before any subprocess call."""
    fake = _FakeSubprocess()
    monkeypatch.setattr(backup_postgres, "subprocess", fake)

    env = dict(_VALID_ENV)
    del env["POSTGRES_HOST"]
    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=env)

    assert rc == 1
    assert fake.popen_calls == [], "no dump may start without POSTGRES_HOST"


@ldd_trajectory
def test_missing_postgres_password_fails(caplog, tmp_path, monkeypatch):
    """POSTGRES_PASSWORD missing → FAIL (return 1)."""
    fake = _FakeSubprocess()
    monkeypatch.setattr(backup_postgres, "subprocess", fake)

    env = dict(_VALID_ENV)
    del env["POSTGRES_PASSWORD"]
    rc = backup_postgres.run_backup(spool_dir=str(tmp_path), timestamp="t", env=env)

    assert rc == 1
    assert fake.popen_calls == [], "no dump may start without POSTGRES_PASSWORD"
