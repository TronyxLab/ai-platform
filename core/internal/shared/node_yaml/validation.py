#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-validation, ValidationMixin, validate, jsonschema, schema-validator, basic-checks, single-context-gate, 1-noda-1-kontekst, 119-H, 010-T0.3
# STRUCTURE: ▶ ValidationMixin → ◇ _load() → ◇ basic checks (node/domain/contexts) → ◇ гейт len(contexts)>1 → raise ConfigValidationError → ◇ jsonschema (shared schema_validator) → ⎋ list[str] errors
# region MODULE_CONTRACT
## @purpose  Доменный миксин NodeYaml — структурная валидация node.yaml (DevPlan 119 H1).
##           validate() = basic checks (node section, flat domain, contexts[] canon) +
##           опциональный jsonschema через shared schema_validator (DevPlan 116 B6 T5).
##           Гейт «1 нода = 1 контекст» (DevPlan 010 T0.3): len(contexts) > 1 → ConfigValidationError.
## @scope    Миксин для NodeYaml-агрегатора (node_yaml/__init__.py). Потребители:
##           preflight, scaffold_helpers, lifecycle/helpers/validation, CLI --validate.
## @invariants
##   1. Basic checks: node section exists, node.host non-empty, domain flat-string (dict → error),
##      contexts[] canon (top-level 'context' → error, contexts[0].name non-empty).
##   2. Гейт «1 нода = 1 контекст» (DevPlan 010 T0.3, §2.2 п.4): contexts — список И len(contexts) > 1
##      → ConfigValidationError (exit 4), fail-fast ДО остальных проверок контекста. Schema допускает
##      массив без maxItems, читается только contexts[0] — закрыто жёстко (schema: maxItems 1).
##   3. jsonschema: ЕДИНСТВЕННАЯ точка jsonschema-валидации — shared/schema_validator (T5).
##      node_yaml/validation.py делегирует validate_dict_against_schema и НЕ содержит
##      прямого Draft7-цикла (gate test_gate_single_project_parser (c) — подстрока-скан).
##   4. Возвращает list[str] ошибок (пустой = валиден) для накопимых ошибок; ЕДИНСТВЕННОЕ
##      исключение-raise — гейт «1 нода = 1 контекст» (invariant 2).
## @rationale DevPlan 119 H1 (AUDIT-2 M1): валидация выделена из монолита node_yaml.py.
##            Делегирование schema_validator сохранено (single validator point, gate T5).
##            DevPlan 010 T0.3: multi-context был тихим недоспецифицированным состоянием
##            (schema без maxItems, читается только contexts[0]) — гейт делает его явным fail-fast.
## @changes 2026-08-22 · DevPlan 010 T0.3 — гейт «1 нода = 1 контекст»: len(contexts) > 1 → ConfigValidationError
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

from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)


class _Loadable(Protocol):
    """Протокол миксин-композиции: ValidationMixin требует _load() из NodeYamlCore
    (в агрегаторе NodeYaml порядок MRO гарантирует наличие; reportAttributeAccessIssue)."""

    def _load(self) -> dict[str, object]: ...


# region CLASS_ValidationMixin
class ValidationMixin:
    """Доменный миксин NodeYaml: валидация node.yaml (DevPlan 119 H1).

    GREP_SUMMARY: ValidationMixin, validate, jsonschema, basic-checks, single-context-gate
    STRUCTURE: ▶ ValidationMixin → ◇ validate(schema_path) → ◇ basic → ◇ гейт 1-node-1-context (raise) → ◇ jsonschema → ⎋ list[str]
    """

    # region FUNC_validate
    ## @purpose  Validate node.yaml structure — basic checks + optional jsonschema.
    ## @io — ⇥ schema_path: Optional[str] = None → ⎋ list[str] | raise ConfigValidationError
    ## @complexity — O(N) for YAML parse + O(S) for jsonschema validation
    ## @invariants
    ##   Basic checks: node section exists, node.host non-empty, domain section exists (flat string
    ##   only — dict form is an error), contexts[] canon ('context' field → error,
    ##   contexts must be a list with non-empty contexts[0].name).
    ##   Гейт «1 нода = 1 контекст» (DevPlan 010 T0.3): len(contexts) > 1 → ConfigValidationError
    ##   (fail-fast, ДО проверок contexts[0].name).
    ##   If schema_path provided or schema exists at default path → also run Draft7 jsonschema
    ##   via shared schema_validator (DevPlan 116 B6 T5).
    ##   Returns list of error messages (empty = valid); raises ConfigValidationError on multi-context.
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
            # ── Гейт «1 нода = 1 контекст» (DevPlan 010 T0.3, §2.2 п.4) ──
            # fail-fast ДО остальных проверок контекста: schema допускает массив без maxItems
            # (schema: maxItems 1), читается только contexts[0] — закрыто жёстко здесь.
            # ⚠️ TRAP[DECISION] · 2026-08-22 · — · Single-context гейт: raise ConfigValidationError (exit 4)
            # вместо аккумуляции в errors-список · Rejected: добавить ошибку в list[str] как прочие basic-ошибки
            # · Reason: DevPlan 010 T0.3 явно требует ConfigValidationError(4) — машиночитаемый fail-fast,
            #   silent-skip multi-context был источником тихой потери contexts[1+] · Rev: если multi-context
            #   вернётся как легитимная фича — убрать гейт и maxItems из schema синхронно
            if len(contexts) > 1:
                msg = "1 нода = 1 контекст; сейчас схема допускает массив, читается только contexts[0]"
                raise ConfigValidationError(msg)
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
