#!/usr/bin/env python3
"""Shared helpers for deploy-modules audit tests (DevPlan 139 W3 T6 split).

# GREP_SUMMARY: deploy-modules-audit, shared-helpers, extract-python-func, ldd-trajectory, path-constants
# STRUCTURE: ┌path constants (deploy-modules/state-machine/phases/orchestrator)┐ → ◇ _extract_python_func → ◇ _assert_ldd_trajectory → ◇ _setup_module_yaml → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Общие хелперы трёх test_deploy_modules_* файлов (DevPlan 139 W3 T6 — сплит
##           62KB-монолита по подобластям: фасады / пакеты / env). Устраняет дублирование
##           путей, _extract_python_func, _assert_ldd_trajectory, _setup_module_yaml.
## @scope    Только для тестов домена deploy-modules. Не содержит тестов (helpers/).
## @invariants
##   - _extract_python_func бросает ValueError если функция отсутствует (fail-verbose)
##   - _assert_ldd_trajectory требует ≥1 IMP:9 лог (LDD инвариант 3)
##   - Пути — канон repo_root()-relative (Zero Hardcode Rule)
## @rationale 3 файла-сабдомена используют одни и те же константы/хелперы — вынос в helpers
##            предотвращает дрейф (дублирование путей = точки расхождения).
## @changes  2026-08-05 | DevPlan 139 W3 T6 — создан (сплит test_deploy_modules.py)
# endregion MODULE_CONTRACT

import json
import sys
from pathlib import Path

import yaml

from tests.helpers.gate_helpers import repo_root

# ── Path constants (канон deploy-modules домена) ────────────────────────────
DEPLOY_MODULES_SH = repo_root() / "core" / "internal" / "bootstrap" / "deploy-modules.sh"
NODE_LIFECYCLE_SH = repo_root() / "core" / "internal" / "bootstrap" / "node-lifecycle.sh"
BOOTSTRAP_DIR = repo_root() / "core" / "internal" / "bootstrap"
STATE_MACHINE_PY = repo_root() / "core" / "internal" / "bootstrap" / "lifecycle" / "state_machine.py"
# DevPlan 119 E3: phases.py → phases/ пакет; deploy-фазы (φ8/φ12) живут в phases/docker.py.
PHASES_PY = repo_root() / "core" / "internal" / "bootstrap" / "lifecycle" / "phases" / "docker.py"
DEPLOY_PYTHON_DIR = repo_root() / "core" / "internal" / "bootstrap" / "deploy"
# DevPlan 100: routing + deploy delegation moved to deploy_orchestrator.py.
ORCHESTRATOR_PY = DEPLOY_PYTHON_DIR / "deploy_orchestrator.py"

# Add bootstrap dir to sys.path for topo_sort import
if str(BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(BOOTSTRAP_DIR))


def _extract_python_func(filepath: Path, func_name: str) -> str:
    """Extract a Python function definition from a file for static audit.

    ## @purpose  Verify a Python function exists in a given module file (W4-E1 extraction).
    ## @io       ⇥ filepath (Path), func_name (str) → ⎋ str (file content, ValueError if missing)
    ## @complexity 1 — linear scan for `def func_name(`
    """
    content = filepath.read_text()
    if f"def {func_name}(" in content:
        return content
    raise ValueError(f"Function '{func_name}' not found in {filepath}")


def _assert_ldd_trajectory(caplog) -> None:
    """Print LDD trajectory from caplog and assert IMP:9 found.

    ## @purpose  LDD инвариант 3: каждый тест — IMP:9-траектория.
    ## @io       caplog → None, raises AssertionError if no IMP:9 log
    ## @complexity 1
    """
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if hasattr(record, "message") and "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found in test trajectory"


def _setup_module_yaml(
    tmp_path: Path,
    name: str,
    install_type: str = "docker",
    severity: str = "warn",
    depends_on: list | None = None,
) -> Path:
    """Write a module.yaml file with given fields under tmp_path/<name>/.

    ## @purpose  Helper for topo_sort enrichment tests (S10).
    ## @io       tmp_path (Path), name (str), install_type (str), severity (str),
    ##           depends_on (list|None) → Path
    ## @complexity 1
    """
    module_path = tmp_path / name
    module_path.mkdir(parents=True, exist_ok=True)
    yaml_path = module_path / "module.yaml"

    data: dict = {
        "name": name,
        "version": "0.1.0",
        "install_type": install_type,
        "severity": severity,
        "description": f"Test module {name}",
    }
    if depends_on is not None:
        data["depends_on"] = depends_on

    with open(yaml_path, "w") as fh:
        yaml.dump(data, fh, default_flow_style=False)

    return yaml_path


def _enrich_modules_output(tmp_path: Path, module_names: list[str]) -> dict:
    """Build enriched topo_sort output for a tmp_path tree of module.yaml files.

    ## @purpose  S10 enrichment: load module.yamls → filter docker → DAG → Kahn groups →
    ##            enrich modules dict (install_type + severity) → JSON round-trip.
    ## @io       ⇥ tmp_path (Path), module_names (list[str]) → ⎋ dict {groups, modules}
    ## @complexity O(N) — module loading + topo sort
    """
    import topo_sort

    all_modules = topo_sort.load_module_yamls(str(tmp_path))
    docker_modules = topo_sort.filter_docker_modules(all_modules)
    dag = topo_sort.build_dag(docker_modules)
    groups = topo_sort.kahn_topological_sort(dag)

    modules_info = {}
    for m in all_modules:
        name = m.get("name", "")
        if name:
            modules_info[name] = {
                "install_type": m.get("install_type", "unknown"),
                "severity": m.get("severity", "warn"),
            }

    return json.loads(json.dumps({"groups": groups, "modules": modules_info}))
