"""
# GREP_SUMMARY: test_node_yaml, extract-context, node-yaml, yaml, shared-lib, context-extraction
# STRUCTURE: ▶ tmp_path + caplog → ◇ context string → ◇ contexts array → ◇ contexts str array → ◇ missing → ◇ empty → ◇ missing file → ◇ log_tag → ⎋ LDD IMP:9 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/node_yaml.py — extract_context_from_node_yaml()
## @scope    Tests all extraction paths: context string, contexts array, missing, empty, log_tag
## @invariants
##   - All YAML files created via tmp_path (no hardcoded paths)
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
## @changes  2026-07-25 · DevPlan 070 — Created
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
# region Tests: extract_context_from_node_yaml
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · extract context from string field
# · Scenario: node.yaml with `context: "myorg"` → returns "myorg"
# · Last fail: N/A (new test)
# · Remove if: extract_context_from_node_yaml logic changes
@ldd_trajectory
def test_extract_context_string(caplog, tmp_path):
    """extract_context_from_node_yaml should return context from string field.

    ## @purpose  Verify that the primary extraction path (context string field)
    ##           returns the correct value and logs at IMP:8.
    """
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("context: myorg\n")

    result = ny.extract_context_from_node_yaml(str(yaml_file))
    assert result == "myorg"

    logger.critical("[IMP:9][test] extract_context_string: context=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · extract context from contexts array
# · Scenario: node.yaml with `contexts: [{name: "myorg"}]` → returns "myorg"
# · Last fail: N/A (new test)
# · Remove if: extract_context_from_node_yaml logic changes
@ldd_trajectory
def test_extract_context_from_array(caplog, tmp_path):
    """extract_context_from_node_yaml should return context from contexts[0].name.

    ## @purpose  Verify the fallback extraction path (contexts array with dicts).
    """
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("contexts:\n  - name: myorg\n    domains:\n      - example.com\n")

    result = ny.extract_context_from_node_yaml(str(yaml_file))
    assert result == "myorg"

    logger.critical("[IMP:9][test] extract_context_from_array: context=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · extract context from string array
# · Scenario: node.yaml with `contexts: ["first", "second"]` → returns "first"
# · Last fail: N/A (new test)
# · Remove if: extract_context_from_node_yaml logic changes
@ldd_trajectory
def test_extract_context_string_first(caplog, tmp_path):
    """extract_context_from_node_yaml should return first from string array.

    ## @purpose  Verify fallback path when contexts is an array of strings.
    """
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("contexts:\n  - first\n  - second\n")

    result = ny.extract_context_from_node_yaml(str(yaml_file))
    assert result == "first"

    logger.critical("[IMP:9][test] extract_context_string_first: context=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · extract context when missing
# · Scenario: node.yaml with no context/contexts fields → returns ""
# · Last fail: N/A (new test)
# · Remove if: extract_context_from_node_yaml logic changes
@ldd_trajectory
def test_extract_context_missing(caplog, tmp_path):
    """extract_context_from_node_yaml should return '' when context is absent.

    ## @purpose  Verify graceful handling of YAML without context field.
    """
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("domain: example.com\n")

    result = ny.extract_context_from_node_yaml(str(yaml_file))
    assert result == ""

    logger.critical("[IMP:9][test] extract_context_missing: result='' — OK")


# 🧪 TRAP[TEST] · Regression · extract context from empty YAML
# · Scenario: empty YAML file → returns ""
# · Last fail: N/A (new test)
# · Remove if: extract_context_from_node_yaml logic changes
@ldd_trajectory
def test_extract_context_empty_yaml(caplog, tmp_path):
    """extract_context_from_node_yaml should return '' for empty file.

    ## @purpose  Verify graceful handling of empty YAML file.
    """
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("")

    result = ny.extract_context_from_node_yaml(str(yaml_file))
    assert result == ""

    logger.critical("[IMP:9][test] extract_context_empty_yaml: result='' — OK")


# 🧪 TRAP[TEST] · Regression · extract context when file missing
# · Scenario: nonexistent file path → returns "" (no raise)
# · Last fail: N/A (new test)
# · Remove if: extract_context_from_node_yaml logic changes
@ldd_trajectory
def test_extract_context_missing_file(caplog, tmp_path):
    """extract_context_from_node_yaml should return '' for missing file.

    ## @purpose  Verify graceful handling of FileNotFoundError.
    """
    missing = tmp_path / "nonexistent.yaml"

    result = ny.extract_context_from_node_yaml(str(missing))
    assert result == ""

    logger.critical("[IMP:9][test] extract_context_missing_file: result='' — OK")


# 🧪 TRAP[TEST] · Regression · extract context with custom log_tag
# · Scenario: log_tag="my_tag" → [IMP:8][my_tag] in caplog
# · Last fail: N/A (new test)
# · Remove if: log_tag parameter changes
@ldd_trajectory
def test_extract_context_log_tag(caplog, tmp_path):
    """extract_context_from_node_yaml should use custom log_tag in logs.

    ## @purpose  Verify log_tag parameter correctly propagates into LDD log prefix.
    """
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("context: myorg\n")

    result = ny.extract_context_from_node_yaml(str(yaml_file), log_tag="my_tag")
    assert result == "myorg"

    # Verify the custom log tag appears in the log output
    found_tag = False
    for record in caplog.records:
        if "[IMP:8][my_tag]" in record.message:
            found_tag = True
            break
    assert found_tag, "Expected [IMP:8][my_tag] in log output"

    logger.critical("[IMP:9][test] extract_context_log_tag: tag=my_tag result=%s — OK", result)


# endregion Tests: extract_context_from_node_yaml
