#!/usr/bin/env python3
# GREP_SUMMARY: node_yaml, NodeYaml, yaml-facade, lazy-load, cache, dotted-keys, config-reader, extract-context, cli, typed-api, mutation, validation, jsonschema
# STRUCTURE: ▶ NodeYaml(path) → ◇ _load() → ◇ cache → ◇ get(key)/get_list(key)/... → ◇ resolve(name) → ◇ validate(schema) → ◇ mutation(add/remove/update) → ⎋ typed result | raise PlatformError
# region MODULE_CONTRACT
## @purpose  Unified facade for reading ai-platform node.yaml configuration.
##           Provides lazy-load + cache, dotted-key access, typed NamedTuples/dataclasses,
##           structural validation (basic + jsonschema), mutation API (add/remove/update project),
##           3-path node.yaml resolution, and a CLI interface for shell consumers.
## @scope    Single source of truth for all node.yaml consumers.
##           26 Python files and ~8 shell files migrate from yaml.safe_load to NodeYaml.
## @invariants
##   1. Lazy-load: __init__ does NOT read the file. First read on _load() or any getter.
##   2. Cache: parsed data is cached until reload() is called.
##   3. Dotted-key access: get("node.host") traverses nested dicts.
##   4. _load() returns {} for empty/None YAML, never None.
##   5. _load() raises ConfigNotFoundError on FileNotFoundError.
##   6. _load() raises ConfigParseError on YAMLError or non-dict root.
##   7. get(key) raises ConfigValidationError when key not found AND default is None.
##   8. get_list(key) returns [] on missing key, raises ConfigValidationError if not a list.
##   9. Context canon (invariant 3, DevPlan 116 B6): get_context() reads ONLY contexts[0].name;
##      legacy top-level 'context' and str-form contexts[0] are removed (decisions D4/D5).
##   10. resolve() searches 3 paths (config_dir/node-configs, ~/projects/*/node-configs/, /opt/node-configs/).
##   11. validate() with schema_path runs jsonschema Draft7 validation via shared schema_validator
##      against core/schemas/node.schema.json; legacy 'context' field → error.
##   12. Mutation methods (add/remove/update/add_context) write back via ruamel.yaml (if available)
##      or PyYAML on a DEEPCOPY — cache is never poisoned by a failed write (DevPlan 116 B6 T6).
##   13. Single project parser canon: all node.yaml#projects consumers delegate to
##      get_project_entries()/get_projects(); malformed record → ConfigValidationError (D3 fail-fast).
## @rationale Single facade eliminates 36+ duplicate yaml.safe_load calls (DevPlan 038a).
##   Lazy-load prevents I/O in 30% of cases where NodeYaml is created but not used (preflight).
##   Dotted-key API eliminates nested dict boilerplate (data["node"]["host"] → node.get("node.host")).
##   Typed exceptions provide fail-fast and distinct recoverable vs fatal error handling.
##   Typed dataclasses (T1) give shell consumers structured JSON output without ad-hoc parsing.
##   Mutation API (T3.5) enables project lifecycle operations from Python without subprocess make.
##   3-path resolve (T2) eliminates ad-hoc path guessing across 8 call sites.
## @changes 2026-08-01 · DevPlan 116 B6 — contexts[] canon (get_context/validate, D4/D5), deprecated
##           context-extract alias removed, get_project_entries() canon parser (T4),
##           shared schema_validator delegation (T5), _write_back deepcopy + cache invalidation (T6),
##           add_context() mutation (T6.3), flat-only domain (T7)
## @changes 2026-07-30 · DevPlan 088 — Wave 1: typed dataclasses (T1), resolve() (T2), jsonschema validate (T3),
##           mutation API + getters + CLI (T3.5), all LDD logs + region markers
## @changes 2026-07-26 · DevPlan 038a — Complete rewrite: added NodeYaml class, CLI, NamedTuples
## @changes 2026-07-25 · DevPlan 070 — Created as shared module with the deprecated context-extract alias
# endregion MODULE_CONTRACT

import argparse
import copy
import glob as glob_module
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import yaml

from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformFatalError,
)

logger = logging.getLogger(__name__)


# ── Typed Dataclasses (DevPlan 088 T1) ──────────────────────────────────────


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


# region DATACLASS_NodeDeclaration
@dataclass
class NodeDeclaration:
    """Typed node declaration from node.yaml.

    ## @purpose  Structured representation of the node section.
    ## @fields   name — node hostname label
    ##           host — IP address or FQDN
    ##           owner_key — SSH public key of node owner
    ##           ci_deploy_key — SSH public key for ci-deploy user (optional)
    ##           timezone — system timezone (default UTC)
    ## @invariants  ci_deploy_key is Optional (may be None).
    """

    name: str = ""
    host: str = ""
    owner_key: str = ""
    ci_deploy_key: str | None = None
    timezone: str = "UTC"


# endregion DATACLASS_NodeDeclaration


# region DATACLASS_FirewallConfig
@dataclass
class FirewallConfig:
    """Typed firewall configuration from node.yaml.

    ## @purpose  Structured representation of the firewall section.
    ## @fields   extra_ports — additional TCP ports to allow inbound beyond 22/80/443
    ## @invariants  Defaults to empty list.
    """

    extra_ports: list[int] = field(default_factory=list)


# endregion DATACLASS_FirewallConfig


# region DATACLASS_SecretEntry
@dataclass
class SecretEntry:
    """Typed secret entry from node.yaml secrets.required array.

    ## @purpose  Structured representation of a required secret.
    ## @fields   name — secret identifier
    ##           env_var — environment variable name
    ##           description — purpose of this secret
    ## @invariants  All fields default to empty string.
    """

    name: str = ""
    env_var: str = ""
    description: str = ""


# endregion DATACLASS_SecretEntry


# region DATACLASS_SecretsConfig
@dataclass
class SecretsConfig:
    """Typed secrets configuration from node.yaml.

    ## @purpose  Structured representation of the secrets section.
    ## @fields   enc_file — path to encrypted secrets file
    ##           required — list of required SecretEntry items
    ## @invariants  required defaults to empty list.
    """

    enc_file: str = ""
    required: list[SecretEntry] = field(default_factory=list)


# endregion DATACLASS_SecretsConfig


# region DATACLASS_TorConfig
@dataclass
class TorConfig:
    """Typed Tor + Privoxy configuration from node.yaml.

    ## @purpose  Structured representation of the tor section.
    ## @fields   enabled — enable Tor + Privoxy proxy
    ##           skip_verify — skip Tor circuit verification
    ##           bridges_file — path to obfs4 bridges file
    ## @invariants  enabled and skip_verify default to False.
    """

    enabled: bool = False
    skip_verify: bool = False
    bridges_file: str = ""


# endregion DATACLASS_TorConfig


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


# region DATACLASS_ProjectEntry
@dataclass
class ProjectEntry:
    """Typed project entry from node.yaml projects array.

    ## @purpose  Structured representation of a project entry for mutation operations.
    ## @fields   name — project name
    ##           repo — GitHub repository path (org/repo)
    ##           type — project type (frontend, backend, fullstack, agent, bot, landing)
    ##           domain — FQDN for HTTP-routable projects
    ##           database — database name for postgres projects
    ##           context — context name this project belongs to
    ## @invariants  domain, database, context default to empty string.
    """

    name: str = ""
    repo: str = ""
    type: str = ""
    domain: str = ""
    database: str = ""
    context: str = ""


# endregion DATACLASS_ProjectEntry


# region DATACLASS_ReposConfig
@dataclass
class ReposConfig:
    """Typed repos configuration from node.yaml.

    ## @purpose  Structured representation of the repos section.
    ## @fields   core — Git URL for core repository
    ##           node_configs — Git URL for node-configs repository
    ## @invariants  Both fields default to empty string.
    """

    core: str = ""
    node_configs: str = ""


# endregion DATACLASS_ReposConfig


# ── NamedTuples ──────────────────────────────────────────────────────────────


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
    project_domains: list[str] = []  # noqa: RUF012 — NamedTuple, not mutable class


class NodeInfo(NamedTuple):
    """Typed node metadata from node.yaml.

    ## @purpose  Structured representation of the node section in node.yaml.
    ## @fields   fqdn — fully qualified domain name of the node
    ##           owner_key — age key or SSH key of the node owner
    ##           docker_mirror — Docker registry mirror URL
    """

    fqdn: str = ""
    owner_key: str = ""
    docker_mirror: str = ""


# ── NodeYaml Class ───────────────────────────────────────────────────────────


class NodeYaml:
    """Unified facade for reading ai-platform node.yaml configuration.

    GREP_SUMMARY: NodeYaml, yaml-facade, lazy-load, cache, dotted-keys, config-reader, typed-api, mutation
    STRUCTURE: ▶ NodeYaml(path) → ◇ _load() → ◇ cache → ◇ get(key)/get_list(key)/... → ◇ resolve(name) → ◇ validate(schema) → ◇ mutation(add/remove/update) → ⎋ typed result | raise PlatformError

    Usage:
        node = NodeYaml("/etc/platform/node.yaml")
        host = node.get("node.host", default="localhost")
        ctx = node.get_context()
        projects = node.get_projects()

    Lazy loading: file is read on first access, not in constructor.
    Cache: parsed data is cached until reload() is called.
    """

    # region FUNC___init__
    ## @purpose  Constructor. Does NOT read the file (lazy).
    ## @io — ⇥ path: str → ⎋ None
    ## @complexity — O(1)
    ## @invariants  No I/O. _data is None until first access.
    def __init__(self, path: str) -> None:
        """Initialize NodeYaml with file path (no I/O — lazy).

        Args:
            path: Absolute path to node.yaml
        """
        self._path: str = path
        self._data: dict[str, Any] | None = None
        logger.info("[IMP:7][NodeYaml] Created NodeYaml for %s (lazy)", path)

    # endregion FUNC___init__

    # region FUNC__load
    ## @purpose  Internal. Reads and parses YAML file. Returns cached if available.
    ## @io — ⇥ open(path) → yaml.safe_load → ⎋ dict (never None)
    ## @complexity — O(N) for YAML parse
    ## @invariants
    ##   - Raises ConfigNotFoundError on FileNotFoundError
    ##   - Raises ConfigParseError on YAMLError or non-dict root
    ##   - Returns {} for empty/None YAML content
    def _load(self) -> dict:
        """Read and parse node.yaml from disk.

        Returns:
            Parsed dict (empty dict if file is empty or yaml.safe_load returns None)

        Raises:
            ConfigNotFoundError: file does not exist
            ConfigParseError: YAML syntax error or non-dict root
        """
        if self._data is not None:
            return self._data

        logger.info("[IMP:8][NodeYaml] Loading node.yaml from %s", self._path)
        try:
            with open(self._path) as f:
                raw = yaml.safe_load(f)
        except FileNotFoundError as e:
            logger.error("[IMP:9][NodeYaml] node.yaml not found: %s", self._path)
            raise ConfigNotFoundError(f"node.yaml not found: {self._path}") from e
        except yaml.YAMLError as e:
            logger.error("[IMP:9][NodeYaml] YAML parse error in %s: %s", self._path, e)
            raise ConfigParseError(f"YAML parse error in {self._path}: {e}") from e

        # Handle None/empty YAML
        if raw is None:
            self._data = {}
        elif isinstance(raw, dict):
            self._data = raw
        else:
            logger.error("[IMP:9][NodeYaml] node.yaml root is not a dict: %s", type(raw))
            raise ConfigParseError(f"node.yaml root is not a dict: {type(raw)}")

        size = os.path.getsize(self._path)
        logger.info("[IMP:8][NodeYaml] Loaded node.yaml (%d bytes)", size)
        return self._data

    # endregion FUNC__load

    # region FUNC_load
    ## @purpose  Force load (or return cached). Idempotent.
    ## @io — ⇥ → ⎋ dict
    ## @complexity — O(1) if cached, O(N) on first call
    def load(self) -> dict:
        """Force load node.yaml or return cached data.

        Returns:
            Parsed dict
        """
        return self._load()

    # endregion FUNC_load

    # region FUNC_reload
    ## @purpose  Invalidate cache and reload from disk.
    ## @io — ⇥ → ⎋ dict
    ## @complexity — O(N) for YAML parse
    def reload(self) -> dict:
        """Invalidate cache and reload node.yaml from disk.

        Use after external modification (register/deregister project).

        Returns:
            Freshly parsed dict
        """
        self._data = None
        data = self._load()
        size = os.path.getsize(self._path)
        logger.info("[IMP:8][NodeYaml] Reloaded node.yaml (%d bytes)", size)
        return data

    # endregion FUNC_reload

    # region FUNC_get
    ## @purpose  Dotted-key access to YAML value.
    ## @io — ⇥ key: str, default: Any = None → ⎋ Any | raise ConfigValidationError
    ## @complexity — O(D) where D = number of dot-separated segments
    ## @invariants
    ##   - key "node.host" traverses data["node"]["host"]
    ##   - Raises ConfigValidationError if key not found AND default is None
    ##   - Returns default if key not found AND default is not None
    ##   - Raises ConfigValidationError if intermediate node is not a dict
    def get(self, key: str, default: Any = None) -> Any:
        """Dotted-key access to YAML value.

        Args:
            key: Dotted path (e.g., "node.host", "domain.platform")
            default: Value to return if key not found.
                     If default is None AND key not found → raises ConfigValidationError.
                     Explicit default=None is treated as "no default — raise on missing".

        Returns:
            Value at key path, or default

        Raises:
            ConfigValidationError: key not found and default not provided
            ConfigValidationError: intermediate node is not a dict

        Examples:
            node.get("node.host") → "1.2.3.4"
            node.get("node.host", default="localhost") → "1.2.3.4"
            node.get("nonexistent", default="fallback") → "fallback"
            node.get("nonexistent") → raises ConfigValidationError
        """
        data = self._load()
        parts = key.split(".")
        current: Any = data

        for i, part in enumerate(parts):
            if not isinstance(current, dict):
                partial = ".".join(parts[:i])
                logger.error(
                    "[IMP:9][NodeYaml] Cannot traverse into non-dict at '%s' in key '%s': %s",
                    partial,
                    key,
                    type(current),
                )
                raise ConfigValidationError(f"Cannot traverse into non-dict at '{partial}' for key '{key}'")
            if part not in current:
                if default is not None:
                    logger.info("[IMP:7][NodeYaml] get(%s) → default=%s", key, default)
                    return default
                logger.error("[IMP:9][NodeYaml] Key not found: %s in key '%s'", part, key)
                raise ConfigValidationError(f"Key not found: {key} (missing '{part}')")
            current = current[part]

        logger.info("[IMP:7][NodeYaml] get(%s) → %s", key, type(current).__name__)
        return current

    # endregion FUNC_get

    # region FUNC_get_list
    ## @purpose  Typed list access. Guarantees return type is list.
    ## @io — ⇥ key: str → ⎋ list
    ## @complexity — O(D) for dotted-key traversal
    ## @invariants
    ##   - Returns [] if key not found
    ##   - Raises ConfigValidationError if value exists but is not a list
    def get_list(self, key: str) -> list:
        """Typed list access with guaranteed list return type.

        Args:
            key: Dotted path to a list value

        Returns:
            List value (empty list if key not found)

        Raises:
            ConfigValidationError: value exists but is not a list
        """
        data = self._load()
        parts = key.split(".")
        current: Any = data

        for i, part in enumerate(parts):
            if not isinstance(current, dict):
                partial = ".".join(parts[:i])
                logger.error(
                    "[IMP:9][NodeYaml] Cannot traverse into non-dict at '%s' in key '%s': %s",
                    partial,
                    key,
                    type(current),
                )
                raise ConfigValidationError(f"Cannot traverse into non-dict at '{partial}' for key '{key}'")
            if part not in current:
                logger.info("[IMP:7][NodeYaml] get_list(%s) → [] (missing)", key)
                return []
            current = current[part]

        if not isinstance(current, list):
            logger.error("[IMP:9][NodeYaml] '%s' is not a list: %s", key, type(current))
            raise ConfigValidationError(f"'{key}' is not a list: {type(current)}")

        logger.info("[IMP:7][NodeYaml] get_list(%s) → list[%d]", key, len(current))
        return current

    # endregion FUNC_get_list

    # region FUNC_get_context
    ## @purpose  Extract context name from node.yaml — contexts[] canon (invariant 3).
    ## @io — ⇥ → ⎋ str
    ## @complexity — O(1) after _load()
    ## @invariants
    ##   Priority: 1. contexts[0].name (dict-form, node.schema.json canon)  2. Empty string.
    ##   Legacy top-level 'context' field and str-form contexts[0] are REMOVED
    ##   (DevPlan 116 B6, decisions D4/D5). No raise — consumers (deploy_orchestrator,
    ##   context_overlay, reconciler, adopter) rely on "".
    def get_context(self) -> str:
        """Extract context name from node.yaml.

        Canon (invariant 3, DevPlan 116 B6): only `contexts[0].name` (dict-form).
        Legacy top-level 'context' field and str-form `contexts[0]` were removed.

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
                    return ctx

        logger.info("[IMP:7][NodeYaml] Context: (empty)")
        return ""

    # endregion FUNC_get_context

    # region FUNC_get_projects
    ## @purpose  Get projects list from node.yaml.
    ## @io — ⇥ → ⎋ list[dict]
    ## @complexity — O(1) after _load()
    ## @invariants
    ##   - Returns [] if 'projects' key missing
    ##   - Raises ConfigValidationError if 'projects' exists but is not a list
    def get_projects(self) -> list[dict]:
        """Get projects list from node.yaml.

        Returns:
            List of project dicts (empty list if 'projects' key missing)

        Raises:
            ConfigValidationError: 'projects' exists but is not a list
        """
        data = self._load()
        projects = data.get("projects")
        if projects is None:
            logger.info("[IMP:7][NodeYaml] Projects: 0")
            return []
        if not isinstance(projects, list):
            logger.error("[IMP:9][NodeYaml] 'projects' is not a list: %s", type(projects))
            raise ConfigValidationError(f"'projects' is not a list: {type(projects)}")
        logger.info("[IMP:7][NodeYaml] Projects: %d", len(projects))
        return projects

    # endregion FUNC_get_projects

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

    # region FUNC_get_domain_config
    ## @purpose  Extract domain configuration as a typed NamedTuple.
    ## @io — ⇥ → ⎋ DomainConfig
    ## @complexity — O(P) where P = number of projects
    ## @invariants  Returns DomainConfig with defaults if keys missing.
    def get_domain_config(self) -> DomainConfig:
        """Extract domain configuration as a typed NamedTuple.

        Returns:
            DomainConfig(platform_domain, email, acme_dns_plugin, project_domains)

        Flat schema only (invariant: domain is a string, DevPlan 116 B6 T7):
          - platform_domain: top-level data["domain"] as str
          - email: top-level data["email"]
          - acme_dns_plugin: top-level data["acme_dns_plugin"]
          - project_domains: from projects[].domain
        Legacy dict-form (domain: {platform: ...}) was removed (greenfield).
        """
        data = self._load()

        # ── platform_domain ──
        platform_domain = data.get("domain") if isinstance(data.get("domain"), str) else ""

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
            project_domains.extend(proj["domain"] for proj in projects if isinstance(proj, dict) and proj.get("domain"))

        cfg = DomainConfig(
            platform_domain=platform_domain,
            email=email,
            acme_dns_plugin=acme_dns_plugin,
            project_domains=project_domains,
        )
        logger.info("[IMP:8][NodeYaml] Domain config: %s", platform_domain)
        return cfg

    # endregion FUNC_get_domain_config

    # region FUNC_get_node_info
    ## @purpose  Extract node metadata as a typed NamedTuple.
    ## @io — ⇥ → ⎋ NodeInfo
    ## @complexity — O(1) after _load()
    ## @invariants  Returns NodeInfo with defaults if keys missing.
    def get_node_info(self) -> NodeInfo:
        """Extract node metadata as a typed NamedTuple.

        Returns:
            NodeInfo(fqdn, owner_key, docker_mirror)
        """
        data = self._load()
        node = data.get("node", {})

        if not isinstance(node, dict):
            node = {}

        info = NodeInfo(
            fqdn=node.get("fqdn", ""),
            owner_key=node.get("owner_key", ""),
            docker_mirror=node.get("docker_mirror", ""),
        )
        logger.info("[IMP:8][NodeYaml] Node info: %s", info.fqdn)
        return info

    # endregion FUNC_get_node_info

    # region FUNC_validate
    ## @purpose  Validate node.yaml structure — basic checks + optional jsonschema.
    ## @io — ⇥ schema_path: Optional[str] = None → ⎋ list[str]
    ## @complexity — O(N) for YAML parse + O(S) for jsonschema validation
    ## @invariants
    ##   Basic checks: node section exists, node.host non-empty, domain section exists (flat string
    ##   only — dict form is an error), contexts[] canon (legacy 'context' field → error,
    ##   contexts must be a list with non-empty contexts[0].name).
    ##   If schema_path provided or schema exists at default path → also run Draft7 jsonschema
    ##   via shared schema_validator (DevPlan 116 B6 T5).
    ##   Returns list of error messages (empty = valid).
    def validate(self, schema_path: str | None = None) -> list[str]:
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

        # Check domain section (flat schema only — legacy dict form removed, DevPlan 116 B6 T7)
        domain = data.get("domain")
        if domain is None:
            errors.append("Missing 'domain' section")
        elif isinstance(domain, dict):
            errors.append("'domain' must be a string (flat schema — legacy dict form removed)")
        elif not isinstance(domain, str):
            errors.append("'domain' section is not a string")

        # ── Context contract (invariant 3, DevPlan 116 B6 T1): contexts[] canon ──
        # Legacy top-level 'context' field is rejected; contexts must be a list with
        # a dict-form contexts[0].name (node.schema.json canon, decisions D4/D5).
        if data.get("context") is not None:
            errors.append("Legacy 'context' field is removed — use 'contexts[0].name' (invariant 3)")
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
            schema_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "schemas",
                "node.schema.json",
            )

        if not os.path.isfile(schema_path):
            logger.info("[IMP:7][NodeYaml.validate] Schema not found at %s (skipping jsonschema)", schema_path)
        else:
            try:
                import jsonschema

                # DevPlan 116 B6 T5: inline Draft7-цикл → shared schema_validator (single entry).
                from core.internal.shared.schema_validator import validate_dict_against_schema

                with open(schema_path) as f:
                    schema = json.load(f)
                for msg in validate_dict_against_schema(data, schema):
                    errors.append(msg)
                    logger.error("[IMP:10][NodeYaml.validate] Schema error: %s", msg)
            except json.JSONDecodeError as e:
                errors.append(f"Schema JSON parse error: {e}")
                logger.error("[IMP:10][NodeYaml.validate] Schema JSON error: %s", e)
            except jsonschema.exceptions.SchemaError as e:
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

    # region FUNC_resolve
    ## @purpose  Resolve node.yaml via 3-path search and return loaded NodeYaml instance.
    ## @io — ⇥ node_name: Optional[str], config_dir: Optional[str] → ⎋ NodeYaml
    ## @complexity — O(P) where P = number of glob candidates
    ## @invariants
    ##   Searches 3 paths in order:
    ##     1. {platform_root}/node-configs/{node_name}/node.yaml
    ##     2. $HOME/projects/*/node-configs/{node_name}/node.yaml (glob)
    ##     3. /opt/node-configs/{node_name}/node.yaml
    ##   Raises ConfigNotFoundError if not found in any path.
    @classmethod
    def resolve(cls, node_name: str | None = None, config_dir: str | None = None) -> "NodeYaml":
        """Resolve node.yaml via 3-path search and return loaded NodeYaml instance.

        Searches 3 paths in order:
          1. {platform_root}/node-configs/{node_name}/node.yaml
          2. $HOME/projects/*/node-configs/{node_name}/node.yaml (glob)
          3. /opt/node-configs/{node_name}/node.yaml

        Args:
            node_name: Node name. If None, tries from env NODE_NAME, then hostname.
            config_dir: Base config directory. If None, tries PLATFORM_ROOT env, then /opt/platform.

        Returns:
            Loaded NodeYaml instance

        Raises:
            ConfigNotFoundError: if node.yaml not found in any path
        """
        if node_name is None:
            node_name = os.environ.get("NODE_NAME", "")
        if not node_name:
            import socket

            node_name = socket.gethostname()

        if config_dir is None:
            config_dir = os.environ.get("PLATFORM_ROOT", "/opt/platform")

        logger.info("[IMP:8][NodeYaml.resolve] Resolving node.yaml for node=%s", node_name)

        # Path 1: platform_root/node-configs/{node_name}/node.yaml
        candidates: list[str] = [
            os.path.join(config_dir, "node-configs", node_name, "node.yaml"),
        ]

        # Path 2: ~/projects/*/node-configs/{node_name}/node.yaml (glob)
        projects_dir = os.path.expanduser("~/projects")
        candidates.extend(
            sorted(glob_module.glob(os.path.join(projects_dir, "*", "node-configs", node_name, "node.yaml")))
        )

        # Path 3: /opt/node-configs/{node_name}/node.yaml
        candidates.append(f"/opt/node-configs/{node_name}/node.yaml")

        for p in candidates:
            if os.path.isfile(p):
                logger.info("[IMP:9][NodeYaml.resolve] Found: %s", p)
                return cls(p)

        searched = ", ".join(candidates)
        logger.error("[IMP:10][NodeYaml.resolve] Not found for node=%s (searched: %s)", node_name, searched)
        raise ConfigNotFoundError(f"node.yaml not found for node={node_name}")

    # endregion FUNC_resolve

    # ── New Typed Getters (DevPlan 088 T3.5) ──────────────────────────────────

    # region FUNC_get_contexts
    ## @purpose  Get contexts list from node.yaml as raw dicts.
    ## @io — ⇥ → ⎋ list[dict]
    ## @complexity — O(1) after _load()
    def get_contexts(self) -> list[dict]:
        """Get contexts list from node.yaml.

        Returns:
            List of context dicts (empty list if 'contexts' key missing or not a list)
        """
        data = self._load()
        contexts = data.get("contexts", [])
        if not isinstance(contexts, list):
            logger.info("[IMP:7][NodeYaml.get_contexts] 'contexts' is not a list, returning []")
            return []
        logger.info("[IMP:7][NodeYaml.get_contexts] %d context(s)", len(contexts))
        return contexts

    # endregion FUNC_get_contexts

    # region FUNC_get_firewall
    ## @purpose  Get firewall configuration as typed FirewallConfig.
    ## @io — ⇥ → ⎋ FirewallConfig
    ## @complexity — O(1) after _load()
    def get_firewall(self) -> FirewallConfig:
        """Get firewall configuration from node.yaml.

        Returns:
            FirewallConfig with extra_ports list
        """
        data = self._load()
        fw = data.get("firewall", {})
        if not isinstance(fw, dict):
            logger.info("[IMP:7][NodeYaml.get_firewall] 'firewall' not a dict, returning defaults")
            return FirewallConfig()
        cfg = FirewallConfig(
            extra_ports=fw.get("extra_ports", []),
        )
        logger.info("[IMP:7][NodeYaml.get_firewall] %d extra port(s)", len(cfg.extra_ports))
        return cfg

    # endregion FUNC_get_firewall

    # region FUNC_get_secrets_config
    ## @purpose  Get secrets configuration as typed SecretsConfig.
    ## @io — ⇥ → ⎋ SecretsConfig
    ## @complexity — O(K) where K = number of required secret entries
    def get_secrets_config(self) -> SecretsConfig:
        """Get secrets configuration from node.yaml.

        Returns:
            SecretsConfig with enc_file and required list
        """
        data = self._load()
        sc = data.get("secrets", {})
        if not isinstance(sc, dict):
            logger.info("[IMP:7][NodeYaml.get_secrets_config] 'secrets' not a dict, returning defaults")
            return SecretsConfig()
        required: list[SecretEntry] = [
            SecretEntry(
                name=entry.get("name", ""),
                env_var=entry.get("env_var", ""),
                description=entry.get("description", ""),
            )
            for entry in sc.get("required", [])
            if isinstance(entry, dict)
        ]
        cfg = SecretsConfig(
            enc_file=sc.get("enc_file", ""),
            required=required,
        )
        logger.info(
            "[IMP:7][NodeYaml.get_secrets_config] enc_file=%s, %d required secret(s)", cfg.enc_file, len(cfg.required)
        )
        return cfg

    # endregion FUNC_get_secrets_config

    # region FUNC_get_tor_config
    ## @purpose  Get Tor configuration as typed TorConfig.
    ## @io — ⇥ → ⎋ TorConfig
    ## @complexity — O(1) after _load()
    def get_tor_config(self) -> TorConfig:
        """Get Tor configuration from node.yaml.

        Returns:
            TorConfig with enabled, skip_verify, bridges_file
        """
        data = self._load()
        tc = data.get("tor", {})
        if not isinstance(tc, dict):
            logger.info("[IMP:7][NodeYaml.get_tor_config] 'tor' not a dict, returning defaults")
            return TorConfig()
        cfg = TorConfig(
            enabled=bool(tc.get("enabled", False)),
            skip_verify=bool(tc.get("skip_verify", False)),
            bridges_file=tc.get("bridges_file", ""),
        )
        logger.info("[IMP:7][NodeYaml.get_tor_config] enabled=%s, skip_verify=%s", cfg.enabled, cfg.skip_verify)
        return cfg

    # endregion FUNC_get_tor_config

    # region FUNC_get_repos
    ## @purpose  Get repos configuration as typed ReposConfig.
    ## @io — ⇥ → ⎋ ReposConfig
    ## @complexity — O(1) after _load()
    def get_repos(self) -> ReposConfig:
        """Get repos configuration from node.yaml.

        Returns:
            ReposConfig with core and node_configs URLs
        """
        data = self._load()
        rc = data.get("repos", {})
        if not isinstance(rc, dict):
            logger.info("[IMP:7][NodeYaml.get_repos] 'repos' not a dict, returning defaults")
            return ReposConfig()
        cfg = ReposConfig(
            core=rc.get("core", ""),
            node_configs=rc.get("node_configs", ""),
        )
        logger.info("[IMP:7][NodeYaml.get_repos] core=%s", cfg.core)
        return cfg

    # endregion FUNC_get_repos

    # region FUNC_get_postgres_init_databases
    ## @purpose  Get postgres_init_databases list from node.yaml.
    ## @io — ⇥ → ⎋ list[str]
    ## @complexity — O(1) after _load()
    def get_postgres_init_databases(self) -> list[str]:
        """Get postgres_init_databases list from node.yaml.

        Returns:
            List of database names (empty list if key missing or not a list)
        """
        data = self._load()
        dbs = data.get("postgres_init_databases", [])
        if not isinstance(dbs, list):
            logger.info("[IMP:7][NodeYaml.get_postgres_init_databases] not a list, returning []")
            return []
        logger.info("[IMP:7][NodeYaml.get_postgres_init_databases] %d database(s)", len(dbs))
        return dbs

    # endregion FUNC_get_postgres_init_databases

    # region FUNC_get_node_declaration
    ## @purpose  Get node declaration as typed NodeDeclaration dataclass.
    ## @io — ⇥ → ⎋ NodeDeclaration
    ## @complexity — O(1) after _load()
    def get_node_declaration(self) -> NodeDeclaration:
        """Get node declaration as a typed dataclass.

        Returns:
            NodeDeclaration with name, host, owner_key, ci_deploy_key, timezone
        """
        data = self._load()
        node = data.get("node", {})
        if not isinstance(node, dict):
            logger.info("[IMP:7][NodeYaml.get_node_declaration] 'node' not a dict, returning defaults")
            return NodeDeclaration()
        nd = NodeDeclaration(
            name=node.get("name", ""),
            host=node.get("host", ""),
            owner_key=node.get("owner_key", ""),
            ci_deploy_key=node.get("ci_deploy_key"),
            timezone=node.get("timezone", "UTC"),
        )
        logger.info("[IMP:7][NodeYaml.get_node_declaration] name=%s, host=%s", nd.name, nd.host)
        return nd

    # endregion FUNC_get_node_declaration

    # region FUNC_get_acme_dns_plugin
    ## @purpose  Get acme_dns_plugin field from node.yaml.
    ## @io — ⇥ → ⎋ str
    ## @complexity — O(1) after _load()
    def get_acme_dns_plugin(self) -> str:
        """Get acme_dns_plugin from node.yaml.

        Returns:
            ACME DNS plugin name or empty string
        """
        data = self._load()
        val = data.get("acme_dns_plugin", "")
        if not isinstance(val, str):
            return ""
        logger.info("[IMP:7][NodeYaml.get_acme_dns_plugin] %s", val)
        return val

    # endregion FUNC_get_acme_dns_plugin

    # region FUNC_get_email
    ## @purpose  Get email field from node.yaml.
    ## @io — ⇥ → ⎋ str
    ## @complexity — O(1) after _load()
    def get_email(self) -> str:
        """Get email from node.yaml.

        Returns:
            Email string or empty string
        """
        data = self._load()
        val = data.get("email", "")
        if not isinstance(val, str):
            return ""
        logger.info("[IMP:7][NodeYaml.get_email] %s", val if val else "(empty)")
        return val

    # endregion FUNC_get_email

    # region FUNC_get_domain
    ## @purpose  Get domain field from node.yaml (flat string only).
    ## @io — ⇥ → ⎋ str
    ## @complexity — O(1) after _load()
    def get_domain(self) -> str:
        """Get domain from node.yaml.

        Flat string only (DevPlan 116 B6 T7): domain: "example.com".
        Legacy dict form (domain: { platform: ... }) was removed (greenfield).

        Returns:
            Domain string or empty string
        """
        data = self._load()
        domain = data.get("domain")
        if isinstance(domain, str):
            logger.info("[IMP:7][NodeYaml.get_domain] %s", domain)
            return domain
        logger.info("[IMP:7][NodeYaml.get_domain] (empty)")
        return ""

    # endregion FUNC_get_domain

    # ── Mutation API (DevPlan 088 T3.5) ────────────────────────────────────────

    # region FUNC_get_project
    ## @purpose  Get a single project entry by name.
    ## @io — ⇥ name: str → ⎋ Optional[dict]
    ## @complexity — O(P) where P = number of projects
    def get_project(self, name: str) -> dict | None:
        """Get a project entry by name.

        Args:
            name: Project name to find

        Returns:
            Project dict or None if not found
        """
        projects = self.get_projects()
        for p in projects:
            if isinstance(p, dict) and p.get("name") == name:
                logger.info("[IMP:8][NodeYaml.get_project] Found project: %s", name)
                return p
        logger.info("[IMP:7][NodeYaml.get_project] Project not found: %s", name)
        return None

    # endregion FUNC_get_project

    # region FUNC_get_project_entries
    ## @purpose  Canonical typed parser of node.yaml#projects → list[ProjectEntry].
    ## @io — ⇥ → ⎋ list[ProjectEntry]
    ## @complexity — O(P) where P = number of projects
    ## @invariants
    ##   - Fail-fast (decision D3, DevPlan 116 B6 T4): str-entry, non-dict, or dict without a
    ##     non-empty 'name' → ConfigValidationError with record index. Malformed records are
    ##     NEVER silently skipped.
    ##   - Single parser canon: all node.yaml#projects consumers delegate to
    ##     get_project_entries()/get_projects() (reconciler, context_deployer,
    ##     reconciler_projects, vhost_renderer, lister).
    ##   - Empty optional fields → "".
    def get_project_entries(self) -> list[ProjectEntry]:
        """Parse node.yaml#projects into typed ProjectEntry list (canonical parser).

        Returns:
            List of ProjectEntry (empty list if 'projects' key missing)

        Raises:
            ConfigValidationError: malformed record (str, non-dict, or missing/empty 'name')
        """
        projects = self.get_projects()
        entries: list[ProjectEntry] = []
        for idx, p in enumerate(projects):
            if not isinstance(p, dict) or not p.get("name"):
                logger.error(
                    "[IMP:10][NodeYaml.get_project_entries] Malformed project record at index %d: %r",
                    idx,
                    p,
                )
                raise ConfigValidationError(
                    f"Malformed project entry at projects[{idx}]: expected dict with non-empty 'name' "
                    "(fail-fast, DevPlan 116 B6 D3)"
                )
            entries.append(
                ProjectEntry(
                    name=str(p.get("name", "")),
                    repo=str(p.get("repo", "")),
                    type=str(p.get("type", "")),
                    domain=str(p.get("domain", "")),
                    database=str(p.get("database", "")),
                    context=str(p.get("context", "")),
                )
            )
        logger.info("[IMP:9][NodeYaml.get_project_entries] %d project(s) parsed", len(entries))
        return entries

    # endregion FUNC_get_project_entries

    # region FUNC_add_project
    ## @purpose  Add a project to node.yaml and write back to disk.
    ## @io — ⇥ project: ProjectEntry → ⎋ None
    ## @complexity — O(P) for duplicate check + O(N) for YAML dump
    ## @invariants
    ##   Raises ConfigValidationError if project with same name already exists.
    ##   Writes back via _write_back() preserving comments (ruamel.yaml) if available.
    ##   Mutates a DEEPCOPY of _load() — cache is never poisoned by a failed write
    ##   (DevPlan 116 B6 T6.1; TRAP 2026-07-30 fixed).
    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · FIXED — add_project mutates _data cache in-place before _write_back
    # · Symptom: If _write_back fails (disk full, permission denied), the in-memory _data
    # ·   cache already contains the appended project but the file is NOT updated → cache/file desync.
    # · Root: _load() returns the cached dict by reference; appending to its "projects" list
    # ·   mutates the cache in-place.
    # · Fix: `data = copy.deepcopy(self._load())` — mutation happens on a copy; _write_back
    # ·   invalidates cache on success and on failure (DevPlan 116 B6 T6).
    # · Prevention: all mutation methods must deepcopy before modifying; never mutate _load() ref.
    def add_project(self, project: ProjectEntry) -> None:
        """Add a project to node.yaml and write back to disk.

        Args:
            project: ProjectEntry with name, repo, type, domain, database, context

        Raises:
            ConfigValidationError: if project with same name already exists
        """
        data = copy.deepcopy(self._load())
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            projects = []

        # Duplicate check
        for p in projects:
            if isinstance(p, dict) and p.get("name") == project.name:
                logger.error("[IMP:10][NodeYaml.add_project] Duplicate project: %s", project.name)
                raise ConfigValidationError(f"Project already exists: {project.name}")

        new_entry: dict[str, str] = {
            "name": project.name,
            "repo": project.repo,
            "type": project.type,
        }
        if project.domain:
            new_entry["domain"] = project.domain
        if project.database:
            new_entry["database"] = project.database
        if project.context:
            new_entry["context"] = project.context

        projects.append(new_entry)
        data["projects"] = projects

        self._write_back(data)
        logger.info("[IMP:9][NodeYaml.add_project] Added project: %s", project.name)

    # endregion FUNC_add_project

    # region FUNC_remove_project
    ## @purpose  Remove a project from node.yaml and write back to disk.
    ## @io — ⇥ name: str → ⎋ bool
    ## @complexity — O(P) for filter + O(N) for YAML dump
    ## @invariants  Returns False if project not found (no exception raised).
    ##   Mutates a DEEPCOPY — cache clean on write failure (DevPlan 116 B6 T6.1).
    # ⚠️ TRAP[BUG] · 2026-07-30 · P2 · remove_project uses list comprehension filter
    # · Symptom: If projects list contains duplicate names (shouldn't but possible after
    #   manual YAML edits), remove_project removes ALL entries with matching name, not
    #   just the first one. Could silently delete more than intended.
    # · Root: list comprehension [p for p in projects if not ...] filters ALL matches.
    # · Fix: match-and-remove-first if duplicates are semantically meaningful; for now
    #   the all-match behavior is actually preferred (clean up corrupted data).
    # · Prevention: add --remove-project CLI doc that it removes ALL matching entries.
    def remove_project(self, name: str) -> bool:
        """Remove a project from node.yaml and write back to disk.

        Args:
            name: Project name to remove

        Returns:
            True if project was found and removed, False if not found
        """
        data = copy.deepcopy(self._load())
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            return False

        new_projects = [p for p in projects if not (isinstance(p, dict) and p.get("name") == name)]

        if len(new_projects) == len(projects):
            logger.info("[IMP:8][NodeYaml.remove_project] Project not found: %s", name)
            return False

        data["projects"] = new_projects
        self._write_back(data)
        logger.info("[IMP:9][NodeYaml.remove_project] Removed project: %s", name)
        return True

    # endregion FUNC_remove_project

    # region FUNC_update_project
    ## @purpose  Update fields of an existing project entry.
    ## @io — ⇥ name: str, **updates → ⎋ bool
    ## @complexity — O(P) for search + O(N) for YAML dump
    ## @invariants  None-value fields are removed from the dict (pop). Returns False if not found.
    ##   Mutates a DEEPCOPY (nested dict entries) — cache clean on write failure
    ##   (DevPlan 116 B6 T6.1; TRAP 2026-07-30 fixed).
    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · FIXED — update_project mutates cached dict in-place
    # · Symptom: Same cache-corruption risk as add_project. If _write_back fails after
    #   updating the project dict in-place (p[key] = value), the in-memory cache is
    #   desynchronized from the file on disk.
    # · Root: p is a reference into the cached list self._data["projects"]. Mutating p
    #   mutates the cache directly.
    # · Fix: `data = copy.deepcopy(self._load())` — deep copy required because update_project
    #   mutates nested dict entries (shallow would still share the inner project dicts).
    def update_project(self, name: str, **updates: Any) -> bool:
        """Update fields of an existing project entry.

        Args:
            name: Project name to update
            updates: Fields to update (e.g., domain="new.example.com", context="prod")

        Returns:
            True if project was found and updated, False if not found
        """
        data = copy.deepcopy(self._load())
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            return False

        updated = False
        for p in projects:
            if isinstance(p, dict) and p.get("name") == name:
                for key, value in updates.items():
                    if value is not None:
                        p[key] = value
                    else:
                        p.pop(key, None)
                updated = True
                break

        if not updated:
            logger.info("[IMP:8][NodeYaml.update_project] Project not found: %s", name)
            return False

        data["projects"] = projects
        self._write_back(data)
        logger.info("[IMP:9][NodeYaml.update_project] Updated project: %s (%s)", name, ", ".join(updates.keys()))
        return True

    # endregion FUNC_update_project

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
            raise ConfigValidationError(f"'contexts' is not a list: {type(contexts)}")

        # Duplicate check
        for ctx in contexts:
            if isinstance(ctx, dict) and ctx.get("name") == name:
                logger.error("[IMP:10][NodeYaml.add_context] Duplicate context: %s", name)
                raise ConfigValidationError(f"Context already exists: {name}")

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

    # region FUNC__write_back
    ## @purpose  Write YAML data back to the original file.
    ## @io — ⇥ data: dict → ⎋ None | raise ConfigParseError
    ## @complexity — O(N) for YAML dump
    ## @invariants
    ##   Uses ruamel.yaml first for comment preservation, falls back to PyYAML.
    ##   Invalidates _data cache after write AND on failure (DevPlan 116 B6 T6.2).
    ##   Raises ConfigParseError on write failure.
    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · FIXED — broad except Exception for ruamel fallback
    # · Symptom: Any ruamel.yaml failure (unexpected error, FS error during dump) was swallowed
    # ·   by `except Exception` → silent fallback to PyYAML (comments lost) or masked real errors.
    # · Root: try/except ImportError covered the normal case; the additional broad except caught all.
    # · Fix: except narrowed to (yaml.YAMLError, OSError) only — genuine failures surface loudly
    #   (DevPlan 116 B6 T6.2).
    # · Prevention: do not re-broaden the ruamel fallback catch.
    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · FIXED — cache not invalidated on PyYAML failure
    # · Symptom: If PyYAML write fails, self._data retained the pre-mutation value → caller could
    #   believe the file was unchanged while the mutation methods had already worked on cached refs.
    # · Root: no self._data = None in the failure branch of the PyYAML fallback.
    # · Fix: self._data = None BEFORE raise in the PyYAML failure branch + mutations deepcopy
    #   (DevPlan 116 B6 T6.1/T6.2) — cache is never poisoned regardless of write outcome.
    # · Prevention: any _write_back exit path (success or failure) must invalidate _data.
    def _write_back(self, data: dict) -> None:
        """Write the YAML data back to the original file.

        Uses ruamel.yaml if available for comment preservation,
        falls back to PyYAML yaml.dump().

        Args:
            data: Dict to write as YAML

        Raises:
            ConfigParseError: on write failure
        """
        logger.info("[IMP:8][NodeYaml._write_back] Writing to %s", self._path)

        # Try ruamel.yaml first for comment preservation
        try:
            from ruamel.yaml import YAML

            ryaml = YAML()
            ryaml.width = 4096  # prevent line wrapping
            with open(self._path, "w") as f:
                ryaml.dump(data, f)
            self._data = None  # invalidate cache
            logger.info("[IMP:9][NodeYaml._write_back] Written via ruamel.yaml (comments preserved)")
            return
        except ImportError:
            logger.info("[IMP:7][NodeYaml._write_back] ruamel.yaml not available, using PyYAML")
        except (yaml.YAMLError, OSError) as e:
            logger.warning("[IMP:7][NodeYaml._write_back] ruamel.yaml failed (%s), falling back to PyYAML", e)

        # Fallback: PyYAML
        try:
            with open(self._path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            self._data = None  # invalidate cache
            logger.info("[IMP:9][NodeYaml._write_back] Written via PyYAML")
        except (OSError, yaml.YAMLError) as e:
            # DevPlan 116 B6 T6.2: invalidate cache BEFORE raise — mutations work on deepcopy,
            # but this guard protects against any cached-state drift on write failure.
            self._data = None
            logger.error("[IMP:10][NodeYaml._write_back] Write failed: %s", e)
            raise ConfigParseError(f"Failed to write node.yaml: {e}") from e

    # endregion FUNC__write_back

    # region FUNC_raw
    ## @purpose  Access raw parsed dict for backward compatibility.
    ## @io — ⇥ → ⎋ dict
    ## @complexity — O(1) after _load()
    def raw(self) -> dict:
        """Access raw parsed dict for backward compatibility.

        Use sparingly — prefer typed getters.

        Returns:
            Parsed dict
        """
        return self._load()

    # endregion FUNC_raw


# ── CLI Entrypoint ────────────────────────────────────────────────────────────


# region FUNC_cli
## @purpose  CLI entrypoint for shell consumers. python3 -m core.internal.shared.node_yaml [args]
## @io — ⇥ sys.argv → ⎋ sys.exit(code)
## @complexity — O(N) YAML parse + O(K) for operations
## @invariants
##   Exit codes: 0=success, 1=not found/generic, 2=ConfigNotFoundError,
##   3=ConfigParseError, 4=ConfigValidationError, 10=PlatformFatalError.
##   --get with missing key exits 1 (not 4) for shell || compatibility.
def _build_arg_parser() -> argparse.ArgumentParser:
    """Build argument parser for the NodeYaml CLI.

    ## @purpose  Centralized argparse construction for testability.
    ## @io — ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(description="NodeYaml unified facade CLI")
    # --file is NOT argparse-required: --resolve (3-path search) legitimately runs without it.
    # Runtime enforcement in main(): all other operations print help when --file is absent.
    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · --resolve unreachable: --file required=True rejected
    # · Symptom: `python3 -m core.internal.shared.node_yaml --resolve --resolve-node X` → argparse
    # ·   error "the following arguments are required: --file", exit 2 — _cli_resolve never ran.
    # · Root: argparse validates required args BEFORE main() dispatch; --resolve needs no --file.
    # · Fix: required=False + runtime check in main() (line ~1604: `if not args.file`).
    # · Prevention: CLI mode-flag args (--resolve, --find-project) must not require --file at parse time.
    parser.add_argument("--file", required=False, help="Path to node.yaml")
    parser.add_argument("--get", help="Dotted key to retrieve (e.g., node.host)")
    parser.add_argument("--default", help="Default value if key not found")
    parser.add_argument("--items", action="store_true", help="Output list as JSON array")
    parser.add_argument("--domain-config", action="store_true", help="Output domain config as field:value lines")
    parser.add_argument("--json-output", action="store_true", help="Output entire YAML document as JSON")
    parser.add_argument("--find-project", help="Find project by name and output JSON + org + host")
    parser.add_argument("--context", action="store_true", help="Output context name")

    # DevPlan 088 T2: resolve
    parser.add_argument("--resolve", action="store_true", help="Resolve node.yaml via 3-path search")
    parser.add_argument("--resolve-node", help="Node name for --resolve")

    # DevPlan 088 T3: jsonschema validation
    parser.add_argument("--validate-schema", action="store_true", help="Validate node.yaml against JSON schema")
    parser.add_argument("--schema-path", help="Path to JSON schema file for --validate-schema")

    # DevPlan 088 T1/T3.5: typed output
    parser.add_argument("--typed-contexts", action="store_true", help="Output contexts as JSON")
    parser.add_argument("--typed-node", action="store_true", help="Output node declaration as JSON")
    parser.add_argument("--typed-firewall", action="store_true", help="Output firewall config as JSON")
    parser.add_argument("--typed-secrets", action="store_true", help="Output secrets config as JSON")
    parser.add_argument("--typed-tor", action="store_true", help="Output tor config as JSON")
    parser.add_argument("--typed-repos", action="store_true", help="Output repos config as JSON")
    parser.add_argument("--typed-all", action="store_true", help="Output all typed fields as JSON")

    # DevPlan 088 T3.5: mutation API
    parser.add_argument(
        "--add-project",
        type=str,
        nargs=6,
        metavar=("NAME", "REPO", "TYPE", "DOMAIN", "DATABASE", "CONTEXT"),
        help="Add project: name repo type domain database context (use - for empty)",
    )
    parser.add_argument("--remove-project", help="Remove project by name")
    parser.add_argument(
        "--update-project",
        type=str,
        nargs="+",
        help="Update project: name key=value ... (e.g. myapp domain=new.example.com)",
    )

    # Legacy
    parser.add_argument("--validate", action="store_true", help="Validate node.yaml structure (basic checks)")
    return parser


def _cli_get(node: NodeYaml, args: argparse.Namespace) -> int:
    """Handle --get CLI operation.

    ## @purpose  Execute --get with optional --default and --items.
    ## @io — ⇥ node: NodeYaml, args → ⎋ exit_code: int
    ## @complexity — O(1) after load
    """
    try:
        value = node.get(args.get, default=args.default) if args.default is not None else node.get(args.get)
    except ConfigValidationError:
        # Missing key without default → exit 1 for shell || compatibility
        print(f"Key not found: {args.get}", file=sys.stderr)
        return 1

    if args.items:
        print(json.dumps(value, indent=2) if isinstance(value, (list, dict)) else json.dumps([value]))
    else:
        print(value)
    return 0


def _cli_domain_config(node: NodeYaml) -> int:
    """Handle --domain-config CLI operation.

    ## @purpose  Output domain config as field:value lines for shell parsing.
    ## @io — ⇥ node: NodeYaml → ⎋ exit_code: int
    ## @complexity — O(1) after load
    """
    cfg = node.get_domain_config()
    print(f"platform_domain:{cfg.platform_domain}")
    print(f"email:{cfg.email}")
    print(f"acme_dns_plugin:{cfg.acme_dns_plugin}")
    print(f"project_domains:{' '.join(cfg.project_domains)}")
    return 0


def _cli_find_project(node: NodeYaml, project_name: str) -> int:
    """Handle --find-project CLI operation.

    ## @purpose  Find project by name, output JSON + org + host for shell scripts.
    ## @io — ⇥ node: NodeYaml, project_name: str → ⎋ exit_code: int
    ## @complexity — O(P) where P = number of projects
    """
    projects = node.get_projects()
    for proj in projects:
        if isinstance(proj, dict) and proj.get("name") == project_name:
            print(json.dumps(proj, indent=2))
            ctx = node.get_context()
            if ctx:
                print(f"___ORG___{ctx}")
            nfo = node.get_node_info()
            if nfo.fqdn:
                print(f"___HOST___{nfo.fqdn}")
            elif node.get("node.host", default=""):
                print(f"___HOST___{node.get('node.host')}")
            return 0
    print(f"Project not found: {project_name}", file=sys.stderr)
    return 1


def _cli_validate(node: NodeYaml) -> int:
    """Handle --validate CLI operation (basic checks).

    ## @purpose  Validate node.yaml structure, output errors to stderr.
    ## @io — ⇥ node: NodeYaml → ⎋ exit_code: int
    ## @complexity — O(1) after load
    """
    errors = node.validate()
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return len(errors)


def _cli_validate_schema(node: NodeYaml, schema_path: str | None = None) -> int:
    """Handle --validate-schema CLI operation with jsonschema.

    ## @purpose  Validate node.yaml against JSON schema, output errors to stderr.
    ## @io — ⇥ node: NodeYaml, schema_path: Optional[str] → ⎋ exit_code: int
    ## @complexity — O(N) for YAML parse + O(S) for jsonschema
    """
    errors = node.validate(schema_path=schema_path)
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return len(errors)


def _cli_resolve(args: argparse.Namespace) -> int:
    """Handle --resolve CLI operation.

    ## @purpose  Resolve node.yaml via 3-path search and print path.
    ## @io — ⇥ args → ⎋ exit_code: int
    ## @complexity — O(P) for search + O(N) for YAML parse
    ## @invariants
    ##   - stdout contains EXACTLY ONE line: the resolved node.yaml path.
    ##     Shell consumers do `path="$(python3 -m ... --resolve ...)"` — multi-line
    ##     stdout would corrupt NODE_YAML_PATH (path + marker lines).
    """
    try:
        resolved = NodeYaml.resolve(node_name=args.resolve_node)
        print(resolved._path)
        return 0
    except ConfigNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2


def _cli_typed_json(node: NodeYaml, field: str) -> int:
    """Output a typed dataclass as JSON.

    ## @purpose  Handle --typed-* CLI operations.
    ## @io — ⇥ node: NodeYaml, field: str → ⎋ exit_code: int
    ## @complexity — O(1) after load
    """
    import dataclasses

    getters = {
        "contexts": node.get_contexts,
        "node": lambda: dataclasses.asdict(node.get_node_declaration()),
        "firewall": lambda: dataclasses.asdict(node.get_firewall()),
        "secrets": lambda: dataclasses.asdict(node.get_secrets_config()),
        "tor": lambda: dataclasses.asdict(node.get_tor_config()),
        "repos": lambda: dataclasses.asdict(node.get_repos()),
    }

    getter = getters.get(field)
    if getter is None:
        print(f"Unknown typed field: {field}", file=sys.stderr)
        return 1

    value = getter()
    print(json.dumps(value, indent=2, default=str))
    return 0


def main() -> None:
    """NodeYaml CLI entrypoint.

    ## @purpose  Main entry for python3 -m core.internal.shared.node_yaml [args]
    ## @io — ⇥ sys.argv → ⎋ sys.exit(code)
    ## @complexity — O(1) dispatch
    """
    parser = _build_arg_parser()
    args = parser.parse_args()

    # --resolve does not need --file
    if args.resolve:
        sys.exit(_cli_resolve(args))

    # All other operations need --file
    if not args.file:
        parser.print_help()
        sys.exit(0)

    try:
        node = NodeYaml(args.file)
    except ConfigNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    except ConfigParseError as e:
        print(str(e), file=sys.stderr)
        sys.exit(3)

    try:
        if args.get:
            sys.exit(_cli_get(node, args))
        elif args.domain_config:
            sys.exit(_cli_domain_config(node))
        elif args.context:
            print(node.get_context())
            sys.exit(0)
        elif args.json_output:
            print(json.dumps(node.raw(), indent=2))
            sys.exit(0)
        elif args.find_project:
            sys.exit(_cli_find_project(node, args.find_project))
        elif args.validate:
            sys.exit(_cli_validate(node))
        elif args.validate_schema:
            sys.exit(_cli_validate_schema(node, schema_path=args.schema_path))
        elif args.add_project:
            name, repo, ptype, domain, database, context = args.add_project
            project = ProjectEntry(
                name=name,
                repo=repo,
                type=ptype,
                domain=domain if domain != "-" else "",
                database=database if database != "-" else "",
                context=context if context != "-" else "",
            )
            node.add_project(project)
            print(f"Added project: {name}")
            sys.exit(0)
        elif args.remove_project:
            removed = node.remove_project(args.remove_project)
            if removed:
                print(f"Removed project: {args.remove_project}")
                sys.exit(0)
            else:
                print(f"Project not found: {args.remove_project}", file=sys.stderr)
                sys.exit(1)
        elif args.update_project:
            if len(args.update_project) < 2:
                print("Usage: --update-project name key=value [key=value ...]", file=sys.stderr)
                sys.exit(1)
            name = args.update_project[0]
            updates: dict[str, str] = {}
            for kv in args.update_project[1:]:
                if "=" not in kv:
                    print(f"Invalid key=value pair: {kv}", file=sys.stderr)
                    sys.exit(1)
                k, v = kv.split("=", 1)
                updates[k] = v
            updated = node.update_project(name, **updates)
            if updated:
                print(f"Updated project: {name} ({', '.join(updates.keys())})")
                sys.exit(0)
            else:
                print(f"Project not found: {name}", file=sys.stderr)
                sys.exit(1)
        elif args.typed_contexts:
            sys.exit(_cli_typed_json(node, "contexts"))
        elif args.typed_node:
            sys.exit(_cli_typed_json(node, "node"))
        elif args.typed_firewall:
            sys.exit(_cli_typed_json(node, "firewall"))
        elif args.typed_secrets:
            sys.exit(_cli_typed_json(node, "secrets"))
        elif args.typed_tor:
            sys.exit(_cli_typed_json(node, "tor"))
        elif args.typed_repos:
            sys.exit(_cli_typed_json(node, "repos"))
        elif args.typed_all:
            import dataclasses

            output = {
                "contexts": node.get_contexts(),
                "node": dataclasses.asdict(node.get_node_declaration()),
                "firewall": dataclasses.asdict(node.get_firewall()),
                "secrets": dataclasses.asdict(node.get_secrets_config()),
                "tor": dataclasses.asdict(node.get_tor_config()),
                "repos": dataclasses.asdict(node.get_repos()),
                "domain_config": {
                    "platform_domain": node.get_domain(),
                    "email": node.get_email(),
                    "acme_dns_plugin": node.get_acme_dns_plugin(),
                },
                "postgres_init_databases": node.get_postgres_init_databases(),
                "projects": node.get_projects(),
                "modules": node.get_modules(),
            }
            print(json.dumps(output, indent=2, default=str))
            sys.exit(0)
        else:
            parser.print_help()
            sys.exit(0)
    except ConfigNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    except ConfigParseError as e:
        print(str(e), file=sys.stderr)
        sys.exit(3)
    except ConfigValidationError as e:
        print(str(e), file=sys.stderr)
        sys.exit(4)
    except PlatformFatalError as e:
        print(str(e), file=sys.stderr)
        sys.exit(10)


# endregion FUNC_cli


if __name__ == "__main__":
    main()
