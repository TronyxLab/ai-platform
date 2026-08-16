#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-domains, DomainsMixin, domain, contexts, get-context, get-domain-config, DomainConfig, ContextEntry, 119-H
# STRUCTURE: ▶ DomainsMixin → ◇ get_context() contexts[0].name → ◇ get_domain_config() domain/email/acme/project_domains → ◇ add_context() mutation → ⎋ str | DomainConfig | bool
# region MODULE_CONTRACT
## @purpose  Доменный миксин NodeYaml — поддомены `domain` и `contexts` node.yaml (DevPlan 119 H1).
##           Чтение: get_context() (contexts[] canon, invariant 3), get_domain_config() (flat schema).
##           Мутация: add_context() (DevPlan 116 B6 D2).
## @scope    Миксин для NodeYaml-агрегатора (node_yaml/__init__.py). Потребители: context_deployer,
##           context_overlay, deploy_orchestrator, converge/vhosts, preflight, s3_ssl_cache, scaffold.
## @invariants
##   1. get_context(): ТОЛЬКО contexts[0].name (dict-form). No raise — потребители полагаются на "".
##   2. get_domain_config(): flat schema (domain: string). Возвращает DomainConfig с defaults.
##   3. add_context(): бросает ConfigValidationError на duplicate или non-list contexts.
##      Мутирует DEEPCOPY — cache никогда не отравляется провалом записи (T6).
## @rationale DevPlan 119 H1 (AUDIT-2 M1): поддомен domain/contexts выделен из монолита
##            node_yaml.py. get_domain_config сохранён (потребитель preflight.py + CLI --domain-config);
##            геттеры get_domain()/get_contexts()/get_email() НЕ пересоздаются.
## @changes 2026-08-03 · DevPlan 119 H1 — извлечено из node_yaml.py (get_context/get_domain_config/
##           add_context + ContextEntry/DomainConfig) в node_yaml/domains.py без изменения логики
## @changes 2026-08-01 · DevPlan 116 B6 — contexts[] canon (D4/D5), add_context() (D2/T6.3)
# endregion MODULE_CONTRACT

import copy
import logging
from dataclasses import dataclass
from typing import NamedTuple, cast

from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)


# region DATACLASS_ContextEntry
@dataclass
class ContextEntry:
    """Typed context entry from node.yaml.

    ## @purpose  Structured representation of a single context in the contexts array.
    ## @fields   name — context identifier (referenced by projects[].context)
    ##           description — human-readable description
    ##           node_configs_repo — GitHub repo for node-configs (org/repo)
    ##           hermes_agent_repo — GitHub repo for hermes-agent overlay (org/repo)
    ## @invariants  All fields default to empty string on missing data.
    """

    name: str = ""
    description: str = ""
    node_configs_repo: str = ""
    hermes_agent_repo: str = ""


# endregion DATACLASS_ContextEntry


# region NAMEDTUPLE_DomainConfig
class DomainConfig(NamedTuple):
    """Typed domain configuration from node.yaml.

    ## @purpose  Structured representation of the domain section in node.yaml.
    ## @fields   platform_domain — main platform domain (e.g. example.com)
    ##           email — admin email for ACME certificates
    ##           acme_dns_plugin — DNS provider for ACME (e.g. cloudflare)
    ##           project_domains — list of project-specific domains
    """

    platform_domain: str = ""
    email: str = ""
    acme_dns_plugin: str = ""
    project_domains: list[str] = []  # ruff: ignore[RUF012] — NamedTuple, not mutable class


# endregion NAMEDTUPLE_DomainConfig


# region CLASS_DomainsMixin
class DomainsMixin:
    """Доменный миксин NodeYaml: поддомены domain + contexts (DevPlan 119 H1).

    GREP_SUMMARY: DomainsMixin, domain, contexts, get-context, get-domain-config, add-context
    STRUCTURE: ▶ DomainsMixin → ◇ get_context() → ◇ get_domain_config() → ◇ add_context() → ⎋ typed
    """

    # ── Mixin-контракт: _load/_write_back предоставляет NodeYamlCore (агрегатор) ──
    def _load(self) -> dict[str, object]:
        """Read node.yaml (реализация в NodeYamlCore — mixin живёт только в составе NodeYaml)."""
        msg = "_load provided by NodeYamlCore"
        raise NotImplementedError(msg)

    def _write_back(self, data: dict[str, object]) -> None:
        """Write node.yaml атомарно (реализация в NodeYamlCore — mixin живёт только в составе NodeYaml)."""
        msg = "_write_back provided by NodeYamlCore"
        raise NotImplementedError(msg)

    # region FUNC_get_context
    ## @purpose  Extract context name from node.yaml — contexts[] canon (invariant 3).
    ## @io — ⇥ → ⎋ str
    ## @complexity — O(1) after _load()
    ## @invariants
    ##   Priority: 1. contexts[0].name (dict-form, node.schema.json canon)  2. Empty string.
    ##   No raise — consumers (deploy_orchestrator, context_overlay, reconciler, adopter)
    ##   rely on "".
    def get_context(self) -> str:
        """Extract context name from node.yaml.

        Canon (invariant 3): only `contexts[0].name` (dict-form).

        Returns:
            Context name or ""
        """
        data = self._load()

        contexts = data.get("contexts", [])
        if contexts and isinstance(contexts, list) and len(contexts) > 0:
            first = contexts[0]
            if isinstance(first, dict):
                ctx = first.get("name", "")
                if ctx:
                    logger.info("[IMP:8][NodeYaml] Context: %s (from contexts[0].name)", ctx)
                    return str(ctx)

        logger.info("[IMP:7][NodeYaml] Context: (empty)")
        return ""

    # endregion FUNC_get_context

    # region FUNC_get_domain_config
    ## @purpose  Extract domain configuration as a typed NamedTuple.
    ## @io — ⇥ → ⎋ DomainConfig
    ## @complexity — O(P) where P = number of projects
    ## @invariants  Returns DomainConfig with defaults if keys missing.
    def get_domain_config(self) -> DomainConfig:
        """Extract domain configuration as a typed NamedTuple.

        Returns:
            DomainConfig(platform_domain, email, acme_dns_plugin, project_domains)

        Flat schema only (invariant: domain is a string):
          - platform_domain: top-level data["domain"] as str
          - email: top-level data["email"]
          - acme_dns_plugin: top-level data["acme_dns_plugin"]
          - project_domains: from projects[].domain
        """
        data = self._load()

        # ── platform_domain ──
        raw_domain = data.get("domain")
        platform_domain = raw_domain if isinstance(raw_domain, str) else ""

        # ── email ──
        email = data.get("email", "")
        if not isinstance(email, str):
            email = ""

        # ── acme_dns_plugin ──
        acme_dns_plugin = data.get("acme_dns_plugin", "")
        if not isinstance(acme_dns_plugin, str):
            acme_dns_plugin = ""

        # Collect project domains from the projects list
        project_domains: list[str] = []
        projects = data.get("projects", [])
        if isinstance(projects, list):
            # yaml-payload → str-граница: proj["domain"] из dict[Any, Any] — cast (W11)
            project_domains.extend(
                cast(str, proj["domain"]) for proj in projects if isinstance(proj, dict) and proj.get("domain")
            )

        cfg = DomainConfig(
            platform_domain=platform_domain,
            email=email,
            acme_dns_plugin=acme_dns_plugin,
            project_domains=project_domains,
        )
        logger.info("[IMP:8][NodeYaml] Domain config: %s", platform_domain)
        return cfg

    # endregion FUNC_get_domain_config

    # region FUNC_add_context
    ## @purpose  Add a context entry to node.yaml contexts[] and write back to disk (DevPlan 116 B6 D2).
    ## @io — ⇥ name: str, description: str = "", node_configs_repo: str = "",
    ##        hermes_agent_repo: str = "" → ⎋ bool
    ## @complexity — O(C) for duplicate check + O(N) for YAML dump
    ## @invariants
    ##   - Raises ConfigValidationError if context with same name already exists (like add_project).
    ##   - Missing/None 'contexts' section → created as a list.
    ##   - Entry keys limited to the 4 schema-allowed fields (node.schema.json items
    ##     additionalProperties: false): name, description, node_configs_repo, hermes_agent_repo.
    ##   - Mutates a DEEPCOPY — cache clean on write failure (DevPlan 116 B6 T6).
    def add_context(
        self,
        name: str,
        description: str = "",
        node_configs_repo: str = "",
        hermes_agent_repo: str = "",
    ) -> bool:
        """Add a context entry to node.yaml contexts[] and write back to disk.

        Args:
            name: Context name (referenced by projects[].context)
            description: Human-readable description
            node_configs_repo: GitHub repo for node-configs (org/repo)
            hermes_agent_repo: GitHub repo for hermes-agent overlay (org/repo)

        Returns:
            True on success

        Raises:
            ConfigValidationError: if context with same name already exists,
                                   or 'contexts' section is not a list
        """
        data = copy.deepcopy(self._load())
        contexts = data.get("contexts")
        if contexts is None:
            contexts = []
            data["contexts"] = contexts
        if not isinstance(contexts, list):
            logger.error("[IMP:10][NodeYaml.add_context] 'contexts' is not a list: %s", type(contexts))
            msg = f"'contexts' is not a list: {type(contexts)}"
            raise ConfigValidationError(msg)

        # Duplicate check
        for ctx in contexts:
            if isinstance(ctx, dict) and ctx.get("name") == name:
                logger.error("[IMP:10][NodeYaml.add_context] Duplicate context: %s", name)
                msg = f"Context already exists: {name}"
                raise ConfigValidationError(msg)

        new_entry: dict[str, str] = {"name": name}
        if description:
            new_entry["description"] = description
        if node_configs_repo:
            new_entry["node_configs_repo"] = node_configs_repo
        if hermes_agent_repo:
            new_entry["hermes_agent_repo"] = hermes_agent_repo

        contexts.append(new_entry)

        self._write_back(data)
        logger.info("[IMP:9][NodeYaml.add_context] Added context: %s", name)
        return True

    # endregion FUNC_add_context


# endregion CLASS_DomainsMixin
