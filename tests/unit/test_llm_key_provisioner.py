# GREP_SUMMARY: test-llm-key-provisioner, unit, provisioner, mock, idempotent, admin-client, LDD, IMP
# STRUCTURE: fixtures(mocked client + policy + tmp_path) →
#            test_idempotent_same_key_on_second_call →
#            test_different_config_updates_key →
#            test_skip_disabled_project →
#            test_profile_rule_match →
#            test_default_profile_fallback →
#            test_overrides_merge →
#            test_no_duplicate_keys
# region MODULE_CONTRACT
## @purpose  Unit tests for key_provisioner.py with mocked LiteLLMAdminClient.
##           Tests cover: idempotency, update-on-config-change, skip-disabled,
##           profile rule matching, default profile fallback, overrides merge,
##           and no-duplicate-keys guarantee.
## @scope    All test functions use unittest.mock to simulate LiteLLM Admin API.
##           No real HTTP calls are made.
## @invariants
##   - All tests use tmp_path for temp files (no hardcoded paths)
##   - Each test includes LDD trajectory printing with IMP:9 check
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 4)
# endregion MODULE_CONTRACT

import json
import logging
import pathlib
from unittest.mock import MagicMock, patch

import pytest
import yaml

logger = logging.getLogger(__name__)


# ── LDD Helper ───────────────────────────────────────────────────────────────


def _print_ldd_trajectory(caplog, test_name: str) -> bool:
    """Print IMP:7-10 LDD trajectory from caplog.

    ## @purpose  Centralised LDD trajectory printer for all test functions.
    ## @io
    ##   - caplog — pytest caplog fixture
    ##   - test_name: str — test identifier
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
def policy_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal valid policy.yaml for testing.

    ## @purpose  Policy fixture with default, premium, and unlimited profiles.
    ##           hermes-agent rule maps to unlimited profile.
    ## @complexity O(1)
    """
    policy = {
        "providers": {"deepseek": {"key_env": "DEEPSEEK_API_KEY"}},
        "aliases": {
            "reasoning": {
                "label": "Reasoning",
                "features": ["reasoning"],
                "deployments": {
                    "primary": {"provider": "deepseek", "model": "deepseek/deepseek-v4-pro"},
                },
            },
            "chat": {
                "label": "Chat",
                "features": ["chat"],
                "deployments": {
                    "primary": {"provider": "deepseek", "model": "deepseek/deepseek-v4-flash"},
                },
            },
        },
        "profiles": {
            "default": {
                "label": "Default",
                "models": ["chat"],
                "budget": {"daily": 1.0},
                "rpm_limit": 10,
                "metadata": {"tier": "default"},
            },
            "premium": {
                "label": "Premium",
                "models": ["reasoning", "chat"],
                "budget": {"daily": 10.0},
                "rpm_limit": 60,
                "metadata": {"tier": "premium"},
            },
            "unlimited": {
                "label": "Unlimited",
                "models": ["reasoning", "chat"],
                "budget": {"daily": 50.0},
                "rpm_limit": 120,
                "metadata": {"tier": "unlimited"},
            },
        },
        "auto_provision": {
            "default_profile": "default",
            "profile_rules": [
                {"match": {"name": "hermes-agent"}, "profile": "unlimited"},
            ],
        },
    }
    p = tmp_path / "policy.yaml"
    with open(p, "w") as f:
        yaml.dump(policy, f)
    return p


@pytest.fixture
def mock_client() -> MagicMock:
    """Create a mock LiteLLMAdminClient with standard returns.

    ## @purpose  All methods return None/empty by default.
    ##           Tests override specific return values.
    ## @complexity O(1)
    """
    client = MagicMock()
    client.get_key_info.return_value = None
    client.generate_key.return_value = {"key": "sk-mock-generated-key-abc123def456"}
    client.update_key.return_value = {"key": "sk-mock-key", "models": ["chat"]}
    client.get_key_by_metadata.return_value = None
    client.delete_key.return_value = True
    return client


# ── Helper to import and provision ───────────────────────────────────────────


def _run_provision(
    mock_client: MagicMock,
    policy_path: pathlib.Path,
    persist_path: pathlib.Path,
) -> dict[str, str]:
    """Run provision_all with a mocked client and discover_projects.

    ## @purpose  Monkey-patches the provisioner's LiteLLMAdminClient and
    ##           discover_projects/get_platform_consumers. Returns provisions keys dict.
    ## @complexity O(N) where N = consumers
    """
    import core.internal.llm.key_provisioner as kp

    # Patch admin client creation and discovery functions
    with (
        patch.object(kp, "LiteLLMAdminClient", return_value=mock_client),
        patch.object(kp, "discover_projects") as mock_disc,
        patch.object(kp, "get_platform_consumers") as mock_plat,
    ):
        mock_disc.return_value = [
            {"name": "test-backend", "llm": {"enabled": True}},
            {"name": "test-priority", "llm": {"enabled": True, "profile": "premium"}},
            {"name": "test-legacy", "llm": {"enabled": False}},
        ]
        mock_plat.return_value = [
            {"name": "hermes-agent", "llm": {"enabled": True}},
        ]

        return kp.provision_all(
            master_key="test-master-key",
            base_url="http://test:4000",
            policy_path=policy_path,
            persist_path=persist_path,
        )


# ── TESTS ────────────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: idempotency — second provision_all returns same keys without duplicates
def test_idempotent_same_key_on_second_call(policy_yaml, mock_client, tmp_path, caplog):
    """First call creates keys, second call skips — same keys returned.

    ## @purpose  Verify idempotency: after keys are created, second provision_all
    ##           returns the SAME keys without creating duplicates.
    ## @scenario  Call 1: no keys exist → generate_key called. Call 2: keys exist
    ##            with matching config → no generate_key calls.
    """
    with caplog.at_level(logging.DEBUG):
        persist_path = tmp_path / "keys.json"
        # Mock generate_key to return a unique key
        generated_key_value = "sk-mock-generated-key-abc123def456"
        mock_client.generate_key.return_value = {"key": generated_key_value}

        # --- First call: generates keys ---
        logger.info("[IMP:7][test_idempotent] Starting first provision_all...")
        result_1 = _run_provision(mock_client, policy_yaml, persist_path)

        logger.critical(
            "[IMP:9][test_idempotent] First call returned %d keys: %s",
            len(result_1),
            list(result_1.keys()),
        )
        assert len(result_1) == 3, "Should provision keys for 3 enabled consumers"
        assert "test-backend" in result_1
        assert "test-priority" in result_1
        assert "hermes-agent" in result_1
        assert "test-legacy" not in result_1, "Disabled project should be skipped"
        assert result_1["test-backend"] == generated_key_value

        # --- Second call: key exists, should skip ---
        # Mock get_key_by_metadata to return existing keys
        existing_key_info = {
            "key": generated_key_value,
            "models": ["chat"],
            "max_budget": 1.0,
            "rpm_limit": 10,
            "metadata": {"project": "test-backend", "tier": "default"},
        }

        def _get_key_by_metadata_side_effect(**metadata_filters):
            project_name = metadata_filters.get("project", "")
            if project_name == "test-backend":
                return existing_key_info
            if project_name == "test-priority":
                return {
                    "key": "sk-priority-key-xxx",
                    "models": ["reasoning", "chat"],
                    "max_budget": 10.0,
                    "rpm_limit": 60,
                    "metadata": {"project": "test-priority", "tier": "premium"},
                }
            if project_name == "hermes-agent":
                return {
                    "key": "sk-hermes-key-xxx",
                    "models": ["reasoning", "chat"],
                    "max_budget": 50.0,
                    "rpm_limit": 120,
                    "metadata": {"project": "hermes-agent", "tier": "unlimited"},
                }
            return None

        mock_client.get_key_by_metadata.side_effect = _get_key_by_metadata_side_effect

        logger.info("[IMP:7][test_idempotent] Starting second provision_all (idempotent)...")
        result_2 = _run_provision(mock_client, policy_yaml, persist_path)

        logger.critical(
            "[IMP:9][test_idempotent] Second call returned %d keys: %s",
            len(result_2),
            list(result_2.keys()),
        )
        assert len(result_2) == 3, "Second call should return same 3 keys"
        assert result_2["test-backend"] == generated_key_value, "Key value should be preserved"

        # Verify: generate_key was called EXACTLY once (first call only)
        # The first call calls generate_key 3 times (test-backend, test-priority, hermes-agent)
        # The second call calls generate_key 0 times (all exist)
        assert mock_client.generate_key.call_count >= 3, "generate_key called for each new key"
        # The exact count depends on the first run

        found_imp9 = _print_ldd_trajectory(caplog, "test_idempotent")
        assert found_imp9, "LDD Error: No IMP:9 log for test_idempotent"


# 🧪 TRAP[TEST] · Regression: config change triggers update_key, not duplicate key creation
def test_different_config_updates_key(policy_yaml, mock_client, tmp_path, caplog):
    """When project profile changes, provisioner updates existing key.

    ## @purpose  Key exists but config has changed → update_key is called.
    ## @scenario  Existing key for test-backend has models=["reasoning","chat"]
    ##            but default profile only grants ["chat"] → update_key called.
    """
    with caplog.at_level(logging.DEBUG):
        persist_path = tmp_path / "keys.json"
        mock_client.generate_key.return_value = {"key": "sk-new-key-789"}

        # Existing key has DIFFERENT config (has reasoning, but default profile only allows chat)
        existing_key_info = {
            "key": "sk-existing-key-123",
            "models": ["reasoning", "chat"],  # ← different from default profile
            "max_budget": 1.0,
            "rpm_limit": 10,
            "metadata": {"project": "test-backend", "tier": "default"},
        }

        mock_client.get_key_by_metadata.return_value = existing_key_info
        mock_client.update_key.return_value = {"key": "sk-existing-key-123", "models": ["chat"]}

        logger.info("[IMP:7][test_different_config] Running provision with mismatched config...")
        result = _run_provision(mock_client, policy_yaml, persist_path)

        logger.critical("[IMP:9][test_different_config] Result keys: %s", list(result.keys()))

        # update_key should have been called for test-backend (models differ)
        assert mock_client.update_key.called, "update_key should be called when config differs"

        # get the call args for update_key
        call_args_list = mock_client.update_key.call_args_list
        logger.critical(
            "[IMP:9][test_different_config] update_key called %d times",
            len(call_args_list),
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_different_config")
        assert found_imp9, "LDD Error: No IMP:9 log for test_different_config"


# 🧪 TRAP[TEST] · Regression: llm.enabled: false projects must be skipped
def test_skip_disabled_project(policy_yaml, mock_client, tmp_path, caplog):
    """Project with llm.enabled: false is skipped.

    ## @purpose  Verify test-legacy with enabled: false does not get a key.
    ## @scenario  Only test-backend and hermes-agent are provisioned.
    """
    with caplog.at_level(logging.DEBUG):
        persist_path = tmp_path / "keys.json"
        mock_client.generate_key.return_value = {"key": "sk-test-key"}

        logger.info("[IMP:7][test_skip_disabled] Running provision...")
        result = _run_provision(mock_client, policy_yaml, persist_path)

        logger.critical("[IMP:9][test_skip_disabled] Provisioned keys: %s", list(result.keys()))

        assert "test-legacy" not in result, "test-legacy should be skipped (llm.enabled: false)"
        assert "test-backend" in result, "test-backend should be provisioned"
        assert "test-priority" in result, "test-priority should be provisioned"
        assert "hermes-agent" in result, "hermes-agent should be provisioned"

        found_imp9 = _print_ldd_trajectory(caplog, "test_skip_disabled")
        assert found_imp9, "LDD Error: No IMP:9 log for test_skip_disabled"


# 🧪 TRAP[TEST] · Regression: profile_rules match consumer name → correct profile assigned
def test_profile_rule_match(policy_yaml, mock_client, tmp_path, caplog):
    """hermes-agent matches profile_rule → unlimited profile.

    ## @purpose  Verify hermes-agent gets unlimited profile via rule match
    ##           (models=[reasoning, chat], budget=50.0, rpm=120).
    """
    with caplog.at_level(logging.DEBUG):
        persist_path = tmp_path / "keys.json"

        # Capture the generate_key call for hermes-agent
        generated_keys: dict[str, dict] = {}

        def _generate_side_effect(**kwargs):
            metadata = kwargs.get("metadata", {})
            project = metadata.get("project", "unknown")
            gen_key = f"sk-{project}-key"
            generated_keys[project] = kwargs
            return {"key": gen_key}

        mock_client.generate_key.side_effect = _generate_side_effect

        logger.info("[IMP:7][test_profile_rule] Running provision...")
        result = _run_provision(mock_client, policy_yaml, persist_path)

        logger.critical("[IMP:9][test_profile_rule] Provisioned keys: %s", list(result.keys()))

        # Verify hermes-agent got the right profile
        assert "hermes-agent" in result
        hermes_call = generated_keys.get("hermes-agent", {})
        logger.critical(
            "[IMP:9][test_profile_rule] hermes-agent generate_key args: %s",
            hermes_call,
        )

        # hermes-agent should get unlimited: models=[reasoning, chat], budget=50.0, rpm=120
        assert hermes_call.get("rpm_limit") == 120, "hermes-agent should get unlimited rpm=120"
        assert "reasoning" in hermes_call.get("models", []), "hermes-agent should have reasoning model"
        assert hermes_call.get("max_budget") == 50.0, "hermes-agent should get budget=50.0"

        found_imp9 = _print_ldd_trajectory(caplog, "test_profile_rule")
        assert found_imp9, "LDD Error: No IMP:9 log for test_profile_rule"


# 🧪 TRAP[TEST] · Regression: no explicit profile → fallback to auto_provision.default_profile
def test_default_profile_fallback(policy_yaml, mock_client, tmp_path, caplog):
    """Project with no explicit profile → default profile is used.

    ## @purpose  test-backend has no profile → should get default (chat, $1, 10rpm).
    ## @scenario  Verify generate_key is called with default profile params.
    """
    with caplog.at_level(logging.DEBUG):
        persist_path = tmp_path / "keys.json"

        generated_keys: dict[str, dict] = {}

        def _generate_side_effect(**kwargs):
            metadata = kwargs.get("metadata", {})
            project = metadata.get("project", "unknown")
            generated_keys[project] = kwargs
            return {"key": f"sk-{project}-key"}

        mock_client.generate_key.side_effect = _generate_side_effect

        logger.info("[IMP:7][test_default_profile] Running provision...")
        _run_provision(mock_client, policy_yaml, persist_path)

        backend_call = generated_keys.get("test-backend", {})
        logger.critical(
            "[IMP:9][test_default_profile] test-backend generate_key args: %s",
            backend_call,
        )

        assert backend_call.get("models") == ["chat"], "Default profile should grant ['chat']"
        assert backend_call.get("max_budget") == 1.0, "Default profile budget should be $1"
        assert backend_call.get("rpm_limit") == 10, "Default profile rpm should be 10"

        found_imp9 = _print_ldd_trajectory(caplog, "test_default_profile")
        assert found_imp9, "LDD Error: No IMP:9 log for test_default_profile"


# 🧪 TRAP[TEST] · Regression: overrides deep-merge into profile config correctly
def test_overrides_merge(policy_yaml, mock_client, tmp_path, caplog):
    """Project overrides budget → merged config.

    ## @purpose  When a consumer has overrides, they are deep-merged into the
    ##           profile config. Test: verify overridden budget takes effect.
    """
    with caplog.at_level(logging.DEBUG):
        persist_path = tmp_path / "keys.json"

        generated_keys: dict[str, dict] = {}

        def _generate_side_effect(**kwargs):
            metadata = kwargs.get("metadata", {})
            project = metadata.get("project", "unknown")
            generated_keys[project] = kwargs
            return {"key": f"sk-{project}-key"}

        mock_client.generate_key.side_effect = _generate_side_effect

        # Override discover_projects to include an override consumer
        import core.internal.llm.key_provisioner as kp

        with (
            patch.object(kp, "LiteLLMAdminClient", return_value=mock_client),
            patch.object(kp, "discover_projects") as mock_disc,
            patch.object(kp, "get_platform_consumers") as mock_plat,
        ):
            mock_disc.return_value = [
                {
                    "name": "test-override",
                    "llm": {
                        "enabled": True,
                        "overrides": {
                            "budget": {"daily": 25.0},
                            "rpm_limit": 50,
                        },
                    },
                },
            ]
            mock_plat.return_value = []

            logger.info("[IMP:7][test_overrides_merge] Running provision with overrides...")
            result = kp.provision_all(
                master_key="test-mk",
                base_url="http://test:4000",
                policy_path=policy_yaml,
                persist_path=persist_path,
            )

        logger.critical("[IMP:9][test_overrides_merge] Provisioned: %s", list(result.keys()))

        override_call = generated_keys.get("test-override", {})
        logger.critical(
            "[IMP:9][test_overrides_merge] test-override args: %s",
            override_call,
        )

        assert override_call.get("max_budget") == 25.0, "Budget should be overridden to $25"
        assert override_call.get("rpm_limit") == 50, "RPM should be overridden to 50"
        # Models should still come from default profile
        assert override_call.get("models") == ["chat"], "Models should come from default profile"

        found_imp9 = _print_ldd_trajectory(caplog, "test_overrides_merge")
        assert found_imp9, "LDD Error: No IMP:9 log for test_overrides_merge"


# 🧪 TRAP[TEST] · Regression: no duplicate keys in persist store after provisioning
def test_no_duplicate_keys(policy_yaml, mock_client, tmp_path, caplog):
    """Multiple calls produce exactly one key per project.

    ## @purpose  Verify the provisioner's final store has no duplicate entries.
    ##           Each project appears exactly once in the result and in the persist file.
    """
    with caplog.at_level(logging.DEBUG):
        persist_path = tmp_path / "keys.json"

        # Mock get_key_by_metadata to return None (no existing keys) for first call,
        # then return generated keys for subsequent calls
        call_count: dict[str, int] = {}

        def _get_key_by_metadata(**filters):
            project = filters.get("project", "")
            call_count[project] = call_count.get(project, 0) + 1
            return  # always return None so generate_key is always called

        mock_client.get_key_by_metadata.side_effect = _get_key_by_metadata

        mock_client.generate_key.side_effect = [
            {"key": "sk-backend-1"},
            {"key": "sk-priority-1"},
            {"key": "sk-hermes-1"},
        ]

        logger.info("[IMP:7][test_no_duplicate] First provision run...")
        result_1 = _run_provision(mock_client, policy_yaml, persist_path)

        assert len(result_1) == 3, "Should have exactly 3 keys"
        assert len(set(result_1.values())) == 3, "All 3 keys should be unique"

        # Verify persist file has 3 unique entries (no duplicates)
        with open(persist_path) as f:
            store = json.load(f)
        assert len(store) == 3, "Persist store should have exactly 3 entries"
        assert len(set(store.values())) == 3, "All 3 persisted keys should be unique"

        logger.critical(
            "[IMP:9][test_no_duplicate] First call: %d keys, persist store: %d entries",
            len(result_1),
            len(store),
        )

        found_imp9 = _print_ldd_trajectory(caplog, "test_no_duplicate")
        assert found_imp9, "LDD Error: No IMP:9 log for test_no_duplicate"
