# GREP_SUMMARY: check-project-files, iter-files, excluded-dirs, code-extensions, grep-summary, hygiene-scan, text-extensions
# STRUCTURE: ▶ iter_project_files (walk, EXCLUDED_DIRS-фильтр) → ⊕ iter_text_files (гигиена) → ⊕ iter_code_files (grep-summary) → ⊕ iter_code_files_by_languages (языко-зависимый скан) → ⊕ missing_grep_summary / parse_structured → ⎋ файловые итераторы проверок
# region MODULE_CONTRACT
## @purpose  Файловые итераторы и детекторы check_project (DevPlan 170 W10-A декомпозиция):
##           обход проекта с исключением кэшей/библиотек (EXCLUDED_DIRS из practices/constants),
##           фильтрация по текстовым/кодовым расширениям, языко-зависимый скан исходников
##           (python → .py; typescript/react → ts/tsx/js/jsx; sh → .sh), GREP_SUMMARY-детектор
##           (первые 10 строк — канон gate), TOML/JSON-парсер (hygiene invalid-syntax).
## @scope    Потребители: checks/file.py (hygiene/grep-summary/docs-in-code/transition/agent-check),
##           checks/compose.py (restart-policies — через iter_project_files), fixer.py
##           (гигиена-автофикс — через iter_text_files), checks/tool.py (shellcheck — файлы .sh).
##           НЕ импортируется runner/cli (лист — только вниз).
## @invariants
##   - EXCLUDED_DIRS — единый канон из practices/constants (12 каталогов, включая .mypy_cache)
##   - iter_text_files: текст-суффиксы + Dockerfile/Makefile по имени (compose-имена НЕ
##     литералятся — гейт compose_files_sole_path: только shared/compose_files)
##   - iter_code_files_by_languages: пустой/неизвестный набор языков → ВСЕ код-суффиксы
##     (безопасный fallback, не угадываем язык)
##   - missing_grep_summary: OSError → пропуск (read-only, тихий fallback)
## @rationale  Выделение файлового слоя — 5 проверок используют обход; единая точка
##             консистентности исключений (research-A §2: дубль констант устранён).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:1226-1331)
# endregion MODULE_CONTRACT

from __future__ import annotations

import os
from pathlib import Path

from core.internal.practices.constants import CODE_EXTENSIONS, EXCLUDED_DIRS

# ── Расширения текстовых файлов для hygiene-скана (трайлинг/CRLF/конфликты/ключи) ──
TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".py",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".sh",
    ".md",
    ".txt",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".html",
    ".css",
})

# ── Суффиксы кода по языкам канона (transition-traces-ban / agent-check, 164 W5-1) ──
_LANG_CODE_SUFFIXES: dict[str, frozenset[str]] = {
    "python": frozenset({".py"}),
    "typescript": frozenset({".ts", ".tsx", ".js", ".jsx"}),
    "react": frozenset({".ts", ".tsx", ".js", ".jsx"}),
    "sh": frozenset({".sh"}),
}
_ALL_CODE_SUFFIXES: frozenset[str] = CODE_EXTENSIONS


# region HELPERS_iter_files
def iter_project_files(project_dir: Path):
    """Iterate project files skipping excluded dirs (node_modules/.venv/...)."""
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for name in files:
            yield Path(root) / name


def iter_text_files(project_dir: Path):
    """Iterate text files (known text extensions) for hygiene scan."""
    for path in iter_project_files(project_dir):
        # docker-compose.yml покрыт .yml расширением — отдельный литерал запрещён
        # (гейт compose_files_sole_path: compose-имена только из shared/compose_files)
        if path.suffix.lower() in TEXT_EXTENSIONS or path.name in {"Dockerfile", "Makefile"}:
            yield path, True


def iter_code_files(project_dir: Path):
    """Iterate code files (py/ts/tsx/js/jsx/sh) for grep-summary scan."""
    for path in iter_project_files(project_dir):
        if path.suffix.lower() in CODE_EXTENSIONS:
            yield path


def iter_code_files_by_languages(project_dir: Path, languages: tuple[str, ...]):
    """Iterate code files по языкам проекта (неизвестный язык → все суффиксы кода).

    ## @purpose  Языко-зависимый скан исходников (transition-traces-ban/agent-check):
    ##           python → .py; typescript/react → ts/tsx/js/jsx; sh → .sh. Fallback на
    ##           полный набор суффиксов при пустом/неизвестном наборе языков.
    ## @io       ⇥ project_dir, languages → ⎋ Iterator[Path]
    ## @complexity O(F)
    """
    suffixes: frozenset[str] = frozenset().union(*(_LANG_CODE_SUFFIXES.get(lang, frozenset()) for lang in languages))
    if not suffixes:
        suffixes = _ALL_CODE_SUFFIXES
    for path in iter_project_files(project_dir):
        if path.suffix.lower() in suffixes:
            yield path


# endregion HELPERS_iter_files


# region FUNC_missing_grep_summary
## @purpose  GREP_SUMMARY отсутствует в первых 10 строках файла → список rel-путей (канон gate).
## @io       ⇥ project_dir, paths → ⎋ list[str] rel-пути без GREP_SUMMARY
## @complexity O(F * H) — файлы × первые 10 строк
def missing_grep_summary(project_dir: Path, paths: list[Path]) -> list[str]:
    """GREP_SUMMARY missing in first 10 lines of file → rel-paths (canon gate)."""
    missing: list[str] = []
    for path in paths:
        try:
            with Path(path).open(encoding="utf-8", errors="ignore") as f:
                head = "".join(f.readline() for _ in range(10))
        except OSError:
            continue
        if "GREP_SUMMARY" not in head:
            missing.append(str(path.relative_to(project_dir)))
    return missing


# endregion FUNC_missing_grep_summary


# region FUNC_parse_structured
## @purpose  Валидация TOML/JSON-синтаксиса файла (hygiene invalid-syntax): json → json.loads,
##           иначе tomllib.loads. False при OSError/ValueError (битый файл).
## @io       ⇥ path: Path → ⎋ bool — синтаксис валиден
## @complexity O(S) — размер файла
def _plw_body__parse_structured(path: Path):
    """Body of try-block (PLW0717 extraction из parse_structured) — except-семантика не меняется."""
    if path.suffix == ".json":
        import json

        json.loads(path.read_text(encoding="utf-8"))
    else:
        import tomllib

        tomllib.loads(path.read_text(encoding="utf-8"))


def parse_structured(path: Path) -> bool:
    """Validate TOML/JSON syntax of a file."""
    try:
        _plw_body__parse_structured(path)
    except (OSError, ValueError):
        return False
    else:
        return True


# endregion FUNC_parse_structured
