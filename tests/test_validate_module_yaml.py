# GREP_SUMMARY: unit-test validate_module_yaml D5-validator module-yaml schema validation restart-drift env_requires
# STRUCTURE: ▶ tmp_path fixtures → ┌module.yaml + compose + .env.example + secrets-manifest┐ → ○ test_env_var_types → ◇ test_restart_drift → ◇ test_backward_compat → ◇ test_normalize → ◇ test_CLI
# region MODULE_CONTRACT
## @purpose  Unit-тесты для validate_module_yaml.py (DevPlan 033 W3-E1). Покрытие ≥85%.
## @scope    Каждая функция валидатора: load_module, validate_schema, check_env_requires_presence,
##           check_restart_drift, main, _normalize_env_requires_entry, _env_var_in_dotenv,
##           _env_var_in_secrets_manifest, _extract_per_service_restart.
## @invariants
##   - Все тесты используют tmp_path фикстуру (Zero Hardcode Rule)
##   - LDD trajectory: caplog.set_level(DEBUG) + IMP:9 лог в каждом success-сценарии
##   - # 🧪 TRAP[TEST] на каждом тесте
## @rationale  DevPlan 033 AC-1: coverage ≥85%, unit-тесты для каждой функции валидатора.
## @changes   CREATED 2026-07-21 | DevPlan 033 W3-E1.3
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib
from typing import Any

import pytest

from core.internal.scripts.validate_module_yaml import (
    _env_var_in_dotenv,
    _env_var_in_secrets_manifest,
    _extract_per_service_restart,
    _normalize_env_requires_entry,
    check_env_requires_presence,
    check_restart_drift,
    load_module,
    main,
    validate_module,
    validate_schema,
)

logger = logging.getLogger(__name__)
VALIDATOR_LOG = "core.internal.scripts.validate_module_yaml"


def _ldd_ok(caplog) -> bool:
    """Check caplog has IMP:9 log. Returns True if found."""
    return any("[IMP:" in r.message and int(r.message.split("[IMP:")[1].split("]")[0]) >= 9 for r in caplog.records)


# region FIXTURES


@pytest.fixture(autouse=True)
def _setup_logger(caplog):
    """Auto-configure caplog + validator logger for every test."""
    import logging as lg

    caplog.set_level(lg.DEBUG)
    lg.getLogger(VALIDATOR_LOG).setLevel(lg.DEBUG)
    lg.getLogger(VALIDATOR_LOG).propagate = True


@pytest.fixture
def tmp_module_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal module.yaml for tests."""
    yaml_path = tmp_path / "module.yaml"
    yaml_path.write_text("""\
name: test-module
install_type: docker
description: "Test module for unit tests"
depends_on: []
env_requires:
  - TEST_SECRET
  - POSTGRES_PASSWORD
""")
    return yaml_path


@pytest.fixture
def tmp_schema(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal D5 schema file for tests."""
    import json

    schema_path = tmp_path / "module.schema.json"
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["name", "install_type", "description"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "install_type": {"type": "string", "enum": ["docker", "system"]},
            "description": {"type": "string"},
            "env_requires": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "required": ["name"],
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string", "enum": ["string", "secret", "int", "bool"]},
                                "required": {"type": "boolean"},
                            },
                        },
                    ]
                },
            },
            "restart": {"type": "string", "enum": ["always", "unless-stopped", "no", "on-failure"]},
        },
        "additionalProperties": True,
    }
    schema_path.write_text(json.dumps(schema))
    return schema_path


@pytest.fixture
def tmp_compose_base(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create docker-compose.base.yml with restart: unless-stopped for tests."""
    compose_path = tmp_path / "docker-compose.base.yml"
    compose_path.write_text("""\
services:
  test-module:
    container_name: test-module
    restart: unless-stopped
    image: test-image:latest
  test-init:
    container_name: test-module-init
    restart: "no"
    image: test-init:latest
""")
    return compose_path


@pytest.fixture
def tmp_dotenv(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create .env.example with test values."""
    dotenv_path = tmp_path / ".env.example"
    dotenv_path.write_text("""\
TEST_SECRET=test-value
POSTGRES_PASSWORD=test-pg-pwd-no-prod
OPTIONAL_VAR=value
EMPTY_VAR=
# ⚠️ NOT for production — generated via SOPS
GENERATED_VAR=
""")
    return dotenv_path


@pytest.fixture
def tmp_secrets_manifest(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create secrets-manifest.yaml with test entries."""
    manifest_path = tmp_path / "secrets-manifest.yaml"
    manifest_path.write_text("""\
version: 1
secrets:
  - name: TEST_SECRET
    tier: required
    consumers: [test-module]
    source: sops
  - name: POSTGRES_PASSWORD
    tier: required
    consumers: [postgres]
    source: sops
""")
    return manifest_path


def _write_module_yaml(path: pathlib.Path, content: dict[str, Any]) -> None:
    """Helper: write dict as YAML to path."""
    import yaml

    with open(path, "w") as f:
        yaml.dump(content, f)


def _create_module(tmp: pathlib.Path, data: dict) -> pathlib.Path:
    """Create a module.yaml in tmp with given data."""
    path = tmp / "module.yaml"
    _write_module_yaml(path, data)
    return path


# endregion FIXTURES


# region UNIT_TEST_LOAD_MODULE


# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · load_module — YAML loading + env_requires normalization
# · Last fail: N/A (new test)
# · Remove if: load_module функция удалена
class TestLoadModule:
    # 🧪 TRAP[TEST] · 2026-07-21 · UNIT · load_module — bare-string env_requires → D5 objects
    def test_normalize_bare_strings(self, tmp_module_yaml, caplog):
        module = load_module(tmp_module_yaml)
        logger.info("[IMP:9][test] load_module normalize bare-strings: %s", module["env_requires"])
        assert isinstance(module["env_requires"], list)
        for entry in module["env_requires"]:
            assert isinstance(entry, dict)
            assert entry["type"] == "secret"
            assert entry["required"] is True
        assert module["env_requires"][0]["name"] == "TEST_SECRET"
        assert _ldd_ok(caplog)

    # 🧪 TRAP[TEST] · 2026-07-21 · UNIT · load_module — typed env_requires objects pass through
    def test_typed_objects_passthrough(self, tmp_path, caplog):
        data = {
            "name": "typed-mod",
            "install_type": "docker",
            "description": "test",
            "env_requires": [
                {"name": "VAR_STRING", "type": "string", "required": True},
                {"name": "VAR_INT", "type": "int", "required": False},
                {"name": "VAR_BOOL", "type": "bool", "required": True},
            ],
        }
        module = load_module(_create_module(tmp_path, data))
        logger.info("[IMP:9][test] typed objects preserved: %d entries", len(module["env_requires"]))
        assert len(module["env_requires"]) == 3
        assert module["env_requires"][0] == {"name": "VAR_STRING", "type": "string", "required": True}
        assert module["env_requires"][1] == {"name": "VAR_INT", "type": "int", "required": False}
        assert _ldd_ok(caplog)

    # 🧪 TRAP[TEST] · 2026-07-21 · NEGATIVE · load_module — malformed YAML raises ConfigValidationError (T2 миграция)
    def test_malformed_yaml_raises(self, tmp_path, caplog):
        from core.internal.shared.exceptions import ConfigValidationError

        path = tmp_path / "bad.yaml"
        path.write_text(": :broken: yaml: ]]]")
        with pytest.raises(ConfigValidationError):
            load_module(path)
        logger.info("[IMP:9][test] malformed YAML caught ✓")
        assert _ldd_ok(caplog)


# endregion UNIT_TEST_LOAD_MODULE


# region UNIT_TEST_VALIDATE_SCHEMA


# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · validate_schema — valid module + negative cases
class TestValidateSchema:
    def test_all_env_var_types_valid(self, tmp_path, tmp_schema, caplog):
        """string, secret, int, bool — all accepted by D5 schema."""
        data = {
            "name": "test",
            "install_type": "docker",
            "description": "test",
            "env_requires": [
                {"name": "S", "type": "string", "required": True},
                {"name": "K", "type": "secret", "required": True},
                {"name": "I", "type": "int", "required": False},
                {"name": "B", "type": "bool", "required": True},
            ],
        }
        module = load_module(_create_module(tmp_path, data))
        errors = validate_schema(module, tmp_schema)
        logger.info("[IMP:9][test] all types valid: errors=%s", errors)
        assert not errors
        assert _ldd_ok(caplog)

    def test_missing_required_field(self, tmp_path, tmp_schema, caplog):
        """Module without 'name' → schema violation."""
        data = {"install_type": "docker", "description": "no name"}
        errors = validate_schema(load_module(_create_module(tmp_path, data)), tmp_schema)
        logger.info("[IMP:9][test] missing field violation: %s", errors)
        assert len(errors) >= 1
        assert _ldd_ok(caplog)

    def test_bare_strings_still_valid(self, tmp_path, tmp_schema, caplog):
        """D4 backward-compat: bare-string env_requires → valid."""
        data = {"name": "test", "install_type": "docker", "description": "test", "env_requires": ["VAR1", "VAR2"]}
        module = load_module(_create_module(tmp_path, data))
        errors = validate_schema(module, tmp_schema)
        logger.info("[IMP:9][test] bare strings valid: errors=%s", errors)
        assert not errors
        assert _ldd_ok(caplog)

    def test_wrong_type_rejected(self, tmp_path, tmp_schema, caplog):
        """Invalid env_requires type → schema violation."""
        data = {
            "name": "test",
            "install_type": "docker",
            "description": "test",
            "env_requires": [{"name": "X", "type": "invalid_type"}],
        }
        module = load_module(_create_module(tmp_path, data))
        errors = validate_schema(module, tmp_schema)
        logger.info("[IMP:9][test] wrong type rejected: %s", errors)
        assert len(errors) >= 1
        assert _ldd_ok(caplog)

    def test_restart_field_accepted(self, tmp_path, tmp_schema, caplog):
        """Valid restart value → schema accepts."""
        data = {"name": "test", "install_type": "docker", "description": "test", "restart": "unless-stopped"}
        module = load_module(_create_module(tmp_path, data))
        errors = validate_schema(module, tmp_schema)
        logger.info("[IMP:9][test] restart accepted: errors=%s", errors)
        assert not errors
        assert _ldd_ok(caplog)


# endregion UNIT_TEST_VALIDATE_SCHEMA


# region UNIT_TEST_ENV_REQUIRES_PRESENCE


# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · check_env_requires_presence — required/missing/empty/optional/marker/secret-manifest
class TestEnvRequiresPresence:
    def test_required_secret_present(self, tmp_dotenv, tmp_secrets_manifest, caplog):
        """Required+secret var present in both .env.example and secrets-manifest → clean."""
        module = load_module(
            _create_module(
                tmp_dotenv.parent,
                {
                    "name": "test",
                    "install_type": "docker",
                    "description": "t",
                    "env_requires": [{"name": "TEST_SECRET", "type": "secret", "required": True}],
                },
            )
        )
        violations = check_env_requires_presence(module, tmp_dotenv, tmp_secrets_manifest)
        logger.info("[IMP:9][test] presence OK: violations=%s", violations)
        assert not violations
        assert _ldd_ok(caplog)

    def test_missing_var_violation(self, tmp_path, caplog):
        """Required var absent from .env.example → violation."""
        dotenv = tmp_path / ".env.example"
        dotenv.write_text("OTHER=value\n")
        manifest = tmp_path / "secrets.yaml"
        manifest.write_text("version: 1\nsecrets: []\n")
        module = load_module(
            _create_module(
                tmp_path,
                {
                    "name": "test",
                    "install_type": "docker",
                    "description": "t",
                    "env_requires": [{"name": "MISSING_VAR", "type": "string", "required": True}],
                },
            )
        )
        violations = check_env_requires_presence(module, dotenv, manifest)
        logger.info("[IMP:9][test] missing detected: %s", violations)
        assert len(violations) >= 1
        assert "MISSING_VAR" in violations[0]
        assert _ldd_ok(caplog)

    def test_empty_var_violation(self, tmp_dotenv, tmp_secrets_manifest, caplog):
        """Required var declared but EMPTY (no marker) → violation."""
        module = load_module(
            _create_module(
                tmp_dotenv.parent,
                {
                    "name": "test",
                    "install_type": "docker",
                    "description": "t",
                    "env_requires": [{"name": "EMPTY_VAR", "type": "string", "required": True}],
                },
            )
        )
        violations = check_env_requires_presence(module, tmp_dotenv, tmp_secrets_manifest)
        logger.info("[IMP:9][test] empty violation: %s", violations)
        assert len(violations) >= 1
        assert "EMPTY" in violations[0]
        assert _ldd_ok(caplog)

    def test_marker_bypass_generated_var(self, tmp_dotenv, caplog):
        """Empty var with '# NOT for production — generated via SOPS' → no EMPTY violation."""
        manifest = tmp_dotenv.parent / "secrets.yaml"
        manifest.write_text("version: 1\nsecrets: []\n")
        module = load_module(
            _create_module(
                tmp_dotenv.parent,
                {
                    "name": "test",
                    "install_type": "docker",
                    "description": "t",
                    "env_requires": [{"name": "GENERATED_VAR", "type": "secret", "required": True}],
                },
            )
        )
        violations = check_env_requires_presence(module, tmp_dotenv, manifest)
        logger.info("[IMP:9][test] marker bypass violations=%s", violations)
        generated_empty = [v for v in violations if "EMPTY" in v and "GENERATED_VAR" in v]
        assert not generated_empty, "GENERATED_VAR should bypass empty-check via marker"
        assert _ldd_ok(caplog)

    def test_optional_var_skipped(self, tmp_path, caplog):
        """required:false → not checked, no violation."""
        dotenv = tmp_path / ".env.example"
        dotenv.write_text("OTHER=value\n")
        manifest = tmp_path / "secrets.yaml"
        manifest.write_text("version: 1\nsecrets: []\n")
        module = load_module(
            _create_module(
                tmp_path,
                {
                    "name": "test",
                    "install_type": "docker",
                    "description": "t",
                    "env_requires": [{"name": "OPTIONAL_VAR", "type": "string", "required": False}],
                },
            )
        )
        violations = check_env_requires_presence(module, dotenv, manifest)
        logger.info("[IMP:9][test] optional skipped: violations=%s", violations)
        assert not violations
        assert _ldd_ok(caplog)

    def test_secret_not_in_manifest(self, tmp_dotenv, caplog):
        """Secret var in .env.example but NOT in secrets-manifest → violation."""
        manifest = tmp_dotenv.parent / "secrets.yaml"
        manifest.write_text("version: 1\nsecrets: []\n")
        module = load_module(
            _create_module(
                tmp_dotenv.parent,
                {
                    "name": "test",
                    "install_type": "docker",
                    "description": "t",
                    "env_requires": [{"name": "TEST_SECRET", "type": "secret", "required": True}],
                },
            )
        )
        violations = check_env_requires_presence(module, tmp_dotenv, manifest)
        logger.info("[IMP:9][test] missing from manifest: %s", violations)
        assert len(violations) >= 1
        assert "secrets-manifest" in violations[0].lower() or "not registered" in violations[0].lower()
        assert _ldd_ok(caplog)


# endregion UNIT_TEST_ENV_REQUIRES_PRESENCE


# region UNIT_TEST_RESTART_DRIFT


# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · check_restart_drift — match/mismatch/init-skip/carve-out/no-field
class TestRestartDrift:
    def test_matching_no_violation(self, tmp_compose_base, caplog):
        """module.yaml restart matches compose → clean."""
        module = {"name": "test-module", "restart": "unless-stopped"}
        violations = check_restart_drift(module, tmp_compose_base)
        logger.info("[IMP:9][test] matching restart: violations=%s", violations)
        assert not violations
        assert _ldd_ok(caplog)

    def test_mismatch_violation(self, tmp_compose_base, caplog):
        """module.yaml restart: always vs compose unless-stopped → drift."""
        module = {"name": "test-module", "restart": "always"}
        violations = check_restart_drift(module, tmp_compose_base)
        logger.info("[IMP:9][test] mismatch detected: %s", violations)
        assert len(violations) >= 1
        assert "drift" in violations[0].lower()
        assert _ldd_ok(caplog)

    def test_init_service_skipped(self, tmp_path, caplog):
        """init-service with restart: 'no' → skipped (not drift)."""
        compose = tmp_path / "docker-compose.base.yml"
        compose.write_text('services:\n  main-svc:\n    restart: unless-stopped\n  init-svc:\n    restart: "no"\n')
        module = {"name": "test-module", "restart": "unless-stopped"}
        violations = check_restart_drift(module, compose)
        logger.info("[IMP:9][test] init skipped: violations=%s", violations)
        assert not violations
        assert _ldd_ok(caplog)

    def test_critical_carve_out(self, tmp_compose_base, caplog):
        """severity:critical + restart: always vs unless-stopped → accepted (W3-R7 carve-out)."""
        module = {"name": "test-module", "restart": "always", "severity": "critical"}
        violations = check_restart_drift(module, tmp_compose_base)
        logger.info("[IMP:9][test] critical carve-out: violations=%s", violations)
        assert not violations
        assert _ldd_ok(caplog)

    def test_no_restart_skipped(self, tmp_compose_base, caplog):
        """D4 module.yaml without restart field → no drift check (backward-compat)."""
        module = {"name": "test-module"}
        violations = check_restart_drift(module, tmp_compose_base)
        logger.info("[IMP:9][test] no restart field: violations=%s", violations)
        assert not violations
        assert _ldd_ok(caplog)


# endregion UNIT_TEST_RESTART_DRIFT


# region UNIT_TEST_BACKWARD_COMPAT_D4


# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · D4 backward-compat — full pipeline
class TestBackwardCompatD4:
    def test_d4_module_passes_full_pipeline(self, tmp_path, tmp_schema, tmp_dotenv, tmp_secrets_manifest, caplog):
        """Full D4 module.yaml → validate_module returns empty violations."""
        data = {
            "name": "d4-mod",
            "install_type": "docker",
            "description": "D4 compat",
            "env_requires": ["TEST_SECRET", "POSTGRES_PASSWORD"],
        }
        path = _create_module(tmp_path, data)
        compose = tmp_path / "docker-compose.base.yml"
        compose.write_text("services:\n  d4-mod:\n    restart: unless-stopped\n")
        violations = validate_module(path, tmp_schema, tmp_dotenv, tmp_secrets_manifest)
        logger.info("[IMP:9][test] D4 full pipeline: violations=%s", violations)
        assert not violations
        assert _ldd_ok(caplog)

    def test_d4_module_missing_env_fails(self, tmp_path, tmp_schema, caplog):
        """D4 module with missing env var → violation."""
        data = {"name": "d4-mod", "install_type": "docker", "description": "D4", "env_requires": ["MISSING_VAR"]}
        path = _create_module(tmp_path, data)
        dotenv = tmp_path / ".env.example"
        dotenv.write_text("OTHER=value\n")
        manifest = tmp_path / "secrets.yaml"
        manifest.write_text("version: 1\nsecrets: []\n")
        compose = tmp_path / "docker-compose.base.yml"
        compose.write_text("services:\n  d4-mod:\n    restart: unless-stopped\n")
        violations = validate_module(path, tmp_schema, dotenv, manifest)
        logger.info("[IMP:9][test] D4 missing env detected: %s", violations)
        assert len(violations) >= 1
        assert "MISSING_VAR" in violations[0]
        assert _ldd_ok(caplog)


# endregion UNIT_TEST_BACKWARD_COMPAT_D4


# region UNIT_TEST_HELPER_FUNCTIONS


# 🧪 TRAP[TEST] · 2026-07-21 · UNIT · _normalize_env_requires_entry — all branches
class TestNormalizeHelper:
    def test_bare_string_to_object(self, caplog):
        result = _normalize_env_requires_entry("MY_VAR")
        logger.info("[IMP:9][test] bare string normalised: %s", result)
        assert result == {"name": "MY_VAR", "type": "secret", "required": True}
        assert _ldd_ok(caplog)

    def test_object_defaults_filled(self, caplog):
        result = _normalize_env_requires_entry({"name": "X"})
        logger.info("[IMP:9][test] defaults filled: %s", result)
        assert result == {"name": "X", "type": "secret", "required": True}
        assert _ldd_ok(caplog)

    def test_object_preserved(self, caplog):
        result = _normalize_env_requires_entry({"name": "Y", "type": "int", "required": False})
        logger.info("[IMP:9][test] object preserved: %s", result)
        assert result == {"name": "Y", "type": "int", "required": False}
        assert _ldd_ok(caplog)

    def test_invalid_type_raises(self, caplog):
        from core.internal.shared.exceptions import ConfigValidationError

        with pytest.raises(ConfigValidationError):
            _normalize_env_requires_entry(123)  # type: ignore[arg-type]
        logger.info("[IMP:9][test] invalid type raised ✓")
        assert _ldd_ok(caplog)

    def test_object_missing_name_raises(self, caplog):
        from core.internal.shared.exceptions import ConfigValidationError

        with pytest.raises(ConfigValidationError):
            _normalize_env_requires_entry({"type": "secret"})
        logger.info("[IMP:9][test] missing name raised ✓")
        assert _ldd_ok(caplog)


# 🧪 TRAP[TEST] · 2026-07-21 · UNIT · _env_var_in_dotenv — presence/missing/empty/marker
class TestEnvVarInDotenv:
    def test_present_non_empty(self, tmp_path, caplog):
        path = tmp_path / ".env.example"
        path.write_text("MY_VAR=my-value\nOTHER=val\n")
        present, value = _env_var_in_dotenv(path, "MY_VAR")
        logger.info("[IMP:9][test] present=%s value=%s", present, value)
        assert present is True
        assert value == "my-value"
        assert _ldd_ok(caplog)

    def test_absent(self, tmp_path, caplog):
        path = tmp_path / ".env.example"
        path.write_text("OTHER=val\n")
        present, _ = _env_var_in_dotenv(path, "MISSING")
        logger.info("[IMP:9][test] absent: present=%s", present)
        assert present is False
        assert _ldd_ok(caplog)

    def test_empty_no_marker(self, tmp_path, caplog):
        path = tmp_path / ".env.example"
        path.write_text("EMPTY=\n")
        present, value = _env_var_in_dotenv(path, "EMPTY")
        logger.info("[IMP:9][test] empty no marker: present=%s value=%r", present, value)
        assert present is True
        assert value == ""
        assert _ldd_ok(caplog)

    def test_empty_with_marker(self, tmp_path, caplog):
        path = tmp_path / ".env.example"
        path.write_text("# Генерация: openssl rand -hex 32\nGENERATED=\n")
        present, value = _env_var_in_dotenv(path, "GENERATED")
        logger.info("[IMP:9][test] empty with marker: present=%s value=%s", present, value)
        assert present is True
        assert value == "<marker:runtime-generated>"
        assert _ldd_ok(caplog)


# 🧪 TRAP[TEST] · 2026-07-21 · UNIT · _env_var_in_secrets_manifest — registration check
class TestEnvVarInSecretsManifest:
    def test_registered_found(self, tmp_secrets_manifest, caplog):
        result = _env_var_in_secrets_manifest(tmp_secrets_manifest, "TEST_SECRET")
        logger.info("[IMP:9][test] TEST_SECRET registered=%s", result)
        assert result is True
        assert _ldd_ok(caplog)

    def test_not_registered(self, tmp_secrets_manifest, caplog):
        result = _env_var_in_secrets_manifest(tmp_secrets_manifest, "UNKNOWN_VAR")
        logger.info("[IMP:9][test] UNKNOWN_VAR registered=%s", result)
        assert result is False
        assert _ldd_ok(caplog)

    def test_removed_tier_skipped(self, tmp_path, caplog):
        path = tmp_path / "secrets.yaml"
        path.write_text(
            "version: 1\nsecrets:\n  - name: OLD_VAR\n    tier: removed\n    consumers: []\n    source: sops\n"
        )
        result = _env_var_in_secrets_manifest(path, "OLD_VAR")
        logger.info("[IMP:9][test] removed tier skipped: result=%s", result)
        assert result is False
        assert _ldd_ok(caplog)


# 🧪 TRAP[TEST] · 2026-07-21 · UNIT · _extract_per_service_restart — compose parsing
class TestExtractPerServiceRestart:
    def test_extracts_restart(self, tmp_compose_base, caplog):
        result = _extract_per_service_restart(tmp_compose_base)
        logger.info("[IMP:9][test] extracted: %s", result)
        assert result == {"test-module": "unless-stopped", "test-init": "no"}
        assert _ldd_ok(caplog)

    def test_empty_for_nonexistent(self, tmp_path, caplog):
        result = _extract_per_service_restart(tmp_path / "nonexistent.yml")
        logger.info("[IMP:9][test] nonexistent: %s", result)
        assert result == {}
        assert _ldd_ok(caplog)


# endregion UNIT_TEST_HELPER_FUNCTIONS


# region UNIT_TEST_MAIN_CLI


# 🧪 TRAP[TEST] · 2026-07-21 · UNIT · main() — CLI exit codes
class TestMainCLI:
    def test_all_passes_on_valid(self, tmp_path, tmp_schema, tmp_dotenv, tmp_secrets_manifest, caplog, monkeypatch):
        """--all with valid modules → exit 0."""
        modules_dir = tmp_path / "core" / "modules"
        modules_dir.mkdir(parents=True)
        for mod_name in ["test-a", "test-b"]:
            mod_dir = modules_dir / mod_name
            mod_dir.mkdir()
            _write_module_yaml(
                mod_dir / "module.yaml",
                {
                    "name": mod_name,
                    "install_type": "docker",
                    "description": f"Mod {mod_name}",
                    "env_requires": ["TEST_SECRET"],
                },
            )
            svc_line = f"services:\n  {mod_name}:\n    restart: unless-stopped\n"
            (mod_dir / "docker-compose.base.yml").write_text(svc_line)

        monkeypatch.setattr("core.internal.scripts.validate_module_yaml.DEFAULT_SCHEMA_PATH", tmp_schema)
        monkeypatch.setattr("core.internal.scripts.validate_module_yaml.DEFAULT_MODULES_DIR", modules_dir)
        monkeypatch.setattr("core.internal.scripts.validate_module_yaml.DEFAULT_SECRETS_MANIFEST", tmp_secrets_manifest)

        rc = main(["--all", "--env-example", str(tmp_dotenv), "--secrets-manifest", str(tmp_secrets_manifest)])
        logger.info("[IMP:9][test] CLI exit code: %d", rc)
        assert rc == 0
        assert _ldd_ok(caplog)

    def test_strict_mode_detects_issues(self, tmp_path, tmp_schema, tmp_dotenv, tmp_secrets_manifest, caplog):
        """--schema-strict on module without compose → exit 1."""
        modules_dir = tmp_path / "core" / "modules" / "broken-mod"
        modules_dir.mkdir(parents=True)
        _write_module_yaml(
            modules_dir / "module.yaml",
            {
                "name": "broken-mod",
                "install_type": "docker",
                "description": "Broken",
                "env_requires": [],
            },
        )
        rc = main(
            [
                "--module",
                "broken-mod",
                "--schema-strict",
                "--env-example",
                str(tmp_dotenv),
                "--secrets-manifest",
                str(tmp_secrets_manifest),
                "--modules-dir",
                str(modules_dir.parent),
            ]
        )
        logger.info("[IMP:9][test] strict mode exit code: %d", rc)
        assert rc == 1
        assert _ldd_ok(caplog)


# endregion UNIT_TEST_MAIN_CLI
