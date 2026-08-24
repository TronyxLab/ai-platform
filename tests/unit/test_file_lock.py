# GREP_SUMMARY: test-file-lock, TEST-32, flock, nested-acquire, reentrant-depth, eacces, fail-closed, degrade-no-lock, timeout-poll, locked-by-pid, mode-0664, unbalanced-release, REF-0011
# STRUCTURE: ▶ test_nested ┌acquire×2 → release×2 → raw flock OK┐ │ ▶ test_eacces_existing → FileLockError (fail-closed) │ ▶ test_degrade_missing → no-lock WARN │ ▶ test_timeout_poll → FileLockError + PID ≥ deadline │ ▶ test_unbalanced/double-release → no poison │ ▶ test_mode_0664
# region MODULE_CONTRACT
## @purpose  Unit-тесты канонического file_lock (TEST-32 карточки REF-0011, DevPlan 11 В1):
##           nested acquire/release (reentrant depth per-instance + общий holder), fail-closed
##           EACCES на СУЩЕСТВУЮЩЕМ lock-файле → FileLockError, деградация только для
##           отсутствующего файла/dev-кейса, timeout-poll до deadline («locked by PID»),
##           балансировка release (unbalanced/double — no-op без порчи реестра), права 0664.
## @scope    unit; tmp_path + PLATFORM_LOCK_DIR — никаких /var/lock на dev-машине.
##           EACCES симулируется monkeypatch os.open (детерминированно под любым uid).
## @invariants
##   - Native imports; tmp_path; LDD IMP:9 в каждом сценарии (anti-illusion)
##   - R1/R2: каждый тест содержит содержательные assert'ы на поведение лока
## @rationale  $TEST_SPEC REF-0011: базовый test_file_lock.py (nested acquire/release,
##            EACCES-existing→raise, timeout-poll) — TEST-32; file_lock ранее был БЕЗ тестов.
## @changes  2026-08-24 · Created (REF-0011, meta-refactoring В1)
# endregion MODULE_CONTRACT

import errno
import fcntl
import logging
import os
import stat
import time
from pathlib import Path

import pytest

import core.internal.shared.file_lock as file_lock_module
from core.internal.shared.file_lock import (
    _ACTIVE_HOLDERS,
    FileLock,
    FileLockError,
    platform_lock_path,
)
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _lock_path(tmp_path) -> str:
    """Канонический lock-путь в изолированном каталоге (никаких /var/lock на dev)."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir(exist_ok=True)
    return str(lock_dir / "platform-deploy-testproj.lock")


def _raw_holder(lock_path: str, holder_pid: int = 424242) -> int:
    """Raw-flock holder — имитация ДРУГОГО процесса (обход reentrant-реестра file_lock)."""
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.ftruncate(fd, 0)
    os.write(fd, str(holder_pid).encode())
    return fd


# 🧪 TRAP[TEST] · 2026-08-24 · Regression · TEST-32/REF-0011 — nested acquire/release
# · Scenario: два инстанса одного пути: вложенный acquire = reentrant ref++; inner release
# ·   НЕ снимает flock (outer ещё держит); после outer release raw-flock проходит.
# · Last fail: N/A (TEST-32 — file_lock был без тестов)
# · Remove if: reentrancy semantics change
@ldd_trajectory
def test_nested_acquire_release_reentrant(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """TEST-32: nested acquire/release — реентрантность через общий holder-реестр."""
    caplog.set_level(logging.DEBUG)
    path = _lock_path(tmp_path)

    outer = FileLock(path, timeout=0.0)
    outer.acquire()
    assert outer.held(), "первый acquire обязан держать лок"
    assert path in _ACTIVE_HOLDERS, "holder обязан появиться в process-wide реестре"

    inner = FileLock(path, timeout=0.0)
    inner.acquire()  # не дедлочит и не FileLockError (reentrant ref++)
    assert inner.held(), "вложенный инстанс считается держателем"

    inner.release()
    assert not inner.held(), "inner отпущен"
    assert outer.held(), "outer ОБЯЗАН продолжать держать flock после inner release"

    # Внешний свидетель: чужой raw-flock на тот же путь обязан быть отклонён (flock ещё держится)
    fd_probe = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd_probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd_probe)

    outer.release()
    assert not outer.held()
    assert path not in _ACTIVE_HOLDERS, "refs==0 → holder удалён из реестра"

    # После полного освобождения raw-flock проходит (замок реально снят ядром)
    fd_probe = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd_probe, fcntl.LOCK_EX | fcntl.LOCK_NB)  # не raise
        fcntl.flock(fd_probe, fcntl.LOCK_UN)
    finally:
        os.close(fd_probe)
    logger.critical("[IMP:9][test] nested acquire/release: reentrant refs + real unlock verified")


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION (R5 negative) · REF-0011/BUG-0104 — EACCES-existing fail-closed
# · Scenario: СУЩЕСТВУЮЩИЙ lock-файл + PermissionError на open (root-owned 0644 после
# ·   root-bootstrap — штатный сценарий) → FileLockError, а НЕ молчаливый no-lock.
# · Last fail: 2026-08-24 — degrade-to-no-lock возвращал успех без лока навсегда (BUG-0104≡DATA-302)
# · Remove if: lock-open failure policy change
@ldd_trajectory
def test_eacces_on_existing_lock_file_raises_fail_closed(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REF-0011: EACCES на существующем lock-файле → FileLockError (fail-closed, TEST-32)."""
    caplog.set_level(logging.DEBUG)
    path = _lock_path(tmp_path)
    Path(path).write_text("", encoding="utf-8")  # файл СУЩЕСТВУЕТ (создан root-bootstrap'ом)
    real_open = os.open

    def fake_open(file, flags, mode=0o644):
        if str(file) == path and flags & os.O_RDWR:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_open(file, flags, mode)

    monkeypatch.setattr(file_lock_module.os, "open", fake_open)
    lock = FileLock(path, timeout=0.0)
    with pytest.raises(FileLockError, match=r"not writable|fail-closed"):
        lock.acquire()
    assert not lock.held(), "после fail-closed отказа лок НЕ удерживается"
    logger.critical("[IMP:9][test] EACCES on EXISTING lock file → FileLockError (fail-closed, BUG-0104 closed)")


# 🧐 TRAP[DECISION] · 2026-08-24 · — · degrade остаётся только для отсутствующего файла/dev-кейса
# · Reason: карточка REF-0011 требует сохранить dev-деградацию (машина без прав на каталог),
# ·   но запретить её для существующего файла — тест ниже фиксирует границу двух режимов.
@ldd_trajectory
def test_eacces_on_missing_lock_file_degrades_no_lock(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REF-0011: деградация no-lock — ТОЛЬКО отсутствующий файл (dev-кейс), без raise."""
    caplog.set_level(logging.DEBUG)
    path = _lock_path(tmp_path)
    real_open = os.open

    def fake_open(file, flags, mode=0o644):
        if str(file) == path and flags & os.O_RDWR and not Path(path).exists():
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_open(file, flags, mode)

    monkeypatch.setattr(file_lock_module.os, "open", fake_open)
    lock = FileLock(path, timeout=0.0)
    lock.acquire()  # НЕ raise — деградация
    assert not lock.held(), "degraded no-lock: held()==False"
    lock.release()  # сбалансированный release в degraded-режиме — no-op
    logger.critical("[IMP:9][test] missing-file EACCES → degrade no-lock preserved (dev case)")


# 🧪 TRAP[TEST] · 2026-08-24 · Regression · TEST-32 — timeout-poll до deadline
# · Scenario: чужой raw-flock держит путь; FileLock(timeout>0) поллит и падает по deadline
# ·   с сообщением «locked by PID <holder>»; фактическое время ≥ заявленного таймаута.
# · Remove if: poll-loop semantics change
@ldd_trajectory
def test_timeout_poll_raises_with_holder_pid(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """TEST-32: timeout-poll — FileLockError с PID владельца после deadline."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("PLATFORM_LOCK_DIR", str(tmp_path / "locks"))
    (tmp_path / "locks").mkdir(exist_ok=True)
    path = platform_lock_path("testproj")
    fd = _raw_holder(path, holder_pid=7777)
    try:
        lock = FileLock(path, timeout=0.4, poll_interval=0.02)
        start = time.monotonic()
        with pytest.raises(FileLockError) as exc_info:
            lock.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.35, f"poll обязан длиться ~timeout: {elapsed:.3f}s"
        assert "7777" in str(exc_info.value), f"сообщение содержит PID владельца: {exc_info.value}"
        assert not lock.held()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    logger.critical("[IMP:9][test] timeout-poll raised with holder PID after deadline — OK (TEST-32)")


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · A-19 — unbalanced/double release не отравляет реестр
# · Scenario: double-release одного инстанса — no-op; новый инстанс после этого получает
# ·   РЕАЛЬНЫЙ flock (не reentrant-фантом против мёртвого holder'а).
# · Root (историч.): module-level depth переживал лишний release/disbalance и навсегда
# ·   отключал flock для последующих acquire того же пути.
# · Remove if: depth ownership changes
@ldd_trajectory
def test_unbalanced_and_double_release_do_not_poison_registry(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """A-19/REF-0011: балансировка release — per-instance depth, реестр без фантомов."""
    caplog.set_level(logging.DEBUG)
    path = _lock_path(tmp_path)

    lock = FileLock(path, timeout=0.0)
    lock.acquire()
    lock.release()
    lock.release()  # double-release — no-op + DEBUG, не raise
    assert not lock.held()
    assert path not in _ACTIVE_HOLDERS, "double-release не оставляет holder в реестре"

    # Release без acquire — no-op
    stray = FileLock(path, timeout=0.0)
    stray.release()
    assert not stray.held()

    # Новый инстанс обязан взять НАСТОЯЩИЙ flock (сырой конкурент — BlockingIOError)
    fresh = FileLock(path, timeout=0.0)
    fresh.acquire()
    fd_probe = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd_probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd_probe)
    fresh.release()
    logger.critical("[IMP:9][test] unbalanced/double release safe; fresh instance takes REAL flock")


# 🧪 TRAP[TEST] · 2026-08-24 · Regression · REF-0011 — контеншн другого процесса при timeout=0
# · Scenario: raw-flock holder (чужой процесс) → non-blocking acquire → немедленный
# ·   FileLockError «locked by PID» (терминальная ветка timeout<=0 — см. TRAP[BUG] в acquire).
# · Remove if: contention semantics change
@ldd_trajectory
def test_nonblocking_contention_immediate_file_lock_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REF-0011: занятый чужим процессом путь + timeout=0 → мгновенный FileLockError."""
    caplog.set_level(logging.DEBUG)
    path = _lock_path(tmp_path)
    fd = _raw_holder(path, holder_pid=99999)
    try:
        lock = FileLock(path, timeout=0.0)
        start = time.monotonic()
        with pytest.raises(FileLockError, match="locked by PID 99999"):
            lock.acquire()
        assert time.monotonic() - start < 1.0, "non-blocking контеншн — без poll-задержки"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    logger.critical("[IMP:9][test] non-blocking contention fails fast with holder PID")


# 🧪 TRAP[TEST] · 2026-08-24 · Regression · REF-0011 — права замка 0664 независимо от umask
# · Scenario: созданный lock-файл имеет mode 0664 (fchmod после open) — канал chown
# ·   ci-deploy:ci-deploy (history.py:188 pattern) предполагает групповую запись.
# · Remove if: lock mode policy change
def test_lock_file_mode_is_0664(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """REF-0011: lock-файл создаётся с mode 0664 (umask-независимо, fchmod)."""
    caplog.set_level(logging.INFO)
    path = _lock_path(tmp_path)
    lock = FileLock(path, timeout=0.0)
    lock.acquire()
    try:
        mode = stat.S_IMODE(Path(path).stat().st_mode)
        assert mode == 0o664, f"ожидался 0664, получен {oct(mode)} (umask не должен влиять)"
    finally:
        lock.release()
    logger.critical("[IMP:9][test] lock file mode is 0664 regardless of umask")
