# GREP_SUMMARY: test-orchestrator-receive-version, receive, version, sha-pinning, D5, U-37, post-deploy-chain, notify-hook, generate-catalog, DI, stream-param, subclass
# STRUCTURE: ▶ 5 scenarios ┌version из аргументов + yaml без version + chain после успеха + chain не при FAILED + chain WARN не фейлит┐ → ○ caplog LDD IMP:9 → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 116 B1 T2 (D5, U-37) — DeployOrchestrator.receive():
##           версия ТОЛЬКО из аргументов (receive <project> <sha>), phantom-read version/service
##           из ai-platform.yaml УДАЛЁН; post-deploy цепочка (notify-hook + generate-catalog, D4):
##           вызывается после успеха, НЕ при FAILED, сбой цепочки → WARN не фейлит деплой.
## @scope    Tests DeployOrchestrator.receive() напрямую (native import, DI — W-H DevPlan 163:
##           stream= io.BytesIO вместо патча sys.stdin, DeployOrchestrator-субкласс вместо
##           патчей _deploy_compose/poll_until_healthy/_run_post_deploy_chain, projects_base=
##           вместо setenv PROJECTS_BASE). Остаток: subprocess.run в _run_post_deploy_chain
##           (deploy-интеграция notify-hook/generate-catalog) — честный остаток (H3).
## @invariants
##   - No Docker, no SSH, no subprocess for business logic
##   - LDD: IMP:9 лог на receive start + deploy done
##   - R5 anti-survivorship: yaml БЕЗ version-поля → версия НЕ "latest" при переданном sha
## @rationale  DevPlan 116 B1 T2/D5: version в DeployResult/snapshot = sha-pinning из CI-аргументов.
##             U-37: чтение config.get("version")/config.get("service") удаляется.
## @changes    2026-08-01 | Created (DevPlan 116 B1 T2)
## @changes    2026-08-13 | DevPlan 163 W-H — DI-перевод: stream/projects_base/субкласс
# endregion MODULE_CONTRACT

import io
import json
import logging
import subprocess
import tarfile

import pytest

from core.internal.deploy.healthcheck_poller import HealthcheckResult
from core.internal.deploy.orchestrator import DeployOrchestrator

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _assert_imp9_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Print IMP:7-10 trajectory and assert at least one IMP:9 log present."""
    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# ── L1-валидный payload-compose (176 A.2: receive исполняет pre-deploy L1-гейт) ──
_VALID_COMPOSE: str = """\
services:
  web:
    image: nginx:alpine
    env_file:
      - .env.platform
    healthcheck:
      test: ["CMD", "echo", "ok"]
    deploy:
      resources:
        limits:
          memory: "128M"
          cpus: "0.25"
    labels:
      - "platform.type=backend"
    networks:
      - proxy-net
networks:
  proxy-net:
    external: true
"""


def _make_tar(tmp_path, project: str = "testproj", include_version_field: bool = False) -> bytes:
    """Create tar.gz payload with ai-platform.yaml БЕЗ version-поля (D5: версия из аргументов)."""
    proj_dir = tmp_path / "payload"
    proj_dir.mkdir(exist_ok=True)
    (proj_dir / "docker-compose.yml").write_text(_VALID_COMPOSE)
    yaml_content = f"name: {project}\n"
    if include_version_field:
        yaml_content += "version: from-yaml\n"
    (proj_dir / "ai-platform.yaml").write_text(yaml_content)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fname in ("docker-compose.yml", "ai-platform.yaml"):
            tar.add(proj_dir / fname, arcname=fname)
    return buf.getvalue()


def _stdin_stream(tar_bytes: bytes) -> io.BytesIO:
    """DI-канал stdin: io.BytesIO вместо патча sys.stdin (W-H DevPlan 163)."""
    return io.BytesIO(tar_bytes)


class _FakePoller:
    """HealthcheckPoller-fake (W-H DI): poll_until_healthy → healthy (0 патчей)."""

    def poll_until_healthy(self, _project_name, _project_dir):
        return HealthcheckResult(status="healthy", project="testproj", method="http", attempts=1)


class _ReceiveSuccessOrch(DeployOrchestrator):
    """DeployOrchestrator-субкласс с DI-переопределениями (W-H: 0 патчей _deploy_compose и др.).

    ## @purpose — receive-тесты: compose-деплой + post-deploy chain переопределены методами
    ##            субкласса (AF-паттерн fake-объект вместо патчей);
    ##            healthcheck — через healthcheck_poller= конструкторный DI (W4b).
    ## @io — ⇥ projects_base: str, chain_calls: list | None (запись вызовов D4) → ⎋ субкласс
    """

    def __init__(self, projects_base: str, chain_calls: list | None = None) -> None:
        super().__init__(projects_base=projects_base, healthcheck_poller=_FakePoller())
        self._chain_calls = chain_calls if chain_calls is not None else []

    def _deploy_compose(self, _project_dir, _service, _version) -> bool:
        return True

    def _run_post_deploy_chain(self, project, version, status, project_dir=None, node_name="", *, run_cmd=None):
        # Волна 118 B8: сигнатура _run_post_deploy_chain расширена (project_dir, node_name) —
        # module deploy-hooks (nginx wire). Запись только первых 3 (D4 контракт).
        # W-H: run_cmd= DI-канал subprocess (None = канонический subprocess.run).
        if run_cmd is not None:
            return super()._run_post_deploy_chain(
                project, version, status, project_dir=project_dir, node_name=node_name, run_cmd=run_cmd
            )
        if self._chain_calls is not None:
            self._chain_calls.append((project, version, status))
        return None


class _ReceiveFailComposeOrch(_ReceiveSuccessOrch):
    """Субкласс: compose-деплой падает (False) — chain НЕ вызывается при FAILED."""

    def _deploy_compose(self, _project_dir, _service, _version) -> bool:
        return False


# region FUNC_test_receive_version_from_args
## @purpose — receive(project, version="abc123") → DeployResult.version == "abc123" (D5 sha-pinning).
##            yaml БЕЗ version-поля → версия НЕ "latest" (U-37 negative).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · D5 версия из аргументов
# · Regression: version читалась из ai-platform.yaml (phantom-поля) — "latest" вместо sha
# · Scenario: tar без version в yaml + receive(project="testproj", version="abc123") → JSON version="abc123"
# · Last fail: — version = config.get("version", "latest")
# · Remove if: version-контракт receive меняется
def test_receive_version_from_args(tmp_path, caplog: pytest.LogCaptureFixture, capsys) -> None:
    """receive(project, version='abc123') → DeployResult JSON version='abc123' (D5, U-37)."""
    caplog.set_level(logging.INFO)
    chain_calls: list[tuple[str, str, str]] = []

    tar_bytes = _make_tar(tmp_path, include_version_field=False)
    orch = _ReceiveSuccessOrch(projects_base=str(tmp_path), chain_calls=chain_calls)
    rc = orch.receive(
        project_name="testproj",
        version="abc123",
        stream=_stdin_stream(tar_bytes),
        orchestrator_factory=lambda base: _ReceiveSuccessOrch(base, chain_calls=chain_calls),
    )

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    payload = json.loads(out.strip().splitlines()[-1])
    assert rc == 0
    assert payload["project"] == "testproj"
    assert payload["version"] == "abc123", f"U-37: версия должна прийти из аргументов, got {payload['version']!r}"
    assert payload["version"] != "latest"
    assert payload["status"] == "DEPLOYED"
    # D4: post-deploy цепочка вызвана после успеха
    assert chain_calls == [("testproj", "abc123", "DEPLOYED")]


# endregion FUNC_test_receive_version_from_args


# region FUNC_test_receive_yaml_version_field_ignored
## @purpose — yaml С version-полем → поле ИГНОРИРУЕТСЯ (U-37: чтение version из yaml удалено).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · U-37 negative: yaml version игнорируется
# · Regression: version-поле из ai-platform.yaml побеждает аргументы
# · Scenario: yaml version=from-yaml + receive(version="abc123") → JSON version="abc123" (НЕ from-yaml)
# · Last fail: — config.get("version") имел приоритет
# · Remove if: phantom-read version/service возвращается (запрещено U-37)
def test_receive_yaml_version_field_ignored(tmp_path, caplog: pytest.LogCaptureFixture, capsys) -> None:
    """version-поле из yaml игнорируется — версия ТОЛЬКО из аргументов (U-37)."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_tar(tmp_path, include_version_field=True)
    orch = _ReceiveSuccessOrch(projects_base=str(tmp_path))
    rc = orch.receive(
        project_name="testproj",
        version="abc123",
        stream=_stdin_stream(tar_bytes),
        orchestrator_factory=_ReceiveSuccessOrch,
    )

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    payload = json.loads(out.strip().splitlines()[-1])
    assert rc == 0
    assert payload["version"] == "abc123", f"yaml version-поле должно игнорироваться, got {payload['version']!r}"


# endregion FUNC_test_receive_yaml_version_field_ignored


# region FUNC_test_receive_chain_skipped_on_failed
## @purpose — post-deploy цепочка НЕ вызывается при FAILED деплое (D4: только после успеха).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · D4 chain не при FAILED
# · Regression: цепочка выполняется даже при провале деплоя
# · Scenario: _deploy_compose → False → receive FAILED; chain_calls пуст
# · Last fail: N/A (new test)
# · Remove if: post-deploy chain семантика меняется
def test_receive_chain_skipped_on_failed(tmp_path, caplog: pytest.LogCaptureFixture, capsys) -> None:
    """При FAILED деплое post-deploy цепочка НЕ вызывается."""
    caplog.set_level(logging.INFO)
    chain_calls: list[tuple[str, str, str]] = []

    tar_bytes = _make_tar(tmp_path)
    orch = _ReceiveFailComposeOrch(projects_base=str(tmp_path), chain_calls=chain_calls)
    rc = orch.receive(
        project_name="testproj",
        version="abc123",
        stream=_stdin_stream(tar_bytes),
        orchestrator_factory=lambda base: _ReceiveFailComposeOrch(base, chain_calls=chain_calls),
    )

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    payload = json.loads(out.strip().splitlines()[-1])
    assert rc == 1
    assert payload["status"] == "FAILED"
    assert chain_calls == [], "D4: цепочка не должна вызываться при FAILED"


# endregion FUNC_test_receive_chain_skipped_on_failed


# region FUNC_test_receive_chain_failure_warns_not_fails
## @purpose — сбой post-deploy цепочки (notify-hook/generate-catalog OSError) → WARN,
##            деплой НЕ фейлится (D4: best-effort).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · D4 negative: сбой цепочки не фейлит деплой
# · Regression: сбой notify-hook роняет деплой (exit != 0)
# · Scenario: subprocess.run внутри _run_post_deploy_chain → OSError → receive всё равно rc 0
# · Last fail: N/A (new test)
# · Remove if: best-effort семантика цепочки меняется
def test_receive_chain_failure_warns_not_fails(tmp_path, caplog: pytest.LogCaptureFixture, capsys) -> None:
    """Сбой цепочки → WARN, деплой остаётся успешным (D4 best-effort)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · 2026-08-06 · B19 (141 r2): chown в DeployHistory.create_snapshot (verify-путь)
    # · попадает под глобальный mock subprocess.run → OSError ронял деплой. Здесь run_cmd-канал
    # · цепочки бросает OSError (D4-сценарий: notify-hook/generate-catalog недоступны).
    def _boom(*a, **k):
        cmd = a[0] if a else k.get("args", [])
        if isinstance(cmd, list) and cmd and cmd[0] == "chown":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        msg = "notify-hook not found (test)"
        raise OSError(msg)

    class _ChainFailOrch(_ReceiveSuccessOrch):
        def _run_post_deploy_chain(self, project, version, status, project_dir=None, node_name=""):
            # DI (W-H): run_cmd= канал subprocess — OSError ловится внутри chain (D4 best-effort)
            super()._run_post_deploy_chain(
                project, version, status, project_dir=project_dir, node_name=node_name, run_cmd=_boom
            )

    tar_bytes = _make_tar(tmp_path)
    orch = _ChainFailOrch(projects_base=str(tmp_path))
    rc = orch.receive(
        project_name="testproj",
        version="abc123",
        stream=_stdin_stream(tar_bytes),
        orchestrator_factory=_ChainFailOrch,
    )

    out = capsys.readouterr().out
    _assert_imp9_logged(caplog)
    payload = json.loads(out.strip().splitlines()[-1])
    assert rc == 0, "D4: сбой цепочки НЕ должен фейлить деплой"
    assert payload["status"] == "DEPLOYED"
    # WARN залогирован
    warn_msgs = [r.message for r in caplog.records if "WARN" in r.message or "non-fatal" in r.message.lower()]
    assert warn_msgs, "Ожидался WARN-лог о сбое best-effort цепочки"


# endregion FUNC_test_receive_chain_failure_warns_not_fails


# region FUNC_test_receive_empty_stdin
## @purpose — пустой stdin → JSON-ошибка + exit 1 (fail-fast, БЕЗ || true-масок).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · пустой stdin → fail
# · Regression: пустой stdin молча проходит (|| true-маски в CI)
# · Scenario: receive() с пустым stdin → rc 1, JSON {"status":"FAILED"}
# · Last fail: — CI гонял receive с пустым stdin под || true
# · Remove if: fail-fast семантика receive меняется
def test_receive_empty_stdin(capsys) -> None:
    """Пустой stdin → JSON-ошибка + exit 1 (fail-fast)."""
    orch = DeployOrchestrator(projects_base="/tmp/unused")
    rc = orch.receive(project_name="testproj", version="abc123", stream=_stdin_stream(b""))

    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out.strip())
    assert payload["status"] == "FAILED"
    assert "No data received on stdin" in payload["error"]


# endregion FUNC_test_receive_empty_stdin
