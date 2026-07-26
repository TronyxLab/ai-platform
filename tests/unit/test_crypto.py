# GREP_SUMMARY: unit-test, crypto, htpasswd, apr1, hash, password, openssl
# STRUCTURE: ▶ 4 tests → ◇ hash_random → ◇ hash_fixed_salt → ◇ entry → ◇ idempotent → ⎋ pass|fail

# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/crypto.py — hash_apr1() and
##           generate_htpasswd_entry() functions.
## @scope    Pure unit tests — requires openssl binary. No Docker, no external services.
## @invariants
##   - 4 tests: random salt, fixed salt, entry generation, idempotent with salt
##   - All tests use @ldd_trajectory decorator
##   - hash_apr1 with same salt+password = deterministic (idempotent)
##   - hash_apr1 without salt = random each call (non-deterministic)
## @rationale DevPlan 078 §$TEST_SPEC: 4 tests for crypto.py
## @changes  2026-07-25 | DevPlan 078 Phase B T3 — Created unit tests
# endregion MODULE_CONTRACT

import logging
import os
import sys

import pytest

from tests.conftest import ldd_trajectory

# Add shared module to path
_SHARED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "core",
    "internal",
    "shared",
)
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

from crypto import generate_htpasswd_entry, hash_apr1

logger = logging.getLogger(__name__)

TEST_PASSWORD = "test-password-123"
TEST_USERNAME = "admin@example.com"


# region FUNC_test_hash_apr1_random_salt
## @purpose — Verify hash_apr1 generates different hashes on each call (random salt).
## @io — ⇥ None → ⎋ None (asserts 2 hashes differ)
## @complexity — O(1) + subprocess
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · APR1 hash with random salt
# · Last fail: N/A (new test)
# · Remove if: hash_apr1 function is removed
def test_hash_apr1_random_salt(caplog: pytest.LogCaptureFixture) -> None:
    """hash_apr1 with no salt generates different hashes each call."""
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:7][test_crypto] Testing APR1 hash with random salt")
    hash1 = hash_apr1(TEST_PASSWORD)
    hash2 = hash_apr1(TEST_PASSWORD)

    assert hash1 is not None, "First hash should not be None"
    assert hash2 is not None, "Second hash should not be None"
    assert hash1 != hash2, "Two calls with random salt should produce different hashes"
    assert hash1.startswith("$apr1$"), f"Expected APR1 hash format, got: {hash1[:20]}..."
    logger.info("[IMP:9][test_crypto] ✅ hash_apr1 with random salt: %s...", hash1[:20])


# endregion FUNC_test_hash_apr1_random_salt


# region FUNC_test_hash_apr1_fixed_salt
## @purpose — Verify hash_apr1 with fixed salt produces deterministic output.
## @io — ⇥ None → ⎋ None (asserts 2 hashes are identical)
## @complexity — O(1) + subprocess
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · APR1 hash with fixed salt (idempotent)
# · Last fail: N/A (new test)
# · Remove if: hash_apr1 function is removed
def test_hash_apr1_fixed_salt(caplog: pytest.LogCaptureFixture) -> None:
    """hash_apr1 with fixed salt produces deterministic output."""
    caplog.set_level(logging.DEBUG)

    fixed_salt = "fixedSal"  # APR1 salt limited to 8 chars
    logger.info("[IMP:7][test_crypto] Testing APR1 hash with fixed salt: %s", fixed_salt)

    hash1 = hash_apr1(TEST_PASSWORD, fixed_salt)
    hash2 = hash_apr1(TEST_PASSWORD, fixed_salt)

    assert hash1 is not None, "First hash should not be None"
    assert hash2 is not None, "Second hash should not be None"
    assert hash1 == hash2, "Same salt + same password should produce identical hash"
    assert hash1.startswith(f"$apr1${fixed_salt}"), f"Hash should contain fixed salt: {hash1}"
    logger.info("[IMP:9][test_crypto] ✅ hash_apr1 with fixed salt: %s", hash1[:30])


# endregion FUNC_test_hash_apr1_fixed_salt


# region FUNC_test_generate_htpasswd_entry
## @purpose — Verify generate_htpasswd_entry produces valid "user:hash" format.
## @io — ⇥ None → ⎋ None (asserts format and content)
## @complexity — O(1) + hash_apr1
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · htpasswd entry generation
# · Last fail: N/A (new test)
# · Remove if: generate_htpasswd_entry function is removed
def test_generate_htpasswd_entry(caplog: pytest.LogCaptureFixture) -> None:
    """generate_htpasswd_entry produces valid username:hash format."""
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:7][test_crypto] Testing htpasswd entry generation for %s", TEST_USERNAME)
    entry = generate_htpasswd_entry(TEST_USERNAME, TEST_PASSWORD)

    assert entry is not None, "Entry should not be None"
    assert ":" in entry, "Entry should contain ':' separator"
    username_part, hash_part = entry.split(":", 1)
    assert username_part == TEST_USERNAME, f"Username mismatch: {username_part}"
    assert hash_part.startswith("$apr1$"), f"Hash should be APR1 format: {hash_part[:20]}..."
    logger.info("[IMP:9][test_crypto] ✅ htpasswd entry: %s:%s...", username_part, hash_part[:20])


# endregion FUNC_test_generate_htpasswd_entry


# region FUNC_test_generate_htpasswd_idempotent
## @purpose — Verify generate_htpasswd_entry with fixed salt is idempotent.
## @io — ⇥ None → ⎋ None (asserts 2 calls produce identical entry)
## @complexity — O(1) + hash_apr1
@pytest.mark.unit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · htpasswd entry idempotency
# · Last fail: N/A (new test)
# · Remove if: generate_htpasswd_entry function is removed
def test_generate_htpasswd_idempotent(caplog: pytest.LogCaptureFixture) -> None:
    """generate_htpasswd_entry with fixed salt is idempotent."""
    caplog.set_level(logging.DEBUG)

    fixed_salt = "idempotent"  # APR1 salt limited to 8 chars
    logger.info("[IMP:7][test_crypto] Testing htpasswd entry idempotency with salt: %s", fixed_salt)

    entry1 = generate_htpasswd_entry(TEST_USERNAME, TEST_PASSWORD, fixed_salt)
    entry2 = generate_htpasswd_entry(TEST_USERNAME, TEST_PASSWORD, fixed_salt)

    assert entry1 is not None, "First entry should not be None"
    assert entry2 is not None, "Second entry should not be None"
    assert entry1 == entry2, "Same inputs with fixed salt should produce identical entry"
    logger.info("[IMP:9][test_crypto] ✅ generate_htpasswd_entry idempotent: %s...", entry1[:30])


# endregion FUNC_test_generate_htpasswd_idempotent
