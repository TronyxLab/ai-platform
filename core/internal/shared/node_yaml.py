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
## @changes 2026-08-01 · DevPlan 116 B3 T5 (U-52) — CLI +--get-many (batch alias:dotted-key pairs,
##           TAB-separated output, empty value for missing key exit 0, malformed/empty spec → exit 4).
##           bootstrap.sh single batch call replaces 6 per-field --get invocations.
## @changes 2026-07-30 · DevPlan 088 — Wave 1: typed dataclasses (T1), resolve() (T2), jsonschema validate (T3),
##           mutation API + getters + CLI (T3.5), all LDD logs + region markers
## @changes 2026-07-26 · DevPlan 038a — Complete rewrite: added NodeYaml class, CLI, NamedTuples
## @changes 2026-07-25 · DevPlan 070 — Created as shared module with the deprecated context-extract alias
# endregion MODULE_CONTRACT

import copy
import glob as glob_module
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, NamedTuple

import yaml

# B3: канонический platform base — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)

logger = logging.getLogger(__name__)


# ── Typed Dataclasses (DevPlan 088 T1) ──────────────────────────────────────
# Волна 118 B3: NodeDeclaration/FirewallConfig/SecretsConfig/TorConfig/ReposConfig +
# get_tor_config/get_repos/get_postgres_init_databases/get_node_declaration/
# get_acme_dns_plugin/get_email/get_firewall/get_secrets_config/get_contexts/get_domain
# УДАЛЕНЫ (verify-then-delete: 0 потребителей вне node_yaml_cli.py --typed-*, которые сами
# 0 вызовов; get_domain_config/get_node_info сохранены — потребители preflight.py + CLI).


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
            config_dir = os.environ.get("PLATFORM_ROOT", str(platform_remote_base()))

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

# DevPlan 117 G T51: CLI extracted to core.internal.shared.node_yaml_cli (all 10 _cli_* functions,
# _build_arg_parser, main — ~430 LOC). Lazy import keeps `python3 -m core.internal.shared.node_yaml`
# working (AC-G5) without importing node_yaml_cli at module load (start-up time unchanged).

# ⚠️ NOTE (DevPlan 117 G T51 backward-compat): node_yaml_cli symbols are re-exported lazily via
# PEP 562 __getattr__ — the extraction moved _cli_* helpers to node_yaml_cli.py, and a legacy test
# (test_node_yaml_cli_get_many.py) imports _cli_get_many from this module. Lazy resolution keeps
# the name importable without loading node_yaml_cli at module load (AC-G5).

# region FUNC___getattr__
_CLI_SYMBOLS = frozenset(
    {
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
    }
)


def __getattr__(name: str):
    """Lazily re-export node_yaml_cli symbols for backward compatibility (PEP 562).

    ## @purpose — Legacy consumers (tests, external code) importing _cli_* / main from
    ##            node_yaml still work after the DevPlan 117 G T51 extraction. The import
    ##            happens on first attribute access — node_yaml_cli is not loaded otherwise.
    ## @io — ⇥ name: str → ⎋ Any | raise AttributeError
    ## @complexity — O(1) + first-access import cost
    """
    if name in _CLI_SYMBOLS:
        import importlib as _importlib

        module = _importlib.import_module("core.internal.shared.node_yaml_cli")
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# endregion FUNC___getattr__


if __name__ == "__main__":
    # Lazy import — node_yaml_cli is only loaded on direct CLI invocation.
    from core.internal.shared.node_yaml_cli import main as _cli_main

    sys.exit(_cli_main())
