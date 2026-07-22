#!/usr/bin/env python3
"""Unit tests for compose_preflight.py — docker compose preflight secret validation."""
# GREP_SUMMARY: test_compose_preflight, preflight, secrets-validation, parse-compose-args, resolve-modules, check-secrets, charset-validation
# STRUCTURE: ▶ compose_preflight module → ◇ test_parse_compose_args(4 cases) → ◇ test_resolve_modules(3 cases) → ◇ test_load_env_map(2 cases) → ◇ test_check_secrets(3 cases) → ◇ test_validate_charsets(2 cases) → ◇ test_main_cli(4 cases) → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Unit tests for compose_preflight.py — all 5 public functions + main CLI dispatch
## @scope    Direct Python import of compose_preflight module; tests each function in isolation with tmp_path fixtures
## @invariants
##   - No Docker dependency — pure Python unit tests
##   - Uses tmp_path for YAML fixtures (Zero Hardcode Rule)
##   - Tests cover: arg parsing, module resolution, env file loading, secret checking, charset validation, and main()
## @rationale New module (TASK-4 Plan 049) needs full unit test coverage before deployment
## @changes 2026-07-22 | Created (TASK-6 Plan 049)
# endregion MODULE_CONTRACT

import logging
import os
import sys
from pathlib import Path
from typing import Generator

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"))
from compose_preflight import (
    check_secrets,
    load_env_map,
    load_manifest,
    main,
    parse_compose_args,
    resolve_modules,
    validate_charsets,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_manifest(tmp_path: Path) -> Path:
    """Create a sample secrets-manifest.yaml for testing."""
    manifest = tmp_path / "secrets-manifest.yaml"
    manifest.write_text(
        """\
version: 1
secrets:
  - name: POSTGRES_PASSWORD
    tier: required
    consumers: [postgres, litellm]
    source: sops
    charset: "^[A-Za-z0-9._-]+$"
  - name: LITELLM_MASTER_KEY
    tier: generated
    consumers: [litellm]
    source: autogen
  - name: TELEGRAM_BOT_TOKEN
    tier: required
    consumers: [hermes-agent]
    source: sops
    charset: "^[0-9]+:[A-Za-z0-9_-]+$"
  - name: DEEPSEEK_API_KEY
    tier: optional
    consumers: [litellm]
    source: sops
  - name: API_SERVER_KEY
    tier: optional
    consumers: [hermes-agent]
    source: autogen
  - name: LITELLM_METRICS_TOKEN
    tier: required
    consumers: [monitoring]
    source: sops
"""
    )
    return manifest


@pytest.fixture
def sample_secrets_env(tmp_path: Path) -> Path:
    """Create a sample secrets.env file."""
    env_file = tmp_path / "secrets.env"
    env_file.write_text(
        """\
# Platform secrets
POSTGRES_PASSWORD=supersecret123
LITELLM_MASTER_KEY=sk-abcdef0123456789
TELEGRAM_BOT_TOKEN=123456:ABC-DEF12345
"""
    )
    return env_file


# =============================================================================
# test_parse_compose_args
# =============================================================================


# 🧪 TRAP[TEST] · 2026-07-22 · REGRESSION · parse_compose_args with --profile args
# · Last fail: N/A (new module)
# · Remove if: compose arg parsing changes
class TestParseComposeArgs:
    @pytest.mark.parametrize(
        "args, expected_profiles, expected_subcommand",
        [
            (["up", "--profile", "postgres"], {"postgres"}, "up"),
            (["--profile", "postgres", "--profile", "litellm", "up"], {"postgres", "litellm"}, "up"),
            (["--profile=postgres", "--profile=litellm", "up"], {"postgres", "litellm"}, "up"),
            (["up", "-d", "--profile", "postgres"], {"postgres"}, "up"),
            (["down"], set(), "down"),
            (["config"], set(), "config"),
            (["-f", "docker-compose.yml", "up"], set(), "up"),
            (["--profile=postgres"], {"postgres"}, None),  # no subcommand
        ],
    )
    def test_parse_compose_args(
        self, caplog: pytest.LogCaptureFixture, args: list[str], expected_profiles: set[str], expected_subcommand: str | None
    ) -> None:
        """Verify parse_compose_args extracts profiles and subcommand correctly."""
        caplog.set_level(logging.INFO)

        profiles, subcommand = parse_compose_args(args)

        assert profiles == expected_profiles, f"Expected profiles={expected_profiles}, got={profiles}"
        assert subcommand == expected_subcommand, f"Expected subcommand={expected_subcommand}, got={subcommand}"

        logger.info("[IMP:9][test_parse_compose_args][pass] profiles=%s, subcommand=%s", profiles, subcommand)


# =============================================================================
# test_resolve_modules
# =============================================================================


# 🧪 TRAP[TEST] · 2026-07-22 · REGRESSION · resolve_modules with COMPOSE_PROFILES env
# · Last fail: N/A (new module)
# · Remove if: module resolution logic changes
class TestResolveModules:
    def test_with_profiles(self, caplog: pytest.LogCaptureFixture) -> None:
        """Profiles set explicitly are returned as-is."""
        caplog.set_level(logging.INFO)
        result = resolve_modules({"postgres", "litellm"})
        assert result == {"postgres", "litellm"}

    def test_with_env_profiles(self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        """If no explicit profiles, COMPOSE_PROFILES env var is used."""
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("COMPOSE_PROFILES", "postgres,litellm,monitoring")
        result = resolve_modules(set())
        assert result == {"postgres", "litellm", "monitoring"}

    def test_no_profiles(self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        """If no profiles anywhere, return empty set (all modules will be checked)."""
        caplog.set_level(logging.INFO)
        monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
        result = resolve_modules(set())
        assert result == set()

    def test_empty_env_profiles(self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty COMPOSE_PROFILES should result in empty set."""
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("COMPOSE_PROFILES", "")
        result = resolve_modules(set())
        assert result == set()


# =============================================================================
# test_load_env_map
# =============================================================================


# 🧪 TRAP[TEST] · 2026-07-22 · REGRESSION · load_env_map loading env files
# · Last fail: N/A (new module)
# · Remove if: env file loading logic changes
class TestLoadEnvMap:
    def test_load_existing_file(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        """Load valid secrets.env file — empty values are included."""
        caplog.set_level(logging.INFO)
        env_file = tmp_path / "secrets.env"
        env_file.write_text("POSTGRES_PASSWORD=abc123\nLITELLM_KEY=sk-test\n# comment line\nEMPTY=\n")
        result = load_env_map(str(env_file))
        assert result == {"POSTGRES_PASSWORD": "abc123", "LITELLM_KEY": "sk-test", "EMPTY": ""}
        logger.info("[IMP:9][test_load_env_map][pass] loaded %d vars", len(result))

    def test_missing_file(self, caplog: pytest.LogCaptureFixture) -> None:
        """Missing file returns empty dict."""
        caplog.set_level(logging.INFO)
        result = load_env_map("/nonexistent/secrets.env")
        assert result == {}

    def test_empty_file(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        """Empty file returns empty dict."""
        caplog.set_level(logging.INFO)
        env_file = tmp_path / "secrets.env"
        env_file.write_text("")
        result = load_env_map(str(env_file))
        assert result == {}

    def test_with_blank_lines(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        """File with blank lines should skip them."""
        caplog.set_level(logging.INFO)
        env_file = tmp_path / "secrets.env"
        env_file.write_text("\n\nPOSTGRES_PASSWORD=abc123\n\n\nLITELLM_KEY=sk-test\n")
        result = load_env_map(str(env_file))
        assert result == {"POSTGRES_PASSWORD": "abc123", "LITELLM_KEY": "sk-test"}


# =============================================================================
# test_load_manifest
# =============================================================================


# 🧪 TRAP[TEST] · 2026-07-22 · REGRESSION · load_manifest loading secrets-manifest.yaml
# · Last fail: N/A (new module)
# · Remove if: manifest loading logic changes
class TestLoadManifest:
    def test_load_valid_manifest(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path) -> None:
        """Load a valid manifest and verify secrets count."""
        caplog.set_level(logging.INFO)
        secrets = load_manifest(str(sample_manifest))
        assert secrets is not None
        assert len(secrets) == 6
        logger.info("[IMP:9][test_load_manifest][pass] loaded %d secrets", len(secrets))

    def test_missing_manifest(self, caplog: pytest.LogCaptureFixture) -> None:
        """Missing manifest returns None (graceful degradation)."""
        caplog.set_level(logging.INFO)
        result = load_manifest("/nonexistent/manifest.yaml")
        assert result is None

    def test_empty_manifest(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        """Empty manifest file returns None."""
        caplog.set_level(logging.INFO)
        manifest = tmp_path / "empty.yaml"
        manifest.write_text("")
        result = load_manifest(str(manifest))
        assert result is None


# =============================================================================
# test_check_secrets
# =============================================================================


# 🧪 TRAP[TEST] · 2026-07-22 · REGRESSION · check_secrets identifies missing required vars
# · Last fail: N/A (new module)
# · Remove if: secret checking logic changes
class TestCheckSecrets:
    def test_all_secrets_present(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When all secrets are present, missing list is empty."""
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("POSTGRES_PASSWORD", "abc")
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-key")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("LITELLM_METRICS_TOKEN", "metrics-token")

        secrets = load_manifest(str(sample_manifest))
        env_map: dict[str, str] = {}
        missing = check_secrets({"postgres", "litellm", "hermes-agent", "monitoring"}, secrets, env_map)  # type: ignore[arg-type]
        assert missing == []

    def test_missing_required(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing required secrets are reported."""
        caplog.set_level(logging.INFO)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-key")
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        secrets = load_manifest(str(sample_manifest))
        env_map: dict[str, str] = {}
        missing = check_secrets({"postgres", "litellm", "hermes-agent"}, secrets, env_map)  # type: ignore[arg-type]
        assert "POSTGRES_PASSWORD" in missing
        assert "TELEGRAM_BOT_TOKEN" in missing
        assert "LITELLM_MASTER_KEY" not in missing  # generated but present in env

    def test_skips_optional(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path) -> None:
        """Optional tier secrets are not checked."""
        caplog.set_level(logging.INFO)
        secrets = load_manifest(str(sample_manifest))
        env_map: dict[str, str] = {}
        missing = check_secrets({"litellm", "hermes-agent"}, secrets, env_map)  # type: ignore[arg-type]
        assert "DEEPSEEK_API_KEY" not in missing  # optional — skipped
        assert "API_SERVER_KEY" not in missing  # optional — skipped

    def test_env_file_fallback(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, sample_secrets_env: Path) -> None:
        """Secrets from env file are used when not in os.environ."""
        caplog.set_level(logging.INFO)
        env_map = load_env_map(str(sample_secrets_env))
        secrets = load_manifest(str(sample_manifest))
        missing = check_secrets({"postgres", "litellm", "hermes-agent"}, secrets, env_map)  # type: ignore[arg-type]
        # POSTGRES_PASSWORD and TELEGRAM_BOT_TOKEN are in the env file
        assert "POSTGRES_PASSWORD" not in missing
        assert "TELEGRAM_BOT_TOKEN" not in missing
        assert "LITELLM_MASTER_KEY" not in missing

    def test_module_filtering(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Secrets for non-target modules are not checked."""
        caplog.set_level(logging.INFO)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

        secrets = load_manifest(str(sample_manifest))
        env_map: dict[str, str] = {}
        # Only checking litellm — POSTGRES_PASSWORD is consumed by postgres+litellm
        missing = check_secrets({"litellm"}, secrets, env_map)  # type: ignore[arg-type]
        # POSTGRES_PASSWORD is consumed by litellm too, so it should be in the missing list
        # But LITELLM_METRICS_TOKEN is consumed by monitoring, not litellm
        assert "POSTGRES_PASSWORD" in missing
        assert "LITELLM_METRICS_TOKEN" not in missing  # different module


# =============================================================================
# test_validate_charsets
# =============================================================================


# 🧪 TRAP[TEST] · 2026-07-22 · REGRESSION · validate_charsets checks charset constraints
# · Last fail: N/A (new module)
# · Remove if: charset validation logic changes
class TestValidateCharsets:
    def test_all_charsets_pass(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, sample_secrets_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Secrets matching their charset regex produce no errors."""
        caplog.set_level(logging.INFO)
        # Clear env vars that might override file values
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

        env_map = load_env_map(str(sample_secrets_env))
        secrets = load_manifest(str(sample_manifest))
        errors = validate_charsets(secrets, env_map)  # type: ignore[arg-type]
        assert errors == []

    def test_charset_violation(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A secret violating its charset produces an error."""
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("POSTGRES_PASSWORD", "invalid@charset!!!")  # contains @ and !
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-ok")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abc")

        secrets = load_manifest(str(sample_manifest))
        env_map: dict[str, str] = {}
        errors = validate_charsets(secrets, env_map)  # type: ignore[arg-type]
        assert any("POSTGRES_PASSWORD" in e for e in errors)

    def test_skip_empty_values(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Secrets with empty values are skipped (caught by check_secrets)."""
        caplog.set_level(logging.INFO)
        # Clear env vars that might be set in native environment
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

        secrets = load_manifest(str(sample_manifest))
        env_map: dict[str, str] = {}
        errors = validate_charsets(secrets, env_map)  # type: ignore[arg-type]
        # POSTGRES_PASSWORD has charset but empty value — skip
        assert errors == []

    def test_skip_missing_charset(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Secrets without charset field are skipped."""
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("LITELLM_MASTER_KEY", "any-value-ok")
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        secrets = load_manifest(str(sample_manifest))
        env_map: dict[str, str] = {}
        errors = validate_charsets(secrets, env_map)  # type: ignore[arg-type]
        # LITELLM_MASTER_KEY has no charset — harmless
        assert errors == []


# =============================================================================
# test_main_cli
# =============================================================================


# 🧪 TRAP[TEST] · 2026-07-22 · REGRESSION · main() CLI dispatch
# · Last fail: N/A (new module)
# · Remove if: main() CLI logic changes
class TestMainCLI:
    def test_skip_preflight(self, caplog: pytest.LogCaptureFixture) -> None:
        """--skip-preflight bypasses all checks."""
        caplog.set_level(logging.INFO)
        exit_code = main(["--skip-preflight", "up", "--profile", "postgres"])
        assert exit_code == 0

    def test_down_subcommand_bypass(self, caplog: pytest.LogCaptureFixture) -> None:
        """Non-'up' subcommands bypass checks."""
        caplog.set_level(logging.INFO)
        exit_code = main(["down"])
        assert exit_code == 0

    def test_config_subcommand_bypass(self, caplog: pytest.LogCaptureFixture) -> None:
        """'config' subcommand bypasses checks."""
        caplog.set_level(logging.INFO)
        exit_code = main(["config"])
        assert exit_code == 0

    def test_passes_with_env(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, sample_secrets_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If all secrets are present, preflight passes."""
        caplog.set_level(logging.INFO)
        # Clear env vars that might interfere with file-based values
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        monkeypatch.setenv("SECRETS_ENV_FILE", str(sample_secrets_env))
        exit_code = main(
            [
                "--manifest",
                str(sample_manifest),
                "--secrets-env",
                str(sample_secrets_env),
                "up",
                "--profile",
                "postgres",
                "--profile",
                "litellm",
                "--profile",
                "hermes-agent",
            ]
        )
        assert exit_code == 0

    def test_blocks_with_missing_secret(self, caplog: pytest.LogCaptureFixture, sample_manifest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing secrets block the compose up."""
        caplog.set_level(logging.INFO)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

        exit_code = main(
            [
                "--manifest",
                str(sample_manifest),
                "up",
                "--profile",
                "postgres",
                "--profile",
                "litellm",
            ]
        )
        assert exit_code == 1  # blocked

    def test_missing_manifest_graceful(self, caplog: pytest.LogCaptureFixture) -> None:
        """Missing manifest produces graceful degradation (exit 0, not crash)."""
        caplog.set_level(logging.INFO)
        exit_code = main(["--manifest", "/nonexistent/manifest.yaml", "up", "--profile", "postgres"])
        assert exit_code == 0
