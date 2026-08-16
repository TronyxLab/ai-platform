#!/usr/bin/env python3
# GREP_SUMMARY: loadtest baseline history json compare regression delta p95 error thresholds host-reset
# STRUCTURE: ▶ load_history → ◇ append_run (atomic) → ◇ compare_previous (same-mode prev, host-детекция,
#           delta_p95=ratio, delta_error_pp=pp) → ⊕ baseline_reset/first_run флаги → ⎋ BaselineComparison
# region MODULE_CONTRACT
## @purpose  Baseline-хранилище и сравнение прогонов (DevPlan 146 W3): компактный
##           history.json в core/loadtest/history/<node>/<scenario>/ (КОММИТИТСЯ в репо —
##           вне load-results/, инвариант 6), поле host для детекции пересоздания тестовой
##           VPS (инвариант 9 платформы), сравнение с previous-прогоном того же режима,
##           дельты p95 (множитель 1.5×) и error_rate (+2pp) из SoT-порогов.
## @scope    Потребитель: runner_cli.py (единственный). Чистые функции — native pytest
##           (tests/unit/test_loadtest_baseline.py), tmp_path фикстуры.
## @invariants
##   1. history.json формат: {"runs": [{ts, host, mode, rps, p50, p95, p99, error_rate,
##      max_rps, verdict, delta_vs_prev, version}]} — компактные строки, diff-читаемость
##   2. Запись — atomic_write_json (канон shared/atomic_writer.py, DevPlan 119 E5)
##   3. previous = последний прогон ТОГО ЖЕ режима (smoke-90s vs regression-300s —
##      несравнимы); нет previous → first_run (PASS, пометка «first run»)
##   4. Смена host относительно previous → baseline_reset («node recreated», инвариант 9):
##      сравнение недействительно — вердикт НЕ FAIL, PASS с пометкой (мусорное сравнение
##      с другим железом исключено, DevPlan 146 §3.5)
##   5. Регрессионные пороги из SoT: delta_p95 > 1.5 → regression_fail;
##      delta_error_pp > +2.0 → regression_fail (деление на ноль исключено)
##   6. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
## @rationale Baseline в репо (а не в load-results/) — простой .gitignore
##            (load-results/ целиком, без negate-паттернов, инвариант 6); компактный
##            формат — вердикт и дельты видны в git diff (риск R6: дрейф при ручных
##            правках — митигирован регенерацией только через прогон).
## @changes  2026-08-11 | DevPlan 146 W3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

from core.internal.shared.atomic_writer import atomic_write_json

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "history.json"


# region DATA_HistoryRun
class HistoryRun(TypedDict, total=False):
    """Строка history.json (граница JSON; компактный формат, diff-читаемость).

    ## @purpose  Тип строки прогона в core/loadtest/history/<node>/<scenario>/history.json —
    ##            единый носитель baseline (потребляется compare_previous и render-блоками).
    ## @invariants
    ##   - ts — ISO-время прогона; host — идентификатор ноды (детекция пересоздания VPS)
    ##   - tasks — opaque (per-task breakdown, 148 TASK-7): детализированный тип не нужен
    ##     для сравнения (p95/error_rate — скалярные поля строки)
    """

    ts: str
    host: str
    mode: str
    duration_s: float
    tasks: object
    rps: float | None
    p50: float | None
    p95: float | None
    p99: float | None
    error_rate: float
    max_rps: int | None
    verdict: str
    delta_vs_prev: dict[str, float | None] | None
    version: str


# endregion DATA_HistoryRun


# region DATA_BaselineComparison
@dataclass(frozen=True)
class BaselineComparison:
    """Результат сравнения с previous-прогоном (блок baseline отчёта).

    ## @purpose  Вход для report.verdict_regression: prev-строка, дельты и флаги
    ##            недействительности сравнения (first_run / baseline_reset).
    ## @invariants
    ##   - first_run XOR baseline_reset XOR (prev is not None) — ровно одно состояние
    ##   - regression_fail — пороговое превышение (1.5× p95 ИЛИ +2pp error) при валидном prev
    ##   - delta_p95 = new/prev (ratio); delta_error_pp = (new−prev)×100
    """

    prev: HistoryRun | None = None
    delta_p95: float | None = None
    delta_error_pp: float | None = None
    first_run: bool = False
    baseline_reset: bool = False
    regression_fail: bool = False


# endregion DATA_BaselineComparison


# region FUNC_load_history
def load_history(history_dir: str | Path) -> list[HistoryRun]:
    """Чтение history.json → список прогонов ([] при отсутствии файла).

    ▶ ┌history_dir┐ → ◇ файла нет → [] → ◇ битый JSON → ConfigParseError(3) → ⎋ runs

    ## @purpose  Единая точка чтения baseline. Отсутствие файла — НЕ ошибка (первый
    ##            прогон), битый JSON — конфигурационная ошибка (exit 3 по контракту).
    ## @io — ⇥ history_dir: str | Path (core/loadtest/history/<node>/<scenario>/)
    ##       → ⎋ list[dict] (строки runs; порядок хронологический)
    ## @complexity — O(R) — R = прогонов
    ## @raises — ConfigParseError: history.json существует, но битый JSON
    """
    from core.internal.shared.exceptions import ConfigParseError

    path = Path(history_dir) / HISTORY_FILENAME
    if not path.is_file():
        logger.info("[IMP:7][baseline][load_history] history.json отсутствует: %s (первый прогон)", path)
        return []
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = cast("object", json.load(f))  # W11: json.load → Any → object-граница
    except json.JSONDecodeError as exc:
        msg = f"history.json битый JSON ({path}): {exc}"
        raise ConfigParseError(msg) from exc
    if isinstance(data, dict):
        data_dict = cast("dict[str, object]", data)
        runs_raw: object = data_dict.get("runs", [])
    else:
        runs_raw = []
    if not isinstance(runs_raw, list):
        msg = f"history.json ({path}): runs не является списком"
        raise ConfigParseError(msg)
    runs = cast("list[HistoryRun]", runs_raw)  # W11: json → object → list[HistoryRun] (граница JSON)
    logger.info("[IMP:8][baseline][load_history] %d run(s) in %s", len(runs), path)
    return runs


# endregion FUNC_load_history


# region FUNC_append_run
def append_run(history_dir: str | Path, run: HistoryRun) -> None:
    """Добавление строки прогона в history.json (атомарно).

    ▶ ┌history_dir, run┐ → ○ load_history → ⊕ append → ○ atomic_write_json → ⎋ None

    ## @purpose  Единственный writer baseline (инвариант 2): атомарная запись,
    ##            компактные строки. Репо-коммит подразумевается оператором.
    ## @io — ⇥ history_dir, run: dict {ts, host, mode, rps, p50, p95, p99, error_rate,
    ##         max_rps, verdict, delta_vs_prev, version} → ⎋ None
    ## @complexity — O(R) — R = прогонов
    ## @invariants
    ##   - Чтение-модификация-запись целиком в атомарном writer (нет partial-файла)
    ##   - Формат файла сохраняется: {"runs": [...]}
    """
    path = Path(history_dir)
    path.mkdir(parents=True, exist_ok=True)
    runs = load_history(path)
    runs.append(run)
    atomic_write_json(path / HISTORY_FILENAME, {"runs": runs})
    logger.info("[IMP:9][baseline][append_run] Appended run ts=%s mode=%s → %s", run.get("ts"), run.get("mode"), path)


# endregion FUNC_append_run


# region FUNC_compare_previous
def compare_previous(
    runs: Sequence[HistoryRun],
    mode: str,
    current: Mapping[str, object],
    host: str,
    delta_p95_mult: float = 1.5,
    delta_error_pp: float = 2.0,
) -> BaselineComparison:
    """Сравнение с previous-прогоном того же режима (пороги регрессии из SoT).

    ▶ ┌runs, mode, current, host┐ → ○ prev = last same-mode run → ◇ нет → first_run
      → ◇ prev.host != host → baseline_reset → ○ delta_p95 = new/prev → ○ delta_error_pp
      → ◇ пороги → regression_fail → ⎋ BaselineComparison

    ## @purpose  Ядро regression-сравнения (DevPlan 146 §3.3/§3.5): дельты + флаги
    ##            недействительности. Чистая функция — детерминированные unit-тесты.
    ## @io — ⇥ runs: list[dict] (хронологический), mode: str, current: dict (текущий
    ##         прогон {p95, error_rate, ...}), host: str (идентификатор ноды),
    ##         delta_p95_mult: float (1.5×), delta_error_pp: float (2.0pp)
    ##       → ⎋ BaselineComparison
    ## @complexity — O(R) — R = прогонов (поиск last same-mode)
    ## @invariants
    ##   - previous = последний прогон того же mode (smoke-90s vs regression-300s несравнимы)
    ##   - prev.host != host → baseline_reset=True, prev=None (сравнение отброшено)
    ##   - prev_p95 == 0/None → delta_p95=None (нет сравнения p95, только error)
    ##   - regression_fail = delta_p95 > mult ИЛИ delta_error_pp > порог (при валидных дельтах)
    """
    prev: HistoryRun | None = None
    for run in runs:
        if run.get("mode") == mode:
            prev = run
    if prev is None:
        logger.info("[IMP:8][baseline][compare] first_run (no previous %s run)", mode)
        return BaselineComparison(first_run=True)

    prev_host = str(prev.get("host", "") or "")
    if prev_host and prev_host != host:
        logger.info(
            "[IMP:9][baseline][compare] baseline_reset: prev host=%s != current host=%s (node recreated)",
            prev_host,
            host,
        )
        return BaselineComparison(baseline_reset=True)

    computed_delta_p95: float | None = None
    prev_p95 = _float_or(prev.get("p95"))
    new_p95 = _float_or(current.get("p95"))
    if prev_p95 is not None and prev_p95 > 0 and new_p95 is not None:
        computed_delta_p95 = new_p95 / prev_p95

    computed_delta_error_pp: float | None = None
    prev_error = _float_or(prev.get("error_rate"))
    new_error = _float_or(current.get("error_rate"))
    if prev_error is not None and new_error is not None:
        computed_delta_error_pp = (new_error - prev_error) * 100.0

    regression_fail = (computed_delta_p95 is not None and computed_delta_p95 > delta_p95_mult) or (
        computed_delta_error_pp is not None and computed_delta_error_pp > delta_error_pp
    )
    logger.info(
        "[IMP:9][baseline][compare] delta_p95=%s delta_error_pp=%s regression_fail=%s",
        computed_delta_p95,
        computed_delta_error_pp,
        regression_fail,
    )
    return BaselineComparison(
        prev=prev,
        delta_p95=computed_delta_p95,
        delta_error_pp=computed_delta_error_pp,
        regression_fail=regression_fail,
    )


# endregion FUNC_compare_previous


# region FUNC__float_or
def _float_or(value: object) -> float | None:
    """float(value) с fallback None (пустая/мусорная ячейка)."""
    try:
        raw = str(value).strip()
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


# endregion FUNC__float_or
