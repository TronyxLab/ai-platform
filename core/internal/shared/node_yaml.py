#!/usr/bin/env python3
# GREP_SUMMARY: node_yaml, NodeYaml, yaml-facade, lazy-load, cache, dotted-keys, config-reader, extract-context, cli
# STRUCTURE: ▶ NodeYaml(path) → ◇ _load() → ◇ cache → ◇ get(key)/get_list(key)/... → ⎋ typed result | raise PlatformError
# region MODULE_CONTRACT
## @purpose  Unified facade for reading ai-platform node.yaml configuration.
##           Provides lazy-load + cache, dotted-key access, typed NamedTuples,
##           structural validation, and a CLI interface for shell consumers.
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
##   9. extract_context_from_node_yaml() is maintained as deprecated alias.
## @rationale Single facade eliminates 36+ duplicate yaml.safe_load calls (DevPlan 038a).
##   Lazy-load prevents I/O in 30% of cases where NodeYaml is created but not used (preflight).
##   Dotted-key API eliminates nested dict boilerplate (data["node"]["host"] → node.get("node.host")).
##   Typed exceptions provide fail-fast and distinct recoverable vs fatal error handling.
## @changes 2026-07-26 · DevPlan 038a — Complete rewrite: added NodeYaml class, CLI, NamedTuples
## @changes 2026-07-25 · DevPlan 070 — Created as shared module with extract_context_from_node_yaml
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import sys
import warnings
from typing import Any, NamedTuple

import yaml

from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformFatalError,
)

logger = logging.getLogger(__name__)


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

    GREP_SUMMARY: NodeYaml, yaml-facade, lazy-load, cache, dotted-keys, config-reader
    STRUCTURE: ▶ NodeYaml(path) → ◇ _load() → ◇ cache → ◇ get(key)/get_list(key)/... → ⎋ typed result | raise PlatformError

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
    ## @purpose  Extract context name from node.yaml.
    ## @io — ⇥ → ⎋ str
    ## @complexity — O(1) after _load()
    ## @invariants
    ##   Priority: 1. 'context' field (string)  2. 'contexts[0].name' (dict) or 'contexts[0]' (str)
    ##   3. Empty string if neither found
    def get_context(self) -> str:
        """Extract context name from node.yaml.

        Priority:
          1. Top-level 'context' field (string)
          2. 'contexts' array → first element's 'name' field (dict) or value (string)
          3. Empty string if neither found

        Returns:
            Context name or ""
        """
        data = self._load()

        # Primary: context field (string)
        ctx = data.get("context", "")
        if ctx and isinstance(ctx, str):
            logger.info("[IMP:8][NodeYaml] Context: %s", ctx)
            return ctx

        # Fallback: contexts array
        contexts = data.get("contexts", [])
        if contexts and isinstance(contexts, list) and len(contexts) > 0:
            first = contexts[0]
            if isinstance(first, dict):
                ctx = first.get("name", "")
            elif isinstance(first, str):
                ctx = first
            if ctx:
                logger.info("[IMP:8][NodeYaml] Context: %s (from contexts[0])", ctx)
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

        Data source priority:
          1. Top-level field (e.g., data["domain"] as string)
          2. Nested domain.{field} (e.g., data.get("domain", {}).get("platform"))
        This supports both schemas: legacy with domain as dict, and new flat schema.
        """
        data = self._load()

        # ── platform_domain ──
        # Priority: top-level "domain" string > nested domain.platform > ""
        domain_raw = data.get("domain")
        if isinstance(domain_raw, str):
            platform_domain = domain_raw
        elif isinstance(domain_raw, dict):
            platform_domain = domain_raw.get("platform", "")
        else:
            platform_domain = ""
        if not isinstance(platform_domain, str):
            platform_domain = ""

        # ── email ──
        # Priority: top-level "email" string > nested domain.email > ""
        email = data.get("email", "")
        if not isinstance(email, str) or not email:
            domain = data.get("domain", {})
            if isinstance(domain, dict):
                email = domain.get("email", "")
        if not isinstance(email, str):
            email = ""

        # ── acme_dns_plugin ──
        # Priority: top-level "acme_dns_plugin" string > nested domain.acme_dns_plugin > ""
        acme_dns_plugin = data.get("acme_dns_plugin", "")
        if not isinstance(acme_dns_plugin, str) or not acme_dns_plugin:
            domain = data.get("domain", {})
            if isinstance(domain, dict):
                acme_dns_plugin = domain.get("acme_dns_plugin", "")
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
    ## @purpose  Validate node.yaml structure.
    ## @io — ⇥ → ⎋ list[str]
    ## @complexity — O(1) after _load()
    ## @invariants
    ##   Checks: node section exists, node.host non-empty, domain section exists, domain.platform non-empty.
    ##   Returns list of error messages (empty = valid).
    def validate(self) -> list[str]:
        """Validate node.yaml structure.

        Checks:
          - 'node' section exists
          - 'node.host' is non-empty
          - 'domain' section exists
          - 'domain.platform' is non-empty

        Returns:
            List of error messages (empty list = valid)
        """
        errors: list[str] = []
        data = self._load()

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

        # Check domain section
        domain = data.get("domain")
        if domain is None:
            errors.append("Missing 'domain' section")
        elif not isinstance(domain, dict):
            errors.append("'domain' section is not a dict")
        else:
            platform = domain.get("platform", "")
            if not platform:
                errors.append("Missing or empty 'domain.platform'")

        logger.info("[IMP:8][NodeYaml] Validation: %d errors", len(errors))
        return errors

    # endregion FUNC_validate

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


# ── Backward-Compat Alias (Deprecated) ────────────────────────────────────────


# region FUNC_extract_context_from_node_yaml (deprecated)
## @purpose  DEPRECATED: Use NodeYaml(path).get_context() instead.
##            Maintained for backward compatibility during migration.
## @io — ⇥ node_yaml_path: str, log_tag: str = "context" → ⎋ str
## @complexity — O(N) YAML parse on first call
## @invariants  Returns "" on error (backward-compat with old exception-absorbing behavior)
def extract_context_from_node_yaml(node_yaml_path: str, log_tag: str = "context") -> str:
    """DEPRECATED: Use NodeYaml(path).get_context() instead.

    Maintained for backward compatibility during migration.
    The old behavior absorbed FileNotFoundError and YAMLError and returned "".
    This wrapper preserves that contract while emitting a DeprecationWarning.
    """
    warnings.warn(
        "extract_context_from_node_yaml() is deprecated. Use NodeYaml(path).get_context() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        ctx = NodeYaml(node_yaml_path).get_context()
        if ctx:
            logger.info("[IMP:8][%s] Context: %s", log_tag, ctx)
        return ctx
    except (ConfigNotFoundError, ConfigParseError) as e:
        logger.warning("[IMP:7][%s] Failed to parse %s: %s", log_tag, node_yaml_path, e)
        return ""


# endregion FUNC_extract_context_from_node_yaml (deprecated)


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
    parser.add_argument("--file", required=True, help="Path to node.yaml")
    parser.add_argument("--get", help="Dotted key to retrieve (e.g., node.host)")
    parser.add_argument("--default", help="Default value if key not found")
    parser.add_argument("--items", action="store_true", help="Output list as JSON array")
    parser.add_argument("--domain-config", action="store_true", help="Output domain config as field:value lines")
    parser.add_argument("--json-output", action="store_true", help="Output entire YAML document as JSON")
    parser.add_argument("--find-project", help="Find project by name and output JSON + org + host")
    parser.add_argument("--context", action="store_true", help="Output context name")
    parser.add_argument("--validate", action="store_true", help="Validate node.yaml structure")
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
    """Handle --validate CLI operation.

    ## @purpose  Validate node.yaml structure, output errors to stderr.
    ## @io — ⇥ node: NodeYaml → ⎋ exit_code: int
    ## @complexity — O(1) after load
    """
    errors = node.validate()
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return len(errors)


def main() -> None:
    """NodeYaml CLI entrypoint.

    ## @purpose  Main entry for python3 -m core.internal.shared.node_yaml [args]
    ## @io — ⇥ sys.argv → ⎋ sys.exit(code)
    ## @complexity — O(1) dispatch
    """
    parser = _build_arg_parser()
    args = parser.parse_args()

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
