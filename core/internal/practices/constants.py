# GREP_SUMMARY: practices-constants, EXCLUDED_DIRS, CODE_EXTENSIONS, canon, shared-constants, excluded-dirs, code-extensions
# STRUCTURE: ┌единый канон констант практик┐ → ⊕ EXCLUDED_DIRS (12 каталогов-кэшей/артефактов) → ⊕ CODE_EXTENSIONS (6 код-суффиксов) → ⎋ импортируются maturity + check_project-пакет
# region MODULE_CONTRACT
## @purpose  Единый канон общих констант практик (DevPlan 170 W10-A): каталоги-исключения
##           (node_modules/.venv/dist/build/coverage/.next/__pycache__/.git/.pytest_cache/
##           .ruff_cache/.mypy_cache) и расширения файлов кода (py/ts/tsx/js/jsx/sh).
##           Устраняет дубликат _EXCLUDED_DIRS/_CODE_EXTENSIONS: ранее существовали в двух
##           местах — check_project.py (11 каталогов) и maturity.py (12 каталогов, с
##           .mypy_cache) — расхождение вело к дрейфу охвата сканов (research-A §2).
## @scope    Потребители: maturity.py (подсчёт файлов кода), check_project-пакет (files.py:
##           walk-итераторы + языко-зависимый скан). Публичные имена — импорт из соседних
##           модулей без SLF-нарушений (private-доступ запрещён, ruff select SLF).
## @invariants
##   - EXCLUDED_DIRS — НАДМНОЖЕСТВО прежних двух наборов (12 каталогов, включая .mypy_cache)
##   - CODE_EXTENSIONS — код-суффиксы (НЕ текстовые): текстовый набор hygiene остаётся
##     локальным в check_project/files.py (другая семантика — .md/.toml/.json и т.п.)
##   - Data-only модуль: 0 импортов, 0 логики (безопасен для AST/манифест-гейтов)
## @rationale Q: Почему не импорт из maturity (research-A §2 вариант «импорт из maturity»)?
##            A: maturity._EXCLUDED_DIRS/_CODE_EXTENSIONS — приватные; ruff SLF001 (select SLF)
##            блокирует приватный доступ. Отдельный data-only модуль с public именами —
##            единственный «чистый» канал консолидации без per-file-игноров.
# 🧐 TRAP[DECISION] · 2026-08-15 · — · Консолидация _EXCLUDED_DIRS/_CODE_EXTENSIONS через
# · data-only practices/constants.py · Rejected: импорт приватных констант из maturity.py
# · (ruff SLF001 + ns-гейт private-imports) · Reason: единственный «чистый» канал без
# · per-file-игноров; +.mypy_cache к исключениям check_project (было 11, стало 12) — кэш
# · типизации исключается из сканов (семантически корректно) · Rev: консолидация maturity
# · в пакет → перенос канона в тот пакет
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (консолидация констант, research-A §2)
# endregion MODULE_CONTRACT

# ── Директории, исключаемые из файловых проверок/подсчёта (библиотеки/кэши/артефакты) ──
EXCLUDED_DIRS: frozenset[str] = frozenset({
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "coverage",
    ".next",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
})

# ── Расширения файлов кода (НЕ библиотек) — py/ts/tsx/js/jsx/sh ──
CODE_EXTENSIONS: frozenset[str] = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".sh"})
