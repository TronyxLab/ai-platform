#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-provision-flow, llm, render, provision, litellm-config, virtual-keys, E7, R5, unit-tests
# STRUCTURE: ▶ test_render_and_provision_llm ┌mock subprocess + tmp core┐ → render_and_provision_llm() → ◇ renderer called → ◇ provision called → ⎋ None (non-fatal) │ ▶ test_render_and_provision_missing_scripts_negative → non-fatal skip │ ▶ test_context_deployer_llm_flow_negative → context_deployer._render_and_provision_llm делегирует в llm_provision
# region MODULE_CONTRACT
## @purpose  Unit tests for llm-provision flow (DevPlan 119 E7 $TEST_SPEC: test_render_and_provision_llm).
##           LLM-слой извлечён в bootstrap/deploy/llm_provision.py (wave 117 G T58.5);
##           context_deployer._render_and_provision_llm — ленивый фасад (E7 verify).
## @scope    render_and_provision_llm (llm_provision.py) + context_deployer делегирование.
## @invariants
##   - Native imports; mock subprocess; tmp_path
##   - R5: flow не нарушен — context_deployer → llm_provision (test_context_deployer_llm_flow_negative)
## @rationale  $TEST_SPEC E7 — LLM provisioning flow изолированно; R5 — фасад контракт сохранён.
## @changes  2026-08-02 · Created (DevPlan 119 E7)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.internal.bootstrap.deploy import context_deployer, llm_provision
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E7 LLM provision flow
# · Regression: DevPlan 119 E7 — LLM-слой извлечён из context_deployer (117 G T58.5 + E7 verify)
# · Last fail: N/A (new flow module)
# · Remove if: LLM provision flow changes
@ldd_trajectory
def test_render_and_provision_llm(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    """render_and_provision_llm: render + provision via subprocess (non-fatal)."""
    caplog.set_level(logging.INFO)
    core_dir = tmp_path / "core"
    (core_dir / "internal" / "llm").mkdir(parents=True)
    (core_dir / "internal" / "llm" / "config_renderer.py").write_text("")
    (core_dir / "entrypoints").mkdir()
    (core_dir / "entrypoints" / "provision-llm.sh").write_text("")

    monkeypatch.setenv("CORE_DIR", str(core_dir))

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(llm_provision.subprocess, "run", side_effect=_fake_run):
        llm_provision.render_and_provision_llm()

    all_cmds = " ".join(" ".join(c) for c in calls)
    assert "config_renderer.py" in all_cmds, "renderer must be invoked"
    assert "provision-llm.sh" in all_cmds, "provision entrypoint must be invoked"
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


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E7 missing scripts → non-fatal skip
# · Regression: render/provision скрипты отсутствуют → WARN, не raise (non-fatal)
# · Remove if: non-fatal LLM semantics change
def test_render_and_provision_missing_scripts_negative(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """render_and_provision_llm with no scripts → no subprocess calls (graceful skip)."""
    caplog.set_level(logging.INFO)
    core_dir = tmp_path / "core"
    (core_dir / "internal" / "llm").mkdir(parents=True)
    (core_dir / "entrypoints").mkdir()

    monkeypatch.setenv("CORE_DIR", str(core_dir))

    with patch.object(llm_provision.subprocess, "run", return_value=MagicMock(returncode=0)) as mock_run:
        llm_provision.render_and_provision_llm()
    mock_run.assert_not_called()
    logger.critical("[IMP:9][test] missing-scripts graceful skip verified")


# 🧪 TRAP[TEST] · 2026-08-02 · R5 · E7 flow — context_deployer делегирует в llm_provision
# · Regression: DevPlan 119 E7 — _render_and_provision_llm (context_deployer) → llm_provision
# · Scenario: deploy_context_projects вызывает _render_and_provision_llm после деплоя проектов
# · Remove if: LLM-слой переносится в другой модуль
def test_context_deployer_llm_flow_negative() -> None:
    """R5 (E7): context_deployer._render_and_provision_llm делегирует в llm_provision."""
    assert hasattr(context_deployer, "_render_and_provision_llm"), (
        "context_deployer must keep _render_and_provision_llm"
    )
    assert hasattr(llm_provision, "render_and_provision_llm"), "llm_provision must define render_and_provision_llm"

    # Ленивый фасад импортирует llm_provision.render_and_provision_llm (117 G T58.5)
    import inspect

    src = inspect.getsource(context_deployer._render_and_provision_llm)
    assert "llm_provision" in src, "facade must import llm_provision (E7)"
    assert "render_and_provision_llm" in src, "facade must delegate to render_and_provision_llm (E7)"
    logger.critical("[IMP:9][test] context_deployer → llm_provision flow verified (E7)")
