#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-modules, ModulesMixin, modules, get-modules, ModuleEntry, 119-H
# STRUCTURE: ▶ ModulesMixin → ◇ _load() → ◇ data.get("modules") → ◇ list-проверка → ⎋ list[dict]
# region MODULE_CONTRACT
## @purpose  Доменный миксин NodeYaml — поддомен `modules` node.yaml (DevPlan 119 H1).
##           get_modules() возвращает список модулей (пустой список если ключ отсутствует).
## @scope    Миксин для NodeYaml-агрегатора (node_yaml/__init__.py). Потребители:
##           deploy_orchestrator (enabled-модули), converge, reporting, secrets_validator.
## @invariants
##   1. Returns [] if 'modules' key missing.
##   2. Raises ConfigValidationError if 'modules' exists but is not a list.
## @rationale DevPlan 119 H1 (AUDIT-2 M1): поддомен modules выделен из монолита node_yaml.py.
##            ModuleEntry dataclass сохранён для typed-потребителей (deploy_orchestrator).
## @changes 2026-08-03 · DevPlan 119 H1 — извлечено из node_yaml.py (get_modules + ModuleEntry)
##           в node_yaml/modules.py без изменения логики
## @changes 2026-07-30 · DevPlan 088 — get_modules + ModuleEntry created
# endregion MODULE_CONTRACT

import logging
from dataclasses import dataclass

from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)


# region DATACLASS_ModuleEntry
@dataclass
class ModuleEntry:
    """Typed module entry from node.yaml modules array.

    ## @purpose  Structured representation of a module entry.
    ## @fields   name — module name (matches modules/<name>/ directory)
    ##           enabled — whether module should be deployed
    ##           config_overlay — path to node-specific config overlay
    ## @invariants  config_overlay defaults to empty string.
    """

    name: str = ""
    enabled: bool = False
    config_overlay: str = ""


# endregion DATACLASS_ModuleEntry


# region CLASS_ModulesMixin
class ModulesMixin:
    """Доменный миксин NodeYaml: поддомен modules (DevPlan 119 H1).

    GREP_SUMMARY: ModulesMixin, modules, get-modules
    STRUCTURE: ▶ ModulesMixin → ◇ get_modules() → ⎋ list[dict]
    """

    # region FUNC_get_modules
    ## @purpose  Get modules list from node.yaml.
    ## @io — ⇥ → ⎋ list[dict]
    ## @complexity — O(1) after _load()
    ## @invariants
    ##   - Returns [] if 'modules' key missing
    ##   - Raises ConfigValidationError if 'modules' exists but is not a list
    def get_modules(self) -> list[dict]:
        """Get modules list from node.yaml.

        Returns:
            List of module dicts (empty list if 'modules' key missing)

        Raises:
            ConfigValidationError: 'modules' exists but is not a list
        """
        data = self._load()
        modules = data.get("modules")
        if modules is None:
            logger.info("[IMP:7][NodeYaml] Modules: 0")
            return []
        if not isinstance(modules, list):
            logger.error("[IMP:9][NodeYaml] 'modules' is not a list: %s", type(modules))
            raise ConfigValidationError(f"'modules' is not a list: {type(modules)}")
        logger.info("[IMP:7][NodeYaml] Modules: %d", len(modules))
        return modules

    # endregion FUNC_get_modules


# endregion CLASS_ModulesMixin
