"""
# GREP_SUMMARY: test_generate_entrypoint_manifest, extract_phony_targets, collect_gate_tests, merge, load_existing_manifest, tmp_path
# STRUCTURE: ▶ merge 3× (preserves-sections/replaces-allowed/replaces-gates) → ▶ extract_phony_targets 2× (gmake/grep) → ▶ load_existing_manifest 2× (valid/missing) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for generate_entrypoint_manifest.py — merge(), extract_phony_targets(),
##           load_existing_manifest(). No subprocess calls in merge tests.
## @scope    Tests merge logic (preserves sections, replaces allowed_verbs/gates), target extraction,
##           and manifest loading. extract_phony_targets tested with mock directories.
## @invariants
##   - All tests import the module directly via sys.path.insert
##   - Each test is decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for temp file and directory creation
## @rationale DevPlan 051 §5: Unit coverage for generate_entrypoint_manifest generator
## @changes 2026-07-22 | Created (DevPlan 051 Wave 2)
# endregion MODULE_CONTRACT
"""

import logging
import sys
import textwrap
from pathlib import Path

import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import generate_entrypoint_manifest as gem

# ═══════════════════════════════════════════════════════════════════
# region Tests: merge
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · merge preserves all non-target sections from existing manifest
# · Scenario: Existing manifest with bootstrap, deploy, forbidden sections + allowed_verbs/gates →
#            merge replaces allowed_verbs and gates, preserves everything else
# · Last fail: N/A (new test)
# · Remove if: merge logic changes
@ldd_trajectory
def test_merge_preserves_sections(caplog):
    """merge should preserve bootstrap, deploy, forbidden sections while replacing allowed_verbs and gates."""
    existing = {
        "bootstrap": [{"make_target": "bootstrap-node", "mechanism": "ssh+rsync"}],
        "deploy": [{"make_target": "deploy", "mechanism": "git-push"}],
        "allowed_verbs": ["old-verb"],
        "gates": [{"id": "old-gate", "test_file": "old_test.py"}],
        "forbidden_verbs": ["push-core"],
        "forbidden_scripts": ["dev.sh"],
        "name_linter": {"system_exceptions": ["help"]},
        "module_lifecycle": ["start", "stop"],
        "lib": [{"script": "ssh.sh", "path": "core/lib/ssh.sh"}],
    }

    new_allowed_verbs = ["deploy", "bootstrap-node", "test"]
    new_gates = [{"id": "new-gate", "test_file": "test_new_gate.py"}]

    result = gem.merge(new_allowed_verbs, new_gates, existing)

    # allowed_verbs should be replaced
    assert result["allowed_verbs"] == new_allowed_verbs, "allowed_verbs should be replaced"
    # gates should be replaced
    assert result["gates"] == new_gates, "gates should be replaced"
    # Other sections preserved
    assert result["bootstrap"] == existing["bootstrap"], "bootstrap should be preserved"
    assert result["deploy"] == existing["deploy"], "deploy should be preserved"
    assert result["forbidden_verbs"] == existing["forbidden_verbs"], "forbidden_verbs should be preserved"
    assert result["forbidden_scripts"] == existing["forbidden_scripts"], "forbidden_scripts should be preserved"
    assert result["name_linter"] == existing["name_linter"], "name_linter should be preserved"
    assert result["module_lifecycle"] == existing["module_lifecycle"], "module_lifecycle should be preserved"
    assert result["lib"] == existing["lib"], "lib should be preserved"

    logger.critical(
        "[IMP:9][test] merge preserves %d sections, replaces verbs (%d) and gates (%d)",
        len(result),
        len(new_allowed_verbs),
        len(new_gates),
    )


# 🧪 TRAP[TEST] · Regression · merge replaces allowed_verbs even when existing is empty
# · Scenario: Existing manifest with empty allowed_verbs → merge populates it
# · Last fail: N/A (new test)
# · Remove if: merge logic changes
@ldd_trajectory
def test_merge_replaces_allowed_verbs_empty(caplog):
    """merge should replace allowed_verbs even when existing value is empty."""
    existing = {"bootstrap": [{"make_target": "bootstrap-node"}], "allowed_verbs": [], "gates": []}
    new_verbs = ["deploy", "test"]

    result = gem.merge(new_verbs, [], existing)
    assert result["allowed_verbs"] == new_verbs, "allowed_verbs should be replaced"

    logger.critical("[IMP:9][test] merge replaces empty allowed_verbs — %d verbs", len(new_verbs))


# 🧪 TRAP[TEST] · Regression · merge replaces gates even when existing is empty
# · Scenario: Existing manifest with empty gates → merge populates from collection
# · Last fail: N/A (new test)
# · Remove if: merge logic changes
@ldd_trajectory
def test_merge_replaces_gates_empty(caplog):
    """merge should replace gates[] even when existing value is empty."""
    existing = {"bootstrap": [{"make_target": "bootstrap-node"}], "allowed_verbs": [], "gates": []}
    new_gates = [{"id": "new-gate", "test_file": "test_new_gate.py"}]

    result = gem.merge([], new_gates, existing)
    assert result["gates"] == new_gates, "gates should be replaced"

    logger.critical("[IMP:9][test] merge replaces empty gates — %d gates", len(new_gates))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: extract_phony_targets (grep fallback)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · extract_phony_targets grep fallback parses .PHONY lines
# · Scenario: tmp_path with Makefile + makefiles/*.mk containing .PHONY targets →
#            filters out system_exceptions (help, venv, pre-commit-*, test-*, gate-*)
# · Last fail: N/A (new test)
# · Remove if: extract_phony_targets grep fallback logic changes
@ldd_trajectory
def test_extract_phony_targets_grep_fallback(caplog, tmp_path):
    """extract_phony_targets grep fallback should parse .PHONY lines and filter system_exceptions."""
    # Create Makefile with .PHONY
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        textwrap.dedent("""\
        .PHONY: help venv deploy test pre-commit-run bootstrap-node
    """)
    )

    # Create makefiles subdir with .mk files
    mk_dir = tmp_path / "makefiles"
    mk_dir.mkdir()
    (mk_dir / "helpers.mk").write_text(
        textwrap.dedent("""\
        .PHONY: templates-check templates-render dev-certs provision test-inventory-sync _get_all_profiles
    """)
    )
    (mk_dir / "deploy.mk").write_text(
        textwrap.dedent("""\
        .PHONY: context-promote deploy-project
    """)
    )

    # Use nonexistent gmake path to force grep fallback
    targets = gem.extract_phony_targets(str(tmp_path), "/nonexistent/gmake")

    # help, venv, pre-commit-run should be filtered out
    assert "help" not in targets, "help should be filtered out"
    assert "venv" not in targets, "venv should be filtered out"
    assert "pre-commit-run" not in targets, "pre-commit-run should be filtered out"
    # test-* prefix filters "test-inventory-sync" but exact "test" target is kept
    assert "deploy" in targets, "deploy should be kept"
    assert "bootstrap-node" in targets, "bootstrap-node should be kept"
    assert "test" in targets, "test should be kept (exact match, not prefix)"
    assert "templates-check" in targets, "templates-check should be in targets"
    assert "context-promote" in targets, "context-promote should be in targets"
    # test-* prefix: test-inventory-sync is ALLOWED_PREFIX_EXCEPTIONS (registered in AGENTS.md)
    assert "test-inventory-sync" in targets, "test-inventory-sync should be kept (ALLOWED_PREFIX_EXCEPTIONS)"
    # system_exceptions: help, venv should be filtered
    assert "help" not in targets, "help should be filtered (system_exception)"
    assert "venv" not in targets, "venv should be filtered (system_exception)"
    # pre-commit-* prefix: pre-commit-run should be filtered
    assert "pre-commit-run" not in targets, "pre-commit-run should be filtered (pre-commit- prefix)"

    logger.critical("[IMP:9][test] extract_phony_targets grep fallback found %d targets: %s", len(targets), targets)


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: load_existing_manifest
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_existing_manifest loads valid YAML file
# · Scenario: tmp_path with valid YAML → returns parsed dict
# · Last fail: N/A (new test)
# · Remove if: load_existing_manifest logic changes
@ldd_trajectory
def test_load_existing_manifest(caplog, tmp_path):
    """load_existing_manifest should parse existing YAML manifest file."""
    manifest_file = tmp_path / "manifest.yaml"
    manifest_data = {
        "bootstrap": [{"make_target": "bootstrap-node"}],
        "allowed_verbs": ["deploy", "test"],
        "forbidden_verbs": ["push-core"],
    }
    with open(str(manifest_file), "w") as f:
        yaml.dump(manifest_data, f)

    result = gem.load_existing_manifest(str(manifest_file))
    assert result["allowed_verbs"] == ["deploy", "test"], "allowed_verbs should be loaded"
    assert result["forbidden_verbs"] == ["push-core"], "forbidden_verbs should be loaded"

    logger.critical("[IMP:9][test] load_existing_manifest loaded %d keys", len(result))


# 🧪 TRAP[TEST] · Regression · Missing manifest file returns empty dict
# · Scenario: Non-existent path → returns empty dict
# · Last fail: N/A (new test)
# · Remove if: load_existing_manifest logic changes
@ldd_trajectory
def test_load_existing_manifest_missing(caplog):
    """load_existing_manifest should return empty dict for missing file."""
    result = gem.load_existing_manifest("/tmp/nonexistent_manifest.yaml")
    assert result == {}, f"Expected empty dict, got {result}"

    logger.critical("[IMP:9][test] load_existing_manifest missing returns {}")


# endregion
