# GREP_SUMMARY: predeploy, node-yaml, projects, compose-files, platform-env-networks, platform-ports, fixtures
# STRUCTURE: ┌_find_node_yaml┐ → ◇ node_yaml_projects → ◇ project_compose_files → ◇ platform_networks_list → ◇ platform_port_mappings_dict
# region MODULE_CONTRACT
## @purpose  Fixtures for predeploy gate tests — load node.yaml projects list,
##           project docker-compose.yml paths, platform networks, and port mappings.
## @scope    Consumed by test_predeploy_gate.py T1-T5. Project directory scanning is
##           env-aware: PROJECTS_DIR env var (default /opt/projects/).
## @invariants
##   - node_yaml_projects: parses node.yaml from node-configs/<node>/node.yaml or NODE_YAML env
##   - project_compose_files: yields paths to existing compose files in PROJECTS_DIR/<name>/
##   - Both gracefully return empty structures when files are absent (no crash)
##   - All fixtures are session-scoped for performance
## @rationale Predeploy gate tests need access to project configs before deploy.
##            These fixtures provide a unified way to discover projects and their
##            compose files, with graceful degradation when not running on VPS.
# endregion MODULE_CONTRACT

import logging
import os
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)


# region HELPERS


def _find_node_yaml() -> str | None:
    """Locate node.yaml from standard locations.

    ## @purpose — Find a node.yaml for project discovery. Priority order:
    ##            1. NODE_YAML env var
    ##            2. node-configs/<first-dir>/node.yaml
    ##            3. <PLATFORM_ROOT>/node.yaml
    ## @io — ⎋ str | None (absolute path to node.yaml if found)
    ## @complexity — O(D) where D = number of node-config subdirectories
    """
    # 1. Check NODE_YAML env var
    env_path = os.environ.get("NODE_YAML")
    if env_path:
        abs_env = os.path.abspath(env_path)
        if os.path.isfile(abs_env):
            logger.info("[IMP:8][_find_node_yaml] Using NODE_YAML: %s", abs_env)
            return abs_env

    # Find platform root
    platform_root = os.environ.get(
        "PLATFORM_ROOT",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    )
    _pr = Path(platform_root)

    # 2. Scan node-configs/ for any node.yaml
    node_configs = _pr / "node-configs"
    if node_configs.is_dir():
        for node_dir in sorted(node_configs.iterdir()):
            if node_dir.is_dir():
                ny = node_dir / "node.yaml"
                if ny.is_file():
                    logger.info("[IMP:8][_find_node_yaml] Found: %s", ny)
                    return str(ny)

    # 3. Root-level node.yaml
    root_ny = _pr / "node.yaml"
    if root_ny.is_file():
        logger.info("[IMP:8][_find_node_yaml] Found root-level: %s", root_ny)
        return str(root_ny)

    logger.info("[IMP:7][_find_node_yaml] node.yaml not found in any standard location")
    return None


def _parse_projects_from_node_yaml(node_yaml_path: str) -> list[dict]:
    """Parse the projects list from a node.yaml file.

    ## @purpose — Extract the projects list safely from node.yaml.
    ## @io — ⇥ node_yaml_path: str → ⎋ list[dict] (each with at least 'name' key)
    ## @complexity — O(N) where N = number of projects
    ## @invariants
    ##   - Returns empty list on parse error or missing projects key
    ##   - Filters out entries without a 'name' field
    ##   - Does NOT raise on malformed YAML — logs warning at IMP:4
    """
    try:
        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("[IMP:4][_parse_projects] Failed to parse %s: %s", node_yaml_path, exc)
        return []

    if not isinstance(data, dict):
        logger.warning("[IMP:4][_parse_projects] node.yaml root is not a dict (type=%s)", type(data).__name__)
        return []

    projects_raw = data.get("projects", [])
    if not isinstance(projects_raw, list):
        logger.warning("[IMP:4][_parse_projects] 'projects' field is not a list (type=%s)", type(projects_raw).__name__)
        return []

    validated: list[dict] = []
    for p in projects_raw:
        if isinstance(p, dict) and "name" in p:
            validated.append(p)
        else:
            logger.warning("[IMP:4][_parse_projects] Skipping invalid project entry: %s", p)

    return validated


def _load_platform_env_networks() -> list[str]:
    """Load network names from platform-env.yaml.

    ## @purpose — Return canonical platform network names for T3/T4 validation
    ## @io — ⎋ list[str] (empty if platform-env.yaml missing or parse error)
    ## @complexity — O(N) where N = number of networks
    """
    platform_root = os.environ.get(
        "PLATFORM_ROOT",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    )
    yaml_path = os.path.join(platform_root, "platform-env.yaml")
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("[IMP:4][_load_platform_env_networks] Failed to parse %s: %s", yaml_path, exc)
        return []

    if not isinstance(data, dict):
        return []

    networks_raw = data.get("networks", [])
    if not isinstance(networks_raw, list):
        return []

    return [n["name"] for n in networks_raw if isinstance(n, dict) and "name" in n]


def _load_platform_env_port_mappings() -> dict[str, int]:
    """Load port_mappings from platform-env.yaml.

    ## @purpose — Return canonical platform port dict for T2 port conflict detection
    ## @io — ⎋ dict[str, int] (empty if platform-env.yaml missing or parse error)
    ## @complexity — O(N) where N = number of port mappings
    """
    platform_root = os.environ.get(
        "PLATFORM_ROOT",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    )
    yaml_path = os.path.join(platform_root, "platform-env.yaml")
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("[IMP:4][_load_platform_env_port_mappings] Failed to parse %s: %s", yaml_path, exc)
        return {}

    if not isinstance(data, dict):
        return {}

    mappings = data.get("port_mappings", {})
    if not isinstance(mappings, dict):
        return {}

    return {k: int(v) for k, v in mappings.items()}


# endregion HELPERS


# region FIXTURES


@pytest.fixture(scope="session")
def node_yaml_projects() -> list[dict]:
    """Parse node.yaml and return list of project dicts.

    ## @purpose — Provide parsed project list from node.yaml for T1-T5 predeploy validation.
    ##            Each project dict has at least 'name', possibly 'domain', 'repo', 'branch'.
    ## @io — ⎋ list[dict] (empty list if node.yaml not found or no projects declared)
    ## @complexity — O(N) where N = number of projects
    ## @invariants
    ##   - Returns empty list if node.yaml not found (graceful degradation)
    ##   - Each project dict has at minimum {'name': str}
    ##   - Does NOT raise on file-not-found — logs at IMP:7
    """
    node_yaml_path = _find_node_yaml()
    if not node_yaml_path:
        logger.info("[IMP:7][node_yaml_projects] node.yaml not found — returning empty projects list")
        return []

    projects = _parse_projects_from_node_yaml(node_yaml_path)
    logger.info(
        "[IMP:9][node_yaml_projects] Found %d project(s) in %s: %s",
        len(projects),
        node_yaml_path,
        [p.get("name") for p in projects],
    )
    return projects


@pytest.fixture(scope="session")
def project_compose_files(node_yaml_projects: list[dict]) -> list[Path]:
    """Scan project directories for docker-compose.yml files.

    ## @purpose — Provide paths to existing project compose files for T1-T4 validation.
    ##            Scans PROJECTS_DIR/<project.name>/docker-compose.yml for each project.
    ## @io — ⎋ list[Path] — only existing files; empty list if no project dirs found
    ## @complexity — O(N) where N = number of projects in node.yaml
    ## @invariants
    ##   - PROJECTS_DIR env var overrides default /opt/projects/
    ##   - Also checks tests/test_data/projects/<name>/ for local development
    ##   - Only returns paths that actually exist on disk
    ##   - Returns empty list when no projects or no compose files found (graceful degradation)
    ## @rationale Local dev has no /opt/projects/, so we fall back to test_data/projects/
    """
    projects_dir = os.environ.get("PROJECTS_DIR", "/opt/projects")
    test_data_dir = os.environ.get(
        "TEST_PROJECTS_DIR",
        os.path.join(
            os.environ.get("PLATFORM_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))),
            "tests",
            "test_data",
            "projects",
        ),
    )

    results: list[Path] = []
    for proj in node_yaml_projects:
        name = proj.get("name", "")
        if not name:
            continue

        # Primary: PROJECTS_DIR/<name>/docker-compose.yml
        primary = Path(projects_dir) / name / "docker-compose.yml"
        if primary.is_file():
            results.append(primary)
            logger.debug("[IMP:8][project_compose_files] Found (primary): %s", primary)
            continue

        # Fallback: tests/test_data/projects/<name>/docker-compose.yml
        fallback = Path(test_data_dir) / name / "docker-compose.yml"
        if fallback.is_file():
            results.append(fallback)
            logger.debug("[IMP:8][project_compose_files] Found (test_data): %s", fallback)

    logger.info(
        "[IMP:9][project_compose_files] Found %d project compose file(s) in %s",
        len(results),
        projects_dir,
    )
    return results


@pytest.fixture(scope="session")
def platform_networks_list() -> list[str]:
    """Return list of platform network names from platform-env.yaml.

    ## @purpose — Provide canonical platform network names for T3 (external networks exist)
    ##            and T4 (proxy-net required) validation.
    ## @io — ⎋ list[str] (empty if platform-env.yaml not found)
    ## @complexity — O(N) where N = number of networks in platform-env.yaml
    """
    result = _load_platform_env_networks()
    logger.info("[IMP:9][platform_networks_list] Loaded %d platform network(s)", len(result))
    return result


@pytest.fixture(scope="session")
def platform_port_mappings_dict() -> dict[str, int]:
    """Return port_mappings dict from platform-env.yaml.

    ## @purpose — Provide canonical platform port dict for T2 port conflict detection.
    ## @io — ⎋ dict[str, int] (empty if platform-env.yaml not found)
    ## @complexity — O(N) where N = number of port mappings
    """
    result = _load_platform_env_port_mappings()
    logger.info("[IMP:9][platform_port_mappings_dict] Loaded %d port mapping(s)", len(result))
    return result


# endregion FIXTURES
