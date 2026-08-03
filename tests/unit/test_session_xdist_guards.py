"""
# GREP_SUMMARY: test-session-xdist-guards, session-hooks, master-worker, PYTEST_XDIST_WORKER, attempt-counter, docker-cleanup, DevPlan-124, T1
# STRUCTURE: ▶ monkeypatch env (worker/master) + mocks → ◇ pytest_sessionstart (increment? ) → ◇ pytest_sessionfinish (cleanup/reset?) → ⊕ asserts no-op vs executed → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 124 T1: session-hooks master-guard. При -n auto хуки
##           pytest_sessionstart/sessionfinish выполняются в каждом xdist-воркере; без гейта
##           attempt-счётчик инкрементировался N раз (факт 4: -n 2 → Attempt #2 за один прогон),
##           docker-cleanup в рано завершившемся воркере сносил стек других (факт 5).
##           Воркер (PYTEST_XDIST_WORKER set) — НЕ инкрементирует/НЕ сбрасывает/НЕ чистит;
##           master — инкрементирует, чистит, сбрасывает при aggregate-100% PASS.
## @scope    Только session-хуки tests/_conftest/session.py (T1); counter-семантика —
##           через mocked функции (counter.py покрыт отдельно, DevPlan 120 §3.3).
## @invariants
##   - tmp_path-независимые unit-тесты: реальные файлы НЕ читаются (_validate_test_fixtures mocked)
##   - Native imports: from _conftest.session import ... (tests/conftest.py site.addsitedir)
##   - LDD: @ldd_trajectory asserts IMP:9 presence (tests/_conftest/ldd.py)
##   - Test Honesty R1/R2: real falsifiable assertions, no pass-tests
## @rationale DevPlan 124 T1 приёмка: «master — делает; воркер — не инкрементирует/
##            не сбрасывает/не чистит» — прямая проверка master-guard семантики.
## @changes 2026-08-03 | Created (DevPlan 124 T1)
# endregion MODULE_CONTRACT
"""

import logging

import _conftest.session as session_mod
import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# region TEST_DOUBLES
class _FakeConfig:
    """Config stub: getoption returns default (нет -m маркерного фильтра в тесте)."""

    def getoption(self, name: str, default=None):
        return default


class _FakeSession:
    """Минимальный session-стаб: используется только session.config.getoption."""

    config = _FakeConfig()


def _as_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Смоделировать xdist-воркер: PYTEST_XDIST_WORKER установлен (стандартный env xdist)."""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")


def _as_master(monkeypatch: pytest.MonkeyPatch) -> None:
    """Смоделировать master-процесс: PYTEST_XDIST_WORKER отсутствует (не воркер)."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


def _guard_hooks(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Замокать мутирующие зависимости session-хуков; вернуть recorder-словарь.

    ## @purpose  Изолировать hooks от реального docker/counter: _validate_test_fixtures
    ##            (чтение test_data — не нужно в unit), _increment_counter/_read_counter/
    ##            _write_counter (реальный файл .test_counter.json — не трогаем),
    ##            cleanup-функции (docker ps/rm — не запускаем).
    ## @io       ⇥ monkeypatch → ⎋ dict: calls (list[str]) + fake-функции
    ## @complexity O(1)
    """
    calls: list[str] = []
    monkeypatch.setattr(session_mod, "_validate_test_fixtures", lambda: None)

    def _fake_increment() -> int:
        calls.append("increment")
        return 42

    def _fake_read() -> dict:
        calls.append("read")
        return {"attempts": 7}

    def _fake_write(data: dict) -> None:
        calls.append(f"write:{data}")

    def _fake_compose_cleanup() -> None:
        calls.append("compose_cleanup")

    def _fake_hermes_cleanup() -> None:
        calls.append("hermes_cleanup")

    def _fake_network_release() -> None:
        calls.append("network_release")

    monkeypatch.setattr(session_mod, "_increment_counter", _fake_increment)
    monkeypatch.setattr(session_mod, "_read_counter", _fake_read)
    monkeypatch.setattr(session_mod, "_write_counter", _fake_write)
    monkeypatch.setattr(session_mod, "_final_compose_cleanup", _fake_compose_cleanup)
    monkeypatch.setattr(session_mod, "_final_hermes_test_cleanup", _fake_hermes_cleanup)
    monkeypatch.setattr(session_mod, "_force_release_test_networks", _fake_network_release)
    return calls


# endregion TEST_DOUBLES


# ═══════════════════════════════════════════════════════════════
# region Tests: pytest_sessionstart master-guard
# ═══════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · DevPlan 124 T1 · sessionstart: воркер НЕ инкрементирует attempt-счётчик
# · Scenario: PYTEST_XDIST_WORKER=gw0 → pytest_sessionstart не вызывает _increment_counter
# ·   (no-op + лог worker id); иначе -n auto дал бы Attempt #N за один прогон (факт 4)
# · Last fail: 2026-08-03 — эксперимент -n 2 → Attempt #2 за ОДИН прогон
# · Remove if: master-guard снят или инкремент перенесён из sessionstart
@ldd_trajectory
def test_sessionstart_worker_skips_counter_increment(caplog, monkeypatch) -> None:
    """DevPlan 124 T1: xdist-воркер не инкрементирует attempt-счётчик (no-op + worker log)."""
    calls = _guard_hooks(monkeypatch)
    _as_worker(monkeypatch)

    session_mod.pytest_sessionstart(_FakeSession())

    assert calls == [], f"воркер не должен мутировать счётчик, calls={calls}"
    logger.critical("[IMP:9][test] worker sessionstart: increment skipped (calls=%d)", len(calls))


# 🧪 TRAP[TEST] · DevPlan 124 T1 · sessionstart: master инкрементирует ровно 1 раз
# · Scenario: без PYTEST_XDIST_WORKER → _increment_counter вызван (Attempt #42 из fake)
# · Last fail: N/A (базовое поведение, сохраняемое гейтом)
# · Remove if: семантика инкремента изменена
@ldd_trajectory
def test_sessionstart_master_increments_counter(caplog, monkeypatch) -> None:
    """DevPlan 124 T1: master-сессия инкрементирует attempt-счётчик (1 раз за сессию)."""
    calls = _guard_hooks(monkeypatch)
    _as_master(monkeypatch)

    session_mod.pytest_sessionstart(_FakeSession())

    assert calls == ["increment"], f"master должен инкрементировать ровно 1 раз, calls={calls}"
    logger.critical("[IMP:9][test] master sessionstart: increment executed (calls=%s)", calls)


# endregion Tests: pytest_sessionstart master-guard


# ═══════════════════════════════════════════════════════════════
# region Tests: pytest_sessionfinish master-guard
# ═══════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · DevPlan 124 T1 · sessionfinish: воркер НЕ чистит docker и НЕ трогает счётчик
# · Scenario: PYTEST_XDIST_WORKER=gw0 → cleanup-функции и read/reset НЕ вызываются даже при
# ·   exitstatus==0; иначе рано завершившийся воркер сносил контейнеры/сети других (факт 5)
# · Last fail: 2026-08-03 — docker-гонка sessionfinish (воркер A удалял стек воркера B)
# · Remove if: master-guard снят или cleanup перенесён из sessionfinish
@ldd_trajectory
def test_sessionfinish_worker_skips_cleanup_and_counter(caplog, monkeypatch) -> None:
    """DevPlan 124 T1: воркер не выполняет docker-cleanup и не читает/сбрасывает счётчик."""
    calls = _guard_hooks(monkeypatch)
    _as_worker(monkeypatch)

    session_mod.pytest_sessionfinish(_FakeSession(), exitstatus=0)

    assert calls == [], f"воркер не должен чистить/сбрасывать, calls={calls}"
    logger.critical("[IMP:9][test] worker sessionfinish: cleanup+reset skipped (calls=%d)", len(calls))


# 🧪 TRAP[TEST] · DevPlan 124 T1 · sessionfinish: master чистит и сбрасывает при 100% PASS
# · Scenario: без PYTEST_XDIST_WORKER, exitstatus==0 → все 3 cleanup + read + write({"attempts": 0})
# ·   (master видит aggregate-результат xdist-сессии — reset корректен только там)
# · Last fail: 2026-08-03 — каждый воркер сбрасывал счётчик при своём локальном PASS (факт 4)
# · Remove if: порядок cleanup/reset в sessionfinish изменён
@ldd_trajectory
def test_sessionfinish_master_cleans_and_resets(caplog, monkeypatch) -> None:
    """DevPlan 124 T1: master при exitstatus==0 чистит docker и сбрасывает счётчик в 0."""
    calls = _guard_hooks(monkeypatch)
    _as_master(monkeypatch)
    monkeypatch.setenv("PYTEST_NO_ESCALATION", "1")

    session_mod.pytest_sessionfinish(_FakeSession(), exitstatus=0)

    assert "compose_cleanup" in calls, "master должен выполнить final compose cleanup"
    assert "hermes_cleanup" in calls, "master должен выполнить hermes-test sweep"
    assert "network_release" in calls, "master должен выполнить network release"
    assert "read" in calls, "master должен прочитать счётчик"
    assert "write:{'attempts': 0}" in calls, f"master должен сбросить счётчик в 0, calls={calls}"
    logger.critical("[IMP:9][test] master sessionfinish: cleanup+reset executed (calls=%s)", calls)


# 🧪 TRAP[TEST] · DevPlan 124 T1 · sessionfinish: master при фейле НЕ сбрасывает счётчик
# · Scenario: exitstatus!=0 → cleanup выполняется, но write-сброс НЕ вызывается
# ·   (счётчик остаётся на инкрементированном значении — анти-loop эскалация)
# · Last fail: 2026-08-03 — воркер с exitstatus==0 сбрасывал фейл параллельного воркера
# · Remove if: семантика reset при фейле изменена
@ldd_trajectory
def test_sessionfinish_master_failure_keeps_counter(caplog, monkeypatch) -> None:
    """DevPlan 124 T1: master при фейле чистит docker, но НЕ сбрасывает счётчик."""
    calls = _guard_hooks(monkeypatch)
    _as_master(monkeypatch)
    monkeypatch.setenv("PYTEST_NO_ESCALATION", "1")  # подавить checklist-вывод (git hooks-контракт)

    session_mod.pytest_sessionfinish(_FakeSession(), exitstatus=1)

    assert "compose_cleanup" in calls, "master чистит docker и при фейле"
    assert "read" in calls, "master читает счётчик для эскалации"
    assert not any(c.startswith("write:") for c in calls), f"сброс при фейле запрещён, calls={calls}"
    logger.critical("[IMP:9][test] master sessionfinish: failure keeps counter (calls=%s)", calls)


# endregion Tests: pytest_sessionfinish master-guard
