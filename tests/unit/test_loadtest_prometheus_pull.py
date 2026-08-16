# GREP_SUMMARY: loadtest prometheus unit promql build query-range discovery missing insufficient parse
# STRUCTURE: ▶ fixtures (query_range JSON, discovery JSON) → ◇ build_queries (rate-window parametrized) → ◇ parse/aggregate
#           (avg/max/pct, NaN-фильтр) → ◇ discovery (missing → WARN-список) → ◇ run_saturation (http_get=fake
#           параметром, W4b DI: missing/insufficient/ok) → ⎋ 10 tests
# region MODULE_CONTRACT
## @purpose  Unit-тесты PromQL-pull (DevPlan 146 W2, tests/unit/test_loadtest_prometheus_pull.py):
##           построение запросов (rate-окна по run_time), парс ответов query_range
##           (fixture JSON), агрегаты avg/max (cpu → pct), discovery имён, семантика
##           missing_metrics/insufficient_metrics (WARN, не FAIL), недоступный Prometheus.
## @scope    Чистые функции + http_get=fake параметром (W4b DI, 0 monkeypatch) — БЕЗ сети и subprocess
##           (native pytest, Zero Hardcode Rule: JSON-фикстуры инлайн).
## @invariants
##   - rate-окно: run_time ≤ 180 → "1m", иначе "2m" (DevPlan 146 §3.4, инвариант 10)
##   - <2 сэмплов → insufficient_metrics; имя вне discovery → missing_metrics
##   - NaN/нечисловые сэмплы пропускаются (не роняют pull)
##   - LDD: IMP:9 в успешных сценариях (Anti-Illusion Rule)
## @rationale PromQL — единственная post-run телеметрия (инвариант 5): контракт
##            недостающих/недостоверных метрик тестируется детерминированно (риск R2 §7).
## @changes  2026-08-11 | DevPlan 146 W2 — Created
## @changes  2026-08-13 | DevPlan 160 W4b — http_get параметром (убраны monkeypatch _http_get_json)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

import pytest

from core.internal.loadtest.prometheus_pull import (
    PrometheusError,
    SaturationResult,
    aggregate_series,
    build_queries,
    query_range,
    run_saturation,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region HELPER_make_matrix_response
def _make_matrix_response(series_values: list[list[list]]):
    """Фикстур ответа query_range (data.result matrix).

    ## @purpose — JSON-схема Prometheus /api/v1/query_range: result[] → metric + values [[ts, "v"]].
    ## @io — ⇥ series_values: list[list[list]] → ⎋ dict (payload)
    """
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {}, "values": values} for values in series_values],
        },
    }


# endregion HELPER_make_matrix_response


# region TEST_build_queries
# 🧪 TRAP[TEST] · Scenario: rate-окна по длительности прогона (инвариант 10)
# · Regression: smoke 90s → 1m; regression 300s → 2m (иначе [5m] захватывает пре-ран)
# · Last fail: N/A (new)
# · Remove if: политика rate-окон изменена
class TestBuildQueries:
    @pytest.mark.parametrize(
        "run_time,metric,expected_rate",
        [
            (90, "nginx_rps", "rate(nginx_http_requests_total[1m])"),  # smoke 90s → 1m
            (90, "cpu_nginx", 'rate(container_cpu_usage_seconds_total{name="nginx"}[1m])'),  # capacity 60s → 1m
            (
                300,
                "nginx_rps",
                "rate(nginx_http_requests_total[2m])",
            ),  # regression 300s → 2m (иначе [5m] захватывает пре-ран)
        ],
    )
    def test_rate_window_by_run_time(self, run_time: int, metric: str, expected_rate: str):
        """Rate-окно по длительности прогона: run_time ≤ 180 → 1m, иначе 2m (инвариант 10)."""
        queries = build_queries(run_time)
        assert queries[metric][0] == expected_rate

    def test_gauge_queries_have_no_rate(self):
        """Гейдж-метрики (pg_backends/load1) — без rate-окна."""
        queries = build_queries(90)
        assert queries["pg_backends"][0] == "pg_stat_database_numbackends"
        assert queries["load1"][0] == "node_load1"

    def test_base_metric_names_present(self):
        """Каждый запрос несёт базовое имя метрики (для discovery)."""
        queries = build_queries(90)
        assert queries["nginx_rps"][1] == "nginx_http_requests_total"
        assert queries["litellm_reqs"][1] == "litellm_proxy_total_requests"


# endregion TEST_build_queries


# region TEST_query_range_and_aggregate
# 🧪 TRAP[TEST] · Scenario: парс query_range + агрегаты (avg/max, NaN-фильтр)
# · Regression: NaN-сэмплы пропускаются; ошибка схемы → PrometheusError
# · Last fail: N/A (new)
# · Remove if: формат ответа Prometheus API изменён
class TestQueryRangeAndAggregate:
    def test_query_range_parses_series(self):
        """query_range: матрица → серии (ts, value); нечисловые сэмплы пропускаются."""
        payload = _make_matrix_response([
            [[1700000000, "1.5"], [1700000030, "2.5"], [1700000060, "NaN"]],
            [[1700000000, "0.5"]],
        ])
        series = query_range(
            "http://prom:9090",
            "rate(x[1m])",
            1700000000,
            1700000060,
            http_get=lambda url, timeout=15: payload,  # ruff: ignore[ARG005]
        )
        assert len(series) == 2
        assert series[0] == [(1700000000.0, 1.5), (1700000030.0, 2.5)]  # NaN отфильтрован

    def test_query_range_error_status(self):
        """status != success → PrometheusError (exit 1 контракт)."""
        with pytest.raises(PrometheusError):
            query_range(
                "http://prom:9090",
                "x",
                1,
                2,
                http_get=lambda url, timeout=15: {"status": "error", "error": "bad"},  # ruff: ignore[ARG005]
            )

    @pytest.mark.parametrize(
        "series,expected",
        [
            ([[(1, 1.0), (2, 3.0)], [(3, 5.0)]], (3.0, 5.0)),  # avg/max по всем сэмплам всех серий
            ([], (None, None)),  # пустой набор → статистика недостоверна
        ],
    )
    def test_aggregate_series(self, series: list, expected: tuple):
        """aggregate_series: avg/max по сэмплам; пустой набор → (None, None)."""
        assert aggregate_series(series) == expected


# endregion TEST_query_range_and_aggregate


# region TEST_run_saturation
# 🧪 TRAP[TEST] · Scenario: run_saturation (missing / insufficient / ok + cpu pct)
# · Regression: метрика вне discovery → missing (WARN); <2 сэмплов → insufficient (WARN)
# · Last fail: N/A (new)
# · Remove if: семантика saturation-секции изменена
class TestRunSaturation:
    def _fake_http(self, discovered_names: set[str], responses: dict[str, dict]):
        """Фабрика fake _http_get_json: discovery + per-query payloads.

        ## @purpose — Маршрутизация по URL: label/__name__/values → discovery;
        ##            query_range-запросы → по вхождению имени метрики в URL.
        """

        def _fake(url: str, timeout: int = 15) -> dict:
            if "/label/__name__/values" in url:
                return {"status": "success", "data": sorted(discovered_names)}
            for name, payload in responses.items():
                if name in url:  # urlencoded PromQL содержит имя метрики как есть
                    return payload
            return _make_matrix_response([])

        return _fake

    def test_missing_metric_warn(self):
        """Метрика вне discovery → missing_metrics (WARN, не error) — пустой discovery-набор."""
        fake = self._fake_http(set(), {})
        result = run_saturation("http://prom:9090", 90, 1000, 1090, http_get=fake)
        assert isinstance(result, SaturationResult)
        assert "node_load1" in result.missing_metrics  # вне discovery-набора
        assert result.aggregates == {}  # ни один запрос не выполнен

    def test_insufficient_samples_warn(self):
        """Метрика найдена, но сэмплов < 2 → insufficient_metrics (WARN)."""
        fake = self._fake_http(
            {"nginx_http_requests_total"},
            {"nginx_http_requests_total": _make_matrix_response([[[1000, "1.0"]]])},
        )
        result = run_saturation("http://prom:9090", 90, 1000, 1090, http_get=fake)
        assert "nginx_http_requests_total" in result.insufficient_metrics
        assert result.aggregates["nginx_rps"]["avg"] == 1.0

    def test_ok_aggregates_and_cpu_pct(self, caplog):
        """Полный набор: avg/max + cpu pct (avg×100); вне-discovery → missing (WARN)."""
        caplog.set_level(logging.INFO)
        values = [[[1000, "0.4"], [1030, "0.6"], [1060, "0.5"]]]
        fake = self._fake_http(
            {"container_cpu_usage_seconds_total", "pg_stat_database_numbackends"},
            {
                "container_cpu_usage_seconds_total": _make_matrix_response(values),
                "pg_stat_database_numbackends": _make_matrix_response(values),
            },
        )
        result = run_saturation("http://prom:9090", 90, 1000, 1090, http_get=fake)
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in list(caplog.records):
            if "[IMP:" in record.message:
                logger.info("%s", record.message)
                if "[IMP:9]" in record.message:
                    found = True
        logger.info("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (successful saturation pull)"
        assert result.aggregates["cpu_nginx"]["avg"] == 0.5
        assert result.aggregates["cpu_nginx"]["pct"] == 50.0  # avg × 100
        assert result.aggregates["cpu_nginx"]["max"] == 0.6
        assert result.aggregates["pg_backends"]["max"] == 0.6
        assert "node_load1" in result.missing_metrics  # вне discovery-набора → WARN
        assert result.insufficient_metrics == []

    def test_prometheus_unreachable_raises(self):
        """Недоступный Prometheus → PrometheusError (exit 1 по guard-таблице §3.7)."""

        def _boom(url: str, timeout: int = 15) -> dict:
            msg = "connection refused"
            raise PrometheusError(msg)

        with pytest.raises(PrometheusError):
            run_saturation("http://prom:9090", 90, 1000, 1090, http_get=_boom)


# endregion TEST_run_saturation
