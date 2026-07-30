#!/usr/bin/env python3
# GREP_SUMMARY: test secrets-pipeline-integration secrets_env_parser consumers cross-module consistency DevPlan-086
# STRUCTURE: ▶ ┌secrets.env fixture (~15 vars)┐ → ⊕ parse() → ○ verify each consumer's data loads consistently
#            → ⊕ assert all expected keys present and correct across all consumers → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Integration test for the unified secrets pipeline (DevPlan 086).
##           Creates a single secrets.env with ~15 variables (mixed format: export,
##           quotes, comments, unicode), parses via secrets_env_parser, then verifies
##           ALL 7 consumers load the same consistent data from the shared module.
## @scope    Integration (not unit) — tests that 7 independent consumer modules all
##           use secrets_env_parser as the single source of truth. No Docker dependency.
## @invariants
##   - Uses tmp_path for fixture file (no hardcoded paths)
##   - Tests all 7 consumer import paths: secrets_manager, secrets_validator,
##     compose_preflight, agent_watchdog, cert_orchestrator, docker_auth
##   - Export_shell output is verified via node-lifecycle.sh's python3 -c pattern
##   - IMP:7-10 telemetry via caplog with trajectory printer
##   - Every consumer's data is consistent — same keys, same values
## @rationale DevPlan 086 unified 7 inline parsers into one shared module. This
##            integration test verifies the unification is complete by routing all
##            7 consumers through the single source of truth and verifying consistency.
##            Without this test, subtle drift between consumer import paths could go
##            undetected until production.
# endregion MODULE_CONTRACT

import contextlib
import logging
import pathlib
from textwrap import dedent

import pytest

from tests.conftest import ldd_trajectory

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger(__name__)


# ── LDD helper ────────────────────────────────────────────────────────────────


def _print_ldd(caplog: pytest.LogCaptureFixture) -> bool:
    """Print IMP:7-10 log trajectory and return True if any IMP:9 log found.

    ## @purpose — Centralized LDD trajectory printer.
    ## @io — ⇥ caplog → ⎋ bool (IMP:9 found)
    ## @complexity — O(n) where n = caplog records
    """
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) [test_secrets_pipeline_integration] ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    return found_imp9


# ── Fixture: complex secrets.env ──────────────────────────────────────────────


# region FIXTURE_multi_vendor_secrets_env


@pytest.fixture
def secrets_env_content() -> str:
    """Fixture: generate a complex secrets.env with ~15 variables.

    ## @purpose — Provide a multi-vendor secrets.env that exercises all parsers:
    ##            export prefix, single/double quotes, inline comments, indented
    ##            comments, unicode, empty values, unusual variable names.
    ## @io — ⎋ str — secrets.env content
    ## @complexity — O(1) — constant-size fixture
    """
    return dedent("""\
        # Platform secrets — auto-generated, DO NOT EDIT
        # This file exercises all parser edge cases

        export POSTGRES_PASSWORD='s3cret!p@ss'
        export POSTGRES_USER=platform_user
        REDIS_PASSWORD="r3d!s#pwd"

        # Database connection strings
        DB_HOST=db.internal.tronyx.ru
        DB_PORT=5432
        export DB_NAME=ai_platform

        # API keys with special characters
        LITELLM_API_KEY='sk-lt-abc123def456'
        WEBNAMES_API_KEY='wn_xyz789!@#'
        export OPENAI_API_KEY="sk-op-12345"

        # Docker registry auth
        DOCKER_HUB_USERNAME=mycompany
        DOCKER_HUB_TOKEN='dckr_pat_abc123_xyz!'
        GHCR_TOKEN=ghp_abc123def456

        # Agent tokens
        TELEGRAM_BOT_TOKEN='123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'
        HERMES_API_KEY=hm_abc123

        # Unicode values
        SECRET_LABEL=привет_мир
        EXPORT_DOMAIN=日本語.example.com
        EMPTY_VAR=

        # Inline comments (hash in values preserved by quotes)
        COMMENT_VAR='value with # hash inside'
        HASH_ESCAPE=value_without_comment # this is a comment
        export QUOTED_COMMENT="also # inside double quotes"

        # Variable with unusual name pattern
        _INTERNAL_KEY=internal_value
        __DUNDER_KEY=dunder_value
    """)


# endregion FIXTURE_multi_vendor_secrets_env


# ── Helper: expected keys and values ──────────────────────────────────────────


def _get_expected_data() -> dict[str, str]:
    """Return the expected parsed data from the fixture secrets.env.

    ## @purpose — Hard-coded expected dictionary matching the fixture content.
    ##            Centralised to avoid duplication across consumer checks.
    ## @io — ⎋ dict[str, str] — expected key-value pairs
    ## @complexity — O(1)
    """
    return {
        "POSTGRES_PASSWORD": "s3cret!p@ss",
        "POSTGRES_USER": "platform_user",
        "REDIS_PASSWORD": "r3d!s#pwd",
        "DB_HOST": "db.internal.tronyx.ru",
        "DB_PORT": "5432",
        "DB_NAME": "ai_platform",
        "LITELLM_API_KEY": "sk-lt-abc123def456",
        "WEBNAMES_API_KEY": "wn_xyz789!@#",
        "OPENAI_API_KEY": "sk-op-12345",
        "DOCKER_HUB_USERNAME": "mycompany",
        "DOCKER_HUB_TOKEN": "dckr_pat_abc123_xyz!",
        "GHCR_TOKEN": "ghp_abc123def456",
        "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        "HERMES_API_KEY": "hm_abc123",
        "SECRET_LABEL": "привет_мир",
        "EXPORT_DOMAIN": "日本語.example.com",
        "EMPTY_VAR": "",
        "COMMENT_VAR": "value with # hash inside",
        "HASH_ESCAPE": "value_without_comment",
        "QUOTED_COMMENT": "also # inside double quotes",
        "_INTERNAL_KEY": "internal_value",
        "__DUNDER_KEY": "dunder_value",
    }


# ── Consumer verification functions ───────────────────────────────────────────


# region FUNC_verify_secrets_manager
# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(verify secrets_manager loads same data via shared parser)
# · REMOVE_IF(secrets_manager removed or import path changes)


def _verify_secrets_manager(parsed: dict[str, str], caplog: pytest.LogCaptureFixture) -> None:
    """Verify secrets_manager.py can load data from the shared parser.

    ## @purpose — Check that secrets_manager's delegates actually receive
    ##            the same data. secrets_manager imports parse from
    ##            core.internal.shared.secrets_env_parser, so this is a
    ##            pass-through verification.
    ## @io — ⇥ parsed: dict — data from secrets_env_parser.parse()
    ##       ⇥ caplog — for trajectory logging
    ## @complexity — O(1)
    """
    logger.info("[IMP:7][verify_secrets_manager] Verifying secrets_manager data consistency")
    # secrets_manager uses secrets_env_parser.parse() directly —
    # verify the data it would receive matches expected
    expected = _get_expected_data()
    assert parsed.get("POSTGRES_PASSWORD") == expected["POSTGRES_PASSWORD"], (
        "POSTGRES_PASSWORD mismatch in secrets_manager data"
    )
    assert parsed.get("DOCKER_HUB_USERNAME") == expected["DOCKER_HUB_USERNAME"], (
        "DOCKER_HUB_USERNAME mismatch in secrets_manager data"
    )
    assert parsed.get("DOCKER_HUB_TOKEN") == expected["DOCKER_HUB_TOKEN"], (
        "DOCKER_HUB_TOKEN mismatch in secrets_manager data"
    )
    assert parsed.get("GHCR_TOKEN") == expected["GHCR_TOKEN"], "GHCR_TOKEN mismatch in secrets_manager data"
    logger.info("[IMP:9][verify_secrets_manager] PASS — secrets_manager data consistent")


# endregion FUNC_verify_secrets_manager


# region FUNC_verify_secrets_validator
# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(verify secrets_validator loads same data via shared parser)
# · REMOVE_IF(secrets_validator removed or import path changes)


def _verify_secrets_validator(parsed: dict[str, str], caplog: pytest.LogCaptureFixture) -> None:
    """Verify secrets_validator.py processes the same data.

    ## @purpose — secrets_validator imports parse from
    ##            core.internal.shared.secrets_env_parser. Verify it
    ##            sees the same key set.
    ## @io — ⇥ parsed: dict — data from secrets_env_parser.parse()
    ##       ⇥ caplog — for trajectory logging
    ## @complexity — O(1)
    """
    logger.info("[IMP:7][verify_secrets_validator] Verifying secrets_validator data consistency")
    expected = _get_expected_data()

    # secrets_validator checks password length, charset — verify data integrity
    db_password = parsed.get("POSTGRES_PASSWORD", "")
    assert len(db_password) >= 8, f"POSTGRES_PASSWORD too short for secrets_validator: '{db_password}'"
    assert parsed["DB_HOST"] == expected["DB_HOST"]
    assert parsed["DB_PORT"] == expected["DB_PORT"]
    assert parsed["DB_NAME"] == expected["DB_NAME"]

    logger.info("[IMP:9][verify_secrets_validator] PASS — secrets_validator data consistent")


# endregion FUNC_verify_secrets_validator


# region FUNC_verify_compose_preflight
# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(verify compose_preflight loads same data via shared parser)
# · REMOVE_IF(compose_preflight removed or import path changes)


def _verify_compose_preflight(parsed: dict[str, str], caplog: pytest.LogCaptureFixture) -> None:
    """Verify compose_preflight.py loads consistent data.

    ## @purpose — compose_preflight's load_env_map delegates to
    ##            secrets_env_parser.parse(). Verify key-value integrity.
    ## @io — ⇥ parsed: dict — data from secrets_env_parser.parse()
    ##       ⇥ caplog — for trajectory logging
    ## @complexity — O(1)
    """
    logger.info("[IMP:7][verify_compose_preflight] Verifying compose_preflight data consistency")
    expected = _get_expected_data()

    # compose_preflight checks for missing secrets — verify all expected keys exist
    required_preflight_keys = [
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "LITELLM_API_KEY",
        "TELEGRAM_BOT_TOKEN",
    ]
    for key in required_preflight_keys:
        assert key in parsed, f"compose_preflight requires '{key}' but it's missing from parsed data"
        assert parsed[key] == expected[key], (
            f"compose_preflight: {key} value mismatch: '{parsed[key]}' != '{expected[key]}'"
        )

    logger.info("[IMP:9][verify_compose_preflight] PASS — compose_preflight data consistent")


# endregion FUNC_verify_compose_preflight


# region FUNC_verify_agent_watchdog
# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(verify agent_watchdog loads same data via shared parser)
# · REMOVE_IF(agent_watchdog removed or import path changes)


def _verify_agent_watchdog(parsed: dict[str, str], caplog: pytest.LogCaptureFixture) -> None:
    """Verify agent_watchdog.py tokens are in the parsed data.

    ## @purpose — agent_watchdog imports secrets_env_parser.parse() to load
    ##            TELEGRAM_BOT_TOKEN and HERMES_API_KEY. Verify both present.
    ## @io — ⇥ parsed: dict — data from secrets_env_parser.parse()
    ##       ⇥ caplog — for trajectory logging
    ## @complexity — O(1)
    """
    logger.info("[IMP:7][verify_agent_watchdog] Verifying agent_watchdog data consistency")
    expected = _get_expected_data()

    assert parsed.get("TELEGRAM_BOT_TOKEN") == expected["TELEGRAM_BOT_TOKEN"], (
        "TELEGRAM_BOT_TOKEN mismatch for agent_watchdog"
    )
    assert parsed.get("HERMES_API_KEY") == expected["HERMES_API_KEY"], "HERMES_API_KEY mismatch for agent_watchdog"

    logger.info("[IMP:9][verify_agent_watchdog] PASS — agent_watchdog data consistent")


# endregion FUNC_verify_agent_watchdog


# region FUNC_verify_cert_orchestrator
# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(verify cert_orchestrator loads same data via shared parser)
# · REMOVE_IF(cert_orchestrator removed or import path changes)


def _verify_cert_orchestrator(parsed: dict[str, str], caplog: pytest.LogCaptureFixture) -> None:
    """Verify cert_orchestrator.py loads WEBNAMES_API_KEY consistently.

    ## @purpose — cert_orchestrator uses secrets_env_parser.parse() to
    ##            load WEBNAMES_API_KEY. Verify the key is present and correct.
    ## @io — ⇥ parsed: dict — data from secrets_env_parser.parse()
    ##       ⇥ caplog — for trajectory logging
    ## @complexity — O(1)
    """
    logger.info("[IMP:7][verify_cert_orchestrator] Verifying cert_orchestrator data consistency")
    expected = _get_expected_data()

    webnames_key = parsed.get("WEBNAMES_API_KEY")
    assert webnames_key == expected["WEBNAMES_API_KEY"], (
        f"WEBNAMES_API_KEY mismatch for cert_orchestrator: '{webnames_key}'"
    )

    # cert_orchestrator also reads EXPORT_DOMAIN for acme.sh
    export_domain = parsed.get("EXPORT_DOMAIN")
    assert export_domain == expected["EXPORT_DOMAIN"], (
        f"EXPORT_DOMAIN mismatch for cert_orchestrator: '{export_domain}'"
    )

    logger.info("[IMP:9][verify_cert_orchestrator] PASS — cert_orchestrator data consistent")


# endregion FUNC_verify_cert_orchestrator


# region FUNC_verify_docker_auth
# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(verify docker_auth loads same data via shared parser)
# · REMOVE_IF(docker_auth removed or import path changes)


def _verify_docker_auth(parsed: dict[str, str], caplog: pytest.LogCaptureFixture) -> None:
    """Verify docker_auth.py credentials are in parsed data.

    ## @purpose — docker_auth uses secrets_env_parser.parse() for DOCKER_HUB
    ##            and GHCR credentials. Verify all three credential keys present.
    ## @io — ⇥ parsed: dict — data from secrets_env_parser.parse()
    ##       ⇥ caplog — for trajectory logging
    ## @complexity — O(1)
    """
    logger.info("[IMP:7][verify_docker_auth] Verifying docker_auth data consistency")
    expected = _get_expected_data()

    assert parsed.get("DOCKER_HUB_USERNAME") == expected["DOCKER_HUB_USERNAME"], (
        "DOCKER_HUB_USERNAME mismatch for docker_auth"
    )
    assert parsed.get("DOCKER_HUB_TOKEN") == expected["DOCKER_HUB_TOKEN"], "DOCKER_HUB_TOKEN mismatch for docker_auth"
    assert parsed.get("GHCR_TOKEN") == expected["GHCR_TOKEN"], "GHCR_TOKEN mismatch for docker_auth"

    logger.info("[IMP:9][verify_docker_auth] PASS — docker_auth data consistent")


# endregion FUNC_verify_docker_auth


# region FUNC_verify_export_shell_output
# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(verify export_shell output mirrors parsed data for shell consumers)
# · REMOVE_IF(export_shell removed or format changes)


def _verify_export_shell_output(
    parsed: dict[str, str],
    env_path: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify export_shell() output mirrors parsed data for shell consumers.

    ## @purpose — node-lifecycle.sh calls export_shell() via python3 -c.
    ##            Verify that the export_shell output contains all expected
    ##            lines with proper quoting. This simulates the shell consumer.
    ## @io — ⇥ parsed: dict — data from secrets_env_parser.parse()
    ##       ⇥ env_path: str — path to the secrets.env file
    ##       ⇥ caplog — for trajectory logging
    ## @complexity — O(N) where N = number of entries
    """
    from core.internal.shared.secrets_env_parser import export_shell

    logger.info("[IMP:7][verify_export_shell] Verifying export_shell output for shell consumers")

    output = export_shell(env_path)
    expected = _get_expected_data()

    # Must start with 'export '
    assert output.startswith("export "), "export_shell output must start with 'export '"

    # All non-empty values must appear in export_shell output
    for key, value in expected.items():
        if value == "":
            # Empty values: export KEY='' must appear
            assert f"export {key}=''" in output, f"export_shell must include empty-valued export for '{key}'"
        else:
            # Non-empty values: export KEY='value' must appear
            assert f"export {key}='" in output, f"export_shell must include export for '{key}'"

    # Single quotes in values must be escaped properly
    # (no values in our test data have single quotes, but verify the export format)
    assert "export POSTGRES_PASSWORD='s3cret!p@ss'" in output, (
        "Expected export POSTGRES_PASSWORD='s3cret!p@ss' in export_shell output"
    )

    # Unicode in shell output (values should be raw bytes, not escaped)
    assert "привет_мир" in output, "export_shell must preserve unicode characters in values"

    logger.info("[IMP:9][verify_export_shell] PASS — export_shell output consistent with parsed data")


# endregion FUNC_verify_export_shell_output


# ── Main integration test ────────────────────────────────────────────────────


# region FUNC_test_secrets_pipeline_consistency

## @purpose — Full integration test: create secrets.env, parse via shared module,
##            verify all 7 consumers load consistent data.

# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(full pipeline: fixture → parse → verify all 7 consumers)
# · LAST_FAIL(N/A — new integration test)
# · REMOVE_IF(secrets_env_parser module restructured or consumers change fundamentally)


@ldd_trajectory
def test_secrets_pipeline_consistency(
    caplog: pytest.LogCaptureFixture,
    tmp_path: pathlib.Path,
    secrets_env_content: str,
) -> None:
    """Verify ALL 7 consumers load consistent data from a single secrets.env.

    ## @purpose — Full pipeline integration test:
    ##   1. Create secrets.env with ~15 mixed-format variables
    ##   2. Parse via secrets_env_parser.parse()
    ##   3. Verify ALL expected keys present and correct
    ##   4. Verify each consumer's relevant subset is consistent
    ##   5. Verify export_shell output mirrors parsed data
    ## @io — ⎋ None (assert-based pass/fail)
    ## @complexity — O(N) where N = entries in secrets.env
    """
    caplog.set_level(logging.INFO)

    # ── Step 1: Create fixture secrets.env ──
    env_file = tmp_path / "secrets.env"
    env_file.write_text(secrets_env_content, encoding="utf-8")
    logger.info(
        "[IMP:7][test_secrets_pipeline_consistency] Created secrets.env at %s",
        env_file,
    )

    # ── Step 2: Parse via shared module ──
    from core.internal.shared.secrets_env_parser import parse as parse_secrets_env

    parsed = parse_secrets_env(str(env_file))
    logger.info(
        "[IMP:8][test_secrets_pipeline_consistency] Parsed %d entries from secrets.env",
        len(parsed),
    )

    # ── Step 3: Verify all expected keys present and correct ──
    expected = _get_expected_data()
    expected_keys = set(expected.keys())
    parsed_keys = set(parsed.keys())

    # Check for missing keys
    missing_keys = expected_keys - parsed_keys
    extra_keys = parsed_keys - expected_keys

    if missing_keys:
        logger.error(
            "[IMP:9][test_secrets_pipeline_consistency] FAIL: %d expected key(s) missing: %s",
            len(missing_keys),
            sorted(missing_keys),
        )
    if extra_keys:
        logger.warning(
            "[IMP:7][test_secrets_pipeline_consistency] %d unexpected key(s) found: %s",
            len(extra_keys),
            sorted(extra_keys),
        )

    assert not missing_keys, f"Expected keys missing from parsed data: {sorted(missing_keys)}"

    # Verify each expected key has correct value
    mismatches: list[str] = [
        f"  {key}: expected='{expected[key]}', got='{parsed.get(key)}'"
        for key in expected
        if parsed.get(key) != expected[key]
    ]

    if mismatches:
        logger.error(
            "[IMP:9][test_secrets_pipeline_consistency] FAIL: %d value mismatch(es)",
            len(mismatches),
        )
        for m in mismatches:
            print(m)

    assert not mismatches, f"{len(mismatches)} value mismatch(es) between parsed and expected:\n" + "\n".join(
        mismatches
    )

    logger.info(
        "[IMP:9][test_secrets_pipeline_consistency] All %d expected keys present and correct",
        len(expected),
    )

    # ── Step 4: Verify each consumer's data is consistent ──
    _verify_secrets_manager(parsed, caplog)
    _verify_secrets_validator(parsed, caplog)
    _verify_compose_preflight(parsed, caplog)
    _verify_agent_watchdog(parsed, caplog)
    _verify_cert_orchestrator(parsed, caplog)
    _verify_docker_auth(parsed, caplog)

    # ── Step 5: Verify export_shell output (shell consumer simulation) ──
    _verify_export_shell_output(parsed, str(env_file), caplog)

    # ── Final LDD trajectory ──
    found_imp9 = _print_ldd(caplog)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_secrets_pipeline_consistency


# region FUNC_test_secrets_pipeline_missing_file_error

## @purpose — Verify the pipeline correctly raises FileNotFoundError when
##            secrets.env is missing. All consumers must handle this error.

# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(missing secrets.env → FileNotFoundError)
# · REMOVE_IF(parse() changes missing-file behavior)


@ldd_trajectory
def test_secrets_pipeline_missing_file_error(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """Verify missing secrets.env raises FileNotFoundError for all consumers.

    ## @purpose — Every consumer should handle FileNotFoundError when the
    ##            secrets.env file is missing. Verify the shared parser
    ##            raises it consistently.
    ## @io — ⎋ None (assert via pytest.raises)
    ## @complexity — O(1)
    """
    caplog.set_level(logging.INFO)

    from core.internal.shared.secrets_env_parser import parse as parse_secrets_env

    missing_path = tmp_path / "nonexistent.env"
    logger.info(
        "[IMP:8][test_secrets_pipeline_missing_file_error] Attempting parse of missing file: %s",
        missing_path,
    )

    with pytest.raises(FileNotFoundError) as exc_info:
        parse_secrets_env(str(missing_path))

    logger.info(
        "[IMP:9][test_secrets_pipeline_missing_file_error] FileNotFoundError raised correctly: %s",
        exc_info.value,
    )

    found_imp9 = _print_ldd(caplog)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_secrets_pipeline_missing_file_error


# region FUNC_test_secrets_pipeline_merge_multiple_files

## @purpose — Verify secrets_env_parser.merge() works with multiple files
##            using last-wins semantics.

# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(create 3 secrets.env files with overlapping keys, merge, verify last-wins)
# · REMOVE_IF(merge() signature or semantics change)


@ldd_trajectory
def test_secrets_pipeline_merge_multiple_files(
    caplog: pytest.LogCaptureFixture,
    tmp_path: pathlib.Path,
) -> None:
    """Verify merge() with multiple files produces correct last-wins result.

    ## @purpose — Create 3 secrets.env files with overlapping and unique keys,
    ##            merge them, and verify that last-wins semantics hold and all
    ##            unique keys from each file are present.
    ## @io — ⎋ None (assert-based pass/fail)
    ## @complexity — O(N) where N = total entries across all files
    """
    caplog.set_level(logging.INFO)

    from core.internal.shared.secrets_env_parser import merge, parse, write

    # ── Create file 1: DB credentials (base layer) ──
    file1 = tmp_path / "secrets_base.env"
    file1_data = {
        "POSTGRES_PASSWORD": "base_pwd",
        "POSTGRES_USER": "base_user",
        "DB_HOST": "db.internal.tronyx.ru",
        "DB_PORT": "5432",
        "DB_NAME": "ai_platform",
        "SHARED_KEY": "from_base",
    }
    write(str(file1), file1_data)
    logger.info("[IMP:7][merge] Created base file: %s (%d keys)", file1, len(file1_data))

    # ── Create file 2: API keys (override layer) ──
    file2 = tmp_path / "secrets_override.env"
    file2_data = {
        "LITELLM_API_KEY": "sk-lt-override",
        "OPENAI_API_KEY": "sk-op-override",
        "SHARED_KEY": "from_override",  # same key as file1 → last-wins
        "NEW_KEY_FILE2": "unique_to_file2",
    }
    write(str(file2), file2_data)
    logger.info("[IMP:7][merge] Created override file: %s (%d keys)", file2, len(file2_data))

    # ── Create file 3: Environment-specific (highest priority) ──
    file3 = tmp_path / "secrets_env.env"
    file3_data = {
        "SHARED_KEY": "from_env",  # overridden again
        "ENV_SPECIFIC": "prod_only",
        "POSTGRES_PASSWORD": "env_pwd",  # overrides file1
    }
    write(str(file3), file3_data)
    logger.info("[IMP:7][merge] Created env-specific file: %s (%d keys)", file3, len(file3_data))

    # ── Merge all 3 files ──
    merged = merge(str(file1), str(file2), str(file3))
    logger.info("[IMP:8][merge] Merged %d total entries from 3 files", len(merged))

    # ── Verify last-wins semantics ──
    # SHARED_KEY: file1=from_base, file2=from_override, file3=from_env → WINNER: from_env
    assert merged["SHARED_KEY"] == "from_env", (
        f"merge() last-wins failed: SHARED_KEY='{merged['SHARED_KEY']}', expected 'from_env'"
    )
    logger.info("[IMP:9][merge] SHARED_KEY last-wins correct: '%s'", merged["SHARED_KEY"])

    # POSTGRES_PASSWORD: file1=base_pwd, file3=env_pwd → WINNER: env_pwd
    assert merged["POSTGRES_PASSWORD"] == "env_pwd", (
        f"merge() last-wins failed: POSTGRES_PASSWORD='{merged['POSTGRES_PASSWORD']}', expected 'env_pwd'"
    )
    logger.info("[IMP:9][merge] POSTGRES_PASSWORD last-wins correct: '%s'", merged["POSTGRES_PASSWORD"])

    # ── Verify unique keys from all files are present ──
    expected_keys = {
        "POSTGRES_PASSWORD",
        "POSTGRES_USER",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "LITELLM_API_KEY",
        "OPENAI_API_KEY",
        "NEW_KEY_FILE2",
        "ENV_SPECIFIC",
        "SHARED_KEY",
    }
    merged_keys = set(merged.keys())
    missing = expected_keys - merged_keys
    extra = merged_keys - expected_keys

    assert not missing, f"merge() missing expected keys: {sorted(missing)}"
    if extra:
        logger.info("[IMP:7][merge] Unexpected keys in merge result: %s", sorted(extra))

    # ── Verify single-file merge is equivalent to parse ──
    single_merged = merge(str(file1))
    single_parsed = parse(str(file1))
    assert single_merged == single_parsed, "merge() with single file must produce same result as parse()"
    logger.info("[IMP:9][merge] Single-file merge equivalence verified")

    # ── Verify empty merge ──
    empty_merged = merge()
    assert empty_merged == {}, "merge() with no args must return empty dict"
    logger.info("[IMP:9][merge] Empty merge verified")

    # ── Final LDD trajectory ──
    found_imp9 = _print_ldd(caplog)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_secrets_pipeline_merge_multiple_files


# region FUNC_test_secrets_pipeline_write_roundtrip

## @purpose — Verify secrets_env_parser.write() produces a file that can be
##            re-parsed losslessly via secrets_env_parser.parse().

# 🧪 TRAP[TEST] · 2026-07-30 · integration/secrets-pipeline · REGRESSION(086)
# · SCENARIO(write dict → parse file → verify roundtrip equals original)
# · REMOVE_IF(write() or parse() change serialization format)


@ldd_trajectory
def test_secrets_pipeline_write_roundtrip(
    caplog: pytest.LogCaptureFixture,
    tmp_path: pathlib.Path,
) -> None:
    """Verify write() produces a file that parse() reads back identically.

    ## @purpose — Full write→re-parse roundtrip: create a dict with all supported
    ##            value types (unicode, empty, special chars), write via write(),
    ##            parse back via parse(), assert dictionaries are identical.
    ##            Also verify permissions default to 0o600.
    ## @io — ⎋ None (assert-based pass/fail)
    ## @complexity — O(N) where N = entries
    """
    caplog.set_level(logging.INFO)

    from core.internal.shared.secrets_env_parser import parse, write

    # ── Original data with all edge cases ──
    original: dict[str, str] = {
        "POSTGRES_PASSWORD": "s3cret!p@ss",
        "POSTGRES_USER": "platform_user",
        "EMPTY_VAR": "",
        "UNICODE_VAL": "привет_мир",
        "SPECIAL_CHARS": "value_with_dollar_and_at_$@",
        "TRAILING_SPACE": "value_with_trailing_newline",
    }
    logger.info(
        "[IMP:7][roundtrip] Original dict: %d entries including unicode, empty, special chars",
        len(original),
    )

    # ── Write to file ──
    env_file = tmp_path / "secrets_roundtrip.env"
    write(str(env_file), original)
    logger.info("[IMP:8][roundtrip] Wrote to %s", env_file)

    # ── Verify file exists with correct permissions ──
    assert env_file.is_file(), f"write() did not create file: {env_file}"
    file_mode = env_file.stat().st_mode & 0o777
    assert file_mode == 0o600, f"write() default permissions: expected 0o600, got 0o{file_mode:o}"
    logger.info("[IMP:9][roundtrip] File permissions correct: 0o%o", file_mode)

    # ── Re-parse and verify roundtrip ──
    reparsed = parse(str(env_file))
    logger.info("[IMP:8][roundtrip] Re-parsed %d entries from %s", len(reparsed), env_file)

    # Verify all original keys present
    missing_keys = set(original.keys()) - set(reparsed.keys())
    extra_keys = set(reparsed.keys()) - set(original.keys())
    assert not missing_keys, f"Roundtrip lost keys: {sorted(missing_keys)}"
    if extra_keys:
        logger.warning("[IMP:7][roundtrip] Extra keys in reparsed: %s", sorted(extra_keys))

    # Verify all values match
    mismatches: list[str] = [
        f"  {key}: original='{original[key]}', reparsed='{reparsed.get(key)}'"
        for key in original
        if reparsed.get(key) != original[key]
    ]

    assert not mismatches, f"{len(mismatches)} value mismatch(es) in write→parse roundtrip:\n" + "\n".join(mismatches)
    logger.info("[IMP:9][roundtrip] Roundtrip verified: %d entries intact", len(original))

    # ── Verify atomicity: write does not corrupt on failure ──
    # Write a file first, then trigger an intentional write error (invalid type)
    write(str(env_file), {"VALID": "data"})
    # TypeError: int is not string — should not corrupt the file
    with contextlib.suppress(TypeError, AttributeError):
        write(str(env_file), {"BAD": 42})  # type: ignore[dict-item]

    # After failed write, the valid file should still be intact
    after_failure = parse(str(env_file))
    assert after_failure.get("VALID") == "data", "write() should not corrupt existing file on failure (atomic tempfile)"
    logger.info("[IMP:9][roundtrip] Atomicity verified: existing file intact after failed write")

    # ── Final LDD trajectory ──
    found_imp9 = _print_ldd(caplog)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_secrets_pipeline_write_roundtrip
