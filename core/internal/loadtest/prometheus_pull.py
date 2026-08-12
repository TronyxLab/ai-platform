#!/usr/bin/env python3
# GREP_SUMMARY: loadtest prometheus promql query-range discovery saturation insufficient missing-metrics rate-window
# STRUCTURE: ▶ build_queries(run_time) → ◇ discover_metric_names (label/__name__/values) → ◇ query_range per query
#           (window [t0-60s, t1+60s], step 30s) → ○ aggregate avg/max (cpu → pct=avg×100) → ⊕ missing/insufficient
#           → ⎋ SaturationResult
# region MODULE_CONTRACT
## @purpose  Post-run PromQL pull из существующего Prometheus ноды (DevPlan 146 W2, инвариант 5:
##           НОЛЬ новой мониторинговой инфраструктуры — никаких новых экспортёров/pushgateway).
##           Range-запросы /api/v1/query_range в окне [t0-60s, t1+60s] с шагом 30s, discovery
##           имён метрик (label/__name__/values, ранний FAIL при недоступном Prometheus),
##           агрегаты avg/max за окно, секция saturation; метрика с <2 сэмплами →
##           insufficient → WARN (не FAIL); отсутствующая → missing → WARN.
## @scope    Потребитель: runner_cli.py (единственный) + unit-тесты (native, без сети —
##           monkeypatch _http_get_json). HTTP — urllib (requests не runtime-зависимость).
## @invariants
##   1. rate-окна ≤ run_time/2 (иначе [5m] при 300s-прогоне захватывает пре-ран):
##      run_time ≤ 180s → "1m", иначе → "2m" (константы DevPlan 146 §3.4)
##   2. Шаг = 30s (= min scrape_interval global), паддинг окна = 60s (scrape-лаг)
##   3. Недоступный Prometheus → PrometheusError (exit 1 по контракту guard-таблицы §3.7)
##   4. Метрика с суммарно <2 сэмплов за окно → insufficient_metrics (WARN)
##   5. Имя метрики вне discovery-набора → missing_metrics (WARN), НЕ ошибка
##   6. cpu_* rate-метрики → ключ *_pct = avg×100 (проценты одного ядра, как в примере
##      отчёта DevPlan 146: "cpu_nginx_pct": 42.3)
##   7. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
## @rationale Существующая телеметрия (Prometheus + cadvisor + node-exporter + экспортёры)
##            уже покрывает все нужные сигналы — saturation читается post-run pull-ом,
##            без новой инфраструктуры (D4 Brief 146).
## @changes  2026-08-11 | DevPlan 146 W2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Константы окна/шага (DevPlan 146 §3.4; scrape_interval: 30s global, 60s cadvisor/node-exporter) ──
RATE_WINDOW = "1m"  # smoke 90s / capacity 60s
RATE_WINDOW_LONG = "2m"  # regression 300s
QUERY_STEP = "30s"
QUERY_PAD = 60  # window [t0-60s, t1+60s] — страховка на scrape-лаг
MIN_SAMPLES = 2  # статистическая достоверность (инвариант 10 DevPlan 146)

_CPU_RATE_RE = re.compile(r"^cpu_")

# Имя → (PromQL, базовое имя метрики для discovery). Контейнерные — по label name (cadvisor).
_BASE_QUERIES: dict[str, tuple[str, str]] = {
    "cpu_nginx": ('rate(container_cpu_usage_seconds_total{name="nginx"}[{w}])', "container_cpu_usage_seconds_total"),
    "mem_nginx": ('container_memory_working_set_bytes{name="nginx"}', "container_memory_working_set_bytes"),
    "cpu_litellm": (
        'rate(container_cpu_usage_seconds_total{name="litellm"}[{w}])',
        "container_cpu_usage_seconds_total",
    ),
    "mem_litellm": ('container_memory_working_set_bytes{name="litellm"}', "container_memory_working_set_bytes"),
    "cpu_langfuse": (
        'rate(container_cpu_usage_seconds_total{name="langfuse"}[{w}])',
        "container_cpu_usage_seconds_total",
    ),
    "mem_langfuse": ('container_memory_working_set_bytes{name="langfuse"}', "container_memory_working_set_bytes"),
    "cpu_postgres": (
        'rate(container_cpu_usage_seconds_total{name="postgres"}[{w}])',
        "container_cpu_usage_seconds_total",
    ),
    "mem_postgres": ('container_memory_working_set_bytes{name="postgres"}', "container_memory_working_set_bytes"),
    "cpu_redis": ('rate(container_cpu_usage_seconds_total{name="redis"}[{w}])', "container_cpu_usage_seconds_total"),
    "mem_redis": ('container_memory_working_set_bytes{name="redis"}', "container_memory_working_set_bytes"),
    "cpu_clickhouse": (
        'rate(container_cpu_usage_seconds_total{name="clickhouse"}[{w}])',
        "container_cpu_usage_seconds_total",
    ),
    "mem_clickhouse": ('container_memory_working_set_bytes{name="clickhouse"}', "container_memory_working_set_bytes"),
    "nginx_rps": ("rate(nginx_http_requests_total[{w}])", "nginx_http_requests_total"),
    "nginx_conns": ("nginx_connections_active", "nginx_connections_active"),
    "pg_backends": ("pg_stat_database_numbackends", "pg_stat_database_numbackends"),
    "redis_ops": ("rate(redis_commands_processed_total[{w}])", "redis_commands_processed_total"),
    "redis_clients": ("redis_connected_clients", "redis_connected_clients"),
    "litellm_reqs": ("rate(litellm_proxy_total_requests[{w}])", "litellm_proxy_total_requests"),
    "litellm_err": ("rate(litellm_proxy_failed_requests[{w}])", "litellm_proxy_failed_requests"),
    "load1": ("node_load1", "node_load1"),
    "mem_avail": ("node_memory_MemAvailable_bytes", "node_memory_MemAvailable_bytes"),
    "net_rx": ("rate(node_network_receive_bytes_total[{w}])", "node_network_receive_bytes_total"),
}


# region DATA_SaturationResult
@dataclass(frozen=True)
class SaturationResult:
    """Результат PromQL-pull: агрегаты, пропущенные и недостоверные метрики.

    ## @purpose  Секция saturation отчёта (DevPlan 146 §3.4): avg/max по каждой метрике
    ##            за окно прогона + диагностика missing_metrics/insufficient_metrics (WARN).
    ## @invariants
    ##   - aggregates: {имя: {"avg": float|None, "max": float|None}}; cpu_* дополнительно
    ##     {"pct": avg×100}
    ##   - missing_metrics: базовое имя метрики вне discovery-набора ноды
    ##   - insufficient_metrics: метрика найдена, но сэмплов < MIN_SAMPLES за окно
    """

    aggregates: dict[str, dict[str, float | None]] = field(default_factory=dict)
    missing_metrics: list[str] = field(default_factory=list)
    insufficient_metrics: list[str] = field(default_factory=list)


# endregion DATA_SaturationResult


# region DATA_PrometheusError
class PrometheusError(Exception):
    """Prometheus недоступен / неверный ответ — exit 1 (guard-таблица DevPlan 146 §3.7)."""


# endregion DATA_PrometheusError


# region FUNC_build_queries
def build_queries(run_time: int) -> dict[str, tuple[str, str]]:
    """Построение PromQL-пула с rate-окном по длительности прогона.

    ▶ ┌run_time┐ → ◇ > 180s → window=2m | → window=1m → ⊕ {w} подстановка → ⎋ {имя: (promql, base_metric)}

    ## @purpose  Единая точка построения запросов (инвариант 1: rate-окна ≤ run_time/2 —
    ##            иначе при 300s-прогоне [5m] захватывает пре-ран).
    ## @io — ⇥ run_time: int (s) → ⎋ dict[str, (promql, base_metric_name)]
    ## @complexity — O(Q) — Q = число запросов в пуле
    ## @invariants
    ##   - run_time ≤ 180 → window "1m"; иначе → "2m" (константы §3.4)
    ##   - Возвращаемая копия — мутации пула не влияют на модульный уровень
    """
    window = RATE_WINDOW_LONG if run_time > 180 else RATE_WINDOW
    queries: dict[str, tuple[str, str]] = {}
    for name, (promql, base) in _BASE_QUERIES.items():
        # ⚠️ TRAP[BUG] · 2026-08-11 · P1 · str.format на PromQL-строках → KeyError на {name="nginx"}
        # · Symptom: build_queries() падал KeyError: 'name="nginx"' при первом же вызове (rate-окно)
        # · Root: promql.format(w=window) — фигурные скобки селекторов {name="..."} интерпретируются
        # ·   str.format как поля подстановки (не экранированы в строковом литерале пула запросов)
        # · Fix: .replace("{w}", window) — строковая подстановка без format-семантики
        # · Prevention: НЕ использовать str.format для PromQL-строк с селекторами; построение
        # ·   запросов покрыто unit-тестами (tests/unit/test_loadtest_prometheus_pull.py::TestBuildQueries)
        queries[name] = (promql.replace("{w}", window), base)
    logger.info(
        "[IMP:8][prometheus][build_queries] run_time=%ds → rate window=%s (%d queries)", run_time, window, len(queries)
    )
    return queries


# endregion FUNC_build_queries


# region FUNC__http_get_json
def _http_get_json(url: str, timeout: int = 15) -> dict:
    """GET + JSON-parse через urllib (единая точка для monkeypatch в тестах).

    ▶ ┌url┐ → ○ urllib.request.urlopen(timeout) → ○ json.loads → ◇ ошибки → PrometheusError → ⎋ dict

    ## @purpose  HTTP-слой пула: urllib (requests не runtime-зависимость платформы).
    ##            Все сетевые ошибки → PrometheusError с читаемым сообщением (exit 1).
    ## @io — ⇥ url: str, timeout: int → ⎋ dict (JSON-ответ)
    ## @complexity — O(1) — один запрос
    ## @raises — PrometheusError: недоступен / не-200 / битый JSON
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310 — internal Prometheus API (node host, fixed port)
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PrometheusError(f"Prometheus недоступен: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise PrometheusError(f"Prometheus вернул не-JSON: {exc}") from exc


# endregion FUNC__http_get_json


# region FUNC_discover_metric_names
def discover_metric_names(base_url: str, timeout: int = 15) -> set[str]:
    """Discovery имён метрик ноды: GET /api/v1/label/__name__/values.

    ▶ ┌base_url┐ → ○ GET label/__name__/values → ○ data[] → ⎋ set[str]

    ## @purpose  Инвариант 5: отсутствие метрики детектируется по discovery-набору
    ##            (риск R2 DevPlan 146 §7: имена отличаются от ожиданий → missing → WARN).
    ## @io — ⇥ base_url: str (http://host:9090), timeout → ⎋ set[str]
    ## @complexity — O(M) — M = число метрик на ноде (один запрос)
    ## @raises — PrometheusError (недоступен / неверная схема ответа)
    """
    payload = _http_get_json(f"{base_url}/api/v1/label/__name__/values", timeout=timeout)
    if payload.get("status") != "success":
        raise PrometheusError(f"label/__name__/values: status={payload.get('status')!r}, error={payload.get('error')}")
    names = payload.get("data")
    if not isinstance(names, list):
        raise PrometheusError("label/__name__/values: data не является списком")
    logger.info("[IMP:8][prometheus][discover] Discovered %d metric names", len(names))
    return {str(n) for n in names}


# endregion FUNC_discover_metric_names


# region FUNC_query_range
def query_range(
    base_url: str, query: str, start: float, end: float, timeout: int = 15
) -> list[list[tuple[float, float]]]:
    """Range-запрос /api/v1/query_range → матрица серий (ts, value).

    ▶ ┌query, [start, end]┐ → ○ GET query_range (step=30s) → ○ parse result[] → ⎋ series

    ## @purpose  Единственная точка range-запроса (шаг фиксирован QUERY_STEP=30s).
    ## @io — ⇥ base_url, query: str, start/end: float (unix), timeout
    ##       → ⎋ list[list[(ts: float, value: float)]] — серии значений
    ## @complexity — O(S) — S = суммарное число сэмплов в ответе
    ## @raises — PrometheusError (недоступен / неверная схема ответа)
    ## @invariants
    ##   - step=QUERY_STEP ("30s") — min scrape_interval (инвариант 2)
    ##   - Нечисловое значение сэмпла ("NaN"/"Inf") → пропускается (не падает pull)
    """
    params = urllib.parse.urlencode({"query": query, "start": int(start), "end": int(end), "step": QUERY_STEP})
    payload = _http_get_json(f"{base_url}/api/v1/query_range?{params}", timeout=timeout)
    if payload.get("status") != "success":
        raise PrometheusError(f"query_range: status={payload.get('status')!r}, error={payload.get('error')}")
    result = payload.get("data", {}).get("result")
    if not isinstance(result, list):
        raise PrometheusError("query_range: data.result не является списком")
    series: list[list[tuple[float, float]]] = []
    for item in result:
        values: list[tuple[float, float]] = []
        for ts, raw in item.get("values", []) if isinstance(item, dict) else []:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value != value:  # NaN
                continue
            values.append((float(ts), value))
        series.append(values)
    return series


# endregion FUNC_query_range


# region FUNC_aggregate_series
def aggregate_series(series: list[list[tuple[float, float]]]) -> tuple[float | None, float | None]:
    """Среднее и максимум по всем сэмплам всех серий (за окно прогона).

    ▶ ┌series┐ → ○ flatten samples → ◇ пусто → (None, None) → ⊕ avg/max → ⎋ (avg, max)

    ## @purpose  Агрегация диапазона: avg = среднее по всем сэмплам (для rate — средний
    ##            rate за окно), max — пик.
    ## @io — ⇥ series → ⎋ (avg: float|None, max: float|None) — None при 0 сэмплов
    ## @complexity — O(S) — S = число сэмплов
    ## @invariants
    ##   - Сэмпл-счётчик (< MIN_SAMPLES → insufficient) считается по этой же выборке
    """
    samples = [v for s in series for _, v in s]
    if not samples:
        return None, None
    return sum(samples) / len(samples), max(samples)


# endregion FUNC_aggregate_series


# region FUNC_run_saturation
def run_saturation(
    base_url: str,
    run_time: int,
    t0: float,
    t1: float,
    timeout: int = 15,
) -> SaturationResult:
    """Полный PromQL-pull: discovery → query_range × пул → агрегаты + диагностика.

    ▶ ┌base_url, run_time, t0, t1┐ → ○ discover → ○ build_queries → ○ per-query query_range
      (окно [t0-60, t1+60]) → ○ aggregate → ⊕ missing/insufficient → ⎋ SaturationResult

    ## @purpose  Секция saturation отчёта за один вызов (инварианты 3-6). Окно —
    ##            [t0-QUERY_PAD, t1+QUERY_PAD], шаг QUERY_STEP (scrape-лаг страховка).
    ## @io — ⇥ base_url: str, run_time: int (для rate-окна), t0/t1: float (unix секунды),
    ##         timeout: int → ⎋ SaturationResult
    ## @complexity — O(Q×(S + M)) — Q=пул запросов, S=сэмплы, M=размер discovery
    ## @raises — PrometheusError (недоступный Prometheus → exit 1, guard-таблица §3.7)
    ## @invariants
    ##   - Метрика вне discovery-набора → missing_metrics (НЕ error, НЕ запрос)
    ##   - Найденная метрика с суммарно < MIN_SAMPLES сэмплов → insufficient_metrics
    ##   - cpu_* → дополнительно "pct" = avg×100 (проценты одного ядра)
    ##   - missing_metrics/insufficient_metrics дедуплицируются (BUG-3, 146-m3): несколько
    ##     query_range на одну базовую метрику (litellm_proxy_failed_requests ×2,
    ##     container_cpu_usage_seconds_total ×6) давали дубли в отчёте — первое вхождение
    ##     сохраняется, итоговый список уникален (sorted, детерминирован)
    """
    window_start = t0 - QUERY_PAD
    window_end = t1 + QUERY_PAD
    discovered = discover_metric_names(base_url, timeout=timeout)
    queries = build_queries(run_time)

    aggregates: dict[str, dict[str, float | None]] = {}
    missing: list[str] = []
    insufficient: list[str] = []
    for name, (promql, base_metric) in queries.items():
        if base_metric not in discovered:
            if base_metric not in missing:
                missing.append(base_metric)
                logger.info("[IMP:7][prometheus][saturation] Metric missing (WARN): %s", base_metric)
            continue
        series = query_range(base_url, promql, window_start, window_end, timeout=timeout)
        sample_count = sum(len(s) for s in series)
        avg, max_val = aggregate_series(series)
        if sample_count < MIN_SAMPLES and base_metric not in insufficient:
            insufficient.append(base_metric)
            logger.info(
                "[IMP:7][prometheus][saturation] Insufficient samples (WARN): %s (%d < %d)",
                base_metric,
                sample_count,
                MIN_SAMPLES,
            )
        entry: dict[str, float | None] = {"avg": avg, "max": max_val}
        if _CPU_RATE_RE.match(name) and avg is not None:
            entry["pct"] = round(avg * 100, 1)
        aggregates[name] = entry
        logger.info("[IMP:8][prometheus][saturation] %s: avg=%s max=%s", name, avg, max_val)

    logger.info(
        "[IMP:9][prometheus][saturation] Pull complete: %d aggregates, %d missing, %d insufficient",
        len(aggregates),
        len(missing),
        len(insufficient),
    )
    return SaturationResult(
        aggregates=aggregates, missing_metrics=sorted(missing), insufficient_metrics=sorted(insufficient)
    )


# endregion FUNC_run_saturation
