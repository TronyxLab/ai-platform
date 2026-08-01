# GREP_SUMMARY: test-llm-policy-schema, policy.yaml, LLMPolicy, Pydantic, jsonschema, valid, invalid, LDD, IMP
# STRUCTURE: fixtures(paths + schemas) → test_valid_policy_loading → test_valid_policy_jsonschema → test_invalid_missing_aliases → test_invalid_missing_providers → test_invalid_bad_default_profile → test_invalid_bad_provider_ref
# region MODULE_CONTRACT
## @purpose  Unit tests for LLM policy YAML validation: policy_schema.py Pydantic models
##           and llm-policy.schema.json JSON Schema.
## @scope    Tests both the Pydantic model code path (LLMPolicy.from_yaml) and the
##           JSON Schema validation (using jsonschema directly).
## @invariants
##   - All tests use tmp_path fixture (no hardcoded paths) — YAML written to temp dirs
##   - Fixtures tests/test_data/llm/policy.valid.yaml and policy.invalid.yaml are used
##   - Each test includes LDD trajectory printing with IMP:7-10 log levels
##   - At least one IMP:9 log per successful test (§TESTING)
## @rationale Dual validation path: Pydantic for runtime type safety, JSON Schema for
##            structural YAML validation. Both must agree on valid/invalid inputs.
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 1)
# endregion MODULE_CONTRACT

import json
import logging
import pathlib

import jsonschema
import pytest
import yaml

logger = logging.getLogger(__name__)

# Resolve paths relative to this file
TEST_DIR = pathlib.Path(__file__).resolve().parent  # tests/unit/
TEST_DATA_DIR = TEST_DIR.parent / "test_data" / "llm"
SCHEMA_DIR = TEST_DIR.parent.parent / "core" / "schemas"
LLM_POLICY_SCHEMA_PATH = SCHEMA_DIR / "llm-policy.schema.json"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_llm_schema() -> dict:
    """Load the llm-policy.schema.json.

    ## @purpose  Load the JSON Schema for LLM policy validation.
    ## @io  ⎋ dict — parsed JSON Schema
    ## @complexity O(1)
    """
    if not LLM_POLICY_SCHEMA_PATH.exists():
        raise FileNotFoundError(f"LLM policy schema not found: {LLM_POLICY_SCHEMA_PATH}")
    with open(LLM_POLICY_SCHEMA_PATH) as f:
        return json.load(f)


def _print_ldd_trajectory(caplog, test_name: str) -> bool:
    """Print IMP:7-10 LDD trajectory from caplog and return whether IMP:9+ was found.

    ## @purpose  Centralised LDD trajectory printer for all test functions.
    ##           Follows the pattern from RULES.md §TESTING.
    ## @io
    ##   - caplog — pytest caplog fixture
    ##   - test_name: str — test identifier for log prefix
    ##   - ⎋ bool — True if at least one IMP:9+ log was found
    ## @complexity O(N) where N = caplog records
    """
    found = False
    print(f"\n--- LDD TRAJECTORY ({test_name}) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            try:
                imp_str = record.message.split("[IMP:")[1].split("]")[0]
                imp_level = int(imp_str)
                if imp_level >= 7:
                    print(record.message)
                if imp_level >= 9:
                    found = True
            except (IndexError, ValueError):
                pass
    print("--- END LDD TRAJECTORY ---")
    return found


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def llm_policy_schema() -> dict:
    """Load the llm-policy.schema.json fixture."""
    return _load_llm_schema()


@pytest.fixture
def valid_policy_path() -> pathlib.Path:
    """Path to the valid policy fixture."""
    p = TEST_DATA_DIR / "policy.valid.yaml"
    if not p.exists():
        raise FileNotFoundError(f"Valid policy fixture not found: {p}")
    return p


@pytest.fixture
def invalid_policy_path() -> pathlib.Path:
    """Path to the invalid policy fixture (missing aliases)."""
    p = TEST_DATA_DIR / "policy.invalid.yaml"
    if not p.exists():
        raise FileNotFoundError(f"Invalid policy fixture not found: {p}")
    return p


# ── TESTS: LLMPolicy.from_yaml (Pydantic) ───────────────────────────────────


@pytest.mark.parametrize(
    "test_name, path_fixture, expect_success",
    [
        ("valid_policy", "valid_policy_path", True),
        ("invalid_missing_aliases", "invalid_policy_path", False),
    ],
)
def test_policy_from_yaml(test_name, path_fixture, expect_success, caplog, request):
    """Parametrized: valid policy loads OK, invalid policy raises ValidationError.

    ## @purpose  Verify LLMPolicy.from_yaml() correctly accepts valid policy
    ##           and rejects invalid policy (missing aliases).
    ## @scenario  Two sub-tests: (1) valid policy with all 5 aliases, (2) invalid
    ##            policy missing the entire aliases section.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.policy_schema import LLMPolicy

        path = request.getfixturevalue(path_fixture)
        logger.info("[IMP:7][test_policy_from_yaml][%s] Loading policy from: %s", test_name, path)

        if expect_success:
            policy = LLMPolicy.from_yaml(path)
            logger.critical(
                "[IMP:9][test_policy_from_yaml][%s] ASSERT: policy loaded OK, aliases=%d, providers=%d, profiles=%d",
                test_name,
                len(policy.aliases),
                len(policy.providers),
                len(policy.profiles),
            )
            assert len(policy.aliases) >= 2
            assert "reasoning" in policy.aliases
            assert "chat" in policy.aliases
            assert policy.providers["deepseek"].key_env == "DEEPSEEK_API_KEY"
            assert policy.auto_provision.default_profile == "default"
        else:
            with pytest.raises((jsonschema.ValidationError, ValueError, Exception)):
                LLMPolicy.from_yaml(path)
            logger.critical(
                "[IMP:9][test_policy_from_yaml][%s] ASSERT: expected exception raised for invalid policy",
                test_name,
            )

        found_imp9 = _print_ldd_trajectory(caplog, f"test_policy_from_yaml_{test_name}")
        assert found_imp9, (
            f"LDD Error: No IMP:9 log for test_policy_from_yaml/{test_name} — business logic assertions not logged"
        )


def test_valid_policy_aliases_content(valid_policy_path, caplog):
    """Valid policy has correct alias structure: features, context_window, deployments.

    ## @purpose  Verify that all expected aliases are present with correct properties.
    ##           reasoning has primary+fallback, chat has primary, others are reserved.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.policy_schema import DeploymentList, LLMPolicy

        logger.info("[IMP:7][test_valid_policy_aliases_content] Loading valid policy...")
        policy = LLMPolicy.from_yaml(valid_policy_path)

        # Reasoning alias
        reasoning = policy.aliases["reasoning"]
        assert reasoning.label == "Complex reasoning"
        assert reasoning.context_window == 128000
        assert "reasoning" in reasoning.features
        assert "structured_output" in reasoning.features
        assert isinstance(reasoning.deployments, DeploymentList)
        assert reasoning.deployments.primary is not None
        assert reasoning.deployments.primary.provider == "deepseek"
        assert reasoning.deployments.primary.model == "deepseek/deepseek-v4-pro"
        assert reasoning.deployments.fallback is not None
        assert reasoning.deployments.fallback.model == "deepseek/deepseek-v4-flash"

        # Chat alias
        chat = policy.aliases["chat"]
        assert chat.label == "Fast chat"
        assert "chat" in chat.features
        assert isinstance(chat.deployments, DeploymentList)
        assert chat.deployments.primary is not None
        assert chat.deployments.primary.model == "deepseek/deepseek-v4-flash"
        assert chat.deployments.fallback is None  # no fallback configured

        # Reserved aliases (empty deployments list)
        for reserved_name in ("coding", "vision", "embedding"):
            alias = policy.aliases[reserved_name]
            assert isinstance(alias.deployments, list), f"Alias '{reserved_name}' should have empty list deployments"
            assert len(alias.deployments) == 0, f"Alias '{reserved_name}' deployments list should be empty"

        logger.critical(
            "[IMP:9][test_valid_policy_aliases_content] ASSERT: all 5 aliases verified — "
            "reasoning(primary+fallback), chat(primary), coding/vision/embedding(reserved)"
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_valid_policy_aliases_content")
        assert found_imp9, "LDD Error: No IMP:9 log for test_valid_policy_aliases_content"


def test_valid_policy_profiles(valid_policy_path, caplog):
    """Valid policy has correct profile structure: budget, rpm, metadata.

    ## @purpose  Verify that profiles have correct budget limits, rpm limits,
    ##           and metadata tags.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.policy_schema import LLMPolicy

        logger.info("[IMP:7][test_valid_policy_profiles] Loading valid policy...")
        policy = LLMPolicy.from_yaml(valid_policy_path)

        # Default profile
        default = policy.profiles["default"]
        assert default.label == "Default (chat only)"
        assert default.models == ["chat"]
        assert default.budget.daily == 1.0
        assert default.rpm_limit == 10
        assert default.metadata == {"tier": "default"}

        # Premium profile
        premium = policy.profiles["premium"]
        assert premium.models == ["reasoning", "chat"]
        assert premium.budget.daily == 10.0
        assert premium.rpm_limit == 60
        assert premium.metadata == {"tier": "premium"}

        # Unlimited profile
        unlimited = policy.profiles["unlimited"]
        assert unlimited.models == ["reasoning", "chat"]
        assert unlimited.budget.daily == 50.0
        assert unlimited.rpm_limit == 120
        assert unlimited.metadata == {"tier": "unlimited"}

        logger.critical(
            "[IMP:9][test_valid_policy_profiles] ASSERT: all 3 profiles verified — "
            "default($1/day, 10rpm), premium($10/day, 60rpm), unlimited($50/day, 120rpm)"
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_valid_policy_profiles")
        assert found_imp9, "LDD Error: No IMP:9 log for test_valid_policy_profiles"


# ── TESTS: Invalid policy rejection ─────────────────────────────────────────


def test_invalid_policy_missing_aliases_rejected(invalid_policy_path, caplog):
    """Policy missing aliases section must be rejected by LLMPolicy.from_yaml.

    ## @purpose  Negative test: the invalid fixture has no 'aliases' key.
    ##           Both JSON Schema and Pydantic should reject it.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.policy_schema import LLMPolicy

        logger.info("[IMP:7][test_invalid_missing_aliases] Loading policy WITHOUT aliases section...")

        with pytest.raises(Exception) as exc_info:
            LLMPolicy.from_yaml(invalid_policy_path)

        logger.critical(
            "[IMP:9][test_invalid_missing_aliases] ASSERT: exception raised: %s: %s",
            type(exc_info.value).__name__,
            str(exc_info.value)[:200],
        )

        assert True, "Exception was raised as expected"

        found_imp9 = _print_ldd_trajectory(caplog, "test_invalid_missing_aliases")
        assert found_imp9, "LDD Error: No IMP:9 log for test_invalid_missing_aliases"


def test_invalid_policy_empty_providers(caplog, tmp_path):
    """Policy with empty providers must be rejected.

    ## @purpose  Negative test: providers: {} should fail validation
    ##           because at least one provider is required.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.policy_schema import LLMPolicy

        # Write invalid policy to tmp_path
        invalid = {
            "providers": {},
            "aliases": {
                "reasoning": {
                    "label": "Reasoning",
                    "features": ["reasoning"],
                    "deployments": [],
                },
                "chat": {
                    "label": "Chat",
                    "features": ["chat"],
                    "deployments": [],
                },
            },
            "profiles": {
                "default": {
                    "label": "Default",
                    "models": ["chat"],
                    "budget": {"daily": 1.0},
                    "rpm_limit": 10,
                }
            },
            "auto_provision": {
                "default_profile": "default",
                "profile_rules": [],
            },
        }
        p = tmp_path / "policy.empty_providers.yaml"
        with open(p, "w") as f:
            yaml.dump(invalid, f)

        logger.info("[IMP:7][test_invalid_empty_providers] Loading policy with empty providers...")

        with pytest.raises(Exception) as exc_info:
            LLMPolicy.from_yaml(p)

        logger.critical(
            "[IMP:9][test_invalid_empty_providers] ASSERT: exception raised: %s: %s",
            type(exc_info.value).__name__,
            str(exc_info.value)[:200],
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_invalid_empty_providers")
        assert found_imp9, "LDD Error: No IMP:9 log for test_invalid_empty_providers"


def test_invalid_policy_bad_default_profile(caplog, tmp_path):
    """Policy with auto_provision.default_profile referencing non-existent profile must fail.

    ## @purpose  Cross-field validation: default_profile must reference an existing profile.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.policy_schema import LLMPolicy

        invalid = {
            "providers": {"deepseek": {"key_env": "DEEPSEEK_API_KEY"}},
            "aliases": {
                "reasoning": {
                    "label": "Reasoning",
                    "features": ["reasoning"],
                    "deployments": [],
                },
                "chat": {
                    "label": "Chat",
                    "features": ["chat"],
                    "deployments": [],
                },
            },
            "profiles": {
                "default": {
                    "label": "Default",
                    "models": ["chat"],
                    "budget": {"daily": 1.0},
                    "rpm_limit": 10,
                }
            },
            "auto_provision": {
                "default_profile": "nonexistent_profile",
                "profile_rules": [],
            },
        }
        p = tmp_path / "policy.bad_default.yaml"
        with open(p, "w") as f:
            yaml.dump(invalid, f)

        logger.info("[IMP:7][test_invalid_bad_default_profile] Loading policy with non-existent default_profile...")

        from core.internal.shared.exceptions import ConfigValidationError

        with pytest.raises(ConfigValidationError, match="nonexistent_profile"):
            LLMPolicy.from_yaml(p)

        logger.critical(
            "[IMP:9][test_invalid_bad_default_profile] ASSERT: ValueError raised for non-existent default_profile"
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_invalid_bad_default_profile")
        assert found_imp9, "LDD Error: No IMP:9 log for test_invalid_bad_default_profile"


def test_invalid_policy_bad_provider_ref(caplog, tmp_path):
    """Policy with deployment referencing non-existent provider must fail.

    ## @purpose  Cross-field validation: deployment provider must exist in providers section.
    """
    with caplog.at_level(logging.DEBUG):
        from core.internal.llm.policy_schema import LLMPolicy

        invalid = {
            "providers": {"deepseek": {"key_env": "DEEPSEEK_API_KEY"}},
            "aliases": {
                "reasoning": {
                    "label": "Reasoning",
                    "features": ["reasoning"],
                    "deployments": {
                        "primary": {
                            "provider": "nonexistent_provider",
                            "model": "fake/fake-model",
                        }
                    },
                },
                "chat": {
                    "label": "Chat",
                    "features": ["chat"],
                    "deployments": [],
                },
            },
            "profiles": {
                "default": {
                    "label": "Default",
                    "models": ["chat"],
                    "budget": {"daily": 1.0},
                    "rpm_limit": 10,
                }
            },
            "auto_provision": {
                "default_profile": "default",
                "profile_rules": [],
            },
        }
        p = tmp_path / "policy.bad_provider.yaml"
        with open(p, "w") as f:
            yaml.dump(invalid, f)

        logger.info("[IMP:7][test_invalid_bad_provider_ref] Loading policy with non-existent provider in deployment...")

        from core.internal.shared.exceptions import ConfigValidationError

        with pytest.raises(ConfigValidationError, match="nonexistent_provider"):
            LLMPolicy.from_yaml(p)

        logger.critical(
            "[IMP:9][test_invalid_bad_provider_ref] ASSERT: ValueError raised for non-existent provider reference"
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_invalid_bad_provider_ref")
        assert found_imp9, "LDD Error: No IMP:9 log for test_invalid_bad_provider_ref"


# ── TESTS: JSON Schema direct validation ─────────────────────────────────────


def test_valid_policy_jsonschema_pass(valid_policy_path, llm_policy_schema, caplog):
    """Valid policy YAML passes JSON Schema validation.

    ## @purpose  Direct JSON Schema validation (not via LLMPolicy.from_yaml)
    ##           to verify the schema independently.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_valid_policy_jsonschema] Validating valid policy against JSON Schema...")

        with open(valid_policy_path) as f:
            data = yaml.safe_load(f)

        validator = jsonschema.Draft7Validator(llm_policy_schema)
        errors = list(validator.iter_errors(data))

        logger.critical(
            "[IMP:9][test_valid_policy_jsonschema] ASSERT: JSON Schema errors=%d (expected 0)",
            len(errors),
        )
        assert errors == [], f"Valid policy failed JSON Schema: {[e.message for e in errors]}"

        found_imp9 = _print_ldd_trajectory(caplog, "test_valid_policy_jsonschema")
        assert found_imp9, "LDD Error: No IMP:9 log for test_valid_policy_jsonschema"


def test_invalid_policy_jsonschema_fails(invalid_policy_path, llm_policy_schema, caplog):
    """Invalid policy YAML (missing aliases) fails JSON Schema validation.

    ## @purpose  Verify JSON Schema catches missing required fields.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_invalid_policy_jsonschema] Validating INVALID policy against JSON Schema...")

        with open(invalid_policy_path) as f:
            data = yaml.safe_load(f)

        validator = jsonschema.Draft7Validator(llm_policy_schema)
        errors = list(validator.iter_errors(data))

        logger.critical(
            "[IMP:9][test_invalid_policy_jsonschema] ASSERT: JSON Schema errors=%d (expected >0)",
            len(errors),
        )
        assert len(errors) > 0, "Invalid policy should fail JSON Schema validation"

        found_imp9 = _print_ldd_trajectory(caplog, "test_invalid_policy_jsonschema")
        assert found_imp9, "LDD Error: No IMP:9 log for test_invalid_policy_jsonschema"
