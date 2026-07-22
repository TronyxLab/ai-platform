"""
# GREP_SUMMARY: test-reconciler, r8-sudoers, reconcile-sudoers, sudoers-drift, visudo, atomic-write, self-heal
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R8 reconcile_sudoers 3× (converged/drift/visudo-fail) → ⊕ atomic write verify → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for R8 reconcile_sudoers in reconciler.py — sudoers drift detection and self-heal
## @scope    Tests sudoers file comparison between desired state (from sudoers_generator render) and
##           actual files, with atomic write + visudo -c validation
## @invariants
##   - All system-dependent tests mock subprocess.run to avoid real visudo/chmod
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via LDD trajectory
## @rationale Direct function testing with mock subprocess.run for visudo and file operations
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    reconciler._reset_state()
    reconciler._node_name = "test-node"
    reconciler._core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")


@pytest.fixture
def node_yaml_with_modules(tmp_path):
    """Create a node.yaml with modules (for sudoers generation)."""
    yaml_content = """
context: test-context
modules:
  - name: nginx
    enabled: true
  - name: postgres
    enabled: true
projects: []
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content)
    return str(yaml_path)


@pytest.fixture
def sudoers_env(tmp_path):
    """Create a tmp_path environment simulating /etc/sudoers.d/, templates_dir, and modules_dir.

    Returns a dict with paths for the test functions.
    """
    sudoers_d_dir = tmp_path / "etc" / "sudoers.d"
    sudoers_d_dir.mkdir(parents=True)

    # Create a templates dir with sudo-whitelist.template
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir(parents=True)
    template_file = templates_dir / "sudo-whitelist.template"
    template_file.write_text(
        "# sudo-whitelist template\nci make:restart /path/to/module\nci make:reload /path/to/module\n"
    )

    # Create a modules dir with module.yaml files to simulate modules
    modules_dir = tmp_path / "modules"
    for mod in ("nginx", "postgres"):
        mod_dir = modules_dir / mod
        mod_dir.mkdir(parents=True)
        module_yaml = mod_dir / "module.yaml"
        module_yaml.write_text(f"name: {mod}\ntype: docker\n")

    return {
        "sudoers_d_dir": str(sudoers_d_dir),
        "templates_dir": str(templates_dir),
        "modules_dir": str(modules_dir),
        "tmp_path": tmp_path,
    }


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# R8 — reconcile_sudoers
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_reconcile_sudoers_converged
## 🧪 TRAP[TEST] · R8 converged · Scenario: all sudoers files match desired state → status=converged
## · Regression: R8 drift detection — no diff = converged
## · Last fail: never
## · Remove if: reconcile_sudoers detection logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_sudoers_converged(tmp_path, caplog, node_yaml_with_modules, sudoers_env, monkeypatch):
    """R8: All sudoers.d files match desired state → status=converged."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R8 converged — sudoers files match desired state")

    # Set _core_dir to match the test modules directory so R8 resolves the right modules path
    reconciler._core_dir = str(tmp_path)
    cooldown_file = tmp_path / ".converge_cooldown.json"
    monkeypatch.setattr(reconciler, "COOLDOWN_FILE", str(cooldown_file))
    monkeypatch.setattr(reconciler, "SUDOERS_DIR", sudoers_env["sudoers_d_dir"])

    # Compute expected desired content dynamically for ALL modules (matches what R8 will compute)
    modules_dir_path = Path(sudoers_env["modules_dir"]).resolve()
    for mod in ("nginx", "postgres"):
        expected_rules = [
            f"ci-deploy ALL=(root) NOPASSWD: /usr/bin/make -C {modules_dir_path}/{mod} restart",
            f"ci-deploy ALL=(root) NOPASSWD: /usr/bin/make -C {modules_dir_path}/{mod} reload",
        ]
        desired_content = reconciler._build_sudoers_content(mod, expected_rules)
        sudoers_file = Path(sudoers_env["sudoers_d_dir"]) / f"platform-{mod}"
        sudoers_file.write_text(desired_content)

    # Mock template-engine.sh render — write template content to output file (cmd[4])
    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "template-engine.sh" in cmd_str or "sudo-whitelist.template" in cmd_str:
            output_path = cmd[4]
            with open(output_path, "w") as f:
                f.write("ci make:restart /path/to/module\nci make:reload /path/to/module\n")
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="",
                stderr="",
            )
        # Mock visudo validation
        if "visudo" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "chmod" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_sudoers(
            node_yaml_path=node_yaml_with_modules,
            templates_dir=sudoers_env["templates_dir"],
            dry_run=False,
            report_only=False,
        )

    assert entry["unit"] == "R8"
    assert entry["status"] == "converged"


# endregion FUNC_test_reconcile_sudoers_converged


# region FUNC_test_reconcile_sudoers_drift_detected
## 🧪 TRAP[TEST] · R8 drift detected · Scenario: sudoers file content differs → self-heal via atomic write
## · Regression: R8 self-heal — tmp+rename pattern confirmed via os.replace/os.rename
## · Last fail: never
## · Remove if: reconcile_sudoers self-heal logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_sudoers_drift_detected(tmp_path, caplog, node_yaml_with_modules, sudoers_env, monkeypatch):
    """R8: sudoers file content differs → self-heal via atomic write (tmp+rename + visudo)."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R8 drift detected — self-heal via atomic write")

    reconciler._core_dir = str(tmp_path)
    cooldown_file = tmp_path / ".converge_cooldown.json"
    monkeypatch.setattr(reconciler, "COOLDOWN_FILE", str(cooldown_file))
    monkeypatch.setattr(reconciler, "SUDOERS_DIR", sudoers_env["sudoers_d_dir"])

    # Create a STALE sudoers file (WRONG content — should trigger self-heal)
    sudoers_target = Path(sudoers_env["sudoers_d_dir"]) / "platform-nginx"
    sudoers_target.write_text("# STALE content that differs from desired state\n")

    # Track atomic write: temp file creation + os.replace
    temp_files_created = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        # Mock template-engine.sh render — write content to output file (cmd[4])
        if "template-engine.sh" in cmd_str or "sudo-whitelist.template" in cmd_str:
            output_path = cmd[4]
            with open(output_path, "w") as f:
                f.write("ci make:restart /path/to/module\nci make:reload /path/to/module\n")
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="",
                stderr="",
            )
        # Mock visudo validation
        if "visudo" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # chmod
        if "chmod" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    # Monkeypatch os.replace to track atomic writes
    original_replace = os.replace

    def track_replace(src, dst):
        temp_files_created.append((src, dst))
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", track_replace)

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_sudoers(
            node_yaml_path=node_yaml_with_modules,
            templates_dir=sudoers_env["templates_dir"],
            dry_run=False,
            report_only=False,
        )

    assert entry["unit"] == "R8"
    assert entry["status"] == "mutated"
    # Verify atomic write: os.replace should have been called (tmp → target)
    assert len(temp_files_created) > 0, "os.replace should have been called for atomic write"


# endregion FUNC_test_reconcile_sudoers_drift_detected


# region FUNC_test_reconcile_sudoers_visudo_fail
## 🧪 TRAP[TEST] · R8 visudo fail · Scenario: visudo -c fails → WARN, file NOT changed
## · Regression: R8 visudo validation MUST gate atomic write — on failure, original untouched
## · Last fail: never
## · Remove if: reconcile_sudoers visudo validation logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_sudoers_visudo_fail(tmp_path, caplog, node_yaml_with_modules, sudoers_env, monkeypatch):
    """R8: visudo -c fails → WARN, sudoers file unchanged."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] R8 visudo-fail — atomic write blocked, original untouched")

    reconciler._core_dir = str(tmp_path)
    cooldown_file = tmp_path / ".converge_cooldown.json"
    monkeypatch.setattr(reconciler, "COOLDOWN_FILE", str(cooldown_file))
    monkeypatch.setattr(reconciler, "SUDOERS_DIR", sudoers_env["sudoers_d_dir"])

    # Create original sudoers content (should remain unchanged)
    original_content = "# Original sudoers content — should survive\n"
    sudoers_target = Path(sudoers_env["sudoers_d_dir"]) / "platform-nginx"
    sudoers_target.write_text(original_content)

    # Track any writes to the actual target file
    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        # Mock template-engine.sh render — write content to output file (cmd[4])
        if "template-engine.sh" in cmd_str or "sudo-whitelist.template" in cmd_str:
            output_path = cmd[4]
            with open(output_path, "w") as f:
                f.write("ci make:restart /path/to/module\nci make:reload /path/to/module\n")
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="",
                stderr="",
            )
        # Mock visudo VALIDATION FAILURE
        if "visudo" in cmd_str:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr=">>> Error: Invalid sudoers entry\n"
            )
        if "chmod" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    original_replace = os.replace
    replaced = []

    def track_replace(src, dst):
        replaced.append((src, dst))
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", track_replace)

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_sudoers(
            node_yaml_path=node_yaml_with_modules,
            templates_dir=sudoers_env["templates_dir"],
            dry_run=False,
            report_only=False,
        )

    assert entry["unit"] == "R8"
    # visudo fail should result in 'warn' or 'fail' — not 'mutated'
    assert entry["status"] == "warn"

    # Original file should NOT have been replaced
    actual_content = sudoers_target.read_text()
    assert actual_content == original_content, "Original sudoers file should NOT have been changed"
    logger.info("[IMP:9][test] R8 visudo-fail verified: original file unchanged")


# endregion FUNC_test_reconcile_sudoers_visudo_fail
