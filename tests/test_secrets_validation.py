# GREP_SUMMARY: secrets-validation env-file secrets-env empty-secrets hardcoded-secrets predeploy
# STRUCTURE: ▶ test_secrets_env_file_exists ◇ .env found? → ⎋ pass|fail → ▶ test_required_secrets_not_empty ∋ .env → ⊕ required keys → ◇ empty? → ⎋ pass|fail → ▶ test_no_secret_leaks_in_compose ∋ compose_files → ◇ literal secret pattern? → ⎋ pass|fail
# @file test_secrets_validation.py
# @purpose  Secrets and env file validation before deployment: ensure required secrets
#           are present and non-empty, .env file exists, and no literal secrets leak
#           into docker-compose files.
# @scope    Static validation of core/modules/hermes-agent/.env and all
#           docker-compose.base.yml files. Lightweight checks, no Docker daemon required.
# @invariants
#   - All tests use @pytest.mark.predeploy
#   - test_required_secrets_not_empty: checks .env required keys are non-empty
#   - test_secrets_env_file_exists: checks .env file presence in hermes-agent dir
#   - test_no_secret_leaks_in_compose: scans compose for literal secret patterns
#   - No subprocess calls — pure file I/O and regex parsing
# @rationale  AC-T4 from DevPlan — secrets validation before deploy prevents
#             deployment with empty credentials or leaked secrets in config.
#             Separate from predeploy gate to allow independent CI parallelisation.
#

# region MODULE_CONTRACT
## @purpose  3 pre-deploy tests validating secrets and env configuration:
##
##           - .env file exists (AC-T4.2)
##           - Required secrets are non-empty (AC-T4.1)
##           - No literal secrets in compose files (AC-T4.3)
## @scope    Static validation of core/modules/hermes-agent/.env and all
##           docker-compose.base.yml files across all docker modules.
##           All tests are marked @pytest.mark.predeploy.
## @invariants
##
##   - test_secrets_env_file_exists: checks hermes-agent/.env file existence
##   - test_required_secrets_not_empty: reads .env, asserts required keys non-empty
##   - test_no_secret_leaks_in_compose: scans compose environment: sections for
##     literal patterns resembling secrets (sk-*, secret, tok_*, 20+ hex chars)
##   - No Docker daemon required
##   - Uses conftest fixtures: modules_dir, all_compose_files
## @rationale — Secrets validation before deploy prevents two failure modes:
##              (1) deploying with empty credentials → service fails at startup
##              (2) leaking secrets in compose config → security incident
##              AC-T4 from DevPlan §TASK-4.
## @changes — CREATED: 2026-07-03 | Wave 2: TASK-4 secrets validation
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os

import pytest
from conftest import ldd_trajectory, scan_directory_for_secrets

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Required secrets that must be present and non-empty in the .env file
# AC-T4.1: these keys MUST have a non-empty value
REQUIRED_SECRET_KEYS: list[str] = [
    "HERMES_DASHBOARD_PASSWORD",
    "GF_SECURITY_ADMIN_PASSWORD",
    "LANGFUSE_INIT_USER_PASSWORD",
    "DEEPSEEK_API_KEY",
    "CLICKHOUSE_PASSWORD",
    "POSTGRES_PASSWORD",
    "LITELLM_MASTER_KEY",
]

# Forbidden CONTEXT_IMAGE patterns per Brief §3.5:
# · ai-platform-context — deleted, use hermes-agent-context (L2)
# · hermes-agent-platform — deleted, use hermes-agent-base (L1, local only)
# ⚠️ TRAP[BUG] · 2026-07-08 · CONTEXT_IMAGE pointing to deleted/obsolete image names
OLD_CONTEXT_IMAGE_PATTERNS: list[str] = [
    "ai-platform-context",
    "hermes-agent-platform",
]

# ── Helpers ────────────────────────────────────────────────────────────────────

# region HELPERS


def _parse_dotenv(filepath: str) -> dict[str, str]:
    """Parse a .env file (key=value, ignoring comments/blank lines).

    ## @purpose — Load .env file entries into a dict without python-dotenv.
    ## @io — ⇥ filepath: str → ⎋ dict[str, str]
    ## @complexity — O(n) where n = number of lines
    """
    result: dict[str, str] = {}
    if not os.path.isfile(filepath):
        logger.info("[IMP:7][_parse_dotenv] File not found: %s", filepath)
        return result
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    logger.info("[IMP:8][_parse_dotenv] Parsed %d entries from %s", len(result), filepath)
    return result


def _get_env_path(modules_dir: str) -> str:
    """Resolve the .env file path for hermes-agent.

    ## @purpose — Single source of truth for .env location.
    ## @io — ⇥ modules_dir: str → ⎋ str (absolute path)
    ## @complexity — O(1)
    """
    return os.path.join(modules_dir, "hermes-agent", ".env")


# endregion HELPERS


# ── Tests ──────────────────────────────────────────────────────────────────────

# region TESTS

# ══════════════════════════════════════════════════════════════════════════════
# Test 1: .env file exists (AC-T4.2)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_secrets_env_file_exists
## @purpose — Verify that core/modules/hermes-agent/.env exists before deploy.
##            This file is the single source of truth for test credentials.
##            AC-T4.2 from DevPlan §TASK-4.
## @io — ⇥ caplog, modules_dir → ⎋ None (pytest.fail if .env missing)
## @complexity — O(1) — single os.path.isfile check
## @invariants
##   - Looks specifically for core/modules/hermes-agent/.env
##   - Does NOT check secrets.env (only .env)
##   - If file missing → pytest.fail with explicit instructions


@pytest.mark.predeploy
@pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("E2E_MODE") == "ci",
    reason="CI: .env file with production secrets unavailable",
)
@ldd_trajectory
def test_secrets_env_file_exists(
    caplog: pytest.LogCaptureFixture,
    modules_dir: str,
) -> None:
    """
    # ◇ _get_env_path(modules_dir) → ⊕ os.path.isfile() → ◇ exists? → ⎋ pass | fail
    """
    # region BLOCK_Setup

    env_path = _get_env_path(modules_dir)
    logger.info("[IMP:7][test_secrets_env_file_exists] Checking .env file: %s", env_path)
    # endregion

    # region BLOCK_Check
    file_exists = os.path.isfile(env_path)
    logger.info("[IMP:8][test_secrets_env_file_exists] .env exists: %s", file_exists)
    # endregion

    # region BLOCK_Assert
    if not file_exists:
        logger.error("[IMP:9][test_secrets_env_file_exists] .env file NOT FOUND: %s", env_path)
        pytest.fail(
            f"Required .env file not found: {env_path}\n"
            f"Create it from .env.example or copy from another environment.\n"
            f"This file provides test credentials for pre-deploy validation."
        )

    logger.info("[IMP:9][test_secrets_env_file_exists] ✅ .env file exists: %s", env_path)
    # endregion


# endregion FUNC_test_secrets_env_file_exists


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: Required secrets not empty (AC-T4.1)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_required_secrets_not_empty
## @purpose — Verify that all REQUIRED_SECRET_KEYS in the .env file have
##            non-empty values. Empty secrets cause service startup failures.
##            AC-T4.1 from DevPlan §TASK-4.
## @io — ⇥ caplog, modules_dir → ⎋ None (pytest.fail on empty secrets)
## @complexity — O(N) where N = REQUIRED_SECRET_KEYS length
## @invariants
##   - Reads .env via _parse_dotenv (no python-dotenv dependency)
##   - Checks each REQUIRED_SECRET_KEYS for non-empty value
##   - A key set to empty string "" counts as missing
##   - pytest.fail with list of empty/missing keys


@pytest.mark.predeploy
@pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("E2E_MODE") == "ci",
    reason="CI: .env file with production secrets unavailable",
)
@ldd_trajectory
def test_required_secrets_not_empty(
    caplog: pytest.LogCaptureFixture,
    modules_dir: str,
) -> None:
    """
    # ◇ _get_env_path → ∋ _parse_dotenv → ⊕ REQUIRED_SECRET_KEYS → ◇ empty? → ⎋ fail | pass
    """
    # region BLOCK_Setup

    env_path = _get_env_path(modules_dir)
    logger.info("[IMP:7][test_required_secrets_not_empty] Reading .env: %s", env_path)

    env_vars = _parse_dotenv(env_path)
    if not env_vars:
        logger.warning("[IMP:4][test_required_secrets_not_empty] .env file is empty or missing")
    # endregion

    # region BLOCK_CheckRequired
    empty_or_missing: list[str] = []
    for key in REQUIRED_SECRET_KEYS:
        value = env_vars.get(key, "")
        if not value.strip():
            empty_or_missing.append(key)
            logger.warning("[IMP:7][test_required_secrets_not_empty] %s = (EMPTY or MISSING)", key)
        else:
            # Log first 4 chars for verification without exposing full secret
            visible = value[:4] + "..." if len(value) > 4 else value
            logger.info("[IMP:8][test_required_secrets_not_empty] %s = %s (len=%d)", key, visible, len(value))
    # endregion

    # region BLOCK_Assert
    if empty_or_missing:
        logger.error(
            "[IMP:9][test_required_secrets_not_empty] %d secret(s) are empty/missing: %s",
            len(empty_or_missing),
            empty_or_missing,
        )
        pytest.fail(
            f"Required secrets are empty or missing in {env_path}:\n"
            + "\n".join(f"  - {k}" for k in empty_or_missing)
            + "\n"
            "Set them in the .env file before deployment."
        )

    logger.info(
        "[IMP:9][test_required_secrets_not_empty] ✅ All %d required secrets are set", len(REQUIRED_SECRET_KEYS)
    )
    # endregion


# endregion FUNC_test_required_secrets_not_empty


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: No secret leaks in compose files (AC-T4.3)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_no_secret_leaks_in_compose
## @purpose — Scan all docker-compose.base.yml files for literal secret values
##            using canonical secret scanner from conftest (password/token/api_key/secret/credential).
##            All secrets MUST use ${VAR} references, never literal values.
##            AC-T4.3 from DevPlan §TASK-4.
## @io — ⇥ caplog, modules_dir → ⎋ None (pytest.fail on leaks found)
## @complexity — O(F * N * P) where F = files, N = lines, P = patterns
## @invariants
##   - Uses canonical scan_directory_for_secrets from conftest
##   - Scans raw file content via regex patterns (not YAML parsing)
##   - Each finding is (line_number, matched_text)
##   - pytest.fail with sorted detail per file:line


@pytest.mark.predeploy
@ldd_trajectory
def test_no_secret_leaks_in_compose(
    caplog: pytest.LogCaptureFixture,
    modules_dir: str,
) -> None:
    """
    # ◇ modules_dir → scan_directory_for_secrets() → ◇ findings? → ⎋ fail | pass
    """
    # region BLOCK_Setup

    logger.info(
        "[IMP:7][test_no_secret_leaks_in_compose] Scanning %s for secret leaks using canonical scanner ...", modules_dir
    )
    # endregion

    # region BLOCK_Scan
    findings = scan_directory_for_secrets(modules_dir)
    total_findings = sum(len(v) for v in findings.values())
    # endregion

    # region BLOCK_Assert
    if findings:
        logger.error(
            "[IMP:9][test_no_secret_leaks_in_compose] Found %d potential secret leak(s) in %d file(s)!",
            total_findings,
            len(findings),
        )
        detail_lines = []
        for fp, file_findings in sorted(findings.items()):
            for ln, mt in file_findings:
                truncated = mt[:30] + "..." if len(mt) > 30 else mt
                detail_lines.append(f"  {fp}:{ln} → {truncated}")
                logger.warning("[IMP:7][test_no_secret_leaks_in_compose] %s:%d → %s", fp, ln, mt[:20])
        pytest.fail(
            f"Found {total_findings} potential secret leak(s) in docker-compose files.\n"
            f"All secrets must use ${{VAR}} references, never literal values.\n" + "\n".join(detail_lines)
        )

    logger.info("[IMP:9][test_no_secret_leaks_in_compose] ✅ No secret leaks found in compose files")
    # endregion


# endregion FUNC_test_no_secret_leaks_in_compose


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: CONTEXT_IMAGE not pointing to old image name
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_context_image_not_old_name
## @purpose — Verify CONTEXT_IMAGE in .env does not reference old/renamed image names.
##            Migrated from validate-hermes-env.sh::check_context_image().
## @io — ⇥ caplog, modules_dir → ⎋ None (pytest.fail if CONTEXT_IMAGE points to old name)
## @complexity — O(N) where N = OLD_CONTEXT_IMAGE_PATTERNS length
## @invariants
##   - Reads .env via _parse_dotenv (no python-dotenv dependency)
##   - If CONTEXT_IMAGE not overridden (compose default used) — OK
##   - If CONTEXT_IMAGE contains any OLD_CONTEXT_IMAGE_PATTERNS — pytest.fail
##   - @pytest.mark.predeploy, skips in CI


@pytest.mark.predeploy
@pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("E2E_MODE") == "ci",
    reason="CI: .env file with production CONTEXT_IMAGE unavailable",
)
@ldd_trajectory
def test_context_image_not_old_name(
    caplog: pytest.LogCaptureFixture,
    modules_dir: str,
) -> None:
    """CONTEXT_IMAGE must not point to old image names (ai-platform-context, etc.)."""
    # region BLOCK_Setup
    env_path = _get_env_path(modules_dir)
    env_vars = _parse_dotenv(env_path)
    # endregion

    # region BLOCK_Check
    context_image = env_vars.get("CONTEXT_IMAGE", "")

    if not context_image:
        logger.info("[IMP:9][test_context_image_not_old_name] CONTEXT_IMAGE not overridden — compose default OK")
        return

    for pattern in OLD_CONTEXT_IMAGE_PATTERNS:
        if pattern in context_image:
            logger.error("[IMP:9][test_context_image_not_old_name] CONTEXT_IMAGE points to old name: %s", context_image)
            pytest.fail(
                f"CONTEXT_IMAGE contains old image name '{pattern}': {context_image}\n"
                f"Expected: ghcr.io/tronyxlab/hermes-agent-context:latest"
            )
    # endregion

    # region BLOCK_Assert
    logger.info("[IMP:9][test_context_image_not_old_name] CONTEXT_IMAGE OK: %s", context_image)
    # endregion


# endregion FUNC_test_context_image_not_old_name


# ══════════════════════════════════════════════════════════════════════════════
# Test 5: LITELLM_MASTER_KEY present in REQUIRED_SECRET_KEYS
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_litellm_master_key_present
## @purpose — Verify LITELLM_MASTER_KEY is in REQUIRED_SECRET_KEYS and has a
##            ci_default in secret-definitions.yaml. Replaces OPENAI_API_KEY
##            enforcement (removed per DevPlan 078 — OPENAI_API_KEY is tier:removed).
## @io — ⇥ caplog → ⎋ None (pytest.fail if missing)
## @complexity — O(N) — one list membership check + one YAML lookup
## @invariants
##   - LITELLM_MASTER_KEY must be in REQUIRED_SECRET_KEYS
##   - secret-definitions.yaml must have ci_default for LITELLM_MASTER_KEY
##   - @pytest.mark.predeploy, static check — no CI skip needed


@pytest.mark.predeploy
@ldd_trajectory
def test_litellm_master_key_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LITELLM_MASTER_KEY must be in REQUIRED_SECRET_KEYS and have ci_default in definitions."""
    # region BLOCK_Check_REQUIRED
    logger.info("[IMP:7][test_litellm_master_key_present] Checking LITELLM_MASTER_KEY in REQUIRED_SECRET_KEYS")
    assert "LITELLM_MASTER_KEY" in REQUIRED_SECRET_KEYS, "LITELLM_MASTER_KEY missing from REQUIRED_SECRET_KEYS"
    logger.info("[IMP:9][test_litellm_master_key_present] ✅ LITELLM_MASTER_KEY present in REQUIRED_SECRET_KEYS")
    # endregion

    # region BLOCK_Check_definitions
    import os as _os

    import yaml

    _defs_path = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "core",
        "secret-definitions.yaml",
    )
    logger.info("[IMP:7][test_litellm_master_key_present] Reading definitions: %s", _defs_path)
    with open(_defs_path) as _f:
        _defs = yaml.safe_load(_f)

    _found = False
    for _s in _defs.get("secrets", []):
        if _s.get("name") == "LITELLM_MASTER_KEY":
            _ci = _s.get("ci_default", "")
            logger.info("[IMP:8][test_litellm_master_key_present] LITELLM_MASTER_KEY ci_default=%s", _ci)
            assert _ci, "LITELLM_MASTER_KEY must have ci_default in secret-definitions.yaml"
            _found = True
            break

    assert _found, "LITELLM_MASTER_KEY not found in secret-definitions.yaml"
    logger.info("[IMP:9][test_litellm_master_key_present] ✅ LITELLM_MASTER_KEY has ci_default in definitions")
    # endregion


# endregion FUNC_test_litellm_master_key_present


# ══════════════════════════════════════════════════════════════════════════════
# Test 6: HERMES_DASHBOARD_PASSWORD var name (not BASIC_AUTH variant)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_password_var_name_not_mismatched
## @purpose — Verify HERMES_DASHBOARD_PASSWORD is set (not BASIC_AUTH variant).
##            Compose uses HERMES_DASHBOARD_PASSWORD → maps to BASIC_AUTH_PASSWORD inside container.
##            Migrated from validate-hermes-env.sh::check_password_mismatch().
## @io — ⇥ caplog, modules_dir → ⎋ None (pytest.fail if password var name is wrong)
## @complexity — O(1) — two dict lookups
## @invariants
##   - Reads .env via _parse_dotenv
##   - If HERMES_DASHBOARD_PASSWORD is set → PASS
##   - If HERMES_DASHBOARD_BASIC_AUTH_PASSWORD is set but correct name is missing → pytest.fail
##   - @pytest.mark.predeploy, skips in CI


@pytest.mark.predeploy
@pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("E2E_MODE") == "ci", reason="CI: production env vars unavailable"
)
@ldd_trajectory
def test_password_var_name_not_mismatched(
    caplog: pytest.LogCaptureFixture,
    modules_dir: str,
) -> None:
    """HERMES_DASHBOARD_PASSWORD must be set — compose uses this exact name.

    TRAP[BUG] · 2026-07-08:
    Compose maps HERMES_DASHBOARD_PASSWORD → BASIC_AUTH_PASSWORD inside container.
    Setting HERMES_DASHBOARD_BASIC_AUTH_PASSWORD instead causes 'required in secrets.env' error.
    """
    # region BLOCK_Setup
    env_path = _get_env_path(modules_dir)
    env_vars = _parse_dotenv(env_path)
    # endregion

    # region BLOCK_Check
    has_password = bool(env_vars.get("HERMES_DASHBOARD_PASSWORD", "").strip())
    has_basic_auth = bool(env_vars.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "").strip())
    # endregion

    # region BLOCK_Assert
    if has_password:
        logger.info(
            "[IMP:9][test_password_var_name_not_mismatched] ✅ HERMES_DASHBOARD_PASSWORD is set (compose-compatible)"
        )
        return

    fail_msg = "HERMES_DASHBOARD_PASSWORD is MISSING — compose requires this variable.\n"
    if has_basic_auth:
        fail_msg += (
            "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD is set but compose uses HERMES_DASHBOARD_PASSWORD.\n"
            "Rename: HERMES_DASHBOARD_BASIC_AUTH_PASSWORD → HERMES_DASHBOARD_PASSWORD\n"
            "Compose maps it to BASIC_AUTH_PASSWORD inside the container automatically."
        )
    else:
        fail_msg += "Add: HERMES_DASHBOARD_PASSWORD=<value>"

    logger.error("[IMP:9][test_password_var_name_not_mismatched] %s", fail_msg)
    pytest.fail(fail_msg)
    # endregion


# endregion FUNC_test_password_var_name_not_mismatched

# endregion TESTS
