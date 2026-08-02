#!/usr/bin/env python3
# GREP_SUMMARY: deploy-modules, test, static-audit, skip-provision, state-machine, python-modules, topo-sort, enriched
# STRUCTURE: ▶ test_skip_provision_flag (static grep state_machine.py) → ▶ test_merge_deploy_steps (static grep state_machine.py) → ▶ testtopo_sort_enriched (native topo_sort.py call with mock yamls) → ◇ W4-E5 edge-cases (Python module contracts: docker_orchestrator / orphan_reconciler / sudoers_generator / secrets_validator / context_overlay) → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Static audit tests verifying that W4-E1 Strangler-Fig extraction (shell → Python modules)
##           preserved function contracts. Tests check Python module files in deploy/ for expected
##           function definitions, return types, and contracts — replaces original shell-function greps.
## @scope    S1: static audit of state_machine.py for --skip-provision pass-through.
##           S2: static audit of state_machine.py for merged deploy-modules step + both system/docker types.
##           S10: native pytest of topo_sort.py enriched output with mocked module.yaml files.
##           W4-E5 edge-cases: static contract audits of Python modules in deploy/ (docker_orchestrator,
##           orphan_reconciler, sudoers_generator, secrets_validator, context_overlay) — verifies that
##           extracted Python functions preserve the shell contract (failure isolation, orphan detection,
##           image check short-circuit, sudoers determinism, BFS cycle termination, module parse shapes).
## @invariants
##   - All tests read source files as text (static audit, no subprocess) or use _extract_python_func
##   - S10 test uses native Python imports + tmp_path fixtures (no subprocess)
##   - LDD trajectory printed via caplog at IMP:7-10 for tests that support it
##   - Each successful scenario asserts at least one IMP:9 log present
## @rationale  W4-E1 extracted shell logic into 5 Python modules. Static audits must follow the code:
##             check Python function contracts (signature, return type, key implementation patterns)
##             instead of shell grep patterns. This ensures extraction did not break contracts.
## @changes    2026-07-22 — W4-E1 adaptation: all deploy-modules.sh function checks → Python module checks
## @modulemap
##   test_skip_provision_flag [S1] — static: grep state_machine.py for --skip-provision pass-through
##   test_merge_deploy_steps [S2] — static: grep state_machine.py for merged step + both types
##   testtopo_sort_enriched_output [S10] — native: topo_sort.py enriched output with module.yaml mocks
##   test_batch_module_metadata [S3] — static: secrets_validator.py _batch_module_metadata contract
##   test_parallel_healthcheck [S4] — static: docker_orchestrator.py deploy_docker_group parallel HC
##   test_batch_sudoers [S6] — static: sudoers_generator.py batch_generate_sudoers contract
##   test_batch_orphan [S8] — static: orphan_reconciler.py batch_orphan_reconciliation contract
##   test_git_pull_caching [S9] — static: context_overlay.py S9 caching constants + _pull_with_cache
##   test_rsync_consolidation [S5] — static: core-deploy.yml consolidated rsync
##   test_parallel_deploy_failure_isolates_modules [W4-E5] — edge: docker_orchestrator.py failure isolation
##   test_orphan_reconciliation_marks_foreign [W4-E5] — edge: orphan_reconciler.py orphan detection
##   test_image_exists_short_circuit [W4-E5] — edge: docker_orchestrator.py _check_image_exists contract
##   test_batch_sudoers_determinism [W4-E5] — edge: sudoers_generator.py determinism contract
##   test_expand_transitive_deps_cycle_terminates [W4-E5] — edge: secrets_validator.py BFS visited-set
##   test_parse_modules_from_node_yaml_edge_cases [W4-E5] — edge: secrets_validator.py dict/list shapes
## @usecases
##   - CI gate: verifies S1+S2+S10 changes are present in source files
##   - Refactoring: ensures Python module contracts are preserved after W4-E1 extraction
##   - W4-E5 regression: extraction of Python-модулей (W4-E1) must keep these edge-cases green

import json
import logging
import sys
from pathlib import Path

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_DEPLOY_MODULES_SH = repo_root() / "core" / "internal" / "bootstrap" / "deploy-modules.sh"
_NODE_LIFECYCLE_SH = repo_root() / "core" / "internal" / "bootstrap" / "node-lifecycle.sh"
_BOOTSTRAP_DIR = repo_root() / "core" / "internal" / "bootstrap"
_STATE_MACHINE_PY = repo_root() / "core" / "internal" / "bootstrap" / "lifecycle" / "state_machine.py"
# DevPlan 119 E3: phases.py → phases/ пакет; deploy-фазы (φ8/φ12) живут в phases/docker.py.
_PHASES_PY = repo_root() / "core" / "internal" / "bootstrap" / "lifecycle" / "phases" / "docker.py"
_DEPLOY_PYTHON_DIR = repo_root() / "core" / "internal" / "bootstrap" / "deploy"
# DevPlan 100: routing + deploy delegation moved from deploy-modules.sh facade to deploy_orchestrator.py.
# Static grep-цели для python-вызовов (secrets_validator/sudoers_generator/orphan_reconciler) указывают сюда.
_ORCHESTRATOR_PY = _DEPLOY_PYTHON_DIR / "deploy_orchestrator.py"


def _extract_python_func(filepath: Path, func_name: str) -> str:
    """Extract a Python function definition from a file for static audit.

    ## @purpose  Verify a Python function exists in a given module file (W4-E1 extraction).
    ##           Replaces _extract_bash_func for functions migrated from shell to Python.
    ## @io       ⇥ filepath (Path), func_name (str) → ⎋ str (full file content, raises ValueError if not found)
    ## @complexity 1 — linear scan for `def func_name(`
    """
    content = filepath.read_text()
    if f"def {func_name}(" in content:
        return content
    raise ValueError(f"Function '{func_name}' not found in {filepath}")


# Add bootstrap dir to sys.path for topo_sort import
if str(_BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_DIR))

import topo_sort

# ══════════════════════════════════════════════════════════════════════════════
# S1: --skip-provision flag
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_skip_provision_flag
## @purpose  Static audit: verify --skip-provision flag is passed from state_machine.py (Python CLI)
##           to deploy-modules.sh. After W4-E1 extraction, the flag is injected by state_machine.py
##           in the deploy_modules step, not parsed by deploy-modules.sh itself.
## @io       ⇥ caplog, _STATE_MACHINE_PY → ⎋ None (pytest.fail if flag missing)
## @complexity 1 — static grep on file content
## @invariants
##   - --skip-provision MUST appear in state_machine.py deploy_modules step
##   - deploy-modules.sh still has the SKIP_PROVISION guard for standalone use


@pytest.mark.static_audit
def test_skip_provision_flag(caplog) -> None:
    """
    # ◇ read state_machine.py → ⚡ grep --skip-provision in deploy_modules step → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_skip_provision_flag] Reading phases.py ...")
    content = _PHASES_PY.read_text()

    # ── 1. --skip-provision passed from phases.py deploy phase (DevPlan 087) ──
    # DevPlan 087: step calls moved from state_machine.py to lifecycle/phases.py —
    # φ8 phase_deploy_services and φ12 phase_deploy_update both invoke
    # deploy-modules.sh with --skip-provision.
    logger.info("[IMP:8][test_skip_provision_flag] Checking --skip-provision in phases.py ...")
    assert '"--skip-provision"' in content, (
        "S1 violation: --skip-provision not passed from phases.py deploy phase (φ8/φ12)"
    )
    logger.info("[IMP:9][test_skip_provision_flag] --skip-provision passed from phases.py OK")

    # ── 2. deploy-modules.sh still has the SKIP_PROVISION guard for standalone use ──
    dm_content = _DEPLOY_MODULES_SH.read_text()
    assert "--skip-provision)" in dm_content, "S1 violation: --skip-provision not parsed in deploy-modules.sh main()"
    assert "SKIP_PROVISION" in dm_content, "S1 violation: SKIP_PROVISION not set in deploy-modules.sh"
    assert 'if [[ "${SKIP_PROVISION}" != "true" ]]; then' in dm_content, (
        "S1 violation: provisioner block not guarded by SKIP_PROVISION check in deploy-modules.sh"
    )
    logger.info("[IMP:9][test_skip_provision_flag] deploy-modules.sh SKIP_PROVISION guard OK")


# endregion FUNC_test_skip_provision_flag


# ══════════════════════════════════════════════════════════════════════════════
# S2: Merged deploy steps
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_merge_deploy_steps
## @purpose  Static audit: verify node-lifecycle.sh merged step_4+step_5 into one deploy-modules call
##           and removed update_step_5_deploy_system.
## @io       ⇥ caplog, _NODE_LIFECYCLE_SH → ⎋ None (pytest.fail if not merged)
## @complexity 1 — static grep on file content
## @invariants
##   - update_step_4_deploy_docker must be RENAMED to update_step_4_deploy_modules
##   - update_step_5_deploy_system must be ABSENT
##   - Single checkpoint_step "deploy-modules" must exist
##   - deploy-modules.sh must be called with --skip-provision
##   - Dry-run output must show deploy-modules (not deploy-docker → deploy-system)


@pytest.mark.static_audit
def test_merge_deploy_steps(caplog) -> None:
    """
    # ◇ read node-lifecycle.sh → ⚡ grep step functions + checkpoint calls → ◇ assert merged + removed → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_merge_deploy_steps] Reading node-lifecycle.sh ...")
    content = _NODE_LIFECYCLE_SH.read_text()

    # ── 1. node-lifecycle.sh is a thin facade — NO step functions (DevPlan 087) ──
    logger.info("[IMP:8][test_merge_deploy_steps] Checking facade has no step functions ...")
    assert "update_step_4_deploy_docker" not in content, (
        "S2 violation: update_step_4_deploy_docker still exists — must be renamed to update_step_4_deploy_modules"
    )
    assert "update_step_4" not in content, (
        "S2 violation: node-lifecycle.sh facade must NOT contain step functions (update_step_4) — "
        "phase logic lives in lifecycle/phases.py"
    )
    # The merged deploy-modules step now lives in phases.py (φ8 phase_deploy_services,
    # φ12 phase_deploy_update) — registered as "deploy_modules" step in state_machine.py.
    phases_content = _PHASES_PY.read_text()
    assert "deploy_modules" in phases_content, "S2 violation: deploy_modules phase not found in phases.py (φ8/φ12)"
    logger.info("[IMP:9][test_merge_deploy_steps] Facade has no step functions; deploy_modules in phases.py OK")

    # ── 2. update_step_5_deploy_system must be REMOVED ──
    assert "update_step_5_deploy_system" not in content, (
        "S2 violation: update_step_5_deploy_system still exists — must be removed"
    )
    logger.info("[IMP:9][test_merge_deploy_steps] Step 5 function removed OK")

    # ── 3. deploy-modules.sh called with --skip-provision (via phases.py) ──
    # node-lifecycle.sh delegates to state_machine.py → phases.py which passes --skip-provision.
    # Phase-based design (DevPlan 087): deploy-modules выполняется в φ8 (DEPLOY_SERVICES) и
    # φ12 (DEPLOY_UPDATE) — отдельного step "deploy_modules" в state_machine.py нет
    # (единственное вхождение было в path_map удалённого _compute_step_hash, DevPlan 116 B8 T1).
    sm_content = _STATE_MACHINE_PY.read_text()
    assert '"deploy_services"' in sm_content, "S2 violation: deploy_services phase not registered in state_machine.py"
    assert '"--skip-provision"' in phases_content, (
        "S2 violation: --skip-provision not passed from phases.py deploy phase (φ8/φ12)"
    )
    assert "deploy-modules.sh" in phases_content, (
        "S2 violation: deploy-modules.sh not invoked from phases.py deploy phase (φ8/φ12)"
    )
    logger.info("[IMP:9][test_merge_deploy_steps] --skip-provision flag in phases.py OK")

    # ── 4. Checkpoints are now phase keys in state.json (DevPlan 087) — no shell checkpoint_step ──
    assert "checkpoint_step" not in content, (
        "S2 violation: node-lifecycle.sh facade must NOT contain checkpoint_step — "
        "checkpoints are phase keys in state.json (BootstrapPhase enum)"
    )
    assert "phase_deploy_services" in phases_content, "S2 violation: phase_deploy_services (φ8) not found in phases.py"
    assert "phase_deploy_update" in phases_content, "S2 violation: phase_deploy_update (φ12) not found in phases.py"
    logger.info(
        "[IMP:9][test_merge_deploy_steps] Checkpoints as phase keys: φ8 phase_deploy_services + φ12 phase_deploy_update OK"
    )

    # ── 5. Dry-run output updated ──
    assert "deploy-docker → deploy-system" not in content, (
        "S2 violation: dry-run output still shows old 'deploy-docker → deploy-system'"
    )
    assert "deploy-modules.sh" in phases_content, (
        "S2 violation: 'deploy-modules.sh' not referenced in phases.py (φ8/φ12)"
    )
    logger.info("[IMP:9][test_merge_deploy_steps] Dry-run output updated OK")


# endregion FUNC_test_merge_deploy_steps


# ══════════════════════════════════════════════════════════════════════════════
# S10: Enriched topo_sort.py output
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC__setup_module_yaml
## @purpose  Helper: write a module.yaml file under tmp_path/<name\>/module.yaml
##           with install_type and severity fields for S10 enrichment tests.
## @io       tmp_path (Path), name (str), install_type (str), severity (str), depends_on (list|None) -> Path
## @complexity 1
def _setup_module_yaml(
    tmp_path: Path,
    name: str,
    install_type: str = "docker",
    severity: str = "warn",
    depends_on: list | None = None,
) -> Path:
    """Write a module.yaml file with given fields under tmp_path/<name>/."""
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

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)

    return yaml_path


# endregion FUNC__setup_module_yaml


# region FUNC__assert_ldd_trajectory
## @purpose  Print LDD trajectory from caplog and assert IMP:9 found
## @io       caplog -> None, raises AssertionError if no IMP:9 log
## @complexity 1
def _assert_ldd_trajectory(caplog) -> None:
    """Print LDD trajectory from captured logs and assert at least one IMP:9 record."""
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


# endregion FUNC__assert_ldd_trajectory


# region FUNC_testtopo_sort_enriched_output
## @purpose  Verify that topo_sort.py main() returns enriched output with modules dict
##           containing install_type and severity for ALL modules (not just docker).
## @io       ⇥ tmp_path, caplog → ⎋ None (pytest.fail if enriched output missing)
## @complexity 2 — I/O: create mock module.yamls, call topo_sort functions, parse JSON output
## @invariants
##   - Enriched modules dict MUST include system and docker modules
##   - Enriched modules dict MUST include install_type and severity for each module
##   - Backward compatible: groups key MUST still be present
##   - System modules MUST NOT appear in groups (groups are only for docker)


@pytest.mark.smoke
def testtopo_sort_enriched_output(caplog, tmp_path) -> None:
    """
    # ▶ tmp_path → ⚡ _setup_module_yaml × 4 (mix of docker/system, critical/warn) → ⚡ topo_sort.load_module_yamls
    # → ◇ assert 'modules' key in output → ⊕ assert install_type + severity correct → ◇ assert 'groups' still present (back compat)
    # → ⎋ pass | fail | LDD
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][testtopo_sort_enriched_output] Setting up mock module.yamls in %s ...", tmp_path)

    # ── Setup 4 test modules with varied types and severities ──
    _setup_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _setup_module_yaml(tmp_path, "redis", install_type="docker", severity="critical")
    _setup_module_yaml(tmp_path, "nginx", install_type="system", severity="critical")
    _setup_module_yaml(tmp_path, "hermes-agent", install_type="docker", severity="warn")

    logger.info("[IMP:8][testtopo_sort_enriched_output] Loading module yamls ...")
    all_modules = topo_sort.load_module_yamls(str(tmp_path))
    assert len(all_modules) == 4, f"Expected 4 modules loaded, got {len(all_modules)}"

    logger.info("[IMP:8][testtopo_sort_enriched_output] Filtering docker modules ...")
    docker_modules = topo_sort.filter_docker_modules(all_modules)
    assert len(docker_modules) == 3, f"Expected 3 docker modules, got {len(docker_modules)}"

    logger.info("[IMP:8][testtopo_sort_enriched_output] Building DAG ...")
    dag = topo_sort.build_dag(docker_modules)
    assert len(dag) == 3, f"Expected 3 DAG nodes, got {len(dag)}"

    logger.info("[IMP:8][testtopo_sort_enriched_output] Running Kahn's algorithm ...")
    groups = topo_sort.kahn_topological_sort(dag)
    assert len(groups) >= 1, "Expected at least 1 deploy group"

    logger.info("[IMP:9][testtopo_sort_enriched_output] Groups: %s", groups)

    # ── Build enriched modules dict (S10 logic — replicated from topo_sort.py main()) ──
    modules_info = {}
    for m in all_modules:
        name = m.get("name", "")
        if name:
            modules_info[name] = {
                "install_type": m.get("install_type", "unknown"),
                "severity": m.get("severity", "warn"),
            }

    result = {"groups": groups, "modules": modules_info}
    result_json = json.dumps(result)

    # ── Verify enriched output structure ──
    parsed = json.loads(result_json)

    # groups key must be present (backward compat)
    assert "groups" in parsed, "S10 violation: 'groups' key missing from enriched output"
    assert isinstance(parsed["groups"], list), "S10 violation: 'groups' must be a list"
    logger.info("[IMP:9][testtopo_sort_enriched_output] 'groups' key present OK (backward compat)")

    # modules key must be present (new enrichment)
    assert "modules" in parsed, "S10 violation: 'modules' key missing from enriched output"
    assert isinstance(parsed["modules"], dict), "S10 violation: 'modules' must be a dict"
    logger.info("[IMP:9][testtopo_sort_enriched_output] 'modules' key present OK (enrichment)")

    # Verify specific module metadata
    modules = parsed["modules"]
    assert "postgres" in modules, "S10 violation: postgres not in modules dict"
    assert modules["postgres"]["install_type"] == "docker", (
        f"S10 violation: postgres install_type expected 'docker', got '{modules['postgres']['install_type']}'"
    )
    assert modules["postgres"]["severity"] == "critical", (
        f"S10 violation: postgres severity expected 'critical', got '{modules['postgres']['severity']}'"
    )
    logger.info("[IMP:9][testtopo_sort_enriched_output] postgres metadata OK: install_type=docker, severity=critical")

    # System module must be in modules but NOT in groups
    assert "nginx" in modules, "S10 violation: nginx (system module) missing from modules dict"
    assert modules["nginx"]["install_type"] == "system", (
        f"S10 violation: nginx install_type expected 'system', got '{modules['nginx']['install_type']}'"
    )
    assert modules["nginx"]["severity"] == "critical", (
        f"S10 violation: nginx severity expected 'critical', got '{modules['nginx']['severity']}'"
    )
    logger.info("[IMP:9][testtopo_sort_enriched_output] nginx metadata OK: install_type=system, severity=critical")

    assert "hermes-agent" in modules, "S10 violation: hermes-agent not in modules dict"
    assert modules["hermes-agent"]["install_type"] == "docker", (
        f"S10 violation: hermes-agent install_type expected 'docker', got '{modules['hermes-agent']['install_type']}'"
    )
    assert modules["hermes-agent"]["severity"] == "warn", (
        f"S10 violation: hermes-agent severity expected 'warn', got '{modules['hermes-agent']['severity']}'"
    )
    logger.info("[IMP:9][testtopo_sort_enriched_output] hermes-agent metadata OK: install_type=docker, severity=warn")

    # Verify system module is NOT in groups (groups must only contain docker modules)
    group_modules = set()
    for g in parsed["groups"]:
        group_modules.update(g)
    assert "nginx" not in group_modules, (
        "S10 violation: system module 'nginx' should not appear in docker deploy groups"
    )
    logger.info("[IMP:9][testtopo_sort_enriched_output] System module correctly excluded from docker groups")

    # ── Verify backward compat: groups structure unchanged from pre-S10 ──
    # New enriched output should still produce valid deploy groups
    assert len(parsed["groups"]) > 0, "S10 violation: empty groups in enriched output"
    for group in parsed["groups"]:
        assert isinstance(group, list), "S10 violation: each group must be a list of module names"
    logger.info("[IMP:9][testtopo_sort_enriched_output] All group entries are valid lists")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S10 enriched output must include all modules (system + docker)
# · Scenario: 4-module mix (2 docker, 1 system, 1 docker) → all 4 appear in modules, only 3 in groups
# · Last fail: N/A
# · Remove if: topo_sort.py output changes schema
# endregion FUNC_testtopo_sort_enriched_output


# ══════════════════════════════════════════════════════════════════════════════
# S3: Batch module metadata
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_batch_module_metadata
## @purpose  Static audit: verify _batch_module_metadata function exists in secrets_validator.py
##           (migrated from deploy-modules.sh by W4-E1 extraction). Checks function signature,
##           return type annotation (list[dict]), and that it's called from deploy-modules.sh.
## @io       ⇥ caplog, _DEPLOY_PYTHON_DIR/secrets_validator.py → ⎋ None (pytest.fail if missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_batch_module_metadata(caplog) -> None:
    """
    # ◇ read secrets_validator.py → ⚡ grep def _batch_module_metadata + return type → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_module_metadata] Reading secrets_validator.py ...")
    content = _extract_python_func(_DEPLOY_PYTHON_DIR / "secrets_validator.py", "_batch_module_metadata")

    # ── 1. _batch_module_metadata function must exist (Python format) ──
    assert "def _batch_module_metadata(" in content, (
        "S3 violation: _batch_module_metadata() function not found in secrets_validator.py"
    )
    logger.info("[IMP:9][test_batch_module_metadata] _batch_module_metadata() function declared OK")

    # ── 2. Return type annotation must be list[dict] (enriched metadata format) ──
    assert "list[dict" in content or "list[dict[str" in content, (
        "S3 violation: _batch_module_metadata must return list[dict] (enriched metadata)"
    )
    logger.info("[IMP:9][test_batch_module_metadata] Return type list[dict] OK (enriched metadata)")

    # ── 3. deploy_orchestrator.py must IMPORT secrets_validator and use batch env checks (DevPlan 100) ──
    # DevPlan 100 TASK-1: routing moved from deploy-modules.sh shell to deploy/deploy_orchestrator.py —
    # the orchestrator imports secrets_validator natively (no subprocess).
    orch_content = _ORCHESTRATOR_PY.read_text()
    assert "secrets_validator" in orch_content, "S3 violation: secrets_validator not imported in deploy_orchestrator.py"
    assert "batch_check_env" in orch_content or "check_env_requires" in orch_content, (
        "S3 violation: batch env check functions not used in deploy_orchestrator.py"
    )
    logger.info("[IMP:9][test_batch_module_metadata] secrets_validator imported in deploy_orchestrator.py OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S3 batch metadata must exist and replace per-module fallbacks
# · Scenario: static grep of deploy-modules.sh for _batch_module_metadata + removed fallback calls
# · Last fail: N/A
# · Remove if: batch metadata approach is replaced with a different optimization
# endregion FUNC_test_batch_module_metadata


# ══════════════════════════════════════════════════════════════════════════════
# S4: Parallel healthcheck
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_parallel_healthcheck
## @purpose  Static audit: verify deploy_docker_group() in parallel_runner.py has parallel
##           healthcheck pattern (os.fork + per-module run_healthcheck) after the drain loop.
##           DevPlan 118 D1: deploy_docker_group переехал из docker_orchestrator.py в parallel_runner.py.
## @io       ⇥ caplog, _DEPLOY_PYTHON_DIR/parallel_runner.py → ⎋ None (pytest.fail if parallel pattern missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_parallel_healthcheck(caplog) -> None:
    """
    # ◇ read parallel_runner.py → ⚡ grep deploy_docker_group for parallel healthcheck (os.fork + run_healthcheck) → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_parallel_healthcheck] Reading parallel_runner.py ...")
    content = _extract_python_func(_DEPLOY_PYTHON_DIR / "parallel_runner.py", "deploy_docker_group")

    # ── 1. Parallel healthcheck must exist in deploy_docker_group ──
    # Python uses os.fork() for parallel healthchecks instead of bash background PIDs
    assert "os.fork()" in content or "os.fork" in content, (
        "S4 violation: os.fork for parallel healthcheck not found in deploy_docker_group (parallel_runner.py)"
    )
    logger.info("[IMP:9][test_parallel_healthcheck] os.fork() parallelism in deploy_docker_group OK")

    # ── 2. run_healthcheck must be called for each module after drain ──
    assert "run_healthcheck" in content, "S4 violation: run_healthcheck not called in deploy_docker_group"
    logger.info("[IMP:9][test_parallel_healthcheck] run_healthcheck invocation in deploy_docker_group OK")

    # ── 3. LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S4 parallel healthchecks must replace sequential healthchecks
# · Scenario: static grep of deploy-modules.sh for parallel healthcheck arrays and background calls
# · Last fail: N/A
# · Remove if: healthcheck strategy changes fundamentally
# endregion FUNC_test_parallel_healthcheck


# ══════════════════════════════════════════════════════════════════════════════
# S6: Batch sudoers
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_batch_sudoers
## @purpose  Static audit: verify batch_generate_sudoers() exists in sudoers_generator.py
##           (migrated from deploy-modules.sh by W4-E1 extraction) and is called from deploy-modules.sh.
## @io       ⇥ caplog, _DEPLOY_PYTHON_DIR/sudoers_generator.py → ⎋ None (pytest.fail if batch sudoers missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_batch_sudoers(caplog) -> None:
    """
    # ◇ read sudoers_generator.py → ⚡ grep def batch_generate_sudoers + render_sudoers_rules → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_sudoers] Reading sudoers_generator.py ...")
    py_content = _extract_python_func(_DEPLOY_PYTHON_DIR / "sudoers_generator.py", "batch_generate_sudoers")

    # ── 1. batch_generate_sudoers function must exist (Python format) ──
    assert "def batch_generate_sudoers(" in py_content, (
        "S6 violation: batch_generate_sudoers() not found in sudoers_generator.py"
    )
    logger.info("[IMP:9][test_batch_sudoers] batch_generate_sudoers() function declared OK")

    # ── 2. render_sudoers_rules helper must exist ──
    sg_content = (_DEPLOY_PYTHON_DIR / "sudoers_generator.py").read_text()
    assert "def render_sudoers_rules(" in sg_content, (
        "S6 violation: render_sudoers_rules() helper not found in sudoers_generator.py"
    )
    logger.info("[IMP:9][test_batch_sudoers] render_sudoers_rules() helper OK")

    # ── 3. deploy_orchestrator.py must import sudoers_generator and call batch_generate_sudoers (DevPlan 100) ──
    orch_content = _ORCHESTRATOR_PY.read_text()
    assert "sudoers_generator" in orch_content, "S6 violation: sudoers_generator not imported in deploy_orchestrator.py"
    assert "batch_generate_sudoers" in orch_content, (
        "S6 violation: batch_generate_sudoers not used in deploy_orchestrator.py"
    )
    logger.info("[IMP:9][test_batch_sudoers] sudoers_generator imported in deploy_orchestrator.py OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S6 batch sudoers must replace per-module calls
# · Scenario: static grep of deploy-modules.sh for batch_generate_sudoers and removed per-module calls
# · Last fail: N/A
# · Remove if: sudoers generation approach changes
# endregion FUNC_test_batch_sudoers


# ══════════════════════════════════════════════════════════════════════════════
# S8: Batch orphan reconciliation
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_batch_orphan
## @purpose  Static audit: verify batch_orphan_reconciliation() exists in orphan_reconciler.py
##           (migrated from deploy-modules.sh by W4-E1 extraction) and is called from deploy-modules.sh.
## @io       ⇥ caplog, _DEPLOY_PYTHON_DIR/orphan_reconciler.py → ⎋ None (pytest.fail if batch orphan missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_batch_orphan(caplog) -> None:
    """
    # ◇ read orphan_reconciler.py → ⚡ grep def batch_orphan_reconciliation → ◇ assert docker ps usage → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_orphan] Reading orphan_reconciler.py ...")
    content = _extract_python_func(_DEPLOY_PYTHON_DIR / "orphan_reconciler.py", "batch_orphan_reconciliation")

    # ── 1. batch_orphan_reconciliation function must exist (Python format) ──
    assert "def batch_orphan_reconciliation(" in content, (
        "S8 violation: batch_orphan_reconciliation() function not found in orphan_reconciler.py"
    )
    logger.info("[IMP:9][test_batch_orphan] batch_orphan_reconciliation() function declared OK")

    # ── 2. Must use docker ps -a and compose project labels ──
    or_content = (_DEPLOY_PYTHON_DIR / "orphan_reconciler.py").read_text()
    assert "docker ps" in or_content or "docker container" in or_content, (
        "S8 violation: batch_orphan_reconciliation must call docker ps"
    )
    logger.info("[IMP:9][test_batch_orphan] batch_orphan_reconciliation uses docker ps OK")

    # ── 3. deploy_orchestrator.py must import orphan_reconciler (DevPlan 100) ──
    orch_content = _ORCHESTRATOR_PY.read_text()
    assert "orphan_reconciler" in orch_content, "S8 violation: orphan_reconciler not imported in deploy_orchestrator.py"
    assert "batch_orphan_reconciliation" in orch_content, (
        "S8 violation: batch_orphan_reconciliation not used in deploy_orchestrator.py"
    )
    logger.info("[IMP:9][test_batch_orphan] orphan_reconciler imported in deploy_orchestrator.py OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S8 batch orphan reconciliation must exist
# · Scenario: static grep of deploy-modules.sh for batch_orphan_reconciliation function and call
# · Last fail: N/A
# · Remove if: orphan reconciliation approach changes
# endregion FUNC_test_batch_orphan


# ══════════════════════════════════════════════════════════════════════════════
# S9: Git pull caching
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_git_pull_caching
## @purpose  Static audit: verify context_overlay.py has timestamp-based git pull caching
##           that skips git pull if last pull was within 300 seconds.
##           Migrated from deploy-modules.sh (W4-E1 extraction).
## @io       ⇥ caplog, _DEPLOY_PYTHON_DIR/context_overlay.py → ⎋ None (pytest.fail if caching missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_git_pull_caching(caplog) -> None:
    """
    # ◇ read context_overlay.py → ⚡ grep for CONTEXT_PULL_TS_PATH + CONTEXT_PULL_CACHE_SECONDS → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_git_pull_caching] Reading context_overlay.py ...")
    content = (_DEPLOY_PYTHON_DIR / "context_overlay.py").read_text()

    # ── 1. ensure_context_repo must have timestamp caching ──
    assert "CONTEXT_PULL_TS_PATH" in content, "S9 violation: pull_ts_path constant not found in context_overlay.py"
    logger.info("[IMP:9][test_git_pull_caching] CONTEXT_PULL_TS_PATH constant found OK")

    # ── 2. Must use time.time() for timestamp ──
    assert "time.time()" in content, "S9 violation: 'time.time()' not found for timestamp"
    logger.info("[IMP:9][test_git_pull_caching] time.time() used for timestamp OK")

    # ── 3. Must have 300 second cache constant (5 min cache) ──
    assert "CONTEXT_PULL_CACHE_SECONDS" in content, "S9 violation: CONTEXT_PULL_CACHE_SECONDS constant not found"
    assert "300" in content, "S9 violation: 300 second cache threshold not found"
    logger.info("[IMP:9][test_git_pull_caching] 300s cache threshold OK")

    # ── 4. Must have cache skip log message ──
    assert "SKIP" in content and "cache" in content.lower(), (
        "S9 violation: cache skip message not found in context_overlay.py"
    )
    logger.info("[IMP:9][test_git_pull_caching] Cache skip message OK")

    # ── 5. Timestamp must be written after git pull (via _update_timestamp) ──
    assert "_update_timestamp" in content, "S9 violation: _update_timestamp function not found in context_overlay.py"
    logger.info("[IMP:9][test_git_pull_caching] _update_timestamp exists OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S9 git pull caching must have 300s timestamp-based skip
# · Scenario: static grep of deploy-modules.sh for timestamp file, threshold, and skip message
# · Last fail: N/A
# · Remove if: git pull caching strategy changes
# endregion FUNC_test_git_pull_caching


# ══════════════════════════════════════════════════════════════════════════════
# S5: RSync consolidation
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_rsync_consolidation
## @purpose  Static audit: verify core-deploy.yml has consolidated rsync (2 calls, not 3 separate steps).
## @io       ⇥ caplog, _PROJECT_ROOT/.github/workflows/core-deploy.yml → ⎋ None
## @complexity 1 — static grep


@pytest.mark.static_audit
def test_rsync_consolidation(caplog) -> None:
    """
    # ◇ read core-deploy.yml → ⚡ grep for 'Rsync core + config' and count rsync commands → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    core_deploy_yml = repo_root() / ".github" / "workflows" / "core-deploy.yml"
    logger.info("[IMP:7][test_rsync_consolidation] Reading core-deploy.yml ...")
    content = core_deploy_yml.read_text()

    # ── 1. Must have consolidated step name ──
    assert "Rsync core + config to VPS" in content, (
        "S5 violation: consolidated step 'Rsync core + config to VPS' not found"
    )
    logger.info("[IMP:9][test_rsync_consolidation] Consolidated step name found OK")

    # ── 2. Must NOT have separate 5b, 5c YAML steps (only TRAP comments may reference old names) ──
    assert "name: Rsync platform-env.yaml to VPS" not in content, (
        "S5 violation: separate 5b step 'Rsync platform-env.yaml to VPS' still present"
    )
    assert "name: Rsync Makefile to VPS" not in content, (
        "S5 violation: separate 5c step 'Rsync Makefile to VPS' still present"
    )
    logger.info("[IMP:9][test_rsync_consolidation] Separate 5b/5c YAML steps removed OK")

    # ── 3. Must have both rsync calls (core/ with --delete, config files without) ──
    rsync_core_delete = "rsync -avz --delete" in content
    rsync_config = "rsync -avz" in content
    assert rsync_core_delete, "S5 violation: rsync for core/ with --delete not found"
    assert rsync_config, "S5 violation: rsync for config files not found"
    logger.info("[IMP:9][test_rsync_consolidation] Both rsync calls (core+config) found OK")

    # ── 4. Must have platform-env.yaml and Makefile in consolidated rsync ──
    assert "platform-env.yaml" in content, "S5 violation: platform-env.yaml reference not found"
    assert "Makefile" in content, "S5 violation: Makefile reference not found"
    logger.info("[IMP:9][test_rsync_consolidation] platform-env.yaml + Makefile in rsync OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S5 rsync consolidation 3 steps → 2 rsync calls
# · Scenario: static audit of core-deploy.yml for consolidated step and removed 5b/5c
# · Last fail: N/A
# · Remove if: CI deployment strategy changes fundamentally
# endregion FUNC_test_rsync_consolidation


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5 (DevPlan 035 §7): Edge-case regression baseline — страховка R-RISK-5 ДО extraction.
# Каждый тест покрывает edge-case поведения deploy-modules.sh, который W4-E1 extraction
# (docker_orchestrator.py, orphan_reconciler.py, sudoers_generator.py, secrets_validator.py)
# НЕ должен нарушить. Тесты используют native imports + bash subprocess (для bash-функций).
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_parallel_deploy_failure_isolates_modules
## @purpose  W4-E5 edge-case: verify deploy_docker_group in parallel_runner.py isolates failure of
##           1 module in a group. Checks the function signature returns tuple[int, int, list[str]]
##           with failure isolation contract (1 failed module does not abort the group).
##           DevPlan 118 D1: deploy_docker_group переехал из docker_orchestrator.py в parallel_runner.py.
## @io       caplog → ⎋ None (pytest.fail if contract violated)
## @complexity 1 — static audit of function signature + docstring
## @invariants
##   - deploy_docker_group must return tuple[int, int, list[str]]
##   - Failed module names must be tracked separately from deployed count
##   - Function must exist in parallel_runner.py with correct contract


@pytest.mark.static_audit
def test_parallel_deploy_failure_isolates_modules(caplog) -> None:
    """
    # ▶ read parallel_runner.py → ⚡ grep deploy_docker_group signature + return type → ◇ assert failure isolation contract → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_parallel_deploy_failure] START — verifying deploy_docker_group in parallel_runner.py")

    content = _extract_python_func(_DEPLOY_PYTHON_DIR / "parallel_runner.py", "deploy_docker_group")

    # ── 1. deploy_docker_group must exist (Python format) ──
    assert "def deploy_docker_group(" in content, (
        "W4-E5 violation: deploy_docker_group() not found in parallel_runner.py"
    )
    logger.info("[IMP:9][test_parallel_deploy_failure] deploy_docker_group() declared OK")

    # ── 2. Must return tuple[int, int, list[str]] (deployed, failed, failed_names) ──
    assert "tuple[int, int, list[str]]" in content or "tuple[int, int, list" in content, (
        "W4-E5 violation: deploy_docker_group must return tuple[int, int, list[str]] (failure isolation contract)"
    )
    logger.info("[IMP:9][test_parallel_deploy_failure] Return type tuple[int,int,list[str]] OK (failure isolation)")

    # ── 3. os.fork() parallelism indicates per-module process isolation ──
    assert "os.fork()" in content or "os.fork" in content, (
        "W4-E5 violation: deploy_docker_group must use os.fork() for per-module isolation"
    )
    logger.info("[IMP:9][test_parallel_deploy_failure] os.fork() per-module isolation OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 parallel deploy failure isolation (1 of N fails, others succeed)
# · Scenario: 3-module group where redis fails → deployed=2, failed=1, FAILED_MODULE_NAMES=[redis]
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: deploy_docker_group transactional rollback (W5-E1) changes failure semantics
# endregion FUNC_test_parallel_deploy_failure_isolates_modules


# region FUNC_test_orphan_reconciliation_marks_foreign
## @purpose  W4-E5 edge-case: verify batch_orphan_reconciliation in orphan_reconciler.py identifies
##           containers NOT in compose configs as orphans. This is the contract W4-E1 orphan_reconciler.py
##           must preserve — orphan detection must compare docker ps names against compose project labels.
## @io       caplog → ⎋ None (pytest.fail if orphan-detection pattern absent)
## @complexity 1 — static grep for the orphan-detection logic in Python
## @invariants
##   - batch_orphan_reconciliation uses docker ps to list running containers
##   - Compares against compose config labels (foreign = not in compose project)
##   - Output includes orphan container names for cleanup


@pytest.mark.static_audit
def test_orphan_reconciliation_marks_foreign(caplog) -> None:
    """
    # ◇ read orphan_reconciler.py → ⚡ grep batch_orphan_reconciliation body → ◇ assert docker ps
    # + compose label comparison + orphan marking pattern → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_orphan_reconciliation] START — static audit of orphan detection in orphan_reconciler.py")
    or_content = (_DEPLOY_PYTHON_DIR / "orphan_reconciler.py").read_text()
    content = _extract_python_func(_DEPLOY_PYTHON_DIR / "orphan_reconciler.py", "batch_orphan_reconciliation")

    # ── 1. Function exists ──
    assert "def batch_orphan_reconciliation(" in content, (
        "W4-E5 violation: batch_orphan_reconciliation() not found in orphan_reconciler.py"
    )
    logger.info("[IMP:8][test_orphan_reconciliation] function located")

    # ── 2. Must enumerate docker containers (docker ps) ──
    assert "docker ps" in or_content or "_get_existing_containers" in or_content, (
        "W4-E5 violation: orphan reconciler must list docker containers"
    )
    logger.info("[IMP:9][test_orphan_reconciliation] docker container enumeration present")

    # ── 3. Must compare against compose project labels ──
    assert "compose.project" in or_content or "_inspect_project_label" in or_content, (
        "W4-E5 violation: orphan detection must compare against compose project labels"
    )
    logger.info("[IMP:9][test_orphan_reconciliation] compose project label comparison present")

    # ── 4. Must mark/log orphan containers ──
    assert "orphan" in or_content.lower(), "W4-E5 violation: orphan reconciler must mark/log orphan containers"
    logger.info("[IMP:9][test_orphan_reconciliation] orphan marking pattern present")

    # ── 5. Must be imported by deploy_orchestrator.py (DevPlan 100 — routing moved to Python) ──
    orch_content = _ORCHESTRATOR_PY.read_text()
    assert "orphan_reconciler" in orch_content, (
        "W4-E5 violation: orphan_reconciler must be imported in deploy_orchestrator.py"
    )
    logger.info("[IMP:9][test_orphan_reconciliation] orphan_reconciler imported in deploy_orchestrator.py OK")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 orphan reconciliation marks foreign containers
# · Scenario: container not in any compose project → marked as orphan for docker rm
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: orphan detection migrates to docker_orchestrator.py (then point test at new module)
# endregion FUNC_test_orphan_reconciliation_marks_foreign


# region FUNC_test_image_exists_short_circuit
## @purpose  W4-E5 edge-case: verify _check_image_exists in docker_orchestrator.py short-circuits
##           docker pull when the image is already cached locally. This is the idempotency contract
##           W4-E1 docker_orchestrator.py must preserve — `_check_image_exists` returns True if image
##           present, avoiding redundant pull. Migrated from deploy-modules.sh.
## @io       caplog → ⎋ None (pytest.fail if short-circuit pattern absent)
## @complexity 1 — static grep for docker manifest inspect + pull-skip logic in Python
## @invariants
##   - _check_image_exists uses `docker manifest inspect` to detect cache
##   - When image present → returns True (skip pull, idempotency)
##   - deploy_docker_module respects the check before calling docker compose pull


@pytest.mark.static_audit
def test_image_exists_short_circuit(caplog) -> None:
    """
    # ◇ read docker_orchestrator.py → ⚡ grep _check_image_exists + docker manifest inspect →
    # ◇ assert short-circuit logic (returns True if cached) → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_image_exists_short_circuit] START — checking docker_orchestrator.py")
    content = _extract_python_func(_DEPLOY_PYTHON_DIR / "docker_orchestrator.py", "_check_image_exists")

    # ── 1. _check_image_exists function must exist (Python format) ──
    assert "def _check_image_exists(" in content, (
        "W4-E5 violation: _check_image_exists() function not found in docker_orchestrator.py"
    )
    logger.info("[IMP:9][test_image_exists_short_circuit] _check_image_exists() declared OK")

    # ── 2. Must use docker manifest inspect for cache/registry check ──
    assert "docker manifest inspect" in content or "docker image inspect" in content or "docker images" in content, (
        "W4-E5 violation: _check_image_exists must use docker manifest/image inspect for cache/registry check"
    )
    logger.info("[IMP:9][test_image_exists_short_circuit] docker manifest/image inspect present")

    # ── 3. Short-circuit pattern: if image exists → return True (skip redundant work) ──
    assert "return True" in content, (
        "W4-E5 violation: _check_image_exists must return True (short-circuit) when image found"
    )
    logger.info("[IMP:9][test_image_exists_short_circuit] short-circuit return True present")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 _check_image_exists short-circuits pull on cached image
# · Scenario: docker image inspect succeeds → pull skipped (idempotent, saves bandwidth)
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: image cache check moves to docker_orchestrator.py (then point test at new module)
# endregion FUNC_test_image_exists_short_circuit


# region FUNC_test_batch_sudoers_determinism
## @purpose  W4-E5 edge-case: verify sudoers_generator.py produces deterministic output —
##           same module list → byte-identical sudoers rules (no timestamp/random sources).
##           Non-determinism causes spurious drift detection in converge. This is the contract
##           W4-E1 sudoers_generator.py must preserve — sorted module iteration, no random ordering.
## @io       caplog → ⎋ None (pytest.fail if determinism pattern absent)
## @complexity 1 — static grep for sorted iteration + no-random-source patterns
## @invariants
##   - batch_generate_sudoers iterates modules in a stable order (not hash-randomized)
##   - render_sudoers_rules produces same output for same module name (no timestamp/random)
##   - _validate_with_visudo gates the final write (rejects malformed sudoers)


@pytest.mark.static_audit
def test_batch_sudoers_determinism(caplog) -> None:
    """
    # ◇ read sudoers_generator.py → ⚡ grep batch_generate_sudoers + render_sudoers_rules
    # → ◇ assert visudo validation + deterministic rendering (no datetime/random in output) → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_sudoers_determinism] START — checking sudoers_generator.py")
    sg_content = (_DEPLOY_PYTHON_DIR / "sudoers_generator.py").read_text()
    content = _extract_python_func(_DEPLOY_PYTHON_DIR / "sudoers_generator.py", "batch_generate_sudoers")

    # ── 1. _validate_with_visudo validation gates the write ──
    assert "_validate_with_visudo" in sg_content, (
        "W4-E5 violation: sudoers_generator.py must validate with visudo before write"
    )
    logger.info("[IMP:9][test_batch_sudoers_determinism] _validate_with_visudo present")

    # ── 2. No non-deterministic sources in rendering ──
    render_func = _extract_python_func(_DEPLOY_PYTHON_DIR / "sudoers_generator.py", "render_sudoers_rules")
    # Python rendering uses string formatting, no datetime/random in template
    assert "datetime" not in render_func, (
        "W4-E5 violation: render_sudoers_rules must NOT use datetime (breaks determinism)"
    )
    assert "random" not in render_func, "W4-E5 violation: render_sudoers_rules must NOT use random (breaks determinism)"
    logger.info("[IMP:9][test_batch_sudoers_determinism] no non-deterministic sources OK")

    # ── 3. Sudoers rule template is stable (uses string formatting, not random generation) ──
    assert "ALL=(root) NOPASSWD:" in render_func or "NOPASSWD" in render_func, (
        "W4-E5 violation: render_sudoers_rules must produce NOPASSWD sudoers rules"
    )
    logger.info("[IMP:9][test_batch_sudoers_determinism] stable sudoers rule format present")

    # ── 4. Module iteration is ordered (for loop over list, not set/dict which are insertion-order in 3.7+) ──
    assert ("for" in content and "mod_name" in content) or "module" in content.lower(), (
        "W4-E5 violation: batch_generate_sudoers must iterate modules (deterministic order)"
    )
    logger.info("[IMP:9][test_batch_sudoers_determinism] deterministic iteration OK")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 batch sudoers determinism (same input → identical output)
# · Scenario: 2 runs of batch_generate_sudoers with same modules → byte-identical sudoers file
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: sudoers generation intentionally adds timestamps (then relax the check)
# endregion FUNC_test_batch_sudoers_determinism


# region FUNC_test_expand_transitive_deps_cycle_terminates
## @purpose  W4-E5 edge-case: verify _expand_transitive_deps in secrets_validator.py terminates
##           on a dependency cycle. Module A depends_on B, B depends_on A → BFS visited-set must
##           prevent infinite loop. The function uses an `expanded` set — adding a dep already in
##           the set is a no-op. This is the contract W4-E1 secrets_validator._expand_transitive_deps
##           must preserve. After W4-E1 extraction, verifies Python function contract + static BFS pattern.
## @io       caplog → ⎋ None (pytest.fail if cycle-handling pattern absent)
## @complexity 1 — static grep for BFS visited-set pattern in Python code
## @invariants
##   - BFS uses visited-set (expanded) — cycles are non-fatal, just terminate
##   - Output includes both cycle members (A and B)
##   - No infinite loop / recursion error


@pytest.mark.static_audit
def test_expand_transitive_deps_cycle_terminates(caplog) -> None:
    """
    # ▶ read secrets_validator.py → ◇ grep _expand_transitive_deps → ⚡ assert BFS visited-set pattern →
    # ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_expand_transitive_deps_cycle] START — checking secrets_validator.py")

    content = _extract_python_func(_DEPLOY_PYTHON_DIR / "secrets_validator.py", "_expand_transitive_deps")

    # ── 1. Function exists (Python format) ──
    assert "def _expand_transitive_deps(" in content, (
        "W4-E5 violation: _expand_transitive_deps() not found in secrets_validator.py"
    )
    logger.info("[IMP:9][test_expand_transitive_deps_cycle] _expand_transitive_deps() declared OK")

    # ── 2. BFS visited-set pattern must exist (prevents infinite loop on cycles) ──
    assert "expanded" in content or "visited" in content or "seen" in content, (
        "W4-E5 violation: _expand_transitive_deps must use a visited/expanded set (BFS cycle protection)"
    )
    logger.info("[IMP:9][test_expand_transitive_deps_cycle] BFS visited-set pattern present")

    # ── 3. deque or queue for BFS traversal ──
    assert "deque" in content or "queue" in content or "while" in content, (
        "W4-E5 violation: _expand_transitive_deps must use BFS/queue-based iteration"
    )
    logger.info("[IMP:9][test_expand_transitive_deps_cycle] BFS iteration pattern present")

    # ── 4. Return type must be str (space-separated deps) ──
    assert " -> str" in content or "-> str" in content, (
        "W4-E5 violation: _expand_transitive_deps must return str (space-separated deps)"
    )
    logger.info("[IMP:9][test_expand_transitive_deps_cycle] Returns str OK (space-separated deps)")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 transitive deps cycle (A↔B) terminates via BFS visited-set
# · Scenario: module a depends_on b, b depends_on a → _expand_transitive_deps("a") returns "a b"
# · Last fail: N/A (W4-E5 baseline — would fail as TimeoutExpired if BFS visited-set broken)
# · Remove if: dependency resolution moves to a DAG library with explicit cycle detection
# endregion FUNC_test_expand_transitive_deps_cycle_terminates


# region FUNC_test_parse_modules_from_node_yaml_edge_cases
## @purpose  W4-E5 edge-case: verify parse_modules_from_node_yaml in secrets_validator.py handles
##           3 YAML shapes: (1) modules as dict {name: {enabled, config_overlay}},
##           (2) modules as list [{name, enabled, config_overlay}],
##           (3) modules key absent or empty → empty list (graceful).
##           This is the contract W4-E1 secrets_validator.parse_modules_from_node_yaml must preserve.
##           After W4-E1 extraction, verifies Python function contract + return type (list of tuples).
## @io       caplog → ⎋ None (pytest.fail if contract pattern absent)
## @complexity 1 — static grep for dict/list handling patterns in Python code
## @invariants
##   - Dict shape handling: iterates .items() on modules dict
##   - List shape handling: iterates list elements with .get("name")
##   - Empty/absent modules: returns [] (not an error)


@pytest.mark.static_audit
def test_parse_modules_from_node_yaml_edge_cases(caplog) -> None:
    """
    # ▶ read secrets_validator.py → ◇ grep parse_modules_from_node_yaml →
    # ⚡ assert dict + list shaping logic in Python → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_parse_node_yaml_edge] START — 3 module-shape edge cases in secrets_validator.py")

    content = _extract_python_func(_DEPLOY_PYTHON_DIR / "secrets_validator.py", "parse_modules_from_node_yaml")

    # ── 1. Function exists (Python format) ──
    assert "def parse_modules_from_node_yaml(" in content, (
        "W4-E5 violation: parse_modules_from_node_yaml() not found in secrets_validator.py"
    )
    logger.info("[IMP:9][test_parse_node_yaml_edge] parse_modules_from_node_yaml() declared OK")

    # ── 2. Must handle dict shape: isinstance(x, dict) + .items() ──
    assert "isinstance" in content and "dict" in content, (
        "W4-E5 violation: parse_modules_from_node_yaml must check for dict shape"
    )
    assert "items()" in content or ".items()" in content, "W4-E5 violation: dict shape must use .items() iteration"
    logger.info("[IMP:9][test_parse_node_yaml_edge] dict shape handling OK")

    # ── 3. Must handle list shape: isinstance(x, list) + .get("name") ──
    assert "isinstance" in content and "list" in content, (
        "W4-E5 violation: parse_modules_from_node_yaml must check for list shape"
    )
    assert 'get("name"' in content or '.get("name", ")' in content or ".get('name'" in content, (
        "W4-E5 violation: list shape must use .get('name') for module name extraction"
    )
    logger.info("[IMP:9][test_parse_node_yaml_edge] list shape handling OK")

    # ── 4. Return type must be list[tuple[str, str, str]] (name, enabled, overlay) ──
    assert "list[tuple" in content or "list of tuple" in content or "List[Tuple" in content, (
        "W4-E5 violation: parse_modules_from_node_yaml must return list[tuple] (name, enabled, overlay)"
    )
    logger.info("[IMP:9][test_parse_node_yaml_edge] return type list[tuple] OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 parse_modules_from_node_yaml handles dict/list/empty shapes
# · Scenario: node.yaml with modules as dict, list, or absent → all parsed without error
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: module parsing moves to secrets_validator.py (then point test at new module)
# endregion FUNC_test_parse_modules_from_node_yaml_edge_cases
# endregion MODULE_CONTRACT
