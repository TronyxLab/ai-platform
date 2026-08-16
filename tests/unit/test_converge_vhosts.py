"""
# GREP_SUMMARY: test-converge-vhosts, r5, r6, detect-hosts-drift, verify-vhosts, nginx-vhosts, HOSTS_FILE
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R5 detect_hosts_drift 3× (unreadable/drift-found/converged) → ◇ R6 verify_vhosts 3× (no-nginx/orphan/all-ok) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for converge/vhosts.py via reconciler.detect_hosts_drift (R5)
##           and reconciler.verify_vhosts (R6).
## @scope    Tests /etc/hosts drift detection and nginx vhost verification
##           (orphan detection, nginx -t, GENERATED markers). Uses mock subprocess.run
##           for docker/system commands. Does NOT require a real docker daemon.
## @invariants
##   - All docker-dependent tests mock subprocess.run to avoid real docker calls
##   - File operations use tmp_path exclusively
##   - HOSTS_FILE monkeypatched to tmp_path
##   - Each test validates IMP:9 business logic log presence via caplog
## @rationale Direct function testing with tmp_path and mock subprocess.run.
##   Вынесен из монолита test_reconciler.py (DevPlan 118 F6).
## @changes 2026-08-02 · F6 split — R5/R6 vhosts (DevPlan 118)
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

import core.internal.bootstrap.converge.vhosts as _converge_vhosts
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


# endregion Fixtures


# region FUNC_test_detect_hosts_drift_unreadable
## 🧪 TRAP[TEST] · R5 unreadable · Scenario: /etc/hosts unreadable → WARN
## · Regression: converge.sh lines 795-799
## · Last fail: never
## · Remove if: reconciler.R5 hosts readability check changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_detect_hosts_drift_unreadable(tmp_path, caplog, monkeypatch):
    """R5: /etc/hosts not readable → status=warn."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R5 unreadable hosts — warn-path")

    # Monkeypatch HOSTS_FILE to a nonexistent path
    _converge_vhosts.HOSTS_FILE = str(tmp_path / "nonexistent_hosts")

    entry = reconciler.detect_hosts_drift(str(tmp_path))

    assert entry["unit"] == "R5"
    assert "warn" in entry["status"] or "warn" in entry["detail"].lower()


# endregion FUNC_test_detect_hosts_drift_unreadable


# region FUNC_test_detect_hosts_drift_found
## 🧪 TRAP[TEST] · R5 drift found · Scenario: stale /etc/hosts entry for project
## · Regression: converge.sh lines 822-838
## · Last fail: never
## · Remove if: reconciler.R5 drift detection logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_detect_hosts_drift_found(tmp_path, caplog, monkeypatch):
    """R5: Stale /etc/hosts entry found → WARN and set exit 1."""
    caplog.set_level(logging.INFO)

    # Create a fake /etc/hosts with a stale entry
    hosts_file = tmp_path / "hosts"
    hosts_file.write_text("127.0.0.1 localhost\n192.168.1.1 myapp.example.com myapp\n")
    _converge_vhosts.HOSTS_FILE = str(hosts_file)

    # Create node.yaml with project name "myapp"
    yaml_path = tmp_path / "node.yaml"
    yaml_content = """
projects:
  - name: myapp
    domain: myapp.example.com
"""
    yaml_path.write_text(yaml_content)

    entry = reconciler.detect_hosts_drift(str(yaml_path))

    assert entry["unit"] == "R5"
    assert infra.has_warnings
    assert infra.exit_code >= 1


# endregion FUNC_test_detect_hosts_drift_found


# region FUNC_test_detect_hosts_drift_clean
## 🧪 TRAP[TEST] · R5 clean · Scenario: no stale /etc/hosts entries → converged
## · Regression: converge.sh lines 840-842
## · Last fail: never
## · Remove if: reconciler.R5 drift detection logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_detect_hosts_drift_clean(tmp_path, caplog, monkeypatch):
    """R5: No stale entries in /etc/hosts → status=converged."""
    caplog.set_level(logging.INFO)

    # Clean hosts file
    hosts_file = tmp_path / "hosts"
    hosts_file.write_text("127.0.0.1 localhost\n::1 localhost\n")
    _converge_vhosts.HOSTS_FILE = str(hosts_file)

    yaml_path = tmp_path / "node.yaml"
    yaml_content = """
projects:
  - name: myapp
"""
    yaml_path.write_text(yaml_content)

    entry = reconciler.detect_hosts_drift(str(yaml_path))

    assert entry["unit"] == "R5"
    assert entry["status"] == "converged" or "converged" in entry.get("detail", "")


# endregion FUNC_test_detect_hosts_drift_clean


# region FUNC_test_verify_vhosts_no_nginx
## 🧪 TRAP[TEST] · R6 no nginx container · Scenario: nginx container not running → WARN
## · Regression: converge.sh lines 1009-1012
## · Last fail: never
## · Remove if: reconciler.R6 nginx check logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_verify_vhosts_no_nginx(tmp_path, caplog):
    """R6: nginx container not running → WARN nginx -t skipped."""
    caplog.set_level(logging.INFO)

    yaml_path = tmp_path / "node.yaml"
    yaml_content = """
contexts:
  - name: test-context
projects:
  - name: myapp
    domain: myapp.example.com
"""
    yaml_path.write_text(yaml_content)

    # Create nginx overlay directory
    overlay_dir = tmp_path / "opt" / "test-context" / "platform" / "modules" / "nginx"
    overlay_dir.mkdir(parents=True)
    vhost_file = overlay_dir / "myapp.example.com.conf"
    vhost_file.write_text("# GENERATED by add-vhost.sh\nserver { listen 80; }")

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        # docker ps → no nginx
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.verify_vhosts(
            str(yaml_path),
            converge_node="test-node",
            core_dir=str(tmp_path),
            dry_run=False,
            report_only=False,
            overlay_base=str(tmp_path / "opt"),
        )

    assert entry["unit"] == "R6"
    # The function should have skipped nginx -t but still checked vhost files
    assert not infra.has_errors


# endregion FUNC_test_verify_vhosts_no_nginx


# region FUNC_test_verify_vhosts_orphan
## 🧪 TRAP[TEST] · R6 orphan vhost · Scenario: orphan vhost file with no matching project
## · Regression: converge.sh lines 974-996
## · Last fail: never
## · Remove if: reconciler.R6 orphan detection logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_verify_vhosts_orphan(tmp_path, caplog):
    """R6: Orphan vhost detected → WARN logged."""
    caplog.set_level(logging.INFO)

    yaml_path = tmp_path / "node.yaml"
    yaml_content = """
contexts:
  - name: test-context
projects:
  - name: myapp
    domain: myapp.example.com
"""
    yaml_path.write_text(yaml_content)

    # Create nginx overlay with an orphan vhost
    overlay_dir = tmp_path / "opt" / "test-context" / "platform" / "modules" / "nginx"
    overlay_dir.mkdir(parents=True)
    # Matching vhost
    (overlay_dir / "myapp.example.com.conf").write_text("# GENERATED by add-vhost.sh\nserver { listen 80; }")
    # Orphan vhost
    (overlay_dir / "orphan-project.example.com.conf").write_text("# GENERATED by add-vhost.sh\nserver { listen 80; }")

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="nginx\n", stderr="")
        if "nginx -t" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="syntax is ok", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.verify_vhosts(
            str(yaml_path),
            converge_node="test-node",
            core_dir=str(tmp_path),
            dry_run=False,
            report_only=False,
            overlay_base=str(tmp_path / "opt"),
        )

    assert entry["unit"] == "R6"
    # Should have found orphan
    orphan_reports = [d for d in infra.drifts if "Orphan" in d.get("detail", "")]
    assert len(orphan_reports) > 0, "Orphan vhost should have been detected"


# endregion FUNC_test_verify_vhosts_orphan


# region FUNC_test_verify_vhosts_all_ok
## 🧪 TRAP[TEST] · R6 all ok · Scenario: all vhosts present, nginx -t passes
## · Regression: converge.sh lines 1014-1017
## · Last fail: never
## · Remove if: reconciler.R6 verification logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_verify_vhosts_all_ok(tmp_path, caplog):
    """R6: All vhosts present with GENERATED markers, nginx -t passes."""
    caplog.set_level(logging.INFO)

    yaml_path = tmp_path / "node.yaml"
    yaml_content = """
contexts:
  - name: test-context
projects:
  - name: myapp
    domain: myapp.example.com
  - name: api
    domain: api.example.com
"""
    yaml_path.write_text(yaml_content)

    overlay_dir = tmp_path / "opt" / "test-context" / "platform" / "modules" / "nginx"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "myapp.example.com.conf").write_text("# GENERATED by add-vhost.sh\nserver { listen 80; }")
    (overlay_dir / "api.example.com.conf").write_text("# GENERATED by add-vhost.sh\nserver { listen 80; }")

    nginx_test_called = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="nginx\n", stderr="")
        if "nginx -t" in cmd_str:
            nginx_test_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="syntax is ok", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.verify_vhosts(
            str(yaml_path),
            converge_node="test-node",
            core_dir=str(tmp_path),
            dry_run=False,
            report_only=False,
            overlay_base=str(tmp_path / "opt"),
        )

    assert entry["unit"] == "R6"
    assert not infra.has_errors, "Should have no errors for all-ok case"
    assert len(nginx_test_called) > 0, "nginx -t should have been called"


# endregion FUNC_test_verify_vhosts_all_ok


# region FUNC_test_vhost_renderer_marker_accepted
# 🧪 TRAP[TEST] · Regression · vhost от vhost_renderer.py (маркер на 2-й строке) НЕ warning
# · Scenario: шапка vhost_renderer.py (# ==== + # GENERATED by vhost_renderer.py) → OK
# · Last fail: RC-прогон 2026-08-12 — converge R6 false-warning «missing GENERATED marker» (N4)
# · Remove if: формат GENERATED-маркера vhost-файлов меняется
def test_vhost_renderer_marker_accepted(tmp_path, caplog):
    """Vhost with multi-line vhost_renderer.py header (marker on 2nd line) → no warning."""
    caplog.set_level(logging.INFO)

    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(
        """
contexts:
  - name: test-context
projects:
  - name: myapp
    domain: myapp.example.com
"""
    )

    overlay_dir = tmp_path / "opt" / "test-context" / "platform" / "modules" / "nginx"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "myapp.example.com.conf").write_text(
        "# ============================================================\n"
        "# GENERATED by vhost_renderer.py — DO NOT EDIT\n"
        "# Source: node.yaml#projects[myapp]\n"
        "# ============================================================\n"
        "server { listen 80; }\n"
    )

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="nginx\n", stderr="")
        if "nginx -t" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="syntax is ok", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.verify_vhosts(
            str(yaml_path),
            converge_node="test-node",
            core_dir=str(tmp_path),
            dry_run=False,
            report_only=False,
            overlay_base=str(tmp_path / "opt"),
        )

    assert entry["unit"] == "R6"
    assert not infra.has_errors, "Should have no errors"
    assert not any("missing GENERATED marker" in str(r.message) for r in caplog.records), (
        "R6 FAIL: vhost_renderer.py маркер не должен давать warning"
    )
    logger.info("[IMP:9][test] vhost_renderer.py multi-line marker accepted — no false warning")


# endregion FUNC_test_vhost_renderer_marker_accepted


# region FUNC_test_add_vhost_marker_still_ok
# 🧪 TRAP[TEST] · Regression · vhost от add-vhost.sh (маркер на 1-й строке) → OK (регрессия)
# · Scenario: шапка add-vhost.sh (# GENERATED by add-vhost.sh) → OK, warning отсутствует
# · Last fail: N/A (new test, DevPlan 153 T4 — регрессия на старый формат)
# · Remove if: формат GENERATED-маркера vhost-файлов меняется
def test_add_vhost_marker_still_ok(tmp_path, caplog):
    """Vhost with add-vhost.sh marker (1st line) → OK (regression)."""
    caplog.set_level(logging.INFO)

    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(
        """
contexts:
  - name: test-context
projects:
  - name: myapp
    domain: myapp.example.com
"""
    )

    overlay_dir = tmp_path / "opt" / "test-context" / "platform" / "modules" / "nginx"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "myapp.example.com.conf").write_text("# GENERATED by add-vhost.sh\nserver { listen 80; }")

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="nginx\n", stderr="")
        if "nginx -t" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="syntax is ok", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.verify_vhosts(
            str(yaml_path),
            converge_node="test-node",
            core_dir=str(tmp_path),
            dry_run=False,
            report_only=False,
            overlay_base=str(tmp_path / "opt"),
        )

    assert entry["unit"] == "R6"
    assert not infra.has_errors, "Should have no errors"
    assert not any("missing GENERATED marker" in str(r.message) for r in caplog.records), (
        "R6 FAIL: add-vhost.sh маркер должен оставаться валидным"
    )
    logger.info("[IMP:9][test] add-vhost.sh marker still accepted — regression OK")


# endregion FUNC_test_add_vhost_marker_still_ok
