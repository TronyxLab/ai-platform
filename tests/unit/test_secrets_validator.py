"""
# GREP_SUMMARY: test_secrets_validator, check-env, charset-validation, module-metadata, batch-metadata, transitive-deps, node-yaml-parser, detect-install-type
# STRUCTURE: ┌tmp_path + mock YAML stubs → ◇ [7 test functions] → ∋ _check_env_requires ⋙ ⊕ _validate_secret_charsets ⋙ ⊕ _get_module_severity ⋙ ⊕ _batch_module_metadata ⋙ ⊕ _expand_transitive_deps ⋙ ⊕ parse_modules_from_node_yaml ⋙ ⊕ detect_install_type → ⎋ LDD trajectory(IMP:7-10) assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for secrets_validator.py — all 7 public functions, tmp_path-based YAML fixtures, no external dependencies
## @scope    Direct Python import of secrets_validator module; tests each function in isolation with mock YAML files
## @invariants
##   - All tests use tmp_path for temp YAML files (no hardcoded paths)
##   - Each test includes caplog-based LDD trajectory [IMP:7-10] verification via ldd_trajectory decorator
##   - _expand_transitive_deps cycle handling verified via visited-set convergence (no infinite loop)
##   - File-not-found graceful degradation tested for every relevant function
## @rationale Tests mirror shell functions 1:1 — each function has dedicated tests for happy-path, edge-cases,
##            file-not-found, and type-variants. Compatible with Anti-Loop protocol via ldd_trajectory decorator.
## @changes
##   2026-07-22 · Created (W4-E1 secrets_validator)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

# Import the module under test
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"))
from secrets_validator import (
    _batch_module_metadata,
    _check_env_requires,
    _expand_transitive_deps,
    _get_module_severity,
    _validate_secret_charsets,
    detect_install_type,
    parse_modules_from_node_yaml,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

SAMPLE_SECRETS_MANIFEST = """version: 1
secrets:
  - name: POSTGRES_PASSWORD
    tier: required
    consumers: [postgres, litellm, backup-cron]
    charset: "^[A-Za-z0-9._-]+$"
  - name: POSTGRES_USER
    tier: required
    consumers: [postgres, infra-metrics]
  - name: MINIO_ROOT_USER
    tier: required
    consumers: [minio]
    charset: "^[A-Za-z0-9._-]+$"
  - name: MINIO_ROOT_PASSWORD
    tier: required
    consumers: [minio]
    charset: "^[A-Za-z0-9._-]+$"
  - name: LITELLM_MASTER_KEY
    tier: generated
    consumers: [litellm]
  - name: LANGFUSE_SECRET_KEY
    tier: generated
    consumers: [langfuse]
"""

SAMPLE_MODULE_YAML = """name: postgres
install_type: docker
severity: critical
depends_on:
  - postgres-data
  - platform-network
"""

SAMPLE_MODULE_YAML_WARN = """name: monitoring
install_type: docker
severity: warn
"""

SAMPLE_MODULE_YAML_NO_SEVERITY = """name: redis
install_type: docker
"""

SAMPLE_NODE_YAML_DICT = """modules:
  postgres:
    enabled: true
    config_overlay: overlay-v2
  redis:
    enabled: false
  monitoring: true
"""

SAMPLE_NODE_YAML_LIST = """modules:
  - name: postgres
    enabled: true
    config_overlay: overlay-v2
  - name: redis
    enabled: false
"""

SAMPLE_NODE_YAML_EMPTY = """modules: {}
"""

SAMPLE_MODULE_YAML_DEPS_A = """name: module-a
install_type: docker
depends_on:
  - module-b
  - module-c
"""

SAMPLE_MODULE_YAML_DEPS_B = """name: module-b
install_type: docker
depends_on:
  - module-d
"""

SAMPLE_MODULE_YAML_DEPS_C = """name: module-c
install_type: system
depends_on: []
"""

SAMPLE_MODULE_YAML_DEPS_D = """name: module-d
install_type: docker
"""

SAMPLE_MODULE_YAML_CYCLE_A = """name: cycle-a
install_type: docker
depends_on:
  - cycle-b
"""

SAMPLE_MODULE_YAML_CYCLE_B = """name: cycle-b
install_type: docker
depends_on:
  - cycle-c
"""

SAMPLE_MODULE_YAML_CYCLE_C = """name: cycle-c
install_type: docker
depends_on:
  - cycle-a
"""


# region FUNC_secrets_manifest_file
@pytest.fixture
def secrets_manifest_file(tmp_path):
    """Create a temporary secrets-manifest.yaml."""
    manifest = tmp_path / "secrets-manifest.yaml"
    manifest.write_text(SAMPLE_SECRETS_MANIFEST)
    return manifest


# endregion FUNC_secrets_manifest_file


# region FUNC_module_yaml_file
@pytest.fixture
def module_yaml_file(tmp_path):
    """Create a temporary module.yaml."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text(SAMPLE_MODULE_YAML)
    return yaml_file


# endregion FUNC_module_yaml_file


# region FUNC_node_yaml_dict_file
@pytest.fixture
def node_yaml_dict_file(tmp_path):
    """Create a temporary node.yaml (dict format)."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text(SAMPLE_NODE_YAML_DICT)
    return yaml_file


# endregion FUNC_node_yaml_dict_file


# region FUNC_node_yaml_list_file
@pytest.fixture
def node_yaml_list_file(tmp_path):
    """Create a temporary node.yaml (list format)."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text(SAMPLE_NODE_YAML_LIST)
    return yaml_file


# endregion FUNC_node_yaml_list_file


# region FUNC_node_yaml_empty_file
@pytest.fixture
def node_yaml_empty_file(tmp_path):
    """Create a temporary node.yaml (empty modules dict)."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text(SAMPLE_NODE_YAML_EMPTY)
    return yaml_file


# endregion FUNC_node_yaml_empty_file


# region FUNC_modules_dir_with_deps
@pytest.fixture
def modules_dir_with_deps(tmp_path):
    """Create a modules directory with module-a, module-b, module-c, module-d with depends_on DAG.

    DAG structure:
      module-a → [module-b, module-c]
      module-b → [module-d]
      module-c → []
      module-d → []
    """
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    def _write_module(name: str, content: str):
        mod_dir = modules_dir / name
        mod_dir.mkdir()
        (mod_dir / "module.yaml").write_text(content)

    _write_module("module-a", SAMPLE_MODULE_YAML_DEPS_A)
    _write_module("module-b", SAMPLE_MODULE_YAML_DEPS_B)
    _write_module("module-c", SAMPLE_MODULE_YAML_DEPS_C)
    _write_module("module-d", SAMPLE_MODULE_YAML_DEPS_D)

    return modules_dir


# endregion FUNC_modules_dir_with_deps


# region FUNC_modules_dir_with_cycle
@pytest.fixture
def modules_dir_with_cycle(tmp_path):
    """Create a modules directory with a circular dependency DAG (cycle-a → cycle-b → cycle-c → cycle-a)."""
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    def _write_module(name: str, content: str):
        mod_dir = modules_dir / name
        mod_dir.mkdir()
        (mod_dir / "module.yaml").write_text(content)

    _write_module("cycle-a", SAMPLE_MODULE_YAML_CYCLE_A)
    _write_module("cycle-b", SAMPLE_MODULE_YAML_CYCLE_B)
    _write_module("cycle-c", SAMPLE_MODULE_YAML_CYCLE_C)

    return modules_dir


# endregion FUNC_modules_dir_with_cycle


# ═══════════════════════════════════════════════════════════════════════════════
# Test: _check_env_requires
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_check_env_requires_all_present
## @purpose  All required env vars for postgres are set via os.environ — returns empty missing list
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _check_env_requires with all env vars set → empty missing list
# · Last fail: N/A · Remove if: _check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_all_present(secrets_manifest_file, caplog, monkeypatch):
    """All required env vars are set — should return empty missing list."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret123")
    monkeypatch.setenv("POSTGRES_USER", "postgres")

    missing = _check_env_requires("postgres", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for postgres: %s", missing)
    assert missing == [], f"Expected no missing vars, got: {missing}"


# endregion FUNC_test_check_env_requires_all_present


# region FUNC_test_check_env_requires_missing
## @purpose  Required env var is not set — returns list with the missing var name
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _check_env_requires with missing env var → returns missing list
# · Last fail: N/A · Remove if: _check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_missing(secrets_manifest_file, caplog, monkeypatch):
    """POSTGRES_PASSWORD not set — should return missing list with it."""
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    missing = _check_env_requires("postgres", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for postgres: %s", missing)
    assert "POSTGRES_PASSWORD" in missing, "Expected POSTGRES_PASSWORD to be missing"


# endregion FUNC_test_check_env_requires_missing


# region FUNC_test_check_env_requires_secrets_env_file
## @purpose  Secret is set via SECRETS_ENV_FILE (not os.environ) — should pass
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _check_env_requires reads from secrets.env file
# · Last fail: N/A · Remove if: _check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_secrets_env_file(secrets_manifest_file, caplog, monkeypatch, tmp_path):
    """Secret set in secrets.env file (not os.environ) — should pass."""
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("POSTGRES_PASSWORD=from_file\nPOSTGRES_USER=pgu\n")
    monkeypatch.setenv("SECRETS_ENV_FILE", str(secrets_env))

    missing = _check_env_requires("postgres", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for postgres: %s", missing)
    assert missing == [], f"Expected no missing vars (from file), got: {missing}"


# endregion FUNC_test_check_env_requires_secrets_env_file


# region FUNC_test_check_env_requires_generated_tier
## @purpose  Module with generated-tier secret checked — should require the var like required
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _check_env_requires checks generated-tier secrets too
# · Last fail: N/A · Remove if: _check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_generated_tier(secrets_manifest_file, caplog, monkeypatch):
    """Generated-tier secret should be checked like required."""
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pg-test-password")

    missing = _check_env_requires("litellm", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for litellm: %s", missing)
    assert missing == [], f"Expected no missing vars for litellm, got: {missing}"


# endregion FUNC_test_check_env_requires_generated_tier


# region FUNC_test_check_env_requires_no_manifest_found
## @purpose  Manifest file does not exist — STRICT mode raises FileNotFoundError
##           (graceful degradation removed, DevPlan 116 T4 / U-33 / invariant 7)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _check_env_requires with missing manifest → raises FileNotFoundError
# · Last fail: 2026-07-31 · Remove if: strict manifest reader is superseded
@ldd_trajectory
def test_check_env_requires_no_manifest_found(tmp_path, caplog):
    """Manifest not found — strict reader raises FileNotFoundError (fail-visible)."""
    with pytest.raises(FileNotFoundError):
        _check_env_requires("postgres", str(tmp_path / "nonexistent.yaml"))

    logger.info("[IMP:9][test][check_env] Missing manifest raises FileNotFoundError — OK")


# endregion FUNC_test_check_env_requires_no_manifest_found


# region FUNC_test_check_env_requires_no_consumers_match
## @purpose  Module with no matching consumers in manifest — returns empty list
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _check_env_requires with unmatched consumer → empty
# · Last fail: N/A · Remove if: _check_env_requires behavior changed
@ldd_trajectory
def test_check_env_requires_no_consumers_match(secrets_manifest_file, caplog):
    """Module not in any secret's consumers list — should return empty."""
    missing = _check_env_requires("nonexistent-module", str(secrets_manifest_file))

    logger.info("[IMP:9][test][check_env] Missing vars for nonexistent: %s", missing)
    assert missing == [], "Expected empty missing list for unmatched consumer"


# endregion FUNC_test_check_env_requires_no_consumers_match


# ═══════════════════════════════════════════════════════════════════════════════
# Test: _validate_secret_charsets
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_validate_secret_charsets_valid
## @purpose  All secrets with charset match their regex — returns (0, [])
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _validate_secret_charsets with valid charset values → zero failures
# · Last fail: N/A · Remove if: _validate_secret_charsets behavior changed
@ldd_trajectory
def test_validate_secret_charsets_valid(secrets_manifest_file, caplog, monkeypatch):
    """All charset values match — should return 0 failures."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "ValidPass.123")
    monkeypatch.setenv("MINIO_ROOT_USER", "admin_user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "Secure.Pass-123")

    failed, errors = _validate_secret_charsets(str(secrets_manifest_file))

    logger.info("[IMP:9][test][charset] Failed=%d, errors=%s", failed, errors)
    assert failed == 0, f"Expected 0 failures, got {failed}: {errors}"


# endregion FUNC_test_validate_secret_charsets_valid


# region FUNC_test_validate_secret_charsets_invalid
## @purpose  Secret value does not match charset regex — returns failure
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _validate_secret_charsets with invalid charset value → failure
# · Last fail: N/A · Remove if: _validate_secret_charsets behavior changed
@ldd_trajectory
def test_validate_secret_charsets_invalid(secrets_manifest_file, caplog, monkeypatch):
    """Secret value with invalid characters — should return 1 failure."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "invalid!!password@@")
    monkeypatch.setenv("MINIO_ROOT_USER", "admin_user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "Secure.Pass-123")

    failed, errors = _validate_secret_charsets(str(secrets_manifest_file))

    logger.info("[IMP:9][test][charset] Failed=%d, errors=%s", failed, errors)
    assert failed == 1, f"Expected 1 failure, got {failed}"
    assert any("POSTGRES_PASSWORD" in e for e in errors), "Expected POSTGRES_PASSWORD in errors"


# endregion FUNC_test_validate_secret_charsets_invalid


# region FUNC_test_validate_secret_charsets_no_charset_field
## @purpose  Secrets without charset field are skipped — no failures
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _validate_secret_charsets with no charset fields → zero failures
# · Last fail: N/A · Remove if: _validate_secret_charsets behavior changed
@ldd_trajectory
def test_validate_secret_charsets_no_charset_field(secrets_manifest_file, caplog, monkeypatch):
    """POSTGRES_USER has no charset — skip. MINIO_ROOT_USER matches — pass."""
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("MINIO_ROOT_USER", "admin_user")

    failed, errors = _validate_secret_charsets(str(secrets_manifest_file))

    logger.info("[IMP:9][test][charset] No-charset-field: Failed=%d, errors=%s", failed, errors)
    assert failed == 0, f"Expected 0 failures, got {failed}"


# endregion FUNC_test_validate_secret_charsets_no_charset_field


# region FUNC_test_validate_secret_charsets_manifest_missing
## @purpose  Manifest file not found — STRICT mode raises FileNotFoundError
##           (graceful degradation removed, DevPlan 116 T4 / U-33 / invariant 7)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _validate_secret_charsets with missing manifest → raises
# · Last fail: 2026-07-31 · Remove if: strict manifest reader is superseded
@ldd_trajectory
def test_validate_secret_charsets_manifest_missing(tmp_path, caplog):
    """Manifest not found — strict reader raises FileNotFoundError (fail-visible)."""
    with pytest.raises(FileNotFoundError):
        _validate_secret_charsets(str(tmp_path / "nonexistent.yaml"))

    logger.info("[IMP:9][test][charset] Missing manifest raises FileNotFoundError — OK")


# endregion FUNC_test_validate_secret_charsets_manifest_missing


# ═══════════════════════════════════════════════════════════════════════════════
# Test: _get_module_severity
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_get_module_severity_critical
## @purpose  module.yaml with severity: critical returns "critical"
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _get_module_severity returns critical from yaml
# · Last fail: N/A · Remove if: _get_module_severity behavior changed
@ldd_trajectory
def test_get_module_severity_critical(module_yaml_file, caplog):
    """severity: critical → returns 'critical'."""
    sev = _get_module_severity(str(module_yaml_file))
    logger.info("[IMP:9][test][severity] Value=%s", sev)
    assert sev == "critical", f"Expected 'critical', got {sev!r}"


# endregion FUNC_test_get_module_severity_critical


# region FUNC_test_get_module_severity_warn
## @purpose  module.yaml with severity: warn returns "warn"
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _get_module_severity returns warn from yaml
# · Last fail: N/A · Remove if: _get_module_severity behavior changed
@ldd_trajectory
def test_get_module_severity_warn(tmp_path, caplog):
    """severity: warn in module.yaml → returns 'warn'."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text(SAMPLE_MODULE_YAML_WARN)

    sev = _get_module_severity(str(yaml_file))
    logger.info("[IMP:9][test][severity] Value=%s", sev)
    assert sev == "warn", f"Expected 'warn', got {sev!r}"


# endregion FUNC_test_get_module_severity_warn


# region FUNC_test_get_module_severity_default
## @purpose  module.yaml without severity field defaults to "warn"
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _get_module_severity defaults to warn when absent
# · Last fail: N/A · Remove if: _get_module_severity behavior changed
@ldd_trajectory
def test_get_module_severity_default(tmp_path, caplog):
    """No severity field → defaults to 'warn'."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text(SAMPLE_MODULE_YAML_NO_SEVERITY)

    sev = _get_module_severity(str(yaml_file))
    logger.info("[IMP:9][test][severity] Default value=%s", sev)
    assert sev == "warn", f"Expected default 'warn', got {sev!r}"


# endregion FUNC_test_get_module_severity_default


# region FUNC_test_get_module_severity_file_not_found
## @purpose  module.yaml not found — returns "warn" (graceful degradation)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _get_module_severity with missing file → default warn
# · Last fail: N/A · Remove if: _get_module_severity behavior changed
@ldd_trajectory
def test_get_module_severity_file_not_found(tmp_path, caplog):
    """File not found → returns default 'warn'."""
    sev = _get_module_severity(str(tmp_path / "nonexistent.yaml"))
    logger.info("[IMP:9][test][severity] Not-found default=%s", sev)
    assert sev == "warn"


# endregion FUNC_test_get_module_severity_file_not_found


# ═══════════════════════════════════════════════════════════════════════════════
# Test: _batch_module_metadata
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_batch_module_metadata_multiple
## @purpose  Multiple module.yaml files with different severities and install_types
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _batch_module_metadata collects N modules correctly
# · Last fail: N/A · Remove if: _batch_module_metadata behavior changed
@ldd_trajectory
def test_batch_module_metadata_multiple(tmp_path, caplog):
    """Multiple modules with different metadata — collected correctly."""
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    # module-a: postgres (docker/critical)
    (modules_dir / "mod-a").mkdir()
    (modules_dir / "mod-a" / "module.yaml").write_text("name: postgres\ninstall_type: docker\nseverity: critical\n")
    # module-b: monitoring (docker/warn — explicit)
    (modules_dir / "mod-b").mkdir()
    (modules_dir / "mod-b" / "module.yaml").write_text("name: monitoring\ninstall_type: docker\nseverity: warn\n")
    # module-c: redis (docker/no severity — default warn)
    (modules_dir / "mod-c").mkdir()
    (modules_dir / "mod-c" / "module.yaml").write_text("name: redis\ninstall_type: docker\n")

    metadata = _batch_module_metadata(str(modules_dir))

    logger.info("[IMP:9][test][batch] Collected %d modules", len(metadata))
    assert len(metadata) == 3, f"Expected 3 modules, got {len(metadata)}"

    mod_map = {m["name"]: m for m in metadata}
    assert mod_map["postgres"]["install_type"] == "docker"
    assert mod_map["postgres"]["severity"] == "critical"
    assert mod_map["monitoring"]["severity"] == "warn"
    assert mod_map["redis"]["severity"] == "warn"
    assert mod_map["redis"]["install_type"] == "docker"


# endregion FUNC_test_batch_module_metadata_multiple


# region FUNC_test_batch_module_metadata_empty_dir
## @purpose  No module.yaml files found — returns empty list
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _batch_module_metadata with empty modules dir → []
# · Last fail: N/A · Remove if: _batch_module_metadata behavior changed
@ldd_trajectory
def test_batch_module_metadata_empty_dir(tmp_path, caplog):
    """No module.yaml files → returns []."""
    modules_dir = tmp_path / "empty_modules"
    modules_dir.mkdir()

    metadata = _batch_module_metadata(str(modules_dir))

    logger.info("[IMP:9][test][batch] Empty dir returned %d items", len(metadata))
    assert metadata == [], "Expected empty list for empty modules dir"


# endregion FUNC_test_batch_module_metadata_empty_dir


# region FUNC_test_batch_module_metadata_name_from_dir
## @purpose  module.yaml without name field uses parent directory name
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _batch_module_metadata falls back to dir name when name absent
# · Last fail: N/A · Remove if: _batch_module_metadata behavior changed
@ldd_trajectory
def test_batch_module_metadata_name_from_dir(tmp_path, caplog):
    """Missing name in module.yaml → uses directory name."""
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "my-custom-module").mkdir()
    (modules_dir / "my-custom-module" / "module.yaml").write_text("install_type: system\n")

    metadata = _batch_module_metadata(str(modules_dir))

    logger.info("[IMP:9][test][batch] Name-from-dir: %s", metadata)
    assert len(metadata) == 1
    assert metadata[0]["name"] == "my-custom-module"
    assert metadata[0]["install_type"] == "system"


# endregion FUNC_test_batch_module_metadata_name_from_dir


# ═══════════════════════════════════════════════════════════════════════════════
# Test: _expand_transitive_deps
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_expand_transitive_deps_basic
## @purpose  module-a expands to [module-a, module-b, module-c, module-d] via BFS
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps resolves 3-step transitive deps
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
@ldd_trajectory
def test_expand_transitive_deps_basic(modules_dir_with_deps, caplog):
    """module-a depends on module-b and module-c; module-b depends on module-d.

    Expected expanded: module-a, module-b, module-c, module-d (sorted).
    """
    result = _expand_transitive_deps("module-a", str(modules_dir_with_deps))

    expanded = result.split()
    logger.info("[IMP:9][test][deps] Expanded: %s", expanded)
    assert "module-a" in expanded
    assert "module-b" in expanded
    assert "module-c" in expanded
    assert "module-d" in expanded
    assert len(expanded) == 4, f"Expected 4 modules, got: {expanded}"


# endregion FUNC_test_expand_transitive_deps_basic


# region FUNC_test_expand_transitive_deps_multiple_seeds
## @purpose  Multiple seed modules expand to union of transitive closures
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with multiple seeds → union
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
@ldd_trajectory
def test_expand_transitive_deps_multiple_seeds(modules_dir_with_deps, caplog):
    """Seed with module-b (depends on module-d) and module-c (no deps)."""
    result = _expand_transitive_deps("module-b,module-c", str(modules_dir_with_deps))

    expanded = result.split()
    logger.info("[IMP:9][test][deps] Multiple seed expanded: %s", expanded)
    assert "module-b" in expanded
    assert "module-c" in expanded
    assert "module-d" in expanded
    assert "module-a" not in expanded, "module-a should NOT be in closure"


# endregion FUNC_test_expand_transitive_deps_multiple_seeds


# region FUNC_test_expand_transitive_deps_no_deps
## @purpose  Seed module with no depends_on — returns just itself
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with no-deps module → singleton
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
@ldd_trajectory
def test_expand_transitive_deps_no_deps(modules_dir_with_deps, caplog):
    """module-d has no depends_on — returns just module-d."""
    result = _expand_transitive_deps("module-d", str(modules_dir_with_deps))

    expanded = result.split()
    logger.info("[IMP:9][test][deps] No-deps module expanded: %s", expanded)
    assert expanded == ["module-d"]


# endregion FUNC_test_expand_transitive_deps_no_deps


# region FUNC_test_expand_transitive_deps_cycle_handling
## @purpose  Circular dependency does not cause infinite loop — visited set converges
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with cycle → converges (no infinite loop)
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
@ldd_trajectory
def test_expand_transitive_deps_cycle_handling(modules_dir_with_cycle, caplog):
    """cycle-a → cycle-b → cycle-c → cycle-a — BFS must converge without infinite loop."""
    result = _expand_transitive_deps("cycle-a", str(modules_dir_with_cycle))

    expanded = result.split()
    logger.info("[IMP:9][test][deps] Cycle expanded: %s", expanded)
    assert "cycle-a" in expanded
    assert "cycle-b" in expanded
    assert "cycle-c" in expanded
    assert len(expanded) == 3, f"Expected 3 modules in cycle closure, got: {expanded}"


# endregion FUNC_test_expand_transitive_deps_cycle_handling


# region FUNC_test_expand_transitive_deps_unknown_seed
## @purpose  Unknown seed module causes sys.exit(1) with error on stderr
## @complexity 2
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with unknown module → exit(1) + stderr
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
@ldd_trajectory
def test_expand_transitive_deps_unknown_seed(modules_dir_with_deps, caplog):
    """Unknown seed module 'ghost' — should sys.exit(1) with ERROR on stderr."""
    with pytest.raises(SystemExit) as exc_info:
        _expand_transitive_deps("ghost", str(modules_dir_with_deps))

    logger.info("[IMP:9][test][deps] Unknown seed exit code=%s", exc_info.value.code)
    assert exc_info.value.code == 1, "Expected exit code 1 for unknown module"


# endregion FUNC_test_expand_transitive_deps_unknown_seed


# region FUNC_test_expand_transitive_deps_empty_filter
## @purpose  Empty filter string returns empty string
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _expand_transitive_deps with empty filter → ""
# · Last fail: N/A · Remove if: _expand_transitive_deps behavior changed
@ldd_trajectory
def test_expand_transitive_deps_empty_filter(modules_dir_with_deps, caplog):
    """Empty filter → returns empty string."""
    result = _expand_transitive_deps("", str(modules_dir_with_deps))
    logger.info("[IMP:9][test][deps] Empty filter result=%r", result)
    assert result == ""


# endregion FUNC_test_expand_transitive_deps_empty_filter


# ═══════════════════════════════════════════════════════════════════════════════
# Test: parse_modules_from_node_yaml
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_parse_modules_dict_format
## @purpose  node.yaml with dict-format modules section parses correctly
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: parse_modules_from_node_yaml with dict format → 3 tuples
# · Last fail: N/A · Remove if: parse_modules_from_node_yaml behavior changed
@ldd_trajectory
def test_parse_modules_dict_format(node_yaml_dict_file, caplog):
    """Dict-format modules → returns 3 (name, enabled, overlay) tuples."""
    modules = parse_modules_from_node_yaml(str(node_yaml_dict_file))

    logger.info("[IMP:9][test][node-yaml] Dict-format parsed %d modules", len(modules))
    assert len(modules) == 3, f"Expected 3 modules, got {len(modules)}"

    mod_map = {m[0]: m for m in modules}
    assert mod_map["postgres"][1] == "true"
    assert mod_map["postgres"][2] == "overlay-v2"
    assert mod_map["redis"][1] == "false"
    assert mod_map["redis"][2] == ""
    assert mod_map["monitoring"][1] == "true"
    assert mod_map["monitoring"][2] == ""


# endregion FUNC_test_parse_modules_dict_format


# region FUNC_test_parse_modules_list_format
## @purpose  node.yaml with list-format modules section parses correctly
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: parse_modules_from_node_yaml with list format → tuples
# · Last fail: N/A · Remove if: parse_modules_from_node_yaml behavior changed
@ldd_trajectory
def test_parse_modules_list_format(node_yaml_list_file, caplog):
    """List-format modules → returns (name, enabled, overlay) tuples."""
    modules = parse_modules_from_node_yaml(str(node_yaml_list_file))

    logger.info("[IMP:9][test][node-yaml] List-format parsed %d modules", len(modules))
    assert len(modules) == 2, f"Expected 2 modules, got {len(modules)}"

    mod_map = {m[0]: m for m in modules}
    assert mod_map["postgres"][1] == "true"
    assert mod_map["postgres"][2] == "overlay-v2"
    assert mod_map["redis"][1] == "false"
    assert mod_map["redis"][2] == ""


# endregion FUNC_test_parse_modules_list_format


# region FUNC_test_parse_modules_empty
## @purpose  Empty modules section returns empty list
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: parse_modules_from_node_yaml with empty modules → []
# · Last fail: N/A · Remove if: parse_modules_from_node_yaml behavior changed
@ldd_trajectory
def test_parse_modules_empty(node_yaml_empty_file, caplog):
    """Empty modules dict → returns []."""
    modules = parse_modules_from_node_yaml(str(node_yaml_empty_file))

    logger.info("[IMP:9][test][node-yaml] Empty modules=%s", modules)
    assert modules == [], "Expected empty list for empty modules dict"


# endregion FUNC_test_parse_modules_empty


# region FUNC_test_parse_modules_file_not_found
## @purpose  node.yaml not found — graceful degradation returns []
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: parse_modules_from_node_yaml with missing file → []
# · Last fail: N/A · Remove if: parse_modules_from_node_yaml behavior changed
@ldd_trajectory
def test_parse_modules_file_not_found(tmp_path, caplog):
    """node.yaml not found → returns []."""
    modules = parse_modules_from_node_yaml(str(tmp_path / "nonexistent.yaml"))

    logger.info("[IMP:9][test][node-yaml] Missing file=%s", modules)
    assert modules == [], "Expected empty list when file not found"


# endregion FUNC_test_parse_modules_file_not_found


# ═══════════════════════════════════════════════════════════════════════════════
# Test: detect_install_type
# ═══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_detect_install_type_docker
## @purpose  module.yaml with install_type: docker returns "docker"
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: detect_install_type returns docker
# · Last fail: N/A · Remove if: detect_install_type behavior changed
@ldd_trajectory
def test_detect_install_type_docker(module_yaml_file, caplog):
    """install_type: docker in module.yaml."""
    itype = detect_install_type(str(module_yaml_file))
    logger.info("[IMP:9][test][detect] install_type=%s", itype)
    assert itype == "docker"


# endregion FUNC_test_detect_install_type_docker


# region FUNC_test_detect_install_type_system
## @purpose  module.yaml with install_type: system returns "system"
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: detect_install_type returns system
# · Last fail: N/A · Remove if: detect_install_type behavior changed
@ldd_trajectory
def test_detect_install_type_system(tmp_path, caplog):
    """install_type: system in module.yaml."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text("name: nginx\ninstall_type: system\n")

    itype = detect_install_type(str(yaml_file))
    logger.info("[IMP:9][test][detect] install_type=%s", itype)
    assert itype == "system"


# endregion FUNC_test_detect_install_type_system


# region FUNC_test_detect_install_type_default
## @purpose  module.yaml without install_type field defaults to "unknown"
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: detect_install_type defaults to unknown
# · Last fail: N/A · Remove if: detect_install_type behavior changed
@ldd_trajectory
def test_detect_install_type_default(tmp_path, caplog):
    """No install_type field → defaults to 'unknown'."""
    yaml_file = tmp_path / "module.yaml"
    yaml_file.write_text("name: custom\n")

    itype = detect_install_type(str(yaml_file))
    logger.info("[IMP:9][test][detect] Default install_type=%s", itype)
    assert itype == "unknown"


# endregion FUNC_test_detect_install_type_default


# region FUNC_test_detect_install_type_file_not_found
## @purpose  module.yaml not found — returns "unknown" (graceful degradation)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: detect_install_type with missing file → unknown
# · Last fail: N/A · Remove if: detect_install_type behavior changed
@ldd_trajectory
def test_detect_install_type_file_not_found(tmp_path, caplog):
    """File not found → returns 'unknown'."""
    itype = detect_install_type(str(tmp_path / "nonexistent.yaml"))
    logger.info("[IMP:9][test][detect] Not found default=%s", itype)
    assert itype == "unknown"


# endregion FUNC_test_detect_install_type_file_not_found
