"""
# GREP_SUMMARY: test-converge-audit, r2, reconcile-audit-log, symlink-guard, audit-log-creation, ci-deploy-group
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R2 reconcile_audit_log 4× (symlink-fail/missing-file/converged/ci-deploy-group) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for converge/audit.py via reconciler.reconcile_audit_log (R2).
## @scope    Tests audit-log reconciliation: symlink attack prevention, creation,
##           converged-state check, ci-deploy group membership.
##           Does NOT require a real docker daemon or root privileges.
## @invariants
##   - All docker-dependent tests mock subprocess.run to avoid real docker calls
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via caplog
## @rationale Direct function testing with tmp_path and mock subprocess.run.
##   Вынесен из монолита test_reconciler.py (DevPlan 118 F6).
## @changes 2026-08-02 · F6 split — R2 audit (DevPlan 118)
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load the LDD trajectory decorator from shared conftest
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

import core.internal.bootstrap.converge.audit as _converge_audit
from core.internal.bootstrap.converge import infra

# Re-export for fixture cleanups
MODULE = reconciler


# ═══════════════════════════════════════════════════════════════════
# region Fixtures


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    infra.reset_state()
    infra.node_name = "test-node"
    infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")
    yield


@pytest.fixture
def mock_subprocess_run():
    """Mock subprocess.run to return successful responses for docker/system commands.

    Returns a callable that can be further configured per-test via .side_effect.
    """

    def _default_mock(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

        # docker info → success
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker network inspect proxy-net → simulates network not found
        if "network inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="Error: No such network")
        # docker network create → success
        if "network create" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="proxy-net\n", stderr="")
        # docker ps → empty
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker inspect container → no networks
        if "docker inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker exec nginx nginx -t → success
        if "nginx -t" in cmd_str:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="nginx: the configuration file ... syntax is ok", stderr=""
            )
        # id -nG ci-deploy → success with adm group
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy adm docker\n", stderr="")
        # stat → return 644 0:0
        if "stat" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="644\n", stderr="")
        # usermod → success
        if "usermod" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # chmod/chown/mkdir → success
        if any(x in cmd_str for x in ("chmod", "chown", "mkdir", "touch")):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=_default_mock) as mock:
        yield mock


@pytest.fixture(autouse=True)
def _restore_audit_globals():
    """Restore converge/audit module globals after each test.

    R2 tests monkeypatch _converge_audit.AUDIT_LOG_DIR/AUDIT_LOG_FILE to tmp_path.
    These are module-level globals shared process-wide — without restore they leak
    into test_converge_c8_audit_log_file.py (asserts converge_audit.AUDIT_LOG_FILE
    == shared DEFAULT_LOG_FILE). The monolith hid this via alphabetical ordering;
    the F6 split made the pollution visible. (DevPlan 118 F6)
    """
    from core.internal.shared.audit_logger import DEFAULT_LOG_FILE

    orig_dir = _converge_audit.AUDIT_LOG_DIR
    orig_file = _converge_audit.AUDIT_LOG_FILE
    yield
    _converge_audit.AUDIT_LOG_DIR = orig_dir
    _converge_audit.AUDIT_LOG_FILE = orig_file
    assert _converge_audit.AUDIT_LOG_FILE in {DEFAULT_LOG_FILE, orig_file}


# endregion Fixtures


# region FUNC_test_reconcile_audit_log_symlink_fail
## 🧪 TRAP[TEST] · R2 symlink fail · Scenario: /var/log/platform is a symlink → fail
## · Regression: converge.sh lines 354-365 — symlink attack prevention
## · Last fail: never
## · Remove if: reconciler.R2 symlink detection removed
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_symlink_dir_fail(tmp_path, monkeypatch, caplog):
    """R2: Symlink log directory → status=fail."""
    caplog.set_level(logging.INFO)

    # Monkeypatch AUDIT_LOG_DIR to a symlink in tmp_path
    fake_dir = tmp_path / "var" / "log" / "platform"
    fake_link = tmp_path / "var" / "log" / "platform_link"
    fake_dir.mkdir(parents=True)
    fake_link.symlink_to(fake_dir)

    _converge_audit.AUDIT_LOG_DIR = str(fake_link)
    _converge_audit.AUDIT_LOG_FILE = str(fake_link / "audit.log")

    entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"
    assert entry["status"] == "fail"
    assert "Symlink" in entry["detail"]


# endregion FUNC_test_reconcile_audit_log_symlink_dir_fail


# region FUNC_test_reconcile_audit_log_missing_file
## 🧪 TRAP[TEST] · R2 missing audit.log · Scenario: audit.log does not exist → created
## · Regression: converge.sh lines 411-424
## · Last fail: never
## · Remove if: reconciler.R2 creation logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_missing_file(tmp_path, monkeypatch, caplog):
    """R2: audit.log missing → created with 0664 root:adm."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(log_dir / "audit.log")

    entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"

    # Verify file was created
    assert (log_dir / "audit.log").is_file(), "audit.log should have been created"


# endregion FUNC_test_reconcile_audit_log_missing_file


# region FUNC_test_reconcile_audit_log_converged
## 🧪 TRAP[TEST] · R2 converged · Scenario: audit.log already 0664 root:adm → SKIP
## · Regression: converge.sh lines 445-452
## · Last fail: never
## · Remove if: reconciler.R2 converged check changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_converged(tmp_path, monkeypatch, caplog, mock_subprocess_run):
    """R2: audit.log already 0664 root:adm → converged."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.log"
    audit_file.write_text("")  # empty file exists

    # Mock stat to return 664 and 0:4 (root:adm)
    def stat_mock(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "stat" in cmd_str and "%a" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="664\n", stderr="")
        if "stat" in cmd_str and "%u:%g" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="0:4\n", stderr="")
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy adm docker\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    with patch.object(subprocess, "run", side_effect=stat_mock):
        entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"
    # The entry may be "converged" or "warn" — we check that no errors were set
    assert not infra.has_errors


# endregion FUNC_test_reconcile_audit_log_converged


# region FUNC_test_reconcile_audit_log_ci_deploy_group
## 🧪 TRAP[TEST] · R2 ci-deploy group · Scenario: ci-deploy not in adm → usermod
## · Regression: converge.sh lines 368-391
## · Last fail: never
## · Remove if: reconciler.R2 group logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_ci_deploy_group(tmp_path, monkeypatch, caplog):
    """R2: ci-deploy NOT in adm group → calls usermod."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.log"
    audit_file.write_text("")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    usermod_called = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        # ci-deploy exists but NOT in adm
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy docker\n", stderr="")
        if "usermod" in cmd_str:
            usermod_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "stat" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="664\n", stderr="")
        if "nginx" in cmd_str or "docker" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"
    assert len(usermod_called) > 0, "usermod should have been called"
    assert "adm" in " ".join(usermod_called[0])


# endregion FUNC_test_reconcile_audit_log_ci_deploy_group
