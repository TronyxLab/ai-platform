"""
# GREP_SUMMARY: test_generate_help, scenarios, registry, visibility, internal-verbs, r5-negative, two-role-help
# STRUCTURE: ▶ load_verb_map (default-public/internal) → ▶ render_scenarios (public-only/skip-internal) → ▶ R5-negative (internal-in-scenarios → excluded) → ▶ render_registry (sorted/internal-marked) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/generate_help.py (План 175 W1.3) —
##           two-role help: scenarios (public only) + registry (all, internal-marked).
## @scope    load_verb_map, render_scenarios, render_registry — native pytest, no subprocess.
## @invariants
##   - Все тесты импортируют модуль напрямую (sys.path.insert на core/internal/scripts)
##   - Каждый тест декорирован @ldd_trajectory + IMP:9 лог
##   - R5-negative: internal-глагол в scenarios НЕ попадает в public-вывод (детектор честен)
## @rationale Двухролевой help — новый генератор; unit-тест + R5-negative (канон W1.3).
## @changes  2026-08-16 | Created (План 175 W1)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test (standalone-скрипт, как generate_entrypoint_manifest) ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import generate_help as gh

pytestmark = pytest.mark.static_audit


def _synthetic_manifest() -> dict:
    """Минимальный манифест: 2 public + 1 internal глагол + scenarios-секция."""
    return {
        "deploy": [
            {
                "make_target": "deploy",
                "visibility": "public",
                "operation_ru": "Деплой проекта",
                "signature": "make deploy PROJECT=<dir>",
            },
            {
                "make_target": "context-promote",
                "visibility": "public",
                "operation_ru": "Промоут платформы в контекст",
                "signature": "make context-promote CONTEXT=<ctx>",
            },
        ],
        "dev": [
            {
                "make_target": "generate-secrets-manifest",
                "visibility": "internal",
                "operation_ru": "Генерация secrets-manifest.yaml",
                "signature": "make generate-secrets-manifest",
            },
        ],
        "scenarios": {
            "stack": ["deploy"],
            "generate": ["generate-secrets-manifest"],  # internal в scenarios — контракт R5
        },
    }


# 🧪 TRAP[TEST] · Regression · load_verb_map строит verb→entry с visibility (default public)
# · Scenario: entry без visibility → public; internal → internal; operation_ru fallback description
# · Last fail: N/A (new test)
# · Remove if: load_verb_map семантика изменена
@ldd_trajectory
def test_load_verb_map_visibility(caplog) -> None:
    """load_verb_map: visibility по умолчанию public, явный internal сохраняется."""
    manifest = {
        "a": [{"make_target": "no-vis", "operation_ru": "без поля"}],
        "b": [{"make_target": "int-vis", "visibility": "internal", "operation_ru": "внутренний"}],
    }
    verb_map = gh.load_verb_map(manifest)

    assert verb_map["no-vis"]["visibility"] == "public", "default visibility должен быть public"
    assert verb_map["int-vis"]["visibility"] == "internal", "явный internal должен сохраниться"
    assert verb_map["no-vis"]["operation_ru"] == "без поля"
    logger.critical(
        "[IMP:9][test] load_verb_map visibility: default=%s explicit=%s — OK",
        verb_map["no-vis"]["visibility"],
        verb_map["int-vis"]["visibility"],
    )


# 🧪 TRAP[TEST] · Regression · render_scenarios выводит только public-глаголы
# · Scenario: scenarios со списком глаголов → public печатается, internal пропускается
# · Last fail: N/A (new test)
# · Remove if: render_scenarios семантика изменена
@ldd_trajectory
def test_render_scenarios_public_only(caplog) -> None:
    """render_scenarios: public-глагол в выводе, internal отсутствует."""
    out = gh.render_scenarios(_synthetic_manifest())

    assert "make deploy" in out, "public-глагол должен быть в scenarios-выводе"
    assert "generate-secrets-manifest" not in out, "internal-глагол НЕ должен попасть в public-вывод"
    logger.critical("[IMP:9][test] render_scenarios: public присутствует, internal исключён — OK")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · render_scenarios — internal-verb-leak (План 175 W1.3)
# · Last fail: internal-глагол (generate-secrets-manifest) в scenarios → попал в public-вывод
# · Remove if: render_scenarios перестаёт фильтровать по visibility
@ldd_trajectory
def test_render_scenarios_negative_internal_not_leaked(caplog) -> None:
    """R5 negative: internal-глагол, инжектированный в scenarios, НЕ протекает в public-вывод."""
    manifest = _synthetic_manifest()
    # Точный вход бага: internal-имя в scenarios-списке 'generate'
    assert "generate-secrets-manifest" in manifest["scenarios"]["generate"], "precondition: internal в scenarios"

    out = gh.render_scenarios(manifest)

    assert "generate-secrets-manifest" not in out, (
        "R5 FAIL: internal-глагол протёк в public-вывод scenarios (детектор не отфильтровал)"
    )
    logger.critical("[IMP:9][test] R5-negative: internal-глагол не протёк в scenarios-вывод — OK")


# 🧪 TRAP[TEST] · Regression · render_registry выводит все глаголы с internal-пометкой
# · Scenario: public + internal → оба в реестре, internal помечен, сортировка по имени
# · Last fail: N/A (new test)
# · Remove if: render_registry семантика изменена
@ldd_trajectory
def test_render_registry_all_marked(caplog) -> None:
    """render_registry: все глаголы, internal помечен [internal], сортировка по имени."""
    out = gh.render_registry(_synthetic_manifest())

    assert "make deploy" in out, "public-глагол должен быть в реестре"
    assert "make generate-secrets-manifest" in out, "internal-глагол должен быть в реестре"
    assert "[internal]" in out, "internal-глагол должен иметь пометку [internal]"
    assert "[public]" in out, "public-глагол должен иметь пометку [public]"
    # Сортировка: context-promote < deploy < generate-secrets-manifest
    assert out.index("context-promote") < out.index("deploy") < out.index("generate-secrets-manifest"), (
        "реестр должен быть отсортирован по имени глагола"
    )
    logger.critical("[IMP:9][test] render_registry: все глаголы + пометки + сортировка — OK")


# 🧪 TRAP[TEST] · Regression · render_scenarios без scenarios-секции → placeholder
# · Scenario: манифест без scenarios → '(no scenarios defined)'
# · Last fail: N/A (new test)
# · Remove if: render_scenarios fallback изменён
@ldd_trajectory
def test_render_scenarios_empty_manifest(caplog) -> None:
    """render_scenarios: пустой манифест → '(no scenarios defined)' без ошибки."""
    out = gh.render_scenarios({"deploy": [{"make_target": "deploy", "operation_ru": "x"}]})

    assert "(no scenarios defined)" in out, "пустой scenarios → placeholder"
    logger.critical("[IMP:9][test] render_scenarios empty manifest → placeholder — OK")
