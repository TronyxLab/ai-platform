"""
# GREP_SUMMARY: test_node_yaml, get-context, node-yaml, yaml, shared-lib, context-extraction, contexts-canon
# STRUCTURE: ▶ tmp_path + caplog → ◇ contexts dict array → ◇ missing → ◇ empty → ◇ legacy context field (negative) → ⎋ LDD IMP:9 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/node_yaml.py — NodeYaml.get_context()
## @scope    Tests the contexts[] canon (invariant 3, DevPlan 116 B6 T1): contexts[0].name extraction,
##           empty handling, and negative case for the REMOVED legacy top-level 'context' field.
## @invariants
##   - All YAML files created via tmp_path (no hardcoded paths)
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
##   - get_context() → "" when contexts missing/empty (no-raise contract)
## @changes  2026-07-25 · DevPlan 070 — Created
## @changes  2026-08-01 · DevPlan 116 B6 T1 — rewritten: extract-context alias tests → get_context() canon
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

# Load the LDD trajectory decorator
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SHARED_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "shared"
sys.path.insert(0, str(_SHARED_DIR))
import node_yaml as ny

# ═══════════════════════════════════════════════════════════════════
# region Tests: NodeYaml.get_context (contexts[] canon)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · get_context from contexts[0].name dict
# · Scenario: node.yaml with `contexts: [{name: "myorg"}]` → returns "myorg"
# · Last fail: N/A (canon per DevPlan 116 B6 T1)
# · Remove if: contexts[] canon semantics change
@ldd_trajectory
def test_get_context_from_contexts_dict(caplog, tmp_path):
    """NodeYaml.get_context() should return contexts[0].name (dict-form canon)."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("contexts:\n  - name: myorg\n    description: test\n")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == "myorg"

    logger.critical("[IMP:9][test] get_context_from_contexts_dict: context=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · get_context ignores legacy top-level context field
# · Scenario: node.yaml with `context: legacy` + `contexts: [{name: "canon"}]` → returns "canon"
# · Last fail: N/A (canon per DevPlan 116 B6 T1 — legacy field removed from priority)
# · Remove if: contexts[] canon semantics change
@ldd_trajectory
def test_get_context_prefers_contexts_over_legacy(caplog, tmp_path):
    """get_context() must read contexts[0].name, NOT the removed legacy 'context' field."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("context: legacy\ncontexts:\n  - name: canon\n")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == "canon"

    logger.critical("[IMP:9][test] get_context_prefers_contexts: context=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · get_context when contexts missing → ""
# · Scenario: node.yaml with no contexts field → returns "" (no raise)
# · Last fail: N/A (canon per DevPlan 116 B6 T1)
# · Remove if: no-raise contract changes
@ldd_trajectory
def test_get_context_missing(caplog, tmp_path):
    """get_context() should return '' when contexts field is absent."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("domain: example.com\n")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == ""

    logger.critical("[IMP:9][test] get_context_missing: result='' — OK")


# 🧪 TRAP[TEST] · Regression · get_context on empty contexts list → ""
# · Scenario: `contexts: []` → returns "" (no IndexError, no raise)
# · Last fail: N/A (canon per DevPlan 116 B6 T1)
# · Remove if: no-raise contract changes
@ldd_trajectory
def test_get_context_empty_contexts(caplog, tmp_path):
    """get_context() should return '' for an empty contexts list (no IndexError)."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("contexts: []\n")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == ""

    logger.critical("[IMP:9][test] get_context_empty_contexts: result='' — OK")


# 🧪 TRAP[TEST] · Regression · get_context on empty YAML → ""
# · Scenario: empty YAML file → returns ""
# · Last fail: N/A (canon per DevPlan 116 B6 T1)
# · Remove if: no-raise contract changes
@ldd_trajectory
def test_get_context_empty_yaml(caplog, tmp_path):
    """get_context() should return '' for an empty YAML file."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == ""

    logger.critical("[IMP:9][test] get_context_empty_yaml: result='' — OK")


# 🧪 TRAP[TEST] · Regression · get_context on missing file → raises ConfigNotFoundError
# · Scenario: nonexistent file path → ConfigNotFoundError (get_context no longer absorbs errors)
# · Last fail: N/A (canon per DevPlan 116 B6 T2 — exception-absorbing alias removed)
# · Remove if: facade error contract changes
@ldd_trajectory
def test_get_context_missing_file_raises(caplog, tmp_path):
    """get_context() on a nonexistent file must raise ConfigNotFoundError (facade, not absorbing alias)."""
    from core.internal.shared.exceptions import ConfigNotFoundError

    missing = tmp_path / "nonexistent.yaml"

    try:
        ny.NodeYaml(str(missing)).get_context()
        raise AssertionError("Expected ConfigNotFoundError for missing file")
    except ConfigNotFoundError:
        pass

    logger.critical("[IMP:9][test] get_context_missing_file_raises: ConfigNotFoundError — OK")


# endregion Tests: NodeYaml.get_context
