"""
# GREP_SUMMARY: test-receive-flow, receive, unpack, validate, deploy, pre-deploy-gate, L1, PRACTICES-BLOCK, C1, LocalChannel, parity, R5, E2, unit-tests
# STRUCTURE: ▶ test_receive_unpack_validate ┌tar bytes + staging┐ → unpack → validate → ⎋ (project, service) │ ▶ test_receive_unpack_empty_negative → пустой stdin → False │ ▶ test_receive_flow_pre_deploy_gate_* → L1-гейт (176 A.2) блок до deploy │ ▶ test_orchestrator_receive_flow_parity_negative → ReceiveFlow vs DeployOrchestrator.receive() contract
# region MODULE_CONTRACT
## @purpose  Unit tests for deploy/receive_flow.py (DevPlan 119 E2 $TEST_SPEC): test_receive_unpack_validate
##           + R5 test_orchestrator_receive_flow_parity_negative (старый/новый код — одинаковый результат)
##           + DevPlan 176 A.2: pre-deploy L1-гейт (C1 root-эскалация) — violation блокирует
##           деплой ДО orchestrator.deploy; легитимный compose проходит.
## @scope    ReceiveFlow.unpack/validate/deploy изолированно; parity через DeployOrchestrator.receive()
##           (тонкий фасад-делегат) — контракт JSON + exit code сохранён.
## @invariants
##   - Native imports; tmp_path; stdin mocked через FakeStdin
##   - R5: parity — ReceiveFlow.run возвращает тот же exit-контракт, что receive() до E2
##   - 176 A.2: payload/staging-compose — L1-валидный (базовый _VALID_COMPOSE): receive теперь
##     исполняет pre-deploy L1-гейт, L1-инвалидный compose блокируется (privileged/cap_add/devices)
## @rationale  $TEST_SPEC E2 — unpack/validate тестируются изолированно; parity-тест фиксирует
##             сохранение контракта после экстракции (CC 15 → ≤8 на метод).
##             176 A.2 — C1: единственная реальная root-эскалация закрыта pre-up L1-гейтом.
## @changes  2026-08-02 · Created (DevPlan 119 E2)
## @changes  2026-08-16 · DevPlan 176 A.2 — фикстуры переведены на L1-валидный compose
##           (receive теперь гейтится) + тесты pre-deploy gate (block/pass/JSON)
# endregion MODULE_CONTRACT
"""

import io
import json
import logging
import os
import tarfile
from pathlib import Path

import pytest

from core.internal.deploy.receive_flow import ReceiveFlow, _default_pre_deploy_gate, _PreDeployBlocked

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# ── L1-валидный payload-compose (проходит ВСЕ 10 L1-контрактов: env_file .env.platform,
# healthcheck, labels platform.*, limits memory+cpus, proxy-net external; БЕЗ privileged/
# cap_add/devices/ports/секретов) — 176 A.2: receive теперь исполняет pre-deploy L1-гейт ──
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


def _make_payload_tar(tmp_path: Path, project: str = "testproj", compose: str = _VALID_COMPOSE) -> bytes:
    """Create a tar.gz payload in memory (ai-platform.yaml + docker-compose.yml + .env.platform)."""
    proj_dir = tmp_path / "payload"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").write_text(compose, encoding="utf-8")
    (proj_dir / "ai-platform.yaml").write_text(f"name: {project}\n", encoding="utf-8")
    (proj_dir / ".env.platform").write_text("PLATFORM_DOMAIN=example.com\n", encoding="utf-8")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", encoding="utf-8") as tar:
        for fname in ("docker-compose.yml", "ai-platform.yaml", ".env.platform"):
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

    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_log = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_log = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E2 unpack empty → False
# · Regression: fail-fast пустой stdin (контракт receive, БЕЗ || true-масок)
# · Remove if: unpack empty semantics change
# GUARD-PRESERVE (168): R5-негатив — единственное покрытие unpack(empty) fail-fast (контракт receive, без || true-масок)
def test_receive_unpack_empty_negative() -> None:
    """ReceiveFlow.unpack(empty) → False (fail-fast, no staging side-effect)."""
    flow = ReceiveFlow()
    assert flow.unpack(b"", "/tmp/nonexistent-staging") is False


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E2 validate missing ai-platform.yaml → ConfigValidationError
# · Regression: fail-fast отсутствие ai-platform.yaml (контракт receive, B4 — не bare ValueError)
# · Remove if: validate contract change
def test_receive_validate_missing_yaml_negative(tmp_path: Path) -> None:
    """ReceiveFlow.validate without ai-platform.yaml → ConfigValidationError."""
    from core.internal.shared.exceptions import ConfigValidationError

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    flow = ReceiveFlow()
    with pytest.raises(ConfigValidationError):
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

    from core.internal.deploy.healthcheck_poller import HealthcheckResult
    from core.internal.deploy.orchestrator import DeployOrchestrator

    tar_bytes = _make_payload_tar(tmp_path)

    class _DeployOKOrch(DeployOrchestrator):
        def _deploy_compose(self, _project_dir, _service, _version):
            return True

        def _run_post_deploy_chain(self, _project, _version, _status, _project_dir=None, _node_name=""):
            return None

    class _FakePoller:
        def poll_until_healthy(self, _project_name, _project_dir):
            return HealthcheckResult(status="healthy", project="testproj", method="test", attempts=1)

    def _factory(*args, **kwargs):
        if not args and "projects_base" not in kwargs:
            kwargs["projects_base"] = str(tmp_path)
        if "healthcheck_poller" not in kwargs:
            kwargs["healthcheck_poller"] = _FakePoller()
        return _DeployOKOrch(*args, **kwargs)

    # DI (W-H): stream= io.BytesIO + orchestrator_factory (0 патчей stdin/класса)
    orch = DeployOrchestrator(projects_base=str(tmp_path))
    rc = orch.receive(
        project_name="testproj", version="abc123", stream=io.BytesIO(tar_bytes), orchestrator_factory=_factory
    )

    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["project"] == "testproj"
    assert payload["version"] == "abc123"  # D5 sha-pinning
    assert rc in {0, 1}
    assert rc == (0 if payload["status"] in {"DEPLOYED", "PARTIAL", "SKIPPED"} else 1)
    logger.critical("[IMP:9][test] receive parity OK — status=%s rc=%d", payload["status"], rc)


# ═══════════════════════════════════════════════════════════════════════════
# region Tests: root-owned bootstrap-стуб overwrite (D11 — DevPlan 136 W1 T1.11)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D11 — root-owned стуб docker-compose.yml (9f91a78)
# · Scenario: target_dir/docker-compose.yml = root-owned readonly (GENERATED-STUB из context_deployer φ8) →
# ·   flow.deploy → os.remove + shutil.copy2 перезаписывает payload (dir ci-deploy-writable)
# · Last fail: 2026-08-04 — Permission denied при overwrite root-файла (fresh bootstrap → CI receive)
# · Remove if: receive copy-логика меняется
def test_receive_deploy_overwrites_root_owned_stub(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D11: receive перезаписывает root-owned readonly стуб docker-compose.yml (os.remove + copy2)."""
    caplog.set_level(logging.INFO)
    from unittest.mock import MagicMock

    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    stub = target_dir / "docker-compose.yml"
    stub.write_text("# GENERATED-STUB (bootstrap)\nservices: {}\n", encoding="utf-8")
    Path(stub).chmod(0o444)  # readonly — имитация root-owned файла (прямой overwrite дал бы Permission denied)

    staging = tmp_path / "staging"
    staging.mkdir()
    # 176 A.2: L1-валидный compose (receive исполняет pre-deploy L1-гейт)
    (staging / "docker-compose.yml").write_text(_VALID_COMPOSE, encoding="utf-8")
    (staging / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")

    class _FakeStatus:
        value = "DEPLOYED"

    class _FakeResult:
        def __init__(self) -> None:
            self.status = _FakeStatus()
            self.version = "abc123"

        def is_success(self) -> bool:
            return True

        def to_dict(self) -> dict:
            return {"status": "DEPLOYED", "project": "testproj", "version": "abc123"}

    fake_orch = MagicMock()
    fake_orch.deploy.return_value = _FakeResult()

    # 170 W10-B: orchestrator_factory — конструкторный DI (ReceiveFlow не импортирует DeployOrchestrator)
    flow = ReceiveFlow(
        projects_base=str(tmp_path / "projects"),
        orchestrator_factory=lambda *_, **__: fake_orch,
    )
    result = flow.deploy(
        "testproj",
        "testproj",
        "abc123",
        str(staging),
        str(target_dir),
        base=str(tmp_path / "projects"),
    )

    assert result.is_success() is True
    new_content = stub.read_text(encoding="utf-8")
    assert "nginx:alpine" in new_content, f"D11: payload обязан перезаписать root-owned стуб:\n{new_content}"
    assert "# GENERATED-STUB" not in new_content, "D11: стуб заменён payload'ом (не смержен)"
    assert "[IMP:9][ReceiveFlow][deploy]" in caplog.text, "IMP:9 deploy-лог ожидался"

    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_log = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_log = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"
    logger.critical("[IMP:9][test] D11 PASS: root-owned стуб перезаписан через os.remove + copy2")


# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · D11 — os.remove падает → WARN, copy продолжается
# · Scenario: os.remove(dest) кидает OSError → «Cannot remove existing» WARN (ошибка всплывёт на copy2)
# · Last fail: 2026-08-04 — без os.remove copy2 в root-файл давал Permission denied (receive FAIL)
# · Remove if: receive copy-логика меняется
def test_receive_deploy_remove_failure_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R5 negative (D11): os.remove падает → WARN-лог, copy не блокируется молча (никаких pass-tests)."""
    caplog.set_level(logging.INFO)
    from unittest.mock import MagicMock

    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    (target_dir / "docker-compose.yml").write_text("old-stub\n", encoding="utf-8")

    staging = tmp_path / "staging"
    staging.mkdir()
    # 176 A.2: L1-валидный compose (receive исполняет pre-deploy L1-гейт)
    (staging / "docker-compose.yml").write_text(_VALID_COMPOSE, encoding="utf-8")

    fake_orch = MagicMock()
    fake_orch.deploy.return_value = MagicMock(is_success=lambda: True, status=type("S", (), {"value": "DEPLOYED"})())

    # DI (W-H): os.remove-failure канал — WARN-путь D11 (0 патчей DeployOrchestrator-класса)
    real_remove = os.remove

    def _remove_fail(path):
        raise OSError(13, "Permission denied")

    os.remove = _remove_fail  # type: ignore[assignment]
    try:
        # 170 W10-B: orchestrator_factory — конструкторный DI
        flow = ReceiveFlow(
            projects_base=str(tmp_path / "projects"),
            orchestrator_factory=lambda *_, **__: fake_orch,
        )
        flow.deploy(
            "testproj",
            "testproj",
            "abc123",
            str(staging),
            str(target_dir),
            base=str(tmp_path / "projects"),
        )
    finally:
        os.remove = real_remove

    assert "Cannot remove existing" in caplog.text, "D11: WARN о неудачном os.remove ожидался"
    logger.critical("[IMP:9][test] D11 negative PASS: os.remove-fail логируется WARN (не молча)")


# endregion Tests: root-owned bootstrap-стуб overwrite (D11 — DevPlan 136 W1 T1.11)


# ═══════════════════════════════════════════════════════════════════
# DevPlan 176 A.2 (C1 root-эскалация): pre-deploy L1-гейт в receive-флоу
# ═══════════════════════════════════════════════════════════════════

# L1-нарушающий compose (точный вектор C1): privileged: true — блок ДО запуска контейнеров
_PRIVILEGED_COMPOSE: str = _VALID_COMPOSE.replace(
    "    image: nginx:alpine\n",
    "    image: nginx:alpine\n    privileged: true\n",
)


# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · 176 A.2 — privileged compose → _PreDeployBlocked
# · Last fail: prior — receive исполнял compose с privileged:true ДО любых L1-проверок
# ·   (C1 root-эскалация: ci-deploy в docker-группе = root-эквивалент ноды)
# · Remove if: pre-deploy L1-гейт убирается/смягчается (запрещено — security-гейт C1)
def test_receive_flow_pre_deploy_gate_blocks_privileged(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1-гейт (176 A.2): privileged:true в staging → _PreDeployBlocked ДО orchestrator.deploy."""
    caplog.set_level(logging.INFO)
    from unittest.mock import MagicMock

    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    (target_dir / "docker-compose.yml").write_text("OLD-payload\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "docker-compose.yml").write_text(_PRIVILEGED_COMPOSE, encoding="utf-8")
    (staging / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")

    fake_orch = MagicMock()
    flow = ReceiveFlow(
        projects_base=str(tmp_path / "projects"),
        orchestrator_factory=lambda *_, **__: fake_orch,
    )
    with pytest.raises(_PreDeployBlocked) as exc_info:
        flow.deploy(
            "testproj",
            "testproj",
            "sha1",
            str(staging),
            str(target_dir),
            base=str(tmp_path / "projects"),
        )

    assert exc_info.value.report.has_blocking_violation()
    blocked_ids = [f.contract_id for f in exc_info.value.report.findings if f.severity == "block"]
    assert "privileged" in blocked_ids, f"L1-гейт обязан поймать privileged: {blocked_ids}"
    fake_orch.deploy.assert_not_called(), "ДО запуска контейнеров — orchestrator.deploy НЕ вызывается"
    assert (target_dir / "docker-compose.yml").read_text(encoding="utf-8") == "OLD-payload\n", (
        "блок НЕ должен мутировать target_dir (гейт на staging ДО копирования)"
    )
    assert "[IMP:10][ReceiveFlow][pre-deploy] BLOCKED" in caplog.text, "IMP:10 pre-deploy BLOCKED ожидался"


# 🧪 TRAP[TEST] · 2026-08-16 · unit · 176 A.2 — валидный compose → гейт PASS, deploy продолжается
# · Regression: легитимный проект (L1-чистый) НЕ блокируется pre-deploy гейтом
# · Last fail: N/A (new gate)
# · Remove if: pre-deploy L1-гейт меняется
def test_receive_flow_pre_deploy_gate_passes_valid(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L1-валидный staging → гейт PASS, копирование + orchestrator.deploy исполняются."""
    caplog.set_level(logging.INFO)
    from unittest.mock import MagicMock

    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "docker-compose.yml").write_text(_VALID_COMPOSE, encoding="utf-8")
    (staging / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")
    (staging / ".env.platform").write_text("PLATFORM_DOMAIN=example.com\n", encoding="utf-8")

    fake_orch = MagicMock()
    fake_orch.deploy.return_value = MagicMock(
        is_success=lambda: True,
        status=type("S", (), {"value": "DEPLOYED"})(),
        to_dict=lambda: {"status": "DEPLOYED", "project": "testproj", "version": "sha1"},
        version="sha1",
    )
    flow = ReceiveFlow(
        projects_base=str(tmp_path / "projects"),
        orchestrator_factory=lambda *_, **__: fake_orch,
    )
    result = flow.deploy(
        "testproj",
        "testproj",
        "sha1",
        str(staging),
        str(target_dir),
        base=str(tmp_path / "projects"),
    )

    assert result.is_success() is True
    fake_orch.deploy.assert_called_once()
    assert (target_dir / "docker-compose.yml").is_file(), "валидный payload скопирован в target_dir"
    assert "[IMP:9][ReceiveFlow][pre-deploy] L1 gate PASS" in caplog.text, "IMP:9 pre-deploy PASS ожидался"

    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_log = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_log = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · 176 A.2 — run(): L1-блок → JSON FAILED + exit 1
# · Last fail: prior — receive с privileged compose возвращал DEPLOYED (контейнеры запускались)
# · Remove if: pre-deploy gate output-контракт меняется (JSON FAILED + exit 1)
def test_receive_flow_pre_deploy_gate_run_blocks_json(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys,
) -> None:
    """run() c L1-нарушающим tar → exit 1, JSON FAILED в stdout, [PRACTICES:BLOCK] в stderr."""
    caplog.set_level(logging.INFO)
    from unittest.mock import MagicMock

    tar_bytes = _make_payload_tar(tmp_path, compose=_PRIVILEGED_COMPOSE)
    fake_orch = MagicMock()
    flow = ReceiveFlow(
        projects_base=str(tmp_path / "projects"),
        orchestrator_factory=lambda *_, **__: fake_orch,
    )
    rc = flow.run(project_name="testproj", version="sha1", stream=io.BytesIO(tar_bytes))

    captured = capsys.readouterr()
    assert rc == 1, "L1-блок → exit 1 (контракт forced-command)"
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["status"] == "FAILED"
    assert "PRACTICES:BLOCK" in payload["error"]
    assert "containers NOT started" in payload["error"]
    assert "[PRACTICES:BLOCK]" in captured.err and "privileged" in captured.err, (
        "[PRACTICES:BLOCK]-отчёт обязан быть в stderr (CI-видимый)"
    )
    fake_orch.deploy.assert_not_called(), "блок ДО orchestrator.deploy — контейнеры НЕ запускаются"
    assert "[IMP:10][ReceiveFlow][run] pre-deploy L1 gate BLOCKED" in caplog.text


# 🧪 TRAP[TEST] · 2026-08-16 · unit · 176 A.2 — _default_pre_deploy_gate: l1_only (без docker-L2)
# · Regression: pre-up гейт не должен тянуть docker-подвызовы (compose-config/build-check)
# ·   и drift — чистая L1-статика, быстрый fail-fast в receive-канале
# · Last fail: N/A (new gate)
# · Remove if: l1_only семантика _default_pre_deploy_gate меняется
def test_default_pre_deploy_gate_l1_only(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_default_pre_deploy_gate: L1-нарушение блокирует, docker-L2/drift НЕ исполняются."""
    caplog.set_level(logging.INFO)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "docker-compose.yml").write_text(_PRIVILEGED_COMPOSE, encoding="utf-8")
    (proj / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")

    report = _default_pre_deploy_gate(str(proj), "testproj")

    assert report.has_blocking_violation(), f"privileged обязан блокировать: {report.format_for_ssh()}"
    blocked_ids = [f.contract_id for f in report.findings if f.severity == "block"]
    assert "privileged" in blocked_ids
    run_ids = [f.contract_id for f in report.findings]
    assert "compose-config-valid" not in run_ids, "l1_only: docker-L2 compose-config НЕ исполняется"
    assert "build-check" not in run_ids, "l1_only: docker-L2 build-check НЕ исполняется"
    assert "drift-practices" not in run_ids, "l1_only: drift-practices (L2) НЕ исполняется"
    logger.critical("[IMP:9][test] default pre-deploy gate l1_only PASS: %s", report.format_for_ssh())
