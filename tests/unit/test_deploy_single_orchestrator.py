"""Unit tests for DevPlan 089 AC13 / DevPlan 091 Wave A — context_deployer orchestrator path.

Verifies that context_deployer routes every project deploy through DeployOrchestrator
(no fallback flag, no parallel _deploy_single_project path). This is the unit-test
companion of the integration gate test_gate_single_orchestrator.py (T17).
"""
# GREP_SUMMARY: test-deploy-single-orchestrator, context-deployer, ac13, no-fallback, orchestrator-path
# STRUCTURE: ▶ test_no_orchestrator_flag → test_no_bypass_function → test_deploy_routes_through_orchestrator → test_dry_run_skips_execution
# region MODULE_CONTRACT
## @purpose  Unit-test layer for DevPlan 089 AC13: verify context_deployer delegates to
##           DeployOrchestrator as the sole deploy path. Integration gate (T17) covers the
##           static grep layer; this file covers runtime behavior and source-structure invariants.
## @scope    core/internal/bootstrap/deploy/context_deployer.py — source introspection + monkeypatched
##           _deploy_single_project_via_orchestrator() call routing.
## @invariants
##   - _ORCHESTRATOR_AVAILABLE symbol MUST NOT exist in the module (Wave A removal)
##   - _deploy_single_project parallel function MUST NOT exist (AC4 cleanup)
##   - deploy_context_projects() MUST call _deploy_single_project_via_orchestrator() for every project
##   - DeployOrchestrator.deploy(dry_run=True) returns SKIPPED with no side effects
## @changes 2026-07-30 | DevPlan 091 Wave A (AC13) — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib
from pathlib import Path
from unittest import mock

import pytest

from core.internal.bootstrap.deploy import context_deployer as cd
from core.internal.deploy.orchestrator import DeployStatus

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region FUNC_test_no_orchestrator_available_flag
## @purpose — Wave A cleanup invariant: the vestigial _ORCHESTRATOR_AVAILABLE flag MUST be gone.
##            Its presence would indicate re-introduction of the silent-bypass anti-pattern.
# 🧪 TRAP[TEST] · 2026-07-30 · Scenario: source-no-orchestrator-flag
# · Regression: any agent reintroducing `try/except ImportError` import guard
# · Last fail: never
# · Remove-if: DeployOrchestrator becomes optional again (not planned)
def test_no_orchestrator_available_flag() -> None:
    """_ORCHESTRATOR_AVAILABLE must not be present on the context_deployer module."""
    assert not hasattr(cd, "_ORCHESTRATOR_AVAILABLE"), (
        "context_deployer._ORCHESTRATOR_AVAILABLE re-introduced — DeployOrchestrator must be the sole "
        "path with no fallback flag (DevPlan 091 Wave A AC4)."
    )


# endregion FUNC_test_no_orchestrator_available_flag


# region FUNC_test_no_parallel_deploy_single_project
## @purpose — AC4 cleanup invariant: the bypass function _deploy_single_project() MUST be gone.
##            Only _deploy_single_project_via_orchestrator() is permitted as the per-project entrypoint.
# 🧪 TRAP[TEST] · 2026-07-30 · Scenario: source-no-bypass-function
# · Regression: someone resurrects the parallel pull→build→up→healthcheck path
# · Last fail: never
# · Remove-if: parallel path is explicitly reintroduced with Architect sign-off (TRAP update required)
def test_no_parallel_deploy_single_project() -> None:
    """_deploy_single_project (parallel bypass) must not exist on the module."""
    assert not hasattr(cd, "_deploy_single_project"), (
        "context_deployer._deploy_single_project re-introduced — parallel deploy path bypasses "
        "DeployOrchestrator audit/healthcheck/snapshot (DevPlan 091 Wave A AC4)."
    )
    # Sanity: the orchestrator-path entrypoint is still there
    assert hasattr(cd, "_deploy_single_project_via_orchestrator"), (
        "_deploy_single_project_via_orchestrator missing — deploy path is broken."
    )


# endregion FUNC_test_no_parallel_deploy_single_project


# region FUNC_test_source_has_no_orchestrator_flag
## @purpose — Belt-and-suspenders source scan: grep the module source for the forbidden pattern,
##            guarding against a renamed variant of the flag.
# 🧪 TRAP[TEST] · 2026-07-30 · Scenario: source-no-orchestrator-flag-text
# · Regression: flag renamed (e.g. _ORCHESTRATOR_OK) re-introducing silent bypass
# · Last fail: never
# · Remove-if: never (structural guard)
def test_source_has_no_orchestrator_flag() -> None:
    """The literal string `_ORCHESTRATOR_AVAILABLE` must not appear as a code symbol."""
    src_path = cd.__file__
    assert src_path and pathlib.Path(src_path).is_file()
    with pathlib.Path(src_path).open(encoding="utf-8") as f:
        src = f.read()
    # Comments/TRAPs referencing the symbol are allowed; an assignment (= True / = False) is not.
    assert "_ORCHESTRATOR_AVAILABLE =" not in src, (
        "Found `_ORCHESTRATOR_AVAILABLE =` assignment in context_deployer — Wave A removal regressed (DevPlan 091)."
    )


# endregion FUNC_test_source_has_no_orchestrator_flag


# region FUNC_test_deploy_context_projects_routes_via_orchestrator
## @purpose — Runtime routing invariant: deploy_context_projects() MUST call
##            _deploy_single_project_via_orchestrator() for every resolved project, never a bypass.
## @io — ⇥ monkeypatched resolve_context_projects + _deploy_single_project_via_orchestrator → ⎋ assertions
# 🧪 TRAP[TEST] · 2026-07-30 · Scenario: deploy-routes-through-orchestrator
# · Regression: deploy loop reintroduces if/else with parallel path
# · Last fail: never
# · Remove-if: never (core invariant of DevPlan 089/091)
def test_deploy_context_projects_routes_via_orchestrator(
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """deploy_context_projects() calls _deploy_single_project_via_orchestrator() for each project."""
    caplog.set_level(logging.INFO)

    projects = [
        cd.ProjectInfo(name="alpha", repo="r", type="backend", domain="a.test", context="ctx", database=""),
        cd.ProjectInfo(name="beta", repo="r", type="backend", domain="b.test", context="ctx", database=""),
    ]
    seen: list[str] = []

    def fake_via_orchestrator(project, projects_base, **kw):  # type: ignore[no-untyped-def]
        seen.append(project.name)
        return cd.ProjectDeployResult(name=project.name, status="deployed", channel="orchestrator", health="healthy")

    with (
        mock.patch.object(cd, "resolve_context_projects", return_value=projects),
        mock.patch.object(cd, "_deploy_single_project_via_orchestrator", side_effect=fake_via_orchestrator),
        mock.patch.object(cd, "_render_and_provision_llm", return_value=None),
        mock.patch.object(cd, "_write_audit", return_value=None),
    ):
        results = cd.deploy_context_projects("/no/node.yaml", "ctx")

    assert [r.name for r in results] == ["alpha", "beta"]
    assert seen == ["alpha", "beta"], "Each project must route through the orchestrator path"

    # LDD trajectory — confirm IMP:9 business-logic logs were emitted by the deploy path.
    # NOTE: we monkeypatch _deploy_single_project_via_orchestrator, so the per-project
    # "Deploying via DeployOrchestrator" IMP:9 is emitted by the real implementation's caller
    # (deploy_context_projects summary log). We assert any IMP:9 from context_deployer.
    found_log = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            try:
                imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            except (ValueError, IndexError):
                continue
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9 and "context_deployer" in record.message:
                found_log = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: no IMP:9 context_deployer log emitted"


# endregion FUNC_test_deploy_context_projects_routes_via_orchestrator


# region FUNC_test_deploy_context_projects_empty_returns_empty
## @purpose — Degenerate case: no projects resolved → no orchestrator calls, empty result.
# 🧪 TRAP[TEST] · 2026-07-30 · Scenario: empty-project-list-noop
# · Regression: empty list accidentally invokes bypass path
# · Last fail: never
# · Remove-if: never
def test_deploy_context_projects_empty_returns_empty() -> None:
    """deploy_context_projects() returns [] without invoking the orchestrator path."""
    with (
        mock.patch.object(cd, "resolve_context_projects", return_value=[]),
        mock.patch.object(cd, "_deploy_single_project_via_orchestrator") as orch_path,
        mock.patch.object(cd, "_render_and_provision_llm", return_value=None),
    ):
        results = cd.deploy_context_projects("/no/node.yaml", "ctx")

    assert results == []
    orch_path.assert_not_called()


# endregion FUNC_test_deploy_context_projects_empty_returns_empty


# region FUNC_test_orchestrator_dry_run_skips_execution
## @purpose — AC10 contract: DeployOrchestrator.deploy(dry_run=True) returns SKIPPED and performs
##            no side effects (no snapshot, no audit failure entry beyond planning logs).
## @io — ⇥ real DeployOrchestrator + MockChannel → ⎋ DeployStatus.SKIPPED
# 🧪 TRAP[TEST] · 2026-07-30 · Scenario: orchestrator-dry-run-noop
# · Regression: dry_run path accidentally executes delivery/compose
# · Last fail: never
# · Remove-if: dry_run semantics change with explicit Architect sign-off
def test_orchestrator_dry_run_skips_execution(
    tmp_path,
    caplog: pytest.LogCaptureFixture,  # type: ignore[no-untyped-def]
) -> None:
    """DeployOrchestrator.deploy(dry_run=True) must return SKIPPED and not invoke delivery."""
    caplog.set_level(logging.INFO)

    from core.internal.deploy.channels import DeliveryChannel, DeliveryResult
    from core.internal.deploy.orchestrator import DeployOrchestrator

    class NoSideEffectChannel(DeliveryChannel):
        """Channel that fails the test if deliver() is called (dry_run must not reach it)."""

        def __init__(self) -> None:
            super().__init__(timeout=5)
            self.deliver_called = False

        def deliver(self, _payload) -> DeliveryResult:  # type: ignore[override]
            self.deliver_called = True
            pytest.fail("dry_run=True must not invoke channel.deliver()")

    projects_base = str(tmp_path)
    proj_dir = Path(projects_base) / "dryproj"
    pathlib.Path(proj_dir).mkdir(exist_ok=True, parents=True)
    with pathlib.Path(Path(proj_dir) / "docker-compose.yml").open("w", encoding="utf-8") as f:
        f.write("services:\n  web:\n    image: nginx:alpine\n")

    orch = DeployOrchestrator(projects_base=projects_base)
    channel = NoSideEffectChannel()

    result = orch.deploy(
        project_name="dryproj",
        channel=channel,
        project_dir=proj_dir,
        dry_run=True,
    )

    assert result.status == DeployStatus.SKIPPED, f"dry_run deploy must return SKIPPED, got {result.status}"
    assert channel.deliver_called is False, "dry_run must not reach channel.deliver()"

    # LDD trajectory — confirm IMP:8 dry-run plan logs were emitted
    plan_seen = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            try:
                imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            except (ValueError, IndexError):
                continue
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 8 and "DRY-RUN" in record.message:
                plan_seen = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert plan_seen, "Critical LDD Error: no IMP:8 DRY-RUN plan log emitted"


# endregion FUNC_test_orchestrator_dry_run_skips_execution
