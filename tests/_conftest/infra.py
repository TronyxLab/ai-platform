# GREP_SUMMARY: test-infra, auto-discovery, container-names, compose-ports, test-networks, STALE_CONTAINER_NAMES, singleton
# STRUCTURE: ┌_TestInfra singleton┐ → ◇ _load_test_infra(subprocess discover_modules.py --test-infra) → ⊕ cached dict[key:module] → ├── get_container_name() ├── get_test_port() ├── get_compose_file() ├── get_networks_for_module() → ⎋ STALE_CONTAINER_NAMES + ALL_TEST_NETWORKS
# region MODULE_CONTRACT
## @purpose  Canonical test infrastructure auto-discovery cache. Derives container names, ports,
##           compose files, and networks from docker-compose.test.yml via discover_modules.py --test-infra.
##           Replaces hardcoded _STALE_CONTAINER_NAMES, container_name constants, and port literals
##           across all test files with a single source of truth derived from compose files.
## @scope    Used by all test files that need container_name, test ports, compose paths, or test networks.
##           Calls discover_modules.py once per session via subprocess (cached at module level).
## @invariants
##   - _load_test_infra() cached via _TEST_INFRA_CACHE — one subprocess call per pytest session
##   - STALE_CONTAINER_NAMES always contains ALL container_name values from docker-compose.test.yml
##   - get_container_name() returns the first container_name for a module (single-container modules)
##   - get_container_names() returns ALL container_names for multi-service modules (e.g. infra-metrics → 5)
##   - _TestInfra singleton wraps cached data and provides all accessor methods
##   - Any KeyError indicates a module name mismatch — fail fast with clear message
## @rationale Eliminates FRAGILITY COLLAPSE where _STALE_CONTAINER_NAMES and container_name hardcodes
##            drift from compose files. Deriving from compose files ensures always-in-sync state.
##            Singleton pattern ensures exactly one subprocess call per test session.
## @changes CREATED: 2026-07-22 | DevPlan 041 W2: Test infrastructure auto-discovery cache
# endregion MODULE_CONTRACT

import json
import logging
import subprocess
from functools import lru_cache
from pathlib import Path

_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DISCOVER_SCRIPT = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "discover_modules.py"


# region FUNC_load_test_infra
## @purpose  Run discover_modules.py --test-infra --json, parse and cache result
## @io       ⇥ None → ⎋ list[dict]: module test info sorted by module_name
## @complexity — O(1) subprocess call, cached after first invocation
## @invariants
##   - Result cached via _TEST_INFRA_CACHE module-level variable (not lru_cache on function)
##   - Subprocess runs with cwd=_PROJECT_ROOT to resolve relative paths correctly
##   - Raises RuntimeError if discover script not found or subprocess fails


@lru_cache(maxsize=1)
def _load_test_infra() -> list[dict]:
    """Load test infrastructure data from discover_modules.py --test-infra --json.

    Cached at import level — one subprocess call per pytest session.
    """
    if not _DISCOVER_SCRIPT.exists():
        raise RuntimeError(f"Discover script not found: {_DISCOVER_SCRIPT}")

    _logger.info("[IMP:7][infra][_load_test_infra] Loading test infrastructure from %s", _DISCOVER_SCRIPT)
    result = subprocess.run(
        ["python3", str(_DISCOVER_SCRIPT), "--test-infra", "--json"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(_PROJECT_ROOT),
        timeout=30,
    )
    data = json.loads(result.stdout)
    _logger.info("[IMP:8][infra][_load_test_infra] Loaded %d modules", len(data))
    return data


# endregion FUNC_load_test_infra


# region CLASS_TestInfra
## @purpose  Singleton wrapper around cached test infrastructure data. Provides all accessor methods.
## @scope    Module-level singleton `infra` instantiated at import time.
## @invariants
##   - Single instance per process (module-level singleton pattern)
##   - All methods delegate to cached _data dict indexed by module name
##   - Unknown module name raises KeyError with descriptive message


class _TestInfra:
    """Singleton providing canonical access to test infrastructure data.

    ## @purpose — Central access point for container names, ports, compose files, and networks.
    ##             Loads data once via subprocess, caches for the entire test session.
    ## @io — All methods: ⇥ str module_name → ⎋ str|list[str]|int|tuple[Path,Path]
    ## @complexity — All getters: O(1) dict lookup
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = _load_test_infra()
            # Build index: module_name → data dict
            cls._instance._index: dict[str, dict] = {}
            for mod in cls._instance._data:
                cls._instance._index[mod["module"]] = mod
        return cls._instance

    def _get(self, module_name: str) -> dict:
        """Get module data by name. Raises KeyError with available modules on miss."""
        if module_name not in self._index:
            available = sorted(self._index.keys())
            raise KeyError(
                f"Module '{module_name}' not found in test infrastructure. "
                f"Available modules ({len(available)}): {available}"
            )
        return self._index[module_name]

    # region FUNC_get_container_name
    ## @purpose  Return container_name for a service within a module.
    ##           If module has one container, returns it directly.
    ##           If service param given, uses service_containers mapping for precise lookup.
    ## @io       ⇥ module_name: str, service: str|None → ⎋ str
    ## @complexity — O(1)
    def get_container_name(self, module_name: str, service: str | None = None) -> str:
        """Return container_name for a module/service.

        Args:
            module_name: e.g., 'postgres'
            service: optional service name for multi-service modules (e.g., 'pgbouncer')

        Returns:
            str: container_name (e.g., 'postgres-test', 'pgbouncer-test')
        """
        data = self._get(module_name)
        if service:
            svc_containers = data.get("service_containers", {})
            if service in svc_containers:
                return svc_containers[service]
            raise KeyError(
                f"Service '{service}' not found in module '{module_name}'. "
                f"Available services: {sorted(svc_containers.keys())}"
            )
        names = data.get("container_names", [])
        if not names:
            raise KeyError(f"Module '{module_name}' has no container_names in test infrastructure")
        return names[0]

    # endregion

    # region FUNC_get_container_names
    ## @purpose  Return ALL container_names for a module (e.g., infra-metrics → 5 names)
    ## @io       ⇥ module_name: str → ⎋ list[str]
    ## @complexity — O(1)
    def get_container_names(self, module_name: str) -> list[str]:
        """Return all container_names for a module (multi-service modules)."""
        return list(self._get(module_name).get("container_names", []))

    # endregion

    # region FUNC_get_test_port
    ## @purpose  Return external test port(s) for a module.
    ##           If service is specified, returns int port; else returns dict of {service: port}.
    ##           For services with multiple ports (nginx: http+https), use port_name to disambiguate.
    ## @io       ⇥ module_name: str, service: str|None, port_name: str|None → ⎋ int|dict
    ## @complexity — O(P) where P = ports per service
    def get_test_port(self, module_name: str, service: str | None = None, port_name: str | None = None) -> int | dict:
        """Return external test port(s) for a module.

        Args:
            module_name: e.g., 'pgbouncer' → 6432
            service: if module has multiple services, specify which one
            port_name: for multi-port services, specify port identity (e.g., 'http' vs 'https')

        Returns:
            int if service specified, dict[str, list[dict]] if not.
        """
        data = self._get(module_name)
        ports = data.get("ports", {})
        if service:
            if service not in ports:
                available = sorted(ports.keys())
                raise KeyError(
                    f"Service '{service}' not found in module '{module_name}'. Available services: {available}"
                )
            port_list = ports[service]
            if len(port_list) == 1:
                return port_list[0]["external"]
            # Multi-port service: use port_name to disambiguate if provided
            if port_name:
                # Match by approximate internal port convention
                for p in port_list:
                    internal = p["internal"]
                    if port_name == "http" and internal in (80, 8080):
                        return p["external"]
                    if port_name == "https" and internal == 443:
                        return p["external"]
                    if port_name == "dashboard" and internal == 9119:
                        return p["external"]
                    if port_name == "desktop" and internal in (8642, 842):
                        return p["external"]
                # Fallback: return first port
                return port_list[0]["external"]
            # No port_name — return dict of service → port(s)
            return {service: port_list[0]["external"]}
        # Return all ports grouped by service
        return {
            svc: plist[0]["external"] if len(plist) == 1 else [p["external"] for p in plist]
            for svc, plist in ports.items()
        }

    # endregion

    # region FUNC_get_compose_file
    ## @purpose  Return (base_compose_path, test_compose_path) for a module.
    ## @io       ⇥ module_name: str → ⎋ tuple[Path, Path]
    ## @complexity — O(1)
    def get_compose_file(self, module_name: str) -> tuple[Path, Path]:
        """Return (base_compose, test_compose) Paths for a module."""
        data = self._get(module_name)
        return Path(data["compose_base"]), Path(data["compose_test"])

    # endregion

    # region FUNC_get_networks_for_module
    ## @purpose  Return list of network names a module's test container connects to.
    ## @io       ⇥ module_name: str → ⎋ list[str]
    ## @complexity — O(1)
    def get_networks_for_module(self, module_name: str) -> list[str]:
        """Return list of test networks for a module."""
        return list(self._get(module_name).get("networks", []))

    # endregion

    # region PROPERTY_stale_container_names
    ## @purpose  All container names across ALL test modules — always in sync with compose files.
    ## @io       ⇥ (self) → ⎋ list[str] sorted
    ## @complexity — O(M) where M = total container names across all modules
    @property
    def stale_container_names(self) -> list[str]:
        """Sorted list of ALL container names from all test compose files."""
        names: list[str] = []
        for mod in self._data:
            names.extend(mod.get("container_names", []))
        return sorted(names)

    # endregion

    # region PROPERTY_all_test_networks
    ## @purpose  All unique test network names across all modules.
    ## @io       ⇥ (self) → ⎋ set[str]
    ## @complexity — O(M * N) where M = modules, N = networks per module
    @property
    def all_test_networks(self) -> set[str]:
        """Set of all unique test network names across all modules."""
        networks: set[str] = set()
        for mod in self._data:
            networks.update(mod.get("networks", []))
        return networks

    # endregion

    # region PROPERTY_all_modules
    ## @purpose  List of all module names with test infrastructure data.
    ## @io       ⇥ (self) → ⎋ list[str]
    ## @complexity — O(1)
    @property
    def all_modules(self) -> list[str]:
        """Sorted list of all module names with docker-compose.test.yml."""
        return sorted(self._index.keys())

    # endregion


# endregion CLASS_TestInfra


# Module-level singleton — instantiated at import time
# ⚠️ TRAP[PERF] · 2026-07-22 · Subprocess on import — triggers discover_modules.py --test-infra
# · Mitigation: _load_test_infra cached via @lru_cache; one subprocess call per pytest session.
# · If discover_modules.py becomes slow (>500ms), add file-based cache with mtime check.
infra = _TestInfra()


# Mutable flag for dynamic detection of requires_docker marker presence.
# Used by test_conftest_isolation.py to verify static tests don't trigger Docker infra.
# Set to True after pytest_collection_modifyitems if any collected test has requires_docker marker.
class _InfraActiveFlag:
    """Mutable boolean container — allows post-import modification via pytest hooks."""

    def __init__(self) -> None:
        self._active: bool = True

    def __bool__(self) -> bool:
        return self._active

    def set(self, value: bool) -> None:
        self._active = value


_test_infra_was_active: _InfraActiveFlag = _InfraActiveFlag()
