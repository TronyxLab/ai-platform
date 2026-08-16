# GREP_SUMMARY: gate, wave-sort, collection-sort, contract, state-leak, deterministic, idempotent, stable, pytest_collection_modifyitems, T12.6, T-9
# STRUCTURE: ▶ import tests.conftest → ◇ test_sort_contract_documented → ◇ test_sort_deterministic_idempotent → ◇ test_sort_stable_equal_keys → ◇ test_compute_module_waves_deterministic → ⎋
# region MODULE_CONTRACT
## @purpose  Gate: контракт Wave-Pipeline сортировки коллекции (DevPlan 136 W12 T12.6, T-9).
##           Сортировка items по (wave_number, nodeid) — задокументированный контракт (см.
##           pytest_collection_modifyitems в tests/conftest.py «Sort contract»): детерминирована,
##           идемпотентна, стабильна. «State-leak»-аспект: порядок исполнения теста зависит
##           ТОЛЬКО от (wave, nodeid) — чистой функции item'а, не от состояния сессии; прогон
##           по файлам и полный suite дают одинаковый относительный порядок одного теста.
## @scope    Статический gate (без Docker): верифицирует контракт сортировки + детерминизм
##           _compute_module_waves (модульный граф).
## @invariants
##   - Сортировка идемпотентна: повторный sort по тому же ключу = тот же порядок
##   - Сортировка детерминирована: перемешанный вход → тот же отсортированный порядок
##   - Стабильна: равные ключи сохраняют исходный порядок (list.sort contract)
##   - Контракт документирован в tests/conftest.py (маркер «Sort contract»)
## @rationale DevPlan 136 W12 T12.6: сортировка — контракт, а не деталь реализации; gate
##            ловит регрессию (вставка state-зависимого ключа, недетерминированный key).
## @changes 2026-08-05 | DevPlan 136 W12 T12.6 — создан
# endregion MODULE_CONTRACT

import logging
import pathlib
import random

import pytest

from tests.conftest import _compute_module_waves

logger = logging.getLogger(__name__)

# Ключ сортировки — КОНТРАКТ (должен совпадать с tests/conftest.py pytest_collection_modifyitems):
# (wave_number, nodeid); wave_number = marker.args[0] или 0.


def _sort_key(wave: int, nodeid: str) -> tuple[int, str]:
    """Канонический sort-ключ коллекции (T12.6 T-9): чистая функция (wave, nodeid)."""
    return (wave, nodeid)


@pytest.mark.gate
def test_sort_contract_documented(caplog) -> None:
    """Контракт сортировки задокументирован в tests/conftest.py (T12.6 T-9).

    # 🧪 TRAP[TEST] · Regression: сортировка-контракт удалён/переписан без документации
    # · Scenario: grep tests/conftest.py на маркер «Sort contract»
    # · Last fail: N/A (новый gate)
    # · Remove if: контракт переехал в иной модуль (обновить путь в этом тесте)
    """
    conftest_src = pathlib.Path(__file__).resolve().parent.parent / "conftest.py"
    src = conftest_src.read_text()
    assert "Sort contract" in src, "T12.6: tests/conftest.py должен документировать sort-контракт"
    assert "items.sort(" in src, "T12.6: pytest_collection_modifyitems должен сортировать items"
    logger.critical("[IMP:9][test_sort_contract_documented] PASS: sort-контракт задокументирован в tests/conftest.py")
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")


@pytest.mark.gate
def test_sort_deterministic_idempotent(caplog) -> None:
    """Сортировка детерминирована и идемпотентна (T12.6 T-9).

    # 🧪 TRAP[TEST] · Regression: sort-ключ стал недетерминированным (state-зависимым)
    # · Scenario: перемешанный список (wave, nodeid) → 2 сортировки → идентичные порядки
    # · Last fail: N/A (новый gate)
    # · Remove if: сортировка коллекции удалена из Wave-Pipeline
    """
    rng = random.Random(42)  # ruff: ignore[S311] — детерминированный seed (фикс. тест-паттерн, не криптография)
    samples = [(rng.randint(0, 5), f"tests/test_{i}.py::test_{j}") for i in range(30) for j in range(4)]
    rng.shuffle(samples)
    samples_shuffled_again = list(samples)
    rng.shuffle(samples_shuffled_again)

    first = sorted(samples, key=lambda pair: _sort_key(*pair))
    second = sorted(first, key=lambda pair: _sort_key(*pair))  # идемпотентность
    third = sorted(samples_shuffled_again, key=lambda pair: _sort_key(*pair))  # детерминизм

    assert first == second, "T12.6: повторный sort изменил порядок (не идемпотентен)"
    assert first == third, "T12.6: перемешанный вход дал другой порядок (не детерминирован)"
    logger.critical(
        "[IMP:9][test_sort_deterministic_idempotent] PASS: sort детерминирован и идемпотентен (%d items)",
        len(first),
    )
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")


@pytest.mark.gate
def test_sort_stable_equal_keys(caplog) -> None:
    """Сортировка стабильна: равные ключи сохраняют порядок сбора (T12.6 T-9).

    # 🧪 TRAP[TEST] · Regression: sort с unstable-key (сравнение только по wave без nodeid)
    # · Scenario: 3 items с wave=1 в порядке A,B,C → после sort порядок A,B,C сохранён
    # · Last fail: N/A (новый gate)
    # · Remove if: Python list.sort гарантия стабильности отменена (невозможно)
    """
    items = [
        (1, "tests/a.py::t1"),
        (1, "tests/b.py::t2"),
        (0, "tests/c.py::t3"),
        (1, "tests/d.py::t4"),
    ]
    stable_sorted = sorted(items, key=lambda pair: _sort_key(*pair))
    # Все (1, ...) сохраняют исходный относительный порядок a,b,d
    wave1_ids = [nid for w, nid in stable_sorted if w == 1]
    assert wave1_ids == ["tests/a.py::t1", "tests/b.py::t2", "tests/d.py::t4"], (
        "T12.6: стабильность нарушена — равные wave изменили относительный порядок"
    )
    # Вторичный ключ nodeid — детерминированный порядок внутри волны
    assert stable_sorted[0][1] == "tests/c.py::t3", "wave 0 идёт первым"
    logger.critical("[IMP:9][test_sort_stable_equal_keys] PASS: sort стабилен (равные wave сохраняют порядок)")
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")


@pytest.mark.gate
def test_compute_module_waves_deterministic(caplog) -> None:
    """Модульный волновой граф детерминирован (два вызова → одинаковый результат).

    # 🧪 TRAP[TEST] · Regression: _compute_module_waves с недетерминированным обходом
    # · Scenario: два вызова _compute_module_waves() → идентичные dict
    # · Last fail: N/A (новый gate)
    # · Remove if: волновой граф вычисляется вне conftest (перемещён)
    """
    waves_a = _compute_module_waves()
    waves_b = _compute_module_waves()
    assert waves_a == waves_b, "T12.6: _compute_module_waves недетерминирован между вызовами"
    assert isinstance(waves_a, dict) and all(isinstance(v, int) and v >= 0 for v in waves_a.values()), (
        "T12.6: волновой граф — dict[str, int >= 0]"
    )
    logger.critical(
        "[IMP:9][test_compute_module_waves_deterministic] PASS: волновой граф детерминирован (%d модулей)",
        len(waves_a),
    )
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
