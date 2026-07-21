# GREP_SUMMARY: test, yaml_query, unit-test, edge-cases, error-handling
# STRUCTURE: ▶ test_yaml_get_nested -> ◇ tmp_path fixture -> ⊕ assert dotted-key -> ⎋ PASSED
#            ▶ test_yaml_get_missing_key_with_default -> ⊕ assert default returned
#            ▶ test_yaml_get_missing_key_no_default -> ⊕ assert KeyError
#            ▶ test_malformed_yaml -> ⊕ assert YAMLError
#            ▶ test_file_not_found -> ⊕ assert FileNotFoundError
# region MODULE_CONTRACT
## @purpose  Unit-тесты для core/internal/scripts/yaml_query.py
## @scope    Все public API функции + CLI + edge cases
## @invariants
##   - Test Honesty R1: каждый тест имеет реальное assertion (не assert True)
##   - Test Honesty R5: negative tests для error-paths (missing key, malformed, not found)
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E7)
# endregion MODULE_CONTRACT

import pytest
import yaml

from core.internal.scripts.yaml_query import (
    _dotted_get,
    _load_yaml,
    json_get,
    yaml_get,
    yaml_query,
)


@pytest.fixture
def sample_yaml(tmp_path):
    p = tmp_path / "sample.yaml"
    p.write_text("node:\n  host: 127.0.0.1\n  port: 8080\nmodules:\n  - name: postgres\n  - name: redis\n")
    return p


# region POSITIVE_TESTS


def test_yaml_get_nested(sample_yaml):
    """Get value by dotted key path."""
    # 🧪 TRAP[TEST] · Regression · Scenario: dotted key traversal · Last fail: N/A · Remove if: refactor API
    assert yaml_get(sample_yaml, "node.host") == "127.0.0.1"


def test_yaml_get_list_index(sample_yaml):
    """Get list item by numeric index in dotted key."""
    # 🧪 TRAP[TEST] · Regression · Scenario: list index access · Last fail: N/A · Remove if: refactor API
    assert yaml_get(sample_yaml, "modules.0.name") == "postgres"


def test_yaml_get_with_default(sample_yaml):
    """Get with default when key missing — returns default."""
    # 🧪 TRAP[TEST] · Regression · Scenario: default value fallback · Last fail: N/A · Remove if: refactor API
    assert yaml_get(sample_yaml, "node.nonexistent", default="fb") == "fb"


def test_yaml_query_dotted(sample_yaml):
    """Jq-like dotted query."""
    # 🧪 TRAP[TEST] · Regression · Scenario: jq dotted filter · Last fail: N/A · Remove if: refactor API
    assert yaml_query(sample_yaml, ".node.host") == "127.0.0.1"


def test_yaml_query_list_iteration(sample_yaml):
    """Jq-like list iteration with []."""
    # 🧪 TRAP[TEST] · Regression · Scenario: list iteration filter · Last fail: N/A · Remove if: refactor API
    result = yaml_query(sample_yaml, ".modules[]")
    assert result == [{"name": "postgres"}, {"name": "redis"}]


def test_json_get(tmp_path):
    """Get value from JSON file."""
    # 🧪 TRAP[TEST] · Regression · Scenario: JSON file access · Last fail: N/A · Remove if: refactor API
    p = tmp_path / "data.json"
    p.write_text('{"a": {"b": 42}}')
    assert json_get(p, "a.b") == 42


def test_yaml_get_top_level_key(sample_yaml):
    """Get top-level key from YAML."""
    # 🧪 TRAP[TEST] · Regression · Scenario: top-level access · Last fail: N/A · Remove if: refactor API
    result = yaml_get(sample_yaml, "node")
    assert result == {"host": "127.0.0.1", "port": 8080}


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


# endregion POSITIVE_TESTS


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
    p.write_text("node:\n  host: [unclosed")
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
