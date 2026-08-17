# GREP_SUMMARY: conftest thin re-export layer, _conftest package, pytest_collection_modifyitems, wave-sorting
# STRUCTURE: ┌re-export _conftest.*┐ → ┌_compute_module_waves(module.yaml → wave numbers)┐ →
#            ┌pytest_collection_modifyitems(dynamic wave tagging + sort by wave)┐
# region MODULE_CONTRACT
## @purpose — Thin re-export layer for pytest conftest + Wave-Pipeline dynamic test sorting.
##            All logic lives in tests/_conftest/ package. This file re-exports public names
##            and provides pytest_collection_modifyitems for Wave-Pipeline (DevPlan 040 Wave 4).
## @scope — Re-exports public names from _conftest/__init__.py + provides collection hook
##          for dynamic wave tagging and test ordering.
## @invariants
##   - This file is <150 lines
##   - Public names from _conftest/__init__.py are re-exported via `from _conftest import *`
##   - Underscore-prefixed names used by test files are imported explicitly below
##   - pytest_collection_modifyitems is a pytest hook, NOT a fixture — auto-discovered
##   - Wave numbers are computed from core/modules/*/module.yaml (zero hardcoded numbers)
## @rationale — Wave-Pipeline (DevPlan 040 Wave 4) requires test ordering by wave number.
##              pytest_collection_modifyitems is the canonical pytest hook for this purpose.
##              Wave numbers are derived from module.yaml#depends_on, not hardcoded.
## @changes — 2026-07-12 | Rewritten as thin re-export from _conftest package (DevPlan 031)
##            2026-07-16 | T6 cleanup: removed stale underscore re-exports no test file imports
##            2026-07-22 | DevPlan 040 Wave 4: added pytest_collection_modifyitems + _compute_module_waves
##            2026-08-13 | DevPlan 160 W6 T6.3: подключён Quarantine-протокол
##                       (tests/_conftest/quarantine.py) — вызов _quarantine_collection(items)
##                       в pytest_collection_modifyitems (docker/network флак → skip, Rev-дата)
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import site

import pytest
import yaml

# ═══════════════════════════════════════════════════════════════════════════════
# CI smoke-hang диагностика (2026-08-17, platform-test): флаки-hang смоука ДО баннера
# pytest (900s, 0 вывода; run 32025761115/32029164898) происходит ДО pytest_configure —
# в import-цепочке conftest. SMOKE_HANG_PROBE=1 (CI) включает: (1) faulthandler-арм
# ПРЯМО ЗДЕСЬ (самое начало module-уровня — до импортов _conftest) — dump_traceback_later(600s)
# дампит СТЕКИ ВСЕХ ПОТОКОВ через 10 минут; (2) bisect-печати между импорт-блоками —
# последняя печать перед зависанием указывает точный модуль. Локально env не задан → no-op.
_HANG_PROBE = os.environ.get("SMOKE_HANG_PROBE") == "1"
if _HANG_PROBE:
    import faulthandler

    faulthandler.dump_traceback_later(600, exit=False)
    print("[conftest-import] begin (faulthandler armed 600s)", flush=True)


# ── Test import paths: canonical roots for all test files ────────────────────
# DevPlan 117 Brief F (T6 #47, D47-A): добавляем repo_root/, core/, core/internal/
# через site.addsitedir — общие пути, используемые >50% тестов. Это легитимизирует
# 65 индивидуальных sys.path.insert (policy-раздел в tests/AGENTS.md §sys.path policy).
# site.addsitedir идемпотентен (повторные вызовы не дублируют пути).
_PKG_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
for _p in (_PKG_ROOT, _PKG_ROOT / "core", _PKG_ROOT / "core" / "internal", _PKG_ROOT / "vendor"):
    site.addsitedir(str(_p))

from _conftest import *  # ruff: ignore[F403]

if _HANG_PROBE:
    print("[conftest-import] _conftest star done", flush=True)
from _conftest.containers import _module_container_running  # ruff: ignore[F401]  # DevPlan 170 W8: containers.py

if _HANG_PROBE:
    print("[conftest-import] containers done", flush=True)

# Also import underscore-prefixed names explicitly (not included in *)
# — autouse fixtures (needed for pytest discovery) —
from _conftest.e2e import _e2e_disable_proxy, _load_test_env  # ruff: ignore[F401]

if _HANG_PROBE:
    print("[conftest-import] e2e done", flush=True)

# — consumed by test files via `from conftest import ...` —
from _conftest.infra import _test_infra_was_active  # ruff: ignore[F401]
from _conftest.ldd import (  # ruff: ignore[F401]
    _ensure_volume_dirs,
    _handle_e2e_error,
    _print_ldd_trajectory,
)

# Quarantine-протокол (DevPlan 160 W6 T6.3): вызывается явно из pytest_collection_modifyitems
# ниже (псевдоним без pytest_-префикса — pytest НЕ регистрирует его повторно как hook).
from _conftest.quarantine import pytest_collection_modifyitems as _quarantine_collection
from _conftest.session import _fixture_schema_integrity  # ruff: ignore[F401] — autouse per-test fail (T12.5 T-8)
from _conftest.state_reset import _reset_fresh_state  # ruff: ignore[F401]
from _conftest.wave_pipeline import _ensure_wave_ready  # ruff: ignore[F401]

if _HANG_PROBE:
    print("[conftest-import] wave_pipeline done", flush=True)

logger = logging.getLogger(__name__)

# ── Wave-Pipeline: dynamic wave computation from module.yaml ────────────────
# DevPlan 040 Wave 4: Wave numbers derived from core/modules/*/module.yaml#depends_on.
# DevPlan 170 W8: волновой алгоритм — ЕДИНЫЙ канон в _conftest/shared.py (compute_module_waves).
# Дубль «must stay in sync» (smoke.py:_build_waves vs conftest.py) УДАЛЁН.


def _compute_module_waves() -> dict[str, int]:
    """Read core/modules/*/module.yaml, compute wave numbers from depends_on.

    ## @purpose — Derive wave numbers from the module dependency graph.
    ##            Wave 0: modules with no dependencies.
    ##            Wave N: modules whose max dependency wave + 1.
    ##            DevPlan 170 W8: тело делегирует ЕДИНОМУ канону _conftest/shared.py
    ##            (compute_module_waves) — дубль с smoke._build_waves удалён.
    ## @io — ⎋ dict[str, int]: {module_name: wave_number}
    ## @complexity — O(M * D) where M=modules, D=avg dependencies
    ## @invariants
    ##   - Module without depends_on → wave 0
    ##   - Module with depends_on → wave = max(dep_waves) + 1
    ##   - Unknown dependencies → wave 0 (safe default)
    ## @rationale — Dynamic computation eliminates hardcoded wave numbers.
    ##              Adding a new module with dependencies automatically adjusts
    ##              downstream wave numbers. Сигнатура () -> dict[str, int] сохранена
    ##              (импортируется tests/gates/test_gate_wave_sort_contract.py).
    """
    from _conftest.shared import compute_module_waves

    platform_root = pathlib.Path(__file__).resolve().parent.parent  # project root (tests/../)
    modules_dir = platform_root / "core" / "modules"

    mod_deps: dict[str, list[str]] = {}
    if modules_dir.is_dir():
        for entry in sorted(p.name for p in modules_dir.iterdir()):
            mod_path = modules_dir / entry
            yaml_path = mod_path / "module.yaml"
            if mod_path.is_dir() and yaml_path.is_file():
                with pathlib.Path(str(yaml_path)).open(encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                mod_deps[entry] = data.get("depends_on") or []

    return compute_module_waves(mod_deps)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tag tests with wave number and sort by wave for Wave-Pipeline.

    ## @purpose — pytest collection hook. Computes wave numbers from module.yaml,
    ##            maps test fixtures to modules via FIXTURE_TO_MODULE, tags tests
    ##            with @pytest.mark.wave(N), and sorts tests so Wave 0 runs first.
    ## @io — ⇥ items: list[pytest.Item] → ⎋ None (side-effect: markers + sort)
    ## @complexity — O(I * F) where I=items, F=fixtures per item
    ## @invariants
    ##   - Tests without any wave-mapped fixture → wave 0 (no marker added)
    ##   - Tests using platform_services → max_wave + 1 (Wave 3)
    ##   - Sorting is stable: items with same wave keep original order
    ##   - FIXTURE_TO_MODULE is a mechanical mapping (fixture→module name),
    ##     not dependency-driven — does not change with dependency changes
    ## @rationale — Wave-Pipeline needs tests ordered by dependency wave so
    ##              Wave 0 tests run while Wave 1 containers start in background.
    ##              Without sorting, test execution order would be non-deterministic,
    ##              potentially running Wave 2 tests before Wave 1 containers ready.
    """
    module_waves = _compute_module_waves()
    max_wave = max(module_waves.values()) if module_waves else 0

    # ── Quarantine-протокол (DevPlan 160 W6 T6.3): docker/network флак → skip (Rev-дата) ──
    # Валидация реестра (запись без Rev-даты = RED) + применение skip к docker/network items.
    # Пустой реестр (default) = no-op. Детерминированные слои (static/unit/gates) НЕ карантинятся.
    _quarantine_collection(items)

    # Stable mapping: fixture name → module name (mechanical, not dependency-driven)
    FIXTURE_TO_MODULE: dict[str, str | None] = {
        "redis_compose": "redis",
        "nginx_compose": "nginx",
        "clickhouse_up": "clickhouse",
        "postgres_up": "postgres",
        "pgbouncer_up": "postgres",
        "logging_compose": "logging",
        "infra_metrics_compose": "infra-metrics",
        "minio_compose": "minio",
        "litellm_up": "litellm",
        "langfuse_up": "langfuse",
        "monitoring_compose": "monitoring",
        "backup_cron_compose": "backup-cron",
        "status_page_compose": "status-page",
        "hermes_up": "hermes-agent",
        "platform_services": None,  # special: always max_wave + 1
    }

    for item in items:
        test_wave = 0
        if hasattr(item, "fixturenames"):
            for fname in item.fixturenames:
                if fname == "platform_services":
                    test_wave = max(test_wave, max_wave + 1)
                elif fname in FIXTURE_TO_MODULE and FIXTURE_TO_MODULE[fname] is not None:
                    mod = FIXTURE_TO_MODULE[fname]  # type: ignore[assignment]
                    test_wave = max(test_wave, module_waves.get(mod, 0))

        if test_wave > 0:
            item.add_marker(pytest.mark.wave(test_wave))

    # Dynamic _test_infra_was_active: detect if any collected test has requires_docker marker.
    # Set to True/False so test_conftest_isolation.py can verify static tests don't trigger Docker.
    # T12.10 (T-15): вычисление — в master на ПОЛНОЙ коллекции. pytest_collection_modifyitems
    # исполняется ТОЛЬКО в master (xdist: воркеры получают готовые items) и видит ВСЕ собранные
    # тесты (включая позже деселектед по -m). Defensive-гейт _is_xdist_worker() — модификацию
    # флага в воркере исключает даже если hook вызовется в воркере.
    from _conftest.infra import _test_infra_was_active as _infra_flag

    if _is_xdist_worker():
        logger.info(
            "%s",
            f"[IMP:7][conftest][collection] Worker {os.environ.get('PYTEST_XDIST_WORKER')} — infra-active flag not modified (master computes on full collection, T12.10 T-15)",
        )
    else:
        requires_docker = any(item.get_closest_marker("requires_docker") for item in items)
        _infra_flag.set(requires_docker)

    # ── Sort contract (T12.6 T-9) ──────────────────────────────────────────────
    # КОНТРАКТ (документирован, гейт tests/gates/test_gate_wave_sort_contract.py):
    #   items.sort(key=(wave_number, nodeid))
    #   - wave_number: @pytest.mark.wave(N) из module.yaml#depends_on (0 = нет зависимости)
    #   - nodeid: детерминированный стабильный вторичный ключ
    #   - Сортировка детерминирована: тот же набор items → тот же порядок, независимо от
    #     порядка сбора (state-leak: порядок исполнения теста зависит ТОЛЬКО от (wave, nodeid),
    #     не от состояния сессии/предыдущих прогонов)
    #   - Python list.sort стабилен: равные ключи сохраняют порядок сбора
    #   - Gate (T12.6): test_gate_wave_sort_contract проверяет, что (а) sort-ключ —
    #     чистая функция item'а, (б) сортировка идемпотентна (повторный sort = тот же порядок),
    #     (в) стабильность равных ключей
    items.sort(
        key=lambda item: (
            item.get_closest_marker("wave").args[0] if item.get_closest_marker("wave") else 0,
            item.nodeid,
        )
    )


# region FUNC_is_xdist_worker
## @purpose  Детекция xdist-воркера (PYTEST_XDIST_WORKER, DevPlan 124 T1): session/collection
##           хуки исполняются в master; воркеры получают готовые items. Гейт используется для
##           master-only мутаций (attempt-counter, docker-cleanup, _test_infra_was_active T12.10).
##           DevPlan 170 W8: ЕДИНЫЙ канон в _conftest/shared.py — здесь re-import (дубль ×3 удалён).
## @io       → ⎋ bool (True = текущий процесс — xdist-воркер)
## @complexity O(1)
from _conftest.shared import _is_xdist_worker

# endregion FUNC_is_xdist_worker


# region FUNC_hang_probe
## @purpose  CI smoke-hang диагностика (2026-08-17, platform-test): флаки-hang смоука ДО баннера
##            pytest (900s, 0 вывода, run 32025761115) происходит в фазах вне pytest-timeout
##            (pytest_sessionstart/collection). При SMOKE_HANG_PROBE=1 (CI gate-step env)
##            pytest_configure вооружает faulthandler.dump_traceback_later(600s, exit=False) —
##            дамп СТЕКОВ ВСЕХ ПОТОКОВ в stderr через 10 минут (exit=False: здоровый медленный
##            прогон не роняется; отмена в sessionfinish). Следующий hang покажет ТОЧКУ.
## @io       → ⎋ None (side-effect: faulthandler-таймер)
## @complexity O(1)
_HANG_PROBE_ARMED = False


def pytest_configure(config: object) -> None:
    """Arm faulthandler dump BEFORE sessionstart/collection when SMOKE_HANG_PROBE=1."""
    global _HANG_PROBE_ARMED  # ruff: ignore[PLW0603] — diagnostic state, single-threaded startup
    if os.environ.get("SMOKE_HANG_PROBE") == "1":
        import faulthandler

        faulthandler.dump_traceback_later(600, exit=False)
        _HANG_PROBE_ARMED = True
        logger.info("[IMP:7][hang_probe] faulthandler.dump_traceback_later(600s) armed — SMOKE_HANG_PROBE=1")
    # NOTE: cancel в pytest_sessionfinish НЕ добавляем — tests/conftest.py НЕ определяет этот
    # хук (зашэдоулил бы re-export pytest_sessionfinish из _conftest.session — counter/cleanup).
    # Таймер faulthandler не блокирует выход процесса; здоровый прогон просто не доживает до 600s.


# endregion FUNC_hang_probe
