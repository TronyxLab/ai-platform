# GREP_SUMMARY: audit platform-root modules-dir module-yamls module-graph compose-files all-networks static-audit
# STRUCTURE: platform_root → modules_dir → all_module_yamls → ◇ module_graph + all_compose_files → ⊕ all_networks
# region MODULE_CONTRACT
## @purpose  Session-scoped fixtures for static module audit: platform root, module YAML graph,
##           compose files, and network topology.
## @scope    Consumed by test_static_audit.py and similar module audit tests.
## @invariants
##   - All fixtues are session-scoped (data is static during a test run)
##   - platform_root uses pathlib.Path(__file__).resolve().parent.parent.parent (adjusted from conftest.py)
##   - modules_dir = platform_root + "/core/modules"
##   - all_module_yamls reads all modules/*/module.yaml; skips non-module dirs
##   - module_graph builds depends_on DAG; topologically sorted via Kahn's algorithm
##   - all_compose_files only includes install_type: docker modules with docker-compose.base.yml
##   - all_networks merges config.network from YAML + networks from compose files
## @rationale Centralising module fixture logic in conftest avoids duplication across audit tests.
# endregion MODULE_CONTRACT

import logging
import pathlib
from collections import deque
from pathlib import Path
from typing import Any

import pytest
import yaml

logger = logging.getLogger(__name__)

# region STATIC_AUDIT_FIXTURES
## @purpose — Session-scoped fixtures for static module audit: platform root, module YAML graph,
##            compose files, and network topology.
## @scope — Used by test_static_audit.py and similar module audit tests.
## @invariants
##   - All fixtures are session-scoped (data is static during a test run)
##   - platform_root uses pathlib.Path(__file__).resolve().parent.parent.parent (adjusted from conftest.py)
##   - modules_dir = platform_root + "/core/modules"
##   - all_module_yamls reads all modules/*/module.yaml; skips non-module dirs
##   - module_graph builds depends_on DAG; topologically sorted via Kahn's algorithm
##   - all_compose_files only includes install_type: docker modules with docker-compose.base.yml
##   - all_networks merges config.network from YAML + networks from compose files
## @rationale — Centralising module fixture logic in conftest avoids duplication across audit tests.
## @usecases — test_static_audit.py (module graph integrity, network consistency, topological order)


@pytest.fixture(scope="session")
def platform_root() -> str:
    """
    ## @purpose — Absolute path to the project root (ai-platform/).
    ## @io — ⎋ str: absolute POSIX path to project root
    ## @complexity — O(1)
    """
    root = str(pathlib.Path(__file__).resolve().parent.parent.parent)
    # [IMP:9][conftest][platform_root] Resolved platform root
    logger.info("%s", f"[IMP:9][conftest][platform_root] platform_root = {root}")
    return root


@pytest.fixture(scope="session")
def modules_dir(platform_root: str) -> str:
    """
    ## @purpose — Path to core/modules/ directory.
    ## @io — ⎋ str: absolute path to modules dir
    ## @complexity — O(1)
    """
    path = Path(platform_root) / "core" / "modules"
    logger.info("%s", f"[IMP:9][conftest][modules_dir] modules_dir = {path}")
    return path


@pytest.fixture(scope="session")
def all_module_yamls(modules_dir: str) -> dict[str, dict[str, Any]]:
    """
    ## @purpose — Parse all modules/*/module.yaml into {module_name: parsed_dict}.
    ## @io — ⇥ modules_dir → ⎋ dict[str, dict]
    ## @complexity — O(N) where N = number of module subdirectories
    ## @invariants
    ##   - Only parses directories that contain a module.yaml file
    ##   - Directories without module.yaml are silently skipped
    """
    result: dict[str, dict[str, Any]] = {}
    if not pathlib.Path(modules_dir).is_dir():
        logger.info("%s", f"[IMP:4][conftest][all_module_yamls] modules_dir not found: {modules_dir}")
        return result

    for entry in sorted(p.name for p in pathlib.Path(modules_dir).iterdir()):
        module_path = Path(modules_dir) / entry
        yaml_path = Path(module_path) / "module.yaml"
        if pathlib.Path(module_path).is_dir() and pathlib.Path(yaml_path).is_file():
            with pathlib.Path(yaml_path).open(encoding="utf-8") as f:
                result[entry] = yaml.safe_load(f)

    logger.info("%s", f"[IMP:9][conftest][all_module_yamls] Loaded {len(result)} module YAML(s)")
    return result


@pytest.fixture(scope="session")
def module_graph(all_module_yamls: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """
    ## @purpose — Build dependency graph from module.yaml depends_on fields.
    ##            Returns adjacency list topologically sorted via Kahn's algorithm.
    ## @io — ⇥ all_module_yamls → ⎋ dict[str, list[str]]: {module_name: [dependency_names]}
    ## @complexity — O(V + E) where V = module count, E = dependency edges
    ## @invariants
    ##   - depends_on may be None, [] (empty), or a list of names
    ##   - Graph is validated as acyclic (DAG); if cycle detected, raises RuntimeError
    ## @rationale — Topological ordering ensures deploy-modules.sh processes modules in correct sequence.
    """
    graph: dict[str, list[str]] = {}
    for name, data in all_module_yamls.items():
        deps = data.get("depends_on") or []
        graph[name] = list(deps)

    # Validate DAG via Kahn's algorithm (topological sort)
    in_degree: dict[str, int] = {}
    for node, deps in graph.items():
        in_degree[node] = len(deps)
        for dep in deps:
            if dep not in in_degree:
                in_degree[dep] = 0

    # Build reverse adjacency: for each dep, which nodes depend on it?
    dependents: dict[str, list[str]] = {n: [] for n in graph}
    for node, deps in graph.items():
        for dep in deps:
            if dep not in dependents:
                dependents[dep] = []
            dependents[dep].append(node)

    queue = deque([node for node in graph if in_degree[node] == 0])
    sorted_nodes: list[str] = []
    while queue:
        node = queue.popleft()
        sorted_nodes.append(node)
        for dependent in dependents.get(node, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(sorted_nodes) != len(graph):
        msg = (
            f"[IMP:9][conftest][module_graph] Cycle detected in dependency graph! "
            f"Processed {len(sorted_nodes)}/{len(graph)} nodes"
        )
        raise RuntimeError(msg)

    # Return adjacency list, sorted by topological order for deterministic output
    ordered: dict[str, list[str]] = {}
    for node in sorted_nodes:
        ordered[node] = sorted(graph[node])

    logger.info("%s", f"[IMP:9][conftest][module_graph] Topological order: {sorted_nodes}")
    return ordered


@pytest.fixture(scope="session")
def all_compose_files(modules_dir: str, all_module_yamls: dict[str, dict[str, Any]]) -> dict[str, str]:
    """
    ## @purpose — Map docker modules to their docker-compose.base.yml paths.
    ## @io — ⇥ modules_dir, all_module_yamls → ⎋ dict[str, str]: {module_name: compose_path}
    ## @complexity — O(N) where N = module count
    ## @invariants
    ##   - Only modules with install_type: docker are considered
    ##   - Module must have a docker-compose.base.yml file on disk
    """
    result: dict[str, str] = {}
    for name, data in all_module_yamls.items():
        if data.get("install_type") == "docker":
            compose_path = Path(modules_dir) / name / "docker-compose.base.yml"
            if pathlib.Path(compose_path).is_file():
                result[name] = compose_path

    logger.info("%s", f"[IMP:9][conftest][all_compose_files] Found {len(result)} docker-compose.base.yml file(s)")
    return result


@pytest.fixture(scope="session")
def all_networks(
    all_module_yamls: dict[str, dict],
    all_compose_files: dict[str, str],
) -> dict[str, set[str]]:
    """
    ## @purpose — Aggregate all declared networks across modules.
    ##            Networks are sourced from two origins:
    ##              1. module.yaml → config.network field
    ##              2. docker-compose.base.yml → top-level networks: key
    ## @io — ⇥ all_module_yamls, all_compose_files → ⎋ dict[str, set[str]]
    ## @complexity — O(N + C) where N = modules, C = compose YAML parse size
    ## @invariants
    ##   - Module names are added as set members (modules may share networks)
    ##   - Empty set means no networks declared for that network name from compose
    """
    networks: dict[str, set[str]] = {}

    # (1) Collect networks from module.yaml → config.network
    for name, data in all_module_yamls.items():
        config = data.get("config") or {}
        network: str | None = config.get("network") if isinstance(config, dict) else None
        if network:
            networks.setdefault(network, set()).add(name)

    # (2) Collect networks from docker-compose.base.yml → top-level networks:
    for name, compose_path in all_compose_files.items():
        try:
            with pathlib.Path(compose_path).open(encoding="utf-8") as f:
                compose_data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as exc:
            logger.info("%s", f"[IMP:4][conftest][all_networks] Failed to parse {compose_path}: {exc}")
            continue

        compose_networks = compose_data.get("networks", {}) if isinstance(compose_data, dict) else {}
        for net_name in compose_networks:
            networks.setdefault(net_name, set()).add(name)

    logger.info("%s", f"[IMP:9][conftest][all_networks] Aggregated {len(networks)} unique network(s)")
    return networks


# region DISCOVER_DOCKER_MODULES
## @purpose — Canonical function for discovering docker module names across the project.
##            Globs core/modules/*/docker-compose.base.yml and returns sorted names.
##            Replaces 5+ hardcoded DOCKER_MODULES lists across gate tests.
## @scope — Used by gate tests that need to iterate docker modules (compose, env, dockerignore).
## @invariants
##   - Pure function: no side effects, no caching
##   - Returns sorted list of directory names that contain docker-compose.base.yml
##   - Accepts optional modules_dir override (default: project_root + core/modules)
##   - Empty list if no docker-compose.base.yml found (defensive)
## @rationale — Knowledge dedup (§Step 1.11): 5 copies of DOCKER_MODULES had diverged
##              (minio missing in 4 of 5). Single source of truth via glob discovery.
##              New module with docker-compose.base.yml is auto-discovered — zero maintenance.
## @usecases — test_gate_compose_base_contract, test_gate_env_hostname_drift,
##             test_gate_env_shared_consistency, test_gate_dockerignore_symlink,
##             test_gate_module_yaml_contract
## @changes — 2026-07-16 | Created per T7 design decision: shared discovery replaces hardcoded lists


def discover_docker_modules(modules_dir: str | None = None) -> list[str]:
    """
    ## @purpose — Discover docker module names by globbing docker-compose.base.yml.
    ## @io — ⇥ modules_dir: str|None → ⎋ list[str]: sorted docker module names
    ## @complexity — O(N) where N = number of entries in modules_dir
    ## @invariants
    ##   - Returns sorted list (deterministic order for tests)
    ##   - Only dirs with actual docker-compose.base.yml are included
    ##   - platform-secrets (no compose file) is excluded automatically
    """
    if modules_dir is None:
        project_root = pathlib.Path(__file__).resolve().parent.parent.parent
        modules_dir = str(project_root / "core" / "modules")

    matches = sorted(Path(modules_dir).glob("*/docker-compose.base.yml"))
    result = [Path(m).parent.name for m in matches]
    logger.info("%s", f"[IMP:9][discover_docker_modules] Discovered {len(result)} docker modules: {result}")
    return result


# endregion DISCOVER_DOCKER_MODULES

# endregion STATIC_AUDIT_FIXTURES
