#!/usr/bin/env python3
# GREP_SUMMARY: schema_validator, jsonschema, Draft7Validator, iter-errors, validate_yaml_against_schema, validate_dict_against_schema, single-entry, shared, schema-validation
# STRUCTURE: ▶ validate_yaml_against_schema(yaml_file, schema_file) → ○ yaml.safe_load + json.load → ◇ isinstance(schema, dict)? → ◇ Draft7Validator.iter_errors → ⊕ "  Error at '<path>': msg" lines → ⎋ list[str]
#            ▶ validate_dict_against_schema(data, schema) → ◇ Draft7Validator.iter_errors → ⊕ "path -> msg" lines → ⎋ list[str]
# region MODULE_CONTRACT
## @purpose  Единый schema_validator (shared) — единственная Draft7Validator-точка YAML↔JSON-Schema
##           валидации. jsonschema_validate.py и NodeYaml.validate() — тонкие обёртки над ним
##           (DevPlan 116 B6 T5, U-21: schema-валидация ×4 → 1 ядро).
## @scope    core/internal/shared/ — переиспользуемая бизнес-логика валидации схем.
##           - validate_yaml_against_schema(yaml_file, schema_file) — файловый вариант
##             (ядро CLI jsonschema_validate.py, формат AC1 byte-identical)
##           - validate_dict_against_schema(data, schema) — in-memory вариант (NodeYaml.validate)
## @invariants
##   1. Единственная Draft7Validator-инстанциация в shared/ (grep-гейт T9.2c:
##      `rg "Draft7Validator" jsonschema_validate.py node_yaml.py` → 0).
##   2. validate_yaml_against_schema: формат ошибок "  Error at '<path>': <message>"
##      (байт-идентично legacy PYOF heredoc, AC1 — " > ".join(absolute_path), "(root)" для пустого пути).
##   3. validate_dict_against_schema: формат " -> ".join(absolute_path) (как прежний node_yaml.validate 766-768).
##   4. Guard isinstance(schema, dict) ПЕРЕД Draft7Validator (TRAP[BUG] 2026-07-31 —
##      referencing._core AttributeError на не-dict корне вместо SchemaError).
##   5. Exception-контракт: yaml.YAMLError / json.JSONDecodeError / jsonschema.exceptions.SchemaError.
##   6. Read-only: никогда не модифицирует yaml/schema файлы.
## @rationale Два потребителя (jsonschema_validate.py, node_yaml.validate()) имели собственные копии
##            Draft7Validator-цикла с разными форматами ошибок. Единый модуль дедуплицирует ядро
##            и делает grep-гейт «единственная Draft7Validator» структурно выполнимым.
##            Обоснование shared-модуля (shared/AGENTS.md правило 3): дедупликация ≥2 реализаций.
## @changes  2026-08-01 · DevPlan 116 B6 T5 — Created (перенос ядра из jsonschema_validate.py:73-99)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import jsonschema
import yaml

logger = logging.getLogger(__name__)


# region FUNC__error_path
def _error_path(error: jsonschema.exceptions.ValidationError) -> str:
    """Render absolute JSON path of a validation error for the error line.

    ▶ ┌error.absolute_path┐ → ◇ non-empty? → ⊕ " > ".join(parts) → ⎋ str | "(root)"

    ## @purpose — Byte-identical path rendering to legacy PYOF heredoc (DevPlan 093 W1-T1):
    ##             " > ".join(str(p) for p in e.absolute_path), fallback "(root)".
    ## @io — ⇥ error: jsonschema ValidationError → ⎋ str path (e.g. "node > name")
    ## @complexity — O(D) where D = absolute_path depth
    ## @invariants
    ##   - Empty absolute_path (root-level violation) → "(root)"
    ##   - Non-empty → parts joined with " > " (exact separator from legacy output)
    """
    if error.absolute_path:
        return " > ".join(str(p) for p in error.absolute_path)
    return "(root)"


# endregion FUNC__error_path


# region FUNC_validate_yaml_against_schema
def validate_yaml_against_schema(yaml_file: Path, schema_file: Path) -> list[str]:
    """Validate a YAML instance against a JSON schema; return error lines.

    ▶ ┌(yaml_file, schema_file)┐ → ○ yaml.safe_load(instance) → ○ json.load(schema) → ◇ Draft7Validator → ⊕ iter_errors → ∑ error lines → ⎋ list[str]

    ## @purpose — Core validation logic (pure, importable): load instance+schema, run
    ##             jsonschema Draft7Validator.iter_errors, render error lines (format AC1).
    ## @io — ⇥ yaml_file: Path (instance), schema_file: Path (draft-07 schema)
    ##         → ⎋ list[str] error lines (empty = valid)
    ## @raises — yaml.YAMLError (malformed instance), json.JSONDecodeError (broken schema),
    ##            jsonschema.exceptions.SchemaError (invalid schema structure)
    ## @complexity — O(S * I) where S = schema size, I = instance size (draft-07 validator)
    ## @invariants
    ##   - Empty error list ⇔ instance valid (exit 0 contract)
    ##   - ALL violations aggregated via iter_errors — never first-error-only
    ##   - Error format: "  Error at '<path>': <message>" (legacy byte-identical)
    """
    with open(yaml_file) as f:
        instance: Any = yaml.safe_load(f)

    with open(schema_file) as f:
        schema = json.load(f)

    # ⚠️ TRAP[BUG] · 2026-07-31 · P2 · Non-dict schema root → AttributeError (not SchemaError)
    # · Symptom: schema file with valid JSON but non-object root (e.g. [1,2,3]) crashed with
    #   referencing._core AttributeError instead of clean exit 2.
    # · Root: Draft7Validator() delegates root-id resolution to referencing lib, which calls
    #   contents.get("$id") on the raw root — a list has no .get(). The lib raises AttributeError,
    #   bypassing the documented jsonschema.exceptions.SchemaError path.
    # · Fix: explicit fail-fast isinstance(schema, dict) check BEFORE Draft7Validator — JSON
    #   Schema spec requires an object root; a non-dict root is a malformed schema → exit 2.
    # · Prevention: keep the root-type guard ahead of validator construction.
    if not isinstance(schema, dict):
        raise jsonschema.exceptions.SchemaError("JSON schema root must be an object")

    validator = jsonschema.Draft7Validator(schema)
    return [f"  Error at '{_error_path(e)}': {e.message}" for e in validator.iter_errors(instance)]


# endregion FUNC_validate_yaml_against_schema


# region FUNC_validate_dict_against_schema
def validate_dict_against_schema(data: dict, schema: dict) -> list[str]:
    """Validate an in-memory dict against a JSON schema dict; return error lines.

    ▶ ┌(data, schema)┐ → ◇ Draft7Validator(schema) → ⊕ iter_errors(data) → ⊕ " -> ".join(path) → ⎋ list[str]

    ## @purpose — In-memory variant for NodeYaml.validate() (DevPlan 116 B6 T5.3).
    ##             Error format matches the former node_yaml.validate jsonschema block
    ##             (766-768): `" -> ".join(str(p) for p in ve.absolute_path)` / "root".
    ## @io — ⇥ data: dict (instance), schema: dict (draft-07 schema) → ⎋ list[str]
    ## @raises — jsonschema.exceptions.SchemaError (invalid schema structure)
    ## @complexity — O(S * I) where S = schema size, I = instance size
    ## @invariants
    ##   - Empty list ⇔ instance valid
    ##   - ALL violations aggregated (iter_errors)
    ##   - Path separator " -> " (node_yaml.validate format), "(root)" fallback
    """
    validator = jsonschema.Draft7Validator(schema)
    return [
        f"{' -> '.join(str(p) for p in ve.absolute_path) if ve.absolute_path else 'root'}: {ve.message}"
        for ve in validator.iter_errors(data)
    ]


# endregion FUNC_validate_dict_against_schema
