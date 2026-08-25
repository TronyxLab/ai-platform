# GREP_SUMMARY: REF-0016 xs-access-hardening sshd-T fixture kbdinteractive challengeresponse maxauthtries cloud-init-neutralize sudoers arg-spec mode-pin SEC-0002 SEC-0005 SEC-0014
# STRUCTURE: ▶ emulated sshd -T fixtures → ◇ check_sshd positive/negative (R5: kbd=yes/challenge=yes/maxauthtries=6 → FAIL) →
#            ▶ desired_ssh_hardening_dropin content (REF-0016 директивы) → ▶ *cloud* neutralization (tmp_path DI) →
#            ◇ rename → .disabled / benign untouched / .disabled idempotent / rename-fail → False →
#            ▶ render_sudoers arg-spec gate (--mode init|update pin, bare-rule detector + R5 negative) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты REF-0016 (Волна 0, meta-refactoring; SEC-0002/SEC-0005/SEC-0014):
##           (1) S4 ловит key-only обходы в эффективном sshd -T — KbdInteractiveAuthentication/
##           ChallengeResponseAuthentication ≠ no и MaxAuthTries > 3 → FAIL (fixture-парсинг итогового
##           вывода, без реального sshd); (2) hardening drop-in содержит все REF-0016 директивы;
##           (3) apply нейтрализует ЛЮБОЙ *cloud* vendor drop-in с ослабляющей директивой
##           (glob вместо точечного 50-cloud-init.conf), rename-fail → apply False (fail-fast);
##           (4) sudoers line-format gate: node-lifecycle.sh правило пинит аргументы
##           `--mode init|--mode update` (bare NOPASSWD = SEC-0014 root-backdoor вектор).
## @scope    Native pytest — прямые вызовы security_posture.check_sshd / desired_ssh_hardening_dropin /
##           apply_sshd_dropin (DI probe/sshd_config_dir/write_fn) и setup_node.render_sudoers.
##           tmp_path (Zero Hardcode), 0 реальных subprocess/бинарей.
## @invariants  Никаких hardcoded системных путей (tmp_path + DI-швы W-H 163);
##              R5-негативы на точных входах багов: kbd=yes, challenge=yes, maxauthtries=6 (дефолт
##              OpenSSH), bare sudoers-правило без аргументов;
##              LDD IMP:9 траектория (@ldd_trajectory)
## @rationale Карточка REF-0016 «Tests required»: drop-in content gate (парсинг итогового sshd -T
##            на fixture) + sudoers line-format gate. Размещение tests/unit — по прецеденту
##            test_security_posture_maxstartups.py / test_setup_node.py (static_audit).
## @changes 2026-08-24 | REF-0016 — Created
## @links   core/internal/bootstrap/security/sshd_policy.py,
##          core/internal/bootstrap/lifecycle/phases/system.py (шаг 5.6 blocking),
##          core/internal/bootstrap/setup_node.py (render_sudoers arg-spec)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.internal.bootstrap import security_posture
from core.internal.bootstrap.setup_node import DEFAULT_PLATFORM_ROOT, render_sudoers
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_SSHD_CONFIG_DIR_NAME = "sshd_config.d"


@dataclass
class FakeResult:
    """Фейковый CompletedProcess: returncode/stdout/stderr (DI probe-канал)."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@pytest.fixture()
def fake_probe():
    """Probe-DI (W-H 163): registry[binary] → FakeResult; вызовы фиксируются (reload-трекинг)."""

    registry: dict[str, FakeResult] = {}
    calls: list[list[str]] = []

    def _probe(cmd, timeout):
        calls.append(cmd)
        return registry.get(cmd[0], FakeResult())

    return _probe, registry, calls


def _dropin_paths(tmp_path: Path) -> tuple[Path, Path]:
    """DI-пути apply_sshd_dropin: hardening + superseded внутри tmp sshd_config.d."""
    config_dir = tmp_path / "etc" / "ssh" / _SSHD_CONFIG_DIR_NAME
    return (
        config_dir / "99-platform-ssh-hardening.conf",
        config_dir / "99-platform-maxstartups.conf",
    )


def _make_cloud_dropin(config_dir: Path, name: str, content: str) -> Path:
    """Создать vendor/cloud drop-in в tmp sshd_config.d (вход для нейтрализации)."""
    config_dir.mkdir(parents=True, exist_ok=True)
    cloud = config_dir / name
    cloud.write_text(content, encoding="utf-8")
    return cloud


# Эмулированный «идеальный» вывод sshd -T (все канонические значения платформенной политики)
SSHD_T_HARDENED = (
    "permitrootlogin prohibit-password\n"
    "passwordauthentication no\n"
    "pubkeyauthentication yes\n"
    "kbdinteractiveauthentication no\n"
    "challengeresponseauthentication no\n"
    "maxauthtries 3\n"
    "maxstartups 30:50:200\n"
    "allowusers root platform ci-deploy\n"
    "clientaliveinterval 300\n"
    "permituserenvironment no\n"
    "x11forwarding no\n"
    "allowtcpforwarding no\n"
    "kexalgorithms curve25519-sha256,diffie-hellman-group-exchange-sha256\n"
    "ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com\n"
    "macs hmac-sha2-256-etm@openssh.com,hmac-sha2-512-etm@openssh.com\n"
    "logingracetime 30\n"
)


# ═══════════════════════════════════════════════════════════════════════
# S4 × REF-0016: парсинг итогового sshd -T (fixture gate)
# ═══════════════════════════════════════════════════════════════════════


class TestSshdEffectiveKeyOnlyPolicy:
    # 🧪 TRAP[TEST] · REGRESSION · REF-0016 · S4 PASS на полностью захардененном sshd -T
    # · Scenario: kbd-interactive/challenge-response = no, MaxAuthTries = 3 → PASS + IMP:9
    # · Last fail: N/A (новый кейс REF-0016)
    # · Remove if: политика key-only входа изменена через TRAP[DECISION]
    @ldd_trajectory
    def test_hardened_fixture_passes(self, fake_probe, caplog) -> None:
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_T_HARDENED)
        with caplog.at_level(logging.INFO):
            result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_PASS, result.message
        logger.critical("[IMP:9][test][s4-ref0016] Захардененный sshd -T → PASS")

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · REF-0016/SEC-0002 · kbd-interactive включён → FAIL
    # · Scenario: vendor drop-in включил KbdInteractiveAuthentication yes — точный вход бага:
    # ·   keyboard-interactive обходит «PasswordAuthentication no» через PAM
    # · Last fail: 2026-08-24 — S4 не проверял строку вовсе («root только по ключу» = ложь без сигнала)
    # · Remove if: kbd-interactive проверка удалена из _SSHD_EXTRA_DIRECTIVES
    @pytest.mark.parametrize(
        "line",
        ["kbdinteractiveauthentication yes\n", "kbdinteractiveauthentication without-password\n"],
    )
    @ldd_trajectory
    def test_negative_kbd_interactive_enabled(self, fake_probe, caplog, line) -> None:
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_T_HARDENED.replace("kbdinteractiveauthentication no\n", line))
        with caplog.at_level(logging.INFO):
            result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_FAIL
        assert "KbdInteractiveAuthentication" in result.message
        logger.critical("[IMP:9][test][s4-ref0016] kbd-interactive обход детектирован: %s", line.strip())

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · REF-0016/SEC-0002 · challenge-response включён → FAIL
    # · Scenario: legacy-алиас ChallengeResponseAuthentication=yes (OpenSSH <8.7) — тот же обход
    # · Last fail: 2026-08-24 — строка отсутствовала в S4-каноне
    # · Remove if: challenge-response проверка удалена
    @ldd_trajectory
    def test_negative_challenge_response_enabled(self, fake_probe, caplog) -> None:
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(
            0, SSHD_T_HARDENED.replace("challengeresponseauthentication no\n", "challengeresponseauthentication yes\n")
        )
        with caplog.at_level(logging.INFO):
            result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_FAIL
        assert "ChallengeResponseAuthentication" in result.message
        logger.critical("[IMP:9][test][s4-ref0016] challenge-response обход детектирован")

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · REF-0016/SEC-0005 · дефолт MaxAuthTries 6 → FAIL
    # · Scenario: свежая нода БЕЗ drop-in — sshd -T печатает дефолт OpenSSH maxauthtries 6 (> 3)
    # · Last fail: 2026-08-24 — MaxAuthTries не проверялся (rider SEC-0005)
    # · Remove if: порог MaxAuthTries изменён
    @ldd_trajectory
    def test_negative_maxauthtries_default_6(self, fake_probe, caplog) -> None:
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_T_HARDENED.replace("maxauthtries 3\n", "maxauthtries 6\n"))
        with caplog.at_level(logging.INFO):
            result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_FAIL
        assert "MaxAuthTries" in result.message
        logger.critical("[IMP:9][test][s4-ref0016] MaxAuthTries=6 (дефолт) детектирован")

    # 🧪 TRAP[TEST] · Regression · graceful-skip: новые OpenSSH убрали challenge-response строку —
    # ·   отсутствие строки НЕ должно давать ложный FAIL (контракт расширенных директив)
    # · Remove if: graceful-семантика расширенных директив изменена
    @ldd_trajectory
    def test_absent_challenge_response_line_is_graceful(self, fake_probe, caplog) -> None:
        probe, registry, _ = fake_probe
        registry["sshd"] = FakeResult(0, SSHD_T_HARDENED.replace("challengeresponseauthentication no\n", ""))
        with caplog.at_level(logging.INFO):
            result = security_posture.check_sshd(probe=probe)
        assert result.status == security_posture.STATUS_PASS, result.message
        logger.critical("[IMP:9][test][s4-ref0016] Отсутствие challenge-response строки — graceful PASS")


# ═══════════════════════════════════════════════════════════════════════
# Drop-in content: REF-0016 директивы
# ═══════════════════════════════════════════════════════════════════════


class TestHardeningDropinRef0016Content:
    # 🧪 TRAP[TEST] · REGRESSION · REF-0016 · drop-in пинит kbd/challenge/maxauthtries
    # · Scenario: desired_ssh_hardening_dropin() обязан содержать ТОЧНЫЕ строки политики
    # · Last fail: 2026-08-24 — drop-in не пинил эти директивы (vendor drop-in побеждал по порядку)
    # · Remove if: состав drop-in изменён через TRAP[DECISION]
    @ldd_trajectory
    def test_ref0016_directives_present(self, caplog) -> None:  # ruff: ignore[ARG002]
        content = security_posture.desired_ssh_hardening_dropin()
        for directive in (
            "KbdInteractiveAuthentication no",
            "ChallengeResponseAuthentication no",
            "MaxAuthTries 3",
        ):
            assert directive in content, f"missing REF-0016 directive: {directive}\n{content}"
        logger.critical("[IMP:9][test][dropin] REF-0016 директивы присутствуют в hardening drop-in")

    # 🧪 TRAP[TEST] · REGRESSION · REF-0016 · прежние 8 директив не потеряны (superset-контракт)
    # · Remove if: состав drop-in изменён
    @ldd_trajectory
    def test_legacy_directives_still_present(self, caplog) -> None:  # ruff: ignore[ARG002]
        content = security_posture.desired_ssh_hardening_dropin()
        for directive in (
            "PermitRootLogin prohibit-password",
            "PasswordAuthentication no",
            "AllowUsers root platform ci-deploy",
            "MaxStartups 30:50:200",
        ):
            assert directive in content, f"потеряна легаси-директива: {directive}"
        logger.critical("[IMP:9][test][dropin] Легаси-директивы superset сохранены")


# ═══════════════════════════════════════════════════════════════════════
# *cloud* vendor drop-in нейтрализация (apply_sshd_dropin)
# ═══════════════════════════════════════════════════════════════════════


class TestCloudDropinNeutralization:
    # 🧪 TRAP[TEST] · REGRESSION · v1.0.1 Фаза 6 + REF-0016 · 50-cloud-init.conf → .disabled
    # · Scenario: cloud-init drop-in с PasswordAuthentication yes переименован, reload вызван
    # · Last fail: 2026-08-13 — sshd -T давал passwordauthentication yes при запрете в drop-in
    # · Remove if: механизм нейтрализации изменён (include-нейтрализация и т.п.)
    @ldd_trajectory
    def test_cloud_init_dropin_neutralized(self, fake_probe, tmp_path, caplog) -> None:
        dropin, superseded = _dropin_paths(tmp_path)
        cloud = _make_cloud_dropin(dropin.parent, "50-cloud-init.conf", "PasswordAuthentication yes\n")
        probe, registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin(
                hardening_dropin=str(dropin),
                superseded_dropin=str(superseded),
                probe_fn=probe,
            )
        assert ok is True
        assert not cloud.exists(), "ослабляющий cloud drop-in должен быть переименован"
        assert Path(str(cloud) + ".disabled").is_file(), "нейтрализация = rename → .disabled"
        assert calls == [["systemctl", "reload", "sshd"]], "нейтрализация = изменение → reload"
        assert dropin.is_file(), "hardening drop-in записан"
        logger.critical("[IMP:9][test][cloud] 50-cloud-init.conf нейтрализован → .disabled")

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · REF-0016 · vendor-вариант имени + challenge-response ловится
    # · Scenario: 60-cloudimg-settings.conf с ChallengeResponseAuthentication yes (точный вход бага:
    # ·   прежний код матчит только точное имя 50-cloud-init.conf и не знал challenge-response)
    # · Last fail: 2026-08-24 — glob-нейтрализация отсутствовала
    # · Remove if: нейтрализация заменена другим механизмом
    @ldd_trajectory
    def test_vendor_glob_variant_with_challenge_response_neutralized(self, fake_probe, tmp_path, caplog) -> None:
        dropin, superseded = _dropin_paths(tmp_path)
        cloud = _make_cloud_dropin(dropin.parent, "60-cloudimg-settings.conf", "ChallengeResponseAuthentication yes\n")
        probe, registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin(
                hardening_dropin=str(dropin),
                superseded_dropin=str(superseded),
                probe_fn=probe,
            )
        assert ok is True
        assert not cloud.exists(), "vendor *cloud* вариант обязан нейтрализоваться (glob)"
        assert Path(str(cloud) + ".disabled").is_file()
        assert calls == [["systemctl", "reload", "sshd"]]
        logger.critical("[IMP:9][test][cloud] Vendor *cloud* glob-вариант нейтрализован")

    # 🧪 TRAP[TEST] · Regression · доброкачественный vendor drop-in НЕ трогается
    # · Scenario: *cloud*-файл без ослабляющих директив остаётся активным
    # · Remove if: нейтрализация стала тотальной (без разбора контента)
    @ldd_trajectory
    def test_benign_cloud_file_untouched(self, fake_probe, tmp_path, caplog) -> None:  # ruff: ignore[ARG002]
        dropin, superseded = _dropin_paths(tmp_path)
        cloud = _make_cloud_dropin(dropin.parent, "60-cloudimg-settings.conf", "# vendor defaults\nUsePAM yes\n")
        probe, registry, _ = fake_probe
        registry["systemctl"] = FakeResult(0)
        ok = security_posture.apply_sshd_dropin(
            hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
        )
        assert ok is True
        assert cloud.is_file(), "доброкачественный vendor drop-in не должен переименовываться"
        logger.critical("[IMP:9][test][cloud] Доброкачественный vendor drop-in сохранён")

    # 🧪 TRAP[TEST] · Regression · идемпотентность: уже-.disabled файл НЕ переименовывается повторно
    # · Remove if: суффикс нейтрализации изменён
    @ldd_trajectory
    def test_already_disabled_not_reprocessed(self, fake_probe, tmp_path, caplog) -> None:  # ruff: ignore[ARG002]
        dropin, superseded = _dropin_paths(tmp_path)
        disabled = _make_cloud_dropin(dropin.parent, "50-cloud-init.conf.disabled", "PasswordAuthentication yes\n")
        probe, registry, _ = fake_probe
        registry["systemctl"] = FakeResult(0)
        ok = security_posture.apply_sshd_dropin(
            hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
        )
        assert ok is True
        assert disabled.is_file(), ".disabled должен остаться как есть (без .disabled.disabled)"
        assert not Path(str(disabled) + ".disabled").exists()
        logger.critical("[IMP:9][test][cloud] Повторный apply не трогает .disabled")

    # 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R10/T2.E — произвольное имя vendor drop-in
    # · Scenario: «60-custom.conf» (БЕЗ "cloud" в имени!) с PasswordAuthentication yes —
    #   прежний glob *cloud* его не видел → ослабляющая политика оставалась активной
    # · Last fail: 2026-08-25 (REGRESSIONS.md R10) — имя файла было единственным сигналом
    # · Remove if: нейтрализация заменена include-механизмом
    @ldd_trajectory
    def test_noncloud_named_weakening_dropin_neutralized(self, fake_probe, tmp_path, caplog) -> None:
        """Drop-in произвольного имени с ослабляющей директивой → neutralized."""
        dropin, superseded = _dropin_paths(tmp_path)
        custom = _make_cloud_dropin(dropin.parent, "60-custom.conf", "PasswordAuthentication yes\n")
        probe, registry, calls = fake_probe
        registry["systemctl"] = FakeResult(0)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin(
                hardening_dropin=str(dropin),
                superseded_dropin=str(superseded),
                probe_fn=probe,
            )
        assert ok is True
        assert not custom.exists(), "R10 FAIL: weakening drop-in без 'cloud' в имени обязан нейтрализоваться"
        assert Path(str(custom) + ".disabled").is_file(), "нейтрализация = rename → .disabled"
        assert calls == [["systemctl", "reload", "sshd"]]
        logger.critical("[IMP:9][test][cloud] Non-cloud имя 60-custom.conf нейтрализовано (content-based)")

    # 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R10/T2.E — case-вариант директивы ловится
    # · Scenario: «passwordauthentication YES» (нижний регистр) в vendor conf — прежний regex
    #   без IGNORECASE пропускал
    # · Last fail: 2026-08-25 — детект был case-sensitive
    # · Remove if: парсер директив станет структурным (тогда кейс нормализуется там)
    @ldd_trajectory
    def test_case_variant_weakening_detected(self, fake_probe, tmp_path, caplog) -> None:  # ruff: ignore[ARG002]
        """Case-insensitive детект: 'passwordauthentication yes' → neutralized."""
        dropin, superseded = _dropin_paths(tmp_path)
        vendor = _make_cloud_dropin(dropin.parent, "70-vendor-tweaks.conf", "passwordauthentication YES\n")
        probe, registry, _ = fake_probe
        registry["systemctl"] = FakeResult(0)
        ok = security_posture.apply_sshd_dropin(
            hardening_dropin=str(dropin),
            superseded_dropin=str(superseded),
            probe_fn=probe,
        )
        assert ok is True
        assert not vendor.exists(), "R10 FAIL: case-вариант ослабления обязан детектироваться"
        assert Path(str(vendor) + ".disabled").is_file()
        logger.critical("[IMP:9][test][cloud] Case-вариант passwordauthentication YES пойман")

    # 🧪 TRAP[TEST] · Regression · доброкачественный *.conf без cloud-имени НЕ трогается
    # · Scenario: расширение скана на ВСЕ *.conf не должно переименовывать невинные файлы
    # · Last fail: N/A (preventive против false-positive расширения R10)
    # · Remove if: вместе со сканом *.conf
    @ldd_trajectory
    def test_benign_noncloud_conf_untouched(self, fake_probe, tmp_path, caplog) -> None:  # ruff: ignore[ARG002]
        """Benign 60-motd.conf остаётся активным (content-based = только weakening)."""
        dropin, superseded = _dropin_paths(tmp_path)
        benign = _make_cloud_dropin(dropin.parent, "60-motd.conf", "# motd banner config\nPrintMotd no\n")
        probe, registry, _ = fake_probe
        registry["systemctl"] = FakeResult(0)
        ok = security_posture.apply_sshd_dropin(
            hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
        )
        assert ok is True
        assert benign.is_file(), "доброкачественный non-cloud .conf не должен переименовываться"
        logger.critical("[IMP:9][test][cloud] Benign non-cloud .conf сохранён")

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · REF-0016 · rename-fail → apply False (не тихий WARN)
    # · Scenario: ОС отвергает rename (EACCES/EBUSY) — точный вход бага: прежний код логировал
    # ·   WARN и возвращал True при АКТИВНОМ ослабляющем vendor drop-in
    # · Last fail: 2026-08-24 — best-effort применение маскировало провал (карточка REF-0016)
    # · Remove if: fail-fast семантика apply изменена
    @ldd_trajectory
    def test_rename_failure_returns_false(self, fake_probe, tmp_path, caplog, monkeypatch) -> None:
        dropin, superseded = _dropin_paths(tmp_path)
        _make_cloud_dropin(dropin.parent, "50-cloud-init.conf", "PermitRootLogin yes\n")
        probe, _, calls = fake_probe

        def _boom(self: pathlib.Path, target: pathlib.Path) -> None:
            msg = "permission denied"
            raise OSError(msg)

        monkeypatch.setattr(pathlib.Path, "rename", _boom)
        with caplog.at_level(logging.INFO):
            ok = security_posture.apply_sshd_dropin(
                hardening_dropin=str(dropin), superseded_dropin=str(superseded), probe_fn=probe
            )
        assert ok is False, "активный ослабляющий vendor drop-in = apply FAIL (fail-fast, REF-0016)"
        assert calls == [], "при провале нейтрализации reload не достигается (fail-fast до reload)"
        assert any("[IMP:10]" in r.message and "left ACTIVE" in r.message for r in caplog.records)
        logger.critical("[IMP:9][test][cloud] rename-fail → apply False + IMP:10 (blocking сигнал)")


# ═══════════════════════════════════════════════════════════════════════
# sudoers line-format gate: arg-spec pin node-lifecycle.sh (SEC-0014)
# ═══════════════════════════════════════════════════════════════════════

_PINNED_RULE_RE = re.compile(r"node-lifecycle\.sh\s+--mode\s+(init|update)\s*$")


def _unpinned_node_lifecycle_rules(content: str) -> list[str]:
    """Детектор unpinned node-lifecycle правил (некомментарные строки без --mode pin в конце).

    ## @purpose — line-format gate REF-0016/SEC-0014: каждое NOPASSWD-правило на node-lifecycle.sh
    ##            обязано заканчиваться строгим аргументным пином `--mode init|update`.
    """
    violations: list[str] = []
    for lineno, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or "node-lifecycle.sh" not in stripped:
            continue
        if not _PINNED_RULE_RE.search(stripped):
            violations.append(f"sudoers L{lineno}: unpinned rule — {stripped}")
            logger.info("[IMP:9][sudoers-argpin] UNPINNED: %s", violations[-1])
    return violations


class TestSudoersArgSpecPin:
    # 🧪 TRAP[TEST] · REGRESSION · REF-0016/SEC-0014 · оба режима запинены
    # · Scenario: render_sudoers содержит ровно --mode init и --mode update правила
    # · Last fail: 2026-08-24 — bare NOPASSWD допускал --ci-root-key/--state-file (root-backdoor)
    # · Remove if: операционная модель platform user изменена
    @ldd_trajectory
    def test_pinned_mode_rules_present(self, caplog) -> None:  # ruff: ignore[ARG002]
        content = render_sudoers("node-a", DEFAULT_PLATFORM_ROOT, "2026-08-24T00:00:00Z")
        for mode in ("init", "update"):
            rule = f"{DEFAULT_PLATFORM_ROOT}/core/internal/bootstrap/node-lifecycle.sh --mode {mode}"
            assert rule in content, f"missing pinned rule: {rule}\n{content}"
        logger.critical("[IMP:9][test][sudoers] Pinned правила --mode init|update присутствуют")

    # 🧪 TRAP[TEST] · REGRESSION · REF-0016/SEC-0014 · bare/unpinned форма отсутствует
    # · Scenario: ни одна некомментарная строка с node-lifecycle.sh не нарушает пин-формат
    # · Remove if: формат пина изменён (тогда синхронизировать детектор)
    @ldd_trajectory
    def test_no_unpinned_rules_in_render(self, caplog) -> None:  # ruff: ignore[ARG002]
        content = render_sudoers("node-a", DEFAULT_PLATFORM_ROOT, "2026-08-24T00:00:00Z")
        violations = _unpinned_node_lifecycle_rules(content)
        assert violations == [], f"unpinned node-lifecycle правила: {violations}"
        # опасные флаги никогда не входят в granted-argv (только в комментариях допустимы)
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for flag in ("--ci-root-key", "--owner-key", "--state-file", "--run-phase"):
                assert flag not in stripped, f"L{lineno}: опасный флаг {flag} в granted-строке"
        logger.critical("[IMP:9][test][sudoers] Unpinned правил: 0; опасные флаги вне granted-argv")

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · REF-0016/SEC-0014 · возврат bare-правила детектируется
    # · Scenario: легаси bare-правило (точный вход бага SEC-0014) добавлено в контент — детектор
    # ·   ОБЯЗАН его поймать (иначе gate слеп к регрессии)
    # · Last fail: 2026-08-24 — setup_node.py:176 содержал именно эту строку
    # · Remove if: детектор заменён иным механизмом
    @ldd_trajectory
    def test_unpinned_rule_detected_negative(self, caplog) -> None:  # ruff: ignore[ARG002]
        clean = render_sudoers("node-a", DEFAULT_PLATFORM_ROOT, "2026-08-24T00:00:00Z")
        regressed = clean + (
            f"\nplatform ALL=(root) NOPASSWD: {DEFAULT_PLATFORM_ROOT}/core/internal/bootstrap/node-lifecycle.sh\n"
        )
        violations = _unpinned_node_lifecycle_rules(regressed)
        assert len(violations) >= 1, "R5 FAIL: детектор не поймал возврат bare node-lifecycle правила"
        assert any("node-lifecycle.sh" in v and "--mode" not in v for v in violations)
        logger.critical("[IMP:9][test][sudoers] R5: возврат bare-правила детектирован (%d)", len(violations))
