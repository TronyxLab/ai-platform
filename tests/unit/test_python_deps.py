"""
# GREP_SUMMARY: test_python_deps, content-hash, pip-install, idempotent, requirements, apt-get, python3.14, deadsnakes, ensurepip, symlink, fallback
# STRUCTURE: ▶ tmp_path + monkeypatch + mock → ◇ hash+pyver match → skip ◇ hash changed → python3.14 install ◇ deadsnakes sequence ◇ non-24.04 fallback → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for python_deps.py — idempotent Python 3.14 (deadsnakes PPA) + requirements install on VPS.
## @scope    Tests ensure_python_deps through content-hash guard (hash + python version),
##           Python 3.14 interpreter install, requirements install, non-24.04 fallback.
## @invariants
##   - All subprocess calls mocked (no real apt-get/add-apt-repository/pip)
##   - requirements.txt created in tmp_path as needed
##   - Each test validates IMP:9 business logic log presence via ldd_trajectory decorator
## @rationale DevPlan 058: Python port of node-lifecycle.sh:_ensure_python_deps();
##            extended 2026-08-01 with Python 3.14 via deadsnakes PPA (user decision).
## @changes  2026-07-25 | DevPlan 058 — Created
##           2026-08-01 | Adapted to Python 3.14 install path (deadsnakes PPA) + marker version
# endregion MODULE_CONTRACT
"""

import hashlib
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import python_deps

# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_content_hash_skip
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Content hash + python version match → skip install
# · Scenario: saved marker (hash + version) matches requirements.txt content and active
#             interpreter version → _check_content_hash returns True → ensure_python_deps
#             returns True immediately, _install_python314 NOT called
# · Last fail: N/A (new test)
# · Remove if: content-hash guard logic changes
@ldd_trajectory
def test_python_deps_content_hash_skip(caplog, tmp_path, monkeypatch):
    """## @purpose When marker (hash + python version) matches, skip interpreter + pip install."""
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

    # Active interpreter is 3.14 — same version the marker was saved with
    monkeypatch.setattr(python_deps, "_compute_python_version", lambda: "3.14.5")

    # Write marker that MATCHES current requirements.txt content AND python version
    (hash_dir / "python-deps.hash").write_text(f"{expected_hash}\n3.14.5\n")

    # ── Execute ──
    with patch.object(python_deps, "_install_python314", return_value=True) as mock_inst:
        result = python_deps.ensure_python_deps(str(core_dir))

    # ── Assert ──
    assert result is True, "Hash + version match should skip install and return True"
    mock_inst.assert_not_called()
    logger.critical("[IMP:9][test] Content hash + python version match — install skipped")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_content_hash_changed
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Content hash / python version changed → install proceeds
# · Scenario: saved marker differs (stale hash or old python version) →
#             _install_python314 + _install_requirements called → marker persisted
#             with hash + NEW version
# · Last fail: N/A (new test)
# · Remove if: content-hash mismatch logic changes
@ldd_trajectory
def test_python_deps_content_hash_changed(caplog, tmp_path, monkeypatch):
    """## @purpose When marker hash or python version differs, proceed with 3.14 + requirements install."""
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

    # Stale marker: hash differs AND saved python version (3.14.0) differs from current (3.14.1)
    (hash_dir / "python-deps.hash").write_text("a" * 64 + "\n3.14.0\n")
    monkeypatch.setattr(python_deps, "_compute_python_version", lambda: "3.14.1")

    # ── Mock subprocess-heavy functions ──
    with (
        patch.object(python_deps, "_install_python314", return_value=True) as mock_inst,
        patch.object(python_deps, "_install_requirements", return_value=True) as mock_reqs,
    ):
        result = python_deps.ensure_python_deps(str(core_dir))

    # ── Assert ──
    assert result is True, "Marker mismatch should proceed with install and return True"
    mock_inst.assert_called_once()
    mock_reqs.assert_called_once_with(str(core_dir))

    # Marker persisted with hash + new python version (both components)
    marker_parts = (hash_dir / "python-deps.hash").read_text().strip().split("\n")
    assert len(marker_parts) == 2, "Marker must contain hash AND python version"
    assert marker_parts[1] == "3.14.1", "Marker must record the new interpreter version"
    logger.critical("[IMP:9][test] Marker mismatch — Python 3.14 + requirements installed")


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

    # ── Mock _install_python314 to avoid real subprocess calls ──
    with patch.object(python_deps, "_install_python314", return_value=True) as mock_inst:
        result = python_deps.ensure_python_deps(str(core_dir))

    # ── Assert ──
    assert result is False, "Missing core_dir should return False"
    mock_inst.assert_called_once()
    logger.critical("[IMP:9][test] Core dir missing — ensure_python_deps returned False")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_python314_idempotent_skip
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Python 3.14 already active → _install_python314 no-op
# · Scenario: /usr/local/bin/python3 --version reports 3.14.x →
#             _install_python314 returns True after a single version probe,
#             NO apt-get/add-apt-repository/ensurepip/ln commands run
# · Last fail: N/A (new test)
# · Remove if: _install_python314 idempotent check logic changes
@ldd_trajectory
def test_python_deps_python314_idempotent_skip(caplog, tmp_path):
    """## @purpose Repeated bootstrap with 3.14 already installed must be a no-op."""
    version_probe = subprocess.CompletedProcess(["/usr/local/bin/python3", "--version"], 0, "Python 3.14.5\n", "")

    with patch.object(python_deps.subprocess, "run", return_value=version_probe) as mock_run:
        result = python_deps._install_python314()

    # ── Assert ──
    assert result is True, "3.14 already active — install must be skipped"
    assert mock_run.call_count == 1, "Only the version probe should run — no apt/PPA commands"
    cmd = mock_run.call_args_list[0].args[0]
    assert cmd == ["/usr/local/bin/python3", "--version"]
    logger.critical("[IMP:9][test] Python 3.14 already installed — interpreter install skipped")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_python314_deadsnakes_install
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Bare Ubuntu 24.04 → full deadsnakes install sequence
# · Scenario: no 3.14 active + Ubuntu 24.04 → software-properties-common →
#             add-apt-repository ppa:deadsnakes/ppa → apt-get update →
#             python3.14 + python3.14-venv → ensurepip --upgrade → ln symlink →
#             post-verify probe. DEBIAN_FRONTEND=noninteractive on every apt step.
# · Last fail: N/A (new test)
# · Remove if: _install_python314 apt sequence changes
@ldd_trajectory
def test_python_deps_python314_deadsnakes_install(caplog, tmp_path, monkeypatch):
    """## @purpose On bare Ubuntu 24.04, install 3.14 in the exact specified command sequence."""
    # Force the Ubuntu 24.04 path (test machine has no /etc/os-release)
    monkeypatch.setattr(python_deps, "_detect_ubuntu_version", lambda: "24.04")

    def _result(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], returncode, stdout, "")

    # subprocess.run sequence:
    #  1. version probe (not installed yet) → rc=1
    #  2-7. six install commands → rc=0
    #  8. post-install version probe → 3.14.5
    side_effect = [
        _result(1),
        _result(0),
        _result(0),
        _result(0),
        _result(0),
        _result(0),
        _result(0),
        _result(0, "Python 3.14.5\n"),
    ]

    with patch.object(python_deps.subprocess, "run", side_effect=side_effect) as mock_run:
        result = python_deps._install_python314()

    # ── Assert ──
    assert result is True, "Deadsnakes install sequence must succeed"
    assert mock_run.call_count == 8

    commands = [call.args[0] for call in mock_run.call_args_list]
    expected_commands = [
        ["/usr/local/bin/python3", "--version"],
        ["apt-get", "install", "-y", "-qq", "software-properties-common"],
        ["add-apt-repository", "-y", "ppa:deadsnakes/ppa"],
        ["apt-get", "update", "-qq"],
        ["apt-get", "install", "-y", "-qq", "python3.14", "python3.14-venv"],
        ["/usr/bin/python3.14", "-m", "ensurepip", "--upgrade"],
        ["ln", "-sfn", "/usr/bin/python3.14", "/usr/local/bin/python3"],
        ["/usr/local/bin/python3", "--version"],
    ]
    assert commands == expected_commands, f"Unexpected command sequence:\n{commands}"

    # DEBIAN_FRONTEND=noninteractive on every apt/PPA/ensurepip/symlink step (calls 2-7)
    for call in mock_run.call_args_list[1:7]:
        env = call.kwargs.get("env") or {}
        assert env.get("DEBIAN_FRONTEND") == "noninteractive", f"DEBIAN_FRONTEND missing for {call.args[0]}"

    logger.critical("[IMP:9][test] Python 3.14 installed via deadsnakes PPA (6-command sequence)")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_marker_hash_version
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Marker must carry hash AND python version
# · Scenario A: old-format marker (hash only, pre-3.14 era) → mismatch (reinstall required)
# · Scenario B: marker hash matches but saved version (3.14.0) != current (3.14.1) → mismatch
# · Scenario C: hash + version both match → skip (True)
# · Last fail: N/A (new test)
# · Remove if: marker format / _check_content_hash comparison logic changes
@ldd_trajectory
def test_python_deps_marker_hash_version(caplog, tmp_path, monkeypatch):
    """## @purpose Marker format changed compatibly: old-format and version drift force reinstall."""
    # ── Setup: core_dir with requirements.txt ──
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    req_file = core_dir / "requirements.txt"
    req_file.write_text("requests==2.31.0\npydantic==2.0.0\n")

    h = hashlib.sha256()
    h.update(req_file.read_bytes())
    expected_hash = h.hexdigest()

    hash_dir = tmp_path / ".bootstrap"
    hash_dir.mkdir()
    monkeypatch.setattr(python_deps, "HASH_DIR", str(hash_dir))
    monkeypatch.setattr(python_deps, "HASH_FILE", str(hash_dir / "python-deps.hash"))
    # Active interpreter is 3.14.1 (no real subprocess in tests)
    monkeypatch.setattr(python_deps, "_compute_python_version", lambda: "3.14.1")

    # ── Scenario A: old-format marker (hash only) → mismatch ──
    (hash_dir / "python-deps.hash").write_text(expected_hash + "\n")
    assert python_deps._check_content_hash(str(req_file)) is False, "Old-format marker must force reinstall"

    # ── Scenario B: hash matches but python version drifted → mismatch ──
    (hash_dir / "python-deps.hash").write_text(f"{expected_hash}\n3.14.0\n")
    assert python_deps._check_content_hash(str(req_file)) is False, "Version drift must force reinstall"

    # ── Scenario C: hash + version both match → skip ──
    (hash_dir / "python-deps.hash").write_text(f"{expected_hash}\n3.14.1\n")
    assert python_deps._check_content_hash(str(req_file)) is True, "Matching hash + version must skip"

    logger.critical("[IMP:9][test] Marker carries hash + python version — old-format and drift detected")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_fallback_non_ubuntu_2404
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Ubuntu != 24.04 → WARN + fallback to system python3-pip
# · Scenario: no 3.14 active + Ubuntu 22.04 → _install_python314 logs WARN and
#             delegates to _install_pip3() (old system-python path). Fail-soft:
#             _install_pip3 failure propagates as False (module never raises).
# · Last fail: N/A (new test)
# · Remove if: _install_python314 fallback branch logic changes
@ldd_trajectory
def test_python_deps_fallback_non_ubuntu_2404(caplog, tmp_path, monkeypatch):
    """## @purpose Non-24.04 Ubuntu falls back to system python3-pip with a WARN log."""
    monkeypatch.setattr(python_deps, "_python314_installed", lambda: False)
    monkeypatch.setattr(python_deps, "_detect_ubuntu_version", lambda: "22.04")

    with patch.object(python_deps, "_install_pip3", return_value=True) as mock_pip3:
        result = python_deps._install_python314()

    assert result is True, "Fallback to system python3-pip must succeed"
    mock_pip3.assert_called_once()
    assert any("!= 24.04" in r.message for r in caplog.records), "Expected WARN about non-24.04 Ubuntu"

    # ── Fail-soft: pip3 install failure → False (never raises) ──
    with patch.object(python_deps, "_install_pip3", return_value=False) as mock_pip3_fail:
        result_fail = python_deps._install_python314()
    assert result_fail is False, "Fail-soft contract: returns False, never raises"
    mock_pip3_fail.assert_called_once()

    logger.critical("[IMP:9][test] Non-24.04 fallback to system python3-pip (WARN + fail-soft)")


# endregion
