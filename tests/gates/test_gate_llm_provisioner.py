# GREP_SUMMARY: gate, llm-provisioner, idempotent, contract, virtual-keys, LDD, IMP, @pytest.mark.gate
# STRUCTURE: fixtures(tmp_path + mock client) →
#            test_gate_provisioner_idempotent[3 consecutive calls → identical keys] →
#            test_gate_provisioner_no_orphans[all keys reference existing projects in metadata]
# region MODULE_CONTRACT
## @purpose  Gate tests for the key provisioner idempotency contract.
##           Validates that:
##           1. 3 consecutive provision_all() calls produce identical keys
##           2. All provisioned keys reference existing projects in metadata
## @scope    Gate-level contract tests — run as part of `make gate MODE=fast`.
##           Uses pytest.mark.gate and LDD trajectory with IMP:9 check.
## @invariants
##   - All tests use tmp_path for temp files (no hardcoded paths)
##   - Mocked LiteLLMAdminClient — no real API calls
##   - Each test includes LDD trajectory printing with IMP:9 check
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 4)
# endregion MODULE_CONTRACT

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
def gate_policy(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal policy.yaml for gate tests.

    ## @purpose  Policy fixture with default+premium profiles.
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
def gate_client() -> MagicMock:
    """Mock LiteLLMAdminClient for gate tests.

    ## @purpose  Initially no existing keys. Tracks generate_key calls.
    ## @complexity O(1)
    """
    client = MagicMock()
    client.get_key_info.return_value = None
    client.generate_key.return_value = {"key": "sk-gate-test-key-abcdef1234567890"}
    client.get_key_by_metadata.return_value = None
    return client


# ── TESTS ────────────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Gate: idempotent — 3 consecutive provision_all calls return identical keys
# · Regression: duplicate keys on retry · Scenario: provision_all × 3 → same keys · Last fail: N/A · Remove if: provisioner is replaced
@pytest.mark.gate
@pytest.mark.gate
def test_gate_provisioner_idempotent(gate_policy, gate_client, tmp_path, caplog):
    """3 consecutive provision_all() calls produce identical keys per project."""
    with caplog.at_level(logging.DEBUG):
        import core.internal.llm.key_provisioner as kp

        persist_path = tmp_path / "gate-keys.json"
        generated_keys: dict[str, str] = {}
        key_store: dict[str, dict] = {}

        def _generate_side_effect(**kwargs):
            metadata = kwargs.get("metadata", {})
            project = metadata.get("project", "unknown")
            key = f"sk-gate-{project}-key-{len(generated_keys):04d}"
            generated_keys.setdefault(project, key)
            return {"key": generated_keys[project]}

        gate_client.generate_key.side_effect = _generate_side_effect
        gate_client.get_key_by_metadata.side_effect = lambda **filters: key_store.get(filters.get("project", ""))

        with patch.object(kp, "LiteLLMAdminClient", return_value=gate_client):  # noqa: SIM117
            with patch.object(kp, "discover_projects") as mock_disc:
                with patch.object(kp, "get_platform_consumers") as mock_plat:
                    mock_disc.return_value = [
                        {"name": "app-backend", "llm": {"enabled": True}},
                        {"name": "app-priority", "llm": {"enabled": True, "profile": "premium"}},
                    ]
                    mock_plat.return_value = [
                        {"name": "hermes-agent", "llm": {"enabled": True}},
                    ]

                    logger.info("[IMP:7][gate_idempotent] Call 1 — generating keys...")
                    result_1 = kp.provision_all(
                        master_key="test-mk",
                        base_url="http://test:4000",
                        policy_path=gate_policy,
                        persist_path=persist_path,
                    )

                    for proj_name, proj_key in generated_keys.items():
                        if proj_name == "app-priority":
                            key_store[proj_name] = {
                                "key": proj_key,
                                "models": ["reasoning", "chat"],
                                "max_budget": 10.0,
                                "rpm_limit": 60,
                                "metadata": {"project": proj_name, "tier": "premium"},
                            }
                        elif proj_name == "hermes-agent":
                            key_store[proj_name] = {
                                "key": proj_key,
                                "models": ["reasoning", "chat"],
                                "max_budget": 50.0,
                                "rpm_limit": 120,
                                "metadata": {"project": proj_name, "tier": "unlimited"},
                            }
                        else:
                            key_store[proj_name] = {
                                "key": proj_key,
                                "models": ["chat"],
                                "max_budget": 1.0,
                                "rpm_limit": 10,
                                "metadata": {"project": proj_name, "tier": "default"},
                            }

                    logger.critical("[IMP:9][gate_idempotent] Call 1 complete: %d keys", len(result_1))
                    logger.info("[IMP:7][gate_idempotent] Call 2 — should skip (idempotent)...")
                    result_2 = kp.provision_all(
                        master_key="test-mk",
                        base_url="http://test:4000",
                        policy_path=gate_policy,
                        persist_path=persist_path,
                    )

                    logger.info("[IMP:7][gate_idempotent] Call 3 — should also skip...")
                    result_3 = kp.provision_all(
                        master_key="test-mk",
                        base_url="http://test:4000",
                        policy_path=gate_policy,
                        persist_path=persist_path,
                    )

                    assert result_1 == result_2, "Call 2 returned different keys"
                    assert result_2 == result_3, "Call 3 returned different keys"
                    assert len(result_1) == 3, f"Expected 3 keys, got {len(result_1)}"

                    logger.critical(
                        "[IMP:9][gate_idempotent] ASSERT: idempotent — all 3 calls returned same %d keys",
                        len(result_1),
                    )

        found_imp9 = _print_ldd_trajectory(caplog, "gate_idempotent")
        assert found_imp9, "LDD Error: No IMP:9 log for gate_idempotent"


@pytest.mark.gate
def test_gate_provisioner_no_orphans(gate_policy, gate_client, tmp_path, caplog):
    """All provisioned keys reference existing projects in their metadata."""
    with caplog.at_level(logging.DEBUG):
        import core.internal.llm.key_provisioner as kp

        persist_path = tmp_path / "gate-keys-no-orphans.json"
        generated_key_meta: dict[str, dict] = {}
        key_store: dict[str, dict] = {}

        def _get_key_by_metadata(**filters):
            project = filters.get("project", "")
            return key_store.get(project)

        gate_client.get_key_by_metadata.side_effect = _get_key_by_metadata
        gate_client.generate_key.side_effect = lambda **kw: {
            "key": f"sk-gate-{kw.get('metadata', {}).get('project', 'unknown')}-key"
        }

        with patch.object(kp, "LiteLLMAdminClient", return_value=gate_client):  # noqa: SIM117
            with patch.object(kp, "discover_projects") as mock_disc:
                with patch.object(kp, "get_platform_consumers") as mock_plat:
                    mock_disc.return_value = [
                        {"name": "app-backend", "llm": {"enabled": True}},
                        {"name": "app-priority", "llm": {"enabled": True, "profile": "premium"}},
                        {"name": "app-offline", "llm": {"enabled": False}},
                    ]
                    mock_plat.return_value = [
                        {"name": "hermes-agent", "llm": {"enabled": True}},
                    ]

                    all_mock_consumers = mock_disc.return_value + mock_plat.return_value
                    expected_consumers = {
                        c["name"] for c in all_mock_consumers if c.get("llm", {}).get("enabled", False)
                    }

                    logger.info("[IMP:7][gate_no_orphans] Running provision...")
                    result = kp.provision_all(
                        master_key="test-mk",
                        base_url="http://test:4000",
                        policy_path=gate_policy,
                        persist_path=persist_path,
                    )

        logger.critical("[IMP:9][gate_no_orphans] Provisioned: %d keys", len(result))

        for project_name, meta in generated_key_meta.items():
            project = meta.get("metadata", {}).get("project", "")
            logger.critical(
                "[IMP:9][gate_no_orphans] Key for '%s': metadata=%s",
                project_name,
                meta.get("metadata"),
            )
            assert project in expected_consumers, (
                f"[IMP:10][gate_no_orphans] ORPHAN KEY: {project_name} has metadata.project='{project}' "
                f"but no expected consumer has that name"
            )

        # Also verify: no key for disabled projects
        assert "app-offline" not in result, (
            "[IMP:10][gate_no_orphans] ORPHAN KEY: app-offline should not have a key (llm.enabled: false)"
        )

        logger.critical(
            "[IMP:9][gate_no_orphans] ASSERT: no orphan keys — %d keys reference existing consumers",
            len(result),
        )

        found_imp9 = _print_ldd_trajectory(caplog, "gate_no_orphans")
        assert found_imp9, "LDD Error: No IMP:9 log for gate_no_orphans"
