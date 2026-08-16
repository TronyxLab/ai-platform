#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-validation, ValidationMixin, validate, jsonschema, schema-validator, basic-checks, 119-H
# STRUCTURE: ▶ ValidationMixin → ◇ _load() → ◇ basic checks (node/domain/contexts) → ◇ jsonschema (shared schema_validator) → ⎋ list[str] errors
# region MODULE_CONTRACT
## @purpose  Доменный миксин NodeYaml — структурная валидация node.yaml (DevPlan 119 H1).
##           validate() = basic checks (node section, flat domain, contexts[] canon) +
##           опциональный jsonschema через shared schema_validator (DevPlan 116 B6 T5).
## @scope    Миксин для NodeYaml-агрегатора (node_yaml/__init__.py). Потребители:
##           preflight, scaffold_helpers, lifecycle/helpers/validation, CLI --validate.
## @invariants
##   1. Basic checks: node section exists, node.host non-empty, domain flat-string (dict → error),
##      contexts[] canon (top-level 'context' → error, contexts[0].name non-empty).
##   2. jsonschema: ЕДИНСТВЕННАЯ точка jsonschema-валидации — shared/schema_validator (T5).
##      node_yaml/validation.py делегирует validate_dict_against_schema и НЕ содержит
##      прямого Draft7-цикла (gate test_gate_single_project_parser (c) — подстрока-скан).
##   3. Возвращает list[str] ошибок (пустой = валиден). Не бросает исключений.
## @rationale DevPlan 119 H1 (AUDIT-2 M1): валидация выделена из монолита node_yaml.py.
##            Делегирование schema_validator сохранено (single validator point, gate T5).
## @changes 2026-08-03 · DevPlan 119 H1 — извлечено из node_yaml.py (validate) в node_yaml/validation.py
##           без изменения логики; путь к node.schema.json скорректирован для глубины пакета
##           (dirname ×4 вместо ×3 — файл переехал на 1 уровень глубже)
## @changes 2026-08-01 · DevPlan 116 B6 — shared schema_validator delegation (T5)
## @changes 2026-07-30 · DevPlan 088 — validate created (T3)
# endregion MODULE_CONTRACT

import json
import logging
import os
import pathlib
from typing import Protocol, cast

logger = logging.getLogger(__name__)


class _Loadable(Protocol):
    """Протокол миксин-композиции: ValidationMixin требует _load() из NodeYamlCore
    (в агрегаторе NodeYaml порядок MRO гарантирует наличие; reportAttributeAccessIssue)."""

    def _load(self) -> dict[str, object]: ...


# region CLASS_ValidationMixin
class ValidationMixin:
    """Доменный миксин NodeYaml: валидация node.yaml (DevPlan 119 H1).

    GREP_SUMMARY: ValidationMixin, validate, jsonschema, basic-checks
    STRUCTURE: ▶ ValidationMixin → ◇ validate(schema_path) → ◇ basic → ◇ jsonschema → ⎋ list[str]
    """

    # region FUNC_validate
    ## @purpose  Validate node.yaml structure — basic checks + optional jsonschema.
    ## @io — ⇥ schema_path: Optional[str] = None → ⎋ list[str]
    ## @complexity — O(N) for YAML parse + O(S) for jsonschema validation
    ## @invariants
    ##   Basic checks: node section exists, node.host non-empty, domain section exists (flat string
    ##   only — dict form is an error), contexts[] canon ('context' field → error,
    ##   contexts must be a list with non-empty contexts[0].name).
    ##   If schema_path provided or schema exists at default path → also run Draft7 jsonschema
    ##   via shared schema_validator (DevPlan 116 B6 T5).
    ##   Returns list of error messages (empty = valid).
    def validate(self: _Loadable, schema_path: str | None = None) -> list[str]:
        """Validate node.yaml structure — basic checks + optional jsonschema.

        Args:
            schema_path: Path to JSON schema file. If None, auto-detects
                         core/schemas/node.schema.json relative to module path.

        Returns:
            List of error messages (empty list = valid)
        """
        errors: list[str] = []
        data = self._load()

        # ── Basic structural checks ──

        # Check node section
        node = data.get("node")
        if node is None:
            errors.append("Missing 'node' section")
        elif not isinstance(node, dict):
            errors.append("'node' section is not a dict")
        else:
            host = node.get("host", "")
            if not host:
                errors.append("Missing or empty 'node.host'")

        # Check domain section (flat schema only — dict form → error)
        domain = data.get("domain")
        if domain is None:
            errors.append("Missing 'domain' section")
        elif isinstance(domain, dict):
            errors.append("'domain' must be a string (flat schema)")
        elif not isinstance(domain, str):
            errors.append("'domain' section is not a string")

        # ── Context contract (invariant 3): contexts[] canon ──
        # top-level 'context' field is rejected; contexts must be a list with
        # a dict-form contexts[0].name.
        if data.get("context") is not None:
            errors.append("'context' field is rejected — use 'contexts[0].name' (invariant 3)")
        contexts = data.get("contexts")
        if contexts is None or not isinstance(contexts, list):
            errors.append("Missing 'contexts' section")
        elif not contexts:
            errors.append("Missing or empty 'contexts[0].name'")
        else:
            first = contexts[0]
            if not isinstance(first, dict) or not first.get("name"):
                errors.append("Missing or empty 'contexts[0].name'")

        # ── Optional jsonschema validation ──
        if schema_path is None:
            # Пакет node_yaml/: /core/internal/shared/node_yaml/validation.py → dirname ×4 = /core/
            # (файл на 1 уровень глубже корня core/ — dirname ×4 вместо ×3)
            # os.path.abspath/dirname — НЕ pathlib: .resolve() резолвит symlink'и (семантика расходится
            # с abspath при symlinked-путях репозитория); PTH100/PTH120 — per-file-ignore (ruff_policy.md)
            schema_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                "schemas",
                "node.schema.json",
            )

        if not os.path.isfile(schema_path):
            logger.info("[IMP:7][NodeYaml.validate] Schema not found at %s (skipping jsonschema)", schema_path)
        else:
            # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализир...
            try:
                import jsonschema

                # inline Draft7-цикл → shared schema_validator (единая точка).
                from core.internal.shared.schema_validator import validate_dict_against_schema

                with pathlib.Path(schema_path).open(encoding="utf-8") as f:
                    # json.load → Any; cast до объектной границы (W11, research-B)
                    schema: dict[str, object] = cast(dict[str, object], json.load(f))
                for msg in validate_dict_against_schema(data, schema):
                    errors.append(msg)
                    logger.error("[IMP:10][NodeYaml.validate] Schema error: %s", msg)
            except json.JSONDecodeError as e:
                errors.append(f"Schema JSON parse error: {e}")
                logger.error("[IMP:10][NodeYaml.validate] Schema JSON error: %s", e)
            except jsonschema.exceptions.SchemaError as e:  # pyright: ignore[reportAttributeAccessIssue,reportPossiblyUnboundVariable] — jsonschema опционален (ImportError-ветка ниже); SchemaError достижим только после успешного `import jsonschema`
                errors.append(f"Schema validation error: {e}")
                logger.error("[IMP:10][NodeYaml.validate] Schema error: %s", e)
            except ImportError:
                errors.append("jsonschema module not installed")
                logger.error("[IMP:10][NodeYaml.validate] jsonschema not available: schema validation skipped")

        if errors:
            logger.info("[IMP:9][NodeYaml.validate] Found %d error(s)", len(errors))
        else:
            logger.info("[IMP:9][NodeYaml.validate] Validation OK")

        return errors

    # endregion FUNC_validate


# endregion CLASS_ValidationMixin
