# GREP_SUMMARY: test-channels-injection, T9.7, shlex-quote, project-name, injection, validate-project-name, dispatch, forced-command, scp, prepare-deploy
# STRUCTURE: ▶ test_*_forced_cmd_quote → remote_cmd = "receive 'p;rm' sha" (shlex.quote) │ ▶ test_*_scp_quote → mkdir/unpack quote │ ▶ test_*_prepare_rejects → project_name 'a;b' → FAILED │ ▶ test_*_dispatch_rejects → _dispatch 'status a;b' → JSON ERROR exit 1
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.7 (L-8/L-10) DevPlan 136 W9: shlex.quote(project_name) в
##           SSH-командах каналов (инъекция `;`/`../` не выполняется на хосте) +
##           validate_project_name в _prepare_deploy (ДО deliver) и _dispatch (ДО маршрутизации).
## @scope    unit-тесты: monkeypatch subprocess.run (каналы), direct _prepare_deploy/_dispatch.
## @invariants
##   - Native imports; tmp_path; LDD IMP:9 в успешных сценариях
##   - R5-negative: точный вход `;`-инъекции — remote_cmd содержит ОДИН кавычкованный аргумент
##   - Dispatch: невалидный проект → JSON {"status":"ERROR"} + exit 1 (до маршрутизации)
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: test_channels_injection.py — project_name
##            `;`/`../` инъекция (D17-фикс 135 покрыл split; инъекция — отдельный аспект, L-8).
## @changes  2026-08-05 · Created (DevPlan 136 W9)
# endregion MODULE_CONTRACT

import json
import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.deploy.channels import ForcedCommandChannel, LocalChannel, Payload, SCPChannel
from core.internal.deploy.orchestrator import DeployOrchestrator, DeployStatus
from core.internal.deploy.orchestrator_cli import _dispatch
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _mk_payload(tmp_path: Path, project: str) -> Payload:
    tar = tmp_path / "payload.tar"
    tar.write_bytes(b"x")
    return Payload(tar_path=tar, project_name=project, version="sha-123")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.7/L-8 — remote_cmd с host: один кавычкованный аргумент
# · Scenario: deliver с host + project_name с `;` → ssh_cmd[-1] == "receive 'p;rm -rf /' sha-123"
# · Last fail: 2026-08-05 — remote_cmd без кавычек: `;` выполнил бы команду на VPS (L-8)
# · Remove if: channel quoting semantics change
@ldd_trajectory
def test_forced_command_remote_cmd_quoted(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.7: ssh_cmd[-1] содержит shlex.quote(project_name) — инъекция не проходит как команда."""
    caplog.set_level(logging.INFO)
    captured: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="DEPLOYED", stderr="")

    channel = ForcedCommandChannel(runner=_fake_run)
    payload = _mk_payload(tmp_path, "p;rm -rf /")
    payload.metadata["host"] = "test-vps"
    result = channel.deliver(payload)

    assert result.success
    assert captured, "ssh subprocess должен вызываться"
    remote_cmd = captured[0][-1]
    assert remote_cmd == "receive 'p;rm -rf /' sha-123", f"project_name обязан быть shlex.quote'd: {remote_cmd!r}"
    logger.critical("[IMP:9][test] forced-command remote_cmd quoted — OK (T9.7)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.7/L-8 — SCPChannel mkdir/unpack экранируются
# · Scenario: project_name с `;` → mkdir-команда "mkdir -p <dir>/'p;rm -rf /'" (shlex.quote)
# · Last fail: 2026-08-05 — "mkdir -p {remote_dir}/{project_name}" (L-8)
# · Remove if: channel quoting semantics change
@ldd_trajectory
def test_scp_channel_quotes_project_name(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.7: SCPChannel экранирует project_name в mkdir/unpack SSH-командах."""
    caplog.set_level(logging.INFO)
    captured: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        if cmd[0] == "ssh":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="sent", stderr="")

    channel = SCPChannel(runner=_fake_run)
    payload = _mk_payload(tmp_path, "p;rm -rf /")
    payload.metadata["host"] = "test-vps"
    payload.metadata["remote_dir"] = "/opt/projects"
    result = channel.deliver(payload)

    assert result.success
    ssh_calls = [c for c in captured if c[0] == "ssh"]
    assert ssh_calls, "ssh-вызовы обязаны быть"
    mkdir_cmd = ssh_calls[0][-1]
    # remote_dir (без спецсимволов) shlex.quote оставляет как есть; project_name кавычкует
    assert mkdir_cmd == "mkdir -p /opt/projects/'p;rm -rf /'", f"mkdir обязан экранировать имя: {mkdir_cmd!r}"
    logger.critical("[IMP:9][test] SCPChannel mkdir quoted — OK (T9.7)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.7/L-10 — _prepare_deploy отклоняет инъекцию
# · Scenario: project_name = "a;b" / "../evil" → _prepare_deploy → FAILED "Invalid or reserved project name"
# · Last fail: 2026-08-05 — deploy() принимал любой project_name (путь-резолв `../` escape) (L-10)
# · Remove if: prepare validation semantics change
@ldd_trajectory
def test_prepare_deploy_rejects_injection_project_name(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.7: validate_project_name в _prepare_deploy (ДО deliver) отклоняет `;`/`../`."""
    caplog.set_level(logging.INFO)
    orch = DeployOrchestrator(projects_base=str(tmp_path / "projects"))
    for bad in ("a;b", "../evil", "status", "receive", "-leading", "with space"):
        payload, failure = orch._prepare_deploy(
            project_name=bad,
            channel=LocalChannel(),
            version="sha1",
            service=bad,
            project_dir=str(tmp_path / "projects" / bad),
            metadata={},
            dry_run=False,
            start=0.0,
        )
        assert failure is not None and failure.status == DeployStatus.FAILED, f"имя {bad!r} обязано быть отклонено"
        assert "Invalid or reserved project name" in (failure.error_info or "")
        assert payload is None, "инъекция не должна доходить до сборки payload"
    logger.critical("[IMP:9][test] _prepare_deploy rejects injection names — OK (T9.7)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.7/L-10 — dispatch отклоняет невалидный проект
# · Scenario: SSH_ORIGINAL_COMMAND="status a;b" → _dispatch → JSON ERROR + exit 1 (до маршрутизации)
# · Last fail: 2026-08-05 — dispatch маршрутизировал project без валидации (L-10)
# · Remove if: dispatch validation semantics change
@ldd_trajectory
def test_dispatch_rejects_invalid_project(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """T9.7: _dispatch валидирует project_name ДО маршрутизации (status/remove/receive)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "status a;b")
    rc = _dispatch([])
    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["status"] == "ERROR"
    assert "Invalid or reserved project name" in payload["error"]
    logger.critical("[IMP:9][test] dispatch rejects invalid project name — OK (T9.7)")
