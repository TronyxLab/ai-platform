# GREP_SUMMARY: yaml-query, unit-test, json-output, dict-list, python-repr-regression
# STRUCTURE: test_yaml_get_list_returns_valid_json → test_yaml_get_dict_returns_valid_json → test_yaml_get_scalar_unchanged → test_yaml_get_list_no_python_repr_single_quotes
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/yaml_query.py JSON output regression prevention
## @scope    Verify that yaml_get for dict/list returns valid JSON, not Python repr
## @invariants
##   - yaml_get for list → valid JSON array
##   - yaml_get for dict → valid JSON object
##   - yaml_get for scalar → unchanged value (no JSON wrapping)
## @rationale  TRAP[BUG] 2026-07-21: print(value) for dict/list output Python repr
##             (single quotes) instead of JSON (double quotes) → broke all
##             provision-environment.sh consumers. Prevention test.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 031 W1-E2)
# endregion MODULE_CONTRACT

import json
import pathlib
import subprocess
from pathlib import Path

import pytest

YAML_QUERY_PATH = Path(__file__).resolve().parents[1] / "core" / "internal" / "scripts" / "yaml_query.py"


def _query(file_path: Path, key: str) -> subprocess.CompletedProcess:
    """Run yaml_query.py --get and return subprocess result.

    ## @purpose  CLI wrapper helper for all tests in this module
    ## @io  file_path : Path — YAML file to query
    ##      key : str — dotted-key path to fetch
    ##      ← subprocess.CompletedProcess — result with stdout/stderr
    ## @complexity O(1) — single subprocess call
    """
    return subprocess.run(
        ["python3", str(YAML_QUERY_PATH), "--file", str(file_path), "--get", key],
        capture_output=True,
        text=True,
        timeout=10,
    )


# region CLI_OUTPUT_FORMAT_TESTS


def test_yaml_get_list_returns_valid_json(tmp_path: pathlib.Path) -> None:
    """yaml_get for a list must output valid JSON (not Python repr).

    ## @purpose — Prevention for TRAP[BUG] 2026-07-21.
    ## @rationale — Before fix: output was [{'name': 'test-net'}] (Python repr).
    ##              After fix: output must be [{"name": "test-net"}] (JSON).
    # 🧪 TRAP[TEST] · Regression · Scenario: list → JSON array · Last fail: pre-fix all 9 prov tests
    # · Remove if: yaml_query.py output format refactored
    """
    # region SETUP
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
networks:
  - name: proxy-net
    driver: bridge
  - name: shared-db-net
    driver: bridge
""")
    # endregion SETUP

    # region EXECUTE
    result = _query(yaml_file, "networks")
    # endregion EXECUTE

    # region VERIFY
    assert result.returncode == 0, f"Exit {result.returncode}: stderr={result.stderr[:500]}"

    output = result.stdout.strip()
    # Must be valid JSON
    parsed = json.loads(output)
    assert isinstance(parsed, list), f"Expected JSON array, got {type(parsed).__name__}"
    assert len(parsed) == 2, f"Expected 2 networks, got {len(parsed)}"
    assert parsed[0]["name"] == "proxy-net"
    assert parsed[1]["name"] == "shared-db-net"

    # Must NOT contain Python repr syntax (single quotes around keys/values)
    assert "'" not in output, f"Python repr detected in output (single quotes): {output[:100]}"
    assert '"' in output, f"Expected JSON double quotes: {output[:100]}"
    # endregion VERIFY


def test_yaml_get_dict_returns_valid_json(tmp_path: pathlib.Path) -> None:
    """yaml_get for a dict must output valid JSON object.

    ## @purpose — Dict regression guard for TRAP[BUG] 2026-07-21.
    # 🧪 TRAP[TEST] · Regression · Scenario: dict → JSON object · Last fail: pre-fix `test_scope_env_dry_run`
    # · Remove if: yaml_query.py output format refactored
    """
    # region SETUP
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
env_defaults:
  POSTGRES_PASSWORD: test-pg-pwd
  LITELLM_MASTER_KEY: sk-ci-test
""")
    # endregion SETUP

    # region EXECUTE
    result = _query(yaml_file, "env_defaults")
    # endregion EXECUTE

    # region VERIFY
    assert result.returncode == 0, f"Exit {result.returncode}: stderr={result.stderr[:500]}"

    output = result.stdout.strip()
    parsed = json.loads(output)
    assert isinstance(parsed, dict), f"Expected JSON object, got {type(parsed).__name__}"
    assert parsed["POSTGRES_PASSWORD"] == "test-pg-pwd"

    # Must NOT contain Python repr syntax
    assert "'" not in output, f"Python repr detected in output: {output[:100]}"
    # endregion VERIFY


def test_yaml_get_scalar_unchanged(tmp_path: pathlib.Path) -> None:
    """yaml_get for a scalar must NOT be JSON-wrapped (backward compat).

    ## @purpose — Backward compatibility guard: scalars unchanged.
    ## @rationale — Fix only affects dict/list. Strings, ints, bools must remain bare.
    # 🧪 TRAP[TEST] · Regression · Scenario: scalar unchanged · Last fail: N/A
    # · Remove if: yaml_query.py output format refactored
    """
    # region SETUP
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
node:
  host: 127.0.0.1
  port: 8080
  enabled: true
""")
    # endregion SETUP

    # region EXECUTE & VERIFY — String scalar
    result = _query(yaml_file, "node.host")
    assert result.returncode == 0
    assert result.stdout.strip() == "127.0.0.1", f"Expected bare scalar '127.0.0.1', got: {result.stdout.strip()}"

    # Integer scalar
    result = _query(yaml_file, "node.port")
    assert result.returncode == 0
    assert result.stdout.strip() == "8080", f"Expected bare scalar '8080', got: {result.stdout.strip()}"

    # Boolean scalar
    result = _query(yaml_file, "node.enabled")
    assert result.returncode == 0
    assert result.stdout.strip() == "True", f"Expected bare scalar 'True', got: {result.stdout.strip()}"
    # endregion EXECUTE & VERIFY


def test_yaml_get_list_no_python_repr_single_quotes(tmp_path: pathlib.Path) -> None:
    """Explicit regression test: output must not contain Python repr single quotes.

    ## @purpose — TRAP[BUG] 2026-07-21 specific regression: single quotes in output
    ##            break json.load() consumers.
    ## @rationale — Python repr uses single quotes for strings inside dicts,
    ##              which is not valid JSON. This test catches the exact symptom.
    # 🧪 TRAP[TEST] · Regression · Scenario: Python repr single quotes detection
    # · Last fail: pre-fix json.JSONDecodeError in provision-environment.sh
    # · Remove if: yaml_query.py output format refactored
    """
    # region SETUP
    # Minimal reproduction of the exact platform-env.yaml networks structure
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
networks:
  - name: proxy-net
    driver: bridge
    internal: false
""")
    # endregion SETUP

    # region EXECUTE
    result = _query(yaml_file, "networks")
    assert result.returncode == 0

    output = result.stdout.strip()
    # endregion EXECUTE

    # region VERIFY
    # Attempt json.load — must succeed
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"json.JSONDecodeError on yaml_query.py output: {e}\n"
            f"Output was: {output[:200]}\n"
            f"Likely cause: Python repr instead of JSON (single quotes)."
        )

    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "proxy-net"
    # endregion VERIFY


# endregion CLI_OUTPUT_FORMAT_TESTS
