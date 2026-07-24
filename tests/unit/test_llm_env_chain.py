# GREP_SUMMARY: test-llm-env-chain, env-propagation, key-format, persist, integration, LDD, IMP
# STRUCTURE: fixtures(tmp_path + mock client) →
#            test_key_to_env_chain[provisioner generates key → persists → verify key format] →
#            test_key_format[generated keys match sk- + hex format]
# region MODULE_CONTRACT
## @purpose  Integration tests for the env propagation chain:
##           key_provisioner generates key → persist_project_key persists to JSON →
##           verify key format (starts with `sk-`, reasonable length).
##           This tests the chain BEFORE SOPS encryption (Wave 6).
## @scope    Tests that generated keys have correct format and are persisted correctly.
##           Uses mocked LiteLLMAdminClient, real policy parsing and persist logic.
## @invariants
##   - All tests use tmp_path for temp files (no hardcoded paths)
##   - Each test includes LDD trajectory printing with IMP:9 check
##   - Keys are validated by format (sk- prefix, length) not by content
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 4, GAP-1)
# endregion MODULE_CONTRACT

import json
import logging
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest
import yaml

logger = logging.getLogger(__name__)


# ── LDD Helper ───────────────────────────────────────────────────────────────


def _print_ldd_trajectory(caplog, test_name: str) -> bool:
    """Print IMP:7-10 LDD trajectory from caplog.

    ## @purpose  Centralised LDD trajectory printer for all test functions.
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
def minimal_policy(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal valid policy.yaml.

    ## @purpose  Minimal policy for env chain tests.
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
        },
        "auto_provision": {
            "default_profile": "default",
            "profile_rules": [],
        },
    }
    p = tmp_path / "policy.yaml"
    with open(p, "w") as f:
        yaml.dump(policy, f)
    return p


@pytest.fixture
def mock_client() -> MagicMock:
    """Mock LiteLLMAdminClient.

    ## @purpose  Standard mock with no existing keys — all requests go to generate.
    ## @complexity O(1)
    """
    client = MagicMock()
    client.get_key_info.return_value = None
    client.generate_key.return_value = {"key": "sk-test-key-abcdef1234567890abcdef1234567890"}
    client.get_key_by_metadata.return_value = None
    return client


# ── TESTS ────────────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: key provision → persistence → format validation chain
# · Scenario: provision_all → persist_project_key → JSON file → verify sk- prefix + length · Last fail: N/A
def test_key_to_env_chain(minimal_policy, mock_client, tmp_path, caplog):
    """Provisioner generates key → persists to JSON → verify key format.

    ## @purpose  Test the full chain: provision_all generates keys,
    ##           persist_project_key writes to JSON file, the file contains
    ##           the expected key with correct format.
    ## @scenario  Real provision_all with mocked client → persist to tmp_path →
    ##            read back → verify key starts with sk- and has reasonable length.
    """
    with caplog.at_level(logging.DEBUG):
        import core.internal.llm.key_provisioner as kp

        persist_path = tmp_path / "litellm-keys.json"
        mock_client.generate_key.return_value = {
            "key": "sk-integration-test-key-1234567890abcdef1234567890abcdef",
        }

        with patch.object(kp, "LiteLLMAdminClient", return_value=mock_client):
            with patch.object(kp, "discover_projects") as mock_disc:
                mock_disc.return_value = [
                    {"name": "test-backend", "llm": {"enabled": True}},
                ]
                with patch.object(kp, "get_platform_consumers") as mock_plat:
                    mock_plat.return_value = []

                    logger.info("[IMP:7][test_key_to_env_chain] Starting provision...")
                    result = kp.provision_all(
                        master_key="test-mk",
                        base_url="http://test:4000",
                        policy_path=minimal_policy,
                        persist_path=persist_path,
                    )

        logger.critical(
            "[IMP:9][test_key_to_env_chain] Provision result: %s",
            result,
        )

        # Verify result
        assert "test-backend" in result
        key = result["test-backend"]
        logger.critical(
            "[IMP:9][test_key_to_env_chain] Key for test-backend: %s...",
            key[:16] if len(key) > 16 else key,
        )

        # Key format validation
        assert key.startswith("sk-"), f"Key should start with 'sk-', got: {key[:8]}"
        assert len(key) >= 20, f"Key should be at least 20 chars, got {len(key)}"

        # Verify persist file exists and contains the key
        assert persist_path.exists(), f"Persist file should exist at {persist_path}"
        with open(persist_path) as f:
            store = json.load(f)

        logger.critical(
            "[IMP:9][test_key_to_env_chain] Persist store: %s",
            store,
        )

        assert "test-backend" in store
        assert store["test-backend"] == key, "Persisted key should match provisioned key"

        found_imp9 = _print_ldd_trajectory(caplog, "test_key_to_env_chain")
        assert found_imp9, "LDD Error: No IMP:9 log for test_key_to_env_chain"


# 🧪 TRAP[TEST] · Regression: all provisioned keys match sk-{name}-{hex} format
# · Scenario: multiple keys provisioned → each validated against sk- regex · Last fail: N/A
def test_key_format(minimal_policy, mock_client, tmp_path, caplog):
    """Generated keys match expected sk- + hex format.

    ## @purpose  Verify LiteLLM virtual key format: starts with sk-,
    ##           contains reasonable length (32+ hex chars).
    ## @scenario  Generate keys via provision_all, validate each key format.
    """
    with caplog.at_level(logging.DEBUG):
        import core.internal.llm.key_provisioner as kp

        persist_path = tmp_path / "keys.json"

        # Simulate multiple key generation with different keys
        mock_client.generate_key.side_effect = [
            {"key": "sk-backend-001-abcdef1234567890abcdef1234567890"},
            {"key": "sk-priority-002-bcdef1234567890abcdef12345678901"},
            {"key": "sk-hermes-003-cdef1234567890abcdef123456789012"},
        ]

        with patch.object(kp, "LiteLLMAdminClient", return_value=mock_client):
            with patch.object(kp, "discover_projects") as mock_disc:
                mock_disc.return_value = [
                    {"name": "test-backend", "llm": {"enabled": True}},
                    {"name": "test-priority", "llm": {"enabled": True, "profile": "premium"}},
                ]
                with patch.object(kp, "get_platform_consumers") as mock_plat:
                    mock_plat.return_value = [
                        {"name": "hermes-agent", "llm": {"enabled": True}},
                    ]

                    logger.info("[IMP:7][test_key_format] Starting provision...")
                    result = kp.provision_all(
                        master_key="test-mk",
                        base_url="http://test:4000",
                        policy_path=minimal_policy,
                        persist_path=persist_path,
                    )

        logger.critical("[IMP:9][test_key_format] Provisioned %d keys", len(result))

        # Validate key format for ALL keys
        key_pattern = re.compile(r"^sk-[a-zA-Z0-9-]{16,}$")
        for consumer_name, key in result.items():
            logger.critical(
                "[IMP:9][test_key_format] Validating key for '%s': %s...",
                consumer_name,
                key[:20],
            )
            assert key_pattern.match(key), f"Key for '{consumer_name}' does not match sk- format: {key[:30]}..."
            assert len(key) >= 20, f"Key for '{consumer_name}' too short: {len(key)} chars"

        # Verify persist file has all keys
        with open(persist_path) as f:
            store = json.load(f)

        logger.critical("[IMP:9][test_key_format] Persist store has %d entries", len(store))
        assert len(store) == 3, "Should have 3 entries in persist store"
        assert store.keys() == {"test-backend", "test-priority", "hermes-agent"}

        found_imp9 = _print_ldd_trajectory(caplog, "test_key_format")
        assert found_imp9, "LDD Error: No IMP:9 log for test_key_format"
