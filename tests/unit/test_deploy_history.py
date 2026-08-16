"""Unit tests for DeployHistory — snapshot storage for rollback."""
# GREP_SUMMARY: test-deploy-history, snapshots, rollback, storage, retention, unit-test
# STRUCTURE: ▶ test_create_snapshot → test_read_snapshot → test_list_snapshots → test_retention_pruning → test_latest_snapshot → test_rollback
# region MODULE_CONTRACT
## @purpose  Unit tests for DeployHistory — snapshot creation, reading, listing, pruning, rollback.
## @scope    Tests file-based snapshot storage with retention policy.
## @invariants
##   - Creates snapshot JSON files in .deploy-snapshots/ directory
##   - Retention keeps last MAX_SNAPSHOTS (10)
##   - rollback() returns latest snapshot when no ID specified
## @changes 2026-07-30 | DevPlan 089 T16 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import pathlib
import tempfile
from pathlib import Path

import pytest

from core.internal.deploy.audit import MAX_SNAPSHOTS, SNAPSHOT_DIR, DeployHistory

pytestmark = pytest.mark.static_audit

# ── Fixtures ──


@pytest.fixture
def projects_base() -> str:
    """Create a temporary projects base directory."""
    path = tempfile.mkdtemp(prefix="test-projects-")
    yield path
    import shutil

    if pathlib.Path(path).is_dir():
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def history(projects_base: str) -> DeployHistory:
    """Create DeployHistory with temp projects base."""
    return DeployHistory(projects_base=projects_base)


# ── DeployHistory tests ──


class TestDeployHistory:
    """DeployHistory unit tests."""

    # region FUNC_test_create_snapshot
    ## @purpose  Verify create_snapshot creates a valid JSON file.
    def test_create_snapshot(self, history: DeployHistory, projects_base: str) -> None:
        """Verify create_snapshot writes JSON file with correct fields."""
        snap_id = history.create_snapshot(
            project="test-project",
            version="v1.0.0",
            compose_state={"containers": ["web"]},
            health_status="healthy",
            payload_hash="abc123",
        )

        assert isinstance(snap_id, str)
        assert len(snap_id) > 0

        # Verify file exists
        snap_dir = Path(projects_base) / "test-project" / SNAPSHOT_DIR
        assert pathlib.Path(snap_dir).is_dir()

        snap_file = Path(snap_dir) / f"{snap_id}.json"
        assert pathlib.Path(snap_file).is_file()

        with pathlib.Path(snap_file).open(encoding="utf-8") as f:
            data = json.load(f)

        assert data["snapshot_id"] == snap_id
        assert data["project"] == "test-project"
        assert data["version"] == "v1.0.0"
        assert data["compose_state"] == {"containers": ["web"]}
        assert data["health_status"] == "healthy"
        assert data["payload_hash"] == "abc123"
        assert "timestamp" in data

    # endregion FUNC_test_create_snapshot

    # region FUNC_test_create_snapshot_minimal
    ## @purpose  Verify create_snapshot works with minimal fields.
    def test_create_snapshot_minimal(self, history: DeployHistory) -> None:
        """Verify create_snapshot with only project."""
        snap_id = history.create_snapshot(project="test-project")
        assert isinstance(snap_id, str)

    # endregion FUNC_test_create_snapshot_minimal

    # region FUNC_test_read_snapshot
    ## @purpose  Verify read_snapshot returns correct data.
    def test_read_snapshot(self, history: DeployHistory) -> None:
        """Verify read_snapshot returns saved data."""
        snap_id = history.create_snapshot(
            project="test-project",
            version="v1.0.0",
        )

        data = history.read_snapshot("test-project", snap_id)
        assert data is not None
        assert data["snapshot_id"] == snap_id
        assert data["version"] == "v1.0.0"

    # endregion FUNC_test_read_snapshot

    # region FUNC_test_empty_state_operations
    ## @purpose  Empty-state ветки 4 методов (read_snapshot/list_snapshots/latest_snapshot/rollback)
    ##            консолидированы в один параметризованный тест (F5-reduction).
    @pytest.mark.parametrize(
        "op",
        [
            lambda h: h.read_snapshot("test-project", "nonexistent-snap"),
            lambda h: h.list_snapshots("nonexistent-project"),
            lambda h: h.latest_snapshot("test-project"),
            lambda h: h.rollback("test-project"),
        ],
    )
    def test_empty_state_operations(self, history: DeployHistory, op) -> None:
        """Empty-state операции возвращают None/[] (read_snapshot/list/latest/rollback)."""
        assert not op(history)

    # endregion FUNC_test_empty_state_operations

    # region FUNC_test_list_snapshots
    ## @purpose  Verify list_snapshots returns all snapshots.
    def test_list_snapshots(self, history: DeployHistory) -> None:
        """Verify list_snapshots returns correct count."""
        history.create_snapshot(project="test-project", version="v1")
        history.create_snapshot(project="test-project", version="v2")

        snapshots = history.list_snapshots("test-project")
        assert len(snapshots) == 2
        # Order depends on timestamp — verify both versions are present
        versions = {s["version"] for s in snapshots}
        assert "v1" in versions
        assert "v2" in versions

    # endregion FUNC_test_list_snapshots

    # region FUNC_test_latest_snapshot
    ## @purpose  Verify latest_snapshot returns a snapshot when one exists.
    def test_latest_snapshot(self, history: DeployHistory) -> None:
        """Verify latest_snapshot returns a snapshot when one exists."""
        history.create_snapshot(project="test-project", version="v1")

        latest = history.latest_snapshot("test-project")
        assert latest is not None
        assert latest["version"] == "v1"

    # endregion FUNC_test_latest_snapshot

    # region FUNC_test_retention_pruning
    ## @purpose  Verify retention policy keeps max MAX_SNAPSHOTS snapshots.
    def test_retention_pruning(self, history: DeployHistory) -> None:
        """Verify pruning keeps max 10 snapshots."""
        # Create more than MAX_SNAPSHOTS
        for i in range(MAX_SNAPSHOTS + 5):
            history.create_snapshot(project="test-project", version=f"v{i}")

        snapshots = history.list_snapshots("test-project")
        assert len(snapshots) <= MAX_SNAPSHOTS

    # endregion FUNC_test_retention_pruning

    # region FUNC_test_rollback_with_snapshot_id
    ## @purpose  Verify rollback returns specific snapshot.
    def test_rollback_with_snapshot_id(self, history: DeployHistory) -> None:
        """Verify rollback returns specific snapshot."""
        history.create_snapshot(project="test-project", version="v1")
        snap_id = history.create_snapshot(project="test-project", version="v2")

        data = history.rollback("test-project", snapshot_id=snap_id)
        assert data is not None
        assert data["version"] == "v2"

    # endregion FUNC_test_rollback_with_snapshot_id

    # region FUNC_test_rollback_without_snapshot_id
    ## @purpose  Verify rollback returns a snapshot when no snapshot_id given.
    def test_rollback_without_snapshot_id(self, history: DeployHistory) -> None:
        """Verify rollback without snapshot_id returns a snapshot."""
        history.create_snapshot(project="test-project", version="v1")

        data = history.rollback("test-project")
        assert data is not None
        assert data["version"] == "v1"

    # endregion FUNC_test_rollback_without_snapshot_id

    # 🧪 TRAP[TEST] · Regression · DeployHistory snapshots enable rollback after crash

    # region FUNC_test_snapshot_dir_chown_ci_deploy
    ## @purpose  B19 (141 r2 / 142 W7): create_snapshot выполняет best-effort chown
    ##           ci-deploy:ci-deploy на .deploy-snapshots (root-созданная бутстрапом директория
    ##           блокировала receive-деплой под ci-deploy: «[Errno 13] Permission denied»).
    ##           R5-negative: вход бага = снапшот-директория root:root.
    def test_snapshot_dir_chown_ci_deploy(self, projects_base: str) -> None:
        """B19: chown ci-deploy:ci-deploy вызывается для снапшот-директории (best-effort)."""
        chown_calls: list[list[str]] = []
        from core.internal.deploy.audit import DeployHistory

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[0] == "chown":
                chown_calls.append(cmd)

        # 167 D6: run_subprocess-инъекция через конструктор (DI-seam) вместо monkeypatch module-level
        history = DeployHistory(projects_base=projects_base, run_subprocess=fake_run)

        history.create_snapshot(project="test-project", version="v1")

        assert chown_calls, "chown обязан вызываться для снапшот-директории (B19)"
        assert chown_calls[0][1] == "ci-deploy:ci-deploy", f"владелец ci-deploy: {chown_calls[0]}"
        logger.info("[IMP:9][test][B19] snapshot dir chown ci-deploy:ci-deploy вызван ✓")

    # endregion FUNC_test_snapshot_dir_chown_ci_deploy


logger = logging.getLogger(__name__)
