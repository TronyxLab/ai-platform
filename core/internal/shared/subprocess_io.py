#!/usr/bin/env python3
# GREP_SUMMARY: subprocess-io, run-subprocess, canonical, check, non-fatal, timeout, rc-127, rc-124, graceful
# STRUCTURE: ▶ ┌cmd + timeout + check + non_fatal┐ → ⚡ subprocess.run(capture_output, text, timeout) → ◇ FileNotFound → ⎋ rc=127 | check? raise ┤
#            → ◇ TimeoutExpired → ⎋ rc=124 | check? raise → ◇ rc!=0 → check? raise │ non_fatal? WARN │ ⎋ CompletedProcess
# region MODULE_CONTRACT
## @purpose  Единый канон run_subprocess (DevPlan 118 C10 + 119 B4) — единственный источник семантики
##           subprocess-вызова «graceful | raise» для платформенных Python-модулей.
##           Дедупликация двух несовместимых реализаций: lifecycle/helpers/subprocess_io.py
##           (raise PlatformFatalError, exit-127 always fatal) и converge/infra.py
##           (никогда не raise, rc 127/124). Обе семантики выражаются параметрами:
##           check=False (default) = graceful-стиль converge; check=True = raise-стиль lifecycle.
##           119 B4: lifecycle/helpers/subprocess_io.py УДАЛЁН — bootstrap-фазы мигрированы
##           на этот канон; exit=127 всегда fatal выражается через fatal_rc=(127,) (TRAP[BUG]
##           2026-07-22 семантика сохраняется и для non_fatal-вызовов).
## @scope    Импортируется converge/infra.py (делегирование, DevPlan 118 C10) и
##           bootstrap-фазами lifecycle/helpers/* + phases.py (DevPlan 119 B4).
## @invariants
##   1. Сигнатура: run_subprocess(cmd, *, timeout, check, non_fatal, fatal_rc) → subprocess.CompletedProcess
##   2. check=False (default): FileNotFoundError → rc=127, TimeoutExpired → rc=124, ненулевой rc
##      возвращается как есть — НИКОГДА не raise (семантика converge, DevPlan 118 C10)
##   3. check=True: любой failure → PlatformFatalError (семантика lifecycle, exit-127 фатален)
##   4. non_fatal=True: WARN-лог на ненулевой rc без raise (полезно при check=False для видимости)
##   5. fatal_rc=(127,) (B4): реальный rc команды = 127 → PlatformFatalError ДАЖЕ при check=False
##      (lifecycle TRAP[BUG] 2026-07-22: command-not-found — конфигурационная ошибка, не runtime).
##      Синтетические rc (FileNotFound=127, Timeout=124) НЕ подпадают под fatal_rc — graceful.
##   6. capture_output=True + text=True — всегда (стандарт платформы)
##   7. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
## @rationale C10 (DevPlan 118): ДВА run_subprocess с разными сигнатурами/семантиками
##            (lifecycle: raise + exit-127 always fatal; converge: никогда не raise, rc 127/124)
##            — источник дрейфа при правках. Единый канон в shared/ выражает обе семантики
##            через параметры; converge/infra делегирует (check=False — сохранение своей семантики).
##            B4 (DevPlan 119): копия lifecycle/helpers/subprocess_io.py удалена — единый канон.
## @changes  2026-08-02 | DevPlan 118 C10 — Created (единый канон run_subprocess)
## @changes  2026-08-02 | DevPlan 119 B4 — +fatal_rc=(127,); lifecycle/helpers/subprocess_io.py удалён
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess

from core.internal.shared.exceptions import PlatformFatalError

logger = logging.getLogger(__name__)

# ── Default timeout: канон converge DOCKER_TIMEOUT=30 (DevPlan 118 C10) ──
DEFAULT_TIMEOUT: int = 30
"""## @invariant Default timeout (sec) — совпадает с converge/infra.DOCKER_TIMEOUT=30 (C10)."""


# region FUNC_run_subprocess
## @purpose  Единый канон subprocess-вызова: graceful (check=False) | raise (check=True).
## @io       ⇥ cmd: list[str]; timeout: int; check: bool; non_fatal: bool; fatal_rc: tuple[int, ...]
##           ⎋ subprocess.CompletedProcess (check=False) ⚡ PlatformFatalError (check=True / fatal_rc)
## @complexity O(M) where M = command execution time
## @invariants
##   - check=False: FileNotFoundError → rc=127, TimeoutExpired → rc=124 (graceful, никогда не raise)
##   - check=True:  FileNotFoundError/TimeoutExpired/ненулевой rc → PlatformFatalError
##   - non_fatal=True: WARN-лог на ненулевой rc (видимость при graceful-режиме)
##   - fatal_rc (DevPlan 119 B4): реальный rc команды ∈ fatal_rc → PlatformFatalError ДАЖЕ при
##     check=False (семантика lifecycle exit=127 всегда fatal, TRAP[BUG] 2026-07-22).
##     НЕ применяется к синтетическим rc из FileNotFoundError (127) / TimeoutExpired (124) —
##     они возвращаются graceful при check=False (lifecycle non_fatal → rc=-1 семантика).
def run_subprocess(
    cmd: list[str],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    check: bool = False,
    non_fatal: bool = False,
    fatal_rc: tuple[int, ...] = (),
) -> subprocess.CompletedProcess:
    """Run a subprocess with the canonical graceful/raise semantics (DevPlan 118 C10, 119 B4).

    ▶ ┌cmd┐ → ○ subprocess.run(capture_output, text, timeout) → ◇ FileNotFound → ⎋ rc=127 | check? raise ┤
      → ◇ TimeoutExpired → ⎋ rc=124 | check? raise → ◇ rc!=0 → check? raise │ rc∈fatal_rc? raise │
      non_fatal? WARN │ ⎋ result
    """
    logger.info("[IMP:8][run_subprocess][exec] Running: %s (timeout=%ds)", " ".join(cmd), timeout)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        logger.warning("[IMP:7][run_subprocess][not-found] Binary not found: %s", cmd[0])
        if check:
            raise PlatformFatalError(f"Command not found: {cmd[0]}") from None
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][run_subprocess][timeout] Timed out after %ds: %s", timeout, " ".join(cmd))
        if check:
            raise PlatformFatalError(f"Command {' '.join(cmd)} timed out after {timeout}s") from None
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")

    if result.returncode != 0:
        if check or result.returncode in fatal_rc:
            # fatal_rc (B4): lifecycle-семантика exit=127 (command not found) всегда fatal —
            # даже при check=False/non_fatal (TRAP[BUG] 2026-07-22, lifecycle/helpers/subprocess_io)
            raise PlatformFatalError(
                f"Command {' '.join(cmd)} failed (exit={result.returncode}): {result.stderr.strip()}"
            )
        if non_fatal:
            logger.warning(
                "[IMP:7][run_subprocess][warn] Command returned %d: %s",
                result.returncode,
                result.stderr.strip()[:200],
            )
        else:
            logger.info(
                "[IMP:8][run_subprocess][rc] Command returned %d (graceful): %s",
                result.returncode,
                result.stderr.strip()[:120],
            )
    else:
        logger.info("[IMP:9][run_subprocess][ok] Command succeeded (exit=0): %s", " ".join(cmd))
    return result


# endregion FUNC_run_subprocess
