#!/usr/bin/env python3
# GREP_SUMMARY: test-orchestrator-receive-version, receive, version, sha-pinning, D5, U-37, post-deploy-chain, notify-hook, generate-catalog
# STRUCTURE: ▶ 5 scenarios ┌version из аргументов + yaml без version + chain после успеха + chain не при FAILED + chain WARN не фейлит┐ → ○ caplog LDD IMP:9 → ⊕ TRAP[TEST] → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 116 B1 T2 (D5, U-37) — DeployOrchestrator.receive():
##           версия ТОЛЬКО из аргументов (receive <project> <sha>), phantom-read version/service
##           из ai-platform.yaml УДАЛЁН; post-deploy цепочка (notify-hook + generate-catalog, D4):
##           вызывается после успеха, НЕ при FAILED, сбой цепочки → WARN не фейлит деплой.
## @scope    Tests DeployOrchestrator.receive() напрямую (native import, monkeypatch stdin +
##           deploy-сателлиты _deploy_compose / poll_until_healthy / _run_post_deploy_chain).
## @invariants
##   - No Docker, no SSH, no subprocess for business logic (только патчи)
##   - LDD: IMP:9 лог на receive start + deploy done
##   - R5 anti-survivorship: yaml БЕЗ version-поля → версия НЕ "latest" при переданном sha
## @rationale  DevPlan 116 B1 T2/D5: version в DeployResult/snapshot = sha-pinning из CI-аргументов.
##             U-37: чтение config.get("version")/config.get("service") удаляется.
## @changes    2026-08-01 | Created (DevPlan 116 B1 T2)
# endregion MODULE_CONTRACT

import io
import json
import logging
import tarfile
from types import SimpleNamespace

import pytest

from core.internal.deploy.healthcheck_poller import HealthcheckResult
from core.internal.deploy.orchestrator import DeployOrchestrator

logger = logging.getLogger(__name__)


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


def _make_tar(tmp_path, project: str = "testproj", include_version_field: bool = False) -> bytes:
    """Create tar.gz payload with ai-platform.yaml БЕЗ version-поля (D5: версия из аргументов)."""
    proj_dir = tmp_path / "payload"
    proj_dir.mkdir(exist_ok=True)
    (proj_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx:alpine\n")
    yaml_content = f"name: {project}\n"
    if include_version_field:
        yaml_content += "version: from-yaml\n"
    (proj_dir / "ai-platform.yaml").write_text(yaml_content)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fname in ("docker-compose.yml", "ai-platform.yaml"):
            tar.add(proj_dir / fname, arcname=fname)
    return buf.getvalue()


def _patch_stdin_tar(monkeypatch, tar_bytes: bytes) -> None:
    """Feed tar bytes through sys.stdin.buffer (receive читает sys.stdin.buffer.read())."""
    fake_stdin = SimpleNamespace(buffer=io.BytesIO(tar_bytes))
    monkeypatch.setattr("sys.stdin", fake_stdin)


def _patch_deploy_success(monkeypatch, chain_calls: list | None = None) -> None:
    """Patch deploy-сателлиты для успешного деплоя: compose OK + healthy + цепочка записывается.

    ## @purpose — DeployOrchestrator.deploy() → _deploy_compose True + healthcheck healthy →
    ##            результат DEPLOYED; _run_post_deploy_chain записывает вызовы (D4).
    """
    from core.internal.deploy.healthcheck_poller import HealthcheckPoller

    monkeypatch.setattr(DeployOrchestrator, "_deploy_compose", lambda self, *a, **k: True)

    def _healthy_poll(self, *a, **k):
        return HealthcheckResult(status="healthy", project="testproj", method="http", attempts=1)

    monkeypatch.setattr(HealthcheckPoller, "poll_until_healthy", _healthy_poll)

    def _record_chain(self, project, version, status, project_dir=None, node_name=""):
        # Волна 118 B8: сигнатура _run_post_deploy_chain расширена (project_dir, node_name) —
        # module deploy-hooks (nginx wire). Тест записывает только первые 3 (D4 контракт).
        if chain_calls is not None:
            chain_calls.append((project, version, status))

    monkeypatch.setattr(DeployOrchestrator, "_run_post_deploy_chain", _record_chain)


# region FUNC_test_receive_version_from_args
## @purpose — receive(project, version="abc123") → DeployResult.version == "abc123" (D5 sha-pinning).
##            yaml БЕЗ version-поля → версия НЕ "latest" (U-37 negative).
# 🧪 TRAP[TEST] · DevPlan 116 B1 T2 · D5 версия из аргументов
# · Regression: version читалась из ai-platform.yaml (phantom-поля) — "latest" вместо sha
# · Scenario: tar без version в yaml + receive(project="testproj", version="abc123") → JSON version="abc123"
# · Last fail: legacy — version = config.get("version", "latest")
# · Remove if: version-контракт receive меняется
def test_receive_version_from_args(monkeypatch, tmp_path, caplog: pytest.LogCaptureFixture, capsys) -> None:
    """receive(project, version='abc123') → DeployResult JSON version='abc123' (D5, U-37)."""
    caplog.set_level(logging.INFO)
    chain_calls: list[tuple[str, str, str]] = []

    tar_bytes = _make_tar(tmp_path, include_version_field=False)
    _patch_stdin_tar(monkeypatch, tar_bytes)
    _patch_deploy_success(monkeypatch, chain_calls)
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))

    orch = DeployOrchestrator(projects_base=str(tmp_path))
    rc = orch.receive(project_name="testproj", version="abc123")

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
# · Last fail: legacy — config.get("version") имел приоритет
# · Remove if: phantom-read version/service возвращается (запрещено U-37)
def test_receive_yaml_version_field_ignored(monkeypatch, tmp_path, caplog: pytest.LogCaptureFixture, capsys) -> None:
    """version-поле из yaml игнорируется — версия ТОЛЬКО из аргументов (U-37)."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_tar(tmp_path, include_version_field=True)
    _patch_stdin_tar(monkeypatch, tar_bytes)
    _patch_deploy_success(monkeypatch)
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))

    orch = DeployOrchestrator(projects_base=str(tmp_path))
    rc = orch.receive(project_name="testproj", version="abc123")

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
def test_receive_chain_skipped_on_failed(monkeypatch, tmp_path, caplog: pytest.LogCaptureFixture, capsys) -> None:
    """При FAILED деплое post-deploy цепочка НЕ вызывается."""
    caplog.set_level(logging.INFO)
    chain_calls: list[tuple[str, str, str]] = []

    tar_bytes = _make_tar(tmp_path)
    _patch_stdin_tar(monkeypatch, tar_bytes)
    monkeypatch.setattr(DeployOrchestrator, "_deploy_compose", lambda self, *a, **k: False)
    monkeypatch.setattr(DeployOrchestrator, "_run_post_deploy_chain", lambda *a, **k: chain_calls.append(a))
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))

    orch = DeployOrchestrator(projects_base=str(tmp_path))
    rc = orch.receive(project_name="testproj", version="abc123")

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
def test_receive_chain_failure_warns_not_fails(monkeypatch, tmp_path, caplog: pytest.LogCaptureFixture, capsys) -> None:
    """Сбой цепочки → WARN, деплой остаётся успешным (D4 best-effort)."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_tar(tmp_path)
    _patch_stdin_tar(monkeypatch, tar_bytes)
    monkeypatch.setattr(DeployOrchestrator, "_deploy_compose", lambda self, *a, **k: True)

    from core.internal.deploy.healthcheck_poller import HealthcheckPoller

    def _healthy_poll(self, *a, **k):
        return HealthcheckResult(status="healthy", project="testproj", method="http", attempts=1)

    monkeypatch.setattr(HealthcheckPoller, "poll_until_healthy", _healthy_poll)

    # Сбой subprocess внутри _run_post_deploy_chain (notify-hook/generate-catalog недоступны)
    def _boom(*a, **k):
        raise OSError("notify-hook not found (test)")

    monkeypatch.setattr("core.internal.deploy.orchestrator.subprocess.run", _boom)
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))

    orch = DeployOrchestrator(projects_base=str(tmp_path))
    rc = orch.receive(project_name="testproj", version="abc123")

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
# · Regression: пустой stdin молча проходит (legacy || true-маски в CI)
# · Scenario: receive() с пустым stdin → rc 1, JSON {"status":"FAILED"}
# · Last fail: legacy — CI гонял receive с пустым stdin под || true
# · Remove if: fail-fast семантика receive меняется
def test_receive_empty_stdin(monkeypatch, capsys) -> None:
    """Пустой stdin → JSON-ошибка + exit 1 (fail-fast)."""
    _patch_stdin_tar(monkeypatch, b"")

    orch = DeployOrchestrator(projects_base="/tmp/unused")
    rc = orch.receive(project_name="testproj", version="abc123")

    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out.strip())
    assert payload["status"] == "FAILED"
    assert "No data received on stdin" in payload["error"]


# endregion FUNC_test_receive_empty_stdin
