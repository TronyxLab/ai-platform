# GREP_SUMMARY: gate cross-layer-linter import-layer-isolation modules-internal import-linter arch-imports dotted-python3m R5-negative deploy-bootstrap entrypoints-modules postgres-hook-allowlist shell-source
# STRUCTURE: ▶ import-linter (.importlinter, 7 контрактов) + slim lint_core() (shell) → ◇ violations==0 → ⊕ R5-negative (dotted py, python3 -m sh, deploy→bootstrap, entrypoints→modules) → ⎋ PASS/FAIL
# region MODULE_CONTRACT
## @purpose  CI gate #8: cross-layer изоляция (core/AGENTS.md) — Python-dotted-импорты → import-linter
##           (.importlinter, DevPlan 163 W-D D1), shell-source → кастомный slim-линтер (<200 LOC, D2).
## @scope    Два механизма: (1) import-linter 2.13 — декларативные контракты layers/forbidden/
##           independence/acyclic (Python-импорты); (2) tests.helpers.cross_layer_linter.lint_core —
##           shell-паттерны (source/. /bash/python3 -m в .sh) + Gate #8 v2 + Makefile contract.
##           R5-негативы (U-09, W5/G3) сохранены через виртуальный граф grimp (без записи в дерево).
## @invariants
##   - import-linter: 7 контрактов зелёные (layers-core, forbidden-*, independence, acyclic)
##   - slim lint_core(): 0 shell-source нарушений
##   - R5-negative: dotted py в modules RED; python3 -m в modules sh RED; deploy→bootstrap RED;
##     entrypoints→modules RED; postgres-hook allowlist → PASS (D1)
##   - Parity со старым линтером (881 LOC): files/importlinter_parity.md (§4.3 прямого замещения)
## @rationale  Декларативные контракты детерминированнее grep-гейта (AST-граф grimp); shell-файлы
##             grimp не видит — остаётся тонкий кастомный линтер (DevPlan 163 M5/W-D).
## @changes   2026-08-13 | DevPlan 163 W-D D2 — переписан: import-linter + slim вместо 881 LOC
# endregion MODULE_CONTRACT

import copy
import logging
import shutil
import uuid
from pathlib import Path

# ⚠️ TRAP[DECISION] · 2026-08-13 · MED · grimp/importlinter — LAZY-импорты (DevPlan 163 W-G интеграция)
# · Rejected: module-level import (check-manifests G3 собирает гейты СИСТЕМНЫМ python3, где
# ·   grimp/importlinter отсутствуют → collection-error → генератор падает в filesystem-fallback
# ·   → 120 file-based gate-записей → DIVERGE манифеста, DevPlan 163 фаза 2)
# · Reason: ленивый импорт внутри функций сохраняет runtime-гарантию (venv-прогон pytest),
# ·   а collection-only прогоны (генератор манифеста, system python3) не требуют importlinter.
# · Rev: если collection-окружение получит importlinter/grimp — вернуть module-level импорты.
import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.cross_layer_linter import CORE_DIR, lint_core

logger = logging.getLogger(__name__)

IL_CONFIG = Path(__file__).resolve().parents[2] / ".importlinter"


def _il_contract_fails(importer: str, imported: str, line_contents: str, contract_id: str) -> bool:
    """True если контракт ловит искусственный импорт (R5-negative на виртуальном графе).

    ## @purpose — R5-негатив через grimp-граф: добавляем запрещённый импорт в копию
    ##            реального графа и проверяем, что контракт его ловит. Без записи
    ##            probe-файлов в дерево (xdist-безопасно, R5-конвенция _gate_probe_).
    ## @io — importer, imported, line_contents (точный R5-вход), contract_id → ⎋ bool
    """
    import grimp
    import importlinter.api  # ruff: ignore[F401] — side-effect: регистрирует USER_OPTION_READERS в реестре
    from importlinter.application.use_cases import _register_contract_types, read_user_options
    from importlinter.domain.contract import registry

    user_options = read_user_options(config_filename=str(IL_CONFIG))
    _register_contract_types(user_options)
    opts = next(c for c in user_options.contracts_options if c["id"] == contract_id)
    contract = registry.get_contract_class(opts["type"])(
        name=opts["name"], session_options=user_options.session_options, contract_options=opts
    )
    graph = grimp.build_graph("core", include_external_packages=False)
    graph.add_module(importer)
    graph.add_import(importer=importer, imported=imported, line_number=1, line_contents=line_contents)
    logger.info("[IMP:8][il][r5] contract '%s' на импорте %s → %s", contract_id, importer, imported)
    return not contract.check(copy.deepcopy(graph), verbose=False).kept


# region TEST_GATE_CROSS_LAYER


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · cross-layer gate (import-linter + slim)
# · Last fail: N/A (новый механизм; старый ловил 4/4 корпусных входа)
# · Remove if: cross-layer гарантия заменена более сильным механизмом
def test_gate_cross_layer(caplog) -> None:
    """CI gate #8: import-linter (Python) + slim shell-линтер (0 нарушений).

    ## @purpose — Единый gate cross-layer изоляции: import-linter 7 контрактов +
    ##            shell-source slim-линтер. Python-dotted-импорты — import-linter;
    ##            source/. /bash/python3 -m в .sh — кастомный линтер.
    ## @io — ⎋ None (assert через pytest.fail на нарушениях)
    ## @complexity — O(n) файлы core/ (grimp AST ~0.05 s + shell-скан)
    """
    # region FUNC_il
    logger.info("[IMP:8][gate-cross-layer] Running import-linter (7 contracts, %s)", IL_CONFIG)
    import importlinter.api  # ruff: ignore[F401] — инициализирует settings (importlinter.configuration.configure())
    from importlinter.application.use_cases import lint_imports

    il_passed = lint_imports(config_filename=str(IL_CONFIG), cache_dir=None)
    if not il_passed:
        logger.info("[IMP:9][gate-cross-layer] FAIL — import-linter контракты нарушены (см. вывод)")
    # endregion FUNC_il

    # region FUNC_slim
    logger.info("[IMP:8][gate-cross-layer] Running slim shell-source linter")
    violations = lint_core()
    # endregion FUNC_slim

    print("\n" + "=" * 70)
    print("  GATE #8: CROSS-LAYER ISOLATION (import-linter + shell-source)")
    print("=" * 70)
    print(f"  import-linter: {'✅ 7/7 контрактов' if il_passed else '❌ нарушение контрактов'}")
    if violations:
        for v in violations:
            print(v)
    print("=" * 70 + "\n")

    assert il_passed, "import-linter FAILED: контракты .importlinter нарушены (см. вывод выше)"
    assert len(violations) == 0, f"Gate #8 FAILED: {len(violations)} shell-source violation(s):\n" + "\n".join(
        violations
    )
    logger.info("[IMP:9][gate-cross-layer] PASS — 0 нарушений (import-linter + shell-source)")


# endregion TEST_GATE_CROSS_LAYER


# region TEST_R5_NEGATIVE (anti-survivorship — DevPlan 116 B11 T1 U-09, W5/G3)


class TestCrossLayerNegativeR5:
    """R5-негативы: оригинальные входы, поймавшие баги, детектируются новым механизмом."""

    # 🧪 TRAP[TEST] · 2026-08-01 · NEGATIVE (R5) · cross-layer — U-09 dotted py
    # · Last fail: старый гейт — 36 passed при 4 реальных py-нарушениях (слепота к dotted)
    # · Remove if: import-linter forbidden-modules-internal заменён
    def test_dotted_py_in_modules_is_violation(self) -> None:
        """R5: `from core.internal.shared.telegram_notifier import ...` в modules → RED."""
        # region FUNC_test_dotted_py_in_modules_is_violation
        caught = _il_contract_fails(
            importer="core.modules._gate_probe_py_tmp.test_negative",
            imported="core.internal.shared.telegram_notifier",
            line_contents="from core.internal.shared.telegram_notifier import send_telegram",
            contract_id="forbidden-modules-internal",
        )
        assert caught, "R5 FAIL: dotted py import в modules должен быть RED (старый гейт был слеп)"
        logger.info("[IMP:9][test][r5] dotted py import в modules → RED (forbidden-modules-internal)")
        # endregion FUNC_test_dotted_py_in_modules_is_violation

    # 🧪 TRAP[TEST] · 2026-08-01 · NEGATIVE (R5) · cross-layer — U-09 python3 -m
    # · Last fail: старый гейт — слепота к python3 -m (disk-monitor/postgres-hook жили незамеченными)
    # · Remove if: slim-линтер python3 -m паттерн заменён
    def test_python3_m_in_modules_is_violation(self) -> None:
        """R5: `python3 -m core.internal.shared.node_yaml` в modules sh → RED (slim-линтер)."""
        # region FUNC_test_python3_m_in_modules_is_violation
        fixture_dir = CORE_DIR / "modules" / f"_gate_probe_sh_{uuid.uuid4().hex[:8]}_tmp"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        sh_file = fixture_dir / "test_negative.sh"
        try:
            sh_file.write_text(
                "#!/usr/bin/env bash\n"
                'db_name="$(python3 -m core.internal.shared.node_yaml \\\n'
                '    --file "${ai_yaml}" --get needs.database)"\n'
            )
            violations = lint_core()
            assert len(violations) >= 1, f"R5 FAIL: python3 -m в modules sh должен быть RED: {violations}"
            assert "[modules→internal]" in violations[0]
            logger.info("[IMP:9][test][r5] python3 -m в modules sh → RED: %s", violations[0])
        finally:
            shutil.rmtree(fixture_dir, ignore_errors=True)
        # endregion FUNC_test_python3_m_in_modules_is_violation

    # 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · cross-layer — deploy→bootstrap (W5/G3)
    # · Last fail: старый линтер НЕ ловил (слой-слепота internal→internal) — улучшение import-linter
    # · Remove if: forbidden-deploy-bootstrap заменён
    def test_deploy_to_bootstrap_detected(self) -> None:
        """R5: deploy → bootstrap импорт → RED (forbidden-deploy-bootstrap, AGENTS.md G3)."""
        # region FUNC_test_deploy_to_bootstrap_detected
        caught = _il_contract_fails(
            importer="core.internal.deploy._gate_probe_db_tmp.test_negative",
            imported="core.internal.bootstrap.preflight",
            line_contents="from core.internal.bootstrap.preflight import run_preflight",
            contract_id="forbidden-deploy-bootstrap",
        )
        assert caught, "R5 FAIL: deploy→bootstrap должен быть RED (AGENTS.md: инверсия запрещена)"
        logger.info("[IMP:9][test][r5] deploy→bootstrap → RED (forbidden-deploy-bootstrap)")
        # endregion FUNC_test_deploy_to_bootstrap_detected

    # 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · cross-layer — entrypoints→modules
    # · Last fail: старый линтер НЕ ловил dotted entrypoints→modules (resolve-баг) — улучшение
    # · Remove if: forbidden-entrypoints-modules заменён
    def test_entrypoints_to_modules_detected(self) -> None:
        """R5: entrypoints → modules импорт → RED (forbidden-entrypoints-modules)."""
        # region FUNC_test_entrypoints_to_modules_detected
        caught = _il_contract_fails(
            importer="core.entrypoints._gate_probe_ep_tmp.test_negative",
            imported="core.modules.status_page.app",
            line_contents="from core.modules.status_page.app import app",
            contract_id="forbidden-entrypoints-modules",
        )
        assert caught, "R5 FAIL: entrypoints→modules должен быть RED (AGENTS.md: entrypoints только internal/lib)"
        logger.info("[IMP:9][test][r5] entrypoints→modules → RED (forbidden-entrypoints-modules)")
        # endregion FUNC_test_entrypoints_to_modules_detected

    # 🧪 TRAP[TEST] · 2026-08-13 · POSITIVE (D1) · cross-layer — postgres-hook allowlist
    # · Scenario: контракт НЕ ловит легитимный modules→shared postgres-hook (D1 by design)
    # · Remove if: postgres-hook allowlist заменён
    def test_postgres_hook_allowlisted(self) -> None:
        """Позитив: postgres-hook modules→shared НЕ является нарушением (D1 by design)."""
        # region FUNC_test_postgres_hook_allowlisted
        caught = _il_contract_fails(
            importer="core.modules.postgres.hooks.on_project_deploy",
            imported="core.internal.shared.node_yaml",
            line_contents="from core.internal.shared.node_yaml import NodeYaml",
            contract_id="forbidden-modules-internal",
        )
        assert not caught, "R5 FAIL: postgres-hook должен быть allowlisted (D1 by design, ignore-imports)"
        logger.info("[IMP:9][test][r5] postgres-hook modules→shared → allowlisted (D1)")
        # endregion FUNC_test_postgres_hook_allowlisted


# endregion TEST_R5_NEGATIVE
