"""
# GREP_SUMMARY: test_generate_secrets_manifest, compute_consumers, load_secret_definitions, generate-output, tmp_path
# STRUCTURE: ▶ compute_consumers 4× (multi-module/empty/no-module/no-env-requires) → ▶ load_secret_definitions 2× (valid/missing) → ▶ generate_output_structure 1× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for generate_secrets_manifest.py — compute_consumers() and load_secret_definitions().
## @scope    Tests consumer computation logic and YAML definition loading. No subprocess calls.
## @invariants
##   - All tests import the module directly via sys.path.insert
##   - Each test is decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for temp file creation where needed
## @rationale DevPlan 051 §5: Unit coverage for compute_consumers and load_secret_definitions
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
import generate_secrets_manifest as gsm

# ═══════════════════════════════════════════════════════════════════
# region Tests: compute_consumers
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · compute_consumers returns consumers across multiple modules
# · Scenario: 2 modules with shared + unique env_requires → correct consumer lists for both secrets
# · Last fail: N/A (new test)
# · Remove if: compute_consumers logic changes
@ldd_trajectory
def test_compute_consumers(caplog):
    """compute_consumers should return all modules that require a given secret."""
    modules = [
        {"name": "clickhouse", "env_requires": ["CLICKHOUSE_PASSWORD", "POSTGRES_PASSWORD"]},
        {"name": "langfuse", "env_requires": ["CLICKHOUSE_PASSWORD"]},
    ]

    clickhouse_consumers = gsm.compute_consumers("CLICKHOUSE_PASSWORD", modules)
    assert clickhouse_consumers == ["clickhouse", "langfuse"], (
        f"Expected [clickhouse, langfuse], got {clickhouse_consumers}"
    )

    postgres_consumers = gsm.compute_consumers("POSTGRES_PASSWORD", modules)
    assert postgres_consumers == ["clickhouse"], f"Expected [clickhouse], got {postgres_consumers}"

    logger.critical(
        "[IMP:9][test] compute_consumers multi-module correct — CLICKHOUSE_PASSWORD: %s, POSTGRES_PASSWORD: %s",
        clickhouse_consumers,
        postgres_consumers,
    )


# 🧪 TRAP[TEST] · Regression · Empty env_requires returns empty consumer list
# · Scenario: Module with empty env_requires → no consumers for any secret
# · Last fail: N/A (new test)
# · Remove if: compute_consumers logic changes
@ldd_trajectory
def test_empty_env_requires(caplog):
    """compute_consumers should return empty list for modules without env_requires."""
    modules = [{"name": "nginx", "env_requires": []}]

    result = gsm.compute_consumers("POSTGRES_PASSWORD", modules)
    assert result == [], f"Expected empty list, got {result}"

    logger.critical("[IMP:9][test] compute_consumers empty env_requires returns []")


# 🧪 TRAP[TEST] · Regression · No matching secret returns empty consumer list
# · Scenario: Modules with env_requires that don't include the queried secret → empty
# · Last fail: N/A (new test)
# · Remove if: compute_consumers logic changes
@ldd_trajectory
def test_no_matching_secret(caplog):
    """compute_consumers should return empty list if no module requires the given secret."""
    modules = [
        {"name": "clickhouse", "env_requires": ["CLICKHOUSE_PASSWORD"]},
        {"name": "nginx", "env_requires": ["NGINX_VAR"]},
    ]

    result = gsm.compute_consumers("POSTGRES_PASSWORD", modules)
    assert result == [], f"Expected empty list, got {result}"

    logger.critical("[IMP:9][test] compute_consumers no matching secret returns []")


# 🧪 TRAP[TEST] · Regression · Module without env_requires key returns empty
# · Scenario: Module dict missing env_requires key → treated as empty
# · Last fail: N/A (new test)
# · Remove if: compute_consumers logic changes
@ldd_trajectory
def test_missing_env_requires_key(caplog):
    """compute_consumers should handle modules without env_requires key gracefully."""
    modules = [
        {"name": "nginx"},
        {"name": "postgres", "env_requires": ["POSTGRES_PASSWORD"]},
    ]

    result = gsm.compute_consumers("POSTGRES_PASSWORD", modules)
    assert result == ["postgres"], f"Expected [postgres], got {result}"

    logger.critical("[IMP:9][test] compute_consumers handles missing env_requires key")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: load_secret_definitions
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_secret_definitions loads valid YAML
# · Scenario: tmp_path with valid secret-definitions.yaml → returns list of secret dicts
# · Last fail: N/A (new test)
# · Remove if: load_secret_definitions logic changes
@ldd_trajectory
def test_load_secret_definitions(caplog, tmp_path):
    """load_secret_definitions should parse valid YAML secret definition file."""
    secret_file = tmp_path / "secret-definitions.yaml"
    secret_data = {
        "secrets": [
            {"name": "CLICKHOUSE_PASSWORD", "tier": "required", "source": "sops", "ci_default": "test-pwd"},
            {"name": "POSTGRES_PASSWORD", "tier": "required", "source": "sops", "ci_default": "test-pg-pwd"},
        ]
    }
    with open(str(secret_file), "w") as f:
        yaml.dump(secret_data, f)

    result = gsm.load_secret_definitions(str(secret_file))
    assert len(result) == 2, f"Expected 2 secrets, got {len(result)}"
    assert result[0]["name"] == "CLICKHOUSE_PASSWORD"
    assert result[1]["name"] == "POSTGRES_PASSWORD"

    logger.critical("[IMP:9][test] load_secret_definitions loaded %d secrets", len(result))


# 🧪 TRAP[TEST] · Regression · Missing file returns empty list
# · Scenario: Non-existent path → returns empty list
# · Last fail: N/A (new test)
# · Remove if: load_secret_definitions logic changes
@ldd_trajectory
def test_load_secret_definitions_missing_file(caplog):
    """load_secret_definitions should return empty list for missing file."""
    result = gsm.load_secret_definitions("/tmp/nonexistent_secrets_file.yaml")
    assert result == [], f"Expected empty list, got {result}"

    logger.critical("[IMP:9][test] load_secret_definitions missing file returns []")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: generate_output_structure
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Generated output has correct structure with consumers
# · Scenario: Secret definitions + modules → generated output has secret entries with consumers
# · Last fail: N/A (new test)
# · Remove if: output structure logic changes
@ldd_trajectory
def test_generate_output_structure(caplog, tmp_path):
    """Generated secrets manifest should include consumers derived from module env_requires."""
    secret_defs = [
        {"name": "CLICKHOUSE_PASSWORD", "tier": "required", "source": "sops", "ci_default": "test-pwd"},
        {"name": "POSTGRES_PASSWORD", "tier": "required", "source": "sops", "ci_default": "test-pg-pwd"},
    ]
    modules = [
        {"name": "clickhouse", "env_requires": ["CLICKHOUSE_PASSWORD", "POSTGRES_PASSWORD"]},
        {"name": "langfuse", "env_requires": ["CLICKHOUSE_PASSWORD"]},
    ]

    output = gsm.generate(secret_defs, modules)
    assert "secrets" in output, "Output should have 'secrets' key"

    pwd_entry = None
    for s in output["secrets"]:
        if s["name"] == "CLICKHOUSE_PASSWORD":
            pwd_entry = s
            break
    assert pwd_entry is not None, "CLICKHOUSE_PASSWORD should be in output"
    assert "consumers" in pwd_entry, "Secret entry should have consumers"
    assert sorted(pwd_entry["consumers"]) == sorted(["clickhouse", "langfuse"]), (
        f"Expected consumers [clickhouse, langfuse], got {pwd_entry['consumers']}"
    )

    logger.critical("[IMP:9][test] generate_output structure valid — %d secrets", len(output["secrets"]))


# endregion
