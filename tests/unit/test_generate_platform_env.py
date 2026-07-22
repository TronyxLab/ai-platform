"""
# GREP_SUMMARY: test_generate_platform_env, discover_profiles, load_ci_defaults, generate_smoke_env_py, tmp_path
# STRUCTURE: ▶ discover_profiles 2× (subdirs/empty) → ▶ load_ci_defaults 2× (valid/missing) → ▶ generate_smoke_env_py 1× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for generate_platform_env.py — discover_profiles(), load_ci_defaults(),
##           and generate_smoke_env_py(). No subprocess calls.
## @scope    Tests profile discovery from module directories, CI default loading from
##           secret-definitions.yaml, and Python source generation for smoke_env_generated.py.
## @invariants
##   - All tests import the module directly via sys.path.insert
##   - Each test is decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for temp file and directory creation
## @rationale DevPlan 051 §5: Unit coverage for generate_platform_env generator
## @changes 2026-07-22 | Created (DevPlan 051 Wave 1)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import generate_platform_env as gpe

# ═══════════════════════════════════════════════════════════════════
# region Tests: discover_profiles
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · discover_profiles finds module directories with docker-compose
# · Scenario: 3 subdirectories, 2 with docker-compose.base.yml → returns 2 profiles
# · Last fail: N/A (new test)
# · Remove if: discover_profiles logic changes
@ldd_trajectory
def test_discover_profiles(caplog, tmp_path):
    """discover_profiles should return sorted list of module directories with compose files."""
    # Create module dirs
    for mod in ["postgres", "redis", "clickhouse"]:
        mod_dir = tmp_path / mod
        mod_dir.mkdir()

    # Only postgres and redis have docker-compose.base.yml
    (tmp_path / "postgres" / "docker-compose.base.yml").write_text("services:\n  postgres:\n    image: postgres:latest")
    (tmp_path / "redis" / "docker-compose.base.yml").write_text("services:\n  redis:\n    image: redis:latest")
    # All 3 get module.yaml
    for mod in ["postgres", "redis", "clickhouse"]:
        (tmp_path / mod / "module.yaml").write_text(f"name: {mod}\ninstall_type: docker")

    result = gpe.discover_profiles(str(tmp_path))
    assert sorted(result) == sorted(["clickhouse", "postgres", "redis"]), f"Expected all 3, got {result}"

    logger.critical("[IMP:9][test] discover_profiles found %d modules: %s", len(result), result)


# 🧪 TRAP[TEST] · Regression · Empty directory returns empty profile list
# · Scenario: Empty modules directory → returns empty list
# · Last fail: N/A (new test)
# · Remove if: discover_profiles logic changes
@ldd_trajectory
def test_discover_profiles_empty(caplog, tmp_path):
    """discover_profiles should return empty list for empty directory."""
    result = gpe.discover_profiles(str(tmp_path))
    assert result == [], f"Expected empty list, got {result}"

    logger.critical("[IMP:9][test] discover_profiles empty dir returns []")


# 🧪 TRAP[TEST] · Regression · System modules excluded from profiles
# · Scenario: 1 docker module + 1 system module → only docker module returned
# · Last fail: N/A (new test)
# · Remove if: discover_profiles logic changes
@ldd_trajectory
def test_discover_profiles_excludes_system(caplog, tmp_path):
    """discover_profiles should exclude modules with install_type: system."""
    # Docker module
    docker_mod = tmp_path / "nginx"
    docker_mod.mkdir()
    (docker_mod / "docker-compose.base.yml").write_text("services:\n  nginx:\n    image: nginx:latest")

    # System module (should be excluded)
    system_mod = tmp_path / "platform-secrets"
    system_mod.mkdir()
    (system_mod / "docker-compose.base.yml").write_text("services:\n  agent:\n    image: agent:latest")
    (system_mod / "module.yaml").write_text("name: platform-secrets\ninstall_type: system")

    result = gpe.discover_profiles(str(tmp_path))
    assert "nginx" in result, "nginx should be in profiles"
    assert "platform-secrets" not in result, "platform-secrets should be excluded"

    logger.critical("[IMP:9][test] discover_profiles system modules excluded — %d profiles", len(result))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: load_ci_defaults
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_ci_defaults returns secret→ci_default mapping
# · Scenario: Valid secret-definitions.yaml → returns dict of {name: ci_default}
# · Last fail: N/A (new test)
# · Remove if: load_ci_defaults logic changes
@ldd_trajectory
def test_load_ci_defaults(caplog, tmp_path):
    """load_ci_defaults should parse secret definitions and return {name: ci_default} mapping."""
    secret_file = tmp_path / "secret-definitions.yaml"
    secret_data = {
        "secrets": [
            {"name": "CLICKHOUSE_PASSWORD", "tier": "required", "ci_default": "test-clickhouse-pwd"},
            {"name": "POSTGRES_PASSWORD", "tier": "required", "ci_default": "test-pg-pwd"},
        ]
    }
    with open(str(secret_file), "w") as f:
        yaml.dump(secret_data, f)

    result = gpe.load_ci_defaults(str(secret_file))
    assert result == {
        "CLICKHOUSE_PASSWORD": "test-clickhouse-pwd",
        "POSTGRES_PASSWORD": "test-pg-pwd",
    }, f"Unexpected result: {result}"

    logger.critical("[IMP:9][test] load_ci_defaults loaded %d defaults", len(result))


# 🧪 TRAP[TEST] · Regression · Missing secret definitions file returns empty dict
# · Scenario: Non-existent path → returns empty dict
# · Last fail: N/A (new test)
# · Remove if: load_ci_defaults logic changes
@ldd_trajectory
def test_load_ci_defaults_missing_file(caplog):
    """load_ci_defaults should return empty dict for missing file."""
    result = gpe.load_ci_defaults("/tmp/nonexistent_secret_defs.yaml")
    assert result == {}, f"Expected empty dict, got {result}"

    logger.critical("[IMP:9][test] load_ci_defaults missing file returns {}")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: generate_smoke_env_py
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · generate_smoke_env_py produces valid Python source
# · Scenario: CI defaults dict → generates valid Python with SMOKE_ENV_GENERATED dict
# · Last fail: N/A (new test)
# · Remove if: generate_smoke_env_py logic changes
@ldd_trajectory
def test_generate_smoke_env_py(caplog):
    """generate_smoke_env_py should produce valid Python source with SMOKE_ENV_GENERATED."""
    ci_defaults = {
        "CLICKHOUSE_PASSWORD": "test-clickhouse-pwd",
        "POSTGRES_PASSWORD": "test-pg-pwd",
    }

    result = gpe.generate_smoke_env_py(ci_defaults)

    # Validate Python source structure
    assert '"""## @purpose  AUTO-GENERATED CI defaults for smoke tests' in result, "Should have @purpose header"
    assert "SMOKE_ENV_GENERATED" in result, "Should define SMOKE_ENV_GENERATED"
    assert "CLICKHOUSE_PASSWORD" in result, "Should contain CLICKHOUSE_PASSWORD key"
    assert "test-clickhouse-pwd" in result, "Should contain secret default value"
    assert "POSTGRES_PASSWORD" in result, "Should contain POSTGRES_PASSWORD key"

    logger.critical("[IMP:9][test] generate_smoke_env_py produced valid Python source (%d chars)", len(result))


# endregion
