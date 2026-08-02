#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-subprocess-io run-subprocess canonical check non-fatal rc-127 rc-124 unit C10
# STRUCTURE: ▶ test_graceful_not_found (rc=127) → test_graceful_timeout (rc=124) → test_check_raises → test_non_fatal_warns → test_success
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/subprocess_io.py — единый канон run_subprocess (DevPlan 118 C10).
##           Обе семантики: graceful (check=False, rc 127/124) и raise (check=True).
## @scope    Tests: run_subprocess(). subprocess.run мокается.
## @invariants
##   - check=False: FileNotFoundError → rc=127, TimeoutExpired → rc=124, никогда не raise
##   - check=True: любой failure → PlatformFatalError
##   - non_fatal=True: WARN на ненулевой rc
## @rationale DevPlan 118 C10 §TEST — unit: обе семантики (raise и no-raise) через один канон.
## @changes 2026-08-02 | DevPlan 118 C10 — created
# endregion MODULE_CONTRACT

import logging
from unittest import mock

import pytest

from core.internal.shared import subprocess_io
from core.internal.shared.exceptions import PlatformFatalError
from core.internal.shared.subprocess_io import run_subprocess

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · graceful FileNotFoundError → rc=127 (C10, converge семантика)
# · Scenario: binary not found, check=False → CompletedProcess rc=127, никогда не raise
# · Last fail: converge/infra.py run_subprocess — FileNotFoundError → rc=127 (graceful)
# · Remove if: graceful-семантика канона меняется
def test_graceful_not_found_rc127(caplog) -> None:
    """check=False + FileNotFoundError → rc=127 (graceful, никогда не raise)."""
    caplog.set_level(logging.INFO)
    with mock.patch.object(subprocess_io.subprocess, "run", side_effect=FileNotFoundError("docker")):
        result = run_subprocess(["docker", "ps"])
    assert result.returncode == 127
    assert "not found" in result.stderr


# 🧪 TRAP[TEST] · Regression · graceful TimeoutExpired → rc=124 (C10, converge семантика)
# · Scenario: timeout, check=False → CompletedProcess rc=124, никогда не raise
# · Last fail: converge/infra.py run_subprocess — TimeoutExpired → rc=124 (graceful)
# · Remove if: graceful-семантика канона меняется
def test_graceful_timeout_rc124(caplog) -> None:
    """check=False + TimeoutExpired → rc=124 (graceful, никогда не raise)."""
    caplog.set_level(logging.INFO)
    import subprocess

    with mock.patch.object(subprocess_io.subprocess, "run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
        result = run_subprocess(["docker", "ps"], timeout=30)
    assert result.returncode == 124
    assert "timeout" in result.stderr


# 🧪 TRAP[TEST] · Regression · check=True → PlatformFatalError (C10, lifecycle семантика)
# · Scenario: ненулевой rc + check=True → PlatformFatalError
# · Last fail: lifecycle/helpers/subprocess_io.py — check_required → PlatformFatalError
# · Remove if: raise-семантика канона меняется
def test_check_true_raises(caplog) -> None:
    """check=True + ненулевой rc → PlatformFatalError."""
    caplog.set_level(logging.INFO)
    fake = mock.MagicMock(returncode=3, stdout="", stderr="boom")
    with mock.patch.object(subprocess_io.subprocess, "run", return_value=fake), pytest.raises(PlatformFatalError):
        run_subprocess(["cmd"], check=True)


# 🧪 TRAP[TEST] · Regression · check=True + not-found → PlatformFatalError
# · Scenario: FileNotFoundError + check=True → PlatformFatalError
# · Last fail: lifecycle exit=127 always fatal (TRAP[BUG] 2026-07-22)
# · Remove if: raise-семантика канона меняется
def test_check_true_not_found_raises(caplog) -> None:
    """check=True + FileNotFoundError → PlatformFatalError (exit-127 fatal)."""
    caplog.set_level(logging.INFO)
    with (
        mock.patch.object(subprocess_io.subprocess, "run", side_effect=FileNotFoundError("cmd")),
        pytest.raises(PlatformFatalError),
    ):
        run_subprocess(["cmd"], check=True)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · fatal_rc=(127,) — lifecycle exit=127 always fatal (B4)
# · Scenario: реальный rc=127 + check=False + non_fatal=True + fatal_rc=(127,) → PlatformFatalError
# · Last fail: lifecycle/helpers/subprocess_io.py — exit=127 raise даже при non_fatal=True
# ·   (TRAP[BUG] 2026-07-22: command not found — конфигурационная ошибка, не runtime)
# · Remove if: lifecycle exit=127-fatal семантика меняется
def test_run_subprocess_fatal_rc_127(caplog) -> None:
    """fatal_rc=(127,) + check=False: реальный rc=127 → PlatformFatalError (B4, lifecycle семантика)."""
    caplog.set_level(logging.INFO)
    fake = mock.MagicMock(returncode=127, stdout="", stderr="command not found")
    with mock.patch.object(subprocess_io.subprocess, "run", return_value=fake), pytest.raises(PlatformFatalError):
        run_subprocess(["chown", "x:y", "/tmp"], check=False, non_fatal=True, fatal_rc=(127,))
    # Контроль: без fatal_rc rc=127 возвращается graceful (не raise)
    with mock.patch.object(subprocess_io.subprocess, "run", return_value=fake):
        result = run_subprocess(["chown", "x:y", "/tmp"], check=False, non_fatal=True)
    assert result.returncode == 127


# 🧪 TRAP[TEST] · Regression · success → CompletedProcess rc=0 (C10)
# · Scenario: rc=0 → результат возвращается
# · Last fail: N/A (C10 unit)
# · Remove if: success-семантика меняется
def test_success_returns_process(caplog) -> None:
    """rc=0 → CompletedProcess (exit=0)."""
    caplog.set_level(logging.INFO)
    fake = mock.MagicMock(returncode=0, stdout="ok", stderr="")
    with mock.patch.object(subprocess_io.subprocess, "run", return_value=fake) as mock_run:
        result = run_subprocess(["docker", "ps"])
    assert result.returncode == 0
    assert result.stdout == "ok"
    assert mock_run.call_args.kwargs["capture_output"] is True
    assert mock_run.call_args.kwargs["text"] is True
    assert any("[IMP:9]" in r.message for r in caplog.records), "LDD: no IMP:9 log"
