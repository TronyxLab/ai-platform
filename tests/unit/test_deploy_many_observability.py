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
## @purpose — returncode != 0 → честный (deployed, failed) из JSON (НЕ (0, [])).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T6 · R5 negative: returncode != 0
# · Regression: returncode != 0 → (0, []) — фейлы терялись (U-30)
# · Scenario: stdout [DEPLOYED, ROLLED_BACK], returncode 1 → (1, ['mod2'])
# · Last fail: — return 0, [] при ненулевом exit; 2026-08-25 QA R3/T2.C — WARN-семантика
#   заменена на CRIT + unproven-учёт (обновлено под новый контракт)
# · Remove if: deploy-many наблюдаемость меняется
def test_deploy_many_returncode_nonzero(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """returncode != 0 → честный (deployed, failed) из JSON + IMP:10 (DEPLOY_BEST_EFFORT)."""
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
    crit_msgs = [r.message for r in caplog.records if "[IMP:10][_deploy_orchestrator][fail]" in r.message]
    assert crit_msgs, f"QA R3/T2.C: ожидается IMP:10 CRIT при returncode != 0:\n{caplog.text[-1500:]}"


# endregion FUNC_test_deploy_many_returncode_nonzero


# ═══════════════════════════════════════════════════════════════════
# QA R3/T2.C (DevPlan 14): честный failed-accounting хвостов
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_json_corrupt_all_failed
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R3/T2.C — битый JSON = ноль доказательств
# · Scenario: deploy-many упал до записи результата (crash/OOM kill) → stdout мусор/пустой;
#   прежний WARN оставлял failed=[] → severity-агрегация слепа, success-marker писался,
#   healthcheck гасился при полном провале
# · Last fail: 2026-08-25 — except JSONDecodeError логировал IMP:5 и возвращал (0, [])
# · Remove if: deploy-many начнёт писать per-project partial-results файл (другой канал)
def test_json_corrupt_all_failed(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """Битый stdout → deployed=0, ВСЕ недоказанные проекты в failed, exit-контракт 2-класс."""
    caplog.set_level(logging.INFO)

    fake = _fake_run("Traceback (most recent call last): ...", returncode=2)

    deployed, failed = mod._deploy_orchestrator(["mod1", "mod2", "mod3"], run_cmd=fake)

    assert deployed == 0, "битый вывод не доказывает ни одного деплоя"
    assert sorted(failed) == ["mod1", "mod2", "mod3"], f"R3 FAIL: недоказанные не в failed: {failed}"
    crit = [r for r in caplog.records if "[IMP:10][_deploy_orchestrator][parse]" in r.message]
    assert crit, "ожидается IMP:10 zero-proof-of-success лог"
    logger.info("[IMP:9][test][json-corrupt] corrupt stdout → all %d failed", len(failed))
    # exit-контракт: критические фейлы → exit 2 (severity-агрегация DEPLOY_BEST_EFFORT)
    rc = mod._compute_exit_code(crit=len(failed), warn=0, deployed=deployed)
    assert rc == 2, f"битый JSON обязан давать exit 2 (severity-агрегация), получен {rc}"


# endregion FUNC_test_json_corrupt_all_failed


# region FUNC_test_rc_nonzero_crit
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R3/T2.C — rc≠0 + недоказанный проект
# · Scenario: deploy-many записал только mod1=DEPLOYED и умер (rc≠0); mod2/mod3 не имеют
#   записей — недоказанные идут в failed наравне с явными FAILED (паттерн :911-920)
# · Last fail: 2026-08-25 — отсутствующие в выводе проекты молча игнорировались
# · Remove if: deploy-many гарантирует запись результата для КАЖДОГО проекта при любом rc
def test_rc_nonzero_crit(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """rc≠0 + неполный вывод → unproven проекты в failed + IMP:10."""
    caplog.set_level(logging.INFO)

    fake = _fake_run(
        _json_array({"status": "DEPLOYED", "project": "mod1"}),
        returncode=3,
    )

    deployed, failed = mod._deploy_orchestrator(["mod1", "mod2", "mod3"], run_cmd=fake)

    assert deployed == 1
    assert sorted(failed) == ["mod2", "mod3"], f"unproven обязаны быть в failed: {failed}"
    crit = [r for r in caplog.records if "[IMP:10][_deploy_orchestrator][fail]" in r.message]
    assert crit and any("unproven" in m for m in (r.message for r in crit)), "ожидается IMP:10 unproven-accounting лог"
    logger.info("[IMP:9][test][rc-crit] unproven projects accounted: %s", failed)


# endregion FUNC_test_rc_nonzero_crit


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
## @purpose — stdout не-JSON → IMP:10 + ВСЕ недоказанные проекты в failed (QA R3/T2.C).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T6 · не-JSON вывод
# · Scenario: stdout = "some noise" → раньше WARN + (0, []) — маскировка полного провала;
#   2026-08-25 (QA R3/T2.C) контракт перевёрнут: zero proof of success → all failed
# · Last fail: старый ассерт failed==[] закреплял дефект R3
# · Remove if: deploy-many парсинг меняется
def test_deploy_many_non_json_stdout(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """Не-JSON stdout → IMP:10 + недоказанные проекты в failed (не падает)."""
    caplog.set_level(logging.INFO)

    fake = _fake_run("some noise from deploy-many", returncode=0)
    deployed, failed = mod._deploy_orchestrator(["mod1"], run_cmd=fake)

    assert deployed == 0
    assert failed == ["mod1"], f"R3 FAIL: не-JSON вывод не доказывает успех mod1: {failed}"
    parse_msgs = [r.message for r in caplog.records if "не JSON" in r.message]
    assert parse_msgs, "Ожидался IMP:10 лог о не-JSON stdout"


# endregion FUNC_test_deploy_many_non_json_stdout


# region FUNC_test_deploy_many_timeout_marks_all_failed
## @purpose — TimeoutExpired/OSError → (0, [ВСЕ запрошенные]) — незавершённые = failed.
# 🧪 TRAP[TEST] · NEGATIVE (R5) · REF-0103 · таймаут deploy-many ≠ «0 фейлов»
# · Scenario: subprocess.run бросает TimeoutExpired (DEPLOY_TIMEOUT=900) → старый код
# ·   возвращал (0, []) — нулевой failed маскировал убитый deploy-many как успех (exit 0).
# ·   Новый код обязан пометить ВСЕ недошедшие проекты failed.
# · Remove if: _deploy_orchestrator перестаёт честно маркировать незавершённые
def test_deploy_many_timeout_marks_all_failed(caplog: pytest.LogCaptureFixture) -> None:
    """TimeoutExpired → (0, ['mod1', 'mod2']) — все незавершённые = failed, не (0, [])."""
    import subprocess

    caplog.set_level(logging.INFO)

    def _timeout_run(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=900)

    deployed, failed = mod._deploy_orchestrator(["mod1", "mod2"], run_cmd=_timeout_run)

    assert deployed == 0
    assert failed == ["mod1", "mod2"], f"REF-0103 FAIL: таймаут должен помечать ВСЕ незавершённые failed, got {failed}"
    warn_msgs = [r.message for r in caplog.records if "incomplete" in r.message]
    assert warn_msgs, "Ожидался WARN о неполном deploy-many (наблюдаемость REF-0103)"
    logger.critical("[IMP:9][test][REF-0103] timeout → all-failed: %s", failed)


# 🧪 TRAP[TEST] · Regression · REF-0103 · OSError (канал умер) → паритет с таймаутом
# · Scenario: subprocess.run бросает OSError → все незавершённые = failed
# · Remove if: error-handling _deploy_orchestrator расходится для Timeout/OSError
def test_deploy_many_oserror_marks_all_failed(caplog: pytest.LogCaptureFixture) -> None:
    """OSError → (0, ['mod1']) — тот же честный контракт, что и для таймаута."""
    caplog.set_level(logging.INFO)

    def _oserror_run(cmd, *args, **kwargs):
        msg = "spawn failed"
        raise OSError(msg)

    deployed, failed = mod._deploy_orchestrator(["mod1"], run_cmd=_oserror_run)

    assert deployed == 0
    assert failed == ["mod1"]
    logger.critical("[IMP:9][test][REF-0103] OSError → all-failed: %s", failed)


# endregion FUNC_test_deploy_many_timeout_marks_all_failed
