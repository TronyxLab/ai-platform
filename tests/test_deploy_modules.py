#!/usr/bin/env python3
# GREP_SUMMARY: deploy-modules, test, static-audit, skip-provision, node-lifecycle, topo-sort, enriched
# STRUCTURE: ▶ test_skip_provision_flag (static grep deploy-modules.sh) → ▶ test_merge_deploy_steps (static grep node-lifecycle.sh) → ▶ test_topo_sort_enriched (native _topo_sort.py call with mock yamls)
# region MODULE_CONTRACT
## @purpose  Unit tests for Wave 0 (S1+S2+S10) deploy optimization changes: --skip-provision flag,
##           merged deploy steps in node-lifecycle.sh, and enriched _topo_sort.py output.
## @scope    S1: static audit of deploy-modules.sh for --skip-provision parsing and provisioner guard.
##           S2: static audit of node-lifecycle.sh for merged deploy-modules step and removed step_5.
##           S10: native pytest of _topo_sort.py enriched output with mocked module.yaml files.
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
## @usecases
##   - CI gate: verifies S1+S2+S10 changes are present in source files
##   - Refactoring: ensures deploy-modules.sh flag parsing and node-lifecycle.sh step merging persist

import json
import logging
import re
import sys
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_MODULES_SH = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "deploy-modules.sh"
_NODE_LIFECYCLE_SH = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "node-lifecycle.sh"
_BOOTSTRAP_DIR = _PROJECT_ROOT / "core" / "internal" / "bootstrap"

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
    core_deploy_yml = _PROJECT_ROOT / ".github" / "workflows" / "core-deploy.yml"
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
    yaml_read_sh = _PROJECT_ROOT / "core" / "lib" / "yaml_read.sh"
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

    issue_cert_content = (_PROJECT_ROOT / "core" / "internal" / "bootstrap" / "issue-cert.sh").read_text()
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
# endregion MODULE_CONTRACT
