#!/usr/bin/env python3
# GREP_SUMMARY: jsonschema-validate, generic, yaml-schema, CLI, exit-codes, validate.sh, shared-wrapper
# STRUCTURE: ▶ ┌--yaml-file + --schema-file┐ → ○ fail-fast file checks → ○ shared.validate_yaml_against_schema → ⊕ error lines → ◇ exit 0=valid / 1=errors / 2=usage|file → ⎋
# region MODULE_CONTRACT
## @purpose  Generic YAML↔JSON-Schema (draft-07) validator CLI. Тонкий wrapper над
##           core.internal.shared.schema_validator (DevPlan 116 B6 T5.2) — вся Draft7-логика
##           живёт в shared/, здесь только argparse + печать + exit-коды.
##           Strangler Tier-1 extraction of the PYOF heredoc `validate_with_python()`
##           from validate.sh (DevPlan 093 W1) — контракт AC1 (byte-identical stderr) сохранён.
## @scope    CLI consumed by core/internal/validate/validate_orchestrator.py (python validator
##           path, DevPlan 173 W1.2 — двух-хоповый фасад validate.sh → internal validate.sh
##           схлопнут); importable functions for unit tests. NOT a module.yaml D5-validator — that
##           is validate_module_yaml.py (SRP per DevPlan 093 DD1, do NOT merge).
## @invariants
##   - Exit 0 = instance valid; exit 1 = schema violations; exit 2 = usage/file/parse error
##   - Error line format byte-identical to PYOF heredoc:
##     "  Error at '<path>': <message>" (two leading spaces) — сгенерировано shared
##   - Validation errors → stderr only, stdout stays empty (byte-stability for make validate)
##   - LDD logs via logger.info — visible under caplog in tests, dropped in CLI mode
##   - Instance loaded with yaml.safe_load, schema with json.load (внутри shared)
##   - SchemaError (valid JSON, invalid schema) → exit 2; malformed YAML/JSON → exit 2
##   - Read-only: never modifies yaml/schema files
## @rationale
##   - DevPlan 116 B6 T5 (U-21): единый schema_validator в shared; jsonschema_validate —
##     thin wrapper (~40 строк логики). Устраняет дублирование валидационного цикла.
##   - Tier-1 языковая политика (AGENTS.md): inline python3 heredoc в validate.sh →
##     standalone Python CLI. validate.sh остаётся диспетчером (AC3, AC6).
## @changes
##   2026-08-01 | DevPlan 116 B6 T5 — rewritten as thin wrapper over shared/schema_validator.py
##   LAST_CHANGE: 2026-07-31 | Created (DevPlan 093 W1-T1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import ClassVar

import yaml
from jsonschema.exceptions import SchemaError

from core.internal.shared.schema_validator import validate_yaml_against_schema

logger = logging.getLogger(__name__)


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
    """CLI entry point: parse args, validate via shared, map verdict to exit code.

    ▶ ┌argv┐ → ◇ files exist? (→2) → ○ try shared.validate_yaml_against_schema → ◇ YAMLError/JSONDecodeError/SchemaError (→2) → ◇ errors? (→1, print lines) → ⎋ 0

    ## @purpose — Orchestrate CLI flow with fail-fast file checks and exit-code semantics:
    ##             0 = valid, 1 = schema violations (error lines → stderr), 2 = usage/file error.
    ##             Validation core delegated to core.internal.shared.schema_validator (DevPlan 116 B6 T5.2).
    ## @io — ⇥ argv: list[str] | None → ⎋ int exit code
    ## @complexity — O(S * I) dominated by shared.validate_yaml_against_schema
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

    class _Args(argparse.Namespace):
        """Typed argparse namespace (W11: Namespace attribute access is Any).

        ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
        """

        yaml_file: ClassVar[str]
        schema_file: ClassVar[str]

    args = parser.parse_args(argv, namespace=_Args())

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
    except SchemaError as e:
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
