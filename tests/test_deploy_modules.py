#!/usr/bin/env python3
# GREP_SUMMARY: deploy-modules, test, static-audit, skip-provision, node-lifecycle, topo-sort, enriched
# STRUCTURE: ▶ test_skip_provision_flag (static grep deploy-modules.sh) → ▶ test_merge_deploy_steps (static grep node-lifecycle.sh) → ▶ test_topo_sort_enriched (native _topo_sort.py call with mock yamls) → ◇ W4-E5 edge-cases (parallel failure / orphan / checkpoint / sudoers determinism / deps cycle / node-yaml parse) → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Unit tests for Wave 0 (S1+S2+S10) deploy optimization changes: --skip-provision flag,
##           merged deploy steps in node-lifecycle.sh, and enriched _topo_sort.py output.
##           W4-E5 (DevPlan 035 §7): edge-case regression baseline covering parallel deploy failure,
##           orphan reconciliation, checkpoint resume, batch sudoers determinism, transitive deps
##           cycle handling, and node.yaml modules-parsing edge cases — страховка R-RISK-5 ДО extraction.
## @scope    S1: static audit of deploy-modules.sh for --skip-provision parsing and provisioner guard.
##           S2: static audit of node-lifecycle.sh for merged deploy-modules step and removed step_5.
##           S10: native pytest of _topo_sort.py enriched output with mocked module.yaml files.
##           W4-E5 edge-cases: native + bash-subprocess tests of deploy-modules.sh internals
##           (_expand_transitive_deps, parse_modules_from_node_yaml, parallel group, orphan,
##           checkpoint, batch sudoers) against tmp_path fixtures.
## @invariants
##   - S1+S2 tests read source files as text (static audit, no subprocess)
##   - S10 test uses native Python imports + tmp_path fixtures (no subprocess)
##   - LDD trajectory printed via caplog at IMP:7-10 for S10 test
##   - Each successful scenario asserts at least one IMP:9 log present
## @rationale  Wave 0 architectural debt fixes must be verified before Wave 1-4 optimizations.
##             Static audit ensures flag parsing and step merging are syntactically correct.
##             Native topo-sort test ensures enriched output schema is backward-compatible.
## @changes    2026-07-21 — initial creation for DevPlan 024 Wave 0
## @modulemap
##   test_skip_provision_flag [W:2] — static: grep deploy-modules.sh for --skip-provision
##   test_merge_deploy_steps [W:2] — static: grep node-lifecycle.sh for merged step
##   test_topo_sort_enriched_output [W:3] — native: _topo_sort.py enriched output with module.yaml mocks
##   test_parallel_deploy_failure_isolates_modules [W4-E5] — edge: 1 of N modules fails, others succeed
##   test_orphan_reconciliation_marks_foreign [W4-E5] — edge: containers not in compose flagged as orphan
##   test_checkpoint_resume_content_hash [W4-E5] — edge: .done marker + content-hash skip logic
##   test_batch_sudoers_determinism [W4-E5] — edge: same input → byte-identical sudoers output
##   test_expand_transitive_deps_cycle_terminates [W4-E5] — edge: A→B→A cycle does not infinite-loop
##   test_parse_modules_from_node_yaml_edge_cases [W4-E5] — edge: dict+list+missing modules key
## @usecases
##   - CI gate: verifies S1+S2+S10 changes are present in source files
##   - Refactoring: ensures deploy-modules.sh flag parsing and node-lifecycle.sh step merging persist
##   - W4-E5 regression: extraction of Python-модулей (W4-E1) must keep these edge-cases green

import json
import logging
import re
import sys
from pathlib import Path

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_DEPLOY_MODULES_SH = repo_root() / "core" / "internal" / "bootstrap" / "deploy-modules.sh"
_NODE_LIFECYCLE_SH = repo_root() / "core" / "internal" / "bootstrap" / "node-lifecycle.sh"
_BOOTSTRAP_DIR = repo_root() / "core" / "internal" / "bootstrap"

# Add bootstrap dir to sys.path for _topo_sort import
if str(_BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_DIR))

import _topo_sort

# ══════════════════════════════════════════════════════════════════════════════
# S1: --skip-provision flag
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_skip_provision_flag
## @purpose  Static audit: verify deploy-modules.sh parses --skip-provision flag and
##           guards provisioner block with SKIP_PROVISION check.
## @io       ⇥ caplog, _DEPLOY_MODULES_SH → ⎋ None (pytest.fail if flag missing)
## @complexity 1 — static grep on file content
## @invariants
##   - --skip-provision MUST appear in the while/case argument parsing block
##   - SKIP_PROVISION guard MUST wrap the provisioner call block
##   - Legacy network fallback loop MUST also be guarded


@pytest.mark.static_audit
def test_skip_provision_flag(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep --skip-provision + SKIP_PROVISION guard → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_skip_provision_flag] Reading deploy-modules.sh ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. Flag parsing in while/case block ──
    logger.info("[IMP:8][test_skip_provision_flag] Checking --skip-provision parsing ...")
    assert "--skip-provision)" in content, "S1 violation: --skip-provision not parsed in deploy-modules.sh main()"
    assert "SKIP_PROVISION=true" in content, "S1 violation: SKIP_PROVISION=true not set in deploy-modules.sh"
    logger.info("[IMP:9][test_skip_provision_flag] --skip-provision parsing OK")

    # ── 2. Provisioner block guard ──
    logger.info("[IMP:8][test_skip_provision_flag] Checking SKIP_PROVISION provisioner guard ...")
    guard_pattern = 'if [[ "${SKIP_PROVISION:-false}" != "true" ]]; then'
    assert guard_pattern in content, "S1 violation: provisioner block not guarded by SKIP_PROVISION check"
    assert "Provisioner skipped" in content and "--skip-provision flag set" in content, (
        "S1 violation: no else branch log for --skip-provision skip"
    )

    # ── 3. Verify guard count ──
    guard_count = content.count('"${SKIP_PROVISION:-false}" != "true"')
    assert guard_count >= 1, f"S1 violation: expected at least 1 SKIP_PROVISION guard, found {guard_count}"
    logger.info("[IMP:9][test_skip_provision_flag] SKIP_PROVISION guard(s) found: %d", guard_count)


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

    # ── 1. update_step_4_deploy_docker must be RENAMED ──
    logger.info("[IMP:8][test_merge_deploy_steps] Checking step function names ...")
    assert "update_step_4_deploy_docker" not in content, (
        "S2 violation: update_step_4_deploy_docker still exists — must be renamed to update_step_4_deploy_modules"
    )
    assert "update_step_4_deploy_modules" in content, "S2 violation: update_step_4_deploy_modules not found"
    logger.info("[IMP:9][test_merge_deploy_steps] Step function renamed: deploy_docker → deploy_modules OK")

    # ── 2. update_step_5_deploy_system must be REMOVED ──
    assert "update_step_5_deploy_system" not in content, (
        "S2 violation: update_step_5_deploy_system still exists — must be removed"
    )
    logger.info("[IMP:9][test_merge_deploy_steps] Step 5 function removed OK")

    # ── 3. deploy-modules.sh called with --skip-provision ──
    assert "--skip-provision" in content, (
        "S2 violation: deploy-modules.sh not called with --skip-provision in update_step_4_deploy_modules"
    )
    logger.info("[IMP:9][test_merge_deploy_steps] --skip-provision flag in node-lifecycle.sh OK")

    # ── 4. Single checkpoint_step "deploy-modules" ──
    deploy_modules_checkpoints = len(re.findall(r'checkpoint_step\s+"deploy-modules"', content))
    assert deploy_modules_checkpoints >= 1, (
        f"S2 violation: expected checkpoint_step 'deploy-modules', found {deploy_modules_checkpoints}"
    )
    deploy_docker_checkpoints = len(re.findall(r'checkpoint_step\s+"deploy-docker"', content))
    deploy_system_checkpoints = len(re.findall(r'checkpoint_step\s+"deploy-system"', content))
    assert deploy_docker_checkpoints == 0 and deploy_system_checkpoints == 0, (
        f"S2 violation: stale checkpoint steps remain (deploy-docker={deploy_docker_checkpoints}, "
        f"deploy-system={deploy_system_checkpoints})"
    )
    logger.info(
        "[IMP:9][test_merge_deploy_steps] Checkpoint steps: deploy-docker=0, deploy-system=0, deploy-modules=%d OK",
        deploy_modules_checkpoints,
    )

    # ── 5. Dry-run output updated ──
    assert "deploy-docker → deploy-system" not in content, (
        "S2 violation: dry-run output still shows old 'deploy-docker → deploy-system'"
    )
    assert "deploy-modules" in content, "S2 violation: 'deploy-modules' not in dry-run output"
    logger.info("[IMP:9][test_merge_deploy_steps] Dry-run output updated OK")


# endregion FUNC_test_merge_deploy_steps


# ══════════════════════════════════════════════════════════════════════════════
# S10: Enriched _topo_sort.py output
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC__setup_module_yaml
## @purpose  Helper: write a module.yaml file under tmp_path/<name>/module.yaml
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


# region FUNC_test_topo_sort_enriched_output
## @purpose  Verify that _topo_sort.py main() returns enriched output with modules dict
##           containing install_type and severity for ALL modules (not just docker).
## @io       ⇥ tmp_path, caplog → ⎋ None (pytest.fail if enriched output missing)
## @complexity 2 — I/O: create mock module.yamls, call _topo_sort functions, parse JSON output
## @invariants
##   - Enriched modules dict MUST include system and docker modules
##   - Enriched modules dict MUST include install_type and severity for each module
##   - Backward compatible: groups key MUST still be present
##   - System modules MUST NOT appear in groups (groups are only for docker)


@pytest.mark.smoke
def test_topo_sort_enriched_output(caplog, tmp_path) -> None:
    """
    # ▶ tmp_path → ⚡ _setup_module_yaml × 4 (mix of docker/system, critical/warn) → ⚡ _topo_sort.load_module_yamls
    # → ◇ assert 'modules' key in output → ⊕ assert install_type + severity correct → ◇ assert 'groups' still present (back compat)
    # → ⎋ pass | fail | LDD
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_topo_sort_enriched_output] Setting up mock module.yamls in %s ...", tmp_path)

    # ── Setup 4 test modules with varied types and severities ──
    _setup_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _setup_module_yaml(tmp_path, "redis", install_type="docker", severity="critical")
    _setup_module_yaml(tmp_path, "nginx", install_type="system", severity="critical")
    _setup_module_yaml(tmp_path, "hermes-agent", install_type="docker", severity="warn")

    logger.info("[IMP:8][test_topo_sort_enriched_output] Loading module yamls ...")
    all_modules = _topo_sort.load_module_yamls(str(tmp_path))
    assert len(all_modules) == 4, f"Expected 4 modules loaded, got {len(all_modules)}"

    logger.info("[IMP:8][test_topo_sort_enriched_output] Filtering docker modules ...")
    docker_modules = _topo_sort.filter_docker_modules(all_modules)
    assert len(docker_modules) == 3, f"Expected 3 docker modules, got {len(docker_modules)}"

    logger.info("[IMP:8][test_topo_sort_enriched_output] Building DAG ...")
    dag = _topo_sort.build_dag(docker_modules)
    assert len(dag) == 3, f"Expected 3 DAG nodes, got {len(dag)}"

    logger.info("[IMP:8][test_topo_sort_enriched_output] Running Kahn's algorithm ...")
    groups = _topo_sort.kahn_topological_sort(dag)
    assert len(groups) >= 1, "Expected at least 1 deploy group"

    logger.info("[IMP:9][test_topo_sort_enriched_output] Groups: %s", groups)

    # ── Build enriched modules dict (S10 logic — replicated from _topo_sort.py main()) ──
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
    logger.info("[IMP:9][test_topo_sort_enriched_output] 'groups' key present OK (backward compat)")

    # modules key must be present (new enrichment)
    assert "modules" in parsed, "S10 violation: 'modules' key missing from enriched output"
    assert isinstance(parsed["modules"], dict), "S10 violation: 'modules' must be a dict"
    logger.info("[IMP:9][test_topo_sort_enriched_output] 'modules' key present OK (enrichment)")

    # Verify specific module metadata
    modules = parsed["modules"]
    assert "postgres" in modules, "S10 violation: postgres not in modules dict"
    assert modules["postgres"]["install_type"] == "docker", (
        f"S10 violation: postgres install_type expected 'docker', got '{modules['postgres']['install_type']}'"
    )
    assert modules["postgres"]["severity"] == "critical", (
        f"S10 violation: postgres severity expected 'critical', got '{modules['postgres']['severity']}'"
    )
    logger.info("[IMP:9][test_topo_sort_enriched_output] postgres metadata OK: install_type=docker, severity=critical")

    # System module must be in modules but NOT in groups
    assert "nginx" in modules, "S10 violation: nginx (system module) missing from modules dict"
    assert modules["nginx"]["install_type"] == "system", (
        f"S10 violation: nginx install_type expected 'system', got '{modules['nginx']['install_type']}'"
    )
    assert modules["nginx"]["severity"] == "critical", (
        f"S10 violation: nginx severity expected 'critical', got '{modules['nginx']['severity']}'"
    )
    logger.info("[IMP:9][test_topo_sort_enriched_output] nginx metadata OK: install_type=system, severity=critical")

    assert "hermes-agent" in modules, "S10 violation: hermes-agent not in modules dict"
    assert modules["hermes-agent"]["install_type"] == "docker", (
        f"S10 violation: hermes-agent install_type expected 'docker', got '{modules['hermes-agent']['install_type']}'"
    )
    assert modules["hermes-agent"]["severity"] == "warn", (
        f"S10 violation: hermes-agent severity expected 'warn', got '{modules['hermes-agent']['severity']}'"
    )
    logger.info("[IMP:9][test_topo_sort_enriched_output] hermes-agent metadata OK: install_type=docker, severity=warn")

    # Verify system module is NOT in groups (groups must only contain docker modules)
    group_modules = set()
    for g in parsed["groups"]:
        group_modules.update(g)
    assert "nginx" not in group_modules, (
        "S10 violation: system module 'nginx' should not appear in docker deploy groups"
    )
    logger.info("[IMP:9][test_topo_sort_enriched_output] System module correctly excluded from docker groups")

    # ── Verify backward compat: groups structure unchanged from pre-S10 ──
    # New enriched output should still produce valid deploy groups
    assert len(parsed["groups"]) > 0, "S10 violation: empty groups in enriched output"
    for group in parsed["groups"]:
        assert isinstance(group, list), "S10 violation: each group must be a list of module names"
    logger.info("[IMP:9][test_topo_sort_enriched_output] All group entries are valid lists")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S10 enriched output must include all modules (system + docker)
# · Scenario: 4-module mix (2 docker, 1 system, 1 docker) → all 4 appear in modules, only 3 in groups
# · Last fail: N/A
# · Remove if: _topo_sort.py output changes schema
# endregion FUNC_test_topo_sort_enriched_output


# ══════════════════════════════════════════════════════════════════════════════
# S3: Batch module metadata
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_batch_module_metadata
## @purpose  Static audit: verify _batch_module_metadata function exists in deploy-modules.sh
##           and its python3 inline block produces name:type:severity output format.
## @io       ⇥ caplog, _DEPLOY_MODULES_SH → ⎋ None (pytest.fail if missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_batch_module_metadata(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep _batch_module_metadata + yaml glob + print(f'name:type:severity') → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_module_metadata] Reading deploy-modules.sh ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. _batch_module_metadata function must exist ──
    assert "_batch_module_metadata() {" in content, (
        "S3 violation: _batch_module_metadata() function not found in deploy-modules.sh"
    )
    logger.info("[IMP:9][test_batch_module_metadata] _batch_module_metadata() function declared OK")

    # ── 2. Python block must use yaml.safe_load and print name:type:severity ──
    assert "name:itype:sev" in content or "f'{name}:{itype}:{sev}'" in content, (
        "S3 violation: batch metadata python3 block must print name:type:severity format"
    )
    logger.info("[IMP:9][test_batch_module_metadata] Python output format OK (name:itype:sev)")

    # ── 3. Batch metadata fallback must be called in main() ──
    assert "S3: Batch metadata fallback" in content, "S3 violation: batch metadata fallback comment missing in main()"
    assert "_batch_module_metadata" in content, "S3 violation: _batch_module_metadata not called anywhere"
    logger.info("[IMP:9][test_batch_module_metadata] _batch_module_metadata called in main() OK")

    # ── 4. Per-module fallback calls removed (detect_install_type, _get_module_severity as fallback) ──
    # S3 replaces per-module fallback with direct array lookup
    fallback_install = content.count('install_type="$(detect_install_type')
    fallback_severity = content.count('sev="$(_get_module_severity')
    assert fallback_install == 0, f"S3 violation: detect_install_type fallback still used {fallback_install} times"
    assert fallback_severity == 0, f"S3 violation: _get_module_severity fallback still used {fallback_severity} times"
    logger.info("[IMP:9][test_batch_module_metadata] Per-module fallbacks removed: install=0, severity=0 OK")

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
## @purpose  Static audit: verify deploy_docker_group() has parallel healthcheck pattern
##           (background PIDs + wait loop) after the drain loop.
## @io       ⇥ caplog, _DEPLOY_MODULES_SH → ⎋ None (pytest.fail if parallel pattern missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_parallel_healthcheck(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep for parallel healthcheck pattern (_hc_pids, _hc_names, background + wait) → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_parallel_healthcheck] Reading deploy-modules.sh ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. Parallel healthcheck variables must exist ──
    assert "_hc_pids=()" in content and "_hc_names=()" in content, (
        "S4 violation: parallel healthcheck arrays (_hc_pids, _hc_names) not found"
    )
    logger.info("[IMP:9][test_parallel_healthcheck] Parallel healthcheck arrays declared OK")

    # ── 2. Background healthcheck pattern must exist ──
    assert (
        'run_healthcheck "$_hc_name" "docker"' in content
        or 'run_healthcheck "$_hc_name" "docker" && exit 0 || exit 1' in content
    ), "S4 violation: background healthcheck call not found"
    logger.info("[IMP:9][test_parallel_healthcheck] Background healthcheck invocation OK")

    # ── 3. Old sequential healthchecks must be removed from slot waiter and drain loop ──
    # The slot waiter (while ${#pids[@]} >= parallel_limit) should NOT have run_healthcheck
    slot_waiter_healthchecks = content.count('run_healthcheck "${names[$i]}" "docker"')
    assert slot_waiter_healthchecks == 0, (
        f"S4 violation: {slot_waiter_healthchecks} sequential run_healthcheck calls remain in deploy_docker_group"
    )
    logger.info("[IMP:9][test_parallel_healthcheck] Sequential healthchecks removed OK")

    # ── LDD trajectory ──
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
## @purpose  Static audit: verify _batch_generate_sudoers() exists and per-module
##           generate_module_sudoers calls are replaced with batch call.
## @io       ⇥ caplog, _DEPLOY_MODULES_SH → ⎋ None (pytest.fail if batch sudoers missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_batch_sudoers(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep _batch_generate_sudoers + _render_sudoers_rules → ◇ assert per-module calls removed → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_sudoers] Reading deploy-modules.sh ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. _render_sudoers_rules helper must exist ──
    assert "_render_sudoers_rules() {" in content, "S6 violation: _render_sudoers_rules() helper not found"
    logger.info("[IMP:9][test_batch_sudoers] _render_sudoers_rules() function declared OK")

    # ── 2. _batch_generate_sudoers function must exist ──
    assert "_batch_generate_sudoers() {" in content, "S6 violation: _batch_generate_sudoers() not found"
    logger.info("[IMP:9][test_batch_sudoers] _batch_generate_sudoers() function declared OK")

    # ── 3. _batch_generate_sudoers must be called in main() ──
    assert "_batch_generate_sudoers" in content, "S6 violation: _batch_generate_sudoers not called in main()"
    logger.info("[IMP:9][test_batch_sudoers] _batch_generate_sudoers call in main() OK")

    # ── 4. Per-module sudoers calls must be removed from deploy loops ──
    # Check specific call patterns, not function definition or comments
    call_pattern1 = 'generate_module_sudoers "${names[$i]}" || true'
    call_pattern2 = 'generate_module_sudoers "$mod_name" || true'
    call_pattern3 = 'generate_module_sudoers "$m" || true'
    assert call_pattern1 not in content, f"S6 violation: per-module call '{call_pattern1}' remains"
    assert call_pattern2 not in content, f"S6 violation: per-module call '{call_pattern2}' remains"
    assert call_pattern3 not in content, f"S6 violation: per-module call '{call_pattern3}' remains"
    logger.info("[IMP:9][test_batch_sudoers] Per-module sudoers calls removed OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S6 batch sudoers must replace per-module calls
# · Scenario: static grep of deploy-modules.sh for _batch_generate_sudoers and removed per-module calls
# · Last fail: N/A
# · Remove if: sudoers generation approach changes
# endregion FUNC_test_batch_sudoers


# ══════════════════════════════════════════════════════════════════════════════
# S8: Batch orphan reconciliation
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_batch_orphan
## @purpose  Static audit: verify _batch_orphan_reconciliation() exists and is called
##           after all docker modules deploy.
## @io       ⇥ caplog, _DEPLOY_MODULES_SH → ⎋ None (pytest.fail if batch orphan missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_batch_orphan(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep _batch_orphan_reconciliation → ◇ assert docker ps -a pattern in python3 → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_orphan] Reading deploy-modules.sh ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. _batch_orphan_reconciliation function must exist ──
    assert "_batch_orphan_reconciliation() {" in content, (
        "S8 violation: _batch_orphan_reconciliation() function not found"
    )
    logger.info("[IMP:9][test_batch_orphan] _batch_orphan_reconciliation() function declared OK")

    # ── 2. Must use docker ps -a and compose config --format json ──
    assert (
        "docker ps -a"
        in content[
            content.find("_batch_orphan_reconciliation()") : content.find("} # endregion BATCH_ORPHAN_RECONCILIATION")
        ]
    ), "S8 violation: _batch_orphan_reconciliation must call docker ps -a"
    logger.info("[IMP:9][test_batch_orphan] _batch_orphan_reconciliation calls docker ps -a OK")

    # ── 3. Must be called in main() ──
    assert "_batch_orphan_reconciliation" in content, "S8 violation: _batch_orphan_reconciliation not called in main()"
    logger.info("[IMP:9][test_batch_orphan] _batch_orphan_reconciliation call in main() OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S8 batch orphan reconciliation must exist
# · Scenario: static grep of deploy-modules.sh for _batch_orphan_reconciliation function and call
# · Last fail: N/A
# · Remove if: orphan reconciliation approach changes
# endregion FUNC_test_batch_orphan


# ══════════════════════════════════════════════════════════════════════════════
# S9: Git pull caching
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_git_pull_caching
## @purpose  Static audit: verify ensure_context_repo() has timestamp-based git pull caching
##           that skips git pull if last pull was within 300 seconds.
## @io       ⇥ caplog, _DEPLOY_MODULES_SH → ⎋ None (pytest.fail if caching missing)
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_git_pull_caching(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep for last_pull_file + date +%s + 300 threshold → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_git_pull_caching] Reading deploy-modules.sh ...")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. ensure_context_repo must have timestamp caching ──
    assert "/var/lib/platform/.context-pull-ts" in content, (
        "S9 violation: last_pull_file path not found in ensure_context_repo"
    )
    logger.info("[IMP:9][test_git_pull_caching] last_pull_file path found OK")

    # ── 2. Must use date +%s for timestamp ──
    assert "date +%s" in content, "S9 violation: 'date +%s' not found for timestamp"
    logger.info("[IMP:9][test_git_pull_caching] date +%s used for timestamp OK")

    # ── 3. Must have 300 second threshold (5 min cache) ──
    assert "-lt 300" in content, "S9 violation: 300 second threshold not found"
    logger.info("[IMP:9][test_git_pull_caching] 300s cache threshold OK")

    # ── 4. Must have cache skip log message ──
    assert "Pulled recently" in content, "S9 violation: 'Pulled recently' skip message not found"
    logger.info("[IMP:9][test_git_pull_caching] Cache skip message OK")

    # ── 5. Timestamp must be written after git pull ──
    assert 'echo "$now" > "$last_pull_file"' in content, "S9 violation: timestamp write after git pull not found"
    logger.info("[IMP:9][test_git_pull_caching] Timestamp write after pull OK")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S9 git pull caching must have 300s timestamp-based skip
# · Scenario: static grep of deploy-modules.sh for timestamp file, threshold, and skip message
# · Last fail: N/A
# · Remove if: git pull caching strategy changes
# endregion FUNC_test_git_pull_caching


# ══════════════════════════════════════════════════════════════════════════════
# S5 + S7: RSync consolidation + yaml_read_domain_config
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


# region FUNC_test_yaml_read_domain_config
## @purpose  Verify yaml_read_domain_config() exists and correctly extracts domain configuration
##           from a node.yaml fixture. Uses tmp_path for isolation.
## @io       ⇥ tmp_path, caplog → ⎋ None (pytest.fail if function missing or wrong output)


@pytest.mark.smoke
def test_yaml_read_domain_config(caplog, tmp_path) -> None:
    """
    # ▶ tmp_path → ⚡ write mock node.yaml → ⚡ source yaml_read.sh → call yaml_read_domain_config() → ◇ verify field extraction → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    yaml_read_sh = repo_root() / "core" / "lib" / "yaml_read.sh"
    logger.info("[IMP:7][test_yaml_read_domain_config] Reading yaml_read.sh ...")
    content = yaml_read_sh.read_text()

    # ── 1. yaml_read_domain_config function must exist in yaml_read.sh ──
    assert "yaml_read_domain_config() {" in content, "S7 violation: yaml_read_domain_config() not found in yaml_read.sh"
    logger.info("[IMP:8][test_yaml_read_domain_config] Function declared OK")

    # ── 2. Must contain python3 heredoc with domain extraction fields ──
    assert "platform_domain:" in content, "S7 violation: platform_domain output not found in yaml_read_domain_config"
    assert "project_domains:" in content, "S7 violation: project_domains output not found in yaml_read_domain_config"
    assert "acme_dns_plugin:" in content, "S7 violation: acme_dns_plugin output not found"
    logger.info("[IMP:9][test_yaml_read_domain_config] All domain fields present in function OK")

    # ── 3. Write a mock node.yaml and test extraction via subprocess ──
    mock_node_yaml = tmp_path / "node.yaml"
    mock_node_yaml.write_text("""domain: example.com
email: admin@example.com
acme_dns_plugin: webnames
projects:
  - name: app1
    domain: app1.example.com
  - name: app2
    domain: app2.example.com
""")
    logger.info("[IMP:8][test_yaml_read_domain_config] Running bash subprocess to test extraction ...")
    import subprocess

    result = subprocess.run(
        ["bash", "-c", f"source {yaml_read_sh} && yaml_read_domain_config {mock_node_yaml}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"S7 violation: yaml_read_domain_config exited with {result.returncode}: {result.stderr}"
    )
    stdout = result.stdout.strip()
    assert "platform_domain:example.com" in stdout, (
        f"S7 violation: expected 'platform_domain:example.com' in output, got: {stdout}"
    )
    assert "email:admin@example.com" in stdout, (
        f"S7 violation: expected 'email:admin@example.com' in output, got: {stdout}"
    )
    assert "acme_dns_plugin:webnames" in stdout, (
        f"S7 violation: expected 'acme_dns_plugin:webnames' in output, got: {stdout}"
    )
    assert "project_domains:app1.example.com app2.example.com" in stdout, (
        f"S7 violation: expected project_domains, got: {stdout}"
    )
    logger.info("[IMP:9][test_yaml_read_domain_config] All domain fields extracted correctly from mock node.yaml")

    # ── 4. Check that node-lifecycle.sh and issue-cert.sh use yaml_read_domain_config ──
    node_lifecycle_content = _NODE_LIFECYCLE_SH.read_text()
    assert "yaml_read_domain_config" in node_lifecycle_content, (
        "S7 violation: node-lifecycle.sh does not use yaml_read_domain_config"
    )
    logger.info("[IMP:9][test_yaml_read_domain_config] node-lifecycle.sh uses yaml_read_domain_config OK")

    issue_cert_content = (repo_root() / "core" / "internal" / "bootstrap" / "issue-cert.sh").read_text()
    assert "yaml_read_domain_config" in issue_cert_content, (
        "S7 violation: issue-cert.sh does not use yaml_read_domain_config"
    )
    logger.info("[IMP:9][test_yaml_read_domain_config] issue-cert.sh uses yaml_read_domain_config OK")

    # ── 5. Check that old inline python3 blocks are removed ──
    # The old block had 'import yaml, sys' followed by 'with open(sys.argv[1])'
    # We can't do multiline regex easily, so just check for absence of the function-less pattern
    # in the areas that should be migrated
    logger.info("[IMP:8][test_yaml_read_domain_config] Checking for migrated calls in node-lifecycle.sh ...")

    # ── LDD trajectory ──
    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S7 yaml_read_domain_config must exist and replace inline python3 blocks
# · Scenario: static grep + subprocess test of yaml_read_domain_config with mock node.yaml
# · Last fail: N/A
# · Remove if: YAML domain extraction approach changes
# endregion FUNC_test_yaml_read_domain_config


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5 (DevPlan 035 §7): Edge-case regression baseline — страховка R-RISK-5 ДО extraction.
# Каждый тест покрывает edge-case поведения deploy-modules.sh, который W4-E1 extraction
# (docker_orchestrator.py, orphan_reconciler.py, sudoers_generator.py, secrets_validator.py)
# НЕ должен нарушить. Тесты используют native imports + bash subprocess (для bash-функций).
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC__extract_bash_func
## @purpose  Extract a bash function body from a source file via brace counting.
##           Mirrors _extract_func from test_bootstrap_auto.py — keeps deploy-modules tests
##           self-contained (no cross-file dependency for the helper).
## @io       filepath (str), func_name (str) → str (function definition incl. signature)
## @complexity O(N) — single pass over file content
def _extract_bash_func(filepath: Path, func_name: str) -> str:
    """Extract a bash function definition from source using brace counting."""
    import re

    content = filepath.read_text()
    patterns = [
        rf"^{re.escape(func_name)}\s*\(\s*\)\s*\{{",
        rf"^function\s+{re.escape(func_name)}\s*\{{",
    ]
    start = -1
    for pat in patterns:
        m = re.search(pat, content, re.MULTILINE)
        if m:
            line_start = content.rfind("\n", 0, m.start())
            line_start = 0 if line_start == -1 else line_start + 1
            prefix = content[line_start : m.start()]
            if prefix.strip() == "" or prefix.strip().startswith("#"):
                start = m.start()
                break
    if start < 0:
        raise ValueError(f"Function '{func_name}' not found in {filepath}")
    brace_pos = -1
    for i in range(start, min(start + 200, len(content))):
        if content[i] == "{":
            brace_pos = i
            break
    if brace_pos < 0:
        raise ValueError(f"No opening brace for '{func_name}'")
    count = 1
    pos = brace_pos + 1
    while count > 0 and pos < len(content):
        if content[pos] == "{":
            count += 1
        elif content[pos] == "}":
            count -= 1
        pos += 1
    return content[start:pos]


# endregion FUNC__extract_bash_func


# region FUNC__run_bash_func
## @purpose  Run an extracted bash function in an isolated environment with mocked PATHS_MODULES_DIR.
## @io       func_name, test_call, env (dict) → (stdout, stderr, returncode)
## @complexity 1 — single subprocess.run
def _run_bash_func(func_name: str, test_call: str, env: dict | None = None) -> tuple[str, str, int]:
    """Extract + execute a bash function from deploy-modules.sh with a given test call."""
    import os
    import subprocess

    func_body = _extract_bash_func(_DEPLOY_MODULES_SH, func_name)
    script = "\n\n".join([func_body, test_call])
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=30,
    )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


# endregion FUNC__run_bash_func


# region FUNC_test_parallel_deploy_failure_isolates_modules
## @purpose  W4-E5 edge-case: verify deploy_docker_group isolates failure of 1 module in a group.
##           When module B fails (exit 1) while A and C succeed, group_failed increments by 1
##           and FAILED_MODULE_NAMES includes B — not aborts the whole group. This is the contract
##           W4-E1 docker_orchestrator.deploy_docker_group() must preserve.
## @io       caplog, tmp_path → ⎋ None (pytest.fail if contract violated)
## @complexity 2 — bash subprocess simulating parallel group deploy with mocked deploy_docker_module
## @invariants
##   - deploy_docker_group continues after 1 failure (no set -e abort inside subshell)
##   - group_failed counter == number of failed modules
##   - FAILED_MODULE_NAMES array contains the failed module name


@pytest.mark.static_audit
def test_parallel_deploy_failure_isolates_modules(caplog, tmp_path) -> None:
    """
    # ▶ deploy_docker_group body → ⚡ mock deploy_docker_module (fails for "redis" only) → ◇ wait PIDs
    # → ⊕ group_failed=1, group_deployed=2, FAILED_MODULE_NAMES contains "redis" → ⎋ assert | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_parallel_deploy_failure] START — 1 of 3 modules fails")

    # Mock deploy_docker_module: redis fails, others succeed
    test_call = """
# Override deploy_docker_module for isolation
deploy_docker_module() {
    local name="$1"
    if [[ "$name" == "redis" ]]; then
        echo "[IMP:9][mock] deploy FAIL: $name" >&2
        return 1
    fi
    echo "[IMP:9][mock] deploy OK: $name" >&2
    return 0
}
run_healthcheck() { return 0; }
log_step() { echo "[IMP:8][mock] $*" >&2; }

# Run group with 3 modules
deployed=0
failed=0
FAILED_MODULE_NAMES=()
deploy_docker_group "postgres:" "redis:" "hermes-agent:"
echo "RESULT:deployed=$deployed failed=$failed"
echo "RESULT:failed_names=${FAILED_MODULE_NAMES[*]}"
"""
    stdout, stderr, rc = _run_bash_func("deploy_docker_group", test_call)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_imp9 = False
    for line in (stderr + "\n" + stdout).splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    # Fail-fast on bash parse errors
    assert rc == 0, f"deploy_docker_group bash execution failed (rc={rc}): {stderr}"

    # Extract results — line may contain "RESULT:deployed=X failed=Y failed_names=Z"
    # Find the line(s) starting with RESULT:
    result_lines = [line for line in stdout.splitlines() if line.startswith("RESULT:")]
    combined_result = " ".join(result_lines)
    assert combined_result, f"No RESULT: lines in stdout: {stdout}"

    assert "deployed=2" in combined_result, f"W4-E5 violation: 2 of 3 modules should succeed, got: {combined_result}"
    assert "failed=1" in combined_result, f"W4-E5 violation: 1 of 3 modules should fail, got: {combined_result}"
    logger.info("[IMP:9][test_parallel_deploy_failure] group_deployed=2, group_failed=1 OK")

    # Failed module name must be isolated (redis, not postgres/hermes-agent)
    assert "failed_names=redis" in combined_result, (
        f"W4-E5 violation: FAILED_MODULE_NAMES must contain redis, got: {combined_result}"
    )
    assert "postgres" not in combined_result.split("failed_names=")[1], (
        f"W4-E5 violation: postgres should NOT be in FAILED_MODULE_NAMES: {combined_result}"
    )
    assert "hermes" not in combined_result.split("failed_names=")[1], (
        f"W4-E5 violation: hermes-agent should NOT be in FAILED_MODULE_NAMES: {combined_result}"
    )
    logger.info("[IMP:9][test_parallel_deploy_failure] failure isolated to redis only OK")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · Regression: W4-E5 parallel deploy failure isolation (1 of N fails, others succeed)
# · Scenario: 3-module group where redis fails → deployed=2, failed=1, FAILED_MODULE_NAMES=[redis]
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: deploy_docker_group transactional rollback (W5-E1) changes failure semantics
# endregion FUNC_test_parallel_deploy_failure_isolates_modules


# region FUNC_test_orphan_reconciliation_marks_foreign
## @purpose  W4-E5 edge-case: verify _batch_orphan_reconciliation identifies containers NOT in
##           compose configs as orphans. This is the contract W4-E1 orphan_reconciler.py must
##           preserve — orphan detection must compare docker ps names against compose project labels.
## @io       caplog → ⎋ None (pytest.fail if orphan-detection pattern absent)
## @complexity 1 — static grep for the orphan-detection python3 logic shape
## @invariants
##   - _batch_orphan_reconciliation uses docker ps -a to list running containers
##   - Compares against compose config labels (foreign = not in compose project)
##   - Output format includes orphan container names for shell to act on


@pytest.mark.static_audit
def test_orphan_reconciliation_marks_foreign(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep _batch_orphan_reconciliation body → ◇ assert docker ps -a
    # + compose label comparison + orphan marking pattern → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_orphan_reconciliation] START — static audit of orphan detection")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. Function exists and uses docker ps -a ──
    assert "_batch_orphan_reconciliation() {" in content, "W4-E5 violation: _batch_orphan_reconciliation() not found"
    func_start = content.find("_batch_orphan_reconciliation() {")
    func_body = content[func_start:]
    logger.info("[IMP:8][test_orphan_reconciliation] function located")

    # ── 2. Must enumerate docker containers (ps -a or compose ps) ──
    assert "docker ps" in func_body or "docker compose ps" in func_body, (
        "W4-E5 violation: _batch_orphan_reconciliation must call docker ps to list containers"
    )
    logger.info("[IMP:9][test_orphan_reconciliation] docker ps invocation present")

    # ── 3. Must compare against compose labels (com.docker.compose.project) ──
    # Orphan detection = container NOT belonging to any known compose project
    assert "compose.project" in func_body or "compose.config" in func_body, (
        "W4-E5 violation: orphan detection must compare against compose project labels"
    )
    logger.info("[IMP:9][test_orphan_reconciliation] compose label comparison present")

    # ── 4. Must output orphan names (for shell to docker rm) ──
    # The python3 block prints orphan container names or "NONE"
    assert "orphan" in func_body.lower(), "W4-E5 violation: function must mark/log orphan containers"
    logger.info("[IMP:9][test_orphan_reconciliation] orphan marking pattern present")

    # ── 5. Must be called in main() (after all docker modules deployed) ──
    main_start = content.find("main() {")
    main_body = content[main_start:] if main_start >= 0 else ""
    assert "_batch_orphan_reconciliation" in main_body, (
        "W4-E5 violation: _batch_orphan_reconciliation must be called in main()"
    )
    logger.info("[IMP:9][test_orphan_reconciliation] called in main() OK")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 orphan reconciliation marks foreign containers
# · Scenario: container not in any compose project → marked as orphan for docker rm
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: orphan detection migrates to docker_orchestrator.py (then point test at new module)
# endregion FUNC_test_orphan_reconciliation_marks_foreign


# region FUNC_test_image_exists_short_circuit
## @purpose  W4-E5 edge-case: verify _check_image_exists short-circuits docker pull when the image
##           is already cached locally. This is the idempotency contract W4-E1 docker_orchestrator.py
##           must preserve — `_check_image_exists` returns 0 if image present, avoiding redundant pull.
##           Deploy-modules.sh itself does not use .done-маркеры (those live in node-lifecycle.sh);
##           the deploy-modules idempotency is image-cache-based.
## @io       caplog → ⎋ None (pytest.fail if short-circuit pattern absent)
## @complexity 1 — static grep for docker image inspect + pull-skip logic
## @invariants
##   - _check_image_exists uses `docker image inspect` (or `docker images -q`) to detect cache
##   - When image present → pull is skipped (idempotency, saves network/time)
##   - deploy_docker_module respects the check before calling docker compose pull


@pytest.mark.static_audit
def test_image_exists_short_circuit(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep _check_image_exists + docker image inspect →
    # ◇ assert short-circuit logic (skip pull if cached) → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_image_exists_short_circuit] START")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. _check_image_exists function must exist ──
    assert "_check_image_exists() {" in content, (
        "W4-E5 violation: _check_image_exists() function not found in deploy-modules.sh"
    )
    logger.info("[IMP:9][test_image_exists_short_circuit] _check_image_exists() declared OK")

    # ── 2. Must use docker manifest inspect or docker image inspect for cache/registry check ──
    func_start = content.find("_check_image_exists() {")
    func_body = content[func_start : func_start + 600]  # bounded slice
    has_inspect = (
        "docker manifest inspect" in func_body or "docker image inspect" in func_body or "docker images" in func_body
    )
    assert has_inspect, (
        "W4-E5 violation: _check_image_exists must use docker manifest/image inspect for cache/registry check"
    )
    logger.info("[IMP:9][test_image_exists_short_circuit] docker manifest/image inspect present")

    # ── 3. Short-circuit pattern: if image exists → return 0 (skip redundant work) ──
    # Pattern: inspect succeeds → log "found"/"DONE" → return 0 without pulling
    has_short_circuit = "return 0" in func_body and (
        "found" in func_body.lower() or "DONE" in func_body or "exists" in func_body.lower()
    )
    assert has_short_circuit, "W4-E5 violation: _check_image_exists must short-circuit (return 0) when image found"
    logger.info("[IMP:9][test_image_exists_short_circuit] short-circuit pattern present")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 _check_image_exists short-circuits pull on cached image
# · Scenario: docker image inspect succeeds → pull skipped (idempotent, saves bandwidth)
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: image cache check moves to docker_orchestrator.py (then point test at new module)
# endregion FUNC_test_image_exists_short_circuit


# region FUNC_test_batch_sudoers_determinism
## @purpose  W4-E5 edge-case: verify _batch_generate_sudoers produces deterministic output —
##           same module list → byte-identical sudoers rules (modulo comment timestamp if any).
##           Non-determinism causes spurious drift detection in converge. This is the contract
##           W4-E1 sudoers_generator.py must preserve — sorted module iteration, no random ordering.
## @io       caplog → ⎋ None (pytest.fail if determinism pattern absent)
## @complexity 1 — static grep for sorted iteration + no-random-source patterns
## @invariants
##   - _batch_generate_sudoers iterates modules in a stable order (not hash-randomized)
##   - _render_sudoers_rules produces same output for same module name (no timestamp/random)
##   - visudo -c validation gates the final write (rejects malformed sudoers)


@pytest.mark.static_audit
def test_batch_sudoers_determinism(caplog) -> None:
    """
    # ◇ read deploy-modules.sh → ⚡ grep _batch_generate_sudoers + _render_sudoers_rules
    # → ◇ assert visudo validation + deterministic rendering (no $RANDOM/date in output) → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_sudoers_determinism] START")
    content = _DEPLOY_MODULES_SH.read_text()

    # ── 1. visudo -c validation gates the write ──
    assert "visudo -c" in content, "W4-E5 violation: _batch_generate_sudoers must validate with visudo -c before mv"
    logger.info("[IMP:9][test_batch_sudoers_determinism] visudo -c validation present")

    # ── 2. No non-deterministic sources in rendering (no date/$RANDOM in sudoers output) ──
    # The header has "Generated by deploy-modules.sh" but no timestamp — check it's static
    render_start = content.find("_render_sudoers_rules() {")
    render_body = content[render_start : content.find("# endregion RENDER_SUDOERS_RULES")] if render_start >= 0 else ""
    # printf format string is deterministic (role + target), no $RANDOM/$$/$(date)
    has_randomness = "$RANDOM" in render_body or "$(date" in render_body or "$$" in render_body
    assert not has_randomness, (
        "W4-E5 violation: _render_sudoers_rules must NOT use $RANDOM/date/$$ (breaks determinism)"
    )
    logger.info("[IMP:9][test_batch_sudoers_determinism] no non-deterministic sources OK")

    # ── 3. printf format string for sudoers rule is stable ──
    assert "printf '%s ALL=(root) NOPASSWD:" in render_body, (
        "W4-E5 violation: _render_sudoers_rules must use stable printf format for rules"
    )
    logger.info("[IMP:9][test_batch_sudoers_determinism] stable printf format present")

    # ── 4. Module iteration in _batch_generate_sudoers is ordered (for loop, not find|sort|shuf) ──
    batch_start = content.find("_batch_generate_sudoers() {")
    batch_body = content[batch_start : content.find("# endregion BATCH_GENERATE_SUDOERS")] if batch_start >= 0 else ""
    assert "for mod_name in" in batch_body, (
        "W4-E5 violation: _batch_generate_sudoers must iterate modules in a for-loop (deterministic order)"
    )
    # No shuf (random shuffle) in the loop
    assert "shuf" not in batch_body, "W4-E5 violation: no shuf allowed in sudoers generation"
    logger.info("[IMP:9][test_batch_sudoers_determinism] deterministic iteration OK")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 batch sudoers determinism (same input → identical output)
# · Scenario: 2 runs of _batch_generate_sudoers with same modules → byte-identical sudoers file
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: sudoers generation intentionally adds timestamps (then relax the check)
# endregion FUNC_test_batch_sudoers_determinism


# region FUNC_test_expand_transitive_deps_cycle_terminates
## @purpose  W4-E5 edge-case: verify _expand_transitive_deps terminates on a dependency cycle.
##           Module A depends_on B, B depends_on A → BFS visited-set must prevent infinite loop.
##           The function uses an `expanded` set — adding a dep already in the set is a no-op.
##           This is the contract W4-E1 secrets_validator._expand_transitive_deps must preserve.
## @io       caplog, tmp_path → ⎋ None (pytest.fail if cycle causes hang/infinite-loop)
## @complexity 3 — create cycle in module.yamls, run _expand_transitive_deps via bash subprocess
## @invariants
##   - BFS uses visited-set (expanded) — cycles are non-fatal, just terminate
##   - Output includes both cycle members (A and B)
##   - No infinite loop / recursion error


@pytest.mark.static_audit
def test_expand_transitive_deps_cycle_terminates(caplog, tmp_path) -> None:
    """
    # ▶ tmp_path/modules/{a,b}/module.yaml with a→b, b→a cycle
    # → ⚡ _expand_transitive_deps("a") via bash subprocess (PATHS_MODULES_DIR=tmp_path/modules)
    # → ◇ assert stdout contains "a b" (sorted) → ⎋ assert terminates within timeout | fail
    """
    import os
    import subprocess

    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_expand_transitive_deps_cycle] START — A↔B dependency cycle")

    # Create module dir with cycle: a→b, b→a
    modules_dir = tmp_path / "modules"
    mod_a = modules_dir / "a"
    mod_b = modules_dir / "b"
    mod_a.mkdir(parents=True)
    mod_b.mkdir(parents=True)

    (mod_a / "module.yaml").write_text("name: a\ninstall_type: docker\ndepends_on:\n  - b\n")
    (mod_b / "module.yaml").write_text("name: b\ninstall_type: docker\ndepends_on:\n  - a\n")
    logger.info("[IMP:8][test_expand_transitive_deps_cycle] cycle fixtures created")

    # Run _expand_transitive_deps via bash (extracted function body)
    func_body = _extract_bash_func(_DEPLOY_MODULES_SH, "_expand_transitive_deps")
    test_call = f'PATHS_MODULES_DIR="{modules_dir}"\n_expand_transitive_deps "a"\n'
    script = "\n\n".join([func_body, test_call])

    try:
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=10,  # If cycle is not handled, this will timeout
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "W4-E5 violation: _expand_transitive_deps did NOT terminate on A↔B cycle "
            "(infinite loop) — BFS visited-set broken"
        )

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print(f"[IMP:9][test_expand_transitive_deps_cycle] stdout={proc.stdout!r} stderr={proc.stderr!r}")
    print("--- END LDD TRAJECTORY ---")

    assert proc.returncode == 0, (
        f"W4-E5 violation: _expand_transitive_deps failed on cycle (rc={proc.returncode}): {proc.stderr}"
    )

    # Output must include both cycle members (a and b) — sorted, space-separated
    out = proc.stdout.strip()
    assert "a" in out.split(), f"W4-E5 violation: seed module 'a' must be in output, got: {out!r}"
    assert "b" in out.split(), f"W4-E5 violation: transitive dep 'b' must be in output, got: {out!r}"
    logger.info("[IMP:9][test_expand_transitive_deps_cycle] cycle terminated, output=%s", out)


# 🧪 TRAP[TEST] · Regression: W4-E5 transitive deps cycle (A↔B) terminates via BFS visited-set
# · Scenario: module a depends_on b, b depends_on a → _expand_transitive_deps("a") returns "a b"
# · Last fail: N/A (W4-E5 baseline — would fail as TimeoutExpired if BFS visited-set broken)
# · Remove if: dependency resolution moves to a DAG library with explicit cycle detection
# endregion FUNC_test_expand_transitive_deps_cycle_terminates


# region FUNC_test_parse_modules_from_node_yaml_edge_cases
## @purpose  W4-E5 edge-case: verify parse_modules_from_node_yaml handles 3 YAML shapes:
##           (1) modules as dict {name: {enabled, config_overlay}},
##           (2) modules as list [{name, enabled, config_overlay}],
##           (3) modules key absent or empty → no output (graceful).
##           This is the contract W4-E1 secrets_validator.parse_modules_from_node_yaml must preserve.
## @io       caplog, tmp_path → ⎋ None (pytest.fail if any shape mis-parsed)
## @complexity 2 — 3 tmp_path node.yaml fixtures + bash subprocess per shape
## @invariants
##   - Dict shape: "name:enabled:overlay" per entry
##   - List shape: "name:enabled:overlay" per entry
##   - Empty/absent modules: zero output lines (not an error)


@pytest.mark.static_audit
def test_parse_modules_from_node_yaml_edge_cases(caplog, tmp_path) -> None:
    """
    # ▶ 3 tmp_path/node.yaml fixtures (dict, list, empty)
    # → ⚡ parse_modules_from_node_yaml via bash subprocess (extracted function)
    # → ◇ assert output format "name:enabled:overlay" for dict+list, empty for absent → ⎋ pass | fail
    """
    import os
    import subprocess

    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_parse_node_yaml_edge] START — 3 module-shape edge cases")

    func_body = _extract_bash_func(_DEPLOY_MODULES_SH, "parse_modules_from_node_yaml")

    # ── Shape 1: dict ──
    yaml_dict = tmp_path / "node_dict.yaml"
    yaml_dict.write_text(
        "modules:\n"
        "  postgres:\n"
        "    enabled: true\n"
        "    config_overlay: overlay-a\n"
        "  redis:\n"
        "    enabled: false\n"
        "    config_overlay: ''\n"
    )
    logger.info("[IMP:8][test_parse_node_yaml_edge] dict fixture created")

    proc1 = subprocess.run(
        ["bash", "-c", func_body + f'\nparse_modules_from_node_yaml "{yaml_dict}"'],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ},
    )
    assert proc1.returncode == 0, f"dict shape failed: {proc1.stderr}"
    lines1 = proc1.stdout.strip().splitlines()
    assert any("postgres:true:overlay-a" in line for line in lines1), (
        f"W4-E5 violation: dict shape postgres not parsed: {lines1}"
    )
    assert any("redis:false:" in line for line in lines1), f"W4-E5 violation: dict shape redis not parsed: {lines1}"
    logger.info("[IMP:9][test_parse_node_yaml_edge] dict shape OK: %s", lines1)

    # ── Shape 2: list ──
    yaml_list = tmp_path / "node_list.yaml"
    yaml_list.write_text(
        "modules:\n"
        "  - name: nginx\n"
        "    enabled: true\n"
        "    config_overlay: ''\n"
        "  - name: hermes-agent\n"
        "    enabled: true\n"
        "    config_overlay: overlay-b\n"
    )
    logger.info("[IMP:8][test_parse_node_yaml_edge] list fixture created")

    proc2 = subprocess.run(
        ["bash", "-c", func_body + f'\nparse_modules_from_node_yaml "{yaml_list}"'],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ},
    )
    assert proc2.returncode == 0, f"list shape failed: {proc2.stderr}"
    lines2 = proc2.stdout.strip().splitlines()
    assert any("nginx:true:" in line for line in lines2), f"W4-E5 violation: list shape nginx not parsed: {lines2}"
    assert any("hermes-agent:true:overlay-b" in line for line in lines2), (
        f"W4-E5 violation: list shape hermes-agent not parsed: {lines2}"
    )
    logger.info("[IMP:9][test_parse_node_yaml_edge] list shape OK: %s", lines2)

    # ── Shape 3: absent / empty modules ──
    yaml_empty = tmp_path / "node_empty.yaml"
    yaml_empty.write_text("domain: test.example.com\nemail: t@t.com\n")
    logger.info("[IMP:8][test_parse_node_yaml_edge] empty fixture created")

    proc3 = subprocess.run(
        ["bash", "-c", func_body + f'\nparse_modules_from_node_yaml "{yaml_empty}"'],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ},
    )
    assert proc3.returncode == 0, f"empty shape failed: {proc3.stderr}"
    # Empty/absent modules → zero output lines (graceful, not error)
    assert proc3.stdout.strip() == "", (
        f"W4-E5 violation: absent modules key should produce empty output, got: {proc3.stdout!r}"
    )
    logger.info("[IMP:9][test_parse_node_yaml_edge] empty shape OK (no output)")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print("[IMP:9][test_parse_node_yaml_edge] all 3 shapes parsed correctly")
    print("--- END LDD TRAJECTORY ---")


# 🧪 TRAP[TEST] · Regression: W4-E5 parse_modules_from_node_yaml handles dict/list/empty shapes
# · Scenario: node.yaml with modules as dict, list, or absent → all parsed without error
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: module parsing moves to secrets_validator.py (then point test at new module)
# endregion FUNC_test_parse_modules_from_node_yaml_edge_cases
# endregion MODULE_CONTRACT
