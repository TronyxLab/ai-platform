#!/usr/bin/env python3
"""Topological sort for docker module deploy order based on module.yaml depends_on."""
# GREP_SUMMARY: topo-sort, kahn, dag, deploy-order, module-yaml, depends-on, topological-sort
# STRUCTURE: ▶ glob module.yaml → yaml.safe_load each → filter install_type:docker → build DAG → Kahn's algorithm → ∋ groups JSON
# region MODULE_CONTRACT [DOMAIN(INFRA): bootstrap; CONCEPT(DAG): module-dependency-graph; TECH(PYTHON): argparse+yaml+json]
## @purpose  Compute parallel-deploy groups for docker modules via topological sort of module.yaml depends_on
## @scope    Reads all core/modules/*/module.yaml, filters to docker modules, builds dependency DAG,
##           applies Kahn's algorithm, outputs JSON groups for deploy-modules.sh to consume
## @input    --modules-dir path (required), --filter-names list (optional)
## @output    JSON: {"groups": [["a","b"], ["c"], ["d"]], "modules": {"name": {"install_type": "...", "severity": "..."}}}
## @links    USED_BY(core/internal/bootstrap/deploy-modules.sh), READS_DATA_FROM(core/modules/*/module.yaml)
## @invariants
##   - Only modules with install_type: docker are included in the DAG
##   - Dependencies referencing non-docker or absent modules are silently dropped
##   - Empty depends_on, null depends_on, and missing depends_on are equivalent to zero dependencies
##   - The output JSON groups preserve module names exactly as they appear in module.yaml name field
## @rationale Q: Why not implement this in bash? A: Kahn's algorithm with cycle detection requires
##   associative arrays, queues, and recursion — Python's yaml.safe_load + collections.deque
##   does it in ~40 lines. Python is already present on all production nodes (bootstrap dependency).
## @changes   LAST_CHANGE: 2026-07-12 — initial implementation for TASK-I4
## @modulemap
##   load_module_yamls [W:1] — glob and parse module.yaml files
##   filter_docker_modules [W:1] — filter to install_type: docker
##   extract_depends_on [W:1] — normalize depends_on to list
##   build_dag [W:2] — build adjacency list from module data
##   kahn_topological_sort [W:3] — Kahn's algorithm returning deploy groups
##   main [W:2] — CLI entry point with argparse
## @usecases
##   - deploy-modules.sh → _topo_sort.py → groups JSON → parallel deploy groups
##   - test_topo_sort.py → _topo_sort.py functions with tmp_path fixtures
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import sys
from collections import deque
from pathlib import Path
from typing import TypedDict, cast

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

from core.internal.shared.exceptions import ConfigValidationError


# region TYPEDEF_ModuleYaml
class ModuleYaml(TypedDict, total=False):
    """Одна запись module.yaml (W11-G3: YAML-граница вместо dict[str, Any]).

    ## @purpose — Типизированная граница yaml.safe_load для module.yaml.
    ## @invariants — total=False: ключи могут отсутствовать (get с дефолтами);
    ##               depends_on в норме list[str], malformed YAML — любой тип (isinstance-гард).
    ## @complexity — O(1) — декларация
    """

    name: str
    install_type: str
    depends_on: list[str]
    severity: str


# endregion TYPEDEF_ModuleYaml


# region FUNC_load_module_yamls
## @purpose  Glob and parse all module.yaml files under modules_dir/*/module.yaml
## @io       str (modules_dir path) → List[ModuleYaml] (parsed YAML contents)
## @complexity 2 — file I/O with sorted glob
def load_module_yamls(modules_dir: str) -> list[ModuleYaml]:
    modules_path = Path(modules_dir)
    yaml_files = sorted(modules_path.glob("*/module.yaml"))
    if not yaml_files:
        logger.warning("[IMP:5][load_module_yamls][scan] No module.yaml files found in %s", modules_dir)
        return []

    modules: list[ModuleYaml] = []
    for yf in yaml_files:
        with Path(yf).open(encoding="utf-8") as f:
            data = cast("ModuleYaml | None", yaml.safe_load(f))  # W11-G3: yaml.safe_load → Any; граница module.yaml
        if data is None:
            logger.warning("[IMP:5][load_module_yamls][skip] Empty YAML in %s, skipping", yf)
            continue
        module_name = data.get("name", yf.parent.name)
        modules.append(data)
        logger.info(
            "[IMP:7][load_module_yamls][%s] Loaded install_type=%s, depends_on=%s",
            module_name,
            data.get("install_type"),
            data.get("depends_on"),
        )
    logger.info("[IMP:9][load_module_yamls][count] Total module.yaml files loaded: %d", len(modules))
    return modules


# endregion FUNC_load_module_yamls


# region FUNC_filter_docker_modules
## @purpose  Filter modules list to only those with install_type: docker
## @io       List[ModuleYaml] → List[ModuleYaml]
## @complexity 1 — simple list comprehension
def filter_docker_modules(modules: list[ModuleYaml]) -> list[ModuleYaml]:
    filtered = [m for m in modules if m.get("install_type") == "docker"]
    logger.info(
        "[IMP:9][filter_docker_modules][filter] Docker modules: %d out of %d total",
        len(filtered),
        len(modules),
    )
    return filtered


# endregion FUNC_filter_docker_modules


# region FUNC_extract_depends_on
## @purpose  Normalize depends_on field: handle None, empty list, or list of strings
## @io       ModuleYaml (module) → List[str]
## @complexity 1 — type-checking branch
def extract_depends_on(module: ModuleYaml) -> list[str]:
    deps = module.get("depends_on")
    if deps is None:
        return []
    if isinstance(deps, list):  # pyright: ignore[reportUnnecessaryIsInstance] — runtime-гард: YAML может содержать depends_on любого типа (malformed)
        return [str(d) for d in deps]
    logger.warning(
        "[IMP:5][extract_depends_on][type] Unexpected depends_on type %s for module %s, treating as empty",
        type(deps).__name__,
        module.get("name", "?"),
    )
    return []


# endregion FUNC_extract_depends_on


# region FUNC_build_dag
## @purpose  Build adjacency-list DAG from module data, optionally filtered to specific names
## @io       modules (List[ModuleYaml]), filter_names (Optional[List[str]]) → Dict[str, List[str]]
## @complexity 2 — O(N) pass with set membership checks
## @invariants
##   - Dependencies referencing modules not in the filter set are silently dropped
##   - Module names without depends_on have an empty dependency list
def build_dag(modules: list[ModuleYaml], filter_names: list[str] | None = None) -> dict[str, list[str]]:
    if filter_names is not None:
        filter_set = set(filter_names)
        modules = [m for m in modules if m.get("name") in filter_set]
        logger.info("[IMP:7][build_dag][filter] Filtered to %d modules matching --filter-names", len(modules))

    module_names = {m.get("name") for m in modules if m.get("name")}
    dag: dict[str, list[str]] = {}

    for module in modules:
        name = module.get("name")
        if not name:
            logger.warning("[IMP:5][build_dag][skip] Module without name field, skipping")
            continue
        # Only keep dependencies that are in the docker module set (filtered)
        deps = [d for d in extract_depends_on(module) if d in module_names]
        dag[name] = deps

    logger.info("[IMP:9][build_dag][dag] Built DAG with %d nodes: %s", len(dag), list(dag.keys()))
    return dag


# endregion FUNC_build_dag


# region FUNC_kahn_topological_sort
## @purpose  Kahn's algorithm returning groups of parallel-deployable modules
## @io       Dict[str, List[str]] (DAG) → List[List[str]] (deploy groups)
## @complexity 3 — O(V + E) with queue-based in-degree counting
## @invariants
##   - Returns groups where all modules in the same group have no dependency on each other
##   - Groups are ordered: group[0] deploys first (no deps), group[1] after, etc.
##   - Raises ConfigValidationError if a cycle is detected, listing the stuck nodes
def kahn_topological_sort(dag: dict[str, list[str]]) -> list[list[str]]:
    logger.info("[IMP:7][kahn_topological_sort][start] Starting Kahn's algorithm on %d nodes", len(dag))

    # Build reverse adjacency list (children): for each node, list nodes that depend on it
    # This enables O(V+E) traversal instead of O(V²) by avoiding scanning all nodes per iteration
    children: dict[str, list[str]] = {node: [] for node in dag}
    for node, deps in dag.items():
        for dep in deps:
            if dep in children:
                children[dep].append(node)

    # Build in-degree: number of unresolved dependencies for each node
    in_degree: dict[str, int] = {}
    for node in dag:
        in_degree[node] = 0
    for node, deps in dag.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[node] = in_degree.get(node, 0) + 1

    # Seed queue with nodes that have no unresolved dependencies within the docker set
    # Note: a node may depend on a system module (filtered out) — those deps are already
    # removed by build_dag, so such nodes have in_degree 0.
    queue = deque([node for node, degree in in_degree.items() if degree == 0])
    logger.info("[IMP:7][kahn_topological_sort][init] %d nodes with in_degree=0 seeded", len(queue))

    groups: list[list[str]] = []
    visited_count = 0
    total_nodes = len(dag)

    while queue:
        current_group: list[str] = []
        # Process one full "wave": all nodes currently at in_degree 0
        for _ in range(len(queue)):
            node = queue.popleft()
            current_group.append(node)
            visited_count += 1

            # Decrease in-degree for all dependents using reverse adjacency list (O(1) per edge)
            for dependent in children[node]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        groups.append(current_group)
        logger.info(
            "[IMP:8][kahn_topological_sort][group] Group %d: %s (visited=%d/%d)",
            len(groups),
            current_group,
            visited_count,
            total_nodes,
        )

    if visited_count != total_nodes:
        cycle_nodes = sorted([n for n, d in in_degree.items() if d > 0])
        logger.error(
            "[IMP:10][kahn_topological_sort][cycle] Cycle detected! %d nodes unresolved: %s",
            len(cycle_nodes),
            cycle_nodes,
        )
        msg = f"Cycle detected in module dependency graph. Unresolved modules: {cycle_nodes}"
        raise ConfigValidationError(msg)

    logger.info("[IMP:9][kahn_topological_sort][done] %d groups produced: %s", len(groups), groups)
    return groups


# endregion FUNC_kahn_topological_sort


# region FUNC_main
## @purpose  CLI entry point: parse args, load modules, sort, print JSON
## @io       sys.argv → JSON stdout, int exit code
## @complexity 2
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Topological sort for docker module deploy order from module.yaml depends_on"
    )
    parser.add_argument(
        "--modules-dir",
        required=True,
        help="Path to core/modules/ directory containing */module.yaml files",
    )
    parser.add_argument(
        "--filter-names",
        nargs="*",
        default=None,
        help="Only consider these module names (space-separated). Default: all docker modules.",
    )

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3)."""

        def __init__(self) -> None:
            super().__init__()
            self.modules_dir: str
            self.filter_names: list[str] | None

    args = parser.parse_args(namespace=_CliArgs())

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    logger.info("[IMP:9][main][start] Topological sort from %s", args.modules_dir)

    all_modules = load_module_yamls(args.modules_dir)
    docker_modules = filter_docker_modules(all_modules)

    if not docker_modules:
        logger.warning("[IMP:5][main][empty] No docker modules found — returning empty groups")
        print(json.dumps({"groups": []}))
        return 0

    dag = build_dag(docker_modules, filter_names=args.filter_names)

    if not dag:
        logger.warning("[IMP:5][main][empty_dag] No nodes in DAG — returning empty groups")
        print(json.dumps({"groups": []}))
        return 0

    groups = kahn_topological_sort(dag)

    # ── S10: Build enriched modules dict from ALL loaded modules ──
    # Includes install_type and severity so deploy-modules.sh can avoid
    # separate detect_install_type() / _get_module_severity() calls.
    modules_info: dict[str, dict[str, str]] = {}
    for m in all_modules:
        name = m.get("name", "")
        if name:
            modules_info[name] = {
                "install_type": m.get("install_type", "unknown"),
                "severity": m.get("severity", "warn"),
            }
    logger.info(
        "[IMP:9][main][enriched] Enriched modules dict built: %d modules",
        len(modules_info),
    )

    result = {"groups": groups, "modules": modules_info}
    print(json.dumps(result))

    logger.info("[IMP:9][main][done] Output: %s", json.dumps(result))
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
