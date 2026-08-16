# GREP_SUMMARY: unit-test, age-key, removed, R5, negative, ModuleNotFoundError, D3, shared-cleanup
# STRUCTURE: ▶ importlib.util.find_spec("core.internal.shared.age_key") → ◇ exists? → ⊕ assert None (удалён) → ⎋ pass
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship negative-тест (DevPlan 118 D3): модуль age_key.py УДАЛЁН.
##           Детекция AGE-ключа делегирована в канонический node_detect.py (DevPlan 104);
##           compat-шим age_key.py больше не существует — импорт обязан падать.
## @scope    Один тест на удалённый API. Не дублирует test_node_detect.py (там живут
##           позитивные сценарии detect_age_key).
## @invariants
##   - `core.internal.shared.age_key` НЕ существует на диске (importlib.util.find_spec → None)
##   - `from age_key import detect_age_key` (bare-импорт) бросает ModuleNotFoundError
##   - LDD: IMP:9 лог в успешном сценарии (подтверждение удаления)
## @rationale R5 (Test Honesty): каждое удаление API покрывается negative-тестом на удалённый API.
##            Без этого теста будущий агент мог бы молча вернуть age_key.py (регрессия D3).
## @changes  2026-08-02 | DevPlan 118 D3 — переписан: тесты detect_age_key (6 шт) удалены,
##                      покрытие переехало в test_node_detect.py; заменён R5 negative-тестом.
# endregion MODULE_CONTRACT

import importlib.util
import logging

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)


# region FUNC_test_age_key_module_removed
## @purpose — R5 negative: age_key.py удалён в DevPlan 118 D3 — модуль не существует.
## @io — ⇥ None → ⎋ None (asserts find_spec None + ModuleNotFoundError)
## @complexity — O(1)
@ldd_trajectory

# GUARD-PRESERVE (168): R5-negative (anti-survivorship) — age_key.py удалён (DevPlan 118 D3), импорт обязан падать ModuleNotFoundError; единственное покрытие удалённого API
# 🧪 TRAP[TEST] · 2026-08-02 · R5 NEGATIVE · age_key.py removed (DevPlan 118 D3)
# · Last fail: age_key.py существовал как compat-шим (DevPlan 104) — detect_age_key
# ·   реэкспортировался из node_detect; decrypt_secrets.py импортировал через sys.path-хак.
# · Remove if: age_key.py будет возвращён (регрессия D3) — тогда тест должен стать
# ·   позитивным снова и обновиться под новую реализацию.
def test_age_key_module_removed(caplog: pytest.LogCaptureFixture) -> None:
    """age_key.py удалён (D3) — импорт обязан падать с ModuleNotFoundError."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_age_key] Verifying age_key.py removed (DevPlan 118 D3)")

    # ── 1. Пакетный spec отсутствует (файл удалён) ──
    spec = importlib.util.find_spec("core.internal.shared.age_key")
    logger.info("[IMP:8][test_age_key] find_spec(core.internal.shared.age_key) = %s", spec)
    assert spec is None, "D3 regression: core.internal.shared.age_key существует — compat-шим не удалён"

    # ── 2. Bare-импорт (sys.path-хак) тоже падает ──
    with pytest.raises(ModuleNotFoundError):
        import age_key  # ruff: ignore[F401] — R5: модуль удалён, импорт обязан падать

    logger.info("[IMP:9][test_age_key] PASS: age_key.py removed — ModuleNotFoundError confirmed")


# endregion FUNC_test_age_key_module_removed
