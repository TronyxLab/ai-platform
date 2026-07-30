"""
# GREP_SUMMARY: test_generate_entrypoint_manifest, extract_phony_targets, collect_gate_tests, merge, load_existing_manifest, load_structural_sections, tmp_path, g3-cycle-break
# STRUCTURE: ▶ merge 3× (preserves-sections/replaces-allowed/replaces-gates) → ▶ extract_phony_targets 2× (gmake/grep) → ▶ load_existing_manifest 2× (valid/missing) → ▶ load_structural_sections 3× (excludes/missing/preserves) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for generate_entrypoint_manifest.py — merge(), extract_phony_targets(),
##           load_existing_manifest(), load_structural_sections(). No subprocess calls in merge tests.
## @scope    Tests merge logic (preserves sections, replaces allowed_verbs/gates), target extraction,
##           manifest loading, and G3 cycle break (load_structural_sections).
## @invariants
##   - All tests import the module directly via sys.path.insert
##   - Each test is decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for temp file and directory creation
##   - load_structural_sections tests verify G3 cycle break: allowed_verbs/gates excluded
## @rationale DevPlan 051 §5: Unit coverage for generate_entrypoint_manifest generator
##            DevPlan 090 T6: G3 cycle break — load_structural_sections excludes generated keys
## @changes 2026-07-22 | Created (DevPlan 051 Wave 2)
##           2026-07-30 | Added load_structural_sections tests (G3 cycle break)
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


# ═══════════════════════════════════════════════════════════════════
# region Tests: load_structural_sections (G3 cycle break)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_structural_sections excludes allowed_verbs and gates
# · Scenario: Valid YAML with structural keys + allowed_verbs/gates → returns ONLY structural keys
# · Last fail: N/A (new test)
# · Remove if: load_structural_sections logic changes
@ldd_trajectory
def test_load_structural_sections_excludes_generated_keys(caplog, tmp_path):
    """load_structural_sections should exclude allowed_verbs and gates from result."""
    manifest_file = tmp_path / "manifest.yaml"
    manifest_data = {
        "metadata": {"version": 1},
        "convention": {"entrypoint_prefix": "core/entrypoints/"},
        "allowed_verbs": ["deploy", "test"],
        "gates": [{"id": "gate1", "test_file": "test_gate_1.py"}],
        "forbidden_verbs": ["push-core"],
        "name_linter": {"system_exceptions": ["help"]},
        "module_lifecycle": ["start", "stop"],
        "lib": [{"script": "ssh.sh", "path": "core/lib/ssh.sh"}],
    }
    with open(str(manifest_file), "w") as f:
        yaml.dump(manifest_data, f)

    result = gem.load_structural_sections(str(manifest_file))

    # allowed_verbs and gates must be excluded
    assert "allowed_verbs" not in result, (
        f"G3 CYCLE BREAK: allowed_verbs must NOT be in load_structural_sections result. "
        f"Found key in result: {list(result.keys())}"
    )
    assert "gates" not in result, (
        f"G3 CYCLE BREAK: gates must NOT be in load_structural_sections result. "
        f"Found key in result: {list(result.keys())}"
    )

    # Structural sections must be preserved
    assert result["metadata"] == {"version": 1}, "metadata should be preserved"
    assert result["convention"] == {"entrypoint_prefix": "core/entrypoints/"}, "convention should be preserved"
    assert result["forbidden_verbs"] == ["push-core"], "forbidden_verbs should be preserved"
    assert result["name_linter"] == {"system_exceptions": ["help"]}, "name_linter should be preserved"
    assert result["module_lifecycle"] == ["start", "stop"], "module_lifecycle should be preserved"
    assert result["lib"] == [{"script": "ssh.sh", "path": "core/lib/ssh.sh"}], "lib should be preserved"

    logger.critical(
        "[IMP:9][test] load_structural_sections returned %d keys (excluded allowed_verbs/gates)",
        len(result),
    )


# 🧪 TRAP[TEST] · Regression · load_structural_sections missing file returns empty dict
# · Scenario: Non-existent path → returns empty dict
# · Last fail: N/A (new test)
# · Remove if: load_structural_sections logic changes
@ldd_trajectory
def test_load_structural_sections_missing(caplog):
    """load_structural_sections should return empty dict for missing file."""
    result = gem.load_structural_sections("/tmp/nonexistent_structural_manifest.yaml")
    assert result == {}, f"Expected empty dict, got {result}"

    logger.critical("[IMP:9][test] load_structural_sections missing returns {}")


# 🧪 TRAP[TEST] · Regression · load_structural_sections preserves ALL other keys
# · Scenario: YAML with 20+ non-generated keys → all preserved, only allowed_verbs/gates excluded
# · Last fail: N/A (new test)
# · Remove if: load_structural_sections logic changes
@ldd_trajectory
def test_load_structural_sections_preserves_all_structural_keys(caplog, tmp_path):
    """load_structural_sections should preserve all keys except allowed_verbs and gates."""
    manifest_file = tmp_path / "manifest.yaml"
    # Construct a manifest with many structural sections
    manifest_data = {
        "metadata": {"version": 1},
        "convention": {"entrypoint_prefix": "core/entrypoints/"},
        "schema": {"type": "object"},
        "repair": [{"repair_id": "test", "repairs_gates": [{"gate_id": "g1"}]}],
        "forbidden_directories": ["core/scripts/e2e"],
        "forbidden_scripts": ["dev.sh"],
        "forbidden_verbs": ["push-core"],
        "name_linter": {"system_exceptions": ["help"]},
        "module_lifecycle": ["start", "stop"],
        "system_module_lifecycle": ["init"],
        "lib": [{"script": "ssh.sh"}],
        "module_hooks": {"pre-up": ["check"]},
        "shared_modules": ["secrets_env_parser"],
        "bootstrap": [{"make_target": "bootstrap-node"}],
        "deploy": [{"make_target": "deploy-project"}],
        "non_repairable_gates": ["test_gate_env_extra_vars"],
        "allowed_verbs": ["deploy"],  # must be excluded
        "gates": [{"id": "g1"}],  # must be excluded
    }
    with open(str(manifest_file), "w") as f:
        yaml.dump(manifest_data, f)

    result = gem.load_structural_sections(str(manifest_file))

    # These must be excluded
    assert "allowed_verbs" not in result, "allowed_verbs MUST be excluded"
    assert "gates" not in result, "gates MUST be excluded"

    # All other keys must be preserved
    preserved_count = 0
    for key in manifest_data:
        if key in ("allowed_verbs", "gates"):
            continue
        assert key in result, f"Structural key '{key}' should be preserved"
        preserved_count += 1

    assert preserved_count == len(result), (
        f"Expected {preserved_count} preserved keys, got {len(result)}. "
        f"Missing keys: {set(manifest_data.keys()) - {'allowed_verbs', 'gates'} - set(result.keys())}"
    )

    logger.critical(
        "[IMP:9][test] load_structural_sections preserved %d structural keys (excluded 2 generated keys)",
        len(result),
    )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: _check_generated_content / _generate_output (--check mode)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _generate_output produces valid YAML string with header
# · Scenario: Merged dict → returns YAML string with header comments
# · Last fail: N/A (new test)
# · Remove if: _generate_output logic changes
@ldd_trajectory
def test_generate_output(caplog):
    """_generate_output should produce valid YAML string with header."""
    merged = {
        "metadata": {"version": 1},
        "allowed_verbs": ["deploy", "test"],
        "gates": [{"id": "gate1", "test_file": "test_gate_1.py"}],
    }
    result = gem._generate_output(merged)

    assert "core/entrypoint-manifest.yaml" in result, "Should contain header comment"
    assert "allowed_verbs" in result, "Should contain allowed_verbs"
    assert "deploy" in result, "Should contain deploy verb"
    assert "gate1" in result, "Should contain gate1"

    logger.critical("[IMP:9][test] _generate_output produced %d chars", len(result))


# 🧪 TRAP[TEST] · Regression · check passes when content matches existing file
# · Scenario: tmp_path with file containing matching content → exit 0
# · Last fail: N/A (new test)
# · Remove if: _check_generated_content logic changes
@ldd_trajectory
def test_check_matches(caplog, tmp_path):
    """_check_generated_content should return 0 when content matches existing file."""
    test_file = tmp_path / "manifest.yaml"
    content = "hello: world\n"
    test_file.write_text(content, encoding="utf-8")

    result = gem._check_generated_content(content, test_file)
    assert result == 0, f"Expected 0 (match), got {result}"

    logger.critical("[IMP:9][test] _check_generated_content match returns 0")


# 🧪 TRAP[TEST] · Regression · check fails when content diverges from file
# · Scenario: tmp_path with file containing DIFFERENT content → exit 1, stderr diff
# · Last fail: N/A (new test)
# · Remove if: _check_generated_content logic changes
@ldd_trajectory
def test_check_diverges(caplog, tmp_path):
    """_check_generated_content should return 1 when content diverges from file."""
    test_file = tmp_path / "manifest.yaml"
    test_file.write_text("old: content\n", encoding="utf-8")
    generated = "new: content\n"

    result = gem._check_generated_content(generated, test_file)
    assert result == 1, f"Expected 1 (diverges), got {result}"

    logger.critical("[IMP:9][test] _check_generated_content diverges returns 1")


# 🧪 TRAP[TEST] · Regression · check fails when file does not exist
# · Scenario: Non-existent file path → exit 1
# · Last fail: N/A (new test)
# · Remove if: _check_generated_content logic changes
@ldd_trajectory
def test_check_missing_file(caplog):
    """_check_generated_content should return 1 for missing file."""
    result = gem._check_generated_content("content", Path("/tmp/nonexistent_check_manifest.yaml"))
    assert result == 1, f"Expected 1 (missing file), got {result}"

    logger.critical("[IMP:9][test] _check_generated_content missing file returns 1")


# endregion
