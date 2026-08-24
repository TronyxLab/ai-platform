# 🧪 TRAP[TEST] · REF-0013 · empty-parse→fatal + TEST-07 stderr-redaction
# GREP_SUMMARY: test-secrets-decrypt-failfast, empty-parse, fatal, non-flat-yaml, zero-keys, stderr-redaction, age-key, sops, TEST-07
# STRUCTURE: ▶ main() [patched detect/decrypt] → ◇ blank payload ⚡10 → ◇ JSON payload 0 keys ⚡10 → ◇ flat payload → ⎋ rc=0 + file written → ▶ fake-sops-on-PATH leak → ◇ redaction markers present, key/path absent → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for REF-0013 fail-fast guards in core/internal/secrets/decrypt_secrets.py:
##           (1) непустой enc + пустой decrypted payload / 0 распарсенных KEY → PlatformFatalError,
##           secrets.env НЕ пишется («decrypted successfully» при пустом результате невозможен);
##           (2) TEST-07 stderr-redaction: значение AGE-ключа и путь temp-ключа не попадают
##           в stderr/логи/сообщение исключения при сбое sops.
## @scope    Pure unit tests — subprocess для sops эмулируется (patched decrypt_sops_file /
##           fake-sops-скрипт на PATH); реальный dd-wipe temp-ключа допустим (крошечный файл).
## @invariants
##   - Fatal-кейсы возвращают exit_code 10 (PlatformFatalError) и НЕ создают output-файл
##   - Green-кейс пишет файл и возвращает 0 (контроль, что guard не ложноположительный)
##   - Redaction: полный ключ и префикс пути /dev/shm/platform-age-key- отсутствуют в
##     исключении и caplog; маркеры <redacted-age-key>/<redacted-age-key-path> присутствуют
## @rationale REF-0013: φ4 рапортовала успех с пустым результатом (повторение P0-класса
##            2026-07-23 уровнем глубже); TEST-07 — непротестированный контракт redaction.
# endregion MODULE_CONTRACT

import logging
import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from core.internal.secrets import decrypt_secrets as decrypt_mod
from core.internal.shared.exceptions import PlatformFatalError
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# Уникальный тестовый ключ длиннее маски (_AGE_KEY_PREVIEW_LEN = 8) — утечка проверяется
# по полному значению (hardcoded ci-test-значение, не секрет).
_TEST_AGE_KEY = "AGE-SECRET-KEY-ref0013-test-key-0123456789abcdef"


# ═══════════════════════════════════════════════════════════════════
# Fixtures/helpers
# ═══════════════════════════════════════════════════════════════════


def _run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, decrypted_payload: str) -> tuple[int, Path]:
    """Run decrypt main() with patched detect/decrypt; return (exit_code, output_path)."""
    enc_path = tmp_path / "secrets.enc.yaml"
    enc_path.write_text("dummy: encrypted\n", encoding="utf-8")
    out_path = tmp_path / "run" / "secrets.env"
    monkeypatch.setattr(sys, "argv", ["decrypt_secrets.py", str(enc_path), str(out_path)])
    with (
        mock.patch.object(decrypt_mod, "detect_age_key", return_value=_TEST_AGE_KEY),
        mock.patch.object(decrypt_mod, "decrypt_sops_file", return_value=decrypted_payload),
    ):
        rc = decrypt_mod.main()
    return rc, out_path


# ═══════════════════════════════════════════════════════════════════
# Tests: empty-parse → PlatformFatalError (REF-0013)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_blank_payload_fatal
## @purpose  Guard #1: sops вернул пустой payload → PlatformFatalError, output НЕ создан.
## @io       ⇥ monkeypatch, tmp_path, caplog → ⎋ None (asserts)
@ldd_trajectory
def test_blank_payload_fatal(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Empty decrypted payload → exit 10, empty secrets.env never written."""
    rc, out_path = _run_main(monkeypatch, tmp_path, decrypted_payload="")

    assert rc == PlatformFatalError.exit_code == 10, f"Expected exit 10 for blank payload, got {rc}"
    assert not out_path.exists(), f"Empty secrets.env must NOT be written, found: {out_path}"
    imp10 = any("[IMP:10]" in r.message and "EMPTY payload" in r.message for r in caplog.records)
    assert imp10, "Missing IMP:10 fail-fast log for blank payload"
    logger.info("[IMP:9][test_blank_payload_fatal] PASS: blank payload → fatal, no output file")


# endregion FUNC_test_blank_payload_fatal


# region FUNC_test_zero_parsed_keys_fatal
## @purpose  Guard #2: непустой payload без единого flat KEY:value (JSON/вложенный YAML) →
##           PlatformFatalError, output НЕ создан. Точный вход исходного бага REF-0013:
##           _yaml_to_env молча терял non-flat YAML и писал пустой secrets.env с «success».
## @io       ⇥ monkeypatch, tmp_path, caplog → ⎋ None (asserts)
@pytest.mark.parametrize(
    "payload",
    [
        '{"POSTGRES_PASSWORD": "pg-pass", "nested": {"a": 1}}',  # JSON-формат (не YAML key:value)
        "- item-a\n- item-b\n",  # YAML list
        "plain text line one\nanother line without mapping syntax\n",  # произвольный текст
    ],
    ids=["json", "yaml-list", "plain-text"],
)
@ldd_trajectory
def test_zero_parsed_keys_fatal(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str
) -> None:
    """Non-flat payload yielding 0 parsable keys → exit 10, no output file."""
    rc, out_path = _run_main(monkeypatch, tmp_path, decrypted_payload=payload)

    assert rc == 10, f"Expected exit 10 for payload {payload!r}, got {rc}"
    assert not out_path.exists(), f"Empty secrets.env must NOT be written for payload {payload!r}"
    imp10 = any("[IMP:10]" in r.message and "0 parsable" in r.message for r in caplog.records)
    assert imp10, "Missing IMP:10 fail-fast log for 0-parsable-keys payload"
    logger.info("[IMP:9][test_zero_parsed_keys_fatal] PASS: %d-byte unparsable payload → fatal", len(payload))


# endregion FUNC_test_zero_parsed_keys_fatal


# region FUNC_test_flat_payload_success_control
## @purpose  Контроль green-пути: плоский KEY:value payload проходит guard'ы, пишет файл, rc=0 —
##           доказывает, что fail-fast не ложноположительный.
## @io       ⇥ monkeypatch, tmp_path → ⎋ None (asserts)
@ldd_trajectory
def test_flat_payload_success_control(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Flat KEY:value payload passes guards, writes secrets.env, exit 0."""
    payload = "POSTGRES_PASSWORD: pg-pass\nGHCR_PULL_TOKEN: gh-token\n"
    rc, out_path = _run_main(monkeypatch, tmp_path, decrypted_payload=payload)

    assert rc == 0, f"Flat payload must succeed, got rc={rc}"
    content = out_path.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD='pg-pass'" in content, f"Written env missing POSTGRES_PASSWORD: {content!r}"
    logger.info("[IMP:9][test_flat_payload_success_control] PASS: flat payload → rc=0 + file written")


# endregion FUNC_test_flat_payload_success_control


# ═══════════════════════════════════════════════════════════════════
# Tests: TEST-07 stderr-redaction
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_stderr_redaction_on_sops_failure
## @purpose  TEST-07: fake-sops печатает ЗНАЧЕНИЕ age-ключа (из SOPS_AGE_KEY_FILE) и путь
##           temp-ключа в stderr при ошибке → ни ключ, ни /dev/shm/platform-age-key-* путь
##           не появляются в исключении/caplog; redaction-маркеры присутствуют.
## @io       ⇥ caplog, tmp_path, monkeypatch (PATH) → ⎋ None (asserts)
@ldd_trajectory
def test_stderr_redaction_on_sops_failure(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Secrets from failing sops stderr are redacted before logs/exception (TEST-07)."""
    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    fake_sops = fake_dir / "sops"
    # Fake sops leaks BOTH the key material and the temp-key path into stderr (worst case).
    script = (
        "#!/bin/sh\n"
        'echo "fatal: leaked key $(cat "$SOPS_AGE_KEY_FILE")" >&2\n'
        'echo "fatal: temp at $SOPS_AGE_KEY_FILE" >&2\n'
        "exit 1\n"
    )
    fake_sops.write_text(script, encoding="utf-8")
    fake_sops.chmod(fake_sops.stat().st_mode | stat.S_IXUSR)

    enc_path = tmp_path / "secrets.enc.yaml"
    enc_path.write_text("dummy: encrypted\n", encoding="utf-8")

    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    caplog.set_level(logging.DEBUG)

    with pytest.raises(PlatformFatalError) as excinfo:
        decrypt_mod.decrypt_sops_file(_TEST_AGE_KEY, str(enc_path))

    rendered = str(excinfo.value)
    log_text = caplog.text

    # ── Полный ключ НЕ утёк ни в исключение, ни в логи ──
    assert _TEST_AGE_KEY not in rendered, f"Full AGE key leaked into exception message: {rendered!r}"
    assert _TEST_AGE_KEY not in log_text, "Full AGE key leaked into logs"
    # ── Путь temp-ключа НЕ утёк (ищем канонический префикс из /dev/shm) ──
    assert "/dev/shm/platform-age-key-" not in rendered, "Temp key path leaked into exception message"
    assert "/dev/shm/platform-age-key-" not in log_text, "Temp key path leaked into logs"
    # ── Redaction-маркеры ДОЛЖНЫ присутствовать (доказательство санитизации, не молчания) ──
    assert "<redacted-age-key>" in rendered, f"Expected redaction marker in message: {rendered!r}"
    assert "<redacted-age-key-path>" in rendered, f"Expected path-redaction marker in message: {rendered!r}"

    logger.info("[IMP:9][test_stderr_redaction_on_sops_failure] ✅ TEST-07 PASS: key+path redacted, markers present")


# endregion FUNC_test_stderr_redaction_on_sops_failure
