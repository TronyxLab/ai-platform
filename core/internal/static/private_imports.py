"""Private-imports detector — ban on private cross-module imports in core/ (DevPlan 163 W-C).

# GREP_SUMMARY: static private-imports underscore-import private-api ast import-map allowlist-empty SRP U-07
# STRUCTURE: ▶ ast-скан core/**/*.py → ⊕ import map (Import/ImportFrom) → ◇ (a) from X import _name?
#            violation | ◇ (b) X._attr (X из map)? violation → ∖ stdlib + `import X as _alias` → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор приватных межмодульных импортов (DevPlan 163 W-C C3; порт
##           tests/gates/test_gate_no_private_cross_module_imports.py, DevPlan 116 B9 T6.1,
##           U-07): приватные имена (_-префикс) НЕ используются между модулями core/.
##           Публичные API — через публичные имена + экспорт через __init__.py.
##           Allowlist ПУСТ (прецедент B8 D3 — строгий гейт).
## @scope    ast-скан всех core/**/*.py (кроме __pycache__). tests/ НЕ сканируется
##           (white-box unit-тесты легитимно вызывают приватные внутримодульные функции).
## @invariants
##   - (a) `from X import _name` (имя с _-префиксом, БЕЗ alias) → RED
##   - (b) Attribute-доступ `X._attr(...)`/`X._attr`, где X — модуль, импортированный
##         в этом файле (карта имя→модуль из Import/ImportFrom) → RED
##   - Исключения: stdlib (sys.stdlib_module_names); `from X import name as _alias` /
##     `import X as _alias` (импортируется ПУБЛИЧНАЯ сущность, приватный только алиас)
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale SRP-декомпозиция (B9) легализует межмодульные контракты через публичные
##            имена; детектор фиксирует границу: приватные имена остаются внутри
##            home-модуля (быстрый слой, без pytest-гейта).
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created (порт B9 T6.1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)


# region FUNC_collect_violations
def _collect_violations(path: Path) -> list[tuple[int, str]]:
    """Собрать (lineno, описание) приватных импортов в одном .py файле.

    ## @purpose  ast-анализ: (a) `from X import _name`, (b) `X._attr` на импортированный
    ##           модуль. Возвращает пары (lineno, message) для построения Finding.
    ## @io       ⇥ path: Path → ⎋ list[tuple[int, str]]
    ## @complexity O(N) — AST-узлы
    ## @invariants  Синтаксическая ошибка → пустой результат (не RED — парсер слоя уже
    ##              покрывает SyntaxError; тихий пропуск согласован с гейтом-поверхностным fail)
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []

    module_map: dict[str, str] = {}
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    module_map[alias.asname] = alias.name
                else:
                    module_map[alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                full = f"{node.module}.{alias.name}" if node.module else alias.name
                if alias.name.startswith("_") and not alias.asname:
                    violations.append((node.lineno, f"from-import приватного имени {alias.name!r}"))
                if alias.asname:
                    module_map[alias.asname] = full
                else:
                    module_map[alias.name] = full
        elif isinstance(node, ast.Attribute):
            if not node.attr.startswith("_"):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            mod_full = module_map.get(node.value.id)
            if mod_full is None:
                continue
            top_level = mod_full.split(".")[0]
            if top_level in getattr(sys, "stdlib_module_names", set()):
                continue
            violations.append((
                node.lineno,
                f"attribute-доступ {node.value.id}.{node.attr} на импортированный модуль {mod_full}",
            ))
    return violations


# endregion FUNC_collect_violations


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти приватные межмодульные импорты в core/.

    # ▶ ┌core/**/*.py┐ → ○ ast-скан (import map) → ◇ from _name / X._attr → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry) — правило B9 T6.1 (U-07).
    ##           Для probe-деревьев (без core/) — рекурсивный скан всех .py.
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * N) — файлы × AST-узлы
    ## @invariants  Allowlist пуст (B8 D3 — строгий гейт)
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
                    rule="private-imports",
                    file=rel,
                    line=lineno,
                    message="private cross-module import: " + message,
                )
            )
            logger.warning("[IMP:9][private_imports][RED] %s:%d %s", rel, lineno, message)
    logger.info("[IMP:9][private_imports] Scanned %d file(s), findings=%d", len(files), len(findings))
    if not findings:
        logger.info("[IMP:9][private_imports] PASS: 0 private cross-module imports in core/")
    return findings


# endregion FUNC_detect
