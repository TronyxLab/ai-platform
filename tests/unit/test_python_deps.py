"""
# GREP_SUMMARY: test_python_deps, content-hash, pip-install, idempotent, requirements, apt-get
# STRUCTURE: ▶ tmp_path + monkeypatch + mock → ◇ hash match → skip ◇ hash changed → install ◇ missing dir → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for python_deps.py — idempotent pip3 + requirements install on VPS.
## @scope    Tests ensure_python_deps through content-hash guard, pip3 install, requirements install.
## @invariants
##   - All subprocess calls mocked (no real apt-get/pip3)
##   - requirements.txt created in tmp_path as needed
##   - Each test validates IMP:9 business logic log presence via ldd_trajectory decorator
## @rationale DevPlan 058: Python port of node-lifecycle.sh:_ensure_python_deps()
## @changes  2026-07-25 | DevPlan 058 — Created
# endregion MODULE_CONTRACT
"""

import hashlib
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import python_deps


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_content_hash_skip
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Content hash matches → skip pip install
# · Scenario: saved hash file exists and matches requirements.txt content →
#             _check_content_hash returns True → ensure_python_deps returns True immediately
# · Last fail: N/A (new test)
# · Remove if: content-hash guard logic changes
@ldd_trajectory
def test_python_deps_content_hash_skip(caplog, tmp_path, monkeypatch):
    """## @purpose When content hash matches saved hash, skip pip install (return True immediately)."""
    # ── Setup: create core_dir with requirements.txt ──
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    req_file = core_dir / "requirements.txt"
    req_file.write_text("requests==2.31.0\npydantic==2.0.0\n")

    # Compute expected sha256 hash of the requirements file
    h = hashlib.sha256()
    h.update(req_file.read_bytes())
    expected_hash = h.hexdigest()

    # ── Point HASH_FILE to tmp_path so _load_saved_hash reads our controlled file ──
    hash_dir = tmp_path / ".bootstrap"
    hash_dir.mkdir()
    monkeypatch.setattr(python_deps, "HASH_DIR", str(hash_dir))
    monkeypatch.setattr(python_deps, "HASH_FILE", str(hash_dir / "python-deps.hash"))

    # Write saved hash that MATCHES current requirements.txt content
    (hash_dir / "python-deps.hash").write_text(expected_hash + "\n")

    # ── Execute ──
    result = python_deps.ensure_python_deps(str(core_dir))

    # ── Assert ──
    assert result is True, "Hash match should skip install and return True"
    logger.critical("[IMP:9][test] Content hash skip — pip install skipped")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_content_hash_changed
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Content hash changed → pip install proceeds
# · Scenario: saved hash differs from requirements.txt content →
#             _install_pip3 + _install_requirements called → hash persisted
# · Last fail: N/A (new test)
# · Remove if: content-hash mismatch logic changes
@ldd_trajectory
def test_python_deps_content_hash_changed(caplog, tmp_path, monkeypatch):
    """## @purpose When content hash differs from saved hash, proceed with install."""
    # ── Setup: create core_dir with requirements.txt ──
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    req_file = core_dir / "requirements.txt"
    req_file.write_text("requests==2.31.0\npydantic==2.0.0\n")

    # ── Point HASH_FILE to tmp_path ──
    hash_dir = tmp_path / ".bootstrap"
    hash_dir.mkdir()
    monkeypatch.setattr(python_deps, "HASH_DIR", str(hash_dir))
    monkeypatch.setattr(python_deps, "HASH_FILE", str(hash_dir / "python-deps.hash"))

    # Write stale hash that DIFFERS from current requirements.txt content
    (hash_dir / "python-deps.hash").write_text("a" * 64 + "\n")

    # ── Mock subprocess-heavy functions ──
    with patch.object(python_deps, "_install_pip3", return_value=True) as mock_pip3, \
         patch.object(python_deps, "_install_requirements", return_value=True) as mock_reqs:
        result = python_deps.ensure_python_deps(str(core_dir))

    # ── Assert ──
    assert result is True, "Hash mismatch should proceed with install and return True"
    mock_pip3.assert_called_once()
    mock_reqs.assert_called_once_with(str(core_dir))
    logger.critical("[IMP:9][test] Content hash changed — pip install proceeded")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_core_dir_missing
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Core directory missing → returns False
# · Scenario: requirements.txt does not exist →
#             _install_requirements returns False → ensure_python_deps returns False
# · Last fail: N/A (new test)
# · Remove if: missing-requirements handling logic changes
@ldd_trajectory
def test_python_deps_core_dir_missing(caplog, tmp_path, monkeypatch):
    """## @purpose When core_dir doesn't exist or requirements.txt missing, returns False."""
    # ── Setup: non-existent core_dir ──
    core_dir = tmp_path / "nonexistent"

    # ── Point HASH_FILE to tmp_path ──
    hash_dir = tmp_path / ".bootstrap"
    hash_dir.mkdir()
    monkeypatch.setattr(python_deps, "HASH_DIR", str(hash_dir))
    monkeypatch.setattr(python_deps, "HASH_FILE", str(hash_dir / "python-deps.hash"))

    # ── Mock _install_pip3 to avoid real which/subprocess call ──
    with patch.object(python_deps, "_install_pip3", return_value=True) as mock_pip3:
        result = python_deps.ensure_python_deps(str(core_dir))

    # ── Assert ──
    assert result is False, "Missing core_dir should return False"
    mock_pip3.assert_called_once()
    logger.critical("[IMP:9][test] Core dir missing — ensure_python_deps returned False")


# endregion
