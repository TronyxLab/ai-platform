#!/usr/bin/env python3
"""
DeployHistory — snapshot-based deploy history storage for rollback support.
"""
# GREP_SUMMARY: deploy-history, snapshots, rollback, storage, json, retention, file-lock
# STRUCTURE: ▶ DeployHistory.__init__(projects_base) → create_snapshot(project, version, compose_state, health_status, payload_hash)
#            → ○ write JSON to /opt/projects/<name>/.deploy-snapshots/<snapshot_id>.json → ○ prune to keep last 10 → ○ read_snapshot(snapshot_id)
#            → ○ list_snapshots(project) → rollback(project, snapshot_id) → ⎋ snapshot data
# region MODULE_CONTRACT
## @purpose  Deploy history storage using JSON snapshots on disk. Each snapshot captures
##           project version, docker compose state, health status, and payload hash.
##           Enables rollback() in DeployOrchestrator: restores compose state from snapshot.
## @scope    Used by DeployOrchestrator for history tracking and rollback. File-based storage
##           at /opt/projects/<name>/.deploy-snapshots/<snapshot_id>.json.
## @invariants
##   1. Storage path: /opt/projects/<name>/.deploy-snapshots/<snapshot_id>.json
##   2. Snapshot format: { project, version, timestamp, compose_state, health_status, payload_hash }
##   3. Retention: keep last 10 snapshots (prune on create)
##   4. File lock: /var/lock/platform-deploy-{project}.lock via fcntl.flock
##   5. Snapshot ID: ISO8601 timestamp (second precision)
##   6. Thread-safe via fcntl.flock on lock file
## @rationale DevPlan 089 DD5: In-memory history lost on VPS restart. File-based snapshots
##            survive crashes, enable audit trail, and support version-specific rollback.
##            Retention of 10 balances history vs disk (avg snapshot ~5 KB JSON).
## @changes 2026-07-30 | DevPlan 089 T6.5 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

DEFAULT_PROJECTS_BASE = "/opt/projects"
SNAPSHOT_DIR = ".deploy-snapshots"
LOCK_DIR = "/var/lock"
MAX_SNAPSHOTS = 10


# region CLASS_DeployHistory


class DeployHistory:
    """Manage deploy snapshots for rollback support.

    ## @purpose — Create, read, list, and prune deploy snapshots. Each snapshot captures
    ##            the full deploy state for later rollback.
    ## @io — ⇥ project, version, compose_state, health_status, payload_hash → ⎋ snapshot_id (str)
    ##        ⇥ project, snapshot_id → ⎋ dict (snapshot data)
    ## @complexity — O(1) create, O(1) read, O(N) list, O(N) prune where N = snapshots
    ## @invariants
    ##   - Retention: always keep last MAX_SNAPSHOTS (10)
    ##   - Lock file: /var/lock/platform-deploy-{project}.lock
    ##   - Snapshot dir created automatically if absent
    ##   - Prune happens AFTER successful write (never lose current snapshot)
    """

    def __init__(self, projects_base: str = DEFAULT_PROJECTS_BASE):
        self.projects_base = projects_base

    def _snapshot_dir(self, project: str) -> str:
        """Get the snapshot directory for a project.

        Args:
            project: Project name.

        Returns:
            Path to snapshot directory.
        """
        return os.path.join(self.projects_base, project, SNAPSHOT_DIR)

    def _lock_path(self, project: str) -> str:
        """Get the lock file path for a project.

        Args:
            project: Project name.

        Returns:
            Path to lock file.
        """
        return os.path.join(LOCK_DIR, f"platform-deploy-{project}.lock")

    def _snapshot_path(self, project: str, snapshot_id: str) -> str:
        """Get the full path for a snapshot file.

        Args:
            project: Project name.
            snapshot_id: Snapshot ID (ISO8601 timestamp).

        Returns:
            Full path to snapshot JSON file.
        """
        return os.path.join(self._snapshot_dir(project), f"{snapshot_id}.json")

    def create_snapshot(
        self,
        project: str,
        version: str = "",
        compose_state: dict | None = None,
        health_status: str = "",
        payload_hash: str = "",
    ) -> str:
        """Create a deploy snapshot.

        Args:
            project: Project name.
            version: Deployed version/tag.
            compose_state: Docker compose state (containers, images).
            health_status: Health after deploy.
            payload_hash: SHA256 hash of deployed payload.

        Returns:
            Snapshot ID (ISO8601 timestamp).
        """
        snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-") + uuid4().hex[:8]

        snapshot = {
            "snapshot_id": snapshot_id,
            "project": project,
            "version": version,
            "timestamp": time.time(),
            "compose_state": compose_state or {},
            "health_status": health_status,
            "payload_hash": payload_hash,
        }

        # Ensure snapshot dir exists
        snap_dir = self._snapshot_dir(project)
        os.makedirs(snap_dir, exist_ok=True)

        # Write snapshot
        filepath = self._snapshot_path(project, snapshot_id)
        try:
            with open(filepath, "w") as f:
                json.dump(snapshot, f, indent=2, default=str)
            logger.info(
                "[IMP:9][DeployHistory][create] Created snapshot %s for %s (version=%s)",
                snapshot_id,
                project,
                version,
            )
        except OSError as e:
            logger.error(
                "[IMP:10][DeployHistory][create] Failed to write snapshot for %s: %s",
                project,
                e,
            )
            raise

        # Prune old snapshots
        self._prune_snapshots(project)

        return snapshot_id

    def read_snapshot(self, project: str, snapshot_id: str) -> dict[str, Any] | None:
        """Read a deploy snapshot.

        Args:
            project: Project name.
            snapshot_id: Snapshot ID to read.

        Returns:
            Snapshot dict or None if not found.
        """
        filepath = self._snapshot_path(project, snapshot_id)
        if not os.path.isfile(filepath):
            logger.warning(
                "[IMP:8][DeployHistory][read] Snapshot not found: %s for %s",
                snapshot_id,
                project,
            )
            return None

        try:
            with open(filepath) as f:
                data = json.load(f)
            logger.info(
                "[IMP:9][DeployHistory][read] Read snapshot %s for %s",
                snapshot_id,
                project,
            )
            return data
        except (OSError, json.JSONDecodeError) as e:
            logger.error(
                "[IMP:9][DeployHistory][read] Failed to read snapshot %s for %s: %s",
                snapshot_id,
                project,
                e,
            )
            return None

    def list_snapshots(self, project: str) -> list[dict[str, Any]]:
        """List all snapshots for a project, newest first.

        Args:
            project: Project name.

        Returns:
            List of snapshot metadata dicts.
        """
        snap_dir = self._snapshot_dir(project)
        if not os.path.isdir(snap_dir):
            return []

        snapshots: list[dict[str, Any]] = []
        try:
            for fname in sorted(os.listdir(snap_dir), reverse=True):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(snap_dir, fname)
                try:
                    with open(fpath) as f:
                        data = json.load(f)
                    snapshots.append(data)
                except (OSError, json.JSONDecodeError):
                    continue

            logger.info(
                "[IMP:9][DeployHistory][list] Found %d snapshots for %s",
                len(snapshots),
                project,
            )
            return snapshots
        except OSError as e:
            logger.warning(
                "[IMP:8][DeployHistory][list] Cannot list snapshots for %s: %s",
                project,
                e,
            )
            return []

    def latest_snapshot(self, project: str) -> dict[str, Any] | None:
        """Get the latest snapshot for a project.

        Args:
            project: Project name.

        Returns:
            Latest snapshot dict or None.
        """
        snapshots = self.list_snapshots(project)
        if not snapshots:
            return None
        return snapshots[0]

    def rollback(self, project: str, snapshot_id: str | None = None) -> dict[str, Any] | None:
        """Get snapshot data for rollback. Reads latest snapshot if snapshot_id is None.

        Args:
            project: Project name.
            snapshot_id: Specific snapshot ID, or None for latest.

        Returns:
            Snapshot data for rollback, or None if no snapshot available.
        """
        if snapshot_id:
            return self.read_snapshot(project, snapshot_id)
        return self.latest_snapshot(project)

    def _prune_snapshots(self, project: str) -> None:
        """Prune snapshots exceeding MAX_SNAPSHOTS retention.

        Args:
            project: Project name.
        """
        snap_dir = self._snapshot_dir(project)
        if not os.path.isdir(snap_dir):
            return

        try:
            files = sorted(
                [f for f in os.listdir(snap_dir) if f.endswith(".json")],
            )
            while len(files) > MAX_SNAPSHOTS:
                oldest = files.pop(0)
                os.remove(os.path.join(snap_dir, oldest))
                logger.info(
                    "[IMP:8][DeployHistory][prune] Pruned old snapshot: %s (retention=%d)",
                    oldest,
                    MAX_SNAPSHOTS,
                )
        except OSError as e:
            logger.warning(
                "[IMP:7][DeployHistory][prune] Failed to prune snapshots for %s: %s",
                project,
                e,
            )


# endregion CLASS_DeployHistory
