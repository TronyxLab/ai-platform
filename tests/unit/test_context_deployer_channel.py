"""
# GREP_SUMMARY: test-context-deployer-channel, SCPChannel, LocalChannel, payload, receive, VPS-side, delivery, A1
# STRUCTURE: ▶ test_channel_contract (SCP fails w/o metadata | Local succeeds) → ◇ test_context_deployer_uses_local_channel →
#            ◇ test_no_scp_channel_in_source (R5) → ⊕ LDD IMP:9 → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for the A1 fix (DevPlan 118 A1): context_deployer must use LocalChannel,
##           NOT SCPChannel(), when deploying via DeployOrchestrator on the VPS side.
##           Payload is already in place after context_overlay — a transport channel is meaningless
##           there and ALWAYS fails ("SCPChannel requires 'host' in payload.metadata").
## @scope    Tests:
##           1. Channel contract — SCPChannel() with empty metadata fails (the original bug input),
##              LocalChannel succeeds on an already-assembled payload.
##           2. context_deployer._deploy_single_project_via_orchestrator passes a LocalChannel.
##           3. R5 negative — context_deployer no longer constructs SCPChannel.
## @invariants
##   - No real docker/ssh calls — channels tested in isolation, DeployOrchestrator faked
##   - LDD IMP:9 log presence asserted per test
## @rationale DevPlan 095 E2E T16 exposed the bug: receive()/context_deployer used SCPChannel with
##            empty metadata → deliver() always failed → deploy-context returned status="failed"
##            for all projects. LocalChannel is the contract-compliant no-op (TRAP channels.py:327).
## @changes  2026-08-02 | DevPlan 118 A1 — Created (negative test BEFORE fix, R5)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test (module-level sys.path для bootstrap/deploy) ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_MODULE_DIR))
import context_deployer as cd

from core.internal.deploy.channels import LocalChannel, Payload, SCPChannel

pytestmark = pytest.mark.static_audit

_CTX_DEPLOYER_SOURCE = Path(cd.__file__).read_text(encoding="utf-8")


# region Fixtures


@pytest.fixture
def assembled_payload(tmp_path) -> Payload:
    """Build a real payload via PayloadDeliverer.assemble_payload (already-extracted VPS-side case)."""
    from core.internal.deploy.payload_deliverer import PayloadDeliverer

    project_dir = tmp_path / "projects" / "demo-app"
    project_dir.mkdir(parents=True)
    (project_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx:alpine\n", encoding="utf-8")
    (project_dir / "ai-platform.yaml").write_text("project: demo-app\n", encoding="utf-8")
    deliverer = PayloadDeliverer(projects_base=str(tmp_path / "projects"))
    return deliverer.assemble_payload(
        project_name="demo-app",
        version="sha123",
        project_dir=str(project_dir),
    )


@pytest.fixture
def fake_orchestrator_cls():
    """Fake DeployOrchestrator that records the channel passed by context_deployer."""
    captured: dict[str, object] = {}

    class _FakeOrchestrator:
        def __init__(self, projects_base: str):
            self.projects_base = projects_base

        def deploy(self, project_name: str, channel, project_dir: str):  # ruff: ignore[ARG002]
            captured["channel"] = channel
            captured["project_name"] = project_name
            return SimpleNamespace(
                is_success=lambda: True,
                healthcheck_status="healthy",
                error_info=None,
            )

    return _FakeOrchestrator, captured


# endregion Fixtures


# region Tests: channel contract (original bug input + fix path)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · A1 — SCPChannel() with empty metadata fails delivery
# · Regression: DevPlan 095 E2E T16 — context_deployer.py:287 SCPChannel() → delivery ALWAYS failed
# · Last fail: "SCPChannel requires 'host' in payload.metadata" (channels.py:225-230)
# · Remove if: SCPChannel contract changes to allow metadata-less delivery
@ldd_trajectory
def test_scp_channel_empty_metadata_fails_negative(caplog, assembled_payload) -> None:
    """SCPChannel() with no metadata must fail — reproduces the original A1 bug input (R5)."""
    caplog.set_level(logging.INFO)

    result = SCPChannel().deliver(assembled_payload)

    assert result.success is False, "SCPChannel without host metadata must fail"
    assert "host" in (result.error_message or ""), f"unexpected error: {result.error_message}"
    logger.critical("[IMP:9][test] SCPChannel empty-metadata → FAILED (original bug input) — OK")


# GUARD-PRESERVE (168): единственное покрытие LocalChannel.deliver (A1 fix-path); позитив-пара R5-негатива test_scp_channel_empty_metadata_fails_negative
# 🧪 TRAP[TEST] · REGRESSION · A1 fix — LocalChannel accepts an already-assembled payload
# · Scenario: payload built by PayloadDeliverer.assemble_payload → LocalChannel.deliver → success
# · Last fail: N/A (fix path — payload already extracted on VPS after context_overlay)
# · Remove if: LocalChannel contract changes
@ldd_trajectory
def test_local_channel_accepts_assembled_payload(caplog, assembled_payload) -> None:
    """LocalChannel must accept an already-assembled payload (VPS-side receive, A1)."""
    caplog.set_level(logging.INFO)

    result = LocalChannel().deliver(assembled_payload)

    assert result.success is True, "LocalChannel must succeed on an already-assembled payload"
    logger.critical("[IMP:9][test] LocalChannel accepted assembled payload — OK")


# endregion Tests: channel contract (original bug input + fix path)


# region Tests: context_deployer channel selection (A1)


# 🧪 TRAP[TEST] · REGRESSION · A1 — context_deployer passes LocalChannel to DeployOrchestrator
# · Scenario: _deploy_single_project_via_orchestrator with healthy=False, bootstrap OK, fake orchestrator
# ·   that records the channel → channel must be LocalChannel (not SCPChannel) → result deployed
# · Last fail: context_deployer.py:287 SCPChannel() → deliver always failed → status="failed" for all projects
# · Remove if: channel selection moves out of context_deployer
@ldd_trajectory
def test_context_deployer_uses_local_channel(caplog, tmp_path, fake_orchestrator_cls) -> None:
    """_deploy_single_project_via_orchestrator must deliver through LocalChannel (A1)."""
    caplog.set_level(logging.INFO)
    fake_cls, captured = fake_orchestrator_cls

    project = cd.ProjectInfo(
        name="demo-app",
        repo="https://github.com/test/demo-app",
        type="backend",
        domain="demo.example.com",
        context="test-ctx",
    )
    project_dir = str(tmp_path / "projects" / "demo-app")
    Path(project_dir).mkdir(exist_ok=True, parents=True)

    class _FakeFacts:
        """Fake EnvironmentFacts: docker-compose.yml «существует» → bootstrap-генерация пропускается."""

        def path_isfile(self, _path) -> bool:
            return True

    # 167 D3 (DI): health_fn/orchestrator_cls/facts — 0 setattr(cd, ...)
    result = cd._deploy_single_project_via_orchestrator(
        project,
        str(tmp_path / "projects"),
        health_fn=lambda _: False,
        orchestrator_cls=fake_cls,
        facts=_FakeFacts(),
    )

    channel = captured.get("channel")
    assert channel is not None, "DeployOrchestrator.deploy must have been called with a channel"
    assert isinstance(channel, LocalChannel), (
        f"A1 FAIL: context_deployer must use LocalChannel, got {type(channel).__name__} — "
        "SCPChannel with empty metadata always fails delivery"
    )
    assert result.status == "deployed", f"expected deployed, got {result.status} ({result.error})"
    logger.critical("[IMP:9][test] context_deployer channel=LocalChannel → deployed — OK")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · A1 — SCPChannel construction removed from context_deployer
# · Scenario: AST-level check — context_deployer must not construct/import SCPChannel
# · Last fail: context_deployer.py:287 `channel = SCPChannel()` (import at line 36)
# · Remove if: a legitimate SCPChannel usage is reintroduced with explicit host metadata
@ldd_trajectory
def test_no_scp_channel_construction_in_source_negative(caplog) -> None:
    """R5 negative: SCPChannel construction/import must be absent from context_deployer source.

    AST-level scan (ignores TRAP-комментарии, документирующие историю фикса).
    """
    import ast

    caplog.set_level(logging.INFO)

    tree = ast.parse(_CTX_DEPLOYER_SOURCE)
    scp_uses = []
    for node in ast.walk(tree):
        # Импорт SCPChannel
        if isinstance(node, ast.ImportFrom) and any(a.name == "SCPChannel" for a in node.names):
            scp_uses.append(f"{node.lineno}: import SCPChannel")
        if isinstance(node, ast.Import) and any(a.name == "SCPChannel" for a in node.names):
            scp_uses.append(f"{node.lineno}: import SCPChannel")
        # Конструкция SCPChannel(...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "SCPChannel":
            scp_uses.append(f"{node.lineno}: SCPChannel()")

    assert not scp_uses, (
        f"A1 FAIL: context_deployer still uses SCPChannel ({', '.join(scp_uses)}) — the transport "
        "channel must be removed (payload is already on the VPS after context_overlay)"
    )
    assert "LocalChannel" in _CTX_DEPLOYER_SOURCE, "context_deployer must import LocalChannel (A1)"
    logger.critical("[IMP:9][test] context_deployer SCPChannel removed / LocalChannel present — OK")


# endregion Tests: context_deployer channel selection (A1)
