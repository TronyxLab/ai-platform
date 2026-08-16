# GREP_SUMMARY: test-cert-collector timezone naive datetime tzinfo cryptography load_cert san_match wildcard
# STRUCTURE: ▶ test_not_valid_after_naive_datetime → ◇ mock cert with naive not_valid_after → assert days_remaining without TypeError
#            ▶ test_not_valid_after_aware_datetime → ◇ mock cert with aware not_valid_after → assert days_remaining
#            ▶ test_load_cert_file_not_found → ◇ non-existent path → assert None
#            ▶ test_san_match_exact → ◇ exact domain match → assert True
#            ▶ test_san_match_wildcard → ◇ *.domain match → assert True|False
# @file test_cert_collector.py
# @purpose  Unit tests for cert_collector.py — timezone normalization (P3 fix), SAN matching, error handling
# @scope    Unit-level: tests call _load_cert, _san_match directly with mocked cryptography.x509
# @invariants
#   - All tests use tmp_path fixture (Zero Hardcode Rule)
#   - LDD trajectory (IMP:7-10) printed before every assert
#   - No Docker required — static unit tests only
#   - Test Honesty Rules: R1 (no pass-tests), R2 (no unfalsifiable asserts)
# @rationale  P3 fix (naive→aware timezone normalization) must be tested to prevent regression.
# @changes 2026-07-24 | CREATED | DevPlan 066 Wave 3 — test timezone normalization in _load_cert
# region MODULE_CONTRACT
## @purpose  Unit tests for cert_collector.py timezone fix (P3)
## @scope    Unit tests — mock cryptography.x509, no live certs needed
## @invariants
##   - tmp_path fixture for all file operations
##   - caplog for LDD trajectory capture
##   - At least one IMP:9 log in successful scenarios
# endregion MODULE_CONTRACT

import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

# 177 W2.4: канонический импорт (core.internal...); sys.path-хак и bare-импорт internal.
# удалены — conftest предоставляет <repo_root>/ (tests/AGENTS.md §sys.path policy)
from core.internal.healthcheck.metrics.cert_collector import _load_cert, _san_match

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_cert_pem(tmp_path: Path) -> str:
    """Create a minimal self-signed cert PEM file for testing.

    Uses cryptography to generate a real cert so _load_cert can parse it.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
    cert = (
        x509
        .CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=timezone.utc))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("test.example.com"), x509.DNSName("*.example.com")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    pem_path = tmp_path / "fullchain.pem"
    pem_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(pem_path)


# ═══════════════════════════════════════════════════════════════════
# TESTS: P3 — Timezone normalization
# ═══════════════════════════════════════════════════════════════════


class TestCertCollectorTimezone:
    """P3 fix: timezone normalization for naive datetime from cryptography < 41.0.0."""

    def test_not_valid_after_naive_datetime(self, mock_cert_pem, caplog):
        """_load_cert handles naive not_valid_after without TypeError (P3 fix).

        Simulates cryptography < 41.0.0 behavior where not_valid_after
        returns a naive datetime (without tzinfo).
        """
        caplog.set_level(0)

        # Mock cert.not_valid_after to return naive datetime (no tzinfo)
        # намеренно naive: симуляция cryptography < 41.0.0 (P3 fix)
        naive_expiry = datetime(2030, 1, 1)  # ruff: ignore[DTZ001] — No tzinfo — naive!

        with mock.patch("cryptography.x509.load_pem_x509_certificate") as mock_load:
            # Create a mock certificate that returns naive datetime
            mock_cert = mock.MagicMock()
            mock_cert.not_valid_after = naive_expiry
            mock_cert.issuer.rfc4514_string.return_value = "CN=Test CA"
            mock_cert.subject.rfc4514_string.return_value = "CN=test.example.com"

            # Mock SAN extension

            san_ext = mock.MagicMock()
            san_ext.value.get_values_for_type.return_value = ["test.example.com"]
            mock_cert.extensions.get_extension_for_class.return_value = san_ext

            # hasattr check for not_valid_after_utc — False (old API)
            del mock_cert.not_valid_after_utc

            mock_load.return_value = mock_cert

            result = _load_cert(mock_cert_pem)

        # ── LDD TRAJECTORY ──
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in list(caplog.records):
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        logger.info("%s", msg)
        logger.info("--- END LDD TRAJECTORY ---")

        assert result is not None, "_load_cert returned None — should have succeeded"
        assert "days_remaining" in result, "Missing days_remaining in result"
        assert isinstance(result["days_remaining"], int), (
            f"days_remaining should be int, got {type(result['days_remaining'])}"
        )
        # With expiry 2030-01-01, days_remaining should be positive
        assert result["days_remaining"] > 0, (
            f"Expected positive days_remaining for 2030 expiry, got {result['days_remaining']}"
        )

    def test_not_valid_after_aware_datetime(self, mock_cert_pem, caplog):
        """_load_cert handles aware not_valid_after (modern cryptography >= 41.0.0)."""
        caplog.set_level(0)

        aware_expiry = datetime(2030, 1, 1, tzinfo=timezone.utc)

        with mock.patch("cryptography.x509.load_pem_x509_certificate") as mock_load:
            mock_cert = mock.MagicMock()
            mock_cert.not_valid_after = aware_expiry
            mock_cert.issuer.rfc4514_string.return_value = "CN=Test CA"
            mock_cert.subject.rfc4514_string.return_value = "CN=test.example.com"

            san_ext = mock.MagicMock()
            san_ext.value.get_values_for_type.return_value = ["test.example.com"]
            mock_cert.extensions.get_extension_for_class.return_value = san_ext

            del mock_cert.not_valid_after_utc

            mock_load.return_value = mock_cert

            result = _load_cert(mock_cert_pem)

        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in list(caplog.records):
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        logger.info("%s", msg)
        logger.info("--- END LDD TRAJECTORY ---")

        assert result is not None
        assert result["days_remaining"] > 0

    def test_not_valid_after_utc_attribute(self, mock_cert_pem, caplog):
        """_load_cert uses not_valid_after_utc when available (cryptography >= 41.0.0)."""
        caplog.set_level(0)

        utc_expiry = datetime(2030, 1, 1, tzinfo=timezone.utc)

        with mock.patch("cryptography.x509.load_pem_x509_certificate") as mock_load:
            mock_cert = mock.MagicMock()
            # Modern API: has not_valid_after_utc
            mock_cert.not_valid_after_utc = utc_expiry
            mock_cert.issuer.rfc4514_string.return_value = "CN=Test CA"
            mock_cert.subject.rfc4514_string.return_value = "CN=test.example.com"

            san_ext = mock.MagicMock()
            san_ext.value.get_values_for_type.return_value = ["test.example.com"]
            mock_cert.extensions.get_extension_for_class.return_value = san_ext

            mock_load.return_value = mock_cert

            result = _load_cert(mock_cert_pem)

        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in list(caplog.records):
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        logger.info("%s", msg)
        logger.info("--- END LDD TRAJECTORY ---")

        assert result is not None
        assert result["days_remaining"] > 0


# ═══════════════════════════════════════════════════════════════════
# TESTS: _san_match
# ═══════════════════════════════════════════════════════════════════


class TestSanMatch:
    """Tests for _san_match — exact and wildcard domain matching (parametrized, F5-reduction)."""

    @pytest.mark.parametrize(
        ("san_list", "domain", "expected"),
        [
            (["example.com", "www.example.com"], "example.com", True),  # exact match
            (["Example.COM"], "example.com", True),  # case-insensitive
            (["*.example.com"], "sub.example.com", True),  # wildcard matches subdomain
            (["*.test.com", "*.example.com"], "sub.example.com", True),  # multiple wildcard entries (merged W2 T2.1)
            (["*.example.com"], "example.com", False),  # wildcard does NOT match root
            (["*.example.com"], "deep.sub.example.com", False),  # wildcard does NOT match deep subdomain
            (["example.com"], "other.com", False),  # unrelated domain
            ([], "example.com", False),  # empty SAN list
            (["example.com."], "example.com.", True),  # trailing dots are normalized
        ],
    )
    def test_san_match(self, san_list, domain, expected):
        """Parametrized: all _san_match exact/wildcard/edge cases."""
        assert _san_match(san_list, domain) is expected


# ═══════════════════════════════════════════════════════════════════
# TESTS: Error handling
# ═══════════════════════════════════════════════════════════════════


class TestCertCollectorErrors:
    """Tests for error handling in _load_cert."""

    def test_file_not_found(self, tmp_path, caplog):
        """_load_cert returns None for non-existent file."""
        caplog.set_level(0)

        result = _load_cert(str(tmp_path / "nonexistent.pem"))

        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in list(caplog.records):
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        logger.info("%s", msg)
        logger.info("--- END LDD TRAJECTORY ---")

        assert result is None

    def test_invalid_pem(self, tmp_path, caplog):
        """_load_cert returns None for invalid PEM content."""
        caplog.set_level(0)

        bad_file = tmp_path / "bad.pem"
        bad_file.write_text("not a valid certificate")

        result = _load_cert(str(bad_file))

        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in list(caplog.records):
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        logger.info("%s", msg)
        logger.info("--- END LDD TRAJECTORY ---")

        assert result is None
