# GREP_SUMMARY: loadtest baseline unit history json compare regression delta host-reset first-run thresholds
# STRUCTURE: ▶ fixtures (tmp history dir) → ◇ append/load roundtrip (atomic) → ◇ compare_previous
#           (same-mode prev, host-reset, delta_p95 1.5×, delta_error_pp 2pp, first_run, битый JSON) → ⎋ 10 tests
# region MODULE_CONTRACT
## @purpose  Unit-тесты baseline (DevPlan 146 W3, tests/unit/test_loadtest_baseline.py):
##           запись/чтение history.json (atomic_write_json канон), выбор previous ТОЛЬКО
##           того же режима, регрессионные пороги (p95 1.5×, error +2pp), negative-сценарии:
##           первый прогон (нет previous), смена host → baseline_reset (не FAIL), битый JSON.
## @scope    Чистые функции core/internal/loadtest/baseline.py — tmp_path фикстуры,
##           без subprocess и сети (native pytest).
## @invariants
##   - previous = последний прогон того же mode (smoke-90s vs regression-300s несравнимы)
##   - host-смена → baseline_reset=True, prev=None (инвариант 9: пересоздание VPS,
##     сравнение с другим железом — мусор, DevPlan 146 §3.5)
##   - delta_p95 = new/prev (ratio); prev_p95 == 0/None → delta_p95 None (нет деления на 0)
##   - regression_fail = delta_p95 > 1.5 ИЛИ delta_error_pp > 2.0
##   - LDD: IMP:9 в успешных сценариях (Anti-Illusion Rule)
## @rationale Baseline — ядро AC2 (regression-сравнение и FAIL при 1.5×): пороги и
##            host-детекция тестируются детерминированно (риск R7 пересоздания VPS).
## @changes  2026-08-11 | DevPlan 146 W3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging

import pytest

from core.internal.loadtest.baseline import (
    BaselineComparison,
    append_run,
    compare_previous,
    load_history,
)
from core.internal.shared.exceptions import ConfigParseError

logger = logging.getLogger(__name__)


# region HELPER_make_run
def _make_run(ts: str, host: str, mode: str, p95: float, error_rate: float) -> dict:
    """Компактная строка прогона (формат DevPlan 146 §3.5)."""
    return {
        "ts": ts,
        "host": host,
        "mode": mode,
        "rps": 10.0,
        "p50": 0.1,
        "p95": p95,
        "p99": 0.5,
        "error_rate": error_rate,
        "max_rps": None,
        "verdict": "PASS",
        "delta_vs_prev": None,
        "version": "abc123",
    }


# endregion HELPER_make_run


# ═══════════════════════════════════════════════════════════════════════════════
# load/append roundtrip
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_history_io
# 🧪 TRAP[TEST] · Scenario: history.json roundtrip (append/load, битый JSON)
# · Regression: append не затирает существующие runs; битый JSON → ConfigParseError (exit 3)
# · Last fail: N/A (new)
# · Remove if: формат history.json изменён
class TestHistoryIo:
    def test_append_load_roundtrip(self, tmp_path, caplog):
        """append → load: два прогона в хронологическом порядке, файл атомарен."""
        caplog.set_level(logging.INFO)
        history_dir = tmp_path / "history" / "node" / "web"
        append_run(history_dir, _make_run("2026-08-11T10:00:00Z", "h1", "smoke", 0.3, 0.0))
        append_run(history_dir, _make_run("2026-08-11T11:00:00Z", "h1", "smoke", 0.31, 0.0))
        runs = load_history(history_dir)
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in caplog.records:
            if "[IMP:" in record.message:
                print(record.message)
                if "[IMP:9]" in record.message:
                    found = True
        print("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (append success)"
        assert len(runs) == 2
        assert runs[0]["p95"] == 0.3 and runs[1]["p95"] == 0.31
        # Формат файла: {"runs": [...]}
        raw = json.loads((history_dir / "history.json").read_text(encoding="utf-8"))
        assert len(raw["runs"]) == 2

    def test_load_missing_returns_empty(self, tmp_path):
        """Отсутствующий history.json → [] (первый прогон — не ошибка)."""
        assert load_history(tmp_path / "nohistory") == []

    def test_broken_json_raises_parse_error(self, tmp_path):
        """Битый history.json → ConfigParseError (exit 3 по контракту)."""
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        (history_dir / "history.json").write_text("{broken", encoding="utf-8")
        with pytest.raises(ConfigParseError):
            load_history(history_dir)


# endregion TEST_history_io


# ═══════════════════════════════════════════════════════════════════════════════
# compare_previous
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_compare_previous
# 🧪 TRAP[TEST] · Scenario: compare_previous (first_run / host-reset / пороги 1.5× и +2pp)
# · Regression: порог превышен → regression_fail=True (AC2); host-смена → baseline_reset
# · Last fail: N/A (new)
# · Remove if: критерии регрессии DevPlan 146 §3.5 изменены
class TestComparePrevious:
    def test_first_run(self):
        """Нет previous того же режима → first_run=True, prev=None, нет FAIL."""
        comparison = compare_previous([], "smoke", {"p95": 0.3, "error_rate": 0.0}, host="h1")
        assert comparison.first_run is True
        assert comparison.prev is None and comparison.regression_fail is False

    def test_same_mode_prev_selected(self):
        """previous = последний прогон ТОГО ЖЕ режима (smoke не затеняет regression)."""
        runs = [
            _make_run("t1", "h1", "smoke", 0.9, 0.0),
            _make_run("t2", "h1", "regression", 0.3, 0.0),
            _make_run("t3", "h1", "regression", 0.35, 0.0),
        ]
        comparison = compare_previous(runs, "regression", {"p95": 0.4, "error_rate": 0.0}, host="h1")
        assert comparison.prev["p95"] == 0.35  # последний regression, НЕ smoke 0.9
        assert comparison.first_run is False

    def test_baseline_reset_on_host_change(self, caplog):
        """Смена host (VPS пересоздана, инвариант 9) → baseline_reset, prev=None, НЕ FAIL."""
        caplog.set_level(logging.INFO)
        runs = [_make_run("t1", "old-host", "smoke", 0.3, 0.0)]
        comparison = compare_previous(runs, "smoke", {"p95": 0.8, "error_rate": 0.0}, host="new-host")
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in caplog.records:
            if "[IMP:" in record.message:
                print(record.message)
                if "[IMP:9]" in record.message:
                    found = True
        print("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (baseline_reset detection)"
        assert comparison.baseline_reset is True
        assert comparison.prev is None and comparison.regression_fail is False

    def test_delta_p95_within_threshold(self):
        """p95 1.2× prev → regression_fail=False (1.2 < 1.5)."""
        runs = [_make_run("t1", "h1", "smoke", 0.25, 0.0)]
        comparison = compare_previous(runs, "smoke", {"p95": 0.3, "error_rate": 0.0}, host="h1")
        assert comparison.delta_p95 == pytest.approx(1.2)
        assert comparison.regression_fail is False

    def test_delta_p95_exceeds_threshold(self):
        """p95 1.6× prev → regression_fail=True (AC2: 1.5× порог)."""
        runs = [_make_run("t1", "h1", "smoke", 0.2, 0.0)]
        comparison = compare_previous(runs, "smoke", {"p95": 0.32, "error_rate": 0.0}, host="h1")
        assert comparison.delta_p95 == pytest.approx(1.6)
        assert comparison.regression_fail is True

    def test_delta_error_pp_exceeds_threshold(self):
        """error +3pp (> +2pp) → regression_fail=True даже при p95-норме."""
        runs = [_make_run("t1", "h1", "smoke", 0.3, 0.01)]
        comparison = compare_previous(runs, "smoke", {"p95": 0.3, "error_rate": 0.04}, host="h1")
        assert comparison.delta_error_pp == pytest.approx(3.0)
        assert comparison.regression_fail is True

    def test_prev_p95_zero_no_division(self):
        """prev p95 = 0 → delta_p95 None (деление на ноль исключено, сравнение по error)."""
        runs = [dict(_make_run("t1", "h1", "smoke", 0.0, 0.0), p95=0.0)]
        comparison = compare_previous(runs, "smoke", {"p95": 0.3, "error_rate": 0.0}, host="h1")
        assert comparison.delta_p95 is None
        assert comparison.regression_fail is False

    def test_compare_returns_dataclass(self):
        """Тип результата — BaselineComparison (контракт отчёта)."""
        comparison = compare_previous([], "smoke", {"p95": 0.3, "error_rate": 0.0}, host="h1")
        assert isinstance(comparison, BaselineComparison)


# endregion TEST_compare_previous
