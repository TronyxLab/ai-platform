# GREP_SUMMARY: gate env-defaults-consistency POSTGRES_PASSWORD NEXTAUTH_SECRET unified defaults drift
# STRUCTURE: ◇ test_env_defaults_consistency → ○ load .env → ○ load hermes-agent/.env → ○ load definitions → ⊕ compare → ⎋ pass|fail

# region MODULE_CONTRACT
## @purpose  Gate test: verify POSTGRES_PASSWORD and NEXTAUTH_SECRET have consistent
##           (unified) values across all config layers:
##           - .env (root project env)
##           - core/modules/hermes-agent/.env (module test env)
##           - core/secret-definitions.yaml (ci_default)
##           Drift means CI tests would use different credentials than local dev.
## @scope    Static YAML/.env parsing — no Docker daemon. @pytest.mark.gate.
## @invariants
##   - POSTGRES_PASSWORD must equal 'test-pg-pwd' in all 3 layers
##   - NEXTAUTH_SECRET must equal 'ci-test-nextauth-secret-32-chars-min!!' in all 3 layers
##   - Failure means CI-local credential mismatch (flaky tests)
## @rationale DevPlan 078 T9/T11: POSTGRES_PASSWORD and NEXTAUTH_SECRET were found
##            to have different values across .env files and secret-definitions.yaml.
##            This gate locks the unified values and prevents future drift.
## @changes  CREATED: 2026-07-25 | DevPlan 078 Phase A T11
# endregion MODULE_CONTRACT

import logging
import os

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT_ENV_PATH = os.path.join(ROOT_DIR, ".env")
HERMES_ENV_PATH = os.path.join(ROOT_DIR, "core", "modules", "hermes-agent", ".env")
SECRET_DEFINITIONS_PATH = os.path.join(ROOT_DIR, "core", "secret-definitions.yaml")

UNIFIED_PG_PASSWORD = "test-pg-pwd"
UNIFIED_NEXTAUTH_SECRET = "ci-test-nextauth-secret-32-chars-min!!"


# ── Helpers ────────────────────────────────────────────────────────────────────

# region HELPERS


def _parse_dotenv(filepath: str) -> dict[str, str]:
    """Parse a .env file into key → value dict. No python-dotenv dependency."""
    result: dict[str, str] = {}
    if not os.path.isfile(filepath):
        logger.warning("[IMP:4][env_defaults] File not found: %s", filepath)
        return result
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    logger.info("[IMP:8][env_defaults] Parsed %d entries from %s", len(result), filepath)
    return result


def _get_definitions_value(key_name: str) -> str:
    """Read ci_default for a given secret name from secret-definitions.yaml."""
    if not os.path.isfile(SECRET_DEFINITIONS_PATH):
        logger.warning("[IMP:4][env_defaults] Definitions file not found: %s", SECRET_DEFINITIONS_PATH)
        return ""
    with open(SECRET_DEFINITIONS_PATH) as f:
        defs = yaml.safe_load(f)
    for secret in defs.get("secrets", []):
        if secret.get("name") == key_name:
            return secret.get("ci_default", "")
    return ""


def _check_env_var(layer_name: str, env_dict: dict[str, str], key: str, expected: str) -> str | None:
    """Check that a key in an env dict matches expected. Returns error message or None."""
    actual = env_dict.get(key, "")
    if not actual:
        return f"[{layer_name}] {key} is MISSING"
    if actual != expected:
        return f"[{layer_name}] {key} = '{actual}', expected '{expected}'"
    return None


# endregion HELPERS


# ── Tests ──────────────────────────────────────────────────────────────────────

# region FUNC_test_env_defaults_consistency
## @purpose — Verify POSTGRES_PASSWORD and NEXTAUTH_SECRET have the same unified
##            value across root .env, hermes-agent/.env, and secret-definitions.yaml.
## @io — ⇥ caplog → ⎋ None (pytest.fail on inconsistency)
## @complexity — O(N + M) — two .env parses + one YAML parse
## @invariants
##   - Layers checked: .env, hermes-agent/.env, secret-definitions.yaml ci_default
##   - All must show the exact unified value (test-pg-pwd / ci-test-nextauth-secret-32-chars-min!!)
##   - @pytest.mark.gate, static check — no CI skip needed


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · POSTGRES_PASSWORD / NEXTAUTH_SECRET unified value drift
# · Last fail: N/A (new gate — locks unified values from DevPlan 078)
# · Remove if: POSTGRES_PASSWORD and NEXTAUTH_SECRET are removed from the platform
def test_env_defaults_consistency(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """POSTGRES_PASSWORD and NEXTAUTH_SECRET must be consistent across all config layers."""
    # region BLOCK_Load
    logger.info("[IMP:7][env_defaults] Loading root .env: %s", ROOT_ENV_PATH)
    root_env = _parse_dotenv(ROOT_ENV_PATH)

    logger.info("[IMP:7][env_defaults] Loading hermes-agent .env: %s", HERMES_ENV_PATH)
    hermes_env = _parse_dotenv(HERMES_ENV_PATH)

    pg_def = _get_definitions_value("POSTGRES_PASSWORD")
    ns_def = _get_definitions_value("NEXTAUTH_SECRET")
    logger.info("[IMP:8][env_defaults] Definitions ci_default: POSTGRES_PASSWORD='%s', NEXTAUTH_SECRET='%s'", pg_def, ns_def)
    # endregion

    # region BLOCK_Check
    errors: list[str] = []

    # POSTGRES_PASSWORD
    for name, env_dict in [(".env", root_env), ("hermes-agent/.env", hermes_env)]:
        err = _check_env_var(name, env_dict, "POSTGRES_PASSWORD", UNIFIED_PG_PASSWORD)
        if err:
            errors.append(err)
            logger.warning("[IMP:7][env_defaults] %s", err)
        else:
            logger.info("[IMP:8][env_defaults] %s POSTGRES_PASSWORD = '%s' ✓", name, UNIFIED_PG_PASSWORD)

    if pg_def != UNIFIED_PG_PASSWORD:
        errors.append(f"[secret-definitions.yaml] POSTGRES_PASSWORD ci_default = '{pg_def}', expected '{UNIFIED_PG_PASSWORD}'")
        logger.warning("[IMP:7][env_defaults] definitions POSTGRES_PASSWORD mismatch")

    # NEXTAUTH_SECRET
    for name, env_dict in [(".env", root_env), ("hermes-agent/.env", hermes_env)]:
        err = _check_env_var(name, env_dict, "NEXTAUTH_SECRET", UNIFIED_NEXTAUTH_SECRET)
        if err:
            errors.append(err)
            logger.warning("[IMP:7][env_defaults] %s", err)
        else:
            logger.info("[IMP:8][env_defaults] %s NEXTAUTH_SECRET = '%s' ✓", name, UNIFIED_NEXTAUTH_SECRET)

    if ns_def != UNIFIED_NEXTAUTH_SECRET:
        errors.append(
            f"[secret-definitions.yaml] NEXTAUTH_SECRET ci_default = '{ns_def}', expected '{UNIFIED_NEXTAUTH_SECRET}'"
        )
        logger.warning("[IMP:7][env_defaults] definitions NEXTAUTH_SECRET mismatch")
    # endregion

    # region BLOCK_Assert
    if errors:
        logger.error("[IMP:9][env_defaults] %d consistency error(s) found", len(errors))
        pytest.fail(
            f"Env defaults consistency check failed — {len(errors)} error(s):\n"
            + "\n".join(f"  {e}" for e in errors)
            + "\n\nRun 'make fix-gate' or manually unify values to:\n"
            f"  POSTGRES_PASSWORD={UNIFIED_PG_PASSWORD}\n"
            f"  NEXTAUTH_SECRET={UNIFIED_NEXTAUTH_SECRET}"
        )

    logger.info("[IMP:9][env_defaults] ✅ All env defaults consistent across all 3 layers")
    # endregion


# endregion FUNC_test_env_defaults_consistency
