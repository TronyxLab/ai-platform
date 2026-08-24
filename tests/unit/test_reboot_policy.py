"""
# GREP_SUMMARY: test-reboot-policy, reboot-required, active-sessions, postpone, telegram-notify, state-file, anti-spam, dry-run, execute, install, units, R5-negative, DevPlan-164
# STRUCTURE: ▶ no-file → no-op │ ▶ active-session → postpone + TG │ ▶ idle + dry-run → no reboot │ ▶ idle + --execute → reboot + TG │ ▶ same-hash → 0 повторных уведомлений (R5) │ ▶ install idempotent │ ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for reboot_policy.py (DevPlan 164 W1-1, вариант A: сессия важнее ребута).
## @scope    Pure functions + check()/install() с DI (loginctl_fn/notify_fn/reboot_fn/systemctl_fn,
##           tmp_path для required_file/state_file/unit_dir). 0 monkeypatch-патчей (DI-HYG).
## @invariants
##   - no-file → exit 0, 0 уведомлений (R5-negative: ложный ребут при отсутствии флага)
##   - active-session → НЕТ ребута + TG «отложен» + state обновлён
##   - idle + dry-run (execute=False) → НЕТ reboot-вызова (R5-negative: ребут без --execute)
##   - idle + execute → reboot-вызов ровно 1 раз + TG «выполнен»
##   - same-hash + same-day → 0 повторных postpone-уведомлений (анти-спам R5-negative)
##   - install: content-match no-op (повторный вызов → 0 systemctl)
##   - Каждый тест — LDD IMP:9 через ldd_trajectory
## @rationale Вариант A (решение оператора 2026-08-13): Automatic-Reboot WithUsers=true отвергнут —
##            активная SSH-сессия блокирует ребут; таймер Persistent=true повторяет завтра.
## @changes  2026-08-13 | DevPlan 164 W1-1 — Created
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"))

import reboot_policy as rp

from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

REQUIRED_CONTENT = "*** System restart required ***"


# ═══════════════════════════════════════════════════════════════════
# region Tests: pure functions
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_read_reboot_required_absent(caplog, tmp_path: Path) -> None:
    """Отсутствующий файл → None (no-op путь)."""
    caplog.set_level(logging.INFO)
    assert rp.read_reboot_required(str(tmp_path / "nope")) is None
    logger.critical("[IMP:9][test] read_reboot_required absent → None")


@ldd_trajectory
def test_parse_loginctl_sessions(caplog) -> None:
    """loginctl-вывод → [(session, user), ...]; активные платформенные пользователи фильтруются."""
    caplog.set_level(logging.INFO)
    text = "c1 0 root seat0 tty1\nc2 1000 platform seat0 pts/0\nc3 1001 vasya seat0 pts/1\nc4 1002 ci-deploy\n"
    sessions = rp.parse_loginctl_sessions(text)
    assert ("c1", "root") in sessions
    assert ("c2", "platform") in sessions
    assert rp.active_platform_sessions(text) == ["ci-deploy", "platform", "root"]
    logger.critical("[IMP:9][test] loginctl parse + active platform users — OK")


@ldd_trajectory
def test_should_notify_postpone_anti_spam(caplog) -> None:
    """Анти-спам: новый хеш ИЛИ новая дата → notify; тот же хеш + та же дата → suppress."""
    caplog.set_level(logging.INFO)
    state = {"content_hash": "abc", "postpone_notified_at": "2026-08-13"}
    assert rp.should_notify_postpone(state, "abc", "2026-08-13") is False, "same hash + same day → suppress"
    assert rp.should_notify_postpone(state, "def", "2026-08-13") is True, "new hash → notify"
    assert rp.should_notify_postpone(state, "abc", "2026-08-14") is True, "new day → notify"
    logger.critical("[IMP:9][test] postpone anti-spam logic — OK")


# endregion Tests: pure functions


# ═══════════════════════════════════════════════════════════════════
# region Tests: check() orchestration
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_check_no_file_noop(caplog, tmp_path: Path) -> None:
    """R5-negative: нет reboot-required → exit 0, 0 уведомлений, 0 ребутов."""
    caplog.set_level(logging.INFO)
    notifications: list[str] = []
    reboots: list[str] = []

    rc = rp.check(
        execute=True,
        required_file=str(tmp_path / "reboot-required"),
        state_file=str(tmp_path / "state.json"),
        loginctl_fn=lambda: "c1 0 root seat0 tty1\n",
        notify_fn=lambda t: notifications.append(t) or True,
        reboot_fn=lambda: reboots.append("reboot") or True,
    )
    assert rc == 0
    assert notifications == [], f"нет флага → 0 уведомлений, got {notifications}"
    assert reboots == [], f"нет флага → 0 ребутов, got {reboots}"
    logger.critical("[IMP:9][test] check no-file → no-op (R5 negative) — OK")


@ldd_trajectory
def test_check_active_session_postpones(caplog, tmp_path: Path) -> None:
    """Активная сессия → TG «отложен», БЕЗ ребута, state записан."""
    caplog.set_level(logging.INFO)
    required = tmp_path / "reboot-required"
    required.write_text(REQUIRED_CONTENT, encoding="utf-8")
    state_file = str(tmp_path / "state.json")
    notifications: list[str] = []
    reboots: list[str] = []

    rc = rp.check(
        execute=True,
        required_file=str(required),
        state_file=state_file,
        loginctl_fn=lambda: "c2 1000 platform seat0 pts/0\n",
        notify_fn=lambda t: notifications.append(t) or True,
        reboot_fn=lambda: reboots.append("reboot") or True,
    )
    assert rc == 0
    assert reboots == [], "активная сессия → ребут НЕ выполняется"
    assert len(notifications) == 1 and "отложен" in notifications[0]
    state = rp.load_state(state_file)
    assert state.get("content_hash") == rp.content_hash(REQUIRED_CONTENT)
    assert state.get("postpone_notified_at")
    logger.critical("[IMP:9][test] active session → postpone + TG — OK")


@ldd_trajectory
def test_check_idle_dry_run_no_reboot(caplog, tmp_path: Path) -> None:
    """R5-negative: idle, но execute=False (dry-run) → НЕТ reboot-вызова."""
    caplog.set_level(logging.INFO)
    required = tmp_path / "reboot-required"
    required.write_text(REQUIRED_CONTENT, encoding="utf-8")
    reboots: list[str] = []

    rc = rp.check(
        execute=False,
        required_file=str(required),
        state_file=str(tmp_path / "state.json"),
        loginctl_fn=lambda: "",
        notify_fn=lambda _: None,
        reboot_fn=lambda: reboots.append("reboot") or True,
    )
    assert rc == 0
    assert reboots == [], "dry-run (execute=False) → ребут не выполняется (R5 negative)"
    logger.critical("[IMP:9][test] check idle dry-run → no reboot (R5 negative) — OK")


@ldd_trajectory
def test_check_idle_execute_reboots(caplog, tmp_path: Path) -> None:
    """Idle + --execute → reboot-вызов 1 раз + TG «выполнен» + state сброшен."""
    caplog.set_level(logging.INFO)
    required = tmp_path / "reboot-required"
    required.write_text(REQUIRED_CONTENT, encoding="utf-8")
    state_file = str(tmp_path / "state.json")
    notifications: list[str] = []
    reboots: list[str] = []

    rc = rp.check(
        execute=True,
        required_file=str(required),
        state_file=state_file,
        loginctl_fn=lambda: "",
        notify_fn=lambda t: notifications.append(t) or True,
        reboot_fn=lambda: reboots.append("reboot") or True,
    )
    assert rc == 0
    assert reboots == ["reboot"], "idle + execute → ровно 1 ребут"
    assert len(notifications) == 1 and "выполнен" in notifications[0]
    logger.critical("[IMP:9][test] check idle + execute → reboot + TG — OK")


@ldd_trajectory
def test_check_postpone_notify_once_per_day(caplog, tmp_path: Path) -> None:
    """R5-negative анти-спам: тот же хеш + та же дата → повторного уведомления НЕТ."""
    caplog.set_level(logging.INFO)
    required = tmp_path / "reboot-required"
    required.write_text(REQUIRED_CONTENT, encoding="utf-8")
    state_file = str(tmp_path / "state.json")
    notifications: list[str] = []

    args = {
        "execute": True,
        "required_file": str(required),
        "state_file": state_file,
        "loginctl_fn": lambda: "c2 1000 platform seat0 pts/0\n",
        "notify_fn": lambda t: notifications.append(t) or True,
        "reboot_fn": lambda: True,
    }
    assert rp.check(**args) == 0
    assert len(notifications) == 1, "первый вызов → уведомление"
    assert rp.check(**args) == 0
    assert len(notifications) == 1, "повторный вызов (тот же день, тот же хеш) → 0 новых (R5 negative)"
    logger.critical("[IMP:9][test] postpone anti-spam once-per-day — OK")


# endregion Tests: check() orchestration


# ═══════════════════════════════════════════════════════════════════
# region Tests: install()
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_install_writes_units_and_enables(caplog, tmp_path: Path) -> None:
    """install: юниты записаны + daemon-reload + enable --now (первый вызов)."""
    caplog.set_level(logging.INFO)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    systemctl_calls: list[list[str]] = []

    assert rp.install(str(unit_dir), systemctl_fn=lambda cmd: systemctl_calls.append(cmd) or True)
    assert (unit_dir / rp.SERVICE_UNIT_NAME).is_file()
    assert (unit_dir / rp.TIMER_UNIT_NAME).is_file()
    assert ["daemon-reload"] in systemctl_calls
    assert ["enable", "--now", rp.TIMER_UNIT_NAME] in systemctl_calls
    timer_text = (unit_dir / rp.TIMER_UNIT_NAME).read_text(encoding="utf-8")
    # REF-0009: 05:45 — ребут вне окна nightly backup (03:00 дамп + upload + retry 01:30)
    assert "OnCalendar=*-*-* 05:45:00" in timer_text
    assert "04:30" not in timer_text, "старое окно 04:30 пересекало backup-цикл"
    assert "Persistent=true" in timer_text
    service_text = (unit_dir / rp.SERVICE_UNIT_NAME).read_text(encoding="utf-8")
    assert "cert_expiry_check.py check" in service_text
    assert "reboot_policy.py check --execute" in service_text
    logger.critical("[IMP:9][test] install units + enable — OK")


@ldd_trajectory
def test_install_idempotent_no_systemctl(caplog, tmp_path: Path) -> None:
    """R5-negative: повторный install (идентичное содержимое) → 0 systemctl-вызовов."""
    caplog.set_level(logging.INFO)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    calls: list[list[str]] = []

    assert rp.install(str(unit_dir), systemctl_fn=lambda cmd: calls.append(cmd) or True)
    assert len(calls) == 2, "первый install → daemon-reload + enable"
    calls.clear()
    assert rp.install(str(unit_dir), systemctl_fn=lambda cmd: calls.append(cmd) or True)
    assert calls == [], f"повторный install → 0 systemctl (idempotent), got {calls}"
    logger.critical("[IMP:9][test] install idempotent — 0 systemctl on second call (R5 negative) — OK")


# endregion Tests: install()
