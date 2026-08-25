"""Exception import-path detector — единый import-путь иерархии PlatformError (REF-0107).

# GREP_SUMMARY: exception-imports platform-fatal-error single-import-path dual-class bare-shim ast REF-0107
# STRUCTURE: ▶ ast-скан core/**/*.py ImportFrom → ◇ имя ∈ {PlatformError, PlatformFatalError, Config*Error}
#            ∧ module ≠ core.internal.shared.exceptions → ⊕ Findings → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор единого import-пути исключений платформы (REF-0107 problem 7):
##           PlatformError/PlatformFatalError/Config*Error импортируются ТОЛЬКО из
##           core.internal.shared.exceptions. Импорт из shim-имени («from exceptions import …»,
##           относительный импорт) создаёт ВТОРОЙ класс с тем же именем — Python кэширует
##           модули по имени, pytest.raises(PlatformFatalError) не ловит raise второго класса,
##           except глотает мимо (TRAP[BUG] decrypt_secrets.py 2026-08-01).
## @scope    ast-скан core/**/*.py (кроме __pycache__). tests/ НЕ сканируется.
## @invariants
##   - Нарушение = ЛЮБОЙ ImportFrom имени семейства из модуля, чей путь НЕ заканчивается
##     на core.internal.shared.exceptions (bare «exceptions», относительные импорты — RED)
##   - Allowlist пуст (прецедент private_imports B8 D3)
## @rationale Dual-class loading — тихий отказ error-handling: код выглядит корректным,
##            но ловит чужой класс. Единственная профилактика — структурный запрет
##            альтернативных путей импорта.
## @changes 2026-08-25 | REF-0107 (DevPlan 11 Волна 3) — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import ast
import logging
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

_CANONICAL_MODULE_SUFFIX = "core.internal.shared.exceptions"
# Семейство ошибок платформы (shared.exceptions): базовые + конфигурационные + lock/runtime.
_PROTECTED_NAMES: frozenset[str] = frozenset({
    "PlatformError",
    "PlatformFatalError",
    "ConfigNotFoundError",
    "ConfigParseError",
    "ConfigValidationError",
})


# region FUNC_collect_violations
def _collect_violations(path: Path) -> list[tuple[int, str]]:
    """Собрать (lineno, описание) импортов платформенных исключений мимо канонического пути.

    ## @purpose  ast-анализ одного .py файла: ImportFrom защищённых имён из не-канонического модуля.
    ## @io       ⇥ path: Path → ⎋ list[tuple[int, str]]
    ## @complexity O(N) — AST-узлы
    ## @invariants  Синтаксическая ошибка → пустой результат (парсер слоя покрывает SyntaxError)
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if module == _CANONICAL_MODULE_SUFFIX:
            # Канонический абсолютный путь — единственный разрешённый источник
            continue
        hit = sorted({a.name for a in node.names if a.name in _PROTECTED_NAMES})
        if hit:
            violations.append((
                node.lineno,
                (
                    f"import {', '.join(hit)} из '{module or '<relative>'}' — канонический путь: "
                    f"from {_CANONICAL_MODULE_SUFFIX} import … (dual-class loading)"
                ),
            ))
    return violations


# endregion FUNC_collect_violations


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти неканонические импорты платформенных исключений в core/.

    # ▶ ┌core/**/*.py┐ → ○ ast-скан ImportFrom → ◇ защищённое имя ∧ не canonical → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry) — правило REF-0107 (single import path).
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * N) — файлы × AST-узлы
    ## @invariants  Allowlist пуст; changed-фильтр как в остальных детекторах
    """
    core_dir = root / "core"
    scan_root = core_dir if core_dir.is_dir() else root
    files = sorted(p for p in scan_root.rglob("*.py") if "__pycache__" not in p.parts and p.is_file())
    findings: list[Finding] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if changed is not None and rel not in changed:
            continue
        for lineno, message in _collect_violations(path):
            findings.append(
                Finding(
                    rule="exception-import-path",
                    file=rel,
                    line=lineno,
                    message="non-canonical exception import: " + message,
                )
            )
            logger.warning("[IMP:9][exception_imports][RED] %s:%d %s", rel, lineno, message)
    logger.info("[IMP:9][exception_imports] Scanned %d file(s), findings=%d", len(files), len(findings))
    if not findings:
        logger.info("[IMP:9][exception_imports] PASS: все импорты исключений — через shared.exceptions")
    return findings


# endregion FUNC_detect
