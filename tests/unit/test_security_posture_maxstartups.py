#!/usr/bin/env python3
# GREP_SUMMARY: security-posture maxstartups sshd drop-in apply no-op reload fallback R5-negative unit-tests DevPlan-136
# STRUCTURE: ▶ S4 fixtures (sshd -T effective maxstartups) → ◇ positive/negative (R5: 10:30:100 → FAIL) →
#            ▶ apply drop-in (tmp_path + fake probe) → ◇ create/no-op/update → ◇ reload fallback systemctl→service → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты MaxStartups (DevPlan 136 W3, T3.1-T3.3): check_sshd эффективное значение
##           ≥ 30:50:200 + apply_sshd_dropin (идемпотентный sshd_config.d drop-in + reload).
## @scope    Native pytest — прямые вызовы функций, tmp_path (Zero Hardcode Rule), monkeypatch
##           для subprocess-проб (fake probe) и путей. НЕ запускает реальные бинари.
##           Покрывает: FAIL на эффективном 10:30:100 (R5-negative — точный вход бага: дефолт
##           OpenSSH без drop-in), drop-in создаётся/no-op/обновляется, reload только при
##           изменении, fallback systemctl → service ssh reload, error-пути (write/reload).
## @invariants  Никаких hardcoded путей (tmp_path); никаких реальных subprocess;
##              caplog LDD IMP:9 в успешных сценариях (Anti-Illusion Rule)
## @rationale DevPlan 136 AC W3: unit-тесты зелёные; R5 anti-survivorship (Test Honesty).
## @changes 2026-08-05 | DevPlan 136 W3 — Created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap import security_posture

logger = logging.getLogger(__name__)


class FakeResult:
    """Graceful CompletedProcess stand-in (rc + stdout + stderr)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# S4 базовые директивы (фикстуры изолируют поведение MaxStartups)
SSHD_BASE = "permitrootlogin prohibit-password\npasswordauthentication no\npubkeyauthentication yes\n"


@pytest.fixture()
def fake_probe(monkeypatch):
    """Подменяет _probe: registry[name] → FakeResult; фиксирует все вызовы (reload-трекинг)."""
    registry: dict[str, FakeResult] = {}
    calls: list[list[str]] = []

    def _probe(cmd, timeout):
        calls.append(cmd)
        return registry.get(cmd[0], FakeResult())

    monkeypatch.setattr(security_posture, "_probe", _probe)
    return registry, calls


def _patch_dropin_path(monkeypatch, tmp_path) -> Path:
    """Перенаправляет SSHD_MAXSTARTUPS_DROPIN в tmp_path (Zero Hardcode Rule)."""
    dropin = tmp_path / "etc" / "ssh" / "sshd_config.d" / "99-platform-maxstartups.conf"
    monkeypatch.setattr(security_posture, "SSHD_MAXSTARTUPS_DROPIN", str(dropin))
    return dropin


def _assert_imp9(caplog, needle: str) -> None:
    """Anti-Illusion: в успешном сценарии должна быть IMP:9 траектория."""
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9 and needle in record.message:
                found = True
    print("--- END LDD TRAJECTORY ---")
    assert found, f"Critical LDD Error: No IMP:9 log containing '{needle}' found"


# region Tests: S4 — эффективный MaxStartups (T3.1)
class TestCheckSshdMaxStartups:
    # 🧪 TRAP[TEST] · Regression · S4 PASS при эффективном MaxStartups = минимуму 30:50:200
    # · Scenario: sshd -T выдаёт maxstartups 30:50:200 → check_sshd PASS + IMP:9
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: MaxStartups-проверка удалена из S4
    def test_positive_at_minimum(self, fake_probe, caplog):
        registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE + "maxstartups 30:50:200\n")
        with caplog.at_level(logging.INFO):
            result = security_posture.check_sshd()
        assert result.status == security_posture.STATUS_PASS
        assert "MaxStartups=30:50:200" in result.message
        _assert_imp9(caplog, "[S4]")

    # 🧪 TRAP[TEST] · Regression · S4 PASS при эффективном MaxStartups ВЫШЕ минимума
    # · Scenario: 40:60:250 ≥ 30:50:200 покомпонентно → PASS (не строгое равенство)
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: MaxStartups-проверка удалена из S4
    def test_positive_above_minimum(self, fake_probe):
        registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE + "maxstartups 40:60:250\n")
        result = security_posture.check_sshd()
        assert result.status == security_posture.STATUS_PASS

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · эффективный дефолт OpenSSH 10:30:100 → FAIL
    # · Scenario: свежий бутстрап БЕЗ drop-in — sshd -T печатает дефолт 10:30:100 < 30:50:200
    # · Last fail: свежий бутстрап не воспроизводил MaxStartups 30:50:200 (инцидент D-класса,
    #   ручной конфиг) — ТОЧНЫЙ вход бага: эффективное значение 10:30:100
    # · Remove if: политика MaxStartups отменена или канон изменён
    def test_negative_default_10_30_100(self, fake_probe):
        registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE + "maxstartups 10:30:100\n")
        result = security_posture.check_sshd()
        assert result.status == security_posture.STATUS_FAIL
        assert "MaxStartups=10:30:100" in result.message
        assert "30:50:200" in result.message

    # 🧪 TRAP[TEST] · Regression · Покомпонентное сравнение: rate 40 < 50 → FAIL
    # · Scenario: 30:40:200 — start/full на уровне, rate ниже → FAIL (не лексикографическое ≥)
    # · Last fail: лексикографическое сравнение кортежей пропустило бы (30,40,200) < (30,50,200) → False
    # · Remove if: сравнение MaxStartups изменено
    def test_negative_component_below(self, fake_probe):
        registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE + "maxstartups 30:40:200\n")
        result = security_posture.check_sshd()
        assert result.status == security_posture.STATUS_FAIL
        assert "MaxStartups" in result.message

    # 🧪 TRAP[TEST] · Regression · Не-числовой формат (OpenSSH ≥9.6 random:) → FAIL
    # · Scenario: maxstartups random:50:200 — политика должна быть явной → unparseable → FAIL
    # · Last fail: N/A (защита от будущих форматов OpenSSH)
    # · Remove if: поддержка random-формата добавлена намеренно
    def test_negative_unparseable(self, fake_probe):
        registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE + "maxstartups random:50:200\n")
        result = security_posture.check_sshd()
        assert result.status == security_posture.STATUS_FAIL
        assert "unparseable" in result.message

    # 🧪 TRAP[TEST] · Regression · Ненаблюдаемый maxstartups в выводе sshd -T → PASS
    # · Scenario: legacy-фикстура без строки maxstartups (существующий TestS4.SSHD_OK) —
    #   не утверждаем то, что не наблюдаем (graceful, как apt-check в S2)
    # · Last fail: добавление строгой проверки сломало бы существующие S4-фикстуры
    # · Remove if: sshd -T гарантированно всегда печатает maxstartups и фикстуры обновлены
    def test_positive_unobserved_value(self, fake_probe):
        registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE)
        result = security_posture.check_sshd()
        assert result.status == security_posture.STATUS_PASS


# endregion Tests: S4 — эффективный MaxStartups


# region Tests: apply_sshd_dropin (T3.2)
class TestApplyDropin:
    # 🧪 TRAP[TEST] · Regression · drop-in создаётся при отсутствии + reload ровно один раз
    # · Scenario: файла нет → атомарная запись (содержимое корректно) → systemctl reload sshd
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: apply_sshd_dropin удалён
    def test_creates_dropin_when_absent(self, fake_probe, tmp_path, monkeypatch, caplog):
        dropin = _patch_dropin_path(monkeypatch, tmp_path)
        registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin()
        assert ok is True
        assert dropin.is_file(), "drop-in должен быть создан"
        content = dropin.read_text()
        assert "MaxStartups 30:50:200" in content
        assert "DO NOT EDIT MANUALLY" in content
        assert "sshd_config.d drop-in" in content
        assert calls == [["systemctl", "reload", "sshd"]], "reload ровно один раз (fallback не нужен)"
        _assert_imp9(caplog, "[maxstartups]")

    # 🧪 TRAP[TEST] · Regression · no-op при совпадении содержимого — reload НЕ вызывается
    # · Scenario: drop-in уже с каноническим содержимым → 0 записей, 0 reload
    # · Last fail: повторный apply перезаписывал файл и перезагружал sshd (не идемпотентно)
    # · Remove if: семантика идемпотентности изменена
    def test_noop_when_content_matches(self, fake_probe, tmp_path, monkeypatch, caplog):
        dropin = _patch_dropin_path(monkeypatch, tmp_path)
        dropin.parent.mkdir(parents=True)
        dropin.write_text(security_posture.desired_maxstartups_dropin())
        registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin()
        assert ok is True
        assert calls == [], "reload НЕ должен вызываться при no-op"
        joined = " ".join(r.message for r in caplog.records)
        assert "no-op" in joined
        assert "[IMP:9][posture][maxstartups][write]" not in joined, "не должно быть записи на диск"

    # 🧪 TRAP[TEST] · Regression · изменение содержимого → перезапись + reload
    # · Scenario: существующий drop-in с чужим значением (10:30:100) → атомарно заменён + reload
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: apply_sshd_dropin удалён
    def test_updates_on_content_change(self, fake_probe, tmp_path, monkeypatch):
        dropin = _patch_dropin_path(monkeypatch, tmp_path)
        dropin.parent.mkdir(parents=True)
        dropin.write_text("MaxStartups 10:30:100\n")
        registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        ok = security_posture.apply_sshd_dropin()
        assert ok is True
        assert "MaxStartups 30:50:200" in dropin.read_text()
        assert calls == [["systemctl", "reload", "sshd"]]

    # 🧪 TRAP[TEST] · Regression · systemctl недоступен → fallback service ssh reload
    # · Scenario: systemctl reload sshd rc!=0 → service ssh reload rc=0 → apply True
    # · Last fail: единственный systemctl-путь ломал apply в окружениях без systemd
    # · Remove if: fallback-семантика изменена
    def test_reload_fallback_service(self, fake_probe, tmp_path, monkeypatch):
        _patch_dropin_path(monkeypatch, tmp_path)  # monkeypatch-сайд-эффект (путь → tmp_path)
        registry, calls = fake_probe
        registry["systemctl"] = FakeResult(1, "", "Unit sshd.service not found")
        registry["service"] = FakeResult(0)
        ok = security_posture.apply_sshd_dropin()
        assert ok is True
        assert calls == [["systemctl", "reload", "sshd"], ["service", "ssh", "reload"]]

    # 🧪 TRAP[TEST] · Regression · оба reload-пути не удались → apply False (честный отказ)
    # · Scenario: drop-in записан, но reload не удался → конфиг не активен → False + IMP:10
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: политика «файл записан, reload не критичен» изменится
    def test_both_reload_fail_returns_false(self, fake_probe, tmp_path, monkeypatch, caplog):
        dropin = _patch_dropin_path(monkeypatch, tmp_path)
        registry, _ = fake_probe
        registry["systemctl"] = FakeResult(1)
        registry["service"] = FakeResult(1)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin()
        assert ok is False
        assert dropin.is_file(), "drop-in записан, но reload не удался → False (не активен)"
        assert any("[IMP:10]" in r.message and "reload FAILED" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · ошибка записи → apply False с понятным сообщением
    # · Scenario: atomic_write_text бросает OSError → False + IMP:10 Cannot write
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: обработка ошибок записи изменена
    def test_write_error_returns_false(self, fake_probe, tmp_path, monkeypatch, caplog):
        _patch_dropin_path(monkeypatch, tmp_path)

        def _boom(path, content, mode=0o644):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(security_posture, "atomic_write_text", _boom)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin()
        assert ok is False
        assert any("[IMP:10]" in r.message and "Cannot write" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · CLI --apply-sshd: exit 0 при успехе
    # · Scenario: main(["--apply-sshd"]) с root + успешным reload → 0
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: CLI-контракт --apply-sshd изменён
    def test_main_apply_sshd_exit_zero(self, fake_probe, tmp_path, monkeypatch, capsys):
        _patch_dropin_path(monkeypatch, tmp_path)
        registry, _ = fake_probe
        registry["systemctl"] = FakeResult(0)
        monkeypatch.setattr(security_posture.os, "geteuid", lambda: 0)
        assert security_posture.main(["--apply-sshd"]) == 0

    # 🧪 TRAP[TEST] · Regression · CLI --apply-sshd: exit 1 при ошибке reload
    # · Scenario: main(["--apply-sshd"]) с root + оба reload rc!=0 → 1
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: CLI-контракт --apply-sshd изменён
    def test_main_apply_sshd_exit_one_on_reload_failure(self, fake_probe, tmp_path, monkeypatch, capsys):
        _patch_dropin_path(monkeypatch, tmp_path)
        registry, _ = fake_probe
        registry["systemctl"] = FakeResult(1)
        registry["service"] = FakeResult(1)
        monkeypatch.setattr(security_posture.os, "geteuid", lambda: 0)
        assert security_posture.main(["--apply-sshd"]) == 1

    # 🧪 TRAP[TEST] · Regression · CLI --apply-sshd: root-check fail-fast → exit 2
    # · Scenario: euid != 0 → 2 (без записи/реload) — тот же root-контракт, что и check-режим
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: root-check семантика изменена
    def test_main_apply_sshd_root_check_fail_fast(self, fake_probe, tmp_path, monkeypatch, capsys):
        _patch_dropin_path(monkeypatch, tmp_path)
        monkeypatch.setattr(security_posture.os, "geteuid", lambda: 1000)
        assert security_posture.main(["--apply-sshd"]) == 2
        assert "must run as root" in capsys.readouterr().err


# endregion Tests: apply_sshd_dropin
