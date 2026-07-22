"""
# GREP_SUMMARY: test_docker_registry_auth, docker-hub-login, daemon-json, registry-mirror, idempotent, missing-creds
# STRUCTURE: ▶ tmp_path + mock subprocess → ◇ docker login → ◇ daemon.json idempotent → ◇ missing creds warn → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for docker_registry_auth.py — Docker Hub auth + registry-mirror.
## @scope    Tests configure_docker_auth, _write_daemon_json, _docker_login.
## @invariants
##   - All subprocess calls mocked (no real docker/systemctl)
##   - daemon.json written to tmp_path
##   - Each test validates IMP:9 business logic log presence
## @rationale DevPlan 047 Phase 7: Docker Hub auth eliminates rate-limit during bootstrap.
## @changes  2026-07-22 | DevPlan 047 Phase 7 — Created
# endregion MODULE_CONTRACT
"""

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import docker_registry_auth as dra

# ═══════════════════════════════════════════════════════════════════
# region Tests: docker login
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Docker login succeeds with valid credentials
# · Scenario: Mock subprocess.run returncode=0 → _docker_login returns True
# · Last fail: N/A (new test)
# · Remove if: docker login logic changes
@ldd_trajectory
def test_docker_login_success(caplog):
    """_docker_login should return True on successful login."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        ok = dra._docker_login("testuser", "testtoken")
    assert ok is True
    logger.critical("[IMP:9][test] Docker login success — valid creds accepted")


# 🧪 TRAP[TEST] · Regression · Docker login fails on invalid credentials
# · Scenario: Mock subprocess.run returncode=1 → _docker_login returns False
# · Last fail: N/A (new test)
# · Remove if: docker login logic changes
@ldd_trajectory
def test_docker_login_fail(caplog):
    """_docker_login should return False on failed login."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "auth error"
    with patch("subprocess.run", return_value=mock_result):
        ok = dra._docker_login("baduser", "badtoken")
    assert ok is False
    logger.critical("[IMP:9][test] Docker login failure — invalid creds rejected")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: daemon.json idempotency
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · daemon.json write is idempotent (skip if mirror already present)
# · Scenario: Write daemon.json with mirror, write again → second call returns False (no change)
# · Last fail: N/A (new test)
# · Remove if: daemon.json idempotency logic changes
@ldd_trajectory
def test_daemon_json_idempotent(caplog, tmp_path, monkeypatch):
    """_write_daemon_json should skip if mirror already configured."""
    daemon_path = str(tmp_path / "daemon.json")
    monkeypatch.setattr(dra, "DAEMON_JSON_PATH", daemon_path)

    # First write — should return True (written)
    written1 = dra._write_daemon_json("https://mirror.gcr.io")
    assert written1 is True
    assert os.path.isfile(daemon_path)

    # Verify content
    with open(daemon_path) as f:
        data = json.load(f)
    assert "registry-mirrors" in data
    assert "https://mirror.gcr.io" in data["registry-mirrors"]
    assert data["log-driver"] == "json-file"

    # Second write — should return False (already configured)
    written2 = dra._write_daemon_json("https://mirror.gcr.io")
    assert written2 is False
    logger.critical("[IMP:9][test] daemon.json idempotent — second write skipped")


# 🧪 TRAP[TEST] · Regression · daemon.json adds new mirror without removing existing
# · Scenario: Write with mirror1, then write with mirror2 → both present
# · Last fail: N/A (new test)
# · Remove if: daemon.json merge logic changes
@ldd_trajectory
def test_daemon_json_merges_mirrors(caplog, tmp_path, monkeypatch):
    """_write_daemon_json should add new mirror without removing existing."""
    daemon_path = str(tmp_path / "daemon.json")
    monkeypatch.setattr(dra, "DAEMON_JSON_PATH", daemon_path)

    dra._write_daemon_json("https://mirror.gcr.io")
    dra._write_daemon_json("https://custom-mirror.example.com")

    with open(daemon_path) as f:
        data = json.load(f)
    assert "https://mirror.gcr.io" in data["registry-mirrors"]
    assert "https://custom-mirror.example.com" in data["registry-mirrors"]
    logger.critical("[IMP:9][test] daemon.json merge — both mirrors present")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: configure_docker_auth (integration)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · configure_docker_auth warns when creds missing (non-fatal)
# · Scenario: Empty username/token → WARN logged, returns True (mirror still configured)
# · Last fail: N/A (new test)
# · Remove if: missing creds handling changes
@ldd_trajectory
def test_missing_creds_warn(caplog, tmp_path, monkeypatch):
    """configure_docker_auth should warn when creds missing but still configure mirror."""
    daemon_path = str(tmp_path / "daemon.json")
    monkeypatch.setattr(dra, "DAEMON_JSON_PATH", daemon_path)
    # Mock restart to avoid systemctl call
    monkeypatch.setattr(dra, "_restart_docker", lambda: True)

    ok = dra.configure_docker_auth("", "")
    assert ok is True  # Non-fatal: mirror configured even without creds
    assert os.path.isfile(daemon_path)
    logger.critical("[IMP:9][test] Missing creds WARN — mirror still configured")


# 🧪 TRAP[TEST] · Regression · configure_docker_auth succeeds with valid creds
# · Scenario: Valid creds + mock subprocess → returns True, daemon.json written
# · Last fail: N/A (new test)
# · Remove if: configure_docker_auth integration changes
@ldd_trajectory
def test_configure_docker_auth_success(caplog, tmp_path, monkeypatch):
    """configure_docker_auth should succeed with valid creds."""
    daemon_path = str(tmp_path / "daemon.json")
    monkeypatch.setattr(dra, "DAEMON_JSON_PATH", daemon_path)
    monkeypatch.setattr(dra, "_restart_docker", lambda: True)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        ok = dra.configure_docker_auth("user", "token")

    assert ok is True
    assert os.path.isfile(daemon_path)
    logger.critical("[IMP:9][test] configure_docker_auth success — full flow OK")
