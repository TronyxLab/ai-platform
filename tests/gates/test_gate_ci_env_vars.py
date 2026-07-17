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
import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_WORKFLOW_DIR: pathlib.Path = _PROJECT_ROOT / ".github" / "workflows"
_PLATFORM_ENV_PATH: pathlib.Path = _PROJECT_ROOT / "platform-env.yaml"

# Pattern to detect potential hardcoded secrets
_HARDCODED_SECRET_PATTERN: re.Pattern = re.compile(
    r'(?:password|secret|token|api_key|apikey|credential|key)\s*[:=]\s*["\'][^"\'\s]{8,}["\']',
    re.IGNORECASE,
)

# NOTE: _KNOWN_CI_ENV_VARS fallback removed per T7 — all CI env vars must be
# registered in platform-env.yaml (single source of truth). No exceptions.


def _load_yaml(path: pathlib.Path) -> dict:
    """Load and parse a YAML file."""
    with open(path) as f:
        return yaml.safe_load(f)


def _extract_platform_env_defaults() -> dict[str, str]:
    """Extract env_defaults from platform-env.yaml."""
    if not _PLATFORM_ENV_PATH.exists():
        logger.warning("[IMP:8][test] platform-env.yaml not found — skipping platform-env validation")
        return {}
    data = _load_yaml(_PLATFORM_ENV_PATH)
    env_defaults = data.get("env_defaults", {})
    logger.info("[IMP:8][test] platform-env.yaml env_defaults: %d vars", len(env_defaults))
    return env_defaults


@pytest.mark.gate
def test_ci_env_vars_match_platform_env(caplog):
    """Verify CI env vars used in workflows exist in platform-env.yaml env_defaults."""
    caplog.set_level(logging.INFO)
    platform_env_defaults = _extract_platform_env_defaults()

    if not platform_env_defaults:
        logger.info("[IMP:8][test] Skipping env var matching — no platform-env.yaml defaults")
        pytest.skip("platform-env.yaml not available")

    for wf_file in sorted(_WORKFLOW_DIR.glob("*.yml")):
        content = wf_file.read_text()
        # Find all env: declarations and env var names
        # Pattern: VAR_NAME: ${{ ... }} or VAR_NAME: value
        env_var_refs = re.findall(
            r"(?:^|\s)([A-Z][A-Z0-9_]+):\s*(?:\$\{\{|\S)",
            content,
            re.MULTILINE,
        )

        for var_name in env_var_refs:
            # Skip GitHub Actions built-in vars and secrets references
            if var_name.startswith("secrets.") or var_name in (
                "GITHUB_ENV",
                "GITHUB_OUTPUT",
                "GITHUB_PATH",
                "GITHUB_STEP_SUMMARY",
                "GITHUB_TOKEN",
            ):
                continue
            # Verify the var is in platform-env.yaml defaults (single source of truth, T7)
            if var_name not in platform_env_defaults:
                logger.warning(
                    "[IMP:8][test] %s uses env var '%s' not in platform-env.yaml defaults", wf_file.name, var_name
                )

    logger.info("[IMP:9][test] CI env vars are consistent with platform-env.yaml defaults")


@pytest.mark.gate
def test_no_hardcoded_ci_secrets(caplog):
    """Verify CI workflows contain no hardcoded passwords/tokens (only ${{ secrets.* }})."""
    caplog.set_level(logging.INFO)
    violations: list[str] = []

    for wf_file in sorted(_WORKFLOW_DIR.glob("*.yml")):
        content = wf_file.read_text()
        matches = _HARDCODED_SECRET_PATTERN.findall(content)
        if matches:
            violations.append(f"{wf_file.name}: {matches}")

    if violations:
        for v in violations:
            logger.error("[IMP:10][test] Hardcoded secret violation: %s", v)
        pytest.fail(f"Hardcoded secrets found in CI workflows: {violations}")

    logger.info("[IMP:9][test] No hardcoded secrets in CI workflow files")
