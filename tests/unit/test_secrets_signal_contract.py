# 🧪 TRAP[TEST] · REF-0013 · TEST-08 signal-handler contract
# GREP_SUMMARY: test-secrets-signal-contract, TEST-08, signal-handlers, main-registration, import-purity, SIGHUP, snapshot-list, stale-sweep, dev-shm
# STRUCTURE: ▶ subprocess-import → ◇ TERM/INT/HUP == SIG_DFL (нет import-time hijack) → ▶ register_cleanup_handlers() → ◇ handlers installed → ▶ _cleanup_temp_files + live-mutation → ⎋ snapshot semantics → ▶ sweep_stale_temp_keys(tmp) → ◇ stale wiped / fresh kept → ⎋
# region MODULE_CONTRACT
## @purpose  TEST-08: контракт signal/atexit-хендлеров decrypt_secrets.py после REF-0013:
##           (1) импорт модуля НЕ устанавливает хендлеры (module-level hijack устранён);
##           (2) регистрация происходит в main()/register_cleanup_handlers() и покрывает
##           SIGTERM/SIGINT/SIGHUP; (3) _cleanup_temp_files итерирует SNAPSHOT list(_TEMP_FILES)
##           (DEP-0026 — живой список при мутации во время итерации пропускал элементы);
##           (4) стартовый sweep удаляет только STALE temp-key'и (/dev/shm leftovers).
## @scope    Pure unit tests; import-purity проверяется изолированным subprocess.
## @invariants
##   - После `import decrypt_secrets` все сигнальные диспозиции остаются SIG_DFL
##   - register_cleanup_handlers() идемпотентен; ставит один handler на TERM/INT/HUP
##   - Мутация _TEMP_FILES во время cleanup не влияет на набор wipe'аемых путей
##   - Sweep трогает только файлы старше порога (живой параллельный decrypt не страдает)
## @rationale REF-0013/DEP-0025=A-20/HYP-03: side-effects на import перехватывали диспозицию
##            импортёра (тесты/CLI/сервисы получали чужие хендлеры); live-list итерация —
##            классическая гонка cleanup-путей.
# endregion MODULE_CONTRACT

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from core.internal.secrets import decrypt_secrets as decrypt_mod

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_PLATFORM_ROOT = str(Path(__file__).resolve().parent.parent.parent)


# ═══════════════════════════════════════════════════════════════════
# Tests: import purity (no module-level hijack)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_import_does_not_install_handlers
## @purpose  Импорт модуля в чистом интерпретаторе НЕ меняет диспозиции TERM/INT/HUP —
##           точный инвариант REF-0013 (прежний module-level signal.signal hijack'ал импортёра).
## @io       ⇥ None → ⎋ None (subprocess asserts SIG_DFL)
def test_import_does_not_install_handlers() -> None:
    """Importing decrypt_secrets leaves signal dispositions at SIG_DFL."""
    code = (
        "import signal\n"
        "from core.internal.secrets import decrypt_secrets\n"
        "for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):\n"
        "    h = signal.getsignal(sig)\n"
        "    assert h in (signal.SIG_DFL, signal.default_int_handler), f'{sig} hijacked at import: {h!r}'\n"
        "assert not decrypt_secrets._CLEANUP_REGISTERED[0], 'handlers registered at import'\n"
        "print('PURITY_OK')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = _PLATFORM_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=_PLATFORM_ROOT,
        env=env,
        check=False,
        timeout=60,
    )
    assert "PURITY_OK" in result.stdout, (
        f"Import-time signal hijack detected (REF-0013 regression)\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    logger.info("[IMP:9][test_import_does_not_install_handlers] ✅ Import keeps TERM/INT/HUP at SIG_DFL")


# endregion FUNC_test_import_does_not_install_handlers


# ═══════════════════════════════════════════════════════════════════
# Tests: registration in main() + coverage of TERM/INT/HUP
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def restore_signal_handlers() -> Any:
    """Save and restore TERM/INT/HUP dispositions around handler-installation tests."""
    sigs = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGHUP"):
        sigs.append(signal.SIGHUP)
    saved = {sig: signal.getsignal(sig) for sig in sigs}
    yield saved
    for sig, handler in saved.items():
        signal.signal(sig, handler)


# region FUNC_test_register_installs_handlers_including_sighup
## @purpose  register_cleanup_handlers() (вызывается из main()) ставит handler на
##           SIGTERM/SIGINT/SIGHUP — SIGHUP добавлен REF-0013 (разрыв SSH не должен
##           оставлять ключ на tmpfs).
## @io       ⇥ restore_signal_handlers → ⎋ None (asserts)
def test_register_installs_handlers_including_sighup(restore_signal_handlers: Any) -> None:
    """register_cleanup_handlers installs one handler per TERM/INT/HUP, idempotently."""
    decrypt_mod._CLEANUP_REGISTERED[0] = False  # reset для детерминизма
    try:
        decrypt_mod.register_cleanup_handlers()
        assert signal.getsignal(signal.SIGTERM) is decrypt_mod._signal_handler
        assert signal.getsignal(signal.SIGINT) is decrypt_mod._signal_handler
        if hasattr(signal, "SIGHUP"):
            assert signal.getsignal(signal.SIGHUP) is decrypt_mod._signal_handler
        # Idempotency: повторная регистрация не меняет диспозицию
        decrypt_mod.register_cleanup_handlers()
        assert signal.getsignal(signal.SIGTERM) is decrypt_mod._signal_handler
        logger.info("[IMP:9][test_register_installs_handlers_including_sighup] PASS: TERM/INT/HUP registered")
    finally:
        decrypt_mod._CLEANUP_REGISTERED[0] = True  # модуль уже мог зарегистрироваться ранее в процессе


# endregion FUNC_test_register_installs_handlers_including_sighup


# region FUNC_test_main_registers_handlers
## @purpose  Контракт TEST-08 «хендлеры зарегистрированы в main()»: запуск main() с
##           несуществующим enc-path (rc=1) завершается установленным handler'ом SIGTERM.
## @io       ⇥ monkeypatch (sys.argv), restore_signal_handlers → ⎋ None (asserts)
def test_main_registers_handlers(monkeypatch: pytest.MonkeyPatch, restore_signal_handlers: Any) -> None:
    """main() registers cleanup handlers before doing any decryption work."""
    decrypt_mod._CLEANUP_REGISTERED[0] = False
    try:
        monkeypatch.setattr(sys, "argv", ["decrypt_secrets.py", "/nonexistent/ref0013.enc.yaml"])
        rc = decrypt_mod.main()
        assert rc == 1, f"Missing enc-file must exit 1, got {rc}"
        assert signal.getsignal(signal.SIGTERM) is decrypt_mod._signal_handler, (
            "main() did not register SIGTERM cleanup handler (TEST-08 contract)"
        )
        logger.info("[IMP:9][test_main_registers_handlers] PASS: main() installs handlers")
    finally:
        decrypt_mod._CLEANUP_REGISTERED[0] = True


# endregion FUNC_test_main_registers_handlers


# ═══════════════════════════════════════════════════════════════════
# Tests: snapshot iteration (DEP-0026)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_cleanup_iterates_snapshot
## @purpose  _cleanup_temp_files итерирует list(_TEMP_FILES) SNAPSHOT: мутация списка
##           прямо во время wipe-цикла (симуляция DEP-0026) не расширяет набор обрабатываемых
##           путей — добавленный «на лету» путь p3 не wipe'ается этим проходом.
## @io       ⇥ monkeypatch (_TEMP_FILES/_wipe_temp_key) → ⎋ None (asserts)
def test_cleanup_iterates_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup wipes exactly the snapshot taken at loop start; live mutations are ignored."""
    tracked = ["/tmp/ref0013-p1.key", "/tmp/ref0013-p2.key"]  # nosec B108 — тестовые фейк-пути, файлов нет
    wiped: list[str] = []

    def _fake_wipe(path: str) -> None:
        wiped.append(path)
        # Симуляция гонки: wipe-путь мутирует живой список посреди итерации
        tracked.append("/tmp/ref0013-p3-midwipe.key")

    monkeypatch.setattr(decrypt_mod, "_TEMP_FILES", tracked)
    monkeypatch.setattr(decrypt_mod, "_wipe_temp_key", _fake_wipe)

    decrypt_mod._cleanup_temp_files()

    assert wiped == ["/tmp/ref0013-p1.key", "/tmp/ref0013-p2.key"], (
        f"Snapshot semantics violated — expected only initial 2 paths, wiped: {wiped}"
    )
    # clear() очищает живой список целиком (контракт функции) — включая mid-wipe append
    assert tracked == [], "clear() must empty the live list after cleanup"
    logger.info("[IMP:9][test_cleanup_iterates_snapshot] PASS: snapshot iteration, live mutation isolated")


# endregion FUNC_test_cleanup_iterates_snapshot


# ═══════════════════════════════════════════════════════════════════
# Tests: starter sweep /dev/shm leftovers
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_sweep_removes_only_stale_keys
## @purpose  sweep_stale_temp_keys удаляет ТОЛЬКО platform-age-key-* старше порога;
##           свежие файлы (живой параллельный процесс) и чужие файлы не трогаются.
## @io       ⇥ tmp_path (DI tmp_dir) → ⎋ None (asserts)
def test_sweep_removes_only_stale_keys(tmp_path: Path) -> None:
    """Sweep wipes stale platform-age-key leftovers, keeps fresh and foreign files."""
    old_ts = time.time() - (decrypt_mod._STALE_TEMP_KEY_MAX_AGE_S + 600)
    stale = tmp_path / "platform-age-key-crashed.key"
    stale.write_text("stale-key", encoding="utf-8")
    os.utime(stale, (old_ts, old_ts))

    fresh = tmp_path / "platform-age-key-live.key"
    fresh.write_text("live-key", encoding="utf-8")  # свежий mtime — не трогаем

    foreign = tmp_path / "unrelated-user-file.txt"
    foreign.write_text("keep me", encoding="utf-8")
    os.utime(foreign, (old_ts, old_ts))  # старый, но не наш префикс

    swept = decrypt_mod.sweep_stale_temp_keys(str(tmp_path))

    assert swept == 1, f"Expected exactly 1 swept file, got {swept}"
    assert not stale.exists(), "Stale temp key must be wiped by startup sweep"
    assert fresh.exists(), "Fresh temp key (parallel process) must NOT be touched"
    assert foreign.exists(), "Foreign files without platform-age-key prefix must NOT be touched"
    logger.info("[IMP:9][test_sweep_removes_only_stale_keys] PASS: stale wiped=1, fresh+foreign kept")


# endregion FUNC_test_sweep_removes_only_stale_keys
