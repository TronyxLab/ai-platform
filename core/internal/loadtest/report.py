#!/usr/bin/env python3
# GREP_SUMMARY: loadtest report locust-csv parse verdict json markdown junit stats p95 p99 error-rate tasks duration
# STRUCTURE: ▶ parse_stats_csv (header-индекс, Aggregated-строка + per-task) → ◇ verdict_smoke/verdict_regression/verdict_capacity
#           → ⊕ build_report (json: duration_s + tasks) → ○ render_markdown (Duration + tasks-таблица)
#           → ○ write_junit_xml → ⎋ report.json + сводка
# region MODULE_CONTRACT
## @purpose  Отчёт прогона нагрузки (DevPlan 146 W2 + 148 TASK-6): парс locust CSV
##           (stats/history), сборка report.json {scenario, mode, timestamp, duration_s,
##           rps, p50/p95/p99, error_rate, tasks (per-task), max_rps, saturation, verdict},
##           markdown-сводка, junit.xml (опция --junit).
##           Вердикт по контракту: PASS/WARN → exit 0, FAIL → exit 1 (shared/contracts.py).
##           duration_s (148 TASK-6/7): t1-t0 прогона — «что сколько времени выполняется»;
##           tasks (148 TASK-6/7): per-task breakdown из stats.csv (read_query/write_query —
##           скорость записи vs чтения PostgreSQL, SC_DB_RW).
## @scope    Потребитель: runner_cli.py (единственный). Чистые функции парса/вердиктов —
##           native pytest (tests/unit/test_loadtest_report.py) с CSV-фикстурами.
## @invariants
##   1. Парс CSV — по заголовкам (не по позициям): колонки локаст-версии стабильны по именам
##   2. error_rate = failures / requests (по Aggregated-строке; fallback — суммы по строкам)
##   3. Вердикты:
##      - smoke: 0 errors AND p95 < max_p95 → PASS, иначе FAIL
##      - regression: p95 <= 1.5×prev_p95 AND error <= prev+2pp AND p95 < max_p95 → PASS,
##        иначе FAIL; first_run/baseline_reset → сравнение недействительно (PASS при p95 < max_p95)
##      - capacity: max_rps > 0 → PASS, иначе FAIL (ни один шаг не успешен)
##      - WARN (missing/insufficient метрики) → PASS+WARN → exit 0 (НЕ блокирует)
##   4. Отчёт пишется atomic_write_json (канон shared/atomic_writer.py)
##   5. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
##   6. parse_stats_csv возвращает (Stats, tasks) — перцентили в СЕКУНДАХ (÷1000, BUG-3
##      146-m3), tasks — {name: {rps, p95, p99, error_rate}} для строк Name != Aggregated
##   7. duration_s/tasks — ОПЦИОНАЛЬНЫЕ параметры build_report (обратная совместимость:
##      существующие вызовы/тесты не ломаются; без них поля = None)
## @rationale Единый формат отчёта (json+markdown+junit) — потребляется оператором
##            и CI-скриптами; вердикт маппится на exit-код контракта (инвариант 9 DevPlan 146).
##            duration_s + per-task закрывают пользовательский запрос 148 («сколько времени
##            выполняется», «скорость записи vs чтения») — locust stats.csv уже содержит
##            per-task строки, расширение парсера обратно-совместимо (Aggregated остаётся stats.*).
## @changes  2026-08-11 | DevPlan 146 W2 — Created
## @changes  2026-08-12 | DevPlan 148 TASK-6 — (Stats, tasks), duration_s, tasks-таблица
# endregion MODULE_CONTRACT

from __future__ import annotations

import csv
import datetime
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from core.internal.shared.atomic_writer import atomic_write_json

logger = logging.getLogger(__name__)

# ── Канонические имена колонок stats.csv (Locust 2.x) ──
COL_REQUESTS = "Request Count"
COL_FAILURES = "Failure Count"
COL_RPS = "Requests/s"
COL_P50 = "50%"
COL_P95 = "95%"
COL_P99 = "99%"
AGGREGATED_NAME = "Aggregated"

VERDICT_PASS = "PASS"
VERDICT_WARN = "WARN"
VERDICT_FAIL = "FAIL"


# region DATA_Stats
@dataclass(frozen=True)
class Stats:
    """Агрегированная статистика прогона (из stats.csv).

    ## @purpose  Числовое ядро вердиктов (smoke/regression/capacity): rps, перцентили,
    ##            error_rate. Все поля float|None — None при отсутствии данных (битый CSV).
    ## @invariants
    ##   - error_rate ∈ [0, 1] (доля, не проценты); 0 при 0 запросов
    """

    rps: float | None = None
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    error_rate: float = 0.0
    total_requests: int = 0
    total_failures: int = 0


# endregion DATA_Stats


# region DATA_BaselineBlock
@dataclass(frozen=True)
class BaselineBlock:
    """Блок baseline в отчёте (DevPlan 146 W3): prev-строка, дельты, флаги.

    ## @purpose  Результат сравнения с previous-прогоном (baseline.py) — переносится
    ##            в report.json как "baseline": {...}.
    ## @invariants
    ##   - first_run / baseline_reset → сравнение недействительно (prev=None)
    ##   - delta_p95 — отношение new/prev; delta_error_pp — разница в процентных пунктах
    """

    prev: dict | None = None
    delta_p95: float | None = None
    delta_error_pp: float | None = None
    first_run: bool = False
    baseline_reset: bool = False
    regression_fail: bool = False


# endregion DATA_BaselineBlock


# region FUNC_parse_stats_csv
def parse_stats_csv(path: str | Path) -> tuple[Stats, dict[str, dict]]:
    """Парс locust stats.csv (header-based) → (агрегированная статистика, per-task словарь).

    ▶ ┌path┐ → ○ csv.DictReader → ◇ Aggregated-строка → Stats | → ⊕ строки Name != Aggregated
      → tasks[name] = {rps, p95, p99, error_rate} → ⎋ (Stats, tasks)

    ## @purpose  Парс CSV-фикстуры локаста (invariant 1: по именам колонок). Строка
    ##            "Aggregated" (Type="", Name="Aggregated") — итоги; fallback — суммы.
    ##            Per-task строки (read_query/write_query, "/", "/status"...) — словарь
    ##            tasks (148 TASK-6): скорость записи vs чтения PostgreSQL (SC_DB_RW).
    ## @io — ⇥ path: str | Path → ⎋ (Stats, tasks: dict[str, dict]) — tasks[name] =
    ##         {"rps": float|None, "p95": float|None (s), "p99": float|None (s),
    ##          "error_rate": float} для каждой строки с непустым Name != Aggregated
    ## @complexity — O(R) — R = строк CSV
    ## @invariants
    ##   - Несуществующий файл / 0 запросов → (Stats с нулями, {}) — rps/p95=None (insufficient)
    ##   - Нечисловые ячейки перцентилей → None (не роняем отчёт на мусорной строке)
    ##   - ЕДИНИЦЫ (BUG-3, 146-m3): locust отдаёт перцентили (50%/95%/99%) в МИЛЛИСЕКУНДАХ
    ##     (270 = 270ms); Stats и tasks нормализуются в СЕКУНДЫ (÷1000) — совместимо с
    ##     порогами SoT (max_p95/max_p99 — s), baseline history и verdict-функциями
    ##   - tasks.error_rate — по собственной строке задачи (failures/requests задачи)
    """
    p = Path(path)
    if not p.is_file():
        logger.error("[IMP:10][report][parse_stats_csv] CSV не найден: %s", p)
        return Stats(), {}
    try:
        with open(p, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error) as exc:
        logger.error("[IMP:10][report][parse_stats_csv] Ошибка чтения CSV %s: %s", p, exc)
        return Stats(), {}

    aggregated: dict | None = None
    tasks: dict[str, dict] = {}
    total_requests = 0
    total_failures = 0
    for row in rows:
        name = row.get("Name", "").strip()
        if name == AGGREGATED_NAME:
            aggregated = row
        elif name:
            # Per-task строка (read_query/write_query для db — 148 TASK-6): свой rps/p95/p99
            # и error_rate по собственной строке (перцентили ms → s, BUG-3 146-m3).
            task_reqs = _int_or(row.get(COL_REQUESTS), 0)
            task_fails = _int_or(row.get(COL_FAILURES), 0)
            tasks[name] = {
                "rps": _float_or(row.get(COL_RPS)),
                "p95": _ms_to_s(_float_or(row.get(COL_P95))),
                "p99": _ms_to_s(_float_or(row.get(COL_P99))),
                "error_rate": (task_fails / task_reqs) if task_reqs > 0 else 0.0,
            }
        total_requests += _int_or(row.get(COL_REQUESTS), 0)
        total_failures += _int_or(row.get(COL_FAILURES), 0)
    # Aggregated-строка уже содержит итоги — при её наличии суммы по строкам
    # задваивают счётчики (per-endpoint + total); используем её значения.
    if aggregated is not None:
        total_requests = _int_or(aggregated.get(COL_REQUESTS), 0)
        total_failures = _int_or(aggregated.get(COL_FAILURES), 0)

    source: dict = aggregated or (rows[-1] if rows else {})
    stats = Stats(
        rps=_float_or(source.get(COL_RPS)),
        p50=_ms_to_s(_float_or(source.get(COL_P50))),
        p95=_ms_to_s(_float_or(source.get(COL_P95))),
        p99=_ms_to_s(_float_or(source.get(COL_P99))),
        error_rate=(total_failures / total_requests) if total_requests > 0 else 0.0,
        total_requests=total_requests,
        total_failures=total_failures,
    )
    logger.info(
        "[IMP:9][report][parse_stats_csv] rps=%s p95=%ss p99=%ss errors=%d/%d tasks=%s",
        stats.rps,
        stats.p95,
        stats.p99,
        stats.total_failures,
        stats.total_requests,
        sorted(tasks),
    )
    return stats, tasks


# endregion FUNC_parse_stats_csv


# region FUNC__int_or
def _int_or(value: object, default: int) -> int:
    """int(value) с fallback (мусорная ячейка → default)."""
    try:
        return int(str(value).strip() or default)
    except (TypeError, ValueError):
        return default


# endregion FUNC__int_or


# region FUNC__float_or
def _float_or(value: object) -> float | None:
    """float(value) с fallback None (пустая/мусорная ячейка перцентиля)."""
    try:
        raw = str(value).strip()
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


# endregion FUNC__float_or


# region FUNC__ms_to_s
def _ms_to_s(value: float | None) -> float | None:
    """Нормализация перцентиля locust: миллисекунды → секунды (None → None).

    ▶ ┌value┐ → ◇ None → None → ⎋ value / 1000

    ## @purpose  locust stats.csv отдаёт перцентили (50%/95%/99%) в МИЛЛИСЕКУНДАХ
    ##            (270 = 270ms); Stats нормализуется в СЕКУНДЫ — совместимо с
    ##            порогами SoT (max_p95/max_p99 — s), baseline history и
    ##            verdict-функциями (BUG-3, 146-m3: боевой прогон FAIL при p95=270ms
    ##            против max_p95=1.0s).
    ## @io — ⇥ value: float | None (ms) → ⎋ float | None (s)
    ## @complexity — O(1)
    """
    return value / 1000.0 if value is not None else None


# endregion FUNC__ms_to_s


# region FUNC_verdict_smoke
def verdict_smoke(stats: Stats, max_p95: float) -> str:
    """Smoke-вердикт: 0 errors AND p95 < max_p95 → PASS, иначе FAIL.

    ▶ ┌stats, max_p95┐ → ◇ error_rate != 0 → FAIL → ◇ p95 >= max_p95 → FAIL → ⎋ PASS

    ## @purpose  Критерий smoke (DevPlan 146 §3.3): после деплоя/обновления — ноль ошибок
    ##            и p95 под порогом. FAIL → exit 1.
    ## @io — ⇥ stats: Stats, max_p95: float (s) → ⎋ "PASS" | "FAIL"
    ## @complexity — O(1)
    ## @invariants
    ##   - p95 None (нет данных) → FAIL (прогон не дал статистики — не PASS)
    """
    if stats.error_rate != 0.0:
        return VERDICT_FAIL
    if stats.p95 is None or stats.p95 >= max_p95:
        return VERDICT_FAIL
    return VERDICT_PASS


# endregion FUNC_verdict_smoke


# region FUNC_verdict_regression
def verdict_regression(stats: Stats, max_p95: float, baseline: BaselineBlock) -> str:
    """Regression-вердикт: p95 < max_p95 AND НЕ regression_fail (дельта-пороги из baseline).

    ▶ ┌stats, max_p95, baseline┐ → ◇ p95 None | >= max_p95 → FAIL → ◇ first_run/reset/нет prev
      → PASS → ◇ regression_fail (1.5× p95 | +2pp error, вычислено baseline.py) → FAIL → ⎋ PASS

    ## @purpose  Критерий regression (DevPlan 146 §3.3): ежемесячное сравнение по датам.
    ##            Пороговые дельты (baseline_delta_p95=1.5, baseline_delta_error_pp=2.0)
    ##            применяются в baseline.compare_previous (владеет prev-строкой и порогами
    ##            из SoT) → verdict использует готовый regression_fail (единый источник).
    ## @io — ⇥ stats: Stats, max_p95: float, baseline: BaselineBlock (prev + regression_fail)
    ##       → ⎋ "PASS" | "FAIL"
    ## @complexity — O(1)
    ## @invariants
    ##   - first_run / baseline_reset (нет валидного prev) → PASS только при p95 < max_p95
    ##   - regression_fail=True → FAIL (порог 1.5× p95 ИЛИ +2pp error превышен, AC2)
    """
    if stats.p95 is None or stats.p95 >= max_p95:
        return VERDICT_FAIL
    if baseline.first_run or baseline.baseline_reset or baseline.prev is None:
        return VERDICT_PASS
    if baseline.regression_fail:
        logger.info("[IMP:9][report][verdict_regression] FAIL: delta-порог регрессии превышен")
        return VERDICT_FAIL
    return VERDICT_PASS


# endregion FUNC_verdict_regression


# region FUNC_verdict_capacity
def verdict_capacity(max_rps: int) -> str:
    """Capacity-вердикт: max_rps > 0 → PASS, иначе FAIL.

    ▶ ┌max_rps┐ → ◇ 0 (ни один шаг не успешен) → FAIL → ⎋ PASS

    ## @purpose  Критерий capacity (DevPlan 146 §3.3): найден максимальный успешный RPS.
    ## @io — ⇥ max_rps: int → ⎋ "PASS" | "FAIL"
    ## @complexity — O(1)
    """
    return VERDICT_PASS if max_rps > 0 else VERDICT_FAIL


# endregion FUNC_verdict_capacity


# region FUNC_apply_warnings
def apply_warnings(verdict: str, warnings: list[str]) -> str:
    """PASS + warnings → WARN (WARN не блокирует: exit 0).

    ▶ ┌verdict, warnings┐ → ◇ FAIL → FAIL → ◇ warnings → WARN → ⎋ verdict

    ## @purpose  Инвариант 3: missing/insufficient метрики — WARN при PASS-основе,
    ##            FAIL не понижается (регрессия/ошибки важнее диагностики метрик).
    ## @io — ⇥ verdict: str, warnings: list[str] → ⎋ str
    ## @complexity — O(1)
    """
    if verdict == VERDICT_FAIL or not warnings:
        return verdict
    return VERDICT_WARN


# endregion FUNC_apply_warnings


# region FUNC_build_report
def build_report(
    *,
    scenario: str,
    mode: str,
    node: str,
    endpoint: str,
    version: str,
    stats: Stats,
    saturation_aggregates: dict[str, dict[str, float | None]] | None = None,
    missing_metrics: list[str] | None = None,
    insufficient_metrics: list[str] | None = None,
    baseline: BaselineBlock | None = None,
    max_rps: int | None = None,
    capacity_profile: list[dict] | None = None,
    duration_s: float | None = None,
    tasks: dict | None = None,
    verdict: str,
    warnings: list[str] | None = None,
    timestamp: str | None = None,
) -> dict:
    """Сборка полного report.json (единая структура для всех режимов).

    ▶ ┌поля┐ → ⊕ dict {scenario, mode, ..., duration_s, tasks, saturation, baseline, verdict} → ⎋ report dict

    ## @purpose  Единый формат отчёта (DevPlan 146 §3.5 + 148 TASK-6): json + markdown +
    ##            junit из одного источника. Все секции опциональны — режим определяет
    ##            заполнение. duration_s (t1-t0) и tasks (per-task breakdown) — новые поля 148.
    ## @io — ⇥ именованные поля (duration_s/tasks — ОПЦИОНАЛЬНЫ, обратная совместимость)
    ##       → ⎋ dict (готов к atomic_write_json)
    ## @complexity — O(K) — K = полей
    ## @invariants
    ##   - verdict ∈ {PASS, WARN, FAIL}; warnings — человекочитаемые причины WARN
    ##   - capacity_profile — список шагов {step, rps, p95, p99, error_rate, success, reason}
    ##   - duration_s: float (s) | None; tasks: dict {name: {rps, p95, p99, error_rate}} | None
    ##   - Без duration_s/tasks (None) — поля в report.json = null (обратная совместимость)
    """
    report: dict = {
        "scenario": scenario,
        "mode": mode,
        "node": node,
        "endpoint": endpoint,
        "timestamp": timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "version": version,
        "duration_s": duration_s,
        "stats": {
            "rps": stats.rps,
            "p50": stats.p50,
            "p95": stats.p95,
            "p99": stats.p99,
            "error_rate": stats.error_rate,
            "total_requests": stats.total_requests,
            "total_failures": stats.total_failures,
        },
        "tasks": tasks,
        "saturation": saturation_aggregates or {},
        "missing_metrics": missing_metrics or [],
        "insufficient_metrics": insufficient_metrics or [],
        "baseline": {
            "prev": baseline.prev if baseline else None,
            "delta_p95": baseline.delta_p95 if baseline else None,
            "delta_error_pp": baseline.delta_error_pp if baseline else None,
            "first_run": baseline.first_run if baseline else False,
            "baseline_reset": baseline.baseline_reset if baseline else False,
        },
        "max_rps": max_rps,
        "capacity_profile": capacity_profile or [],
        "verdict": verdict,
        "warnings": warnings or [],
    }
    logger.info("[IMP:9][report][build_report] verdict=%s scenario=%s mode=%s", verdict, scenario, mode)
    return report


# endregion FUNC_build_report


# region FUNC_render_markdown
def render_markdown(report: dict) -> str:
    """Markdown-сводка отчёта (stdout оператору + файл в load-results/).

    ▶ ┌report┐ → ○ строки сводки → ○ saturation-таблица → ○ baseline-блок → ⎋ md-строка

    ## @purpose  Человекочитаемая сводка (AC1: «markdown-сводка»). Не дублирует
    ##            report.json — отображает ключевые поля и диагностику WARN.
    ##            Duration (t1-t0) и per-task таблица (read_query/write_query — 148 TASK-6).
    ## @io — ⇥ report: dict → ⎋ str
    ## @complexity — O(S + T) — S = число метрик saturation, T = число per-task строк
    """
    stats = report.get("stats", {})
    lines = [
        f"# Load test report — {report.get('scenario')} ({report.get('mode')})",
        "",
        f"- Node: `{report.get('node')}` · Endpoint: `{report.get('endpoint')}`",
        f"- Timestamp: `{report.get('timestamp')}` · Version: `{report.get('version')}`",
        f"- **Verdict: `{report.get('verdict')}`**",
        "",
        "## Stats",
        "",
        "| rps | p50 | p95 | p99 | error_rate | requests | failures |",
        "|-----|-----|-----|-----|-----------|----------|-----------|",
        f"| {_fmt(stats.get('rps'))} | {_fmt(stats.get('p50'))} | {_fmt(stats.get('p95'))} | "
        f"{_fmt(stats.get('p99'))} | {_fmt(stats.get('error_rate'))} | {stats.get('total_requests')} | "
        f"{stats.get('total_failures')} |",
    ]
    if report.get("duration_s") is not None:
        lines += ["", f"- **Duration: `{_fmt(report.get('duration_s'))}s`**"]
    if report.get("tasks"):
        lines += [
            "",
            "## Tasks (per-task)",
            "",
            "| task | rps | p95 | p99 | error_rate |",
            "|------|-----|-----|-----|-----------|",
        ]
        for name, task_stats in sorted(report["tasks"].items()):
            lines.append(
                f"| {name} | {_fmt(task_stats.get('rps'))} | {_fmt(task_stats.get('p95'))} | "
                f"{_fmt(task_stats.get('p99'))} | {_fmt(task_stats.get('error_rate'))} |"
            )
    if report.get("max_rps") is not None:
        lines += ["", f"- **max_rps (capacity): `{report.get('max_rps')}`**"]
    if report.get("capacity_profile"):
        lines += [
            "",
            "## Capacity profile",
            "",
            "| step rps | rps | p99 | error_rate | success | reason |",
            "|----------|-----|-----|-----------|---------|--------|",
        ]
        for step in report["capacity_profile"]:
            lines.append(
                f"| {step.get('step')} | {_fmt(step.get('rps'))} | {_fmt(step.get('p99'))} | "
                f"{_fmt(step.get('error_rate'))} | {step.get('success')} | {step.get('reason') or '—'} |"
            )
    if report.get("saturation"):
        lines += [
            "",
            "## Saturation (PromQL post-run)",
            "",
            "| metric | avg | max | pct |",
            "|--------|-----|-----|-----|",
        ]
        for name, agg in sorted(report["saturation"].items()):
            lines.append(f"| {name} | {_fmt(agg.get('avg'))} | {_fmt(agg.get('max'))} | {_fmt(agg.get('pct'))} |")
    if report.get("missing_metrics"):
        lines += ["", f"- ⚠️ Missing metrics (WARN): {', '.join(report['missing_metrics'])}"]
    if report.get("insufficient_metrics"):
        lines += ["", f"- ⚠️ Insufficient metrics (WARN): {', '.join(report['insufficient_metrics'])}"]
    baseline = report.get("baseline") or {}
    if baseline.get("prev") is not None:
        lines += [
            "",
            "## Baseline",
            "",
            f"- prev p95: `{_fmt(baseline.get('prev', {}).get('p95'))}` · delta_p95: `{_fmt(baseline.get('delta_p95'))}`",
            f"- prev error: `{_fmt(baseline.get('prev', {}).get('error_rate'))}` · delta: `{_fmt(baseline.get('delta_error_pp'))}pp`",
        ]
    if baseline.get("first_run"):
        lines += ["", "- ℹ️ First run — baseline записан, сравнение недоступно"]
    if baseline.get("baseline_reset"):
        lines += ["", "- ℹ️ baseline_reset: нода пересоздана (host изменился) — сравнение недействительно"]
    if report.get("warnings"):
        lines += ["", "## Warnings", ""]
        lines += [f"- ⚠️ {w}" for w in report["warnings"]]
    return "\n".join(lines) + "\n"


# endregion FUNC_render_markdown


# region FUNC__fmt
def _fmt(value: object) -> str:
    """Формат числа для markdown (None → "—", float → округление до 3 знаков)."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


# endregion FUNC__fmt


# region FUNC_write_junit_xml
def write_junit_xml(report: dict, path: str | Path) -> None:
    """Запись junit.xml (опция --junit): 1 testsuite, 1 testcase, failure при FAIL.

    ▶ ┌report, path┐ → ○ ET.Element testsuite → ○ testcase (verdict) → ◇ FAIL → failure → ○ write → ⎋

    ## @purpose  CI-интеграция (DevPlan 146 W3: «junit.xml (опция --junit)») — прогон
    ##            представим как тест-сьют для внешних пайплайнов.
    ## @io — ⇥ report: dict, path: str | Path → ⎋ None (пишет файл)
    ## @complexity — O(1)
    """
    root = ET.Element(
        "testsuite",
        {
            "name": f"load-test-{report.get('scenario')}-{report.get('mode')}",
            "tests": "1",
            "failures": "1" if report.get("verdict") == VERDICT_FAIL else "0",
        },
    )
    case = ET.SubElement(root, "testcase", {"name": f"{report.get('scenario')} ({report.get('mode')})"})
    if report.get("verdict") == VERDICT_FAIL:
        failure = ET.SubElement(case, "failure", {"message": f"verdict={VERDICT_FAIL}"})
        failure.text = json.dumps(report, ensure_ascii=False, indent=2)
    tree = ET.ElementTree(root)
    tree.write(str(path), encoding="utf-8", xml_declaration=True)
    logger.info("[IMP:8][report][write_junit_xml] junit.xml → %s", path)


# endregion FUNC_write_junit_xml


# region FUNC_write_report_json
def write_report_json(report: dict, path: str | Path) -> None:
    """Атомарная запись report.json (канон shared/atomic_writer.py, инвариант 4).

    ▶ ┌report, path┐ → ○ atomic_write_json → ⎋ None

    ## @purpose  Единая точка записи отчёта — atomic_write_json (fsync + os.replace,
    ##            DevPlan 119 E5): читатель никогда не видит частичный report.json.
    ## @io — ⇥ report: dict, path: str | Path → ⎋ None
    ## @complexity — O(R) — сериализация отчёта
    """
    atomic_write_json(path, report)
    logger.info("[IMP:8][report][write_report_json] report.json → %s", path)


# endregion FUNC_write_report_json
