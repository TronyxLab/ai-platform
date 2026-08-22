# GREP_SUMMARY: security-posture maxstartups sshd drop-in apply no-op reload fallback R5-negative unit-tests DevPlan-136 DevPlan-162-ssh-hardening
# STRUCTURE: ▶ S4 fixtures (sshd -T effective maxstartups) → ◇ positive/negative (R5: 10:30:100 → FAIL) →
#            ▶ hardening drop-in content (8 директив, no weak MACs) → ▶ apply (tmp_path + fake probe) →
#            ◇ create/no-op/update → ◇ superseded maxstartups removal → ◇ reload fallback systemctl→service → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты sshd drop-in (DevPlan 136 W3 MaxStartups + DevPlan 162 W2-1 hardening):
##           check_sshd эффективное значение ≥ 30:50:200 + apply_sshd_dropin (идемпотентный
##           sshd_config.d hardening drop-in: 8 директив, superset MaxStartups, removal + reload).
## @scope    Native pytest — прямые вызовы функций, tmp_path (Zero Hardcode Rule), monkeypatch
##           для subprocess-проб (fake probe) и путей. НЕ запускает реальные бинари.
##           Покрывает: FAIL на эффективном 10:30:100 (R5-negative — точный вход бага: дефолт
##           OpenSSH без drop-in), desired_ssh_hardening_dropin содержимое (8 директив, без
##           слабых MACs hmac-sha1/umac-64), drop-in создаётся/no-op/обновляется, superseded
##           maxstartups-файл удаляется при apply, reload только при изменении,
##           fallback systemctl → service ssh reload, error-пути (write/reload).
## @invariants  Никаких hardcoded путей (tmp_path); никаких реальных subprocess;
##              caplog LDD IMP:9 в успешных сценариях (Anti-Illusion Rule)
## @rationale DevPlan 136 AC W3 + 162 W2-1: unit-тесты зелёные; R5 anti-survivorship (Test Honesty).
## @changes 2026-08-05 | DevPlan 136 W3 — Created
## @changes 2026-08-13 | DevPlan 162 W2-1 — apply_sshd_dropin → hardening drop-in; +content tests
# endregion MODULE_CONTRACT

import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.internal.bootstrap import security_posture
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


@dataclass
class FakeResult:
    """Фейковый CompletedProcess: returncode/stdout/stderr (DI probe-канал)."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class FakeFacts:
    """EnvironmentFacts-fake (W-H DevPlan 163): is_root через параметр (0 патчей os.geteuid)."""

    def __init__(self, is_root: bool = True) -> None:
        self._is_root = is_root

    def is_root(self) -> bool:
        return self._is_root

    def which(self, binary: str) -> str | None:
        return binary


# S4 базовые директивы (фикстуры изолируют поведение MaxStartups)
SSHD_BASE = "permitrootlogin prohibit-password\npasswordauthentication no\npubkeyauthentication yes\n"


@pytest.fixture()
def fake_probe():
    """Probe-DI (167 D0): registry[name] → FakeResult; фиксирует все вызовы (reload-трекинг).

    ## @purpose — Возвращает (probe, registry, calls): probe передаётся напрямую в
    ##             check_sshd(probe=) / apply_sshd_dropin(probe_fn=) / main(probe=) —
    ##             production УЖЕ имеет DI-шов (W-H 163), 0 monkeypatch _probe.
    """
    registry: dict[str, FakeResult] = {}
    calls: list[list[str]] = []

    def _probe(cmd, timeout):
        calls.append(cmd)
        return registry.get(cmd[0], FakeResult())

    return _probe, registry, calls


def _dropin_paths(tmp_path) -> tuple[Path, Path]:
    """DI-пути apply_sshd_dropin (W-H DevPlan 163): hardening + drop-in в tmp_path."""
    dropin = tmp_path / "etc" / "ssh" / "sshd_config.d" / "99-platform-ssh-hardening.conf"
    superseded = tmp_path / "etc" / "ssh" / "sshd_config.d" / "99-platform-maxstartups.conf"
    return dropin, superseded


# T2.16a: _assert_imp9 консолидирован в gate_helpers.assert_ldd_imp9
# region Tests: S4 — эффективный MaxStartups (T3.1)
class TestCheckSshdMaxStartups:
    # 🧪 TRAP[TEST] · Regression · S4 PASS при эффективном MaxStartups = минимуму 30:50:200
    # · Scenario: sshd -T выдаёт maxstartups 30:50:200 → check_sshd PASS + IMP:9
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: MaxStartups-проверка удалена из S4
    def test_positive_at_minimum(self, fake_probe, caplog):
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE + "maxstartups 30:50:200\n")
        with caplog.at_level(logging.INFO):
            result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_PASS
        assert "MaxStartups=30:50:200" in result.message
        assert_ldd_imp9(caplog, needle="[S4]")

    # 🧪 TRAP[TEST] · Regression · S4 PASS при эффективном MaxStartups ВЫШЕ минимума / ненаблюдаемом
    # · Scenario: 40:60:250 ≥ 30:50:200 покомпонентно → PASS; отсутствие строки → PASS (graceful)
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: MaxStartups-проверка удалена из S4
    @pytest.mark.parametrize(
        "config_line",
        [
            SSHD_BASE + "maxstartups 40:60:250\n",  # выше минимума (покомпонентно ≥)
            SSHD_BASE,  # ненаблюдаемое значение — PASS (graceful, как apt-check в S2)
        ],
    )
    def test_positive_variants(self, fake_probe, config_line):
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, config_line)
        result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_PASS

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · эффективный дефолт OpenSSH 10:30:100 → FAIL
    # · Scenario: свежий бутстрап БЕЗ drop-in — sshd -T печатает дефолт 10:30:100 < 30:50:200
    # · Last fail: свежий бутстрап не воспроизводил MaxStartups 30:50:200 (инцидент D-класса,
    #   ручной конфиг) — ТОЧНЫЙ вход бага: эффективное значение 10:30:100
    # · Remove if: политика MaxStartups отменена или канон изменён
    def test_negative_default_10_30_100(self, fake_probe):
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE + "maxstartups 10:30:100\n")
        result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_FAIL
        assert "MaxStartups=10:30:100" in result.message
        assert "30:50:200" in result.message

    # 🧪 TRAP[TEST] · Regression · Покомпонентное сравнение: rate 40 < 50 → FAIL
    # · Scenario: 30:40:200 — start/full на уровне, rate ниже → FAIL (не лексикографическое ≥)
    # · Last fail: лексикографическое сравнение кортежей пропустило бы (30,40,200) < (30,50,200) → False
    # · Remove if: сравнение MaxStartups изменено
    def test_negative_component_below(self, fake_probe):
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE + "maxstartups 30:40:200\n")
        result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_FAIL
        assert "MaxStartups" in result.message

    # 🧪 TRAP[TEST] · Regression · Не-числовой формат (OpenSSH ≥9.6 random:) → FAIL
    # · Scenario: maxstartups random:50:200 — политика должна быть явной → unparseable → FAIL
    # · Last fail: N/A (защита от будущих форматов OpenSSH)
    # · Remove if: поддержка random-формата добавлена намеренно
    def test_negative_unparseable(self, fake_probe):
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_BASE + "maxstartups random:50:200\n")
        result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_FAIL
        assert "unparseable" in result.message


# endregion Tests: S4 — эффективный MaxStartups


# region Tests: desired_ssh_hardening_dropin (DevPlan 162 W2-1)
class TestHardeningDropinContent:
    # 🧪 TRAP[TEST] · REGRESSION · 162 W2-1 · hardening drop-in содержит все 8 директив
    # · Scenario: desired_ssh_hardening_dropin() → PermitRootLogin prohibit-password,
    # ·   PasswordAuthentication no, AllowUsers root/platform/ci-deploy, X11Forwarding no,
    # ·   AllowTcpForwarding no, ClientAliveInterval 300, MACs *-etm, MaxStartups 30:50:200
    # · Last fail: 2026-08-13 — SSH-drift на проде (PermitRootLogin yes / PasswordAuthentication yes)
    # · Remove if: политика sshd-харденинга изменена через TRAP[DECISION]
    def test_all_eight_directives_present(self):
        content = security_posture.desired_ssh_hardening_dropin()
        for directive in (
            "PermitRootLogin prohibit-password",
            "PasswordAuthentication no",
            "AllowUsers root platform ci-deploy",
            "X11Forwarding no",
            "AllowTcpForwarding no",
            "ClientAliveInterval 300",
            "MACs hmac-sha2-256-etm@openssh.com,hmac-sha2-512-etm@openssh.com",
            "MaxStartups 30:50:200",
        ):
            assert directive in content, f"missing directive: {directive}\n{content}"
        assert "DO NOT EDIT MANUALLY" in content

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · 162 W2-1 · слабые MACs (hmac-sha1/umac-64) НЕ в drop-in
    # · Scenario: SSH-drift — hmac-sha1/umac-64 присутствовали в sshd -T на проде; drop-in
    # ·   обязан содержать ТОЛЬКО *-etm@openssh.com
    # · Last fail: 2026-08-13 — слабые MACs в эффективном конфиге (аудит SSH-drift)
    # · Remove if: политика MACs изменена
    def test_no_weak_macs(self):
        content = security_posture.desired_ssh_hardening_dropin()
        for weak in ("hmac-sha1", "hmac-md5", "umac-64"):
            assert weak not in content, f"weak MAC {weak} присутствует в hardening drop-in:\n{content}"

    # 🧪 TRAP[TEST] · REGRESSION · 162 W2-1 · hardening — superset maxstartups drop-in
    # · Scenario: MaxStartups директива + комментарий superset присутствуют; отдельный
    # ·   maxstartups-файл больше не нужен (удаляется при apply)
    # · Last fail: N/A (новый кейс DevPlan 162 W2-1)
    # · Remove if: apply перестанет писать superset-контент
    def test_hardening_is_superset_of_maxstartups(self):
        hardening = security_posture.desired_ssh_hardening_dropin()
        minimal = security_posture.desired_maxstartups_dropin()
        assert "MaxStartups 30:50:200" in hardening
        assert "MaxStartups 30:50:200" in minimal
        assert hardening != minimal, "hardening обязан содержать больше, чем только MaxStartups"


# endregion Tests: desired_ssh_hardening_dropin


# region Tests: apply_sshd_dropin (T3.2)
class TestApplyDropin:
    # 🧪 TRAP[TEST] · Regression · drop-in создаётся при отсутствии + reload ровно один раз
    # · Scenario: файла нет → атомарная запись (hardening-содержимое) → systemctl reload sshd
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: apply_sshd_dropin удалён
    def test_creates_dropin_when_absent(self, fake_probe, tmp_path, caplog):
        dropin, superseded = _dropin_paths(tmp_path)
        probe, registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin(
                hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
            )
        assert ok is True
        assert dropin.is_file(), "drop-in должен быть создан"
        content = dropin.read_text()
        assert "MaxStartups 30:50:200" in content
        assert "DO NOT EDIT MANUALLY" in content
        assert "PermitRootLogin prohibit-password" in content
        assert "AllowUsers root platform ci-deploy" in content
        assert calls == [["systemctl", "reload", "sshd"]], "reload ровно один раз (fallback не нужен)"
        assert_ldd_imp9(caplog, needle="[sshd-hardening]")

    # 🧪 TRAP[TEST] · Regression · no-op при совпадении содержимого — reload НЕ вызывается
    # · Scenario: drop-in уже с каноническим hardening-содержимым → 0 записей, 0 reload
    # · Last fail: повторный apply перезаписывал файл и перезагружал sshd (не идемпотентно)
    # · Remove if: семантика идемпотентности изменена
    def test_noop_when_content_matches(self, fake_probe, tmp_path, caplog):
        dropin, superseded = _dropin_paths(tmp_path)
        dropin.parent.mkdir(parents=True)
        dropin.write_text(security_posture.desired_ssh_hardening_dropin())
        probe, registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin(
                hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
            )
        assert ok is True
        assert calls == [], "reload НЕ должен вызываться при no-op"
        joined = " ".join(r.message for r in caplog.records)
        assert "no-op" in joined
        assert "[IMP:9][posture][sshd-hardening][write]" not in joined, "не должно быть записи на диск"

    # 🧪 TRAP[TEST] · Regression · изменение содержимого → перезапись + reload
    # · Scenario: существующий drop-in с чужим значением (MaxStartups 10:30:100) → атомарно заменён + reload
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: apply_sshd_dropin удалён
    def test_updates_on_content_change(self, fake_probe, tmp_path):
        dropin, superseded = _dropin_paths(tmp_path)
        dropin.parent.mkdir(parents=True)
        dropin.write_text("MaxStartups 10:30:100\n")
        probe, registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        ok = security_posture.apply_sshd_dropin(
            hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
        )
        assert ok is True
        assert "MaxStartups 30:50:200" in dropin.read_text()
        assert calls == [["systemctl", "reload", "sshd"]]

    # 🧪 TRAP[TEST] · Regression · 162 W2-1 · superseded maxstartups-файл удаляется при apply
    # · Scenario: 99-platform-maxstartups.conf существует → apply удаляет его (superseded) + reload
    # · Last fail: N/A (новый кейс DevPlan 162 W2-1 — чистый каталог sshd_config.d)
    # · Remove if: superseded-файл оставлен намеренно (дубликат MaxStartups)
    def test_superseded_maxstartups_removed(self, fake_probe, tmp_path, caplog):
        dropin, superseded = _dropin_paths(tmp_path)
        superseded.parent.mkdir(parents=True)
        superseded.write_text("MaxStartups 30:50:200\n")
        probe, registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin(
                hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
            )
        assert ok is True
        assert dropin.is_file()
        assert not superseded.exists(), "superseded maxstartups drop-in обязан быть удалён (superseded)"
        assert calls == [["systemctl", "reload", "sshd"]], "удаление = изменение → reload"
        assert any("superseded" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · 162 W2-1 · superseded-файл НЕ удаляется, если это тот же путь
    # · Scenario: SSHD_MAXSTARTUPS_DROPIN == SSHD_HARDENING_DROPIN (тест/аномальная конфигурация) —
    # ·   unlink собственного только что записанного файла не происходит (resolve-guard)
    # · Last fail: N/A (защита от self-delete при коллизии констант)
    # · Remove if: resolve-guard удалён
    def test_superseded_same_path_not_removed(self, fake_probe, tmp_path):
        dropin, _ = _dropin_paths(tmp_path)
        probe, registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        # == hardening (resolve-guard: self-delete предотвращается)
        ok = security_posture.apply_sshd_dropin(
            hardening_dropin=str(dropin), superseded_dropin=str(dropin), probe_fn=probe
        )
        assert ok is True
        assert dropin.is_file(), "файл должен существовать (не self-delete)"
        assert calls == [["systemctl", "reload", "sshd"]]

    # 🧪 TRAP[TEST] · Regression · systemctl недоступен → fallback service ssh reload
    # · Scenario: systemctl reload sshd rc!=0 → service ssh reload rc=0 → apply True
    # · Last fail: единственный systemctl-путь ломал apply в окружениях без systemd
    # · Remove if: fallback-семантика изменена
    def test_reload_fallback_service(self, fake_probe, tmp_path):
        dropin, superseded = _dropin_paths(tmp_path)
        probe, registry, calls = fake_probe
        registry["systemctl"] = FakeResult(1, "", "Unit sshd.service not found")
        registry["service"] = FakeResult(0)
        ok = security_posture.apply_sshd_dropin(
            hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
        )
        assert ok is True
        assert calls == [["systemctl", "reload", "sshd"], ["service", "ssh", "reload"]]

    # 🧪 TRAP[TEST] · Regression · оба reload-пути не удались → apply False (честный отказ)
    # · Scenario: drop-in записан, но reload не удался → конфиг не активен → False + IMP:10
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: политика «файл записан, reload не критичен» изменится
    def test_both_reload_fail_returns_false(self, fake_probe, tmp_path, caplog):
        dropin, superseded = _dropin_paths(tmp_path)
        probe, registry, _ = fake_probe
        registry["systemctl"] = FakeResult(1)
        registry["service"] = FakeResult(1)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin(
                hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
            )
        assert ok is False
        assert dropin.is_file(), "drop-in записан, но reload не удался → False (не активен)"
        assert any("[IMP:10]" in r.message and "reload FAILED" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · ошибка записи → apply False с понятным сообщением
    # · Scenario: atomic_write_text бросает OSError → False + IMP:10 Cannot write
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: обработка ошибок записи изменена
    def test_write_error_returns_false(self, fake_probe, tmp_path, caplog):
        probe, _, _ = fake_probe
        dropin, superseded = _dropin_paths(tmp_path)

        def _boom(path, content, mode=0o644):
            msg = "read-only filesystem"
            raise OSError(msg)

        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin(
                hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe, write_fn=_boom
            )
        assert ok is False
        assert any("[IMP:10]" in r.message and "Cannot write" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · CLI --apply-sshd: exit 0 при успехе
    # · Scenario: main(["--apply-sshd"]) с root + успешным reload → 0
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: CLI-контракт --apply-sshd изменён
    def test_main_apply_sshd_exit_zero(self, fake_probe, tmp_path):
        dropin, superseded = _dropin_paths(tmp_path)
        probe, registry, _ = fake_probe
        registry["systemctl"] = FakeResult(0)
        # DI (W-H): facts= (root) + paths= (dropin-пути) — 0 патчей
        assert (
            security_posture.main(
                ["--apply-sshd"],
                facts=FakeFacts(is_root=True),
                paths={"sshd_hardening_dropin": str(dropin), "sshd_maxstartups_dropin": str(superseded)},
                probe=probe,
            )
            == 0
        )

    # 🧪 TRAP[TEST] · Regression · CLI --apply-sshd: exit 1 при ошибке reload
    # · Scenario: main(["--apply-sshd"]) с root + оба reload rc!=0 → 1
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: CLI-контракт --apply-sshd изменён
    def test_main_apply_sshd_exit_one_on_reload_failure(self, fake_probe, tmp_path):
        dropin, superseded = _dropin_paths(tmp_path)
        probe, registry, _ = fake_probe
        registry["systemctl"] = FakeResult(1)
        registry["service"] = FakeResult(1)
        assert (
            security_posture.main(
                ["--apply-sshd"],
                facts=FakeFacts(is_root=True),
                paths={"sshd_hardening_dropin": str(dropin), "sshd_maxstartups_dropin": str(superseded)},
                probe=probe,
            )
            == 1
        )

    # 🧪 TRAP[TEST] · Regression · CLI --apply-sshd: root-check fail-fast → exit 2
    # · Scenario: euid != 0 → 2 (без записи/реload) — тот же root-контракт, что и check-режим
    # · Last fail: N/A (новый кейс DevPlan 136 W3)
    # · Remove if: root-check семантика изменена
    def test_main_apply_sshd_root_check_fail_fast(self, fake_probe, tmp_path, capsys):  # ruff: ignore[ARG002]
        probe, _, _ = fake_probe
        assert security_posture.main(["--apply-sshd"], facts=FakeFacts(is_root=False), probe=probe) == 2
        assert "must run as root" in capsys.readouterr().err


# endregion Tests: apply_sshd_dropin
