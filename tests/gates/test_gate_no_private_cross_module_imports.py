#!/usr/bin/env python3
# GREP_SUMMARY: gate no-private-cross-module-imports ast underscore-import private-api allowlist-empty srp
# STRUCTURE: ▶ ast-скан core/internal/**/*.py → ⊕ import map (Import/ImportFrom) → ◇ (a) from X import _name? violation │ ◇ (b) X._attr (X из map)? violation → ∖ stdlib + `import X as _alias` исключения → ⎋ 0 violations | RED
# region MODULE_CONTRACT
## @purpose  Gate test (DevPlan 116 B9 T6.1, U-07): запрет приватных межмодульных импортов в core/.
##           Публичные API — через публичные имена + экспорт через __init__.py; приватные функции
##           НЕ используются между модулями (allowlist ПУСТ — прецедент B8 D3 строгий гейт).
## @scope    ast-скан всех core/internal/**/*.py (кроме __pycache__). tests/ НЕ сканируется
##           (white-box unit-тесты легитимно вызывают приватные внутримодульные функции).
## @invariants
##   - (a) `from X import _name` (имя с _-префиксом, БЕЗ alias) → violation
##   - (b) Attribute-доступ `X._attr(...)`/`X._attr`, где X — модуль, импортированный в этом файле
##         (карта имя→модуль из Import/ImportFrom) → violation
##   - Исключения: stdlib (sys.stdlib_module_names); `from X import name as _alias` /
##     `import X as _alias` (импортируется ПУБЛИЧНАЯ сущность, приватный только алиас);
##     `from .pkg import _x` внутри пакета — НЕ исключается (гейт строгий)
##   - Allowlist: ПУСТ
## @rationale SRP-декомпозиция (B9) легализует межмодульные контракты через публичные имена.
##            Гейт фиксирует границу: приватные имена остаются внутри home-модуля.
## @changes  2026-08-01 · Created (B9 T6.1)
# endregion MODULE_CONTRACT

import ast
import logging
import pathlib
import sys

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

PLATFORM_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
CORE_DIR: pathlib.Path = PLATFORM_ROOT / "core"

# Allowlist: ПУСТ (B8 D3 — строгий гейт, прецедент dead-code phantom gate)
ALLOWLIST: list[str] = []


def _collect_violations(py_file: pathlib.Path) -> list[str]:
    """Collect private cross-module import violations in one Python file.

    ## @purpose  ast-анализ одного файла: (a) `from X import _name`, (b) `X._attr` на импортированный модуль.
    ## @io       ⇥ py_file → ⎋ list[str] violation-описаний
    ## @complexity O(N) по AST-узлам
    """
    src = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        # Файл с синтаксической ошибкой — поверхностный fail (не должен быть в core/)
        return [f"{py_file}:{exc.lineno}: syntax error — cannot audit"]

    # Карта имя→полное имя модуля из Import/ImportFrom (включая alias)
    module_map: dict[str, str] = {}
    violations: list[str] = []

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
                    # (a) импорт приватного имени БЕЗ публичного алиаса → violation
                    violations.append(f"{py_file}:{node.lineno}: from-import приватного имени {alias.name!r}")
                if alias.asname:
                    module_map[alias.asname] = full
                else:
                    module_map[alias.name] = full
        elif isinstance(node, ast.Attribute):
            # (b) X._attr — X из карты импортированных модулей
            if not node.attr.startswith("_"):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            mod_full = module_map.get(node.value.id)
            if mod_full is None:
                continue
            # Исключение: stdlib модули (например os._exit — легитимный stdlib приватный API)
            top_level = mod_full.split(".")[0]
            if top_level in getattr(sys, "stdlib_module_names", set()):
                continue
            violations.append(
                f"{py_file}:{node.lineno}: attribute-доступ {node.value.id}.{node.attr} "
                f"на импортированный модуль {mod_full}"
            )

    return violations


@ldd_trajectory
@pytest.mark.gate
# 🧪 TRAP[TEST] · Gate invariant · 0 приватных межмодульных импортов в core/ (SRP-граница, B9 T6.1)
# · Scenario: ast-скан всех core/**/*.py — from-import _name + X._attr на импортированный модуль
# · Last fail: N/A (new gate, B9)
# · Remove if: гейт приватных импортов заменён иным механизмом enforcement
def test_gate_no_private_cross_module_imports(caplog):
    """Gate: 0 приватных межмодульных импортов в core/ (allowlist пуст)."""
    caplog.set_level(logging.INFO)
    all_violations: list[str] = []

    py_files = sorted(p for p in CORE_DIR.rglob("*.py") if "__pycache__" not in p.parts)
    audited = 0
    for py_file in py_files:
        viols = _collect_violations(py_file)
        audited += 1
        all_violations.extend(viols)

    # Allowlist apply (пуст — но контракт гейта явный)
    remaining = [v for v in all_violations if not any(wl in v for wl in ALLOWLIST)]

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    logger.info("[IMP:9][gate][private-import] Audited %d core/**/*.py files", audited)
    for v in all_violations:
        logger.error("[IMP:9][gate][private-import] VIOLATION: %s", v)
    logger.info(
        "[IMP:9][gate][private-import] %d violation(s), allowlist=%d → %d remaining",
        len(all_violations),
        len(ALLOWLIST),
        len(remaining),
    )
    print("--- END LDD TRAJECTORY ---")

    assert not remaining, f"Private cross-module imports detected in core/ ({len(remaining)}):\n" + "\n".join(remaining)
