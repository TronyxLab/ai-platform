"""
# GREP_SUMMARY: test_shared_schema_validator, schema-validator, validate-yaml-against-schema, validate-dict-against-schema, Draft7, SchemaError, YAMLError, shared
# STRUCTURE: ▶ tmp_path fixtures → ◇ validate_yaml_against_schema (valid/invalid) → ◇ non-dict schema root → SchemaError → ◇ broken YAML → YAMLError → ◇ empty dict → ◇ validate_dict_against_schema format → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/schema_validator.py (DevPlan 116 B6 T5) —
##           единая Draft7Validator-точка, дедупликация jsonschema_validate + node_yaml.validate.
## @scope    Tests validate_yaml_against_schema (файловый) и validate_dict_against_schema (in-memory):
##           норма valid/invalid, не-dict корень схемы → SchemaError (TRAP[BUG] 2026-07-31),
##           битый YAML → YAMLError, empty dict, формат путей " > "/" -> ".
## @invariants
##   - tmp_path fixtures (Zero Hardcode Rule)
##   - Каждый тест валидирует IMP:9 наличие через @ldd_trajectory
##   - Read-only: schema/instance файлы не модифицируются
## @rationale DevPlan 116 B6 §TEST_SPEC (T5): норма, не-dict root → SchemaError, битый YAML → YAMLError,
##            empty dict + регрессия формата node_yaml.validate (T5.3).
## @changes 2026-08-01 · DevPlan 116 B6 T5 — Created
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import jsonschema
import pytest
import yaml

from core.internal.shared.schema_validator import (
    validate_dict_against_schema,
    validate_yaml_against_schema,
)
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# ── Static minimal schema (draft-07) ─────────────────────────────────────────
_MINIMAL_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
    "additionalProperties": False,
}


# region HELPER
def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write fixture content into tmp_path and return the file path."""
    p = tmp_path / name
    p.write_text(content)
    return p


# endregion HELPER


# region TEST_VALID
@ldd_trajectory
def test_validate_yaml_valid(tmp_path, caplog) -> None:
    """Позитив: валидный YAML против валидной схемы → пустой список ошибок."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 116 B6 T5 — норма (валид)
    # · Scenario: instance со всеми required-полями → [] (valid)
    # · Last fail: N/A (new shared module)
    # · Remove if: schema_validator core changes
    logger.info("[IMP:7][test_validate_yaml_valid] START")
    import json

    yaml_f = _write(tmp_path, "instance.yaml", "name: app1\ncount: 3\n")
    schema_f = _write(tmp_path, "schema.json", json.dumps(_MINIMAL_SCHEMA))

    errors = validate_yaml_against_schema(yaml_f, schema_f)
    assert errors == [], f"valid instance must produce no errors, got {errors}"
    logger.info("[IMP:9][test_validate_yaml_valid] PASS: valid instance → 0 errors")


# endregion TEST_VALID


# region TEST_INVALID
@ldd_trajectory
def test_validate_yaml_invalid(tmp_path, caplog) -> None:
    """Негатив: недостающее required-поле → ошибка с путём '(root)'."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 116 B6 T5 — норма (невалид)
    # · Scenario: instance без required 'name' → 1 error line формата AC1
    # · Last fail: N/A (new shared module)
    # · Remove if: error-format contract changes
    logger.info("[IMP:7][test_validate_yaml_invalid] START")
    import json

    yaml_f = _write(tmp_path, "instance.yaml", "count: 3\n")
    schema_f = _write(tmp_path, "schema.json", json.dumps(_MINIMAL_SCHEMA))

    errors = validate_yaml_against_schema(yaml_f, schema_f)
    assert len(errors) == 1, f"expected 1 error, got {errors}"
    assert errors[0].startswith("  Error at '(root)': 'name' is a required property"), (
        f"AC1 error format expected, got: {errors[0]!r}"
    )
    logger.info("[IMP:9][test_validate_yaml_invalid] PASS: 1 aggregated error, AC1 format")


# endregion TEST_INVALID


# region TEST_SCHEMA_ROOT_NOT_DICT
@ldd_trajectory
def test_schema_root_not_dict_raises_schema_error(tmp_path, caplog) -> None:
    """Не-dict корень схемы (JSON array) → SchemaError (TRAP[BUG] 2026-07-31 guard)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T1 TRAP[BUG] 2026-07-31 — non-dict schema root
    # · Scenario: schema = [1,2,3] → jsonschema.exceptions.SchemaError (не AttributeError)
    # · Last fail: N/A (guard moved to shared with the core)
    # · Remove if: root-type guard changes
    logger.info("[IMP:7][test_schema_root_not_dict_raises_schema_error] START")
    yaml_f = _write(tmp_path, "instance.yaml", "name: app\n")
    schema_f = _write(tmp_path, "bad-schema.json", "[1, 2, 3]")

    with pytest.raises(jsonschema.exceptions.SchemaError):
        validate_yaml_against_schema(yaml_f, schema_f)
    logger.info("[IMP:9][test_schema_root_not_dict_raises_schema_error] PASS: SchemaError raised")


# endregion TEST_SCHEMA_ROOT_NOT_DICT


# region TEST_BROKEN_YAML
@ldd_trajectory
def test_broken_yaml_raises_yaml_error(tmp_path, caplog) -> None:
    """Битый YAML → yaml.YAMLError (не глотается)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 116 B6 T5 — exception-контракт (yaml.YAMLError)
    # · Scenario: instance не парсится → YAMLError поднимается наружу
    # · Last fail: N/A (new shared module)
    # · Remove if: exception contract changes
    logger.info("[IMP:7][test_broken_yaml_raises_yaml_error] START")
    import json

    yaml_f = _write(tmp_path, "instance.yaml", "key: [unclosed\n: : :\n")
    schema_f = _write(tmp_path, "schema.json", json.dumps(_MINIMAL_SCHEMA))

    with pytest.raises(yaml.YAMLError):
        validate_yaml_against_schema(yaml_f, schema_f)
    logger.info("[IMP:9][test_broken_yaml_raises_yaml_error] PASS: YAMLError raised")


# endregion TEST_BROKEN_YAML


# region TEST_EMPTY_DICT
@ldd_trajectory
def test_validate_empty_dict(tmp_path, caplog) -> None:
    """Empty dict instance: validate_dict_against_schema возвращает ошибки required (не падает)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 116 B6 T5 — empty dict in-memory validation
    # · Scenario: data={} против схемы с required [name] → 1 ошибка, без исключений
    # · Last fail: N/A (new shared module)
    # · Remove if: validate_dict_against_schema changes
    logger.info("[IMP:7][test_validate_empty_dict] START")
    errors = validate_dict_against_schema({}, _MINIMAL_SCHEMA)
    assert len(errors) == 1
    assert "required" in errors[0]
    logger.info("[IMP:9][test_validate_empty_dict] PASS: empty dict → 1 required error")


# endregion TEST_EMPTY_DICT


# region TEST_DICT_FORMAT
@ldd_trajectory
def test_validate_dict_path_format(tmp_path, caplog) -> None:
    """Формат путей in-memory варианта: " -> " (регрессия node_yaml.validate 766-768, T5.3)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 116 B6 T5.3 — node_yaml.validate error format preserved
    # · Scenario: вложенный тип-мисматч → путь 'node > name'-стиль c " -> " (node_yaml legacy format)
    # · Last fail: N/A (new shared module)
    # · Remove if: validate_dict_against_schema format changes
    logger.info("[IMP:7][test_validate_dict_path_format] START")
    schema = {
        "type": "object",
        "properties": {"node": {"type": "object", "properties": {"name": {"type": "string"}}}},
    }
    errors = validate_dict_against_schema({"node": {"name": 123}}, schema)
    assert any("node -> name" in e for e in errors), f"expected 'node -> name' path, got {errors}"
    logger.info("[IMP:9][test_validate_dict_path_format] PASS: ' -> ' path format preserved")


# endregion TEST_DICT_FORMAT
