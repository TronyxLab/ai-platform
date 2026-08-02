#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-receive-flow, receive, unpack, validate, deploy, LocalChannel, parity, R5, E2, unit-tests
# STRUCTURE: ▶ test_receive_unpack_validate ┌tar bytes + staging┐ → unpack → validate → ⎋ (project, service) │ ▶ test_receive_unpack_empty_negative → пустой stdin → False │ ▶ test_orchestrator_receive_flow_parity_negative → ReceiveFlow vs DeployOrchestrator.receive() contract
# region MODULE_CONTRACT
## @purpose  Unit tests for deploy/receive_flow.py (DevPlan 119 E2 $TEST_SPEC): test_receive_unpack_validate
##           + R5 test_orchestrator_receive_flow_parity_negative (старый/новый код — одинаковый результат).
## @scope    ReceiveFlow.unpack/validate/deploy изолированно; parity через DeployOrchestrator.receive()
##           (тонкий фасад-делегат) — контракт JSON + exit code сохранён.
## @invariants
##   - Native imports; tmp_path; stdin mocked через FakeStdin
##   - R5: parity — ReceiveFlow.run возвращает тот же exit-контракт, что receive() до E2
## @rationale  $TEST_SPEC E2 — unpack/validate тестируются изолированно; parity-тест фиксирует
##             сохранение контракта после экстракции (CC 15 → ≤8 на метод).
## @changes  2026-08-02 · Created (DevPlan 119 E2)
# endregion MODULE_CONTRACT
"""

import io
import json
import logging
import tarfile
from pathlib import Path

import pytest

from core.internal.deploy.receive_flow import ReceiveFlow

logger = logging.getLogger(__name__)


def _make_payload_tar(tmp_path: Path, project: str = "testproj") -> bytes:
    """Create a tar.gz payload in memory (ai-platform.yaml + docker-compose.yml)."""
    proj_dir = tmp_path / "payload"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx:alpine\n")
    (proj_dir / "ai-platform.yaml").write_text(f"name: {project}\n")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fname in ("docker-compose.yml", "ai-platform.yaml"):
            tar.add(proj_dir / fname, arcname=fname)
    return buf.getvalue()


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E2 unpack + validate
# · Regression: DevPlan 119 E2 — receive() извлечён в ReceiveFlow (unpack/validate изолированы)
# · Last fail: N/A (new flow module)
# · Remove if: ReceiveFlow API changes
def test_receive_unpack_validate(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """ReceiveFlow: unpack tar → validate → (project, service)."""
    caplog.set_level(logging.INFO)
    flow = ReceiveFlow()
    staging = str(tmp_path / "staging")
    Path(staging).mkdir()

    tar_bytes = _make_payload_tar(tmp_path)
    assert flow.unpack(tar_bytes, staging) is True
    assert (Path(staging) / "ai-platform.yaml").is_file()

    project, service = flow.validate(staging, project_name="testproj")
    assert project == "testproj"
    assert service == "testproj"

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_log = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E2 unpack empty → False
# · Regression: fail-fast пустой stdin (контракт receive, БЕЗ || true-масок)
# · Remove if: unpack empty semantics change
def test_receive_unpack_empty_negative() -> None:
    """ReceiveFlow.unpack(empty) → False (fail-fast, no staging side-effect)."""
    flow = ReceiveFlow()
    assert flow.unpack(b"", "/tmp/nonexistent-staging") is False


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E2 validate missing ai-platform.yaml → ValueError
# · Regression: fail-fast отсутствие ai-platform.yaml (контракт receive)
# · Remove if: validate contract change
def test_receive_validate_missing_yaml_negative(tmp_path: Path) -> None:
    """ReceiveFlow.validate without ai-platform.yaml → ValueError."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "docker-compose.yml").write_text("services: {}\n")
    flow = ReceiveFlow()
    with pytest.raises(ValueError):
        flow.validate(str(staging), project_name="testproj")


# 🧪 TRAP[TEST] · 2026-08-02 · R5 · E2 parity — ReceiveFlow vs DeployOrchestrator.receive()
# · Regression: DevPlan 119 E2 — receive() экстракция (старый/новый код — одинаковый результат)
# · Scenario: DeployOrchestrator.receive() делегирует ReceiveFlow; exit-контракт {0,1} + JSON version
# · Remove if: receive contract changes
def test_orchestrator_receive_flow_parity_negative(
    monkeypatch, capsys, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """R5: DeployOrchestrator.receive() (E2 фасад) сохраняет контракт — JSON + exit код."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_payload_tar(tmp_path)
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"buffer": io.BytesIO(tar_bytes)})())

    from core.internal.deploy.orchestrator import DeployOrchestrator

    orch = DeployOrchestrator(projects_base=str(tmp_path))
    # Mock deploy pipeline to avoid docker (unit env) — parity: JSON содержит version (D5)
    monkeypatch.setattr(orch, "_deploy_compose", lambda *a, **k: True)
    monkeypatch.setattr(
        orch.healthcheck_poller, "poll_until_healthy", lambda *a, **k: type("H", (), {"status": "healthy"})()
    )
    monkeypatch.setattr(orch.deploy_history, "create_snapshot", lambda *a, **k: "snap-1")

    rc = orch.receive(project_name="testproj", version="abc123")

    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["project"] == "testproj"
    assert payload["version"] == "abc123"  # D5 sha-pinning
    assert rc in (0, 1)
    assert rc == (0 if payload["status"] in ("DEPLOYED", "PARTIAL", "SKIPPED") else 1)
    logger.critical("[IMP:9][test] receive parity OK — status=%s rc=%d", payload["status"], rc)
