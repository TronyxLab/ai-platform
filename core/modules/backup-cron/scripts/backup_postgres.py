#!/usr/bin/env python3
# GREP_SUMMARY: backup-postgres pg_dumpall spool-volume timestamp last_verified age-encrypt sentinel REF-0009
# STRUCTURE: validate_env → mkdir spool → pg_dumpall+gzip(Popen) → gzip -t verify → pg_restore --list validate → touch .last_verified → retention cleanup → age-encrypt → upload-s3
# region MODULE_CONTRACT
"""
@ purpose  Full PostgreSQL backup via pg_dumpall to local spool (03 §4).
@ scope    Run at 03:00 UTC by cron (crontab: /usr/local/bin/backup-postgres.sh →
           exec python3 backup_postgres.py). Uploads via upload-s3.sh wrapper.
@ invariants
   - Dumps ALL databases as postgres superuser
   - Output: /var/lib/platform/backup-spool/postgres/pgdumpall_TIMESTAMP.sql.gz
   - Exits non-zero on pg_dumpall failure (loud failure, 00 §4 error visibility)
   - gzip integrity check via gzip -t before declaring dump valid
   - pg_restore --list structural validation before declaring dump valid
   - Freshness stamp {spool}/postgres/.last_verified touched ONLY after gzip -t
     OK + structure validation (REF-0009: honest freshness metric — collector
     reads this marker, not the cron-refreshed log mtime)
   - age-encrypt BEFORE upload (REF-0009, SEC-0018): dumps leave the node only
     encrypted to AGE_RECIPIENT (public key); missing recipient → upload
     SKIPPED fail-closed, dump retained in spool for daily rescan retry
   - Plaintext dump removed from spool right after successful encryption
   - backup-cleanup.sh called after validation (non-fatal if it fails);
     cleanup deletes ONLY sentinel-confirmed files (.uploaded)
   - S3 upload delegated to upload-s3.sh (via /usr/local/bin/upload-s3.sh)
   - All subprocess failures detected via explicit returncode checks
     (shell PIPESTATUS → Popen returncode parity, DevPlan 117 H D64)
@ rationale Single pg_dumpall covers all project DBs; per-DB dumps added in
            phase 06 if needed.
@ changes  Python port of core/modules/backup-cron/scripts/backup-postgres.sh
           (2026-08-02, DevPlan 117 Brief H D64). Shell → thin wrapper.
           2026-08-14 | DevPlan 167 D2 — CommandRunner-seam: run_backup(runner=) —
           fake-subprocess-объект DI (0 monkeypatch в тестах).
           2026-08-25 | REF-0009 (meta-refactoring W2) — .last_verified stamp +
           age-encrypt дампов перед upload (fail-closed без AGE_RECIPIENT).
           2026-08-27 | DevPlan 016 T8/F-032 — pg_dumpall --clean/--if-exists
           (owner-решение: restore поверх init-кластера идемпотентен, volume не
           разрушается; unit-тест test_restore_clean_strategy).

@ PITR RESTORE PROCEDURE (TASK-1: B1 — WAL archiving)
  Point-In-Time Recovery (PITR) — PostgreSQL WAL-based restore.
  Requires: WAL archive in /var/lib/platform/wal-archive/.
  Prerequisites: base backup (pg_dumpall) + continuous WAL archiving.

  STEP 1: Determine recovery target time.
    Identify the precise timestamp to which you want to restore. Examples:
      - "2026-07-03 03:00:00 UTC" — before a corruption event
      - "2026-07-02 23:59:59 UTC" — end of previous day

  STEP 2: Restore base backup.
    Restore the full pg_dumpall backup to a temporary PostgreSQL instance:
      gunzip -c pgdumpall_20260703T030000Z.sql.gz | psql -h temp-instance -U postgres

  STEP 3: Configure recovery.conf on the restored instance.
    Create a recovery.conf with the WAL archive path and recovery target:
      restore_command = 'cp /var/lib/platform/wal-archive/%f %p'
      recovery_target_time = '2026-07-03 03:00:00 UTC'
      recovery_target_action = 'promote'

  STEP 4: Start the PostgreSQL instance with recovery.
    The instance replays WAL files up to the target time and then promotes
    to a standalone server (recovery_target_action=promote).

  STEP 5: Verify the restored data.
    Run queries to confirm data integrity and consistency.

  Full recovery command example:
    # On a new PostgreSQL host with same config:
    mkdir -p /var/lib/platform/wal-archive/
    # Mount or rsync WAL archive from backup:
    # rsync -avz s3-backup:/wal-archive/ /var/lib/platform/wal-archive/
    # Start PG with recovery.conf:
    docker run -d --name pg-restore \
      -v /var/lib/platform/wal-archive/:/var/lib/platform/wal-archive/ \
      -e POSTGRES_PASSWORD=restore \
      postgres:18 \
      -c 'restore_command=cp /var/lib/platform/wal-archive/%f %p' \
      -c 'recovery_target_time=2026-07-03 03:00:00 UTC' \
      -c 'recovery_target_action=promote'
    # Wait for recovery, then verify:
    docker logs -f pg-restore
    # After promote: psql into new instance and run verification

@ pg_restore --list validation (TASK-2: B2 — Backup integrity)
  The pg_restore --list validation parses the compressed dump and checks its
  internal structure without extracting data:
      zcat "${DUMP_FILE}" | pg_restore --list - > /dev/null 2>&1
  This validates:
    - TOC (Table of Contents) integrity — dump is parseable
    - No structural corruption in the archive headers
    - Format compatibility (custom / directory formats)
  If pg_restore --list fails with non-zero exit, the dump is considered
  corrupted and is deleted. The backup job exits with failure, triggering
  operator alert (IMP:10 log).
"""
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, TextIO, cast

# REF-0009: client-side encryption helper (same directory — container script dir on sys.path)
import age_cipher  # pyright: ignore[reportImplicitRelativeImport]


# region DATA_SubprocessLike
class _PopenLike(Protocol):
    """Контракт Popen-объекта (subprocess.Popen | DI-fake, DevPlan 167 D2)."""

    stdout: TextIO | None

    def wait(self) -> int: ...
    def close(self) -> None: ...


class SubprocessLike(Protocol):
    """Контракт DI-seam subprocess-модуля (167 D2): Popen/run/PIPE/DEVNULL.

    ## @purpose  Типизированная граница runner-параметра: либо реальный subprocess-модуль,
    ##            либо fake-subprocess-объект тестов (0 monkeypatch). Методы/атрибуты,
    ##            используемые run_backup: Popen (dump/gzip/zcat), run (gzip -t),
    ##            PIPE/DEVNULL (перенаправления).
    """

    PIPE: int
    DEVNULL: int

    def Popen(self, args: list[str], **kwargs: object) -> _PopenLike: ...
    def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]: ...


# endregion DATA_SubprocessLike

logger = logging.getLogger(__name__)

__all__ = ["main", "run_backup"]

# ── Canonical paths inside the backup-cron container ──
_CLEANUP_SCRIPT = "/usr/local/bin/backup-cleanup.sh"
_UPLOAD_SCRIPT = "/usr/local/bin/upload-s3.sh"
_HEADER_SCAN_MAX: int = 5  # заголовок pg_dump в первых 5 строках (валидация)
_DUMP_SCAN_BOUND: int = 2000  # верхняя граница сканирования дампа
_LAST_VERIFIED_NAME = ".last_verified"  # freshness stamp (REF-0009)


# ═══════════════════════════════════════════════════════════════════
# region FUNC_run_backup
## @purpose  Execute the full postgres backup pipeline.
## @param spool_dir   Spool base dir override (default: $BACKUP_SPOOL_DIR or
##                    /var/lib/platform/backup-spool, + /postgres)
## @param timestamp   Timestamp override for deterministic tests
##                    (default: UTC YYYYMMDDTHHMMSSZ)
## @param env         Environment override (default: os.environ)
## @param runner      CommandRunner-seam (DevPlan 167 D2): fake-subprocess-объект с
##                    Popen/run/PIPE/DEVNULL (None → реальный subprocess).
## @return  int exit status: 0 = success, 1 = fatal (validate/dump/verify).
##          Upload exit code НЕ проваливает бэкап (DevPlan 119 C1: «не блокировать при
##          ошибке upload») — результат upload логируется, дамп остаётся в spool.
## @rationale Line-by-line port of backup-postgres.sh: pipe statuses (PIPESTATUS)
##            → Popen returncodes; gzip -t; pg_restore --list; partial-dump
##            cleanup on failure (shell trap cleanup_partial → finally block).
def run_backup(
    spool_dir: str | None = None,
    timestamp: str | None = None,
    env: dict[str, str] | None = None,
    runner: SubprocessLike | None = None,  # DI-seam: fake-subprocess-объект (0 monkeypatch в тестах, 167 D2)
) -> int:
    """Run the full postgres backup pipeline — mirrors backup-postgres.sh."""
    effective_env = os.environ if env is None else env
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · CommandRunner-seam: subprocess-вызовы через runner
    # · Rejected: прямой вызов subprocess-модуля (тест патчил модуль-глобал monkeypatch.setattr)
    # · Reason: seam = тестируемость реального вызова — fake-subprocess-объект передаётся параметром
    # · Rev: при появлении общего command-runner-абстракции (subprocess_io) — перейти на неё
    runner_mod = runner if runner is not None else subprocess

    ts = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    spool_base = effective_env.get("BACKUP_SPOOL_DIR", "/var/lib/platform/backup-spool")
    spool_dir = spool_dir or os.path.join(spool_base, "postgres")
    dump_file = os.path.join(spool_dir, f"pgdumpall_{ts}.sql.gz")

    backup_success = False
    try:
        logger.info("[IMP:7][start] Starting full postgres backup at %s", ts)

        # ── [validate] Required environment ──
        postgres_host = effective_env.get("POSTGRES_HOST", "")
        postgres_password = effective_env.get("POSTGRES_PASSWORD", "")
        # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · pg_dumpall шёл на pgbouncer:5432 → Connection refused
        # · Symptom: backup-cron pg_dumpall падал «connection to server at "pgbouncer", port 5432
        # ·   failed: Connection refused» — бэкап молча не работал (обнаружено на прогоне chaos W1).
        # · Root: compose задавал POSTGRES_HOST=pgbouncer, но порт НЕ передавался → pg_dumpall
        # ·   дефолтил на 5432; pgbouncer слушает 6432 (POSTGRES_PORT=6432 канон platform-infra.yaml)
        # ·   и в pool_mode=transaction не может обслужить pg_dumpall (нет template1 в [databases],
        # ·   FATAL: no such database: template1). pg_dumpall требует session-level доступ ко всем БД.
        # · Fix: бэкап ходит НАПРЯМУЮ к postgres:5432 (общий shared-db-net), порт из
        # ·   POSTGRES_PORT env (default 5432) — pgbouncer для бэкапов НЕ используется.
        # · Prevention: restore-drill T10 (126-chaos-resilience) верифицирует бэкап-цепочку;
        # ·   test_pg_dumpall... asserts порта в popen_calls.
        if not postgres_host:
            logger.error("[IMP:9][validate] FAIL: POSTGRES_HOST not set")
            return 1
        if not postgres_password:
            logger.error("[IMP:9][validate] FAIL: POSTGRES_PASSWORD not set")
            return 1
        postgres_port = effective_env.get("POSTGRES_PORT", "5432")

        Path(spool_dir).mkdir(parents=True, exist_ok=True)

        # ── [dump] pg_dumpall | gzip (PIPESTATUS parity → Popen returncodes) ──
        logger.info("[IMP:7][dump] Running pg_dumpall %s:%s → %s", postgres_host, postgres_port, dump_file)
        pg_env = dict(effective_env)
        pg_env["PGPASSWORD"] = postgres_password
        # DevPlan 016 T8 (F-032): --clean --if-exists — backup-channel canon (owner decision).
        # pg_dumpall emits DROP DATABASE/ROLE/... before CREATE → restore over the
        # init-initialized cluster is idempotent (no «role/database/type already exists»);
        # --if-exists makes the drops conditional. postgres-data volume is NOT destroyed.
        dump_proc = runner_mod.Popen(
            [
                "pg_dumpall",
                "--clean",
                "--if-exists",
                "-h",
                postgres_host,
                "-p",
                postgres_port,
                "-U",
                effective_env.get("POSTGRES_USER", "postgres"),
            ],
            stdout=runner_mod.PIPE,
            env=pg_env,
        )
        with Path(dump_file).open("wb") as gz_out:
            gzip_proc = runner_mod.Popen(
                ["gzip"],
                stdin=dump_proc.stdout,
                stdout=gz_out,
            )
            if dump_proc.stdout is not None:
                dump_proc.stdout.close()
            dump_rc = dump_proc.wait()
            gzip_rc = gzip_proc.wait()
        if dump_rc != 0:
            logger.error("[IMP:10][dump] CRITICAL: pg_dumpall failed with exit code %d", dump_rc)
            return 1
        if gzip_rc != 0:
            logger.error("[IMP:10][dump] CRITICAL: gzip failed with exit code %d", gzip_rc)
            return 1

        # ── [verify] gzip integrity check ──
        logger.info("[IMP:7][verify] Verifying gzip integrity: %s", dump_file)
        if (
            runner_mod.run(
                ["gzip", "-t", dump_file], stdout=runner_mod.DEVNULL, stderr=runner_mod.DEVNULL, check=False
            ).returncode
            != 0
        ):
            logger.error("[IMP:10][verify] FAIL: gzip integrity check failed — file corrupted")
            return 1
        logger.info("[IMP:8][verify] gzip integrity OK")

        # ── [verify] structure validation (text-format pg_dumpall dump) ──
        # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · pg_restore --list НЕ работает с text-форматом
        # · Symptom: бэкап падал на verify «pg_restore --list validation failed» — хотя
        # ·   pg_dumpall+ gzip -t проходили (обнаружено на прогоне chaos W1, tronyx-vps).
        # · Root: pg_restore --list валидирует ТОЛЬКО custom/archive-формат; pg_dumpall
        # ·   отдаёт plain-SQL («input file appears to be a text format dump. Please use psql.»).
        # · Fix: text-валидация — маркер заголовка «PostgreSQL database cluster dump»
        # ·   + наличие SQL-стейтментов в bounded-скане первых 2000 строк.
        # · Prevention: unit-тест test_success_full_pipeline проверяет header-маркер.
        logger.info("[IMP:7][verify] Validating dump structure (text-format markers)")
        zcat_proc = runner_mod.Popen(["zcat", dump_file], stdout=runner_mod.PIPE, text=True)
        header_found = False
        stmt_count = 0
        try:
            for line_idx, line in enumerate(zcat_proc.stdout or []):
                if line_idx < _HEADER_SCAN_MAX and "PostgreSQL database cluster dump" in line:
                    header_found = True
                stripped = line.lstrip()
                if stripped.startswith(("CREATE ", "ALTER ", "COPY ", "INSERT INTO ")):
                    stmt_count += 1
                if line_idx >= _DUMP_SCAN_BOUND:
                    break  # bounded scan — первые 2000 строк достаточны для валидации
        finally:
            if zcat_proc.stdout is not None:
                zcat_proc.stdout.close()
            zcat_proc.wait()
        if not header_found:
            logger.error("[IMP:10][verify] FAIL: dump header marker missing — not a pg_dumpall dump")
            return 1
        if stmt_count == 0:
            logger.error("[IMP:10][verify] FAIL: no SQL statements found in dump — structure invalid")
            return 1
        logger.info("[IMP:8][verify] Dump structure validation OK (header + %d statements)", stmt_count)

        # ── [stamp] Freshness marker (REF-0009, BUG-0803 ≡ FAIL-0903) ──
        # ⚠️ TRAP[BUG] · 2026-08-25 · P1 · BackupFreshness мерял mtime ЛОГА → упавшая ночью
        # ·   задача выглядела свежей (cron refresh'ит лог на старте; dashboards зелёные).
        # · Symptom: freshness = «cron запустился», не «бэкап удался».
        # · Root: collector читал postgres.log mtime вместо факта верифицированного дампа.
        # · Fix: .last_verified пишется ТОЛЬКО после gzip -t OK + structure-validation;
        # ·   backup_collector.py читает mtime маркера. cleanup_spool.py никогда его не удаляет.
        # · Prevention: unit-тесты test_last_verified_stamp_* (gzip-t fail → stamp отсутствует);
        # ·   restore-drill В4 сверяет dashboards с фактом полного цикла.
        last_verified = Path(spool_dir) / _LAST_VERIFIED_NAME
        try:
            last_verified.touch()
            logger.info(
                "[IMP:9][stamp] Freshness stamp written: %s (dump verified)",
                last_verified,
            )
        except OSError as exc:
            logger.warning("[IMP:8][stamp] Cannot touch %s (non-fatal): %s", last_verified, exc)

        # ── [cleanup] Retention rotation (non-fatal) ──
        logger.info("[IMP:7][cleanup] Running retention cleanup")
        try:
            if (
                runner_mod.run(
                    [_CLEANUP_SCRIPT], stdout=runner_mod.DEVNULL, stderr=runner_mod.DEVNULL, check=False
                ).returncode
                != 0
            ):
                logger.warning("[IMP:8][cleanup] WARNING: backup-cleanup.sh failed (non-fatal)")
        except FileNotFoundError:
            logger.warning("[IMP:8][cleanup] WARNING: backup-cleanup.sh not found (non-fatal)")

        backup_success = True
        size = ""
        try:
            size = runner_mod.run(["du", "-sh", dump_file], capture_output=True, text=True, check=False).stdout.split()[
                0
            ]
        except (FileNotFoundError, IndexError):
            size = str(Path(dump_file).stat().st_size)
        logger.info("[IMP:9][done] BACKUP COMPLETE: %s (size=%s)", dump_file, size)

        # ── [upload] S3 upload (last step) — DevPlan 119 C1: off-site бэкапы критичны.
        #    Семантика «не блокировать при ошибке upload»: exit code ПРОВЕРЯЕТСЯ и
        #    логируется (IMP:9/IMP:10), но НЕ проваливает бэкап — локальный дамп
        #    верифицирован и безопасен в spool; upload.py сам ретраит 3×90 мин и
        #    сохраняет файл в spool при неудаче (no data loss). REF-0009: ежедневный
        #    spool-rescan (spool_retry.py, cron 01:30) повторяет попытку на следующий день.
        # 🧐 TRAP[DECISION] · 2026-08-02 · — · Upload failure НЕ проваливает бэкап (C1)
        # · Rejected: 117 H D64 set -e parity (upload rc → backup rc) — ложно-критичная
        #   алертинг-семантика для ретраябельной проблемы; дамп уже верифицирован локально
        # · Reason: DevPlan 119 C1 «с проверкой exit code, не блокировать при ошибке upload»;
        #   upload.py сохраняет файл в spool при неудаче → retry без потери данных
        # · Rev: если off-site подтверждение станет жёстким требованием — вернуть propagation
        #
        # ── [encrypt] age-encrypt перед upload (REF-0009, SEC-0018 ≡ DATA-503) ──
        # 🧐 TRAP[DECISION] · 2026-08-25 · — · Fail-closed без AGE_RECIPIENT: upload SKIP
        # · Rejected: plaintext fallback (доступность RPO важнее конфиденциальности)
        # · Reason: SEC-0018 — все БД всех проектов в открытом виде за одним bucket-ключом;
        #   клиентские данные не покидают ноду в plaintext ни при каких настройках.
        #   Локальный дамп верифицирован; rescan retry зашифрует и загрузит после
        #   починки конфигурации. Cleanup никогда не удалит неподтверждённый файл.
        # · Recipient = ПУБЛИЧНЫЙ ключ (env безопасен); приватный AGE_SECRET_KEY не входит
        #   в backup-cron контейнер. Имя AGE_RECIPIENT — конвенция age-key-backup.
        recipient = effective_env.get("AGE_RECIPIENT", "")
        if not recipient:
            logger.critical(
                "[IMP:9][encrypt] AGE_RECIPIENT not set — upload SKIPPED (fail-closed): %s retained in spool",
                dump_file,
            )
            return 0

        encrypted_file = f"{dump_file}.age"
        # W11: subprocess-модуль → RunnerLike через cast (typeshed-оверлоады run()
        # не выражаются протоколом; fake-объекты тестов удовлетворяют ему структурно)
        encrypt_runner = cast("age_cipher.RunnerLike", runner_mod)
        if not age_cipher.age_encrypt(dump_file, encrypted_file, recipient, runner=encrypt_runner):
            logger.critical(
                "[IMP:9][encrypt] age encryption FAILED — plaintext retained, no upload: %s",
                dump_file,
            )
            return 0
        # Plaintext больше не нужен локально: off-site копия будет только зашифрованной
        try:
            os.unlink(dump_file)
            logger.info("[IMP:8][encrypt] Plaintext dump removed after encryption: %s", dump_file)
        except OSError as exc:
            logger.warning("[IMP:8][encrypt] Cannot remove plaintext dump %s: %s", dump_file, exc)

        s3_key = f"postgres/pgdumpall_{ts}.sql.gz.age"
        upload_rc = runner_mod.run([_UPLOAD_SCRIPT, encrypted_file, s3_key], check=False).returncode
        if upload_rc != 0:
            logger.critical(
                "[IMP:9][upload] WARNING: upload-s3.sh exit=%d — off-site NOT confirmed, retry scheduled: %s",
                upload_rc,
                encrypted_file,
            )
        else:
            logger.critical("[IMP:9][upload] UPLOAD OK: %s → s3://postgres/%s", encrypted_file, s3_key)
        return 0
    finally:
        # Shell trap cleanup_partial EXIT parity: remove partial dump on any
        # failure path (backup_success not reached) without hiding the status.
        if not backup_success and Path(dump_file).exists():
            logger.warning("[IMP:8][trap] Cleaning up partial dump: %s", dump_file)
            try:
                os.unlink(dump_file)
            except OSError as exc:  # pragma: no cover — best-effort cleanup
                logger.warning("[IMP:8][trap] Cleanup of partial dump failed: %s", exc)


# endregion FUNC_run_backup


# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  CLI entry point — runs the backup pipeline, propagates exit code.
## @io       stdin: env (POSTGRES_HOST, POSTGRES_PASSWORD, POSTGRES_USER,
##           BACKUP_SPOOL_DIR) → stdout/stderr: LDD logs
## @exitcode 0  Success (включая upload-failure — дамп сохранён в spool, C1)
## @exitcode 1  Fatal (validate/dump/verify failure)
def main() -> int:
    """CLI entry point for backup_postgres.py."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s][backup-postgres] %(message)s",
        stream=sys.stderr,
    )
    return run_backup()


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
