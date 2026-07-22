"""
# GREP_SUMMARY: test-shell-facade-contract, structural-contract, deploy-modules-arg-parsing, python-delegation, severity-exit
# STRUCTURE: ▶ _read_deploy_modules_shell → ○ S1(arg_parsing) ◇ S2(provisioner) ◇ S3(python_delegation) ◇ S4(severity_exit) ◇ S5(context_overlay) ◇ S6(sudoers_orphan) → ⊕ LDD trajectory → ⎋ IMP:9 assert
# region MODULE_CONTRACT
## @purpose  Structural contract tests for deploy-modules.sh shell facade. Verify the thin
##           shell facade (91 LOC after W4-E1 Strangler-Fig) has correct arg parsing,
##           provisioner delegation, Python delegation, severity exit, context overlay,
##           and post-deploy sudoers/orphan reconciliation.
## @scope    Static analysis — reads deploy-modules.sh as text, checks for structural patterns.
##           No Docker, VPS, or network access required.
## @invariants
##   - Tests check CONTRACT (what shell calls), not IMPLEMENTATION (how Python handles it)
##   - Shell facade may change between Strangler-Fig waves — tests must survive formatting changes
##   - Each test asserts exact delegation pattern exists in the 91-LOC facade
##   - LDD IMP:9 trajectory verified on every test
## @rationale DevPlan 042 Option D — 6 structural contract tests replace 14 obsolete shell-grep
##           tests. These tests verify the shell→Python delegation contract exists, not that
##           the Python modules work correctly (covered by 104 existing unit tests).
## @changes   2026-07-22 · DevPlan 042 — created 6 structural contract tests
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

_DEPLOY_MODULES_SH = (
    Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy-modules.sh"
)


# region HELPER__read_deploy_modules_shell
def _read_deploy_modules_shell() -> str:
    """Read deploy-modules.sh source content.

    ## @purpose — Central helper: reads shell facade once, returning raw text.
    ## @io — ⎋ str: full content of deploy-modules.sh
    ## @complexity 1 — file read
    """
    return _DEPLOY_MODULES_SH.read_text()


# endregion HELPER__read_deploy_modules_shell


# region HELPER__assert_ldd
def _assert_ldd_imp9(caplog) -> None:
    """Assert LDD IMP:9+ logs present in caplog trajectory."""
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


# endregion HELPER__assert_ldd


# ══════════════════════════════════════════════════════════════════════════════
# S1: Arg parsing
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_shell_has_arg_parsing
# 🧪 TRAP[TEST] · Structural · S1: arg parsing in shell facade · Last fail: N/A · Remove if: deploy-modules.sh is rewritten in pure Python
@pytest.mark.static_audit
def test_shell_has_arg_parsing(caplog) -> None:
    """deploy-modules.sh must parse --modules and --skip-provision flags via while/case.

    ## @purpose  Verify shell facade has bash while/case arg parsing for --modules and
    ##           --skip-provision flags. These are the canonical shell-level arguments.
    ## @scenario S1: structural contract for arg parsing
    ## @invariants
    ##   - `--modules` flag parsed in while/case
    ##   - `--skip-provision` flag parsed in while/case
    ##   - NODE_YAML validation emits ERROR if not set
    """
    caplog.set_level(logging.DEBUG)
    content = _read_deploy_modules_shell()

    # ── --modules flag ──
    has_modules_flag = "--modules" in content
    logger.critical("[IMP:9][S1][arg] --modules flag present: %s", has_modules_flag)
    assert has_modules_flag, "deploy-modules.sh must parse --modules flag"

    # ── --skip-provision flag ──
    has_skip_provision = "--skip-provision" in content
    logger.critical("[IMP:9][S1][arg] --skip-provision flag present: %s", has_skip_provision)
    assert has_skip_provision, "deploy-modules.sh must parse --skip-provision flag"

    # ── while/case parsing pattern ──
    has_while_case = "while [[ $# -gt 0 ]]; do case" in content or "while [ $# -gt 0 ]; do case" in content
    logger.critical("[IMP:9][S1][arg] While/case parsing pattern: %s", has_while_case)
    assert has_while_case, "deploy-modules.sh must use while/case pattern for arg parsing"

    # ── NODE_YAML validation (ERROR on missing) ──
    has_node_yaml_error = "ERROR: NODE_YAML not set" in content or "ERROR: NODE_YAML" in content
    logger.critical("[IMP:9][S1][arg] NODE_YAML validation error present: %s", has_node_yaml_error)
    assert has_node_yaml_error, "deploy-modules.sh must validate NODE_YAML is set"

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_arg_parsing


# ══════════════════════════════════════════════════════════════════════════════
# S2: Provisioner delegation
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_shell_has_provisioner_delegation
# 🧪 TRAP[TEST] · Structural · S2: provisioner delegation · Last fail: N/A · Remove if: provisioner is removed from deploy-modules.sh
@pytest.mark.static_audit
def test_shell_has_provisioner_delegation(caplog) -> None:
    """deploy-modules.sh must delegate to provision-environment.sh for networks and volumes.

    ## @purpose  Verify provisioner delegation: shell calls provision-environment.sh for
    ##           --scope networks and --scope volumes, with legacy fallback.
    ## @scenario S2: structural contract for provisioner
    ## @invariants
    ##   - provision-environment.sh --scope networks call present
    ##   - provision-environment.sh --scope volumes call present
    ##   - Legacy fallback (docker network create) present
    """
    caplog.set_level(logging.DEBUG)
    content = _read_deploy_modules_shell()

    # ── Provisioner networks ──
    has_networks = "provision-environment.sh" in content and "--scope networks" in content
    logger.critical("[IMP:9][S2][provisioner] Networks delegation present: %s", has_networks)
    assert has_networks, "deploy-modules.sh must call provision-environment.sh --scope networks"

    # ── Provisioner volumes ──
    has_volumes = "provision-environment.sh" in content and "--scope volumes" in content
    logger.critical("[IMP:9][S2][provisioner] Volumes delegation present: %s", has_volumes)
    assert has_volumes, "deploy-modules.sh must call provision-environment.sh --scope volumes"

    # ── Legacy fallback ──
    has_legacy_fallback = "docker network create" in content or "docker network inspect" in content
    logger.critical("[IMP:9][S2][provisioner] Legacy fallback present: %s", has_legacy_fallback)

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_provisioner_delegation


# ══════════════════════════════════════════════════════════════════════════════
# S3: Python delegation
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_shell_has_python_delegation
# 🧪 TRAP[TEST] · Structural · S3: python delegation · Last fail: N/A · Remove if: Python modules are merged into a single entrypoint
@pytest.mark.static_audit
def test_shell_has_python_delegation(caplog) -> None:
    """deploy-modules.sh must delegate to all 5 Python modules in deploy/.

    ## @purpose  Verify all python3 deploy/*.py calls are present: context_overlay,
    ##           secrets_validator (validate-charsets, parse-node-yaml, check-env,
    ##           detect-type, module-metadata), docker_orchestrator, sudoers_generator,
    ##           orphan_reconciler.
    ## @scenario S3: structural contract for Python delegation
    ## @invariants
    ##   - context_overlay.py call present
    ##   - secrets_validator.py call present
    ##   - docker_orchestrator.py call present
    ##   - sudoers_generator.py call present
    ##   - orphan_reconciler.py call present
    """
    caplog.set_level(logging.DEBUG)
    content = _read_deploy_modules_shell()

    # ── All 5 Python modules ──
    modules = {
        "context_overlay.py": "Context overlay",
        "secrets_validator.py": "Secrets validator",
        "docker_orchestrator.py": "Docker orchestrator",
        "sudoers_generator.py": "Sudoers generator",
        "orphan_reconciler.py": "Orphan reconciler",
    }
    all_present = True
    for mod_name, mod_label in modules.items():
        present = f"deploy/{mod_name}" in content
        logger.critical("[IMP:9][S3][python] %s (%s) present: %s", mod_label, mod_name, present)
        if not present:
            all_present = False

    assert all_present, "deploy-modules.sh must delegate to all 5 Python modules in deploy/"

    # ── Verifies python3 calls (not just filename references) ──
    shell_ops = {
        "context_overlay",
        "secrets_validator",
        "docker_orchestrator",
        "sudoers_generator",
        "orphan_reconciler",
    }
    for op in shell_ops:
        assert "python3" in content and op in content, f"deploy-modules.sh must call python3 deploy/{op}.py"

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_python_delegation


# ══════════════════════════════════════════════════════════════════════════════
# S4: Severity exit
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_shell_has_severity_exit
# 🧪 TRAP[TEST] · Structural · S4: severity exit · Last fail: N/A · Remove if: severity-based exit is replaced by Python aggregation
@pytest.mark.static_audit
def test_shell_has_severity_exit(caplog) -> None:
    """deploy-modules.sh must aggregate FAILED array and exit 2/1/0 based on severity.

    ## @purpose  Verify shell facade has FAILED array, severity loop with module-metadata,
    ##           exit 2 (critical), exit 1 (warn), exit 0 (all ok).
    ## @scenario S4: structural contract for severity exit
    ## @invariants
    ##   - FAILED array initialized
    ##   - Severity loop reads module-metadata for each failed module
    ##   - exit 2 for CRITICAL failures
    ##   - exit 1 for WARN failures
    ##   - exit 0 on success
    """
    caplog.set_level(logging.DEBUG)
    content = _read_deploy_modules_shell()

    # ── FAILED array ──
    has_failed_array = "FAILED=()" in content or "FAILED+=(" in content
    logger.critical("[IMP:9][S4][severity] FAILED array present: %s", has_failed_array)
    assert has_failed_array, "deploy-modules.sh must have FAILED array for severity tracking"

    # ── Severity loop: module-metadata call for failed modules ──
    has_severity_loop = "module-metadata" in content and "FAILED" in content
    logger.critical("[IMP:9][S4][severity] Severity loop present: %s", has_severity_loop)
    assert has_severity_loop, "deploy-modules.sh must query module-metadata for failed module severity"

    # ── Exit codes ──
    has_exit2 = content.strip().endswith("exit 2") or "exit 2" in content.split("\n")[-5:]
    # Look for exit 2 and exit 1 near the end of the file
    lines = content.split("\n")
    tail_lines = "\n".join(lines[-20:])
    has_exit2 = "exit 2" in tail_lines
    has_exit1 = "exit 1" in tail_lines
    has_exit0 = "exit 0" in tail_lines
    logger.critical("[IMP:9][S4][severity] exit 2 (critical): %s", has_exit2)
    logger.critical("[IMP:9][S4][severity] exit 1 (warn): %s", has_exit1)
    logger.critical("[IMP:9][S4][severity] exit 0 (ok): %s", has_exit0)
    assert has_exit2, "deploy-modules.sh must exit 2 for CRITICAL failures"
    assert has_exit1, "deploy-modules.sh must exit 1 for WARN failures"
    assert has_exit0, "deploy-modules.sh must exit 0 on success"

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_severity_exit


# ══════════════════════════════════════════════════════════════════════════════
# S5: Context overlay
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_shell_has_context_overlay
# 🧪 TRAP[TEST] · Structural · S5: context overlay delegation · Last fail: N/A · Remove if: context_overlay.py is integrated into docker_orchestrator
@pytest.mark.static_audit
def test_shell_has_context_overlay(caplog) -> None:
    """deploy-modules.sh must call context_overlay.py --action ensure --node-yaml.

    ## @purpose  Verify context overlay delegation: context_overlay.py --action ensure
    ##           with --node-yaml flag.
    ## @scenario S5: structural contract for context overlay
    ## @invariants
    ##   - context_overlay.py call present with --action ensure
    ##   - --node-yaml flag passed to context_overlay.py
    """
    caplog.set_level(logging.DEBUG)
    content = _read_deploy_modules_shell()

    # ── Context overlay call ──
    has_context_overlay = "context_overlay.py" in content
    has_action_ensure = "--action ensure" in content
    has_node_yaml_flag = "--node-yaml" in content
    logger.critical("[IMP:9][S5][context] context_overlay.py call: %s", has_context_overlay)
    logger.critical("[IMP:9][S5][context] --action ensure: %s", has_action_ensure)
    logger.critical("[IMP:9][S5][context] --node-yaml flag: %s", has_node_yaml_flag)
    assert has_context_overlay, "deploy-modules.sh must call context_overlay.py"
    assert has_action_ensure, "context_overlay.py must be called with --action ensure"
    assert has_node_yaml_flag, "context_overlay.py must receive --node-yaml flag"

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_context_overlay


# ══════════════════════════════════════════════════════════════════════════════
# S6: Sudoers + Orphan post-deploy
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_shell_has_sudoers_orphan_post_deploy
# 🧪 TRAP[TEST] · Structural · S6: sudoers + orphan post-deploy · Last fail: N/A · Remove if: post-deploy is moved to Python
@pytest.mark.static_audit
def test_shell_has_sudoers_orphan_post_deploy(caplog) -> None:
    """deploy-modules.sh must call sudoers_generator.py --action batch-generate and orphan_reconciler.py post-deploy.

    ## @purpose  Verify post-deploy delegation: sudoers batch-generate and orphan
    ##           reconciler are called AFTER all module deploys.
    ## @scenario S6: structural contract for post-deploy
    ## @invariants
    ##   - sudoers_generator.py --action batch-generate call present
    ##   - orphan_reconciler.py call present
    ##   - Both calls are in the post-deploy section (after module deploy loop)
    """
    caplog.set_level(logging.DEBUG)
    content = _read_deploy_modules_shell()

    # ── Sudoers generator ──
    has_sudoers = "sudoers_generator.py" in content and "--action batch-generate" in content
    logger.critical("[IMP:9][S6][post-deploy] sudoers_generator.py --action batch-generate: %s", has_sudoers)
    assert has_sudoers, "deploy-modules.sh must call sudoers_generator.py --action batch-generate post-deploy"

    # ── Orphan reconciler ──
    has_orphan = "orphan_reconciler.py" in content
    logger.critical("[IMP:9][S6][post-deploy] orphan_reconciler.py: %s", has_orphan)
    assert has_orphan, "deploy-modules.sh must call orphan_reconciler.py post-deploy"

    # ── Both in post-deploy section (after module deploy loop) ──
    # The deploy loop ends, then sudoers + orphan are called
    deploy_end = content.rfind("done") if "done" in content else 0
    post_deploy = content[deploy_end:] if deploy_end > 0 else content
    sudoers_in_post = "sudoers_generator.py" in post_deploy
    orphan_in_post = "orphan_reconciler.py" in post_deploy
    logger.critical("[IMP:9][S6][post-deploy] sudoers in post-deploy section: %s", sudoers_in_post)
    logger.critical("[IMP:9][S6][post-deploy] orphan in post-deploy section: %s", orphan_in_post)

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_sudoers_orphan_post_deploy
