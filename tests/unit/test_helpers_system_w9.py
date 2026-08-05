#!/usr/bin/env python3
# GREP_SUMMARY: test-helpers-system-w9, T9.12, ensure-sops, shutil-which, no-redownload, T9.13, journald, active-line, commented, idempotent
# STRUCTURE: ▶ test_*_sops_no_redownload ┌shutil.which → path┐ → ensure_sops → 0 subprocess (нет re-download) │ ▶ test_*_journald_commented ┌#Storage=persistent┐ → active-check False → append активной строки │ ▶ test_*_journald_active → no-op (без записи)
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.12 (B-4) и T9.13 (B-8) DevPlan 136 W9: helpers/system.py —
##           ensure_sops через shutil.which (повторный φ1 БЕЗ re-download) и journald
##           idempotency-гейт по активной строке (комментарий `#Storage=persistent` НЕ маскирует).
## @scope    unit-тесты: monkeypatch shutil.which/run_subprocess/atomic_write; tmp_path.
## @invariants
##   - Native imports; tmp_path; LDD IMP:9 в успешных сценариях
##   - sops в PATH → 0 скачиваний (R5-negative: command -v через exec ВСЕГДА падал → re-download)
##   - Комментированная Storage=строка не считается настроенной (R5-negative на вход B-8)
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: T9.12/T9.13 — тест на повторный φ1 без
##            re-download; тест на `#Storage=persistent` commented.
## @changes  2026-08-05 · Created (DevPlan 136 W9)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.helpers import system as sys_helpers
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.12/B-4 — sops в PATH → НЕТ re-download
# · Scenario: shutil.which("sops") → найден → ensure_sops не запускает ни одного subprocess
# · Last fail: 2026-08-05 — subprocess.run(["command", "-v", "sops"]) (bash-builtin через exec)
# ·   ВСЕГДА FileNotFoundError → sops перекачивался на КАЖДОМ φ1 (B-4)
# · Remove if: sops detection semantics change
@ldd_trajectory
def test_ensure_sops_no_redownload_when_installed(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.12: shutil.which находит sops → 0 subprocess-вызовов (нет re-download)."""
    caplog.set_level(logging.INFO)
    calls: list = []

    # ensure_sops делает локальный import shutil → патчим глобальный shutil.which
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/sops")
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.helpers.system.run_subprocess",
        lambda *a, **k: calls.append(a),
    )

    sys_helpers.ensure_sops()
    assert not calls, f"повторный φ1 не должен качать sops (B-4): {calls}"
    assert "Already installed" in caplog.text
    logger.critical("[IMP:9][test] ensure_sops no re-download when installed — OK (T9.12)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.13/B-8 — комментарий НЕ активная строка
# · Scenario: "#Storage=persistent" (commented) → _journald_persistent_active False;
# ·   _set_storage_persistent добавляет АКТИВНУЮ Storage=persistent (комментарий сохраняется)
# · Last fail: 2026-08-05 — substring "Storage=persistent" in content матчил комментарий → false no-op
# · Remove if: journald idempotency semantics change
@ldd_trajectory
def test_journald_commented_line_not_active(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T9.13: активная-строка проверка не путает комментарий с конфигурацией."""
    caplog.set_level(logging.INFO)
    assert sys_helpers._journald_persistent_active("#Storage=persistent\n") is False, (
        "комментированная строка не активна (B-8)"
    )
    assert sys_helpers._journald_persistent_active("# Storage=persistent\n") is False
    assert sys_helpers._journald_persistent_active("Storage=persistent\n") is True
    assert sys_helpers._journald_persistent_active("Storage=auto\n") is False, "Storage=auto требует перезаписи"

    content = "#Storage=persistent\nStorage=auto\n"
    result = sys_helpers._set_storage_persistent(content)
    assert "#Storage=persistent" in result, "комментарий сохраняется"
    assert result.splitlines().count("Storage=persistent") == 1, "append ровно одной активной строки"
    assert "Storage=auto" not in result, "активная Storage=auto заменяется"
    logger.critical("[IMP:9][test] journald commented line not active — OK (T9.13)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.13 — активная Storage=persistent → no-op (0 записей)
# · Scenario: journald.conf уже с активной Storage=persistent → ensure_journald_persistent НЕ пишет
# · Remove if: journald idempotency semantics change
@ldd_trajectory
def test_journald_active_line_noop(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.13: активная Storage=persistent → no-op, без записи и рестарта."""
    caplog.set_level(logging.INFO)
    conf = tmp_path / "journald.conf"
    conf.write_text("# comment\nStorage=persistent\n", encoding="utf-8")
    monkeypatch.setattr(sys_helpers, "JOURNALD_CONF", str(conf))
    writes: list = []
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.helpers.system._atomic_write_text",
        lambda *a, **k: writes.append(a),
    )
    monkeypatch.setattr(
        "core.internal.bootstrap.lifecycle.helpers.system.run_subprocess",
        lambda *a, **k: writes.append(a),
    )

    assert sys_helpers.ensure_journald_persistent() is True
    assert not writes, "активная строка → 0 записей/рестартов (no-op)"
    assert "already set (active line)" in caplog.text
    logger.critical("[IMP:9][test] journald active line → no-op — OK (T9.13)")
