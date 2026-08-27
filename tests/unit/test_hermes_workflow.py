"""
# GREP_SUMMARY: test-hermes-workflow, hermes-agent, single-build, compose-config-images, phase-hermes, E1, unit-tests, L1-collapse
# STRUCTURE: ▶ test_phase_hermes_build ┌compose_args + module_dir┐ → mock compose config --images → mock image check (missing) → mock build (1 call) → ⎋ True │ ▶ test_phase_hermes_all_found → images exist → True (no build) │ ▶ test_phase_hermes_no_images → config fail → False
# region MODULE_CONTRACT
## @purpose  Unit tests for hermes_workflow.py (DevPlan 119 E1 $TEST_SPEC: test_phase_hermes_build)
##           — hermes-agent pre-deploy image check/build phase (extracted in D1, phased in E1).
##           L1→L2 коллапс DevPlan 002: L1 pull/bare-tag/build удалены — один build call.
## @scope    Covers handle_hermes_agent contract: all-images-found → True; missing → единый build;
##           compose config failure → False. mock subprocess.run + shared docker_compose.
## @invariants
##   - Native imports only, no real docker
##   - LDD: IMP:9 log assertion via ldd_trajectory
##   - E1: phase signature (module_name, module_dir, compose_file, compose_args) via _phase_hermes
##   - DevPlan 002: build цепочка = ОДИН docker_compose_build (не 2 — L1+L2)
## @rationale  $TEST_SPEC E1: test_phase_hermes_build — сборка hermes-образа. Изолированное
##             тестирование спец-фазы (после phased decomposition).
## @changes  2026-08-02 · Created (DevPlan 119 E1)
## @changes  2026-08-16 · DevPlan 002 W5 T5.2 — rewrite под single-build (1 build call, не 2)
# endregion MODULE_CONTRACT
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from core.internal.bootstrap.deploy import docker_orchestrator as dorch
from core.internal.bootstrap.deploy import hermes_workflow

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E1 hermes phase — build path
# · Regression: DevPlan 119 E1 — _phase_hermes (stale cleanup + hermes_workflow)
# · Scenario: images missing → единый compose build → True
# · Remove if: hermes phase semantics change
@patch.object(hermes_workflow, "_shared_docker_compose_build", return_value=True)
@patch.object(hermes_workflow, "_shared_docker_prebuild_pull", return_value=True)
@patch.object(hermes_workflow, "_shared_check_image_exists", return_value=False)
def test_phase_hermes_build(
    mock_check, mock_prebuild_pull, mock_build, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """E1: _phase_hermes triggers hermes build path when images missing (returns True)."""
    caplog.set_level(logging.INFO)
    module_dir = tmp_path
    compose_file = tmp_path / "docker-compose.base.yml"
    compose_file.write_text("services:\n  hermes:\n    image: ghcr.io/tronyx161/hermes:latest\n")
    compose_args = ["-f", str(compose_file), "--profile", "hermes-agent"]

    cfg_result = MagicMock()
    cfg_result.returncode = 0
    cfg_result.stdout = "ghcr.io/tronyx161/hermes:latest\n"
    with (
        patch.object(hermes_workflow, "_shared_docker_compose_config", return_value=cfg_result),
        patch.object(dorch, "_cleanup_stale_container", return_value=None),
    ):
        result = dorch._phase_hermes(
            module_name="hermes-agent",
            module_dir=str(module_dir),
            compose_file=compose_file,
            compose_args=compose_args,
        )

    assert result is True
    # DevPlan 002 W5 T5.2 (single-build): ровно ОДИН build call (L1 pull/bare-tag/L1-build удалены).
    assert mock_build.call_count == 1, (
        f"Expected exactly 1 build call (single image), got {mock_build.call_count} — L1-chain должен быть удалён"
    )
    build_call = mock_build.call_args_list[0]
    assert build_call.kwargs.get("compose_args") == compose_args, (
        f"build compose_args mismatch: {build_call.kwargs.get('compose_args')} != {compose_args}"
    )
    assert "flags" not in build_call.kwargs, (
        f"single-build не должен нести source-build флаги (L1-механика удалена): {build_call.kwargs}"
    )

    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_log = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_log = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · 2026-08-02 · unit · hermes all images found → True (no build)
# · Regression: handle_hermes_agent contract (D1)
# · Remove if: hermes workflow semantics change
@patch.object(hermes_workflow, "_shared_check_image_exists", return_value=True)
def test_handle_hermes_all_found_no_build(mock_check, tmp_path) -> None:
    """All hermes images in registry → True immediately (no build)."""
    compose_file = tmp_path / "docker-compose.base.yml"
    compose_file.write_text("services:\n  hermes:\n    image: x:latest\n")
    compose_args = ["-f", str(compose_file)]
    cfg_result = MagicMock()
    cfg_result.returncode = 0
    cfg_result.stdout = "x:latest\n"
    with patch.object(hermes_workflow, "_shared_docker_compose_config", return_value=cfg_result):
        result = hermes_workflow.handle_hermes_agent(compose_args, str(tmp_path), "hermes-agent")
    assert result is True


# 🧪 TRAP[TEST] · 2026-08-02 · unit · hermes compose config failure → False
# · Regression: handle_hermes_agent contract (D1)
# · Remove if: hermes workflow semantics change
def test_handle_hermes_config_fail(tmp_path) -> None:
    """compose config --images failure → False (fatal)."""
    compose_file = tmp_path / "docker-compose.base.yml"
    compose_file.write_text("services:\n  hermes:\n    image: x:latest\n")
    compose_args = ["-f", str(compose_file)]
    cfg_result = MagicMock()
    cfg_result.returncode = 1
    cfg_result.stdout = ""
    with patch.object(hermes_workflow, "_shared_docker_compose_config", return_value=cfg_result):
        result = hermes_workflow.handle_hermes_agent(compose_args, str(tmp_path), "hermes-agent")
    assert result is False


# 🧪 TRAP[TEST] · 2026-08-16 · unit · single build fail → False (DevPlan 002 R5 negative)
# · Scenario: image missing + build fails → False
# · Remove if: hermes workflow semantics change
@patch.object(hermes_workflow, "_shared_docker_compose_build", return_value=False)
@patch.object(hermes_workflow, "_shared_docker_prebuild_pull", return_value=True)
@patch.object(hermes_workflow, "_shared_check_image_exists", return_value=False)
def test_handle_hermes_build_fail(
    mock_check, mock_prebuild_pull, mock_build, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing image + build failure → False (IMP:10 build_fail)."""
    caplog.set_level(logging.INFO)
    compose_file = tmp_path / "docker-compose.base.yml"
    compose_file.write_text("services:\n  hermes:\n    image: x:latest\n")
    compose_args = ["-f", str(compose_file)]
    cfg_result = MagicMock()
    cfg_result.returncode = 0
    cfg_result.stdout = "x:latest\n"
    with patch.object(hermes_workflow, "_shared_docker_compose_config", return_value=cfg_result):
        result = hermes_workflow.handle_hermes_agent(compose_args, str(tmp_path), "hermes-agent")
    assert result is False
    assert "[IMP:10][handle_hermes_agent][build_fail]" in caplog.text, "IMP:10 build_fail log expected"


# 🧪 TRAP[TEST] · 2026-08-27 · unit · F-03 prebuild-pull вызывается РОВНО один раз ДО build_fn
# · Regression: hermes cold-bootstrap упал на ноде первым — _phase_rebuild pre-pull не покрывал
# ·   hermes_workflow fallback build (compose_build_fn DI); pre-pull баз Dockerfile ДО build.
# · Scenario: images missing → docker_prebuild_pull (mock) → ровно 1 вызов, затем build_fn → True
# · Remove if: pre-pull-семантика handle_hermes_agent меняется
def test_handle_hermes_prebuild_pull_before_build(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Fallback build: docker_prebuild_pull вызывается ровно один раз ДО compose_build_fn."""
    caplog.set_level(logging.INFO)
    compose_file = tmp_path / "docker-compose.base.yml"
    compose_file.write_text("services:\n  hermes:\n    image: x:latest\n")
    compose_args = ["-f", str(compose_file)]
    cfg_result = MagicMock()
    cfg_result.returncode = 0
    cfg_result.stdout = "x:latest\n"

    call_order: list[str] = []

    def _fake_prebuild_pull(*args: object, **kwargs: object) -> bool:
        call_order.append("prebuild_pull")
        return True

    def _fake_build(*args: object, **kwargs: object) -> bool:
        call_order.append("build")
        return True

    with (
        patch.object(hermes_workflow, "_shared_docker_compose_config", return_value=cfg_result),
        patch.object(hermes_workflow, "_shared_check_image_exists", return_value=False),
        patch.object(hermes_workflow, "_shared_docker_prebuild_pull", side_effect=_fake_prebuild_pull),
        patch.object(hermes_workflow, "_shared_docker_compose_build", side_effect=_fake_build),
    ):
        result = hermes_workflow.handle_hermes_agent(compose_args, str(tmp_path), "hermes-agent")

    assert result is True
    assert call_order == ["prebuild_pull", "build"], (
        f"docker_prebuild_pull обязан выполниться РОВНО один раз ДО build_fn; фактический порядок: {call_order}"
    )
    assert call_order.count("prebuild_pull") == 1, (
        f"prebuild_pull должен вызываться ровно один раз за fallback, получено: {call_order.count('prebuild_pull')}"
    )

    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_log = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_log = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · 2026-08-27 · unit · F-03 prebuild-pull exception не роняет workflow (negative)
# · Regression: pre-pull — оптимизация cold-bootstrap (best-effort); exception от него НЕ должен
# ·   абортить деплой — build остаётся арбитром (как _phase_rebuild: fail-fast был бы регрессией).
# · Scenario: docker_prebuild_pull поднимает RuntimeError → build всё равно выполняется → True
# · Remove if: pre-pull best-effort-семантика handle_hermes_agent меняется
def test_handle_hermes_prebuild_pull_exception_no_crash(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Exception от docker_prebuild_pull не роняет workflow — build продолжается (best-effort)."""
    caplog.set_level(logging.INFO)
    compose_file = tmp_path / "docker-compose.base.yml"
    compose_file.write_text("services:\n  hermes:\n    image: x:latest\n")
    compose_args = ["-f", str(compose_file)]
    cfg_result = MagicMock()
    cfg_result.returncode = 0
    cfg_result.stdout = "x:latest\n"

    def _raise_pull(*args: object, **kwargs: object) -> bool:
        failure_msg = "registry transient failure"
        raise RuntimeError(failure_msg)

    with (
        patch.object(hermes_workflow, "_shared_docker_compose_config", return_value=cfg_result),
        patch.object(hermes_workflow, "_shared_check_image_exists", return_value=False),
        patch.object(hermes_workflow, "_shared_docker_prebuild_pull", side_effect=_raise_pull),
        patch.object(hermes_workflow, "_shared_docker_compose_build", return_value=True),
    ):
        result = hermes_workflow.handle_hermes_agent(compose_args, str(tmp_path), "hermes-agent")

    assert result is True, "Exception от pre-pull не должен ронять workflow (build остаётся арбитром)"
    assert "[IMP:7][handle_hermes_agent][prebuild_pull_exc]" in caplog.text, "ожидался IMP:7 warning о raised pre-pull"
    assert "[IMP:9][handle_hermes_agent][built]" in caplog.text, "build должен выполниться после exception pre-pull"
