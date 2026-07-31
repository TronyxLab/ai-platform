#!/usr/bin/env python3
# GREP_SUMMARY: jsonschema-validate, generic, Draft7Validator, iter-errors, yaml-schema, CLI, exit-codes, validate.sh
# STRUCTURE: ▶ ┌--yaml-file + --schema-file┐ → ○ yaml.safe_load(instance) ⊕ json.load(schema) → ◇ Draft7Validator.iter_errors → ⊕ "  Error at '<path>': msg" → ◇ exit 0=valid / 1=errors / 2=usage|file → ⎋
# region MODULE_CONTRACT
## @purpose  Generic YAML↔JSON-Schema (draft-07) validator CLI. Strangler Tier-1 extraction
##           of the PYOF heredoc `validate_with_python()` from validate.sh (DevPlan 093 W1).
##           Validates ANY YAML instance against ANY JSON schema — node.yaml, ai-platform.yaml,
##           module.yaml — with byte-identical error format to the original inline python3 block.
## @scope    CLI consumed by core/internal/validate/validate.sh (python validator path);
##           importable functions for unit tests. NOT a module.yaml D5-validator — that is
##           validate_module_yaml.py (SRP per DevPlan 093 DD1, do NOT merge).
## @invariants
##   - Exit 0 = instance valid; exit 1 = schema violations; exit 2 = usage/file/parse error
##   - Error line format byte-identical to legacy PYOF heredoc:
##     "  Error at '<path>': <message>" (two leading spaces), path = " > ".join(absolute_path)
##     or "(root)" for empty path — AC1 regression contract
##   - Validation errors → stderr only, stdout stays empty (byte-stability for make validate)
##   - LDD logs via logger.info — visible under caplog in tests, dropped in CLI mode
##     (no handlers → default WARNING level) so stderr carries ONLY error lines
##   - Instance loaded with yaml.safe_load (YAML natively, NOT yaml→json conversion)
##   - Schema loaded with json.load; broken schema JSON → exit 2 (AC7b)
##   - SchemaError (valid JSON, invalid schema) → exit 2
##   - Read-only: never modifies yaml/schema files
## @rationale
##   - Tier-1 языковая политика (AGENTS.md): inline python3 heredoc в validate.sh →
##     standalone Python CLI. validate.sh остаётся диспетчером (AC3, AC6).
##   - New file, НЕ расширение validate_module_yaml.py (DD1): тот модуль — D5-specific
##     (638 LOC, 3 cross-check'а); generic валидация туда = god-module + SRP violation.
##   - logger.info (не basicConfig/error): единственный способ сохранить AC1 byte-identical
##     stderr при работающих LDD [IMP:9] логах под caplog.
## @changes
##   LAST_CHANGE: 2026-07-31 | Created (DevPlan 093 W1-T1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import sys
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
    ##             jsonschema Draft7Validator.iter_errors, render error lines.
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


# region FUNC_build_arg_parser
def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    ▶ ┌none┐ → ⊕ argparse.ArgumentParser(prog, description) → ⎋ parser

    ## @purpose — CLI contract (DevPlan 093 W1-T1): --yaml-file + --schema-file.
    ## @io — ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    ## @invariants
    ##   - Both options required (argparse exits 2 with usage on missing)
    ##   - --help exits 0 without side effects
    """
    parser = argparse.ArgumentParser(
        prog="jsonschema_validate",
        description=(
            "Generic YAML↔JSON-Schema (draft-07) validator. "
            "Exit codes: 0=valid, 1=validation errors, 2=usage/file/parse error."
        ),
    )
    parser.add_argument("--yaml-file", required=True, help="Path to YAML instance file to validate")
    parser.add_argument("--schema-file", required=True, help="Path to JSON schema file (draft-07)")
    return parser


# endregion FUNC_build_arg_parser


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, validate, map verdict to exit code.

    ▶ ┌argv┐ → ◇ files exist? (→2) → ○ try validate → ◇ YAMLError/JSONDecodeError/SchemaError (→2) → ◇ errors? (→1, print lines) → ⎋ 0

    ## @purpose — Orchestrate CLI flow with fail-fast file checks and exit-code semantics:
    ##             0 = valid, 1 = schema violations (error lines → stderr), 2 = usage/file error.
    ## @io — ⇥ argv: list[str] | None → ⎋ int exit code
    ## @complexity — O(S * I) dominated by validate_yaml_against_schema
    ## @invariants
    ##   - Missing YAML file → exit 2 (fail-fast before any validation)
    ##   - Missing schema file → exit 2
    ##   - Malformed YAML (yaml.YAMLError) → exit 2 (AC7b)
    ##   - Broken schema JSON (JSONDecodeError) → exit 2 (AC7b, merge-conflict risk)
    ##   - Invalid schema structure (SchemaError) → exit 2
    ##   - Schema violations → error lines printed to stderr, exit 1
    ##   - Valid → silent (stdout AND stderr empty), exit 0 — AC1 byte-identical
    ##   - LDD verdicts at logger.info — captured by caplog in tests, silent in CLI mode
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    yaml_path = Path(args.yaml_file)
    schema_path = Path(args.schema_file)

    # Fail-fast: validate inputs BEFORE any business logic (CONSTITUTION §3)
    if not yaml_path.is_file():
        logger.info("[IMP:9][jsonschema_validate][file] ERROR: YAML file not found: %s", yaml_path)
        print(f"ERROR: YAML file not found: {yaml_path}", file=sys.stderr)
        return 2
    if not schema_path.is_file():
        logger.info("[IMP:9][jsonschema_validate][file] ERROR: Schema file not found: %s", schema_path)
        print(f"ERROR: Schema file not found: {schema_path}", file=sys.stderr)
        return 2

    try:
        errors = validate_yaml_against_schema(yaml_path, schema_path)
    except yaml.YAMLError as e:
        logger.info("[IMP:9][jsonschema_validate][parse] ERROR: malformed YAML in %s: %s", yaml_path, e)
        print(f"ERROR: malformed YAML in {yaml_path}: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        logger.info("[IMP:9][jsonschema_validate][parse] ERROR: malformed JSON schema in %s: %s", schema_path, e)
        print(f"ERROR: malformed JSON schema in {schema_path}: {e}", file=sys.stderr)
        return 2
    except jsonschema.exceptions.SchemaError as e:
        logger.info(
            "[IMP:9][jsonschema_validate][parse] ERROR: invalid JSON schema structure in %s: %s", schema_path, e
        )
        print(f"ERROR: invalid JSON schema structure in {schema_path}: {e}", file=sys.stderr)
        return 2

    if errors:
        # Validation verdict — error lines only to stderr (byte-stable output contract)
        for line in errors:
            print(line, file=sys.stderr)
        logger.info("[IMP:9][jsonschema_validate][result] INVALID: %d error(s) in %s", len(errors), yaml_path)
        return 1

    logger.info("[IMP:9][jsonschema_validate][result] VALID: %s conforms to %s", yaml_path, schema_path)
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
