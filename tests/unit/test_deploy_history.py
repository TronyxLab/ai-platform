#!/usr/bin/env python3
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
import os
import tempfile

import pytest

from core.internal.deploy.deploy_history import MAX_SNAPSHOTS, SNAPSHOT_DIR, DeployHistory

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
        snap_dir = os.path.join(projects_base, "test-project", SNAPSHOT_DIR)
        assert os.path.isdir(snap_dir)

        snap_file = os.path.join(snap_dir, f"{snap_id}.json")
        assert os.path.isfile(snap_file)

        with open(snap_file) as f:
            data = json.load(f)

        assert data["snapshot_id"] == snap_id
        assert data["project"] == "test-project"
        assert data["version"] == "v1.0.0"
        assert data["compose_state"] == {"containers": ["web"]}
        assert data["health_status"] == "healthy"
        assert data["payload_hash"] == "abc123"
        assert "timestamp" in data

    # endregion

    # region FUNC_test_create_snapshot_minimal
    ## @purpose  Verify create_snapshot works with minimal fields.
    def test_create_snapshot_minimal(self, history: DeployHistory) -> None:
        """Verify create_snapshot with only project."""
        snap_id = history.create_snapshot(project="test-project")
        assert isinstance(snap_id, str)

    # endregion

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

    # endregion

    # region FUNC_test_read_snapshot_not_found
    ## @purpose  Verify read_snapshot returns None for missing snapshot.
    def test_read_snapshot_not_found(self, history: DeployHistory) -> None:
        """Verify read_snapshot returns None for non-existent ID."""
        data = history.read_snapshot("test-project", "nonexistent-snap")
        assert data is None

    # endregion

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

    # endregion

    # region FUNC_test_list_snapshots_empty
    ## @purpose  Verify list_snapshots returns empty list for non-existent project.
    def test_list_snapshots_empty(self, history: DeployHistory) -> None:
        """Verify list_snapshots returns [] when no snapshots."""
        snapshots = history.list_snapshots("nonexistent-project")
        assert snapshots == []

    # endregion

    # region FUNC_test_latest_snapshot
    ## @purpose  Verify latest_snapshot returns a snapshot.
    def test_latest_snapshot(self, history: DeployHistory) -> None:
        """Verify latest_snapshot returns a snapshot when one exists."""
        history.create_snapshot(project="test-project", version="v1")

        latest = history.latest_snapshot("test-project")
        assert latest is not None
        assert latest["version"] == "v1"

    # endregion

    # region FUNC_test_latest_snapshot_empty
    ## @purpose  Verify latest_snapshot returns None when no snapshots exist.
    def test_latest_snapshot_empty(self, history: DeployHistory) -> None:
        """Verify latest_snapshot returns None."""
        latest = history.latest_snapshot("test-project")
        assert latest is None

    # endregion

    # region FUNC_test_retention_pruning
    ## @purpose  Verify retention policy keeps max MAX_SNAPSHOTS snapshots.
    def test_retention_pruning(self, history: DeployHistory) -> None:
        """Verify pruning keeps max 10 snapshots."""
        # Create more than MAX_SNAPSHOTS
        for i in range(MAX_SNAPSHOTS + 5):
            history.create_snapshot(project="test-project", version=f"v{i}")

        snapshots = history.list_snapshots("test-project")
        assert len(snapshots) <= MAX_SNAPSHOTS

    # endregion

    # region FUNC_test_rollback_with_snapshot_id
    ## @purpose  Verify rollback returns specific snapshot.
    def test_rollback_with_snapshot_id(self, history: DeployHistory) -> None:
        """Verify rollback returns specific snapshot."""
        history.create_snapshot(project="test-project", version="v1")
        snap_id = history.create_snapshot(project="test-project", version="v2")

        data = history.rollback("test-project", snapshot_id=snap_id)
        assert data is not None
        assert data["version"] == "v2"

    # endregion

    # region FUNC_test_rollback_without_snapshot_id
    ## @purpose  Verify rollback returns a snapshot when no snapshot_id given.
    def test_rollback_without_snapshot_id(self, history: DeployHistory) -> None:
        """Verify rollback without snapshot_id returns a snapshot."""
        history.create_snapshot(project="test-project", version="v1")

        data = history.rollback("test-project")
        assert data is not None
        assert data["version"] == "v1"

    # endregion

    # region FUNC_test_rollback_empty
    ## @purpose  Verify rollback returns None when no snapshots exist.
    def test_rollback_empty(self, history: DeployHistory) -> None:
        """Verify rollback returns None when no snapshots."""
        data = history.rollback("test-project")
        assert data is None

    # endregion

    # 🧪 TRAP[TEST] · Regression · DeployHistory snapshots enable rollback after crash
