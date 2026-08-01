# GREP_SUMMARY: test-llm-config-renderer, config_renderer, policy.yaml, Jinja2, model_list, fallbacks, --check, LDD, IMP
# STRUCTURE: fixtures(minimal_policy_path, full_policy_path, template_path) →
#            test_render_minimal_policy → test_model_list_contains_active_aliases →
#            test_reserved_aliases_not_in_model_list → test_fallback_chain_in_output →
#            test_api_key_from_provider → test_check_mode_stale → test_check_mode_fresh →
#            test_drop_params_enabled → test_invalid_policy_raises
# region MODULE_CONTRACT
## @purpose  Unit tests for config_renderer.py: policy → litellm-config.yml rendering.
## @scope    Tests all render paths: model_list generation, fallback chains, api_key resolution,
##           reserved alias exclusion, --check dry-run mode, invalid policy rejection.
## @invariants
##   - All tests use tmp_path fixture (no hardcoded paths)
##   - Policy YAML is written inline or from test_data to temp dirs
##   - Each test includes LDD trajectory printing with IMP:7-10 log levels
##   - At least one IMP:9 log per successful test (§TESTING)
## @rationale  Unit tests verify the renderer logic independently of the real policy.yaml.
##             Separate integration tests (test_llm_config_renderer_integration) use the real policy.
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 3)
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest
import yaml
from _conftest.ldd import _print_ldd_trajectory

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_policy(policy_data: dict, tmp_path: pathlib.Path, name: str = "policy.yaml") -> pathlib.Path:
    """Write a policy YAML dict to a temp file and return the path.

    ## @purpose  Helper to create temporary policy.yaml files for tests without
    ##           needing static fixtures. Uses yaml.dump for proper YAML formatting.
    ## @complexity O(N) where N = YAML tree size
    """
    p = tmp_path / name
    with open(p, "w") as f:
        yaml.dump(policy_data, f)
    return p


def _write_template(tmp_path: pathlib.Path, name: str = "litellm-config.yml.j2") -> pathlib.Path:
    """Write the Jinja2 template to a temp dir and return the path.

    ## @purpose  Creates a copy of the real template in tmp_path so tests
    ##           don't depend on file system location.
    ## @complexity O(1) — reads one file, writes one file
    """
    # Load the real template from the project
    template_rel = pathlib.Path("core") / "modules" / "litellm" / "config" / "litellm-config.yml.j2"
    src = pathlib.Path(__file__).resolve().parent.parent.parent / template_rel
    if not src.exists():
        raise FileNotFoundError(f"Template not found: {src}")
    dst = tmp_path / name
    dst.write_text(src.read_text())
    return dst


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def minimal_policy_data() -> dict:
    """Minimal valid policy with reasoning (primary + fallback) and chat (primary only).

    ## @purpose  Minimal policy that exercises all rendering features:
    ##           primary + fallback deployments, reserved aliases, api_key resolution.
    """
    return {
        "providers": {
            "deepseek": {"key_env": "DEEPSEEK_API_KEY"},
        },
        "aliases": {
            "reasoning": {
                "label": "Complex reasoning",
                "context_window": 128000,
                "features": ["reasoning", "structured_output"],
                "deployments": {
                    "primary": {"provider": "deepseek", "model": "deepseek/deepseek-v4-pro"},
                    "fallback": {"provider": "deepseek", "model": "deepseek/deepseek-v4-flash"},
                },
            },
            "chat": {
                "label": "Fast chat",
                "context_window": 128000,
                "features": ["chat"],
                "deployments": {
                    "primary": {"provider": "deepseek", "model": "deepseek/deepseek-v4-flash"},
                },
            },
            "coding": {
                "label": "Code generation",
                "context_window": 200000,
                "features": ["code", "structured_output"],
                "deployments": [],
            },
            "vision": {
                "label": "Image analysis",
                "context_window": 128000,
                "features": ["vision", "multimodal"],
                "deployments": [],
            },
            "embedding": {
                "label": "Text embeddings",
                "features": ["embedding"],
                "deployments": [],
            },
        },
        "profiles": {
            "default": {
                "label": "Default (chat only)",
                "models": ["chat"],
                "budget": {"daily": 1.0},
                "rpm_limit": 10,
                "metadata": {"tier": "default"},
            },
        },
        "auto_provision": {
            "default_profile": "default",
            "profile_rules": [],
        },
    }


@pytest.fixture
def minimal_policy_path(minimal_policy_data, tmp_path) -> pathlib.Path:
    """Write minimal policy to temp dir and return the path."""
    return _write_policy(minimal_policy_data, tmp_path)


@pytest.fixture
def template_path(tmp_path) -> pathlib.Path:
    """Create a copy of the real Jinja2 template in a temp dir."""
    return _write_template(tmp_path)


# ── TESTS ────────────────────────────────────────────────────────────────────


def test_render_minimal_policy(minimal_policy_path, template_path, caplog):
    """Render from valid minimal policy — verify output YAML is parseable.

    ## @purpose  Basic render test: load minimal policy, render via template,
    ##           verify output YAML is parseable dict with expected top-level keys.
    ## @scenario  Render minimal policy and parse output YAML.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import render_litellm_config

        logger.info("[IMP:7][test_render_minimal_policy] Rendering minimal policy...")
        rendered = render_litellm_config(minimal_policy_path, template_path)

        # Verify YAML is parseable
        parsed = yaml.safe_load(rendered)
        logger.critical("[IMP:9][test_render_minimal_policy] ASSERT: rendered YAML is parseable dict")
        assert isinstance(parsed, dict), "Rendered output must be a valid YAML mapping"

        # Verify top-level keys
        assert "model_list" in parsed, "Output must contain model_list"
        assert "litellm_settings" in parsed, "Output must contain litellm_settings"
        assert "general_settings" in parsed, "Output must contain general_settings"

        logger.critical(
            "[IMP:9][test_render_minimal_policy] ASSERT: top-level keys: %s",
            list(parsed.keys()),
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_render_minimal_policy")
        assert found_imp9, "LDD Error: No IMP:9 log for test_render_minimal_policy"


def test_model_list_contains_active_aliases(minimal_policy_path, template_path, caplog):
    """Assert reasoning (primary + fallback) and chat (primary) are in model_list.

    ## @purpose  Active aliases with non-empty deployments must appear in model_list.
    ##           reasoning has 2 entries (primary + fallback), chat has 1 (primary only).
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import render_litellm_config

        logger.info("[IMP:7][test_model_list_contains_active_aliases] Rendering minimal policy...")
        rendered = render_litellm_config(minimal_policy_path, template_path)
        parsed = yaml.safe_load(rendered)

        model_names = [entry["model_name"] for entry in parsed["model_list"]]
        logger.critical("[IMP:9][test_model_list_contains_active_aliases] model_list names: %s", model_names)

        # reasoning primary + fallback
        assert "reasoning" in model_names, "reasoning must be in model_list"
        assert "reasoning-fallback" in model_names, "reasoning-fallback must be in model_list"
        # chat primary (no fallback)
        assert "chat" in model_names, "chat must be in model_list"

        assert len(model_names) == 3, (
            f"Expected 3 model_list entries (reasoning, reasoning-fallback, chat), got {len(model_names)}"
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_model_list_contains_active_aliases")
        assert found_imp9, "LDD Error: No IMP:9 log for test_model_list_contains_active_aliases"


def test_reserved_aliases_not_in_model_list(minimal_policy_path, template_path, caplog):
    """Assert coding, vision, embedding aliases are NOT rendered.

    ## @purpose  Reserved aliases with empty deployments list must be skipped.
    ##           They should not appear in model_list at all.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import render_litellm_config

        logger.info("[IMP:7][test_reserved_aliases_not_in_model_list] Rendering minimal policy...")
        rendered = render_litellm_config(minimal_policy_path, template_path)
        parsed = yaml.safe_load(rendered)

        model_names = [entry["model_name"] for entry in parsed["model_list"]]
        logger.critical("[IMP:9][test_reserved_aliases_not_in_model_list] model_list names: %s", model_names)

        for reserved in ("coding", "vision", "embedding"):
            assert reserved not in model_names, f"Reserved alias '{reserved}' must NOT be in model_list"
            assert f"{reserved}-fallback" not in model_names, (
                f"Reserved alias '{reserved}-fallback' must NOT be in model_list"
            )

        found_imp9 = _print_ldd_trajectory(caplog, "test_reserved_aliases_not_in_model_list")
        assert found_imp9, "LDD Error: No IMP:9 log for test_reserved_aliases_not_in_model_list"


def test_fallback_chain_in_output(minimal_policy_path, template_path, caplog):
    """Assert fallbacks section contains reasoning → reasoning-fallback.

    ## @purpose  Aliases with both primary AND fallback deployments must have
    ##           a fallback entry in litellm_settings.fallbacks.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import render_litellm_config

        logger.info("[IMP:7][test_fallback_chain_in_output] Rendering minimal policy...")
        rendered = render_litellm_config(minimal_policy_path, template_path)
        parsed = yaml.safe_load(rendered)

        # fallbacks may be under litellm_settings or at top level
        if "fallbacks" in parsed:
            fallbacks = parsed["fallbacks"]
        else:
            fallbacks = parsed.get("litellm_settings", {}).get("fallbacks", [])

        logger.critical("[IMP:9][test_fallback_chain_in_output] fallbacks: %s", fallbacks)

        # Check reasoning -> reasoning-fallback
        found_reasoning_fb = False
        for fb in fallbacks:
            if isinstance(fb, dict):
                if fb.get("reasoning") == "reasoning-fallback":
                    found_reasoning_fb = True
                    break
            elif isinstance(fb, str):
                # Some formats may use list notation
                pass
        # The fallback is rendered as a YAML mapping with key = primary, value = fallback
        # In YAML: - reasoning: reasoning-fallback  (single mapping)
        # In Python dict: {"reasoning": "reasoning-fallback"}
        # But yaml.safe_load may parse this differently depending on format.
        # Let's check: - reasoning: reasoning-fallback → yaml parses as [{"reasoning": "reasoning-fallback"}]

        found_reasoning_fb = any(
            isinstance(fb, dict) and fb.get("reasoning") == "reasoning-fallback" for fb in fallbacks
        )
        assert found_reasoning_fb, f"Fallback chain 'reasoning -> reasoning-fallback' not found in {fallbacks}"

        # chat has no fallback — should not be in fallbacks
        chat_fb = any(isinstance(fb, dict) and "chat" in fb for fb in fallbacks)
        assert not chat_fb, "chat (no fallback) should not appear in fallbacks"

        found_imp9 = _print_ldd_trajectory(caplog, "test_fallback_chain_in_output")
        assert found_imp9, "LDD Error: No IMP:9 log for test_fallback_chain_in_output"


def test_api_key_from_provider(minimal_policy_path, template_path, caplog):
    """Assert api_key matches os.environ/DEEPSEEK_API_KEY.

    ## @purpose  The renderer must resolve provider key_env to api_key string
    ##           in the format os.environ/<KEY_ENV>.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import render_litellm_config

        logger.info("[IMP:7][test_api_key_from_provider] Rendering minimal policy...")
        rendered = render_litellm_config(minimal_policy_path, template_path)
        parsed = yaml.safe_load(rendered)

        for entry in parsed["model_list"]:
            api_key = entry["litellm_params"]["api_key"]
            logger.critical(
                "[IMP:9][test_api_key_from_provider] model_name='%s' api_key='%s'",
                entry["model_name"],
                api_key,
            )
            assert api_key == "os.environ/DEEPSEEK_API_KEY", (
                f"Expected 'os.environ/DEEPSEEK_API_KEY', got '{api_key}' for {entry['model_name']}"
            )

        found_imp9 = _print_ldd_trajectory(caplog, "test_api_key_from_provider")
        assert found_imp9, "LDD Error: No IMP:9 log for test_api_key_from_provider"


def test_check_mode_stale(minimal_policy_data, tmp_path, template_path, caplog):
    """Modify policy, run --check, assert exit code 1.

    ## @purpose  --check must detect staleness: when output file content differs
    ##           from freshly rendered content, exit code 1.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import main

        # Write minimal policy
        policy_path = _write_policy(minimal_policy_data, tmp_path, "policy.yaml")
        output_path = tmp_path / "litellm-config.yml"

        # Render fresh output
        from core.internal.llm.config_renderer import render_to_file

        render_to_file(policy_path, output_path, template_path)
        logger.info("[IMP:7][test_check_mode_stale] Fresh initial render written")

        # Modify the output file to make it stale
        with open(output_path, "a") as f:
            f.write("\n# stale modification\n")
        logger.info("[IMP:7][test_check_mode_stale] Output modified to be stale")

        # Run --check (main returns int, does not call sys.exit)
        exit_code = main(["--policy", str(policy_path), "--output", str(output_path), "--check"])
        logger.critical("[IMP:9][test_check_mode_stale] ASSERT: --check exit code=%d (expected 1)", exit_code)
        assert exit_code == 1, f"--check on stale output should exit 1, got {exit_code}"

        found_imp9 = _print_ldd_trajectory(caplog, "test_check_mode_stale")
        assert found_imp9, "LDD Error: No IMP:9 log for test_check_mode_stale"


def test_check_mode_fresh(minimal_policy_data, tmp_path, template_path, caplog):
    """Render then --check, assert exit code 0.

    ## @purpose  --check must confirm freshness: when output file matches
    ##           freshly rendered content, exit code 0.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import main, render_to_file

        policy_path = _write_policy(minimal_policy_data, tmp_path, "policy.yaml")
        output_path = tmp_path / "litellm-config.yml"

        # Render fresh output
        render_to_file(policy_path, output_path, template_path)
        logger.info("[IMP:7][test_check_mode_fresh] Fresh initial render written")

        # Run --check immediately (should be fresh, main returns int)
        exit_code = main(["--policy", str(policy_path), "--output", str(output_path), "--check"])
        logger.critical("[IMP:9][test_check_mode_fresh] ASSERT: --check exit code=%d (expected 0)", exit_code)
        assert exit_code == 0, f"--check on fresh output should exit 0, got {exit_code}"

        found_imp9 = _print_ldd_trajectory(caplog, "test_check_mode_fresh")
        assert found_imp9, "LDD Error: No IMP:9 log for test_check_mode_fresh"


def test_drop_params_enabled(minimal_policy_path, template_path, caplog):
    """Assert drop_params: true in rendered output.

    ## @purpose  litellm_settings.drop_params must be set to true (lowercase YAML).
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import render_litellm_config

        logger.info("[IMP:7][test_drop_params_enabled] Rendering minimal policy...")
        rendered = render_litellm_config(minimal_policy_path, template_path)
        parsed = yaml.safe_load(rendered)

        # drop_params is in litellm_settings
        litellm_settings = parsed.get("litellm_settings", {})
        drop_params = litellm_settings.get("drop_params")

        logger.critical("[IMP:9][test_drop_params_enabled] drop_params=%s (expected True)", drop_params)
        assert drop_params is True, f"Expected drop_params=True, got {drop_params}"

        found_imp9 = _print_ldd_trajectory(caplog, "test_drop_params_enabled")
        assert found_imp9, "LDD Error: No IMP:9 log for test_drop_params_enabled"


def test_invalid_policy_raises(template_path, caplog, tmp_path):
    """Bad policy (missing required alias) must raise exception.

    ## @purpose  render_litellm_config must not silently accept invalid policy.
    ##           Missing 'chat' alias should cause a validation error.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.config_renderer import render_litellm_config

        # Write policy missing 'chat' alias
        bad_policy = {
            "providers": {"deepseek": {"key_env": "DEEPSEEK_API_KEY"}},
            "aliases": {
                "reasoning": {
                    "label": "Reasoning",
                    "features": ["reasoning"],
                    "deployments": [],
                },
            },
            "profiles": {
                "default": {
                    "label": "Default",
                    "models": ["chat"],
                    "budget": {"daily": 1.0},
                    "rpm_limit": 10,
                },
            },
            "auto_provision": {
                "default_profile": "default",
                "profile_rules": [],
            },
        }
        policy_path = _write_policy(bad_policy, tmp_path, "policy.bad.yaml")

        logger.info("[IMP:7][test_invalid_policy_raises] Rendering invalid policy...")
        with pytest.raises(Exception) as exc_info:
            render_litellm_config(policy_path, template_path)

        logger.critical(
            "[IMP:9][test_invalid_policy_raises] ASSERT: exception raised: %s: %s",
            type(exc_info.value).__name__,
            str(exc_info.value)[:200],
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_invalid_policy_raises")
        assert found_imp9, "LDD Error: No IMP:9 log for test_invalid_policy_raises"


# 🧪 TRAP[TEST] · test_render_minimal_policy · Regression · Render fails · Remove if core rendering changes
# 🧪 TRAP[TEST] · test_model_list_contains_active_aliases · Regression · Aliases missing · Remove if alias schema changes
# 🧪 TRAP[TEST] · test_reserved_aliases_not_in_model_list · Regression · Reserved aliases leak · Remove if reserved alias policy changes
# 🧪 TRAP[TEST] · test_fallback_chain_in_output · Regression · Fallback missing · Remove if fallback logic changes
# 🧪 TRAP[TEST] · test_api_key_from_provider · Regression · API key format · Remove if key_env resolution changes
# 🧪 TRAP[TEST] · test_check_mode_stale · Regression · Freshness check false negative · Remove if check mode changes
# 🧪 TRAP[TEST] · test_check_mode_fresh · Regression · Freshness check false positive · Remove if check mode changes
# 🧪 TRAP[TEST] · test_drop_params_enabled · Regression · drop_params wrong · Remove if settings change
# 🧪 TRAP[TEST] · test_invalid_policy_raises · Scenario · Bad policy accepted · Remove if validation logic changes
