# GREP_SUMMARY: gate-test module-yaml D5 negative anti-survivorship R5 validate_module_yaml drift schema
# STRUCTURE: ▶ create broken module.yaml tmp_path → ◇ validate_module → ⊕ assert violations → ⎋ verify D5 enforcement
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship companion для test_gate_module_yaml_contract.py. Верифицирует, что D5-валидатор
##           действительно детектирует нарушения: missing type, wrong type, restart-drift,
##           backward-compat bare-string valid, missing required env var.
## @scope    Negative tests for validate_module_yaml.py D5 contract enforcement.
## @invariants
##   - Каждый тест подаёт валидатору intentionally-broken module.yaml → assert violations не empty
##   - @pytest.mark.gate на каждой test-функции
##   - Позитивные сценарии: validate_module возвращает [] (empty)
##   - Негативные сценарии: validate_module возвращает непустой список
## @rationale R5 ANTI-SURVIVORSHIP: для каждого gate-теста с bug-ID должен существовать
##             _negative companion, использующий тот же input, который вызвал баг.
##             Предотвращает ложные «все тесты зелёные — система сломана» ситуации.
## @changes   CREATED 2026-07-21 | DevPlan 033 W3-E5
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib

import pytest

from core.internal.scripts.validate_module_yaml import (
    validate_module,
)
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)
VALIDATOR_LOG = "core.internal.scripts.validate_module_yaml"

# Real schema path for full integration validation
REAL_SCHEMA = repo_root() / "core" / "schemas" / "module.schema.json"


@pytest.fixture(autouse=True)
def _setup_logger(caplog):
    """Auto-configure caplog + validator logger."""
    import logging as lg

    caplog.set_level(lg.DEBUG)
    lg.getLogger(VALIDATOR_LOG).setLevel(lg.DEBUG)
    lg.getLogger(VALIDATOR_LOG).propagate = True


def _write_module_yaml(path: pathlib.Path, content: dict) -> pathlib.Path:
    import yaml

    with open(path, "w") as f:
        yaml.dump(content, f)
    return path


def _has_violations(violations: list[str], substr: str) -> bool:
    """Check if any violation message contains the given substring."""
    return any(substr in v for v in violations)


@pytest.mark.gate
class TestD5NegativeValidateModule:
    """R5 companion: verify that D5 validator detects violations in broken module.yaml (issue: 033-wave3-contract-d5)."""

    # 🧪 TRAP[TEST] · 2026-07-21 · NEGATIVE · validate_module detects wrong env_requires type
    # · Scenario: module.yaml with env_requires type "float" (not valid enum) → violations
    def test_wrong_env_requires_type(self, tmp_path, caplog):
        """env_requires type 'float' → schema violation."""
        data = {
            "name": "neg-test",
            "install_type": "docker",
            "description": "neg",
            "env_requires": [{"name": "X", "type": "float"}],  # invalid type
        }
        path = _write_module_yaml(tmp_path / "module.yaml", data)
        # Setup minimal compose
        (tmp_path / "docker-compose.base.yml").write_text("services:\n  neg-test:\n    restart: unless-stopped\n")
        (tmp_path / ".env.example").write_text("X=value\n")
        (tmp_path / "secrets-manifest.yaml").write_text("version: 1\nsecrets: []\n")

        violations = validate_module(path, REAL_SCHEMA)
        logger.info("[IMP:9][gate][d5_negative] wrong type violations: %s", violations)
        assert _has_violations(violations, "Schema") or len(violations) > 0, (
            "Expected violations for invalid env_requires type, got empty list"
        )

    # 🧪 TRAP[TEST] · 2026-07-21 · NEGATIVE · validate_module detects missing required env var
    # · Scenario: module.yaml with required env var MISSING from .env.example → violations
    def test_missing_required_env_var(self, tmp_path, caplog):
        """Required env var NOT in .env.example → violation."""
        data = {
            "name": "neg-test",
            "install_type": "docker",
            "description": "neg",
            "env_requires": [{"name": "REQUIRED_SECRET", "type": "secret", "required": True}],
        }
        path = _write_module_yaml(tmp_path / "module.yaml", data)
        (tmp_path / "docker-compose.base.yml").write_text("services:\n  neg-test:\n    restart: unless-stopped\n")
        (tmp_path / ".env.example").write_text("OTHER=val\n")  # REQUIRED_SECRET missing
        (tmp_path / "secrets-manifest.yaml").write_text(
            "version: 1\nsecrets:\n  - name: REQUIRED_SECRET\n    tier: required\n    consumers: []\n    source: sops\n"
        )

        violations = validate_module(path, REAL_SCHEMA)
        logger.info("[IMP:9][gate][d5_negative] missing env violations: %s", violations)
        assert _has_violations(violations, "missing"), (
            f"Expected 'missing' violation for REQUIRED_SECRET, got: {violations}"
        )

    # 🧪 TRAP[TEST] · 2026-07-21 · NEGATIVE · validate_module detects restart drift
    # · Scenario: module.yaml restart: always vs compose unless-stopped → drift violation
    def test_restart_drift(self, tmp_path, caplog):
        """module.yaml restart: always vs compose unless-stopped → drift."""
        data = {
            "name": "neg-test",
            "install_type": "docker",
            "description": "neg",
            "restart": "always",  # declares always
        }
        path = _write_module_yaml(tmp_path / "module.yaml", data)
        (tmp_path / "docker-compose.base.yml").write_text(
            "services:\n  neg-test:\n    restart: unless-stopped\n"  # compose says unless-stopped
        )
        (tmp_path / ".env.example").write_text("")
        (tmp_path / "secrets-manifest.yaml").write_text("version: 1\nsecrets: []\n")

        violations = validate_module(path, REAL_SCHEMA)
        logger.info("[IMP:9][gate][d5_negative] restart drift violations: %s", violations)
        assert _has_violations(violations, "drift"), f"Expected drift violation, got: {violations}"

    # 🧪 TRAP[TEST] · 2026-07-21 · POSITIVE · D4 bare-string env_requires still valid
    # · Scenario: bare-string env_requires (D4 backward-compat) → no violations
    def test_d4_bare_string_still_valid(self, tmp_path, caplog):
        """D4 bare-string env_requires still passes D5 validator (backward-compat)."""
        data = {
            "name": "d4-valid",
            "install_type": "docker",
            "description": "D4 compat",
            "env_requires": ["TEST_VAR"],
        }
        path = _write_module_yaml(tmp_path / "module.yaml", data)
        (tmp_path / "docker-compose.base.yml").write_text("services:\n  d4-valid:\n    restart: unless-stopped\n")
        dotenv = tmp_path / ".env.example"
        dotenv.write_text("TEST_VAR=val\n")
        manifest = tmp_path / "secrets-manifest.yaml"
        manifest.write_text(
            "version: 1\nsecrets:\n  - name: TEST_VAR\n    tier: required\n    consumers: []\n    source: sops\n"
        )

        violations = validate_module(path, REAL_SCHEMA, dotenv, manifest)
        logger.info("[IMP:9][gate][d5_negative] D4 compat violations: %s", violations)
        assert not violations, f"D4 bare-string env_requires should pass, got: {violations}"
