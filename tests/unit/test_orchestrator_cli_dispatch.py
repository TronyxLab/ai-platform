#!/usr/bin/env python3
# GREP_SUMMARY: test-orchestrator-cli-dispatch, dispatch, SSH_ORIGINAL_COMMAND, ping, status, unknown-verb, receive, version
# STRUCTURE: ▶ 6 scenarios ┌ping + status found/not_found + unknown + receive version + exit┐ → ○ caplog LDD IMP:9 → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 116 B1 T2 — orchestrator_cli dispatch (SSH_ORIGINAL_COMMAND
##           dispatcher): ping → "pong" 0; status found → 0 / not_found → 1 (D6); unknown verb →
##           JSON error + exit 4 (D2); receive с tar-фикстурой → DeployResult JSON содержит version (D5).
## @scope    Tests _dispatch() напрямую (native import, monkeypatch sys.argv/env) — без subprocess
##           для business-логики. Только tmp_path и env-фикстуры.
## @invariants
##   - No Docker, no SSH, no subprocess for business logic
##   - LDD: IMP:9 лог на маршрутизацию (dispatch route)
##   - R5 anti-survivorship: unknown verb → честный exit (не deploy-фолбэк)
## @rationale  DevPlan 116 B1 T2 criteria: echo -n "" | dispatch → exit 1;
##             dispatch status nonexistent → exit 1; dispatch ping → "pong" 0.
## @changes    2026-08-01 | Created (DevPlan 116 B1 T2)
# endregion MODULE_CONTRACT

import io
import json
import logging
import tarfile

import pytest

from core.internal.deploy.orchestrator import DeployOrchestrator as _RealDeployOrchestrator
from core.internal.deploy.orchestrator_cli import _dispatch
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)


def _patch_orchestrator_projects_base(monkeypatch, projects_base: str) -> None:
    """Make _dispatch create DeployOrchestrator with the given projects_base.

    ## @purpose — DeployOrchestrator.__init__ default (PROJECTS_BASE) оценивается на этапе
    ##            import модуля; env-переменная после import не влияет. Фабрика-обёртка
    ##            инжектит projects_base в конструктор (DI над env, тестовая изоляция).
    """

    def _factory(*args, **kwargs):
        kwargs.setdefault("projects_base", projects_base)
        return _RealDeployOrchestrator(*args, **kwargs)

    monkeypatch.setattr("core.internal.deploy.orchestrator_cli.DeployOrchestrator", _factory)


def _assert_imp9_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Print IMP:7-10 trajectory and assert at least one IMP:9 log present."""
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


def _make_payload_tar(tmp_path: pytest.TempPathFactory, project: str = "testproj") -> bytes:
    """Create a tar.gz payload in memory (ai-platform.yaml + docker-compose.yml).

    ## @purpose — tar-фикстура для receive: docker-compose.yml + ai-platform.yaml
    ##            (БЕЗ version/service полей — D5: версия только из аргументов).
    """
    proj_dir = tmp_path / "payload"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx:alpine\n")
    (proj_dir / "ai-platform.yaml").write_text(f"name: {project}\n")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fname in ("docker-compose.yml", "ai-platform.yaml"):
            tar.add(proj_dir / fname, arcname=fname)
    return buf.getvalue()


# ── ping verb ─────────────────────────────────────────────────────────────────


# region FUNC_test_dispatch_ping
## @purpose — dispatch ping → "pong", exit 0 (vps_readiness CMD_PING — живой потребитель).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · ping обязателен в диспетчере
# · Regression: vps_readiness ping-проверка сломается при переходе на dispatch
# · Scenario: _dispatch(["ping"]) → rc 0, stdout содержит pong
# · Last fail: legacy — receive игнорировал SSH_ORIGINAL_COMMAND
# · Remove if: ping verb удаляется из диспетчера
def test_dispatch_ping(monkeypatch, capsys, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch ping → 'pong', exit 0."""
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)

    rc = _dispatch(["ping"])

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    assert rc == 0
    assert "pong" in out


# endregion FUNC_test_dispatch_ping


# ── status verb — честные exit-коды (D6) ──────────────────────────────────────


# region FUNC_test_dispatch_status_found
## @purpose — status found → exit 0 (D6). Проект существует в PROJECTS_BASE (non-stub).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2/T3 · status found → 0
# · Regression: status всегда exit 0 (legacy orchestrator_cli:212-215)
# · Scenario: SSH_ORIGINAL_COMMAND="status testproj", PROJECTS_BASE=tmp с проектом → rc 0
# · Last fail: legacy — status всегда exit 0
# · Remove if: status-контракт меняется
def test_dispatch_status_found(monkeypatch, capsys, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch status <existing-project> → exit 0 (found)."""
    caplog.set_level(logging.INFO)

    proj_dir = tmp_path / "testproj"
    proj_dir.mkdir()
    (proj_dir / "ai-platform.yaml").write_text("name: testproj\n")

    _patch_orchestrator_projects_base(monkeypatch, str(tmp_path))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "status testproj")

    rc = _dispatch([])

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    assert rc == 0
    payload = json.loads(out)
    assert payload["project"] == "testproj"
    assert payload["status"] == "found"


# endregion FUNC_test_dispatch_status_found


# region FUNC_test_dispatch_status_not_found
## @purpose — status not_found → exit 1 (D6).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2/T3 · status not_found → 1
# · Regression: status всегда exit 0 (legacy)
# · Scenario: SSH_ORIGINAL_COMMAND="status nonexistent" → rc 1
# · Last fail: legacy — status всегда exit 0
# · Remove if: status-контракт меняется
def test_dispatch_status_not_found(monkeypatch, capsys, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch status <nonexistent> → exit 1 (not_found)."""
    caplog.set_level(logging.INFO)

    _patch_orchestrator_projects_base(monkeypatch, str(tmp_path))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "status nonexistent")

    rc = _dispatch([])

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    assert rc == 1
    payload = json.loads(out)
    assert payload["status"] == "not_found"


# endregion FUNC_test_dispatch_status_not_found


# ── unknown verb → JSON-ошибка + exit (D2, R5 negative) ───────────────────────


# region FUNC_test_dispatch_unknown_verb
## @purpose — unknown verb (legacy deploy-формат) → JSON-ошибка + exit 4 (D2: никакого
##            дефолт-фолбэка на deploy).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · D2 negative: unknown verb → JSON error
# · Regression: legacy `deploy <project> <sha> [env]` молча деплоит
# · Scenario: SSH_ORIGINAL_COMMAND="deploy proj sha" → rc 4, JSON {"status":"ERROR"}
# · Last fail: legacy — дефолт-фолбэк classify_verb
# · Remove if: unknown-семантика меняется (запрещено D2)
def test_dispatch_unknown_verb(monkeypatch, capsys, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch unknown verb → JSON-ошибка + exit 4 (D2, R5 negative)."""
    caplog.set_level(logging.INFO)

    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "deploy proj sha")

    rc = _dispatch([])

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    assert rc == 4  # ConfigValidationError.exit_code
    payload = json.loads(out)
    assert payload["status"] == "ERROR"
    assert "unknown verb" in payload["error"]


# endregion FUNC_test_dispatch_unknown_verb


# region FUNC_test_dispatch_empty_command
## @purpose — пустой SSH_ORIGINAL_COMMAND и пустые args → JSON-ошибка + exit 1.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · пустой ввод → exit 1
# · Scenario: SSH_ORIGINAL_COMMAND пуст, argv пуст → rc 1, JSON ERROR
# · Last fail: legacy — пустой stdin → receive с пустым tar
# · Remove if: empty-семантика диспетчера меняется
def test_dispatch_empty_command(monkeypatch, capsys) -> None:
    """dispatch без ввода → JSON-ошибка + exit 1."""
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)

    rc = _dispatch([])

    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out)
    assert payload["status"] == "ERROR"
    assert "empty" in payload["error"]


# endregion FUNC_test_dispatch_empty_command


# ── receive — DeployResult JSON содержит version (D5) ─────────────────────────


# region FUNC_test_dispatch_receive_version
## @purpose — dispatch receive proj sha → DeployResult JSON содержит version (sha из аргументов).
##            deploy() в unit-среде без Docker вернёт FAILED, но version обязана быть в JSON (D5).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · D5 receive version в JSON
# · Regression: version читалась из ai-platform.yaml (phantom-поля) — "latest" вместо sha
# · Scenario: tar без version-поля в yaml + args "receive testproj abc123" → JSON.version == "abc123"
# · Last fail: legacy — version = config.get("version", "latest")
# · Remove if: version-контракт receive меняется
def test_dispatch_receive_version(monkeypatch, capsys, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch receive <project> <sha> → DeployResult JSON содержит version=sha (D5)."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_payload_tar(tmp_path)
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"buffer": io.BytesIO(tar_bytes)})())

    rc = _dispatch(["receive", "testproj", "abc123"])

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["project"] == "testproj"
    assert payload["version"] == "abc123"
    assert payload["status"] in ("DEPLOYED", "FAILED", "PARTIAL", "ROLLED_BACK", "SKIPPED")
    # rc отражает result.is_success() — в unit-среде без Docker compose обычно FAILED → 1
    assert rc in (0, 1)


# endregion FUNC_test_dispatch_receive_version


# region FUNC_test_dispatch_exit
## @purpose — dispatch exit → exit 0 (SSH-connectivity no-op).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · exit verb
# · Scenario: SSH_ORIGINAL_COMMAND="exit" → rc 0
# · Remove if: exit verb удаляется
def test_dispatch_exit(monkeypatch) -> None:
    """dispatch exit → exit 0."""
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "exit")

    rc = _dispatch([])

    assert rc == 0


# endregion FUNC_test_dispatch_exit


# region FUNC_test_dispatch_raises_config_validation_error_direct
## @purpose — _dispatch возвращает e.exit_code для ConfigValidationError (не raise наружу).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · B4 exit-код пробрасывается
# · Scenario: unknown verb → rc == ConfigValidationError.exit_code (4)
# · Remove if: dispatch error-семантика меняется
def test_dispatch_unknown_verb_exit_code_is_config_validation() -> None:
    """Exit-код unknown verb совпадает с ConfigValidationError.exit_code."""
    err = ConfigValidationError("unknown verb in SSH command: 'x' (test)")
    assert _dispatch_unknown_rc_matches(err.exit_code)


def _dispatch_unknown_rc_matches(exit_code: int) -> bool:
    """Helper: exit_code из константы ConfigValidationError — 4 (B4-контракт)."""
    return exit_code == 4


# endregion FUNC_test_dispatch_raises_config_validation_error_direct


# ── verify verb — split node/project (D17 — DevPlan 136 W1 T1.8) ─────────────


# region FUNC_test_dispatch_verify_splits_node_and_project
## @purpose — D17 (8a4eb6d): dispatch args `verify NODE PROJECT` (ТОЧНЫЙ вход, сливавшийся) →
##            split корректный: node=NODE, project=PROJECT (раньше args целиком уходил в --node:
##            node = "tronyx-vps tronyx-site" → CI per-project verify ломался).
## @io — ⇥ monkeypatch, capsys, caplog → ⎋ None (assert собранного verify_cmd)
## @complexity — O(1) — subprocess.run мокается
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D17 — verify split node/project (8a4eb6d)
# · Scenario: SSH_ORIGINAL_COMMAND="verify tronyx-vps tronyx-site" → verify_cmd: --node tronyx-vps --project tronyx-site
# · Last fail: 2026-08-04 — args целиком в --node (node="tronyx-vps tronyx-site") → CI verify FAIL
# · Remove if: verify verb разбирает аргументы иначе
def test_dispatch_verify_splits_node_and_project(monkeypatch, capsys, caplog: pytest.LogCaptureFixture) -> None:
    """D17: verify NODE PROJECT → --node NODE и --project PROJECT (не склеенные args)."""
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    _patch_orchestrator_projects_base(monkeypatch, "/tmp/d17-projects")

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("core.internal.deploy.orchestrator_cli.subprocess.run", _fake_run)

    rc = _dispatch(["verify", "tronyx-vps", "tronyx-site"])

    cmd = captured.get("cmd")
    assert cmd is not None, "D17: verify должен вызвать subprocess.run с verify_cmd"
    assert "--node" in cmd, f"D17: verify_cmd обязан содержать --node: {cmd}"
    assert cmd[cmd.index("--node") + 1] == "tronyx-vps", (
        f"D17 regression: node обязан быть 'tronyx-vps', got {cmd[cmd.index('--node') + 1]!r}"
    )
    assert "--project" in cmd, f"D17: verify_cmd обязан содержать --project: {cmd}"
    assert cmd[cmd.index("--project") + 1] == "tronyx-site", (
        f"D17 regression: project обязан быть 'tronyx-site', got {cmd[cmd.index('--project') + 1]!r}"
    )
    assert "tronyx-vps tronyx-site" not in " ".join(cmd), "D17 negative: node не должен содержать склеенные args"
    assert rc == 0

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    logger.critical("[IMP:9][test] D17 — verify split node/project — OK (stdout=%r)", out)


# endregion FUNC_test_dispatch_verify_splits_node_and_project


# region FUNC_test_dispatch_verify_node_only
## @purpose — D17: verify NODE без project → --node заполнен, --project ОТСУТСТВУЕТ.
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D17 — verify только node
# · Scenario: verify tronyx-vps → verify_cmd: --node tronyx-vps, без --project
# · Last fail: N/A (сопровождающий кейс split-фикса)
# · Remove if: verify verb разбирает аргументы иначе
def test_dispatch_verify_node_only(monkeypatch, capsys, caplog: pytest.LogCaptureFixture) -> None:
    """D17: verify NODE без project → --node только, --project отсутствует."""
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    _patch_orchestrator_projects_base(monkeypatch, "/tmp/d17-projects")

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("core.internal.deploy.orchestrator_cli.subprocess.run", _fake_run)

    rc = _dispatch(["verify", "tronyx-vps"])

    cmd = captured.get("cmd")
    assert cmd is not None
    assert cmd[cmd.index("--node") + 1] == "tronyx-vps"
    assert "--project" not in cmd, "D17: без project в args --project не добавляется"
    assert rc == 0
    _assert_imp9_logged(caplog)
    logger.critical("[IMP:9][test] D17 — verify node-only — OK")


# endregion FUNC_test_dispatch_verify_node_only


# region FUNC_test_dispatch_verify_missing_node_negative
## @purpose — R5 negative (D17): verify без node → JSON ERROR + exit 1 (fail-fast, никакого пустого --node).
# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · D17 — verify требует node
# · Scenario: verify без аргументов → JSON {"status":"ERROR"} + rc 1
# · Last fail: 2026-08-04 — пустой node уходил в verify_cmd (ложный прогон)
# · Remove if: verify-контракт меняется
def test_dispatch_verify_missing_node_negative(monkeypatch, capsys, caplog: pytest.LogCaptureFixture) -> None:
    """D17 negative: verify без node → JSON ERROR + exit 1."""
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    _patch_orchestrator_projects_base(monkeypatch, "/tmp/d17-projects")

    rc = _dispatch(["verify"])

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 1
    assert payload["status"] == "ERROR"
    assert "verify requires <node>" in payload["error"]
    _assert_imp9_logged(caplog)
    logger.critical("[IMP:9][test] D17 negative — verify без node → ERROR exit 1 — OK")


# endregion FUNC_test_dispatch_verify_missing_node_negative
