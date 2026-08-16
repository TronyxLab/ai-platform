# GREP_SUMMARY: shared, xdist-worker, wave-algorithm, compute-module-waves, build-waves, canonical, dedup, master-guard
# STRUCTURE: ▶ _is_xdist_worker(PYTEST_XDIST_WORKER env) → ◇ compute_module_waves(graph → dict wave) → ⊕ build_waves(dict → list[list]) → ⎋ waves

# region MODULE_CONTRACT
## @purpose  Canonical shared helpers for the _conftest package: xdist-worker detection and the
##           SINGLE-SOURCE module wave algorithm. DevPlan 170 W8: deduplicates `_is_xdist_worker`
##           ×3 (counter.py:68, session.py:211, conftest.py:233) and the wave algorithm ×2
##           (smoke.py:_build_waves vs conftest.py:_compute_module_waves «must stay in sync»).
## @scope    Imported by _conftest/* submodules and tests/conftest.py. NOT re-exported to test
##           files via _conftest/__init__ (internal package helpers).
## @invariants
##   - _is_xdist_worker: PYTEST_XDIST_WORKER set = xdist-воркер; отсутствует в master
##     (стандартный контракт xdist, DevPlan 124 T1) — master-семантика session/collection хуков
##   - compute_module_waves: multi-pass алгоритм — обрабатывает НЕ-топологический вход
##     (алфавитный порядок module.yaml) И идентичен legacy single-pass на топологически
##     отсортированном графе (проверено: audit.module_graph в Kahn-порядке → одинаковые волны)
##   - compute_module_waves: модуль без deps → wave 0; неизвестный dep → wave 0 (safe fallback);
##     детерминирован (чистая функция графа)
##   - build_waves: группирует compute_module_waves в list[list[str]] с сохранением порядка
##     модулей внутри волны; пустой граф → []
## @rationale  Две копии волнового алгоритма разошлись бы («must stay in sync») — multi-pass
##             суперсет single-pass: безопасен для обоих call-site'ов (conftest на алфавитном
##             module.yaml, smoke на Kahn-sorted module_graph).
## @changes    CREATED: 2026-08-15 | DevPlan 170 W8: вынесен из smoke.py (_build_waves) и
##             conftest.py (_compute_module_waves body) + _is_xdist_worker из 3 модулей
# endregion MODULE_CONTRACT

import logging
import os

logger = logging.getLogger(__name__)


# region FUNC_is_xdist_worker
## @purpose  Детекция xdist-воркера: env PYTEST_XDIST_WORKER устанавливается pytest-xdist
##           в каждом воркере и отсутствует в master (DevPlan 124, факт 11 — стандартный
##           контракт xdist). ЕДИНЫЙ канон — ранее продублирован в counter.py:68,
##           session.py:211, conftest.py:233 (DevPlan 170 W8).
## @io       → ⎋ bool (True = текущий процесс — xdist-воркер)
## @complexity O(1)
def _is_xdist_worker() -> bool:
    """True when running inside a pytest-xdist worker (PYTEST_XDIST_WORKER set)."""
    return bool(os.environ.get("PYTEST_XDIST_WORKER"))


# endregion FUNC_is_xdist_worker


# region FUNC_compute_module_waves
## @purpose  ЕДИНЫЙ канон волнового алгоритма (DevPlan 170 W8): вычисляет номер волны каждого
##           модуля из графа зависимостей. Multi-pass — обрабатывает НЕ-топологический вход
##           (модуль.yaml читается в алфавитном порядке: deps могут ссылаться на модули,
##           обработанные позже). Заменяет оба дубля: smoke.py:_build_waves (single-pass) и
##           conftest.py:_compute_module_waves (локальный multi-pass).
## @io       ⇥ module_graph: dict[str, list[str]] {module → [deps]} → ⎋ dict[str, int] {module → wave}
## @complexity O(M²) worst-case (итерации до стабилизации), M = модули
## @invariants
##   - Модуль без deps → wave 0
##   - Модуль с deps → wave = max(волн всех deps) + 1 (после того, как ВСЕ deps назначены)
##   - Неизвестные deps (нет в графе) → волна модуля = 0 (safe fallback, как legacy conftest)
##   - Детерминизм: чистый обход dict в порядке вставки — тот же граф → тот же результат
##   - Идентичен legacy single-pass на топологически отсортированном входе (проверено
##     эмпирически на Kahn-порядке audit.module_graph: waves идентичны)
# 🧐 TRAP[DECISION] · 2026-08-15 · — · Волновой алгоритм унифицирован в multi-pass (единый канон)
# · Rejected: сохранить single-pass для smoke._build_waves (проще читается, полагается на
# ·   топологический порядок входного графа) + multi-pass для conftest — дубль «must stay in sync»
# · Reason: multi-pass — суперсет single-pass: на топологически отсортированном входе
# ·   (audit.module_graph, Kahn) результаты ИДЕНТИЧНЫ (проверено эмпирически); один канон
# ·   исключает расхождение копий (DevPlan 170 W8)
# · Rev: если появится требование волн по «глубине max(dep)+1» без учёта неизвестных deps
# ·   (single-pass семантика wave=-1 для unassigned) — параметризовать compute_module_waves
def compute_module_waves(module_graph: dict[str, list[str]]) -> dict[str, int]:
    """Compute wave numbers from a module dependency graph (single source of truth)."""
    assigned: dict[str, int] = {}
    changed = True
    while changed:
        changed = False
        for module_name, deps in module_graph.items():
            if module_name in assigned:
                continue
            if not deps:
                assigned[module_name] = 0
                changed = True
            else:
                dep_waves = [assigned.get(dep) for dep in deps]
                if all(wave is not None for wave in dep_waves):
                    assigned[module_name] = max(dep_waves) + 1  # type: ignore[type-var, arg-type]
                    changed = True

    # Fallback: неразрешённые модули (циклы/неизвестные deps) → wave 0 (safe default)
    for module_name in module_graph:
        if module_name not in assigned:
            assigned[module_name] = 0

    logger.info(
        "[IMP:8][shared][compute_module_waves] Computed waves for %d module(s): max=%d",
        len(assigned),
        max(assigned.values(), default=0),
    )
    return assigned


# endregion FUNC_compute_module_waves


# region FUNC_build_waves
## @purpose  Группировка compute_module_waves в волны list[list[str]] (семантика legacy
##           smoke.py:_build_waves): волна 0 — модули без зависимостей, волна N — модули,
##           чьи зависимости все в волнах < N. Порядок модулей внутри волны сохраняется
##           (порядок вставки графа).
## @io       ⇥ module_graph: dict[str, list[str]] → ⎋ list[list[str]] — waves
## @complexity O(M) после compute_module_waves; M = модули
## @invariants
##   - Пустой граф → []
##   - Результат сохраняет порядок модулей внутри каждой волны (детерминизм)
##   - Номер волны из compute_module_waves (единый канон)
def build_waves(module_graph: dict[str, list[str]]) -> list[list[str]]:
    """Group module_graph into waves of independent modules (wave = max(dep wave) + 1)."""
    assigned = compute_module_waves(module_graph)
    if not assigned:
        return []

    max_wave = max(assigned.values())
    waves: list[list[str]] = [[] for _ in range(max_wave + 1)]
    for module_name, wave_idx in assigned.items():
        waves[wave_idx].append(module_name)

    logger.info(
        "[IMP:8][shared][build_waves] Built %d wave(s) from %d module(s)",
        len(waves),
        len(assigned),
    )
    return waves


# endregion FUNC_build_waves
