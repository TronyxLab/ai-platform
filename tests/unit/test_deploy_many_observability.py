# GREP_SUMMARY: test-deploy-many-observability, deploy-many, LocalChannel, JSON-parsing, deployed, failed, U-30, D7
# STRUCTURE: ▶ 4 scenarios ┌2×DEPLOYED+1×FAILED → (2,[mod3]) + пустой список + returncode!=0 + no --scp┐ → ○ caplog LDD IMP:9 → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 116 B1 T6 (U-30, D7) — bootstrap _deploy_orchestrator:
##           deploy-many через LocalChannel (БЕЗ --scp — на-ноде операция), парсинг JSON-вывода
##           → честные (deployed, failed) вместо всегда (0, []).
## @scope    Tests _deploy_orchestrator() с monkeypatched subprocess.run (stdout = JSON-массив).
## @invariants
##   - No Docker, no subprocess (subprocess.run мокается)
##   - LDD: IMP:9 лог на start/done deploy-many
##   - R5 anti-survivorship: returncode != 0 → честный (deployed, failed) из JSON, не (0, [])
## @rationale  DevPlan 116 B1 T6 criteria: deploy-many на ноде не пытается SCP-доставить самому себе;
##             (deployed, failed) отражают реальный JSON.
## @changes    2026-08-01 | Created (DevPlan 116 B1 T6)
# endregion MODULE_CONTRACT

import json
import logging

import pytest

from core.internal.bootstrap.deploy import deploy_orchestrator as mod
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# T2.16a: _assert_imp9_logged консолидирован в gate_helpers.assert_ldd_imp9
def _json_array(*entries: dict) -> str:
    """Serialize JSON-массив DeployResult (stdout deploy-many)."""
    return json.dumps(list(entries))


def _fake_run(stdout: str, returncode: int = 0):
    """Фабрика фейкового subprocess.run."""

    def _run(cmd, *args, **kwargs):
        captured = []
        logger.info("[IMP:7][test][fake_run] cmd=%s", cmd)
        captured.append(cmd)
        return type("Proc", (), {"stdout": stdout, "returncode": returncode, "cmd": cmd})

    return _run


# region FUNC_test_deploy_many_parses_json_counts
## @purpose — stdout = JSON-массив 2×DEPLOYED + 1×FAILED → (2, ["mod3"]).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T6 · U-30 парсинг JSON
# · Regression: _deploy_orchestrator всегда возвращал (0, []) — наблюдаемость отсутствовала
# · Scenario: subprocess.run возвращает JSON [DEPLOYED, DEPLOYED, FAILED] → (2, ['mod3'])
# · Last fail: — return (0, []) без парсинга (U-30)
# · Remove if: deploy-many парсинг меняется
def test_deploy_many_parses_json_counts(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """JSON-массив 2×DEPLOYED + 1×FAILED → (2, ['mod3'])."""
    caplog.set_level(logging.INFO)

    fake = _fake_run(
        _json_array(
            {"status": "DEPLOYED", "project": "mod1"},
            {"status": "DEPLOYED", "project": "mod2"},
            {"status": "FAILED", "project": "mod3", "error_info": "compose failed"},
        )
    )

    # DI (W-H): run_cmd= параметром (0 патчей модульного subprocess)
    deployed, failed = mod._deploy_orchestrator(["mod1", "mod2", "mod3"], run_cmd=fake)

    assert_ldd_imp9(caplog)
    assert deployed == 2
    assert failed == ["mod3"]


# endregion FUNC_test_deploy_many_parses_json_counts


# region FUNC_test_deploy_many_empty_list
## @purpose — пустой docker_names → (0, []) без subprocess.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T6 · пустой список
# · Scenario: _deploy_orchestrator([]) → (0, [])
# · Remove if: _deploy_orchestrator меняется
def test_deploy_many_empty_list(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """Пустой список модулей → (0, []) без subprocess-вызова."""
    caplog.set_level(logging.INFO)
    called = []

    def _run(*a, **k):
        called.append(a)
        msg = "subprocess не должен вызываться при пустом списке"
        raise AssertionError(msg)

    deployed, failed = mod._deploy_orchestrator([], run_cmd=_run)

    assert deployed == 0
    assert failed == []
    assert called == []


# endregion FUNC_test_deploy_many_empty_list


# region FUNC_test_deploy_many_returncode_nonzero
## @purpose — returncode != 0 → WARN + честный (deployed, failed) из JSON (НЕ (0, [])).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T6 · R5 negative: returncode != 0
# · Regression: returncode != 0 → (0, []) — фейлы терялись (U-30)
# · Scenario: stdout [DEPLOYED, ROLLED_BACK], returncode 1 → (1, ['mod2'])
# · Last fail: — return 0, [] при ненулевом exit
# · Remove if: deploy-many наблюдаемость меняется
def test_deploy_many_returncode_nonzero(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """returncode != 0 → WARN + честный (deployed, failed) из JSON (DEPLOY_BEST_EFFORT)."""
    caplog.set_level(logging.INFO)

    fake = _fake_run(
        _json_array(
            {"status": "DEPLOYED", "project": "mod1"},
            {"status": "ROLLED_BACK", "project": "mod2"},
        ),
        returncode=1,
    )

    deployed, failed = mod._deploy_orchestrator(["mod1", "mod2"], run_cmd=fake)

    assert_ldd_imp9(caplog)
    assert deployed == 1
    assert failed == ["mod2"]  # ROLLED_BACK считается фейлом
    warn_msgs = [
        r.message
        for r in caplog.records
        if "non-fatal" in r.message.lower() or "WARN" in r.message or "[IMP:5]" in r.message
    ]
    assert warn_msgs, "Ожидался WARN-лог при returncode != 0"


# endregion FUNC_test_deploy_many_returncode_nonzero


# region FUNC_test_deploy_many_cmd_no_scp_flag
## @purpose — cmd НЕ содержит --scp (D7: LocalChannel — на-ноде операция).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T6 · D7 negative: --scp отсутствует
# · Regression: deploy-many шлёт SCP-доставку самому себе
# · Scenario: перехваченный cmd не содержит '--scp'
# · Last fail: — cmd включал '--scp' (дефолт SCPChannel)
# · Remove if: deploy-many канал меняется
def test_deploy_many_cmd_no_scp_flag(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """cmd deploy-many НЕ содержит --scp (LocalChannel, D7)."""
    caplog.set_level(logging.INFO)
    captured: list[list[str]] = []

    def _run(cmd, *args, **kwargs):
        captured.append(list(cmd))
        return type("Proc", (), {"stdout": "[]", "returncode": 0})()

    deployed, failed = mod._deploy_orchestrator(["mod1"], run_cmd=_run)

    assert deployed == 0
    assert failed == []
    assert captured, "subprocess.run должен был быть вызван"
    cmd = captured[0]
    assert "--scp" not in cmd, "D7: --scp НЕ должен передаваться (LocalChannel на-ноде)"
    assert "--forced-command" not in cmd
    assert "deploy-many" in cmd
    assert "--projects" in cmd


# endregion FUNC_test_deploy_many_cmd_no_scp_flag


# region FUNC_test_deploy_many_non_json_stdout
## @purpose — stdout не-JSON → WARN + (0, []) (graceful degradation, без исключений).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T6 · не-JSON вывод
# · Scenario: stdout = "some noise" → WARN, (0, [])
# · Remove if: deploy-many парсинг меняется
def test_deploy_many_non_json_stdout(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """Не-JSON stdout → WARN + (0, []) (не падает)."""
    caplog.set_level(logging.INFO)

    fake = _fake_run("some noise from deploy-many", returncode=0)
    deployed, failed = mod._deploy_orchestrator(["mod1"], run_cmd=fake)

    assert deployed == 0
    assert failed == []
    parse_msgs = [r.message for r in caplog.records if "не JSON" in r.message]
    assert parse_msgs, "Ожидался WARN о не-JSON stdout"


# endregion FUNC_test_deploy_many_non_json_stdout
