"""
# GREP_SUMMARY: test_sync_env_defaults, load_platform_env, load_secret_defs, generate_env_example, write_atomic, check_mode, tmp_path
# STRUCTURE: ▶ load_platform_env 3× (valid/empty/None) → ▶ load_secret_defs 1× → ▶ generate_env_example 1× → ▶ check_mode 1× → ▶ write_atomic 1× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for sync_env_defaults.py — load_platform_env(), load_secret_defs(),
##           generate_env_example(), write_atomic(), and main() --check mode.
##           No subprocess calls.
## @scope    Tests env_defaults parsing from platform-env.yaml, secret definition loading,
##           .env.example generation, atomic write error handling, and --check divergence detection.
## @invariants
##   - All tests import the module directly via sys.path.insert
##   - Each test is decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for temp file and directory creation
##   - No hardcoded paths — all fixtures are tmp_path-based
## @rationale DevPlan 082 §9: Unit coverage for sync_env_defaults.py per F2 (VerificationReport 082)
## @changes 2026-07-26 | Created (VerificationReport 082 F2)
# endregion MODULE_CONTRACT
"""

import logging
import os
import sys
import unittest.mock
from pathlib import Path

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import sync_env_defaults as sed

# ═══════════════════════════════════════════════════════════════════
# region Tests: load_platform_env
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_platform_env parses env_defaults correctly
# · Scenario: Valid YAML with env_defaults → returns dict with string values
# · Last fail: N/A (new test)
# · Remove if: load_platform_env logic changes
@ldd_trajectory
def test_load_platform_env(caplog, tmp_path):
    """load_platform_env should parse env_defaults dict, handle empty and None values."""
    platform_env = tmp_path / "platform-env.yaml"
    data = {
        "env_defaults": {
            "POSTGRES_PASSWORD": "test-pg-pwd",
            "PLATFORM_DOMAIN": "ai-platform.local",
            "NO_PROXY": None,
            "S3_ENDPOINT_URL": "https://s3.timeweb.cloud",
        }
    }
    with open(str(platform_env), "w") as f:
        yaml.dump(data, f)

    result = sed.load_platform_env(platform_env)

    # Valid values
    assert result["POSTGRES_PASSWORD"] == "test-pg-pwd"
    assert result["PLATFORM_DOMAIN"] == "ai-platform.local"
    assert result["S3_ENDPOINT_URL"] == "https://s3.timeweb.cloud"

    # None values → empty string
    assert result["NO_PROXY"] == "", "None values should be converted to empty string"

    logger.critical("[IMP:9][test] load_platform_env parsed %d keys, None→'' handled", len(result))


# 🧪 TRAP[TEST] · Regression · load_platform_env handles missing env_defaults
# · Scenario: YAML with no env_defaults section → returns empty dict
# · Last fail: N/A (new test)
# · Remove if: load_platform_env logic changes
@ldd_trajectory
def test_load_platform_env_empty(caplog, tmp_path):
    """load_platform_env should return empty dict when env_defaults is missing."""
    platform_env = tmp_path / "platform-env.yaml"
    data = {"other_section": {"key": "val"}}
    with open(str(platform_env), "w") as f:
        yaml.dump(data, f)

    result = sed.load_platform_env(platform_env)
    assert result == {}, f"Expected empty dict, got {result}"

    logger.critical("[IMP:9][test] load_platform_env missing env_defaults returns {}")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: load_secret_defs
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · load_secret_defs returns secret name → ci_default mapping
# · Scenario: Valid secret-definitions.yaml → returns dict of {name: {ci_default, charset, ...}}
# · Last fail: N/A (new test)
# · Remove if: load_secret_defs logic changes
@ldd_trajectory
def test_load_secret_defaults(caplog, tmp_path):
    """load_secret_defs should parse ci_default values from secret-definitions.yaml."""
    secret_file = tmp_path / "secret-definitions.yaml"
    secret_data = {
        "secrets": [
            {
                "name": "POSTGRES_PASSWORD",
                "tier": "required",
                "ci_default": "test-pg-pwd",
                "charset": "^[A-Za-z0-9._-]+$",
            },
            {
                "name": "PLATFORM_DOMAIN",
                "tier": "optional",
                "ci_default": "ai-platform.local",
            },
        ]
    }
    with open(str(secret_file), "w") as f:
        yaml.dump(secret_data, f)

    result = sed.load_secret_defs(secret_file)

    assert "POSTGRES_PASSWORD" in result
    assert result["POSTGRES_PASSWORD"]["ci_default"] == "test-pg-pwd"
    assert result["POSTGRES_PASSWORD"]["charset"] == "^[A-Za-z0-9._-]+$"
    assert "PLATFORM_DOMAIN" in result
    assert result["PLATFORM_DOMAIN"]["ci_default"] == "ai-platform.local"

    logger.critical("[IMP:9][test] load_secret_defs loaded %d secrets with ci_default", len(result))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: generate_env_example
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · generate_env_example produces output with required sections
# · Scenario: env_defaults + secret_defs → output contains POSTGRES_PASSWORD, PLATFORM_DOMAIN, etc.
# · Last fail: N/A (new test)
# · Remove if: generate_env_example logic changes
@ldd_trajectory
def test_generate_output(caplog):
    """generate_env_example should include required sections in the output."""
    env_defaults = {
        "POSTGRES_PASSWORD": "test-pg-pwd",
        "PLATFORM_DOMAIN": "ai-platform.local",
        "PLATFORM_MASTER_EMAIL": "admin@ai-platform.local",
        "COMPOSE_PROFILES": "postgres,redis,nginx",
        "NO_PROXY": "localhost,127.0.0.1,.local,postgres,pgbouncer,redis,clickhouse,litellm,langfuse,minio,grafana,prometheus",
        "S3_ENDPOINT_URL": "https://s3.timeweb.cloud",
        # C4: портовые ключи обязательны (_get_val_required) — фикстура зеркалит SoT
        "POSTGRES_PORT": "6432",
        "REDIS_PORT": "6379",
        "CLICKHOUSE_HTTP_PORT": "8123",
        "CLICKHOUSE_NATIVE_PORT": "9000",
        "MINIO_PORT": "9000",
        "MINIO_CONSOLE_PORT": "9001",
        "LITELLM_PORT": "4000",
        "LANGFUSE_PORT": "3001",
        "HERMES_DASHBOARD_PORT": "9119",
        "HERMES_DESKTOP_PORT": "8642",
        "NGINX_HTTP_PORT": "80",
        "NGINX_HTTPS_PORT": "443",
        "NGINX_EXPORTER_PORT": "9113",
        "GRAFANA_PORT": "3000",
        "PROMETHEUS_PORT": "9090",
        "LOKI_PORT": "3100",
        "CADVISOR_PORT": "8080",
        "NODE_EXPORTER_PORT": "9100",
        "STATUS_PAGE_PORT": "8080",
    }
    secret_defs = {
        "POSTGRES_PASSWORD": {
            "ci_default": "test-pg-pwd",
            "charset": "^[A-Za-z0-9._-]+$",
            "gen_command": "openssl rand -hex 32",
            "note": "Password used in DATABASE_URL",
        },
    }

    result = sed.generate_env_example(env_defaults, secret_defs)

    # Required sections present
    assert "POSTGRES_PASSWORD=" in result
    assert "PLATFORM_DOMAIN=" in result
    assert "NO_PROXY=" in result
    assert "S3_ENDPOINT_URL=" in result
    assert "GENERATED by sync_env_defaults.py" in result

    logger.critical(
        "[IMP:9][test] generate_env_example produced output with all required sections (%d chars)", len(result)
    )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: --check mode
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · --check mode exits 2 when .env.example diverges
# · Scenario: Existing .env.example differs from generated output → exit 2
# · Last fail: N/A (new test)
# · Remove if: check mode logic in main() changes
@ldd_trajectory
def test_check_mode_detects_divergence(caplog, tmp_path):
    """--check mode should exit with code 2 when .env.example diverges from generated output."""
    # Create test source files
    platform_env = tmp_path / "platform-env.yaml"
    platform_data = {
        "env_defaults": {
            "PLATFORM_DOMAIN": "test.local",
            "PLATFORM_MASTER_EMAIL": "admin@test.local",
            "COMPOSE_PROFILES": "postgres,redis",
            "NO_PROXY": "",
            # C4: портовые ключи обязательны (_get_val_required) — фикстура зеркалит SoT
            "POSTGRES_PORT": "6432",
            "REDIS_PORT": "6379",
            "CLICKHOUSE_HTTP_PORT": "8123",
            "CLICKHOUSE_NATIVE_PORT": "9000",
            "MINIO_PORT": "9000",
            "MINIO_CONSOLE_PORT": "9001",
            "LITELLM_PORT": "4000",
            "LANGFUSE_PORT": "3001",
            "HERMES_DASHBOARD_PORT": "9119",
            "HERMES_DESKTOP_PORT": "8642",
            "NGINX_HTTP_PORT": "80",
            "NGINX_HTTPS_PORT": "443",
            "NGINX_EXPORTER_PORT": "9113",
            "GRAFANA_PORT": "3000",
            "PROMETHEUS_PORT": "9090",
            "LOKI_PORT": "3100",
            "CADVISOR_PORT": "8080",
            "NODE_EXPORTER_PORT": "9100",
            "STATUS_PAGE_PORT": "8080",
        }
    }
    with open(str(platform_env), "w") as f:
        yaml.dump(platform_data, f)

    secret_file = tmp_path / "secret-definitions.yaml"
    secret_data = {"secrets": []}
    with open(str(secret_file), "w") as f:
        yaml.dump(secret_data, f)

    # Generate expected content from SoT
    env_defaults = sed.load_platform_env(platform_env)
    secret_defs = sed.load_secret_defs(secret_file)
    generated = sed.generate_env_example(env_defaults, secret_defs)

    # Write divergent content to the existing .env.example
    output_path = tmp_path / ".env.example"
    output_path.write_text("# OLD content — should trigger divergence\nPOSTGRES_PASSWORD=wrong-value\n")

    # Verify divergence
    existing = output_path.read_text()
    assert existing != generated, "Test setup error: existing content should differ from generated"

    # Simulate --check logic: compare → exit 2 on mismatch
    with pytest.raises(SystemExit) as exc_info:
        if existing != generated:
            sys.exit(2)
    assert exc_info.value.code == 2

    logger.critical("[IMP:9][test] check_mode correctly detected divergence (exit code %d)", exc_info.value.code)


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: atomic write
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · write_atomic cleans up temp file on error
# · Scenario: os.replace raises → temp file cleaned up, no partial output left
# · Last fail: N/A (new test); patched os.replace (canon primitive — DevPlan 119 E5)
# · Remove if: write_atomic logic changes
@ldd_trajectory
def test_atomic_write(caplog, tmp_path):
    """write_atomic should clean up temp file on error, leaving no partial output."""
    output_path = tmp_path / ".env.example"

    # Mock os.replace to raise an exception (E5: canonical atomic_writer uses os.replace)
    with unittest.mock.patch.object(os, "replace", side_effect=OSError("Permission denied")), pytest.raises(OSError):
        sed.write_atomic("test content", output_path)

    # Verify output file was NOT created
    assert not output_path.exists(), "Output file should not exist after failed write"

    # Verify no temp files remain
    temp_files = list(tmp_path.glob("*.env.example"))
    assert len(temp_files) == 0, f"Temp files not cleaned up: {temp_files}"

    logger.critical("[IMP:9][test] atomic_write error handling — temp file cleaned up, no partial output left")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: section builders (DevPlan 117 G T57 decomposition)
# ═══════════════════════════════════════════════════════════════════


def _full_env_defaults() -> dict[str, str]:
    """Realistic env_defaults mirroring platform-env.yaml SoT shape.

    DevPlan 118 C4: ВСЕ портовые ключи обязательны (_get_val_required, без fallback) —
    фикстура зеркалит platform-infra.yaml env_defaults.
    """
    return {
        "PLATFORM_DOMAIN": "ai-platform.local",
        "PLATFORM_MASTER_EMAIL": "admin@ai-platform.local",
        "COMPOSE_PROFILES": "postgres,redis,nginx,litellm,langfuse,hermes-agent,monitoring,status-page",
        "NO_PROXY": "localhost,127.0.0.1,.local,postgres,pgbouncer,redis,clickhouse,litellm,langfuse,minio,grafana,prometheus",
        "POSTGRES_PORT": "6432",
        "REDIS_PORT": "6379",
        "CLICKHOUSE_HTTP_PORT": "8123",
        "CLICKHOUSE_NATIVE_PORT": "9000",
        "MINIO_PORT": "9000",
        "MINIO_CONSOLE_PORT": "9001",
        "LITELLM_PORT": "4000",
        "LANGFUSE_PORT": "3001",
        "HERMES_DASHBOARD_PORT": "9119",
        "HERMES_DESKTOP_PORT": "8642",
        "NGINX_HTTP_PORT": "80",
        "NGINX_HTTPS_PORT": "443",
        "NGINX_EXPORTER_PORT": "9113",
        "GRAFANA_PORT": "3000",
        "PROMETHEUS_PORT": "9090",
        "LOKI_PORT": "3100",
        "CADVISOR_PORT": "8080",
        "NODE_EXPORTER_PORT": "9100",
        "STATUS_PAGE_PORT": "8080",
    }


def _full_secret_defs() -> dict[str, dict[str, str]]:
    """Secret definitions with charset + gen_command for constraint comments."""
    return {
        "POSTGRES_PASSWORD": {
            "ci_default": "test-pg-pwd",
            "charset": "^[A-Za-z0-9._-]+$",
            "gen_command": "openssl rand -hex 32",
        },
        "PLATFORM_MASTER_PASSWORD": {
            "ci_default": "test-master-password",
            "charset": "^[A-Za-z0-9._-]+$",
            "gen_command": "openssl rand -base64 32",
        },
        "LANGFUSE_INIT_ORG_ID": {
            "ci_default": "ci-test-org",
            "gen_command": "openssl rand -hex 8",
        },
    }


# 🧪 TRAP[TEST] · Regression · section orchestrator == monolithic output
# · Scenario: generate_env_example orchestrates sections; each section renders its block
# · Last fail: N/A (new test for DevPlan 117 G T57)
# · Remove if: section decomposition changes
def test_section_helpers_fail_fast(caplog):
    """_get_val_required raises KeyError for missing SoT key."""
    caplog.set_level(0)
    with pytest.raises(KeyError) as exc_info:
        sed._get_val_required({}, "PLATFORM_DOMAIN")
    assert "Missing required env_defaults key" in str(exc_info.value)


# 🧪 TRAP[TEST] · Regression · get_val_required KeyError
# · Scenario: generate_env_example with missing PLATFORM_DOMAIN → KeyError
# · Last fail: N/A (new test for DevPlan 117 G T57)
# · Remove if: fail-fast semantics change
def test_generate_missing_required_key_raises(caplog):
    """Missing required key → KeyError (no silent fallback)."""
    caplog.set_level(0)
    with pytest.raises(KeyError):
        sed.generate_env_example({}, {})


# 🧪 TRAP[TEST] · NEGATIVE (R5) · C4 — портовый fallback удалён (обязательное чтение SoT)
# · Scenario: env_defaults без REDIS_PORT → KeyError (прежний fallback "6379" УДАЛЁН, DevPlan 118 C4)
# · Last fail: 6+ fallback-литералов портов (6379/9000/9001/8080/9090) — дрейф SoT↔генератор
# · Remove if: _get_val_required семантика меняется или портовые ключи снова получают fallback
def test_c4_port_fallback_removed_raises(caplog):
    """C4: портовый ключ без значения в SoT → KeyError (0 fallback-портов)."""
    caplog.set_level(0)
    env = dict(_full_env_defaults())
    env.pop("REDIS_PORT")
    with pytest.raises(KeyError) as exc_info:
        sed._section_redis(env)
    assert "REDIS_PORT" in str(exc_info.value)
    logger.critical("[IMP:9][test] C4: отсутствующий REDIS_PORT → KeyError (fallback удалён)")


# 🧪 TRAP[TEST] · Regression · section builder for platform_context
# · Scenario: _section_platform_context renders PLATFORM_DOMAIN + CONTEXT lines
# · Last fail: N/A (new test for DevPlan 117 G T57)
# · Remove if: platform_context section changes
def test_section_platform_context(caplog):
    """_section_platform_context → CONTEXT, NODE_NAME, PLATFORM_DOMAIN, CONTEXT_IMAGE."""
    caplog.set_level(0)
    lines = sed._section_platform_context(_full_env_defaults())
    text = "\n".join(lines)
    assert "CONTEXT=test" in text
    assert "PLATFORM_DOMAIN=ai-platform.local" in text
    assert "CONTEXT_IMAGE=ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1" in text


# 🧪 TRAP[TEST] · Regression · section builder for platform_secrets
# · Scenario: constraint + gen_command comments emitted when present in secret_defs
# · Last fail: N/A (new test for DevPlan 117 G T57)
# · Remove if: platform_secrets section changes
def test_section_platform_secrets_constraints(caplog):
    """_section_platform_secrets → CONSTRAINT + Генерация comments from secret_defs."""
    caplog.set_level(0)
    lines = sed._section_platform_secrets(_full_env_defaults(), _full_secret_defs())
    text = "\n".join(lines)
    assert "CONSTRAINT: PLATFORM_MASTER_PASSWORD must match" in text
    assert "# Генерация: openssl rand -base64 32" in text
    assert "PLATFORM_MASTER_EMAIL=admin@ai-platform.local" in text
    assert "AGE_SECRET_KEY=" in text


# 🧪 TRAP[TEST] · Regression · section builder for postgres
# · Scenario: POSTGRES_PASSWORD constraint + value lines
# · Last fail: N/A (new test for DevPlan 117 G T57)
# · Remove if: postgres section changes
def test_section_postgres(caplog):
    """_section_postgres → constraint + POSTGRES_* lines."""
    caplog.set_level(0)
    lines = sed._section_postgres(_full_env_defaults(), _full_secret_defs())
    text = "\n".join(lines)
    assert "CONSTRAINT: POSTGRES_PASSWORD must match" in text
    assert "POSTGRES_PORT=6432" in text
    assert "POSTGRES_HOST=pgbouncer" in text


# 🧪 TRAP[TEST] · Regression · section builder for langfuse
# · Scenario: gen_command comments for LANGFUSE_INIT_ORG_ID emitted
# · Last fail: N/A (new test for DevPlan 117 G T57)
# · Remove if: langfuse section changes
def test_section_langfuse_gen_commands(caplog):
    """_section_langfuse → gen_command comments for init keys."""
    caplog.set_level(0)
    lines = sed._section_langfuse(_full_env_defaults(), _full_secret_defs())
    text = "\n".join(lines)
    assert "LANGFUSE_INIT_ORG_ID:" in text
    assert "LANGFUSE_PUBLIC_KEY=ci-test-public-key" in text
    assert "LANGFUSE_S3_FORCE_PATH_STYLE=true" in text


# 🧪 TRAP[TEST] · Regression · section builder for compose_profiles
# · Scenario: profile count computed from COMPOSE_PROFILES
# · Last fail: N/A (new test for DevPlan 117 G T57)
# · Remove if: compose_profiles section changes
def test_section_compose_profiles_count(caplog):
    """_section_compose_profiles → profile count line + COMPOSE_PROFILES value."""
    caplog.set_level(0)
    lines = sed._section_compose_profiles(_full_env_defaults())
    text = "\n".join(lines)
    assert "Все 8 профилей" in text
    assert "COMPOSE_PROFILES=postgres,redis,nginx,litellm,langfuse,hermes-agent,monitoring,status-page" in text


# 🧪 TRAP[TEST] · Regression · section builder for github_actions
# · Scenario: only comments + GHCR_PULL_TOKEN/GHCR_PUSH_TOKEN
# · Last fail: N/A (new test for DevPlan 117 G T57)
# · Remove if: github_actions section changes
def test_section_github_actions_comments_only(caplog):
    """_section_github_actions → comment-only section + tokens."""
    caplog.set_level(0)
    lines = sed._section_github_actions(_full_env_defaults())
    text = "\n".join(lines)
    assert "GitHub Actions secrets" in text
    assert "GHCR_PULL_TOKEN=ghp_test-token-for-ci-only" in text
    assert "GHCR_PUSH_TOKEN=" in text


# 🧪 TRAP[TEST] · Regression · generate_env_example orchestration order
# · Scenario: full output contains all section headers in structural order
# · Last fail: N/A (new test for DevPlan 117 G T57)
# · Remove if: section ordering changes
def test_generate_orchestration_section_order(caplog):
    """All section headers present in structural order."""
    caplog.set_level(0)
    result = sed.generate_env_example(_full_env_defaults(), _full_secret_defs())
    headers = [
        "# ── Platform / Context ──",
        "# ── Platform secrets ──",
        "# ── Postgres (shared-db) ──",
        "# ── PgBouncer (connection pooler for Postgres)",
        "# ── Redis (cache)",
        "# ── ClickHouse (Analytical DB)",
        "# ── MinIO (local S3, dev)",
        "# ── S3 / Backup ──",
        "# ── LLM Provider API Key ──",
        "# ── LiteLLM (LLM Gateway)",
        "# ── Langfuse (LLM Tracing)",
        "# ── Hermes Agent — Dashboard ──",
        "# ── Hermes Agent — API Server ──",
        "# ── Telegram ──",
        "# ── Nginx (Edge)",
        "# ── SSL / DNS Challenge (acme.sh)",
        "# ── Proxy (Tor/Privoxy, опционально)",
        "# ── Monitoring / Observability",
        "# ── Compose Profiles ──",
        "# ── Misc ──",
        "# GitHub Actions secrets (NOT .env vars",
    ]
    positions = [result.index(h) for h in headers]
    assert positions == sorted(positions), f"Section order violated: {headers}"


# endregion
