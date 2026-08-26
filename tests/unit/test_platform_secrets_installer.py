# GREP_SUMMARY: test-platform-secrets installer age-key environmentfile bare-migration perm-fix symlink-fallback systemd unit
# STRUCTURE: ┌tmp_path fixtures┐ → ◇ ensure_age_key (create/migrate/perm-fix/fail) → ◇ ensure_secrets_enc (found/symlink-fallback/fail) → ◇ check_prerequisites (ok/abort) → ◇ ensure_platform_dirs (2775) → ◇ install_service (unit content) → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/platform-secrets/installer.py (DevPlan 118 E7 — Python-порт
##           install.sh). Тест ДО/ПОСЛЕ: файловые операции фиксируют контракт старого shell
##           (age-key EnvironmentFile format, B3 миграция, perm auto-fix, symlink-fallback),
##           systemd unit content. Native imports, tmp_path, monkeypatch PATH-констант.
## @scope    Tests: age-key create (KEY=VALUE format — systemd EnvironmentFile), bare→KEY=VALUE миграция,
##           permission auto-fix (600/root:root), missing key + no env → fail, secrets-enc symlink-fallback,
##           prerequisites abort, platform dirs 2775, service unit install content.
## @invariants
##   - Все тесты monkeypatch PATH-константы на tmp_path (zero hardcoded /etc paths)
##   - R5 anti-survivorship: negative-тесты (bare format, missing key, missing secrets)
##   - LDD: IMP:9 on success, IMP:10 on abort
## @rationale E7 Strangler: file-менеджмент → Python. Файловые операции тестируемы с tmp_path.
## @changes  2026-08-02 | DevPlan 118 E7 — Created
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "modules" / "platform-secrets"))
import installer as ps

pytestmark = pytest.mark.static_audit


# region TEST_ensure_age_key
def test_ensure_age_key_creates_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_age_key_creates_from_env — DevPlan 118 E migration unit test
    """ensure_age_key: missing key + AGE_SECRET_KEY env → created in KEY=VALUE EnvironmentFile format."""
    age_key = tmp_path / "age-key.txt"
    ok, msg = ps.ensure_age_key(age_key, {"AGE_SECRET_KEY": "super-secret"})
    assert ok is True
    assert msg == "created"
    content = age_key.read_text()
    assert content == "AGE_SECRET_KEY=super-secret\n", f"EnvironmentFile KEY=VALUE required, got {content!r}"
    assert age_key.stat().st_mode & 0o777 == 0o600


def test_ensure_age_key_missing_env_fails(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_age_key_missing_env_fails — DevPlan 118 E migration unit test
    """ensure_age_key: no file + no AGE_SECRET_KEY → (False, fail) — fail-fast."""
    ok, msg = ps.ensure_age_key(tmp_path / "age-key.txt", {})
    assert ok is False
    assert "AGE_SECRET_KEY not set" in msg


def test_ensure_age_key_migrates_bare_format(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_age_key_migrates_bare_format — DevPlan 118 E migration unit test
    """ensure_age_key: bare key (no AGE_SECRET_KEY= prefix) → migrated to KEY=VALUE (B3 fix)."""
    age_key = tmp_path / "age-key.txt"
    age_key.write_text("bare-secret-value\n")
    ok, msg = ps.ensure_age_key(age_key, {})
    assert ok is True
    assert msg == "migrated"
    assert age_key.read_text() == "AGE_SECRET_KEY=bare-secret-value\n"
    assert age_key.stat().st_mode & 0o777 == 0o600


def test_ensure_age_key_permission_autofix(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_age_key_permission_autofix — DevPlan 118 E migration unit test
    """ensure_age_key: existing key with wrong mode (644) → auto-fixed to 600 (idempotent repeat-run)."""
    age_key = tmp_path / "age-key.txt"
    age_key.write_text("AGE_SECRET_KEY=ok\n")
    age_key.chmod(0o644)
    ok, _ = ps.ensure_age_key(age_key, {})
    assert ok is True
    assert age_key.stat().st_mode & 0o777 == 0o600


# endregion TEST_ensure_age_key


# region TEST_ensure_secrets_enc
def test_ensure_secrets_enc_found(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_secrets_enc_found — DevPlan 118 E migration unit test
    """ensure_secrets_enc: secrets.enc.yaml exists → ok."""
    enc = tmp_path / "secrets.enc.yaml"
    enc.write_text("enc: data\n")
    ok, msg = ps.ensure_secrets_enc(enc)
    assert ok is True
    assert msg == "ok"


def test_ensure_secrets_enc_symlink_fallback(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_secrets_enc_symlink_fallback — DevPlan 118 E migration unit test
    """ensure_secrets_enc: missing + node-configs/*.enc.yaml → symlink created (node_secrets_dir DI)."""
    node_dir = tmp_path / "node-secrets"
    node_dir.mkdir()
    (node_dir / "context.enc.yaml").write_text("enc: data\n")

    enc = tmp_path / "secrets" / "secrets.enc.yaml"
    ok, msg = ps.ensure_secrets_enc(enc, node_secrets_dir=node_dir)
    assert ok is True
    assert msg == "symlinked"
    assert enc.is_symlink(), "symlink fallback expected"
    assert enc.read_text() == "enc: data\n"


def test_ensure_secrets_enc_missing_fails(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_secrets_enc_missing_fails — DevPlan 118 E migration unit test
    """ensure_secrets_enc: neither path → (False, fail) — fail-fast."""
    ok, msg = ps.ensure_secrets_enc(tmp_path / "nope" / "secrets.enc.yaml")
    assert ok is False
    assert "not found" in msg


# endregion TEST_ensure_secrets_enc


# region TEST_check_prerequisites
def test_check_prerequisites_ok(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_prerequisites_ok — DevPlan 118 E migration unit test
    """check_prerequisites: age-key + secrets-enc → True (age_key_path DI)."""
    enc = tmp_path / "secrets" / "secrets.enc.yaml"
    enc.parent.mkdir(parents=True)
    enc.write_text("x\n")
    env = {"AGE_SECRET_KEY": "secret"}
    assert ps.check_prerequisites(tmp_path, env, age_key_path=tmp_path / "age-key.txt") is True


def test_check_prerequisites_abort_missing_key(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_prerequisites_abort_missing_key — DevPlan 118 E migration unit test
    """check_prerequisites: no age key + no env → False (abort)."""
    assert ps.check_prerequisites(tmp_path, {}) is False


# endregion TEST_check_prerequisites


# region TEST_ensure_platform_dirs
def test_ensure_platform_dirs_creates_2775() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_platform_dirs_creates_2775 — DevPlan 118 E migration unit test
    """ensure_platform_dirs: platform_root/prometheus-targets/secrets created with setgid 2775."""
    # ⚠️ TRAP[BUG] · 2026-08-26 · P3 · setgid-тест падал на macOS под pytest-tmp_path
    # · Symptom: st_mode возвращался без S_ISGID при вызове из tmp_path (/private/tmp/…)
    # · Root: macOS kernel МОЛЧА сбрасывает S_ISGID у каталогов внутри world-writable
    #   sticky-каталога (/tmp) — security-hardening; chmod(0o2775) проходит, бит не ставится.
    #   Прод (Linux, /var/lib/platform) не затронут.
    # · Fix: база через tempfile.mkdtemp() (/var/folders/…) — вне sticky-каталога;
    #   ассерты не ослаблены
    # · Prevention: setgid-ассерты — только вне world-writable sticky-баз (macOS)
    import tempfile

    with tempfile.TemporaryDirectory() as base:
        platform_root = Path(base) / "platform"
        ok = ps.ensure_platform_dirs(platform_root)
        assert ok is True
        for d in (platform_root, platform_root / "prometheus-targets", platform_root / "secrets"):
            assert d.is_dir(), f"{d} must exist"
            assert d.stat().st_mode & 0o2775 == 0o2775, f"{d} must be setgid 2775 (got {oct(d.stat().st_mode)})"


# endregion TEST_ensure_platform_dirs


# region TEST_install_service
def test_install_service_unit_content(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_install_service_unit_content — DevPlan 118 E migration unit test
    """install_service: unit copied to SYSTEMD_DIR with Before=docker.service (oneshot contract, DI)."""
    unit_src = tmp_path / "platform-secrets.service"
    unit_src.write_text(
        "[Unit]\nDescription=platform-secrets\nBefore=docker.service\n[Service]\nType=oneshot\nRemainAfterExit=yes\n"
    )

    ok = ps.install_service(
        unit_src,
        systemd_dir=tmp_path / "systemd",
        unit_name="platform-secrets.service",
        systemctl_fn=lambda *_: None,
    )
    assert ok is True
    dst = tmp_path / "systemd" / "platform-secrets.service"
    assert dst.is_file()
    content = dst.read_text()
    assert "Type=oneshot" in content
    assert "RemainAfterExit=yes" in content
    assert dst.stat().st_mode & 0o777 == 0o644


def test_install_service_missing_unit_fails(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_install_service_missing_unit_fails — DevPlan 118 E migration unit test
    """install_service: unit source missing → False (fail-fast)."""
    assert ps.install_service(tmp_path / "no-unit.service") is False


def test_shipped_unit_requires_mounts_for_etc_platform() -> None:
    # 🧪 TRAP[TEST] · 2026-08-13 · DevPlan 162 W3-1 (b) — RequiresMountsFor=/etc/platform
    # · Scenario: shipped unit (core/modules/platform-secrets/platform-secrets.service) — AGE-ключ
    # ·   (/etc/platform/age-key.txt, EnvironmentFile) смонтирован до старта сервиса; TRAP[DECISION]
    # ·   фиксирует отказ ослаблять Requires→Wants (invisible failure «секреты до docker»)
    # · Last fail: N/A (новый тест 162 W3-1)
    # · Remove if: unit-контракт boot-ordering меняется
    """DevPlan 162 W3-1 (b): shipped unit requires /etc/platform mounted (AGE key) before start."""
    unit_path = Path(ps.__file__).resolve().parent / "platform-secrets.service"
    assert unit_path.is_file(), f"shipped unit missing: {unit_path}"
    content = unit_path.read_text(encoding="utf-8")
    assert "RequiresMountsFor=/etc/platform" in content, (
        "AGE-ключ лежит в /etc/platform — юнит обязан требовать его mount (DevPlan 162 W3-1 b)"
    )
    assert "Type=oneshot" in content and "RemainAfterExit=yes" in content, (
        "oneshot + RemainAfterExit — юнит стабильно active (exited) после первого прогона"
    )
    assert "RequiredBy=docker.service" in content, "Requires-связь docker.service сохраняется (не Wants)"


# endregion TEST_install_service


# region TEST_run
def test_run_full_pipeline_dry(
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_full_pipeline_dry — DevPlan 118 E migration unit test
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """run(): full pipeline (prereqs+dirs+service) with tmp paths → True (DI, 0 патчей)."""
    caplog.set_level(logging.INFO)
    platform_root = tmp_path / "platform"
    env = {"PLATFORM_ROOT": str(platform_root), "AGE_SECRET_KEY": "secret"}
    enc = platform_root / "secrets" / "secrets.enc.yaml"
    enc.parent.mkdir(parents=True)
    enc.write_text("x\n")

    assert ps.run(env, age_key_path=tmp_path / "etc" / "age-key.txt", install_fn=lambda _: True) is True
    assert (tmp_path / "etc" / "age-key.txt").read_text().startswith("AGE_SECRET_KEY=secret")


# endregion TEST_run
