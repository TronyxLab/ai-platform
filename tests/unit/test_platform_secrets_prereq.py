# GREP_SUMMARY: platform-secrets-prereq chown-failure-visible warn prereq-fail root AI-0052r
# STRUCTURE: ▶ monkeypatched _chown(False) + geteuid=0 → ⎋ (False, «Cannot chown») │ ▶ не-root → прежний best-effort True
# region MODULE_CONTRACT
## @purpose  AI-0052r (DevPlan 17 T3.5, $TEST_SPEC row): chown-фейл виден — warn всегда,
##           а под root это prereq fail (fail-closed). Не-root локальные прогоны
##           сохраняют best-effort семантику.
## @scope    tests/unit: monkeypatched _chown/geteuid; без subprocess.
## @invariants
##   - _chown → False + euid==0 → ensure_age_key возвращает (False, msg)
##   - _chown → False + euid!=0 → прежний best-effort (True)
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "modules" / "platform-secrets"))
import installer as ps

logger = logging.getLogger(__name__)


@pytest.fixture()
def age_key(tmp_path: Path) -> Path:
    key = tmp_path / "age-key.txt"
    key.write_text("AGE_SECRET_KEY=ok\n", encoding="utf-8")
    return key


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · chown-фейл виден и фейл-клоузит под root (AI-0052r)
# · Regression: _chown глотал неудачу молча (contextlib.suppress) — misconfiguration
#   владельца age-key на VPS проходила незамеченной
# · Scenario: (1) под root _chown=False → ensure_age_key (False, «Cannot chown»);
#   (2) не-root — best-effort True; (3) сам _chown логирует IMP:8 warn при неудаче
# · Last fail: DevPlan 17 верификация @64c2090 ($TEST_SPEC T3.5)
# · Remove if: владелец age-key перестаёт быть root-инвариантом платформы
def test_chown_failure_visible(
    age_key: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    # ── 1. под root: chown неудача → prereq fail ──
    monkeypatch.setattr(ps, "_chown", lambda _path: False)
    monkeypatch.setattr(sys, "platform", "linux")  # geteuid существует на POSIX
    import os

    if not hasattr(os, "geteuid"):
        pytest.skip("geteuid недоступен на этой платформе")
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    ok, msg = ps.ensure_age_key(age_key, {})
    assert ok is False, "под root chown-фейл обязан фейлить prerequisite"
    assert "chown" in msg.lower(), f"сообщение обязано называть причину: {msg}"
    logger.critical("[IMP:9][test] chown failure fails closed under root — OK (AI-0052r)")

    # ── 2. не-root: best-effort сохранён ──
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    ok_nonroot, _msg = ps.ensure_age_key(age_key, {})
    assert ok_nonroot is True, "не-root локальный прогон сохраняет best-effort семантику"


# 🧪 TRAP[TEST] · 2026-08-26 · P4 · _chown логирует warn при неудаче
# · Scenario: subprocess rc≠0 → IMP:8 warn присутствует
# · Last fail: охранник T3.5 (DevPlan 17)
# · Remove if: вместе с test_chown_failure_visible
def test_chown_warn_logged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    class _Fail:
        returncode = 1

        def __init__(self) -> None:
            self.stderr = ""

    monkeypatch.setattr(ps.subprocess, "run", lambda *_a, **_kw: _Fail())
    with caplog.at_level(logging.WARNING):
        assert ps._chown(tmp_path / "x") is False
    assert any("chown root:root failed" in r.getMessage() for r in caplog.records)
    logger.critical("[IMP:9][test] chown failure warns visibly — OK (T3.5)")
