# GREP_SUMMARY: test secret-writers-mode REF-0007 atomic-writer mode-0600 secrets-env litellm-keys env-platform-0640 openssl stdin argv no-world-readable-window
# STRUCTURE: ▶ atomic_write(mode=0600) → ◇ temp 0600 НА МОМЕНТ replace (replace_fn DI) → ⎋ final 0600 ── ▶ secrets_manager.ensure_secrets → ⎋ secrets.env 0600 ── ▶ persist_project_key → ⎋ JSON 0600 ── ▶ gen_env_platform --output → ⎋ .env.platform 0640 ── ▶ hash_apr1 → ⎋ password в stdin, НЕ в argv
# region MODULE_CONTRACT
## @purpose  REF-0007 (11-DevPlan Волна 1): mode-от-создания тесты писателей секретов.
##            Канон atomic_writer применяет mode ДО os.replace (нет окна world-readable);
##            этот файл фиксирует контракт для всех точек свипа: secrets.env (0600),
##            litellm-project-keys.json (0600), .env.platform (0640), openssl -stdin (argv).
## @scope    Pure unit tests — tmp_path, DI/monkeypatch без реальных ssh/sops. openssl —
##            локальный бинарник, вызывается только через mock subprocess.run (детерминизм).
## @invariants
##   - R2-honesty: каждый assert проверяет свойство, которое МОЖЕТ упасть при регрессии
##     (chmod-после-записи вернул бы окно — replace_fn-перехват это ловит)
##   - Файлы — только tmp_path (zero hardcode)
## @rationale Карточка REF-0007 «Tests required»: тест mode=0600 от создания для writer'ов +
##            argv-тесты транспорта. Один файл = один аудит-периметр свипа Волны 1.
## @changes   2026-08-24 | Created (REF-0007)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
from contextlib import ExitStack
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
import yaml
from _conftest.ldd import ldd_trajectory

from core.internal.bootstrap.lifecycle import secrets_manager as sm
from core.internal.llm import key_provisioner as kp
from core.internal.scaffold import gen_env_platform as gep
from core.internal.shared.atomic_writer import atomic_write

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit


# ═══════════════════════════════════════════════════════════════════
# atomic_write: mode=0600 ОТ СОЗДАНИЯ (не chmod-после)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_atomic_write_mode_at_replace_time
# 🧪 TRAP[TEST] · NEGATIVE (R5) · SEC-0015 · mode применяется к temp ДО os.replace
# · Scenario: umask 000 → если writer полагался на chmod ПОСЛЕ записи, на момент replace
# ·   temp был бы 0666; replace_fn перехватывает st_mode temp-файла В МОМЕНТ атомарной смены
# · Last fail: 2026-08-24 — plain open("w")+chmod(0600)-после в 6 местах свипа REF-0007
# · Remove if: канон atomic_writer заменён другим механизмом записи
@ldd_trajectory
def test_atomic_write_mode_at_replace_time(tmp_path: Path) -> None:
    """atomic_write(mode=0o600): temp имеет 0600 УЖЕ на момент os.replace (нет окна)."""
    # Агрессивный umask: любой файл, созданный БЕЗ явного mode, был бы world-readable
    real_umask = os.umask(0o000)
    try:
        target = tmp_path / "secret.env"
        observed_modes: list[int] = []

        def tracking_replace(src: str, dst: str) -> None:
            observed_modes.append(Path(src).stat().st_mode & 0o777)
            Path(src).replace(dst)

        atomic_write(target, "KEY=value\n", mode=0o600, replace_fn=tracking_replace)

        logger.info("[IMP:9][test][atomic] temp mode at replace time: 0%03o", observed_modes[0])
        assert observed_modes == [0o600], (
            f"SEC-0015 FAIL: temp file had mode 0{observed_modes[0]:o} at replace time — "
            "world-readable window (chmod-after-write regression)"
        )
        assert (target.stat().st_mode & 0o777) == 0o600, "final file must be 0600"
    finally:
        os.umask(real_umask)
    logger.info("[IMP:9][test][atomic] mode=0600-from-creation contract OK")


# endregion FUNC_test_atomic_write_mode_at_replace_time


# ═══════════════════════════════════════════════════════════════════
# secrets_manager.ensure_secrets → secrets.env 0600
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_ensure_secrets_env_mode_0600
# 🧪 TRAP[TEST] · NEGATIVE (R5) · SEC-0015 · secrets.env идёт через atomic_write (0600 на commit)
# · Scenario: umask 000 + перехват os.replace в atomic_writer → temp-файл 0600 В МОМЕНТ
# ·   атомарной смены; финальный файл 0600; legacy-файл 0644 ЗАТЯГИВАЕТСЯ (tightening);
# ·   регрессия на Path.replace/plain-open не прошла бы через atomic_writer → observed пуст
# · Last fail: 2026-08-24 — plain open("w") на .env.tmp + chmod после записи
# · Remove if: путь записи secrets.env меняется
@ldd_trajectory
def test_ensure_secrets_env_mode_0600(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ensure_secrets: свежий и legacy secrets.env → 0600, запись через atomic_write."""
    caplog.set_level(logging.DEBUG)
    from core.internal.shared import atomic_writer as aw

    observed_modes: list[int] = []
    real_replace = aw.os.replace

    def tracking_replace(src: str, dst: str) -> None:
        observed_modes.append(Path(src).stat().st_mode & 0o777)
        real_replace(src, dst)

    monkeypatch.setattr(aw.os, "replace", tracking_replace)

    manifest_secrets = [{"name": "LITELLM_MASTER_KEY", "gen_command": "echo sk-test", "tier": "generated"}]
    # GENERATED smoke-env инжектирует LITELLM_MASTER_KEY в процесс pytest — чистим на сценарий
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    # umask 000: plain open("w") дал бы world-readable — только mode-от-создания спасает
    real_umask = os.umask(0o000)
    try:
        for scenario in ("fresh", "legacy-loose"):
            secrets_env = tmp_path / f"secrets-{scenario}.env"
            if scenario == "legacy-loose":
                secrets_env.write_text("GHCR_PULL_TOKEN=operator-token\n", encoding="utf-8")
                secrets_env.chmod(0o644)  # legacy world-readable — должно быть затянуто
                observed_modes.clear()

            with ExitStack() as stack:
                stack.enter_context(patch.object(sm, "_read_manifest", return_value=manifest_secrets))
                stack.enter_context(patch.object(sm, "_generate_secret", return_value="generated-value-x"))
                stack.enter_context(patch.object(sm, "_ensure_htpasswd", return_value=False))
                stack.enter_context(patch.object(sm, "_ensure_master_credentials", return_value=None))
                stack.enter_context(patch.object(sm, "_ensure_derived_passwords", return_value=None))
                generated = sm.ensure_secrets(
                    manifest_path="/fake/manifest.yaml",
                    secrets_env=str(secrets_env),
                    persist_to_sops=False,
                )

            assert generated == ["LITELLM_MASTER_KEY"]
            mode = secrets_env.stat().st_mode & 0o777
            content = secrets_env.read_text(encoding="utf-8")
            logger.info(
                "[IMP:9][test][secrets-manager] scenario=%s mode=0%03o replace_observed=%s",
                scenario,
                mode,
                bool(observed_modes),
            )
            assert observed_modes == [0o600], (
                f"REF-0007 FAIL ({scenario}): temp at replace time = "
                f"{[oct(m) for m in observed_modes]} — writer bypassed atomic_write or left a window"
            )
            assert mode == 0o600, f"REF-0007 FAIL ({scenario}): secrets.env mode is 0{mode:o}, expected 0600"
            assert "LITELLM_MASTER_KEY=generated-value-x" in content
            if scenario == "legacy-loose":
                assert "GHCR_PULL_TOKEN=operator-token" in content, "операторский секрет должен сохраниться"

            monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    finally:
        os.umask(real_umask)
    logger.info("[IMP:9][test][secrets-manager] secrets.env 0600-from-creation OK (fresh + legacy tightening)")


# endregion FUNC_test_ensure_secrets_env_mode_0600


# ═══════════════════════════════════════════════════════════════════
# key_provisioner.persist_project_key → JSON 0600
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_persist_project_key_mode_0600
# 🧪 TRAP[TEST] · Regression · SEC-0017 · litellm-keys JSON пишется 0600 от создания
# · Scenario: persist_project_key в tmp_path → plaintext JSON с виртуальными ключами — 0600
# · Last fail: 2026-08-24 — open("w") + chmod(0600) ПОСЛЕ записи (окно world-readable)
# · Remove if: хранилище ключей переезжает (Wave B age-encrypt)
@ldd_trajectory
def test_persist_project_key_mode_0600(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """persist_project_key: JSON store создаётся сразу 0600 (atomic_write_json)."""
    caplog.set_level(logging.DEBUG)
    persist_path = tmp_path / "state" / "litellm-project-keys.json"

    kp.persist_project_key("myapp", "sk-vk-SECRETVALUE", persist_path=persist_path)

    mode = persist_path.stat().st_mode & 0o777
    store = json.loads(persist_path.read_text(encoding="utf-8"))
    logger.info("[IMP:9][test][key-provisioner] store mode=0%03o entries=%d", mode, len(store))
    assert mode == 0o600, f"REF-0007 FAIL: keys JSON mode is 0{mode:o}, expected 0600"
    assert store == {"myapp": "sk-vk-SECRETVALUE"}
    logger.info("[IMP:9][test][key-provisioner] litellm-keys 0600-from-creation OK")


# endregion FUNC_test_persist_project_key_mode_0600


# ═══════════════════════════════════════════════════════════════════
# gen_env_platform --output → .env.platform 0640
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_gen_env_platform_output_mode_0640
# 🧪 TRAP[TEST] · Regression · SEC-0017 · .env.platform (DSN с паролем БД) — 0640
# · Scenario: main(--output) → файл 0640 (не umask-зависимый 0644); chown ci-deploy
# ·   отсутствует на dev-машине → best-effort skip, rc остаётся 0
# · Last fail: 2026-08-24 — write_text() создавал 0644 с паролем БД в DSN
# · Remove if: ownership-модель .env.platform меняется
@ldd_trajectory
def test_gen_env_platform_output_mode_0640(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """main(--output): .env.platform создаётся 0640 через atomic_write_text."""
    caplog.set_level(logging.DEBUG)
    yaml_path = tmp_path / "platform-env.yaml"
    data = {
        "profiles": ["test"],
        "provides": {
            "test": {"host": "test-host", "dsn_template": "postgresql://${NAME}_user:dbpass@test-host:6432/${NAME}_db"}
        },
        "proxy": {},
    }
    with Path(str(yaml_path)).open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    out_file = tmp_path / "proj" / ".env.platform"

    rc = gep.main(["--yaml", str(yaml_path), "--domain", "example.test", "--name", "myapp", "--output", str(out_file)])

    mode = out_file.stat().st_mode & 0o777
    content = out_file.read_text(encoding="utf-8")
    logger.info("[IMP:9][test][env-platform] rc=%s mode=0%03o", rc, mode)
    assert rc == 0
    assert mode == 0o640, f"REF-0007 FAIL: .env.platform mode is 0{mode:o}, expected 0640 (пароль БД в DSN)"
    assert "PLATFORM_TEST_DSN=postgresql://myapp_user:dbpass@test-host:6432/myapp_db" in content
    logger.info("[IMP:9][test][env-platform] .env.platform 0640-from-creation OK")


# endregion FUNC_test_gen_env_platform_output_mode_0640


# ═══════════════════════════════════════════════════════════════════
# crypto.hash_apr1: пароль через stdin (-stdin), НЕ в argv
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_hash_apr1_password_via_stdin_not_argv
# 🧪 TRAP[TEST] · NEGATIVE (R5) · SEC-0003/DATA-1004 · пароль НЕ в argv openssl
# · Scenario: hash_apr1(password, salt) → cmd содержит -stdin, input=password;
# ·   значение пароля ОТСУТСТВУЕТ в argv (было: openssl passwd -apr1 <password>)
# · Last fail: 2026-08-24 (REF-0007) — пароль светился в /proc/<pid>/cmdline
# · Remove if: APR1-хэширование мигрирует с openssl
@ldd_trajectory
def test_hash_apr1_password_via_stdin_not_argv(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """hash_apr1: password уходит в stdin, argv содержит -stdin и НЕ содержит пароль."""
    caplog.set_level(logging.DEBUG)
    captured: dict[str, object] = {}

    class _CompletedProc:
        returncode = 0
        stdout = "$apr1$fixedsalt$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        stderr = ""

    def fake_run(cmd: list[str], **kwargs: object) -> _CompletedProc:
        captured["cmd"] = list(cmd)
        captured["input"] = kwargs.get("input")
        captured["text"] = kwargs.get("text")
        return _CompletedProc()

    from core.internal.shared import crypto

    monkeypatch.setattr(crypto.subprocess, "run", cast("object", fake_run))

    result = crypto.hash_apr1("SUPERSECRETPW", salt="fixedsalt")

    assert result == "$apr1$fixedsalt$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    cmd = cast("list[str]", captured["cmd"])
    logger.info("[IMP:9][test][crypto] cmd=%s input_present=%s", cmd, bool(captured["input"]))
    assert "SUPERSECRETPW" not in cmd, "REF-0007 FAIL: password leaked into openssl argv"
    assert "-stdin" in cmd, "openssl must read password via stdin"
    assert captured["input"] == "SUPERSECRETPW", "password must be delivered via stdin payload"
    assert captured["text"] is True


# endregion FUNC_test_hash_apr1_password_via_stdin_not_argv


# region FUNC_test_hash_apr1_real_openssl_smoke
# 🧪 TRAP[TEST] · Smoke · -stdin не меняет результат хэширования (реальный openssl)
# · Scenario: фиксированный salt → детерминированный $apr1$salt$... хэш (инвариант 2 crypto.py)
# · Last fail: N/A (smoke)
# · Remove if: openssl недоступен в окружении навсегда (смена toolchain)
def test_hash_apr1_real_openssl_smoke(caplog: pytest.LogCaptureFixture) -> None:
    """Реальный openssl: -stdin канал даёт корректный детерминированный APR1-хэш."""
    caplog.set_level(logging.DEBUG)
    from core.internal.shared.crypto import hash_apr1

    h1 = hash_apr1("smoke-password", salt="abcd1234")
    h2 = hash_apr1("smoke-password", salt="abcd1234")
    logger.info("[IMP:9][test][crypto-smoke] hash=%s deterministic=%s", (h1 or "")[:14], h1 == h2)
    assert h1 is not None, "openssl passwd -apr1 -stdin failed"
    assert h1.startswith("$apr1$abcd1234$"), f"unexpected APR1 format: {h1!r}"
    assert h1 == h2, "fixed-salt hash must be idempotent (stdin transport must not change semantics)"


# endregion FUNC_test_hash_apr1_real_openssl_smoke


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
