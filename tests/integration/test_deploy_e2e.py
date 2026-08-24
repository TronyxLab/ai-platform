"""Integration test for full deploy cycle: assemble_payload → channel deliver → deploy_compose → healthcheck → audit."""
# GREP_SUMMARY: test-deploy-e2e, integration, full-cycle, deploy, payload, channel, healthcheck, audit
# STRUCTURE: ▶ test_full_deploy_cycle_mocked → test_payload_assemble_and_deliver → test_audit_trail → test_history_snapshot_cycle
# region MODULE_CONTRACT
## @purpose  Integration test for full deploy cycle — verifies end-to-end interaction
##           between PayloadDeliverer.assemble_payload(), DeliveryChannel, DeployOrchestrator,
##           HealthcheckPoller, DeployAuditLogger, and DeployHistory. Uses mocked Docker/SSH.
## @scope    Integration (mocked infrastructure — no real Docker/SSH). Validates AC16.
## @invariants
##   - Payload assembly creates valid tar.gz
##   - Channel delivery produces correct DeliveryResult
##   - DeployOrchestrator orchestrates all steps in order
##   - Audit logger records all operations
##   - History snapshot created after deploy
## @changes 2026-07-30 | DevPlan 089 T19 — Created
## @changes 2026-08-13 | DevPlan 160 W6 T6.1 — test_orchestrator_with_mock_channel: мок
##           _deploy_compose (76.2s → <1s). Оркестрация полная: deliver → deploy → healthcheck →
##           audit → history; реальный docker compose не запускается.
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import json
import logging
import pathlib
import tempfile
from pathlib import Path

import pytest

from core.internal.deploy.audit import DeployAuditLogger, DeployHistory
from core.internal.deploy.channels import (
    DeliveryChannel,
    DeliveryResult,
    Payload,
)
from core.internal.deploy.healthcheck_poller import HealthcheckPoller
from core.internal.deploy.orchestrator import (
    DeployOrchestrator,
)
from core.internal.deploy.payload_deliverer import PayloadDeliverer

logger = logging.getLogger(__name__)

# ── Mock Channel for integration test ──


class IntegrationMockChannel(DeliveryChannel):
    """Mock channel that records deliveries."""

    def __init__(self) -> None:
        super().__init__(timeout=30)
        self.deliveries: list[Payload] = []
        self.should_succeed = True

    def deliver(self, payload: Payload) -> DeliveryResult:
        self.deliveries.append(payload)
        if self.should_succeed:
            return DeliveryResult(success=True, stdout="delivered-ok", exit_code=0, duration_s=0.5)
        return DeliveryResult(success=False, error_message="delivery-failed", exit_code=1, duration_s=0.5)


# ── Fixtures ──


@pytest.fixture
def work_dir() -> str:
    """Create a temporary working directory."""
    path = tempfile.mkdtemp(prefix="test-e2e-")
    yield path
    import shutil

    if pathlib.Path(path).is_dir():
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def project_dir(work_dir: str) -> str:
    """Create a project directory with deploy files."""
    proj_dir = Path(work_dir) / "projects" / "test-project"
    pathlib.Path(proj_dir).mkdir(exist_ok=True, parents=True)

    # Create required files
    with pathlib.Path(Path(proj_dir) / "docker-compose.yml").open("w", encoding="utf-8") as f:
        f.write("services:\n  web:\n    image: nginx:alpine\n    ports:\n      - '8080:80'\n")
    with pathlib.Path(Path(proj_dir) / "ai-platform.yaml").open("w", encoding="utf-8") as f:
        f.write("project: test-project\nservice: web\nversion: v1.0.0\n")
    with pathlib.Path(Path(proj_dir) / ".env.platform").open("w", encoding="utf-8") as f:
        f.write("ENV=test\n")

    return proj_dir


# ── Integration tests ──


class TestDeployE2E:
    """End-to-end deploy cycle tests."""

    # region FUNC_test_payload_assemble
    ## @purpose  Verify PayloadDeliverer.assemble_payload creates valid tar.gz.
    def test_payload_assemble(self, project_dir: str, work_dir: str) -> None:
        """Verify assemble_payload produces valid tar.gz."""
        import tarfile

        deliverer = PayloadDeliverer(projects_base=Path(work_dir) / "projects")
        payload = deliverer.assemble_payload(
            project_name="test-project",
            version="v1.0.0",
            project_dir=project_dir,
        )

        assert payload is not None
        assert payload.project_name == "test-project"
        assert payload.version == "v1.0.0"
        assert payload.tar_path.exists()
        assert payload.tar_path.suffix == ".gz"

        # Verify it's a valid tar.gz
        with tarfile.open(str(payload.tar_path), "r:gz", encoding="utf-8") as tar:
            names = tar.getnames()
        assert "docker-compose.yml" in names
        assert "ai-platform.yaml" in names

        # Cleanup
        if payload.tar_path.exists():
            pathlib.Path(str(payload.tar_path)).unlink()

    # endregion FUNC_test_payload_assemble

    # region FUNC_test_channel_delivery
    ## @purpose  Verify channel.deliver() returns correct DeliveryResult.
    def test_channel_delivery(self, project_dir: str, work_dir: str) -> None:
        """Verify channel delivery produces correct result."""

        deliverer = PayloadDeliverer(projects_base=Path(work_dir) / "projects")
        payload = deliverer.assemble_payload(
            project_name="test-project",
            project_dir=project_dir,
        )

        channel = IntegrationMockChannel()
        # Add host metadata for delivery
        payload.metadata["host"] = "test-host"
        result = channel.deliver(payload)

        assert isinstance(result, DeliveryResult)
        assert len(channel.deliveries) == 1
        assert channel.deliveries[0].project_name == "test-project"

        # Cleanup
        if payload.tar_path.exists():
            pathlib.Path(str(payload.tar_path)).unlink()

    # endregion FUNC_test_channel_delivery

    # region FUNC_test_orchestrator_with_mock_channel
    ## @purpose  Verify DeployOrchestrator orchestrates full deploy cycle.
    def test_orchestrator_with_mock_channel(
        self,
        project_dir: str,
        work_dir: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify orchestrator runs full lifecycle with mock channel."""
        caplog.set_level(logging.INFO)

        projects_base = Path(work_dir) / "projects"
        log_file = Path(work_dir) / "audit.log"
        history = DeployHistory(projects_base=projects_base)
        audit = DeployAuditLogger(log_file=log_file)
        healthcheck = HealthcheckPoller(timeout=1, interval=1, max_retries=1)

        # 167 D6 (DI-zero): compose_deployer через конструктор (существующий seam D3) —
        # 0 патчей orchestrator._deploy_compose
        orchestrator = DeployOrchestrator(
            projects_base=projects_base,
            audit_logger=audit,
            deploy_history=history,
            healthcheck_poller=healthcheck,
            compose_deployer=lambda *_a, **_k: True,
            # REF-0006 W2: L1 pre-apply гейт блокирует fixture-compose (bind-mounts вне
            # контрактов) — e2e проверяет ОРКЕСТРАЦИЮ, не L1 (она покрыта unit-гейтом);
            # пермиссивный шов: пустой VerifyReport, тот же паттерн что в test_rollback_contour
            pre_apply_gate=lambda d, _p: __import__(
                "core.internal.deploy.verify_contracts", fromlist=["VerifyReport"]
            ).VerifyReport(project_dir=pathlib.Path(d), state="baseline", findings=()),
        )

        # T6.1 (DevPlan 160 W6): оркестрация (deliver → compose-шаг → healthcheck → snapshot →
        # audit) проверяется ПОЛНОСТЬЮ, но реальный docker compose НЕ запускается (было 76.2s).

        channel = IntegrationMockChannel()

        # Wrap in suppress to handle SystemExit from deploy_engine
        # in environments without Docker
        with contextlib.suppress(SystemExit):
            orchestrator.deploy(
                project_name="test-project",
                channel=channel,
                project_dir=project_dir,
            )

        # Check LDD trajectory (FRAG-4 fix, DevPlan 119 F9): мёртвая ветка
        # `if imp_level >= 9: pass` заменена на found_log + assert.
        found_log = False
        logger.info("\n--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in list(caplog.records):
            if "[IMP:" in record.message:
                imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    logger.info("%s", record.message)
                if imp_level >= 9:
                    found_log = True
        logger.info("--- END LDD TRAJECTORY ---\n")
        assert found_log, "No IMP:9 log found — LDD violation (FRAG-4, DevPlan 119 F9)"

        # Verify audit log was written
        assert pathlib.Path(log_file).is_file()
        with pathlib.Path(log_file).open(encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        # R2 (DevPlan 119 F10): len(entries) >= 0 — всегда истина (len ≥ 0 гарантирован
        # языком). Ужесточено до > 0 — audit-записи обязаны быть после deploy-цикла.
        assert len(entries) > 0, "Audit log должен содержать записи после deploy-цикла (R2)"

        # Verify channel was called
        assert len(channel.deliveries) >= 1

    # endregion FUNC_test_orchestrator_with_mock_channel

    # region FUNC_test_audit_history_interaction
    ## @purpose  Verify audit log and history interaction.
    def test_audit_history_interaction(
        self,
        project_dir: str,  # ruff: ignore[ARG002]
        work_dir: str,
    ) -> None:
        """Verify audit and history records are consistent."""
        log_file = Path(work_dir) / "audit.log"
        audit = DeployAuditLogger(log_file=log_file)
        history = DeployHistory(projects_base=Path(work_dir) / "projects")

        # Simulate deploy cycle
        audit.log(operation="deploy", project="test-project", channel="scp", result="DEPLOYED", duration_s=5.0)
        snap_id = history.create_snapshot(project="test-project", version="v1.0.0", health_status="healthy")

        audit.log(
            operation="rollback", project="test-project", result="ROLLED_BACK", duration_s=1.0, snapshot_id=snap_id
        )

        # Verify audit file
        with pathlib.Path(log_file).open(encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        assert len(entries) == 2
        assert entries[0]["operation"] == "deploy"
        assert entries[1]["operation"] == "rollback"

        # Verify history
        snapshots = history.list_snapshots("test-project")
        assert len(snapshots) >= 1
        assert snapshots[0]["health_status"] == "healthy"

    # endregion FUNC_test_audit_history_interaction

    # 🧪 TRAP[TEST] · Regression · Integration test validates full deploy cycle (AC16)
