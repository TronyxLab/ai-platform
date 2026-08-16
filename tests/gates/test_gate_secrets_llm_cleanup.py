#!/usr/bin/env python3
# GREP_SUMMARY: gate-test llm-secrets-cleanup provider-key-removal OPENAI ANTHROPIC OPENROUTER GLM DEEPSEEK LITELLM_PROJECT_KEYS
# STRUCTURE: ◇ scan_compose_env(key) → ◇ scan_secret_def_tier(key) → ◇ scan_module_yaml_env(key) → ⊕ 7 assertions → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Gate test: verify all unused LLM provider keys are removed from compose files,
##           secret-definitions marks them as tier: removed, and LITELLM_PROJECT_KEYS is present.
## @scope    Static analysis of compose YAMLs, secret-definitions.yaml, and module.yaml.
##           No Docker daemon required.
## @invariants
##   - OPENAI_API_KEY must NOT be in litellm/hermes-agent compose environment
##   - ANTHROPIC_API_KEY must NOT be in any compose file
##   - OPENROUTER_API_KEY must NOT be in any compose file
##   - GLM_API_KEY must NOT be in any compose file
##   - All four above must be tier: removed in secret-definitions.yaml
##   - LITELLM_PROJECT_KEYS must exist in secret-definitions with source: provisioner
##   - DEEPSEEK_API_KEY must be required: true in litellm module.yaml env_requires
## @rationale DevPlan 049 Phase 5 — Provider Key Cleanup. After Waves 1-3 only DEEPSEEK_API_KEY
##            remains as a real provider key. All others are removed or replaced by virtual keys.
## @changes 2026-07-24 | Created per DevPlan 049 Wave 4
# endregion MODULE_CONTRACT

import logging
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = repo_root()
LITELLM_COMPOSE = ROOT / "core" / "modules" / "litellm" / "docker-compose.base.yml"
HERMES_COMPOSE = ROOT / "core" / "modules" / "hermes-agent" / "docker-compose.base.yml"
SECRET_DEFS = ROOT / "core" / "secret-definitions.yaml"
LITELLM_MODULE_YAML = ROOT / "core" / "modules" / "litellm" / "module.yaml"


# region HELPERS


def _get_env_vars_from_compose(compose_path: Path) -> dict[str, str]:
    """Extract all environment variables from a docker-compose YAML file.

    Returns dict mapping env var name to its value (as string).
    Handles both dict-format and list-format environment sections.
    """
    result: dict[str, str] = {}
    if not compose_path.exists():
        logger.warning("[IMP:7][helper] Compose file not found: %s", compose_path)
        return result

    data = load_yaml(compose_path)
    if not isinstance(data, dict):
        return result

    services = data.get("services", {}) or {}
    for svc_config in services.values():
        if not isinstance(svc_config, dict):
            continue
        env = svc_config.get("environment", {}) or {}
        if isinstance(env, dict):
            for k, v in env.items():
                result[k] = str(v) if v is not None else ""
        elif isinstance(env, list):
            for item in env:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    result[k.strip()] = v.strip()

    return result


def _get_secret_tier(secret_name: str) -> str | None:
    """Get the tier of a secret from secret-definitions.yaml. Returns None if not found."""
    if not SECRET_DEFS.exists():
        logger.warning("[IMP:7][helper] Secret definitions not found: %s", SECRET_DEFS)
        return None

    data = load_yaml(SECRET_DEFS)
    secrets: list[dict[str, Any]] = data.get("secrets", []) if isinstance(data, dict) else []
    for secret in secrets:
        if secret.get("name") == secret_name:
            logger.info("[IMP:8][helper] Found secret %s with tier=%s", secret_name, secret.get("tier"))
            return secret.get("tier")
    return None


def _get_secret_source(secret_name: str) -> str | None:
    """Get the source of a secret from secret-definitions.yaml. Returns None if not found."""
    if not SECRET_DEFS.exists():
        return None

    data = load_yaml(SECRET_DEFS)
    secrets: list[dict[str, Any]] = data.get("secrets", []) if isinstance(data, dict) else []
    for secret in secrets:
        if secret.get("name") == secret_name:
            return secret.get("source")
    return None


def _get_env_requires_from_module_yaml(module_yaml_path: Path) -> list[dict[str, Any]]:
    """Parse module.yaml and return the env_requires list with normalized entries."""
    if not module_yaml_path.exists():
        return []

    data = load_yaml(module_yaml_path)
    env_raw = data.get("env_requires", []) if isinstance(data, dict) else []

    normalized: list[dict[str, Any]] = []
    for entry in env_raw:
        if isinstance(entry, str):
            normalized.append({"name": entry, "type": "secret", "required": True})
        elif isinstance(entry, dict):
            normalized.append(entry)
    return normalized


# endregion HELPERS


# region TESTS


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-24 · REGRESSION · OPENAI_API_KEY removed from compose env
# · Scenario: litellm/docker-compose.base.yml and hermes-agent/docker-compose.base.yml
# ·   should NOT contain OPENAI_API_KEY as a provider key env var.
# · Last fail: N/A (preventive — first gate)
# · Remove if: provider key model fundamentally changes
def test_gate_no_openai_as_provider_key(caplog: pytest.LogCaptureFixture) -> None:
    """OPENAI_API_KEY must NOT be present in litellm or hermes-agent compose environments."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:7][test][START] test_gate_no_openai_as_provider_key")

    violations: list[str] = []

    litellm_env = _get_env_vars_from_compose(LITELLM_COMPOSE)
    logger.info("[IMP:8][test] litellm compose env keys: %s", list(litellm_env.keys()))

    if "OPENAI_API_KEY" in litellm_env:
        violations.append(f"OPENAI_API_KEY found in {LITELLM_COMPOSE.name}")

    hermes_env = _get_env_vars_from_compose(HERMES_COMPOSE)
    logger.info("[IMP:8][test] hermes-agent compose env keys: %s", list(hermes_env.keys()))

    if "OPENAI_API_KEY" in hermes_env:
        violations.append(f"OPENAI_API_KEY found in {HERMES_COMPOSE.name}")

    # OPENAI_API_KEY should be tier: removed in secret-definitions (audit trail)
    tier = _get_secret_tier("OPENAI_API_KEY")
    if tier != "removed":
        violations.append(f"OPENAI_API_KEY tier is '{tier}', expected 'removed' in secret-definitions.yaml")

    logger.info("[IMP:9][test] OPENAI_API_KEY violations: %s", violations)

    assert not violations, "GATE_LLM_CLEANUP: OPENAI_API_KEY provider key cleanup:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][test] PASS: OPENAI_API_KEY properly removed as provider key")


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-24 · REGRESSION · ANTHROPIC_API_KEY removed from compose
# · Scenario: ANTHROPIC_API_KEY must NOT be in any compose environment
# · Last fail: N/A (preventive)
# · Remove if: Anthropic is added as a provider
def test_gate_no_anthropic_in_compose(caplog: pytest.LogCaptureFixture) -> None:
    """ANTHROPIC_API_KEY must NOT be present in any compose file environment."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:7][test][START] test_gate_no_anthropic_in_compose")

    violations: list[str] = []

    for compose_path, label in [(LITELLM_COMPOSE, "litellm"), (HERMES_COMPOSE, "hermes-agent")]:
        env = _get_env_vars_from_compose(compose_path)
        if "ANTHROPIC_API_KEY" in env:
            violations.append(f"ANTHROPIC_API_KEY found in {label} ({compose_path.name})")

    tier = _get_secret_tier("ANTHROPIC_API_KEY")
    if tier != "removed":
        violations.append(f"ANTHROPIC_API_KEY tier is '{tier}', expected 'removed' in secret-definitions.yaml")

    logger.info("[IMP:9][test] ANTHROPIC_API_KEY violations: %s", violations)

    assert not violations, "GATE_LLM_CLEANUP: ANTHROPIC_API_KEY still present:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][test] PASS: ANTHROPIC_API_KEY properly removed")


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-24 · REGRESSION · OPENROUTER_API_KEY removed from compose
# · Scenario: OPENROUTER_API_KEY must NOT be in any compose environment
# · Last fail: N/A (preventive)
# · Remove if: OpenRouter is added as a provider
def test_gate_no_openrouter_in_compose(caplog: pytest.LogCaptureFixture) -> None:
    """OPENROUTER_API_KEY must NOT be present in any compose file environment."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:7][test][START] test_gate_no_openrouter_in_compose")

    violations: list[str] = []

    for compose_path, label in [(LITELLM_COMPOSE, "litellm"), (HERMES_COMPOSE, "hermes-agent")]:
        env = _get_env_vars_from_compose(compose_path)
        if "OPENROUTER_API_KEY" in env:
            violations.append(f"OPENROUTER_API_KEY found in {label} ({compose_path.name})")

    tier = _get_secret_tier("OPENROUTER_API_KEY")
    if tier != "removed":
        violations.append(f"OPENROUTER_API_KEY tier is '{tier}', expected 'removed' in secret-definitions.yaml")

    logger.info("[IMP:9][test] OPENROUTER_API_KEY violations: %s", violations)

    assert not violations, "GATE_LLM_CLEANUP: OPENROUTER_API_KEY still present:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][test] PASS: OPENROUTER_API_KEY properly removed")


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-24 · REGRESSION · GLM_API_KEY removed from compose
# · Scenario: GLM_API_KEY must NOT be in any compose environment
# · Last fail: N/A (preventive)
# · Remove if: GLM/Z.AI is added as a provider
def test_gate_no_glm_in_compose(caplog: pytest.LogCaptureFixture) -> None:
    """GLM_API_KEY must NOT be present in any compose file environment."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:7][test][START] test_gate_no_glm_in_compose")

    violations: list[str] = []

    for compose_path, label in [(LITELLM_COMPOSE, "litellm"), (HERMES_COMPOSE, "hermes-agent")]:
        env = _get_env_vars_from_compose(compose_path)
        if "GLM_API_KEY" in env:
            violations.append(f"GLM_API_KEY found in {label} ({compose_path.name})")

    tier = _get_secret_tier("GLM_API_KEY")
    if tier != "removed":
        violations.append(f"GLM_API_KEY tier is '{tier}', expected 'removed' in secret-definitions.yaml")

    logger.info("[IMP:9][test] GLM_API_KEY violations: %s", violations)

    assert not violations, "GATE_LLM_CLEANUP: GLM_API_KEY still present:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][test] PASS: GLM_API_KEY properly removed")


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-24 · REGRESSION · All removed keys in secret-definitions
# · Scenario: OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, GLM_API_KEY
# ·   must all be tier: removed in secret-definitions.yaml
# · Last fail: N/A (preventive)
# · Remove if: secret definitions are restructured
def test_gate_removed_keys_in_secret_definitions(caplog: pytest.LogCaptureFixture) -> None:
    """Verify that all removed provider keys are tier: removed in secret-definitions.yaml."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:7][test][START] test_gate_removed_keys_in_secret_definitions")

    removed_keys = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GLM_API_KEY"]
    violations: list[str] = []

    for key in removed_keys:
        tier = _get_secret_tier(key)
        if tier != "removed":
            violations.append(f"{key} has tier='{tier}', expected 'removed' in secret-definitions.yaml")
        logger.info("[IMP:8][test] %s → tier: %s", key, tier)

    logger.info("[IMP:9][test] Removed keys violations: %s", violations)

    assert not violations, "GATE_LLM_CLEANUP: removed keys status:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][test] PASS: All provider keys are tier: removed")


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-24 · REGRESSION · LITELLM_PROJECT_KEYS exists
# · Scenario: LITELLM_PROJECT_KEYS must be in secret-definitions.yaml with source: provisioner
# · Last fail: N/A (preventive)
# · Remove if: SOPS-based key storage is replaced
def test_gate_litellm_project_keys_exists(caplog: pytest.LogCaptureFixture) -> None:
    """Verify LITELLM_PROJECT_KEYS exists in secret-definitions.yaml with source: provisioner."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:7][test][START] test_gate_litellm_project_keys_exists")

    violations: list[str] = []

    tier = _get_secret_tier("LITELLM_PROJECT_KEYS")
    if tier is None:
        violations.append("LITELLM_PROJECT_KEYS not found in secret-definitions.yaml")
    elif tier != "generated":
        violations.append(f"LITELLM_PROJECT_KEYS has tier='{tier}', expected 'generated'")

    source = _get_secret_source("LITELLM_PROJECT_KEYS")
    if source != "provisioner":
        violations.append(f"LITELLM_PROJECT_KEYS has source='{source}', expected 'provisioner'")

    logger.info("[IMP:9][test] LITELLM_PROJECT_KEYS violations: %s", violations)

    assert not violations, "GATE_LLM_CLEANUP: LITELLM_PROJECT_KEYS missing or misconfigured:\n  " + "\n  ".join(
        violations
    )
    logger.info("[IMP:9][test] PASS: LITELLM_PROJECT_KEYS properly configured")


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-24 · REGRESSION · DEEPSEEK_API_KEY required in module.yaml
# · Scenario: DEEPSEEK_API_KEY must be required: true in litellm module.yaml env_requires
# · Last fail: N/A (preventive)
# · Remove if: DeepSeek is replaced as the sole LLM provider
def test_gate_deepseek_required_in_module_yaml(caplog: pytest.LogCaptureFixture) -> None:
    """Verify DEEPSEEK_API_KEY is required: true in litellm module.yaml env_requires."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:7][test][START] test_gate_deepseek_required_in_module_yaml")

    violations: list[str] = []

    env_entries = _get_env_requires_from_module_yaml(LITELLM_MODULE_YAML)
    logger.info("[IMP:8][test] litellm env_requires: %s", env_entries)

    deepseek_entry = None
    for entry in env_entries:
        if entry.get("name") == "DEEPSEEK_API_KEY":
            deepseek_entry = entry
            break

    if deepseek_entry is None:
        violations.append("DEEPSEEK_API_KEY not found in litellm module.yaml env_requires")
    else:
        required = deepseek_entry.get("required", False)
        if not required:
            violations.append(f"DEEPSEEK_API_KEY has required={required}, expected required: true")
        logger.info("[IMP:8][test] DEEPSEEK_API_KEY entry: %s", deepseek_entry)

    logger.info("[IMP:9][test] DEEPSEEK_API_KEY violations: %s", violations)

    assert not violations, "GATE_LLM_CLEANUP: DEEPSEEK_API_KEY not properly required:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][test] PASS: DEEPSEEK_API_KEY is required in litellm module.yaml")


# endregion TESTS
