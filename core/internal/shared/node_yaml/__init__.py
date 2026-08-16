#!/usr/bin/env python3
# GREP_SUMMARY: node_yaml, NodeYaml, yaml-facade, lazy-load, cache, dotted-keys, config-reader, typed-api, mutation, validation, jsonschema, mixins, aggregator, 119-H
# STRUCTURE: ▶ NodeYaml(domains, projects, modules, node, validation, resolve mixins) → ◇ _load() → ◇ cache → ◇ get(key)/get_list(key) → ◇ mutation(add/remove/update) → ⎋ typed result | raise PlatformError
# region MODULE_CONTRACT
## @purpose  Unified facade for reading ai-platform node.yaml configuration (DevPlan 119 H1).
##           NodeYaml — ТОНКИЙ АГРЕГАТОР: наследует доменные миксины из node_yaml/ пакета,
##           сохраняя полный публичный API (get/get_list/typed-getters/mutation/validate/resolve).
## @scope    Single source of truth for all node.yaml consumers (~21 прямой потребитель .get()).
##           26 Python files and ~8 shell files migrate from yaml.safe_load to NodeYaml.
## @invariants
##   1. NodeYaml наследует ВСЕ доменные миксины — API агрегатора идентичен монолиту node_yaml.py
##      (verify-then-delete: потребители не меняют импорты/вызовы, AC-H1.2/AC-H3.1).
##   2. Агрегатор НЕ содержит бизнес-логики поддоменов — только ядро (_load/get/get_list/raw)
##      + наследование миксинов (AC-H1.3: <300 LOC core logic; паттерн E3 phases/).
##   3. Миксины по поддоменам схемы: Domains (domain/contexts), Projects, Modules, Node,
##      Validation, Resolve. typed-геттеры (get_secrets_config/get_firewall/
##      get_acme_dns_plugin/get_node_declaration/get_email/get_contexts/get_domain/get_repos/
##      get_tor_config/get_postgres_init_databases) НЕ пересоздаются — удалены волной 118 B3
##      (verify-then-delete: 0 потребителей; комментарий в node_yaml.py:72-77).
##   4. CLI (node_yaml/cli.py) re-export'ится лениво через PEP 562 __getattr__ (паттерн
##      DevPlan 117 G T51): `python3 -m core.internal.shared.node_yaml` работает без
##      загрузки CLI при импорте агрегатора (170 W10-B: cli.py — внутри пакета).
## @rationale DevPlan 119 H1 (AUDIT-2 M1, долг D2 09-Debt.md Rev 2026-08-02): монолит 1164 LOC
##            декомпозирован по поддоменам (паттерн E3 phases → phases/). Тонкий агрегатор
##            снижает риск регресса при изменении любого поддомена; 21 потребитель .get()
##            работает без изменений (AC-H-API).
## @changes 2026-08-03 · DevPlan 119 H1 — node_yaml.py (1164 LOC) → пакет node_yaml/:
##           __init__.py агрегатор + миксины domains/projects/modules/node/validation/resolve;
##           файл node_yaml.py удалён (несовместимость файл+пакет в одной директории —
##           пакет перекрыл бы модуль, сломав 30+ импортов; паттерн E3 phases.py → phases/)
## @changes 2026-08-01 · DevPlan 116 B6 — contexts[] canon (get_context/validate, D4/D5)
## @changes 2026-08-01 · DevPlan 116 B3 T5 (U-52) — CLI +--get-many
## @changes 2026-07-30 · DevPlan 088 — typed dataclasses (T1), resolve() (T2), jsonschema validate (T3)
## @changes 2026-07-26 · DevPlan 038a — Complete rewrite: NodeYaml class, CLI, NamedTuples
## @changes 2026-07-25 · DevPlan 070 — Created as shared module
# endregion MODULE_CONTRACT

from typing import cast

# Exceptions re-export — потребители импортируют их из node_yaml (overlay_deliverer, preflight,
# cert_collector, project_collector, scaffold_helpers, domain_verifier): `from ...node_yaml import
# ConfigNotFoundError, ...`. Сохранено из монолита (импорты не меняются, AC-H3.1).
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from core.internal.shared.node_yaml._core import NodeYamlCore
from core.internal.shared.node_yaml.domains import (
    ContextEntry,
    DomainConfig,
    DomainsMixin,
)
from core.internal.shared.node_yaml.modules import ModuleEntry, ModulesMixin
from core.internal.shared.node_yaml.node import NodeInfo, NodeMixin
from core.internal.shared.node_yaml.projects import (
    ProjectEntry,
    ProjectsMixin,
)
from core.internal.shared.node_yaml.resolve import ResolveMixin
from core.internal.shared.node_yaml.validation import ValidationMixin

__all__ = [
    "ConfigNotFoundError",
    "ConfigParseError",
    "ConfigValidationError",
    "ContextEntry",
    "DomainConfig",
    "DomainsMixin",
    "ModuleEntry",
    "ModulesMixin",
    "NodeInfo",
    "NodeMixin",
    "NodeYaml",
    "NodeYamlCore",
    "ProjectEntry",
    "ProjectsMixin",
    "ResolveMixin",
    "ValidationMixin",
]


# region CLASS_NodeYaml
class NodeYaml(
    NodeYamlCore,
    DomainsMixin,
    ProjectsMixin,
    ModulesMixin,
    NodeMixin,
    ValidationMixin,
    ResolveMixin,
):
    """Unified facade for reading ai-platform node.yaml configuration (DevPlan 119 H1).

    GREP_SUMMARY: NodeYaml, yaml-facade, lazy-load, cache, dotted-keys, config-reader, typed-api, mutation
    STRUCTURE: ▶ NodeYaml(path) → ◇ _load() → ◇ cache → ◇ get(key)/get_list(key)/... → ◇ resolve(name) → ◇ validate(schema) → ◇ mutation(add/remove/update) → ⎋ typed result | raise PlatformError

    ## @purpose  Тонкий агрегатор: ядро (NodeYamlCore: lazy-load/cache/get/get_list/raw) +
    ##            наследование доменных миксинов (Domains/Projects/Modules/Node/Validation/Resolve).
    ## @io       ⇥ path: str → ⎋ NodeYaml
    ## @complexity O(1) construction (lazy) — первый I/O на первом геттере
    ## @invariants
    ##   1. Публичный API идентичен монолиту node_yaml.py (verify-then-delete, AC-H1.2)
    ##   2. MRO: NodeYamlCore → миксины → object. Никакой бизнес-логики в агрегаторе
    ##      — только наследование (AC-H1.3, паттерн E3 phases/__init__.py).
    ##   3. Все 21 потребитель .get() работают без изменений (AC-H3.1).

    Usage:
        node = NodeYaml("/etc/platform/node.yaml")
        host = node.get("node.host", default="localhost")
        ctx = node.get_context()
        projects = node.get_projects()

    Lazy loading: file is read on first access, not in constructor.
    Cache: parsed data is cached until reload() is called.
    """

    # Ядро (NodeYamlCore) предоставляет: __init__, _load, load, reload, get, get_list, raw, _write_back.
    # Доменные миксины предоставляют: get_context/get_domain_config/add_context (DomainsMixin),
    #   get_projects/get_project/get_project_entries/add_project/remove_project/update_project
    #   (ProjectsMixin), get_modules (ModulesMixin), get_node_info (NodeMixin),
    #   validate (ValidationMixin), resolve (ResolveMixin).


# endregion CLASS_NodeYaml


# ── CLI lazy re-export (DevPlan 117 G T51 backward-compat) ────────────────────
# тесты (test_node_yaml_cli_get_many.py) импортируют _cli_get_many из этого модуля.
# Ленивый резолв через PEP 562 __getattr__ сохраняет имя импортируемым без загрузки
# CLI при импорте агрегатора (AC-G5). 170 W10-B: target — node_yaml/cli.py (внутри пакета).

# region FUNC___getattr__
_CLI_SYMBOLS = frozenset({
    "main",
    "_build_arg_parser",
    "_cli_get",
    "_cli_get_many",
    "_cli_domain_config",
    "_cli_find_project",
    "_cli_validate",
    "_cli_validate_schema",
    "_cli_resolve",
    # _cli_typed_json удалён (волна 118 B3 — typed-геттеры + --typed-* флаги удалены)
    "_traverse_dotted_list_aware",
})


def __getattr__(name: str) -> object:
    """Lazily re-export node_yaml.cli symbols for backward compatibility (PEP 562).

    ## @purpose — consumers (tests, external code) importing _cli_* / main from
    ##            node_yaml still work after the DevPlan 117 G T51 extraction. The import
    ##            happens on first attribute access — CLI is not loaded otherwise.
    ## @io — ⇥ name: str → ⎋ Any | raise AttributeError
    ## @complexity — O(1) + first-access import cost
    """
    if name in _CLI_SYMBOLS:
        import importlib as _importlib

        # 170 W10-B: node_yaml_cli (sibling) → node_yaml.cli (внутри пакета) — цикл разорван.
        module = _importlib.import_module("core.internal.shared.node_yaml.cli")
        # getattr → Any (динамический символ); object-граница для __getattr__ (W11)
        value = cast(object, getattr(module, name))
        globals()[name] = value
        return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


# endregion FUNC___getattr__
