#!/usr/bin/env python3
"""Unit tests for AuditLogger."""
# GREP_SUMMARY: test-audit-logger, audit, json-lines, deploy-audit, unit-test
# STRUCTURE: ▶ test_audit_logger_init → test_log_entry → test_log_with_channel → test_log_many → test_log_permissions
# region MODULE_CONTRACT
## @purpose  Unit tests for AuditLogger — unified deploy audit logger.
## @scope    Tests log entry format, file output, permissions, multi-project logging.
## @invariants
##   - AuditLogger creates JSON-lines entries
##   - Non-fatal on write failure
##   - Creates log directory if absent
## @changes 2026-07-30 | DevPlan 089 T16 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import tempfile

import pytest

from core.internal.deploy.audit_logger import AuditLogger

# ── Fixtures ──


@pytest.fixture
def temp_log_file() -> str:
    """Create a temporary log file path."""
    fd, path = tempfile.mkstemp(suffix=".audit.log", prefix="test-audit-")
    os.close(fd)
    os.unlink(path)  # Remove so logger creates it fresh
    yield path
    if os.path.isfile(path):
        os.unlink(path)


@pytest.fixture
def temp_log_dir() -> str:
    """Create a temporary log directory."""
    path = tempfile.mkdtemp(prefix="test-audit-dir-")
    yield path
    import shutil

    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


# ── AuditLogger tests ──


class TestAuditLogger:
    """AuditLogger unit tests."""

    # region FUNC_test_init
    ## @purpose  Verify AuditLogger initializes with default log file.
    def test_init(self) -> None:
        """Verify AuditLogger default log path."""
        logger = AuditLogger()
        assert logger.log_file == "/var/log/platform/audit.log"

    # endregion

    # region FUNC_test_init_custom_path
    ## @purpose  Verify AuditLogger accepts custom log file path.
    def test_init_custom_path(self, temp_log_file: str) -> None:
        """Verify custom log path is used."""
        logger = AuditLogger(log_file=temp_log_file)
        assert logger.log_file == temp_log_file

    # endregion

    # region FUNC_test_log_entry_creates_file
    ## @purpose  Verify log() creates the log file with a JSON-lines entry.
    def test_log_entry_creates_file(self, temp_log_file: str) -> None:
        """Verify log() writes to file."""
        logger = AuditLogger(log_file=temp_log_file)
        logger.log(
            operation="deploy",
            project="test-project",
            channel="scp",
            result="DEPLOYED",
            duration_s=10.5,
            snapshot_id="snap-123",
        )

        assert os.path.isfile(temp_log_file)
        with open(temp_log_file) as f:
            lines = f.readlines()
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["operation"] == "deploy"
        assert entry["project"] == "test-project"
        assert entry["channel"] == "scp"
        assert entry["result"] == "DEPLOYED"
        assert entry["duration_s"] == 10.5
        assert entry["snapshot_id"] == "snap-123"
        assert "ts" in entry

    # endregion

    # region FUNC_test_log_entry_with_extra
    ## @purpose  Verify log() accepts extra kwargs.
    def test_log_entry_with_extra(self, temp_log_file: str) -> None:
        """Verify log() with extra fields."""
        logger = AuditLogger(log_file=temp_log_file)
        logger.log(
            operation="rollback",
            project="test-project",
            result="ROLLED_BACK",
            extra_field="custom_value",
            exit_code=1,
        )

        with open(temp_log_file) as f:
            entry = json.loads(f.readline())

        assert entry["operation"] == "rollback"
        assert entry["extra_field"] == "custom_value"
        assert entry["exit_code"] == 1  # int stays as int

    # endregion

    # region FUNC_test_log_many
    ## @purpose  Verify log_many() writes multi-project audit entry.
    def test_log_many(self, temp_log_file: str) -> None:
        """Verify log_many() writes correct multi-project entry."""
        logger = AuditLogger(log_file=temp_log_file)
        logger.log_many(
            operation="deploy_many",
            projects=["proj1", "proj2"],
            channel="scp",
            results=["DEPLOYED", "FAILED"],
            overall_result="PARTIAL",
        )

        with open(temp_log_file) as f:
            entry = json.loads(f.readline())

        assert entry["operation"] == "deploy_many"
        assert entry["projects"] == ["proj1", "proj2"]
        assert entry["project_count"] == 2
        assert entry["per_project_results"] == ["DEPLOYED", "FAILED"]
        assert entry["result"] == "PARTIAL"

    # endregion

    # region FUNC_test_log_creates_directory
    ## @purpose  Verify log() creates directory if absent.
    def test_log_creates_directory(self, temp_log_dir: str) -> None:
        """Verify log() creates directory automatically."""
        nested = os.path.join(temp_log_dir, "subdir", "audit.log")
        logger = AuditLogger(log_file=nested)
        logger.log(operation="deploy", project="test-project", result="DEPLOYED")

        assert os.path.isfile(nested)

    # endregion

    # region FUNC_test_log_non_fatal_on_write_error
    ## @purpose  Verify log() is non-fatal on write error (no exception).
    def test_log_non_fatal_on_write_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify log() does not raise on write error."""
        caplog.set_level(logging.WARNING)

        # Use a path that can't be written (root-owned dir)
        logger = AuditLogger(log_file="/proc/1/audit.log")
        logger.log(operation="deploy", project="test-project", result="DEPLOYED")
        # Should not raise — OSError caught and logged

    # endregion

    # region FUNC_test_multiple_entries
    ## @purpose  Verify multiple log entries are appended.
    def test_multiple_entries(self, temp_log_file: str) -> None:
        """Verify append behavior."""
        logger = AuditLogger(log_file=temp_log_file)
        logger.log(operation="deploy", project="p1", result="DEPLOYED")
        logger.log(operation="deploy", project="p2", result="FAILED")

        with open(temp_log_file) as f:
            lines = f.readlines()
        assert len(lines) == 2

    # endregion

    # 🧪 TRAP[TEST] · Regression · AuditLogger writes valid JSON-lines for observability pipelines
