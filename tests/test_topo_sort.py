#!/usr/bin/env python3
# GREP_SUMMARY: topo-sort, unit-test, kahn, dag, cycle-detection, module-yaml, depends-on
# STRUCTURE: ▶ sys.path+tmp_path → write module.yaml mocks → _topo_sort.load_module_yamls → build_dag → kahn_topological_sort → ◇ assert groups → ⊕ LDD trajectory → ⎋ IMP:9 check
# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(TOPO-SORT):4; TECH(PYTEST):2]
## @purpose  Unit tests for core/internal/bootstrap/_topo_sort.py topological sort
## @scope    Tests linear chains, diamond dependencies, cycle detection, no-deps modules,
##           and --filter-names subset restriction — all using tmp_path mock module.yaml files
## @invariants
##   - Each test creates its own tmp_path module directory with specific dependency topology
##   - Tests call _topo_sort functions directly (native imports, no subprocess)
##   - LDD trajectory printed via caplog at IMP:7-10 levels
##   - Each successful scenario asserts at least one IMP:9 log present
## @rationale Unit tests ensure the topological sorting logic is correct for common DAG patterns
##           before it determines production deploy order. Cycle detection prevents infinite loops.
## @changes LAST_CHANGE: 2026-07-12 — TASK-I4: initial test creation
## @modulemap
##   _setup_module_yaml [W:1] — helper: write a module.yaml file with given fields
##   test_topo_sort_linear [W:2] — a->b->c linear chain -> 3 groups
##   test_topo_sort_parallel_groups [W:2] — diamond dep -> 3 groups with parallelism
##   test_topo_sort_cycle_detection [W:2] — a->b->a cycle -> RuntimeError
##   test_topo_sort_no_deps [W:1] — modules without depends_on -> single group
##   test_topo_sort_filter_names [W:2] — --filter-names restricts to subset
## @usecases
##   - CI: verifies that topological sort correctly orders dependencies
##   - Refactoring: ensures changes to _topo_sort.py don't break deploy ordering
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

import pytest
import yaml

# Add core/internal/bootstrap to sys.path to import _topo_sort
_BOOTSTRAP_DIR = Path(__file__).resolve().parent.parent / "core" / "internal" / "bootstrap"
if str(_BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_DIR))

import _topo_sort

logger = logging.getLogger("test_topo_sort")


# region FUNC__setup_module_yaml
## @purpose  Helper: write a module.yaml file under modules_dir/<name\>/module.yaml
## @io       modules_dir (str), name (str), install_type (str), depends_on (list[str]|None) -> Path
## @complexity 1
def _setup_module_yaml(
    modules_dir: str,
    name: str,
    install_type: str = "docker",
    depends_on: list | None = None,
) -> Path:
    module_path = Path(modules_dir) / name
    module_path.mkdir(parents=True, exist_ok=True)
    yaml_path = module_path / "module.yaml"

    data: dict = {
        "name": name,
        "version": "0.1.0",
        "install_type": install_type,
        "description": f"Test module {name}",
    }
    if depends_on is not None:
        data["depends_on"] = depends_on

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)

    return yaml_path


# endregion FUNC__setup_module_yaml


# region FUNC_print_ldd_trajectory
## @purpose  Print LDD trajectory from caplog and assert IMP:9 found
## @io       caplog -> None, raises AssertionError if no IMP:9 log
## @complexity 1
def _assert_ldd_trajectory(caplog) -> None:
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_print_ldd_trajectory


# region FUNC_test_topo_sort_linear
## @purpose  Linear dependency chain: a -> b -> c -> correct 3-group order
## @io       tmp_path + caplog -> assert groups == [["c"], ["b"], ["a"]]
## @complexity 2
## 🧪 TRAP[TEST] · REGRESSION(topological-order) · SCENARIO(linear-chain) · LAST_FAIL(no failures) · REMOVE_IF(algorithm changed from Kahn's)
def test_topo_sort_linear(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)

    modules_dir = str(tmp_path / "modules")
    # a depends on b, b depends on c -> groups: [["c"], ["b"], ["a"]]
    _setup_module_yaml(modules_dir, "a", depends_on=["b"])
    _setup_module_yaml(modules_dir, "b", depends_on=["c"])
    _setup_module_yaml(modules_dir, "c", depends_on=[])

    modules = _topo_sort.load_module_yamls(modules_dir)
    docker_mods = _topo_sort.filter_docker_modules(modules)
    dag = _topo_sort.build_dag(docker_mods)
    groups = _topo_sort.kahn_topological_sort(dag)

    # Verify order: c (no deps) -> b (depends on c) -> a (depends on b)
    assert len(groups) == 3, f"Expected 3 groups, got {len(groups)}: {groups}"
    assert groups[0] == ["c"], f"Group 0 should be ['c'], got {groups[0]}"
    assert groups[1] == ["b"], f"Group 1 should be ['b'], got {groups[1]}"
    assert groups[2] == ["a"], f"Group 2 should be ['a'], got {groups[2]}"

    _assert_ldd_trajectory(caplog)


# endregion FUNC_test_topo_sort_linear


# region FUNC_test_topo_sort_parallel_groups
## @purpose  Diamond dependency: a->b, a->c, b->d, c->d -> groups allow parallelism
## @io       tmp_path + caplog -> assert groups == [["d"], ["b", "c"], ["a"]]
## @complexity 2
## 🧪 TRAP[TEST] · REGRESSION(parallel-groups) · SCENARIO(diamond-dependency) · LAST_FAIL(no failures) · REMOVE_IF(algorithm changed from Kahn's)
def test_topo_sort_parallel_groups(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)

    modules_dir = str(tmp_path / "modules")
    # Diamond: a depends on b,c; b depends on d; c depends on d
    # groups: [["d"], ["b", "c"], ["a"]]
    _setup_module_yaml(modules_dir, "a", depends_on=["b", "c"])
    _setup_module_yaml(modules_dir, "b", depends_on=["d"])
    _setup_module_yaml(modules_dir, "c", depends_on=["d"])
    _setup_module_yaml(modules_dir, "d", depends_on=[])

    modules = _topo_sort.load_module_yamls(modules_dir)
    docker_mods = _topo_sort.filter_docker_modules(modules)
    dag = _topo_sort.build_dag(docker_mods)
    groups = _topo_sort.kahn_topological_sort(dag)

    # Verify: 3 groups, group 1 has both b and c (parallel candidate)
    assert len(groups) == 3, f"Expected 3 groups, got {len(groups)}: {groups}"
    assert groups[0] == ["d"], f"Group 0 should be ['d'], got {groups[0]}"
    assert sorted(groups[1]) == ["b", "c"], f"Group 1 should be ['b', 'c'], got {groups[1]}"
    assert groups[2] == ["a"], f"Group 2 should be ['a'], got {groups[2]}"

    _assert_ldd_trajectory(caplog)


# endregion FUNC_test_topo_sort_parallel_groups


# region FUNC_test_topo_sort_cycle_detection
## @purpose  Cyclic graph: a->b, b->c, c->a -> RuntimeError with cycle info
## @io       tmp_path + caplog -> assert RuntimeError raised
## @complexity 2
## 🧪 TRAP[TEST] · REGRESSION(cycle-detection) · SCENARIO(cyclic-graph) · LAST_FAIL(no failures) · REMOVE_IF(cycle detection removed)
def test_topo_sort_cycle_detection(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)

    modules_dir = str(tmp_path / "modules")
    # a->b, b->c, c->a -> cycle
    _setup_module_yaml(modules_dir, "a", depends_on=["b"])
    _setup_module_yaml(modules_dir, "b", depends_on=["c"])
    _setup_module_yaml(modules_dir, "c", depends_on=["a"])

    modules = _topo_sort.load_module_yamls(modules_dir)
    docker_mods = _topo_sort.filter_docker_modules(modules)
    dag = _topo_sort.build_dag(docker_mods)

    with pytest.raises(RuntimeError) as excinfo:
        _topo_sort.kahn_topological_sort(dag)

    error_msg = str(excinfo.value)
    assert "Cycle detected" in error_msg, f"Expected 'Cycle detected' in error, got: {error_msg}"

    _assert_ldd_trajectory(caplog)


# endregion FUNC_test_topo_sort_cycle_detection


# region FUNC_test_topo_sort_no_deps
## @purpose  Three modules with empty/reduced depends_on -> single group with all modules
## @io       tmp_path + caplog -> assert all 3 names in groups[0]
## @complexity 1
## 🧪 TRAP[TEST] · REGRESSION(no-deps) · SCENARIO(all-independent) · LAST_FAIL(no failures) · REMOVE_IF(algorithm changed)
def test_topo_sort_no_deps(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)

    modules_dir = str(tmp_path / "modules")
    # All modules are independent — single deploy group
    _setup_module_yaml(modules_dir, "alpha", depends_on=[])
    _setup_module_yaml(modules_dir, "beta", depends_on=None)
    _setup_module_yaml(modules_dir, "gamma")  # no depends_on field at all

    modules = _topo_sort.load_module_yamls(modules_dir)
    docker_mods = _topo_sort.filter_docker_modules(modules)
    dag = _topo_sort.build_dag(docker_mods)
    groups = _topo_sort.kahn_topological_sort(dag)

    # All independent modules go in one group
    assert len(groups) >= 1, f"Expected at least 1 group, got {len(groups)}: {groups}"
    all_in_group0 = set(groups[0])
    for name in ("alpha", "beta", "gamma"):
        assert name in all_in_group0, f"Expected {name} in first group, got {groups[0]}"

    _assert_ldd_trajectory(caplog)


# endregion FUNC_test_topo_sort_no_deps


# region FUNC_test_topo_sort_filter_names
## @purpose  --filter-names restricts the DAG to a subset of docker modules
## @io       tmp_path + caplog -> assert filtered DAG has only the named modules
## @complexity 2
## 🧪 TRAP[TEST] · REGRESSION(filter-names) · SCENARIO(subset-filter) · LAST_FAIL(no failures) · REMOVE_IF(filter-names removed)
def test_topo_sort_filter_names(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)

    modules_dir = str(tmp_path / "modules")
    # a depends on b, b has no deps, c has no deps
    _setup_module_yaml(modules_dir, "a", depends_on=["b"])
    _setup_module_yaml(modules_dir, "b", depends_on=[])
    _setup_module_yaml(modules_dir, "c", depends_on=[])

    modules = _topo_sort.load_module_yamls(modules_dir)
    docker_mods = _topo_sort.filter_docker_modules(modules)

    # Filter: only a and c (exclude b)
    dag = _topo_sort.build_dag(docker_mods, filter_names=["a", "c"])

    assert "a" in dag, "a should be in filtered DAG"
    assert "c" in dag, "c should be in filtered DAG"
    assert "b" not in dag, "b should NOT be in filtered DAG"
    # a's dependency on b is dropped (b not in filter set)
    assert dag["a"] == [], "a's dep on b should be dropped since b is filtered out"
    assert dag["c"] == [], "c should have no deps"

    groups = _topo_sort.kahn_topological_sort(dag)
    assert len(groups) == 1, f"Expected 1 group, got {len(groups)}: {groups}"
    assert sorted(groups[0]) == ["a", "c"], f"Group 0 should contain ['a', 'c'], got {groups[0]}"

    _assert_ldd_trajectory(caplog)


# endregion FUNC_test_topo_sort_filter_names
