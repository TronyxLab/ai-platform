#!/usr/bin/env python3
# GREP_SUMMARY: test-idempotency-hash, T9.3, content-hash, phase-rerun, node-yaml, hash-invalidation, T9.11, retry, should-retry, execute-phase
# STRUCTURE: ▶ test_*_hash_change ┌node.yaml modules v1→v2┐ → phase_needs_rerun True (config change → re-run) │ ▶ test_*_same_hash → False (no-op) │ ▶ test_*_cli_rerun → run_update перевыполняет done-фазу при mismatch │ ▶ test_*_retry ┌OSError×1┐ → execute_phase retry → success │ ▶ test_*_retry_exhausted → OSError×2 → raise
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.3 (L-4/B-1) и T9.11 (B-3) DevPlan 136 W9: content-hash
##           инвалидация фаз (modules/services из node.yaml vs сохранённый hash → re-run;
##           update-фазы тоже инвалидируются) + wiring `_should_retry` вокруг phase_func
##           (RETRY_COUNT=2, exponential backoff).
## @scope    unit-тесты: tmp_path node.yaml + state.json; monkeypatch phases-модуль;
##           time.sleep заглушается (не ждём 2s backoff).
## @invariants
##   - Native imports; tmp_path; NODE_YAML env
##   - Не-hash-фаза (secrets_update) НЕ перевыполняется при изменении modules (только
##     _HASH_INVALIDATED_PHASES)
##   - Retry: транзиентный OSError ретраится (2 попытки), повторный → raise (fail-fast)
##   - LDD IMP:9 в успешных сценариях
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: test_idempotency_hash.py — config change →
##            phase re-run (R5: _step_hash был определён, но НЕ вызывался — мёртвый код, L-4).
## @changes  2026-08-05 · Created (DevPlan 136 W9)
# endregion MODULE_CONTRACT

import json
import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle import cli
from core.internal.bootstrap.lifecycle.state_machine import (
    BootstrapPhase,
    StateMachine,
    StepState,
)
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    """Temporary state.json."""
    return tmp_path / "state.json"


@pytest.fixture
def node_yaml(tmp_path: Path) -> Path:
    """Temporary node.yaml."""
    return tmp_path / "node.yaml"


def _write_node_yaml(node_yaml: Path, modules: dict) -> None:
    node_yaml.write_text(json.dumps({"modules": modules, "services": {}}), encoding="utf-8")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.3 — config change → phase re-run
# · Scenario: node.yaml modules v1 → сохранённый hash → modules v2 → phase_needs_rerun True
# · Last fail: 2026-08-05 — _step_hash определён, но НЕ вызывался (L-4/B-1: node.yaml change
# ·   не инвалидировал done-фазы; update-фазы никогда не перевыполнялись)
# · Remove if: hash invalidation semantics change
@ldd_trajectory
def test_phase_needs_rerun_on_config_change(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, state_file: Path, node_yaml: Path, monkeypatch
) -> None:
    """T9.3: modules/services из node.yaml изменились → done-фаза требует re-run."""
    caplog.set_level(logging.INFO)
    _write_node_yaml(node_yaml, {"postgres": {"enabled": True}})
    monkeypatch.setenv("NODE_YAML", str(node_yaml))

    m = StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="update", node="n")
    phase = BootstrapPhase.DEPLOY_UPDATE
    m.state.steps[phase] = StepState(name=phase, status="done", hash=m._phase_input_hash(phase))
    assert m.phase_needs_rerun(phase) is False, "тот же вход → no-op (идемпотентность)"

    _write_node_yaml(node_yaml, {"postgres": {"enabled": True}, "redis": {"enabled": True}})
    assert m.phase_needs_rerun(phase) is True, "modules change → re-run (T9.3)"
    logger.critical("[IMP:9][test] config change invalidates done phase — OK (T9.3)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.3 — не-relevant фаза не инвалидируется
# · Scenario: secrets_update (вне _HASH_INVALIDATED_PHASES) → config change НЕ перевыполняет
# · Remove if: hash invalidation semantics change
@ldd_trajectory
def test_phase_needs_rerun_only_hash_phases(
    caplog: pytest.LogCaptureFixture, state_file: Path, node_yaml: Path, monkeypatch
) -> None:
    """T9.3: только hash-фазы (deploy/converge/registry_update) инвалидируются."""
    caplog.set_level(logging.INFO)
    _write_node_yaml(node_yaml, {"postgres": {"enabled": True}})
    monkeypatch.setenv("NODE_YAML", str(node_yaml))

    m = StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="update", node="n")
    phase = BootstrapPhase.SECRETS_UPDATE  # вне hash-множества
    m.state.steps[phase] = StepState(name=phase, status="done", hash=m._phase_input_hash(phase))

    _write_node_yaml(node_yaml, {"postgres": {"enabled": True}, "redis": {"enabled": True}})
    assert m.phase_needs_rerun(phase) is False, "не-hash фаза не перевыполняется при config change"
    logger.critical("[IMP:9][test] non-hash phase not invalidated — OK (T9.3)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · T9.3/B-1 — run_update перевыполняет done-фазу при mismatch
# · Scenario: deploy_update done с hash v1 → node.yaml modules v2 → run_update_mode вызывает фазу
# · Last fail: 2026-08-05 — update-фазы со status=done навсегда пропускались (B-1)
# · Remove if: hash invalidation semantics change
@ldd_trajectory
def test_run_update_reruns_done_phase_on_config_change(
    caplog: pytest.LogCaptureFixture,
    state_file: Path,
    node_yaml: Path,
    monkeypatch,
) -> None:
    """T9.3: cli.run_update_mode перевыполняет done-фазу при изменении входов."""
    caplog.set_level(logging.INFO)
    _write_node_yaml(node_yaml, {"postgres": {"enabled": True}})
    monkeypatch.setenv("NODE_YAML", str(node_yaml))

    m = StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="update", node="n")
    # deploy_update done с hash от v1-конфига
    dep_phase = BootstrapPhase.DEPLOY_UPDATE
    m.state.steps[dep_phase] = StepState(name=dep_phase, status="done", hash=m._phase_input_hash(dep_phase))
    m.save()

    executed: list[str] = []

    def _fake_execute_phase(self, phase_value: str):
        executed.append(phase_value)
        return True

    monkeypatch.setattr(StateMachine, "execute_phase", _fake_execute_phase)
    monkeypatch.setattr("core.internal.bootstrap.lifecycle.cli.write_audit_log", lambda sm, **kw: None)
    monkeypatch.setattr("core.internal.bootstrap.lifecycle.cli.send_telegram", lambda sm: None)

    # Фазы до deploy_update (secrets_update, node_config_update, registry_update) — done
    for pv in BootstrapPhase.UPDATE_PHASE_ORDER:
        if pv != dep_phase:
            m.state.steps[pv] = StepState(name=pv, status="done")
    m.save()

    # Конфиг меняется ПОСЛЕ сохранения hash'а (имитация правки node.yaml между прогонами)
    _write_node_yaml(node_yaml, {"postgres": {"enabled": True}, "redis": {"enabled": True}})

    rc = cli.run_update_mode(m)
    assert rc == 0
    assert dep_phase in executed, "deploy_update (done, но hash изменился) обязан перевыполниться (B-1)"
    assert BootstrapPhase.SECRETS_UPDATE not in executed, "не-hash фаза не перевыполняется"
    logger.critical("[IMP:9][test] run_update re-runs done phase on config change — OK (T9.3)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.11 — транзиентный OSError ретраится
# · Scenario: phase_func первый раз raise OSError (transient) → _should_retry → повторный вызов OK
# · Last fail: 2026-08-05 — _should_retry был dead code (B-3): транзиентные сбои фаз
# ·   никогда не ретраились
# · Remove if: retry policy change
@ldd_trajectory
def test_execute_phase_retries_transient_error(caplog: pytest.LogCaptureFixture, state_file: Path, monkeypatch) -> None:
    """T9.11: execute_phase ретраит phase_func при транзиентном OSError (RETRY_COUNT=2)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr("core.internal.bootstrap.lifecycle.state_machine.time.sleep", lambda s: None)
    calls = {"n": 0}

    def _fake_phase(core_dir: str, node_name: str, node_yaml: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient network blip")
        return True

    monkeypatch.setattr("core.internal.bootstrap.lifecycle.phases.phase_registry_update", _fake_phase)
    m = StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="update", node="n")

    result = m.execute_phase(BootstrapPhase.REGISTRY_UPDATE)
    assert result is True
    assert calls["n"] == 2, f"ожидался 1 retry, было {calls['n']} вызова(ов)"
    logger.critical("[IMP:9][test] transient OSError retried once — OK (T9.11)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.11 — ретраи исчерпаны → raise (fail-fast)
# · Scenario: phase_func raise OSError оба раза (attempt 1..2) → _should_retry отказывает → raise
# · Remove if: retry policy change
@ldd_trajectory
def test_execute_phase_retries_exhausted_raises(
    caplog: pytest.LogCaptureFixture, state_file: Path, monkeypatch
) -> None:
    """T9.11: исчерпание ретраев → OSError пробрасывается (fail-fast, не маскировка)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr("core.internal.bootstrap.lifecycle.state_machine.time.sleep", lambda s: None)
    calls = {"n": 0}

    def _always_raise(core_dir: str, node_name: str, node_yaml: str) -> bool:
        calls["n"] += 1
        raise OSError("persistent failure")

    monkeypatch.setattr("core.internal.bootstrap.lifecycle.phases.phase_registry_update", _always_raise)
    m = StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="update", node="n")

    with pytest.raises(OSError, match="persistent failure"):
        m.execute_phase(BootstrapPhase.REGISTRY_UPDATE)
    assert calls["n"] == 2, f"RETRY_COUNT=2: ровно 2 попытки, было {calls['n']}"
    logger.critical("[IMP:9][test] retries exhausted → raise — OK (T9.11)")
