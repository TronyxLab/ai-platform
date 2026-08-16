# GREP_SUMMARY: gate ci-env-vars platform-env env_defaults secrets no-hardcoded-passwords
# STRUCTURE: ▶ parse platform-env.yaml env_defaults → ◇ parse CI workflows → ◇ assert env var consistency → ⎋ 2 tests
# region MODULE_CONTRACT
## @purpose — Gate test suite for CI environment variable consistency (Plan 2).
##            Validates that CI workflow env vars match platform-env.yaml defaults and
##            that no hardcoded secrets/credentials exist in workflow files.
## @scope — Parses platform-env.yaml and .github/workflows/*.yml to verify env var invariants.
## @invariants
##   - All CI env vars referenced in workflows have corresponding defaults in platform-env.yaml
##   - No hardcoded passwords, tokens, or API keys in workflow YAML files
##   - Only ${{ secrets.* }} references for sensitive values
## @rationale — Prevents environment variable drift between platform-env.yaml (Single Source of Truth)
##              and CI workflow files. Hardcoded secrets detection prevents accidental credential exposure.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest

from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

_WORKFLOW_DIR: pathlib.Path = repo_root() / ".github" / "workflows"
_PLATFORM_ENV_PATH: pathlib.Path = repo_root() / "platform-env.yaml"

# Pattern to detect potential hardcoded secrets
_HARDCODED_SECRET_PATTERN: re.Pattern = re.compile(
    r'(?:password|secret|token|api_key|apikey|credential|key)\s*[:=]\s*["\'][^"\'\s]{8,}["\']',
    re.IGNORECASE,
)

# NOTE: _KNOWN_CI_ENV_VARS fallback removed per T7 — all CI env vars must be
# registered in platform-env.yaml (single source of truth). No exceptions.

# GitHub Actions built-in env vars — never expected in platform-env.yaml.
_GITHUB_BUILTINS: set[str] = {
    "GITHUB_ENV",
    "GITHUB_OUTPUT",
    "GITHUB_PATH",
    "GITHUB_STEP_SUMMARY",
    "GITHUB_TOKEN",
    "GITHUB_ACTIONS",
    "GITHUB_SHA",
    "GITHUB_REF",
    "GITHUB_REPOSITORY",
    "GITHUB_WORKSPACE",
    "GITHUB_RUN_ID",
    "GITHUB_EVENT_NAME",
    "GITHUB_HEAD_REF",
    "GITHUB_BASE_REF",
    "GITHUB_ACTOR",
    "GITHUB_WORKFLOW",
    "GITHUB_JOB",
    "GITHUB_RUN_NUMBER",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_REF_NAME",
    "GITHUB_REF_PROTECTED",
    "GITHUB_SERVER_URL",
    "GITHUB_API_URL",
    "GITHUB_GRAPHQL_URL",
    "GITHUB_TRIGGERING_ACTOR",
    "GITHUB_EVENT_PATH",
    "RUNNER_OS",
    "RUNNER_TEMP",
    "RUNNER_TOOL_CACHE",
    "RUNNER_WORKSPACE",
    "RUNNER_ARCH",
    "RUNNER_DEBUG",
    "CI",
    "HOME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "NODE_ENV",
}

# Workflow-LOCAL env vars (defined inline in the workflow for CI-internal use, NOT platform
# deployment config) — documented exemption from platform-env.yaml registration (F1, DevPlan 118).
# DevPlan 002 W5 T5.12: L1_IMAGE удалён (L1 коллапс — единый образ hermes-agent-context)
_WORKFLOW_LOCAL_ENV_VARS: dict[str, str] = {
    "DOCKER_BUILDKIT": "docker buildx flag, workflow-local build constant",
    "REGISTRY": "ghcr.io registry constant, workflow-local",
    "IMAGE_NAME": "derived image name, workflow-local build constant",
    "VPS_USER": "SSH user for VPS ops, workflow-local",
    "NODE_HOST_MAP": "GitHub org variable (vars.NODE_HOST_MAP), not platform env",
    "CI_DEPLOY_KEY": "SSH deploy key (CI secret, secrets:inherit workflow_call), not platform env — M4 env-context hoist",
    "REQUIRE_HONESTY_MODE": "test honesty mode selector, workflow-local",
    "INTEGRATION_MODE": "test integration mode, workflow-local",
    "SKIP": "pre-commit SKIP filter, workflow-local",
}


def _extract_platform_env_defaults() -> dict[str, str]:
    """Extract env_defaults from platform-env.yaml."""
    if not _PLATFORM_ENV_PATH.exists():
        logger.warning("[IMP:8][test] platform-env.yaml not found — skipping platform-env validation")
        return {}
    data = load_yaml(_PLATFORM_ENV_PATH)
    env_defaults = data.get("env_defaults", {})
    logger.info("[IMP:8][test] platform-env.yaml env_defaults: %d vars", len(env_defaults))
    return env_defaults


def _collect_env_block_vars(data: dict, out: set[str]) -> None:
    """Recursively collect env-var keys from `env:` mapping blocks in a workflow YAML dict.

    ## @purpose  Structural extraction — only keys under an actual `env:` dict are counted
    ##            (regex over raw text catches YAML anchors/comments/log keywords as false
    ##            positives — F1 fix, DevPlan 118).
    ## @complexity O(N) where N = YAML tree size
    """
    for key, value in data.items():
        if key == "env" and isinstance(value, dict):
            out.update(value.keys())
        elif isinstance(value, dict):
            _collect_env_block_vars(value, out)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _collect_env_block_vars(item, out)


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_ci_env_vars_match_platform_env(caplog):
    """Verify CI env vars used in workflows exist in platform-env.yaml env_defaults."""
    # 🧪 TRAP[TEST] · F1 (DevPlan 118) · Regression: pass-test (warning+PASS, 0 assert) → real assert
    # · Scenario: every workflow `env:` block key must be in platform-env.yaml env_defaults,
    #   a GitHub builtin, or a documented workflow-local constant
    # · Last fail: N/A (was pass-test, R1 hole U-69 family)
    # · Remove if: CI env-var registration contract is dropped
    caplog.set_level(logging.INFO)
    platform_env_defaults = _extract_platform_env_defaults()

    if not platform_env_defaults:
        logger.info("[IMP:8][test] Skipping env var matching — no platform-env.yaml defaults")
        pytest.skip("platform-env.yaml not available")

    violations: list[str] = []
    checked = 0
    for wf_file in sorted(_WORKFLOW_DIR.glob("*.yml")):
        data = load_yaml(wf_file)
        env_vars: set[str] = set()
        _collect_env_block_vars(data, env_vars)

        for var_name in sorted(env_vars):
            if var_name.startswith("secrets.") or var_name in _GITHUB_BUILTINS:
                continue
            if var_name in _WORKFLOW_LOCAL_ENV_VARS:
                logger.info(
                    "[IMP:8][test] %s: '%s' — workflow-local (%s)",
                    wf_file.name,
                    var_name,
                    _WORKFLOW_LOCAL_ENV_VARS[var_name],
                )
                continue
            checked += 1
            if var_name not in platform_env_defaults:
                violations.append(f"{wf_file.name}: '{var_name}' not in platform-env.yaml env_defaults")
                logger.warning(
                    "[IMP:8][test] %s uses env var '%s' not in platform-env.yaml defaults", wf_file.name, var_name
                )

    assert not violations, (
        "[IMP:9][test] CI env vars missing from platform-env.yaml env_defaults (single source of truth, T7):\n"
        + "\n".join(violations)
    )
    logger.info("[IMP:9][test] CI env vars are consistent with platform-env.yaml defaults (%d vars verified)", checked)


@pytest.mark.gate
def test_no_hardcoded_ci_secrets(caplog):
    """Verify CI workflows contain no hardcoded passwords/tokens (only ${{ secrets.* }})."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []

    # CI smoke-test workflow intentionally uses test-only credentials
    # (no production secrets). Excluded from hardcoded-secret detection.
    # DevPlan 002 W5 T5.7: build-platform.yml удалён (L1 коллапс) — TEST_CREDS_WORKFLOWS пуст
    TEST_CREDS_WORKFLOWS: set[str] = set()

    for wf_file in sorted(_WORKFLOW_DIR.glob("*.yml")):
        if wf_file.name in TEST_CREDS_WORKFLOWS:
            logger.info(
                "[IMP:8][test] Skipping %s — CI smoke-test workflow with documented test credentials",
                wf_file.name,
            )
            continue
        content = wf_file.read_text()
        matches = _HARDCODED_SECRET_PATTERN.findall(content)
        if matches:
            violations.append(f"{wf_file.name}: {matches}")

    if violations:
        for v in violations:
            logger.error("[IMP:10][test] Hardcoded secret violation: %s", v)
        pytest.fail(f"Hardcoded secrets found in CI workflows: {violations}")

    logger.info("[IMP:9][test] No hardcoded secrets in CI workflow files")
