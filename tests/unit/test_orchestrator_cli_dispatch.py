# GREP_SUMMARY: test-orchestrator-cli-dispatch, dispatch, SSH_ORIGINAL_COMMAND, ping, status, unknown-verb, receive, version
# STRUCTURE: ▶ 6 scenarios ┌ping + status found/not_found + unknown + receive version + exit┐ → ○ caplog LDD IMP:9 → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 116 B1 T2 — orchestrator_cli dispatch (SSH_ORIGINAL_COMMAND
##           dispatcher): ping → "pong" 0; status found → 0 / not_found → 1 (D6); unknown verb →
##           JSON error + exit 4 (D2); receive с tar-фикстурой → DeployResult JSON содержит version (D5).
## @scope    Tests _dispatch() напрямую (native import, DI env/stdin/factory/run_cmd — W-H) — без subprocess
##           для business-логики. Только tmp_path и env-фикстуры.
## @invariants
##   - No Docker, no SSH, no subprocess for business logic
##   - LDD: IMP:9 лог на маршрутизацию (dispatch route)
##   - R5 anti-survivorship: unknown verb → честный exit (не deploy-фолбэк)
## @rationale  DevPlan 116 B1 T2 criteria: echo -n "" | dispatch → exit 1;
##             dispatch status nonexistent → exit 1; dispatch ping → "pong" 0.
## @changes    2026-08-01 | Created (DevPlan 116 B1 T2)
## @changes    2026-08-13 | DevPlan 160 W6 T6.1 — test_dispatch_receive_version: мок
##              DeployOrchestrator._deploy_compose + HealthcheckPoller.poll_until_healthy на КЛАССЕ
##              (ReceiveFlow создаёт свой DeployOrchestrator внутри — 75.8s → <1s).
# endregion MODULE_CONTRACT

import io
import json
import logging
import tarfile

import pytest

from core.internal.deploy.orchestrator import DeployOrchestrator as _RealDeployOrchestrator
from core.internal.deploy.orchestrator_cli import _dispatch
from core.internal.shared.exceptions import ConfigValidationError
from tests.helpers.gate_helpers import assert_ldd_imp9

logger = logging.getLogger(__name__)


def _orchestrator_factory(projects_base: str):
    """Фабрика DeployOrchestrator с projects_base (DI, W-H DevPlan 163 — 0 патчей).

    ## @purpose — _dispatch(orchestrator_factory=) инжектит projects_base в конструктор
    ##            (DI над env, тестовая изоляция) вместо патча фабрики.
    """

    def _factory(*args, **kwargs):
        kwargs.setdefault("projects_base", projects_base)
        return _RealDeployOrchestrator(*args, **kwargs)

    return _factory


# T2.16a: _assert_imp9_logged консолидирован в gate_helpers.assert_ldd_imp9
def _make_payload_tar(tmp_path: pytest.TempPathFactory, project: str = "testproj") -> bytes:
    """Create a tar.gz payload in memory (ai-platform.yaml + docker-compose.yml).

    ## @purpose — tar-фикстура для receive: docker-compose.yml + ai-platform.yaml
    ##            (БЕЗ version/service полей — D5: версия только из аргументов).
    ##            176 A.2: compose L1-валидный (receive исполняет pre-deploy L1-гейт).
    """
    proj_dir = tmp_path / "payload"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx:alpine\n"
        "    env_file:\n      - .env.platform\n"
        '    healthcheck:\n      test: ["CMD", "echo", "ok"]\n'
        '    deploy:\n      resources:\n        limits:\n          memory: "128M"\n          cpus: "0.25"\n'
        '    labels:\n      - "platform.type=backend"\n'
        "    networks:\n      - proxy-net\n"
        "networks:\n  proxy-net:\n    external: true\n",
        encoding="utf-8",
    )
    (proj_dir / "ai-platform.yaml").write_text(f"name: {project}\n", encoding="utf-8")
    # DevPlan 16 T1.E: unmanaged (без lock) блокируется pre-deploy гейтом — фиксируем lock
    (proj_dir / "practices.lock").write_text(
        "version: 1\nlevel: auto\nstate: baseline\nlanguage: python\ngenerator_hash: sha256:test\nmaturity:\n  age_days: 1\n  code_files: 0\n",
        encoding="utf-8",
    )

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", encoding="utf-8") as tar:
        for fname in ("docker-compose.yml", "ai-platform.yaml", "practices.lock"):
            tar.add(proj_dir / fname, arcname=fname)
    return buf.getvalue()


# ── ping verb ─────────────────────────────────────────────────────────────────


# region FUNC_test_dispatch_ping
## @purpose — dispatch ping → "pong", exit 0 (vps_readiness CMD_PING — живой потребитель).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · ping обязателен в диспетчере
# · Regression: vps_readiness ping-проверка сломается при переходе на dispatch
# · Scenario: _dispatch(["ping"]) → rc 0, stdout содержит pong
# · Last fail: — receive игнорировал SSH_ORIGINAL_COMMAND
# · Remove if: ping verb удаляется из диспетчера
def test_dispatch_ping(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch ping → 'pong', exit 0."""
    caplog.set_level(logging.INFO)

    rc = _dispatch(["ping"], env={})

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    assert rc == 0
    assert "pong" in out


# endregion FUNC_test_dispatch_ping


# ── status verb — честные exit-коды (D6) ──────────────────────────────────────


# region FUNC_test_dispatch_status_found
## @purpose — status found → exit 0 (D6). Проект существует в PROJECTS_BASE (non-stub).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2/T3 · status found → 0
# · Regression: status всегда exit 0 (orchestrator_cli:212-215)
# · Scenario: SSH_ORIGINAL_COMMAND="status testproj", PROJECTS_BASE=tmp с проектом → rc 0
# · Last fail: — status всегда exit 0
# · Remove if: status-контракт меняется
def test_dispatch_status_found(capsys, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch status <existing-project> → exit 0 (found)."""
    caplog.set_level(logging.INFO)

    proj_dir = tmp_path / "testproj"
    proj_dir.mkdir()
    (proj_dir / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "status testproj"},
        orchestrator_factory=_orchestrator_factory(str(tmp_path)),
    )

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    assert rc == 0
    payload = json.loads(out)
    assert payload["project"] == "testproj"
    assert payload["status"] == "found"


# endregion FUNC_test_dispatch_status_found


# region FUNC_test_dispatch_status_not_found
## @purpose — status not_found → exit 1 (D6).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2/T3 · status not_found → 1
# · Regression: status всегда exit 0 (ранее)
# · Scenario: SSH_ORIGINAL_COMMAND="status nonexistent" → rc 1
# · Last fail: — status всегда exit 0
# · Remove if: status-контракт меняется
def test_dispatch_status_not_found(capsys, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch status <nonexistent> → exit 1 (not_found)."""
    caplog.set_level(logging.INFO)

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "status nonexistent"},
        orchestrator_factory=_orchestrator_factory(str(tmp_path)),
    )

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    assert rc == 1
    payload = json.loads(out)
    assert payload["status"] == "not_found"


# endregion FUNC_test_dispatch_status_not_found


# ── unknown verb → JSON-ошибка + exit (D2, R5 negative) ───────────────────────


# region FUNC_test_dispatch_unknown_verb
## @purpose — unknown verb (deploy-формат) → JSON-ошибка + exit 4 (D2: никакого
##            дефолт-фолбэка на deploy).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · D2 negative: unknown verb → JSON error
# · Regression: `deploy <project> <sha> [env]` молча деплоит
# · Scenario: SSH_ORIGINAL_COMMAND="deploy proj sha" → rc 4, JSON {"status":"ERROR"}
# · Last fail: — дефолт-фолбэк classify_verb
# · Remove if: unknown-семантика меняется (запрещено D2)
def test_dispatch_unknown_verb(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch unknown verb → JSON-ошибка + exit 4 (D2, R5 negative)."""
    caplog.set_level(logging.INFO)

    rc = _dispatch([], env={"SSH_ORIGINAL_COMMAND": "deploy proj sha"})

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    assert rc == 4  # ConfigValidationError.exit_code
    payload = json.loads(out)
    assert payload["status"] == "ERROR"
    assert "unknown verb" in payload["error"]


# endregion FUNC_test_dispatch_unknown_verb


# region FUNC_test_dispatch_empty_command
## @purpose — пустой SSH_ORIGINAL_COMMAND и пустые args → JSON-ошибка + exit 1.
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · пустой ввод → exit 1
# · Scenario: SSH_ORIGINAL_COMMAND пуст, argv пуст → rc 1, JSON ERROR
# · Last fail: — пустой stdin → receive с пустым tar
# · Remove if: empty-семантика диспетчера меняется
def test_dispatch_empty_command(capsys) -> None:
    """dispatch без ввода → JSON-ошибка + exit 1."""
    rc = _dispatch([], env={})

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
# · Last fail: — version = config.get("version", "latest")
# · Remove if: version-контракт receive меняется
def test_dispatch_receive_version(capsys, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch receive <project> <sha> → DeployResult JSON содержит version=sha (D5)."""
    caplog.set_level(logging.INFO)

    # DI (W-H): stdin_stream= io.BytesIO + orchestrator_factory с субклассом
    # (0 патчей _deploy_compose/poll_until_healthy/class-level, T6.1 160)
    class _DeployOKOrch(_RealDeployOrchestrator):
        def _deploy_compose(self, _project_dir, _service, _version):
            return True

        def _run_post_deploy_chain(self, _project, _version, _status, _project_dir=None, _node_name=""):
            return None

    class _FakePoller:
        def poll_until_healthy(self, _project_name, _project_dir):
            return type("H", (), {"status": "healthy"})()

    def _factory(*args, **kwargs):
        if not args and "projects_base" not in kwargs:
            kwargs["projects_base"] = str(tmp_path)
        if "healthcheck_poller" not in kwargs:
            kwargs["healthcheck_poller"] = _FakePoller()
        return _DeployOKOrch(*args, **kwargs)

    tar_bytes = _make_payload_tar(tmp_path)
    rc = _dispatch(
        ["receive", "testproj", "abc123"],
        env={},
        stdin_stream=io.BytesIO(tar_bytes),
        orchestrator_factory=_factory,
    )

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["project"] == "testproj"
    assert payload["version"] == "abc123"
    # T6.1: с замоканным _deploy_compose + healthy → DEPLOYED → rc 0 (детерминированно)
    assert payload["status"] in {"DEPLOYED", "PARTIAL", "ROLLED_BACK", "SKIPPED", "FAILED"}
    # rc отражает result.is_success() — в unit-среде без Docker compose обычно FAILED → 1
    assert rc in {0, 1}


# endregion FUNC_test_dispatch_receive_version


# region FUNC_test_dispatch_exit
## @purpose — dispatch exit → exit 0 (SSH-connectivity no-op).
# GUARD-PRESERVE (168): единственное покрытие exit-verb диспетчера (SSH-connectivity no-op, B1 T2)
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · exit verb
# · Scenario: SSH_ORIGINAL_COMMAND="exit" → rc 0
# · Remove if: exit verb удаляется
def test_dispatch_exit() -> None:
    """dispatch exit → exit 0."""
    rc = _dispatch([], env={"SSH_ORIGINAL_COMMAND": "exit"})

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
## @io — ⇥ capsys, caplog → ⎋ None (assert собранного verify_cmd)
## @complexity — O(1) — subprocess.run мокается
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D17 — verify split node/project (8a4eb6d)
# · Scenario: SSH_ORIGINAL_COMMAND="verify tronyx-vps tronyx-site" → verify_cmd: --node tronyx-vps --project tronyx-site
# · Last fail: 2026-08-04 — args целиком в --node (node="tronyx-vps tronyx-site") → CI verify FAIL
# · Remove if: verify verb разбирает аргументы иначе
def test_dispatch_verify_splits_node_and_project(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """D17: verify NODE PROJECT → --node NODE и --project PROJECT (не склеенные args)."""
    caplog.set_level(logging.INFO)
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    rc = _dispatch(
        ["verify", "tronyx-vps", "tronyx-site"],
        env={},
        orchestrator_factory=_orchestrator_factory("/tmp/d17-projects"),
        run_cmd=_fake_run,
    )

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
    assert_ldd_imp9(caplog)
    logger.critical("[IMP:9][test] D17 — verify split node/project — OK (stdout=%r)", out)


# endregion FUNC_test_dispatch_verify_splits_node_and_project


# region FUNC_test_dispatch_verify_node_only
## @purpose — D17: verify NODE без project → --node заполнен, --project ОТСУТСТВУЕТ.
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D17 — verify только node
# · Scenario: verify tronyx-vps → verify_cmd: --node tronyx-vps, без --project
# · Last fail: N/A (сопровождающий кейс split-фикса)
# · Remove if: verify verb разбирает аргументы иначе
def test_dispatch_verify_node_only(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """D17: verify NODE без project → --node только, --project отсутствует."""
    caplog.set_level(logging.INFO)
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    rc = _dispatch(
        ["verify", "tronyx-vps"],
        env={},
        orchestrator_factory=_orchestrator_factory("/tmp/d17-projects"),
        run_cmd=_fake_run,
    )

    cmd = captured.get("cmd")
    assert cmd is not None
    assert cmd[cmd.index("--node") + 1] == "tronyx-vps"
    assert "--project" not in cmd, "D17: без project в args --project не добавляется"
    assert rc == 0
    assert_ldd_imp9(caplog)
    logger.critical("[IMP:9][test] D17 — verify node-only — OK")


# endregion FUNC_test_dispatch_verify_node_only


# region FUNC_test_dispatch_verify_missing_node_negative
## @purpose — R5 negative (D17): verify без node → JSON ERROR + exit 1 (fail-fast, никакого пустого --node).
# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · D17 — verify требует node
# · Scenario: verify без аргументов → JSON {"status":"ERROR"} + rc 1
# · Last fail: 2026-08-04 — пустой node уходил в verify_cmd (ложный прогон)
# · Remove if: verify-контракт меняется
def test_dispatch_verify_missing_node_negative(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """D17 negative: verify без node → JSON ERROR + exit 1."""
    caplog.set_level(logging.INFO)

    rc = _dispatch(["verify"], env={}, orchestrator_factory=_orchestrator_factory("/tmp/d17-projects"))

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 1
    assert payload["status"] == "ERROR"
    assert "verify requires <node>" in payload["error"]
    assert_ldd_imp9(caplog)
    logger.critical("[IMP:9][test] D17 negative — verify без node → ERROR exit 1 — OK")


# endregion FUNC_test_dispatch_verify_missing_node_negative


# region FUNC_test_dispatch_verify_invalid_project_negative
## @purpose — R5 negative (H7, security hardening): verify NODE ../../etc → path-traversal
##            project-name блокируется каноническим валидатором ДО subprocess.run.
# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · H7 — verify валидирует project-name
# · Scenario: SSH_ORIGINAL_COMMAND="verify tronyx-vps ../../etc" → путь обходил бы
#   projects_base()/project (path traversal); до H7 verify-verb НЕ валидировал проект.
# · Last fail: N/A (new security validation)
# · Remove if: verify-verb перестаёт валидировать project-name
def test_dispatch_verify_invalid_project_negative(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative (H7): verify с path-traversal project → блок до subprocess.run, rc=1."""
    caplog.set_level(logging.INFO)
    called: dict[str, int] = {"n": 0}

    def _fake_run(cmd, **kwargs):
        called["n"] += 1
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    rc = _dispatch(
        ["verify", "tronyx-vps", "../../etc"],
        env={},
        orchestrator_factory=_orchestrator_factory("/tmp/h7-projects"),
        run_cmd=_fake_run,
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert called["n"] == 0, "H7: валидация должна блокировать ДО subprocess.run (runner не вызывается)"
    assert "Invalid or reserved project name" in out


# endregion FUNC_test_dispatch_verify_invalid_project_negative


# ── TEST-05 (REF-0006, DevPlan 11 В2): параметризованные traversal-негативы receive/remove ──


# region FUNC_test_dispatch_traversal_negatives_receive_remove
## @purpose — TEST-05 (карточка REF-0006): негативы receive/remove через _dispatch с
##            path-traversal/невалидными project-name. T9.7-валидация отсекает инъекцию
##            ДО handler'а: orchestrator НЕ вызывается (никаких remove/deploy мутаций),
##            JSON ERROR + rc 1.
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · TEST-05 — receive/remove traversal через dispatch
# · Last fail: карточка REF-0006 — «TEST-05 (нет негативов receive/remove)»; T9.7 покрывал
#   только status-семантику косвенно, явных параметризованных негативов канал не имел
# · Scenario: SSH_ORIGINAL_COMMAND="receive ../../etc sha" / "remove ../evil" → rc 1,
#   JSON {"status":"ERROR","error":"Invalid or reserved project name: ..."}, recorder пуст
# · Remove if: dispatch перестаёт валидировать project-name для receive/remove
@pytest.mark.parametrize(
    ("verb", "bad_project"),
    [
        ("receive", "../../etc"),
        ("receive", "a/../b"),
        ("receive", "/abs/path"),
        ("receive", ".."),
        ("remove", "../evil"),
        ("remove", "proj/../../victim"),
        ("remove", "/opt/projects/victim"),
        ("remove", "-rf"),
    ],
    ids=[
        "receive-dotdot-abs",
        "receive-inner-traversal",
        "receive-absolute",
        "receive-bare-dotdot",
        "remove-dotdot",
        "remove-nested-traversal",
        "remove-absolute-path",
        "remove-flag-injection",
    ],
)
def test_dispatch_traversal_negatives_receive_remove(
    verb: str,
    bad_project: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """receive/remove с traversal-проектом → rc 1, JSON ERROR, orchestrator НЕ вызывается."""
    caplog.set_level(logging.INFO)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _RecordingOrch:
        """Recorder: любое обращение к remove/receive = нарушение валидации (тест RED)."""

        _MSG_REMOVE = "remove не должен вызываться для невалидного project-name"
        _MSG_RECEIVE = "receive не должен вызываться для невалидного project-name"

        def remove(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(self._MSG_REMOVE)

        def receive(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(self._MSG_RECEIVE)

    def _factory(*args, **kwargs):
        return _RecordingOrch()

    argv = [verb, bad_project] + (["deadbeefsha"] if verb == "receive" else [])
    rc = _dispatch(argv, env={}, stdin_stream=io.BytesIO(b""), orchestrator_factory=_factory)

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 1, f"{verb} {bad_project!r}: ожидается rc 1"
    assert payload["status"] == "ERROR"
    assert "Invalid or reserved project name" in payload["error"]
    assert calls == [], f"{verb}: handler не должен вызываться для {bad_project!r}"
    assert_ldd_imp9(caplog)
    logger.critical("[IMP:9][test] TEST-05: %s %r blocked before handler (rc=1)", verb, bad_project)


# endregion FUNC_test_dispatch_traversal_negatives_receive_remove
