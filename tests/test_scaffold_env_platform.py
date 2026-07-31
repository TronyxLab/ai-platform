# GREP_SUMMARY: test gen-env-platform env-generator scaffold platform-env provides profiles pgbouncer dsn
# STRUCTURE: ┌platform_env_yaml fixture┐ → ○ 9 tests → ⊕ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Test suite for gen_env_platform.py — validates .env.platform generation contract
## @scope    9 test functions covering: header, min vars, provides list, DSN format, pgbouncer host,
##           NO_PROXY, idempotency, missing YAML error, provides⊄profiles fail-fast
## @invariants
##   - All tests use tmp_path for fixture files (no hardcoded paths)
##   - Tests call gen_env_platform.py library functions directly (native pytest,
##     NO subprocess — Python-first per DevPlan 090: gen-env-platform.sh deleted)
##   - Every test is decorated with @ldd_trajectory for LDD telemetry
##   - platform_env_yaml fixture provides minimal valid platform-env.yaml with 2 services
##   - Error paths verified via exceptions (FileNotFoundError, GenEnvPlatformValidationError) —
##     the library NEVER calls sys.exit() (module contract invariant)
## @rationale T18 per DevPlan $TEST_SPEC — validates CI-critical gen_env_platform.py contract.
##            DevPlan 090 replaced gen-env-platform.sh with gen_env_platform.py (generate()/
##            generate_env_platform()); tests rewritten from subprocess-bash to native import
##            (pre-existing failures fixed 2026-07-31).
## @changes 2026-07-17 · T18 — initial implementation (subprocess → gen-env-platform.sh)
## @changes 2026-07-31 · T18 — rewritten: native Python calls to gen_env_platform.py
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest
import yaml

from core.internal.scaffold.gen_env_platform import (
    GenEnvPlatformValidationError,
    generate_env_platform,
)
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def platform_env_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal valid platform-env.yaml with provides and profiles.

    ## @purpose — Test fixture: provides 2 services (postgres + redis) with
    ##            matching profiles and proxy NO_PROXY.
    ## @invariants
    ##   - All provides keys are in profiles (valid state)
    ##   - postgres DSN uses pgbouncer:6432 per O5
    ##   - Returns path to YAML file in tmp_path
    """
    data = {
        "provides": {
            "postgres": {
                "host": "pgbouncer",
                "port": 6432,
                "dsn_template": "postgresql://${NAME}_user:***@pgbouncer:6432/${NAME}_db",
                "networks": ["shared-db-net"],
            },
            "redis": {
                "host": "redis",
                "port": 6379,
                "url_template": "redis://redis:6379/0",
                "networks": ["shared-cache-net"],
            },
        },
        "proxy": {
            "no_proxy_internal": "localhost,127.0.0.1,.local,postgres,pgbouncer,redis,clickhouse",
        },
        "profiles": ["postgres", "redis"],
    }
    yaml_path = tmp_path / "platform-env.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
    logger.info("[IMP:8][fixture][platform_env_yaml] Created: %s", yaml_path)
    return yaml_path


@pytest.fixture
def platform_env_yaml_provides_not_in_profiles(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create platform-env.yaml where a provides key is NOT in profiles.

    ## @purpose — Test fixture for fail-fast validation (DD8).
    ##            redis key is in provides but NOT in profiles.
    """
    data = {
        "provides": {
            "postgres": {
                "host": "pgbouncer",
                "port": 6432,
                "dsn_template": "postgresql://${NAME}_user:***@pgbouncer:6432/${NAME}_db",
            },
            "redis": {
                "host": "redis",
                "port": 6379,
                "url_template": "redis://redis:6379/0",
            },
        },
        "profiles": ["postgres"],  # redis is missing
    }
    yaml_path = tmp_path / "platform-env-bad.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
    logger.info(
        "[IMP:8][fixture][platform_env_yaml_provides_not_in_profiles] Created: %s",
        yaml_path,
    )
    return yaml_path


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-07-31 · Regression: DP-090 replaced gen-env-platform.sh with gen_env_platform.py
# · Scenario: valid YAML → generate_env_platform() → first line is GENERATED header
# · Last fail: FileNotFoundError (subprocess ran deleted gen-env-platform.sh)
# · Remove if: gen_env_platform.generate() header contract changes
@ldd_trajectory
def test_gen_env_platform_has_header(caplog, platform_env_yaml: pathlib.Path) -> None:
    """Output must start with '# GENERATED by ai-platform — DO NOT EDIT'.

    ── Scenario: Valid YAML → output header ──
    """
    logger.info("[IMP:9][test][has_header] Starting — yaml=%s", platform_env_yaml)

    lines = generate_env_platform(str(platform_env_yaml), domain="ai-platform.local")

    assert lines, "generate_env_platform() returned no lines"
    first_line = lines[0]
    assert first_line.startswith("# GENERATED by ai-platform"), (
        f"Expected header starting with '# GENERATED by ai-platform', got: {first_line}"
    )
    logger.info("[IMP:9][test][has_header] Header verified: %s", first_line)


# 🧪 TRAP[TEST] · 2026-07-31 · Regression: DP-090 replaced gen-env-platform.sh with gen_env_platform.py
# · Scenario: 2-service fixture generates 9+ PLATFORM_* variables
# · Last fail: FileNotFoundError (subprocess ran deleted gen-env-platform.sh)
# · Remove if: gen_env_platform.generate() PLATFORM_* count contract changes
@ldd_trajectory
def test_gen_env_platform_min_vars(caplog, platform_env_yaml: pathlib.Path) -> None:
    """Output must contain ≥8 PLATFORM_* lines.

    ── Scenario: 2-service fixture generates 9+ PLATFORM_* variables ──
    """
    logger.info("[IMP:9][test][min_vars] Starting")

    lines = generate_env_platform(str(platform_env_yaml), domain="ai-platform.local")

    plat_lines = [line for line in lines if line.startswith("PLATFORM_")]
    plat_count = len(plat_lines)
    logger.info("[IMP:7][test][min_vars] PLATFORM_* count=%d", plat_count)
    assert plat_count >= 8, f"Expected ≥8 PLATFORM_* variables, got {plat_count}. Lines: {plat_lines}"
    logger.info("[IMP:9][test][min_vars] Verified: %d PLATFORM_* variables", plat_count)


# 🧪 TRAP[TEST] · 2026-07-31 · Regression: DP-090 replaced gen-env-platform.sh with gen_env_platform.py
# · Scenario: fixture has postgres,redis → PLATFORM_PROVIDES=postgres,redis
# · Last fail: FileNotFoundError (subprocess ran deleted gen-env-platform.sh)
# · Remove if: gen_env_platform.generate() provides-list contract changes
@ldd_trajectory
def test_gen_env_platform_provides_list(caplog, platform_env_yaml: pathlib.Path) -> None:
    """PLATFORM_PROVIDES must equal sorted keys from provides fixture.

    ── Scenario: Fixture has postgres,redis → PLATFORM_PROVIDES=postgres,redis ──
    """
    logger.info("[IMP:9][test][provides_list] Starting")

    lines = generate_env_platform(str(platform_env_yaml), domain="ai-platform.local")

    provides_line = ""
    for line in lines:
        if line.startswith("PLATFORM_PROVIDES="):
            provides_line = line
            break

    assert provides_line, "PLATFORM_PROVIDES not found in output"
    # Expected: sorted keys from fixture = postgres,redis
    expected = "postgres,redis"
    actual = provides_line.split("=", 1)[1]
    assert actual == expected, f"PLATFORM_PROVIDES mismatch: expected '{expected}', got '{actual}'"
    logger.info("[IMP:9][test][provides_list] PLATFORM_PROVIDES=%s", actual)


# 🧪 TRAP[TEST] · 2026-07-31 · Regression: DP-090 replaced gen-env-platform.sh with gen_env_platform.py
# · Scenario: postgres DSN regex check with project_name substitution
# · Last fail: FileNotFoundError (subprocess ran deleted gen-env-platform.sh)
# · Remove if: gen_env_platform.generate() DSN template contract changes
@ldd_trajectory
def test_gen_env_platform_dsn_format(caplog, platform_env_yaml: pathlib.Path) -> None:
    """DSN must match scheme://user:***@host:port/db pattern.

    ── Scenario: Postgres DSN regex check ──
    """
    import re

    logger.info("[IMP:9][test][dsn_format] Starting")

    lines = generate_env_platform(str(platform_env_yaml), domain="ai-platform.local", project_name="testapp")

    dsn_line = ""
    for line in lines:
        if "PLATFORM_POSTGRES_DSN" in line:
            dsn_line = line
            break

    assert dsn_line, "PLATFORM_POSTGRES_DSN not found in output"
    dsn_value = dsn_line.split("=", 1)[1]

    pattern = r"^\w+://[^:]+:\*\*\*@[^:]+:\d+/\w+$"
    assert re.match(pattern, dsn_value), f"DSN format mismatch: '{dsn_value}' does not match '{pattern}'"
    logger.info("[IMP:9][test][dsn_format] DSN format valid: %s", dsn_value)


# 🧪 TRAP[TEST] · 2026-07-31 · Regression: F3/O5 — postgres DSN must use pgbouncer:6432
# · Scenario: verify host=pgbouncer, port=6432 in postgres DSN (regression guard F3)
# · Last fail: FileNotFoundError (subprocess ran deleted gen-env-platform.sh)
# · Remove if: gen_env_platform.generate() pgbouncer DSN invariant changes
@ldd_trajectory
def test_gen_env_platform_dsn_host_is_pgbouncer(caplog, platform_env_yaml: pathlib.Path) -> None:
    """Postgres DSN must use pgbouncer:6432 (O5 regression check — F3).

    ── Scenario: Verify host=pgbouncer, port=6432 in postgres DSN ──
    """
    logger.info("[IMP:9][test][dsn_pgbouncer] Starting — regression guard F3")

    lines = generate_env_platform(str(platform_env_yaml), domain="ai-platform.local")

    host_line = ""
    port_line = ""
    dsn_line = ""
    for line in lines:
        if "PLATFORM_POSTGRES_HOST" in line:
            host_line = line
        elif "PLATFORM_POSTGRES_PORT" in line:
            port_line = line
        elif "PLATFORM_POSTGRES_DSN" in line:
            dsn_line = line

    assert host_line, "PLATFORM_POSTGRES_HOST not found"
    assert port_line, "PLATFORM_POSTGRES_PORT not found"
    assert dsn_line, "PLATFORM_POSTGRES_DSN not found"

    host_val = host_line.split("=", 1)[1]
    port_val = port_line.split("=", 1)[1]
    dsn_val = dsn_line.split("=", 1)[1]

    assert host_val == "pgbouncer", f"Expected host=pgbouncer, got {host_val}"
    assert port_val == "6432", f"Expected port=6432, got {port_val}"
    assert "pgbouncer" in dsn_val, f"DSN missing pgbouncer: {dsn_val}"
    assert "6432" in dsn_val, f"DSN missing port 6432: {dsn_val}"

    logger.info(
        "[IMP:9][test][dsn_pgbouncer] Verified: host=%s port=%s dsn=%s",
        host_val,
        port_val,
        dsn_val,
    )


# 🧪 TRAP[TEST] · 2026-07-31 · Regression: DP-090 replaced gen-env-platform.sh with gen_env_platform.py
# · Scenario: fixture proxy.no_proxy_internal includes pgbouncer,redis,clickhouse
# · Last fail: FileNotFoundError (subprocess ran deleted gen-env-platform.sh)
# · Remove if: gen_env_platform.generate() NO_PROXY contract changes
@ldd_trajectory
def test_gen_env_platform_no_proxy_internal(caplog, platform_env_yaml: pathlib.Path) -> None:
    """PLATFORM_NO_PROXY must contain pgbouncer and redis.

    ── Scenario: fixture proxy.no_proxy_internal includes pgbouncer,redis ──
    """
    logger.info("[IMP:9][test][no_proxy] Starting")

    lines = generate_env_platform(str(platform_env_yaml), domain="ai-platform.local")

    no_proxy_line = ""
    for line in lines:
        if line.startswith("PLATFORM_NO_PROXY="):
            no_proxy_line = line
            break

    assert no_proxy_line, "PLATFORM_NO_PROXY not found"
    no_proxy_val = no_proxy_line.split("=", 1)[1]

    assert "pgbouncer" in no_proxy_val, f"NO_PROXY missing pgbouncer: {no_proxy_val}"
    assert "redis" in no_proxy_val, f"NO_PROXY missing redis: {no_proxy_val}"
    assert "clickhouse" in no_proxy_val, f"NO_PROXY missing clickhouse: {no_proxy_val}"

    logger.info("[IMP:9][test][no_proxy] Verified NO_PROXY contains pgbouncer,redis,clickhouse: %s", no_proxy_val)


# 🧪 TRAP[TEST] · 2026-07-31 · Regression: DP-090 replaced gen-env-platform.sh with gen_env_platform.py
# · Scenario: same YAML + same args → identical output (excluding timestamp line)
# · Last fail: FileNotFoundError (subprocess ran deleted gen-env-platform.sh)
# · Remove if: gen_env_platform.generate() idempotency contract changes
@ldd_trajectory
def test_gen_env_platform_idempotent(caplog, platform_env_yaml: pathlib.Path) -> None:
    """Two runs with same YAML must produce identical output.

    ── Scenario: Same YAML, same env → identical lines (excluding timestamp line) ──
    """
    logger.info("[IMP:9][test][idempotent] Starting — two runs with same YAML")

    def run_gen() -> list[str]:
        lines = generate_env_platform(str(platform_env_yaml), domain="ai-platform.local")
        # Skip timestamp line (different on each run)
        return [line for line in lines if not line.startswith("# Generated:")]

    output1 = run_gen()
    output2 = run_gen()

    assert output1 == output2, (
        "Idempotency violation: two runs produced different output (ignoring # Generated: timestamp)"
    )
    logger.info("[IMP:9][test][idempotent] Verified: both runs produce identical output (%d lines)", len(output1))


# 🧪 TRAP[TEST] · 2026-07-31 · Regression: DP-090 replaced gen-env-platform.sh with gen_env_platform.py
# · Scenario: --yaml points to non-existent file → FileNotFoundError with "not found" message
# · Last fail: FileNotFoundError (subprocess ran deleted gen-env-platform.sh)
# · Remove if: gen_env_platform.load_yaml() missing-file error contract changes
@ldd_trajectory
def test_gen_env_platform_missing_yaml(caplog, tmp_path: pathlib.Path) -> None:
    """Must fail with error when platform-env.yaml does not exist.

    ── Scenario: missing YAML path → FileNotFoundError, error message ──
    """
    missing_yaml = tmp_path / "nonexistent.yaml"
    logger.info("[IMP:9][test][missing_yaml] Using: %s", missing_yaml)

    with pytest.raises(FileNotFoundError) as exc_info:
        generate_env_platform(str(missing_yaml), domain="ai-platform.local")

    error_text = str(exc_info.value).lower()
    assert "not found" in error_text, f"Expected error message about missing file, got: {error_text}"
    logger.info("[IMP:9][test][missing_yaml] Verified: FileNotFoundError=%s", exc_info.value)


# 🧪 TRAP[TEST] · 2026-07-31 · Regression: DD8/F8 — provides key not in profiles must fail-fast
# · Scenario: redis in provides but not in profiles → GenEnvPlatformValidationError with key name
# · Last fail: FileNotFoundError (subprocess ran deleted gen-env-platform.sh)
# · Remove if: gen_env_platform.validate_provides() fail-fast contract changes
@ldd_trajectory
def test_gen_env_platform_provides_in_profiles(
    caplog,
    platform_env_yaml_provides_not_in_profiles: pathlib.Path,
) -> None:
    """Provides key NOT in profiles must cause fail-fast (DD8 regression — F8).

    ── Scenario: redis in provides but not in profiles → GenEnvPlatformValidationError
    ──          with key name in the error message ──
    """
    bad_yaml = platform_env_yaml_provides_not_in_profiles
    logger.info("[IMP:9][test][provides_in_profiles] Using bad YAML: %s", bad_yaml)

    with pytest.raises(GenEnvPlatformValidationError) as exc_info:
        generate_env_platform(str(bad_yaml), domain="ai-platform.local")

    error_text = str(exc_info.value).lower()
    assert "redis" in error_text, f"Expected error to mention the offending key 'redis'. Got: {error_text}"
    assert "profiles" in error_text, f"Expected error about profiles. Got: {error_text}"
    logger.info("[IMP:9][test][provides_in_profiles] Verified: fail-fast on provides⊄profiles: %s", exc_info.value)
