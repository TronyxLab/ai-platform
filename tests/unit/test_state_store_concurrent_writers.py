# GREP_SUMMARY: test-state-store-concurrent-writers, T9.2, state-json, save-state, flock, concurrent-writers, StateCorruptError, unique-tmp, atomic
# STRUCTURE: ▶ test_*_corrupt ┌{invalid json}┐ → load_state → ⚡ StateCorruptError (явная ошибка) │ ▶ test_*_concurrent_save ┌2 threads × N writes┐ → flock + unique tmp → final state.json = валидный JSON одного из writers (×5) │ ▶ test_*_no_fixed_tmp → 0 .json.tmp-мусора
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.2 (L-2/B-2) DevPlan 136 W9: state_store.save_state под flock
##           + unique tmp (tempfile.mkstemp через shared atomic_writer); load_state — ЯВНАЯ
##           ошибка при коррапте (StateCorruptError, НЕ свежий state); конкурентные writers
##           не рвут файл (consistency-инвариант, не детерминизм).
## @scope    unit-тесты: tmp_path state.json; threads (concurrent writers); без Docker.
## @invariants
##   - Native imports; tmp_path; concurrency-тест параметризован range(5) — assert на инвариант
##   - Коррапт state.json → StateCorruptError (R5-negative: исходный вход, маскировавшийся fresh state)
##   - После save: 0 файлов *.json.tmp (fixed-tmp гонка устранена — unique tmp)
##   - LDD IMP:9 в успешных сценариях
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: test_state_store_concurrent_writers.py — 2-нити
##            save_state → consistency; R5-negative на коррапт (тихий fresh-state → явная ошибка).
## @changes  2026-08-05 · Created (DevPlan 136 W9)
# endregion MODULE_CONTRACT

import json
import logging
import threading
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.state_store import (
    BootstrapState,
    StateCorruptError,
    StepState,
    load_state,
    save_state,
)
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.2 — коррапт state.json → ЯВНАЯ ошибка
# · Scenario: state.json = invalid JSON (точный вход, маскировавшийся fresh state) →
# ·   load_state → StateCorruptError (НЕ тихий BootstrapState())
# · Last fail: 2026-08-05 — load_state возвращал fresh state на коррапте (потеря checkpoint'ов
# ·   без следа; node-update начинал заново; L-2/B-2)
# · Remove if: corrupt state handling changes (T9.2 контракт)
@ldd_trajectory
def test_load_state_corrupt_raises(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.2: коррапт state.json → StateCorruptError (explicit, NOT fresh state)."""
    caplog.set_level(logging.INFO)
    state_file = tmp_path / "state.json"
    state_file.write_text("{invalid json...}", encoding="utf-8")
    with pytest.raises(StateCorruptError, match="corrupt"):
        load_state(state_file)
    logger.critical("[IMP:9][test] corrupt state raises StateCorruptError — OK (T9.2)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.2 — нет ФИКСИРОВАННОГО .json.tmp
# · Scenario: после save_state не остаётся state.json.tmp (fixed-tmp = гонка writers)
# · Last fail: 2026-08-05 — save_state писал в path.with_suffix('.json.tmp') (L-2: два writers
# ·   перезаписывали чужой tmp и os.replace'или чужие данные)
# · Remove if: save semantics change
@ldd_trajectory
def test_save_state_no_fixed_tmp_leftover(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.2: unique tmp (atomic_writer) — 0 .json.tmp мусора, файл валиден."""
    caplog.set_level(logging.INFO)
    state_file = tmp_path / "state.json"
    state = BootstrapState(mode="init", node="test-node")
    state.steps["system_bootstrap"] = StepState(name="system_bootstrap", status="done")
    save_state(state, state_file)

    leftovers = [f.name for f in tmp_path.iterdir() if f.name.endswith(".json.tmp")]
    assert not leftovers, f"fixed-tmp мусор: {leftovers}"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["node"] == "test-node"
    assert data["steps"]["system_bootstrap"]["status"] == "done"
    logger.critical("[IMP:9][test] save_state: unique tmp, no leftovers — OK (T9.2)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · T9.2 — 2 конкурентных writer'а → consistency
# · Scenario: 2 threads пишут state.json (разные node) N раз одновременно → финальный файл =
# ·   валидный JSON одного из writers (flock сериализует + unique tmp: НЕТ tearing)
# · Last fail: 2026-08-05 — fixed tmp + прямой replace позволял межнитевое перетирание
# · Remove if: save semantics change
@ldd_trajectory
@pytest.mark.parametrize("run", range(5), ids=[f"run{i}" for i in range(5)])
def test_save_state_concurrent_writers_consistent(run: int, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.2: конкурентные save_state → consistency-инвариант (валидный JSON одного из writers)."""
    caplog.set_level(logging.INFO)
    state_file = tmp_path / "state.json"

    def _writer(node: str, iterations: int) -> None:
        for i in range(iterations):
            state = BootstrapState(mode="update", node=node, current_step=i)
            state.steps["deploy_update"] = StepState(name="deploy_update", status="done", hash=f"h-{node}-{i}")
            save_state(state, state_file)

    t1 = threading.Thread(target=_writer, args=("node-A", 25))
    t2 = threading.Thread(target=_writer, args=("node-B", 25))
    t1.start()
    t2.start()
    t1.join(timeout=60)
    t2.join(timeout=60)
    assert not t1.is_alive() and not t2.is_alive(), "writer threads must finish"

    # Invariant (consistency, не детерминизм): файл — валидный JSON, steps целостны,
    # node = один из писавших (не смесь), current_step консистентен
    raw = state_file.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["node"] in {"node-A", "node-B"}, f"node обязан быть одним из writers: {data['node']!r}"
    step = data["steps"]["deploy_update"]
    assert step["status"] == "done"
    assert step["hash"] == f"h-{data['node']}-{data['current_step']}", "hash и current_step из одного write"
    # Нет частичной записи (легаси torn state был бы invalid JSON)
    assert "}" in raw and raw.rstrip().endswith("}")

    logger.critical("[IMP:9][test] run=%d: concurrent writers → consistent state.json — OK (T9.2)", run)


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.2 — save_state держит lock на state.json.lock
# · Scenario: после save_state lock-файл существует; повторный non-blocking acquire проходит
# ·   (flock снят в finally) — нет «зависшего замка»
# · Remove if: save semantics change
@ldd_trajectory
def test_save_state_lock_released(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.2: lock state.json.lock освобождается после save (try/finally)."""
    caplog.set_level(logging.INFO)
    from core.internal.shared.file_lock import FileLock

    state_file = tmp_path / "state.json"
    save_state(BootstrapState(mode="init", node="n"), state_file)
    lock_path = tmp_path / "state.json.lock"
    assert lock_path.exists(), "lock-файл создаётся при save"
    lock = FileLock(lock_path, timeout=0.0)
    lock.acquire()  # не должно быть FileLockError — иначе lock не освобождён
    lock.release()
    logger.critical("[IMP:9][test] state save lock released — OK (T9.2)")
