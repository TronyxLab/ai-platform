# GREP_SUMMARY: gate, llm-aliases, model_list, fallbacks, access_groups, DEEPSEEK_API_KEY, LDD
# STRUCTURE: ◇ load_policy → ◇ render_config → ◇ validate_aliases → ⊕ 5 assertions → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Gate tests for LLM alias contracts. Validates that:
##           1. All active aliases (non-empty deployments) have model_list entries
##           2. No alias falls back to itself (primary != fallback)
##           3. Every model_list entry has access_groups with at least one group
##           4. Fallback chains are complete — fallback model_name exists in model_list
##           5. All model_list entries reference os.environ/DEEPSEEK_API_KEY
## @scope    Static analysis of policy.yaml → rendered litellm-config.yml via
##           config_renderer to a temp file. No Docker/LiteLLM required.
## @invariants
##   - Each test uses config_renderer to render to a tmp_path (no stale cache)
##   - LDD trajectory printed with IMP:9 check per test
##   - All tests use @pytest.mark.gate
## @rationale Alias contract gates prevent silent LLM routing failures after policy changes.
##            Rendered config is validated, not raw YAML — catches generator bugs too.
## @changes 2026-07-24 | Created per DevPlan 049 Wave 6
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest
import yaml
from _conftest.ldd import _print_ldd_trajectory

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = repo_root()
POLICY_PATH = ROOT / "core" / "internal" / "llm" / "policy.yaml"


# ── LDD Helper ───────────────────────────────────────────────────────────────


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def rendered_config(tmp_path: pathlib.Path) -> dict:
    """Render litellm-config.yml from policy.yaml to a temp path and parse it.

    ## @purpose  Fixture provides the parsed rendered config dict for validation.
    ## @complexity O(render + parse)
    """
    import core.internal.llm.config_renderer as cr

    output_path = tmp_path / "litellm-config.yml"
    cr.render_to_file(POLICY_PATH, output_path)

    with pathlib.Path(output_path).open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    logger.info("[IMP:8][fixture] Rendered config loaded: %d top-level keys", len(config or {}))
    return config or {}


@pytest.fixture
def model_list(rendered_config: dict) -> list[dict]:
    """Extract model_list from rendered config.

    ## @purpose  Convenience fixture — flattened model_list entries.
    ## @complexity O(1)
    """
    models = rendered_config.get("model_list", [])
    logger.info("[IMP:8][fixture] model_list contains %d entries", len(models))
    return models


# ── Tests ────────────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Gate: every active alias has a corresponding model_list entry
# · Regression: policy alias added without model_list entry → silent 404 on that model
# · Scenario: iterate policy aliases with non-empty deployments → check model_list by model_name
# · Last fail: N/A · Remove if: alias-to-model_list mapping is fundamentally redesigned
@pytest.mark.gate
def test_gate_all_active_aliases_have_models(caplog, rendered_config) -> None:
    """Every alias in policy.yaml with non-empty deployments has a corresponding model_list entry.

    ## @purpose  Gate: no orphan alias — every active alias must produce at least
    ##           one model_list entry (primary deployment).
    ## @scenario  For each alias with non-empty deployments, check that its name
    ##            appears as a model_name in the rendered model_list.
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][gate][START] test_gate_all_active_aliases_have_models")

    # Load policy
    from core.internal.llm.policy_schema import LLMPolicy

    policy = LLMPolicy.from_yaml(POLICY_PATH)
    rendered_models = rendered_config.get("model_list", [])
    rendered_names = {m.get("model_name") for m in rendered_models}

    violations: list[str] = []
    for alias_name, alias in policy.aliases.items():
        deployments = alias.deployments
        # Skip reserved aliases (empty deployments list)
        is_reserved = isinstance(deployments, list) and len(deployments) == 0
        if is_reserved:
            logger.info("[IMP:8][gate] Alias '%s' is reserved (empty deployments) — skipping", alias_name)
            continue

        # Active alias: must have at least primary deployment → must be in model_list
        if alias_name not in rendered_names:
            violations.append(f"Active alias '{alias_name}' has no model_list entry")
            logger.warning(
                "[IMP:7][gate] MISSING: alias '%s' not found in model_list names: %s", alias_name, rendered_names
            )
        else:
            logger.info("[IMP:8][gate] Alias '%s' → model_list: %s found", alias_name, alias_name)

    # Also check fallback entries
    for m in rendered_models:
        if m.get("model_name", "").endswith("-fallback"):
            primary_name = m["model_name"].replace("-fallback", "")
            if primary_name not in rendered_names:
                violations.append(f"Fallback entry '{m['model_name']}' exists but primary '{primary_name}' is missing")

    logger.critical("[IMP:9][gate] test_gate_all_active_aliases_have_models: %d violations", len(violations))
    assert not violations, "GATE_LLM_ALIASES: missing model_list entries:\n  " + "\n  ".join(violations)

    found_imp9 = _print_ldd_trajectory(caplog, "test_gate_all_active_aliases_have_models")
    assert found_imp9, "LDD Error: No IMP:9 log"


# 🧪 TRAP[TEST] · Gate: no circular fallbacks — primary != fallback model
# · Regression: alias configured with same model for primary and fallback → useless fallback
# · Scenario: for each alias with fallback deployment, compare primary vs fallback model
# · Last fail: N/A · Remove if: fallback mechanism is fundamentally redesigned
@pytest.mark.gate
def test_gate_no_circular_fallbacks(caplog, rendered_config) -> None:
    """No alias falls back to itself — primary and fallback must be different models.

    ## @purpose  Gate: prevent useless fallback chains where primary == fallback.
    ##           A circular fallback would never trigger because LiteLLM considers
    ##           both models equivalent.
    ## @scenario  Check rendered fallbacks: for each fallback entry, the primary model_name
    ##            and fallback model_name must resolve to different litellm_params.model.
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][gate][START] test_gate_no_circular_fallbacks")

    rendered_models = rendered_config.get("model_list", [])
    model_map = {m.get("model_name"): m.get("litellm_params", {}).get("model") for m in rendered_models}
    fallbacks = rendered_config.get("litellm_settings", {}).get("fallbacks", [])

    violations: list[str] = []
    for fb_entry in fallbacks:
        for primary, fallback in fb_entry.items():
            primary_model = model_map.get(primary)
            fallback_model = model_map.get(fallback)
            if primary_model and fallback_model and primary_model == fallback_model:
                violations.append(f"Circular fallback: '{primary}' ({primary_model}) → '{fallback}' ({fallback_model})")
            logger.info(
                "[IMP:8][gate] Fallback: '%s' → '%s' (models: %s → %s)",
                primary,
                fallback,
                primary_model,
                fallback_model,
            )

    logger.critical("[IMP:9][gate] test_gate_no_circular_fallbacks: %d violations", len(violations))
    assert not violations, "GATE_LLM_ALIASES: circular fallbacks detected:\n  " + "\n  ".join(violations)

    found_imp9 = _print_ldd_trajectory(caplog, "test_gate_no_circular_fallbacks")
    assert found_imp9, "LDD Error: No IMP:9 log"


# 🧪 TRAP[TEST] · Gate: every model_list entry has access_groups
# · Regression: model_list entry without access_group → cannot be assigned to a virtual key
# · Scenario: iterate rendered model_list, check model_info.access_groups is non-empty
# · Last fail: N/A · Remove if: access_groups are no longer used for key model assignment
@pytest.mark.gate
def test_gate_all_model_list_have_access_group(caplog, model_list) -> None:
    """Every model_list entry has model_info.access_groups with at least one group.

    ## @purpose  Gate: access_groups are required for virtual key model assignment.
    ##           A model without access_groups cannot be referenced by a key's models list.
    ## @scenario  Check every renderered model_list entry for non-empty access_groups.
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][gate][START] test_gate_all_model_list_have_access_group")

    violations: list[str] = []
    for entry in model_list:
        model_name = entry.get("model_name", "unknown")
        model_info = entry.get("model_info", {})
        access_groups = model_info.get("access_groups", []) if isinstance(model_info, dict) else []

        if not access_groups or not isinstance(access_groups, list):
            violations.append(f"Entry '{model_name}' has no access_groups in model_info")
            logger.warning("[IMP:7][gate] MISSING access_groups: %s", model_name)
        else:
            logger.info("[IMP:8][gate] Entry '%s' access_groups: %s", model_name, access_groups)

    logger.critical("[IMP:9][gate] test_gate_all_model_list_have_access_group: %d violations", len(violations))
    assert not violations, "GATE_LLM_ALIASES: missing access_groups:\n  " + "\n  ".join(violations)

    found_imp9 = _print_ldd_trajectory(caplog, "test_gate_all_model_list_have_access_group")
    assert found_imp9, "LDD Error: No IMP:9 log"


# 🧪 TRAP[TEST] · Gate: fallback chain complete — fallback model_name exists
# · Regression: fallback removed from policy but fallback chain still references it → LiteLLM 404
# · Scenario: for each fallback entry, verify the fallback model_name exists in model_list
# · Last fail: N/A · Remove if: fallback mechanism is fundamentally redesigned
@pytest.mark.gate
def test_gate_fallback_chain_complete(caplog, rendered_config) -> None:
    """For each alias with a fallback deployment, the fallback model_name exists in model_list.

    ## @purpose  Gate: prevent dangling fallback references. If a fallback deployment
    ##           is removed from policy.yaml, the rendered model_list will lack the
    ##           corresponding "-fallback" entry, and LiteLLM will return 404 on fallback.
    ## @scenario  Extract all fallback entries from litellm_settings.fallbacks and verify
    ##            each fallback model_name exists in model_list.
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][gate][START] test_gate_fallback_chain_complete")

    rendered_models = rendered_config.get("model_list", [])
    rendered_names = {m.get("model_name") for m in rendered_models}
    fallbacks = rendered_config.get("litellm_settings", {}).get("fallbacks", [])

    violations: list[str] = []
    for fb_entry in fallbacks:
        for primary, fallback in fb_entry.items():
            if fallback not in rendered_names:
                violations.append(f"Fallback chain '{primary}' → '{fallback}': '{fallback}' not found in model_list")
            else:
                logger.info("[IMP:8][gate] Fallback chain OK: '%s' → '%s' (exists in model_list)", primary, fallback)

    logger.critical("[IMP:9][gate] test_gate_fallback_chain_complete: %d violations", len(violations))
    assert not violations, "GATE_LLM_ALIASES: incomplete fallback chains:\n  " + "\n  ".join(violations)

    found_imp9 = _print_ldd_trajectory(caplog, "test_gate_fallback_chain_complete")
    assert found_imp9, "LDD Error: No IMP:9 log"


# 🧪 TRAP[TEST] · Gate: all model_list entries reference DEEPSEEK_API_KEY
# · Regression: new provider added to policy but api_key env var not configured
# · Scenario: check every model_list entry's api_key starts with os.environ/DEEPSEEK_API_KEY
# · Last fail: N/A · Remove if: multi-provider routing with different API keys is implemented
@pytest.mark.gate
def test_gate_deepseek_key_in_all_entries(caplog, model_list) -> None:
    """All model_list entries reference os.environ/DEEPSEEK_API_KEY as their api_key.

    ## @purpose  Gate: after provider key cleanup (Wave 4), DEEPSEEK_API_KEY is the
    ##           only provider key. Every model_list entry must reference it.
    ## @scenario  Check every rendered model_list entry's api_key field matches
    ##            the expected env var reference.
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][gate][START] test_gate_deepseek_key_in_all_entries")

    violations: list[str] = []
    for entry in model_list:
        model_name = entry.get("model_name", "unknown")
        litellm_params = entry.get("litellm_params", {})
        api_key = litellm_params.get("api_key", "") if isinstance(litellm_params, dict) else ""

        expected_ref = "os.environ/DEEPSEEK_API_KEY"
        if api_key != expected_ref:
            violations.append(f"Entry '{model_name}' has api_key='{api_key}', expected '{expected_ref}'")
            logger.warning("[IMP:7][gate] WRONG API KEY: %s → '%s'", model_name, api_key)
        else:
            logger.info("[IMP:8][gate] Entry '%s' api_key: %s ✓", model_name, api_key)

    logger.critical("[IMP:9][gate] test_gate_deepseek_key_in_all_entries: %d violations", len(violations))
    assert not violations, "GATE_LLM_ALIASES: api_key violations:\n  " + "\n  ".join(violations)

    found_imp9 = _print_ldd_trajectory(caplog, "test_gate_deepseek_key_in_all_entries")
    assert found_imp9, "LDD Error: No IMP:9 log"
