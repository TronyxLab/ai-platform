"""
# GREP_SUMMARY: check-suite, journal, runs.jsonl, test-journal, record-run, junit-counts, mtime-guard
# STRUCTURE: ▶ journal_run ┌goal|exit_code|junit_paths|start_wall┐ → ◇ CHECK_JOURNAL=0?→no-op → ○ aggregate junit ┌mtime-гард: fresh only┐ → ○ test_journal.record_run ┌best-effort┐ → ⎋ None
# region MODULE_CONTRACT
## @purpose  Журналирование прогонов пакета check_suite (DevPlan 170 W3 — извлечено из
##           монолита core/internal/check_suite.py): структурированная запись в
##           .ai/logs/runs.jsonl (shared.test_journal) после КАЖДОГО прогона всех режимов.
## @scope    core/internal/check_suite/journal.py — stdlib-only. Потребитель: __init__.py (_cmd_run).
## @invariants
##   - CHECK_JOURNAL=0 → полный no-op (детерминизм unit-тестов executor'а)
##   - raw_log из MAKE_LOG_FILE (make-log-shell.sh) — симлинк latest.log указывает туда
##   - Ошибки журнала логируются, но НИКОГДА не меняют exit-код прогона
##   - Stats агрегируются из junit-файлов чеков, записанных ВО ВРЕМЯ этого прогона
##     (mtime-гард против stale-файлов прошлых прогонов)
## @rationale Выделение журнального слоя из монолита (research-A §1) — те же функции и семантика;
##            monkeypatch-контракт check_suite.test_journal сохраняется (общий модуль shared).
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package); 170 private-imports:
##           journal_run переименована в публичное имя (U-07)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from core.internal.shared import test_journal

logger = logging.getLogger(__name__)


# region RUN_SINGLE_AND_TEST_FILE


# region FUNC_journal_run
## @purpose  Структурированная запись прогона в .ai/logs/runs.jsonl (DevPlan 165 W2).
##           Stats (pass/fail/skip/error) агрегируются из junit-файлов чеков, записанных
##           ВО ВРЕМЯ этого прогона (mtime-гард против stale-файлов прошлых прогонов).
## @io       ⇥ root, goal, exit_code, junit_paths, start_wall (time.time()!) → None
## @complexity O(J * T) где J = junit-файлы, T = testsuite-элементы
## @invariants
##   - CHECK_JOURNAL=0 → полный no-op (детерминизм unit-тестов executor'а)
##   - raw_log из MAKE_LOG_FILE (make-log-shell.sh) — симлинк latest.log указывает туда
##   - Ошибки журнала логируются, но НИКОГДА не меняют exit-код прогона
##   ⚠️ TRAP[BUG] · 2026-08-13 · P2 · st_mtime сравнивался с time.monotonic()
##   · Root: monotonic() считает от загрузки ОС, st_mtime — unix-epoch → гард «свежести»
##   ·   пропускал stale-файлы прошлых прогонов (статистика искажалась).
##   · Fix: параметр start_wall = time.time() (wall-clock, единая эпоха со st_mtime).
def journal_run(
    root: Path,
    goal: str,
    exit_code: int,
    junit_paths: list[str],
    start_wall: float,
) -> None:
    """Append a structured run record to the test journal (never affects the run outcome)."""
    if os.environ.get("CHECK_JOURNAL") == "0":
        return
    duration_s = time.time() - start_wall
    pass_count = fail_count = skip_count = error_count = 0
    for rel in junit_paths:
        junit_file = root / rel
        if not junit_file.is_file():
            continue
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            # mtime-гард: только файлы, записанные ЭТИМ прогоном (stale-файлы прошлых
            # прогонов не искажают статистику текущего)
            if junit_file.stat().st_mtime < start_wall - 1.0:
                continue
            p, f, s, e, _ = test_journal.junit_counts(junit_file)
            pass_count += p
            fail_count += f
            skip_count += s
            error_count += e
        except (OSError, ValueError, TypeError):
            logger.info("[IMP:7][journal][warn] cannot aggregate junit %s — пропуск", junit_file)
    try:
        test_journal.record_run(
            goal=goal,
            exit_code=exit_code,
            pass_count=pass_count,
            fail_count=fail_count,
            skip_count=skip_count,
            error_count=error_count,
            duration_s=duration_s,
            raw_log=os.environ.get("MAKE_LOG_FILE"),
        )
    # ruff: ignore[BLE001] — журнал прогонов best-effort — не роняет прогон
    except Exception as exc:  # noqa: EXC — best-effort: журнал не должен ронять прогон
        logger.info("[IMP:7][journal][warn] journal record failed: %s", exc)


# endregion FUNC_journal_run

# endregion RUN_SINGLE_AND_TEST_FILE
