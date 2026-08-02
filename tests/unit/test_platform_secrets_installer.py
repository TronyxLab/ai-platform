#!/usr/bin/env python3
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


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point PATH-константы на tmp_path (zero /etc hardcoding)."""
    monkeypatch.setattr(ps, "AGE_KEY_PATH", tmp_path / "etc" / "platform" / "age-key.txt")
    monkeypatch.setattr(ps, "SYSTEMD_DIR", tmp_path / "etc" / "systemd" / "system")
    monkeypatch.setattr(ps, "NODE_SECRETS_DIR", tmp_path / "opt" / "node-configs" / "secrets")
    monkeypatch.setattr(ps, "UNIT_NAME", "platform-secrets.service")


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


# endregion


# region TEST_ensure_secrets_enc
def test_ensure_secrets_enc_found(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_secrets_enc_found — DevPlan 118 E migration unit test
    """ensure_secrets_enc: secrets.enc.yaml exists → ok."""
    enc = tmp_path / "secrets.enc.yaml"
    enc.write_text("enc: data\n")
    ok, msg = ps.ensure_secrets_enc(enc)
    assert ok is True
    assert msg == "ok"


def test_ensure_secrets_enc_symlink_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_secrets_enc_symlink_fallback — DevPlan 118 E migration unit test
    """ensure_secrets_enc: missing + node-configs/*.enc.yaml → symlink created."""
    node_dir = tmp_path / "node-secrets"
    node_dir.mkdir()
    (node_dir / "context.enc.yaml").write_text("enc: data\n")
    monkeypatch.setattr(ps, "NODE_SECRETS_DIR", node_dir)

    enc = tmp_path / "secrets" / "secrets.enc.yaml"
    ok, msg = ps.ensure_secrets_enc(enc)
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


# endregion


# region TEST_check_prerequisites
def test_check_prerequisites_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_prerequisites_ok — DevPlan 118 E migration unit test
    """check_prerequisites: age-key + secrets-enc → True."""
    monkeypatch.setattr(ps, "AGE_KEY_PATH", tmp_path / "age-key.txt")
    enc = tmp_path / "secrets" / "secrets.enc.yaml"
    enc.parent.mkdir(parents=True)
    enc.write_text("x\n")
    env = {"AGE_SECRET_KEY": "secret"}
    assert ps.check_prerequisites(tmp_path, env) is True


def test_check_prerequisites_abort_missing_key(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_prerequisites_abort_missing_key — DevPlan 118 E migration unit test
    """check_prerequisites: no age key + no env → False (abort)."""
    assert ps.check_prerequisites(tmp_path, {}) is False


# endregion


# region TEST_ensure_platform_dirs
def test_ensure_platform_dirs_creates_2775(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_ensure_platform_dirs_creates_2775 — DevPlan 118 E migration unit test
    """ensure_platform_dirs: platform_root/prometheus-targets/secrets created with setgid 2775."""
    ok = ps.ensure_platform_dirs(tmp_path / "platform")
    assert ok is True
    for d in (tmp_path / "platform", tmp_path / "platform" / "prometheus-targets", tmp_path / "platform" / "secrets"):
        assert d.is_dir(), f"{d} must exist"
        assert d.stat().st_mode & 0o2775 == 0o2775, f"{d} must be setgid 2775 (got {oct(d.stat().st_mode)})"


# endregion


# region TEST_install_service
def test_install_service_unit_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_install_service_unit_content — DevPlan 118 E migration unit test
    """install_service: unit copied to SYSTEMD_DIR with Before=docker.service (oneshot contract)."""
    unit_src = tmp_path / "platform-secrets.service"
    unit_src.write_text(
        "[Unit]\nDescription=platform-secrets\nBefore=docker.service\n[Service]\nType=oneshot\nRemainAfterExit=yes\n"
    )
    monkeypatch.setattr(ps, "SYSTEMD_DIR", tmp_path / "systemd")
    monkeypatch.setattr(ps, "UNIT_NAME", "platform-secrets.service")
    monkeypatch.setattr(ps, "_systemctl", lambda *a: None)

    ok = ps.install_service(unit_src)
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


# endregion


# region TEST_run
def test_run_full_pipeline_dry(
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_full_pipeline_dry — DevPlan 118 E migration unit test
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """run(): full pipeline (prereqs+dirs+service) with tmp paths → True (после-тест E7)."""
    caplog.set_level(logging.INFO)
    platform_root = tmp_path / "platform"
    env = {"PLATFORM_ROOT": str(platform_root), "AGE_SECRET_KEY": "secret"}
    monkeypatch.setattr(ps, "AGE_KEY_PATH", tmp_path / "etc" / "age-key.txt")
    enc = platform_root / "secrets" / "secrets.enc.yaml"
    enc.parent.mkdir(parents=True)
    enc.write_text("x\n")
    unit = tmp_path / "platform-secrets.service"
    unit.write_text("[Service]\nType=oneshot\nRemainAfterExit=yes\n")
    monkeypatch.setattr(ps, "install_service", lambda src: True)  # systemd mocked (no root in tests)

    assert ps.run(env) is True
    assert (tmp_path / "etc" / "age-key.txt").read_text().startswith("AGE_SECRET_KEY=secret")


# endregion
