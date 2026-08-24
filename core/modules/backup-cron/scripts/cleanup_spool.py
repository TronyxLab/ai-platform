#!/usr/bin/env python3
# GREP_SUMMARY: cleanup-spool spool retention sentinel-gated uploaded last_verified REF-0009 BUG-0802
# STRUCTURE: ▶ main → ◇ spool exists? → ○ files -mtime +7 → ◇ .uploaded sentinel? → ⊕ delete confirmed │ orphan sweep │ skip pending → ∑ counts → ⎋ 0|1
# region MODULE_CONTRACT
"""
Sentinel-gated spool cleanup (REF-0009, BUG-0802 ≡ DATA-502).

@purpose  Remove spool backup files ONLY after confirmed S3 upload (sibling
          ``.uploaded`` sentinel written by upload.py). Files WITHOUT a sentinel
          are NEVER deleted by age — they stay in spool for the daily rescan
          retry (spool_retry.py) until off-site copy is confirmed.
@scope    core/modules/backup-cron/scripts/; invoked via backup-cleanup.sh thin
          facade from crontab (04:00 UTC) and from backup_postgres.py post-dump.
@input    BACKUP_SPOOL_DIR env (default /var/lib/platform/backup-spool),
          optional argv[1] retention days override (default 7, tests).
@output   Exit 0 = ok (deletes and/or skips), exit 1 = fatal I/O error.
@invariants
  - ``.last_verified`` freshness stamp is NEVER deleted (REF-0009: collector
    reads its mtime; deletion would fake staleness or hide real backups).
  - Data file with sibling ``<file>.uploaded`` → delete file + its sentinel.
  - Orphan sentinels (data file gone — crash window between confirm and rm)
    are swept.
  - Plain files without sentinel older than retention → KEPT + counted as
    pending-retry (IMP:8 per file, IMP:9 summary) — the entire point of
    BUG-0802 fix: unuploaded data is not silently destroyed at day 7.
  - Legacy job-run markers ``.backup_ran_*`` (app-data stub, phase-02) are
    not backups — deletable by age as before.
  - Only touches BACKUP_SPOOL_DIR subtree — never live data.
@rationale Q: why Python port instead of extending backup-cleanup.sh? A: language
          policy Tier-1 Strangler trigger (>3 new business-logic branches in
          shell); sh facade keeps _CLEANUP_SCRIPT/crontab paths frozen.
@changes  2026-08-25 | REF-0009 (meta-refactoring W2) — created (shell logic ported)
"""
# endregion MODULE_CONTRACT

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["plan_cleanup", "run_cleanup"]

SENTINEL_SUFFIX = ".uploaded"
LAST_VERIFIED_NAME = ".last_verified"
LEGACY_MARKER_PREFIX = ".backup_ran_"
DEFAULT_RETENTION_DAYS = 7


# region FUNC_plan_cleanup
## @purpose  Чистая функция планировщика: файлы старше retention → решение
##           (delete | delete-with-sentinel | orphan | marker | keep-pending).
## @io       ⇥ (files: list[str], now: float, retention_days: int) →
##           ⎋ tuple[list[str], list[str], list[str], list[str]] —
##           (confirmed_delete, orphan_sentinels, legacy_markers, pending_keep)
## @complexity O(N) — N файлов
## @invariants  Чистая функция без I/O — детерминизм для unit-тестов; mtime читает вызывающий код
def plan_cleanup(
    files: list[str],
    now: float,
    retention_days: int,
    *,
    stat_fn: Callable[[str], float] | None = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Classify spool files into delete/sweep/keep buckets (fresh files skipped).

    Args:
        files: absolute spool file paths to classify (ALL files, not only aged —
            sentinel/data pairing needs full-directory membership).
        now: current epoch seconds (determinism in tests).
        retention_days: age threshold in days.
        stat_fn: optional path→mtime override (DI); None = os.path.getmtime.

    Returns:
        (confirmed_delete, orphan_sentinels, legacy_markers, pending_keep).
        confirmed_delete entries include BOTH the data file and its sentinel.
    """
    stat = os.path.getmtime if stat_fn is None else stat_fn
    cutoff = now - retention_days * 86400
    confirmed: list[str] = []
    orphans: list[str] = []
    markers: list[str] = []
    pending: list[str] = []

    path_by_name = {str(Path(f).resolve()): f for f in files}
    for filepath in sorted(path_by_name):
        name = Path(filepath).name
        try:
            mtime = stat(filepath)
        except OSError:
            logger.warning("[IMP:8][cleanup_spool][plan] stat failed, skipping: %s", filepath)
            continue
        if mtime > cutoff:
            continue  # fresh — вне ретеншена, не классифицируем

        # Freshness stamp — никогда не удаляется (контракт collector'а)
        if name == LAST_VERIFIED_NAME:
            pending.append(filepath)
            continue

        # Сам sentinel: оphan, если данных больше нет; иначе удалим вместе с данными
        if name.endswith(SENTINEL_SUFFIX):
            data_path = str(Path(filepath[: -len(SENTINEL_SUFFIX)]).resolve())
            if data_path not in path_by_name:
                orphans.append(filepath)
            # else: пара удалится при обработке data-файла ниже
            continue

        # Легаси app-data маркеры прогона job'а (не бэкапы) — age-delete как раньше
        if name.startswith(LEGACY_MARKER_PREFIX):
            markers.append(filepath)
            continue

        # Данные: удаляем ТОЛЬКО при подтверждённой загрузке (sibling-sentinel в списке;
        # вызывающий передаёт ПОЛНЫЙ листинг каталога — walk в run_cleanup)
        if filepath + SENTINEL_SUFFIX in path_by_name:
            confirmed.append(filepath)
            confirmed.append(filepath + SENTINEL_SUFFIX)
        else:
            pending.append(filepath)

    return confirmed, orphans, markers, pending


# endregion FUNC_plan_cleanup


# region FUNC__unlink_best_effort
## @purpose  Пофайловое best-effort удаление (try вне цикла — PERF203): гонка с
##           параллельным upload (FileNotFoundError) — не фатально (03 §8 v1).
## @io       ⇥ filepath: str → ⎋ bool (True = удалён)
## @complexity O(1)
def _unlink_best_effort(filepath: str) -> bool:
    """Remove one spool file; missing file = already gone (race), OSError = logged."""
    try:
        Path(filepath).unlink()
    except FileNotFoundError:
        return False  # гонка с параллельным upload — не фатально (03 §8 v1)
    except OSError as exc:
        logger.error("[IMP:9][cleanup_spool][delete] Failed to remove %s: %s", filepath, exc)
        return False
    logger.info("[IMP:8][cleanup_spool][delete] Removed: %s", filepath)
    return True


# endregion FUNC__unlink_best_effort


# region FUNC_run_cleanup
## @purpose  I/O-обёртка: собрать aged-файлы spool → plan_cleanup → rm по корзинам → отчёт.
## @io       ⇥ (spool_dir, retention_days) → ⎋ int exit code (0 ok, 1 fatal)
## @complexity O(N)
## @invariants  Удаление best-effort пофайлово (ошибка одного файла не валит проход);
##              pending_count > 0 НЕ меняет exit code (это штатный retry-сценарий, алертит Loki)
def run_cleanup(spool_dir: str, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Scan spool for aged files, apply sentinel-gated deletion. Returns exit code."""
    import time

    base = Path(spool_dir)
    if not base.is_dir():
        logger.warning("[IMP:8][cleanup_spool][scan] Spool directory does not exist: %s", spool_dir)
        return 0

    now = time.time()
    all_files: list[str] = []
    for root, _dirs, fnames in os.walk(base):
        all_files.extend(str(Path(root) / fname) for fname in fnames)

    confirmed, orphans, markers, pending = plan_cleanup(all_files, now, retention_days)

    deleted = 0
    for bucket in (confirmed, orphans, markers):
        for filepath in bucket:
            if _unlink_best_effort(filepath):
                deleted += 1

    logger.info(
        "[IMP:9][cleanup_spool][done] Cleanup complete: deleted=%d pending=%d retention=%dd (sentinel-gated)",
        deleted,
        len(pending),
        retention_days,
    )
    for filepath in pending:
        logger.info("[IMP:8][cleanup_spool][pending] Kept (no .uploaded sentinel): %s", filepath)
    return 0


# endregion FUNC_run_cleanup


# region FUNC_main
## @purpose  CLI entry point: BACKUP_SPOOL_DIR env + optional retention-days argv → run_cleanup.
## @exitcode 0  Success
## @exitcode 1  Fatal (unexpected I/O error)
def main(argv: list[str] | None = None) -> int:
    """CLI entry point for cleanup_spool.py."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s][backup-cleanup] %(message)s", stream=sys.stderr)
    args = list(sys.argv[1:] if argv is None else argv)
    retention = int(args[0]) if args else DEFAULT_RETENTION_DAYS
    spool_dir = os.environ.get("BACKUP_SPOOL_DIR", "/var/lib/platform/backup-spool")
    return run_cleanup(spool_dir, retention)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
