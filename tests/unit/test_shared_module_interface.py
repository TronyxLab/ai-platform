# GREP_SUMMARY: test-shared-module-interface invoke bash-facade module-hooks unit C5 killpg subprocess-io
# STRUCTURE: ▶ test_invoke_success (rc=0 → (True, stderr)) → test_invoke_failure (rc!=0 → (False, stderr)) → test_invoke_timeout_canon_rc124 → test_invoke_args_quoted → test_invoke_killpg_canon_params → test_invoke_orphan_grandchild_killed
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/module_interface.py — единая bash-обёртка
##           invoke_module_interface (DevPlan 118 C5). Вход для B8 (wire module-hooks).
## @scope    Tests: invoke(). Исполнение через subprocess_io streaming-канон (REF-0103:
##           Popen+start_new_session+killpg) — канон мокается; один behavioral-тест гоняет
##           РЕАЛЬНЫЙ bash с внуком-сна (доказывает killpg-семантику без орфанов).
## @invariants
##   - invoke → (bool, stderr); никогда не raise
##   - bash -c содержит source paths.sh && source module-interface.sh && invoke_module_interface
##   - args экранируются shlex.quote
##   - REF-0103: таймаут → graceful rc=124 каноном; внуки процесса умирают вместе с группой
## @rationale DevPlan 118 C5 §TEST — unit на invoke (subprocess-bash фасад).
## @changes 2026-08-02 | DevPlan 118 C5 — created
## @changes 2026-08-25 | REF-0103 — мок subprocess.run → run_subprocess_streaming;
##           +killpg-canon params тест + orphan-grandchild behavioral тест
# endregion MODULE_CONTRACT

import logging
import os
import time
from unittest import mock

import pytest

from core.internal.shared import module_interface
from core.internal.shared.module_interface import invoke
from core.internal.shared.subprocess_io import StreamingResult

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · invoke success → (True, stderr) (C5)
# · Scenario: bash rc=0 → (True, stderr)
# · Last fail: N/A (new — C5 unit)
# · Remove if: invoke semantics change
def test_invoke_success(caplog) -> None:
    """rc=0 → (True, stderr); bash -c содержит source paths + module-interface + invoke."""
    caplog.set_level(logging.INFO)
    fake = StreamingResult(cmd=["bash", "-c", "x"], returncode=0, stdout="out", stderr="diag")
    with mock.patch.object(module_interface, "run_subprocess_streaming", return_value=fake) as mock_run:
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
    fake = StreamingResult(cmd=["bash", "-c", "x"], returncode=1, stdout="", stderr="boom")
    with mock.patch.object(module_interface, "run_subprocess_streaming", return_value=fake):
        ok, out = invoke("postgres", "install")
    assert ok is False
    assert out == "boom"


# 🧪 TRAP[TEST] · Regression · invoke timeout → graceful rc=124, никогда не raise (C5 + REF-0103)
# · Scenario: канон возвращает rc=124 timed_out=True → (False, msg)
# · Last fail: REF-0103 — прежний subprocess.run бросал TimeoutExpired наверх (теперь канон
# ·   обрабатывает таймаут сам: killpg группы + partial вывод)
# · Remove if: invoke error handling changes
def test_invoke_timeout_never_raises(caplog) -> None:
    """Канон-таймаут (rc=124 timed_out) → (False, msg) — никогда не raise."""
    caplog.set_level(logging.INFO)

    fake = StreamingResult(cmd=["bash", "-c", "x"], returncode=124, stdout="", stderr="", timed_out=True)
    with mock.patch.object(module_interface, "run_subprocess_streaming", return_value=fake):
        ok, out = invoke("postgres", "healthcheck")
    assert ok is False
    assert isinstance(out, str), "stderr-контракт сохранён (строка, даже пустая при killpg)"


# 🧪 TRAP[TEST] · Regression · args экранируются shlex.quote (C5)
# · Scenario: arg с пробелом → shlex.quote в bash -c
# · Last fail: N/A (C5)
# · Remove if: экранирование args меняется
def test_invoke_args_shlex_quoted(caplog) -> None:
    """args экранируются shlex.quote — безопасная передача строк с пробелами."""
    caplog.set_level(logging.INFO)
    fake = StreamingResult(cmd=["bash", "-c", "x"], returncode=0, stdout="", stderr="")
    with mock.patch.object(module_interface, "run_subprocess_streaming", return_value=fake) as mock_run:
        invoke("nginx", "deploy-hook", "two words arg")
    joined = mock_run.call_args.args[0][2]
    assert "'two words arg'" in joined, f"arg должен быть shlex.quote-экранирован: {joined}"


# 🧪 TRAP[TEST] · Regression · default timeout = COMPOSE_UP_TIMEOUT (C5)
# · Scenario: invoke без timeout → канон получает timeout=COMPOSE_UP_TIMEOUT
# · Last fail: docker_orchestrator/deploy_orchestrator использовали разные таймауты
# · Remove if: дефолтный таймаут меняется
def test_invoke_default_timeout(caplog) -> None:
    """Дефолтный timeout — канон COMPOSE_UP_TIMEOUT (180)."""
    caplog.set_level(logging.INFO)
    from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT

    fake = StreamingResult(cmd=["bash", "-c", "x"], returncode=0, stdout="", stderr="")
    with mock.patch.object(module_interface, "run_subprocess_streaming", return_value=fake) as mock_run:
        invoke("postgres", "install")
    assert mock_run.call_args.kwargs["timeout"] == COMPOSE_UP_TIMEOUT


# 🧐 TRAP[DECISION] · 2026-08-25 · — · invoke исполняется через killpg-канон subprocess_io (REF-0103)
# · Rejected: оставить subprocess.run (таймаут убивал только bash — внуки-орфаны жили)
# · Reason: healthcheck.sh/install.sh спавнят docker/psql-процессы; после «завершения» по
# ·   таймауту они продолжали держать ресурсы. Канон: start_new_session + os.killpg(SIGKILL).
# · Rev: если появится потребитель, которому нужны живые внуки ПОСЛЕ таймаута invoke — пересмотреть
# 🧪 TRAP[TEST] · Regression · REF-0103 · killpg-canon параметры invoke
# · Scenario: invoke передаёт stream=False (без tee-шума) и heartbeat=0 (без heartbeat-строк)
# · Last fail: N/A (new — REF-0103)
# · Remove if: invoke перестаёт использовать subprocess_io canon
def test_invoke_killpg_canon_params(caplog) -> None:
    """invoke использует subprocess_io canon: stream=False, heartbeat=0 (killpg при таймауте)."""
    caplog.set_level(logging.INFO)
    fake = StreamingResult(cmd=["bash", "-c", "x"], returncode=0, stdout="", stderr="")
    with mock.patch.object(module_interface, "run_subprocess_streaming", return_value=fake) as mock_run:
        invoke("postgres", "healthcheck", "liveness", timeout=30)
    kwargs = mock_run.call_args.kwargs
    assert kwargs.get("stream") is False, "stream=False обязателен (invoke не tee-ит вывод)"
    assert kwargs.get("heartbeat") == 0, "heartbeat=0 обязателен (capture-контекст)"


# 🧪 TRAP[TEST] · Behavioral · REF-0103 · таймаут invoke убивает ВНУКА (killpg группы)
# · Scenario: реальный bash спавнит отвязанного внука (sleep 30 &) и ждёт; invoke(timeout=1)
# ·   должен вернуть (False, ...) быстро, а внук — умереть вместе с группой процессов.
# · Last fail: subprocess.run убивал только bash — внук оставался жить (орфан)
# · Remove if: механизм таймаута invoke меняется (не killpg)
@pytest.mark.parametrize("static_audit_marker", [pytest.param(True, id="real-subprocess")])
def test_invoke_timeout_kills_grandchild(caplog, tmp_path, static_audit_marker) -> None:
    """Реальный таймаут invoke-канона → внук-процесс мёртв (killpg), dispatch не подвисает."""
    del static_audit_marker  # параметризация только для читаемого test-id
    pidfile = tmp_path / "grandchild.pid"
    # Внук отвязывается (nohup-стиль) и пишет свой PID; скрипт ждёт «вечность» (sleep 60).
    hang_body = f"sleep 60 & echo $! > '{pidfile}'\nwait\n"
    started = time.monotonic()
    # Публичный invoke строит bash -c сам — behavioral-тест идёт через _run_module_script,
    # тот же killpg-канон dispatch-пути REF-0103.
    script_path = tmp_path / "hang.sh"
    script_path.write_text("#!/usr/bin/env bash\n" + hang_body, encoding="utf-8")
    script_path.chmod(0o755)
    rc, _err = module_interface._run_module_script(script_path, (), timeout=1)
    elapsed = time.monotonic() - started

    assert rc != 0, "таймаут скрипта → ненулевой rc (graceful)"
    assert elapsed < 10, f"invoke завис на {elapsed:.1f}s — таймаут не сработал"

    # Внук обязан умереть вместе с группой (killpg SIGKILL): os.kill(pid, 0) → ProcessLookupError.
    deadline_check = time.monotonic() + 5
    grandchild_alive = True
    while time.monotonic() < deadline_check:
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)  # сигнал 0 = проверка жизни
        except (ProcessLookupError, PermissionError, ValueError, OSError):
            grandchild_alive = False
            break
        except FileNotFoundError:
            break  # pidfile не создан — внук не стартовал (тоже нет орфана)
        time.sleep(0.1)
    logger.critical(
        "[IMP:9][test][REF-0103] grandchild killed=%s elapsed=%.1fs rc=%s", not grandchild_alive, elapsed, rc
    )
    assert not grandchild_alive, "внук пережил таймаут invoke — killpg-канон сломан (регрессия REF-0103)"
