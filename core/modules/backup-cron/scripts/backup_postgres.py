#!/usr/bin/env python3
# GREP_SUMMARY: backup-postgres pg_dumpall spool-volume timestamp retention phase-02
# STRUCTURE: validate_env → mkdir spool → pg_dumpall+gzip(Popen) → gzip -t verify → pg_restore --list validate → retention cleanup → upload-s3
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
  - backup-cleanup.sh called after validation (non-fatal if it fails)
  - S3 upload delegated to upload-s3.sh (via /usr/local/bin/upload-s3.sh)
  - All subprocess failures detected via explicit returncode checks
    (shell PIPESTATUS → Popen returncode parity, DevPlan 117 H D64)
@ rationale Single pg_dumpall covers all project DBs; per-DB dumps added in
            phase 06 if needed.
@ changes  Python port of core/modules/backup-cron/scripts/backup-postgres.sh
           (2026-08-02, DevPlan 117 Brief H D64). Shell → thin wrapper.

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
      postgres:16 \
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

logger = logging.getLogger(__name__)

__all__ = ["main", "run_backup"]

# ── Canonical paths inside the backup-cron container ──
_CLEANUP_SCRIPT = "/usr/local/bin/backup-cleanup.sh"
_UPLOAD_SCRIPT = "/usr/local/bin/upload-s3.sh"


# ═══════════════════════════════════════════════════════════════════
# region FUNC_run_backup
## @purpose  Execute the full postgres backup pipeline.
## @param spool_dir   Spool base dir override (default: $BACKUP_SPOOL_DIR or
##                    /var/lib/platform/backup-spool, + /postgres)
## @param timestamp   Timestamp override for deterministic tests
##                    (default: UTC YYYYMMDDTHHMMSSZ)
## @param env         Environment override (default: os.environ)
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
) -> int:
    """Run the full postgres backup pipeline — mirrors backup-postgres.sh."""
    effective_env = os.environ if env is None else env

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
        if not postgres_host:
            logger.error("[IMP:9][validate] FAIL: POSTGRES_HOST not set")
            return 1
        if not postgres_password:
            logger.error("[IMP:9][validate] FAIL: POSTGRES_PASSWORD not set")
            return 1

        Path(spool_dir).mkdir(parents=True, exist_ok=True)

        # ── [dump] pg_dumpall | gzip (PIPESTATUS parity → Popen returncodes) ──
        logger.info("[IMP:7][dump] Running pg_dumpall → %s", dump_file)
        pg_env = dict(effective_env)
        pg_env["PGPASSWORD"] = postgres_password
        dump_proc = subprocess.Popen(
            ["pg_dumpall", "-h", postgres_host, "-U", effective_env.get("POSTGRES_USER", "postgres")],
            stdout=subprocess.PIPE,
            env=pg_env,
        )
        with open(dump_file, "wb") as gz_out:
            gzip_proc = subprocess.Popen(
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
            subprocess.run(["gzip", "-t", dump_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
            != 0
        ):
            logger.error("[IMP:10][verify] FAIL: gzip integrity check failed — file corrupted")
            return 1
        logger.info("[IMP:8][verify] gzip integrity OK")

        # ── [verify] pg_restore structure validation ──
        logger.info("[IMP:7][verify] Validating dump structure via pg_restore --list")
        zcat_proc = subprocess.Popen(["zcat", dump_file], stdout=subprocess.PIPE)
        restore_rc = subprocess.run(
            ["pg_restore", "--list", "-"],
            stdin=zcat_proc.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        if zcat_proc.stdout is not None:
            zcat_proc.stdout.close()
        zcat_proc.wait()
        if restore_rc != 0:
            logger.error("[IMP:10][verify] FAIL: pg_restore --list validation failed — dump structure invalid")
            return 1
        logger.info("[IMP:8][verify] Dump structure validation OK")

        # ── [cleanup] Retention rotation (non-fatal) ──
        logger.info("[IMP:7][cleanup] Running retention cleanup")
        try:
            if subprocess.run([_CLEANUP_SCRIPT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                logger.warning("[IMP:8][cleanup] WARNING: backup-cleanup.sh failed (non-fatal)")
        except FileNotFoundError:
            logger.warning("[IMP:8][cleanup] WARNING: backup-cleanup.sh not found (non-fatal)")

        backup_success = True
        size = ""
        try:
            size = subprocess.run(["du", "-sh", dump_file], capture_output=True, text=True).stdout.split()[0]
        except (FileNotFoundError, IndexError):
            size = str(os.path.getsize(dump_file))
        logger.info("[IMP:9][done] BACKUP COMPLETE: %s (size=%s)", dump_file, size)

        # ── [upload] S3 upload (last step) — DevPlan 119 C1: off-site бэкапы критичны.
        #    Семантика «не блокировать при ошибке upload»: exit code ПРОВЕРЯЕТСЯ и
        #    логируется (IMP:9/IMP:10), но НЕ проваливает бэкап — локальный дамп
        #    верифицирован и безопасен в spool; upload.py сам ретраит 3×90 мин и
        #    сохраняет файл в spool при неудаче (no data loss).
        # 🧐 TRAP[DECISION] · 2026-08-02 · — · Upload failure НЕ проваливает бэкап (C1)
        # · Rejected: 117 H D64 set -e parity (upload rc → backup rc) — ложно-критичная
        #   алертинг-семантика для ретраябельной проблемы; дамп уже верифицирован локально
        # · Reason: DevPlan 119 C1 «с проверкой exit code, не блокировать при ошибке upload»;
        #   upload.py сохраняет файл в spool при неудаче → retry без потери данных
        # · Rev: если off-site подтверждение станет жёстким требованием — вернуть propagation
        s3_key = f"postgres/pgdumpall_{ts}.sql.gz"
        upload_rc = subprocess.run([_UPLOAD_SCRIPT, dump_file, s3_key]).returncode
        if upload_rc != 0:
            logger.critical(
                "[IMP:9][upload] WARNING: upload-s3.sh exit=%d — off-site backup NOT confirmed; "
                "dump retained in spool (%s) for manual retry",
                upload_rc,
                dump_file,
            )
        else:
            logger.critical("[IMP:9][upload] UPLOAD OK: %s → s3://postgres/%s", dump_file, s3_key)
        return 0
    finally:
        # Shell trap cleanup_partial EXIT parity: remove partial dump on any
        # failure path (backup_success not reached) without hiding the status.
        if not backup_success and os.path.exists(dump_file):
            logger.warning("[IMP:8][trap] Cleaning up partial dump: %s", dump_file)
            try:
                os.remove(dump_file)
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
