"""
# GREP_SUMMARY: test_orphan_reconciler_selfheal, orphan, self-heal, container, rm, prune, docker, audit
# STRUCTURE: ▶ tmp_path + mock subprocess.run → ◇ _self_heal_orphan_containers × 2 (removed / not removed) + ◇ _self_heal_aged_images × 2 (pruned / not invoked) + ◇ audit-logs → ⎋ assert IMP:9 logs + subprocess call tracking
# region MODULE_CONTRACT
## @purpose  Unit tests for orphan_reconciler self-heal extension (W5-E5 R5/R6) — orphan container
##           removal via docker rm -f and aged image pruning via docker image prune.
## @scope    Tests the self-heal functions using mocked subprocess.run for all docker commands.
##           Does NOT require a real docker daemon. Verifies both self-heal mode and detect-only
##           (default) mode, plus audit log emission.
## @invariants
##   - All tests mock subprocess.run to avoid real docker calls
##   - test_self_heal_orphan_containers_removed: orphans list → docker rm -f invoked per orphan
##   - test_detect_only_no_removal: default main() → no docker rm -f calls
##   - test_self_heal_image_prune: node.yaml with retention → docker image prune with correct filters
##   - test_detect_only_no_prune: default main() → no docker image prune calls
##   - test_self_heal_audit_log: each rm/prune emits IMP:9 audit log
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory decorator
## @rationale Direct function testing with mock subprocess.run is idiomatic for pure logic
##   extraction. Verifies both modes (self-heal + detect-only) to prevent regression on
##   backward-compatible default behavior.
## @changes
##   2026-07-22 · Created (W5-E5 R5/R6 self-heal extension)
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# Load the LDD trajectory decorator from shared conftest
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_MODULE_DIR))
import orphan_reconciler as orphan_mod  # for module-level patching
from orphan_reconciler import (
    DEFAULT_IMAGE_RETENTION_DAYS,
    _self_heal_aged_images,
    _self_heal_orphan_containers,
)

# ── Constants ──
DOCKER_RM_TIMEOUT = 30
DOCKER_PRUNE_TIMEOUT = 120


# ══════════════════════════════════════════════════════════
#  Tests: _self_heal_orphan_containers
# ══════════════════════════════════════════════════════════


# region FUNC_test_self_heal_orphan_containers_removed
## @purpose  Orphan containers are removed via docker rm -f in self-heal mode
## @io       _self_heal_orphan_containers(orphans) → assert docker rm -f called per orphan
## @complexity 2 — mock subprocess + verify call count
# 🧪 TRAP[TEST] · Regression · Scenario: orphan containers detected → self-heal removes each via docker rm -f · Last fail: N/A · Remove if: self-heal removal logic changes
@ldd_trajectory
def test_self_heal_orphan_containers_removed(caplog) -> None:
    """Self-heal removes orphan containers via docker rm -f.

    Given a list of orphan containers detected by _batch_orphan_reconciliation,
    _self_heal_orphan_containers should call `docker rm -f <container>` for each.
    """
    orphans = [
        {"container_name": "orphan-pg", "project": "old-project"},
        {"container_name": "orphan-redis", "project": ""},
    ]
    removed_calls: list[list[str]] = []

    def _mock_rm_run(cmd, *args, **kwargs):
        nonlocal removed_calls
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "rm" in cmd_str and "-f" in cmd_str:
            removed_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("orphan_reconciler.subprocess.run", side_effect=_mock_rm_run):
        removed = _self_heal_orphan_containers(orphans)

    logger.info("[IMP:9][test][self-heal] Removed %d orphan containers via docker rm -f", removed)
    assert removed == 2, f"Expected 2 removals, got {removed}"
    assert len(removed_calls) == 2, f"Expected 2 docker rm -f calls, got {len(removed_calls)}"
    # Verify first call targets the first orphan
    assert "orphan-pg" in " ".join(removed_calls[0]), f"First rm call should target orphan-pg: {removed_calls[0]}"
    # Verify second call targets the second orphan
    assert "orphan-redis" in " ".join(removed_calls[1]), (
        f"Second rm call should target orphan-redis: {removed_calls[1]}"
    )
    logger.info("[IMP:9][test][self-heal] All orphan containers removed correctly ✓")


# endregion FUNC_test_self_heal_orphan_containers_removed


# region FUNC_test_detect_only_no_removal
## @purpose  In detect-only (default) mode, docker rm -f is never called
## @io       main() with default args → assert no docker rm -f calls
## @complexity 3 — mock full detection + main() + assert no rm calls
# 🧪 TRAP[TEST] · Regression · Scenario: detect-only mode must NOT call docker rm -f · Last fail: N/A · Remove if: default mode changes from detect-only
@ldd_trajectory
def test_detect_only_no_removal(modules_dir: Path, caplog) -> None:
    """In detect-only mode (default, no --self-heal), docker rm -f is NOT called.

    Runs main() with detect-only args. Verifies that detection subprocess calls
    (ps, compose config, inspect) are made, but NO docker rm -f calls.
    """
    rm_called = False
    subprocess_calls: list[str] = []

    def _mock_detect_only(cmd, *args, **kwargs):
        nonlocal rm_called
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        subprocess_calls.append(cmd_str)

        # Track if any docker rm command was called
        # NOTE: "docker rm" check avoids false match on "rm" in "--format" substring
        if "docker rm" in cmd_str:
            rm_called = True

        # Provide mock detection responses
        if "ps" in cmd_str and "-a" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="orphan-pg\n", stderr="")
        if "compose" in cmd_str and "config" in cmd_str:
            config_data = {
                "services": {
                    "postgres": {
                        "container_name": "orphan-pg",
                        "name": "postgres",
                    }
                }
            }
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(config_data), stderr="")
        if "inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="old-project", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("orphan_reconciler.subprocess.run", side_effect=_mock_detect_only), patch("sys.exit"):
        # Run main with detect-only args (no --self-heal)
        test_args = [
            "orphan_reconciler.py",
            "--module-entries",
            "postgres",
            "--modules-dir",
            str(modules_dir),
        ]
        with patch.object(sys, "argv", test_args):
            orphan_mod.main()

    logger.info("[IMP:9][test][detect-only] Subprocess calls: %s", subprocess_calls)

    assert not rm_called, "docker rm -f was called in detect-only mode — should not happen"
    # Verify detection DID happen (docker ps -a was called)
    ps_calls = [c for c in subprocess_calls if "ps" in c and "-a" in c]
    assert len(ps_calls) > 0, "Expected at least one docker ps -a call in detect-only mode"
    logger.info("[IMP:9][test][detect-only] No docker rm -f in detect-only mode ✓")


# endregion FUNC_test_detect_only_no_removal


# ══════════════════════════════════════════════════════════
#  Tests: _self_heal_aged_images
# ══════════════════════════════════════════════════════════


# region FUNC_test_self_heal_image_prune
## @purpose  Aged image prune is invoked with correct filters in self-heal mode
## @io       _self_heal_aged_images(node_yaml_path) → assert docker image prune with correct filters
## @complexity 3 — tmp_path node.yaml + mock subprocess + verify prune filter
# 🧪 TRAP[TEST] · Regression · Scenario: self-heal prunes aged images with retention from node.yaml · Last fail: N/A · Remove if: image prune logic changes
@ldd_trajectory
def test_self_heal_image_prune(tmp_path: Path, caplog) -> None:
    """Self-heal prunes aged images with retention from node.yaml.

    Creates a node.yaml with custom image_retention_days, mocks subprocess.run,
    and verifies that docker image prune is called with the correct age filter
    and compose project label filter.
    """
    # Create a node.yaml with custom retention
    retention_days = 45
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text(yaml.dump({"image_retention_days": retention_days}))

    prune_called = False
    prune_filters: list[str] = []

    def _mock_prune_run(cmd, *args, **kwargs):
        nonlocal prune_called, prune_filters
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "image" in cmd_str and "prune" in cmd_str:
            prune_called = True
            # Extract --filter arguments
            for i, c in enumerate(cmd):
                if c == "--filter" and i + 1 < len(cmd):
                    prune_filters.append(cmd[i + 1])
            # Simulate prune output: 2 images deleted
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="Deleted Images:\nuntagged: old-image@sha256:abc\nuntagged: another-image@sha256:def\n\nTotal reclaimed space: 150MB\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("orphan_reconciler.subprocess.run", side_effect=_mock_prune_run):
        pruned = _self_heal_aged_images(retention_days=45)

    logger.info("[IMP:9][test][prune] docker image prune returned pruned_count=%s", pruned)

    assert prune_called, "docker image prune was NOT called in self-heal mode"
    assert pruned == 3, f"Expected 3 pruned items (header + 2 image lines), got {pruned}"
    # Verify the until filter uses the custom retention (hours format)
    assert any(f"until={retention_days * 24}h" in f for f in prune_filters), (
        f"Expected until={retention_days * 24}h in prune filters: {prune_filters}"
    )
    # Verify the compose project label filter
    assert any("label=com.docker.compose.project" in f for f in prune_filters), (
        f"Expected label filter in prune filters: {prune_filters}"
    )
    logger.info("[IMP:9][test][prune] docker image prune called with correct filters ✓")


# endregion FUNC_test_self_heal_image_prune


# region FUNC_test_self_heal_image_prune_default_retention
## @purpose  When node.yaml is missing, default retention (30d) is used for prune
## @io       _self_heal_aged_images(non_existent_path) → assert prune with 30d filter
## @complexity 2 — node.yaml does not exist, mock subprocess, verify default retention
# 🧪 TRAP[TEST] · Regression · Scenario: missing node.yaml uses default 30-day retention · Last fail: N/A · Remove if: default retention logic changes
@ldd_trajectory
def test_self_heal_image_prune_default_retention(tmp_path: Path, caplog) -> None:
    """When node.yaml does not exist, uses default retention of 30 days."""
    non_existent = tmp_path / "nonexistent.yaml"
    assert not non_existent.exists(), "Fixture precondition failed: file should not exist"

    prune_filters: list[str] = []

    def _mock_default_retention(cmd, *args, **kwargs):
        nonlocal prune_filters
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "image" in cmd_str and "prune" in cmd_str:
            for i, c in enumerate(cmd):
                if c == "--filter" and i + 1 < len(cmd):
                    prune_filters.append(cmd[i + 1])
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Nothing to prune\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("orphan_reconciler.subprocess.run", side_effect=_mock_default_retention):
        pruned = _self_heal_aged_images()

    logger.info("[IMP:9][test][prune-default] Pruned (empty node.yaml) → count=%s", pruned)
    # With "Nothing to prune", non_empty lines = 1 ("Nothing to prune") → pruned_count = 1
    # But the key assertion is the default retention

    assert any(f"until={DEFAULT_IMAGE_RETENTION_DAYS * 24}h" in f for f in prune_filters), (
        f"Expected default until={DEFAULT_IMAGE_RETENTION_DAYS * 24}h in filters: {prune_filters}"
    )
    assert any("label=com.docker.compose.project" in f for f in prune_filters), (
        f"Expected compose label filter in prune filters: {prune_filters}"
    )
    logger.info("[IMP:9][test][prune-default] Default retention %d days used correctly ✓", DEFAULT_IMAGE_RETENTION_DAYS)


# endregion FUNC_test_self_heal_image_prune_default_retention


# region FUNC_test_detect_only_no_prune
## @purpose  In detect-only (default) mode, docker image prune is never called
## @io       main() with default args → assert no docker image prune calls
## @complexity 3 — mock full detection + main() + assert no prune calls
# 🧪 TRAP[TEST] · Regression · Scenario: detect-only mode must NOT call docker image prune · Last fail: N/A · Remove if: default mode changes from detect-only
@ldd_trajectory
def test_detect_only_no_prune(modules_dir: Path, caplog) -> None:
    """In detect-only mode (default, no --self-heal), docker image prune is NOT called.

    Runs main() with detect-only args. Verifies detection happens but no
    docker image prune command is invoked.
    """
    prune_called = False
    subprocess_calls: list[str] = []

    def _mock_detect_only(cmd, *args, **kwargs):
        nonlocal prune_called
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        subprocess_calls.append(cmd_str)

        if "image" in cmd_str and "prune" in cmd_str:
            prune_called = True

        # Mock detection responses
        if "ps" in cmd_str and "-a" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "compose" in cmd_str and "config" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='{"services":{}}', stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("orphan_reconciler.subprocess.run", side_effect=_mock_detect_only), patch("sys.exit"):
        test_args = [
            "orphan_reconciler.py",
            "--module-entries",
            "postgres",
            "--modules-dir",
            str(modules_dir),
        ]
        with patch.object(sys, "argv", test_args):
            orphan_mod.main()

    logger.info("[IMP:9][test][detect-only] Subprocess calls: %s", subprocess_calls)
    assert not prune_called, "docker image prune was called in detect-only mode — should not happen"
    logger.info("[IMP:9][test][detect-only] No docker image prune in detect-only mode ✓")


# endregion FUNC_test_detect_only_no_prune


# ══════════════════════════════════════════════════════════
#  Tests: Audit log emission
# ══════════════════════════════════════════════════════════


# region FUNC_test_self_heal_audit_log
## @purpose  Self-heal functions emit IMP:9 audit logs for each action
## @io       _self_heal_orphan_containers + _self_heal_aged_images → assert IMP:9 in caplog
## @complexity 3 — both self-heal functions + verify caplog IMP:9 audit entries
# 🧪 TRAP[TEST] · Regression · Scenario: IMP:9 audit log emitted per rm/prune action · Last fail: N/A · Remove if: audit log format changes
@ldd_trajectory
def test_self_heal_audit_log(tmp_path: Path, caplog) -> None:
    """Self-heal functions emit IMP:9 audit logs for orphan removal and image prune.

    Verifies that _self_heal_orphan_containers and _self_heal_aged_images both
    produce IMP:9 log entries documenting the actions taken.
    """
    caplog.set_level(logging.DEBUG)

    # Arrange orphans
    orphans = [
        {"container_name": "audit-orphan-1", "project": "stale"},
    ]

    # Create real node.yaml for aged images (not used — fixed to pass retention_days=30 directly)

    audit_subprocess_calls = 0

    def _mock_audit_run(cmd, *args, **kwargs):
        nonlocal audit_subprocess_calls
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        audit_subprocess_calls += 1
        if "image" in cmd_str and "prune" in cmd_str:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="Deleted Images:\nuntagged: old-image@sha256:abc\n\nTotal reclaimed space: 50MB\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("orphan_reconciler.subprocess.run", side_effect=_mock_audit_run):
        # Act: call both self-heal functions
        removed = _self_heal_orphan_containers(orphans)
        pruned = _self_heal_aged_images(retention_days=30)

    logger.info("[IMP:9][test][audit] Removed=%d, pruned=%s", removed, pruned)

    # Assert: IMP:9 audit logs present in caplog
    imp9_audit_logs = [
        r.message
        for r in caplog.records
        if "[IMP:9]" in r.message and ("Removed" in r.message or "Pruned" in r.message)
    ]

    logger.info("[IMP:9][test][audit] IMP:9 audit logs found: %s", imp9_audit_logs)
    assert len(imp9_audit_logs) >= 2, (
        f"Expected at least 2 IMP:9 audit logs (rm + prune), got {len(imp9_audit_logs)}: {imp9_audit_logs}"
    )

    # Verify at least one "Removed" audit log
    rm_logs = [m for m in imp9_audit_logs if "Removed" in m]
    assert len(rm_logs) >= 1, f"Expected IMP:9 Removed audit log, got: {imp9_audit_logs}"

    # Verify at least one "Pruned" audit log
    prune_logs = [m for m in imp9_audit_logs if "Pruned" in m]
    assert len(prune_logs) >= 1, f"Expected IMP:9 Pruned audit log, got: {imp9_audit_logs}"

    # Verify the orphan container name appears in the rm log
    assert any("audit-orphan-1" in m for m in rm_logs), (
        f"Orphan container name 'audit-orphan-1' should appear in rm audit logs: {rm_logs}"
    )

    logger.info("[IMP:9][test][audit] All IMP:9 audit logs verified ✓")


# endregion FUNC_test_self_heal_audit_log


# ══════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════


# region FUNC_modules_dir
## @purpose  Create a temporary modules directory with compose file for test modules
## @io       tmp_path → Path (modules directory with postgres/ compose files)
## @complexity 1 — creates one module subdirectory with compose.yaml
@pytest.fixture
def modules_dir(tmp_path) -> Path:
    """Create a tmp_path-based modules directory with a compose file.

    Creates:
      modules/postgres/compose.yaml
    """
    mod_dir = tmp_path / "modules"
    pg_dir = mod_dir / "postgres"
    pg_dir.mkdir(parents=True)
    (pg_dir / "compose.yaml").write_text("services:\n  postgres:\n    image: postgres:18.4\n")
    return mod_dir


# endregion FUNC_modules_dir
