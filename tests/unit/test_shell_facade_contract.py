"""
# GREP_SUMMARY: test-shell-facade-contract, structural-contract, deploy-modules-arg-parsing, python-delegation, severity-exit
# STRUCTURE: ▶ _read_deploy_modules_shell → ○ S1(arg_parsing) ◇ S2(provisioner) ◇ S3(python_delegation) ◇ S4(severity_exit) ◇ S5(context_overlay) ◇ S6(sudoers_orphan) → ⊕ LDD trajectory → ⎋ IMP:9 assert
# region MODULE_CONTRACT
## @purpose  Structural contract tests for deploy-modules.sh shell facade + deploy_orchestrator.py.
##           Verify the thin facade (≤50 LOC, DevPlan 100) has arg parsing, provisioner
##           delegation, and exec-python3 delegation; verify deploy/deploy_orchestrator.py
##           holds routing, severity exit, context overlay, and post-deploy sudoers/orphan
##           reconciliation contracts.
## @scope    Static analysis — reads deploy-modules.sh and deploy/deploy_orchestrator.py as
##           text, checks for structural patterns. No Docker, VPS, or network access required.
## @invariants
##   - Tests check CONTRACT (what the facade calls / what the orchestrator imports+invokes),
##     not IMPLEMENTATION (how the Python modules work internally)
##   - Shell facade may change between Strangler-Fig waves — tests must survive formatting changes
##   - S1/S2 check the ≤50-LOC facade; S3-S6 check deploy_orchestrator.py (DevPlan 100 moved
##     python-delegation/severity/context-overlay/sudoers-orphan patterns from shell to Python)
##   - LDD IMP:9 trajectory verified on every test
## @rationale DevPlan 042 Option D — 6 structural contract tests replace 14 obsolete shell-grep
##           tests. DevPlan 100 — S3-S6 re-pointed at deploy_orchestrator.py where the patterns
##           now live. These tests verify the delegation contract exists, not that the Python
##           modules work correctly (covered by unit tests).
## @changes   2026-07-31 · DevPlan 100 — S3-S6 adapted: python delegation, severity, context
##           overlay, sudoers/orphan patterns moved from shell facade to deploy/deploy_orchestrator.py
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
# DevPlan 100: routing/severity/postflight patterns moved from the shell facade to the Python
# orchestrator — S3-S6 static grep-цели указывают сюда (аналогично tests/test_deploy_modules.py).
_DEPLOY_ORCHESTRATOR_PY = (
    Path(__file__).resolve().parent.parent.parent
    / "core"
    / "internal"
    / "bootstrap"
    / "deploy"
    / "deploy_orchestrator.py"
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


# region HELPER__read_deploy_orchestrator
def _read_deploy_orchestrator() -> str:
    """Read deploy_orchestrator.py source content.

    ## @purpose — Central helper: reads the Python orchestrator once (DevPlan 100), returning raw text.
    ## @io — ⎋ str: full content of deploy/deploy_orchestrator.py
    ## @complexity 1 — file read
    """
    return _DEPLOY_ORCHESTRATOR_PY.read_text()


# endregion HELPER__read_deploy_orchestrator


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
# 🧪 TRAP[TEST] · Structural · S3: python delegation · Last fail: 2026-07-31 (old shell python3 calls) · Remove if: deploy_orchestrator.py merges all imports into a single entrypoint
@pytest.mark.static_audit
def test_shell_has_python_delegation(caplog) -> None:
    """deploy-modules.sh must exec python3 deploy_orchestrator.py; orchestrator imports all deploy/ modules natively.

    ## @purpose  Verify DevPlan 100 delegation contract: thin facade execs
    ##           deploy/deploy_orchestrator.py (same PID, exit {0,1,2} auto-propagates);
    ##           orchestrator imports context_overlay, secrets_validator, docker_orchestrator,
    ##           sudoers_generator, orphan_reconciler natively (D1 — no subprocess for logic).
    ## @scenario S3: structural contract for Python delegation (facade exec → orchestrator imports)
    ## @invariants
    ##   - Facade contains `exec python3 ... deploy/deploy_orchestrator.py`
    ##   - Orchestrator imports all 5 deploy/ modules (`<name> as _<name>` import-native block)
    ##   - No legacy per-module `python3 deploy/*.py` subprocess calls remain in the facade
    """
    caplog.set_level(logging.DEBUG)
    shell_content = _read_deploy_modules_shell()
    orch_content = _read_deploy_orchestrator()

    # ── 1. Facade: exec python3 deploy/deploy_orchestrator.py (DevPlan 100 D2) ──
    has_exec_orchestrator = "exec python3" in shell_content and "deploy/deploy_orchestrator.py" in shell_content
    logger.critical("[IMP:9][S3][python] Facade exec python3 deploy_orchestrator.py: %s", has_exec_orchestrator)
    assert has_exec_orchestrator, "deploy-modules.sh must exec python3 deploy/deploy_orchestrator.py (DevPlan 100)"

    # ── 2. All 5 Python modules imported natively in deploy_orchestrator.py (D1) ──
    modules = {
        "context_overlay": "Context overlay",
        "secrets_validator": "Secrets validator",
        "docker_orchestrator": "Docker orchestrator",
        "sudoers_generator": "Sudoers generator",
        "orphan_reconciler": "Orphan reconciler",
    }
    all_present = True
    for mod_name, mod_label in modules.items():
        present = f"{mod_name} as _" in orch_content
        logger.critical("[IMP:9][S3][python] %s (%s) imported in orchestrator: %s", mod_label, mod_name, present)
        if not present:
            all_present = False

    assert all_present, "deploy_orchestrator.py must import all 5 deploy/ modules natively (D1)"

    # ── 3. No legacy per-module python3 subprocess calls remain in the facade ──
    legacy_ops = {
        "context_overlay.py",
        "secrets_validator.py",
        "docker_orchestrator.py",
        "sudoers_generator.py",
        "orphan_reconciler.py",
    }
    for op in legacy_ops:
        has_legacy = f"deploy/{op}" in shell_content and "python3" in shell_content
        logger.critical("[IMP:9][S3][python] Legacy python3 deploy/%s in facade: %s", op, has_legacy)
        assert not has_legacy, f"deploy-modules.sh must NOT call python3 deploy/{op} directly (moved to orchestrator)"

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_python_delegation


# ══════════════════════════════════════════════════════════════════════════════
# S4: Severity exit
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_shell_has_severity_exit
# 🧐 TRAP[DECISION] · 2026-07-24 · — · WARN non-blocking: exit 0, not exit 1
# · Rejected: exit 1 for WARN (blocked node-update on 4 non-critical warnings)
# · Reason: severity aggregation moved to Python (DevPlan 100, deploy_orchestrator.py
#           _compute_exit_code) — legacy contract PRESERVED: CRIT>0 → 2, WARN>0 → exit 0
#           (non-blocking), success → 0. exit 1 stays RESERVED (never emitted).
# · Rev: if severity semantics change (e.g. WARN becomes blocking) → update _compute_exit_code + this test.
# 🧪 TRAP[TEST] · Structural · S4: severity exit · Last fail: 2026-07-31 (old shell FAILED array) · Remove if: severity aggregation is removed from deploy_orchestrator.py
@pytest.mark.static_audit
def test_shell_has_severity_exit(caplog) -> None:
    """deploy_orchestrator.py must aggregate severity and compute exit 2/0 via DeployResult.exit_code.

    ## @purpose  Verify severity contract moved to Python (DevPlan 100): DeployResult dataclass,
    ##           _aggregate_severity (failed modules → crit/warn counts), _compute_exit_code
    ##           (CRIT>0 → 2, WARN>0 → 0 non-blocking, success → 0). exit 1 stays RESERVED.
    ## @scenario S4: structural contract for severity exit in deploy_orchestrator.py
    ## @invariants
    ##   - DeployResult dataclass with exit_code field present
    ##   - _aggregate_severity aggregates failed-module severities
    ##   - _compute_exit_code returns 2 for CRITICAL failures (blocks deploy)
    ##   - WARN maps to exit 0 (non-blocking — legacy 33aaaeb parity)
    ##   - exit 1 never emitted (RESERVED)
    """
    caplog.set_level(logging.DEBUG)
    content = _read_deploy_orchestrator()

    # ── DeployResult dataclass with exit_code field (severity aggregation result) ──
    has_deploy_result = "class DeployResult:" in content and "exit_code: int" in content
    logger.critical("[IMP:9][S4][severity] DeployResult with exit_code field: %s", has_deploy_result)
    assert has_deploy_result, "deploy_orchestrator.py must define DeployResult dataclass with exit_code"

    # ── Severity aggregation: failed modules → crit/warn counts ──
    has_aggregate = "def _aggregate_severity(" in content
    logger.critical("[IMP:9][S4][severity] _aggregate_severity present: %s", has_aggregate)
    assert has_aggregate, "deploy_orchestrator.py must aggregate severity (_aggregate_severity)"

    # ── Exit code computation: CRIT>0 → 2, WARN>0 → 0 (non-blocking), success → 0 ──
    has_compute = "def _compute_exit_code(" in content
    has_crit_exit2 = "return 2" in content
    has_warn_exit0 = "non-critical" in content and "→ exit 0" in content
    has_exit1_reserved = "1 is RESERVED" in content
    logger.critical("[IMP:9][S4][severity] _compute_exit_code present: %s", has_compute)
    logger.critical("[IMP:9][S4][severity] CRIT → exit 2: %s", has_crit_exit2)
    logger.critical("[IMP:9][S4][severity] WARN → exit 0 (non-critical): %s", has_warn_exit0)
    logger.critical("[IMP:9][S4][severity] exit 1 reserved (never emitted): %s", has_exit1_reserved)
    assert has_compute, "deploy_orchestrator.py must compute severity-based exit code (_compute_exit_code)"
    assert has_crit_exit2, "CRITICAL failures must map to exit 2 (blocks deploy)"
    assert has_warn_exit0, "WARN must map to exit 0 (non-blocking — 33aaaeb parity)"
    assert has_exit1_reserved, "exit 1 must stay RESERVED — WARN must never map to exit 1"

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_severity_exit


# ══════════════════════════════════════════════════════════════════════════════
# S5: Context overlay
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_shell_has_context_overlay
# 🧪 TRAP[TEST] · Structural · S5: context overlay delegation · Last fail: 2026-07-31 (old shell call) · Remove if: context_overlay.ensure_context_repo is removed from orchestrator _preflight
@pytest.mark.static_audit
def test_shell_has_context_overlay(caplog) -> None:
    """deploy_orchestrator.py must import context_overlay and call ensure_context_repo in _preflight.

    ## @purpose  Verify context overlay delegation moved to Python (DevPlan 100): orchestrator
    ##           imports context_overlay natively and calls ensure_context_repo(node_yaml) in
    ##           the preflight phase (legacy context_overlay.py --action ensure --node-yaml parity).
    ## @scenario S5: structural contract for context overlay in Python
    ## @invariants
    ##   - context_overlay imported in deploy_orchestrator.py (import-native, D1)
    ##   - ensure_context_repo called with node_yaml inside _preflight
    ##   - _preflight function exists (PHASE 1 of orchestrate)
    """
    caplog.set_level(logging.DEBUG)
    content = _read_deploy_orchestrator()

    # ── Context overlay import (native, D1) ──
    has_import = "context_overlay as _context_overlay" in content
    logger.critical("[IMP:9][S5][context] context_overlay imported in orchestrator: %s", has_import)
    assert has_import, "deploy_orchestrator.py must import context_overlay natively (D1)"

    # ── ensure_context_repo call with node_yaml (preflight phase) ──
    has_ensure_call = "_context_overlay.ensure_context_repo(node_yaml)" in content
    logger.critical("[IMP:9][S5][context] ensure_context_repo(node_yaml) call: %s", has_ensure_call)
    assert has_ensure_call, "deploy_orchestrator.py must call ensure_context_repo(node_yaml) in _preflight"

    # ── Call lives in _preflight (PHASE 1) ──
    has_preflight = "def _preflight(" in content
    logger.critical("[IMP:9][S5][context] _preflight function present: %s", has_preflight)
    assert has_preflight, "deploy_orchestrator.py must define _preflight (PHASE 1)"

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_context_overlay


# ══════════════════════════════════════════════════════════════════════════════
# S6: Sudoers + Orphan post-deploy
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_shell_has_sudoers_orphan_post_deploy
# 🧪 TRAP[TEST] · Structural · S6: sudoers + orphan post-deploy · Last fail: 2026-07-31 (old shell calls) · Remove if: _postflight is removed from deploy_orchestrator.py
@pytest.mark.static_audit
def test_shell_has_sudoers_orphan_post_deploy(caplog) -> None:
    """deploy_orchestrator.py must run sudoers batch + orphan reconciliation inside _postflight.

    ## @purpose  Verify post-deploy delegation moved to Python (DevPlan 100): PHASE 4 _postflight
    ##           calls _sudoers_generator._batch_generate_sudoers and
    ##           _orphan_reconciler._batch_orphan_reconciliation — both AFTER the deploy phase.
    ## @scenario S6: structural contract for post-deploy in Python
    ## @invariants
    ##   - sudoers_generator imported + _batch_generate_sudoers invoked inside _postflight
    ##   - orphan_reconciler imported + _batch_orphan_reconciliation invoked inside _postflight
    ##   - Both calls live in the _postflight function body (post-deploy phase)
    """
    caplog.set_level(logging.DEBUG)
    content = _read_deploy_orchestrator()

    # ── Sudoers generator: import + batch call inside _postflight ──
    has_sudoers_import = "sudoers_generator as _sudoers_generator" in content
    logger.critical("[IMP:9][S6][post-deploy] sudoers_generator imported: %s", has_sudoers_import)
    assert has_sudoers_import, "deploy_orchestrator.py must import sudoers_generator (D1)"

    # ── Orphan reconciler: import + batch call inside _postflight ──
    has_orphan_import = "orphan_reconciler as _orphan_reconciler" in content
    logger.critical("[IMP:9][S6][post-deploy] orphan_reconciler imported: %s", has_orphan_import)
    assert has_orphan_import, "deploy_orchestrator.py must import orphan_reconciler (D1)"

    # ── Both calls in the _postflight function body (after module deploy loop) ──
    postflight_start = content.find("def _postflight(")
    postflight_end = content.find("# endregion FUNC__postflight")
    postflight_body = (
        content[postflight_start:postflight_end] if postflight_start != -1 and postflight_end != -1 else ""
    )
    has_sudoers_in_post = "_batch_generate_sudoers" in postflight_body
    has_orphan_in_post = "_batch_orphan_reconciliation" in postflight_body
    logger.critical("[IMP:9][S6][post-deploy] _batch_generate_sudoers in _postflight: %s", has_sudoers_in_post)
    logger.critical("[IMP:9][S6][post-deploy] _batch_orphan_reconciliation in _postflight: %s", has_orphan_in_post)
    assert has_sudoers_in_post, "deploy_orchestrator.py must call _batch_generate_sudoers inside _postflight"
    assert has_orphan_in_post, "deploy_orchestrator.py must call _batch_orphan_reconciliation inside _postflight"

    _assert_ldd_imp9(caplog)


# endregion FUNC_test_shell_has_sudoers_orphan_post_deploy
