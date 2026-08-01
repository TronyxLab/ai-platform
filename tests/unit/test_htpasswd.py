#!/usr/bin/env python3
# GREP_SUMMARY: test-htpasswd write-htpasswd-file ensure-htpasswd extract-apr1-salt idempotent salt
# STRUCTURE: ┌9 test functions┐ → ◇ extract_apr1_salt (3) → ◇ write_htpasswd_file (4) → ◇ ensure_htpasswd (2)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/lifecycle/htpasswd.py — salt extraction,
##           idempotent .htpasswd-platform generation (DevPlan 117 G T58.3 extraction).
## @scope    No openssl/real crypto — generate_htpasswd_entry mocked where needed.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T58.3 §TEST_SPEC — htpasswd module direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T58.3 — created
# endregion MODULE_CONTRACT

import os
import sys
from pathlib import Path
from unittest import mock

# Add shared dir to path so `from crypto import ...` inside htpasswd resolves (same
# pattern as test_crypto.py). mock.patch("crypto.generate_htpasswd_entry") needs the
# bare `crypto` module importable at patch time.
_SHARED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "core",
    "internal",
    "shared",
)
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

from core.internal.bootstrap.lifecycle.htpasswd import (
    ensure_htpasswd,
    extract_apr1_salt,
    write_htpasswd_file,
)

# ══════════════════════════════════════════════════════════════════════
# TESTS: extract_apr1_salt
# ══════════════════════════════════════════════════════════════════════


class TestExtractApr1Salt:
    """Tests for extract_apr1_salt()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: valid apr1 entry
    # · Expect: salt returned
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: salt extraction logic changes
    def test_extract_apr1_valid(self) -> None:
        """$apr1$SALT$HASH → SALT."""
        assert extract_apr1_salt("user:$apr1$abc123$hashvalue") == "abc123"

    # 🧪 TRAP[TEST] · Regression · Scenario: empty entry
    # · Expect: ""
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: salt extraction logic changes
    def test_extract_apr1_empty(self) -> None:
        """Empty string → ''."""
        assert extract_apr1_salt("") == ""

    # 🧪 TRAP[TEST] · Regression · Scenario: non-apr1 hash (bcrypt)
    # · Expect: "" → caller regenerates
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: salt extraction logic changes
    def test_extract_apr1_bcrypt_rejected(self) -> None:
        """bcrypt $2y$ entry → '' (not apr1)."""
        assert extract_apr1_salt("user:$2y$10$somesalthash") == ""


# ══════════════════════════════════════════════════════════════════════
# TESTS: write_htpasswd_file
# ══════════════════════════════════════════════════════════════════════


class TestWriteHtpasswdFile:
    """Tests for write_htpasswd_file()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: fresh file (no existing)
    # · Expect: file created, entry written with newline, HTPASSWD_FILE exported
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: write logic changes
    def test_write_htpasswd_creates_file(self, tmp_path: Path, caplog) -> None:
        """First call → file created with generated entry."""
        caplog.set_level(0)
        target = tmp_path / "sub" / ".htpasswd-platform"

        with mock.patch(
            "crypto.generate_htpasswd_entry",
            return_value="user:$apr1$newsalt$newhash",
        ) as mock_gen:
            result = write_htpasswd_file("user@example.com", "secret", str(target))

        assert result is True
        assert target.exists()
        assert target.read_text() == "user:$apr1$newsalt$newhash\n"
        assert target.stat().st_mode & 0o644 == 0o644
        assert os.environ.get("HTPASSWD_FILE") == str(target)
        mock_gen.assert_called_once_with("user@example.com", "secret")

    # 🧪 TRAP[TEST] · Regression · Scenario: existing file with same credentials
    # · Expect: no rewrite (idempotent), file content unchanged
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: idempotency logic changes
    def test_write_htpasswd_idempotent(self, tmp_path: Path, caplog) -> None:
        """Existing identical entry → skip rewrite."""
        caplog.set_level(0)
        target = tmp_path / ".htpasswd-platform"
        target.write_text("user:$apr1$fixedsalt$existinghash\n", encoding="utf-8")

        with mock.patch(
            "crypto.generate_htpasswd_entry",
            return_value="user:$apr1$fixedsalt$existinghash",
        ) as mock_gen:
            result = write_htpasswd_file("user@example.com", "secret", str(target))

        assert result is True
        assert target.read_text() == "user:$apr1$fixedsalt$existinghash\n"
        mock_gen.assert_called_once_with("user@example.com", "secret", salt="fixedsalt")

    # 🧪 TRAP[TEST] · Regression · Scenario: existing file with changed credentials
    # · Expect: regenerated with existing salt
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: credentials-changed branch logic changes
    def test_write_htpasswd_changed_credentials(self, tmp_path: Path, caplog) -> None:
        """Existing salt reused, new hash written."""
        caplog.set_level(0)
        target = tmp_path / ".htpasswd-platform"
        target.write_text("user:$apr1$fixedsalt$oldhash\n", encoding="utf-8")

        with mock.patch(
            "crypto.generate_htpasswd_entry",
            return_value="user:$apr1$fixedsalt$newhash",
        ):
            result = write_htpasswd_file("user@example.com", "newpass", str(target))

        assert result is True
        assert target.read_text() == "user:$apr1$fixedsalt$newhash\n"

    # 🧪 TRAP[TEST] · Regression · Scenario: generate_htpasswd_entry returns None
    # · Expect: False (non-fatal), no file write
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: crypto-failure handling changes
    def test_write_htpasswd_crypto_failure(self, tmp_path: Path, caplog) -> None:
        """generate_htpasswd_entry → None → False, no crash."""
        caplog.set_level(0)
        target = tmp_path / ".htpasswd-platform"

        with mock.patch(
            "crypto.generate_htpasswd_entry",
            return_value=None,
        ):
            result = write_htpasswd_file("user@example.com", "secret", str(target))

        assert result is False
        assert not target.exists()


# ══════════════════════════════════════════════════════════════════════
# TESTS: ensure_htpasswd
# ══════════════════════════════════════════════════════════════════════


class TestEnsureHtpasswd:
    """Tests for ensure_htpasswd()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: env creds set → delegates to write
    # · Expect: True
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: ensure logic changes
    def test_ensure_htpasswd_env_creds(self, tmp_path: Path, caplog, monkeypatch) -> None:
        """PLATFORM_MASTER_* set in env → write_htpasswd_file called."""
        caplog.set_level(0)
        monkeypatch.setenv("PLATFORM_MASTER_EMAIL", "admin@example.com")
        monkeypatch.setenv("PLATFORM_MASTER_PASSWORD", "adminpass")
        target = tmp_path / ".htpasswd-platform"

        with mock.patch(
            "core.internal.bootstrap.lifecycle.htpasswd.write_htpasswd_file",
            return_value=True,
        ) as mock_write:
            result = ensure_htpasswd("unused.env", str(target))

        assert result is True
        mock_write.assert_called_once_with("admin@example.com", "adminpass", str(target))

    # 🧪 TRAP[TEST] · Regression · Scenario: missing creds → skip
    # · Expect: False, no write
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: ensure-missing-creds branch logic changes
    def test_ensure_htpasswd_missing_creds(self, tmp_path: Path, caplog, monkeypatch) -> None:
        """No PLATFORM_MASTER_* anywhere → False (skip)."""
        caplog.set_level(0)
        monkeypatch.delenv("PLATFORM_MASTER_EMAIL", raising=False)
        monkeypatch.delenv("PLATFORM_MASTER_PASSWORD", raising=False)
        target = tmp_path / ".htpasswd-platform"

        with (
            mock.patch(
                "core.internal.bootstrap.lifecycle.htpasswd.parse_secrets_env",
                return_value={},
            ),
            mock.patch(
                "core.internal.bootstrap.lifecycle.htpasswd.write_htpasswd_file",
                return_value=True,
            ) as mock_write,
        ):
            result = ensure_htpasswd(str(tmp_path / "missing.env"), str(target))

        assert result is False
        mock_write.assert_not_called()
