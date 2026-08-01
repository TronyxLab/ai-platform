#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-ssl-certs openssl x509 parseable expiry issuer lets-encrypt checkend constants
# STRUCTURE: ▶ test_parseable [ok|fail|timeout] → test_expiry [ok|expired] → test_issuer [le|non-le|fail] → test_constants
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/ssl_certs.py — единый SoT openssl-примитивов (DevPlan 117 D21).
## @scope    Tests: cert_is_parseable, cert_check_expiry, cert_get_issuer, cert_is_le_issuer, константы.
## @invariants
##   - Все openssl вызовы мокаются (subprocess.run) — нет реальных вызовов openssl
##   - Non-fatal контракт: subprocess ошибки → False/None (никогда не raise)
##   - LDD: IMP:9 в успешных сценариях (assert в каждом тесте)
## @changes 2026-08-01 | DevPlan 117 D21 — создан
# endregion MODULE_CONTRACT

import logging
from unittest.mock import MagicMock, patch

import pytest

from core.internal.shared.ssl_certs import (
    DEFAULT_EXPIRY_THRESHOLD,
    DEFAULT_OPENSSL_TIMEOUT,
    cert_check_expiry,
    cert_get_issuer,
    cert_is_le_issuer,
    cert_is_parseable,
)

logger = logging.getLogger(__name__)


def _assert_imp9(caplog: pytest.LogCaptureFixture, needle: str | None = None) -> None:
    """Assert at least one IMP:9 log (LDD telemetry standard)."""
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
            if needle and needle in record.message:
                found = True
    print("--- END LDD TRAJECTORY ---")
    if needle:
        assert found, f"Critical LDD Error: No IMP:9 log containing '{needle}'"
    else:
        assert any("[IMP:9]" in r.message for r in caplog.records), "Critical LDD Error: No IMP:9 log found"


# region TEST_constants
def test_default_constants(caplog: pytest.LogCaptureFixture) -> None:
    """DEFAULT_OPENSSL_TIMEOUT=10, DEFAULT_EXPIRY_THRESHOLD=2592000 (30 дней)."""
    caplog.set_level(logging.INFO)
    assert DEFAULT_OPENSSL_TIMEOUT == 10
    assert DEFAULT_EXPIRY_THRESHOLD == 2592000
    logger.info(
        "[IMP:9][test][constants] DEFAULT_OPENSSL_TIMEOUT=%d DEFAULT_EXPIRY_THRESHOLD=%d",
        DEFAULT_OPENSSL_TIMEOUT,
        DEFAULT_EXPIRY_THRESHOLD,
    )


# endregion TEST_constants


# region TEST_cert_is_parseable
def test_parseable_ok(caplog: pytest.LogCaptureFixture) -> None:
    """openssl x509 -noout returncode=0 → True."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert cert_is_parseable("/tmp/cert.pem") is True


def test_parseable_fail(caplog: pytest.LogCaptureFixture) -> None:
    """openssl returncode!=0 → False (corrupt cert)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert cert_is_parseable("/tmp/cert.pem") is False


def test_parseable_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """openssl TimeoutExpired → False (никогда не raise)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run", side_effect=TimeoutError("timeout")):
        assert cert_is_parseable("/tmp/cert.pem") is False


# endregion TEST_cert_is_parseable


# region TEST_cert_check_expiry
def test_expiry_ok(caplog: pytest.LogCaptureFixture) -> None:
    """openssl -checkend returncode=0 → True (>threshold осталось)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert cert_check_expiry("/tmp/cert.pem", 2592000) is True
        # Threshold передаётся в команду
        args = mock_run.call_args.args[0]
        assert "2592000" in args


def test_expiry_expired(caplog: pytest.LogCaptureFixture) -> None:
    """openssl -checkend returncode!=0 → False (истёк / в пределах порога)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert cert_check_expiry("/tmp/cert.pem", 2592000) is False


# endregion TEST_cert_check_expiry


# region TEST_cert_get_issuer
def test_get_issuer_ok(caplog: pytest.LogCaptureFixture) -> None:
    """openssl -issuer returncode=0 → issuer строка (stripped)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n")
        issuer = cert_get_issuer("/tmp/cert.pem")
        assert issuer == "issuer=C = US, O = Let's Encrypt, CN = R11"


def test_get_issuer_fail(caplog: pytest.LogCaptureFixture) -> None:
    """openssl returncode!=0 → None."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert cert_get_issuer("/tmp/cert.pem") is None


def test_get_issuer_empty(caplog: pytest.LogCaptureFixture) -> None:
    """openssl успех, но пустой issuer → None (пустая строка не является валидным issuer)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="   \n")
        assert cert_get_issuer("/tmp/cert.pem") is None


# endregion TEST_cert_get_issuer


# region TEST_cert_is_le_issuer
def test_is_le_issuer_accepts_le(caplog: pytest.LogCaptureFixture) -> None:
    """Issuer содержит 'Let's Encrypt' → True."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n")
        assert cert_is_le_issuer("/tmp/cert.pem") is True


def test_is_le_issuer_rejects_mkcert(caplog: pytest.LogCaptureFixture) -> None:
    """Issuer mkcert/self-signed → False (P0 regression, DevPlan 117 D21)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=O = mkcert development CA, CN = mkcert\n")
        assert cert_is_le_issuer("/tmp/cert.pem") is False
        _assert_imp9(caplog, "not Let's Encrypt")


def test_is_le_issuer_openssl_failure(caplog: pytest.LogCaptureFixture) -> None:
    """openssl возвращает ненулевой код → False (никогда не raise)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert cert_is_le_issuer("/tmp/cert.pem") is False


# endregion TEST_cert_is_le_issuer
