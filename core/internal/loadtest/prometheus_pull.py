#!/usr/bin/env python3
# GREP_SUMMARY: loadtest prometheus promql query-range discovery saturation insufficient missing-metrics rate-window cli node-side
# STRUCTURE: ▶ build_queries(run_time) → ◇ discover_metric_names (label/__name__/values) → ◇ query_range per query
#           (window [t0-60s, t1+60s], step 30s) → ○ aggregate avg/max (cpu → pct=avg×100) → ⊕ missing/insufficient
#           → ⎋ SaturationResult → ○ CLI main (F-036 node-side pull): JSON stdout → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Post-run PromQL pull из существующего Prometheus ноды (DevPlan 146 W2, инвариант 5:
##           НОЛЬ новой мониторинговой инфраструктуры — никаких новых экспортёров/pushgateway).
##           Range-запросы /api/v1/query_range в окне [t0-60s, t1+60s] с шагом 30s, discovery
##           имён метрик (label/__name__/values, ранний FAIL при недоступном Prometheus),
##           агрегаты avg/max за окно, секция saturation; метрика с <2 сэмплами →
##           insufficient → WARN (не FAIL); отсутствующая → missing → WARN.
## @scope    Потребитель: runner_cli.py (единственный) + unit-тесты (native, без сети —
##           инъекция http_get). HTTP — shared/http_client (urllib stdlib, requests не runtime-зависимость).
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
##   8. Standalone-исполнение (F-036, DevPlan 016 TASK-9): node-side CLI запускается на ноде
##      БЕЗ core на sys.path — report.SaturationAgg (только аннотации) и shared.http_client
##      опциональны (fallback: локальный TypedDict + stdlib urllib). Бизнес-логика pull НЕ
##      дублируется: на ноде выполняется тот же run_saturation/discover/query_range.
## @rationale Существующая телеметрия (Prometheus + cadvisor + node-exporter + экспортёры)
##            уже покрывает все нужные сигналы — saturation читается post-run pull-ом,
##            без новой инфраструктуры (D4 Brief 146). CLI (F-036) делает pull исполняемым
##            НА ноде через единичную read-only ssh-команду — REF-0016 (AllowTcpForwarding=no)
##            сохраняется: TCP-forwarding не используется и не требуется.
## @changes  2026-08-11 | DevPlan 146 W2 — Created
## @changes  2026-08-27 | DevPlan 016 TASK-9 — CLI main() + standalone-импорт (F-036 node-side pull)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import http.client
import json
import logging
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, TypeAlias, TypedDict, cast

# F-036 (DevPlan 016 TASK-9): node-side CLI исполняется НА ноде (python3 <workdir>/prometheus_pull.py)
# БЕЗ core на sys.path — оба core-импорта опциональны: SaturationAgg используется только в
# аннотациях (ленивые строки, from __future__ import annotations), http_client — заменяется
# stdlib-urllib-фолбэком _http_get_json_stdlib. Класс-твин зеркалит report.SaturationAgg.
if TYPE_CHECKING:
    from core.internal.loadtest.report import SaturationAgg
else:
    try:
        from core.internal.loadtest.report import SaturationAgg
    except ImportError:  # pragma: no cover — standalone node-side (no core on sys.path)

        class SaturationAgg(TypedDict, total=False):
            """Standalone twin report.SaturationAgg (F-036) — только для node-side исполнения."""

            avg: float | None
            max: float | None
            pct: float | None


try:
    from core.internal.shared import http_client as _http_client_impl  # W3.2 (177): HTTP-слой в shared/
except ImportError:  # pragma: no cover — standalone node-side (no core on sys.path)
    _http_client_impl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# W11: JSON-граница (рекурсивный JSON-тип для jsonschema-совместимых границ)
JsonValue: TypeAlias = "str | int | float | bool | Mapping[str, JsonValue] | Sequence[JsonValue] | None"

# ── Константы окна/шага (DevPlan 146 §3.4; scrape_interval: 30s global, 60s cadvisor/node-exporter) ──
_LONG_RUN_SEC: int = 180  # порог «долгого» прогона (выбор RATE_WINDOW_LONG)

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

    aggregates: dict[str, SaturationAgg] = field(default_factory=dict)
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
    window = RATE_WINDOW_LONG if run_time > _LONG_RUN_SEC else RATE_WINDOW
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
def _http_get_json(url: str, timeout: int = 15) -> dict[str, object]:
    """GET + JSON-parse через shared/http_client (единая точка для monkeypatch/DI в тестах).

    ▶ ┌url┐ → ◇ _http_client_impl None (F-036 standalone) → stdlib urllib fallback →
      → ○ http_client.get_json(timeout) → ◇ HttpRequestError → PrometheusError → ◇ HttpJsonError → PrometheusError → ⎋ dict

    ## @purpose  HTTP-слой пула: urllib через shared/http_client (requests не runtime-зависимость
    ##            платформы). Все сетевые/JSON-ошибки → PrometheusError с читаемым сообщением (exit 1).
    ##            Standalone-ветка (F-036): node-side CLI без core на sys.path → stdlib urllib
    ##            (_http_get_json_stdlib) с той же контрактной семантикой PrometheusError.
    ## @io — ⇥ url: str, timeout: int → ⎋ dict (JSON-ответ)
    ## @complexity — O(1) — один запрос
    ## @raises — PrometheusError: недоступен / не-200 / битый JSON
    ## @changes 2026-08-16 | DevPlan 177 W3.2 — транспорт мигрирован на shared/http_client.get_json
    ## @changes 2026-08-27 | DevPlan 016 TASK-9 — +stdlib-ветка (F-036 standalone node-side)
    """
    if _http_client_impl is None:
        return _http_get_json_stdlib(url, timeout)
    try:
        payload = _http_client_impl.get_json(url, timeout=timeout)
    except _http_client_impl.HttpRequestError as exc:
        msg = f"Prometheus недоступен: {exc}"
        raise PrometheusError(msg) from exc
    except _http_client_impl.HttpJsonError as exc:
        msg = f"Prometheus вернул не-JSON: {exc}"
        raise PrometheusError(msg) from exc
    # W11: json.loads → Any → dict[str, object] (граница JSON)
    return cast("dict[str, object]", payload)


# endregion FUNC__http_get_json


# region FUNC__http_get_json_stdlib
def _http_get_json_stdlib(url: str, timeout: int = 15) -> dict[str, object]:
    """Standalone stdlib urllib fallback (F-036 node-side CLI — core недоступен на ноде).

    ▶ ┌url┐ → ○ urllib.request.urlopen → ◇ URLError/Timeout/OSError → PrometheusError →
      → ○ json.loads → ◇ JSONDecodeError → PrometheusError → ◇ не dict → PrometheusError → ⎋ dict

    ## @purpose  Зеркало семантики http_client.get_json (инвариант 3 shared/http_client):
    ##            сетевые ошибки → «Prometheus недоступен», битый JSON → «не-JSON»;
    ##            HTTPError ⊂ URLError — 4xx/5xx попадает в PrometheusError автоматически.
    ##            НЕ дублирует бизнес-логику pull — только транспортный слой для node-side
    ##            исполнения (F-036): PromQL-пул и агрегация — тот же run_saturation.
    ## @io — ⇥ url: str, timeout: int → ⎋ dict (JSON-ответ)
    ## @complexity — O(B) — B = размер ответа
    ## @raises — PrometheusError: недоступен / не-JSON / не-JSON-объект
    ## @changes 2026-08-27 | DevPlan 016 TASK-9 — Created (F-036 standalone node-side)
    """
    try:
        # W11: urlopen перегружен (HTTPResponse | file-like) → Any; cast к HTTPResponse —
        #      .read() → bytes → .decode() → str (явная граница транспорта, pyright strict)
        resp = cast("http.client.HTTPResponse", urllib.request.urlopen(url, timeout=timeout))  # nosec B310 — внутренний endpoint ноды (localhost:9090), caller-обоснованный
        with resp:
            body: str = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        msg = f"Prometheus недоступен: {exc}"
        raise PrometheusError(msg) from exc
    try:
        parsed = cast("object", json.loads(body))  # W11: json.loads → Any → object (isinstance-гард ниже)
    except json.JSONDecodeError as exc:
        msg = f"Prometheus вернул не-JSON: {exc}"
        raise PrometheusError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "Prometheus вернул не-JSON-объект"
        raise PrometheusError(msg)
    return cast("dict[str, object]", parsed)


# endregion FUNC__http_get_json_stdlib


# region FUNC_discover_metric_names
def discover_metric_names(
    base_url: str, timeout: int = 15, *, http_get: Callable[..., dict[str, object]] | None = None
) -> set[str]:
    """Discovery имён метрик ноды: GET /api/v1/label/__name__/values.

    ▶ ┌base_url┐ → ○ GET label/__name__/values → ○ data[] → ⎋ set[str]

    ## @purpose  Инвариант 5: отсутствие метрики детектируется по discovery-набору
    ##            (риск R2 DevPlan 146 §7: имена отличаются от ожиданий → missing → WARN).
    ## @io — ⇥ base_url: str (http://host:9090), timeout: int,
    ##          http_get: Callable | None (W4b DI: fake-http в тестах; None = _http_get_json) → ⎋ set[str]
    ## @complexity — O(M) — M = число метрик на ноде (один запрос)
    ## @raises — PrometheusError (недоступен / неверная схема ответа)
    ## @changes 2026-08-13 | DevPlan 160 W4b — +http_get (инъекция HTTP-слоя, убирает monkeypatch _http_get_json)
    """
    get = http_get if http_get is not None else _http_get_json
    payload = get(f"{base_url}/api/v1/label/__name__/values", timeout=timeout)
    if payload.get("status") != "success":
        msg = f"label/__name__/values: status={payload.get('status')!r}, error={payload.get('error')}"
        raise PrometheusError(msg)
    names = payload.get("data")
    if not isinstance(names, list):
        msg = "label/__name__/values: data не является списком"
        raise PrometheusError(msg)
    name_list = cast("list[object]", names)  # W11: isinstance-сужение list → list[Unknown] — object-граница
    logger.info("[IMP:8][prometheus][discover] Discovered %d metric names", len(name_list))
    return {str(n) for n in name_list}


# endregion FUNC_discover_metric_names


# region FUNC_query_range
def query_range(
    base_url: str,
    query: str,
    start: float,
    end: float,
    timeout: int = 15,
    *,
    http_get: Callable[..., dict[str, object]] | None = None,
) -> list[list[tuple[float, float]]]:
    """Range-запрос /api/v1/query_range → матрица серий (ts, value).

    ▶ ┌query, [start, end]┐ → ○ GET query_range (step=30s) → ○ parse result[] → ⎋ series

    ## @purpose  Единственная точка range-запроса (шаг фиксирован QUERY_STEP=30s).
    ## @io — ⇥ base_url, query: str, start/end: float (unix), timeout: int,
    ##          http_get: Callable | None (W4b DI; None = _http_get_json)
    ##       → ⎋ list[list[(ts: float, value: float)]] — серии значений
    ## @complexity — O(S) — S = суммарное число сэмплов в ответе
    ## @raises — PrometheusError (недоступен / неверная схема ответа)
    ## @invariants
    ##   - step=QUERY_STEP ("30s") — min scrape_interval (инвариант 2)
    ##   - Нечисловое значение сэмпла ("NaN"/"Inf") → пропускается (не падает pull)
    ## @changes 2026-08-13 | DevPlan 160 W4b — +http_get (инъекция HTTP-слоя)
    """
    params = urllib.parse.urlencode({"query": query, "start": int(start), "end": int(end), "step": QUERY_STEP})
    get = http_get if http_get is not None else _http_get_json
    payload = get(f"{base_url}/api/v1/query_range?{params}", timeout=timeout)
    if payload.get("status") != "success":
        msg = f"query_range: status={payload.get('status')!r}, error={payload.get('error')}"
        raise PrometheusError(msg)
    data_obj = payload.get("data")
    if not isinstance(data_obj, dict):
        msg = "query_range: data не является mapping"
        raise PrometheusError(msg)
    result = cast("dict[str, object]", data_obj).get("result")
    if not isinstance(result, list):
        msg = "query_range: data.result не является списком"
        raise PrometheusError(msg)
    series: list[list[tuple[float, float]]] = []
    for item in cast("list[object]", result):
        if not isinstance(item, dict):
            continue
        raw_values = cast("dict[str, object]", item).get("values")
        if not isinstance(raw_values, list):
            continue
        values: list[tuple[float, float]] = []
        # W11: Prometheus-сэмплы — [ts, "value"]; cast к (str, str) — float() на рантайме
        # принимает и числа, и строки (проверка try/except сохранена — поведение не меняется)
        for ts, raw in cast("list[tuple[str, str]]", raw_values):
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value != value:  # ruff: ignore[PLR0124] — NaN-check канон (NaN != NaN)
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
    *,
    http_get: Callable[..., dict[str, object]] | None = None,
) -> SaturationResult:
    """Полный PromQL-pull: discovery → query_range × пул → агрегаты + диагностика.

    ▶ ┌base_url, run_time, t0, t1┐ → ○ discover → ○ build_queries → ○ per-query query_range
      (окно [t0-60, t1+60]) → ○ aggregate → ⊕ missing/insufficient → ⎋ SaturationResult

    ## @purpose  Секция saturation отчёта за один вызов (инварианты 3-6). Окно —
    ##            [t0-QUERY_PAD, t1+QUERY_PAD], шаг QUERY_STEP (scrape-лаг страховка).
    ## @io — ⇥ base_url: str, run_time: int (для rate-окна), t0/t1: float (unix секунды),
    ##         timeout: int, http_get: Callable | None (W4b DI; None = _http_get_json)
    ##         → ⎋ SaturationResult
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
    ## @changes 2026-08-13 | DevPlan 160 W4b — +http_get (прокидывается в discover/query_range)
    """
    window_start = t0 - QUERY_PAD
    window_end = t1 + QUERY_PAD
    discovered = discover_metric_names(base_url, timeout=timeout, http_get=http_get)
    queries = build_queries(run_time)

    aggregates: dict[str, SaturationAgg] = {}
    missing: list[str] = []
    insufficient: list[str] = []
    for name, (promql, base_metric) in queries.items():
        if base_metric not in discovered:
            if base_metric not in missing:
                missing.append(base_metric)
                logger.info("[IMP:7][prometheus][saturation] Metric missing (WARN): %s", base_metric)
            continue
        series = query_range(base_url, promql, window_start, window_end, timeout=timeout, http_get=http_get)
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
        entry: SaturationAgg = {"avg": avg, "max": max_val}
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


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI node-side PromQL pull (F-036): SaturationResult → JSON stdout, exit 0|1.

    ▶ ┌argv┐ → ○ argparse (--base-url --run-time --t0 --t1 [--timeout]) → ○ run_saturation
      → ○ print asdict JSON → ◇ PrometheusError → stderr + exit 1 → ⎋ int

    ## @purpose  Исполняемая точка pull для node-side режима (F-036, DevPlan 016 TASK-9):
    ##            runner_remote.pull_promql_node_side выполняет на ноде
    ##            `python3 <workdir>/prometheus_pull.py --base-url http://localhost:9090 ...`
    ##            (единичная read-only ssh-команда, ssh_read-семантика). stdout — чистый JSON
    ##            (dataclasses.asdict), ошибки — stderr + exit 1 (парсер на ноде не читает stderr
    ##            как данные). Инвариант main()-контракта core/AGENTS.md: sys.exit — только в __main__.
    ## @io — ⇥ argv: list[str] | None → ⎋ int (0 ok, 1 PrometheusError)
    ## @complexity — O(Q×(S + M)) — полный pull (как run_saturation)
    ## @raises — нет (PrometheusError перехватывается → exit 1)
    ## @changes 2026-08-27 | DevPlan 016 TASK-9 — Created (F-036 node-side pull)
    """
    parser = argparse.ArgumentParser(
        prog="prometheus_pull",
        description="Post-run PromQL saturation pull (F-036 node-side CLI — ssh_read, без TCP-forwarding)",
    )
    parser.add_argument("--base-url", required=True, help="Prometheus base URL (node-side: http://localhost:<port>)")
    parser.add_argument("--run-time", type=int, required=True, help="Run duration in seconds (rate-window выбор)")
    parser.add_argument("--t0", type=float, required=True, help="Run start unix timestamp (float)")
    parser.add_argument("--t1", type=float, required=True, help="Run end unix timestamp (float)")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout per Prometheus request (default: 15)")
    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    from dataclasses import dataclass

    @dataclass
    class _CliArgs:
        base_url: str
        run_time: int
        t0: float
        t1: float
        timeout: int

    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))
    try:
        result = run_saturation(args.base_url, args.run_time, args.t0, args.t1, timeout=args.timeout)
    except PrometheusError as exc:
        # T20 (ruff): print в production запрещён; *_cli.py glob-ignore неприменим (имя файла
        # фиксировано контрактом F-036) — stdout-контракт через sys.stdout/stderr.write
        sys.stderr.write(f"PrometheusError: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(asdict(result), sort_keys=True) + "\n")
    logger.info("[IMP:9][prometheus][cli] Saturation pull OK: %d aggregates", len(result.aggregates))
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
