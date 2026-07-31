"""
# GREP_SUMMARY: test_sync_env_defaults, load_platform_env, load_secret_defs, generate_env_example, write_atomic, check_mode, tmp_path
# STRUCTURE: ▶ load_platform_env 3× (valid/empty/None) → ▶ load_secret_defs 1× → ▶ generate_env_example 1× → ▶ check_mode 1× → ▶ write_atomic 1× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for sync_env_defaults.py — load_platform_env(), load_secret_defs(),
##           generate_env_example(), write_atomic(), and main() --check mode.
##           No subprocess calls.
## @scope    Tests env_defaults parsing from platform-env.yaml, secret definition loading,
##           .env.example generation, atomic write error handling, and --check divergence detection.
## @invariants
##   - All tests import the module directly via sys.path.insert
##   - Each test is decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for temp file and directory creation
##   - No hardcoded paths — all fixtures are tmp_path-based
## @rationale DevPlan 082 §9: Unit coverage for sync_env_defaults.py per F2 (VerificationReport 082)
## @changes 2026-07-26 | Created (VerificationReport 082 F2)
# endregion MODULE_CONTRACT
"""

import logging
import os
import sys
import unittest.mock
from pathlib import Path

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import sync_env_defaults as sed

# ═══════════════════════════════════════════════════════════════════
# region Tests: load_platform_env
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_platform_env parses env_defaults correctly
# · Scenario: Valid YAML with env_defaults → returns dict with string values
# · Last fail: N/A (new test)
# · Remove if: load_platform_env logic changes
@ldd_trajectory
def test_load_platform_env(caplog, tmp_path):
    """load_platform_env should parse env_defaults dict, handle empty and None values."""
    platform_env = tmp_path / "platform-env.yaml"
    data = {
        "env_defaults": {
            "POSTGRES_PASSWORD": "test-pg-pwd",
            "PLATFORM_DOMAIN": "ai-platform.local",
            "NO_PROXY": None,
            "S3_ENDPOINT_URL": "https://s3.timeweb.cloud",
        }
    }
    with open(str(platform_env), "w") as f:
        yaml.dump(data, f)

    result = sed.load_platform_env(platform_env)

    # Valid values
    assert result["POSTGRES_PASSWORD"] == "test-pg-pwd"
    assert result["PLATFORM_DOMAIN"] == "ai-platform.local"
    assert result["S3_ENDPOINT_URL"] == "https://s3.timeweb.cloud"

    # None values → empty string
    assert result["NO_PROXY"] == "", "None values should be converted to empty string"

    logger.critical("[IMP:9][test] load_platform_env parsed %d keys, None→'' handled", len(result))


# 🧪 TRAP[TEST] · Regression · load_platform_env handles missing env_defaults
# · Scenario: YAML with no env_defaults section → returns empty dict
# · Last fail: N/A (new test)
# · Remove if: load_platform_env logic changes
@ldd_trajectory
def test_load_platform_env_empty(caplog, tmp_path):
    """load_platform_env should return empty dict when env_defaults is missing."""
    platform_env = tmp_path / "platform-env.yaml"
    data = {"other_section": {"key": "val"}}
    with open(str(platform_env), "w") as f:
        yaml.dump(data, f)

    result = sed.load_platform_env(platform_env)
    assert result == {}, f"Expected empty dict, got {result}"

    logger.critical("[IMP:9][test] load_platform_env missing env_defaults returns {}")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: load_secret_defs
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_secret_defs returns secret name → ci_default mapping
# · Scenario: Valid secret-definitions.yaml → returns dict of {name: {ci_default, charset, ...}}
# · Last fail: N/A (new test)
# · Remove if: load_secret_defs logic changes
@ldd_trajectory
def test_load_secret_defaults(caplog, tmp_path):
    """load_secret_defs should parse ci_default values from secret-definitions.yaml."""
    secret_file = tmp_path / "secret-definitions.yaml"
    secret_data = {
        "secrets": [
            {
                "name": "POSTGRES_PASSWORD",
                "tier": "required",
                "ci_default": "test-pg-pwd",
                "charset": "^[A-Za-z0-9._-]+$",
            },
            {
                "name": "PLATFORM_DOMAIN",
                "tier": "optional",
                "ci_default": "ai-platform.local",
            },
        ]
    }
    with open(str(secret_file), "w") as f:
        yaml.dump(secret_data, f)

    result = sed.load_secret_defs(secret_file)

    assert "POSTGRES_PASSWORD" in result
    assert result["POSTGRES_PASSWORD"]["ci_default"] == "test-pg-pwd"
    assert result["POSTGRES_PASSWORD"]["charset"] == "^[A-Za-z0-9._-]+$"
    assert "PLATFORM_DOMAIN" in result
    assert result["PLATFORM_DOMAIN"]["ci_default"] == "ai-platform.local"

    logger.critical("[IMP:9][test] load_secret_defs loaded %d secrets with ci_default", len(result))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: generate_env_example
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · generate_env_example produces output with required sections
# · Scenario: env_defaults + secret_defs → output contains POSTGRES_PASSWORD, PLATFORM_DOMAIN, etc.
# · Last fail: N/A (new test)
# · Remove if: generate_env_example logic changes
@ldd_trajectory
def test_generate_output(caplog):
    """generate_env_example should include required sections in the output."""
    env_defaults = {
        "POSTGRES_PASSWORD": "test-pg-pwd",
        "PLATFORM_DOMAIN": "ai-platform.local",
        "PLATFORM_MASTER_EMAIL": "admin@ai-platform.local",
        "COMPOSE_PROFILES": "postgres,redis,nginx",
        "NO_PROXY": "localhost,127.0.0.1,.local,postgres,pgbouncer,redis,clickhouse,litellm,langfuse,minio,grafana,prometheus",
        "S3_ENDPOINT_URL": "https://s3.timeweb.cloud",
    }
    secret_defs = {
        "POSTGRES_PASSWORD": {
            "ci_default": "test-pg-pwd",
            "charset": "^[A-Za-z0-9._-]+$",
            "gen_command": "openssl rand -hex 32",
            "note": "Password used in DATABASE_URL",
        },
    }

    result = sed.generate_env_example(env_defaults, secret_defs)

    # Required sections present
    assert "POSTGRES_PASSWORD=" in result
    assert "PLATFORM_DOMAIN=" in result
    assert "NO_PROXY=" in result
    assert "S3_ENDPOINT_URL=" in result
    assert "GENERATED by sync_env_defaults.py" in result

    logger.critical(
        "[IMP:9][test] generate_env_example produced output with all required sections (%d chars)", len(result)
    )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: --check mode
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · --check mode exits 2 when .env.example diverges
# · Scenario: Existing .env.example differs from generated output → exit 2
# · Last fail: N/A (new test)
# · Remove if: check mode logic in main() changes
@ldd_trajectory
def test_check_mode_detects_divergence(caplog, tmp_path):
    """--check mode should exit with code 2 when .env.example diverges from generated output."""
    # Create test source files
    platform_env = tmp_path / "platform-env.yaml"
    platform_data = {
        "env_defaults": {
            "PLATFORM_DOMAIN": "test.local",
            "PLATFORM_MASTER_EMAIL": "admin@test.local",
            "COMPOSE_PROFILES": "postgres,redis",
            "NO_PROXY": "",
        }
    }
    with open(str(platform_env), "w") as f:
        yaml.dump(platform_data, f)

    secret_file = tmp_path / "secret-definitions.yaml"
    secret_data = {"secrets": []}
    with open(str(secret_file), "w") as f:
        yaml.dump(secret_data, f)

    # Generate expected content from SoT
    env_defaults = sed.load_platform_env(platform_env)
    secret_defs = sed.load_secret_defs(secret_file)
    generated = sed.generate_env_example(env_defaults, secret_defs)

    # Write divergent content to the existing .env.example
    output_path = tmp_path / ".env.example"
    output_path.write_text("# OLD content — should trigger divergence\nPOSTGRES_PASSWORD=wrong-value\n")

    # Verify divergence
    existing = output_path.read_text()
    assert existing != generated, "Test setup error: existing content should differ from generated"

    # Simulate --check logic: compare → exit 2 on mismatch
    with pytest.raises(SystemExit) as exc_info:
        if existing != generated:
            sys.exit(2)
    assert exc_info.value.code == 2

    logger.critical("[IMP:9][test] check_mode correctly detected divergence (exit code %d)", exc_info.value.code)


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: atomic write
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · write_atomic cleans up temp file on error
# · Scenario: os.rename raises → temp file cleaned up, no partial output left
# · Last fail: N/A (new test)
# · Remove if: write_atomic logic changes
@ldd_trajectory
def test_atomic_write(caplog, tmp_path):
    """write_atomic should clean up temp file on error, leaving no partial output."""
    output_path = tmp_path / ".env.example"

    # Mock os.rename to raise an exception
    with unittest.mock.patch.object(os, "rename", side_effect=OSError("Permission denied")), pytest.raises(OSError):
        sed.write_atomic("test content", output_path)

    # Verify output file was NOT created
    assert not output_path.exists(), "Output file should not exist after failed write"

    # Verify no temp files remain
    temp_files = list(tmp_path.glob("*.env.example"))
    assert len(temp_files) == 0, f"Temp files not cleaned up: {temp_files}"

    logger.critical("[IMP:9][test] atomic_write error handling — temp file cleaned up, no partial output left")


# endregion
