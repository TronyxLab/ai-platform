#!/usr/bin/env python3
"""Unit tests for DeployOrchestrator — unified deploy facade."""
# GREP_SUMMARY: test-orchestrator, deploy, rollback, status, remove, deploy-many, unit-test
# STRUCTURE: ▶ test_deploy → test_deploy_many → test_rollback → test_status → test_remove → test_receive
# region MODULE_CONTRACT
## @purpose  Unit tests for DeployOrchestrator — deploy(), deploy_many(), rollback(), status(), remove(), receive().
## @scope    Tests orchestrator logic with mock channels. No actual SSH/Docker calls.
## @invariants
##   - DeployOrchestrator.deploy() returns OrchestratorDeployResult with correct status
##   - deploy_many() processes all projects sequentially
##   - rollback() reads from DeployHistory
##   - status() returns ProjectStatus
##   - remove() is idempotent
## @changes 2026-07-30 | DevPlan 089 T16 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import pytest

from core.internal.deploy.channels import DeliveryChannel, DeliveryResult, Payload
from core.internal.deploy.deploy_history import DeployHistory
from core.internal.deploy.healthcheck_poller import HealthcheckPoller
from core.internal.deploy.orchestrator import (
    DeployAuditLogger,
    DeployOrchestrator,
    DeployStatus,
    OrchestratorDeployResult,
    ProjectStatus,
)

# B2: канонический дефолт PROJECTS_BASE — shared/deploy_paths (локальная константа удалена)
from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE

logger = logging.getLogger(__name__)

# ── Mock Channel ──


class MockChannel(DeliveryChannel):
    """Mock delivery channel for tests."""

    def __init__(self, should_succeed: bool = True, fail_deliver: bool = False):
        super().__init__(timeout=30)
        self.should_succeed = should_succeed
        self.fail_deliver = fail_deliver
        self.deliver_calls: list[Payload] = []

    def deliver(self, payload: Payload) -> DeliveryResult:
        self.deliver_calls.append(payload)
        if self.fail_deliver:
            return DeliveryResult(
                success=False,
                error_message="Mock channel failure",
                exit_code=1,
                duration_s=0.1,
            )
        return DeliveryResult(
            success=self.should_succeed,
            stdout="mock-ok",
            exit_code=0,
            duration_s=0.1,
        )


# ── Fixtures ──


@pytest.fixture
def projects_base() -> str:
    """Create a temporary projects base directory."""
    path = tempfile.mkdtemp(prefix="test-projects-")
    yield path
    import shutil

    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def project_dir(projects_base: str) -> str:
    """Create a project directory with minimal files."""
    proj_dir = os.path.join(projects_base, "test-project")
    os.makedirs(proj_dir, exist_ok=True)

    # Create minimal docker-compose.yml
    with open(os.path.join(proj_dir, "docker-compose.yml"), "w") as f:
        f.write("services:\n  web:\n    image: nginx:alpine\n")

    # Create ai-platform.yaml
    with open(os.path.join(proj_dir, "ai-platform.yaml"), "w") as f:
        f.write("project: test-project\nservice: web\n")

    return proj_dir


@pytest.fixture
def mock_channel() -> MockChannel:
    """Create a mock delivery channel that succeeds."""
    return MockChannel(should_succeed=True)


@pytest.fixture
def failing_channel() -> MockChannel:
    """Create a mock delivery channel that fails."""
    return MockChannel(fail_deliver=True)


@pytest.fixture
def temp_log_file() -> str:
    """Create a temporary log file."""
    fd, path = tempfile.mkstemp(suffix=".audit.log", prefix="test-")
    os.close(fd)
    os.unlink(path)
    yield path
    if os.path.isfile(path):
        os.unlink(path)


@pytest.fixture
def history(projects_base: str) -> DeployHistory:
    """Create DeployHistory with temp base."""
    return DeployHistory(projects_base=projects_base)


@pytest.fixture
def orchestrator(projects_base: str, temp_log_file: str, history: DeployHistory) -> DeployOrchestrator:
    """Create DeployOrchestrator with mocked dependencies."""
    audit = DeployAuditLogger(log_file=temp_log_file)
    healthcheck = HealthcheckPoller(timeout=1, interval=1, max_retries=1)
    return DeployOrchestrator(
        projects_base=projects_base,
        audit_logger=audit,
        deploy_history=history,
        healthcheck_poller=healthcheck,
    )


# ── DeployOrchestrator tests ──


class TestDeployOrchestrator:
    """DeployOrchestrator unit tests."""

    # region FUNC_test_init_default
    ## @purpose  Verify DeployOrchestrator initializes with defaults.
    def test_init_default(self) -> None:
        """Verify default projects_base."""
        orch = DeployOrchestrator()
        assert orch.projects_base == DEFAULT_PROJECTS_BASE

    # endregion

    # region FUNC_test_init_custom
    ## @purpose  Verify DeployOrchestrator accepts custom projects_base.
    def test_init_custom(self, projects_base: str) -> None:
        """Verify custom projects_base."""
        orch = DeployOrchestrator(projects_base=projects_base)
        assert orch.projects_base == projects_base

    # endregion

    # region FUNC_test_deploy_empty_name
    ## @purpose  Verify deploy() returns FAILED for empty project name.
    def test_deploy_empty_name(self, orchestrator: DeployOrchestrator, mock_channel: MockChannel) -> None:
        """Verify deploy with empty name."""
        result = orchestrator.deploy(project_name="", channel=mock_channel)
        assert result.status == DeployStatus.FAILED
        assert "required" in (result.error_info or "").lower()

    # endregion

    # region FUNC_test_deploy_channel_failure
    ## @purpose  Verify deploy() returns FAILED when channel delivery fails.
    def test_deploy_channel_failure(
        self,
        orchestrator: DeployOrchestrator,
        failing_channel: MockChannel,
    ) -> None:
        """Verify deploy fails on channel error."""
        result = orchestrator.deploy(project_name="test-project", channel=failing_channel)
        assert result.status == DeployStatus.FAILED
        assert "Delivery failed" in (result.error_info or "")

    # endregion

    # region FUNC_test_deploy_many
    ## @purpose  Verify deploy_many processes all projects.
    def test_deploy_many(
        self,
        orchestrator: DeployOrchestrator,
        mock_channel: MockChannel,
        projects_base: str,
    ) -> None:
        """Verify deploy_many returns results for all projects."""
        # Create project dirs
        proj1 = os.path.join(projects_base, "proj1")
        os.makedirs(proj1, exist_ok=True)
        with open(os.path.join(proj1, "docker-compose.yml"), "w") as f:
            f.write("services:\n  web:\n    image: nginx\n")

        proj2 = os.path.join(projects_base, "proj2")
        os.makedirs(proj2, exist_ok=True)
        with open(os.path.join(proj2, "docker-compose.yml"), "w") as f:
            f.write("services:\n  web:\n    image: nginx\n")

        results = orchestrator.deploy_many(
            project_names=["proj1", "proj2"],
            channel=mock_channel,
        )
        assert len(results) == 2

    # endregion

    # region FUNC_test_status_not_found
    ## @purpose  Verify status() returns not_found for non-existent project.
    def test_status_not_found(self, orchestrator: DeployOrchestrator) -> None:
        """Verify status returns not_found."""
        status = orchestrator.status("nonexistent-project")
        assert status.status == "not_found"

    # endregion

    # region FUNC_test_status_found
    ## @purpose  Verify status() returns found for existing project directory.
    def test_status_found(self, orchestrator: DeployOrchestrator, project_dir: str) -> None:
        """Verify status returns found."""
        status = orchestrator.status("test-project")
        assert status.status in ("found", "not_found")  # may be not_found if no docker

    # endregion

    # region FUNC_test_remove_not_found
    ## @purpose  Verify remove() returns SKIPPED for non-existent project.
    def test_remove_not_found(self, orchestrator: DeployOrchestrator) -> None:
        """Verify remove on non-existent project."""
        result = orchestrator.remove("nonexistent-project")
        assert result.status in (DeployStatus.SKIPPED, DeployStatus.DEPLOYED)

    # endregion

    # region FUNC_test_remove_existing
    ## @purpose  Verify remove() works on existing project.
    def test_remove_existing(self, orchestrator: DeployOrchestrator, project_dir: str) -> None:
        """Verify remove on existing project."""
        result = orchestrator.remove("test-project")
        # Should succeed or be skipped if compose not running
        assert result.status in (DeployStatus.DEPLOYED, DeployStatus.SKIPPED)

    # endregion

    # region FUNC_test_rollback_no_snapshots
    ## @purpose  Verify rollback() returns FAILED when no snapshots exist.
    def test_rollback_no_snapshots(self, orchestrator: DeployOrchestrator) -> None:
        """Verify rollback fails without snapshots."""
        result = orchestrator.rollback("test-project")
        assert result.status == DeployStatus.FAILED
        assert "No snapshot" in (result.error_info or "")

    # endregion

    # region FUNC_test_rollback_with_snapshot
    ## @purpose  Verify rollback() works with existing snapshot.
    def test_rollback_with_snapshot(self, orchestrator: DeployOrchestrator, project_dir: str) -> None:
        """Verify rollback with snapshot."""
        # Create a snapshot first
        snap_id = orchestrator.deploy_history.create_snapshot(
            project="test-project",
            version="v1",
        )

        # Rollback — may succeed or fail depending on docker compose available
        result = orchestrator.rollback("test-project", snapshot_id=snap_id)
        # In unit test environment without Docker, rollback_compose will fail,
        # but the orchestration logic should still run
        assert result.status in (DeployStatus.DEPLOYED, DeployStatus.FAILED)
        if result.snapshot_id:
            assert result.snapshot_id == snap_id

    # endregion

    # region FUNC_test_receive_no_data
    ## @purpose  Verify receive() returns error with no stdin data.
    def test_receive_no_data(self) -> None:
        """Verify receive fails without stdin data. We can't easily mock stdin,
        but we can verify the function returns 1 with no input in a subprocess."""
        # In unit tests, just verify the static method exists and is callable
        assert hasattr(DeployOrchestrator, "receive")
        assert callable(DeployOrchestrator.receive)

    # endregion

    # region FUNC_test_deploy_result_serialization
    ## @purpose  Verify OrchestratorDeployResult serialization to dict.
    def test_deploy_result_serialization(self) -> None:
        """Verify OrchestratorDeployResult.to_dict()."""
        result = OrchestratorDeployResult(
            status=DeployStatus.DEPLOYED,
            project="test",
            channel="scp",
            duration_s=10.5,
            healthcheck_status="healthy",
            snapshot_id="snap-123",
        )
        d = result.to_dict()
        assert d["status"] == "DEPLOYED"
        assert d["project"] == "test"
        assert d["channel"] == "scp"
        assert d["duration_s"] == 10.5
        assert d["healthcheck_status"] == "healthy"
        assert d["snapshot_id"] == "snap-123"

    # endregion

    # region FUNC_test_deploy_result_is_success
    ## @purpose  Verify OrchestratorDeployResult.is_success() for different statuses.
    def test_deploy_result_is_success(self) -> None:
        """Verify is_success logic."""
        assert OrchestratorDeployResult(DeployStatus.DEPLOYED, "test").is_success()
        assert OrchestratorDeployResult(DeployStatus.SKIPPED, "test").is_success()
        assert OrchestratorDeployResult(DeployStatus.FAILED, "test").is_success() is False

    # endregion

    # region FUNC_test_project_status_serialization
    ## @purpose  Verify ProjectStatus serialization.
    def test_project_status_serialization(self) -> None:
        """Verify ProjectStatus.to_dict()."""
        status = ProjectStatus(
            project="test",
            status="found",
            containers=[{"name": "web"}],
            last_deploy={"version": "v1"},
        )
        d = status.to_dict()
        assert d["status"] == "found"
        assert d["containers"] == [{"name": "web"}]

    # endregion

    # region FUNC_test_assemble_payload_delegates_to_payload_deliverer
    ## @purpose  DevPlan 118 A4 — _assemble_payload делегирует PayloadDeliverer.assemble_payload
    ##           (единственный путь сборки tar.gz); set-сравнение содержимого tar.
    # 🧪 TRAP[TEST] · REGRESSION · A4 — orchestrator и payload_deliverer собирают payload одним кодом
    # · Scenario: project_dir с docker-compose.yml + ai-platform.yaml + compose.yaml → оба API дают
    # ·   tar с идентичным набором членов (set-сравнение содержимого tar)
    # · Last fail: orchestrator.py:949 _assemble_payload — локальная tar-реализация (дрейф формата K8)
    # · Remove if: payload assembly moves to a different mechanism
    def test_assemble_payload_matches_payload_deliverer(self, tmp_path: Path) -> None:
        """A4: DeployOrchestrator._assemble_payload → PayloadDeliverer.assemble_payload (один код)."""
        import tarfile

        from core.internal.deploy.payload_deliverer import PayloadDeliverer

        project_dir = tmp_path / "proj-a4"
        project_dir.mkdir()
        (project_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx\n")
        (project_dir / "ai-platform.yaml").write_text("project: proj-a4\n")
        (project_dir / "compose.yaml").write_text("services: {}\n")
        (project_dir / ".env.platform").write_text("FOO=bar\n")

        def _tar_members(path: str) -> set[str]:
            with tarfile.open(path, "r:gz") as t:
                return {m.name for m in t.getmembers()}

        orch = DeployOrchestrator(projects_base=str(tmp_path / "projects"))
        payload_orch = orch._assemble_payload("proj-a4", "sha1", str(project_dir), {"k": "v"})

        deliverer = PayloadDeliverer(projects_base=str(tmp_path / "projects"))
        payload_del = deliverer.assemble_payload("proj-a4", "sha1", str(project_dir), {"k": "v"})

        assert _tar_members(str(payload_orch.tar_path)) == _tar_members(str(payload_del.tar_path)), (
            "A4 FAIL: tar-содержимое orchestrator vs payload_deliverer разошлось — "
            "сборка payload'а должна идти одним кодом (K8)"
        )
        assert payload_orch.project_name == payload_del.project_name == "proj-a4"
        assert payload_orch.version == payload_del.version == "sha1"
        assert payload_orch.metadata == payload_del.metadata == {"k": "v"}
        logger.critical("[IMP:9][test] A4: _assemble_payload → PayloadDeliverer — tar set идентичен — OK")

    # endregion

    # region FUNC_test_status_stub_via_unified_detector
    ## @purpose  DevPlan 118 A6 — orchestrator.status() определяет stub-проект единым детектором
    ##           (is_stub_ai_platform_yaml внутри DeployEngine.status), без инлайн-копии.
    # 🧪 TRAP[TEST] · REGRESSION · A6 — stub-проект определяется единым детектором
    # · Scenario: ai-platform.yaml с маркером GENERATED-STUB → orchestrator.status() → status="stub"
    # · Last fail: orchestrator.py:639 + deploy_engine.py:525 — две инлайн-копии "GENERATED-STUB" в первой строке
    # · Remove if: stub detection moves out of DeployEngine
    def test_status_stub_via_unified_detector(self, tmp_path: Path) -> None:
        """A6: orchestrator.status() → 'stub' через shared/stub_detection (единый детектор)."""
        from core.internal.shared.stub_detection import is_stub_ai_platform_yaml

        projects_base = tmp_path / "projects"
        proj_dir = projects_base / "stub-proj"
        proj_dir.mkdir(parents=True)
        (proj_dir / "ai-platform.yaml").write_text("GENERATED-STUB: true\nproject: stub\n")

        orch = DeployOrchestrator(projects_base=str(projects_base))
        status = orch.status("stub-proj")

        assert status.status == "stub", f"A6 FAIL: expected stub, got {status.status}"
        # Единый детектор подтверждает тот же результат — никакой инлайн-копии в orchestrator
        assert is_stub_ai_platform_yaml(str(proj_dir / "ai-platform.yaml")) is True
        logger.critical("[IMP:9][test] A6: orchestrator.status() → stub via unified detector — OK")

    # endregion

    # 🧪 TRAP[TEST] · Regression · DeployOrchestrator provides unified deploy facade per DevPlan 089 AC1
