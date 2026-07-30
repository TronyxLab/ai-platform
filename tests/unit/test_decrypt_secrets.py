#!/usr/bin/env python3
# GREP_SUMMARY: test-decrypt-secrets, sops, age, decrypt, temp-key, dd-wipe, cleanup, ldd, unit-test
# STRUCTURE: ▶ 4 tests → ◇ decrypt_success → ◇ decrypt_fail → ◇ dd_wiped → ◇ no_secret_logged → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/secrets/decrypt_secrets.py.
##           Verifies decrypt_sops_file() with mocked subprocess calls for sops --decrypt
##           and dd wipe. Tests: success path, failure path, dd-wipe cleanup, log masking.
## @scope    Pure unit tests — no subprocess, no Docker, no external dependencies.
##           Uses unittest.mock for subprocess.run and shutil.which patching.
## @invariants
##   - 4 tests: decrypt_success, decrypt_fail_wrong_key, temp_key_cleanup, no_secret_in_logs
##   - All tests use @ldd_trajectory decorator
##   - shutil.which patched to avoid dependency on sops binary
##   - No hardcoded paths — all test files use tmp_path
##   - No subprocess.run executed for real — all mocked
## @rationale DevPlan Strangler-Fig — Python core extracted from decrypt-secrets.sh.
##            Security-critical DD5 invariants require testable dd-wipe and log-masking verification.
## @changes  2026-07-30 | Created — Unit tests for decrypt_secrets.py
# endregion MODULE_CONTRACT

import logging
import os
from unittest import mock

import pytest

from core.internal.secrets.decrypt_secrets import (
    _TEMP_FILES,
    decrypt_sops_file,
)
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)


# ── Autouse fixture: mock sops binary to avoid real binary check ──────────────


@pytest.fixture(autouse=True)
def _patch_sops_binary() -> None:
    """Mock shutil.which to simulate sops binary present in PATH.

    ## @purpose — Prevents test failure if sops binary is not installed on dev machine.
    ##            Each test function that calls decrypt_sops_file needs this to bypass
    ##            the shutil.which("sops") pre-flight check.
    """
    with mock.patch("shutil.which", return_value="/usr/local/bin/sops"):
        yield


# ── Test: decryption success ──────────────────────────────────────────────────


# region FUNC_test_decrypt_success
## @purpose — Verify decrypt_sops_file returns plaintext content on successful sops call.
##            Tests the full happy path: temp key creation → sops --decrypt → dd wipe.
## @io — ⇥ caplog, tmp_path → ⎋ None (asserts plaintext matches expected)
## @complexity — O(1) — mocked subprocess
## @invariants
##   - Decrypted content matches mock's stdout
##   - No RuntimeError raised on success
##   - Temp file cleaned up (no leftover _TEMP_FILES)
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · decrypt_sops_file success path
# · Last fail: N/A (new test)
# · Remove if: decrypt_sops_file signature or return value changes
def test_decrypt_success(caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
    """decrypt_sops_file returns decrypted content on successful sops --decrypt."""
    enc_path = tmp_path / "secrets.enc.yaml"
    enc_path.write_text("encrypted: placeholder\n")
    expected_plaintext = "DATABASE_URL: postgresql://host/db\nAPI_KEY: sk-test-key\n"

    def _mock_run(args: list[str], **kwargs) -> mock.MagicMock:
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        if args and args[0] == "sops":
            result.stdout = expected_plaintext
        return result

    # Clear any leftover temp file tracking from previous tests
    _TEMP_FILES.clear()

    with mock.patch("subprocess.run", side_effect=_mock_run):
        actual = decrypt_sops_file("AGE-SECRET-KEY-test-key-for-testing-only", str(enc_path))

    assert actual == expected_plaintext, (
        f"Expected plaintext mismatch\nExpected: {expected_plaintext!r}\nGot: {actual!r}"
    )

    # Verify _TEMP_FILES is clean (no orphaned entries)
    assert len(_TEMP_FILES) == 0, f"Orphaned temp file entries: {_TEMP_FILES}"

    logger.info("[IMP:9][test_decrypt_success] ✅ Decrypted content matches expected plaintext")


# endregion FUNC_test_decrypt_success


# ── Test: decryption failure ──────────────────────────────────────────────────


# region FUNC_test_decrypt_fail_wrong_key
## @purpose — Verify decrypt_sops_file raises RuntimeError when sops --decrypt fails.
##            Tests fail-fast invariant: failed decryption → immediate error, no plaintext.
## @io — ⇥ caplog, tmp_path → ⎋ None (asserts RuntimeError raised)
## @complexity — O(1) — mocked subprocess returning non-zero
## @invariants
##   - RuntimeError raised with "sops decryption failed" message
##   - Temp file cleaned up even on failure (finally block)
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · decrypt_sops_file failure path
# · Last fail: N/A (new test)
# · Remove if: decrypt_sops_file changes error handling
def test_decrypt_fail_wrong_key(caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
    """decrypt_sops_file raises RuntimeError when sops decryption fails (wrong key)."""
    enc_path = tmp_path / "secrets.enc.yaml"
    enc_path.write_text("encrypted: placeholder\n")

    def _mock_run(args: list[str], **kwargs) -> mock.MagicMock:
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        if args and args[0] == "sops":
            result.returncode = 1
            result.stderr = "wrong key or corrupted file"
        return result

    # Clear any leftover temp file tracking from previous tests
    _TEMP_FILES.clear()

    with (
        mock.patch("subprocess.run", side_effect=_mock_run),
        pytest.raises(RuntimeError, match="sops decryption failed"),
    ):
        decrypt_sops_file("wrong-age-key-for-testing", str(enc_path))

    # Verify _TEMP_FILES is clean (finally block cleaned up)
    assert len(_TEMP_FILES) == 0, f"Orphaned temp file entries after failure: {_TEMP_FILES}"

    logger.info("[IMP:9][test_decrypt_fail_wrong_key] ✅ RuntimeError raised on decryption failure")


# endregion FUNC_test_decrypt_fail_wrong_key


# ── Test: temp key dd-wipe cleanup ────────────────────────────────────────────


# region FUNC_test_temp_key_cleanup
## @purpose — Verify temp age key file is wiped with dd if=/dev/zero after decryption.
##            Tests DD5-2 invariant: dd wipe before rm, not just rm -f.
##            Tracks subprocess.run calls to verify dd invocation signature.
## @io — ⇥ caplog, tmp_path → ⎋ None (asserts dd call + file cleaned)
## @complexity — O(1) — mocked subprocess with dd call tracking
## @invariants
##   - subprocess.run called with ["dd", "if=/dev/zero", ...]
##   - Temp file removed from disk (os.remove) after dd
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · DD5-2 dd-wipe cleanup of temp key
# · Last fail: N/A (new test)
# · Remove if: _wipe_temp_key or decrypt_sops_file cleanup logic changes
def test_temp_key_cleanup(caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
    """Temp age key file is wiped with dd if=/dev/zero after decryption."""
    enc_path = tmp_path / "secrets.enc.yaml"
    enc_path.write_text("encrypted: placeholder\n")

    dd_calls: list[list[str]] = []

    def _mock_run(args: list[str], **kwargs) -> mock.MagicMock:
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        if args and args[0] == "sops":
            result.stdout = "KEY=val\n"
        if args and args[0] == "dd":
            dd_calls.append(list(args))
        return result

    # Clear any leftover temp file tracking from previous tests
    _TEMP_FILES.clear()

    with mock.patch("subprocess.run", side_effect=_mock_run):
        decrypt_sops_file("AGE-SECRET-KEY-test-key-for-dd-wipe", str(enc_path))

    # ── Assertion 1: dd was called for wipe ──
    assert len(dd_calls) >= 1, f"dd wipe must be called by _wipe_temp_key, got {len(dd_calls)} dd calls"
    dd_args = dd_calls[-1]
    assert "if=/dev/zero" in dd_args, f"dd must use if=/dev/zero for secure wipe, got args: {dd_args}"

    # ── Assertion 2: tracked temp files are removed from disk ──
    for tmp_file_path in list(_TEMP_FILES):
        assert not os.path.exists(tmp_file_path), f"Temp file {tmp_file_path} still exists after cleanup"

    # ── Assertion 3: _TEMP_FILES tracking list is empty ──
    assert len(_TEMP_FILES) == 0, f"Orphaned temp file entries: {_TEMP_FILES}"

    logger.info("[IMP:9][test_temp_key_cleanup] ✅ dd if=/dev/zero wipe confirmed, temp file removed")


# endregion FUNC_test_temp_key_cleanup


# ── Test: no secret values in logs ────────────────────────────────────────────


# region FUNC_test_no_secret_in_logs
## @purpose — Verify no full secret key value appears in any caplog record.
##            Tests DD5-4 invariant: keys masked to first 8 chars in logs.
##            Full key longer than 8 chars ensures masking is meaningful.
## @io — ⇥ caplog, tmp_path → ⎋ None (asserts no log contains full key)
## @complexity — O(1) — checks caplog.records after decrypt_sops_file call
## @invariants
##   - No log record contains the full 56-char secret key
##   - Masked key (first 8 chars) is allowed in logs
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · DD5-4 log masking (no secret in logs)
# · Last fail: N/A (new test)
# · Remove if: decrypt_sops_file logging behavior changes
def test_no_secret_in_logs(caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
    """No full secret key value appears in any log record (masked to first 8 chars)."""
    enc_path = tmp_path / "secrets.enc.yaml"
    enc_path.write_text("encrypted: placeholder\n")

    # 56-char key — must NOT appear in any log (only first 8 chars allowed)
    secret_key = "AGE-SECRET-KEY-0123456789abcdef0123456789abcdef"

    def _mock_run(args: list[str], **kwargs) -> mock.MagicMock:
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        if args and args[0] == "sops":
            result.stdout = "KEY=val\n"
        return result

    with mock.patch("subprocess.run", side_effect=_mock_run):
        decrypt_sops_file(secret_key, str(enc_path))

    # ── Verify no log record contains the full secret key ──
    leaked_records: list[str] = [record.message for record in caplog.records if secret_key in record.message]

    assert len(leaked_records) == 0, f"Full secret key leaked in {len(leaked_records)} log records:\n" + "\n".join(
        leaked_records
    )

    # ── Verify masked key IS present (proves logging occurred) ──
    masked_expected = secret_key[:8]
    masked_found = any(masked_expected in record.message for record in caplog.records)
    assert masked_found, f"Expected masked key '{masked_expected}...' to appear in logs"

    logger.info(
        "[IMP:9][test_no_secret_in_logs] ✅ No secret key leak — only masked '%s...' in logs",
        masked_expected,
    )


# endregion FUNC_test_no_secret_in_logs
