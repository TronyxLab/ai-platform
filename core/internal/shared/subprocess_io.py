#!/usr/bin/env python3
# GREP_SUMMARY: subprocess-io, run-subprocess, canonical, check, non-fatal, timeout, rc-127, rc-124, graceful, command-runner, CommandRunner, DI, runner-param
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
## @changes  2026-08-13 | DevPlan 160 W4b — +CommandRunner Protocol / SubprocessCommandRunner /
##            default_command_runner (DI-шов для users.py и тестов; поведение без изменений)
## @changes 2026-08-17 | DevPlan 006 W1 — +StreamingResult / run_subprocess_streaming: Popen
##            +start_new_session + reader-потоки (tee в stderr) + heartbeat + killpg при таймауте;
##            сигнатура run_subprocess НЕ меняется (инвариант — расширение, не ломка)
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import logging
import subprocess
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from core.internal.shared.exceptions import PlatformFatalError

# W1-A1 (план 170): DEFAULT_TIMEOUT=30 (дубль SoT) → SSH_CONNECT_TIMEOUT (30) — каноническое
# 30s окно subprocess-команд (совпадает с C10-каноном converge DOCKER_TIMEOUT=30).
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT

logger = logging.getLogger(__name__)

# ── Default timeout: канон converge DOCKER_TIMEOUT=30 (DevPlan 118 C10) ──
DEFAULT_TIMEOUT: int = SSH_CONNECT_TIMEOUT
"""## @invariant Default timeout (sec) — совпадает с converge/infra.DOCKER_TIMEOUT=30 (C10)."""


# region PROTOCOL_CommandRunner
@runtime_checkable
class CommandRunner(Protocol):
    """DI-протокол исполнения команд (DevPlan 160 W4b T4.2) — run() с канонной семантикой.

    ## @purpose — Абстракция subprocess-канала для модулей, использующих run_subprocess:
    ##            тесты передают fake-раннер с ассертами вместо monkeypatch subprocess.run/
    ##            run_subprocess. Методы совпадают с фактическим API run_subprocess
    ##            (run/read — модуль использует только run_subprocess; «read» в W4b —
    ##            результат CompletedProcess со stdout/stderr, НЕ отдельный метод).
    ## @io — ⇥ run(cmd, *, timeout, check, non_fatal, fatal_rc) → subprocess.CompletedProcess
    ##       ⚡ PlatformFatalError (check=True / rc ∈ fatal_rc)
    ## @complexity — O(M) — время выполнения команды
    ## @invariants
    ##   - run() сохраняет канонную семантику run_subprocess (graceful | raise, 127/124)
    ##   - CompletedProcess возвращается ВСЕГДА при check=False (никогда не raise)
    """

    def run(
        self,
        cmd: list[str],
        *,
        timeout: int
        | None = DEFAULT_TIMEOUT,  # None = без таймаута (subprocess.run(timeout=None) канон install_tor_proxy)
        check: bool = False,
        non_fatal: bool = False,
        fatal_rc: tuple[int, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Run command with canonical graceful/raise semantics (run_subprocess)."""
        ...


# endregion PROTOCOL_CommandRunner


# region CLASS_SubprocessCommandRunner
class SubprocessCommandRunner:
    """Реальная реализация CommandRunner — делегирует run_subprocess (канон C10/B4).

    ## @purpose — Ленивый default для runner-параметров: сохраняет ровно текущее
    ##            поведение (канон run_subprocess: graceful check=False | raise check=True).
    ## @io — ⇥ cmd + kwargs → ⎋ subprocess.CompletedProcess ⚡ PlatformFatalError
    ## @complexity — O(M) — делегирование
    ## @invariants
    ##   - Делегирует run_subprocess без изменения семантики (0 дублей логики)
    ##   - Объект без состояния — безопасен для переиспользования
    """

    @staticmethod
    def run(
        cmd: list[str],
        *,
        timeout: int | None = DEFAULT_TIMEOUT,  # None = без таймаута (subprocess.run(timeout=None) канон)
        check: bool = False,
        non_fatal: bool = False,
        fatal_rc: tuple[int, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        return run_subprocess(cmd, timeout=timeout, check=check, non_fatal=non_fatal, fatal_rc=fatal_rc)


# endregion CLASS_SubprocessCommandRunner


# region FUNC_default_command_runner
def default_command_runner() -> CommandRunner:
    """Фабрика реального CommandRunner (ленивый default для runner-параметров).

    ▶ ┌None┐ → ⊕ SubprocessCommandRunner() → ⎋ CommandRunner

    ## @purpose — Единая точка создания default-раннера: `runner = runner if runner is not None else default_command_runner()`.
    ## @io — ⇥ None → ⎋ CommandRunner (SubprocessCommandRunner)
    ## @complexity — O(1)
    ## @invariants
    ##   - Без кэширования/синглтона — каждый вызов новый лёгкий объект
    """
    return SubprocessCommandRunner()


# endregion FUNC_default_command_runner


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
    timeout: int | None = DEFAULT_TIMEOUT,  # None = без таймаута (subprocess.run timeout=None)
    check: bool = False,
    non_fatal: bool = False,
    fatal_rc: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with the canonical graceful/raise semantics (DevPlan 118 C10, 119 B4).

    ▶ ┌cmd┐ → ○ subprocess.run(capture_output, text, timeout) → ◇ FileNotFound → ⎋ rc=127 | check? raise ┤
      → ◇ TimeoutExpired → ⎋ rc=124 | check? raise → ◇ rc!=0 → check? raise │ rc∈fatal_rc? raise │
      non_fatal? WARN │ ⎋ result
    """
    logger.info("[IMP:8][run_subprocess][exec] Running: %s (timeout=%ss)", " ".join(cmd), timeout)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        logger.warning("[IMP:7][run_subprocess][not-found] Binary not found: %s", cmd[0])
        if check:
            msg = f"Command not found: {cmd[0]}"
            raise PlatformFatalError(msg) from None
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][run_subprocess][timeout] Timed out after %ds: %s", timeout, " ".join(cmd))
        if check:
            msg = f"Command {' '.join(cmd)} timed out after {timeout}s"
            raise PlatformFatalError(msg) from None
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")

    if result.returncode != 0:
        if check or result.returncode in fatal_rc:
            # fatal_rc (B4): lifecycle-семантика exit=127 (command not found) всегда fatal —
            # даже при check=False/non_fatal (TRAP[BUG] 2026-07-22, lifecycle/helpers/subprocess_io)
            msg = f"Command {' '.join(cmd)} failed (exit={result.returncode}): {result.stderr.strip()}"
            raise PlatformFatalError(msg)
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


# region CLASS_StreamingResult
@dataclass
class StreamingResult:
    """Результат run_subprocess_streaming (DevPlan 006 W1).

    ## @purpose — Совместим по атрибутам с потребностями CheckOutcome и CompletedProcess-
    ##            контрактом потребителей: .stdout/.stderr/.returncode доступны всегда.
    ## @io — поля данных, без side-effects
    ## @invariants
    ##   - stdout/stderr — ПОЛНЫЙ накопленный вывод (даже после таймаут-килла — partial)
    ##   - timed_out=True ⇔ rc=124 (синтетический); FileNotFoundError → rc=127
    ##   - pid — PID запущенного процесса (0 при FileNotFoundError); нужен reaper'у орфанов
    ##     (DevPlan 015 F-06)
    """

    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int = 0
    timed_out: bool = False
    pid: int = 0


# endregion CLASS_StreamingResult


# region FUNC_reap_process_tree
## @purpose  F-06 (DevPlan 015): psutil-рекурсивный process-tree kill — добивает воркеров,
##           УШЕДШИХ из process-group (killpg их не достаёт). Прецедент: утёкший basedpyright-орфан
##           (209 мин CPU) при timeout pyright-шага check-suite — node-воркеры basedpyright могут
##           создать новую сессию/группу → killpg не видит их → орфан живёт бесконечно.
## @io       ⇥ pid: int, include_root: bool = False → ⎋ int (число добитых процессов)
## @complexity — O(1) + psutil-обход потомков (рекурсивный)
## @invariants
##   - Best-effort: psutil отсутствует → 0 (killpg остаётся единственным механизмом)
##   - NoSuchProcess/AccessDenied → 0 (процесс уже мёртв/чужая сессия)
##   - include_root=True — убивает и сам pid (нужно post-hoc reaper'у: killpg мог не дойти)
##   - Логирует WARN при ненулевом результате (видимость орфанов, конституция §4)
def reap_process_tree(pid: int, *, include_root: bool = False) -> int:
    """Recursively kill the process tree of pid (F-06: workers escaped the process-group)."""
    try:
        import psutil  # runtime-зависимость (lazy, как runner.memory_available_bytes)
    except ImportError:
        logger.info("[IMP:7][reap_process_tree][skip] psutil not available — killpg-only reaping")
        return 0
    try:
        root_proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # type: ignore[attr-defined] — psutil без pyi-стабов
        return 0
    targets: list[object] = []
    try:
        targets = list(root_proc.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # type: ignore[attr-defined] — psutil без pyi-стабов
        targets = []
    if include_root:
        targets = [root_proc, *targets]
    killed = sum(1 for proc in targets if _kill_proc(proc))
    if killed:
        logger.warning(
            "[IMP:7][reap_process_tree] Killed %d orphaned process(es) of pid=%d (escaped process-group, F-06)",
            killed,
            pid,
        )
    return killed


# endregion FUNC_reap_process_tree


# region FUNC__kill_proc
## @purpose  F-06 helper: kill одного процесса psutil'ом, NoSuchProcess/AccessDenied → False.
##           Вынесен из reap_process_tree (ruff B909 try-except-in-loop — производительность).
## @io       ⇥ proc: psutil.Process (as object — psutil без pyi-стабов) → ⎋ bool (убит)
## @complexity — O(1)
def _kill_proc(proc: object) -> bool:
    """Kill a single psutil process; already-dead/denied → False (non-fatal)."""
    try:
        import psutil  # runtime-зависимость (lazy — как runner.memory_available_bytes)
    except ImportError:
        return False
    try:
        cast("psutil.Process", proc).kill()  # type: ignore[attr-defined] — psutil без pyi-стабов
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # type: ignore[attr-defined] — psutil без pyi-стабов
        return False
    else:
        return True


# endregion FUNC__kill_proc


# region FUNC_run_subprocess_streaming
## @purpose  Streaming-канон subprocess (DevPlan 006 §1.1): построчный tee вывода в stderr,
##           heartbeat, killpg при таймауте — НИКОГДА не raise при check=False.
## @io       ⇥ cmd + timeout/env/cwd/stream/heartbeat/check/non_fatal/fatal_rc
##           ⎋ StreamingResult (stdout/stderr/returncode/duration_ms/timed_out)
##           ⚡ PlatformFatalError (check=True / реальный rc ∈ fatal_rc)
## @complexity O(M) where M = command execution time
## @invariants
##   - Popen(stdout=PIPE, stderr=PIPE, text=True, start_new_session=True); child env всегда
##     содержит PYTHONUNBUFFERED=1 (дети не буферизуют Python-вывод)
##   - 2 reader-потока: при stream=True — tee каждой строки в sys.stderr с префиксом [child];
##     всегда накапливают полный вывод в буфере (для отчёта/asserts)
##   - heartbeat>0: каждые heartbeat секунд в sys.stderr «[stream][heartbeat] elapsed=Xs pid=Y»
##   - Таймаут → os.killpg(SIGKILL) всей группы → drain reader-потоков → StreamingResult(rc=124,
##     timed_out=True, partial stdout/stderr) — graceful, никогда не raise (R2/R6: без орфанов)
##   - FileNotFoundError → StreamingResult(rc=127) graceful при check=False
##   - stdout вызывающего процесса остаётся чистым: стрим и heartbeat — ТОЛЬКО в sys.stderr
def run_subprocess_streaming(
    cmd: list[str],
    *,
    timeout: int | None = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    stream: bool = True,
    heartbeat: int = 30,
    check: bool = False,
    non_fatal: bool = False,
    fatal_rc: tuple[int, ...] = (),
) -> StreamingResult:
    """Run a subprocess streaming output line-by-line to stderr (DevPlan 006 W1).

    ▶ ┌cmd┐ → ○ Popen(new_session) → ⊕ reader-потоки (tee в stderr) + heartbeat-поток →
      ◇ таймаут? killpg+drain → ⎋ rc=124 timed_out → ◇ не найден? ⎋ rc=127 →
      ◇ rc!=0 → check/fatal_rc? raise │ non_fatal? WARN │ ⎋ StreamingResult
    """
    import os
    import signal
    import sys
    import threading
    import time

    logger.info(
        "[IMP:8][run_subprocess_streaming][exec] Running: %s (timeout=%ss, stream=%s)",
        " ".join(cmd),
        timeout,
        stream,
    )
    child_env = dict(os.environ if env is None else {**os.environ, **env})
    child_env["PYTHONUNBUFFERED"] = "1"

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _emit(line: str) -> None:
        if stream and line:
            sys.stderr.write(f"[child] {line}\n")
            sys.stderr.flush()

    def _reader(pipe, sink: list[str]) -> None:
        try:
            for raw in pipe:
                line = raw.rstrip("\n")
                sink.append(line)
                _emit(line)
        finally:
            with contextlib.suppress(OSError):
                pipe.close()

    def _heartbeat_loop(pid: int, stop: threading.Event) -> None:
        started = time.monotonic()
        while not stop.wait(heartbeat):
            sys.stderr.write(f"[IMP:8][stream][heartbeat] elapsed={int(time.monotonic() - started)}s pid={pid}\n")
            sys.stderr.flush()

    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=child_env,
            cwd=cwd,
        )
    except FileNotFoundError:
        logger.warning("[IMP:7][run_subprocess_streaming][not-found] Binary not found: %s", cmd[0])
        if check:
            msg = f"Command not found: {cmd[0]}"
            raise PlatformFatalError(msg) from None
        return StreamingResult(cmd, 127, "", f"{cmd[0]}: not found")

    hb_thread: threading.Thread | None = None
    hb_stop = threading.Event()
    if heartbeat and heartbeat > 0:
        hb_thread = threading.Thread(
            target=_heartbeat_loop, args=(proc.pid, hb_stop), daemon=True, name="stream-heartbeat"
        )
        hb_thread.start()

    t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_lines), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, stderr_lines), daemon=True)
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        logger.warning(
            "[IMP:7][run_subprocess_streaming][timeout] Timed out after %ss: %s — killpg",
            timeout,
            " ".join(cmd),
        )
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()  # группа уже умерла или недоступна — добить напрямую
        # F-06 (DevPlan 015): killpg НЕ достаёт воркеров, ушедших из process-group (setsid/
        # новая сессия — basedpyright/node). Process-tree reaper добивает их ПОКА родитель жив
        # (children(recursive=True) валиден только до proc.wait()).
        reap_process_tree(proc.pid)
        proc.wait()

    hb_stop.set()
    # R2: drain reader-потоков после килла — pipe закрыт, потоки завершаются сами
    t_out.join(timeout=10)
    t_err.join(timeout=10)

    duration_ms = int((time.monotonic() - start) * 1000)
    stdout_text = "\n".join(stdout_lines)
    stderr_text = "\n".join(stderr_lines)
    returncode = 124 if timed_out else proc.returncode

    result = StreamingResult(
        cmd,
        returncode,
        stdout_text,
        stderr_text,
        duration_ms=duration_ms,
        timed_out=timed_out,
        pid=proc.pid,  # F-06: pid для post-hoc reaper (runner.run_cmd при timeout)
    )

    if timed_out:
        return result  # graceful: rc=124 + partial вывод, никогда не raise при check=False

    if result.returncode != 0:
        if check or result.returncode in fatal_rc:
            msg = f"Command {' '.join(cmd)} failed (exit={result.returncode}): {stderr_text.strip()[:500]}"
            raise PlatformFatalError(msg)
        if non_fatal:
            logger.warning(
                "[IMP:7][run_subprocess_streaming][warn] Command returned %d: %s",
                result.returncode,
                stderr_text.strip()[:200],
            )
        else:
            logger.info(
                "[IMP:8][run_subprocess_streaming][rc] Command returned %d (graceful): %s",
                result.returncode,
                stderr_text.strip()[:120],
            )
    else:
        logger.info("[IMP:9][run_subprocess_streaming][ok] Command succeeded (exit=0): %s", " ".join(cmd))
    return result


# endregion FUNC_run_subprocess_streaming
