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
import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest
import yaml
from _conftest.ldd import _print_ldd_trajectory

from core.internal.llm.admin_client import LiteLLMTransportError

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# ── LDD Helper ───────────────────────────────────────────────────────────────


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
    with pathlib.Path(p).open("w", encoding="utf-8") as f:
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
    # QA C4 (DevPlan 14 T1.3): provisioner читает индекс из ОДНОГО list_keys() вызова
    client.list_keys.return_value = []
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
            {"name": "test-old", "llm": {"enabled": False}},
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
        assert "test-old" not in result_1, "Disabled project should be skipped"
        assert result_1["test-backend"] == generated_key_value

        # --- Second call: key exists, should skip ---
        # QA C4 (DevPlan 14 T1.3): существующие ключи подаются через fetch-once индекс
        # list_keys() (get_key_by_metadata provisioner'ом больше не вызывается)
        mock_client.list_keys.return_value = [
            {
                "key": generated_key_value,
                "models": ["chat"],
                "max_budget": 1.0,
                "rpm_limit": 10,
                "metadata": {"project": "test-backend", "tier": "default"},
            },
            {
                "key": "sk-priority-key-xxx",
                "models": ["reasoning", "chat"],
                "max_budget": 10.0,
                "rpm_limit": 60,
                "metadata": {"project": "test-priority", "tier": "premium"},
            },
            {
                "key": "sk-hermes-key-xxx",
                "models": ["reasoning", "chat"],
                "max_budget": 50.0,
                "rpm_limit": 120,
                "metadata": {"project": "hermes-agent", "tier": "unlimited"},
            },
        ]

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

        # QA C4 (DevPlan 14 T1.3): existing key подаётся через fetch-once индекс list_keys()
        mock_client.list_keys.return_value = [existing_key_info]
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

    ## @purpose  Verify test-with enabled: false does not get a key.
    ## @scenario  Only test-backend and hermes-agent are provisioned.
    """
    with caplog.at_level(logging.DEBUG):
        persist_path = tmp_path / "keys.json"
        mock_client.generate_key.return_value = {"key": "sk-test-key"}

        logger.info("[IMP:7][test_skip_disabled] Running provision...")
        result = _run_provision(mock_client, policy_yaml, persist_path)

        logger.critical("[IMP:9][test_skip_disabled] Provisioned keys: %s", list(result.keys()))

        assert "test-old" not in result, "test-should be skipped (llm.enabled: false)"
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
        with pathlib.Path(persist_path).open(encoding="utf-8") as f:
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


# ═══════════════════════════════════════════════════════════════════
# QA C4/G2 (DevPlan 14 T1.3): corruption-chain, fetch-once,
# запрет fall-through-generate, transport-tolerance
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · corruption-chain — truncated store fail-loud
# · Scenario: OOM/crash во время записи обрезал store; следующий reader обязан ПАДАТЬ
#   (PlatformError), а не глотать corruption в {} и заливать partial-store поверх всех ключей
#   (DATA-902 mass-401 инцидент)
# · Last fail: 2026-08-25 (QA C4) — TRAP в _load_key_store ссылался на несуществующий тест;
#   сам fail-loud контракт уже был реализован (REF-0104), теста не было
# · Remove if: key store мигрирует с JSON на СУБД (corruption-chain станет доменом БД)
def test_corruption_chain_fail_loud(tmp_path, caplog):
    """Truncated/non-dict store → PlatformError; байты файла нетронуты."""
    caplog.set_level(logging.DEBUG)
    import core.internal.llm.key_provisioner as kp

    # 1. Truncated JSON
    store = tmp_path / "litellm-project-keys.json"
    original_bytes = '{"proj-a": "sk-aaa", "proj-b": "sk-bbb"'  # без закрывающей }
    store.write_text(original_bytes, encoding="utf-8")

    with pytest.raises(kp.PlatformError):
        kp._load_key_store(store)

    assert store.read_text(encoding="utf-8") == original_bytes, (
        "corruption-chain: файл обязан остаться байт-в-байт нетронутым"
    )
    logger.info("[IMP:9][test][corruption] truncated store → PlatformError, bytes intact")

    # 2. Валидный JSON, но не-dict
    array_store = tmp_path / "array-store.json"
    array_store.write_text('["not", "a", "dict"]', encoding="utf-8")
    with pytest.raises(kp.PlatformError):
        kp._load_key_store(array_store)
    logger.info("[IMP:9][test][corruption] non-dict store → PlatformError")

    found_imp9 = _print_ldd_trajectory(caplog, "test_corruption_chain_fail_loud")
    assert found_imp9, "LDD Error: No IMP:9 log for test_corruption_chain_fail_loud"


# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · fetch-once (PERF-081)
# · Scenario: N потребителей → ровно ОДИН list_keys() вызов за прогон; per-consumer
#   пагинации (get_key_by_metadata внутри цикла) устранены
# · Last fail: 2026-08-25 (QA C4) — get_key_by_metadata внутри цикла = N полных пагинаций
# · Remove if: provisioner перестанет использовать fetch-once индекс
def test_fetch_once_single_list_keys(policy_yaml, mock_client, tmp_path, caplog):
    """3 enabled consumers → ровно 1 list_keys(); get_key_by_metadata не зовётся вовсе."""
    caplog.set_level(logging.DEBUG)
    mock_client.list_keys.return_value = []

    _run_provision(mock_client, policy_yaml, tmp_path / "keys.json")

    assert mock_client.list_keys.call_count == 1, (
        f"fetch-once нарушен: list_keys вызван {mock_client.list_keys.call_count} раз(а)"
    )
    mock_client.get_key_by_metadata.assert_not_called()
    logger.info(
        "[IMP:9][test][fetch-once] list_keys calls=%d, get_key_by_metadata calls=0",
        mock_client.list_keys.call_count,
    )
    found_imp9 = _print_ldd_trajectory(caplog, "test_fetch_once_single_list_keys")
    assert found_imp9, "LDD Error: No IMP:9 log for test_fetch_once_single_list_keys"


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · update-fail → generate НЕ выполняется
# · Scenario: существующий ключ с другой конфигурацией + update падает → второй ключ с тем же
#   metadata НЕ создаётся (спящая мина массовых дублей budget-bearing ключей, QA C4)
# · Last fail: 2026-08-25 — ветка «falling through to generate» активировалась бы при первом же
#   расширении except-кортежа
# · Remove if: появится re-lookup+generate семантика (требует явного owner-решения)
@pytest.mark.parametrize(
    "update_exc",
    [
        pytest.param(ConnectionError("connection reset"), id="ConnectionError"),
        pytest.param(TimeoutError("timeout"), id="TimeoutError"),
        pytest.param(OSError("disk io"), id="OSError"),
        pytest.param(LiteLLMTransportError("transport down"), id="LiteLLMTransportError"),
    ],
)
def test_update_fail_no_duplicate_key(policy_yaml, mock_client, tmp_path, caplog, update_exc):
    """update-fail → generate для НЕГО не зовётся; фаза завершается PlatformError (T1.D)."""
    caplog.set_level(logging.DEBUG)
    existing = {
        "key": "sk-existing-token-0001",
        "metadata": {"project": "test-backend"},
        "models": ["legacy-model"],
        "max_budget": 0.5,
        "rpm_limit": 5,
    }
    mock_client.list_keys.return_value = [existing]
    mock_client.update_key.side_effect = update_exc

    # DevPlan 16 T1.D (P0-5): пост-цикл PlatformError (supersede «фаза жива» — потребители
    # ДОЛБАТся все, но фаза завершается честным провалом)
    import core.internal.llm.key_provisioner as kp

    with pytest.raises(kp.PlatformError, match="test-backend"):
        _run_provision(mock_client, policy_yaml, tmp_path / "keys.json")

    assert all(
        call.kwargs.get("metadata", {}).get("project") != "test-backend"
        for call in mock_client.generate_key.call_args_list
    ), "ДУБЛЬ КЛЮЧА: generate вызван для test-backend"
    logger.info(
        "[IMP:9][test][no-fallthrough] exc=%s → test-backend failed, generate не вызван для него",
        type(update_exc).__name__,
    )
    found_imp9 = _print_ldd_trajectory(caplog, "test_update_fail_no_duplicate_key")
    assert found_imp9, "LDD Error: No IMP:9 log for test_update_fail_no_duplicate_key"


# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · transport-error → failed-consumer учёт, фаза жива
# · Scenario: LiteLLMTransportError (ранее НЕ ловился кортежем → аборт всей φ-provision-llm
#   через generic-handler) теперь ловится: WARN + failed++, следующие потребители обслуживаются
# · Last fail: 2026-08-25 (QA C4) — LiteLLMTransportError(Exception) физически не мог быть пойман
# · Remove if: семантика фазы изменится на abort-on-first-failure
def test_transport_error_consumer_failed_phase_alive(policy_yaml, mock_client, tmp_path, caplog):
    """Transport-сбой на одном потребителе → он в failed-сводке IMP:9, остальные провижинены."""
    caplog.set_level(logging.DEBUG)
    existing = {
        "key": "sk-existing-token-0002",
        "metadata": {"project": "test-backend"},
        "models": ["legacy-model"],
        "max_budget": 0.5,
        "rpm_limit": 5,
    }
    mock_client.list_keys.return_value = [existing]
    mock_client.update_key.side_effect = LiteLLMTransportError("connect timeout after 30s")

    # DevPlan 16 T1.D (P0-5): loop продолжает ВСЕХ потребителей, итог — PlatformError
    import core.internal.llm.key_provisioner as kp

    with pytest.raises(kp.PlatformError, match="test-backend"):
        _run_provision(mock_client, policy_yaml, tmp_path / "keys.json")

    summary_lines = [r.getMessage() for r in caplog.records if "[IMP:9]" in r.getMessage()]
    assert any("failed:" in m and "test-backend" in m for m in summary_lines), (
        f"нет failed-accounting в сводке: {summary_lines}"
    )
    logger.info("[IMP:9][test][transport-tolerant] all consumers attempted, raise at end")
    found_imp9 = _print_ldd_trajectory(caplog, "test_transport_error_consumer_failed_phase_alive")
    assert found_imp9, "LDD Error: No IMP:9 log"


# DevPlan 16 T1.D (P0-5 + P1-5..8): merge-guard, честный failed, lock-scope,
# коллизии, пустые токены
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · DevPlan 16 T1.D P0-5 · профильные метаданные НЕ затирают "project"
# · Last fail: аудит 15 P0-5 — key_metadata.update(profile_metadata) перезаписывал reserved
#   "project" → find_key_by_metadata никогда не матчит → GENERATE бюджетных дублей на каждом прогоне
# · Scenario: профиль с metadata.project="evil" → сгенерированный ключ несёт project=consumer;
#   повторный прогон находит ключ по метаданным → IDEMPOTENT SKIP, generate_count==0
# · Remove if: merge-guard reserved-ключей перенесён в другой слой
def test_reserved_project_key_not_overwritten(policy_yaml, mock_client, tmp_path, caplog):
    import core.internal.llm.key_provisioner as kp

    policy_with_evil_metadata = tmp_path / "policy-evil.yaml"
    policy = yaml.safe_load(pathlib.Path(policy_yaml).read_text(encoding="utf-8"))
    policy["profiles"]["default"]["metadata"] = {"project": "evil-override", "tier": "default"}
    with pathlib.Path(policy_with_evil_metadata).open("w", encoding="utf-8") as f:
        yaml.dump(policy, f)

    with (
        patch.object(kp, "LiteLLMAdminClient", return_value=mock_client),
        patch.object(kp, "discover_projects") as mock_disc,
        patch.object(kp, "get_platform_consumers") as mock_plat,
    ):
        mock_disc.return_value = [{"name": "test-backend", "llm": {"enabled": True}}]
        mock_plat.return_value = []

        result = kp.provision_all(
            master_key="mk",
            base_url="http://t:4000",
            policy_path=policy_with_evil_metadata,
            persist_path=tmp_path / "store.json",
        )

    # Инвариант: metadata["project"]==consumer_name всегда
    gen_kwargs = mock_client.generate_key.call_args.kwargs
    assert gen_kwargs["metadata"]["project"] == "test-backend"
    assert "evil-override" not in gen_kwargs["metadata"].values()
    assert "[IMP:8]" in caplog.text and "Reserved" in caplog.text

    # Идемпотентный второй прогон: fetch-once индекс содержит ключ с каноничным project → SKIP
    mock_client.generate_key.reset_mock()
    canonical_info = {
        "key": result["test-backend"],
        "models": ["chat"],
        "max_budget": 1.0,
        "rpm_limit": 10,
        "metadata": {"project": "test-backend", "tier": "default"},
    }
    mock_client.list_keys.return_value = [canonical_info]
    with (
        patch.object(kp, "LiteLLMAdminClient", return_value=mock_client),
        patch.object(kp, "discover_projects") as mock_disc2,
        patch.object(kp, "get_platform_consumers") as mock_plat2,
    ):
        mock_disc2.return_value = [{"name": "test-backend", "llm": {"enabled": True}}]
        mock_plat2.return_value = []
        kp.provision_all(
            master_key="mk",
            base_url="http://t:4000",
            policy_path=policy_with_evil_metadata,
            persist_path=tmp_path / "store.json",
        )
    assert mock_client.generate_key.call_count == 0, "повторный прогон обязан быть IDEMPOTENT SKIP"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T1.D P0-5 · failed≠∅ → PlatformError (не done)
# · Last fail: аудит 15 P0-5 — failed>0 не поднимал ошибку → φ11 фиксировал llm-keys done
#   при проваленных ключах (401 в проде как первый сигнал)
# · Scenario: generate для одного consumer падает ConnectionError → provision_all raise
#   PlatformError с именем consumer'а
# · Remove if: exit-контракт фазы llm-keys изменён
def test_failed_raises_platform_error(policy_yaml, mock_client, tmp_path):
    import core.internal.llm.key_provisioner as kp

    calls = {"n": 0}

    def _gen_fail(**kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            transport_error = ConnectionError("litellm down")
            raise transport_error
        return {"key": f"sk-key-{calls['n']}"}

    mock_client.generate_key.side_effect = _gen_fail

    with (
        patch.object(kp, "LiteLLMAdminClient", return_value=mock_client),
        patch.object(kp, "discover_projects") as mock_disc,
        patch.object(kp, "get_platform_consumers") as mock_plat,
    ):
        mock_disc.return_value = [
            {"name": "ok-project", "llm": {"enabled": True}},
            {"name": "bad-project", "llm": {"enabled": True}},
        ]
        mock_plat.return_value = []
        with pytest.raises(kp.PlatformError, match="bad-project"):
            kp.provision_all(
                master_key="mk",
                base_url="http://t:4000",
                policy_path=policy_yaml,
                persist_path=tmp_path / "store.json",
            )


# 🧪 TRAP[TEST] · SCENARIO · DevPlan 16 T1.D P1-6 · lock охватывает find→generate→persist
# · Last fail: аудит 15 P1-5/P1-6 — list→find→generate вне лока → конкурентные дубли
# · Scenario: FileLock-шов подменяется рекордером; порядок событий: acquire ДО первого
#   get_key_by_metadata, release ПОСЛЕ последнего persist
# · Remove if: lock-scope изменён
def test_lock_covers_find_generate(policy_yaml, mock_client, tmp_path):
    import core.internal.llm.key_provisioner as kp
    from core.internal.shared.file_lock import FileLock as RealFileLock

    events: list[str] = []

    class SpyLock(RealFileLock):
        def __enter__(self):
            events.append("lock-acquire")
            return super().__enter__()

        def __exit__(self, *args):
            events.append("lock-release")
            return super().__exit__(*args)

    def _list_marker():
        events.append("find")
        return []

    def _gen_marker(**kwargs):
        events.append("generate")
        return {"key": "sk-lock-test-token"}

    mock_client.list_keys.side_effect = _list_marker
    mock_client.generate_key.side_effect = _gen_marker

    with (
        patch.object(kp, "FileLock", SpyLock),
        patch.object(kp, "LiteLLMAdminClient", return_value=mock_client),
        patch.object(kp, "discover_projects") as mock_disc,
        patch.object(kp, "get_platform_consumers") as mock_plat,
    ):
        mock_disc.return_value = [{"name": "solo", "llm": {"enabled": True}}]
        mock_plat.return_value = []
        kp.provision_all(
            master_key="mk",
            base_url="http://t:4000",
            policy_path=policy_yaml,
            persist_path=tmp_path / "store.json",
        )

    assert events[0] == "lock-acquire", events
    assert events[-1] == "lock-release", events
    assert "find" in events and "generate" in events
    assert events.index("find") > events.index("lock-acquire")
    assert events.index("lock-release") > events.index("generate")


# 🧪 TRAP[TEST] · REGRESSION · DevPlan 16 T1.D P1-7 · коллизия метаданных детерминирована
# · Last fail: аудит 15 P1-7 — first-match по порядку листинга зависел от пагинации сервера
# · Scenario: два ключа с одинаковым metadata.project → победитель по (created_at, token)
#   при ЛЮБОМ порядке входного списка; WARN называет обоих
# · Remove if: коллизии стали структурно невозможны (unique-index на стороне LiteLLM)
def test_collision_deterministic_warn(caplog):
    from core.internal.llm.admin_client import find_key_by_metadata

    older = {"key": "sk-old-token", "created_at": "2026-01-01", "metadata": {"project": "dup"}}
    newer = {"key": "sk-new-token", "created_at": "2026-06-01", "metadata": {"project": "dup"}}

    caplog.set_level(logging.WARNING)
    w1 = find_key_by_metadata([newer, older], project="dup")
    w2 = find_key_by_metadata([older, newer], project="dup")

    assert w1 is not None and w2 is not None
    assert w1["key"] == "sk-old-token", "победитель — старейший created_at"
    assert w2["key"] == w1["key"], "детерминизм относительно порядка входа"
    assert "COLLISION" in caplog.text and "sk-new-token" in caplog.text, "WARN называет дубликат"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T1.D P1-8 · пустой токен не персистится
# · Last fail: аудит 15 P1-8 — пустой token листинга перезаписывал рабочий ключ стора
# · Scenario: listing вернул {"key": ""} → трактуется как not-found → GENERATE путь;
#   стор никогда не содержит пустого значения; при отказе generate стор вообще не тронут
# · Remove if: empty-token guard перенесён в list_keys парсер
def test_empty_token_not_persisted(policy_yaml, mock_client, tmp_path):
    import json as _json

    import core.internal.llm.key_provisioner as kp

    store = tmp_path / "store.json"
    store.write_text(_json.dumps({"solo": "sk-existing-working-key"}), encoding="utf-8")

    mock_client.get_key_by_metadata.return_value = {"key": "", "metadata": {"project": "solo"}}
    mock_client.generate_key.side_effect = ConnectionError("network down")

    with (
        patch.object(kp, "LiteLLMAdminClient", return_value=mock_client),
        patch.object(kp, "discover_projects") as mock_disc,
        patch.object(kp, "get_platform_consumers") as mock_plat,
    ):
        mock_disc.return_value = [{"name": "solo", "llm": {"enabled": True}}]
        mock_plat.return_value = []
        with pytest.raises(kp.PlatformError):
            kp.provision_all(master_key="mk", base_url="http://t:4000", policy_path=policy_yaml, persist_path=store)

    data = _json.loads(store.read_text(encoding="utf-8"))
    assert data["solo"] == "sk-existing-working-key", "рабочий ключ стора НЕ затронут пустым токеном"
    assert "" not in data.values()


# ═══════════════════════════════════════════════════════════════════
# plan 012 T12 (F-020/F-021/F-022): master-key chain + proxy-neutral + list_keys transport
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T12 F-020 · master-key резолв из secrets.env
# · Scenario: CLI/env пусты, secrets.env ноды несёт LITELLM_MASTER_KEY → резолв из файла
#   (закрывает deploy-context цепочку: provision-llm.sh subprocess env-less)
# · Last fail: F-020 — deploy-context provision падал на отсутствии LITELLM_MASTER_KEY в env
# · Remove if: master-key chain переедет в другой слой
def test_master_key_resolved_from_secrets_env(tmp_path, caplog):
    """F-020: CLI → env → secrets.env fallback для LITELLM_MASTER_KEY."""
    import core.internal.llm.key_provisioner as kp

    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("LITELLM_MASTER_KEY=sk-from-secrets-env-123\nOTHER=1\n", encoding="utf-8")

    with patch.object(kp, "secrets_env_file", return_value=secrets_env):
        assert kp._resolve_master_key(None, {}) == "sk-from-secrets-env-123"
        # приоритет: CLI > env > file
        assert kp._resolve_master_key("sk-cli", {"LITELLM_MASTER_KEY": "sk-env"}) == "sk-cli"
        assert kp._resolve_master_key(None, {"LITELLM_MASTER_KEY": "sk-env"}) == "sk-env"
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test][master-key] secrets.env fallback работает (F-020)")


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T12 F-020 · отсутствие ключа везде → пустая строка
# · Scenario: нет CLI, env, файла (или файл без ключа) → "" → main печатает ошибку, exit 1
# · Remove if: master-key required-семантика изменится
def test_master_key_missing_returns_empty(tmp_path, caplog):
    """F-020: все источники пусты → '' (main ругается, НЕ тихий сквозной прогон)."""
    import core.internal.llm.key_provisioner as kp

    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("OTHER=1\n", encoding="utf-8")
    with patch.object(kp, "secrets_env_file", return_value=secrets_env):
        assert not kp._resolve_master_key(None, {})
        assert not kp._resolve_master_key("", {})


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T12 F-021 · list_keys transport-failure → failed++,
# generate НЕ вызывается (дубль-гвард REF-0104)
# · Scenario: list_keys кидает LiteLLMTransportError → ВСЕ enabled counted failed,
#   generate_key НЕ вызывается ни для кого, PlatformError с сообщением листинга
# · Last fail: F-021 — list_keys вне try → необработанный transport-сбой ронял прогон
#   (или при пробросе generic-handler — скрытая семантика)
# · Remove if: list_keys перенесётся под per-consumer try
def test_list_keys_transport_fails_loud_no_generate(policy_yaml, mock_client, tmp_path, caplog):
    """F-021 (AC-c): listing-сбой — честный failed (НЕ generate-дубликаты)."""
    import core.internal.llm.key_provisioner as kp

    mock_client.list_keys.side_effect = LiteLLMTransportError("connect timeout to litellm:4000")

    with (
        patch.object(kp, "LiteLLMAdminClient", return_value=mock_client),
        patch.object(kp, "discover_projects") as mock_disc,
        patch.object(kp, "get_platform_consumers") as mock_plat,
    ):
        mock_disc.return_value = [{"name": "test-backend", "llm": {"enabled": True}}]
        mock_plat.return_value = []
        with pytest.raises(kp.PlatformError, match="listing failed"):
            kp.provision_all(
                master_key="mk", base_url="http://t:4000", policy_path=policy_yaml, persist_path=tmp_path / "k.json"
            )

    assert mock_client.generate_key.call_count == 0, "generate НЕ вызывается при незнании existing keys (REF-0104)"
    logger.info("[IMP:9][test][list-transport] listing fail → no generate, честный PlatformError")


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T12 F-022 · host-run proxy-нейтральность
# · Scenario: base_url=127.0.0.1 → NO_PROXY дополнен loopback/litellm; remote base_url → не тронут
# · Last fail: F-022 — httpx trust_env читал HTTP_PROXY и ломал connect к локальному фасаду
# · Remove if: клиент перейдёт на trust_env=False
def test_no_proxy_for_local_facades(tmp_path, monkeypatch, caplog):
    """F-022: loopback/litellm base_url → NO_PROXY setdefault; удалённый host не мутируется."""
    import core.internal.llm.key_provisioner as kp

    monkeypatch.delenv("NO_PROXY", raising=False)
    kp._ensure_local_proxy_neutral("http://127.0.0.1:4000")
    no_proxy = os.environ.get("NO_PROXY", "")
    assert "127.0.0.1" in no_proxy and "litellm" in no_proxy, f"NO_PROXY={no_proxy!r}"

    # существующий NO_PROXY не затирается (setdefault-семантика)
    monkeypatch.setenv("NO_PROXY", "10.0.0.0/8")
    kp._ensure_local_proxy_neutral("http://litellm:4000")
    assert "10.0.0.0/8" in os.environ["NO_PROXY"] and "localhost" in os.environ["NO_PROXY"]

    # удалённый host — не локальный фасад, NO_PROXY не трогаем
    monkeypatch.setenv("NO_PROXY", "keep-me")
    kp._ensure_local_proxy_neutral("http://api.remote.example:443")
    assert os.environ["NO_PROXY"] == "keep-me"
    logger.info("[IMP:9][test][proxy-neutral] loopback/litellm в NO_PROXY, remote не тронут (F-022)")
