# GREP_SUMMARY: test-shared-subprocess-io run-subprocess run-subprocess-streaming canonical check non-fatal rc-127 rc-124 killpg heartbeat unit C10 006
# STRUCTURE: ▶ test_graceful_not_found (rc=127) → test_graceful_timeout (rc=124) → test_check_raises → test_non_fatal_warns → test_success → ⊕ streaming (tee/heartbeat/killpg/124/127/env)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/subprocess_io.py — единый канон run_subprocess (DevPlan 118 C10).
##           Обе семантики: graceful (check=False, rc 127/124) и raise (check=True).
##           W4b (160 T4.2): +CommandRunner Protocol / SubprocessCommandRunner / default_command_runner.
## @scope    Tests: run_subprocess() + CommandRunner DI. subprocess.run мокается.
## @invariants
##   - check=False: FileNotFoundError → rc=127, TimeoutExpired → rc=124, никогда не raise
##   - check=True: любой failure → PlatformFatalError
##   - non_fatal=True: WARN на ненулевой rc
##   - SubprocessCommandRunner.run() делегирует run_subprocess (0 дублей логики)
## @rationale DevPlan 118 C10 §TEST — unit: обе семантики (raise и no-raise) через один канон.
## @changes 2026-08-02 | DevPlan 118 C10 — created
## @changes 2026-08-13 | DevPlan 160 W4b — +CommandRunner DI-тесты
## @changes 2026-08-17 | DevPlan 006 W1 — +streaming-тесты: tee в stderr, накопление,
##           heartbeat, killpg при таймауте (без орфанов), rc=127/124, PYTHONUNBUFFERED
# endregion MODULE_CONTRACT

import logging
import sys
from unittest import mock

import pytest

from core.internal.shared import subprocess_io
from core.internal.shared.exceptions import PlatformFatalError
from core.internal.shared.subprocess_io import (
    CommandRunner,
    SubprocessCommandRunner,
    default_command_runner,
    run_subprocess,
    run_subprocess_streaming,
)

pytestmark = pytest.mark.static_audit

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


# 🧪 TRAP[TEST] · Regression · CommandRunner: SubprocessCommandRunner делегирует run_subprocess (W4b)
# · Scenario: runner.run(cmd, check=True) с ненулевым rc → PlatformFatalError (канон C10 через DI)
# · Last fail: N/A (new — DevPlan 160 W4b T4.2 CommandRunner)
# · Remove if: CommandRunner протокол удалён
def test_subprocess_command_runner_delegates_run_subprocess(caplog) -> None:
    """SubprocessCommandRunner.run() == run_subprocess() (единая семантика, 0 дублей)."""
    caplog.set_level(logging.INFO)
    fake = mock.MagicMock(returncode=3, stdout="", stderr="boom")
    runner = SubprocessCommandRunner()
    with mock.patch.object(subprocess_io.subprocess, "run", return_value=fake), pytest.raises(PlatformFatalError):
        runner.run(["cmd"], check=True)
    # graceful-путь через runner: rc возвращается как есть
    with mock.patch.object(subprocess_io.subprocess, "run", return_value=fake):
        result = runner.run(["cmd"], check=False, non_fatal=True)
    assert result.returncode == 3


# 🧪 TRAP[TEST] · Regression · CommandRunner: default_command_runner() фабрика (W4b)
# · Scenario: default_command_runner() → SubprocessCommandRunner (инстанс протокола)
# · Last fail: N/A (new — DevPlan 160 W4b T4.2)
# · Remove if: default_command_runner удалён
def test_default_command_runner_factory() -> None:
    """default_command_runner() возвращает CommandRunner-совместимый объект (Protocol runtime-checkable)."""
    runner = default_command_runner()
    assert isinstance(runner, CommandRunner), "SubprocessCommandRunner обязан соответствовать CommandRunner Protocol"
    assert isinstance(runner, SubprocessCommandRunner)


# ══════════════════════════════════════════════════════════════════════════
# run_subprocess_streaming (DevPlan 006 W1) — реальные subprocess'ы (python3 -c)
# ══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · streaming: накопление полного вывода + rc=0 (006 W1)
# · Scenario: child печатает строки в stdout/stderr → StreamingResult содержит обе, rc=0
# · Last fail: N/A (new — DevPlan 006 W1 streaming-канон)
# · Remove if: run_subprocess_streaming удалён
def test_streaming_accumulates_output_and_rc0() -> None:
    """Стриминг: полный stdout/stderr накапливается, rc=0, timed_out=False."""
    result = run_subprocess_streaming(
        [sys.executable, "-c", "import sys; print('out1'); print('err1', file=sys.stderr)"],
        timeout=30,
        stream=False,
    )
    assert result.returncode == 0
    assert result.timed_out is False
    assert result.stdout == "out1"
    assert result.stderr == "err1"
    assert result.duration_ms >= 0


# 🧪 TRAP[TEST] · Regression · streaming: tee в stderr с префиксом [child] (006 W1)
# · Scenario: stream=True → строки child'а появляются в sys.stderr вызывающего с [child]
# · Last fail: N/A (new — DevPlan 006 W1; R1: stdout остаётся чистым)
# · Remove if: run_subprocess_streaming удалён
def test_streaming_tee_to_stderr_with_child_prefix(capsys) -> None:
    """stream=True: построчный tee в stderr с префиксом [child]; stdout вызывающего чист."""
    run_subprocess_streaming(
        [sys.executable, "-c", "print('visible-line')"],
        timeout=30,
        stream=True,
        heartbeat=0,
    )
    captured = capsys.readouterr()
    assert not captured.out, "R1: stdout вызывающего обязан оставаться чистым"
    assert "[child] visible-line" in captured.err


# 🧪 TRAP[TEST] · Regression · streaming: heartbeat каждые N секунд (006 W1)
# · Scenario: heartbeat=1, спящий 2.5s child → ≥1 heartbeat-строка в stderr
# · Last fail: N/A (new — DevPlan 006 W1)
# · Remove if: run_subprocess_streaming удалён
def test_streaming_heartbeat_emits_to_stderr(capsys) -> None:
    """heartbeat>0: тишина ≠ зависание — [stream][heartbeat] появляется в stderr."""
    run_subprocess_streaming(
        [sys.executable, "-c", "import time; time.sleep(2.5)"],
        timeout=30,
        stream=False,
        heartbeat=1,
    )
    captured = capsys.readouterr()
    assert "[stream][heartbeat]" in captured.err
    assert "pid=" in captured.err


# 🧪 TRAP[TEST] · Regression · streaming: таймаут → killpg, rc=124, без орфанов (006 W1, R2/R6)
# · Scenario: sleep 300 с timeout=2 → StreamingResult(rc=124, timed_out=True), группа убита
# · Last fail: N/A (new — DevPlan 006 W1; орфаны ночь-141 исключены конструктивно)
# · Remove if: run_subprocess_streaming удалён
def test_streaming_timeout_killpg_no_orphans() -> None:
    """Таймаут: killpg → rc=124 + timed_out=True, partial вывод сохранён, НИКОГДА не raise."""
    marker = "before-hang"
    result = run_subprocess_streaming(
        [
            sys.executable,
            "-c",
            f"print('{marker}', flush=True); import time; time.sleep(300)",
        ],
        timeout=2,
        stream=False,
        heartbeat=0,
    )
    assert result.returncode == 124
    assert result.timed_out is True
    assert marker in result.stdout, "partial stdout обязан сохраняться после killpg"


# 🧪 TRAP[TEST] · Regression · streaming: FileNotFoundError → rc=127 graceful (006 W1)
# · Scenario: несуществующий бинарный → StreamingResult(rc=127), check=False — не raise
# · Last fail: N/A (new — DevPlan 006 W1)
# · Remove if: run_subprocess_streaming удалён
def test_streaming_not_found_rc127() -> None:
    """FileNotFoundError → rc=127 graceful; check=True → PlatformFatalError."""
    result = run_subprocess_streaming(["definitely-not-a-binary-006"], timeout=5, stream=False)
    assert result.returncode == 127
    assert "not found" in result.stderr
    with pytest.raises(PlatformFatalError):
        run_subprocess_streaming(["definitely-not-a-binary-006"], timeout=5, stream=False, check=True)


# 🧪 TRAP[TEST] · Regression · streaming: child env содержит PYTHONUNBUFFERED=1 (006 W1)
# · Scenario: child печатает os.environ['PYTHONUNBUFFERED'] → '1'
# · Last fail: N/A (new — DevPlan 006 W1; перенос PYTHONUNBUFFERED из run_cmd)
# · Remove if: run_subprocess_streaming удалён
def test_streaming_child_env_pythonunbuffered() -> None:
    """child_env всегда содержит PYTHONUNBUFFERED=1 — дети не буферизуют Python-вывод."""
    result = run_subprocess_streaming(
        [sys.executable, "-c", "import os; print(os.environ.get('PYTHONUNBUFFERED', ''))"],
        timeout=30,
        stream=False,
        heartbeat=0,
    )
    assert result.stdout.strip() == "1"


# 🧪 TRAP[TEST] · Regression · streaming: env-merge — пользовательский env поверх os.environ (006 W1)
# · Scenario: env={'PROBE_006': 'x'} → child видит PROBE_006 И PATH (наследованный)
# · Last fail: N/A (new — DevPlan 006 W1)
# · Remove if: run_subprocess_streaming удалён
def test_streaming_env_merges_over_environ() -> None:
    """env мержится поверх os.environ (не замещает PATH и пр.)."""
    result = run_subprocess_streaming(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('PROBE_006', ''), os.environ.get('PATH', '') != '')",
        ],
        timeout=30,
        stream=False,
        heartbeat=0,
        env={"PROBE_006": "seen"},
    )
    assert result.stdout.strip() == "seen True"


# 🧪 TRAP[TEST] · Regression · streaming: check=True на ненулевом rc → PlatformFatalError (006 W1)
# · Scenario: rc=3 + check=True → raise; graceful-путь rc=3 возвращается
# · Last fail: N/A (new — DevPlan 006 W1, parity с run_subprocess)
# · Remove if: run_subprocess_streaming удалён
def test_streaming_check_true_raises() -> None:
    """check=True: ненулевой rc → PlatformFatalError; check=False → graceful."""
    code = "import sys; sys.exit(3)"
    with pytest.raises(PlatformFatalError):
        run_subprocess_streaming([sys.executable, "-c", code], timeout=30, stream=False, check=True)
    result = run_subprocess_streaming([sys.executable, "-c", code], timeout=30, stream=False)
    assert result.returncode == 3
