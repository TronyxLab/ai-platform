# GREP_SUMMARY: check-project-fixer, auto-fix, project-fix, hygiene-fix, ruff-format, ruff-check-fix, repair-practices, sync-practices
# STRUCTURE: ▶ fix_hygiene (трайлинг/CRLF/final-newline in-place) → ⊕ fix_ruff_format (ruff format .) → ⊕ fix_ruff_check (ruff check --fix) → ⊕ repair_practices (sync_practices force — ЕДИНАЯ точка) → ⎋ автофикс-канал --fix
# region MODULE_CONTRACT
## @purpose  Автофикс-ветки project-fix (DevPlan 170 W10-A декомпозиция): --fix исполняет
##           локальные автофиксы вместо отчёта FAIL — hygiene (трайлинг-пробелы/CRLF/final
##           newline через shared/atomic_writer), ruff format/check --fix, drift-repair
##           (перегенерация GENERATED-практик через sync_practices force=True). Единая точка
##           импорта sync_practices: устраняет lazy-дубль sync_practices ×3 из drift.py
##           (research-A §2 / wave-briefs W10: «check_project lazy sync_practices ×3 → единая
##           точка (fixer.py при декомпозиции)»).
## @scope    Потребители: checks/file.py (hygiene fix-ветка), checks/tool.py (ruff format/check
##           fix-ветки), drift.py (repair-ветка). Вызывается ТОЛЬКО при fix=True (handler-ы
##           решают по check.auto_fix); read-only прогон НЕ пишет в проект.
## @invariants
##   - hygiene-фикс in-place best-effort: OSError/UnicodeDecodeError файла → пропуск (тихо)
##   - fix_ruff_format: rc ∈ {0, 1} — успех (1 = файлы отформатированы), иной rc → WARN
##   - repair_practices — ЕДИНСТВЕННАЯ точка, где check_project-пакет вызывает sync_practices
##   - Все writes через shared/atomic_writer (единый writer, DevPlan 119 E5)
## @rationale Выделение автофиксов из handler-ов: 4 fix-ветки используют общий слой;
##            единая точка sync_practices устраняет 3 дублирующих lazy-импорта (аудит 156
##            W10: «lazy ×3 (753,761,801)» — разрыв импорт-графа).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:1305-1320
##           + fix-ветки ruff-format/ruff-check + drift-repair)
# endregion MODULE_CONTRACT

from __future__ import annotations

from pathlib import Path

from core.internal.practices.check_project.exec import subprocess_run, tail
from core.internal.practices.check_project.files import iter_text_files
from core.internal.shared.atomic_writer import atomic_write_text


# region FUNC_fix_hygiene
## @purpose  Автофикс hygiene: CRLF → LF, rstrip(' \\t'), удаление хвостовых пустых строк,
##           гарантированный финальный \\n. In-place best-effort (файл не читается → пропуск).
## @io       ⇥ project_dir: Path → ⎋ None (мутация текстовых файлов проекта)
## @complexity O(F * S) — файлы × размер
def fix_hygiene(project_dir: Path) -> None:
    """Auto-fix trailing whitespace + CRLF + final newline (in-place, best-effort)."""
    for path, _ in iter_text_files(project_dir):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fixed = content.replace("\r\n", "\n")
        lines = [line.rstrip(" \t") for line in fixed.split("\n")]
        while lines and not lines[-1]:
            lines.pop()
        fixed = "\n".join(lines) + "\n"
        if fixed != content:
            atomic_write_text(path, fixed)


# endregion FUNC_fix_hygiene


# region FUNC_fix_ruff_format
## @purpose  ruff format . (--fix-ветка ruff-format проверки): форматирует проект каноном ruff.
## @io       ⇥ project_dir: Path, timeout: int → ⎋ tuple[int, str] — (rc, stderr-сниппет)
## @complexity O(T) — прогон форматтера
def fix_ruff_format(project_dir: Path, timeout: int) -> tuple[int, str]:
    """Run `ruff format .`; returns (rc, stderr-snippet) — rc ∈ {0, 1} считается успехом."""
    rc_fix, _, err_fix, _ = subprocess_run(["ruff", "format", "."], project_dir, timeout)
    return rc_fix, tail(err_fix)


# endregion FUNC_fix_ruff_format


# region FUNC_fix_ruff_check
## @purpose  ruff check --fix (--fix-ветка ruff-check проверки): авто-исправление полного
##           набора правил (RUFF_FULL_SELECT/IGNORE канона) в проекте.
## @io       ⇥ project_dir: Path, timeout: int, select: list[str] → ⎋ None (мутация кода)
## @complexity O(T) — прогон ruff check --fix
def fix_ruff_check(project_dir: Path, timeout: int, select: list[str]) -> None:
    """Run `ruff check --fix` with explicit canon select/ignore; result re-checked by caller."""
    subprocess_run(["ruff", "check", "--fix", *select, "."], project_dir, timeout)


# endregion FUNC_fix_ruff_check


# region FUNC_repair_practices
## @purpose  Drift-repair: перегенерация GENERATED-практик + practices.lock до канона.
##           ЕДИНСТВЕННАЯ точка, где check_project-пакет вызывает sync_practices
##           (устраняет lazy-дубль ×3 из старого check_project.py — research-A §2/W10).
## @io       ⇥ project_dir: Path → ⎋ None (sync_practices(force=True) — repair дрейфа)
## @complexity O(N * C) — рендер + атомарные записи
def repair_practices(project_dir: Path) -> None:
    """Regenerate GENERATED practices files + lock via sync_practices(force=True)."""
    from core.internal.practices.sync_practices import sync_practices

    sync_practices(project_dir, force=True)


# endregion FUNC_repair_practices
