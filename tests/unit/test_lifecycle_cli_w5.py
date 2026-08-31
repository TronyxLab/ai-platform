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
import subprocess
from pathlib import Path
from types import SimpleNamespace

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


# ═══════════════════════════════════════════════════════════════════
# region Tests: _forced_command_smoke (T1.1, аудит 2026-08-22)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-22 · PINNING (T1.1) · _forced_command_smoke happy-path → True
# · Scenario: authorized_keys содержит orchestrator_cli dispatch+restrict, dispatch ping → pong
# ·   → ok=True; FAIL-warning'ов НЕТ (до фикса ok был False всегда — FAIL-ветки выполнялись
# ·   безусловно внутри success-if)
# · Last fail: 2026-08-22 — аудит: smoke ВСЕГДА репортил провал на happy-path
# · Remove if: smoke-проверки консолидируются в vps_readiness pre-flight
def test_forced_command_smoke_happy_path(caplog, tmp_path, monkeypatch) -> None:
    """T1.1 pinning: оба чека зелёные → ok=True, без MISSING/FAIL warning'ов."""
    caplog.set_level(logging.DEBUG)
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    keys = ssh_dir / "authorized_keys"
    keys.write_text('command="... orchestrator_cli dispatch receive",restrict ssh-ed25519 AAA', encoding="utf-8")
    monkeypatch.setattr(cli.os.path, "expanduser", lambda _: str(tmp_path))
    monkeypatch.setattr(cli, "platform_remote_base", lambda: str(tmp_path))
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="pong\n", stderr="")

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess:
        return fake

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    ok = cli._forced_command_smoke()

    assert ok is True, "happy-path → канал готов (T1.1: до фикса всегда False)"
    assert "entry OK" in caplog.text and "ping: OK" in caplog.text
    assert "MISSING" not in caplog.text and "ping: FAIL" not in caplog.text, (
        "FAIL-warning не должен появляться на success-ветке (T1.1 regression)"
    )
    logger.critical("[IMP:9][test] forced-command smoke happy-path → True без FAIL-warning — OK (T1.1)")


# 🧪 TRAP[TEST] · 2026-08-22 · Regression · T1.1 — FAIL-ветки → ok=False с диагностикой
# · Scenario: entry отсутствует + ping без pong → ok=False; ровно по одному warning на чек
# · Last fail: N/A (new — T1.1)
# · Remove if: smoke-проверки меняют семантику fail-detection
def test_forced_command_smoke_fail_branches(caplog, tmp_path, monkeypatch) -> None:
    """T1.1: оба чека красные → ok=False; warning'и из else-веток (не дублируются с OK)."""
    caplog.set_level(logging.DEBUG)
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    keys = ssh_dir / "authorized_keys"
    keys.write_text("ssh-ed25519 AAA-no-forced-command", encoding="utf-8")
    monkeypatch.setattr(cli.os.path, "expanduser", lambda _: str(tmp_path))
    monkeypatch.setattr(cli, "platform_remote_base", lambda: str(tmp_path))
    fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="dispatch error: unknown verb", stderr="err")

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess:
        return fake

    monkeypatch.setattr(cli.subprocess, "run", _fake_run)

    ok = cli._forced_command_smoke()

    assert ok is False, "оба чека красные → канал мёртв"
    assert "entry MISSING" in caplog.text, "[IMP:7] MISSING-диагностика обязательна"
    assert "ping: FAIL" in caplog.text, "[IMP:7] ping FAIL-диагностика обязательна"
    assert "entry OK" not in caplog.text and "ping: OK" not in caplog.text, (
        "OK-логи не должны соседствовать с FAIL на тех же чеках"
    )
    logger.critical("[IMP:9][test] forced-command smoke fail-branches → False с диагностикой — OK (T1.1)")


# endregion Tests: _forced_command_smoke


# ═══════════════════════════════════════════════════════════════════
# region Tests: re-exec на Python 3.14 (P0 F-01, 2026-08-31)
# ═══════════════════════════════════════════════════════════════════


class _FakeSMRunPhases:
    """Минимальный fake для _run_phases re-exec wiring (фазы НЕ выполняются)."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.state = SimpleNamespace(steps={}, errors=[], warnings=[], mode="init")
        self.core_dir: str | None = None

    def phase_needs_rerun(self, _phase: str) -> bool:
        return False

    def execute_phase(self, phase_value: str) -> bool:
        self.executed.append(phase_value)
        return True

    def save(self) -> None:
        return None


def _stale_interpreter_ctx(monkeypatch, tmp_path, target_version: str | None) -> str:
    """Старый интерпретатор (< 3.14) + (опционально) целевой python в tmp: вернуть путь цели."""
    monkeypatch.delenv(cli._REEXEC_MARKER_ENV, raising=False)
    cli._reexec_probe_cache.clear()
    monkeypatch.setattr(cli.sys, "version_info", (3, 12, 0, "final", 0))
    target = tmp_path / "python3.14"
    if target_version is not None:
        target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        target.chmod(0o755)
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=f"Python {target_version}\n", stderr="")
        monkeypatch.setattr(cli.subprocess, "run", lambda *_a, **_k: fake)
    monkeypatch.setattr(cli, "_REEXEC_PYTHON_TARGET", str(target))
    return str(target)


# 🧪 TRAP[TEST] · 2026-08-31 · guard (F-01) · современный интерпретатор (>= 3.14) → НЕ re-exec
# · Scenario: dev/CI/тесты на Python 3.14 — версия-гейт возвращает None ДО проверок файловой
# ·   системы: никакой os.execv из тестов (test-safety инвариант _should_reexec_python)
# · Last fail: N/A (guard — P0 F-01)
# · Remove if: механика re-exec (F-01) изменится
@ldd_trajectory
def test_should_reexec_python_modern_interpreter_none(caplog) -> None:
    """На Python >= 3.14 _should_reexec_python() → None (тесты/CI никогда не re-exec'ятся)."""
    caplog.set_level(logging.INFO)
    cli._reexec_probe_cache.clear()

    assert cli._should_reexec_python() is None, (
        "test-safety: интерпретатор >= 3.14 обязан давать None (иначе os.execv убьёт тест-процесс)"
    )
    logger.critical("[IMP:9][test] _should_reexec_python → None на >=3.14 (test-safe) — OK (F-01)")


# 🧪 TRAP[TEST] · 2026-08-31 · guard (F-01) · env-маркер отключает re-exec (loop-guard)
# · Scenario: BOOTSTRAP_PYTHON_REEXEC установлен (после первого execv) → None, даже если
# ·   интерпретатор старый и целевой python существует — защита от бесконечного re-exec loop
# · Last fail: N/A (guard — P0 F-01)
# · Remove if: механика re-exec (F-01) изменится
def test_should_reexec_python_marker_disables(monkeypatch, tmp_path) -> None:
    """Маркер BOOTSTRAP_PYTHON_REEXEC → None (loop-guard: один re-exec за запуск)."""
    _stale_interpreter_ctx(monkeypatch, tmp_path, target_version="3.14.6")
    monkeypatch.setenv(cli._REEXEC_MARKER_ENV, "1")

    assert cli._should_reexec_python() is None, (
        "loop-guard: маркер обязан блокировать повторный re-exec, даже при старом интерпретаторе"
    )
    logger.critical("[IMP:9][test] re-exec marker → None (loop-guard) — OK (F-01)")


# 🧪 TRAP[TEST] · 2026-08-31 · behavior (F-01) · старый интерпретатор + нет цели → None
# · Scenario: голый узел ДО φ1 — /usr/local/bin/python3 ещё не установлен (python_deps не бежал)
# ·   → re-exec невозможен, φ1 обязан выполниться текущим (3.12) интерпретатором
# · Last fail: 2026-08-31 P0 cold bootstrap asi-team-vps
# · Remove if: механика re-exec (F-01) изменится
def test_should_reexec_python_stale_no_target_none(monkeypatch, tmp_path) -> None:
    """Старый интерпретатор + целевой python ОТСУТСТВУЕТ → None (φ1 ставит его сам)."""
    _stale_interpreter_ctx(monkeypatch, tmp_path, target_version=None)

    assert cli._should_reexec_python() is None, "до установки 3.14 (φ1) re-exec невозможен — целевой файл отсутствует"
    logger.critical("[IMP:9][test] stale interpreter + no target → None (φ1 ставит 3.14) — OK (F-01)")


# 🧪 TRAP[TEST] · 2026-08-31 · behavior (F-01) · старый интерпретатор + цель 3.14 → путь
# · Scenario: ПОСЛЕ φ1 python_deps поставил 3.14 (цель отдаёт 3.14.x) → re-exec возвращает
# ·   путь цели — _run_phases перезапустит lifecycle на 3.14 (pydantic доступен)
# · Last fail: 2026-08-31 P0 cold bootstrap asi-team-vps
# · Remove if: механика re-exec (F-01) изменится
def test_should_reexec_python_stale_target_314_returns_path(monkeypatch, tmp_path) -> None:
    """Старый интерпретатор + целевой python = 3.14 → возвращается путь цели."""
    target = _stale_interpreter_ctx(monkeypatch, tmp_path, target_version="3.14.6")

    result = cli._should_reexec_python()

    assert result == target, f"цель 3.14 доступна → re-exec на {target}, got {result!r}"
    logger.critical("[IMP:9][test] stale interpreter + target 3.14 → re-exec path — OK (F-01)")


# 🧪 TRAP[TEST] · 2026-08-31 · behavior (F-01) · цель НЕ 3.14 → None (probe-guard)
# · Scenario: /usr/local/bin/python3 существует, но отдаёт НЕ 3.14 (случайный/иной python) →
# ·   re-exec НЕ триггерится (версия-проуба цели — честная проверка, не только isfile)
# · Last fail: N/A (probe-guard — P0 F-01)
# · Remove if: механика re-exec (F-01) изменится
def test_should_reexec_python_target_not_314_none(monkeypatch, tmp_path) -> None:
    """Целевой python отдаёт 3.12 → None (re-exec ТОЛЬКО на genuine 3.14 от python_deps)."""
    _stale_interpreter_ctx(monkeypatch, tmp_path, target_version="3.12.5")

    assert cli._should_reexec_python() is None, (
        "probe-guard: цель обязана отдавать 3.14 (иначе loop-риск на случайный python)"
    )
    logger.critical("[IMP:9][test] target not-3.14 → None (probe-guard) — OK (F-01)")


# 🧪 TRAP[TEST] · 2026-08-31 · P0 (F-01) · wiring: _run_phases re-exec'ит ДО выполнения фаз
# · Scenario: _should_reexec_python возвращает цель (после φ1) → _run_phases вызывает
# ·   _reexec_lifecycle(target) и НЕ выполняет φ2..φ8 текущим (3.12) интерпретатором
# · Last fail: 2026-08-31 P0 cold bootstrap asi-team-vps
# · Remove if: механика re-exec (F-01) изменится
@ldd_trajectory
def test_run_phases_reexec_wiring(caplog, monkeypatch) -> None:
    """_run_phases: re-exec target доступен → _reexec_lifecycle(target), фазы НЕ выполняются."""
    caplog.set_level(logging.INFO)
    reexec_calls: list[str] = []
    monkeypatch.setattr(cli, "_should_reexec_python", lambda: "/usr/local/bin/python3")
    monkeypatch.setattr(cli, "_reexec_lifecycle", lambda target: reexec_calls.append(target) or 0)

    fake_sm = _FakeSMRunPhases()
    exit_code = cli._run_phases(fake_sm, ["system_bootstrap", "user_accounts"], mode_label="init")

    assert reexec_calls == ["/usr/local/bin/python3"], (
        f"re-exec обязан вызваться с целевым интерпретатором, got {reexec_calls}"
    )
    assert exit_code == 0, "exit-код re-exec (возврат _reexec_lifecycle) должен пробрасываться"
    assert fake_sm.executed == [], (
        "фазы НЕ должны выполняться до re-exec (текущий 3.12 не исполняет φ2..φ8 без pydantic)"
    )
    logger.critical("[IMP:9][test] _run_phases re-exec wiring: _reexec_lifecycle(target) — OK (F-01)")


# endregion Tests: re-exec на Python 3.14 (P0 F-01, 2026-08-31)
