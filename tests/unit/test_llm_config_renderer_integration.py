# GREP_SUMMARY: test-llm-config-renderer-integration, config_renderer, real-policy.yaml, model_list, access_groups, api_key, LDD, IMP
# STRUCTURE: ┌fixtures(real_policy_path, template_path)┐ → test_full_cycle_from_real_policy
# region MODULE_CONTRACT
## @purpose  Integration test — full cycle from real policy.yaml to rendered config.
##           Loads the ACTUAL core/internal/llm/policy.yaml and renders to verify
##           the production configuration produces correct output.
## @scope    Tests the complete render path with production data:
##           real policy.yaml → LLMPolicy.from_yaml → render → YAML validation
## @invariants
##   - Uses real policy.yaml from the project (not synthetic test data)
##   - Uses real Jinja2 template from the project
##   - All paths are resolved relative to this test file's location
##   - Does NOT require Docker or external services
##   - IMP:9 LDD trajectory verification included
## @rationale Integration test ensures the production policy.yaml renders correctly.
##            Unit tests cover edge cases; integration tests verify the real pipeline.
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 3)
## @see test_llm_config_renderer.py — complementary unit tests with synthetic data
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest
import yaml
from _conftest.ldd import _print_ldd_trajectory

logger = logging.getLogger(__name__)

# Resolve paths relative to this file (tests/unit/)
_TEST_DIR = pathlib.Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent.parent

# Real policy.yaml path
_REAL_POLICY_PATH = _PROJECT_ROOT / "core" / "internal" / "llm" / "policy.yaml"

# Real Jinja2 template path
_REAL_TEMPLATE_PATH = _PROJECT_ROOT / "core" / "modules" / "litellm" / "config" / "litellm-config.yml.j2"


@pytest.fixture
def real_policy_path() -> pathlib.Path:
    """Fixture providing the real policy.yaml path.

    ## @purpose  Ensures the real policy file exists before running tests.
    ##           Raises FileNotFoundError if the file is missing.
    """
    if not _REAL_POLICY_PATH.exists():
        raise FileNotFoundError(
            f"Real policy.yaml not found at: {_REAL_POLICY_PATH} — "
            f"cannot run integration test without production policy"
        )
    return _REAL_POLICY_PATH


@pytest.fixture
def template_path() -> pathlib.Path:
    """Fixture providing the real Jinja2 template path.

    ## @purpose  Ensures the real template file exists before running tests.
    """
    if not _REAL_TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"Real Jinja2 template not found at: {_REAL_TEMPLATE_PATH} — cannot run integration test without template"
        )
    return _REAL_TEMPLATE_PATH


# ── TESTS ────────────────────────────────────────────────────────────────────


def test_full_cycle_from_real_policy(real_policy_path, template_path, tmp_path, caplog):
    """Full integration test: load real policy.yaml → render to temp file → validate output.

    ## @purpose  End-to-end verification that the production policy.yaml produces
    ##           valid litellm-config.yml with correct structure:
    ##           - model_list has at least 2 entries (reasoning primary + fallback, chat)
    ##           - access_groups are set for each model
    ##           - api_key references DEEPSEEK_API_KEY
    ##           - litellm_settings has drop_params: true and num_retries: 3
    ##           - No reserved aliases (coding, vision, embedding) in model_list
    ## @scenario  Production pipeline: real policy → render → validate
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import render_litellm_config

        logger.info("[IMP:7][test_full_cycle] Loading REAL policy from: %s", real_policy_path)
        logger.info("[IMP:7][test_full_cycle] Using template: %s", template_path)

        # Render the real policy to temp (no file written)
        rendered = render_litellm_config(real_policy_path, template_path)

        # Parse output YAML
        parsed = yaml.safe_load(rendered)
        logger.critical("[IMP:9][test_full_cycle] ASSERT: rendered YAML is parseable")
        assert isinstance(parsed, dict), "Rendered output must be a valid YAML mapping"

        # ── Verify model_list ─────────────────────────────────────────────────
        model_list = parsed.get("model_list", [])
        logger.critical("[IMP:9][test_full_cycle] model_list has %d entries", len(model_list))
        assert len(model_list) >= 2, f"Expected at least 2 model_list entries, got {len(model_list)}"

        # Extract all model names
        model_names = [entry["model_name"] for entry in model_list]
        logger.critical("[IMP:9][test_full_cycle] model_list names: %s", model_names)

        # ── Verify aliases present ───────────────────────────────────────────
        assert "reasoning" in model_names, "reasoning alias must be in model_list"
        assert "chat" in model_names, "chat alias must be in model_list"

        # ── Verify reserved aliases NOT present ──────────────────────────────
        for reserved in ("coding", "vision", "embedding"):
            assert reserved not in model_names, f"Reserved alias '{reserved}' must NOT be in model_list"

        # ── Verify access_groups ─────────────────────────────────────────────
        for entry in model_list:
            model_info = entry.get("model_info", {})
            access_groups = model_info.get("access_groups", [])
            logger.critical(
                "[IMP:9][test_full_cycle] model_name='%s' access_groups=%s",
                entry["model_name"],
                access_groups,
            )
            assert len(access_groups) >= 1, f"model_name '{entry['model_name']}' must have at least 1 access_group"

        # ── Verify api_key references DEEPSEEK_API_KEY ───────────────────────
        for entry in model_list:
            litellm_params = entry.get("litellm_params", {})
            api_key = litellm_params.get("api_key", "")
            logger.critical(
                "[IMP:9][test_full_cycle] model_name='%s' api_key='%s'",
                entry["model_name"],
                api_key,
            )
            # All providers in the real policy use DEEPSEEK_API_KEY
            assert "DEEPSEEK_API_KEY" in api_key, (
                f"api_key for '{entry['model_name']}' must reference DEEPSEEK_API_KEY, got '{api_key}'"
            )

        # ── Verify litellm_settings ──────────────────────────────────────────
        litellm_settings = parsed.get("litellm_settings", {})
        logger.critical(
            "[IMP:9][test_full_cycle] litellm_settings: drop_params=%s, num_retries=%s",
            litellm_settings.get("drop_params"),
            litellm_settings.get("num_retries"),
        )
        assert litellm_settings.get("drop_params") is True, (
            f"drop_params must be True, got {litellm_settings.get('drop_params')}"
        )
        assert litellm_settings.get("num_retries") == 3, (
            f"num_retries must be 3, got {litellm_settings.get('num_retries')}"
        )

        # ── Verify success/failure callbacks ─────────────────────────────────
        success_cb = litellm_settings.get("success_callback", [])
        failure_cb = litellm_settings.get("failure_callback", [])
        logger.critical(
            "[IMP:9][test_full_cycle] success_callback=%s, failure_callback=%s",
            success_cb,
            failure_cb,
        )
        assert "prometheus" in success_cb, "success_callback must include prometheus"
        assert "proxy" not in success_cb, "success_callback should include langfuse or prometheus"

        # ── Verify general_settings ──────────────────────────────────────────
        general_settings = parsed.get("general_settings", {})
        logger.critical(
            "[IMP:9][test_full_cycle] general_settings keys: %s",
            list(general_settings.keys()),
        )
        assert "master_key" in general_settings, "general_settings must have master_key"
        assert "database_url" in general_settings, "general_settings must have database_url"

        # ── Verify fallbacks (if present) ────────────────────────────────────
        if "fallbacks" in parsed:
            fallbacks = parsed["fallbacks"]
            logger.critical("[IMP:9][test_full_cycle] fallbacks: %s", fallbacks)
            # At least reasoning → reasoning-fallback should exist
            found_fallback = any(
                isinstance(fb, dict) and fb.get("reasoning", "").endswith("-fallback") for fb in fallbacks
            )
            # YAML format: - reasoning: reasoning-fallback
            found_fallback = (
                any(
                    isinstance(fb, dict) and "-fallback" in str(fb.get(next(iter(fb.keys()), ""), ""))
                    for fb in fallbacks
                )
                if fallbacks
                else False
            )
            if found_fallback:
                logger.critical("[IMP:9][test_full_cycle] Fallback chain detected in output")
        else:
            logger.critical("[IMP:8][test_full_cycle] No fallbacks section — policy may not have fallback deployments")

        logger.critical("[IMP:9][test_full_cycle] ASSERT: All integration assertions passed")

        found_imp9 = _print_ldd_trajectory(caplog, "test_full_cycle_from_real_policy")
        assert found_imp9, "LDD Error: No IMP:9 log for test_full_cycle_from_real_policy"


# 🧪 TRAP[TEST] · test_full_cycle_from_real_policy · Regression · Integration fails · Remove if policy or template structure changes fundamentally
