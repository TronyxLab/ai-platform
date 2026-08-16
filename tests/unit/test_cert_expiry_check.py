"""
# GREP_SUMMARY: test-cert-expiry, openssl-enddate, days-left, threshold, telegram-notify, state-hash, anti-spam, R5-negative, DevPlan-164
# STRUCTURE: ▶ parse_enddate │ ▶ days_left (fresh/expiring/expired) │ ▶ scan_expiring (threshold-фильтр, DI run_fn) │ ▶ check: fresh→no-op │ expiring→TG │ same-hash→suppress (R5) │ ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for cert_expiry_check.py (DevPlan 164 W1-2, закрытие deferred-TRAP 162 W6-2).
## @scope    Pure functions + check() с DI (run_fn/notify_fn, tmp_path для cert_dir/state_file).
##            НИКАКИХ реальных openssl/subprocess (unit-контракт).
## @invariants
##   - parse_enddate: "notAfter=..." → UTC datetime; мусор → None
##   - days_left: порог 14 дней (R5-negative: fresh → no-op, expiring → report)
##   - expired (negative days) попадает в отчёт
##   - anti-spam: тот же хеш отчёта → 0 повторных уведомлений (R5-negative)
##   - Каждый тест — LDD IMP:9 через ldd_trajectory
## @rationale Порог 14 дней = неделя на реакцию + неделя на retry acme.sh.
## @changes  2026-08-13 | DevPlan 164 W1-2 — Created
# endregion MODULE_CONTRACT
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"))

import cert_expiry_check as ce

from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════
# region Tests: pure functions
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_parse_enddate(caplog) -> None:
    """notAfter-строка → UTC datetime; мусор → None."""
    caplog.set_level(logging.INFO)
    dt = ce.parse_enddate("notAfter=Sep 11 12:00:00 2026 GMT\n")
    assert dt is not None and dt.tzinfo is not None
    assert ce.parse_enddate("garbage") is None
    logger.critical("[IMP:9][test] parse_enddate valid/garbage — OK")


@ldd_trajectory
def test_days_left_thresholds(caplog) -> None:
    """days_left: fresh (≥14) / expiring (<14) / expired (отрицательные)."""
    caplog.set_level(logging.INFO)
    assert ce.days_left(NOW + timedelta(days=30), NOW) >= 14
    assert ce.days_left(NOW + timedelta(days=7), NOW) < 14
    assert ce.days_left(NOW - timedelta(days=2), NOW) < 0, "истёкший — отрицательные дни"
    logger.critical("[IMP:9][test] days_left thresholds — OK")


@ldd_trajectory
def test_domain_from_dir(caplog) -> None:
    """acme.sh имена каталогов → домены (_ecc-суффикс срезается)."""
    caplog.set_level(logging.INFO)
    assert ce.domain_from_dir("tronyx.ru") == "tronyx.ru"
    assert ce.domain_from_dir("botanika.tronyx.ru_ecc") == "botanika.tronyx.ru"
    logger.critical("[IMP:9][test] domain_from_dir — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: scan + check orchestration
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_scan_expiring_filters_by_threshold(caplog, tmp_path: Path) -> None:
    """R5-negative: свежий (20 дней) НЕ в отчёте; истекающий (7 дней) и истёкший (-2) — в отчёте."""
    caplog.set_level(logging.INFO)
    cert_dir = tmp_path / "acme"
    for name in ("fresh.example", "expiring.example", "expired.example"):
        d = cert_dir / name
        d.mkdir(parents=True)
        (d / "fullchain.cer").write_text("", encoding="utf-8")

    class _Scripted:
        def __call__(self, cmd: list[str]) -> object:
            class _Result:
                pass

            res = _Result()
            res.returncode = 0
            days_map = {"fresh.example": 20, "expiring.example": 7, "expired.example": -2}
            path = cmd[-1]
            days = next((d for name, d in days_map.items() if name in path), 20)
            enddate = NOW + timedelta(days=days)
            res.stdout = f"notAfter={enddate.strftime('%b %d %H:%M:%S %Y')} GMT\n"
            return res

    expiring = ce.scan_expiring(str(cert_dir), threshold_days=14, now=NOW, run_fn=_Scripted())
    assert "fresh.example" not in expiring, "свежий сертификат не должен попадать в отчёт"
    assert expiring.get("expiring.example") == 7
    assert expiring.get("expired.example") == -2
    logger.critical("[IMP:9][test] scan_expiring threshold filter (R5 negative) — OK")


@ldd_trajectory
def test_check_no_expiring_noop(caplog, tmp_path: Path) -> None:
    """R5-negative: свежие сертификаты → exit 0, 0 уведомлений."""
    caplog.set_level(logging.INFO)
    cert_dir = tmp_path / "acme"
    d = cert_dir / "fresh.example"
    d.mkdir(parents=True)
    (d / "fullchain.cer").write_text("", encoding="utf-8")
    notifications: list[str] = []

    class _Fresh:
        def __call__(self, _cmd) -> object:
            class _Result:
                pass

            res = _Result()
            res.returncode = 0
            enddate = NOW + timedelta(days=30)
            res.stdout = f"notAfter={enddate.strftime('%b %d %H:%M:%S %Y')} GMT\n"
            return res

    rc = ce.check(
        cert_dir=str(cert_dir),
        state_file=str(tmp_path / "state.json"),
        now=NOW,
        run_fn=_Fresh(),
        notify_fn=lambda t: notifications.append(t) or True,
    )
    assert rc == 0
    assert notifications == [], f"fresh → 0 уведомлений, got {notifications}"
    logger.critical("[IMP:9][test] check no expiring → no-op (R5 negative) — OK")


@ldd_trajectory
def test_check_expiring_notifies_and_anti_spam(caplog, tmp_path: Path) -> None:
    """Истекающий → TG + state; повторный вызов (тот же хеш) → 0 уведомлений (R5-negative)."""
    caplog.set_level(logging.INFO)
    cert_dir = tmp_path / "acme"
    d = cert_dir / "expiring.example"
    d.mkdir(parents=True)
    (d / "fullchain.cer").write_text("", encoding="utf-8")
    state_file = str(tmp_path / "state.json")
    notifications: list[str] = []

    class _Expiring:
        def __call__(self, _cmd) -> object:
            class _Result:
                pass

            res = _Result()
            res.returncode = 0
            enddate = NOW + timedelta(days=5)
            res.stdout = f"notAfter={enddate.strftime('%b %d %H:%M:%S %Y')} GMT\n"
            return res

    args = {
        "cert_dir": str(cert_dir),
        "state_file": state_file,
        "now": NOW,
        "run_fn": _Expiring(),
        "notify_fn": lambda t: notifications.append(t) or True,
    }
    assert ce.check(**args) == 0
    assert len(notifications) == 1 and "expiring.example" in notifications[0]
    assert ce.check(**args) == 0
    assert len(notifications) == 1, "тот же хеш отчёта → 0 повторных (R5 negative anti-spam)"
    logger.critical("[IMP:9][test] check expiring → TG once (anti-spam R5 negative) — OK")


# endregion
