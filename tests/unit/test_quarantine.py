# GREP_SUMMARY: test-quarantine, quarantine, registry, skip, Rev-date, debt-ref, docker, network, deterministic-layer, W6, unit-test
# STRUCTURE: ▶ add nodeid+docker marker → skip reason [QUARANTINE] → ▶ Rev-date missing → validation RED → ▶ deterministic-layer nodeid → НЕ skipped
# region MODULE_CONTRACT
## @purpose  Unit tests для Quarantine-протокола (DevPlan 160 W6 T6.3) — tests/_conftest/quarantine.py:
##           (1) nodeid в реестре + docker/network маркер → pytest.skip с reason
##           «[QUARANTINE] nodeid — reason — Rev: until (Debt: debt_ref)»;
##           (2) запись БЕЗ Rev-даты (until) → ошибка валидации (RED);
##           (3) детерминированный слой (нет docker-маркера) с совпадающим nodeid → НЕ карантинится.
## @scope    Прямой вызов pytest_collection_modifyitems/validate_quarantine с stub-предметами
##           (без pytest-сессии). Реестр QUARANTINE сбрасывается в фикстуре (чистота по умолчанию).
## @invariants
##   - Skip применяется ТОЛЬКО к items с маркерами requires_docker/smoke/component/integration
##   - Reason содержит: [QUARANTINE], nodeid, reason, Rev: <until>, Debt: <debt_ref>
##   - until обязателен (YYY-MM-DD); отсутствие/невалидность → validate_quarantine ошибка + хук RuntimeError
##   - Пустой реестр = no-op (return 0)
## @rationale  Механизм карантина обязан быть проверяемым без docker-инфраструктуры: stub-предметы
##             изолируют логику хука (валидация + маркировка) от pytest-рантайма.
## @changes  2026-08-13 | DevPlan 160 W6 T6.3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from typing import Any

import pytest

from tests._conftest.quarantine import (
    QUARANTINE,
    pytest_collection_modifyitems,
    validate_quarantine,
)

logger = logging.getLogger(__name__)


class _StubItem:
    """Минимальный pytest.Item-совместимый stub для прямого вызова хука карантина.

    ## @purpose — Хук работает с item.get_closest_marker(name) и item.add_marker(marker).
    ##            Stub воспроизводит эти два метода без pytest-рантайма (unit-изоляция).
    ## @io — ⇥ nodeid: str, markers: dict[name → bool] → ⎋ _StubItem
    ## @complexity — O(1)
    """

    def __init__(self, nodeid: str, markers: dict[str, bool] | None = None) -> None:
        self.nodeid = nodeid
        self._markers = markers or {}
        self.skip_marker: Any = None

    def get_closest_marker(self, name: str) -> Any:
        """Возвращает truthy, если маркер присутствует (имитация pytest.Item)."""
        return object() if self._markers.get(name) else None

    def add_marker(self, marker: Any) -> None:
        """Запоминает добавленный маркер (skip) для ассертов."""
        self.skip_marker = marker


@pytest.fixture(autouse=True)
def _clean_quarantine() -> None:
    """Сбрасывает реестр карантина до пустого (инвариант «пуст по умолчанию»)."""
    QUARANTINE.clear()
    yield
    QUARANTINE.clear()


# region FUNC_test_quarantine_skip_applied
## @purpose — nodeid в реестре + requires_docker маркер → хук добавляет pytest.skip с полным reason.
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · Quarantine (W6 T6.3) — docker/network флак → skip
# · Scenario: QUARANTINE["tests/test_foo.py::test_flaky"] = {until, reason, debt_ref} →
# ·   item с requires_docker → skip-маркер с reason "[QUARANTINE] ... Rev: ... (Debt: ...)"
# · Last fail: N/A (new mechanism)
# · Remove if: Quarantine-протокол отменяется (детерминированные слои карантинятся)
def test_quarantine_skip_applied_with_reason(caplog: pytest.LogCaptureFixture) -> None:
    """docker/network item из реестра → pytest.skip с диагностическим reason."""
    caplog.set_level(logging.INFO)

    QUARANTINE["tests/test_foo.py::test_flaky"] = {
        "until": "2026-09-01",
        "reason": "flaky under background load (langfuse timeout)",
        "debt_ref": "160-test-architecture-revamp (T1.3)",
    }
    item = _StubItem("tests/test_foo.py::test_flaky", markers={"requires_docker": True})

    skipped = pytest_collection_modifyitems([item])

    assert skipped == 1, "docker/network item из реестра обязан быть карантинирован"
    assert item.skip_marker is not None, "skip-маркер обязан быть добавлен"
    assert item.skip_marker.mark.name == "skip", f"ожидался pytest.mark.skip, got {item.skip_marker.mark.name}"
    reason = item.skip_marker.kwargs["reason"]
    assert "[QUARANTINE]" in reason
    assert "tests/test_foo.py::test_flaky" in reason
    assert "flaky under background load" in reason
    assert "Rev: 2026-09-01" in reason
    assert "Debt: 160-test-architecture-revamp (T1.3)" in reason
    assert "[IMP:8][quarantine][skip]" in caplog.text
    logger.critical("[IMP:9][test] quarantine skip reason OK: %r", reason)


# endregion FUNC_test_quarantine_skip_applied


# region FUNC_test_quarantine_missing_rev_date_is_red
## @purpose — запись БЕЗ until (Rev-даты) → validate_quarantine ошибка + хук RuntimeError (RED).
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · Quarantine (W6 T6.3) — отсутствие Rev-даты = RED
# · Scenario: QUARANTINE[nodeid] = {reason, debt_ref} без until → validate_quarantine возвращает
# ·   ошибку «MISSING until», pytest_collection_modifyitems поднимает RuntimeError
# · Last fail: N/A (new mechanism — фиксирует «запись без Rev-даты = RED»)
# · Remove if: Rev-дата перестаёт быть обязательным полем реестра
def test_quarantine_missing_rev_date_is_red() -> None:
    """Запись без until → ошибка валидации + RuntimeError из хука (RED, не тихий skip)."""
    QUARANTINE["tests/test_bar.py::test_flaky"] = {
        "reason": "flaky under load",
        "debt_ref": "160-test-architecture-revamp",
    }

    errors = validate_quarantine()
    assert len(errors) >= 1, "отсутствие Rev-даты обязано давать ошибку валидации"
    assert "MISSING until" in errors[0] and "tests/test_bar.py::test_flaky" in errors[0]

    with pytest.raises(RuntimeError, match=r"(?s)\[QUARANTINE\].*MISSING until"):
        pytest_collection_modifyitems([_StubItem("tests/test_bar.py::test_flaky", markers={"smoke": True})])
    logger.critical("[IMP:9][test] quarantine missing Rev-date → RED (validation error + RuntimeError) — OK")


# endregion FUNC_test_quarantine_missing_rev_date_is_red


# region FUNC_test_quarantine_invalid_rev_date_is_red
## @purpose — невалидный формат until → тоже RED (не только отсутствие).
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · Quarantine (W6 T6.3) — невалидная Rev-дата
# · Scenario: until="09/01/2026" (не YYYY-MM-DD) → validate_quarantine ошибка «invalid until»
# · Last fail: N/A (new mechanism)
# · Remove if: формат Rev-даты меняется
def test_quarantine_invalid_rev_date_is_red() -> None:
    """Невалидный формат until → ошибка валидации (RED)."""
    QUARANTINE["tests/test_baz.py::test_flaky"] = {
        "until": "09/01/2026",
        "reason": "flaky",
        "debt_ref": "160",
    }

    errors = validate_quarantine()
    assert any("invalid until" in e for e in errors), f"невалидная дата обязана детектироваться: {errors}"
    logger.critical("[IMP:9][test] quarantine invalid Rev-date → RED (validation error) — OK")


# endregion FUNC_test_quarantine_invalid_rev_date_is_red


# region FUNC_test_quarantine_skips_only_docker_layers
## @purpose — детерминированный слой (unit/static/gates — НЕТ docker-маркера) с совпадающим
##            nodeid НЕ карантинится: skip НЕ применяется («флак = баг», карантин запрещён).
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · Quarantine (W6 T6.3) — защита детерминированных слоёв
# · Scenario: nodeid в реестре, но item без requires_docker/smoke/component/integration →
# ·   skipped == 0, skip-маркер НЕ добавлен
# · Last fail: N/A (new mechanism — защита static/unit/gates от случайного карантина)
# · Remove if: карантин разрешается для детерминированных слоёв (против политики)
def test_quarantine_skips_only_docker_layers() -> None:
    """Детерминированный item (без docker-маркера) с nodeid из реестра → НЕ карантинится."""
    QUARANTINE["tests/unit/test_static_thing.py::test_deterministic"] = {
        "until": "2026-09-01",
        "reason": "hypothetical flake (должен быть запрещён для unit)",
        "debt_ref": "160",
    }
    item = _StubItem("tests/unit/test_static_thing.py::test_deterministic", markers={})

    skipped = pytest_collection_modifyitems([item])

    assert skipped == 0, "детерминированный слой не подлежит карантину"
    assert item.skip_marker is None, "skip-маркер НЕ должен применяться к детерминированному тесту"
    logger.critical("[IMP:9][test] quarantine skips ONLY docker/network layers — deterministic layer protected — OK")


# endregion FUNC_test_quarantine_skips_only_docker_layers


# region FUNC_test_quarantine_empty_registry_noop
## @purpose — пустой реестр (default) = no-op: skipped == 0, валидация без ошибок.
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · Quarantine (W6 T6.3) — пустой реестр не трогает тесты
# · Scenario: QUARANTINE пуст → хук возвращает 0, ни один item не получает skip
# · Last fail: N/A (new mechanism)
# · Remove if: Quarantine-протокол отменяется
def test_quarantine_empty_registry_noop() -> None:
    """Пустой реестр → 0 карантинов, 0 ошибок валидации (дефолт без побочных эффектов)."""
    docker_item = _StubItem("tests/test_docker_thing.py::test_ok", markers={"requires_docker": True})

    assert validate_quarantine() == [], "пустой реестр обязан быть валиден"
    skipped = pytest_collection_modifyitems([docker_item])
    assert skipped == 0
    assert docker_item.skip_marker is None, "тест без записи в реестре не карантинится"
    logger.critical("[IMP:9][test] quarantine empty registry → no-op — OK")


# endregion FUNC_test_quarantine_empty_registry_noop
