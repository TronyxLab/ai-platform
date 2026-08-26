"""
# GREP_SUMMARY: test_check_suite_runner, runner, run_cmd, timeout, reaper, orphan, pyright, killpg, process-tree, F-06
# STRUCTURE: ▶ FakeStreamingResult(timed_out, pid) → ◇ run_cmd → ◇ reaper reap_process_tree(pid) → ◇ orphan killed → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/check_suite/runner.py — командный слой check_suite
##           (DevPlan 170 W3). Фокус: F-06 (DevPlan 015) — process-tree reaper при таймауте
##           pyright-шага: run_cmd на timed_out добивает орфанов, переживших killpg.
## @scope    tests/unit (без Docker). Вызывает runner.run_cmd с fake StreamingResult
##           (timed_out=True, pid реального дочернего процесса) — верифицирует, что
##           runner.py вызывает reaper и орфан убит.
## @invariants
##   - tmp_path (R1: no hardcoded paths)
##   - Реальный дочерний процесс (НЕ mock процесса) — честная проверка «0 орфанов»
##   - @ldd_trajectory (IMP:9 assertion)
##   - R4: никаких skip за сервисы
## @rationale DevPlan 015 F-06: killpg (run_subprocess_streaming) не достаёт воркеров,
##            ушедших из process-group (basedpyright/node) → утёкший орфан 209 мин CPU.
##            runner.run_cmd при timeout вызывает reap_process_tree(pid, include_root=True)
##            — вторичный reaper поверх shared-реализации.
## @changes 2026-08-27 | DevPlan 015 F-06 — создан
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.internal.check_suite import runner as runner_mod
from core.internal.check_suite.runner import run_cmd
from core.internal.shared.subprocess_io import StreamingResult
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit


# 🧪 TRAP[TEST] · 2026-08-27 · F-06 (P2) · timeout pyright-шага → reaper добивает орфана
# · Regression: F-06 — утёкший basedpyright-орфан (209 мин CPU) при timeout pyright-шага
# ·   check-suite 120s: killpg НЕ достаёт node-воркеров, ушедших из process-group.
# · Last fail: session 014 — ps aux: basedpyright-орфан жив после таймаута (209 мин CPU)
# · Remove if: run_cmd timeout-семантика / reaper вызов меняются
@ldd_trajectory
def test_pyright_timeout_no_orphans(caplog, tmp_path: Path) -> None:
    """F-06: timeout pyright-шага → runner.run_cmd вызывает process-tree reaper → 0 орфанов."""
    caplog.set_level(logging.INFO)

    # Реальный «воркер», ушедший из process-group (имитация basedpyright node-воркера,
    # пережившего killpg — F-06 кейс: start_new_session = собственная сессия/группа).
    orphan = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        # Fake результат таймаута: run_subprocess_streaming "вернул" timed_out + pid орфана.
        fake_result = StreamingResult(
            cmd=["core/entrypoints/pyright-hook.sh"],
            returncode=124,
            stdout="",
            stderr="Timeout after 120s",
            duration_ms=120000,
            timed_out=True,
            pid=orphan.pid,
        )
        with patch.object(runner_mod, "run_subprocess_streaming", return_value=fake_result):
            outcome = run_cmd("core/entrypoints/pyright-hook.sh", 120, os.environ.copy(), Path(tmp_path))

        assert outcome.exit_code == 124, "timeout → CheckOutcome exit_code=124"
        assert "Timeout after 120s" in (outcome.stderr or ""), "timeout-причина в stderr"

        # Reaper (runner.py) должен убить орфана: poll() не None в течение короткого окна
        deadline = time.monotonic() + 5.0
        while orphan.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert orphan.poll() is not None, "F-06: орфан должен быть убит process-tree reaper'ом (0 орфанов)"

        logger.critical("[IMP:9][test][runner] F-06: timeout pyright → reaper убил орфана (0 орфанов)")
    finally:
        if orphan.poll() is None:
            orphan.kill()


# 🧪 TRAP[TEST] · 2026-08-27 · F-06 (P2) · reap_process_tree убивает process-tree (вкл. ушедших из группы)
# · Regression: F-06 — killpg покрывает только process-group; воркеры с новой сессией выживают
# · Last fail: session 014 — basedpyright-орфан 209 мин CPU
# · Remove if: reap_process_tree реализация меняется
@ldd_trajectory
def test_reap_process_tree_kills_escaped_children(caplog) -> None:
    """F-06: reap_process_tree(pid, include_root=True) убивает дерево, включая процессы вне группы."""
    from core.internal.shared.subprocess_io import reap_process_tree

    caplog.set_level(logging.INFO)

    # Лидер группы + ребёнок в ДРУГОЙ сессии (ушедший из process-group — killpg его не достанет)
    leader = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    escaped = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        killed = reap_process_tree(escaped.pid, include_root=True)
        assert killed >= 1, "reaper должен убить хотя бы сам escaped-процесс (include_root=True)"

        deadline = time.monotonic() + 5.0
        while escaped.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert escaped.poll() is not None, "escaped-процесс должен быть мёртв после reaper'а"

        # NoSuchProcess (мёртвый pid) → 0 (не raise)
        assert reap_process_tree(escaped.pid, include_root=True) == 0

        logger.critical("[IMP:9][test][runner] F-06: reap_process_tree убивает процессы вне process-group")
    finally:
        if leader.poll() is None:
            leader.kill()
        if escaped.poll() is None:
            escaped.kill()
