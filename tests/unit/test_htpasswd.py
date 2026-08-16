# GREP_SUMMARY: test-htpasswd write-htpasswd-file ensure-htpasswd extract-apr1-salt idempotent salt dir-heal
# STRUCTURE: ┌11 test functions┐ → ◇ extract_apr1_salt (3) → ◇ write_htpasswd_file (6, incl. dir-heal) → ◇ ensure_htpasswd (2)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/lifecycle/htpasswd.py — salt extraction,
##           idempotent .htpasswd-platform generation (DevPlan 117 G T58.3 extraction),
##           empty-dir self-heal (DevPlan 156 W2 — docker bind-mount artifact).
## @scope    No openssl/real crypto — generate_htpasswd_entry mocked where needed.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T58.3 §TEST_SPEC — htpasswd module direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T58.3 — created
## @changes  2026-08-12 · DevPlan 156 W2 — +2 теста dir-heal (empty → self-heal, non-empty → fail-visible)
# endregion MODULE_CONTRACT

import os
from pathlib import Path
from unittest import mock

# DevPlan 177 W3.6: htpasswd.py импортирует crypto как модуль пакета
# (core.internal.shared.crypto) — mock-таргет — канонический путь, sys.path-хак не нужен.
import pytest

from core.internal.bootstrap.lifecycle.htpasswd import (
    ensure_htpasswd,
    extract_apr1_salt,
    write_htpasswd_file,
)

pytestmark = pytest.mark.static_audit

# ══════════════════════════════════════════════════════════════════════
# TESTS: extract_apr1_salt
# ══════════════════════════════════════════════════════════════════════


class TestExtractApr1Salt:
    """Tests for extract_apr1_salt()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: valid apr1 / empty / bcrypt entry
    # · Expect: salt for apr1; "" for empty and non-apr1 (bcrypt) → caller regenerates
    # · Last fail: None (new test for DevPlan 117 G T58.3)
    # · Remove if: salt extraction logic changes
    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ("user:$apr1$abc123$hashvalue", "abc123"),
            ("", ""),
            ("user:$2y$10$somesalthash", ""),
        ],
    )
    def test_extract_apr1(self, entry: str, expected: str) -> None:
        """extract_apr1_salt: $apr1$SALT$HASH → SALT; пустой/bcrypt → ''."""
        assert extract_apr1_salt(entry) == expected


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
            "core.internal.shared.crypto.generate_htpasswd_entry",
            return_value="user:$apr1$newsalt$newhash",
        ) as mock_gen:
            result = write_htpasswd_file("user@example.com", "secret", str(target))

        assert result is True
        assert target.exists()
        assert target.read_text() == "user:$apr1$newsalt$newhash\n"
        # M8a (security hardening): htpasswd — 0600 (было 0o644 world-readable).
        assert target.stat().st_mode & 0o777 == 0o600
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
            "core.internal.shared.crypto.generate_htpasswd_entry",
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
            "core.internal.shared.crypto.generate_htpasswd_entry",
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
            "core.internal.shared.crypto.generate_htpasswd_entry",
            return_value=None,
        ):
            result = write_htpasswd_file("user@example.com", "secret", str(target))

        assert result is False
        assert not target.exists()

    # 🧪 TRAP[TEST] · Regression · DevPlan 156 W2 · empty dir at target self-healed
    # · Scenario: target path exists as empty DIRECTORY (docker bind-mount artifact) →
    # ·   rmdir + write file with APR1 entry, return True (fix for `pread() Is a directory`)
    # · Last fail: 2026-08-12 (asi-team-vps: nginx 401/500, .htpasswd-platform = пустая директория)
    # · Remove if: dir-heal logic in write_htpasswd_file changes
    def test_write_htpasswd_removes_empty_dir(self, tmp_path: Path, caplog) -> None:
        """Empty dir at target → removed (self-heal), file written with entry, True."""
        caplog.set_level(0)
        target = tmp_path / ".htpasswd-platform"
        target.mkdir()

        with mock.patch(
            "core.internal.shared.crypto.generate_htpasswd_entry",
            return_value="user:$apr1$newsalt$newhash",
        ):
            result = write_htpasswd_file("user@example.com", "secret", str(target))

        assert result is True
        assert target.is_file(), "Empty dir was not replaced by a file (self-heal failed)"
        assert target.read_text() == "user:$apr1$newsalt$newhash\n"
        assert "Removed empty dir" in caplog.text, f"Self-heal log missing:\n{caplog.text}"

    # 🧪 TRAP[TEST] · Regression · DevPlan 156 W2 · non-empty dir at target → fail-visible
    # · Scenario: target path exists as NON-empty directory → False + IMP:7 error (fail-visible,
    # ·   не тихий skip — иначе инцидент 2026-08-12 повторится незаметно)
    # · Last fail: None (new test — fail-visible branch)
    # · Remove if: dir-heal logic in write_htpasswd_file changes
    def test_write_htpasswd_nonempty_dir_fails(self, tmp_path: Path, caplog) -> None:
        """Non-empty dir at target → False (fail-visible ERROR log)."""
        caplog.set_level(0)
        target = tmp_path / ".htpasswd-platform"
        target.mkdir()
        (target / "leftover").write_text("x", encoding="utf-8")

        with mock.patch(
            "core.internal.shared.crypto.generate_htpasswd_entry",
            return_value="user:$apr1$newsalt$newhash",
        ):
            result = write_htpasswd_file("user@example.com", "secret", str(target))

        assert result is False
        assert target.is_dir(), "Non-empty dir must NOT be removed"
        assert "is a non-empty directory" in caplog.text, f"Fail-visible log missing:\n{caplog.text}"


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
