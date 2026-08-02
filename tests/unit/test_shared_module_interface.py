#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-module-interface invoke bash-facade module-hooks unit C5
# STRUCTURE: ▶ test_invoke_success (rc=0 → (True, stderr)) → test_invoke_failure (rc!=0 → (False, stderr)) → test_invoke_timeout → test_invoke_args_quoted
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/module_interface.py — единая bash-обёртка
##           invoke_module_interface (DevPlan 118 C5). Вход для B8 (wire module-hooks).
## @scope    Tests: invoke(). subprocess.run мокается — нет реального bash.
## @invariants
##   - invoke → (bool, stderr); никогда не raise
##   - bash -c содержит source paths.sh && source module-interface.sh && invoke_module_interface
##   - args экранируются shlex.quote
## @rationale DevPlan 118 C5 §TEST — unit на invoke (subprocess-bash фасад).
## @changes 2026-08-02 | DevPlan 118 C5 — created
# endregion MODULE_CONTRACT

import logging
from unittest import mock

from core.internal.shared import module_interface
from core.internal.shared.module_interface import invoke

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · invoke success → (True, stderr) (C5)
# · Scenario: bash rc=0 → (True, stderr)
# · Last fail: N/A (new — C5 unit)
# · Remove if: invoke semantics change
def test_invoke_success(caplog) -> None:
    """rc=0 → (True, stderr); bash -c содержит source paths + module-interface + invoke."""
    caplog.set_level(logging.INFO)
    fake = mock.MagicMock(returncode=0, stdout="out", stderr="diag")
    with mock.patch.object(module_interface.subprocess, "run", return_value=fake) as mock_run:
        ok, out = invoke("postgres", "healthcheck", "liveness", timeout=60)

    assert ok is True
    assert out == "diag"
    bash_cmd = mock_run.call_args.args[0]
    assert isinstance(bash_cmd, list) and bash_cmd[0] == "bash" and bash_cmd[1] == "-c"
    joined = bash_cmd[2]
    assert "source" in joined and "invoke_module_interface" in joined
    assert "'postgres'" in joined and "'healthcheck'" in joined
    assert "liveness" in joined
    assert any("[IMP:9]" in r.message for r in caplog.records), "LDD: no IMP:9 log"


# 🧪 TRAP[TEST] · Regression · invoke failure → (False, stderr) (C5)
# · Scenario: bash rc=1 → (False, stderr) — никогда не raise
# · Last fail: N/A (C5 unit)
# · Remove if: invoke semantics change
def test_invoke_failure(caplog) -> None:
    """rc!=0 → (False, stderr)."""
    caplog.set_level(logging.INFO)
    fake = mock.MagicMock(returncode=1, stdout="", stderr="boom")
    with mock.patch.object(module_interface.subprocess, "run", return_value=fake):
        ok, out = invoke("postgres", "install")
    assert ok is False
    assert out == "boom"


# 🧪 TRAP[TEST] · Regression · invoke timeout → (False, msg) никогда не raise (C5)
# · Scenario: TimeoutExpired → (False, str(exc))
# · Last fail: N/A (C5 unit)
# · Remove if: invoke error handling changes
def test_invoke_timeout_never_raises(caplog) -> None:
    """TimeoutExpired → (False, msg) — никогда не raise."""
    caplog.set_level(logging.INFO)
    import subprocess

    with mock.patch.object(
        module_interface.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired("bash", 60),
    ):
        ok, out = invoke("postgres", "healthcheck")
    assert ok is False
    assert "timed out" in out.lower() or out  # msg содержит детали таймаута


# 🧪 TRAP[TEST] · Regression · args экранируются shlex.quote (C5)
# · Scenario: arg с пробелом → shlex.quote в bash -c
# · Last fail: N/A (C5 unit)
# · Remove if: экранирование args меняется
def test_invoke_args_shlex_quoted(caplog) -> None:
    """args экранируются shlex.quote — безопасная передача строк с пробелами."""
    caplog.set_level(logging.INFO)
    fake = mock.MagicMock(returncode=0, stdout="", stderr="")
    with mock.patch.object(module_interface.subprocess, "run", return_value=fake) as mock_run:
        invoke("nginx", "deploy-hook", "two words arg")
    joined = mock_run.call_args.args[0][2]
    assert "'two words arg'" in joined, f"arg должен быть shlex.quote-экранирован: {joined}"


# 🧪 TRAP[TEST] · Regression · default timeout = COMPOSE_UP_TIMEOUT (C5)
# · Scenario: invoke без timeout → subprocess.run timeout=COMPOSE_UP_TIMEOUT
# · Last fail: docker_orchestrator/deploy_orchestrator использовали разные таймауты
# · Remove if: дефолтный таймаут меняется
def test_invoke_default_timeout(caplog) -> None:
    """Дефолтный timeout — канон COMPOSE_UP_TIMEOUT (180)."""
    caplog.set_level(logging.INFO)
    from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT

    fake = mock.MagicMock(returncode=0, stdout="", stderr="")
    with mock.patch.object(module_interface.subprocess, "run", return_value=fake) as mock_run:
        invoke("postgres", "install")
    assert mock_run.call_args.kwargs["timeout"] == COMPOSE_UP_TIMEOUT
