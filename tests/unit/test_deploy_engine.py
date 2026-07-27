"""
# GREP_SUMMARY: test_deploy_engine, deploy-engine, atomic-deploy, rollback, remove, status, healthcheck, snapshot, prune, mocked-docker
# STRUCTURE: ▶ setUp(tmp_path + mock_subprocess) → ◇ deploy_tests: success/first-fail/health-rollback/pull-fail →
#            ◇ remove_tests: success/not-found/no-minus-v → ◇ status_tests: found/not-found/stub →
#            ◇ engine_helpers: save-image/capture-snapshot/perf-rollback/poll-health → ⎋ LDD IMP:9 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/deploy_engine.py — DeployEngine class with mocked Docker
## @scope    All operations mocked. No real Docker required. Uses tmp_path fixtures.
## @invariants — Each test verifies LDD IMP:9 business logic log presence
## @changes 2026-07-26 · DevPlan 036E — Created (Wave 5e Strangler-Fig)
# endregion MODULE_CONTRACT
"""

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from core.internal.deploy.deploy_engine import (
    DeployEngine,
    DeployResult,
    ImageInfo,
    RemoveResult,
    SnapshotInfo,
    StatusResult,
)

logger = logging.getLogger(__name__)


# ── Helpers ──


@pytest.fixture
def tmp_project(tmp_path: Path) -> str:
    """Create a mock project directory with compose and ai-platform yaml."""
    project_dir = tmp_path / "projects" / "test-app"
    project_dir.mkdir(parents=True)
    (project_dir / "docker-compose.yml").write_text("version: '3'\nservices:\n  app:\n    image: test\n")
    (project_dir / "ai-platform.yaml").write_text("service: app\n")
    return str(project_dir)


@pytest.fixture
def engine() -> DeployEngine:
    """Create DeployEngine with test projects base."""
    return DeployEngine(projects_base="/tmp/test-projects")


def _check_ldd(caplog, min_level: int = 9) -> bool:
    """Check LDD trajectory for IMP:>=min_level logs."""
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= min_level:
                found = True
    print("--- END LDD TRAJECTORY ---")
    return found


# ═══════════════════════════════════════════════════════════════════
# region Tests: deploy() — mocked at method level
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · full deploy pipeline succeeds
# · Scenario: save_previous→capture_snapshot→preflight→pull→up→health→success
# · Last fail: N/A (new test)
@patch.object(DeployEngine, "_preflight_checks", return_value=None)
@patch.object(DeployEngine, "_save_previous_image", return_value=ImageInfo(id="sha256:prev", tag="test:latest"))
@patch.object(
    DeployEngine,
    "_capture_deploy_snapshot",
    return_value=SnapshotInfo(timestamp=12345, ps_file="/tmp/test_ps.txt", images_file="/tmp/test_images.txt"),
)
@patch.object(DeployEngine, "_pull_image_with_retry", return_value=True)
@patch.object(DeployEngine, "_atomic_up", return_value=True)
@patch.object(DeployEngine, "_poll_health", return_value=True)
def test_deploy_success(
    mock_health, mock_up, mock_pull, mock_snap, mock_save, mock_preflight, caplog, tmp_project, engine
):
    """Full deploy pipeline should succeed with all steps mocked."""
    caplog.set_level(logging.INFO)

    result = engine.deploy(
        project="test-app",
        ref="v1.0.0",
        service="app",
        project_dir=tmp_project,
        max_wait=5,
    )

    assert _check_ldd(caplog), "Missing IMP:9 business logic log"
    assert result.success is True
    assert result.project == "test-app"
    assert result.ref == "v1.0.0"
    assert result.rollback_performed is False
    assert result.first_deploy_failed is False
    assert mock_preflight.called
    assert mock_save.called
    assert mock_pull.called
    assert mock_up.called
    assert mock_health.called
    logger.critical("[IMP:9][test] deploy_success: success=%s — OK", result.success)


# 🧪 TRAP[TEST] · Regression · first deploy health fail → sys.exit
# · Scenario: No previous image, health fails → _handle_first_deploy → sys.exit(1)
# · Last fail: N/A (new test)
@patch.object(DeployEngine, "_preflight_checks", return_value=None)
@patch.object(DeployEngine, "_save_previous_image", return_value=None)  # first deploy
@patch.object(
    DeployEngine,
    "_capture_deploy_snapshot",
    return_value=SnapshotInfo(timestamp=12345, ps_file="/tmp/test_ps.txt", images_file="/tmp/test_images.txt"),
)
@patch.object(DeployEngine, "_pull_image_with_retry", return_value=True)
@patch.object(DeployEngine, "_atomic_up", return_value=True)
@patch.object(DeployEngine, "_poll_health", return_value=False)  # health fails
def test_deploy_first_deploy_fail(
    mock_health, mock_up, mock_pull, mock_snap, mock_save, mock_preflight, caplog, tmp_project, engine
):
    """First deploy with health fail should raise SystemExit."""
    caplog.set_level(logging.INFO)

    with pytest.raises(SystemExit) as exc_info:
        engine.deploy(
            project="test-app",
            ref="v1.0.0",
            service="app",
            project_dir=tmp_project,
            max_wait=2,
        )

    assert exc_info.value.code == 1
    assert _check_ldd(caplog), "Missing IMP:9 log"
    logger.critical("[IMP:9][test] first_deploy_fail: SystemExit(1) — OK")


# 🧪 TRAP[TEST] · Regression · healthcheck fail → rollback occurs
# · Scenario: Existing deploy, health fails → _perform_rollback → DeployResult(rollback_performed=True)
# · Last fail: N/A (new test)
@patch.object(DeployEngine, "_preflight_checks", return_value=None)
@patch.object(DeployEngine, "_save_previous_image", return_value=ImageInfo(id="sha256:prev", tag="test:prev"))
@patch.object(
    DeployEngine,
    "_capture_deploy_snapshot",
    return_value=SnapshotInfo(timestamp=12345, ps_file="/tmp/test_ps.txt", images_file="/tmp/test_images.txt"),
)
@patch.object(DeployEngine, "_pull_image_with_retry", return_value=True)
@patch.object(DeployEngine, "_atomic_up", return_value=True)
@patch.object(DeployEngine, "_poll_health", return_value=False)  # health fails
@patch.object(DeployEngine, "_perform_rollback", return_value=True)  # rollback succeeds
def test_deploy_rollback(
    mock_rollback, mock_health, mock_up, mock_pull, mock_snap, mock_save, mock_preflight, caplog, tmp_project, engine
):
    """Healthcheck failure should trigger rollback for existing deploy."""
    caplog.set_level(logging.INFO)

    result = engine.deploy(
        project="test-app",
        ref="v2.0.0-broken",
        service="app",
        project_dir=tmp_project,
        max_wait=2,
    )

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is False
    assert result.rollback_performed is True
    assert "rollback performed" in (result.error_message or "")
    assert mock_rollback.called
    logger.critical("[IMP:9][test] deploy_rollback: rollback=%s — OK", result.rollback_performed)


# 🧪 TRAP[TEST] · Regression · pull fails after 3 retries → first deploy exit
# · Scenario: All 3 pull attempts fail, first deploy → sys.exit(1)
# · Last fail: N/A (new test)
@patch.object(DeployEngine, "_preflight_checks", return_value=None)
@patch.object(DeployEngine, "_save_previous_image", return_value=None)  # first deploy
@patch.object(
    DeployEngine,
    "_capture_deploy_snapshot",
    return_value=SnapshotInfo(timestamp=12345, ps_file="/tmp/test_ps.txt", images_file="/tmp/test_images.txt"),
)
@patch.object(DeployEngine, "_pull_image_with_retry", return_value=False)  # pull fails
def test_pull_image_all_fail(mock_pull, mock_snap, mock_save, mock_preflight, caplog, tmp_project, engine):
    """All pull attempts fail should trigger first-deploy failure."""
    caplog.set_level(logging.INFO)

    with pytest.raises(SystemExit) as exc_info:
        engine.deploy(
            project="test-app",
            ref="v1.0.0",
            service="app",
            project_dir=tmp_project,
            max_wait=2,
        )

    assert exc_info.value.code == 1
    assert _check_ldd(caplog), "Missing IMP:9 log"
    logger.critical("[IMP:9][test] pull_fail_all: SystemExit(1) — OK")


# endregion deploy tests


# ═══════════════════════════════════════════════════════════════════
# region Tests: remove()
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · remove active project
# · Scenario: project exists → docker compose down called WITHOUT -v
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
def test_remove_active(mock_run, caplog, engine, tmp_project):
    """Remove should stop containers with docker compose down (no -v)."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    result = engine.remove(project="test-app", project_dir=tmp_project)

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is True
    assert result.already_removed is False

    # Verify docker compose down was called and does NOT contain -v
    compose_down_calls = [
        c for c in mock_run.call_args_list if "docker" in str(c) and "compose" in str(c) and "down" in str(c)
    ]
    assert len(compose_down_calls) >= 1, "docker compose down should be called"
    for call in compose_down_calls:
        args_str = str(call)
        assert "-v" not in args_str or " — " in args_str, "remove() must NOT use -v flag"

    logger.critical("[IMP:9][test] remove_active: success=%s — OK", result.success)


# 🧪 TRAP[TEST] · Regression · remove already removed project
# · Scenario: project directory doesn't exist → already_removed=True
# · Last fail: N/A (new test)
def test_remove_already_removed(caplog, engine):
    """Remove should be idempotent when project directory is missing."""
    caplog.set_level(logging.INFO)

    result = engine.remove(project="nonexistent", project_dir="/tmp/nonexistent")

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is True
    assert result.already_removed is True
    logger.critical("[IMP:9][test] remove_already_removed: already_removed=%s — OK", result.already_removed)


# endregion remove tests


# ═══════════════════════════════════════════════════════════════════
# region Tests: status()
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · status not found
# · Scenario: project directory missing → status="not_found"
# · Last fail: N/A (new test)
def test_status_not_found(caplog, engine):
    """Status should return 'not_found' when project dir missing."""
    caplog.set_level(logging.INFO)

    result = engine.status(project="nonexistent", project_dir="/tmp/nonexistent")

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.status == "not_found"
    logger.critical("[IMP:9][test] status_not_found: status=%s — OK", result.status)


# 🧪 TRAP[TEST] · Regression · status stub detection
# · Scenario: ai-platform.yaml starts with GENERATED-STUB → status="stub"
# · Last fail: N/A (new test)
def test_status_stub(caplog, engine, tmp_path):
    """Status should detect GENERATED-STUB when stub_aware=True."""
    caplog.set_level(logging.INFO)

    project_dir = tmp_path / "projects" / "stub-project"
    project_dir.mkdir(parents=True)
    (project_dir / "ai-platform.yaml").write_text("GENERATED-STUB: true\nproject: stub\n")

    result = engine.status(project="stub-project", project_dir=str(project_dir), stub_aware=True)

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.status == "stub"
    logger.critical("[IMP:9][test] status_stub: status=%s — OK", result.status)


# 🧪 TRAP[TEST] · Regression · status found with containers
# · Scenario: project exists with docker compose ps data → status="found"
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
def test_status_found(mock_run, caplog, engine, tmp_project):
    """Status should return 'found' with containers when project exists."""
    caplog.set_level(logging.INFO)

    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"Name":"test-app","State":"running"}\n',
        stderr="",
    )

    result = engine.status(project="test-app", project_dir=tmp_project)

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.status == "found"
    assert len(result.containers) >= 1
    logger.critical("[IMP:9][test] status_found: status=%s containers=%d — OK", result.status, len(result.containers))


# endregion status tests


# ═══════════════════════════════════════════════════════════════════
# region Tests: engine helpers
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · save_previous_image returns ImageInfo
# · Scenario: Docker compose images returns ID → ImageInfo with ID and tag
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
def test_save_previous_image_exists(mock_run, caplog, tmp_project, engine):
    """_save_previous_image should return ImageInfo when image exists."""
    caplog.set_level(logging.INFO)

    # Two calls: compose images -q returns ID, image inspect returns tag
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="sha256:prev123\n", stderr=""),  # compose images -q
        MagicMock(returncode=0, stdout="test-app:latest\n", stderr=""),  # image inspect --format
    ]

    result = engine._save_previous_image(tmp_project, "app")

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result is not None
    assert result.id == "sha256:prev123"
    assert result.tag == "test-app:latest"
    logger.critical("[IMP:9][test] save_prev_exists: id=%s — OK", result.id)


# 🧪 TRAP[TEST] · Regression · save_previous_image returns None on first deploy
# · Scenario: compose images returns empty → None
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
def test_save_previous_image_first_deploy(mock_run, caplog, tmp_project, engine):
    """_save_previous_image should return None for first deploy."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    result = engine._save_previous_image(tmp_project, "app")

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result is None
    logger.critical("[IMP:9][test] save_prev_first_deploy: None — OK")


# 🧪 TRAP[TEST] · Regression · capture snapshot creates marker file
# · Scenario: Snapshot dir + .deploy-started created
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
def test_capture_snapshot_creates_files(mock_run, caplog, tmp_project, engine):
    """_capture_deploy_snapshot should create .deploy-started marker."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

    result = engine._capture_deploy_snapshot(tmp_project)
    snapshot_dir = os.path.join(tmp_project, ".deploy-snapshots")
    started_file = os.path.join(snapshot_dir, ".deploy-started")

    assert os.path.isfile(started_file), ".deploy-started marker should exist"
    assert _check_ldd(caplog), "Missing IMP:9 log"
    logger.critical("[IMP:9][test] capture_snapshot: ts=%s — OK", result.timestamp)


# 🧪 TRAP[TEST] · Regression · poll_health finds healthy container
# · Scenario: docker ps returns cid, inspect returns healthy → True
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
@patch("core.internal.deploy.deploy_engine.time.sleep", return_value=None)
def test_poll_health_healthy(mock_sleep, mock_run, caplog, tmp_project, engine):
    """_poll_health should return True when container is healthy."""
    caplog.set_level(logging.INFO)

    # compose ps -q returns cid, inspect returns healthy
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="container123\n", stderr=""),  # compose ps -q
        MagicMock(returncode=0, stdout="running healthy\n", stderr=""),  # inspect
    ]

    result = engine._poll_health(tmp_project, "app", timeout=5, interval=1)

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result is True
    logger.critical("[IMP:9][test] poll_health_healthy: %s — OK", result)


# 🧪 TRAP[TEST] · Regression · poll_health times out
# · Scenario: inspect returns unhealthy → continue polling → timeout → False
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
@patch("core.internal.deploy.deploy_engine.time.time")
@patch("core.internal.deploy.deploy_engine.time.sleep", return_value=None)
def test_poll_health_timeout(mock_sleep, mock_time, mock_run, caplog, tmp_project, engine):
    """_poll_health should return False on timeout when container is unhealthy."""
    caplog.set_level(logging.INFO)

    # Mock time to force immediate timeout: only 1 iteration before deadline
    mock_time.side_effect = [100, 105]  # start=100, deadline=105; second check at 105 → timeout
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="container123\n", stderr=""),  # compose ps -q
        MagicMock(returncode=0, stdout="running unhealthy\n", stderr=""),  # inspect (unhealthy)
    ]

    result = engine._poll_health(tmp_project, "app", timeout=5, interval=1)

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result is False
    logger.critical("[IMP:9][test] poll_health_timeout: %s — OK", result)


# 🧪 TRAP[TEST] · Regression · perform_rollback succeeds
# · Scenario: Re-tag + compose up --force-recreate → True
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
def test_perform_rollback_success(mock_run, caplog, tmp_project, engine):
    """_perform_rollback should succeed with valid previous image."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    prev_image = ImageInfo(id="sha256:prev123", tag="test-app:prev")

    result = engine._perform_rollback(tmp_project, "app", prev_image)

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result is True

    # Verify docker tag was called
    tag_calls = [c for c in mock_run.call_args_list if "tag" in str(c)]
    assert len(tag_calls) >= 1
    logger.critical("[IMP:9][test] rollback_success: %s — OK", result)


# 🧪 TRAP[TEST] · Regression · perform_rollback with no previous image
# · Scenario: previous_image is None → False
# · Last fail: N/A (new test)
def test_perform_rollback_no_image(caplog, engine):
    """_perform_rollback should return False with no previous image."""
    caplog.set_level(logging.INFO)

    result = engine._perform_rollback("/tmp", "app", None)

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result is False
    logger.critical("[IMP:9][test] rollback_no_image: %s — OK", result)


# 🧪 TRAP[TEST] · Regression · validate_project_name called via deploy()
# · Scenario: Invalid project name → DeployResult with error
# · Last fail: N/A (new test)
def test_deploy_calls_validate_project_name(caplog, engine):
    """Deploy should reject invalid project names."""
    caplog.set_level(logging.INFO)

    result = engine.deploy(
        project="../escape",
        ref="v1.0.0",
        service="app",
        project_dir="/tmp",
        max_wait=5,
    )

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is False
    assert "Invalid project name" in (result.error_message or "")
    logger.critical("[IMP:9][test] validate_name: success=%s — OK", result.success)


# endregion engine helper tests


# ═══════════════════════════════════════════════════════════════════
# region Tests: Data classes
# ═══════════════════════════════════════════════════════════════════


def test_deploy_result_dataclass():
    """DeployResult dataclass should create with default values."""
    r = DeployResult(success=True, project="test", ref="v1", service="app")
    assert r.rollback_performed is False
    assert r.first_deploy_failed is False
    assert r.previous_image is None
    logger.critical("[IMP:9][test] DeployResult dataclass — OK")


def test_remove_result_dataclass():
    """RemoveResult dataclass should create with default values."""
    r = RemoveResult(success=True, project="test")
    assert r.already_removed is False
    logger.critical("[IMP:9][test] RemoveResult dataclass — OK")


def test_status_result_dataclass():
    """StatusResult dataclass should create with default values."""
    r = StatusResult(project="test", node="node1", status="not_found")
    assert r.containers == []
    assert r.last_deploy is None
    logger.critical("[IMP:9][test] StatusResult dataclass — OK")


# endregion Data class tests


# ═══════════════════════════════════════════════════════════════════
# region Tests: subprocess-level integration tests
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _pull_image_with_retry succeeds on first attempt
# · Scenario: First pull attempt succeeds → True
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
@patch("core.internal.deploy.deploy_engine.time.sleep", return_value=None)
def test_pull_image_retry_first_attempt(mock_sleep, mock_run, caplog, engine):
    """_pull_image_with_retry should succeed on first attempt."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    result = engine._pull_image_with_retry("/tmp", "app", "v1.0.0", max_attempts=1)

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result is True
    logger.critical("[IMP:9][test] pull_retry_first: %s — OK", result)


# 🧪 TRAP[TEST] · Regression · _atomic_up succeeds
# · Scenario: docker compose up -d returns 0 → True
# · Last fail: N/A (new test)
@patch("core.internal.deploy.deploy_engine.subprocess.run")
def test_atomic_up_success(mock_run, caplog, engine):
    """_atomic_up should return True on success."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    result = engine._atomic_up("/tmp", "app", "v1.0.0")

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result is True
    logger.critical("[IMP:9][test] atomic_up: %s — OK", result)


# endregion subprocess tests
