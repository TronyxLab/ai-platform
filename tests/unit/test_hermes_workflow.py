#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-hermes-workflow, hermes-agent, L1, L2, pull, build, compose-config-images, phase-hermes, E1, unit-tests
# STRUCTURE: ▶ test_phase_hermes_build ┌compose_args + module_dir┐ → mock compose config --images → mock image check (missing) → mock L1 build → ⎋ True │ ▶ test_phase_hermes_all_found → images exist → True (no build) │ ▶ test_phase_hermes_no_images → config fail → False
# region MODULE_CONTRACT
## @purpose  Unit tests for hermes_workflow.py (DevPlan 119 E1 $TEST_SPEC: test_phase_hermes_build)
##           — hermes-agent pre-deploy image check/pull/build phase (extracted in D1, phased in E1).
## @scope    Covers handle_hermes_agent contract: all-images-found → True; missing → L1 pull/build;
##           compose config failure → False. mock subprocess.run + shared docker_compose.
## @invariants
##   - Native imports only, no real docker
##   - LDD: IMP:9 log assertion via ldd_trajectory
##   - E1: phase signature (module_name, module_dir, compose_file, compose_args) via _phase_hermes
## @rationale  $TEST_SPEC E1: test_phase_hermes_build — сборка hermes-образа. Изолированное
##             тестирование спец-фазы (после phased decomposition).
## @changes  2026-08-02 · Created (DevPlan 119 E1)
# endregion MODULE_CONTRACT
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from core.internal.bootstrap.deploy import docker_orchestrator as dorch
from core.internal.bootstrap.deploy import hermes_workflow

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E1 hermes phase — build path
# · Regression: DevPlan 119 E1 — _phase_hermes (legacy cleanup + hermes_workflow)
# · Scenario: images missing → L1 pull/build fallback → True
# · Remove if: hermes phase semantics change
@patch.object(hermes_workflow, "_shared_docker_compose_build", return_value=True)
@patch.object(hermes_workflow, "_shared_check_image_exists", return_value=False)
def test_phase_hermes_build(mock_check, mock_build, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
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
        patch.object(hermes_workflow, "subprocess") as mock_sp,
    ):
        mock_sp.run.return_value = MagicMock(returncode=1)  # docker image inspect → L1 missing
        with patch.object(dorch, "_cleanup_legacy_container", return_value=None):
            result = dorch._phase_hermes(
                module_name="hermes-agent",
                module_dir=str(module_dir),
                compose_file=compose_file,
                compose_args=compose_args,
            )

    assert result is True
    mock_build.assert_called()
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_log = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · 2026-08-02 · unit · hermes all images found → True (no build)
# · Regression: handle_hermes_agent contract (D1)
# · Remove if: hermes workflow semantics change
@patch.object(hermes_workflow, "_shared_check_image_exists", return_value=True)
def test_handle_hermes_all_found_no_build(mock_check, tmp_path) -> None:
    """All hermes images in registry → True immediately (no L1 pull/build)."""
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
