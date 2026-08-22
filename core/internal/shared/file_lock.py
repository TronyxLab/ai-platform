#!/usr/bin/env python3
# GREP_SUMMARY: file-lock, flock, fcntl, lock, deploy-lock, state-lock, concurrency, non-blocking, pid-holder, stale-cleanup, reentrant, canonical
# STRUCTURE: ▶ FileLock(path, timeout, poll) → ○ acquire ┌mkdir parent → open O_RDWR|O_CREAT → flock LOCK_EX|LOCK_NB (timeout-loop) → write holder PID┐ → ◇ contention? → ⊕ FileLockError("locked by PID X") → ○ release ┌flock LOCK_UN + close┐ → ⎋ context-manager
# region MODULE_CONTRACT
## @purpose  Canonical fcntl.flock-based advisory file lock (DevPlan 136 W9 T9.1/T9.2/T9.10).
##           Единая реализация для всех платформенных блокировок:
##             - deploy lock (per-project): /var/lock/platform-deploy-{project}.lock (не-blocking)
##             - state.json lock: state.json.lock (blocking с таймаутом — сериализация writers)
##             - DeployHistory snapshot prune (тот же deploy lock)
##           В отличие от lockfile-подхода (O_EXCL), flock — kernel-managed: замок
##           АВТОМАТИЧЕСКИ снимается при смерти процесса-владельца — stale-блокировки
##           невозможны по построению; «cleanup stale» сводится к зачистке PID-контента
##           мёртвого процесса (диагностика, не функциональность).
## @scope    Consumed by deploy/orchestrator.py, deploy/deploy_history.py,
##           bootstrap/lifecycle/state_store.py (прямой импорт),
##           и любой другой модуль, которому нужна межпроцессная блокировка файла.
## @invariants
##   - flock LOCK_EX|LOCK_NB (non-blocking), таймаут реализуется poll-loop'ом
##   - timeout=0.0 → ровно одна non-blocking попытка; timeout>0 → poll до deadline
##   - Контеншн → FileLockError с PID владельца из файла («locked by PID X»)
##   - Reentrant в пределах процесса (module-level registry) — вложенный acquire того же
##     пути в том же процессе НЕ дедлочит (flock семантика: второй open = отдельное
##     open file description → блокировка была бы отклонена — нужен depth-счётчик)
##   - PID владельца пишется в файл после acquire (диагностика; truncate при release)
##   - Каталог замка недоступен (PermissionError на mkdir — dev-машина, не-node) →
##     WARN + деградация до no-lock (НЕ raise): на ноде /var/lock существует (root/ci-deploy),
##     деградация срабатывает только там, где блокировка физически невозможна
##   - Ошибка flock/IO при release → WARN (не raise — release не должен маскировать бизнес-ошибку)
## @rationale Q: почему flock а не lockfile с PID? A: flock снимается ядром при смерти
##           процесса — исключает класс «завис процесс → вечный lock» (риск §9 meta);
##           lockfile+kill -0 требует ручного cleanup и имеет TOCTOU-окно. PID пишется
##           ТОЛЬКО для диагностики («locked by PID X») — не как механизм блокировки.
##           Q: почему не в bootstrap/lifecycle/? A: deploy/ НЕ может импортировать
##           bootstrap/ (инвариант core/AGENTS.md: направление bootstrap → deploy разрешено,
##           обратное запрещено). Общий канон живёт в shared/ (re-export lifecycle/lock.py
##           существовал до аудита 2026-08-22, удалён — потребители импортируют напрямую).
## @changes  2026-08-05 · DevPlan 136 W9 T9.1/T9.2/T9.10 — создан (W9)
## @modulemap
##   FileLock [W:2] — reentrant flock context manager (timeout/poll/PID/stale-cleanup)
##   FileLockError [W:1] — контеншн/таймаут → «locked by PID X»
##   _pid_alive [W:1] — liveness-проверка PID (os.kill(pid, 0))
## @usecases
##   - DeployOrchestrator.deploy: не-блокирующий lock per project (T9.1, L-1/L-9)
##   - state_store.save_state: blocking lock state.json.lock (T9.2, L-2/B-2)
##   - DeployHistory.create_snapshot/prune: тот же deploy lock (T9.10, L-12)
# endregion MODULE_CONTRACT

from __future__ import annotations

import fcntl
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Reentrancy registry: abspath(lock_path) -> nesting depth (same-process nested acquire).
# flock не реентрантен в рамках процесса (второй open = отдельное open file description →
# блокировка второго open была бы отклонена даже тем же процессом). Depth-счётчик делает
# вложенный acquire безопасным: deploy() держит lock → create_snapshot → prune берёт тот же.
_REENTRANT: dict[str, int] = {}


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is alive (or permission-restricted)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # жив, но принадлежит другому пользователю
    else:
        return True


# region CLASS_FileLockError
class FileLockError(Exception):
    """Raised when a file lock cannot be acquired within the timeout (or at all, non-blocking).

    ## @purpose — Distinguish lock contention from other I/O errors: caller sees
    ##            "locked by PID X" и решает (deploy → FAILED, state save → surface).
    ## @io — ⇥ message → ⎋ FileLockError instance
    ## @complexity O(1)
    """


# endregion CLASS_FileLockError


# region CLASS_FileLock
class FileLock:
    """Reentrant fcntl.flock-based advisory file lock (context manager).

    ## @purpose — Сериализация/предотвращение конкурентного доступа к файловым ресурсам
    ##            платформы (deploy per project, state.json writers, snapshot prune).
    ## @io — ⇥ path: str | Path (lock file path), timeout: float (0.0 = single non-blocking
    ##              attempt; >0 = poll до deadline), poll_interval: float
    ##       ⎋ FileLock instance (acquire()/release()/__enter__/__exit__)
    ## @complexity O(1) per acquire attempt; O(timeout/poll) worst-case
    ## @invariants
    ##   - acquire() raises FileLockError на контеншн/таймаут («locked by PID X»)
    ##   - Reentrant: вложенный acquire того же пути в том же процессе = depth+1 (no-op flock)
    ##   - PID владельца пишется в файл (диагностика); stale-PID зачищается при acquire
    ##   - release() никогда не raise (WARN на ошибку flock/close)
    ##   - Каталог замка недоступен → WARN + деградация (no-lock), не raise
    """

    def __init__(
        self,
        path: str | Path,
        *,
        timeout: float = 0.0,
        poll_interval: float = 0.05,
    ):
        self.path = Path(path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fd: int | None = None
        # Реентрантный ключ — каноническая строка резолвнутого пути (дикт-ключ _REENTRANT: dict[str, int])
        self._key = str(Path(self.path).resolve())

    # region FUNC__read_holder_pid
    ## @purpose  Read the holder PID written by the current lock owner (diagnostic only).
    ## @io       ⇥ None → ⎋ int | None
    ## @complexity O(1)
    def _read_holder_pid(self) -> int | None:
        try:
            content = self.path.read_text().strip()
        except OSError:
            return None
        if content.isdigit():
            pid = int(content)
            return pid if pid > 0 else None
        return None

    # endregion FUNC__read_holder_pid

    # region FUNC__cleanup_stale
    ## @purpose  Зачистить PID-контент файла, если владелец мёртв (косметика — flock
    ##           снимается ядром автоматически; «stale lock» в flock-семантике невозможен).
    ## @io       ⇥ None → ⎋ None
    ## @complexity O(1)
    def _cleanup_stale(self) -> None:
        pid = self._read_holder_pid()
        if pid is not None and not _pid_alive(pid):
            try:
                self.path.write_text("")
                logger.info(
                    "[IMP:7][FileLock][stale] Cleared stale holder PID %d from %s (process dead)",
                    pid,
                    self.path,
                )
            except OSError as e:
                logger.warning("[IMP:7][FileLock][stale] Cannot clear stale PID: %s", e)

    # endregion FUNC__cleanup_stale

    # region FUNC__open_fd
    ## @purpose  Open/create the lock file. Degrades to no-lock (WARN) if the parent dir
    ##           is not writable (dev machine, non-node) — node /var/lock always exists.
    ## @io       ⇥ None → ⎋ int | None (fd; None = degraded no-lock)
    ## @complexity O(1)
    def _open_fd(self) -> int | None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        except PermissionError as e:
            logger.warning(
                "[IMP:7][FileLock][degrade] Cannot create lock dir %s (%s) — running WITHOUT lock "
                "(dev machine; node /var/lock is writable)",
                self.path.parent,
                e,
            )
            return None
        except OSError as e:
            logger.warning(
                "[IMP:7][FileLock][degrade] Cannot open lock file %s (%s) — running WITHOUT lock", self.path, e
            )
            return None

    # endregion FUNC__open_fd

    # region FUNC_acquire
    ## @purpose  Acquire the lock: reentrant depth++ OR flock LOCK_EX|LOCK_NB with poll-loop.
    ##           On success writes the holder PID. Raises FileLockError on contention/timeout.
    ## @io       ⇥ None → ⎋ None ⚡ FileLockError
    ## @complexity O(timeout/poll_interval) worst-case; O(1) uncontended
    def acquire(self) -> None:
        if self._key in _REENTRANT:
            _REENTRANT[self._key] += 1
            logger.debug("[IMP:6][FileLock][acquire] Reentrant acquire (depth=%d) %s", _REENTRANT[self._key], self.path)
            return

        fd = self._open_fd()
        if fd is None:
            # Деградация: lock физически невозможен — контракт no-lock (WARN уже залогирован)
            self._fd = None
            return

        deadline = time.monotonic() + self.timeout if self.timeout > 0 else None
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if self.timeout <= 0:
                    # non-blocking (timeout=0.0): ровно ОДНА попытка — немедленный отказ.
                    # ⚠️ TRAP[BUG] · 2026-08-05 · P1 · timeout=0.0 уходил в БЕСКОНЕЧНЫЙ poll-loop
                    # · Symptom: deploy lock (timeout=0.0) при занятом lock вешал процесс
                    # ·   навсегда (T9.1 contention-тест поймал: pytest hang >180s).
                    # · Root: deadline вычислялся ТОЛЬКО при timeout>0; при 0.0 deadline=None →
                    # ·   ветка таймаута никогда не срабатывала → sleep(poll)+retry вечно.
                    # · Fix: явная ветка timeout<=0 → немедленный FileLockError.
                    # · Prevention: non-blocking режим обязан иметь терминальную ветку
                    # ·   (не зависеть от deadline, которого нет).
                    holder = self._read_holder_pid()
                    os.close(fd)
                    self._fd = None
                    raise FileLockError(
                        f"{self.path} locked by PID {holder}" if holder else f"{self.path} is locked"
                    ) from None
                if deadline is not None and time.monotonic() >= deadline:
                    holder = self._read_holder_pid()
                    os.close(fd)
                    self._fd = None
                    raise FileLockError(
                        f"{self.path} locked by PID {holder}" if holder else f"{self.path} is locked"
                    ) from None
                time.sleep(self.poll_interval)

        self._fd = fd
        _REENTRANT[self._key] = 1
        # ── Диагностика: PID владельца + stale-cleanup (flock сам по себе kernel-managed) ──
        self._cleanup_stale()
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except OSError as e:
            logger.warning("[IMP:7][FileLock][acquire] Cannot write holder PID to %s (non-fatal): %s", self.path, e)
        logger.info(
            "[IMP:8][FileLock][acquire] Acquired %s (pid=%d, timeout=%ss)", self.path, os.getpid(), self.timeout
        )

    # endregion FUNC_acquire

    # region FUNC_release
    ## @purpose  Release the lock: reentrant depth-- / flock LOCK_UN + close + PID truncate.
    ##           Never raises (release must not mask business errors).
    ## @io       ⇥ None → ⎋ None
    ## @complexity O(1)
    def release(self) -> None:
        if self._key in _REENTRANT:
            _REENTRANT[self._key] -= 1
            if _REENTRANT[self._key] > 0:
                logger.debug(
                    "[IMP:6][FileLock][release] Reentrant release (depth=%d) %s", _REENTRANT[self._key], self.path
                )
                return
            del _REENTRANT[self._key]
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.ftruncate(self._fd, 0)
            except OSError as e:
                logger.warning("[IMP:7][FileLock][release] flock/truncate error on %s (non-fatal): %s", self.path, e)
            try:
                os.close(self._fd)
            except OSError as e:
                logger.warning("[IMP:7][FileLock][release] close error on %s (non-fatal): %s", self.path, e)
            self._fd = None
        logger.info("[IMP:8][FileLock][release] Released %s", self.path)

    # endregion FUNC_release

    # region FUNC_held
    ## @purpose  True if this instance currently holds the lock (fd open or reentrant).
    ## @io       ⇥ None → ⎋ bool
    ## @complexity O(1)
    def held(self) -> bool:
        return self._fd is not None or self._key in _REENTRANT

    # endregion FUNC_held

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.release()
        return False


# endregion CLASS_FileLock


# region FUNC_platform_lock_path
## @purpose  Canonical per-project deploy lock path: PLATFORM_LOCK_DIR env override →
##           /var/lock/platform-deploy-{project}.lock (канон DevPlan 136 T9.1 / deploy_history
##           контракт). На ноде /var/lock = /run/lock (1777) — пишется и root, и ci-deploy.
## @io       ⇥ project: str → ⎋ str (lock file path)
## @complexity O(1)
## @invariants
##   - project_name НЕ экранируется — путь строится через os.path.join (вне shell-команды);
##     валидация имени — ответственность validate_project_name (T9.7), не lock-пути
##   - PLATFORM_LOCK_DIR позволяет оператору/тестам переопределить каталог замков
def platform_lock_path(project: str) -> str:
    """Return the canonical per-project deploy lock file path."""
    lock_dir = os.environ.get("PLATFORM_LOCK_DIR", "/var/lock")
    return str(Path(lock_dir) / f"platform-deploy-{project}.lock")


# endregion FUNC_platform_lock_path
