#!/usr/bin/env python3
# GREP_SUMMARY: cross-layer import linter, static-analysis, layer-isolation, direction-allowlist, dotted-imports, python3-m, R5-negative, enforcement
# STRUCTURE: ▶ lint_core() (implementation в tests/helpers/cross_layer_linter.py) → ◇ test_cross_layer_imports (enforcement) → ◇ direction-allowlist test → ◇ R5-negative (dotted py + python3 -m RED) → ⎋ assert 0 violations
# region MODULE_CONTRACT
## @purpose  Enforcement-гейт cross-layer импортной изоляции (core/AGENTS.md §Cross-layer import rules).
##           Реализация линтера извлечена в tests/helpers/cross_layer_linter.py (DevPlan 139 W3 T5):
##           1809 → ≤600 LOC, allowlist переведён с (file, lineno) на НАПРАВЛЕНИЯ (S7).
## @scope    Три уровня в этом файле:
##           1. test_cross_layer_imports — enforcement (0 нарушений)
##           2. direction-allowlist — unit-тест новой семантики (направление + scope-префикс)
##           3. R5-negative (B11, U-09) — dotted py import RED + python3 -m RED (сохранены)
##           Сканер-юнит-тесты (TestLooksLikePath/TestResolveImport/...) — в
##           tests/unit/test_cross_layer_helpers.py (импортируют helper).
## @invariants
##   - lint_core() re-экспортируется — tests/gates/test_gate_cross_layer.py импортирует её отсюда (без изменений)
##   - R5-negative фикстуры под core/modules/_b11_negative_*_tmp/ — вне scope-префикса allowlist → RED
##   - LINT-EXEMPT больше НЕ подавляет (TASK-6C) — warning
##   - НОВОЕ dotted-нарушение вне allowlist → RED (allowlist не растёт)
## @rationale Direction-based (S7): allowlist направлений (слои + scope), не пар модулей —
##            стабильнее (номера строк дрейфуют), проще поддерживать; R5-negative сохранены
##            (anti-survivorship: старый гейт был слеп к dotted/python3 -m паттернам).
## @changes  2026-08-05 | DevPlan 139 W3 T5 — переписан с 1809 LOC на ≤600 (реализация → helpers)
# endregion MODULE_CONTRACT

import logging
import shutil

from tests.conftest import ldd_trajectory
from tests.helpers.cross_layer_linter import (
    _RE_DOTTED_NAME,
    CORE_DIR,
    _is_direction_allowlisted,
    _looks_like_path,
    check_violation,
    lint_core,
    resolve_import,
    scan_py_file,
    scan_sh_file,
)

# lint_core re-export: tests/gates/test_gate_cross_layer.py импортирует отсюда
# (`from tests.test_cross_layer_imports import lint_core`) — сохраняем совместимость.
__all__ = ["lint_core"]

logger = logging.getLogger(__name__)


# region TEST_test_cross_layer_imports


@ldd_trajectory
def test_cross_layer_imports(caplog) -> None:
    """Enforce layer isolation: zero cross-layer import violations in core/.

    Failure means real violations exist. Fix them by moving the import to an
    allowed target layer, or add a documented direction-allowlist entry.
    """
    # region FUNC_test_cross_layer_imports
    ## @purpose  Enforce zero cross-layer import violations in core/ (реализация — helpers)
    ## @io       None → assertion
    ## @complexity O(n) where n = source files under core/

    violations = lint_core()

    print("\n" + "=" * 70)
    print("  CROSS-LAYER IMPORT LINTER REPORT")
    print("=" * 70)

    if not violations:
        print("  ✅ 0 violations — all imports respect layer isolation rules\n")
        logger.info("[IMP:9][lint][result] PASS — 0 cross-layer import violations")
    else:
        print(f"  ❌ {len(violations)} cross-layer import violation(s) found:\n")
        for v in violations:
            print(v)
        print("\n" + "-" * 70)
        print("  To fix: move imports to allowed layers or add a documented")
        print("  direction-allowlist entry (tests/helpers/cross_layer_linter.py).")
        logger.info("[IMP:9][lint][result] FAIL — %d violation(s)", len(violations))
        print("=" * 70 + "\n")

    assert len(violations) == 0, f"Cross-layer import violations found ({len(violations)}):\n" + "\n".join(violations)


# endregion FUNC_test_cross_layer_imports
# endregion TEST_test_cross_layer_imports


# region TEST_ALL_CALL_SITES_USE_INVOKE
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate #8 v2 integration — all call sites validated
# · Last fail: N/A (new test)
# · Remove if: Gate #8 is superseded
def test_all_call_sites_use_invoke() -> None:
    """Gate #8 v2: все call sites используют invoke_module_interface (0 typed contract violations)."""
    # region FUNC_test_all_call_sites_use_invoke
    violations = lint_core()

    gate8_violations = [v for v in violations if "[internal→modules·direct]" in v or "[internal→modules·invoke]" in v]

    assert len(gate8_violations) == 0, (
        f"Gate #8 v2 found {len(gate8_violations)} typed contract violation(s):\n" + "\n".join(gate8_violations)
    )
    logger.info("[IMP:9][gate8-v2][test] All call sites validated — 0 typed contract violations")
    # endregion FUNC_test_all_call_sites_use_invoke


# endregion TEST_ALL_CALL_SITES_USE_INVOKE


# region TEST_DIRECTION_ALLOWLIST (S7, DevPlan 139 W3 T5)
# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · direction-allowlist: направление + scope-префикс (S7)
# · Scenario: postgres-hook (modules→internal) под scope-префиксом → allowlisted (None);
# ·   фикстура вне scope (напр. modules/_b11_negative_py_tmp) → НЕ allowlisted (RED)
# · Last fail: N/A (новая семантика allowlist)
# · Remove if: cross-layer gate superseded
def test_direction_allowlist_scopes_postgres_hook() -> None:
    """Direction-allowlist: postgres-hook под scope → подавляется; вне scope → RED."""
    # region FUNC_test_direction_allowlist_scopes_postgres_hook
    hook = CORE_DIR / "modules" / "postgres" / "hooks" / "on_project_deploy.py"
    assert hook.is_file(), f"postgres hook not found: {hook}"

    # Постgres-hook: направление modules→internal + scope-префикс core/modules/postgres/hooks/
    allowlisted = _is_direction_allowlisted(hook, "modules", "internal")
    assert allowlisted, f"R5 FAIL: postgres hook должен быть allowlisted (D1 by design): {hook}"
    logger.info("[IMP:9][direction-allowlist] postgres-hook allowlisted (direction modules→internal, scope hooks/)")

    # Контроль: фикстура вне scope-префикса → НЕ allowlisted (RED сохраняется)
    out_of_scope = CORE_DIR / "modules" / "_b11_negative_py_tmp" / "test_negative.py"
    not_allowlisted = _is_direction_allowlisted(out_of_scope, "modules", "internal")
    assert not not_allowlisted, "R5 FAIL: фикстура вне scope НЕ должна быть allowlisted"
    logger.info("[IMP:9][direction-allowlist] фикстура вне scope → RED (allowlist не растёт)")
    # endregion FUNC_test_direction_allowlist_scopes_postgres_hook


# endregion TEST_DIRECTION_ALLOWLIST


# region TEST_B11_NEGATIVE (R5 anti-survivorship — DevPlan 116 B11 T1, U-09)
class TestB11DottedImportDetection:
    """R5 negative tests: dotted-imports and python3 -m are RED outside allowlist.

    ## @purpose — Доказывают, что гейт ловит dotted-нарушения (anti-survivorship:
    ##            старый гейт был слеп к этим паттернам). Фикстуры создаются ПОД
    ##            core/modules/ (слой modules — subject to rules) и удаляются в finally.
    """

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · dotted py import in modules → RED
    # · Scenario: `from core.internal.shared.telegram_notifier import ...` в modules-фикстуре
    # · Last fail: old gate — 36 passed при 4 реальных py-нарушениях (слепота к dotted)
    # · Remove if: cross-layer gate superseded
    def test_dotted_py_import_in_modules_is_violation(self) -> None:
        """R5 negative: dotted py-import из modules → violation (RED)."""
        # region FUNC_test_dotted_py_import_in_modules_is_violation
        # 2026-08-04 (DevPlan 129 W2): xdist-гонка устранена exclusions (_EXCLUDED_DIRS)
        # · Probe остаётся в РЕАЛЬНОМ core/modules/ НАМЕРЕННО: resolve_import/scan_py_file
        # · резолвят слой modules по реальному пути CORE_DIR — probe вне дерева не покрыл бы слой.
        fixture_dir = CORE_DIR / "modules" / "_b11_negative_py_tmp"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        py_file = fixture_dir / "test_negative.py"
        try:
            py_file.write_text(
                "#!/usr/bin/env python3\nfrom core.internal.shared.telegram_notifier import send_telegram\n"
            )
            imports = scan_py_file(py_file)
            assert len(imports) == 1, f"Expected 1 dotted import, got {imports}"
            lineno, imp_path, exempt = imports[0]
            assert _looks_like_path(imp_path), f"dotted name must look like path: {imp_path}"
            resolved = resolve_import(py_file, imp_path, "modules")
            assert resolved is not None, "dotted import must resolve to a core/ path"
            assert "core/internal/shared/telegram_notifier" in str(resolved)
            msg = check_violation(py_file, lineno, imp_path, "py", exempt, resolved)
            assert msg is not None, f"R5 FAIL: dotted import {imp_path} in modules must be RED (old gate was blind)"
            assert "[modules→internal]" in msg
            logger.info("[IMP:9][test][b11-negative] dotted py import RED: %s", msg)
        finally:
            shutil.rmtree(fixture_dir, ignore_errors=True)
        # endregion FUNC_test_dotted_py_import_in_modules_is_violation

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · python3 -m in modules sh → RED
    # · Scenario: `python3 -m core.internal.shared.node_yaml` в sh-фикстуре modules
    # · Last fail: old gate — слепота к python3 -m (disk-monitor/postgres-hook жили незамеченными)
    # · Remove if: cross-layer gate superseded
    def test_python3_m_in_modules_is_violation(self) -> None:
        """R5 negative: python3 -m core.internal.* из modules/sh → violation (RED)."""
        # region FUNC_test_python3_m_in_modules_is_violation
        # 2026-08-04 (DevPlan 129 W2): xdist-гонка устранена exclusions (_EXCLUDED_DIRS)
        fixture_dir = CORE_DIR / "modules" / "_b11_negative_sh_tmp"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        sh_file = fixture_dir / "test_negative.sh"
        try:
            sh_file.write_text(
                "#!/usr/bin/env bash\n"
                'db_name="$(python3 -m core.internal.shared.node_yaml \\\n'
                '    --file "${ai_yaml}" --get needs.database)"\n'
            )
            imports = scan_sh_file(sh_file, "modules")
            dotted = [imp for imp in imports if _RE_DOTTED_NAME.match(imp[1])]
            assert len(dotted) >= 1, f"Expected python3 -m dotted import, got {imports}"
            lineno, imp_path, exempt = dotted[0]
            resolved = resolve_import(sh_file, imp_path, "modules")
            assert resolved is not None, "python3 -m dotted module must resolve to a core/ path"
            msg = check_violation(sh_file, lineno, imp_path, "sh", exempt, resolved)
            assert msg is not None, f"R5 FAIL: python3 -m {imp_path} in modules must be RED (old gate was blind)"
            assert "[modules→internal]" in msg
            logger.info("[IMP:9][test][b11-negative] python3 -m RED: %s", msg)
        finally:
            shutil.rmtree(fixture_dir, ignore_errors=True)
        # endregion FUNC_test_python3_m_in_modules_is_violation


# endregion TEST_B11_NEGATIVE
