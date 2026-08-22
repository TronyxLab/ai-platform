# GREP_SUMMARY: wave-pipeline, wave-readiness, threading-event, ensure-wave-ready, autouse, wave-sorting, compute-module-waves
# STRUCTURE: _wave_ready dict[int, Event] → _init_wave_events(N) → signal_wave_ready(wave) → _ensure_wave_ready(autouse ◇ marker wave → event.wait) → _compute_module_waves(module.yaml → wave dict)
# region MODULE_CONTRACT
## @purpose  Wave-Pipeline synchronization for parallel test execution.
##           Tests are tagged with @pytest.mark.wave(N) by pytest_collection_modifyitems
##           based on module dependency graph from core/modules/*/module.yaml.
##           The autouse fixture _ensure_wave_ready blocks test execution until its
##           wave's containers are ready (signaled by platform_services after each wave).
##           This enables test execution to overlap with container startup:
##           Wave 0 tests run while Wave 1 containers start in background.
##           T2.17a: _compute_module_waves перенесён сюда из conftest.py (thin facade <200 LOC).
## @scope    All Docker-dependent tests; autouse fixture is detected via conftest re-export.
##           Global state: _wave_ready module-level dict of threading.Event objects.
## @invariants
##   - _wave_ready is populated by _init_wave_events() at session start
##   - signal_wave_ready() is called by platform_services after each wave's containers are healthy
##   - _ensure_wave_ready autouse fixture blocks test function until its wave's event is set
##   - Wave 0 has no blocking (dependencies satisfied during fixture setup)
##   - Tests without @pytest.mark.wave pass through immediately (no blocking)
##   - event.wait(timeout=600) prevents deadlock — 600s is the safety valve
##   - _compute_module_waves() — детерминированный обход module.yaml (T12.6): два вызова
##     на одном дереве → идентичный dict (гейт test_gate_wave_sort_contract)
## @rationale — Wave-Pipeline (DevPlan 040 Wave 4) overlaps container startup with test execution.
##              Without it, all containers must start before any test runs (~170s).
##              With Wave-Pipeline, Wave 0 tests start after ~20s while Wave 1 containers
##              start in the background. Pipeline gain: ~100s.
## @changes — T2.17a: _compute_module_waves перенесён из conftest.py (thin facade invariant)
##            CREATED: 2026-07-22 | DevPlan 040 Wave 4: Wave-Pipeline
# endregion MODULE_CONTRACT

import pathlib
import threading

import pytest
import yaml

# Wave readiness events — set by platform_services when each wave's containers are healthy
_wave_ready: dict[int, threading.Event] = {}


def _compute_module_waves() -> dict[str, int]:
    """Read core/modules/*/module.yaml, compute wave numbers from depends_on.

    ## @purpose — Derive wave numbers from the module dependency graph.
    ##            Wave 0: modules with no dependencies.
    ##            Wave N: modules whose max dependency wave + 1.
    ##            T2.17a: тело перенесено из conftest.py — единственный канон здесь;
    ##            conftest re-exportирует (test_gate_wave_sort_contract импортирует
    ##            _compute_module_waves из tests.conftest).
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
    from _conftest.shared import compute_module_waves

    platform_root = pathlib.Path(__file__).resolve().parent.parent.parent  # project root (tests/../)
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


def _init_wave_events(num_waves: int) -> None:
    """Initialize threading.Event objects for each wave index.

    ## @purpose — Called by platform_services fixture at session start, before any
    ##            wave startup. Creates one Event per wave (0 through num_waves).
    ## @io — ⇥ num_waves: int — number of waves (from _build_waves result) → ⎋ None
    ## @complexity — O(N) where N = num_waves
    ## @invariants
    ##   - Fresh call clears previous state (idempotent for session reuse)
    ##   - Wave 0 event is created but typically not waited on
    ##   - num_waves is the count of waves, so events for 0..num_waves-1 are created
    """
    _wave_ready.clear()
    for w in range(num_waves):
        _wave_ready[w] = threading.Event()


def signal_wave_ready(wave: int) -> None:
    """Signal that a wave's containers are all healthy.

    ## @purpose — Called by platform_services after _start_wave_sync(wave) completes
    ##            for each wave. Sets the threading.Event, unblocking all tests
    ##            in that wave that are waiting in _ensure_wave_ready.
    ## @io — ⇥ wave: int — wave index (0-based) → ⎋ None
    ## @complexity — O(1)
    ## @invariants
    ##   - Must be called AFTER all containers in the wave are healthy
    ##   - Idempotent: setting an already-set Event is a no-op
    """
    event = _wave_ready.get(wave)
    if event is not None:
        event.set()


@pytest.fixture(scope="function", autouse=True)
def _ensure_wave_ready(request: pytest.FixtureRequest) -> None:
    """Block test execution until its wave's containers are ready.

    ## @purpose — Function-scoped autouse fixture. Checks if the test has a @pytest.mark.wave(N)
    ##            marker. If N > 0, blocks on the wave's threading.Event until the
    ##            platform_services background thread signals readiness.
    ##            Wave 0 tests pass through immediately (containers are started synchronously
    ##            during session fixture setup).
    ## @io — ⇥ request: pytest.FixtureRequest → ⎋ None
    ## @complexity — O(1) — event.wait() blocks until signaled
    ## @invariants
    ##   - Tests without @pytest.mark.wave pass through immediately (no blocking)
    ##   - Wave 0 (marker.args[0] == 0) passes through immediately
    ##   - event.wait(timeout=600) prevents indefinite blocking
    ##   - After timeout, test proceeds anyway (fail-deadly vs fail-dead)
    ## @rationale — Function scope ensures the wait happens immediately before test
    ##              execution, NOT during session fixture setup. This is critical because
    ##              session fixture setup must complete before any test runs, and blocking
    ##              in session-scoped fixtures would prevent Wave 0 tests from starting
    ##              while Wave 1 containers spin up in background.
    """
    marker = request.node.get_closest_marker("wave")
    if marker is None:
        return

    wave = marker.args[0] if marker.args else 0
    if wave == 0:
        return

    event = _wave_ready.get(wave)
    if event is not None:
        LOGGER = __import__("logging").getLogger(__name__)
        LOGGER.info(
            "[IMP:7][wave_pipeline][_ensure_wave_ready] Test '%s' waiting for Wave %d containers...",
            request.node.name,
            wave,
        )
        event.wait(timeout=600)
        LOGGER.info(
            "[IMP:9][wave_pipeline][_ensure_wave_ready] Test '%s' — Wave %d containers ready, proceeding",
            request.node.name,
            wave,
        )
