# GREP_SUMMARY: gate-test schema-d4 module-yaml validation jsonschema
# STRUCTURE: ▶ test_all_modules_valid_against_d4_schema → ◇ module.yaml × count → ⊕ jsonschema.validate → ◇ test_schema_allows_additional_properties → ◇ test_schema_required_fields
# region MODULE_CONTRACT
## @purpose  Gate tests: validate all module.yaml against D4 schema (DevPlan 04 TASK-G1)
## @scope    Validates module.schema.json (D4) + all module.yaml files
## @invariants
##   - Все module.yaml проходят JSON Schema валидацию против D4-схемы
##   - Schema имеет additionalProperties: true
##   - Schema требует name, install_type, description
## @rationale D4-схема — Source of Truth (core/modules/AGENTS.md §module.yaml D4 контракт)
## @changes — 2026-07-16 | Doc strings use dynamic count instead of hardcoded "12" (T7)
# endregion MODULE_CONTRACT

import json
import logging
from pathlib import Path

import jsonschema
import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

SCHEMA_PATH = repo_root() / "core" / "schemas" / "module.schema.json"
MODULES_DIR = repo_root() / "core" / "modules"


def _load_schema() -> dict:
    with Path(SCHEMA_PATH).open(encoding="utf-8") as f:
        return json.load(f)


def _discover_module_yamls() -> list[Path]:
    return sorted(MODULES_DIR.glob("*/module.yaml"))


def _load_module_yaml(path: Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _validate_module_yaml_d4(module_yaml_path: Path) -> list[str]:
    """Validate a single module.yaml against the D4 JSON Schema.

    Returns list of error messages (empty = valid).
    Used by _negative companion test to verify detection of missing fields.
    """
    errors: list[str] = []
    schema = _load_schema()
    try:
        module_data = _load_module_yaml(module_yaml_path)
        jsonschema.validate(module_data, schema)
    except yaml.YAMLError as e:
        errors.append(f"YAML parse error: {e}")
    except jsonschema.ValidationError as e:
        errors.append(f"Validation error: {e.message} (path: {list(e.absolute_path)})")
    except Exception as e:  # ruff: ignore[BLE001] — непредвиденная ошибка валидации = ошибка, а не крах
        errors.append(f"Unexpected error: {e}")
    return errors


MODULE_YAMLS = _discover_module_yamls()
SCHEMA = _load_schema()


# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · D4 module schema — все module.yaml валидны против D4-схемы
# · Last fail: N/A (preventive)
# · Remove if: D4 схема заменена или module.yaml упразднён
class TestModuleSchemaD4:
    @pytest.mark.gate
    @ldd_trajectory
    def test_all_modules_valid_against_d4_schema(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Все module.yaml проходят JSON Schema валидацию против D4-схемы (счёт динамический)."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/schema-d4 · Регресс: module.yaml перестаёт валидироваться против D4-схемы
        errors: list[str] = []
        for mod_path in MODULE_YAMLS:
            mod_name = mod_path.parent.name
            mod_errors = _validate_module_yaml_d4(mod_path)
            errors.extend(f"{mod_name}: {err}" for err in mod_errors)

        total = len(MODULE_YAMLS)
        failed = len(errors)
        passed = total - failed
        logger.info("[IMP:9][gate][schema-d4] Schema validation: %d/%d module.yaml files passed", passed, total)
        assert not errors, f"D4 schema validation failed for {len(errors)} module(s):\n" + "\n".join(errors)

    @pytest.mark.gate
    @ldd_trajectory
    def test_schema_allows_additional_properties(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Schema имеет additionalProperties: true."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/schema-d4 · Регресс: additionalProperties изменено на false (нарушение D4-контракта)
        add_props = SCHEMA.get("additionalProperties")
        logger.info("[IMP:9][gate][schema-d4] additionalProperties=%s", add_props)
        assert add_props is True, f"Expected additionalProperties: true, got: {add_props}"

    @pytest.mark.gate
    @ldd_trajectory
    def test_schema_required_fields(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Schema требует name, install_type, description."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/schema-d4 · Регресс: required поля изменены/удалены
        required = SCHEMA.get("required", [])
        logger.info("[IMP:9][gate][schema-d4] Required fields: %s", required)
        for field in ("name", "install_type", "description"):
            assert field in required, f"Required field '{field}' missing from schema.required. Got: {required}"

    @pytest.mark.gate
    @ldd_trajectory
    def test_version_not_in_required(self, caplog) -> None:  # ruff: ignore[ARG002]
        """version не должен быть в required (D4-формат)."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/schema-d4 · Регресс: version добавлен обратно в required (D3→D4 регресс)
        required = SCHEMA.get("required", [])
        logger.info("[IMP:9][gate][schema-d4] version NOT in required fields ✓")
        assert "version" not in required, f"Field 'version' should NOT be in required for D4 schema. Got: {required}"

    @pytest.mark.gate
    @ldd_trajectory
    def test_no_version_field_in_schema(self, caplog) -> None:  # ruff: ignore[ARG002]
        """D4-схема не содержит version property."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/schema-d4 · Регресс: version property добавлено обратно в D4-схему
        properties = SCHEMA.get("properties", {})
        logger.info("[IMP:9][gate][schema-d4] version NOT in schema properties ✓")
        assert "version" not in properties, "Property 'version' should NOT be in D4 schema properties"
