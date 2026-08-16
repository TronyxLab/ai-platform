"""
# GREP_SUMMARY: test-lifecycle-cli-w5, cli-helpers, inject-cli-env, recover-corrupt-state, run-single-phase, force-recovery, exit-codes, W5-C2
# STRUCTURE: ▶ _inject_cli_env ┌args → env-таблица (setdefault/override/flag)┐ → ◇ _recover_corrupt_state ┌corrupt+--force → audit+unlink+recreate│corrupt → abort exit_code┐ → ◇ _run_single_phase ┌unknown → 1│ok → 0┐ → ⎋ LDD IMP:7-10
# region MODULE_CONTRACT
## @purpose  Unit-тесты новых CLI-хелперов lifecycle (DevPlan 170 W5-C2): _inject_cli_env
##           (env-инъекция из CLI-args), _recover_corrupt_state (T9.2 + B26: force-recovery
##           коррапт state.json с аудит-следом), _run_single_phase (--run-phase).
## @scope    tests/unit — native imports (package-импорт lifecycle.cli); tmp_path;
##           monkeypatch для изоляции os.environ. Покрытие run_init/run_update → _run_phases
##           живёт в test_audit_failure_paths/test_idempotency_hash/test_state_machine.
## @invariants
##   - _inject_cli_env: setdefault НЕ перезаписывает существующий env; override/flag — пишут
##   - _recover_corrupt_state: force → B26-аудит (state.json/removed) ДО unlink + recreate;
##     без force → (None, exit_code) — abort, НЕ тихий сброс (T9.2 контракт)
##   - _run_single_phase: unknown phase → exit 1; успех → 0 (exit-коды контракт)
##   - LDD: IMP:9 лог в успешных сценариях (Anti-Illusion Rule)
## @rationale $TEST_SPEC wave-brief W5-C2: test_cli_* — импорты/сигнатуры новых хелперов;
##            инварианты exit-кодов и force-reset семантики сохранены 1:1.
## @changes  2026-08-15 · Created (DevPlan 170 W5-C2)
# endregion MODULE_CONTRACT
"""

import logging
import os
from pathlib import Path

from core.internal.bootstrap.lifecycle import cli
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


class _FakeSM:
    """Fake StateMachine для _run_single_phase: execute_phase с записью вызовов."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute_phase(self, phase_value: str) -> object:
        self.executed.append(phase_value)
        return True


# ═══════════════════════════════════════════════════════════════════
# region Tests: _inject_cli_env (W5-C2)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-15 · Regression · W5-C2 — env-инъекция: setdefault/override/flag семантика
# · Scenario: CLI args (node_name/tor_enabled/skip_tor_verify/auto_reconcile) → _inject_cli_env
# ·   → NODE_NAME setdefault, TOR_ENABLED override ("false" записывается), SKIP/AUTO — flag "true"
# · Last fail: N/A (new — DevPlan 170 W5-C2 извлечение из main)
# · Remove if: _inject_cli_env/таблица _CLI_ENV_INJECTIONS удаляется
@ldd_trajectory
def test_inject_cli_env_setdefault_override_flag(caplog) -> None:
    """_inject_cli_env: 18×if/setdefault из main → таблица с той же семантикой."""
    caplog.set_level(logging.INFO)
    # ⚠️ TRAP[BUG] · 2026-08-15 · P2 · env-утечка NODE_NAME (DevPlan 172 W3.1)
    # · Symptom: test_node_lifecycle_dry_run_contract FAIL «Expected NODE_NAME-required
    # ·   diagnostic» при полном прогоне tests/unit (переезд W3.1 изменил порядок сбора:
    # ·   lifecycle_cli_w5 < node_lifecycle_static по алфавиту).
    # · Root: _inject_cli_env пишет os.environ НАПРЯМУЮ; monkeypatch.setenv('existing')
    # ·   сохраняет «n1» и ПРИ TEARDOWN восстанавливает её — утечка после finally-снапшота.
    # · Fix: БЕЗ monkeypatch — snapshot/restore в try/finally покрывает все записи.
    touched = ("NODE_NAME", "NODE_YAML", "TOR_ENABLED", "SKIP_TOR_VERIFY", "AUTO_RECONCILE", "CONTEXT")
    snapshot = {var: os.environ.get(var) for var in touched}
    try:
        for var in touched:
            os.environ.pop(var, None)

        args = cli.build_parser().parse_args([
            "--mode",
            "init",
            "--node-name",
            "n1",
            "--tor-enabled",
            "false",
            "--skip-tor-verify",
            "--auto-reconcile",
        ])
        cli._inject_cli_env(args)

        assert os.environ["NODE_NAME"] == "n1", "setdefault-пара: node_name → NODE_NAME"
        assert os.environ["TOR_ENABLED"] == "false", "override-пара: tor_enabled пишет 'false' (не truthy-guard)"
        assert os.environ["SKIP_TOR_VERIFY"] == "true", "flag-пара: skip_tor_verify → 'true'"
        assert os.environ["AUTO_RECONCILE"] == "true", "flag-пара: auto_reconcile → 'true'"

        # setdefault НЕ перезаписывает уже установленный env (канон node-lifecycle.sh)
        os.environ["NODE_NAME"] = "existing"
        cli._inject_cli_env(args)
        assert os.environ["NODE_NAME"] == "existing", "setdefault не должен перезаписывать существующий env"
    finally:
        for var in touched:
            prev = snapshot[var]
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev

    logger.critical("[IMP:9][test] _inject_cli_env: setdefault/override/flag семантика — OK (W5-C2)")


# endregion Tests: _inject_cli_env


# ═══════════════════════════════════════════════════════════════════
# region Tests: _recover_corrupt_state (T9.2 + B26 142 W7)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-15 · Regression · W5-C2 — corrupt + --force → B26-аудит + unlink + recreate
# · Scenario: коррапт state.json + force=True → audit (state.json/removed) ДО unlink; файл
# ·   удалён; StateMachine пересоздан (не None, exit_code None)
# · Last fail: N/A (new — DevPlan 170 W5-C2 извлечение из main; контракт T9.2/B26)
# · Remove if: force-recovery семантика меняется
@ldd_trajectory
def test_recover_corrupt_state_force_recovery(caplog, tmp_path: Path) -> None:
    """_recover_corrupt_state(force=True): аудит-след + unlink + recreate (T9.2, B26)."""
    caplog.set_level(logging.INFO)
    from core.internal.bootstrap.lifecycle.state_machine import StateMachine

    state_file = tmp_path / "state.json"
    state_file.write_text("{ not valid json", encoding="utf-8")

    audit_calls: list[tuple] = []

    def fake_audit(tag, status, message, **extra):
        audit_calls.append((tag, status, message, extra))
        return True

    sm, corrupt_exit = cli._recover_corrupt_state(StateMachine, str(state_file), force=True, audit_impl=fake_audit)

    assert corrupt_exit is None, "force-recovery не abort'ит"
    assert sm is not None, "StateMachine обязан быть пересоздан после unlink"
    assert not state_file.exists(), "коррапт файл обязан быть удалён (unlink)"
    assert audit_calls and audit_calls[0][0] == "state.json" and audit_calls[0][1] == "removed", (
        f"B26: аудит-запись removed обязана быть: {audit_calls}"
    )
    logger.critical("[IMP:9][test] _recover_corrupt_state force: audit+unlink+recreate — OK (W5-C2)")


# 🧪 TRAP[TEST] · 2026-08-15 · Regression · W5-C2 — corrupt БЕЗ --force → abort (не тихий сброс)
# · Scenario: коррапт state.json + force=False → (None, exit_code) — PlatformFatalError.exit_code
# · Last fail: N/A (new — T9.2 контракт: explicit error, NOT fresh state)
# · Remove if: T9.2 corrupt-контракт меняется
@ldd_trajectory
def test_recover_corrupt_state_no_force_aborts(caplog, tmp_path: Path) -> None:
    """_recover_corrupt_state(force=False): коррапт → abort с exit_code (НЕ тихий сброс)."""
    caplog.set_level(logging.INFO)
    from core.internal.bootstrap.lifecycle.state_machine import StateMachine

    state_file = tmp_path / "state.json"
    state_file.write_text("{ not valid json", encoding="utf-8")

    sm, corrupt_exit = cli._recover_corrupt_state(
        StateMachine, str(state_file), force=False, audit_impl=lambda *_, **__: True
    )

    assert sm is None, "без --force коррапт НЕ должен создавать свежий state (T9.2)"
    assert corrupt_exit is not None, "exit_code обязан пробрасываться (abort)"
    assert state_file.exists(), "без --force файл НЕ удаляется (явный операторский reset)"
    logger.critical("[IMP:9][test] _recover_corrupt_state no-force: abort, файл сохранён — OK (T9.2)")


# endregion Tests: _recover_corrupt_state


# ═══════════════════════════════════════════════════════════════════
# region Tests: _run_single_phase (--run-phase, W5-C2)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-15 · Regression · W5-C2 — unknown phase → exit 1 (до execute_phase)
# · Scenario: _run_single_phase(sm, "bogus_phase") → 1; execute_phase НЕ вызывается
# · Last fail: N/A (new — DevPlan 170 W5-C2 извлечение из main)
# · Remove if: --run-phase валидация меняется
@ldd_trajectory
def test_run_single_phase_unknown(caplog) -> None:
    """_run_single_phase: unknown phase → exit 1 (валидация ДО execute_phase)."""
    caplog.set_level(logging.INFO)
    fake_sm = _FakeSM()

    rc = cli._run_single_phase(fake_sm, "bogus_phase")  # type: ignore[arg-type]

    assert rc == 1, "unknown phase → exit 1"
    assert fake_sm.executed == [], "execute_phase НЕ должен вызываться для unknown фазы"
    assert any("Unknown phase" in r.message for r in caplog.records), "Должен быть [IMP:10] лог"
    logger.critical("[IMP:9][test] _run_single_phase unknown → 1, execute не вызван — OK (W5-C2)")


# 🧪 TRAP[TEST] · 2026-08-15 · Regression · W5-C2 — известная фаза → execute + exit 0
# · Scenario: _run_single_phase(sm, "system_bootstrap") → 0; execute_phase вызвана 1 раз
# · Last fail: N/A (new — DevPlan 170 W5-C2)
# · Remove if: --run-phase семантика меняется
@ldd_trajectory
def test_run_single_phase_ok(caplog) -> None:
    """_run_single_phase: известная фаза выполняется → exit 0."""
    caplog.set_level(logging.INFO)
    fake_sm = _FakeSM()

    rc = cli._run_single_phase(fake_sm, "system_bootstrap")  # type: ignore[arg-type]

    assert rc == 0, "успешная фаза → exit 0"
    assert fake_sm.executed == ["system_bootstrap"], "execute_phase обязан вызваться ровно 1 раз"
    assert any("completed successfully" in r.message for r in caplog.records), "Должен быть [IMP:9] success-лог"
    logger.critical("[IMP:9][test] _run_single_phase ok → 0, execute вызвана — OK (W5-C2)")


# endregion Tests: _run_single_phase
