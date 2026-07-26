# GREP_SUMMARY: unit-test, age-key, detect-age-key, AGE_SECRET_KEY, SOPS_AGE_KEY, AGE_SECRET_KEY_FILE
# STRUCTURE: ▶ 6 tests → ◇ env → ◇ SOPS → ◇ file → ◇ empty → ◇ missing → ◇ log_tag → ⎋ pass|fail

# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/age_key.py — detect_age_key() function.
##           Tests all 3 detection mechanisms and edge cases.
## @scope    Pure unit tests — no subprocess, no Docker, no external dependencies.
##           Uses monkeypatch for env var manipulation + tmp_path for temp files.
## @invariants
##   - 6 tests: env, SOPS, file, empty, missing, log_tag
##   - All tests use @ldd_trajectory decorator
##   - Detection chain: AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE
## @rationale DevPlan 078 §$TEST_SPEC: 6 tests for age_key.py
## @changes  2026-07-25 | DevPlan 078 Phase B T1 — Created unit tests
# endregion MODULE_CONTRACT

import logging
import os

import pytest

from tests.conftest import ldd_trajectory

# Add shared module to path
_SHARED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "core",
    "internal",
    "shared",
)
import sys as _sys

if _SHARED_DIR not in _sys.path:
    _sys.path.insert(0, _SHARED_DIR)

from age_key import detect_age_key

logger = logging.getLogger(__name__)

TEST_AGE_KEY = "AGE_SECRET_KEY_CONTENT_0123456789abcdef"


# region FUNC_test_detect_age_key_from_env
## @purpose — Verify detect_age_key reads from AGE_SECRET_KEY env var.
## @io — ⇥ monkeypatch → ⎋ None (asserts key matches)
## @complexity — O(1)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · AGE_SECRET_KEY env detection
# · Last fail: N/A (new test)
# · Remove if: detect_age_key function is removed
def test_detect_age_key_from_env(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """detect_age_key returns AGE_SECRET_KEY from env var."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("AGE_SECRET_KEY", TEST_AGE_KEY)
    monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
    monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)

    logger.info("[IMP:7][test_age_key] Testing AGE_SECRET_KEY env detection")
    result = detect_age_key()
    assert result == TEST_AGE_KEY, f"Expected {TEST_AGE_KEY}, got {result}"
    logger.info("[IMP:9][test_age_key] ✅ detect_age_key returned key from AGE_SECRET_KEY env")


# endregion FUNC_test_detect_age_key_from_env


# region FUNC_test_detect_age_key_from_sops
## @purpose — Verify detect_age_key falls back to SOPS_AGE_KEY env var.
## @io — ⇥ monkeypatch → ⎋ None (asserts key matches)
## @complexity — O(1)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · SOPS_AGE_KEY fallback detection
# · Last fail: N/A (new test)
# · Remove if: detect_age_key function is removed
def test_detect_age_key_from_sops(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """detect_age_key falls back to SOPS_AGE_KEY when AGE_SECRET_KEY is not set."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
    monkeypatch.setenv("SOPS_AGE_KEY", TEST_AGE_KEY)
    monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)

    logger.info("[IMP:7][test_age_key] Testing SOPS_AGE_KEY fallback")
    result = detect_age_key()
    assert result == TEST_AGE_KEY, f"Expected {TEST_AGE_KEY}, got {result}"
    logger.info("[IMP:9][test_age_key] ✅ detect_age_key returned key from SOPS_AGE_KEY fallback")


# endregion FUNC_test_detect_age_key_from_sops


# region FUNC_test_detect_age_key_from_file
## @purpose — Verify detect_age_key reads from AGE_SECRET_KEY_FILE.
## @io — ⇥ monkeypatch, tmp_path → ⎋ None (asserts key matches)
## @complexity — O(1)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · AGE_SECRET_KEY_FILE detection
# · Last fail: N/A (new test)
# · Remove if: detect_age_key function is removed
def test_detect_age_key_from_file(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """detect_age_key reads AGE_SECRET_KEY_FILE when env vars not set."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SOPS_AGE_KEY", raising=False)

    key_file = tmp_path / "age-key.txt"
    key_file.write_text(TEST_AGE_KEY + "\n")
    monkeypatch.setenv("AGE_SECRET_KEY_FILE", str(key_file))

    logger.info("[IMP:7][test_age_key] Testing AGE_SECRET_KEY_FILE detection")
    result = detect_age_key()
    assert result == TEST_AGE_KEY, f"Expected {TEST_AGE_KEY}, got {result}"
    logger.info("[IMP:9][test_age_key] ✅ detect_age_key returned key from AGE_SECRET_KEY_FILE")


# endregion FUNC_test_detect_age_key_from_file


# region FUNC_test_detect_age_key_empty_file
## @purpose — Verify detect_age_key returns None when AGE_SECRET_KEY_FILE is empty.
## @io — ⇥ monkeypatch, tmp_path → ⎋ None (asserts None)
## @complexity — O(1)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · Empty AGE_SECRET_KEY_FILE
# · Last fail: N/A (new test)
# · Remove if: detect_age_key function is removed
def test_detect_age_key_empty_file(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """detect_age_key returns None when AGE_SECRET_KEY_FILE is empty."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SOPS_AGE_KEY", raising=False)

    key_file = tmp_path / "empty-key.txt"
    key_file.write_text("")
    monkeypatch.setenv("AGE_SECRET_KEY_FILE", str(key_file))

    logger.info("[IMP:7][test_age_key] Testing empty AGE_SECRET_KEY_FILE")
    result = detect_age_key()
    assert result is None, f"Expected None, got {result}"
    logger.info("[IMP:9][test_age_key] ✅ detect_age_key returned None for empty file")


# endregion FUNC_test_detect_age_key_empty_file


# region FUNC_test_detect_age_key_missing
## @purpose — Verify detect_age_key returns None when no key source is available.
## @io — ⇥ monkeypatch → ⎋ None (asserts None)
## @complexity — O(1)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · No AGE key source available
# · Last fail: N/A (new test)
# · Remove if: detect_age_key function is removed
def test_detect_age_key_missing(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detect_age_key returns None when no key source is available."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
    monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)

    logger.info("[IMP:7][test_age_key] Testing missing key — all sources absent")
    result = detect_age_key()
    assert result is None, f"Expected None, got {result}"
    logger.info("[IMP:9][test_age_key] ✅ detect_age_key returned None when all sources absent")


# endregion FUNC_test_detect_age_key_missing


# region FUNC_test_detect_age_key_log_tag
## @purpose — Verify detect_age_key logs masked (first 8 chars) when key found.
## @io — ⇥ caplog, monkeypatch → ⎋ None (asserts log contains masked key)
## @complexity — O(1)
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · Log tag masking for AGE key
# · Last fail: N/A (new test)
# · Remove if: detect_age_key function is removed
def test_detect_age_key_log_tag(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detect_age_key logs masked key (first 8 chars)."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("AGE_SECRET_KEY", TEST_AGE_KEY)
    monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
    monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)

    logger.info("[IMP:7][test_age_key] Testing log masking")
    detect_age_key()

    masked_expected = TEST_AGE_KEY[:8]
    found_log = False
    for record in caplog.records:
        if masked_expected in record.message:
            found_log = True
            break
    assert found_log, f"Log should contain masked key '{masked_expected}'"
    logger.info("[IMP:9][test_age_key] ✅ detect_age_key logged masked key '%s...'", masked_expected)


# endregion FUNC_test_detect_age_key_log_tag
