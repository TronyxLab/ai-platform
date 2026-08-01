#!/usr/bin/env python3
# GREP_SUMMARY: subprocess-io, run-subprocess, timeout, non-fatal, check-required, platform-fatal, exit-127
# STRUCTURE: ▶ ┌cmd + step_name┐ → ⚡ subprocess.run(capture_output, timeout) → ◇ rc!=0? → ◇ exit=127? FATAL │ ◇ non_fatal? WARN │ ◇ check_required? FATAL │ ⎋ CompletedProcess
# region MODULE_CONTRACT
## @purpose  Безопасный subprocess wrapper для bootstrap-фаз — единая обработка timeout/exit-127/
##           non-fatal, извлечён из state_machine._subprocess_run (B9 T1, U-08).
## @scope    Используется lifecycle/helpers/{system,users,secrets,validation,domains,reporting}.py
##           и phases.py. Публичная функция: run_subprocess.
## @invariants
##   - exit=127 (command not found) ВСЕГДА fatal (PlatformFatalError) — non_fatal не применяется
##   - non_fatal=True → WARN + возврат CompletedProcess(rc=-1) вместо raise
##   - check_required=False → INFO-лог для ненулевого rc без raise (best-effort)
##   - TimeoutExpired/FileNotFoundError: non_fatal → CompletedProcess(rc=-1), иначе PlatformFatalError
## @rationale Единая точка обработки subprocess-ошибок для всех фаз (W4-E2 legacy parity).
##            Исключение exit=127 задокументировано TRAP[BUG] (043-staging-fix B3).
## @changes  2026-08-01 · Extracted from state_machine._subprocess_run (B9 T1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess

from core.internal.shared.exceptions import PlatformFatalError

logger = logging.getLogger(__name__)


# region FUNC_run_subprocess
## @purpose  Run subprocess with uniform logging, timeout, and error handling (bootstrap-стандарт).
## @io       ⇥ cmd: list[str], step_name: str (для логов),
##           *: non_fatal: bool = False, check_required: bool = True, timeout: int = 120
##           ⎋ subprocess.CompletedProcess
## @complexity O(1) orchestration, O(M) for command execution
## @invariants
##   - Все bootstrap subprocess вызовы проходят через этот wrapper (единый канон)
##   - capture_output=True, text=True, timeout=120 по умолчанию (node_update=600 через timeout=)
##   ⚠️ TRAP[BUG] · 2026-07-22 · P1 · exit=127 всегда fatal — non_fatal НЕ применяется
##   · Symptom: command not found (exit=127) трактовался как non-fatal → bootstrap продолжал
##   ·   с отсутствующим бинарём, последующие фазы падали нечитаемо.
##   · Root: ошибка конфигурации (missing dependency/binary), а не runtime-ошибка.
##   · Fix: raise PlatformFatalError при exit=127 вне зависимости от non_fatal.
##   · Prevention: exit=127 — отдельный класс ошибки, обрабатывается до non_fatal-ветки.
def run_subprocess(
    cmd: list[str],
    step_name: str,
    *,
    non_fatal: bool = False,
    check_required: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run a subprocess command with timeout and error handling."""
    logger.info("[IMP:8][subprocess][%s] Running: %s", step_name, " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            err_msg = f"Command {' '.join(cmd)} failed (exit={result.returncode}): {result.stderr.strip()}"
            if result.stdout:
                logger.debug("[IMP:6][subprocess][%s] stdout: %s", step_name, result.stdout[:500])
            if result.returncode == 127:
                # ⚠️ TRAP[BUG] · 2026-07-22 · P1 · exit=127 (command not found) is always fatal
                # · non_fatal flag does NOT apply to 127 — it's a configuration error, not a runtime error
                raise PlatformFatalError(f"Command not found (exit=127): {err_msg}")
            if non_fatal:
                logger.warning("[IMP:7][subprocess][%s] %s", step_name, err_msg)
            elif check_required:
                raise PlatformFatalError(err_msg)
            else:
                logger.info(
                    "[IMP:7][subprocess][%s] Non-critical command returned %d: %s",
                    step_name,
                    result.returncode,
                    result.stderr[:200],
                )
        else:
            logger.info("[IMP:9][subprocess][%s] Command succeeded (exit=0)", step_name)
        return result
    except subprocess.TimeoutExpired:
        msg = f"Command {' '.join(cmd)} timed out after {timeout}s"
        if non_fatal:
            logger.warning("[IMP:7][subprocess][%s] %s", step_name, msg)
            return subprocess.CompletedProcess(cmd, -1, "", msg)
        raise PlatformFatalError(msg) from None
    except FileNotFoundError:
        msg = f"Command not found: {cmd[0]}"
        if non_fatal:
            logger.warning("[IMP:7][subprocess][%s] %s", step_name, msg)
            return subprocess.CompletedProcess(cmd, -1, "", msg)
        raise PlatformFatalError(msg) from None


# endregion FUNC_run_subprocess
