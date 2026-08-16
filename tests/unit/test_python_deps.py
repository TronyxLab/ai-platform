"""
# GREP_SUMMARY: test_python_deps, content-hash, pip-install, idempotent, requirements, apt-get, python3.14, deadsnakes, ensurepip, symlink, fallback, DI, FakeCommandRunner, hash_file, os_release_path
# STRUCTURE: ▶ tmp_path + FakeCommandRunner + hash_file/os_release_path params → ◇ hash+pyver match → skip ◇ hash changed → python3.14 install ◇ deadsnakes sequence ◇ non-24.04 fallback → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for python_deps.py — idempotent Python 3.14 (deadsnakes PPA) + requirements install on VPS.
## @scope    Tests ensure_python_deps through content-hash guard (hash + python version),
##           Python 3.14 interpreter install, requirements install, non-24.04 fallback.
## @invariants
##   - Все subprocess-вызовы идёт через FakeCommandRunner (runner=) — 0 реальных apt/pip
##   - hash_file/os_release_path — DI-параметры (tmp_path), НЕ monkeypatch HASH_FILE/HASH_DIR
##   - requirements.txt создаётся в tmp_path (Zero Hardcode Rule)
##   - Каждый тест валидирует IMP:9 бизнес-логику через ldd_trajectory декоратор
## @rationale DevPlan 058: Python port of node-lifecycle.sh:_ensure_python_deps();
##            extended 2026-08-01 with Python 3.14 via deadsnakes PPA (user decision).
## @changes  2026-07-25 | DevPlan 058 — Created
##           2026-08-01 | Adapted to Python 3.14 install path (deadsnakes PPA) + marker version
##           2026-08-13 | E1 (160) — DI-конвертация: subprocess.run/os.path.isfile → runner/facts,
##                      HASH_FILE/HASH_DIR/_detect_ubuntu_version → hash_file/os_release_path
##                      параметры (setattr 14 → 0, −100%)
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
import pytest
import python_deps

pytestmark = pytest.mark.static_audit


class FakeCommandRunner:
    """Scripted CommandRunner (DI-канон W4b): результат из последовательности или дефолт.

    ## @purpose — Замена monkeypatch subprocess.run в тестах python_deps: каждый вызов
    ##            записывается (calls), возвращается scripted CompletedProcess.
    ## @io — ⇥ results: list[CompletedProcess] (FIFO), default → ⎋ CompletedProcess
    ## @complexity — O(1) — pop из списка / дефолт
    """

    def __init__(self, results=None, default=None):
        self._results = list(results) if results else []
        self.default = default if default is not None else subprocess.CompletedProcess([], 0, "", "")
        self.calls: list[list[str]] = []

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls.append(list(cmd))
        if self._results:
            return self._results.pop(0)
        return self.default


def _proc(rc: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Build a CompletedProcess with given rc/stdout/stderr (fake-раннер результат)."""
    return subprocess.CompletedProcess([], returncode=rc, stdout=stdout, stderr=stderr)


def _version_proc(version: str) -> subprocess.CompletedProcess:
    """Version-probe результат: `/usr/local/bin/python3 --version` → "Python 3.14.5"."""
    return _proc(0, f"Python {version}\n", "")


def _ok_runner() -> FakeCommandRunner:
    """Fake-раннер успеха: все команды → rc=0 (эквивалент старого _ok_run mock)."""
    return FakeCommandRunner(default=_proc(0))


def _make_hash_dir(tmp_path) -> tuple[Path, Path]:
    """Create tmp .bootstrap dir, return (hash_dir, hash_file)."""
    hash_dir = tmp_path / ".bootstrap"
    hash_dir.mkdir()
    return hash_dir, hash_dir / "python-deps.hash"


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
def test_python_deps_content_hash_skip(caplog, tmp_path):
    """## @purpose When marker (hash + python version) matches, skip interpreter + pip install."""
    # ── Setup: create core_dir with requirements.txt ──
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    req_file = core_dir / "requirements.txt"
    req_file.write_text("requests==2.31.0\npydantic==2.0.0\n")

    h = hashlib.sha256()
    h.update(req_file.read_bytes())
    expected_hash = h.hexdigest()

    _, hash_file = _make_hash_dir(tmp_path)
    hash_file.write_text(f"{expected_hash}\n3.14.5\n")

    # Active interpreter 3.14.5 — через FakeCommandRunner (version probe → 3.14.5)
    runner = FakeCommandRunner(default=_version_proc("3.14.5"))

    # ── Execute ──
    with patch.object(python_deps, "_install_python314", return_value=True) as mock_inst:
        result = python_deps.ensure_python_deps(str(core_dir), runner=runner, hash_file=str(hash_file))

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
def test_python_deps_content_hash_changed(caplog, tmp_path):
    """## @purpose When marker hash or python version differs, proceed with 3.14 + requirements install."""
    # ── Setup: create core_dir with requirements.txt ──
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    req_file = core_dir / "requirements.txt"
    req_file.write_text("requests==2.31.0\npydantic==2.0.0\n")

    _, hash_file = _make_hash_dir(tmp_path)
    # Stale marker: hash differs AND saved python version (3.14.0) differs from current (3.14.1)
    hash_file.write_text("a" * 64 + "\n3.14.0\n")

    # Active interpreter 3.14.1 (version probe через runner)
    runner = FakeCommandRunner(default=_version_proc("3.14.1"))

    # ── Mock subprocess-heavy functions ──
    with (
        patch.object(python_deps, "_install_python314", return_value=True) as mock_inst,
        patch.object(python_deps, "_install_requirements", return_value=True) as mock_reqs,
    ):
        result = python_deps.ensure_python_deps(str(core_dir), runner=runner, hash_file=str(hash_file))

    # ── Assert ──
    assert result is True, "Marker mismatch should proceed with install and return True"
    mock_inst.assert_called_once()
    mock_reqs.assert_called_once()
    assert mock_reqs.call_args.args[0] == str(core_dir), "core_dir пробрасывается первым аргументом"

    # Marker persisted with hash + new python version (both components)
    marker_parts = hash_file.read_text().strip().split("\n")
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
def test_python_deps_core_dir_missing(caplog, tmp_path):
    """## @purpose When core_dir doesn't exist or requirements.txt missing, returns False."""
    # ── Setup: non-existent core_dir ──
    core_dir = tmp_path / "nonexistent"

    _, hash_file = _make_hash_dir(tmp_path)
    runner = FakeCommandRunner(default=_version_proc("3.14.5"))

    class _FakeFacts:
        """Fake EnvironmentFacts: requirements.txt отсутствует (path_isfile → False)."""

        def is_root(self) -> bool:
            return True

        def which(self, _binary) -> str | None:
            return None

        def path_isfile(self, _path) -> bool:
            return False

    # ── Mock _install_python314 to avoid real subprocess calls ──
    with patch.object(python_deps, "_install_python314", return_value=True) as mock_inst:
        result = python_deps.ensure_python_deps(
            str(core_dir), runner=runner, facts=_FakeFacts(), hash_file=str(hash_file)
        )

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
def test_python_deps_python314_idempotent_skip(caplog):
    """## @purpose Repeated bootstrap with 3.14 already installed must be a no-op."""
    runner = FakeCommandRunner(default=_version_proc("3.14.5"))

    result = python_deps._install_python314(runner=runner)

    # ── Assert ──
    assert result is True, "3.14 already active — install must be skipped"
    assert len(runner.calls) == 1, "Only the version probe should run — no apt/PPA commands"
    assert runner.calls[0] == ["/usr/local/bin/python3", "--version"]
    logger.critical("[IMP:9][test] Python 3.14 already installed — interpreter install skipped")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_python_deps_python314_deadsnakes_install
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Bare Ubuntu 24.04 → full deadsnakes install sequence
# · Scenario: no 3.14 active + Ubuntu 24.04 → software-properties-common →
#             add-apt-repository ppa:deadsnakes/ppa → apt-get update →
#             python3.14 + python3.14-venv → ensurepip --upgrade → ln symlink →
#             post-verify probe. DEBIAN_FRONTEND=noninteractive (инвариант _apt_env).
# · Last fail: N/A (new test)
# · Remove if: _install_python314 apt sequence changes
@ldd_trajectory
def test_python_deps_python314_deadsnakes_install(caplog, tmp_path):
    """## @purpose On bare Ubuntu 24.04, install 3.14 in the exact specified command sequence."""
    # /etc/os-release fixture в tmp_path (DI-параметр, без monkeypatch)
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Ubuntu"\nVERSION_ID="24.04"\n')

    # subprocess sequence:
    #  1. version probe (not installed yet) → rc=1
    #  2-7. six install commands → rc=0
    #  8. post-install version probe → 3.14.5
    side_effect = [
        _proc(1),
        _proc(0),
        _proc(0),
        _proc(0),
        _proc(0),
        _proc(0),
        _proc(0),
        _version_proc("3.14.5"),
    ]
    runner = FakeCommandRunner(results=side_effect)

    result = python_deps._install_python314(runner=runner, os_release_path=str(os_release))

    # ── Assert ──
    assert result is True, "Deadsnakes install sequence must succeed"
    assert len(runner.calls) == 8

    commands = runner.calls
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

    # DEBIAN_FRONTEND=noninteractive — инвариант env для apt-шагов (E1: _apt_env вынесен,
    # тестируется детерминированно без subprocess; runner-путь env не пробрасывает — W4d-канон)
    apt_env = python_deps._apt_env()
    assert apt_env.get("DEBIAN_FRONTEND") == "noninteractive", "DEBIAN_FRONTEND=noninteractive обязателен для apt"

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
def test_python_deps_marker_hash_version(caplog, tmp_path):
    """## @purpose Marker format changed compatibly: old-format and version drift force reinstall."""
    # ── Setup: core_dir with requirements.txt ──
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    req_file = core_dir / "requirements.txt"
    req_file.write_text("requests==2.31.0\npydantic==2.0.0\n")

    h = hashlib.sha256()
    h.update(req_file.read_bytes())
    expected_hash = h.hexdigest()

    _, hash_file = _make_hash_dir(tmp_path)
    runner = FakeCommandRunner(default=_version_proc("3.14.1"))

    # ── Scenario A: old-format marker (hash only) → mismatch ──
    hash_file.write_text(expected_hash + "\n")
    assert python_deps._check_content_hash(str(req_file), runner=runner, hash_file=str(hash_file)) is False, (
        "Old-format marker must force reinstall"
    )

    # ── Scenario B: hash matches but python version drifted → mismatch ──
    hash_file.write_text(f"{expected_hash}\n3.14.0\n")
    assert python_deps._check_content_hash(str(req_file), runner=runner, hash_file=str(hash_file)) is False, (
        "Version drift must force reinstall"
    )

    # ── Scenario C: hash + version both match → skip ──
    hash_file.write_text(f"{expected_hash}\n3.14.1\n")
    assert python_deps._check_content_hash(str(req_file), runner=runner, hash_file=str(hash_file)) is True, (
        "Matching hash + version must skip"
    )

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
def test_python_deps_fallback_non_ubuntu_2404(caplog, tmp_path):
    """## @purpose Non-24.04 Ubuntu falls back to system python3-pip with a WARN log."""
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Ubuntu"\nVERSION_ID="22.04"\n')

    # Version probe → 3.12.1 (не 3.14) → _python314_installed=False (через runner, без monkeypatch)
    runner = FakeCommandRunner(default=_version_proc("3.12.1"))

    with patch.object(python_deps, "_install_pip3", return_value=True) as mock_pip3:
        result = python_deps._install_python314(runner=runner, os_release_path=str(os_release))

    assert result is True, "Fallback to system python3-pip must succeed"
    mock_pip3.assert_called_once()
    assert any("!= 24.04" in r.message for r in caplog.records), "Expected WARN about non-24.04 Ubuntu"

    # ── Fail-soft: pip3 install failure → False (never raises) ──
    with patch.object(python_deps, "_install_pip3", return_value=False) as mock_pip3_fail:
        result_fail = python_deps._install_python314(runner=runner, os_release_path=str(os_release))
    assert result_fail is False, "Fail-soft contract: returns False, never raises"
    mock_pip3_fail.assert_called_once()

    logger.critical("[IMP:9][test] Non-24.04 fallback to system python3-pip (WARN + fail-soft)")


# endregion
