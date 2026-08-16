# GREP_SUMMARY: test, yaml-query, unit-test, edge-cases, json-output, cli, stdin, python-repr-regression
# STRUCTURE: ▶ direct-import edge-cases (nested/list-index/default/query) → ◇ CLI subprocess JSON-repr regression → ⊕ stdin-mode → ⎋ negatives (R5)
# region MODULE_CONTRACT
## @purpose  Канонический файл тестов core/internal/scripts/yaml_query.py (DevPlan 122 T5):
##           консолидация tests/test_yaml_query.py (direct-import edge-cases) +
##           tests/test_unit_yaml_query.py (CLI subprocess/JSON-repr regression) — один файл,
##           покрытие не уменьшено, R5 negative на каждый error-path.
## @scope    Все public API функции + CLI + stdin mode + edge cases
## @invariants
##   - Test Honesty R1: каждый тест имеет реальное assertion (не assert True)
##   - Test Honesty R5: negative tests для error-paths (missing key, malformed, not found)
##   - TRAP[BUG] 2026-07-21: print(value) для dict/list выводил Python repr (single quotes)
##     вместо JSON (double quotes) → ломал provision-environment.sh consumers. Prevention tests.
## @changes
##   LAST_CHANGE: 2026-08-03 | Consolidated from tests/test_yaml_query.py + tests/test_unit_yaml_query.py (DevPlan 122 T5)
##   2026-07-21 | Created (DevPlan 028 W1-E7 / DevPlan 031 W1-E2)
# endregion MODULE_CONTRACT

import json
import pathlib
import subprocess
from pathlib import Path

import pytest
import yaml

from core.internal.scripts.yaml_query import (
    _dotted_get,
    _load_yaml,
    json_get,
    yaml_get,
    yaml_query,
)

pytestmark = pytest.mark.static_audit

YAML_QUERY_PATH = Path(__file__).resolve().parents[2] / "core" / "internal" / "scripts" / "yaml_query.py"


@pytest.fixture
def sample_yaml(tmp_path):
    p = tmp_path / "sample.yaml"
    p.write_text(
        "node:\n  host: 127.0.0.1\n  port: 8080\nmodules:\n  - name: postgres\n  - name: redis\n", encoding="utf-8"
    )
    return p


def _query(file_path: Path, key: str) -> subprocess.CompletedProcess:
    """Run yaml_query.py --get and return subprocess result.

    ## @purpose  CLI wrapper helper for all CLI tests in this module
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
        check=False,
    )


def _run_cli(args: list, stdin: str = "") -> subprocess.CompletedProcess:
    """Run yaml_query.py with given args and optional stdin.

    ## @purpose  CLI wrapper for stdin-mode tests (StatusReport 046 T5 — CICD-01d)
    """
    return subprocess.run(
        ["python3", str(YAML_QUERY_PATH), *args], input=stdin, capture_output=True, text=True, timeout=10, check=False
    )


# region POSITIVE_TESTS_DIRECT_IMPORT


@pytest.mark.parametrize(
    ("path", "expected", "query_style"),
    [
        # yaml_get: dotted key traversal
        ("node.host", "127.0.0.1", False),
        # yaml_get: list index in dotted key
        ("modules.0.name", "postgres", False),
        # yaml_get: top-level key returns dict
        ("node", {"host": "127.0.0.1", "port": 8080}, False),
        # yaml_query: jq-style dotted filter
        (".node.host", "127.0.0.1", True),
        # yaml_query: list iteration
        (".modules[]", [{"name": "postgres"}, {"name": "redis"}], True),
    ],
)
def test_yaml_get_and_query_variants(sample_yaml, path, expected, query_style):
    """Parametrized: yaml_get/yaml_query dotted-key access variants (F5-reduction).

    ## @purpose — однотипные smoke-кейсы get/query консолидированы; покрытие веток сохранено.
    """
    # 🧪 TRAP[TEST] · Regression · Scenario: dotted key traversal · Last fail: N/A · Remove if: refactor API
    result = yaml_query(sample_yaml, path) if query_style else yaml_get(sample_yaml, path)
    assert result == expected


def test_yaml_get_with_default(sample_yaml):
    """Get with default when key missing — returns default."""
    # 🧪 TRAP[TEST] · Regression · Scenario: default value fallback · Last fail: N/A · Remove if: refactor API
    assert yaml_get(sample_yaml, "node.nonexistent", default="fb") == "fb"


def test_json_get(tmp_path):
    """Get value from JSON file."""
    # 🧪 TRAP[TEST] · Regression · Scenario: JSON file access · Last fail: N/A · Remove if: refactor API
    p = tmp_path / "data.json"
    p.write_text('{"a": {"b": 42}}', encoding="utf-8")
    assert json_get(p, "a.b") == 42


def test_yaml_get_with_default_none(sample_yaml):
    """When default=None explicitly passed, it means 'no default' — KeyError raised."""
    # 🧪 TRAP[TEST] · Negative · Scenario: default=None does not suppress error · Last fail: N/A · Remove if: refactor API
    # Canonical DevPlan design: default=None is indistinguishable from no-default → KeyError
    with pytest.raises(KeyError, match="key not found"):
        yaml_get(sample_yaml, "node.nonexistent", default=None)


def test_yaml_get_returns_int(sample_yaml):
    """Get numeric value preserves type."""
    # 🧪 TRAP[TEST] · Regression · Scenario: numeric value type preservation · Last fail: N/A · Remove if: refactor API
    assert yaml_get(sample_yaml, "node.port") == 8080
    assert isinstance(yaml_get(sample_yaml, "node.port"), int)


# endregion POSITIVE_TESTS_DIRECT_IMPORT


# region NEGATIVE_TESTS (Test Honesty R5)


def test_yaml_get_missing_key_no_default(sample_yaml):
    """Missing key without default raises KeyError."""
    # 🧪 TRAP[TEST] · Negative · Scenario: missing key exception · Last fail: N/A · Remove if: refactor API
    with pytest.raises(KeyError, match="key not found"):
        yaml_get(sample_yaml, "node.nonexistent")


def test_malformed_yaml(tmp_path):
    """Malformed YAML raises yaml.YAMLError."""
    # 🧪 TRAP[TEST] · Negative · Scenario: malformed YAML · Last fail: N/A · Remove if: refactor API
    p = tmp_path / "bad.yaml"
    p.write_text("node:\n  host: [unclosed", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        _load_yaml(p)


def test_file_not_found(tmp_path):
    """Non-existent file raises FileNotFoundError."""
    # 🧪 TRAP[TEST] · Negative · Scenario: file not found · Last fail: N/A · Remove if: refactor API
    with pytest.raises(FileNotFoundError):
        _load_yaml(tmp_path / "nonexistent.yaml")


def test_dotted_get_missing_key():
    """_dotted_get raises KeyError on missing nested key."""
    # 🧪 TRAP[TEST] · Negative · Scenario: nested missing key · Last fail: N/A · Remove if: refactor API
    data = {"a": {"b": 1}}
    with pytest.raises(KeyError, match="key not found"):
        _dotted_get(data, "a.c")


def test_yaml_query_bad_list(sample_yaml):
    """yaml_query on non-list key with [] raises TypeError."""
    # 🧪 TRAP[TEST] · Negative · Scenario: [] on non-list · Last fail: N/A · Remove if: refactor API
    with pytest.raises(TypeError, match="expected list"):
        yaml_query(sample_yaml, ".node[]")


# endregion NEGATIVE_TESTS


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
    yaml_file.write_text(
        """
networks:
  - name: proxy-net
    driver: bridge
  - name: shared-db-net
    driver: bridge
""",
        encoding="utf-8",
    )
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
    yaml_file.write_text(
        """
env_defaults:
  POSTGRES_PASSWORD: test-pg-pwd
  LITELLM_MASTER_KEY: sk-ci-test
""",
        encoding="utf-8",
    )
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
    yaml_file.write_text(
        """
node:
  host: 127.0.0.1
  port: 8080
  enabled: true
""",
        encoding="utf-8",
    )
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
    yaml_file.write_text(
        """
networks:
  - name: proxy-net
    driver: bridge
    internal: false
""",
        encoding="utf-8",
    )
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


# region STDIN_TESTS


def test_stdin_json_get_returns_value() -> None:
    """--stdin --get reads JSON from stdin and returns value.

    ## @purpose — StatusReport 046 T5: stdin mode for deploy-project.yml NODE_HOST_MAP
    # 🧪 TRAP[TEST] · Scenario · stdin --get · Last fail: N/A
    # · Remove if: --stdin flag removed
    """
    stdin = json.dumps({"tronyx-vps": "203.0.113.5", "other": "198.51.100.1"})

    result = _run_cli(["--stdin", "--get", "tronyx-vps"], stdin=stdin)

    assert result.returncode == 0, f"stderr={result.stderr}"
    assert result.stdout.strip() == "203.0.113.5"


def test_stdin_json_get_missing_key_returns_default() -> None:
    """--stdin --get with missing key returns default.

    ## @purpose — StatusReport 046 T5: default value for missing node
    # 🧪 TRAP[TEST] · Scenario · stdin missing key default · Last fail: N/A
    # · Remove if: --stdin flag removed
    """
    stdin = json.dumps({"other-node": "198.51.100.1"})

    result = _run_cli(["--stdin", "--get", "missing-node", "--default", ""], stdin=stdin)

    assert result.returncode == 0, f"stderr={result.stderr}"
    assert not result.stdout.strip()


def test_stdin_empty_returns_exit_two() -> None:
    """--stdin with empty input exits 2.

    ## @purpose — Error path: empty stdin
    # 🧪 TRAP[TEST] · Scenario · stdin empty · Last fail: N/A
    # · Remove if: empty stdin exit code changes
    """
    result = _run_cli(["--stdin", "--get", "key"], stdin="")

    assert result.returncode == 2
    assert "empty stdin" in result.stderr.lower()


def test_stdin_invalid_json_returns_exit_three() -> None:
    """--stdin with invalid JSON exits 3.

    ## @purpose — Error path: malformed JSON from stdin
    # 🧪 TRAP[TEST] · Scenario · stdin malformed · Last fail: N/A
    # · Remove if: invalid JSON exit code changes
    """
    result = _run_cli(["--stdin", "--get", "key"], stdin="{not json")

    assert result.returncode == 3
    assert "invalid json" in result.stderr.lower()


def test_stdin_and_file_mutually_exclusive(tmp_path: pathlib.Path) -> None:
    """--stdin and --file cannot be used together.

    ## @purpose — R-046-4: mutual exclusion contract
    # 🧪 TRAP[TEST] · Scenario · mutex --stdin --file · Last fail: N/A
    # · Remove if: mutually_exclusive_group removed
    """
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("key: value\n", encoding="utf-8")

    result = _run_cli(["--stdin", "--file", str(yaml_file), "--get", "key"], stdin="{}")

    # argparse exits 2 on mutually exclusive group violation
    assert result.returncode == 2


def test_stdin_neither_file_nor_stdin_errors() -> None:
    """Neither --stdin nor --file is an error (mutually exclusive required group).

    ## @purpose — Contract: must specify one source
    # 🧪 TRAP[TEST] · Scenario · no source · Last fail: N/A
    # · Remove if: required=True removed from source group
    """
    result = _run_cli(["--get", "key"], stdin="")

    assert result.returncode == 2  # argparse error


# endregion STDIN_TESTS
