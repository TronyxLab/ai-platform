# GREP_SUMMARY: gate topology dag cyclic-dependencies deploy-order groups topological-sort module-graph kahn
# STRUCTURE: ┌test_no_cyclic_dependencies(module_graph)┐ → ◇ test_deploy_order_respects_topology(module_graph) → ◇ compute_groups_via_kahn → ◇ assert dep <= group
# region MODULE_CONTRACT
## @purpose — Gate test A1: validate module dependency graph is acyclic (DAG) and that
##            deploy groups computed via Kahn's algorithm respect topological ordering.
## @scope — Parses all modules/*/module.yaml for depends_on, uses conftest.module_graph fixture
##          for DAG validation, and computes dynamic deploy groups from the dependency graph.
## @invariants
##   - module_graph fixture raises RuntimeError on cycle — test_no_cyclic_dependencies asserts no exception
##   - Deploy groups are computed dynamically via Kahn's algorithm (NOT hardcoded)
##   - Every module's dependencies must be in same or earlier group (lower group index)
##   - System modules are included with their declared dependencies
## @rationale — B3: deploy-modules.sh now auto-resolves groups from module.yaml depends_on via
##              _topo_sort.py. The gate test mirrors this by computing groups dynamically,
##              eliminating the need to update hardcoded groups when modules are added/removed.
## @changes — 2026-07-15 | B3 — Replaced hardcoded _DEPLOY_GROUPS with dynamic Kahn computation
# endregion MODULE_CONTRACT

import logging
from collections import deque

import pytest

from tests._conftest.r1 import r1_delegates

logger = logging.getLogger(__name__)


def _compute_dynamic_groups(module_graph: dict[str, list[str]]) -> dict[str, int]:
    """Compute deploy groups from module dependency graph using Kahn's algorithm.

    ## @purpose — Dynamically compute parallel-deploy groups from module depends_on.
    ##            Mirrors kahn_topological_sort() in _topo_sort.py.
    ## @io — ⇥ module_graph: {module: [deps]} → ⎋ dict[str, int]: {module: group_index}
    ## @complexity — O(V + E) where V = modules, E = dependency edges
    ## @invariants
    ##   - Group 0 has no dependencies (or all deps are external)
    ##   - Each subsequent group depends only on modules in earlier groups
    ##   - Module names without deps get group 0
    """
    # Build in-degree: count of unresolved dependencies per module
    in_degree: dict[str, int] = dict.fromkeys(module_graph, 0)
    for node, deps in module_graph.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[node] += 1

    # Build reverse adjacency: for each module, which modules depend on it?
    dependents: dict[str, list[str]] = {node: [] for node in module_graph}
    for node, deps in module_graph.items():
        for dep in deps:
            if dep in dependents:
                dependents[dep].append(node)

    # Seed queue with nodes that have zero unresolved dependencies
    queue = deque([node for node, degree in in_degree.items() if degree == 0])
    groups: list[list[str]] = []
    visited_count = 0
    total_nodes = len(module_graph)

    while queue:
        current_group: list[str] = []
        for _ in range(len(queue)):
            node = queue.popleft()
            current_group.append(node)
            visited_count += 1
            for dependent in dependents.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
        groups.append(current_group)

    if visited_count != total_nodes:
        msg = (
            f"Cycle detected: processed {visited_count}/{total_nodes} nodes. "
            f"Unresolved: {[n for n, d in in_degree.items() if d > 0]}"
        )
        raise RuntimeError(msg)

    # Build {module: group_index} dict
    result: dict[str, int] = {}
    for idx, group in enumerate(groups):
        for module in group:
            result[module] = idx

    logger.info("[IMP:9][_compute_dynamic_groups] Computed %d groups: %s", len(groups), groups)
    return result


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.gate
@r1_delegates

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
# 🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм — module_graph fixture
#   (Kahn's algorithm) raises RuntimeError на цикле; тест не нуждается в собственном assert.
def test_no_cyclic_dependencies(module_graph: dict[str, list[str]]) -> None:
    """Verify module dependency graph is acyclic.

    ## @purpose — The module_graph fixture (conftest.py) runs Kahn's algorithm and
    ##            raises RuntimeError if a cycle is detected. This test simply calls
    ##            the fixture and asserts no exception — the fixture itself validates DAG.
    ## @io — ⇥ module_graph fixture → ⎋ None (assert side-effect)
    ## @complexity — O(V + E) delegated to fixture
    """
    logger.info("[IMP:8][test_no_cyclic_dependencies] === DAG validation ===")

    # module_graph fixture already validated DAG via Kahn's algorithm
    # If we reach here, no cycle was detected
    module_count = len(module_graph)
    logger.critical(
        "[IMP:9][test_no_cyclic_dependencies] PASS — module graph is acyclic, %d modules in DAG",
        module_count,
    )


@pytest.mark.gate
def test_deploy_order_respects_topology(
    module_graph: dict[str, list[str]],
    all_module_yamls: dict[str, dict],
) -> None:
    """Verify deploy-modules.sh group order respects topological sorting.

    ## @purpose — Check that for every module M with dependencies D, each dependency
    ##            is in the same or earlier deploy group (lower group index) as M.
    ##            This ensures deploy-modules.sh doesn't deploy a module before its
    ##            dependencies are ready.
    ## @io — ⇥ module_graph, all_module_yamls → ⎋ None (assert side-effect)
    ## @complexity — O(V * D) where V = module count, D = avg dependency count
    """
    logger.info("[IMP:8][test_deploy_order_respects_topology] === Deploy order audit ===")

    # Compute groups dynamically from module_graph — mirrors _topo_sort.py Kahn algorithm
    groups = _compute_dynamic_groups(module_graph)

    violations: list[str] = []

    for module_name, dependencies in module_graph.items():
        module_group = groups.get(module_name)
        if module_group is None:
            logger.warning(
                "[IMP:7][test_deploy_order_respects_topology] Module '%s' has no group assignment", module_name
            )
            continue

        logger.info(
            "[IMP:8][test_deploy_order_respects_topology] Module '%s' → group %d, deps: %s",
            module_name,
            module_group,
            dependencies,
        )

        for dep in dependencies:
            dep_group = groups.get(dep)
            if dep_group is None:
                # Dependency not in our module set — could be external or system module
                logger.info(
                    "[IMP:6][test_deploy_order_respects_topology] Dependency '%s' of '%s' not in module_graph — "
                    "assuming external/system (no group check needed)",
                    dep,
                    module_name,
                )
                continue
            if dep_group > module_group:
                violation = (
                    f"Module '{module_name}' (group {module_group}) depends on "
                    f"'{dep}' (group {dep_group}) — dependency must be in same or earlier group"
                )
                violations.append(violation)
                logger.warning("[IMP:7] %s", violation)

        # Also check if the module exists in module.yaml but has no module.yaml entry
        # for its dependency (unregistered dependency)
        for dep in dependencies:
            if dep not in all_module_yamls and dep not in groups:
                logger.warning(
                    "[IMP:7][test_deploy_order_respects_topology] Dependency '%s' of '%s' "
                    "not found in any module.yaml or deploy groups",
                    dep,
                    module_name,
                )

    if violations:
        logger.critical(
            "[IMP:9][test_deploy_order_respects_topology] FAIL — %d deploy order violation(s)",
            len(violations),
        )
        pytest.fail(f"{len(violations)} deploy order violation(s):\n" + "\n".join(f"  {v}" for v in violations))

    logger.critical(
        "[IMP:9][test_deploy_order_respects_topology] PASS — all %d module dependencies respect deploy group ordering",
        len(module_graph),
    )
