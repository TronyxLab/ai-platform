# GREP_SUMMARY: gate password-charset charset-constraint secrets-manifest compose-no-fallback postgres-password-encoded url-passwords hermes-agent
# STRUCTURE: ▶ regex ^[A-Za-z0-9._-]+$ ┐ → ◇ test_secrets_manifest_charset_defined(6 URL-password names have charset) → ◇ test_password_charset_validation(7 parametrized cases ∋ !#/space) → ◇ test_no_db_url_contains_POSTGRES_PASSWORD_ENCODED(4 compose files) → ◇ test_hermes_compose_has_no_fallback(no :-${PLATFORM_MASTER_PASSWORD}) → ⎋ 4 gate tests
# region MODULE_CONTRACT
## @purpose  Gate test suite for password charset constraint (DevPlan Wave 014).
##            Validates four invariants:
##            1. All URL-embedded password secrets in secrets-manifest.yaml have charset constraint
##            2. Charset regex ^[A-Za-z0-9._-]+$ correctly rejects special chars and accepts safe chars
##            3. No raw POSTGRES_PASSWORD_ENCODED artifact survives in any compose file (Option B rejection)
##            4. hermes-agent docker-compose.base.yml has no fallback chain on PLATFORM_MASTER_PASSWORD
## @scope    Static file analysis: secrets-manifest.yaml, 4 docker-compose.base.yml files,
##           and charset regex validation via parametrized pytest.
## @invariants
##   - POSTGRES_PASSWORD, CLICKHOUSE_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD,
##     S3_ACCESS_KEY, S3_SECRET_KEY MUST have charset: "^[A-Za-z0-9._-]+$" in manifest
##   - Charset regex MUST reject: ! % ) # space /
##   - Charset regex MUST accept: alphanumeric, dot, underscore, hyphen, and hex patterns
##   - No compose file must reference POSTGRES_PASSWORD_ENCODED (Option B artifact)
##   - hermes-agent compose must NOT use :-${PLATFORM_MASTER_PASSWORD} fallback syntax
## @rationale Charset constraint blocks deployment of passwords with special characters that break
##            DATABASE_URL parsing in pgbouncer and other URL-embedding consumers. The gate prevents
##            regression (re-introduction of special-char passwords or Option B encoding fallback).
## @usecases
##   - CI gate make gate MODE=fast → validates password charset invariants
##   - Pre-merge: new URL-password secret without charset field → RED
##   - Pre-merge: special character password in test data → RED
##   - Pre-merge: POSTGRES_PASSWORD_ENCODED re-introduced → RED
##   - Pre-merge: hermes-agent compose fallback chain re-introduced → RED
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest

from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

MANIFEST_PATH: pathlib.Path = repo_root() / "core" / "secrets-manifest.yaml"

# Compose files under test for POSTGRES_PASSWORD_ENCODED scan
POSTGRES_COMPOSE: pathlib.Path = repo_root() / "core" / "modules" / "postgres" / "docker-compose.base.yml"
LANGFUSE_COMPOSE: pathlib.Path = repo_root() / "core" / "modules" / "langfuse" / "docker-compose.base.yml"
LITELLM_COMPOSE: pathlib.Path = repo_root() / "core" / "modules" / "litellm" / "docker-compose.base.yml"
SERVICE_EXPORTERS_COMPOSE: pathlib.Path = (
    repo_root() / "core" / "modules" / "service-exporters" / "docker-compose.base.yml"
)
HERMES_COMPOSE: pathlib.Path = repo_root() / "core" / "modules" / "hermes-agent" / "docker-compose.base.yml"

# ── Constants ─────────────────────────────────────────────────────────────────

# The 6 URL-password secrets that must have charset constraint in manifest
URL_PASSWORD_NAMES: frozenset[str] = frozenset({
    "POSTGRES_PASSWORD",
    "CLICKHOUSE_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
})

EXPECTED_CHARSET: str = r"^[A-Za-z0-9._-]+$"
CHARSET_PATTERN: re.Pattern = re.compile(EXPECTED_CHARSET)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _read_file(path: pathlib.Path) -> str:
    """Read file content as text.

    ## @purpose  Simple file read with explicit UTF-8 encoding.
    ## @io        ⇥ path: pathlib.Path → ⎋ str: file content
    ## @complexity O(F) where F = file size in bytes
    """
    content = path.read_text(encoding="utf-8")
    logger.info("[IMP:8][_read_file] Read %s (%d bytes)", path.relative_to(repo_root()), len(content))
    return content


# ── Test 1: Manifest charset defined for URL passwords ────────────────────────


# region test_secrets_manifest_charset_defined_for_url_passwords
@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Gate invariant — every URL-embedded
#     password secret in secrets-manifest.yaml must declare charset: "^[A-Za-z0-9._-]+$"
# · Last fail: N/A (preventive)
# · Remove if: secrets-manifest.yaml is replaced by a different SSoT mechanism
def test_secrets_manifest_charset_defined_for_url_passwords(caplog) -> None:
    """Verify that all URL-embedded password secrets have charset constraint in manifest.

    ## @purpose  Gate: ensure POSTGRES_PASSWORD, CLICKHOUSE_PASSWORD, MINIO_ROOT_USER,
    ##            MINIO_ROOT_PASSWORD, S3_ACCESS_KEY, S3_SECRET_KEY have charset field
    ##            set to "^[A-Za-z0-9._-]+$" in secrets-manifest.yaml.
    ##            FAIL code: URL_PASSWORD_MISSING_CHARSET.
    ## @io        ⎋ None — assert side-effect (pytest.fail on violations)
    ## @complexity O(S) where S = number of secrets in manifest
    """
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test_manifest_charset] Loading secrets-manifest.yaml...")

    if not MANIFEST_PATH.exists():
        msg = f"URL_PASSWORD_MISSING_CHARSET: secrets-manifest.yaml not found at {MANIFEST_PATH}"
        logger.error("[IMP:10][test_manifest_charset] %s", msg)
        pytest.fail(msg)

    data = load_yaml(MANIFEST_PATH)
    secrets_list = data.get("secrets", [])

    if not secrets_list:
        msg = "URL_PASSWORD_MISSING_CHARSET: secrets-manifest.yaml has no secrets list"
        logger.error("[IMP:10][test_manifest_charset] %s", msg)
        pytest.fail(msg)

    # Build lookup dict: name → entry
    manifest_map: dict[str, dict] = {}
    for entry in secrets_list:
        name = entry.get("name")
        if name:
            manifest_map[name] = entry

    violations: list[str] = []

    for secret_name in sorted(URL_PASSWORD_NAMES):
        if secret_name not in manifest_map:
            violations.append(f"Secret '{secret_name}' not found in manifest at all")
            logger.error("[IMP:10][test_manifest_charset] Missing in manifest: '%s'", secret_name)
            continue

        entry = manifest_map[secret_name]
        charset = entry.get("charset", "")

        if charset != EXPECTED_CHARSET:
            violations.append(f"Secret '{secret_name}' has charset='{charset}' (expected '{EXPECTED_CHARSET}')")
            logger.error(
                "[IMP:10][test_manifest_charset] '%s' charset mismatch: got '%s', expected '%s'",
                secret_name,
                charset,
                EXPECTED_CHARSET,
            )
        else:
            logger.info(
                "[IMP:9][test_manifest_charset] '%s' charset='%s' ✓",
                secret_name,
                charset,
            )

    if violations:
        msg = "URL_PASSWORD_MISSING_CHARSET: " + "; ".join(violations)
        logger.error("[IMP:10][test_manifest_charset] %s", msg)
        pytest.fail(msg)

    logger.info(
        "[IMP:9][test_manifest_charset] PASS — all %d URL-password secrets have charset='%s'",
        len(URL_PASSWORD_NAMES),
        EXPECTED_CHARSET,
    )


# endregion test_secrets_manifest_charset_defined_for_url_passwords


# ── Test 2: Password charset validation (parametrized) ────────────────────────


# region test_password_charset_validation
@pytest.mark.gate
@pytest.mark.parametrize(
    "special_password,should_fail",
    [
        ("SkyNet!!%)", True),  # original problematic password
        ("pass#hash", True),  # # in password
        ("pwd with space", True),  # space
        ("pass/with/slash", True),  # slash
        ("valid-pass_123.abc", False),  # valid: alphanumeric + . _ -
        ("openssl_rand_hex_32", False),  # typical hex (letters + underscore)
        ("simple", False),  # letters only
    ],
)
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Charset regex must reject special
#     characters (!#%) and accept safe characters (alphanumeric, dot, underscore, hyphen)
# · Last fail: "SkyNet!!%)" was the original production-bug password that broke pgbouncer
# · Remove if: charset constraint is removed or regex is fundamentally changed
def test_password_charset_validation(caplog, special_password: str, *, should_fail: bool) -> None:
    """Validate that charset regex correctly accepts/rejects passwords.

    ## @purpose  Parametrized test: 7 cases from DevPlan Wave 014 Tx3.
    ##            Pattern: ^[A-Za-z0-9._-]+$
    ##            4 negative cases (special chars), 3 positive cases (safe pattern).
    ##            FAIL code: CHARSET_REGEX_MISMATCH.
    ## @io        ⇥ special_password: str — password under test
    ##            ⇥ should_fail: bool — True=expected to FAIL (rejected by regex)
    ##            ⎋ None — assert side-effect (pytest.fail on mismatch)
    ## @complexity O(1) per parametrized case
    """
    caplog.set_level(logging.INFO)

    match_result = CHARSET_PATTERN.match(special_password)
    actual_fail = match_result is None

    status = "REJECT" if actual_fail else "ACCEPT"
    expected_status = "REJECT" if should_fail else "ACCEPT"

    logger.info(
        "[IMP:9][test_charset_validation] password='%s' → %s (expected %s)",
        special_password,
        status,
        expected_status,
    )

    if should_fail:
        # Expected to be rejected (match should be None)
        assert match_result is None, (
            f"CHARSET_REGEX_MISMATCH: password '{special_password}' should be REJECTED "
            f"by pattern '{EXPECTED_CHARSET}' but was ACCEPTED"
        )
        logger.info(
            "[IMP:9][test_charset_validation] ✓ Correctly REJECTED: '%s'",
            special_password,
        )
    else:
        # Expected to be accepted (match should not be None)
        assert match_result is not None, (
            f"CHARSET_REGEX_MISMATCH: password '{special_password}' should be ACCEPTED "
            f"by pattern '{EXPECTED_CHARSET}' but was REJECTED"
        )
        logger.info(
            "[IMP:9][test_charset_validation] ✓ Correctly ACCEPTED: '%s'",
            special_password,
        )


# endregion test_password_charset_validation


# ── Test 3: No POSTGRES_PASSWORD_ENCODED in compose files ─────────────────────


# region test_no_db_url_contains_raw_postgres_password_without_encoded
@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · POSTGRES_PASSWORD_ENCODED is an Option B
#     artifact — must not appear in any compose file after Wave 014 charset constraint
# · Last fail: N/A (preventive — Option B was never deployed)
# · Remove if: the charset constraint approach is replaced by URL-encoding pipeline
def test_no_db_url_contains_raw_postgres_password_without_encoded(caplog) -> None:
    """Verify that POSTGRES_PASSWORD_ENCODED does not appear in any compose file.

    ## @purpose  Gate: ensure POSTGRES_PASSWORD_ENCODED (Option B artifact) is absent
    ##            from 4 compose files: postgres, langfuse, litellm, service-exporters.
    ##            These modules embed POSTGRES_PASSWORD in DATABASE_URLs.
    ##            FAIL code: POSTGRES_PASSWORD_ENCODED_FOUND.
    ## @io        ⎋ None — assert side-effect (pytest.fail on violations)
    ## @complexity O(N×L) where N = 4 compose files, L = lines per file
    """
    caplog.set_level(logging.INFO)

    compose_files: list[tuple[str, pathlib.Path]] = [
        ("postgres", POSTGRES_COMPOSE),
        ("langfuse", LANGFUSE_COMPOSE),
        ("litellm", LITELLM_COMPOSE),
        ("service-exporters", SERVICE_EXPORTERS_COMPOSE),
    ]

    found_violations: list[str] = []

    for module_name, compose_path in compose_files:
        if not compose_path.exists():
            msg = f"POSTGRES_PASSWORD_ENCODED_FOUND: {module_name} compose not found at {compose_path}"
            logger.error("[IMP:10][test_no_encoded] %s", msg)
            found_violations.append(msg)
            continue

        content = _read_file(compose_path)

        # Scan each line for POSTGRES_PASSWORD_ENCODED
        for line_no, line in enumerate(content.splitlines(), 1):
            if "POSTGRES_PASSWORD_ENCODED" in line:
                violation = f"{module_name}:{line_no} — {line.strip()}"
                found_violations.append(violation)
                logger.error(
                    "[IMP:10][test_no_encoded] VIOLATION in %s:%d: %s",
                    module_name,
                    line_no,
                    line.strip(),
                )

    if found_violations:
        msg_lines = [
            (
                f"POSTGRES_PASSWORD_ENCODED_FOUND: {len(found_violations)} occurrence(s) "
                "of POSTGRES_PASSWORD_ENCODED in compose files:"
            )
        ]
        msg_lines.extend(f"  • {v}" for v in found_violations)
        msg = "\n".join(msg_lines)
        logger.error("[IMP:10][test_no_encoded] %s", msg)
        pytest.fail(msg)

    logger.info(
        "[IMP:9][test_no_encoded] PASS — no POSTGRES_PASSWORD_ENCODED in %d compose files",
        len(compose_files),
    )


# endregion test_no_db_url_contains_raw_postgres_password_without_encoded


# ── Test 4: Hermes compose has no fallback ────────────────────────────────────


# region test_hermes_compose_has_no_fallback
@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Hermes-agent compose must not have
#     :-${PLATFORM_MASTER_PASSWORD} fallback — HERMES_DASHBOARD_PASSWORD must be
#     set explicitly (per-secret autogen secrets_manager._ensure_derived_passwords,
#     DevPlan 176 B.8 — НЕ копия PLATFORM_MASTER_PASSWORD)
# · Last fail: N/A (preventive — Wave 014 Tx2 removes the fallback)
# · Remove if: hermes-agent auth mechanism is fundamentally redesigned
def test_hermes_compose_has_no_fallback(caplog) -> None:
    """Verify hermes-agent docker-compose.base.yml has no fallback on PLATFORM_MASTER_PASSWORD.

    ## @purpose  Gate: ensure HERMES_DASHBOARD_PASSWORD is passed directly
    ##            (no "${HERMES_DASHBOARD_PASSWORD:-${PLATFORM_MASTER_PASSWORD}}" fallback pattern).
    ##            Also verify that ${HERMES_DASHBOARD_PASSWORD} variable IS present.
    ##            FAIL code: HERMES_COMPOSE_FALLBACK_FOUND / HERMES_COMPOSE_MISSING_VAR.
    ## @io        ⎋ None — assert side-effect (pytest.fail on violations)
    ## @complexity O(L) where L = lines in hermes-agent compose file
    """
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test_hermes_fallback] Checking hermes-agent compose...")

    if not HERMES_COMPOSE.exists():
        msg = f"HERMES_COMPOSE_FALLBACK_FOUND: hermes-agent compose not found at {HERMES_COMPOSE}"
        logger.error("[IMP:10][test_hermes_fallback] %s", msg)
        pytest.fail(msg)

    content = _read_file(HERMES_COMPOSE)

    # Check 1: No fallback on PLATFORM_MASTER_PASSWORD
    fallback_pattern = ":-${PLATFORM_MASTER_PASSWORD}"
    if fallback_pattern in content:
        msg = (
            "HERMES_COMPOSE_FALLBACK_FOUND: "
            f"'{fallback_pattern}' detected in {HERMES_COMPOSE.relative_to(repo_root())}. "
            "Fallback chain must be removed per Wave 014 Tx2."
        )
        logger.error("[IMP:10][test_hermes_fallback] %s", msg)
        pytest.fail(msg)

    logger.info("[IMP:9][test_hermes_fallback] ✓ No fallback ':-${PLATFORM_MASTER_PASSWORD}' found in compose")

    # Check 2: ${HERMES_DASHBOARD_PASSWORD} variable IS present
    var_pattern = "${HERMES_DASHBOARD_PASSWORD}"
    if var_pattern not in content:
        msg = (
            "HERMES_COMPOSE_MISSING_VAR: "
            f"'${{HERMES_DASHBOARD_PASSWORD}}' not found in {HERMES_COMPOSE.relative_to(repo_root())}. "
            "Expected direct variable reference."
        )
        logger.error("[IMP:10][test_hermes_fallback] %s", msg)
        pytest.fail(msg)

    logger.info("[IMP:9][test_hermes_fallback] ✓ '${HERMES_DASHBOARD_PASSWORD}' variable present in compose")

    logger.info("[IMP:9][test_hermes_fallback] PASS — hermes-agent compose has no fallback chain")


# endregion test_hermes_compose_has_no_fallback
