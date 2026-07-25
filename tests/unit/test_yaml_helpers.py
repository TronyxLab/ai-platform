"""
# GREP_SUMMARY: test_yaml_helpers, extract_yaml_field, yaml, node-yaml, typed, bootstrap, cli
# STRUCTURE: ▶ tmp_path YAML fixtures → ◇ extract simple → ◇ extract nested → ◇ list first → ◇ file not found → ◇ invalid YAML → ◇ CLI mode → ⎋ LDD trajectory (IMP:7-10)
# region MODULE_CONTRACT
## @purpose  Unit tests for yaml_helpers.py — extract_yaml_field() and CLI entry point
## @scope    Tests extract_yaml_field() with top-level, nested, list-first, error branches; CLI subprocess entry point
## @invariants
##   - extract_yaml_field never raises — returns "" on any error (missing file, parse error, missing key)
##   - All business logic tests use native import (no subprocess.run)
##   - CLI test uses subprocess.run — CLI entry point testing, not business logic
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
## @rationale DevPlan 048: extracted from bootstrap.sh inline python3 -c blocks to dedicated testable module.
##            Ensures bootstrap YAML field extraction is covered by unit tests before Strangler migration.
## @changes
##   2026-07-25 · Created
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
import sys
from pathlib import Path

import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import yaml_helpers

# ═══════════════════════════════════════════════════════════════════
# region Tests: extract_yaml_field — top-level field
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_yaml_helpers_extract_simple
## @purpose  extract_yaml_field() extracts top-level field from YAML
## @io       tmp_path YAML → assert value matches expected
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Top-level field extraction from YAML
# · Scenario: Write simple YAML with top-level key "owner_key" → extract_yaml_field returns its value
# · Last fail: N/A (new test)
# · Remove if: extract_yaml_field top-level logic changes
@ldd_trajectory
def test_yaml_helpers_extract_simple(caplog, tmp_path):
    """extract_yaml_field should return top-level field value from YAML."""
    yml_path = tmp_path / "node.yaml"
    yml_path.write_text("owner_key: ssh-ed25519 AAAA...\nfqdn: node1.example.com\n")

    result = yaml_helpers.extract_yaml_field(str(yml_path), "owner_key")

    assert result == "ssh-ed25519 AAAA...", f"Expected 'ssh-ed25519 AAAA...', got '{result}'"
    logger.critical("[IMP:9][test][extract_simple] Top-level field 'owner_key' = '%s'", result)


# endregion FUNC_test_yaml_helpers_extract_simple


# ═══════════════════════════════════════════════════════════════════
# endregion
# ═══════════════════════════════════════════════════════════════════
# region Tests: extract_yaml_field — nested field
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_yaml_helpers_extract_nested
## @purpose  Extract nested fields using dotted path notation (multiple *field_path args)
## @io       tmp_path YAML → assert value equals expected nested field
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Nested field extraction via dotted path
# · Scenario: Write YAML with node.owner_key → extract_yaml_field with "node", "owner_key" returns value
# · Last fail: N/A (new test)
# · Remove if: nested traversal logic changes
@ldd_trajectory
def test_yaml_helpers_extract_nested(caplog, tmp_path):
    """extract_yaml_field should traverse nested keys using *field_path."""
    yml_path = tmp_path / "node.yaml"
    yml_path.write_text(yaml.dump({"node": {"owner_key": "ssh-ed25519 AAAAB3...", "fqdn": "node1.example.com"}}))

    result = yaml_helpers.extract_yaml_field(str(yml_path), "node", "owner_key")

    assert result == "ssh-ed25519 AAAAB3...", f"Expected nested owner_key, got '{result}'"
    logger.critical("[IMP:9][test][extract_nested] Nested field 'node.owner_key' = '%s'", result)


# endregion FUNC_test_yaml_helpers_extract_nested


# ═══════════════════════════════════════════════════════════════════
# endregion
# ═══════════════════════════════════════════════════════════════════
# region Tests: extract_yaml_field — list first element
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_yaml_helpers_extract_list_first
## @purpose  Handles list fields by taking the first element's sub-key
## @io       tmp_path YAML with list value → assert first element's field is returned
## @complexity 1
# 🧪 TRAP[TEST] · Regression · List traversal: takes first element's sub-key
# · Scenario: YAML with docker.mirror as list of mirrors → extract_yaml_field with "docker", "mirror", "url"
#             returns first element's url
# · Last fail: N/A (new test)
# · Remove if: list-first traversal logic changes
@ldd_trajectory
def test_yaml_helpers_extract_list_first(caplog, tmp_path):
    """extract_yaml_field should take first element when encountering a list."""
    yml_path = tmp_path / "node.yaml"
    yml_path.write_text(
        yaml.dump(
            {
                "docker": {
                    "mirror": [
                        {"url": "https://mirror.gcr.io", "enabled": True},
                        {"url": "https://docker.example.com", "enabled": False},
                    ]
                }
            }
        )
    )

    result = yaml_helpers.extract_yaml_field(str(yml_path), "docker", "mirror", "url")

    assert result == "https://mirror.gcr.io", f"Expected first mirror URL, got '{result}'"
    logger.critical("[IMP:9][test][list_first] List first field 'docker.mirror.url' = '%s'", result)


# endregion FUNC_test_yaml_helpers_extract_list_first


# ═══════════════════════════════════════════════════════════════════
# endregion
# ═══════════════════════════════════════════════════════════════════
# region Tests: extract_yaml_field — file not found
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_yaml_helpers_file_not_found
## @purpose  Returns empty string when file doesn't exist — never raises
## @io       non-existent path → assert "" + WARN log
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Missing file returns empty string
# · Scenario: Call extract_yaml_field with non-existent path → returns "" without raising
# · Last fail: N/A (new test)
# · Remove if: FileNotFoundError handling changes
@ldd_trajectory
def test_yaml_helpers_file_not_found(caplog, tmp_path):
    """extract_yaml_field should return '' when file does not exist (no raise)."""
    missing_path = tmp_path / "does_not_exist.yaml"

    result = yaml_helpers.extract_yaml_field(str(missing_path), "owner_key")

    assert result == "", f"Expected empty string, got '{result}'"
    assert "file not found" in caplog.text, "Expected 'file not found' WARN log"
    logger.critical("[IMP:9][test][file_not_found] Missing file returned '' — verified graceful handling")


# endregion FUNC_test_yaml_helpers_file_not_found


# ═══════════════════════════════════════════════════════════════════
# endregion
# ═══════════════════════════════════════════════════════════════════
# region Tests: extract_yaml_field — invalid YAML
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_yaml_helpers_invalid_yaml
## @purpose  Returns empty string on parse error — never raises
## @io       tmp_path with invalid YAML content → assert "" + WARN log
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Invalid YAML returns empty string
# · Scenario: Write malformed YAML → extract_yaml_field returns "" without raising
# · Last fail: N/A (new test)
# · Remove if: YAML parse error handling changes
@ldd_trajectory
def test_yaml_helpers_invalid_yaml(caplog, tmp_path):
    """extract_yaml_field should return '' on YAML parse error (no raise)."""
    yml_path = tmp_path / "node.yaml"
    yml_path.write_text("key: value: broken: [[,],\n  invalid: \t\\unrecognized")

    result = yaml_helpers.extract_yaml_field(str(yml_path), "owner_key")

    assert result == "", f"Expected empty string, got '{result}'"
    assert "YAML parse error" in caplog.text or "parse error" in caplog.text.lower(), "Expected YAML parse error log"
    logger.critical("[IMP:9][test][invalid_yaml] Invalid YAML returned '' — verified graceful handling")


# endregion FUNC_test_yaml_helpers_invalid_yaml


# ═══════════════════════════════════════════════════════════════════
# endregion
# ═══════════════════════════════════════════════════════════════════
# region Tests: CLI mode
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_yaml_helpers_cli
## @purpose  CLI mode works with command-line arguments — invokes the module as a script
## @io       tmp_path YAML + subprocess → assert stdout contains extracted value
## @complexity 2
# 🧪 TRAP[TEST] · Regression · CLI entry point: file + field.path → extracted value on stdout
# · Scenario: Invoke yaml_helpers.py as script with <file> <field.path> → stdout contains field value
# · Last fail: N/A (new test)
# · Remove if: CLI entry point logic changes
@ldd_trajectory
def test_yaml_helpers_cli(caplog, tmp_path):
    """CLI mode: running yaml_helpers.py as script should print extracted field."""
    yml_path = tmp_path / "node.yaml"
    yml_path.write_text("owner_key: ssh-ed25519 AAAA...\nfqdn: node1.example.com\n")

    module_path = _MODULE_DIR / "yaml_helpers.py"
    result = subprocess.run(
        [sys.executable, str(module_path), str(yml_path), "owner_key"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"CLI exited with code {result.returncode}: stderr={result.stderr}"
    stdout_clean = result.stdout.strip()
    assert stdout_clean == "ssh-ed25519 AAAA...", f"Expected 'ssh-ed25519 AAAA...', got '{stdout_clean}'"
    logger.critical("[IMP:9][test][cli] CLI mode returned '%s' with exit code 0", stdout_clean)


# endregion FUNC_test_yaml_helpers_cli


# ═══════════════════════════════════════════════════════════════════
# endregion
