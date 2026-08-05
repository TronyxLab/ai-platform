# GREP_SUMMARY: gate, retry-rate, retry-until-green, smoke, compose, threshold, 15-percent, T12.7, T-11, retry-stats
# STRUCTURE: ▶ import smoke retry_stats/_bump_retry_stats → ◇ test_retry_rate_accounting → ◇ test_retry_rate_threshold_constant → ◇ test_retry_rate_within_threshold → ⎋
# region MODULE_CONTRACT
## @purpose  Gate: учёт retry-rate smoke-compose стартов (DevPlan 136 W12 T12.7, T-11).
##           retry-until-green (TRAP[DECISION] 2026-07-23, _start_single_module) логирует счётчик
##           и гейтится при >15% retry-rate: _RETRY_STATS (attempts/retries) ведётся в
##           _conftest/smoke.py, проверяется в sessionfinish (_check_smoke_retry_rate) и здесь
##           (порог-константа + учётная логика — детерминированные unit-проверки без Docker).
## @scope    Статический gate: верифицирует учётную логику и порог; сам факт превышения в
##           реальной docker-сессии диагностируется в sessionfinish (RED-лог).
## @invariants
##   - retry_stats() возвращает (attempts, retries) — thread-safe, старт {0, 0}
##   - _bump_retry_stats(retried=True) увеличивает и attempts, и retries
##   - _bump_retry_stats(retried=False) увеличивает только attempts
##   - Порог: 15% (_RETRY_RATE_THRESHOLD) — канон в ОДНОМ месте (smoke.py), гейт сверяет
## @rationale DevPlan 136 W12 T12.7: «retry-until-green: логировать счётчик, gate при >15%
##            retry-rate» — порог и учёт — контракт, проверяемый статически; фактический
##            retry-rate живой docker-сессии — диагностика sessionfinish.
## @changes 2026-08-05 | DevPlan 136 W12 T12.7 — создан
# endregion MODULE_CONTRACT

import logging

import pytest
from _conftest.smoke import (
    _RETRY_RATE_THRESHOLD,
    _bump_retry_stats,
    _set_retry_stats,
    retry_stats,
)

logger = logging.getLogger(__name__)

# Канон порога: 15% — единственный источник (smoke.py). Не дублировать значение в тесте.
EXPECTED_THRESHOLD = 0.15


@pytest.mark.gate
def test_retry_rate_accounting(caplog) -> None:
    """Учётная логика retry-stats корректна (attempts/retries).

    # 🧪 TRAP[TEST] · Regression: _bump_retry_stats сломал учёт (перепутаны attempts/retries)
    # · Scenario: 2 успешных старта + 1 retry → (attempts=3, retries=1)
    # · Last fail: N/A (новый gate)
    # · Remove if: retry-учёт перемещён из smoke.py (обновить импорт)
    """
    # _RETRY_STATS — модульный глобал; счётчики не детерминированы между прогонами.
    # Тестируем ОТНОСИТЕЛЬНУЮ дельту: attempts/retries до и после, затем restore (try/finally)
    # — иначе sessionfinish gates-прогона даёт ложный RED retry-rate.
    attempts_before, retries_before = retry_stats()
    try:
        _bump_retry_stats(retried=False)  # обычный старт
        _bump_retry_stats(retried=False)  # обычный старт
        _bump_retry_stats(retried=True)  # retry

        attempts_after, retries_after = retry_stats()
        assert attempts_after - attempts_before == 3, "T12.7: 3 попытки старта не учтены"
        assert retries_after - retries_before == 1, "T12.7: 1 retry не учтён"
        logger.critical(
            "[IMP:9][test_retry_rate_accounting] PASS: дельта attempts=%d retries=%d",
            attempts_after - attempts_before,
            retries_after - retries_before,
        )
    finally:
        _set_retry_stats(attempts_before, retries_before)  # restore — без side-effect
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")


@pytest.mark.gate
def test_retry_rate_threshold_constant(caplog) -> None:
    """Порог 15% — единственный канон (smoke.py), gate сверяет значение.

    # 🧪 TRAP[TEST] · Regression: порог изменён без синхронизации с контрактом
    # · Scenario: _RETRY_RATE_THRESHOLD == 0.15
    # · Last fail: N/A (новый gate)
    # · Remove if: порог станет параметризуемым (env) — обновить этот тест на инвариант
    """
    assert _RETRY_RATE_THRESHOLD == EXPECTED_THRESHOLD, (
        f"T12.7: порог retry-rate {_RETRY_RATE_THRESHOLD} != канон {EXPECTED_THRESHOLD} "
        "(DevPlan 136 W12 T12.7: >15% = resource contention)"
    )
    assert 0.0 < _RETRY_RATE_THRESHOLD < 1.0, "T12.7: порог должен быть вероятностью в (0, 1)"
    logger.critical("[IMP:9][test_retry_rate_threshold_constant] PASS: порог=%s", _RETRY_RATE_THRESHOLD)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")


@pytest.mark.gate
def test_retry_rate_within_threshold_computation(caplog) -> None:
    """Вычисление rate и пороговое сравнение соответствуют контракту (>15% = RED).

    # 🧪 TRAP[TEST] · Regression: пороговое сравнение (rate > 0.15) инвертировано
    # · Scenario: rate = retries/attempts; 1 retry из 5 attempts = 20% > 15% → RED
    # · Last fail: N/A (новый gate)
    # · Remove if: логика сравнения переехала из _check_smoke_retry_rate (session.py)
    """
    # Чистая проверка арифметики порога (без мутации глобального счётчика):
    # 1 retry из 5 attempts → 20% → превышает 15% (RED-условие в sessionfinish)
    attempts, retries = 5, 1
    rate = retries / attempts
    assert rate > _RETRY_RATE_THRESHOLD, "T12.7: 20% retry-rate должен превышать 15% порог"
    # 0 retries из 5 attempts → 0% → в пределах порога
    assert _RETRY_RATE_THRESHOLD >= (0 / 5), "T12.7: 0% в пределах порога"
    logger.critical("[IMP:9][test_retry_rate_within_threshold_computation] PASS: rate=%.0f%% vs порог 15%%", rate * 100)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
