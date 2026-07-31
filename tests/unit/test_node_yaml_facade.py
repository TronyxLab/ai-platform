"""
# GREP_SUMMARY: test_node_yaml_facade, NodeYaml, yaml-facade, lazy-load, cache, dotted-keys, cli
# STRUCTURE: ▶ 28 tests → ◇ load/parse tests → ◇ cache/reload tests → ◇ get/list tests → ◇ context tests → ◇ CLI tests → ⎋ all pass
# region MODULE_CONTRACT
## @purpose  Unit tests for NodeYaml facade (core/internal/shared/node_yaml.py)
## @scope    Tests 21 NodeYaml methods + 7 CLI scenarios:
##           - Load/parse (valid, not found, malformed, non-dict, empty, None)
##           - Cache/reload hit
##           - Dotted-key access (simple, nested, deep, missing with/without default)
##           - Typed list access
##           - Context extraction (string, array, empty)
##           - Projects & modules
##           - Domain config & node info
##           - Validation & raw
##           - CLI via subprocess
## @invariants
##   - All YAML files created via tmp_path (Zero Hardcode Rule)
##   - Each test validates LDD IMP:9 presence via @ldd_trajectory decorator
##   - No hardcoded paths, no subprocess.run for business logic (only for CLI tests)
## @changes 2026-07-26 · DevPlan 038a — Created
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from core.internal.shared.node_yaml import (
    DomainConfig,
    NodeInfo,
    NodeYaml,
)
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

PYTHON_MODULE = "core.internal.shared.node_yaml"
# Project root: tests/unit/../../
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_cli(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run the NodeYaml CLI as a subprocess and return the result.

    ## @purpose  Helper for CLI tests. Runs python3 -m node_yaml with args.
    ## @io — ⇥ args: list[str] → ⎋ subprocess.CompletedProcess
    ## @complexity — O(N) for subprocess run
    ## @invariants  Uses PROJECT_ROOT as default cwd to ensure core/ package is importable.
    """
    cmd = [sys.executable, "-m", PYTHON_MODULE, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or str(_PROJECT_ROOT),
    )


def _write_yaml(tmp_path: Path, content: str) -> Path:
    """Write YAML content to a temp file.

    ## @purpose  Helper — creates a temporary YAML file for testing.
    ## @io — ⇥ tmp_path: Path, content: str → ⎋ Path to written file
    ## @complexity — O(1)
    """
    path = tmp_path / "node.yaml"
    path.write_text(content)
    return path


# ═══════════════════════════════════════════════════════════════════
# region Tests: Load / Parse
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load valid YAML returns correct dict
# · Scenario: Valid node.yaml → load() returns dict with expected keys
# · Last fail: N/A (new test)
# · Remove if: _load() logic changes
@ldd_trajectory
def test_load_valid_yaml(caplog, tmp_path):
    """NodeYaml.load() should return parsed dict from valid YAML.

    ## @purpose  Verify basic YAML parsing works.
    """
    yaml_path = _write_yaml(tmp_path, "node:\n  host: 1.2.3.4\ncontext: myorg\n")
    node = NodeYaml(str(yaml_path))
    data = node.load()
    assert isinstance(data, dict)
    assert data["node"]["host"] == "1.2.3.4"
    assert data["context"] == "myorg"

    logger.critical("[IMP:9][test] load_valid_yaml: host=%s, ctx=%s — OK", data["node"]["host"], data["context"])


# 🧪 TRAP[TEST] · Regression · load raises ConfigNotFoundError for missing file
# · Scenario: Non-existent path → load() raises ConfigNotFoundError
# · Last fail: N/A (new test)
# · Remove if: _load() error handling changes
@ldd_trajectory
def test_load_file_not_found(caplog, tmp_path):
    """NodeYaml.load() should raise ConfigNotFoundError for missing file.

    ## @purpose  Verify FileNotFoundError is wrapped in ConfigNotFoundError.
    """
    missing = tmp_path / "nonexistent.yaml"
    node = NodeYaml(str(missing))
    with pytest.raises(ConfigNotFoundError) as exc_info:
        node.load()
    assert "node.yaml not found" in str(exc_info.value)

    logger.critical("[IMP:9][test] load_file_not_found: raised ConfigNotFoundError — OK")


# 🧪 TRAP[TEST] · Regression · load raises ConfigParseError for malformed YAML
# · Scenario: Invalid YAML syntax → load() raises ConfigParseError
# · Last fail: N/A (new test)
# · Remove if: _load() error handling changes
@ldd_trajectory
def test_load_malformed_yaml(caplog, tmp_path):
    """NodeYaml.load() should raise ConfigParseError for malformed YAML.

    ## @purpose  Verify YAML syntax errors are wrapped in ConfigParseError.
    """
    yaml_path = _write_yaml(tmp_path, "node: [unclosed list\n")
    node = NodeYaml(str(yaml_path))
    with pytest.raises(ConfigParseError) as exc_info:
        node.load()
    assert "YAML parse error" in str(exc_info.value)

    logger.critical("[IMP:9][test] load_malformed_yaml: raised ConfigParseError — OK")


# 🧪 TRAP[TEST] · Regression · load raises ConfigParseError for non-dict root
# · Scenario: YAML with list root [1,2,3] → load() raises ConfigParseError
# · Last fail: N/A (new test)
# · Remove if: _load() validation changes
@ldd_trajectory
def test_load_non_dict_root(caplog, tmp_path):
    """NodeYaml.load() should raise ConfigParseError for non-dict root.

    ## @purpose  Verify YAML with scalar/list root is rejected.
    """
    yaml_path = _write_yaml(tmp_path, "[1, 2, 3]\n")
    node = NodeYaml(str(yaml_path))
    with pytest.raises(ConfigParseError) as exc_info:
        node.load()
    assert "root is not a dict" in str(exc_info.value)

    logger.critical("[IMP:9][test] load_non_dict_root: raised ConfigParseError — OK")


# 🧪 TRAP[TEST] · Regression · load returns {} for empty file
# · Scenario: Empty file → load() returns empty dict
# · Last fail: N/A (new test)
# · Remove if: _load() empty-file handling changes
@ldd_trajectory
def test_load_empty_file(caplog, tmp_path):
    """NodeYaml.load() should return {} for empty file.

    ## @purpose  Verify empty file returns empty dict, not None.
    """
    yaml_path = _write_yaml(tmp_path, "")
    node = NodeYaml(str(yaml_path))
    data = node.load()
    assert data == {}

    logger.critical("[IMP:9][test] load_empty_file: data=%s — OK", data)


# 🧪 TRAP[TEST] · Regression · load returns {} for null YAML
# · Scenario: YAML with `null` value → load() returns empty dict
# · Last fail: N/A (new test)
# · Remove if: _load() None handling changes
@ldd_trajectory
def test_load_none_yaml(caplog, tmp_path):
    """NodeYaml.load() should return {} for null YAML content.

    ## @purpose  Verify yaml.safe_load returning None is handled.
    """
    yaml_path = _write_yaml(tmp_path, "null\n")
    node = NodeYaml(str(yaml_path))
    data = node.load()
    assert data == {}

    logger.critical("[IMP:9][test] load_none_yaml: data=%s — OK", data)


# endregion Tests: Load / Parse


# ═══════════════════════════════════════════════════════════════════
# region Tests: Cache / Reload
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · cache hit returns old data when file changes
# · Scenario: Load once, modify file, load again → returns original (cached)
# · Last fail: N/A (new test)
# · Remove if: caching logic changes
@ldd_trajectory
def test_cache_hit(caplog, tmp_path):
    """NodeYaml.load() should return cached data, not re-read file.

    ## @purpose  Verify cache works: modifying file between loads returns old data.
    """
    yaml_path = _write_yaml(tmp_path, "value: original\n")
    node = NodeYaml(str(yaml_path))
    data1 = node.load()
    assert data1["value"] == "original"

    # Modify file
    yaml_path.write_text("value: modified\n")

    # Cache should still return original
    data2 = node.load()
    assert data2["value"] == "original"

    logger.critical("[IMP:9][test] cache_hit: data=%s — OK", data2["value"])


# 🧪 TRAP[TEST] · Regression · reload() returns new data after file change
# · Scenario: Load once, modify file, reload() → returns new data
# · Last fail: N/A (new test)
# · Remove if: reload() logic changes
@ldd_trajectory
def test_reload_invalidates_cache(caplog, tmp_path):
    """NodeYaml.reload() should return updated data after file change.

    ## @purpose  Verify reload() invalidates cache and re-reads file.
    """
    yaml_path = _write_yaml(tmp_path, "value: original\n")
    node = NodeYaml(str(yaml_path))
    data1 = node.load()
    assert data1["value"] == "original"

    # Modify file
    yaml_path.write_text("value: modified\n")

    # Reload should return new data
    data2 = node.reload()
    assert data2["value"] == "modified"

    logger.critical("[IMP:9][test] reload_cache: before=%s, after=%s — OK", data1["value"], data2["value"])


# endregion Tests: Cache / Reload


# ═══════════════════════════════════════════════════════════════════
# region Tests: Dotted-key access (get / get_list)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · get simple dotted key
# · Scenario: get("node.host") returns correct string value
# · Last fail: N/A (new test)
# · Remove if: get() traversal logic changes
@ldd_trajectory
def test_get_simple_key(caplog, tmp_path):
    """NodeYaml.get() with simple dotted key returns correct value.

    ## @purpose  Verify basic dotted-key traversal.
    """
    yaml_path = _write_yaml(tmp_path, "node:\n  host: 1.2.3.4\n")
    node = NodeYaml(str(yaml_path))
    result = node.get("node.host")
    assert result == "1.2.3.4"

    logger.critical("[IMP:9][test] get_simple_key: node.host=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · get nested key
# · Scenario: get("domain.platform") returns correct string value
# · Last fail: N/A (new test)
# · Remove if: get() traversal logic changes
@ldd_trajectory
def test_get_nested_key(caplog, tmp_path):
    """NodeYaml.get() with 2-level nested key returns correct value.

    ## @purpose  Verify 2-level dotted-key traversal.
    """
    yaml_path = _write_yaml(tmp_path, "domain:\n  platform: example.com\n")
    node = NodeYaml(str(yaml_path))
    result = node.get("domain.platform")
    assert result == "example.com"

    logger.critical("[IMP:9][test] get_nested_key: domain.platform=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · get deeply nested key
# · Scenario: get("a.b.c") traverses 3+ levels
# · Last fail: N/A (new test)
# · Remove if: get() traversal logic changes
@ldd_trajectory
def test_get_deeply_nested(caplog, tmp_path):
    """NodeYaml.get() with 3+ level nested key returns correct value.

    ## @purpose  Verify multi-level dotted-key traversal.
    """
    yaml_path = _write_yaml(tmp_path, "a:\n  b:\n    c: deep_value\n")
    node = NodeYaml(str(yaml_path))
    result = node.get("a.b.c")
    assert result == "deep_value"

    logger.critical("[IMP:9][test] get_deeply_nested: a.b.c=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · get missing key without default raises error
# · Scenario: get("nonexistent") → ConfigValidationError
# · Last fail: N/A (new test)
# · Remove if: get() default=None behavior changes
@ldd_trajectory
def test_get_missing_key_no_default(caplog, tmp_path):
    """NodeYaml.get() with missing key and no default raises ConfigValidationError.

    ## @purpose  Verify missing key without default is an error.
    """
    yaml_path = _write_yaml(tmp_path, "node:\n  host: 1.2.3.4\n")
    node = NodeYaml(str(yaml_path))
    with pytest.raises(ConfigValidationError) as exc_info:
        node.get("nonexistent")
    assert "Key not found" in str(exc_info.value)

    logger.critical("[IMP:9][test] get_missing_no_default: raised ConfigValidationError — OK")


# 🧪 TRAP[TEST] · Regression · get missing key with default returns default
# · Scenario: get("nonexistent", default="fb") returns "fb"
# · Last fail: N/A (new test)
# · Remove if: get() default handling changes
@ldd_trajectory
def test_get_missing_key_with_default(caplog, tmp_path):
    """NodeYaml.get() with missing key and explicit default returns default.

    ## @purpose  Verify missing key returns default when provided.
    """
    yaml_path = _write_yaml(tmp_path, "node:\n  host: 1.2.3.4\n")
    node = NodeYaml(str(yaml_path))
    result = node.get("nonexistent", default="fb")
    assert result == "fb"

    logger.critical("[IMP:9][test] get_missing_with_default: default=fb — OK")


# 🧪 TRAP[TEST] · Regression · get_list returns list of dicts
# · Scenario: get_list("projects") returns list[dict]
# · Last fail: N/A (new test)
# · Remove if: get_list() logic changes
@ldd_trajectory
def test_get_list(caplog, tmp_path):
    """NodeYaml.get_list() returns list of dicts from valid YAML.

    ## @purpose  Verify typed list access works.
    """
    yaml_path = _write_yaml(
        tmp_path,
        "projects:\n  - name: app1\n  - name: app2\n",
    )
    node = NodeYaml(str(yaml_path))
    result = node.get_list("projects")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["name"] == "app1"
    assert result[1]["name"] == "app2"

    logger.critical("[IMP:9][test] get_list: count=%d — OK", len(result))


# 🧪 TRAP[TEST] · Regression · get_list with non-list raises error
# · Scenario: get_list("domain") where domain is a dict → ConfigValidationError
# · Last fail: N/A (new test)
# · Remove if: get_list() type validation changes
@ldd_trajectory
def test_get_list_not_a_list(caplog, tmp_path):
    """NodeYaml.get_list() raises ConfigValidationError for non-list value.

    ## @purpose  Verify get_list rejects non-list values.
    """
    yaml_path = _write_yaml(
        tmp_path,
        "domain:\n  platform: example.com\n",
    )
    node = NodeYaml(str(yaml_path))
    with pytest.raises(ConfigValidationError) as exc_info:
        node.get_list("domain")
    assert "not a list" in str(exc_info.value)

    logger.critical("[IMP:9][test] get_list_not_a_list: raised ConfigValidationError — OK")


# 🧪 TRAP[TEST] · Regression · get_list with missing key returns []
# · Scenario: get_list("nonexistent") returns []
# · Last fail: N/A (new test)
# · Remove if: get_list() missing key handling changes
@ldd_trajectory
def test_get_list_missing_key(caplog, tmp_path):
    """NodeYaml.get_list() returns [] for missing key.

    ## @purpose  Verify get_list returns empty list for missing key.
    """
    yaml_path = _write_yaml(tmp_path, "node:\n  host: 1.2.3.4\n")
    node = NodeYaml(str(yaml_path))
    result = node.get_list("nonexistent")
    assert result == []

    logger.critical("[IMP:9][test] get_list_missing_key: [] — OK")


# endregion Tests: Dotted-key access


# ═══════════════════════════════════════════════════════════════════
# region Tests: Context extraction
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · get_context from contexts[0].name (canon)
# · Scenario: contexts: [{name: "myorg"}] → get_context() returns "myorg"
# · Last fail: N/A (DevPlan 116 B6 T1 — contexts[] canon)
# · Remove if: get_context() canon changes
@ldd_trajectory
def test_get_context_string(caplog, tmp_path):
    """NodeYaml.get_context() returns contexts[0].name (dict-form canon).

    ## @purpose  Verify primary context extraction path (contexts[] canon, DevPlan 116 B6 T1).
    """
    yaml_path = _write_yaml(tmp_path, "contexts:\n  - name: myorg\n")
    node = NodeYaml(str(yaml_path))
    result = node.get_context()
    assert result == "myorg"

    logger.critical("[IMP:9][test] get_context_string: context=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · get_context from contexts array
# · Scenario: contexts array with name → get_context() returns first name
# · Last fail: N/A (new test)
# · Remove if: get_context() fallback changes
@ldd_trajectory
def test_get_context_array(caplog, tmp_path):
    """NodeYaml.get_context() returns context from contexts[0].name.

    ## @purpose  Verify fallback path from contexts array.
    """
    yaml_path = _write_yaml(
        tmp_path,
        "contexts:\n  - name: myorg\n    repo: git@github.com:myorg/ai-platform.git\n",
    )
    node = NodeYaml(str(yaml_path))
    result = node.get_context()
    assert result == "myorg"

    logger.critical("[IMP:9][test] get_context_array: context=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · get_context returns "" when no context present
# · Scenario: No context/contexts fields → get_context() returns ""
# · Last fail: N/A (new test)
# · Remove if: get_context() empty handling changes
@ldd_trajectory
def test_get_context_empty(caplog, tmp_path):
    """NodeYaml.get_context() returns "" when no context/contexts field.

    ## @purpose  Verify graceful handling of missing context fields.
    """
    yaml_path = _write_yaml(tmp_path, "domain: example.com\n")
    node = NodeYaml(str(yaml_path))
    result = node.get_context()
    assert result == ""

    logger.critical("[IMP:9][test] get_context_empty: result='' — OK")


# endregion Tests: Context extraction


# ═══════════════════════════════════════════════════════════════════
# region Tests: Projects / Modules
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · get_projects returns list of dicts
# · Scenario: get_projects() returns list with correct count
# · Last fail: N/A (new test)
# · Remove if: get_projects() logic changes
@ldd_trajectory
def test_get_projects(caplog, tmp_path):
    """NodeYaml.get_projects() returns list of project dicts.

    ## @purpose  Verify projects extraction.
    """
    yaml_path = _write_yaml(
        tmp_path,
        "projects:\n  - name: app1\n  - name: app2\n  - name: app3\n",
    )
    node = NodeYaml(str(yaml_path))
    result = node.get_projects()
    assert isinstance(result, list)
    assert len(result) == 3
    assert result[0]["name"] == "app1"

    logger.critical("[IMP:9][test] get_projects: count=%d — OK", len(result))


# 🧪 TRAP[TEST] · Regression · get_modules returns list of dicts
# · Scenario: get_modules() returns list with correct count
# · Last fail: N/A (new test)
# · Remove if: get_modules() logic changes
@ldd_trajectory
def test_get_modules(caplog, tmp_path):
    """NodeYaml.get_modules() returns list of module dicts.

    ## @purpose  Verify modules extraction.
    """
    yaml_path = _write_yaml(
        tmp_path,
        "modules:\n  - name: nginx\n    enabled: true\n  - name: postgres\n    enabled: false\n",
    )
    node = NodeYaml(str(yaml_path))
    result = node.get_modules()
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["name"] == "nginx"
    assert result[1]["enabled"] is False

    logger.critical("[IMP:9][test] get_modules: count=%d — OK", len(result))


# endregion Tests: Projects / Modules


# ═══════════════════════════════════════════════════════════════════
# region Tests: NamedTuples (DomainConfig / NodeInfo)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · get_domain_config returns correct NamedTuple
# · Scenario: Domain config extracted with correct fields
# · Last fail: N/A (new test)
# · Remove if: get_domain_config() logic changes
@ldd_trajectory
def test_get_domain_config(caplog, tmp_path):
    """NodeYaml.get_domain_config() returns DomainConfig with correct fields (flat schema).

    ## @purpose  Verify domain configuration extraction (flat-only, DevPlan 116 B6 T7).
    """
    yaml_path = _write_yaml(
        tmp_path,
        "domain: example.com\n"
        "email: admin@example.com\n"
        "acme_dns_plugin: cloudflare\n"
        "projects:\n  - name: app1\n    domain: app1.example.com\n"
        "  - name: app2\n    domain: app2.example.com\n",
    )
    node = NodeYaml(str(yaml_path))
    cfg = node.get_domain_config()
    assert isinstance(cfg, DomainConfig)
    assert cfg.platform_domain == "example.com"
    assert cfg.email == "admin@example.com"
    assert cfg.acme_dns_plugin == "cloudflare"
    assert cfg.project_domains == ["app1.example.com", "app2.example.com"]

    logger.critical(
        "[IMP:9][test] get_domain_config: domain=%s, projects=%d — OK", cfg.platform_domain, len(cfg.project_domains)
    )


# 🧪 TRAP[TEST] · Regression · get_node_info returns correct NamedTuple
# · Scenario: Node info extracted with correct fields
# · Last fail: N/A (new test)
# · Remove if: get_node_info() logic changes
@ldd_trajectory
def test_get_node_info(caplog, tmp_path):
    """NodeYaml.get_node_info() returns NodeInfo with correct fields.

    ## @purpose  Verify node metadata extraction.
    """
    yaml_path = _write_yaml(
        tmp_path,
        "node:\n  fqdn: node1.example.com\n  owner_key: age1abc123\n  docker_mirror: https://mirror.example.com\n",
    )
    node = NodeYaml(str(yaml_path))
    info = node.get_node_info()
    assert isinstance(info, NodeInfo)
    assert info.fqdn == "node1.example.com"
    assert info.owner_key == "age1abc123"
    assert info.docker_mirror == "https://mirror.example.com"

    logger.critical("[IMP:9][test] get_node_info: fqdn=%s — OK", info.fqdn)


# endregion Tests: NamedTuples


# ═══════════════════════════════════════════════════════════════════
# region Tests: Validate / Raw
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · validate returns empty for valid YAML
# · Scenario: Valid node.yaml → validate() returns []
# · Last fail: N/A (new test)
# · Remove if: validate() logic changes
@ldd_trajectory
def test_validate_valid(caplog, tmp_path):
    """NodeYaml.validate() returns [] for valid YAML.

    ## @purpose  Verify validation passes for well-formed YAML.
    """
    yaml_path = _write_yaml(
        tmp_path,
        "node:\n  name: test-node\n  host: 1.2.3.4\n  owner_key: test-key\ncontexts:\n  - name: test\ndomain: test.example.com\nmodules: []\n",
    )
    node = NodeYaml(str(yaml_path))
    errors = node.validate()
    assert errors == []

    logger.critical("[IMP:9][test] validate_valid: errors=0 — OK")


# 🧪 TRAP[TEST] · Regression · validate returns errors for invalid YAML
# · Scenario: Missing node section → validate() returns error list
# · Last fail: N/A (new test)
# · Remove if: validate() logic changes
@ldd_trajectory
def test_validate_invalid(caplog, tmp_path):
    """NodeYaml.validate() returns error list for structurally invalid YAML.

    ## @purpose  Verify validation catches missing sections.
    """
    yaml_path = _write_yaml(tmp_path, "projects: []\n")
    node = NodeYaml(str(yaml_path))
    errors = node.validate()
    assert len(errors) >= 1
    assert any("Missing 'node' section" in e for e in errors)

    logger.critical("[IMP:9][test] validate_invalid: errors=%d — OK", len(errors))


# 🧪 TRAP[TEST] · Regression · raw() returns dict
# · Scenario: raw() returns the full parsed dict
# · Last fail: N/A (new test)
# · Remove if: raw() logic changes
@ldd_trajectory
def test_raw(caplog, tmp_path):
    """NodeYaml.raw() returns the full raw dict.

    ## @purpose  Verify raw data access works.
    """
    yaml_path = _write_yaml(tmp_path, "node:\n  host: 1.2.3.4\ncontext: myorg\n")
    node = NodeYaml(str(yaml_path))
    data = node.raw()
    assert isinstance(data, dict)
    assert data["node"]["host"] == "1.2.3.4"

    logger.critical("[IMP:9][test] raw: type=%s — OK", type(data).__name__)


# endregion Tests: Validate / Raw


# ═══════════════════════════════════════════════════════════════════
# region Tests: Edge cases
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · get with non-dict intermediate
# · Scenario: get("node.host.nonexistent") where node.host is a string → ConfigValidationError
# · Last fail: N/A (new test)
# · Remove if: _get() traversal error handling changes
@ldd_trajectory
def test_get_non_dict_intermediate(caplog, tmp_path):
    """NodeYaml.get() raises ConfigValidationError when intermediate is not a dict.

    ## @purpose  Verify traversal error when navigating into scalar.
    """
    yaml_path = _write_yaml(tmp_path, "node:\n  host: 1.2.3.4\n")
    node = NodeYaml(str(yaml_path))
    with pytest.raises(ConfigValidationError) as exc_info:
        node.get("node.host.nonexistent")
    assert "Cannot traverse" in str(exc_info.value)

    logger.critical("[IMP:9][test] get_non_dict_intermediate: raised ConfigValidationError — OK")


# 🧪 TRAP[TEST] · Regression · get_context ignores str-form contexts (legacy, D5)
# · Scenario: contexts: ["first", "second"] (str-form, removed per D5) → get_context() = ""
# · Last fail: N/A (DevPlan 116 B6 T1/D5 — str-форма отменена, schema требует dict)
# · Remove if: contexts[] canon semantics change
@ldd_trajectory
def test_get_context_string_array(caplog, tmp_path):
    """NodeYaml.get_context() returns '' for str-form contexts (legacy removed, D5).

    ## @purpose  Verify str-form contexts[0] is no longer read (node.schema.json requires dict).
    """
    yaml_path = _write_yaml(tmp_path, "contexts:\n  - first\n  - second\n")
    node = NodeYaml(str(yaml_path))
    result = node.get_context()
    assert result == ""

    logger.critical("[IMP:9][test] get_context_string_array: str-form ignored → '' — OK")


# endregion Tests: Edge cases


# ═══════════════════════════════════════════════════════════════════
# region Tests: CLI
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · CLI --get returns value to stdout
# · Scenario: --file node.yaml --get node.host → stdout = "1.2.3.4"
# · Last fail: N/A (new test)
# · Remove if: CLI --get logic changes
@ldd_trajectory
def test_cli_get(caplog, tmp_path):
    """CLI --get returns value to stdout.

    ## @purpose  Verify basic --get flag outputs correct value.
    """
    # Use the test_data fixture to ensure richer YAML
    fixture_path = Path(__file__).resolve().parent.parent / "test_data" / "node_yaml_valid.yaml"
    result = _run_cli(["--file", str(fixture_path), "--get", "node.host"])
    assert result.returncode == 0
    assert result.stdout.strip() == "1.2.3.4"

    logger.critical("[IMP:9][test] cli_get: stdout=%s — OK", result.stdout.strip())


# 🧪 TRAP[TEST] · Regression · CLI --get --items outputs JSON array
# · Scenario: --file node.yaml --get projects --items → valid JSON
# · Last fail: N/A (new test)
# · Remove if: CLI --items logic changes
@ldd_trajectory
def test_cli_get_items(caplog, tmp_path):
    """CLI --get --items outputs valid JSON array.

    ## @purpose  Verify --items flag outputs JSON array.
    """
    fixture_path = Path(__file__).resolve().parent.parent / "test_data" / "node_yaml_valid.yaml"
    result = _run_cli(["--file", str(fixture_path), "--get", "projects", "--items"])
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["name"] == "app1"

    logger.critical("[IMP:9][test] cli_get_items: count=%d — OK", len(parsed))


# 🧪 TRAP[TEST] · Regression · CLI --domain-config outputs field:value lines
# · Scenario: --file node.yaml --domain-config → field:value lines
# · Last fail: N/A (new test)
# · Remove if: CLI --domain-config logic changes
@ldd_trajectory
def test_cli_domain_config(caplog, tmp_path):
    """CLI --domain-config outputs field:value lines for shell parsing.

    ## @purpose  Verify --domain-config format.
    """
    fixture_path = Path(__file__).resolve().parent.parent / "test_data" / "node_yaml_valid.yaml"
    result = _run_cli(["--file", str(fixture_path), "--domain-config"])
    assert result.returncode == 0
    lines = result.stdout.strip().split("\n")
    assert len(lines) >= 4
    assert lines[0] == "platform_domain:example.com"
    assert lines[1] == "email:admin@example.com"
    assert "project_domains:" in lines[3]

    logger.critical("[IMP:9][test] cli_domain_config: %d lines — OK", len(lines))


# 🧪 TRAP[TEST] · Regression · CLI --context outputs context name
# · Scenario: --file node.yaml --context → "myorg"
# · Last fail: N/A (new test)
# · Remove if: CLI --context logic changes
@ldd_trajectory
def test_cli_context(caplog, tmp_path):
    """CLI --context outputs context name.

    ## @purpose  Verify --context flag outputs correct context.
    """
    fixture_path = Path(__file__).resolve().parent.parent / "test_data" / "node_yaml_valid.yaml"
    result = _run_cli(["--file", str(fixture_path), "--context"])
    assert result.returncode == 0
    assert result.stdout.strip() == "myorg"

    logger.critical("[IMP:9][test] cli_context: context=%s — OK", result.stdout.strip())


# 🧪 TRAP[TEST] · Regression · CLI --validate returns exit 0 for valid YAML
# · Scenario: --file node.yaml --validate → exit 0
# · Last fail: N/A (new test)
# · Remove if: CLI --validate logic changes
@ldd_trajectory
def test_cli_validate_valid(caplog, tmp_path):
    """CLI --validate returns exit code 0 for valid YAML.

    ## @purpose  Verify validation CLI for valid input.
    """
    fixture_path = Path(__file__).resolve().parent.parent / "test_data" / "node_yaml_valid.yaml"
    result = _run_cli(["--file", str(fixture_path), "--validate"])
    assert result.returncode == 0
    assert result.stdout == ""

    logger.critical("[IMP:9][test] cli_validate_valid: exit=0 — OK")


# 🧪 TRAP[TEST] · Regression · CLI --validate returns errors for missing sections
# · Scenario: YAML missing node/domain → exit code = count of errors
# · Last fail: N/A (new test)
# · Remove if: CLI --validate logic changes
@ldd_trajectory
def test_cli_validate_invalid(caplog, tmp_path):
    """CLI --validate returns non-zero for structurally invalid YAML.

    ## @purpose  Verify validation CLI detects missing required sections.
    """
    yaml_path = _write_yaml(tmp_path, "projects: []\n")
    result = _run_cli(["--file", str(yaml_path), "--validate"])
    assert result.returncode >= 2  # missing node + domain = 2 errors
    assert "ERROR:" in result.stderr

    logger.critical("[IMP:9][test] cli_validate_invalid: exit=%d, stderr has ERROR — OK", result.returncode)


# 🧪 TRAP[TEST] · Regression · CLI --file /nonexistent exits 2
# · Scenario: --file /nonexistent --get x → exit code 2
# · Last fail: N/A (new test)
# · Remove if: CLI file-not-found handling changes
@ldd_trajectory
def test_cli_file_not_found(caplog, tmp_path):
    """CLI exits with code 2 for non-existent file.

    ## @purpose  Verify CLI maps ConfigNotFoundError to exit code 2.
    """
    result = _run_cli(["--file", "/nonexistent/node.yaml", "--get", "x"])
    assert result.returncode == 2
    assert "node.yaml not found" in result.stderr

    logger.critical("[IMP:9][test] cli_file_not_found: exit=%d — OK", result.returncode)


# endregion Tests: CLI
