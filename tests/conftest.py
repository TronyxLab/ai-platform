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
# endregion MODULE_CONTRACT

import os
import pathlib

import pytest
import yaml
from _conftest import *  # noqa: F403

# Also import underscore-prefixed names explicitly (not included in *)
# — autouse fixtures (needed for pytest discovery) —
from _conftest.e2e import _e2e_disable_proxy, _load_test_env  # noqa: F401

# — consumed by test files via `from conftest import ...` —
from _conftest.infra import _test_infra_was_active  # noqa: F401
from _conftest.ldd import (  # noqa: F401
    _ensure_volume_dirs,
    _handle_e2e_error,
    _print_ldd_trajectory,
)
from _conftest.smoke import _module_container_running  # noqa: F401
from _conftest.state_reset import _reset_fresh_state  # noqa: F401
from _conftest.wave_pipeline import _ensure_wave_ready  # noqa: F401

# ── Wave-Pipeline: dynamic wave computation from module.yaml ────────────────
# DevPlan 040 Wave 4: Wave numbers derived from core/modules/*/module.yaml#depends_on.
# Same algorithm as _build_waves() in smoke.py — must stay in sync.


def _compute_module_waves() -> dict[str, int]:
    """Read core/modules/*/module.yaml, compute wave numbers from depends_on.

    ## @purpose — Derive wave numbers from the module dependency graph.
    ##            Wave 0: modules with no dependencies.
    ##            Wave N: modules whose max dependency wave + 1.
    ##            Same algorithm as smoke.py::_build_waves().
    ## @io — ⎋ dict[str, int]: {module_name: wave_number}
    ## @complexity — O(M * D) where M=modules, D=avg dependencies
    ## @invariants
    ##   - Module without depends_on → wave 0
    ##   - Module with depends_on → wave = max(dep_waves) + 1
    ##   - Unknown dependencies → wave 0 (safe default)
    ## @rationale — Dynamic computation eliminates hardcoded wave numbers.
    ##              Adding a new module with dependencies automatically adjusts
    ##              downstream wave numbers.
    """
    platform_root = pathlib.Path(__file__).resolve().parent.parent  # project root (tests/../)
    modules_dir = platform_root / "core" / "modules"

    mod_deps: dict[str, list[str]] = {}
    if modules_dir.is_dir():
        for entry in sorted(os.listdir(str(modules_dir))):
            mod_path = modules_dir / entry
            yaml_path = mod_path / "module.yaml"
            if mod_path.is_dir() and yaml_path.is_file():
                with open(str(yaml_path)) as f:
                    data = yaml.safe_load(f)
                mod_deps[entry] = data.get("depends_on") or []

    # Multi-pass algorithm: iterate until no new assignments.
    # Needed because module dependencies may reference modules processed later
    # (alphabetical order may not match topological order).
    assigned: dict[str, int] = {}
    changed = True
    while changed:
        changed = False
        for mod, deps in mod_deps.items():
            if mod in assigned:
                continue
            if not deps:
                assigned[mod] = 0
                changed = True
            else:
                # Check if all dependencies have been assigned
                dep_waves = [assigned.get(d) for d in deps]
                if all(w is not None for w in dep_waves):
                    assigned[mod] = max(dep_waves) + 1  # type: ignore[type-var, arg-type]
                    changed = True

    # Fallback: any remaining unassigned modules → wave 0
    for mod in mod_deps:
        if mod not in assigned:
            assigned[mod] = 0

    return assigned


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
    from _conftest.infra import _test_infra_was_active as _infra_flag

    _requires_docker = any(item.get_closest_marker("requires_docker") for item in items)
    _infra_flag.set(_requires_docker)

    items.sort(
        key=lambda item: (
            item.get_closest_marker("wave").args[0] if item.get_closest_marker("wave") else 0,
            item.nodeid,
        )
    )
