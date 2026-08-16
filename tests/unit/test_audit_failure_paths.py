# GREP_SUMMARY: test-audit-failure-paths, T9.6, audit, FAILED, run-init, PhaseDependencyError, PlatformFatalError, orchestrator-verify, deploy-audit
# STRUCTURE: ▶ test_*_cli_dep_error ┌PhaseDependencyError┐ → _audit_failed → write_audit_log(result=FAILED) │ ▶ test_*_cli_fatal → PlatformFatalError → audit FAILED │ ▶ test_*_orchestrator_verify → _verify_deploy raise → audit FAILED + result FAILED
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.6 (L-5/L-11) DevPlan 136 W9: audit-записи result=FAILED в
##           failure-путях (ранее audit писался ТОЛЬКО в успешном хвосте run_*_mode и после
##           verify — фейл фазы/verify покидал run без audit-следа).
## @scope    unit-тесты: DI (субклассы + run_init_mode DI-параметры, W-H DevPlan 163); tmp_path.
## @invariants
##   - Native imports; tmp_path; PLATFORM_LOCK_DIR=tmp (deploy lock не трогает /var/lock)
##   - PhaseDependencyError → write_audit_log(result="FAILED"); PlatformFatalError → тоже
##   - _verify_deploy exception → DeployAuditLogger.log(result="FAILED") + DeployStatus.FAILED
##   - LDD IMP:9 в успешных сценариях
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: test_audit_failure_paths.py — exception →
##            audit entry FAILED (R5-negative: раньше фейл фазы не аудитился, L-5/L-11).
## @changes  2026-08-05 · Created (DevPlan 136 W9)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle import cli
from core.internal.bootstrap.lifecycle.state_machine import (
    PhaseDependencyError,
    StateMachine,
)
from core.internal.deploy.channels import LocalChannel
from core.internal.deploy.orchestrator import DeployOrchestrator, DeployStatus
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# конфигурация из conftest: repo_root/core на sys.path — импорты core.* работают нативно


def _make_sm(state_file: Path) -> StateMachine:
    m = StateMachine(state_file_path=str(state_file))
    m.setup_state(mode="init", node="n")
    return m


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.6/L-5 — PhaseDependencyError → audit FAILED
# · Scenario: execute_phase raise PhaseDependencyError → run_init_mode пишет audit result=FAILED
# · Last fail: 2026-08-05 — audit вызывался только в успешном хвосте; фейл фазы не аудитился (L-5)
# · Remove if: audit failure-path semantics change
@ldd_trajectory
def test_run_init_dependency_error_audits_failed(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.6: PhaseDependencyError в run_init_mode → audit запись result=FAILED."""
    caplog.set_level(logging.INFO)
    m = _make_sm(tmp_path / "state.json")
    audit_calls: list[dict] = []

    class _DepFailSM(StateMachine):
        def execute_phase(self, phase_value: str, *, _env=None, _facts=None):
            msg = f"phase {phase_value} requires missing prerequisite"
            raise PhaseDependencyError(msg)

    m = _DepFailSM(state_file_path=str(tmp_path / "state.json"))
    m.setup_state(mode="init", node="n")

    # DI (W-H): run_init_mode(audit_fn=/smoke_fn=/notify_fn=) — 0 патчей
    rc = cli.run_init_mode(
        m,
        audit_fn=lambda _, **kw: audit_calls.append(kw),
        smoke_fn=lambda: True,
        notify_fn=lambda _: None,
    )
    assert rc == 1
    assert audit_calls, "audit обязан писаться в failure-пути (T9.6)"
    assert audit_calls[0].get("result") == "FAILED", f"status обязана быть FAILED: {audit_calls}"
    logger.critical("[IMP:9][test] PhaseDependencyError → audit FAILED — OK (T9.6)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.6 — PlatformFatalError → audit FAILED
# · Scenario: execute_phase raise PlatformFatalError → run_init_mode пишет audit result=FAILED
# · Remove if: audit failure-path semantics change
@ldd_trajectory
def test_run_init_fatal_error_audits_failed(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.6: PlatformFatalError в run_init_mode → audit result=FAILED + exit_code 10."""
    caplog.set_level(logging.INFO)
    from core.internal.shared.exceptions import PlatformFatalError

    audit_calls: list[dict] = []

    class _FatalFailSM(StateMachine):
        def execute_phase(self, _phase_value, *, _env=None, _facts=None):
            msg = "deploy-modules failed fatally"
            raise PlatformFatalError(msg)

    m = _FatalFailSM(state_file_path=str(tmp_path / "state.json"))
    m.setup_state(mode="init", node="n")

    # DI (W-H): run_init_mode(audit_fn=/smoke_fn=/notify_fn=) — 0 патчей
    rc = cli.run_init_mode(
        m,
        audit_fn=lambda _, **kw: audit_calls.append(kw),
        smoke_fn=lambda: True,
        notify_fn=lambda _: None,
    )
    assert rc == 10
    assert audit_calls and audit_calls[0].get("result") == "FAILED", f"audit FAILED ожидался: {audit_calls}"
    logger.critical("[IMP:9][test] PlatformFatalError → audit FAILED — OK (T9.6)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.6/L-11 — verify-exception → audit FAILED
# · Scenario: _verify_deploy raise OSError (snapshot IO) → DeployOrchestrator пишет audit
# ·   result=FAILED и возвращает FAILED (не молчаливый проброс без audit)
# · Last fail: 2026-08-05 — исключение в verify (create_snapshot OSError) покидало deploy()
# ·   БЕЗ audit-записи (L-11)
# · Remove if: deploy audit semantics change
@ldd_trajectory
def test_orchestrator_verify_exception_audits_failed(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.6: _verify_deploy exception → audit FAILED + DeployStatus.FAILED."""
    caplog.set_level(logging.INFO)
    from core.internal.deploy.channels import Payload

    tar = tmp_path / "payload.tar"
    tar.write_bytes(b"tar-data")
    payload = Payload(tar_path=tar, project_name="proj")

    audit_entries: list[dict] = []

    class _FakeAuditLogger:
        """DI-канал audit: DeployAuditLogger-заменитель с log(**kw) (W-H, 0 патчей)."""

        def log(self, **kw):
            audit_entries.append(kw)

    # DI (W-H): DeployOrchestrator-субкласс с переопределёнными шагами (0 патчей)
    class _VerifyFailOrch(DeployOrchestrator):
        def _prepare_deploy(self, *_a, **_k):
            return payload, None

        def _apply_deploy(self, *_a, **_k):
            return None

        def _verify_deploy(self, *_a, **_k):
            msg = "snapshot io error"
            raise OSError(msg)

    orch = _VerifyFailOrch(projects_base=str(tmp_path / "projects"), audit_logger=_FakeAuditLogger())

    result = orch.deploy(project_name="proj", channel=LocalChannel(), project_dir=str(tmp_path / "projects" / "proj"))

    assert result.status == DeployStatus.FAILED, f"verify-exception → FAILED: {result.status}"
    assert any(e.get("result") == "FAILED" for e in audit_entries), (
        f"audit FAILED ожидался в except-ветке verify: {audit_entries}"
    )
    assert "snapshot io error" in (result.error_info or "")
    logger.critical("[IMP:9][test] verify-exception → audit FAILED + FAILED result — OK (T9.6)")
