#!/usr/bin/env python3
# GREP_SUMMARY: spool-retry daily-rescan unuploaded sentinel age-encrypt upload REF-0009 BUG-0802
# STRUCTURE: ▶ main → ○ scan spool subdirs → ◇ sentinel? skip │ encrypt-if-plain → ⊕ upload-s3.sh → ∑ summary → ⎋ 0|1
# region MODULE_CONTRACT
"""
Daily spool rescan retry (REF-0009): re-upload backups left in spool.

@purpose  Close the auto-retry gap: when the nightly S3 upload fails (outage,
          missing AGE_RECIPIENT, network), the dump stays in spool WITHOUT a
          ``.uploaded`` sentinel — cleanup will never delete it (BUG-0802 fix),
          and this job retries the off-side copy once a day until confirmed.
@scope    core/modules/backup-cron/scripts/; invoked via spool-retry-upload.sh
          thin facade from crontab (01:30 UTC, flock-guarded).
@input    BACKUP_SPOOL_DIR env (default /var/lib/platform/backup-spool);
          AGE_RECIPIENT env (public key) for encrypting plain dumps.
@output   Exit 0 = nothing pending or all retried OK; exit 1 = some files remain
          unuploaded (visible in cron logs / Loki BackupUploadFailure alert).
@invariants
  - Candidates: regular non-dot files in postgres/ and app-data/ subdirs without
    sibling ``.uploaded`` sentinel (legacy ``.backup_ran_*`` markers excluded).
  - Plain dumps (.sql.gz) are encrypted FIRST via age_cipher.age_encrypt — same
    fail-closed contract as backup_postgres.py; plaintext never uploaded.
  - SEC-0018 (plan 012 T7): pre_restore_* plaintext snapshots excluded from the
    scan entirely (writer moved to backup-spool/pre-restore/; legacy leftovers
    in scan dirs skipped by prefix with a WARN).
  - Already-encrypted artifacts (.age) are uploaded as-is with key
    ``<subdir>/<filename>`` (matches nightly pipeline naming).
  - Per-file failure is non-fatal: remaining candidates still attempted;
    exit code reflects overall result.
  - 0 imports from core/internal (container module contract).
@rationale Q: why not retry inside upload.py only? A: in-process retries (3×30min)
          die with the process; an S3 outage longer than 90 min previously meant
          the dump sat in spool until cleanup destroyed it at day 7 (BUG-0802).
          Daily rescan makes RPO 24h+1d honest instead of fictional.
@changes  2026-08-25 | REF-0009 (meta-refactoring W2) — created
"""
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

# Same directory — container script dir on sys.path (contract: backup_postgres.py)
import age_cipher  # pyright: ignore[reportImplicitRelativeImport]

logger = logging.getLogger(__name__)

__all__ = ["find_pending", "run_retry"]

_UPLOAD_SCRIPT = "/usr/local/bin/upload-s3.sh"
_LEGACY_MARKER_PREFIX = ".backup_ran_"
_SENTINEL_SUFFIX = ".uploaded"
_SPOOL_SUBDIRS = ("postgres", "app-data")
# SEC-0018 (plan 012 T7): plaintext pre_restore снапшоты (postgres restore target) живут
# в backup-spool/pre-restore/ ВНЕ скана; legacy-экземпляры, оставшиеся в postgres/ от
# старых версий, дополнительно исключаются по префиксу — plaintext не попадает в S3.
_PRE_RESTORE_PREFIX = "pre_restore_"


# region DATA_RunnerLike
class _RunResultLike(Protocol):
    """Контракт subprocess.CompletedProcess (run-only)."""

    returncode: int


class RunnerLike(Protocol):
    """Контракт DI-runner (subprocess-модуль | fake) — DevPlan 167 D2 pattern.

    DEVNULL — read-only property (ковариантность: int-константа subprocess
    совместима с object; mutable-атрибут был бы инвариантен).
    """

    @property
    def DEVNULL(self) -> object: ...

    def run(self, args: list[str], **kwargs: object) -> _RunResultLike: ...


# endregion DATA_RunnerLike


# region FUNC_find_pending
## @purpose  Собрать кандидатов на retry: файлы без sibling-.uploaded sentinel.
## @io       ⇥ spool_dir: str | None (None = env/дефолт) → ⎋ list[tuple[str, str]] —
##           [(local_path, s3_key_relative), ...] в детерминированном порядке
## @complexity O(N) — N файлов spool
## @invariants  Чистый скан без I/O-записи; dotfiles и легаси-маркеры исключены;
##              порядок сортировки стабилен (детерминизм тестов и логов)
def find_pending(spool_dir: str | None = None) -> list[tuple[str, str]]:
    """Return [(local_path, s3_key)] for spool files lacking a .uploaded sentinel."""
    base = Path(spool_dir or os.environ.get("BACKUP_SPOOL_DIR", "/var/lib/platform/backup-spool"))
    pending: list[tuple[str, str]] = []
    if not base.is_dir():
        logger.warning("[IMP:8][spool_retry][scan] Spool directory does not exist: %s", base)
        return pending
    for subdir in _SPOOL_SUBDIRS:
        dirpath = base / subdir
        if not dirpath.is_dir():
            continue
        for filepath in sorted(dirpath.iterdir()):
            name = filepath.name
            if not filepath.is_file() or name.startswith("."):
                continue
            if name.startswith(_LEGACY_MARKER_PREFIX):
                continue
            # SEC-0018 (plan 012 T7): plaintext pre_restore снапшоты не загружаются —
            # легаси-файлы в скан-каталоге исключаются по префиксу (громко, не молча)
            if name.startswith(_PRE_RESTORE_PREFIX):
                logger.warning(
                    "[IMP:8][spool_retry][scan] Skipping plaintext pre-restore snapshot (SEC-0018, "
                    "move to backup-spool/pre-restore/): %s",
                    filepath,
                )
                continue
            # Сам sentinel-файл — НЕ кандидат на upload (маркер, не данные)
            if name.endswith(_SENTINEL_SUFFIX):
                continue
            if Path(str(filepath) + _SENTINEL_SUFFIX).exists():
                logger.info("[IMP:8][spool_retry][scan] Skipping (already confirmed): %s", filepath)
                continue
            pending.append((str(filepath), f"{subdir}/{name}"))
    return pending


# endregion FUNC_find_pending


# region FUNC_run_retry
## @purpose  Retry-цикл: plain .sql.gz → encrypt (AGE_RECIPIENT), затем upload-s3.sh.
## @io       ⇥ (spool_dir, runner=None DI) → ⎋ int (0 = всё подтверждено/пусто, 1 = есть неуспех)
## @complexity O(N × 1 subprocess)
## @invariants  Fail-closed шифрования: нет AGE_RECIPIENT → plain-дампы НЕ загружаются,
##              остаются в spool (IMP:9 alert); per-file failure не прерывает проход
def run_retry(spool_dir: str | None = None, runner: RunnerLike | None = None) -> int:
    """Re-attempt uploads for pending spool files. Returns aggregate exit code."""
    runner_mod = runner if runner is not None else subprocess
    pending = find_pending(spool_dir)
    if not pending:
        logger.info("[IMP:8][spool_retry][done] No pending spool files — nothing to retry")
        return 0

    recipient = os.environ.get("AGE_RECIPIENT", "")
    failures = 0
    for local_file, s3_key in pending:
        target, target_key = local_file, s3_key
        if local_file.endswith(".sql.gz"):
            # Plain дамп: шифруем ДО выгрузки (fail-closed, как в nightly pipeline)
            if not recipient:
                logger.critical(
                    "[IMP:9][spool_retry][encrypt] AGE_RECIPIENT not set — plaintext not uploaded: %s",
                    local_file,
                )
                failures += 1
                continue
            encrypted = f"{local_file}.age"
            # W11: subprocess-модуль → RunnerLike через cast (typeshed-оверлоады run()
            # не выражаются протоколом; fake-объекты тестов удовлетворяют ему структурно)
            if not age_cipher.age_encrypt(
                local_file, encrypted, recipient, runner=cast("age_cipher.RunnerLike", runner_mod)
            ):
                logger.critical("[IMP:9][spool_retry][encrypt] Encryption failed: %s", local_file)
                failures += 1
                continue
            try:
                Path(local_file).unlink()
                logger.info("[IMP:8][spool_retry][encrypt] Plaintext removed: %s", local_file)
            except OSError as exc:
                logger.warning("[IMP:8][spool_retry][encrypt] Cannot remove plaintext %s: %s", local_file, exc)
            target, target_key = encrypted, f"{s3_key}.age"

        rc = runner_mod.run([_UPLOAD_SCRIPT, target, target_key], check=False).returncode
        if rc == 0:
            # upload-s3.sh сам удаляет spool-файл после верифицированной загрузки
            logger.critical("[IMP:9][spool_retry][upload] RETRY UPLOAD OK: %s → %s", target, target_key)
        else:
            logger.warning(
                "[IMP:8][spool_retry][upload] Retry failed (rc=%d), retained in spool: %s",
                rc,
                target,
            )
            failures += 1

    logger.info("[IMP:9][spool_retry][done] Rescan complete: attempted=%d failures=%d", len(pending), failures)
    return 1 if failures else 0


# endregion FUNC_run_retry


# region FUNC_main
## @purpose  CLI entry point: run_retry + propagate aggregate exit code.
## @exitcode 0  Nothing pending or all retries succeeded
## @exitcode 1  Some files remain unuploaded
def main() -> int:
    """CLI entry point for spool_retry.py."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s][spool-retry] %(message)s", stream=sys.stderr)
    return run_retry()


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
