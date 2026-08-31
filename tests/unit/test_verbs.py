# GREP_SUMMARY: test verbs canonical-verbs verb-dictionary is_verb validate_not_verb-removed R5
# STRUCTURE: ▶ test_is_verb_positive (6 canonical) → ▶ test_is_verb_negative (not-verb) → ▶ test_validate_not_verb_removed_negative (ImportError)
# region MODULE_CONTRACT
"""
@purpose  Unit tests for core/internal/shared/verbs.py — canonical forced-command verb
          dictionary (DevPlan 116 B1 T1, U-56). Covers is_verb predicate and R5
          negative-тест на удалённую validate_not_verb (DevPlan 119 C3, AUDIT-3 A2).
@scope    tests/unit/ — pure Python, 0 Docker, 0 I/O.
@invariants
  - is_verb: exact-match, case-sensitive, None/не-str → False (никогда не raise)
  - validate_not_verb УДАЛЁН (0 внешних вызовов) — импорт → ImportError (R5)
@rationale  DevPlan 119 C3 $TEST_SPEC: test_validate_not_verb_removed_negative —
            anti-survivorship: если функция вернётся, тест упадёт.
@changes 2026-08-02 | Created per DevPlan 119 C3
"""
# endregion MODULE_CONTRACT

import logging

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

from core.internal.shared import verbs

pytestmark = pytest.mark.static_audit

_CANONICAL = ("ping", "exit", "status", "health", "verify", "remove", "receive", "rollback")


@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · is_verb exact-match канона (U-56)
# · Scenario: все 8 CANONICAL_VERBS → True; не-verb и case-варианты → False
# · Last fail: N/A (preventive)
# · Remove if: verb-словарь диспетчера заменён другой моделью
def test_is_verb_positive_and_negative(caplog) -> None:
    """is_verb: 8 канонических verb'ов → True; не-verb/case/None → False."""
    for verb in _CANONICAL:
        assert verbs.is_verb(verb) is True, f"is_verb({verb!r}) must be True"
    logger.critical("[IMP:9][verbs][is_verb] 8 canonical verbs → True")

    for name in ("deploy", "Status", "RECEIVE", "Rollback", "", None, 42):
        assert verbs.is_verb(name) is False, f"is_verb({name!r}) must be False (exact-match)"
    logger.critical("[IMP:9][verbs][is_verb] не-verb/case/None → False (exact-match, D2)")


@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-12 · MERGED (W2 T2.1) · CANONICAL_VERBS закрытое множество (D1)
# · Scenario: ровно 8 verb'ов; platform-deliver/platform-deploy отсутствуют; VERB_RESERVE == set
# · Last fail: N/A (перенесено из test_shared_verbs.py — полнота словаря)
# · Remove if: verb-множество расширяется архитектурным решением
def test_canonical_verbs_set(caplog) -> None:
    """CANONICAL_VERBS — закрытое множество из 8 verb'ов; VERB_RESERVE совпадает (D1)."""
    assert verbs.CANONICAL_VERBS == _CANONICAL
    assert "platform-deliver" not in verbs.CANONICAL_VERBS
    assert "platform-deploy" not in verbs.CANONICAL_VERBS
    assert frozenset(verbs.CANONICAL_VERBS) == verbs.VERB_RESERVE
    logger.critical("[IMP:9][verbs][canonical] CANONICAL_VERBS=%s, VERB_RESERVE совпадает", verbs.CANONICAL_VERBS)


@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · validate_not_verb удалена (C3)
# · Scenario: DevPlan 119 C3 — 0 внешних вызовов validate_not_verb (AUDIT-3 A2);
#   from-импорт удалённой функции должен падать ImportError (anti-survivorship)
# · Last fail: до C3 — validate_not_verb определялась в verbs.py:69 без потребителей
# · Remove if: validate_not_verb намеренно возвращена с реальными вызовами
def test_validate_not_verb_removed_negative(caplog) -> None:
    """R5 negative (C3): from-импорт удалённой validate_not_verb → ImportError."""
    with pytest.raises(ImportError):
        # exec + from-import — точная семантика `from core.internal.shared.verbs import
        # validate_not_verb`: отсутствующий атрибут → ImportError (R5: функция не вернулась).
        # Статический import запрещён ruff (F401) — exec эмулирует from-import механику.
        exec("from core.internal.shared.verbs import validate_not_verb")  # ruff: ignore[S102] — R5: exec эмулирует from-import механику (статический import запрещён F401)

    assert not hasattr(verbs, "validate_not_verb"), "validate_not_verb must not exist on verbs module"
    logger.critical("[IMP:9][verbs][removed] validate_not_verb отсутствует → ImportError (R5 PASS)")
