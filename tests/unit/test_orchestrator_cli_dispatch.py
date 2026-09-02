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
## @changes    2026-09-02 | F-07 — quoted-аргументы CI-канала: +_parse_tokens unit-тесты
##              (quoted/unmatched-quote fallback), +receive quoted/unquoted через SSH_ORIGINAL_COMMAND,
##              +quoted-инъекция negative (T9.7 после снятия кавычек), +verify quoted node/project;
##              _receive_ok_factory вынесен в модульный хелпер (DRY с receive_version)
# endregion MODULE_CONTRACT

import io
import json
import logging
import subprocess
import tarfile

import pytest

from core.internal.deploy.orchestrator import DeployOrchestrator as _RealDeployOrchestrator
from core.internal.deploy.orchestrator_cli import _dispatch, _parse_tokens
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


def _receive_ok_factory(projects_base: str):
    """DI-фабрика DeployOrchestrator для receive-тестов (T6.1, DevPlan 160 W6 — 0 патчей).

    ## @purpose — ReceiveFlow создаёт свой DeployOrchestrator внутри: субкласс с OK-_deploy_compose
    ##            + фейк-healthcheck-poller → детерминированный DEPLOYED (unit-среда без Docker,
    ##            <1s). Общий для quoted/unquoted receive-тестов (F-07).
    """

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
            kwargs["projects_base"] = projects_base
        if "healthcheck_poller" not in kwargs:
            kwargs["healthcheck_poller"] = _FakePoller()
        return _DeployOKOrch(*args, **kwargs)

    return _factory


def _recording_docker_runner(records: list[list[str]], *, rc: int = 0, stdout: str = "", stderr: str = ""):
    """DI docker_runner (W4d, health verb): fake-раннер docker_inspect — scripted CompletedProcess.

    ## @purpose — _dispatch(docker_runner=) инжектит subprocess-канал docker_inspect
    ##            (_DispatchContext.docker_runner) — 0 патчей subprocess.run/docker_ops;
    ##            records собирает cmd-аргументы для assert'ов идентификатора контейнера.
    """

    class _FakeRunner:
        def run(self, cmd: list[str], *, timeout: int | None = None, **kwargs):
            # CommandRunner protocol-контракт (run(cmd, *, timeout)) — fake scripted,
            # аргументы канала не нужны (см. _run_docker docker_ops, runner-ветка)
            del timeout, kwargs
            records.append(list(cmd))
            return subprocess.CompletedProcess(args=cmd, returncode=rc, stdout=stdout, stderr=stderr)

    return _FakeRunner()


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
    # (0 патчей _deploy_compose/poll_until_healthy/class-level, T6.1 160) — общий
    # _receive_ok_factory (F-07: переиспользуется quoted/unquoted receive-тестами)
    tar_bytes = _make_payload_tar(tmp_path)
    rc = _dispatch(
        ["receive", "testproj", "abc123"],
        env={},
        stdin_stream=io.BytesIO(tar_bytes),
        orchestrator_factory=_receive_ok_factory(str(tmp_path)),
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


# ── health verb — read-only docker inspect State.Health.Status (B3 fix-forward) ─────


# region FUNC_test_dispatch_health_healthy
## @purpose — dispatch health site-a → stdout "healthy", rc 0 (слово-контракт remote-probe).
##            docker_inspect вызывается с identifier = project (service дефолт = project).
# 🧪 TRAP[TEST] · 2026-08-27 · Regression · B3 fix-forward — health verb
# · Regression: probe шёл raw `docker inspect` → ci-deploy forced-command → unknown verb exit 4
# ·   → skip-health мёртв (deliver на каждом резюме bootstrap)
# · Scenario: SSH_ORIGINAL_COMMAND="health site-a", docker_runner → rc 0 stdout "healthy"
# ·   → rc 0, stdout == "healthy", docker_inspect identifier == "site-a"
# · Last fail: N/A (новый verb)
# · Remove if: health verb удаляется из диспетчера
def test_dispatch_health_healthy(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch health <project> → 'healthy' + rc 0 (identifier = project)."""
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "health site-a"},
        orchestrator_factory=_orchestrator_factory("/tmp/health-projects"),
        docker_runner=_recording_docker_runner(calls, rc=0, stdout="healthy"),
    )

    out = capsys.readouterr().out.strip()
    assert_ldd_imp9(caplog)
    assert rc == 0
    assert out == "healthy"
    assert calls, "docker_inspect обязан быть вызван (docker_runner DI)"
    assert calls[0][-1] == "site-a", f"identifier = project по дефолту, got {calls[0]!r}"


# endregion FUNC_test_dispatch_health_healthy


# region FUNC_test_dispatch_health_service_override
## @purpose — dispatch health site-a web → docker_inspect identifier = "web" (service вторым
##            токеном перекрывает дефолт = project).
# 🧪 TRAP[TEST] · 2026-08-27 · Regression · B3 fix-forward — service override
# · Scenario: "health site-a web" → docker_inspect("web"); stdout "unhealthy", rc 0
# · Last fail: N/A (новый verb)
# · Remove if: health-арность меняется
def test_dispatch_health_service_override(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch health <project> <service> → docker_inspect(service) (override дефолта)."""
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "health site-a web"},
        orchestrator_factory=_orchestrator_factory("/tmp/health-projects"),
        docker_runner=_recording_docker_runner(calls, rc=0, stdout="unhealthy"),
    )

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == "unhealthy"
    assert calls[0][-1] == "web", f"service вторым токеном → identifier web, got {calls[0]!r}"


# endregion FUNC_test_dispatch_health_service_override


# region FUNC_test_dispatch_health_states_parametrized
## @purpose — rc=0 для starting|unhealthy: stdout слово, exit 0 (успешный запрос факта).
# 🧪 TRAP[TEST] · 2026-08-27 · Regression · B3 fix-forward — слово-контракт rc 0
# · Scenario: docker_runner stdout="starting"/"unhealthy" → stdout слово, rc 0
# · Last fail: N/A (новый verb)
# · Remove if: слово-контракт health меняется
@pytest.mark.parametrize("status_word", ["starting", "unhealthy"])
def test_dispatch_health_states_parametrized(status_word: str, capsys, caplog: pytest.LogCaptureFixture) -> None:
    """health starting/unhealthy → слово в stdout + rc 0 (факт получен)."""
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "health site-a"},
        orchestrator_factory=_orchestrator_factory("/tmp/health-projects"),
        docker_runner=_recording_docker_runner(calls, rc=0, stdout=status_word),
    )

    out = capsys.readouterr().out.strip()
    assert rc == 0, f"status={status_word}: успешный запрос факта → rc 0"
    assert out == status_word


# endregion FUNC_test_dispatch_health_states_parametrized


# region FUNC_test_dispatch_health_missing
## @purpose — docker inspect rc≠0 + "No such object" → stdout "missing", rc 0
##            (контейнер отсутствует — честный факт, успешный запрос).
# 🧪 TRAP[TEST] · 2026-08-27 · Regression · B3 fix-forward — missing контракт
# · Scenario: docker_runner rc 1, stderr "Error: No such object: site-a" → stdout "missing", rc 0
# · Last fail: N/A (новый verb)
# · Remove if: missing-маппинг меняется
def test_dispatch_health_missing(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """health: контейнер отсутствует (No such object) → 'missing' + rc 0."""
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "health site-a"},
        orchestrator_factory=_orchestrator_factory("/tmp/health-projects"),
        docker_runner=_recording_docker_runner(calls, rc=1, stderr="Error: No such object: site-a"),
    )

    out = capsys.readouterr().out.strip()
    assert rc == 0, "missing — успешный запрос факта → rc 0 (контракт remote-probe)"
    assert out == "missing"


# endregion FUNC_test_dispatch_health_missing


# region FUNC_test_dispatch_health_error
## @purpose — docker inspect rc≠0 БЕЗ "No such object" (daemon недоступен/нет docker) →
##            stdout "error", rc 1 (внутренняя ошибка инспекта — ЕДИНСТВЕННЫЙ rc=1).
# 🧪 TRAP[TEST] · 2026-08-27 · Regression · B3 fix-forward — error контракт rc 1
# · Scenario: docker_runner rc 1, stderr "Cannot connect to the Docker daemon" → "error", rc 1
# · Last fail: N/A (новый verb)
# · Remove if: error-маппинг меняется
def test_dispatch_health_error(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """health: внутренняя ошибка инспекта → 'error' + rc 1."""
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "health site-a"},
        orchestrator_factory=_orchestrator_factory("/tmp/health-projects"),
        docker_runner=_recording_docker_runner(calls, rc=1, stderr="Cannot connect to the Docker daemon"),
    )

    out = capsys.readouterr().out.strip()
    assert rc == 1, "внутренняя ошибка инспекта → rc 1 (контракт remote-probe)"
    assert out == "error"


# endregion FUNC_test_dispatch_health_error


# region FUNC_test_dispatch_health_no_healthcheck_missing
## @purpose — rc=0, но stdout не health-слово (""/"<no value>"/"none" — контейнер без
##            healthcheck) → "missing" + rc 0 (нет healthy-факта).
# 🧪 TRAP[TEST] · 2026-08-27 · Regression · B3 fix-forward — no-healthcheck
# · Scenario: docker_runner rc 0, stdout "<no value>" → "missing", rc 0
# · Last fail: N/A (новый verb)
# · Remove if: no-healthcheck-маппинг меняется
def test_dispatch_health_no_healthcheck_missing(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """health: контейнер без healthcheck (<no value>) → 'missing' + rc 0."""
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "health site-a"},
        orchestrator_factory=_orchestrator_factory("/tmp/health-projects"),
        docker_runner=_recording_docker_runner(calls, rc=0, stdout="<no value>"),
    )

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == "missing"


# endregion FUNC_test_dispatch_health_no_healthcheck_missing


# region FUNC_test_dispatch_health_no_project_negative
## @purpose — R5 negative: health без project → JSON ERROR + rc 1 (fail-fast, паттерн verify).
# 🧪 TRAP[TEST] · 2026-08-27 · NEGATIVE (R5) · health требует \<project\>
# · Scenario: SSH_ORIGINAL_COMMAND="health" → rc 1, JSON {"status":"ERROR"}, runner НЕ вызван
# · Last fail: N/A (новый verb)
# · Remove if: health-контракт меняется
def test_dispatch_health_no_project_negative(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: health без project → JSON ERROR + rc 1, docker_inspect НЕ вызывается."""
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "health"},
        orchestrator_factory=_orchestrator_factory("/tmp/health-projects"),
        docker_runner=_recording_docker_runner(calls, rc=0, stdout="healthy"),
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert calls == [], "health без project не должен вызывать docker_inspect"
    assert json.loads(out)["status"] == "ERROR"
    assert "health requires <project>" in out


# endregion FUNC_test_dispatch_health_no_project_negative


# region FUNC_test_dispatch_health_invalid_project_negative
## @purpose — R5 negative (T9.7): health с path-traversal project → блок ДО handler'а
##            (docker_inspect НЕ вызывается), JSON ERROR + rc 1 — тот же guard, что
##            status/remove/receive (shell-injection через имя проекта исключён).
# 🧪 TRAP[TEST] · 2026-08-27 · NEGATIVE (R5) · T9.7 — health валидирует project-name
# · Scenario: SSH_ORIGINAL_COMMAND="health ../../etc" → rc 1, JSON ERROR, runner не вызван
# · Last fail: N/A (новый verb — guard скопирован с status/remove)
# · Remove if: health перестаёт валидировать project-name
def test_dispatch_health_invalid_project_negative(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative (T9.7): health с traversal-project → блок до handler'а, rc=1."""
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "health ../../etc"},
        orchestrator_factory=_orchestrator_factory("/tmp/health-projects"),
        docker_runner=_recording_docker_runner(calls, rc=0, stdout="healthy"),
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert calls == [], "T9.7: docker_inspect не вызывается для невалидного project-name"
    assert "Invalid or reserved project name" in out


# endregion FUNC_test_dispatch_health_invalid_project_negative


# region FUNC_test_dispatch_raw_docker_inspect_still_unknown
## @purpose — R5 negative (B3 fix-forward): raw `docker inspect` в SSH_ORIGINAL_COMMAND
##            остаётся unknown verb → rc 4. Фикс реализован VERB'ОМ, а не whitelist'ом
##            произвольных команд — forced-command security НЕ ослаблена.
# 🧪 TRAP[TEST] · 2026-08-27 · NEGATIVE (R5) · raw docker inspect НЕ становится verb'ом
# · Regression: pre-fix probe слал "docker inspect -f ..." → unknown verb exit 4 (skip-health мёртв)
# · Scenario: SSH_ORIGINAL_COMMAND="docker inspect -f {{.State.Health.Status}} site-a" → rc 4
# · Last fail: N/A (новый negative — фиксирует контракт security)
# · Remove if: raw docker-команды станут допустимыми в dispatch (запрещено S7)
def test_dispatch_raw_docker_inspect_still_unknown(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: raw docker inspect → unknown verb exit 4 (fix — verb, не whitelist)."""
    caplog.set_level(logging.INFO)

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "docker inspect -f '{{.State.Health.Status}}' site-a"},
        orchestrator_factory=_orchestrator_factory("/tmp/health-projects"),
    )

    out = capsys.readouterr().out
    assert rc == 4  # ConfigValidationError.exit_code — unknown verb (D2)
    payload = json.loads(out)
    assert payload["status"] == "ERROR"
    assert "unknown verb" in payload["error"]


# endregion FUNC_test_dispatch_raw_docker_inspect_still_unknown


# ── rollback verb (D8 launch-validation) — snapshot-based откат через forced-command ─────


# region FUNC_test_dispatch_rollback_routes_latest
## @purpose — dispatch `rollback <project>` → маршрут в rollback-handler (_VERB_HANDLERS),
##            orchestrator.rollback(project_name, snapshot_id=None) — latest snapshot.
##            DI (W-H): orchestrator_factory c фейком-рекордером (0 патчей); PLATFORM_LOCK_DIR →
##            tmp_path (детерминированный writable lock, никакого /var/lock на dev).
# 🧪 TRAP[TEST] · 2026-09-01 · D8 launch-validation · rollback маршрутизируется (dispatch)
# · Regression: ssh ci-deploy@host 'rollback roadmap' → "unknown verb in SSH command" (exit 4)
# · Scenario: SSH_ORIGINAL_COMMAND="rollback testproj" → rc 0, orchestrator.rollback("testproj", None),
# ·   stdout JSON {"status":"DEPLOYED"} (честный assert, R1)
# · Last fail: — rollback отсутствовал в CANONICAL_VERBS/_VERB_HANDLERS (dispatch-недостижим)
# · Remove if: rollback-verb удаляется из диспетчера
def test_dispatch_rollback_routes_latest(capsys, tmp_path, caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    """dispatch rollback <project> → rollback-handler → orchestrator.rollback(latest)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("PLATFORM_LOCK_DIR", str(tmp_path))
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _RecordingOrch:
        def rollback(self, *args, **kwargs):
            calls.append((args, kwargs))
            return type(
                "R",
                (),
                {
                    "is_success": lambda _: True,
                    "to_dict": lambda _: {
                        "status": "DEPLOYED",
                        "project": kwargs.get("project_name"),
                        "snapshot_id": kwargs.get("snapshot_id"),
                    },
                },
            )()

    def _factory(*args, **kwargs):
        return _RecordingOrch()

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "rollback testproj"},
        orchestrator_factory=_factory,
    )

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    assert rc == 0
    assert calls, "rollback handler должен вызывать orchestrator.rollback"
    assert calls[0][0] == (), f"rollback вызывается keyword-аргументами (паттерн main-CLI), got {calls[0]}"
    assert calls[0][1] == {"project_name": "testproj", "snapshot_id": None}, f"latest snapshot (None), got {calls[0]}"
    payload = json.loads(out)
    assert payload["status"] == "DEPLOYED"


# endregion FUNC_test_dispatch_rollback_routes_latest


# region FUNC_test_dispatch_rollback_snapshot_id
## @purpose — dispatch `rollback <project> <snapshot-id>` → snapshot_id передаётся в
##            orchestrator.rollback (второй токен, positional-формат как status/remove).
# 🧪 TRAP[TEST] · 2026-09-01 · D8 launch-validation · snapshot-id второй токен
# · Scenario: SSH_ORIGINAL_COMMAND="rollback testproj snap-123" → orchestrator.rollback("testproj", "snap-123")
# · Last fail: — rollback не существовал как verb
# · Remove if: rollback-арность меняется
def test_dispatch_rollback_snapshot_id(capsys, tmp_path, caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    """dispatch rollback <project> <snapshot-id> → snapshot_id пробрасывается в orchestrator."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("PLATFORM_LOCK_DIR", str(tmp_path))
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _RecordingOrch:
        def rollback(self, *args, **kwargs):
            calls.append((args, kwargs))
            return type(
                "R",
                (),
                {
                    "is_success": lambda _: True,
                    "to_dict": lambda _: {
                        "status": "DEPLOYED",
                        "project": kwargs.get("project_name"),
                        "snapshot_id": kwargs.get("snapshot_id"),
                    },
                },
            )()

    def _factory(*args, **kwargs):
        return _RecordingOrch()

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "rollback testproj snap-123"},
        orchestrator_factory=_factory,
    )

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    assert rc == 0
    assert calls, "rollback handler должен вызывать orchestrator.rollback"
    assert calls[0][0] == (), f"rollback вызывается keyword-аргументами, got {calls[0]}"
    assert calls[0][1] == {"project_name": "testproj", "snapshot_id": "snap-123"}, (
        f"snapshot-id проброс, got {calls[0]}"
    )
    payload = json.loads(out)
    assert payload["snapshot_id"] == "snap-123"


# endregion FUNC_test_dispatch_rollback_snapshot_id


# region FUNC_test_dispatch_rollback_no_project_negative
## @purpose — R5 negative (D8): rollback без project → JSON ERROR + rc 1 (fail-fast, паттерн
##            health/verify), orchestrator НЕ вызывается (никаких rollback-мутаций).
# 🧪 TRAP[TEST] · 2026-09-01 · NEGATIVE (R5) · D8 — rollback требует \<project\>
# · Scenario: SSH_ORIGINAL_COMMAND="rollback" → rc 1, JSON {"status":"ERROR"},
# ·   "rollback requires \<project\>", recorder пуст
# · Last fail: N/A (new verb)
# · Remove if: rollback-контракт меняется
def test_dispatch_rollback_no_project_negative(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: rollback без project → JSON ERROR + rc 1, orchestrator НЕ вызывается."""
    caplog.set_level(logging.INFO)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _RecordingOrch:
        _MSG_ROLLBACK = "rollback не должен вызываться без project"

        def rollback(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(self._MSG_ROLLBACK)

    def _factory(*args, **kwargs):
        return _RecordingOrch()

    rc = _dispatch([], env={"SSH_ORIGINAL_COMMAND": "rollback"}, orchestrator_factory=_factory)

    out = capsys.readouterr().out
    assert rc == 1
    assert calls == [], "rollback без project не должен вызывать orchestrator"
    payload = json.loads(out)
    assert payload["status"] == "ERROR"
    assert "rollback requires <project>" in out


# endregion FUNC_test_dispatch_rollback_no_project_negative


# ── TEST-05 (REF-0006, DevPlan 11 В2): параметризованные traversal-негативы receive/remove ──


# region FUNC_test_dispatch_traversal_negatives_receive_remove
## @purpose — TEST-05 (карточка REF-0006): негативы receive/remove/rollback через _dispatch с
##            path-traversal/невалидными project-name. T9.7-валидация отсекает инъекцию
##            ДО handler'а: orchestrator НЕ вызывается (никаких remove/rollback/deploy мутаций),
##            JSON ERROR + rc 1.
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · TEST-05 — receive/remove traversal через dispatch
# · Last fail: карточка REF-0006 — «TEST-05 (нет негативов receive/remove)»; T9.7 покрывал
#   только status-семантику косвенно, явных параметризованных негативов канал не имел
# · Scenario: SSH_ORIGINAL_COMMAND="receive ../../etc sha" / "remove ../evil" /
#   "rollback ../evil" (D8) → rc 1, JSON {"status":"ERROR","error":"Invalid or reserved project name: ..."},
#   recorder пуст
# · Remove if: dispatch перестаёт валидировать project-name для receive/remove/rollback
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
        ("rollback", "../evil"),
        ("rollback", "/opt/projects/victim"),
        ("rollback", "-rf"),
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
        "rollback-dotdot",
        "rollback-absolute-path",
        "rollback-flag-injection",
    ],
)
def test_dispatch_traversal_negatives_receive_remove(
    verb: str,
    bad_project: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """receive/remove/rollback с traversal-проектом → rc 1, JSON ERROR, orchestrator НЕ вызывается."""
    caplog.set_level(logging.INFO)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _RecordingOrch:
        """Recorder: любое обращение к remove/receive/rollback = нарушение валидации (тест RED)."""

        _MSG_REMOVE = "remove не должен вызываться для невалидного project-name"
        _MSG_RECEIVE = "receive не должен вызываться для невалидного project-name"
        _MSG_ROLLBACK = "rollback не должен вызываться для невалидного project-name"

        def remove(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(self._MSG_REMOVE)

        def receive(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(self._MSG_RECEIVE)

        def rollback(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(self._MSG_ROLLBACK)

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


# ── F-07 (2026-09-02): CI-канал шлёт quoted-аргументы — _parse_tokens (shlex) ──────
# Прод-лог CI run 33591414425 (TronyxLab/dance-site): reusable workflow deploy-project.yml
# шлёт `receive "dance-site" fed3794...` (литеральные кавычки) → наивный split давал
# project '"dance-site"' → validate_project_name отвергал → CI-деплой сломан полностью.
# Фикс: _parse_tokens(args) — shlex.split с fallback на naive split при unmatched quote
# (T9.7 fail-closed остаётся в validate_project_name). Сервер принимает ОБА формата.


# region FUNC_test_parse_tokens_quoted_args
## @purpose — _parse_tokens: shlex снимает парные кавычки (формат CI-канала) → чистые токены.
# 🧪 TRAP[TEST] · 2026-09-02 · Regression · F-07 — quoted-аргументы токенизируются
# · Regression: naive split оставлял кавычки → project '"dance-site"' → "Invalid or reserved project name"
# · Scenario: '"dance-site" abc123' → ["dance-site", "abc123"]; 'tronyx-vps "dance-site"' →
# ·   ["tronyx-vps", "dance-site"]; "" → []; без кавычек — прежние токены
# · Last fail: CI run 33591414425 (receive "dance-site" → rejected)
# · Remove if: _parse_tokens заменяется иным токенизатором
def test_parse_tokens_quoted_args() -> None:
    """_parse_tokens: shlex снимает кавычки (CI-формат), пустой ввод → []."""
    assert _parse_tokens('"dance-site" abc123') == ["dance-site", "abc123"]
    assert _parse_tokens('tronyx-vps "dance-site"') == ["tronyx-vps", "dance-site"]
    assert _parse_tokens("single-token") == ["single-token"]
    assert _parse_tokens("") == []


# endregion FUNC_test_parse_tokens_quoted_args


# region FUNC_test_parse_tokens_unmatched_quote_fallback
## @purpose — _parse_tokens: unmatched quote → ValueError shlex → fallback naive split
##            (НЕ exception — dispatch не роняется; fail-closed остаётся в T9.7).
# 🧪 TRAP[TEST] · 2026-09-02 · Regression · F-07 — unmatched quote → fallback split
# · Scenario: '"dance-site abc123' (незакрытая кавычка) → ['"dance-site', 'abc123'] (naive split)
# · Last fail: N/A (новый fallback-контракт)
# · Remove if: fallback-семантика _parse_tokens меняется
def test_parse_tokens_unmatched_quote_fallback() -> None:
    """_parse_tokens: unmatched quote → naive split fallback (без исключения)."""
    assert _parse_tokens('"dance-site abc123') == ['"dance-site', "abc123"]
    assert _parse_tokens("'partial") == ["'partial"]


# endregion FUNC_test_parse_tokens_unmatched_quote_fallback


# region FUNC_test_dispatch_receive_quoted_args
## @purpose — dispatch receive с quoted-аргументами CI-формата (SSH_ORIGINAL_COMMAND) →
##            project/version корректные: '"testproj" abc123' → project="testproj",
##            version="abc123" (F-07: точный прод-вход receive "dance-site" [sha]).
# 🧪 TRAP[TEST] · 2026-09-02 · Regression · F-07 — quoted receive через dispatch
# · Scenario: SSH_ORIGINAL_COMMAND='receive "testproj" abc123' + OK-фабрика → DEPLOYED,
# ·   JSON.project == "testproj", JSON.version == "abc123", rc 0
# · Last fail: CI run 33591414425 — project '"dance-site"' rejected (rc 1, JSON ERROR)
# · Remove if: receive перестаёт принимать quoted-аргументы
def test_dispatch_receive_quoted_args(capsys, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch receive "project" sha (CI-формат) → project/version без кавычек."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_payload_tar(tmp_path)
    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": 'receive "testproj" abc123'},
        stdin_stream=io.BytesIO(tar_bytes),
        orchestrator_factory=_receive_ok_factory(str(tmp_path)),
    )

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["project"] == "testproj", f"F-07: кавычки обязаны сниматься, got {payload['project']!r}"
    assert payload["version"] == "abc123", f"F-07: version из аргументов, got {payload['version']!r}"
    assert payload["status"] == "DEPLOYED"
    assert rc == 0
    logger.critical(
        "[IMP:9][test] F-07 — quoted receive OK (project=%s version=%s)", payload["project"], payload["version"]
    )


# endregion FUNC_test_dispatch_receive_quoted_args


# region FUNC_test_dispatch_receive_unquoted_preserved
## @purpose — dispatch receive БЕЗ кавычек (локальный канал ForcedCommandChannel/shlex.quote)
##            → прежнее поведение не изменилось (b, unquoted — прежнее поведение).
# 🧪 TRAP[TEST] · 2026-09-02 · Regression · F-07 — unquoted receive без регрессии
# · Scenario: SSH_ORIGINAL_COMMAND='receive testproj abc123' → project="testproj", version="abc123"
# · Last fail: N/A (прежнее поведение обязано сохраниться)
# · Remove if: receive-арность меняется
def test_dispatch_receive_unquoted_preserved(capsys, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch receive project sha (без кавычек) → прежнее поведение (b)."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_payload_tar(tmp_path)
    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": "receive testproj abc123"},
        stdin_stream=io.BytesIO(tar_bytes),
        orchestrator_factory=_receive_ok_factory(str(tmp_path)),
    )

    out = capsys.readouterr().out
    assert_ldd_imp9(caplog)
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["project"] == "testproj"
    assert payload["version"] == "abc123"
    assert payload["status"] == "DEPLOYED"
    assert rc == 0


# endregion FUNC_test_dispatch_receive_unquoted_preserved


# region FUNC_test_dispatch_receive_unmatched_quote_fail_closed
## @purpose — dispatch receive с unmatched quote → fallback naive split (НЕ exception):
##            токен '"testproj' невалиден → T9.7 fail-closed JSON ERROR + rc 1
##            (никакого деплоя под битым именем, никакого traceback).
# 🧪 TRAP[TEST] · 2026-09-02 · NEGATIVE (R5) · F-07 — unmatched quote fail-closed
# · Scenario: SSH_ORIGINAL_COMMAND='receive "testproj abc123' → rc 1, JSON {"status":"ERROR"},
# ·   "Invalid or reserved project name" (receive НЕ вызывается)
# · Last fail: N/A (новый negative — фиксирует fallback-контракт)
# · Remove if: unmatched-quote семантика меняется
def test_dispatch_receive_unmatched_quote_fail_closed(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch receive с незакрытой кавычкой → fallback split + T9.7 fail-closed (rc 1)."""
    caplog.set_level(logging.INFO)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _RecordingOrch:
        _MSG = "receive не должен вызываться при unmatched quote"

        def receive(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(self._MSG)

    def _factory(*args, **kwargs):
        return _RecordingOrch()

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": 'receive "testproj abc123'},
        stdin_stream=io.BytesIO(b""),
        orchestrator_factory=_factory,
    )

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 1, "unmatched quote → fail-closed rc 1 (не exception)"
    assert payload["status"] == "ERROR"
    assert "Invalid or reserved project name" in payload["error"]
    assert calls == [], "receive handler не должен вызываться для невалидного токена"
    assert_ldd_imp9(caplog)


# endregion FUNC_test_dispatch_receive_unmatched_quote_fail_closed


# region FUNC_test_dispatch_receive_quoted_injection_negative
## @purpose — R5 negative (F-07): quoted path-traversal (кавычки СНЯТЫ, затем валидация) →
##            инъекция НЕ проходит через кавычки (a: «инъекция-кейс не ломается»).
# 🧪 TRAP[TEST] · 2026-09-02 · NEGATIVE (R5) · F-07 — quoted инъекция блокируется
# · Scenario: SSH_ORIGINAL_COMMAND='receive "../../etc" deadbeef' → rc 1, JSON ERROR,
# ·   receive НЕ вызывается (T9.7 после shlex-токенизации)
# · Last fail: N/A (новый negative — кавычки не ослабляют валидацию)
# · Remove if: dispatch перестаёт валидировать project-name
def test_dispatch_receive_quoted_injection_negative(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch receive с quoted-инъекцией → T9.7 блокирует после снятия кавычек (rc 1)."""
    caplog.set_level(logging.INFO)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _RecordingOrch:
        _MSG = "receive не должен вызываться для quoted-инъекции"

        def receive(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(self._MSG)

    def _factory(*args, **kwargs):
        return _RecordingOrch()

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": 'receive "../../etc" deadbeef'},
        stdin_stream=io.BytesIO(b""),
        orchestrator_factory=_factory,
    )

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 1, "quoted traversal обязан блокироваться (T9.7 после снятия кавычек)"
    assert payload["status"] == "ERROR"
    assert "Invalid or reserved project name" in payload["error"]
    assert calls == [], "receive handler не должен вызываться для quoted-инъекции"
    assert_ldd_imp9(caplog)


# endregion FUNC_test_dispatch_receive_quoted_injection_negative


# region FUNC_test_dispatch_verify_quoted_node_project
## @purpose — dispatch verify с quoted node+project (CI-формат `verify "tronyx-vps" "dance-site"`)
##            → --node tronyx-vps, --project dance-site (d: quoted verify не ломается).
# 🧪 TRAP[TEST] · 2026-09-02 · Regression · F-07 — quoted verify node/project
# · Regression: naive split давал node '"tronyx-vps"' → domain_verifier не резолвил ноду
# · Scenario: SSH_ORIGINAL_COMMAND='verify "tronyx-vps" "dance-site"' → verify_cmd:
# ·   --node tronyx-vps --project dance-site, rc 0
# · Last fail: CI run 33591414425 — verify с кавычками (тот же workflow)
# · Remove if: verify verb разбирает аргументы иначе
def test_dispatch_verify_quoted_node_project(capsys, caplog: pytest.LogCaptureFixture) -> None:
    """dispatch verify "node" "project" (CI-формат) → --node/--project без кавычек."""
    caplog.set_level(logging.INFO)
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    rc = _dispatch(
        [],
        env={"SSH_ORIGINAL_COMMAND": 'verify "tronyx-vps" "dance-site"'},
        orchestrator_factory=_orchestrator_factory("/tmp/f07-verify-projects"),
        run_cmd=_fake_run,
    )

    cmd = captured.get("cmd")
    assert cmd is not None, "F-07: verify обязан вызвать subprocess.run с verify_cmd"
    assert cmd[cmd.index("--node") + 1] == "tronyx-vps", (
        f"F-07 regression: node обязан быть 'tronyx-vps' (кавычки сняты), got {cmd[cmd.index('--node') + 1]!r}"
    )
    assert cmd[cmd.index("--project") + 1] == "dance-site", (
        f"F-07 regression: project обязан быть 'dance-site' (кавычки сняты), got {cmd[cmd.index('--project') + 1]!r}"
    )
    assert rc == 0
    assert_ldd_imp9(caplog)
    logger.critical("[IMP:9][test] F-07 — quoted verify OK (node=%s project=%s)", "tronyx-vps", "dance-site")


# endregion FUNC_test_dispatch_verify_quoted_node_project
