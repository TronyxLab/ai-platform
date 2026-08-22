# GREP_SUMMARY: test-project-status-contract, status, ProjectStatus, StatusResult, exit-codes, D6, U-36, project-lister
# STRUCTURE: ▶ 4 scenarios ┌канон-ключи (orchestrator vs DeployEngine) + exit 0/1 + project_lister таблица┐ → ○ caplog LDD IMP:9 → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 116 B1 T3 (U-36, D6) — status-контракт ProjectStatus JSON:
##           (1) канон-поля {project, status, containers, last_deploy} совпадают у orchestrator.status()
##               и DeployEngine.StatusResult (поле node — расширение); (2) exit-коды честные:
##               found/stub → 0, not_found → 1 (через dispatch); (3) project_lister.get_status_via_ssh
##               рендерит status-JSON в таблицу (НЕ raw docker compose ps).
## @scope    Tests orchestrator.status(), DeployEngine.StatusResult, orchestrator_cli dispatch status,
##           project_lister.get_status_via_ssh (с инъекцией ssh_runner).
## @invariants
##   - No Docker, no SSH (ssh_runner инъектируется)
##   - LDD: IMP:9 лог в каждом успешном сценарии
##   - vps_status_check.py НЕ меняется (T3: уже валидирует канон)
## @rationale  DevPlan 116 B1 T3: ровно один JSON-контракт статуса; status exit-коды честные;
##             make project-status работает через status-verb.
## @changes    2026-08-01 | Created (DevPlan 116 B1 T3)
# endregion MODULE_CONTRACT

import json
import logging

import pytest

from core.internal.deploy.deploy_engine import StatusResult
from core.internal.deploy.orchestrator import DeployOrchestrator, ProjectStatus
from core.internal.deploy.orchestrator_cli import _dispatch
from core.internal.scaffold.project_lister import get_status_via_ssh
from tests.helpers.gate_helpers import assert_ldd_imp9

logger = logging.getLogger(__name__)

# Канон ProjectStatus (orchestrator.py, D6) — единственный JSON-контракт статуса
_CANON_KEYS = frozenset({"project", "status", "containers", "last_deploy"})


# T2.16a: _assert_imp9_logged консолидирован в gate_helpers.assert_ldd_imp9
# region FUNC_test_project_status_canon_keys
## @purpose — ProjectStatus.to_dict() содержит ровно канон-ключи {project, status, containers, last_deploy}.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T3 · канон-ключи ProjectStatus
# · Regression: JSON-контракт статуса расходится с каноном
# · Scenario: ProjectStatus(...).to_dict().keys() == _CANON_KEYS
# · Last fail: N/A (new test)
# · Remove if: статус-контракт меняется архитектурно
def test_project_status_canon_keys() -> None:
    """ProjectStatus.to_dict() — ровно канон-ключи (D6)."""
    st = ProjectStatus(project="p", status="found", containers=[{"name": "web"}], last_deploy={"version": "v1"})
    d = st.to_dict()
    assert set(d.keys()) == _CANON_KEYS
    assert d["project"] == "p"
    assert d["status"] == "found"


# endregion FUNC_test_project_status_canon_keys


# region FUNC_test_deploy_engine_status_result_contract
## @purpose — DeployEngine.StatusResult.to_dict() — тот же канон + node (расширение T3).
##            set-сравнение: канон ⊆ StatusResult-ключи.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T3 · StatusResult = тот же контракт
# · Regression: StatusResult расходится с ProjectStatus (поля дрейфуют)
# · Scenario: _CANON_KEYS ⊆ StatusResult.to_dict().keys(); нет лишних полей кроме node
# · Last fail: N/A (new test)
# · Remove if: статус-контракт меняется архитектурно
def test_deploy_engine_status_result_contract() -> None:
    """StatusResult.to_dict() содержит канон + node (расширение), без лишних полей."""
    sr = StatusResult(
        project="p",
        node="node1",
        status="found",
        containers=[{"name": "web"}],
        last_deploy={"version": "v1"},
    )
    d = sr.to_dict()
    assert set(d.keys()) >= _CANON_KEYS, f"StatusResult должен содержать канон-ключи, got {set(d.keys())}"
    # Единственное расширение — node
    assert set(d.keys()) - _CANON_KEYS == {"node"}
    assert d["node"] == "node1"


# endregion FUNC_test_deploy_engine_status_result_contract


# region FUNC_test_orchestrator_status_to_dict_canon
## @purpose — orchestrator.status() возвращает ProjectStatus, чей to_dict() — канон (D6).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T3 · orchestrator.status канон
# · Scenario: status(несуществующий) → ProjectStatus(status=not_found); to_dict ⊆ канон
# · Remove if: status-контракт меняется
def test_orchestrator_status_to_dict_canon(monkeypatch, tmp_path) -> None:
    """orchestrator.status() → ProjectStatus с канон-ключами (not_found)."""
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))
    orch = DeployOrchestrator(projects_base=str(tmp_path))
    st = orch.status("nonexistent")
    assert isinstance(st, ProjectStatus)
    assert st.status == "not_found"
    assert set(st.to_dict().keys()) == _CANON_KEYS


# endregion FUNC_test_orchestrator_status_to_dict_canon


# region FUNC_test_dispatch_status_exit_codes
## @purpose — exit-коды status через dispatch: found → 0, not_found → 1 (D6).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T3 · D6 exit-коды честные
# · Regression: orchestrator_cli status всегда exit 0 (ранее)
# · Scenario: dispatch "status nonexistent" → rc 1; dispatch "status <existing>" → rc 0
# · Last fail: — status всегда exit 0 (orchestrator_cli.py:212-215)
# · Remove if: status-контракт меняется
def test_dispatch_status_exit_codes(monkeypatch, capsys, tmp_path) -> None:
    """dispatch status: found → 0, not_found → 1 (D6)."""
    from core.internal.deploy.orchestrator import DeployOrchestrator as RealOrch

    # found: создаём проект в tmp
    proj_dir = tmp_path / "myproj"
    proj_dir.mkdir()
    (proj_dir / "ai-platform.yaml").write_text("name: myproj\n")

    def _factory(*args, **kwargs):
        kwargs.setdefault("projects_base", str(tmp_path))
        return RealOrch(*args, **kwargs)

    # found → exit 0
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "status myproj")
    rc_found = _dispatch([], orchestrator_factory=_factory)
    capsys.readouterr()  # сброс found-payload — парсим только not_found

    # not_found → exit 1
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "status nonexistent")
    rc_not_found = _dispatch([], orchestrator_factory=_factory)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert rc_found == 0, "found → exit 0 (D6)"
    assert rc_not_found == 1, "not_found → exit 1 (D6)"
    assert payload["status"] == "not_found"


# endregion FUNC_test_dispatch_status_exit_codes


# region FUNC_test_project_lister_renders_status_json
## @purpose — project_lister.get_status_via_ssh: ssh_runner-инъекция возвращает ProjectStatus JSON →
##            рендер таблицы содержит имя проекта + контейнеры (Name/Status/Ports), НЕ raw compose ps.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T3 · project_lister → status-verb
# · Regression: project_lister гоняет raw docker compose ps по SSH (не через status-verb)
# · Scenario: ssh_runner возвращает ProjectStatus JSON → stdout содержит project name + container
# · Last fail: — raw `docker compose ps` по SSH (U-36)
# · Remove if: project-status переходит на другой механизм
def test_project_lister_renders_status_json(monkeypatch, capsys, caplog: pytest.LogCaptureFixture) -> None:
    """get_status_via_ssh рендерит status-JSON в таблицу (Name/Status/Ports)."""
    caplog.set_level(logging.INFO)

    status_json = json.dumps({
        "project": "myproj",
        "status": "found",
        "containers": [{"Name": "myproj-web-1", "Status": "Up 5 minutes", "Ports": "0.0.0.0:8080->80/tcp"}],
        "last_deploy": None,
    })

    def _fake_ssh_runner(host: str, user: str, cmd: str, timeout: int = 10) -> str:
        # Проверяем: SSH-команда — status-verb, а НЕ raw docker compose ps (U-36)
        assert cmd == "status myproj", f"SSH-команда должна быть status-verb, got {cmd!r}"
        assert "docker compose ps" not in cmd, "raw docker compose ps не должен уходить по SSH"
        return status_json

    result = get_status_via_ssh(host="1.2.3.4", project="myproj", ssh_runner=_fake_ssh_runner)

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    assert result is True
    assert "myproj" in out, "Таблица должна содержать имя проекта"
    assert "myproj-web-1" in out, "Таблица должна содержать имя контейнера"
    assert "Up 5 minutes" in out, "Таблица должна содержать статус контейнера"
    assert "docker compose ps" not in out, "НЕ должен рендериться raw compose ps"


# endregion FUNC_test_project_lister_renders_status_json
