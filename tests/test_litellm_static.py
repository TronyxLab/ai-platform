# GREP_SUMMARY: test-litellm-static config-schema healthcheck-sh module-yaml litellm-no-docker
# STRUCTURE: ○ test_litellm_config_has_required_keys[◈ litellm-config.yml YAML → ⊕ general_settings+model_list] → ○ test_litellm_model_list_non_empty[◈ model_list ≥ 1 entry] → ○ test_litellm_healthcheck_sh_exists[◈ healthcheck.sh executable] → ○ test_litellm_module_yaml_has_required_fields[◈ module.yaml → ⊕ name+install_type+env_requires]
# @file test_litellm_static.py
# @purpose  Static/schema tests for litellm module — validates config files without Docker
# @scope    Static audit tests; no Docker required. Run as part of `make test MARKER=static` or gate fast.
# @invariants
#   - All tests use @pytest.mark.static_audit marker
#   - No Docker dependency — tests are filesystem-only
#   - LDD trajectory printed before every assert
# @rationale  Created as part of wave-litellm reset (T5.5) — provides ≥1 static test per
#             DevPlan §Протокол модульной волны. Validates core config files for structural
#             correctness without needing Docker running.
#
# region MODULE_CONTRACT
## @purpose  — Static/schema tests for litellm module: validates litellm-config.yml,
##            healthcheck.sh, and module.yaml structure without Docker.
## @scope    — Static audit tests; no Docker required. Run as part of make test MARKER=static.
## @invariants
##   - All tests marked @pytest.mark.static_audit
##   - Filesystem-only — no Docker, no HTTP calls
##   - LDD trajectory (IMP:7-10) printed for each test
## @rationale — Per-module static test per DevPlan wave-litellm reset.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import stat

import pytest
import yaml
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

MODULES_DIR = os.path.join(os.path.dirname(__file__), "..", "core", "modules")
LITELLM_DIR = os.path.join(MODULES_DIR, "litellm")
CONFIG_PATH = os.path.join(LITELLM_DIR, "config", "litellm-config.yml")
HEALTHCHECK_PATH = os.path.join(LITELLM_DIR, "healthcheck.sh")
MODULE_YAML_PATH = os.path.join(LITELLM_DIR, "module.yaml")


# region TESTS


@pytest.mark.static_audit
@ldd_trajectory
def test_litellm_config_has_required_keys(caplog) -> None:
    """Validate litellm-config.yml has all required top-level keys.

    ## @purpose — litellm-config.yml is the LiteLLM proxy config. Missing keys
    ##            cause runtime failures (500 errors, model list missing).
    ## @io — ⇥ litellm-config.yml → ⚡ yaml.safe_load → ⊕ assert keys present → ⎋ None
    ## @complexity — O(1)
    """
    # region FUNC_test_litellm_config_has_required_keys

    assert os.path.exists(CONFIG_PATH), f"litellm-config.yml not found: {CONFIG_PATH}"
    logger.info("[IMP:7][test_litellm_static] Config found: %s", CONFIG_PATH)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    required_keys = ["general_settings", "model_list", "router_settings", "litellm_settings"]
    for key in required_keys:
        assert key in config, f"Missing required config key: {key}"
        logger.info("[IMP:8][test_litellm_static] Key present: %s", key)

    # Validate general_settings has master_key reference
    gs = config["general_settings"]
    assert "master_key" in gs, "general_settings missing master_key"
    assert "os.environ/LITELLM_MASTER_KEY" in str(gs["master_key"]), (
        f"master_key should reference os.environ/LITELLM_MASTER_KEY, got: {gs['master_key']}"
    )
    logger.info("[IMP:8][test_litellm_static] master_key references os.environ/LITELLM_MASTER_KEY")

    # Validate database_url
    assert "database_url" in gs, "general_settings missing database_url"
    assert "os.environ/DATABASE_URL" in str(gs["database_url"]), (
        f"database_url should reference os.environ/DATABASE_URL, got: {gs['database_url']}"
    )
    logger.info("[IMP:8][test_litellm_static] database_url references os.environ/DATABASE_URL")

    # Validate auth bypass for health
    assert gs.get("disable_auth_for_health_check") is True, (
        "general_settings.disable_auth_for_health_check must be True"
    )
    logger.info("[IMP:9][test_litellm_static] ✅ Config has all required keys with correct values")
    # endregion FUNC_test_litellm_config_has_required_keys


@pytest.mark.static_audit
@ldd_trajectory
def test_litellm_model_list_non_empty(caplog) -> None:
    """Validate litellm-config.yml has at least one model in model_list.

    ## @purpose — Empty model_list means LiteLLM has no models to serve.
    ##            /v1/models returns empty list, /chat/completions returns 404.
    ## @io — ⇥ litellm-config.yml → ⚡ yaml.safe_load → ⊕ assert model_list ≥ 1 → ⎋ None
    ## @complexity — O(1)
    """
    # region FUNC_test_litellm_model_list_non_empty

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    model_list = config.get("model_list", [])
    assert len(model_list) > 0, "model_list is empty — LiteLLM has no models configured"
    logger.info("[IMP:8][test_litellm_static] model_list has %d entries", len(model_list))

    # Each entry must have model_name and litellm_params.model
    for entry in model_list:
        assert "model_name" in entry, f"model_list entry missing model_name: {entry}"
        assert "litellm_params" in entry, f"model_list entry missing litellm_params: {entry}"
        assert "model" in entry["litellm_params"], (
            f"model_list entry {entry['model_name']} missing litellm_params.model"
        )
        logger.info(
            "[IMP:7][test_litellm_static] Model: %s → %s", entry["model_name"], entry["litellm_params"]["model"]
        )

    logger.info("[IMP:9][test_litellm_static] ✅ model_list validated: %d models", len(model_list))
    # endregion FUNC_test_litellm_model_list_non_empty


@pytest.mark.static_audit
@ldd_trajectory
def test_litellm_healthcheck_sh_exists(caplog) -> None:
    """Validate litellm/healthcheck.sh exists and is executable.

    ## @purpose — Missing or non-executable healthcheck.sh breaks module healthcheck
    ##            contract. Docker HEALTHCHECK uses check_docker_health which
    ##            depends on this script.
    ## @io — ⇥ healthcheck.sh → ⚡ os.stat → ⊕ assert executable → ⎋ None
    ## @complexity — O(1)
    """
    # region FUNC_test_litellm_healthcheck_sh_exists

    assert os.path.exists(HEALTHCHECK_PATH), f"healthcheck.sh not found: {HEALTHCHECK_PATH}"
    logger.info("[IMP:7][test_litellm_static] healthcheck.sh found: %s", HEALTHCHECK_PATH)

    st = os.stat(HEALTHCHECK_PATH)
    is_exec = bool(st.st_mode & stat.S_IXUSR)
    assert is_exec, f"healthcheck.sh is not executable: {HEALTHCHECK_PATH} (mode={oct(st.st_mode)})"
    logger.info("[IMP:8][test_litellm_static] healthcheck.sh is executable")

    # Verify it sources lib/healthcheck.sh
    with open(HEALTHCHECK_PATH) as f:
        content = f.read()
    assert "lib/healthcheck.sh" in content, "healthcheck.sh must source lib/healthcheck.sh"
    assert "check_docker_health" in content, "healthcheck.sh must use check_docker_health"
    logger.info("[IMP:9][test_litellm_static] ✅ healthcheck.sh valid and executable")
    # endregion FUNC_test_litellm_healthcheck_sh_exists


@pytest.mark.static_audit
@ldd_trajectory
def test_litellm_module_yaml_has_required_fields(caplog) -> None:
    """Validate litellm/module.yaml has required D4 contract fields.

    ## @purpose — module.yaml is the D4 contract for module metadata.
    ##            Missing fields break deploy-modules.sh and _topo_sort.py.
    ## @io — ⇥ module.yaml → ⚡ yaml.safe_load → ⊕ assert fields → ⎋ None
    ## @complexity — O(1)
    """
    # region FUNC_test_litellm_module_yaml_has_required_fields

    assert os.path.exists(MODULE_YAML_PATH), f"module.yaml not found: {MODULE_YAML_PATH}"
    logger.info("[IMP:7][test_litellm_static] module.yaml found: %s", MODULE_YAML_PATH)

    with open(MODULE_YAML_PATH) as f:
        mod = yaml.safe_load(f)

    assert mod.get("name") == "litellm", f"module name should be 'litellm', got: {mod.get('name')}"
    assert mod.get("install_type") == "docker", f"install_type should be 'docker', got: {mod.get('install_type')}"

    env_requires = mod.get("env_requires", [])
    assert "LITELLM_MASTER_KEY" in env_requires, "module.yaml env_requires must include LITELLM_MASTER_KEY"
    logger.info("[IMP:8][test_litellm_static] module.yaml env_requires: %s", env_requires)

    depends_on = mod.get("depends_on", [])
    assert "postgres" in depends_on, "module.yaml depends_on must include postgres"
    logger.info("[IMP:8][test_litellm_static] module.yaml depends_on: %s", depends_on)

    logger.info("[IMP:9][test_litellm_static] ✅ module.yaml validated successfully")
    # endregion FUNC_test_litellm_module_yaml_has_required_fields


# endregion TESTS
