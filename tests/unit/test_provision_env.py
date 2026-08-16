# GREP_SUMMARY: test provision_env provision-environment scope-expansion all dedup dry-run platform-env dispatch audit fail-fast exit-code R5 LDD
# STRUCTURE: ┌expand_scopes (all/dedup/unknown)┐ → ◇ parse_args (--scope/--help/usage-errors) → ◇ main dispatch (FakeProvisionerMain, порядок+dedup+dry-run) → ◇ exit-коды (propagate/fail-fast) → ◇ real provisioner dry-run (tmp_path) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/provision_env.py (DevPlan 164 W3.5-1): оркестратор
##           provision-environment.sh — CLI-парсинг, 'all'-расширение, дедупликация (FIX-1), default
##           platform-env, per-scope dispatch (DI FakeProvisionerMain), exit-code propagation, audit,
##           fail-fast. R5-негативы: --scope обязателен; unknown scope → 1; exit-код provisioner'а
##           пробрасывается; dry-run не выполняет реальных docker-мутаций.
## @scope    Native imports; DI-каналы (provisioner_main/audit_fn) — 0 monkeypatch; tmp_path yaml.
## @invariants
##   - Dispatch-тесты передают FakeProvisionerMain (запись argv, scripted rc) — 0 реального docker
##   - 'all' → networks,volumes,env,profiles (порядок канона), dedup при повторе
##   - Fail-fast: первый rc≠0 → return rc, последующие scopes НЕ диспатчатся, "Provision complete" нет
##   - IMP-сообщения через caplog (модульный logger пишет в stderr-handler import-time + propagate)
##   - Usage/parse-сообщения (print → sys.stderr) через capsys
## @rationale Прямое замещение shell-фасада: R5-тесты фиксируют CLI-контракт (сообщения/exit-коды),
##            который тестировался subprocess'ом в test_unit_provision_environment.py.
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

import pytest
from _conftest.ldd import ldd_trajectory

from core.internal.bootstrap.provision_env import expand_scopes, main, parse_args

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region CLS_FakeProvisionerMain
class FakeProvisionerMain:
    """Scripted provisioner.main (DI): записывает argv, возвращает rc из последовательности."""

    def __init__(self, results: list[int] | None = None) -> None:
        self._results = list(results) if results else []
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        if self._results:
            return self._results.pop(0)
        return 0


# endregion CLS_FakeProvisionerMain


# region CLS_FakeAudit
class FakeAudit:
    """Recorder audit_fn (DI): собирает (tag, status, message) записи."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str]] = []

    def __call__(self, tag: str, status: str, message: str) -> None:
        self.entries.append((tag, status, message))


# endregion CLS_FakeAudit


# region FUNC_test_expand_scopes
_EXPAND_SCOPES_CASES = [
    pytest.param(["all"], ["networks", "volumes", "env", "profiles"], None, id="all"),
    pytest.param(
        ["all", "networks", "volumes", "networks"], ["networks", "volumes", "env", "profiles"], None, id="dedup"
    ),
    pytest.param(["invalid"], None, "invalid", id="unknown-raises"),
]


# 🧪 TRAP[TEST] · Regression/NEGATIVE (R5) · expand_scopes — 'all'-расширение + dedup (FIX-1) + unknown → ValueError
# · Scenario: ['all'] → networks,volumes,env,profiles (порядок канона); 'all'+явные повторы → уникальный
# ·   набор; ['invalid'] → ValueError('invalid') (fail-fast, не тихий skip)
# · Last fail: 2026-08-02 (FIX-1: scalar→array регрессия в shell-фасаде)
# · Remove if: канон scopes / валидация scopes отменены
@ldd_trajectory
@pytest.mark.parametrize(("scopes", "expected", "raises_msg"), _EXPAND_SCOPES_CASES)
def test_expand_scopes(
    caplog: pytest.LogCaptureFixture, scopes: list[str], expected: list[str] | None, raises_msg: str | None
) -> None:
    """expand_scopes: 'all'-расширение, дедупликация (FIX-1), неизвестный scope → ValueError."""
    caplog.set_level(logging.DEBUG)
    if raises_msg is not None:
        with pytest.raises(ValueError) as exc_info:
            expand_scopes(scopes)
        logger.info("[IMP:9][test][unknown] ValueError raised: %s", exc_info.value)
        assert str(exc_info.value) == raises_msg
        return
    result = expand_scopes(scopes)
    logger.info("[IMP:9][test][expand] %s → %s", scopes, result)
    assert result == expected


# endregion FUNC_test_expand_scopes


# region FUNC_test_parse_args_help
# 🧪 TRAP[TEST] · Regression · --help → "Usage:" в stdout, exit 0
# · Scenario: parse_args(["--help"]) → (None, 0), stdout содержит "Usage:"
# · Last fail: N/A (parity с shell heredoc USAGE)
# · Remove if: help-контракт меняется
@ldd_trajectory
def test_parse_args_help(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """parse_args: --help → (None, 0), "Usage:" в stdout."""
    caplog.set_level(logging.DEBUG)
    parsed, code = parse_args(["--help"])
    out, _err = capsys.readouterr()
    logger.info("[IMP:9][test][help] code=%s stdout_has_usage=%s", code, "Usage:" in out)
    assert parsed is None
    assert code == 0
    assert "Usage:" in out


# endregion FUNC_test_parse_args_help


# region FUNC_test_parse_args_missing_scope
# 🧪 TRAP[TEST] · NEGATIVE (R5) · --scope отсутствует → exit 1 + "--scope is required"
# · Scenario: parse_args(["--dry-run"]) → (None, 1), "--scope is required" в stderr
# · Last fail: N/A (parity с shell: "FATAL: --scope is required")
# · Remove if: обязательность --scope отменена
@ldd_trajectory
def test_parse_args_missing_scope(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """R5 negative: --scope обязателен — отсутствие → exit 1 + FATAL."""
    caplog.set_level(logging.DEBUG)
    parsed, code = parse_args(["--dry-run"])
    _out, err = capsys.readouterr()
    logger.info("[IMP:9][test][no-scope] code=%s stderr=%s", code, err.strip())
    assert parsed is None
    assert code == 1
    assert "--scope is required" in err


# endregion FUNC_test_parse_args_missing_scope


# region FUNC_test_parse_args_unknown_argument
# 🧪 TRAP[TEST] · NEGATIVE (R5) · неизвестный аргумент → exit 1 + "Unknown argument"
# · Scenario: parse_args(["--bogus"]) → (None, 1), "Unknown argument" в stderr
# · Last fail: N/A (parity с shell: "ERROR: Unknown argument")
# · Remove if: CLI-контракт меняется
@ldd_trajectory
def test_parse_args_unknown_argument(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """R5 negative: неизвестный CLI-аргумент → exit 1 + ERROR."""
    caplog.set_level(logging.DEBUG)
    parsed, code = parse_args(["--bogus"])
    _out, err = capsys.readouterr()
    logger.info("[IMP:9][test][unknown-arg] code=%s stderr=%s", code, err.strip())
    assert parsed is None
    assert code == 1
    assert "Unknown argument" in err


# endregion FUNC_test_parse_args_unknown_argument


# region FUNC_test_parse_args_scope_without_value
# 🧪 TRAP[TEST] · NEGATIVE (R5) · --scope без значения → exit 1
# · Scenario: parse_args(["--scope"]) → (None, 1), "--scope requires a value"
# · Last fail: N/A (parity с shell: "FATAL: --scope requires a value")
# · Remove if: CLI-контракт меняется
@ldd_trajectory
def test_parse_args_scope_without_value(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """R5 negative: --scope без значения → exit 1 (fail-fast)."""
    caplog.set_level(logging.DEBUG)
    parsed, code = parse_args(["--scope"])
    _out, err = capsys.readouterr()
    logger.info("[IMP:9][test][scope-empty] code=%s stderr=%s", code, err.strip())
    assert parsed is None
    assert code == 1
    assert "--scope requires a value" in err


# endregion FUNC_test_parse_args_scope_without_value


# region FUNC_test_main_dispatch_all_dry_run
# 🧪 TRAP[TEST] · Regression · main --scope all --dry-run → 4 диспатча (порядок канона), dry-run флаг
# · Scenario: FakeProvisionerMain записывает argv; 'all' → networks,volumes,env,profiles, каждый с
# ·   --platform-env и --dry-run; exit 0; audit START/DONE ×4; "Provision complete (scope=all)"
# · Last fail: N/A (R5: dry-run НЕ выполняет реальных docker-мутаций — FakeProvisionerMain)
# · Remove if: оркестрация scopes меняется
@ldd_trajectory
def test_main_dispatch_all_dry_run(caplog: pytest.LogCaptureFixture) -> None:
    """main(['--scope','all','--dry-run']): 4 диспатча в порядке канона, dry-run флаг, exit 0."""
    caplog.set_level(logging.DEBUG)
    fake = FakeProvisionerMain()
    audit = FakeAudit()
    rc = main(["--scope", "all", "--dry-run", "--platform-env", "/tmp/x.yaml"], provisioner_main=fake, audit_fn=audit)
    logger.info("[IMP:9][test][dispatch] rc=%s calls=%s", rc, [c[2] for c in fake.calls])
    assert rc == 0
    scopes_dispatched = [call[1] for call in fake.calls]
    assert scopes_dispatched == ["networks", "volumes", "env", "profiles"]
    assert all("--dry-run" in call for call in fake.calls)
    assert all(call[0] == "--scope" and call[2] == "--platform-env" and call[3] == "/tmp/x.yaml" for call in fake.calls)
    assert len(audit.entries) == 8  # START/DONE × 4 scopes
    assert any("Provision complete (scope=all)" in r.message for r in caplog.records)


# endregion FUNC_test_main_dispatch_all_dry_run


# region FUNC_test_main_exit_code_propagated
# 🧪 TRAP[TEST] · NEGATIVE (R5) · exit-код provisioner'а пробрасывается (10 = docker unavailable)
# · Scenario: FakeProvisionerMain возвращает 10 на первом scope → main → 10, audit FAIL, БЕЗ
# ·   "Provision complete" (fail-fast, set -e семантика shell-фасада)
# · Last fail: N/A (контракт exit-кодов provisioner: 0/1/10)
# · Remove if: контракт exit-кодов меняется
@ldd_trajectory
def test_main_exit_code_propagated(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: exit-код провижинера (10) пробрасывается; "Provision complete" не печатается."""
    caplog.set_level(logging.DEBUG)
    fake = FakeProvisionerMain(results=[10])
    audit = FakeAudit()
    rc = main(["--scope", "networks", "--platform-env", "/tmp/x.yaml"], provisioner_main=fake, audit_fn=audit)
    logger.info("[IMP:9][test][exit-code] rc=%s audit=%s", rc, audit.entries)
    assert rc == 10
    assert fake.calls[0][1] == "networks"
    assert ("provision:networks", "FAIL", "failed (rc=10)") in audit.entries
    assert not any("Provision complete" in r.message for r in caplog.records)


# endregion FUNC_test_main_exit_code_propagated


# region FUNC_test_main_fail_fast_stops_dispatch
# 🧪 TRAP[TEST] · NEGATIVE (R5) · fail-fast: rc≠0 на 2-м scope → 3-й НЕ диспатчится
# · Scenario: results=[0, 1] при --scope all → dispatch networks(0), volumes(1) → return 1,
# ·   env/profiles НЕ вызываются
# · Last fail: N/A (set -e семантика shell: прерывание при первом провале)
# · Remove if: fail-fast семантика отменена (best-effort по всем scopes)
@ldd_trajectory
def test_main_fail_fast_stops_dispatch(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: fail-fast — после rc≠0 последующие scopes не диспатчатся."""
    caplog.set_level(logging.DEBUG)
    fake = FakeProvisionerMain(results=[0, 1])
    rc = main(["--scope", "all", "--platform-env", "/tmp/x.yaml"], provisioner_main=fake)
    logger.info("[IMP:9][test][fail-fast] rc=%s calls=%s", rc, [c[1] for c in fake.calls])
    assert rc == 1
    assert [c[1] for c in fake.calls] == ["networks", "volumes"]


# endregion FUNC_test_main_fail_fast_stops_dispatch


# region FUNC_test_main_dedup_single_dispatch
# 🧪 TRAP[TEST] · Regression (FIX-1) · duplicate scopes → один диспатч
# · Scenario: main(["--scope","networks","--scope","networks"]) → Fake вызывается 1 раз
# · Last fail: 2026-08-02 (FIX-1: дубль scope исполнялся дважды)
# · Remove if: дедупликация scopes отменена
@ldd_trajectory
def test_main_dedup_single_dispatch(caplog: pytest.LogCaptureFixture) -> None:
    """Duplicate --scope networks --scope networks → один диспатч (FIX-1 dedup)."""
    caplog.set_level(logging.DEBUG)
    fake = FakeProvisionerMain()
    rc = main(["--scope", "networks", "--scope", "networks", "--platform-env", "/tmp/x.yaml"], provisioner_main=fake)
    logger.info("[IMP:9][test][dedup-dispatch] rc=%s calls=%s", rc, [c[1] for c in fake.calls])
    assert rc == 0
    assert len(fake.calls) == 1
    assert fake.calls[0][1] == "networks"


# endregion FUNC_test_main_dedup_single_dispatch


# region FUNC_test_main_label_original_scopes
# 🧪 TRAP[TEST] · Regression · label = оригинальные scopes (не expanded), comma-joined
# · Scenario: main(["--scope","all","--scope","networks"]) → "Provision complete (scope=all,networks)"
# · Last fail: N/A (parity с shell _SCOPE_LABEL=${SCOPES[*]})
# · Remove if: формат completion-лога меняется
@ldd_trajectory
def test_main_label_original_scopes(caplog: pytest.LogCaptureFixture) -> None:
    """Label completion = оригинальные scopes через запятую (не expanded)."""
    caplog.set_level(logging.DEBUG)
    fake = FakeProvisionerMain()
    rc = main(["--scope", "all", "--scope", "networks", "--platform-env", "/tmp/x.yaml"], provisioner_main=fake)
    logger.info("[IMP:9][test][label] rc=%s", rc)
    assert rc == 0
    assert any("Provision complete (scope=all,networks)" in r.message for r in caplog.records)


# endregion FUNC_test_main_label_original_scopes


# region FUNC_test_main_unknown_scope_fatal
# 🧪 TRAP[TEST] · NEGATIVE (R5) · unknown scope через main → exit 1 + FATAL
# · Scenario: main(["--scope","invalid"]) → 1, "Unknown scope 'invalid'" в caplog
# · Last fail: N/A (parity с shell: "FATAL: Unknown scope")
# · Remove if: валидация scopes отменена
@ldd_trajectory
def test_main_unknown_scope_fatal(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: main с неизвестным scope → exit 1 + FATAL (диспатча нет)."""
    caplog.set_level(logging.DEBUG)
    fake = FakeProvisionerMain()
    rc = main(["--scope", "invalid"], provisioner_main=fake)
    logger.info("[IMP:9][test][unknown-main] rc=%s", rc)
    assert rc == 1
    assert fake.calls == []
    assert any("Unknown scope 'invalid'" in r.message for r in caplog.records)


# endregion FUNC_test_main_unknown_scope_fatal


# region FUNC_test_main_real_missing_platform_env
# 🧪 TRAP[TEST] · NEGATIVE (R5) · отсутствующий platform-env.yaml → exit 1 + FATAL (real provisioner)
# · Scenario: main(["--scope","networks","--platform-env",<404>]) с РЕАЛЬНЫМ provisioner.main → 1,
# ·   "platform-env.yaml not found" в caplog (parity с test_unit_provision_environment)
# · Last fail: N/A (parity: shell пробрасывал provisioner rc=1)
# · Remove if: обработка отсутствующего манифеста меняется
@ldd_trajectory
def test_main_real_missing_platform_env(caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
    """R5 negative: отсутствующий platform-env.yaml → exit 1 (real provisioner, без DI-маскировки)."""
    caplog.set_level(logging.DEBUG)
    missing = str(tmp_path / "no-such-platform-env.yaml")
    rc = main(["--scope", "networks", "--platform-env", missing])
    logger.info("[IMP:9][test][missing-env] rc=%s", rc)
    assert rc == 1
    assert any("platform-env.yaml not found" in r.message for r in caplog.records)


# endregion FUNC_test_main_real_missing_platform_env


# region FUNC_test_main_real_dry_run_networks
# 🧪 TRAP[TEST] · Regression · real provisioner dry-run на tmp_path yaml: 0 docker-мутаций, exit 0
# · Scenario: tmp platform-env.yaml с 1 сетью → main(["--scope","networks","--dry-run",...]) с РЕАЛЬНЫМ
# ·   provisioner.main → 0; "Networks provisioned" + "Provision complete" в caplog; docker НЕ вызывается
# · Last fail: N/A (R5: dry-run гарантирует отсутствие docker-создания — parity shell dry-run)
# · Remove if: dry-run семантика меняется
@ldd_trajectory
def test_main_real_dry_run_networks(caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
    """Real provisioner dry-run: exit 0, IMP:9 Networks provisioned, docker не мутируется."""
    caplog.set_level(logging.DEBUG)
    yaml_path = tmp_path / "platform-env.yaml"
    yaml_path.write_text("networks:\n  - name: test-net-provision-env\n    driver: bridge\n", encoding="utf-8")
    rc = main(["--scope", "networks", "--dry-run", "--platform-env", str(yaml_path)])
    logger.info("[IMP:9][test][real-dry-run] rc=%s", rc)
    assert rc == 0
    assert any("DRY-RUN: Would create network: test-net-provision-env" in r.message for r in caplog.records)
    assert any("Networks provisioned" in r.message for r in caplog.records)
    assert any("Provision complete (scope=networks)" in r.message for r in caplog.records)


# endregion FUNC_test_main_real_dry_run_networks
