#!/usr/bin/env python3
# GREP_SUMMARY: file-lock, flock, fcntl, lock, deploy-lock, state-lock, concurrency, non-blocking, pid-holder, stale-cleanup, reentrant, fail-closed, eacces, chown, ci-deploy, canonical
# STRUCTURE: ▶ FileLock(path, timeout, poll) → ○ acquire ┌mkdir parent → open O_RDWR|O_CREAT 0664 → ◇ EACCES-existing? → ⚡ FileLockError (fail-closed) │ degrade-no-lock (dev)┐ → flock LOCK_EX|LOCK_NB (timeout-loop) → registry holder refs++ → write PID┐ → ○ release ┌depth-- → refs==0? → flock LOCK_UN + close + del entry + UNLINK lock-file (F-025)┐ → ⎋ context-manager
# region MODULE_CONTRACT
## @purpose  Canonical fcntl.flock-based advisory file lock (DevPlan 136 W9 T9.1/T9.2/T9.10;
##           REF-0011 — fail-closed конкурентность деплоя).
##           Единая реализация для всех платформенных блокировок:
##             - deploy lock (per-project): /var/lock/platform-deploy-{project}.lock (не-blocking)
##             - state.json lock: state.json.lock (blocking с таймаутом — сериализация writers)
##             - DeployHistory snapshot prune (тот же deploy lock)
##           В отличие от lockfile-подхода (O_EXCL), flock — kernel-managed: замок
##           АВТОМАТИЧЕСКИ снимается при смерти процесса-владельца — stale-блокировки
##           невозможны по построению; «cleanup stale» сводится к зачистке PID-контента
##           мёртвого процесса (диагностика, не функциональность).
## @scope    Consumed by deploy/orchestrator.py, deploy/receive_flow.py (run-perimeter),
##           deploy/deploy_history.py, bootstrap/lifecycle/state_store.py (прямой импорт),
##           и любой другой модуль, которому нужна межпроцессная блокировка файла.
## @invariants
##   - flock LOCK_EX|LOCK_NB (non-blocking), таймаут реализуется poll-loop'ом
##   - timeout=0.0 → ровно одна non-blocking попытка; timeout>0 → poll до deadline
##   - Контеншн → FileLockError с PID владельца из файла («locked by PID X»)
##   - FAIL-CLOSED (REF-0011): PermissionError/EACCES на СУЩЕСТВУЮЩЕМ lock-файле →
##     FileLockError (root-owned lock после root-bootstrap = штатный сценарий; молчаливая
##     деградация в no-lock выключала T9.1 ровно в этом случае). Деградация до no-lock —
##     ТОЛЬКО для отсутствующего файла/недоступного каталога (dev-машина, не-node).
##   - Lock-файл создаётся 0664 + best-effort chown ci-deploy:ci-deploy (паттерн
##     audit/history.py:188) — root-bootstrap оставляет замок пригодным для ci-deploy receive.
##   - Reentrant в пределах процесса: реестр _ACTIVE_HOLDERS[key] хранит fd+refs (общие для
##     ВСЕХ инстансов пути); depth-счётчик — INSTANCE attr (REF-0011/A-19: module-level depth
##     переживал исключение без release и навсегда отключал flock для последующих инстансов);
##     балансировка acquire/release гарантируется try/finally на стороне вызывателя
##     (context manager __enter__/__exit__ — каноническая форма)
##   - PID владельца пишется в файл после acquire (диагностика; truncate при release)
##   - Каталог замка недоступен (PermissionError на mkdir — dev-машина, не-node) или файл
##     отсутствует и не создаётся → WARN + деградация до no-lock (НЕ raise)
##   - Ошибка flock/IO при release → WARN (не raise — release не должен маскировать бизнес-ошибку)
## @rationale Q: почему flock а не lockfile с PID? A: flock снимается ядром при смерти
##           процесса — исключает класс «завис процесс → вечный lock» (риск §9 meta);
##           lockfile+kill -0 требует ручного cleanup и имеет TOCTOU-окно. PID пишется
##           ТОЛЬКО для диагностики («locked by PID X») — не как механизм блокировки.
##           Q: почему fail-closed а не WARN? A: REF-0011 (BUG-0104≡BUG-0303): EACCES на
##           существующем root-owned lock — штатный root→ci-deploy сценарий; no-lock там =
##           тихое выключение защиты от конкурентного деплоя с зелёным CI. Лучше громкий
##           FAILED, чем mixed payload. Q: почему не в bootstrap/lifecycle/? A: deploy/ НЕ может
##           импортировать bootstrap/ (инвариант core/AGENTS.md: направление bootstrap → deploy
##           разрешено, обратное запрещено). Общий канон живёт в shared/.
## @changes  2026-08-05 · DevPlan 136 W9 T9.1/T9.2/T9.10 — создан (W9)
## @changes  2026-08-24 · REF-0011 (meta-refactoring В1) — fail-closed EACCES-existing;
##           locks 0664 + chown ci-deploy (history.py:188); _REENTRANT module-depth →
##           instance _depth + _ACTIVE_HOLDERS(fd,refs); unbalanced-release guard
## @changes  2026-08-26 · Plan 012 T5 (F-025) — release() удаляет lock-файл при refs==0
##           (cross-user самобой EACCES устранён); platform_lock_path — делегирование
##           в deploy_paths.deploy_lock_path (uid-канон, единственный SoT пути)
## @modulemap
##   FileLock [W:2] — reentrant flock context manager (timeout/poll/PID/stale-cleanup/fail-closed)
##   FileLockError [W:1] — контеншн/таймаут/EACCES-existing → «locked by PID X»
##   _pid_alive [W:1] — liveness-проверка PID (os.kill(pid, 0))
##   _ensure_ci_deploy_owner [W:1] — best-effort chown ci-deploy (0664-канон)
## @usecases
##   - DeployOrchestrator.deploy: не-блокирующий lock per project (T9.1, L-1/L-9)
##   - ReceiveFlow.run: периметр payload-tx под тем же локом (REF-0011 flock-before-copy)
##   - state_store.save_state: blocking lock state.json.lock (T9.2, L-2/B-2)
##   - DeployHistory.create_snapshot/prune: тот же deploy lock (T9.10, L-12)
# endregion MODULE_CONTRACT

from __future__ import annotations

import fcntl
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Канон прав замка (REF-0011): 0664 + владелец ci-deploy — root-bootstrap оставляет
#    замок открытым для ci-deploy receive (раньше root-owned 0644 давал EACCES → no-lock).
_LOCK_FILE_MODE = 0o664
_CI_DEPLOY_USER = "ci-deploy"


@dataclass
class _Holder:
    """Process-wide holder record: real flock fd + число активных инстансов-пользователей.

    ## @purpose — Общее состояние реентрантности одного lock-пути: fd держит ПЕРВЫЙ
    ##            инстанс-владелец; refs считает владельца + всех reentrant-пользователей.
    ##            fd живёт, пока refs > 0 (union времён жизни всех пользователей — LIFO
    ##            порядок гарантирован вложенностью вызовов).
    ## @io       ⇥ fd: int, refs: int → ⎋ _Holder
    ## @complexity O(1)
    """

    fd: int
    refs: int = 0


# Реестр активных локов процесса: abspath(lock_path) -> _Holder (fd + refs).
# flock не реентрантен через второй open (отдельное open file description → блокировка была бы
# отклонена даже в том же процессе). Реестр делает вложенный acquire того же пути безопасным:
# deploy() держит lock → create_snapshot → prune берёт тот же путь (reentrant ref++).
# REF-0011/A-19: depth — instance attr; здесь только ОДНА запись на реально удерживаемый путь,
# которая удаляется при refs==0 — исключение без release больше не «навсегда отключает» flock.
_ACTIVE_HOLDERS: dict[str, _Holder] = {}


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


# region FUNC_ensure_ci_deploy_owner
## @purpose  Best-effort chown ci-deploy:ci-deploy для lock-файла (REF-0011, паттерн
##           deploy/audit/history.py:188 B19): под root (bootstrap/converge) чинит владельца;
##           под ci-deploy/dev chown невозможен → INFO-skip (не ошибка). Пользователь
##           отсутствует (macOS dev, минимальные контейнеры) → no-op.
## @io       ⇥ path: Path → ⎋ None (best-effort, никогда не raise)
## @complexity O(1)
def ensure_ci_deploy_owner(path: Path) -> None:
    """Best-effort chown of the lock file to ci-deploy (non-fatal, history.py:188 pattern)."""
    try:
        import pwd

        pw = pwd.getpwnam(_CI_DEPLOY_USER)
    except (ImportError, KeyError):
        return  # нет пользователя/модуля (dev-машина, не-Linux) — канон no-op
    try:
        st = path.stat()
        if st.st_uid != pw.pw_uid:
            os.chown(path, pw.pw_uid, pw.pw_gid)
            logger.info("[IMP:7][FileLock][chown] chown %s -> %s:%s", path, _CI_DEPLOY_USER, _CI_DEPLOY_USER)
    except OSError as e:
        logger.info("[IMP:7][FileLock][chown] chown %s skipped (non-fatal): %s", path, e)


# endregion FUNC_ensure_ci_deploy_owner


# region CLASS_FileLockError
class FileLockError(Exception):
    """Raised when a file lock cannot be acquired within the timeout (or at all, non-blocking).

    ## @purpose — Distinguish lock contention from other I/O errors: caller sees
    ##            "locked by PID X" и решает (deploy → FAILED, state save → surface).
    ##            REF-0011: также fail-closed сигнал EACCES на существующем root-owned lock.
    ## @io — ⇥ message → ⎋ FileLockError instance
    ## @complexity O(1)
    """


# endregion CLASS_FileLockError


# region CLASS_FileLock
class FileLock:
    """Reentrant fcntl.flock-based advisory file lock (context manager).

    ## @purpose — Сериализация/предотвращение конкурентного доступа к файловым ресурсам
    ##            платформы (deploy per project, state.json writers, snapshot prune,
    ##            receive payload-tx периметр REF-0011).
    ## @io — ⇥ path: str | Path (lock file path), timeout: float (0.0 = single non-blocking
    ##              attempt; >0 = poll до deadline), poll_interval: float
    ##       ⎋ FileLock instance (acquire()/release()/__enter__/__exit__)
    ## @complexity O(1) per acquire attempt; O(timeout/poll) worst-case
    ## @invariants
    ##   - acquire() raises FileLockError на контеншн/таймаут («locked by PID X»)
    ##   - FAIL-CLOSED: EACCES на существующем lock-файле → FileLockError (REF-0011);
    ##     деградация no-lock — только отсутствие файла/каталога (dev-кейс)
    ##   - Reentrant: вложенный acquire того же пути в том же процессе = self._depth++
    ##     (общий _ACTIVE_HOLDERS fd; refs учитывает все инстансы пути)
    ##   - depth — INSTANCE attr; unbalanced release() → no-op + DEBUG (не портит чужой depth)
    ##   - PID владельца пишется в файл (диагностика); stale-PID зачищается при acquire
    ##   - release() никогда не raise (WARN на ошибку flock/close)
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
        # Reentrancy state — INSTANCE attrs (REF-0011/A-19): depth этого инстанса + ссылка
        # на общий holder (fd+refs). Module-level остаётся только фактический holder-реестр.
        self._depth: int = 0
        self._holder: _Holder | None = None
        self._degraded: bool = False
        self._fd: int | None = None
        # Реентрантный ключ — каноническая строка резолвнутого пути (ключ _ACTIVE_HOLDERS)
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
    ## @purpose  Open/create the lock file. FAIL-CLOSED (REF-0011): EACCES на СУЩЕСТВУЮЩЕМ
    ##           файле → FileLockError (root-owned lock после root-bootstrap — штатный
    ##           сценарий, no-lock там недопустим). Деградация до no-lock (WARN) — только
    ##           отсутствующий файл / недоступный каталог (dev-машина, не-node).
    ## @io       ⇥ None → ⎋ int | None (fd; None = degraded no-lock) ⚡ FileLockError
    ## @complexity O(1)
    def _open_fd(self) -> int | None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            logger.warning(
                "[IMP:7][FileLock][degrade] Cannot create lock dir %s (%s) — running WITHOUT lock "
                "(dev machine; node /var/lock is writable)",
                self.path.parent,
                e,
            )
            return None
        existed = self.path.exists()
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, _LOCK_FILE_MODE)
        except PermissionError as e:
            if existed:
                # 🧐 TRAP[DECISION] · 2026-08-24 · — · Fail-closed EACCES-existing (REF-0011)
                # · Rejected: сохранить degrade-to-no-lock для EACCES на существующем файле
                # · Reason: root-owned 0644 lock после root-bootstrap — ШТАТНЫЙ сценарий
                # ·   (φ8 создаёт /var/lock/platform-deploy-*.lock); silent no-lock выключал
                # ·   T9.1 ровно там, где она нужна (BUG-0104≡BUG-0303≡DATA-302). Громкий
                # ·   FAILED лучше mixed payload с зелёным CI. Root-процессы нормализуют
                # ·   права (0664 + chown ниже), так что fail-closed срабатывает только
                # ·   при реально неустранимой конфигурации.
                # · Rev: если появится легитимный read-only consumer замков — отдельный флаг.
                msg = (
                    f"{self.path} exists but is not writable by uid={os.geteuid()} ({e}) — "
                    "fail-closed (REF-0011); fix ownership: chown ci-deploy + chmod 0664"
                )
                raise FileLockError(msg) from None
            logger.warning(
                "[IMP:7][FileLock][degrade] Cannot create lock file %s (%s) — running WITHOUT lock (dev case)",
                self.path,
                e,
            )
            return None
        except OSError as e:
            logger.warning(
                "[IMP:7][FileLock][degrade] Cannot open lock file %s (%s) — running WITHOUT lock", self.path, e
            )
            return None
        # 0664 независимо от umask (групповая запись — канал chown ci-deploy)
        try:
            os.fchmod(fd, _LOCK_FILE_MODE)
        except OSError as e:
            logger.info("[IMP:7][FileLock][chmod] fchmod %s 0664 skipped (non-fatal): %s", self.path, e)
        ensure_ci_deploy_owner(self.path)
        return fd

    # endregion FUNC__open_fd

    # region FUNC_acquire
    ## @purpose  Acquire the lock: reentrant depth++ OR flock LOCK_EX|LOCK_NB with poll-loop.
    ##           On success registers process-wide holder and writes the owner PID.
    ##           Raises FileLockError on contention/timeout/EACCES-existing.
    ## @io       ⇥ None → ⎋ None ⚡ FileLockError
    ## @complexity O(timeout/poll_interval) worst-case; O(1) uncontended
    def acquire(self) -> None:
        holder = _ACTIVE_HOLDERS.get(self._key)
        if holder is not None:
            # Reentrant: тот же процесс уже держит flock этим путём — ref++ (без второго open).
            holder.refs += 1
            self._holder = holder
            self._depth += 1
            logger.debug(
                "[IMP:6][FileLock][acquire] Reentrant acquire (instance depth=%d, refs=%d) %s",
                self._depth,
                holder.refs,
                self.path,
            )
            return

        fd = self._open_fd()
        if fd is None:
            # Деградация: lock физически невозможен — контракт no-lock (WARN уже залогирован)
            self._degraded = True
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
                    holder_pid = self._read_holder_pid()
                    os.close(fd)
                    raise FileLockError(
                        f"{self.path} locked by PID {holder_pid}" if holder_pid else f"{self.path} is locked"
                    ) from None
                if deadline is not None and time.monotonic() >= deadline:
                    holder_pid = self._read_holder_pid()
                    os.close(fd)
                    raise FileLockError(
                        f"{self.path} locked by PID {holder_pid}" if holder_pid else f"{self.path} is locked"
                    ) from None
                time.sleep(self.poll_interval)

        new_holder = _Holder(fd=fd, refs=1)
        _ACTIVE_HOLDERS[self._key] = new_holder
        self._holder = new_holder
        self._depth = 1
        self._fd = fd
        # ── Диагностика: PID владельца + stale-cleanup (flock сам по себе kernel-managed) ──
        self._cleanup_stale()
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except OSError as e:
            logger.warning("[IMP:7][FileLock][acquire] Cannot write holder PID to %s (non-fatal): %s", self.path, e)
        logger.info(
            "[IMP:8][FileLock][acquire] Acquired %s (pid=%d, timeout=%ss, mode=%04o)",
            self.path,
            os.getpid(),
            self.timeout,
            _LOCK_FILE_MODE,
        )

    # endregion FUNC_acquire

    # region FUNC_release
    ## @purpose  Release the lock: instance depth-- / при refs==0 — flock LOCK_UN + close +
    ##           удаление holder-реестра. Never raises (release must not mask business errors).
    ## @io       ⇥ None → ⎋ None
    ## @complexity O(1)
    def release(self) -> None:
        if self._degraded:
            self._degraded = False
            logger.debug("[IMP:6][FileLock][release] Degraded no-lock released %s", self.path)
            return
        if self._depth <= 0:
            # Unbalanced release (двойной release/release без acquire) — no-op, НЕ трогаем
            # чужой holder (REF-0011: instance-depth исключает порчу общего счётчика).
            logger.debug("[IMP:6][FileLock][release] Unbalanced release ignored (%s)", self.path)
            return
        self._depth -= 1
        holder = self._holder if self._holder is not None else _ACTIVE_HOLDERS.get(self._key)
        if holder is None:  # защитная ветка: реестр пуст — считать released
            self._fd = None
            return
        holder.refs -= 1
        if holder.refs > 0:
            logger.debug(
                "[IMP:6][FileLock][release] Reentrant release (instance depth=%d, refs=%d) %s",
                self._depth,
                holder.refs,
                self.path,
            )
            return
        del _ACTIVE_HOLDERS[self._key]
        self._holder = None
        self._fd = None
        try:
            fcntl.flock(holder.fd, fcntl.LOCK_UN)
            os.ftruncate(holder.fd, 0)
        except OSError as e:
            logger.warning("[IMP:7][FileLock][release] flock/truncate error on %s (non-fatal): %s", self.path, e)
        try:
            os.close(holder.fd)
        except OSError as e:
            logger.warning("[IMP:7][FileLock][release] close error on %s (non-fatal): %s", self.path, e)
        # Plan 012 T5 (F-025): удаляем lock-ФАЙЛ при полном release (не только flock UN).
        # Инцидент F-025: оставленный файл с chown ci-deploy → следующий прогон другим
        # пользователем ловил EACCES-existing fail-closed (самобой). Удаление файла даёт
        # каждому acquire свежий файл с корректными правами (0664 + chown текущего юзера).
        # 🧐 TRAP[DECISION] · 2026-08-26 · — · Unlink lock-файла после release (F-025)
        # · Rejected: держать файл вечно (flock kernel-managed, stale невозможны) — отказ
        # ·   от unlink оставлял cross-user самобой (EACCES на чужом 0644/root-файле)
        # · Reason: mutex во время hold — всё равно inode-flock; unlink выполняется ПОСЛЕ
        # ·   LOCK_UN+close (последний держатель), окно гонки «новый acquirer открыл inode
        # ·   до unlink» — миллисекунды и закрыто сериализацией выше (CI/receive per project);
        # ·   best-effort: ENOENT/OSError → WARN без raise (release не маскирует бизнес-ошибки)
        # · Rev: если появится несериализованный конкурентный consumer замков — вернуть
        # ·   persistent-файл и решать cross-user через группу, не через удаление
        try:
            self.path.unlink(missing_ok=True)
            logger.info("[IMP:8][FileLock][release] Lock file removed: %s", self.path)
        except OSError as e:
            logger.warning("[IMP:7][FileLock][release] Cannot remove lock file %s (non-fatal): %s", self.path, e)
        logger.info("[IMP:8][FileLock][release] Released %s", self.path)

    # endregion FUNC_release

    # region FUNC_held
    ## @purpose  True if this instance currently holds the lock (owner fd or reentrant depth>0).
    ## @io       ⇥ None → ⎋ bool
    ## @complexity O(1)
    def held(self) -> bool:
        return self._depth > 0

    # endregion FUNC_held

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.release()
        return False


# endregion CLASS_FileLock


# region FUNC_platform_lock_path
## @purpose  Canonical per-project deploy lock path (SoT-делегирование в deploy_paths,
##           plan 012 T5/F-025: uid-канон через канонический реестр путей).
##           PLATFORM_LOCK_DIR env override → /var/lock/platform-deploy-{project}.lock
##           (канон DevPlan 136 T9.1 / deploy_history контракт).
##           На ноде /var/lock = /run/lock (1777) — пишется и root, и ci-deploy.
## @io       ⇥ project: str → ⎋ str (lock file path)
## @complexity O(1)
## @invariants
##   - project_name НЕ экранируется — путь строится через Path join (вне shell-команды);
##     валидация имени — ответственность validate_project_name (T9.7), не lock-пути
##   - PLATFORM_LOCK_DIR позволяет оператору/тестам переопределить каталог замков
##   - Единственный SoT пути — deploy_paths.deploy_lock_path (plan 012 T5)
def platform_lock_path(project: str) -> str:
    """Return the canonical per-project deploy lock file path."""
    from core.internal.shared.deploy_paths import deploy_lock_path

    return str(deploy_lock_path(project))


# endregion FUNC_platform_lock_path
